"""Database-owned snapshots, preview persistence and atomic assignment execution."""
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
from statistics import median
import uuid
from zoneinfo import ZoneInfo

from fastapi.encoders import jsonable_encoder
from psycopg import sql
from psycopg.types.json import Jsonb
from project.assignment_engine.domain import validate_plan, effective_signals
from normalization.common import ROOT


class Conflict(ValueError):pass
class NotFound(ValueError):pass


def get_state(conn,record_ids):
    rows=conn.execute('''SELECT r.*, r.notes AS original_notes, COALESCE(s.assignment_note,r.notes) AS notes,
        s.assignment_note, s.structured_labels AS signals, s.note_template_id, s.label_source, s.updated_at AS signal_updated_at,
        EXISTS(SELECT 1 FROM sales_assignment.records d WHERE d.nit=r.nit AND d.id<>r.id) AS is_duplicate
        FROM sales_assignment.records r LEFT JOIN sales_assignment.record_signals s ON s.record_id=r.id
        WHERE r.id=ANY(%s) ORDER BY r.id''',(sorted(record_ids),)).fetchall()
    if len(rows)!=len(record_ids):raise NotFound('One or more records do not exist')
    today=datetime.now(ZoneInfo('America/Mexico_City')).date()
    users=conn.execute('SELECT * FROM sales_assignment.users ORDER BY id').fetchall()
    overrides={row['user_id']:row for row in conn.execute('SELECT * FROM sales_assignment.seller_operational_overrides')}
    absences=conn.execute('SELECT * FROM sales_assignment.absences ORDER BY id').fetchall()
    workload={r['seller_id']:r for r in conn.execute('''SELECT COALESCE(a.seller_id,h.user_id) AS seller_id,
        count(*) FILTER (WHERE r.status='asignado') AS assigned_count,
        count(*) FILTER (WHERE r.status='en_gestion') AS in_management_count,
        COALESCE(sum(r.estimated_revenue),0) AS known_estimated_amount,
        count(*) FILTER (WHERE r.estimated_revenue IS NULL) AS missing_amounts
        FROM sales_assignment.records r LEFT JOIN sales_assignment.assignments a ON a.record_id=r.id AND a.is_current
        LEFT JOIN sales_assignment.historical_ownership h ON h.record_id=r.id
        WHERE r.status IN ('asignado','en_gestion') GROUP BY COALESCE(a.seller_id,h.user_id)''')}
    exposure={}
    for r in conn.execute('''SELECT a.user_id,r.sector,count(DISTINCT r.id) AS accounts
        FROM sales_assignment.activities a JOIN sales_assignment.records r ON r.id=a.record_id
        WHERE r.sector IS NOT NULL GROUP BY a.user_id,r.sector'''):
        exposure.setdefault(r['user_id'],{})[r['sector']]=r['accounts']
    response_times={}
    for r in conn.execute('''SELECT a.user_id,r.id,min(a.occurred_on)-r.source_created_on AS days
        FROM sales_assignment.activities a JOIN sales_assignment.records r ON r.id=a.record_id
        WHERE a.occurred_on>=r.source_created_on GROUP BY a.user_id,r.id'''):
        response_times.setdefault(r['user_id'],[]).append(r['days'])
    skills_by_user={}
    for row in conn.execute('SELECT user_id,skill,verification_evidence,updated_at FROM sales_assignment.user_technical_skills ORDER BY user_id,skill'):
        skills_by_user.setdefault(row['user_id'],[]).append(dict(skill=row['skill'],evidence=row['verification_evidence'],updated_at=row['updated_at']))
    sellers=[]
    for user in users:
        user=dict(user);override=overrides.get(user['id'])
        if override:
            for key in ('active','team_id','zone','maximum_capacity'):
                if override[key] is not None:user[key]=override[key]
            user['availability_override']=override['availability_override']
            user['operational_override_reason']=override['reason']
        else:user['availability_override']=False
        counts=workload.get(user['id'],{})
        user['assigned_count']=counts.get('assigned_count',0);user['in_management_count']=counts.get('in_management_count',0)
        user['open_workload']=user['assigned_count']+user['in_management_count']
        user['known_estimated_amount']=counts.get('known_estimated_amount',0)
        user['missing_amounts']=counts.get('missing_amounts',0)
        user['absences']=[dict(starts_on=a['starts_on'],ends_on=a['ends_on'],raw_end_present=bool(a['source_payload'].get('hasta_original'))) for a in absences if a['user_id']==user['id']]
        user['tenure_years']=max(0,(today-user['joined_on']).days/365.25) if user['joined_on'] else None
        user['sector_experience']=exposure.get(user['id'],{})
        user['response_days']=median(response_times[user['id']]) if response_times.get(user['id']) else None
        user['technical_skill_evidence']=skills_by_user.get(user['id'],[])
        user['technical_skills']=[entry['skill'] for entry in user['technical_skill_evidence']]
        sellers.append(user)
    for record in rows:record['segment']=record['source_payload'].get('segmento_normalizado') or None
    run=conn.execute('SELECT id FROM sales_assignment.normalization_runs ORDER BY started_at DESC LIMIT 1').fetchone()
    state=jsonable_encoder(dict(records=rows,sellers=sellers,effective_date=today.isoformat(),normalization_run_id=run['id'] if run else None))
    state['state_hash']=hashlib.sha256(json.dumps(state,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    return state


def get_preview(conn,preview_id):
    row=conn.execute('SELECT * FROM sales_assignment.assignment_previews WHERE id=%s',(preview_id,)).fetchone()
    if not row:raise NotFound('Preview does not exist')
    return dict(row['configuration']['result'],preview_id=str(row['id']),status=row['status'],state_hash=row['snapshot_hash'],created_at=row['created_at'],snapshot=row['operational_snapshot'])

def manual_assignments(conn,preview_id,assignments):
    row=conn.execute('SELECT * FROM sales_assignment.assignment_previews WHERE id=%s FOR UPDATE',(preview_id,)).fetchone()
    if not row:raise NotFound('Preview does not exist')
    if row['status'] not in ('draft','approved'):raise Conflict('Preview cannot be edited in its current state')
    plan=deepcopy(row['configuration']['result']);original={str(item['record_id']):item for item in plan['assignments']}
    supplied={str(item.get('record_id')):str(item.get('seller_id')) for item in assignments}
    if set(supplied)!=set(original) or len(supplied)!=len(assignments):raise ValueError('Los cambios manuales deben incluir cada registro asignado una sola vez')
    state=row['operational_snapshot'];updated=[];changes=[]
    for rid,item in original.items():
        revised=deepcopy(item);seller_id=supplied[rid]
        if seller_id!=str(item['seller_id']):
            revised.update(seller_id=seller_id,score=0.0,reasons=list(item.get('reasons',[]))+['MANUAL_REVIEW'])
            changes.append({'record_id':rid,'from_seller_id':item['seller_id'],'to_seller_id':seller_id})
        updated.append(revised)
    plan['assignments']=updated;validate_plan(plan,state)
    plan.setdefault('trace',{}).setdefault('manual_adjustments',[]).append({'changes':changes,'validated':True})
    configuration=deepcopy(row['configuration']);configuration['result']=plan
    conn.execute('UPDATE sales_assignment.assignment_previews SET configuration=%s,updated_at=clock_timestamp() WHERE id=%s',(Jsonb(configuration),preview_id))
    for item in updated:
        conn.execute('UPDATE sales_assignment.assignment_preview_items SET proposed_seller_id=%s,decision_trace=jsonb_set(decision_trace,\'{manual_seller_id}\',to_jsonb(%s::text),true) WHERE preview_id=%s AND record_id=%s',
            (item['seller_id'],item['seller_id'],preview_id,item['record_id']))
    return get_preview(conn,preview_id)


def save_preview(conn,request,state_hash,preview):
    state=get_state(conn,request['record_ids'])
    if state['state_hash']!=state_hash:raise Conflict('Data changed while generating the preview; generate another preview')
    if preview.get('method')!=request['method']:raise ValueError('Preview method does not match request')
    all_ids=[str(a['record_id']) for a in preview.get('assignments',[])]+[str(a['record_id']) for a in preview.get('unassigned',[])]
    if len(all_ids)!=len(set(all_ids)) or set(all_ids)!=set(request['record_ids']):raise ValueError('Preview must account for every requested record exactly once')
    validate_plan(preview,state)
    preview_id=uuid.uuid4()
    conn.execute('''INSERT INTO sales_assignment.assignment_previews
        (id,method,status,operational_snapshot,snapshot_hash,configuration)
        VALUES (%s,%s,'draft',%s,%s,%s)''',(preview_id,request['method'],Jsonb(state),state_hash,Jsonb({'model_configuration':request['configuration'],'result':preview})))
    records={r['id']:r for r in state['records']}
    selected={str(a['record_id']):a for a in preview['assignments']}
    skipped={str(a['record_id']):a for a in preview['unassigned']}
    for rid in request['record_ids']:
        item=selected.get(rid);trace=dict(method=preview['method'],state_hash=state_hash,effective_date=state['effective_date'],
            configuration=request['configuration'],decision=item or skipped[rid],
            signals=preview.get('trace',{}).get('effective_signals',{}).get(rid,effective_signals(records[rid])),
            warnings=preview.get('trace',{}).get('warnings',[]),
            eligibility_checks=preview.get('trace',{}).get('mandatory_rules',[]),constraints_revalidated=True,
            policy_version=preview.get('trace',{}).get('policy_version','v1'))
        conn.execute('''INSERT INTO sales_assignment.assignment_preview_items
            (id,preview_id,record_id,proposed_seller_id,eligible,exclusions,decision_trace)
            VALUES (%s,%s,%s,%s,%s,%s,%s)''',(uuid.uuid4(),preview_id,rid,item['seller_id'] if item else None,item is not None,
                Jsonb([] if item else skipped[rid]['reasons']),Jsonb(trace)))
    save_extractions(conn,preview_id,preview,state)
    return get_preview(conn,preview_id)


def save_extractions(conn,preview_id,preview,state):
    rows=preview.get('trace',{}).get('llm_extractions',[])
    if not rows:return
    prompt=(ROOT/'project/llm_service/promps/note_extraction_v1.md').read_text();sha=hashlib.sha256(prompt.encode()).hexdigest()
    existing=conn.execute("SELECT sha256 FROM sales_assignment.prompt_versions WHERE id='note_extraction_v1'").fetchone()
    if existing and existing['sha256']!=sha:raise Conflict('Prompt version checksum mismatch')
    if not existing:
        conn.execute('''INSERT INTO sales_assignment.prompt_versions (id,sha256,content,approved,source_filename,run_id)
          VALUES ('note_extraction_v1',%s,%s,false,'runtime:assignment_api',%s)''',(sha,prompt,state['normalization_run_id']))
    records={r['id']:r for r in state['records']}
    for index,row in enumerate(rows,1):
        record=records[str(row['record_id'])];expected=record.get('signals') or {};actual=row.get('output') or {}
        match=bool(actual) and actual.get('action')==expected.get('action') and actual.get('details',{}).get('assignment_action')==expected.get('details',{}).get('assignment_action')
        conn.execute('''INSERT INTO sales_assignment.llm_extractions
          (prompt_version_id,note_template_id,record_id,model,case_id,model_output,expected_output,json_valid,schema_valid,hard_decision_match,
           latency_seconds,error,source_payload,source_filename,source_row,run_id)
          VALUES ('note_extraction_v1',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
          (record.get('note_template_id'),record['id'],row.get('model'),record['id'],row.get('raw_output',''),Jsonb(expected),
           row.get('json_valid',False),row.get('schema_valid',False),match,row.get('latency'),row.get('error'),Jsonb(row),f'preview:{preview_id}',index,state['normalization_run_id']))


def execute_preview(conn,preview_id):
    # Same lock as reset; table locks also protect against any concurrent direct administrative writes.
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('sales-assignment-reset'))")
    conn.execute('''LOCK TABLE sales_assignment.records,sales_assignment.users,sales_assignment.absences,
        sales_assignment.activities,sales_assignment.historical_ownership,sales_assignment.assignments,
        sales_assignment.record_signals,sales_assignment.duplicate_groups IN SHARE ROW EXCLUSIVE MODE''')
    row=conn.execute('SELECT * FROM sales_assignment.assignment_previews WHERE id=%s FOR UPDATE',(preview_id,)).fetchone()
    if not row:raise NotFound('Preview does not exist')
    if row['status']=='executed':
        items=conn.execute('''SELECT a.* FROM sales_assignment.assignments a JOIN sales_assignment.assignment_preview_items p ON p.id=a.preview_item_id WHERE p.preview_id=%s ORDER BY a.record_id''',(preview_id,)).fetchall()
        return {'preview_id':str(preview_id),'status':'executed','assignments':items,'already_executed':True}
    if row['status'] not in {'draft','approved'}:raise Conflict('Preview cannot be executed in its current state')
    previous=row['operational_snapshot'];state=get_state(conn,[r['id'] for r in previous['records']])
    if state['state_hash']!=row['snapshot_hash']:raise Conflict('Preview is stale; generate a new preview')
    plan=row['configuration']['result'];validate_plan(plan,state)
    if not plan['assignments']:raise Conflict('No assignments to execute')
    items=conn.execute('SELECT * FROM sales_assignment.assignment_preview_items WHERE preview_id=%s AND eligible ORDER BY record_id',(preview_id,)).fetchall()
    assigned=[]
    for item in items:
        assignment_id=uuid.uuid4()
        conn.execute('''INSERT INTO sales_assignment.assignments (id,preview_item_id,record_id,seller_id,decision_trace)
            VALUES (%s,%s,%s,%s,%s)''',(assignment_id,item['id'],item['record_id'],item['proposed_seller_id'],Jsonb(item['decision_trace'])))
        conn.execute("UPDATE sales_assignment.records SET status='asignado',updated_at=now() WHERE id=%s",(item['record_id'],))
        conn.execute('''INSERT INTO sales_assignment.assignment_events (id,assignment_id,event_type,payload)
            VALUES (%s,%s,'assignment_created',%s)''',(uuid.uuid4(),assignment_id,Jsonb({'preview_id':str(preview_id),'record_id':item['record_id'],
                'seller_id':item['proposed_seller_id'],'snapshot_hash':row['snapshot_hash'],'decision_trace':item['decision_trace']})))
        assigned.append({'assignment_id':str(assignment_id),'record_id':item['record_id'],'seller_id':item['proposed_seller_id']})
    conn.execute("UPDATE sales_assignment.assignment_previews SET status='executed',updated_at=now() WHERE id=%s",(preview_id,))
    return {'preview_id':str(preview_id),'status':'executed','assignments':assigned,'already_executed':False}


def explanation(conn,record_id):
    row=conn.execute('''SELECT id AS assignment_id,preview_item_id,decision_trace FROM sales_assignment.assignments
        WHERE record_id=%s AND is_current ORDER BY created_at DESC LIMIT 1''',(record_id,)).fetchone()
    if not row:
        row=conn.execute('''SELECT NULL::uuid AS assignment_id,id AS preview_item_id,decision_trace
            FROM sales_assignment.assignment_preview_items WHERE record_id=%s ORDER BY created_at DESC LIMIT 1''',(record_id,)).fetchone()
    if not row:raise NotFound('No assignment or preview exists for this record')
    decision=row['decision_trace']['decision'];seller=decision.get('seller_id')
    text=(f"Registro {record_id}: vendedor {seller}, método {row['decision_trace']['method']}, puntuación {decision.get('score')}. " if seller else f'Registro {record_id}: sin asignación. ')
    text+='Motivos: '+', '.join(decision.get('reasons',[]))+'.'
    return dict(row,record_id=record_id,seller_id=seller,explanation=text,source='deterministic',official=True)


def store_explanation(conn,record_id,payload):
    original=explanation(conn,record_id)
    supplied=str(payload['assignment_id']) if payload.get('assignment_id') else None
    if supplied!=(str(original['assignment_id']) if original['assignment_id'] else None) or str(payload['preview_item_id'])!=str(original['preview_item_id']):
        raise Conflict('Decision changed while generating the explanation')
    eid=uuid.uuid4()
    conn.execute('''INSERT INTO sales_assignment.llm_explanations
        (id,assignment_id,preview_item_id,model,prompt,decision_trace,response,error)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',(eid,original['assignment_id'],original['preview_item_id'],payload['model'],payload['prompt'],Jsonb(original['decision_trace']),payload.get('response'),payload.get('error')))
    return {'explanation_id':str(eid),'record_id':record_id,'seller_id':original['seller_id'],
            'explanation':payload.get('response') or original['explanation'],'source':'ai' if payload.get('response') else 'deterministic',
            'official':False if payload.get('response') else True,'deterministic_explanation':original['explanation'],'error':payload.get('error')}
