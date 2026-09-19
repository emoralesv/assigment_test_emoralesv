import copy
import time
import unittest
from fastapi.testclient import TestClient
from assigment_test_emoralesv.project.api.main import create_app
from assigment_test_emoralesv.project.assignment_engine.simulation import project,historical_examples


def state():
    seller=dict(id='a',name='Ana',role='vendedor',active=True,team_id='1',zone='Norte',expert_segment='pyme',maximum_capacity=4,open_workload=1,assigned_count=1,in_management_count=0,tenure_years=5,sector_experience={'tech':2},response_days=2,absences=[])
    person={**seller,'available':True,'availability_reasons':[],'estimated_amount':100.0,'known_amounts':1,'missing_amounts':0,'inferred_ownership':1}
    records=[dict(id='1',status='nuevo',notes='',signals={},zone='Norte',segment='pyme',sector='tech',estimated_revenue=200.0,assignment_exclusions=[]),dict(id='2',status='nuevo',notes='',signals={},zone='Norte',segment=None,sector='tech',estimated_revenue=None,assignment_exclusions=[])]
    return {'records':records,'sellers':[seller],'effective_date':'2026-09-18','state_hash':'a'*64,'portfolio':{'people':[person],'unattributed':{'records':1,'estimated_amount':0,'missing_amounts':1},'quality_warnings':2}}

class SimulationTests(unittest.TestCase):
    def test_projection_conserves_load_and_missing_amounts(self):
        original=state();before=copy.deepcopy(original)
        plan={'assignments':[{'record_id':'1','seller_id':'a'},{'record_id':'2','seller_id':'a'}],'unassigned':[]}
        result=project(original,plan);person=result['people'][0]
        self.assertEqual(person['open_workload'],3)
        self.assertEqual(person['estimated_amount'],300)
        self.assertEqual(person['missing_amounts'],1)
        self.assertEqual(result['metrics']['amount_comparable_people'],0)
        self.assertIsNone(result['metrics']['amount_dispersion'])
        self.assertIsNone(project(before)['metrics']['amount_dispersion'])
        self.assertEqual(original,before)
        self.assertEqual(result['unattributed']['records'],1)
    def test_historical_copy_preserves_restrictions(self):
        original=state();original['records'][0]['status']='asignado';original['records'][1]['notes']='No contactar'
        result=historical_examples(original)
        self.assertEqual(result['sellers'][0]['open_workload'],0)
        self.assertEqual(result['records'][0]['status'],'nuevo')
        self.assertEqual(original['records'][0]['status'],'asignado')
        self.assertIn('DO_NOT_CONTACT',result['records'][1]['assignment_exclusions'])
        self.assertEqual(project(result)['people'][0]['estimated_amount'],0)
    def test_three_models_share_snapshot_and_cannot_execute_historical(self):
        class DB:
            calls=[]
            def request(self,method,path,body=None):
                self.calls.append((method,path));return state()
        class LLM:
            def extract(self,r):raise AssertionError('No notes in fixture')
        db=DB()
        with TestClient(create_app(db,LLM())) as client:
            response=client.post('/simulations',json={'record_ids':['1','2']});self.assertEqual(response.status_code,202)
            jid=response.json()['id']
            for _ in range(100):
                result=client.get('/simulations/'+jid).json()
                if result['complete']:break
                time.sleep(.01)
            self.assertTrue(result['complete'])
            self.assertEqual(len(result['results']),3)
            self.assertTrue(all(r['status']=='completed' for r in result['results'].values()))
            self.assertTrue(all(r['projection']['people'][0]['open_workload']==3 for r in result['results'].values()))
            self.assertEqual(db.calls,[('POST','/ui/simulation-state')])
            historical=client.post('/historical-simulations',json={}).json()['id']
            response=client.post('/simulations/'+historical+'/capacity_aware/preview')
            self.assertEqual(response.status_code,409)
            self.assertFalse(any(path=='/previews' for _,path in db.calls))

    def test_slow_ai_does_not_block_fuzzy_or_capacity_workers(self):
        from threading import Event
        from assigment_test_emoralesv.project.assignment_engine.simulation import Simulations
        from assigment_test_emoralesv.project.assignment_engine.models import CapacityAware,FuzzyOptimal
        release=Event();started=Event()
        class SlowAI:
            def preview(self,*args):
                started.set();release.wait(10)
                return FuzzyOptimal().preview(*args)
        sims=Simulations({'capacity_aware':CapacityAware(),'fuzzy_optimal':FuzzyOptimal(),'ai_assisted':SlowAI()})
        try:
            first=sims.create(state(),{'record_ids':['1','2'],'configuration':{}})
            self.assertTrue(started.wait(2))
            other=[sims.create(state(),{'record_ids':['1','2'],'configuration':{}}) for _ in range(3)]
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                if all(sims.get(j)['results']['fuzzy_optimal']['status']=='completed' for j in other):break
                time.sleep(.01)
            for jid in other:
                result=sims.get(jid)
                self.assertEqual(result['results']['fuzzy_optimal']['status'],'completed')
                self.assertEqual(result['results']['capacity_aware']['status'],'completed')
                self.assertEqual(result['results']['ai_assisted']['status'],'queued')
                self.assertFalse(result['complete'])
            self.assertEqual(sims.get(first)['results']['ai_assisted']['status'],'running')
        finally:
            release.set();sims.pool.shutdown(wait=True);sims.ai_pool.shutdown(wait=True)

    def test_fuzzy_balances_load_and_known_amounts(self):
        from assigment_test_emoralesv.project.assignment_engine.models import FuzzyOptimal
        from assigment_test_emoralesv.project.assignment_engine.domain import validate_plan
        s=historical_examples(state());s['sellers'][0]['maximum_capacity']=2
        second=copy.deepcopy(s['sellers'][0]);second['id']='b';s['sellers'].append(second)
        template=s['records'][0]
        s['records']=[dict(template,id=str(i),estimated_revenue=value) for i,value in enumerate([90,80,20,10])]
        original=copy.deepcopy(s)
        result=FuzzyOptimal().preview(s['records'],s['sellers'],{'effective_date':s['effective_date'],'balance_weight':.95,'amount_weight':.5})
        self.assertEqual(len(result['assignments']),4);validate_plan(result,s)
        amounts={str(i):v for i,v in enumerate([90,80,20,10])}
        for sid in ('a','b'):
            selected=[a for a in result['assignments'] if a['seller_id']==sid]
            self.assertEqual(len(selected),2)
            self.assertEqual(sum(amounts[a['record_id']] for a in selected),100)
        self.assertEqual(s,original)
        self.assertEqual(result['trace']['optimization']['status'],'optimal')
