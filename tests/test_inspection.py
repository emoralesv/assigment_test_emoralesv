import copy
import time
import unittest
from fastapi.testclient import TestClient
from assigment_test_emoralesv.project.api.main import create_app
from assigment_test_emoralesv.project.assignment_engine.inspection import context
from assigment_test_emoralesv.project.assignment_engine.simulation import project
from assigment_test_emoralesv.project.assignment_engine.models import CapacityAware
from assigment_test_emoralesv.tests.test_simulation import state

class InspectionTests(unittest.TestCase):
    def test_context_distinguishes_initial_added_final_and_exclusions(self):
        s=state();s['records'][0]['notes']='secret raw note, must not be sent'
        plan=CapacityAware().preview(s['records'],s['sellers'],{'effective_date':s['effective_date']})
        job={'id':'test','state':s,'request':{'configuration':{}},'results':{'capacity_aware':{'status':'completed','plan':plan,'projection':project(s,plan)}}}
        before=copy.deepcopy(job)
        evidence,text=context(job,'capacity_aware','¿Por qué Ana tiene 8?',['a'])
        self.assertIn('carga inicial 1',text);self.assertIn('nuevas asignaciones 2',text);self.assertIn('carga final 3',text)
        self.assertNotIn('secret raw note',str(evidence))
        self.assertEqual(job,before)
        with self.assertRaises(ValueError):context(job,'capacity_aware','why',['unknown'])

    def test_explanation_endpoint_uses_server_snapshot_and_falls_back(self):
        calls=[];captured=[]
        class DB:
            def request(self,method,path,body=None):calls.append(path);return state()
        class LLM:
            model='test-model';fail=False
            def extract(self,r):raise AssertionError('No notes')
            def explain_inspection(self,evidence):
                captured.append(evidence)
                if self.fail:raise TimeoutError()
                return {'fact_ids':['person:a','method'],'needs_clarification':False}
        llm=LLM()
        with TestClient(create_app(DB(),llm)) as client:
            jid=client.post('/simulations',json={'record_ids':['1','2']}).json()['id']
            for _ in range(100):
                if client.get('/simulations/'+jid).json()['results']['capacity_aware']['status']=='completed':break
                time.sleep(.01)
            url=f'/simulations/{jid}/capacity_aware/explanation'
            body={'question':'¿Por qué Ana tiene 8?','person_ids':['a']}
            response=client.post(url,json=body)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['source'],'ai')
            self.assertEqual(captured[-1]['people'][0]['open_workload'],3)
            llm.fail=True
            response=client.post(url,json=body)
            self.assertEqual(response.json()['source'],'deterministic')
            self.assertIn('carga final 3',response.json()['explanation'])
            self.assertEqual(client.post(url,json=body|{'evidence':{}}).status_code,422)
            self.assertEqual(client.post(url,json=body|{'person_ids':['missing']}).status_code,422)
            self.assertEqual(calls,['/ui/simulation-state'])

    def test_model_cannot_invent_fact_ids_or_replace_counts_with_sales(self):
        from assigment_test_emoralesv.project.assignment_engine.inspection import render_selection
        from jsonschema import ValidationError
        evidence={'facts':[{'id':'person:a','text':'Ana: carga final 3 registros.'},{'id':'method','text':'Menor carga relativa.'},{'id':'definition','text':'Registros, no ventas.'}]}
        with self.assertRaises(ValidationError):render_selection(evidence,{'fact_ids':['Ana vendió millones'],'needs_clarification':False})
        text=render_selection(evidence,{'fact_ids':['method'],'needs_clarification':False})
        self.assertIn('carga final 3 registros',text)
        self.assertIn('no ventas',text)
