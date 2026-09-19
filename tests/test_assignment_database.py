import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import psycopg
from assigment_test_emoralesv.project.database.safety import from_environment
from assigment_test_emoralesv.project.database import importer
from assigment_test_emoralesv.project.database.api import create_app
from assigment_test_emoralesv.project.assignment_engine.models import CapacityAware

ROOT=Path(__file__).resolve().parents[1]
@unittest.skipUnless(os.environ.get('RUN_DATABASE_INTEGRATION_TESTS')=='1','Requires disposable PostgreSQL')
class AssignmentDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.config=from_environment(environ=os.environ)
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        bundle=importer.read_bundle(ROOT/'normalized')
        for name,item in bundle['files'].items():
            target=self.path/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(item['text'])
        with patch('builtins.print'):
            report=importer.reset(self.config,True,self.path)
        self.assertEqual(report['transaction_status'],'committed')
        self.client=TestClient(create_app(self.config,'service-test','admin-test',self.path));self.client.__enter__()
        self.client.headers['Authorization']='Bearer service-test'
    def tearDown(self):
        self.client.__exit__(None,None,None);self.temp.cleanup()
    def preview(self):
        ids=[r['id'] for r in self.client.get('/data/records?limit=200').json()['items'] if r['status']=='nuevo'][:100]
        response=self.client.post('/state',json={'record_ids':ids});self.assertEqual(response.status_code,200,response.text)
        state=response.json();plan=CapacityAware().preview(state['records'],state['sellers'],{'effective_date':state['effective_date']})
        self.assertTrue(plan['assignments'])
        response=self.client.post('/previews',json={'request':{'record_ids':ids,'method':'capacity_aware','configuration':{}},'state_hash':state['state_hash'],'preview':plan})
        self.assertEqual(response.status_code,201,response.text)
        return response.json()
    def test_execute_idempotent_audited_and_explained(self):
        preview=self.preview();url='/previews/'+preview['preview_id']+'/execute'
        response=self.client.post(url,json={'approved':True});self.assertEqual(response.status_code,200,response.text)
        n=len(response.json()['assignments'])
        self.assertEqual(self.client.get('/summary').json()['counts']['assignment_events'],n)
        self.assertTrue(self.client.post(url,json={'approved':True}).json()['already_executed'])
        self.assertEqual(self.client.get('/summary').json()['counts']['assignments'],n)
        rid=preview['assignments'][0]['record_id'];explanation=self.client.get(f'/records/{rid}/assignment-explanation').json()
        self.assertTrue(explanation['decision_trace']['constraints_revalidated'])
        with psycopg.connect(**self.config.connect_args()) as conn:
            with self.assertRaises(psycopg.Error):conn.execute('DELETE FROM sales_assignment.assignment_events')
    def test_stale_rejected_without_writes(self):
        preview=self.preview()
        with psycopg.connect(**self.config.connect_args()) as conn:conn.execute('UPDATE sales_assignment.users SET maximum_capacity=maximum_capacity+1 WHERE maximum_capacity IS NOT NULL')
        response=self.client.post('/previews/'+preview['preview_id']+'/execute',json={'approved':True})
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(self.client.get('/summary').json()['counts']['assignments'],0)
    def test_transaction_rolls_back_mid_batch(self):
        preview=self.preview();self.assertGreater(len(preview['assignments']),1)
        with psycopg.connect(**self.config.connect_args()) as conn:
            conn.execute("""CREATE FUNCTION public.fail_second_assignment() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
                IF (SELECT count(*) FROM sales_assignment.assignments)>0 THEN RAISE EXCEPTION 'test failure'; END IF;
                RETURN NEW; END $$""")
            conn.execute('CREATE TRIGGER fail_batch BEFORE INSERT ON sales_assignment.assignments FOR EACH ROW EXECUTE FUNCTION public.fail_second_assignment()')
        try:
            response=self.client.post('/previews/'+preview['preview_id']+'/execute',json={'approved':True})
            self.assertEqual(response.status_code,503,response.text)
            self.assertEqual(self.client.get('/summary').json()['counts']['assignments'],0)
            self.assertEqual(self.client.get('/summary').json()['counts']['assignment_events'],0)
        finally:
            with psycopg.connect(**self.config.connect_args()) as conn:
                conn.execute('DROP TRIGGER fail_batch ON sales_assignment.assignments');conn.execute('DROP FUNCTION public.fail_second_assignment()')
    def test_auth_and_admin_isolation(self):
        self.assertEqual(self.client.get('/summary',headers={'Authorization':'Bearer wrong'}).status_code,401)
        self.assertEqual(self.client.post('/admin/reset',json={'confirm_reset':True}).status_code,401)
        self.assertEqual(self.client.get('/data/not_a_table').status_code,422)
        self.assertEqual(self.client.get('/data/users?limit=201').status_code,422)

    def test_frontend_reads_and_reviewed_signal_edit(self):
        self.assertEqual(self.client.get('/ui/dashboard').status_code,200)
        self.assertTrue(self.client.get('/ui/sellers').json()['items'])
        page=self.client.get('/ui/records?q=&status=nuevo&limit=10').json()
        self.assertEqual(len(page['items']),10)
        rid=page['items'][0]['id']
        preview=self.preview()
        detail=self.client.get('/ui/records/'+rid).json()
        labels=detail['signals'];labels['needs_review']=not labels['needs_review']
        body={'signals':labels,'reason':'Reviewed in integration test','expected_updated_at':detail['signal_metadata']['updated_at']}
        response=self.client.patch('/ui/records/'+rid+'/signals',json=body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['signal_metadata']['label_source'],'manual_review')
        self.assertEqual(response.json()['notes'],detail['notes'])
        self.assertEqual(self.client.patch('/ui/records/'+rid+'/signals',json=body).status_code,409)
        self.assertEqual(self.client.post('/previews/'+preview['preview_id']+'/execute',json={'approved':True}).status_code,409)
        body['signals']={'bad':'schema'}
        self.assertEqual(self.client.patch('/ui/records/'+rid+'/signals',json=body).status_code,422)
        self.assertEqual(self.client.get('/ui/audit').json()['total'],0)

    def test_simulation_snapshot_readonly_and_real_portfolio(self):
        before=self.client.get('/summary').json()
        snapshot=self.client.get('/ui/historical-simulation-state')
        self.assertEqual(snapshot.status_code,200,snapshot.text)
        state=snapshot.json()
        self.assertEqual(len(state['records']),167)
        people=state['portfolio']['people']
        self.assertEqual(sum(p['open_workload'] for p in people)+state['portfolio']['unattributed']['records'],76)
        self.assertEqual(sum(p['known_amounts']+p['missing_amounts'] for p in people),sum(p['open_workload'] for p in people))
        self.assertEqual(self.client.get('/summary').json(),before)
        single=self.client.post('/ui/simulation-state',json={'record_ids':['1']})
        self.assertEqual(single.status_code,200,single.text)
        from assigment_test_emoralesv.project.assignment_engine.models import CapacityAware
        state=single.json()
        plan=CapacityAware().preview(state['records'],state['sellers'],{'effective_date':state['effective_date']})
        response=self.client.post('/previews',json={'request':{'record_ids':['1'],'method':'capacity_aware','configuration':{}},'state_hash':state['state_hash'],'preview':plan})
        self.assertEqual(response.status_code,201,response.text)
