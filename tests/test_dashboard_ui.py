import unittest
from pathlib import Path
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from assigment_test_emoralesv.project.frontend.streamlit_app.client import APIClient

class DashboardTests(unittest.TestCase):
    def test_charts_and_problem_navigation(self):
        requests=[]
        def request(client,method,path,body=None,params=None):
            if path=='/services':return {}
            if path=='/dashboard':return {'metrics':{'records':150,'ready_records':10,'blocked_records':5,'available_sellers':1,'free_capacity':18},
                'affected':{'affected':120,'blocking':20},'problems':[{'field_name':'ingresos_estimados','blocking':20,'nonblocking':100}],
                'people':[{'name':'Ana','available':True,'maximum_capacity':40,'open_workload':22}],
                'exclusions':[{'reason':'SELLER_ABSENT','people':2}]}
            if path=='/records':requests.append(params);return {'items':[],'total':0}
            raise AssertionError(path)
        with patch.object(APIClient,'request',request):
            app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'project/frontend/streamlit_app/app.py')).run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get('plotly_chart')),2)
            self.assertFalse(app.dataframe)
            self.assertEqual(app.sidebar.radio[0].value,'Resumen')
            self.assertFalse(requests)
