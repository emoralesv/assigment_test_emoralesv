"""Private persistence for externally submitted commercial records."""
import uuid
from psycopg.types.json import Jsonb
from normalization.notes import neutral, normalize_note

def create_external_record(conn, body):
    existing=conn.execute('SELECT record_id,status FROM sales_assignment.external_record_submissions WHERE integration_id=%s AND external_reference=%s',
        (body['integration_id'],body['external_reference'])).fetchone()
    if existing:return {'record_id':existing['record_id'],'status':existing['status'],'duplicate':True}
    run=conn.execute("SELECT id FROM sales_assignment.normalization_runs ORDER BY completed_at DESC NULLS LAST, started_at DESC LIMIT 1").fetchone()
    if not run:raise ValueError('La base de datos aún no está inicializada')
    rid='external-'+str(uuid.uuid4());note=normalize_note(body.get('notes'))
    payload={'origin':'external_portal','integration_id':body['integration_id'],'external_reference':body['external_reference'],'submitted_payload':body}
    conn.execute('''INSERT INTO sales_assignment.records
        (id,company_name,nit,sector,estimated_revenue,city,zone,source,notes,status,source_payload,source_filename,run_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,'portal_externo',%s,'nuevo',%s,'external_portal',%s)''',
        (rid,body['company_name'],body.get('nit'),body.get('sector'),body.get('estimated_revenue'),body.get('city'),body.get('zone'),note,Jsonb(payload),run['id']))
    conn.execute('''INSERT INTO sales_assignment.record_signals
        (record_id,note_template_id,has_note,raw_note,normalized_note,structured_labels,assignment_note,label_source,review_status,source_payload,source_filename,run_id)
        VALUES (%s,NULL,false,%s,%s,%s,NULL,'external_submission','pending_review',%s,'external_portal',%s)''',
        (rid,note,note,Jsonb(neutral()),Jsonb(payload),run['id']))
    conn.execute('''INSERT INTO sales_assignment.external_record_submissions
        (id,integration_id,external_reference,record_id,status,payload) VALUES (%s,%s,%s,%s,'received',%s)''',
        (uuid.uuid4(),body['integration_id'],body['external_reference'],rid,Jsonb(payload)))
    return {'record_id':rid,'status':'received','duplicate':False}
