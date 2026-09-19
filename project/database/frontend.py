"""Read models and reviewed signal edits for the demo UI."""
from copy import deepcopy
from datetime import datetime, timezone
import re
import uuid
from psycopg.types.json import Jsonb
from normalization.notes import VALIDATOR
from project.assignment_engine.domain import candidate_exclusions, record_exclusions
from .workflows import get_state, NotFound, Conflict

TECHNICAL_SKILL_CATALOG={
    'energy_efficiency':'Eficiencia energética',
    'industrial_automation':'Automatización industrial',
    'process_optimization':'Optimización de procesos',
    'food_safety':'Inocuidad alimentaria',
    'logistics_operations':'Operaciones logísticas',
    'textile_processes':'Procesos textiles',
    'chemical_processes':'Procesos químicos',
    'mining_operations':'Operaciones mineras',
    'construction_projects':'Proyectos de construcción',
    'healthcare_operations':'Operaciones de salud',
    'retail_operations':'Operaciones de retail',
    'cloud_software':'Software y servicios en la nube',
}


def sellers(conn):
    state=get_state(conn,[])
    probe={'status':'nuevo','signals':{},'notes':''}
    items=[]
    for seller in state['sellers']:
        if seller['role']!='vendedor':continue
        reasons=candidate_exclusions(probe,seller,{'effective_date':state['effective_date']})
        seller.update(available=not reasons,availability_reasons=reasons,
                      utilization=seller['open_workload']/seller['maximum_capacity'] if seller['maximum_capacity'] else None)
        items.append(seller)
    teams=conn.execute('SELECT id,name FROM sales_assignment.teams ORDER BY name').fetchall()
    zones=[row['zone'] for row in conn.execute("""SELECT DISTINCT zone FROM (
        SELECT zone FROM sales_assignment.users UNION SELECT zone FROM sales_assignment.teams
        UNION SELECT zone FROM sales_assignment.records) zones WHERE zone IS NOT NULL ORDER BY zone""")]
    return {'items':items,'teams':teams,'zones':zones,'technical_skill_catalog':TECHNICAL_SKILL_CATALOG,
            'effective_date':state['effective_date']}

def edit_technical_skills(conn,seller_id,body):
    seller=conn.execute("SELECT id,role FROM sales_assignment.users WHERE id=%s FOR UPDATE",(seller_id,)).fetchone()
    if not seller:raise NotFound('Seller does not exist')
    if seller['role']!='vendedor':raise ValueError('Solo se pueden registrar habilidades para vendedores')
    cleaned=[];seen=set()
    for entry in body['skills']:
        skill=' '.join(str(entry.get('skill','')).split()).casefold()
        evidence=' '.join(str(entry.get('evidence','')).split())
        if not skill or not evidence:raise ValueError('Cada habilidad debe incluir nombre y evidencia de verificación')
        if len(skill)>100 or len(evidence)>2000:raise ValueError('La habilidad o su evidencia es demasiado larga')
        if skill in seen:raise ValueError('No repitas una habilidad')
        seen.add(skill);cleaned.append((seller_id,skill,evidence))
    conn.execute('DELETE FROM sales_assignment.user_technical_skills WHERE user_id=%s',(seller_id,))
    if cleaned:
        conn.executemany('INSERT INTO sales_assignment.user_technical_skills (user_id,skill,verification_evidence) VALUES (%s,%s,%s)',cleaned)
    conn.execute("UPDATE sales_assignment.users SET source_payload=jsonb_set(source_payload,'{technical_skill_reviews}',COALESCE(source_payload->'technical_skill_reviews','[]'::jsonb)||%s::jsonb),updated_at=clock_timestamp() WHERE id=%s",
        (Jsonb([{'at':datetime.now(timezone.utc).isoformat(),'reason':body['reason'],'skills':[{'skill':s,'evidence':e} for _,s,e in cleaned]}]),seller_id))
    return next(item for item in sellers(conn)['items'] if item['id']==seller_id)

def edit_seller_eligibility(conn,seller_id,body):
    seller=conn.execute("SELECT id,role FROM sales_assignment.users WHERE id=%s FOR UPDATE",(seller_id,)).fetchone()
    if not seller:raise NotFound('Seller does not exist')
    if seller['role']!='vendedor':raise ValueError('Solo se pueden modificar condiciones operativas de vendedores')
    if body['team_id'] is not None and not conn.execute('SELECT 1 FROM sales_assignment.teams WHERE id=%s',(body['team_id'],)).fetchone():
        raise ValueError('El equipo seleccionado no existe')
    zone=' '.join((body['zone'] or '').split()) or None
    conn.execute('''INSERT INTO sales_assignment.seller_operational_overrides
        (user_id,active,team_id,zone,maximum_capacity,availability_override,reason,updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,clock_timestamp())
        ON CONFLICT (user_id) DO UPDATE SET active=EXCLUDED.active,team_id=EXCLUDED.team_id,zone=EXCLUDED.zone,
        maximum_capacity=EXCLUDED.maximum_capacity,availability_override=EXCLUDED.availability_override,
        reason=EXCLUDED.reason,updated_at=clock_timestamp()''',(seller_id,body['active'],body['team_id'],zone,body['maximum_capacity'],body['availability_override'],body['reason']))
    return next(item for item in sellers(conn)['items'] if item['id']==seller_id)

KEYWORDS=re.compile(r'\b(experto|experiencia|senior|especialista|sector|técnic\w*|no contactar|duplicad\w*)\b',re.I)

def _heuristic(record):
    details=(record.get('signals') or {}).get('details',{})
    note=record.get('notes') or ''
    reasons=[]
    if note and KEYWORDS.search(note):reasons.append('La nota contiene una solicitud o restricción identificable')
    if details.get('requires_manual_review') or (record.get('signals') or {}).get('needs_review'):
        reasons.append('El registro ya requiere revisión manual')
    if details.get('sector_expertise_requested') and not record.get('sector'):
        reasons.append('Solicita experiencia sectorial, pero falta el sector')
    if record.get('status')=='nuevo' and (not record.get('sector') or not record.get('zone')):
        reasons.append('Registro nuevo con datos operativos incompletos')
    return reasons

def _tags(record):
    details=(record.get('signals') or {}).get('details',{})
    return [label for enabled,label in ((details.get('sector_expertise_requested'),'Sector'),
        (details.get('seniority_requested'),'Senior'),(bool(details.get('technical_expertise')),'Técnico'),
        (details.get('requires_manual_review') or (record.get('signals') or {}).get('needs_review'),'Revisión')) if enabled]

def review_worklist(conn):
    ids=[row['id'] for row in conn.execute("SELECT id FROM sales_assignment.records WHERE status='nuevo' ORDER BY id")]
    state=get_state(conn,ids)
    suggestions={row['record_id']:row for row in conn.execute('SELECT * FROM sales_assignment.record_ai_suggestions')}
    rows=[]
    for record in state['records']:
        suggestion=suggestions.get(record['id']);reasons=_heuristic(record)
        ai_status=suggestion['status'] if suggestion else ('pending' if reasons else 'not_applicable')
        rows.append({'id':record['id'],'company_name':record.get('company_name') or 'Sin nombre','status':record['status'],
            'city':record.get('city'),'zone':record.get('zone'),'sector':record.get('sector'),
            'note_preview':(record.get('notes') or 'Sin nota')[:120], 'requirements':_tags(record),
            'ai_status':ai_status,'heuristic_reasons':reasons,'suggestion':dict(suggestion) if suggestion else None,
            'signal_metadata':{'updated_at':record.get('signal_updated_at')},'signals':record.get('signals') or {}})
    return {'items':rows,'total':len(rows)}

def queue_suggestions(conn,record_ids):
    rows=review_worklist(conn)['items'];allowed={row['id']:row for row in rows if row['heuristic_reasons']}
    missing=set(record_ids)-set(allowed)
    if missing:raise ValueError('Solo se pueden analizar registros seleccionados por la heurística')
    queued=[]
    for rid in record_ids:
        current=allowed[rid].get('suggestion')
        if current and current['status'] in ('proposed','accepted'):continue
        conn.execute("""INSERT INTO sales_assignment.record_ai_suggestions (record_id,status,heuristic_reasons,proposed_signals,model,error,updated_at)
            VALUES (%s,'pending',%s,NULL,NULL,NULL,clock_timestamp())
            ON CONFLICT (record_id) DO UPDATE SET status='pending',heuristic_reasons=EXCLUDED.heuristic_reasons,
            proposed_signals=NULL,model=NULL,error=NULL,updated_at=clock_timestamp()""",(rid,Jsonb(allowed[rid]['heuristic_reasons'])))
        queued.append(rid)
    return {'record_ids':queued}

def update_suggestion(conn,rid,body):
    if body['status'] not in ('running','proposed','rejected','error'):raise ValueError('Estado de sugerencia no válido')
    if not conn.execute('SELECT 1 FROM sales_assignment.record_ai_suggestions WHERE record_id=%s',(rid,)).fetchone():raise NotFound('Suggestion does not exist')
    conn.execute('''UPDATE sales_assignment.record_ai_suggestions SET status=%s,proposed_signals=%s,model=%s,error=%s,
        updated_at=clock_timestamp() WHERE record_id=%s''',(body['status'],Jsonb(body.get('signals')) if body.get('signals') else None,body.get('model'),body.get('error'),rid))
    return {'record_id':rid,'status':body['status']}

def accept_suggestion(conn,rid,body):
    suggestion=conn.execute('SELECT proposed_signals FROM sales_assignment.record_ai_suggestions WHERE record_id=%s FOR UPDATE',(rid,)).fetchone()
    if not suggestion or not suggestion['proposed_signals']:raise ValueError('No hay una propuesta de IA para aceptar')
    edit_signals(conn,rid,{'signals':body.get('signals') or suggestion['proposed_signals'],'assignment_note':body.get('assignment_note'),
        'expected_updated_at':body['expected_updated_at'],'reason':body['reason']})
    conn.execute("UPDATE sales_assignment.record_ai_suggestions SET status='accepted',updated_at=clock_timestamp() WHERE record_id=%s",(rid,))
    return record_detail(conn,rid)


def records(conn,q='',status='',offset=0,limit=50,problem_field=''):
    where="(%s='' OR concat_ws(' ',id,company_name,nit,city,sector) ILIKE %s) AND (%s='' OR status=%s)"
    params=(q,'%'+q+'%',status,status)
    if problem_field:
        where+=" AND EXISTS (SELECT 1 FROM sales_assignment.data_quality_issues i WHERE i.table_name IN ('registros','records') AND i.row_id=sales_assignment.records.id AND i.field_name=%s)"
        params+= (problem_field,)
    total=conn.execute('SELECT count(*) AS n FROM sales_assignment.records WHERE '+where,params).fetchone()['n']
    ids=[r['id'] for r in conn.execute('SELECT id FROM sales_assignment.records WHERE '+where+' ORDER BY id LIMIT %s OFFSET %s',(*params,limit,offset))]
    state=get_state(conn,ids)
    for record in state['records']:record['assignment_exclusions']=record_exclusions(record)
    return {'items':state['records'],'total':total,'offset':offset,'limit':limit}


def record_detail(conn,rid):
    state=get_state(conn,[rid]);record=state['records'][0]
    record['signal_metadata']=conn.execute('SELECT updated_at,label_source,review_status FROM sales_assignment.record_signals WHERE record_id=%s',(rid,)).fetchone()
    record['warnings']=conn.execute("SELECT * FROM sales_assignment.data_quality_issues WHERE table_name IN ('registros','records') AND row_id=%s ORDER BY id",(rid,)).fetchall()
    record['assignment_exclusions']=record_exclusions(record)
    return record


def edit_signals(conn,rid,body):
    from jsonschema import ValidationError
    try:VALIDATOR.validate(body['signals'])
    except ValidationError as exc:raise ValueError('Invalid signals: '+exc.message) from None
    labels=body['signals']
    if labels['action']!=labels['details']['assignment_action']:raise ValueError('action and details.assignment_action must match')
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('sales-assignment-reset'))")
    row=conn.execute('SELECT * FROM sales_assignment.record_signals WHERE record_id=%s FOR UPDATE',(rid,)).fetchone()
    if not row:raise NotFound('Record signals do not exist')
    if row['updated_at']!=body['expected_updated_at']:raise Conflict('Signals changed; reload the record before saving')
    assignment_note=body.get('assignment_note')
    if assignment_note is not None:
        assignment_note=' '.join(assignment_note.split()) or None
    payload=deepcopy(row['source_payload'])
    payload.setdefault('_signal_edits',[]).append({'id':str(uuid.uuid4()),'at':datetime.now(timezone.utc).isoformat(),
        'reason':body['reason'],'before':row['structured_labels'],'after':labels,
        'assignment_note_before':row.get('assignment_note'),'assignment_note_after':assignment_note})
    conn.execute("""UPDATE sales_assignment.record_signals SET structured_labels=%s,label_source='manual_review',
        assignment_note=%s,review_status='reviewed',source_payload=%s,updated_at=clock_timestamp() WHERE record_id=%s""",
        (Jsonb(labels),assignment_note,Jsonb(payload),rid))
    return record_detail(conn,rid)


def dashboard(conn):
    state=load_snapshot(conn)
    from collections import Counter
    profiles=state['portfolio']['people']
    eligible=[p for p in profiles if p['available']]
    capacity=sum(p['maximum_capacity'] for p in eligible)
    pending=[r for r in state['records'] if r['status']=='nuevo']
    ready=sum(any(not candidate_exclusions(r,s,{'effective_date':state['effective_date']}) for s in state['sellers']) for r in pending)
    problems=conn.execute("""SELECT field_name,
        count(*) FILTER(WHERE blocking) AS blocking,
        count(*) FILTER(WHERE NOT blocking) AS nonblocking
        FROM (SELECT i.field_name,i.row_id,bool_or(i.is_blocking) AS blocking
        FROM sales_assignment.data_quality_issues i JOIN sales_assignment.records r ON r.id=i.row_id
        WHERE i.table_name IN ('registros','records') GROUP BY i.field_name,i.row_id) affected
        GROUP BY field_name ORDER BY count(*) DESC,field_name""").fetchall()
    affected=conn.execute("""SELECT count(*) AS affected,count(*) FILTER(WHERE blocking) AS blocking
        FROM (SELECT i.row_id,bool_or(i.is_blocking) AS blocking
        FROM sales_assignment.data_quality_issues i JOIN sales_assignment.records r ON r.id=i.row_id
        WHERE i.table_name IN ('registros','records') GROUP BY i.row_id) affected""").fetchone()
    reasons=Counter(reason for p in profiles if not p['available'] for reason in p['availability_reasons'])
    return {'metrics':{'pending_records':len(pending),'ready_records':ready,'blocked_records':len(pending)-ready,
        'available_sellers':len(eligible),'free_capacity':sum(max(0,p['maximum_capacity']-p['open_workload']) for p in eligible),
        'capacity_utilization':sum(p['open_workload'] for p in eligible)/capacity if capacity else None,
        'records':len(state['records'])},'problems':problems,'affected':affected,'people':profiles,
        'exclusions':[{'reason':k,'people':v} for k,v in reasons.most_common()]}


def audit(conn,offset=0,limit=50):
    assignments=conn.execute('''SELECT a.id,a.record_id,a.seller_id,a.created_at,a.is_current,
        r.company_name,r.city,r.zone,r.status,u.name AS seller_name
        FROM sales_assignment.assignments a
        JOIN sales_assignment.records r ON r.id=a.record_id
        JOIN sales_assignment.users u ON u.id=a.seller_id
        ORDER BY a.created_at DESC,a.id LIMIT %s OFFSET %s''',(limit,offset)).fetchall()
    events=conn.execute('''SELECT e.id,e.assignment_id,e.event_type,e.created_at,e.payload,
        r.company_name,u.name AS seller_name
        FROM sales_assignment.assignment_events e
        LEFT JOIN sales_assignment.assignments a ON a.id=e.assignment_id
        LEFT JOIN sales_assignment.records r ON r.id=a.record_id
        LEFT JOIN sales_assignment.users u ON u.id=a.seller_id
        ORDER BY e.created_at DESC,e.id LIMIT %s OFFSET %s''',(limit,offset)).fetchall()
    total=conn.execute('SELECT count(*) AS n FROM sales_assignment.assignments').fetchone()['n']
    event_total=conn.execute('SELECT count(*) AS n FROM sales_assignment.assignment_events').fetchone()['n']
    return {'assignments':assignments,'events':events,'total':total,'event_total':event_total}


def load_snapshot(conn,record_ids=None):
    """One PostgreSQL MVCC snapshot for profiles, batch and active ownership."""
    conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    if record_ids is None:
        record_ids=[r['id'] for r in conn.execute('SELECT id FROM sales_assignment.records ORDER BY id')]
    state=get_state(conn,record_ids)
    active=conn.execute('''SELECT r.id,r.estimated_revenue,COALESCE(a.seller_id,h.user_id) AS owner,
        (a.id IS NULL AND h.user_id IS NOT NULL) AS inferred
        FROM sales_assignment.records r
        LEFT JOIN sales_assignment.assignments a ON a.record_id=r.id AND a.is_current
        LEFT JOIN sales_assignment.historical_ownership h ON h.record_id=r.id
        WHERE r.status IN ('asignado','en_gestion') ORDER BY r.id''').fetchall()
    people=[]
    for seller in state['sellers']:
        owned=[r for r in active if r['owner']==seller['id']]
        reasons=candidate_exclusions({'status':'nuevo','signals':{},'notes':''},seller,{'effective_date':state['effective_date']})
        people.append({k:seller.get(k) for k in ('id','name','role','team_id','zone','expert_segment','maximum_capacity','assigned_count','in_management_count','open_workload')}|
            {'available':not reasons,'availability_reasons':reasons,'inferred_ownership':sum(r['inferred'] for r in owned),
             'estimated_amount':float(sum((r['estimated_revenue'] for r in owned if r['estimated_revenue'] is not None),0)),
             'known_amounts':sum(r['estimated_revenue'] is not None for r in owned),
             'missing_amounts':sum(r['estimated_revenue'] is None for r in owned)})
    unknown=[r for r in active if not r['owner']]
    state['portfolio']={'people':people,'unattributed':{'records':len(unknown),
        'estimated_amount':float(sum((r['estimated_revenue'] for r in unknown if r['estimated_revenue'] is not None),0)),
        'missing_amounts':sum(r['estimated_revenue'] is None for r in unknown)},
        'quality_warnings':conn.execute('SELECT count(*) AS n FROM sales_assignment.data_quality_issues').fetchone()['n']}
    for record in state['records']:record['assignment_exclusions']=record_exclusions(record)
    return state
