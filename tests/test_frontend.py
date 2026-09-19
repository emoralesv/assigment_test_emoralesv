import unittest
from pathlib import Path
import httpx
from assigment_test_emoralesv.project.frontend.streamlit_app.client import APIClient,APIError,PreviewWorkflow

class ClientTests(unittest.TestCase):
    def test_success_and_validation(self):
        def handler(request):
            self.assertEqual(request.url.host,'backend')
            return httpx.Response(422,json={'detail':[{'loc':['body','method'],'msg':'Invalid method'}]})
        client=APIClient('http://backend',httpx.MockTransport(handler))
        with self.assertRaisesRegex(APIError,'body.method: Invalid method'):client.request('POST','/assignment-previews',{})
        client=APIClient('http://backend',httpx.MockTransport(lambda r:httpx.Response(200,json={'ok':True})))
        self.assertTrue(client.request('GET','/health')['ok'])
    def test_timeout_and_invalid_response(self):
        def timeout(request):raise httpx.ReadTimeout('slow')
        for handler,text in [(timeout,'tiempo'),(lambda r:httpx.Response(502,text='Bad gateway'),'no válida')]:
            with self.assertRaisesRegex(APIError,text):APIClient('http://backend',httpx.MockTransport(handler)).request('GET','/health')

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.state={};self.flow=PreviewWorkflow(self.state);self.request={'record_ids':['1'],'method':'capacity_aware'}
        self.flow.store({'preview_id':'p','status':'draft','assignments':[{'record_id':'1'}]},self.request)
        self.calls=[]
        self.client=APIClient('http://backend',httpx.MockTransport(self.handle))
    def handle(self,request):self.calls.append(request);return httpx.Response(200,json={'assignments':[{}]})
    def test_cannot_execute_without_display_and_approval(self):
        with self.assertRaises(APIError):self.flow.execute(self.client,self.request,True)
        self.flow.displayed()
        with self.assertRaises(APIError):self.flow.execute(self.client,self.request,False)
        with self.assertRaises(APIError):self.flow.execute(self.client,{},True)
        self.assertEqual(self.calls,[])
        self.flow.execute(self.client,self.request,True)
        with self.assertRaises(APIError):self.flow.execute(self.client,self.request,True)
        self.assertEqual(len(self.calls),1)
    def test_stale_requires_new_preview(self):
        self.flow.displayed();client=APIClient('http://backend',httpx.MockTransport(lambda r:httpx.Response(409,json={'detail':'stale'})))
        with self.assertRaises(APIError):self.flow.execute(client,self.request,True)
        self.assertTrue(self.state['preview_conflict'])
        with self.assertRaises(APIError):self.flow.execute(self.client,self.request,True)
        self.assertEqual(self.calls,[])

class StreamlitTests(unittest.TestCase):
    def test_record_worklist_starts_a_candidate_batch(self):
        from unittest.mock import patch
        from streamlit.testing.v1 import AppTest
        calls=[]
        row={'id':'1','company_name':'Empresa','city':'Medellín','zone':'Centro','sector':'Industria','status':'nuevo',
             'note_preview':'Solicitó alguien experto.','requirements':[],'ai_status':'pending',
             'heuristic_reasons':['La nota contiene una solicitud o restricción identificable'],'suggestion':None,
             'signal_metadata':{'updated_at':'2026-09-19T00:00:00+00:00'},'signals':{}}
        def request(client,method,path,body=None,params=None):
            if path=='/services':return {}
            if path=='/review-worklist':return {'items':[row],'total':1}
            if path=='/review-analysis-batches':
                calls.append((method,path));return {'queued':1,'workers':1}
            raise AssertionError(path)
        with patch.object(APIClient,'request',request):
            app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'project/frontend/streamlit_app/app.py'))
            app.session_state['navigation']='Registros';app.run()
            self.assertFalse(app.exception)
            self.assertTrue(any(b.label=='Analizar candidatos' for b in app.button))
            next(b for b in app.button if b.label=='Analizar candidatos').click().run()
            self.assertFalse(app.exception);self.assertEqual(calls,[('POST','/review-analysis-batches')])

    def test_preview_display_approval_execution(self):
        from unittest.mock import patch
        from streamlit.testing.v1 import AppTest
        calls=[]
        def request(client,method,path,body=None,params=None):
            calls.append((method,path))
            if path=='/assignment-models':return {'models':[{'id':'capacity_aware'}]}
            if path=='/records':return {'items':[{'id':'1','company_name':'Demo','assignment_exclusions':[]}],'total':1}
            if path=='/assignment-previews':return {'preview_id':'p','status':'draft','assignments':[{'record_id':'1','seller_id':'2'}],'unassigned':[],'excluded_candidates':[],'trace':{}}
            if path.endswith('/execute'):return {'assignments':[{}]}
            return {'metrics':dict(pending_records=1,available_sellers=1,capacity_utilization=0,assignments=0,quality_warnings=0),'warnings':[]}
        with patch.object(APIClient,'request',request):
            app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'project/frontend/streamlit_app/app.py')).run()
            app.sidebar.radio[0].set_value('Asignación').run()
            self.assertTrue(app.button(key='generate').disabled)
            app.multiselect(key='selected_records').set_value(['1']).run()
            app.button(key='generate').click().run()
            self.assertFalse(app.exception)
            self.assertTrue(app.button(key='execute').disabled)
            self.assertTrue(app.dataframe)
            app.checkbox[0].check().run()
            app.button(key='execute').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(sum(path.endswith('/execute') for _,path in calls),1)
            self.assertTrue(app.button(key='execute').disabled)

    def test_simulation_queue_and_expired_job_are_handled(self):
        from unittest.mock import patch
        from streamlit.testing.v1 import AppTest
        from assigment_test_emoralesv.tests.test_simulation import state
        from assigment_test_emoralesv.project.assignment_engine.simulation import project
        baseline=project(state())
        result={'id':'job','complete':False,'baseline':baseline,'effective_date':'2026-09-18',
                'request':{'record_ids':['1','2'],'configuration':{'balance_weight':.2}},
                'results':{m:{'status':'queued'} for m in ('capacity_aware','fuzzy_optimal','ai_assisted')}}
        expired=[False]
        def request(client,method,path,body=None,params=None):
            if path=='/services':return {}
            if path in ('/load-analysis','/historical-load-analysis'):return baseline
            if path=='/simulations/job':
                if expired[0]:raise APIError('missing',404)
                return result
            raise AssertionError(path)
        with patch.object(APIClient,'request',request):
            app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'project/frontend/streamlit_app/app.py'))
            app.session_state['navigation']='Prueba histórica';app.session_state['historical_simulation_id']='job';app.run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertTrue(any('Fuzzy Optimal: en cola' in i.value for i in app.info))
            expired[0]=True;app.run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertTrue(any('Genera una nueva' in w.value for w in app.warning))
            app.run();self.assertFalse(app.exception)

    def test_inspection_question_uses_selected_simulation_and_people(self):
        from unittest.mock import patch
        from streamlit.testing.v1 import AppTest
        from assigment_test_emoralesv.tests.test_simulation import state
        from assigment_test_emoralesv.project.assignment_engine.simulation import project,historical_examples
        from assigment_test_emoralesv.project.assignment_engine.models import CapacityAware
        s=historical_examples(state());baseline=project(s)
        plan=CapacityAware().preview(s['records'],s['sellers'],{'effective_date':s['effective_date']})
        calls=[]
        def request(client,method,path,body=None,params=None):
            if path=='/services':return {}
            if path=='/historical-load-analysis':return baseline
            if path=='/simulations/job':return {'id':'job','complete':True,'baseline':baseline,'effective_date':s['effective_date'],
                'request':{'record_ids':['1','2'],'configuration':{'balance_weight':.85,'amount_weight':.5}},
                'results':{'capacity_aware':{'status':'completed','plan':plan,'projection':project(s,plan)}}}
            if path=='/simulations/job/capacity_aware/explanation':
                calls.append(body)
                # Exceed the old three-second refresh interval.
                import time
                time.sleep(3.2)
                return {'question':body['question'],'explanation':'Ana recibe dos registros.','model':'test','source':'ai','error':None,'deterministic_explanation':'Carga final 2','evidence':{}}
            raise AssertionError(path)
        with patch.object(APIClient,'request',request):
            app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'project/frontend/streamlit_app/app.py'))
            app.session_state['navigation']='Prueba histórica';app.session_state['historical_simulation_id']='job';app.run()
            self.assertFalse(app.exception)
            app.multiselect[0].set_value(['a'])
            app.text_area[0].set_value('¿Por qué Ana tiene 8?')
            next(b for b in app.button if b.label=='Explicar con la inspección').click().run(timeout=15)
            self.assertFalse(app.exception)
            self.assertEqual(calls,[{'question':'¿Por qué Ana tiene 8?','person_ids':['a']}])
            self.assertTrue(any('Ana recibe dos registros.' in m.value for m in app.markdown))
            self.assertEqual(len(app.get('plotly_chart')),6)
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(len(calls),1)
            self.assertEqual(len(app.get('plotly_chart')),6)
            self.assertTrue(any('Ana recibe dos registros.' in m.value for m in app.markdown))
