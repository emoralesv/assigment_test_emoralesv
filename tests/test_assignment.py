import copy
import unittest
from fastapi.testclient import TestClient
from assigment_test_emoralesv.project.assignment_engine.models import CapacityAware,FuzzyOptimal,AIAssisted
from assigment_test_emoralesv.project.assignment_engine.domain import candidate_exclusions, validate_plan
from assigment_test_emoralesv.project.api.main import create_app

CONFIG={'effective_date':'2026-09-18'}
def record(rid='1',**kw):return dict(id=rid,status='nuevo',zone='norte',sector='tech',notes='',signals={},**kw)
def seller(sid='1',**kw):
    value=dict(id=sid,active=True,role='vendedor',team_id='1',zone='norte',maximum_capacity=2,open_workload=0,tenure_years=5,sector_experience={'tech':5},response_days=1,absences=[])
    value.update(kw);return value
class FailedLLM:
    model='test'
    def extract(self,r):return {'error':'TimeoutError','output':None}

class EngineTests(unittest.TestCase):
    def test_capacity_and_determinism(self):
        records=[record(str(i)) for i in range(5)];sellers=[seller('a'),seller('b')]
        for model in (CapacityAware(),FuzzyOptimal()):
            first=model.preview(records,sellers,CONFIG)
            self.assertEqual(len(first['assignments']),4)
            self.assertEqual(len(first['unassigned']),1)
            self.assertEqual(first,model.preview(list(reversed(records)),list(reversed(sellers)),CONFIG))
            self.assertTrue(all(x['activated_rules'] for x in first['assignments']))
    def test_hard_constraints(self):
        for change,reason in [({'active':False},'SELLER_INACTIVE'),({'role':'admin'},'ROLE_NOT_SELLER'),({'maximum_capacity':None},'CAPACITY_UNDEFINED'),({'maximum_capacity':0},'ZERO_CAPACITY'),({'open_workload':2},'CAPACITY_EXHAUSTED'),({'team_id':None},'INVALID_TEAM'),({'absences':[{'starts_on':'2026-09-18','ends_on':'2026-09-18'}]},'SELLER_ABSENT')]:
            self.assertIn(reason,candidate_exclusions(record(),seller(**change),CONFIG))
    def test_blocked_and_duplicate_never_assigned(self):
        blocked=record();blocked['notes']='No contactar';duplicate=record('2',is_duplicate=True)
        for model in (CapacityAware(),FuzzyOptimal(),AIAssisted(FailedLLM())):
            self.assertEqual(model.preview([blocked,duplicate],[seller()],CONFIG)['assignments'],[])
    def test_fuzzy_global_optimum(self):
        class Scores(FuzzyOptimal):
            def compatibility(self,r,s,c):return ({('1','a'):.9,('1','b'):.8,('2','a'):.85,('2','b'):.1}[(r['id'],s['id'])],{},[{'rule':'fixture'}],[])
        result=Scores().preview([record('1'),record('2')],[seller('a',maximum_capacity=1),seller('b',maximum_capacity=1)],CONFIG)
        self.assertEqual({(x['record_id'],x['seller_id']) for x in result['assignments']},{('1','b'),('2','a')})
    def test_llm_failure_approved_fallback(self):
        r=record(label_source='approved_golden_with_context_rule');r['notes']='Nota revisada'
        result=AIAssisted(FailedLLM()).preview([r],[seller()],CONFIG)
        self.assertEqual(len(result['assignments']),1)
        self.assertEqual(result['trace']['warnings'][0]['code'],'LLM_FAILED_DETERMINISTIC_FALLBACK')
    def test_persistence_revalidates_stricter_ai_constraints(self):
        state={'records':[record()],'sellers':[seller()],'effective_date':CONFIG['effective_date']}
        plan=CapacityAware().preview(state['records'],state['sellers'],CONFIG)
        plan['trace']['effective_signals']={'1':{'details':{'do_not_contact':True}}}
        with self.assertRaises(ValueError):validate_plan(plan,state)

    def test_unknown_note_failure_requires_review(self):
        r=record();r['notes']='Nota nueva'
        self.assertEqual(AIAssisted(FailedLLM()).preview([r],[seller()],CONFIG)['assignments'],[])

class PublicAPITests(unittest.TestCase):
    def test_routes_and_configuration_validation(self):
        class Database:
            def request(self,method,path,body=None):
                if path=='/state':return {'records':[record()],'sellers':[seller()],'effective_date':CONFIG['effective_date'],'state_hash':'a'*64}
                if path=='/previews':return dict(body['preview'],preview_id='test')
                return {'status':'ok'}
        with TestClient(create_app(Database(),FailedLLM())) as client:
            self.assertEqual(len(client.get('/assignment-models').json()['models']),3)
            self.assertEqual(client.post('/assignment-previews',json={'record_ids':[1],'method':'capacity_aware'}).status_code,201)
            for body in [{'record_ids':[1,1],'method':'capacity_aware'},{'record_ids':[1],'method':'invalid'},{'record_ids':[1],'method':'fuzzy_optimal','configuration':{'effective_date':'1900-01-01'}}]:
                self.assertEqual(client.post('/assignment-previews',json=body).status_code,422)
            self.assertEqual(client.post('/assignment-previews/00000000-0000-0000-0000-000000000000/execute',json={'approved':False}).status_code,422)
