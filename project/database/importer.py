"""Atomic, repeatable import of normalized files into an owned local schema."""
import argparse
import csv
import hashlib
import io
import json
import time
import uuid
from collections import Counter
from datetime import datetime, timezone, date
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from jsonschema import Draft202012Validator

from assigment_test_emoralesv.normalization.common import ROOT
from .safety import SCHEMA, SCHEMA_MARKER, SafetyError, validate, from_environment

REQUIRED = ('usuarios.csv', 'equipos.csv', 'registros.csv', 'ausencias.csv', 'actividad.csv',
            'historical_ownership.csv', 'seller_workload.csv', 'data_quality_issues.csv',
            'duplicate_groups.csv', 'note_examples.csv', 'note_examples.jsonl', 'normalization_report.json')
TABLES = ('normalization_runs','import_artifacts','teams','users','records','absences','activities',
          'historical_ownership','seller_workload_snapshots','note_templates','record_signals',
          'data_quality_issues','duplicate_groups','prompt_versions','llm_extractions',
          'assignment_previews','assignment_preview_items','assignments','assignment_events','llm_explanations')
EMPTY_TABLES = ('assignment_previews','assignment_preview_items','assignments','assignment_events','llm_explanations')
EXPECTED = {'teams':5, 'users':18, 'records':167, 'absences':8, 'activities':264}
OPTIONAL = ('note_training.jsonl','note_evaluation.jsonl','note_holdout.jsonl',
            'note_security_evaluation.jsonl','note_context_evaluation.jsonl',
            'note_golden_review_v1.jsonl','prompt_review_status.json','normalization_summary.md',
            'approval_note.md','evaluation_status.md','notebook_validation.json')


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def nullable(value):
    return None if value is None or value == '' else value


def boolean(value):
    if value is None or value == '': return None
    if value is True or value == 'True' or value == 'true': return True
    if value is False or value == 'False' or value == 'false': return False
    raise ValueError('Invalid boolean value in normalized artifact')


def numeric(value):
    value = nullable(value)
    return Decimal(value) if value is not None else None


def norm(row, field):
    return nullable(row.get(field + ('_normalizada' if field == 'zona' else '_normalizado')))


def read_bundle(directory):
    directory = Path(directory).resolve()
    missing = [name for name in REQUIRED if not (directory/name).is_file()]
    if missing: raise ValueError('Missing normalized inputs: ' + ', '.join(missing))
    paths = [directory/name for name in (*REQUIRED,*OPTIONAL) if (directory/name).is_file()]
    paths += sorted(p for p in (directory/'evaluation_runs').rglob('*') if p.is_file() and p.suffix in {'.json','.jsonl','.csv','.md'})
    paths += sorted(directory.glob('note_extraction_results*.csv'))
    paths += sorted(directory.glob('note_extraction_results*.summary.json'))
    content = {}
    for path in paths:
        if not path.resolve().is_relative_to(directory):
            raise ValueError('Normalized input symlink escapes the input directory')
        name = path.relative_to(directory).as_posix()
        raw = path.read_bytes()
        content[name] = {'text':raw.decode('utf-8-sig'), 'sha256':hashlib.sha256(raw).hexdigest()}
    bundle = {'files':content, 'directory':directory}
    for name in REQUIRED:
        if name.endswith('.csv'): bundle[name] = list(csv.DictReader(io.StringIO(content[name]['text'])))
        elif name.endswith('.jsonl'): bundle[name] = [json.loads(line) for line in content[name]['text'].splitlines() if line.strip()]
        else: bundle[name] = json.loads(content[name]['text'])
    for name in ('usuarios.csv','equipos.csv','registros.csv','ausencias.csv','actividad.csv'):
        rows = bundle[name]
        ids = [norm(row,'id') for row in rows]
        if None in ids or len(set(ids)) != len(ids):
            raise ValueError(f'Empty or duplicate primary IDs in {name}; no reset performed')
    templates = bundle['note_examples.jsonl']
    if len({r['note_template_id'] for r in templates}) != len(templates):
        raise ValueError('Duplicate note template IDs')
    validator = Draft202012Validator(json.loads((ROOT/'project/llm_service/schemas/note_extraction_v1.schema.json').read_text()))
    for row in bundle['note_examples.csv']: validator.validate(json.loads(row['structured_labels']))
    for template in templates: validator.validate(template['expected_output'])
    return bundle


def wait_for_database(config, timeout=60):
    deadline = time.monotonic() + timeout
    while True:
        try:
            conn = psycopg.connect(**config.connect_args(), autocommit=True)
            conn.execute('SELECT 1')
            return conn
        except psycopg.OperationalError:
            if time.monotonic() >= deadline:
                raise RuntimeError('PostgreSQL did not become ready within the configured timeout') from None
            time.sleep(min(1, max(0, deadline-time.monotonic())))


def reset_schema(conn):
    existing = conn.execute('''SELECT obj_description(oid, 'pg_namespace'), pg_get_userbyid(nspowner)
                               FROM pg_namespace WHERE nspname=%s''',(SCHEMA,)).fetchone()
    current_user = conn.execute('SELECT current_user').fetchone()[0]
    if existing:
        if existing != (SCHEMA_MARKER, current_user):
            raise SafetyError('Existing schema has no matching project ownership marker and role; refusing reset.')
        actual = {r[0] for r in conn.execute('''SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                                              WHERE n.nspname=%s AND c.relkind IN ('r','p')''',(SCHEMA,))}
        if not actual.issubset(TABLES):
            raise SafetyError('Unknown tables in the project schema; refusing reset.')
        if actual:
            targets = sql.SQL(', ').join(sql.Identifier(SCHEMA, name) for name in sorted(actual))
            # RESTRICT protects dependencies in other schemas. Never use CASCADE.
            conn.execute(sql.SQL('DROP TABLE {} RESTRICT').format(targets))
        conn.execute(sql.SQL('DROP FUNCTION IF EXISTS {}() RESTRICT').format(sql.Identifier(SCHEMA,'prevent_assignment_event_mutation')))
        conn.execute(sql.SQL('DROP SCHEMA {} RESTRICT').format(sql.Identifier(SCHEMA)))
    conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(SCHEMA)))
    conn.execute(sql.SQL('COMMENT ON SCHEMA {} IS {}').format(sql.Identifier(SCHEMA),sql.Literal(SCHEMA_MARKER)))
    conn.execute(sql.SQL('SET LOCAL search_path TO {}, pg_catalog').format(sql.Identifier(SCHEMA)))
    conn.execute((Path(__file__).parent/'schema.sql').read_text(),prepare=False)


class Loader:
    def __init__(self, conn, run_id, report):
        self.conn, self.run_id, self.report = conn, run_id, report

    def insert(self, table, values):
        query = sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(
            sql.Identifier(SCHEMA,table), sql.SQL(',').join(map(sql.Identifier,values)),
            sql.SQL(',').join(sql.Placeholder() for _ in values))
        try:
            self.conn.execute(query,list(values.values()))
        except Exception:
            self.report['rows_rejected_by_table'][table] += 1
            raise
        self.report['rows_attempted_by_table'][table] += 1

    def provenance(self, row, filename):
        return dict(source_payload=Jsonb(row),source_filename=filename,run_id=self.run_id)

    def source(self, table, filename, row, **values):
        self.insert(table,dict(values,**self.provenance(row,filename)))

    def load(self,bundle):
        files = bundle['files']
        normalized = bundle['normalization_report.json']
        self.insert('normalization_runs',dict(id=self.run_id,environment=self.report['environment'],
            effective_date=date.fromisoformat(normalized['effective_date']),status='loading',
            input_checksums=Jsonb(self.report['input_checksums']),normalization_report=Jsonb(normalized)))
        for name,item in files.items():
            self.insert('import_artifacts',dict(run_id=self.run_id,filename=name,sha256=item['sha256'],content=item['text']))
        teams = {norm(r,'id') for r in bundle['equipos.csv']}
        users = {norm(r,'id') for r in bundle['usuarios.csv']}
        for row in bundle['equipos.csv']:
            self.source('teams','equipos.csv',row,id=norm(row,'id'),name=norm(row,'nombre'),zone=norm(row,'zona'),
                        source_leader_id=norm(row,'lider_id'),leader_id=None)
        for row in bundle['usuarios.csv']:
            team = norm(row,'equipo_id')
            if team and team not in teams:
                evidence = [i for i in bundle['data_quality_issues.csv'] if i['table_name']=='usuarios' and
                            i['row_id']==norm(row,'id') and i['field_name']=='equipo_id' and i['issue_type']=='orphan_reference']
                if not evidence: raise ValueError('Invalid user/team relationship lacks a data-quality issue')
                self.report['warnings'].append(f'User {norm(row,"id")}: source team {team} retained; operational team_id is NULL.')
            self.source('users','usuarios.csv',row,id=norm(row,'id'),name=norm(row,'nombre'),email=norm(row,'email'),
                        role=norm(row,'rol'),team_id=team if team in teams else None,source_team_id=team,
                        zone=norm(row,'zona'),expert_segment=norm(row,'segmento_experto'),
                        maximum_capacity=numeric(norm(row,'capacidad_maxima')),joined_on=norm(row,'fecha_ingreso'),active=boolean(norm(row,'activo')))
        for row in bundle['equipos.csv']:
            leader=norm(row,'lider_id')
            if leader and leader not in users: raise ValueError('Invalid team leader reference')
            self.conn.execute('UPDATE sales_assignment.teams SET leader_id=%s WHERE id=%s',(leader,norm(row,'id')))
        for row in bundle['registros.csv']:
            self.source('records','registros.csv',row,id=norm(row,'id'),company_name=norm(row,'razon_social'),
                        nit=norm(row,'nit'),original_nit=nullable(row.get('nit_original')),sector=norm(row,'sector'),
                        employees=numeric(norm(row,'empleados')),estimated_revenue=numeric(norm(row,'ingresos_estimados')),
                        city=norm(row,'ciudad'),zone=norm(row,'zona'),source=norm(row,'fuente'),notes=norm(row,'notas'),
                        status=norm(row,'estado'),source_created_on=norm(row,'fecha_creacion'))
        for row in bundle['ausencias.csv']:
            self.source('absences','ausencias.csv',row,id=norm(row,'id'),user_id=norm(row,'usuario_id'),
                        starts_on=norm(row,'desde'),ends_on=norm(row,'hasta'),reason=norm(row,'motivo'),is_active=boolean(row['absence_is_active']))
        for row in bundle['actividad.csv']:
            self.source('activities','actividad.csv',row,id=norm(row,'id'),user_id=norm(row,'usuario_id'),
                        record_id=norm(row,'registro_id'),activity_type=norm(row,'tipo'),occurred_on=norm(row,'fecha'))
        for row in bundle['historical_ownership.csv']:
            self.source('historical_ownership','historical_ownership.csv',row,record_id=row['record_id'],
                        user_id=nullable(row['historical_owner_id']),ownership_status=row['ownership_status'],
                        was_inferred=boolean(row['was_inferred']),inference_rule=row['inference_rule'],activity_user_ids=Jsonb(json.loads(row['activity_user_ids'])))
        for row in bundle['seller_workload.csv']:
            self.source('seller_workload_snapshots','seller_workload.csv',row,seller_id=row['seller_id'],
                        **{k:numeric(row[k]) for k in ('assigned_count','in_management_count','open_workload','maximum_capacity','remaining_capacity','utilization')},
                        availability_status=row['availability_status'],eligibility_reasons=Jsonb(json.loads(row['eligibility_reasons'])))
        for row in bundle['note_examples.jsonl']:
            self.source('note_templates','note_examples.jsonl',row,id=row['note_template_id'],raw_note=row['raw_note'],
                        source_frequency=row['source_frequency'],expected_output=Jsonb(row['expected_output']),review_status=row['review_status'])
        for row in bundle['note_examples.csv']:
            self.source('record_signals','note_examples.csv',row,record_id=row['record_id'],note_template_id=nullable(row['note_template_id']),
                        has_note=boolean(row['has_note']),raw_note=nullable(row['raw_note']),normalized_note=nullable(row['normalized_note']),
                        structured_labels=Jsonb(json.loads(row['structured_labels'])),label_source=row['label_source'],review_status=row['review_status'])
        for row in bundle['data_quality_issues.csv']:
            values={key:nullable(row[key]) for key in ('table_name','row_id','field_name','issue_type','severity','original_value','normalized_value','message','recommended_action')}
            self.source('data_quality_issues','data_quality_issues.csv',row,id=row['issue_id'],is_blocking=boolean(row['is_blocking']),**values)
        for row in bundle['duplicate_groups.csv']:
            self.source('duplicate_groups','duplicate_groups.csv',row,id=row['group_id'],table_name=row['table_name'],field_name=row['field_name'],
                        normalized_value=nullable(row['normalized_value']),row_ids=Jsonb(json.loads(row['row_ids'])),member_count=int(row['count']))
        self.load_prompt_history(bundle)

    def load_prompt_history(self,bundle):
        files=bundle['files'];prompts={}
        approval=json.loads(files['prompt_review_status.json']['text']) if 'prompt_review_status.json' in files else {}
        def add_prompt(version,content,filename):
            sha=hashlib.sha256(content.encode()).hexdigest()
            if version in prompts:
                if prompts[version]!=sha:raise ValueError('Same prompt version contains different text; preserve distinct versions')
                return
            self.insert('prompt_versions',dict(id=version,sha256=sha,content=content,
                approved=approval.get('status')=='approved' and approval.get('approved_prompt_sha256')==sha,
                source_filename=filename,run_id=self.run_id))
            prompts[version]=sha
        if 'note_training.jsonl' in files:
            for line in files['note_training.jsonl']['text'].splitlines():
                if not line.strip():continue
                item=json.loads(line)
                system=[m['content'] for m in item['messages'] if m['role']=='system']
                if len(system)!=1:raise ValueError('Training example requires one system prompt')
                add_prompt(item['metadata']['prompt_version'],system[0],'note_training.jsonl')
        history=sorted(name for name in files if name.startswith('evaluation_runs/') and name.endswith('/results.csv'))
        if not history:history=sorted(name for name in files if name.startswith('note_extraction_results') and name.endswith('.csv'))
        records={norm(r,'id') for r in bundle['registros.csv']}
        for name in history:
            parent=name.rsplit('/',1)[0] if '/' in name else ''
            prefix=parent+'/' if parent else ''
            prompt_file=prefix+'prompt.md';examples_file=prefix+'examples.jsonl';runtime_file=prefix+'runtime.json'
            examples={}
            if examples_file in files:
                for line in files[examples_file]['text'].splitlines():
                    e=json.loads(line);examples[e.get('case_id',e['note_template_id'])]=e
            model=json.loads(files[runtime_file]['text']).get('model') if runtime_file in files else None
            if model is None:self.report['warnings'].append(f'Model identity unavailable for {name}; stored as NULL.')
            for source_row,row in enumerate(csv.DictReader(io.StringIO(files[name]['text'])),2):
                version=row['prompt_version']
                if prompt_file in files:add_prompt(version,files[prompt_file]['text'],prompt_file)
                if version not in prompts:raise ValueError('Extraction references unavailable prompt version')
                case=row.get('case_id') or row['note_template_id'];context=examples.get(case,{}).get('input',{})
                record=nullable(context.get('record_id'))
                if record is not None and str(record) not in records:raise ValueError('Extraction references missing record')
                self.source('llm_extractions',name,row,prompt_version_id=version,note_template_id=nullable(row['note_template_id']),
                    record_id=str(record) if record is not None else None,model=model,case_id=case,model_output=row['model_output'],
                    expected_output=Jsonb(json.loads(row['expected_output'])),json_valid=boolean(row['json_valid']),schema_valid=boolean(row['schema_valid']),
                    hard_decision_match=boolean(row['hard_decision_match']),latency_seconds=numeric(row['latency']),error=nullable(row['error']),source_row=source_row)
        if history:self.report['warnings'].append('LLM evaluation outputs are historical evidence, not accepted labels or new assignments.')


def verify(conn,bundle,report):
    counts={table:conn.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(SCHEMA,table))).fetchone()[0] for table in TABLES}
    report['rows_loaded_by_table']=counts
    checks={}
    def check(name,observed,expected):
        checks[name]={'observed':observed,'expected':expected,'passed':observed==expected}
    for table,expected in EXPECTED.items():check('count_'+table,counts[table],expected)
    for table,file in [('teams','equipos.csv'),('users','usuarios.csv'),('records','registros.csv'),('absences','ausencias.csv'),('activities','actividad.csv'),('historical_ownership','historical_ownership.csv'),('seller_workload_snapshots','seller_workload.csv'),('record_signals','note_examples.csv'),('data_quality_issues','data_quality_issues.csv'),('duplicate_groups','duplicate_groups.csv')]:
        check('all_rows_'+table,counts[table],len(bundle[file]))
    statuses=dict(conn.execute('SELECT status,count(*) FROM sales_assignment.records GROUP BY status').fetchall())
    for status,expected in {'nuevo':71,'en_gestion':46,'asignado':30,'descartado':20}.items():check('status_'+status,statuses.get(status,0),expected)
    scalar=lambda query:conn.execute(query).fetchone()[0]
    check('inferred_owners',scalar("SELECT count(*) FROM sales_assignment.historical_ownership WHERE user_id IS NOT NULL AND was_inferred"),96)
    check('records_without_owner',scalar('SELECT count(*) FROM sales_assignment.historical_ownership WHERE user_id IS NULL'),71)
    check('duplicate_nit_groups',scalar("SELECT count(*) FROM sales_assignment.duplicate_groups WHERE field_name='nit'"),3)
    check('actual_duplicate_nit_groups',scalar('SELECT count(*) FROM (SELECT nit FROM sales_assignment.records WHERE nit IS NOT NULL GROUP BY nit HAVING count(*)>1) d'),3)
    check('unique_note_templates',counts['note_templates'],15)
    check('note_frequency',scalar('SELECT sum(source_frequency) FROM sales_assignment.note_templates'),140)
    check('missing_notes',scalar('SELECT count(*) FROM sales_assignment.records WHERE notes IS NULL'),27)
    check('signals_without_notes',scalar('SELECT count(*) FROM sales_assignment.record_signals WHERE NOT has_note'),27)
    check('null_record_zones',scalar('SELECT count(*) FROM sales_assignment.records WHERE zone IS NULL'),sum(norm(r,'zona') is None for r in bundle['registros.csv']))
    check('null_record_sectors',scalar('SELECT count(*) FROM sales_assignment.records WHERE sector IS NULL'),6)
    check('null_employee_counts',scalar('SELECT count(*) FROM sales_assignment.records WHERE employees IS NULL'),10)
    check('null_estimated_revenue',scalar('SELECT count(*) FROM sales_assignment.records WHERE estimated_revenue IS NULL'),5)
    check('open_ended_absence',scalar('SELECT count(*) FROM sales_assignment.absences WHERE ends_on IS NULL'),1)
    check('invalid_team_preserved',scalar("SELECT count(*) FROM sales_assignment.users WHERE source_team_id='99' AND team_id IS NULL"),1)
    check('no_false_team_99',scalar("SELECT count(*) FROM sales_assignment.teams WHERE id='99'"),0)
    check('team_99_issue_preserved',scalar("SELECT count(*) FROM sales_assignment.data_quality_issues WHERE table_name='usuarios' AND field_name='equipo_id' AND normalized_value='99' AND issue_type='orphan_reference'"),1)
    check('audit_original_nit',scalar("SELECT count(*) FROM sales_assignment.records WHERE original_nit IS DISTINCT FROM nullif(source_payload->>'nit_original','')"),0)
    check('audit_normalized_nit',scalar("SELECT count(*) FROM sales_assignment.records WHERE nit IS DISTINCT FROM nullif(source_payload->>'nit_normalizado','')"),0)
    check('workload_matches_records',scalar('''SELECT count(*) FROM sales_assignment.seller_workload_snapshots w
        WHERE w.assigned_count <> (SELECT count(*) FROM sales_assignment.records r JOIN sales_assignment.historical_ownership h ON h.record_id=r.id WHERE h.user_id=w.seller_id AND r.status='asignado')
        OR w.in_management_count <> (SELECT count(*) FROM sales_assignment.records r JOIN sales_assignment.historical_ownership h ON h.record_id=r.id WHERE h.user_id=w.seller_id AND r.status='en_gestion')'''),0)
    for table in EMPTY_TABLES:check('empty_'+table,counts[table],0)
    # All FK constraints are live and validated. Anti-joins make the critical relations explicit in the report.
    fk={}
    for table,column,target in [('users','team_id','teams'),('teams','leader_id','users'),('absences','user_id','users'),('activities','user_id','users'),('activities','record_id','records'),('historical_ownership','record_id','records'),('historical_ownership','user_id','users'),('record_signals','record_id','records')]:
        query=sql.SQL('SELECT count(*) FROM {} a LEFT JOIN {} b ON a.{}=b.id WHERE a.{} IS NOT NULL AND b.id IS NULL').format(sql.Identifier(SCHEMA,table),sql.Identifier(SCHEMA,target),sql.Identifier(column),sql.Identifier(column))
        fk[f'{table}.{column}->{target}.id']={'unresolved':conn.execute(query).fetchone()[0]}
    check('all_foreign_keys_validated',scalar("SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname='sales_assignment' AND c.contype='f' AND NOT c.convalidated"),0)
    report['foreign_key_results']=fk;report['business_invariant_results']=checks
    failures=[name for name,result in checks.items() if not result['passed']]
    failures += [name for name,result in fk.items() if result['unresolved']]
    if failures:raise ValueError('Import invariants failed: '+', '.join(failures))


def write_report(directory,report):
    directory=Path(directory);history=directory/'database_import_runs';history.mkdir(exist_ok=True)
    text=json.dumps(report,ensure_ascii=False,indent=2,default=str)+'\n'
    (history/(report['normalization_run_id']+'.json')).write_text(text)
    tmp=directory/'database_import_report.json.tmp';tmp.write_text(text);tmp.replace(directory/'database_import_report.json')


def reset(config, confirm_reset=False, input_directory=ROOT/'normalized', readiness_timeout=60, allow_compose=False):
    validate(config,confirm_reset,allow_compose=allow_compose)  # Never connect or write on invalid safety configuration.
    bundle=read_bundle(input_directory)  # Fail before destructive work when inputs are missing/invalid.
    report=dict(started_at=utcnow(),completed_at=None,environment=config.environment,database_target=config.target(),
                normalization_run_id=str(uuid.uuid4()),input_files=sorted(bundle['files']),
                input_checksums={k:v['sha256'] for k,v in bundle['files'].items()},
                rows_loaded_by_table={t:0 for t in TABLES},rows_attempted_by_table={t:0 for t in TABLES},
                rows_rejected_by_table={t:0 for t in TABLES},warnings=[],blocking_errors=[],foreign_key_results={},
                business_invariant_results={},transaction_status='not_started')
    print('RESET TARGET: '+json.dumps(config.target())+' APP_ENV='+config.environment,flush=True)
    conn=None
    try:
        conn=wait_for_database(config,readiness_timeout)
        with conn.transaction():
            validate(config,confirm_reset,allow_compose=allow_compose)
            actual=conn.execute('SELECT current_database(), current_user, pg_is_in_recovery()').fetchone()
            if actual != (config.name,config.user,False):raise SafetyError('Connected database identity or writable-primary state does not match configuration.')
            conn.execute("SET LOCAL lock_timeout='10s'")
            conn.execute("SET LOCAL statement_timeout='60s'")
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('sales-assignment-reset'))")
            report['transaction_status']='in_progress'
            reset_schema(conn)
            loader=Loader(conn,uuid.UUID(report['normalization_run_id']),report);loader.load(bundle)
            verify(conn,bundle,report)
            conn.execute("UPDATE sales_assignment.normalization_runs SET status='committed', completed_at=now() WHERE id=%s",(report['normalization_run_id'],))
        report['transaction_status']='committed'
    except Exception as exc:
        report['transaction_status']='rolled_back' if report['transaction_status']=='in_progress' else 'not_started'
        report['rows_loaded_by_table']={t:0 for t in TABLES}
        message=str(exc).replace(config.password,'[REDACTED]')
        report['blocking_errors'].append({'type':type(exc).__name__,'message':message})
    finally:
        if conn:conn.close()
        report['completed_at']=utcnow()
        write_report(input_directory,report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-reset',action='store_true')
    parser.add_argument('--env-file',type=Path,default=ROOT/'.env')
    parser.add_argument('--normalized-dir',type=Path,default=ROOT/'normalized')
    parser.add_argument('--readiness-timeout',type=float,default=60)
    args=parser.parse_args()
    try:
        config=from_environment(args.env_file)
        report=reset(config,args.confirm_reset,args.normalized_dir,args.readiness_timeout)
    except (SafetyError,ValueError,FileNotFoundError) as exc:
        print('REFUSED: '+str(exc));raise SystemExit(2)
    print(json.dumps({k:report[k] for k in ('transaction_status','rows_loaded_by_table','warnings','blocking_errors')},ensure_ascii=False,indent=2))
    raise SystemExit(0 if report['transaction_status']=='committed' else 1)

if __name__=='__main__':main()
