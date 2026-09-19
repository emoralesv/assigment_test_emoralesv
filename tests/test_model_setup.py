import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError,URLError
from assigment_test_emoralesv.project.assignment_engine.model_setup import ModelSetup

class ModelSetupTests(unittest.TestCase):
    def setup_model(self,available,error=None):
        client=SimpleNamespace(base_url='http://ollama',model='requested:1')
        setup=ModelSetup(client);setup.default='default:1'
        setup.lock.acquire()
        with patch.object(setup,'models',return_value=set(available)),patch.object(setup,'pull',side_effect=error) as pull:
            setup.run();calls=[call.args[0] for call in pull.call_args_list]
        return setup,client,calls
    def test_already_downloaded(self):
        setup,client,calls=self.setup_model(['requested:1'])
        self.assertEqual(calls,[]);self.assertEqual(setup.state['status'],'ready')
    def test_missing_model_downloaded(self):
        setup,client,calls=self.setup_model([])
        self.assertEqual(calls,['requested:1']);self.assertEqual(client.model,'requested:1')
        self.assertEqual(setup.state['status'],'ready')
    def test_nonexistent_model_downloads_default(self):
        error=HTTPError('http://ollama/api/pull',404,'not found',{},None)
        setup,client,calls=self.setup_model([],[error,None])
        self.assertEqual(calls,['requested:1','default:1']);self.assertEqual(client.model,'default:1')
        self.assertEqual(setup.state['status'],'ready')
    def test_connectivity_failure_does_not_change_model(self):
        setup,client,calls=self.setup_model([],URLError('offline'))
        self.assertEqual(calls,['requested:1']);self.assertEqual(client.model,'requested:1')
        self.assertEqual(setup.state['status'],'failed')
    def test_existing_default_is_reused(self):
        error=HTTPError('http://ollama/api/pull',404,'not found',{},None)
        setup,client,calls=self.setup_model(['default:1'],error)
        self.assertEqual(calls,['requested:1']);self.assertEqual(client.model,'default:1')

    def test_streamed_manifest_not_found_uses_default(self):
        import io
        setup=ModelSetup(SimpleNamespace(base_url='http://ollama',model='missing:1'))
        setup.default='default:1';setup.lock.acquire()
        with patch.object(setup,'models',return_value={'default:1'}),patch('urllib.request.urlopen',return_value=io.BytesIO(b'{"error":"pull model manifest: file does not exist"}\n')):
            setup.run()
        self.assertEqual(setup.client.model,'default:1')
        self.assertEqual(setup.state['status'],'ready')
