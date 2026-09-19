import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from assigment_test_emoralesv.normalization import common
from assigment_test_emoralesv.normalization import evaluate_note_prompt as evaluator
from assigment_test_emoralesv.normalization.common import OUT, SERVICE, read_jsonl

class EvaluationTests(unittest.TestCase):
    def test_real_request_contract_metrics_and_preserved_runs(self):
        original=read_jsonl(OUT/'note_evaluation.jsonl')
        examples=[e for e in original if e['note_template_id'] in {'NOTE-014','NOTE-015'}]
        expected={e['raw_note']:e['expected_output'] for e in examples}
        sent=[]
        def urlopen(request,timeout):
            if isinstance(request,str):return io.BytesIO(b'{"models":[]}')
            data=json.loads(request.data);sent.append(data)
            note=json.loads(data['messages'][1]['content'])['note']
            return io.BytesIO(json.dumps({'done':True,'message':{'content':json.dumps(expected[note])}}).encode())
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            common.dump_json(root/'prompt_review_status.json',{'status':'approved'})
            common.dump_jsonl(root/'examples.jsonl',examples)
            with patch.object(common,'OUT',root),patch.object(evaluator,'OUT',root),patch.object(evaluator.urllib.request,'urlopen',side_effect=urlopen),patch('builtins.print'):
                summary=evaluator.evaluate(SERVICE/'promps/note_extraction_v1.md',root/'examples.jsonl',root/'results.csv','http://test.invalid','test-model',10)
                again=evaluator.evaluate(SERVICE/'promps/note_extraction_v1.md',root/'examples.jsonl',root/'results.csv','http://test.invalid','test-model',10)
            self.assertTrue(summary['acceptance_thresholds_satisfied'])
            self.assertNotEqual(summary['history'],again['history'])
            self.assertEqual(len(list((root/'evaluation_runs').iterdir())),2)
        for request in sent:
            runtime=json.loads(request['messages'][1]['content'])
            self.assertEqual(set(runtime),{'record_id','note','sector'})
            self.assertNotIn('expected_output',runtime)

    def test_outputs_cannot_escape_normalized(self):
        with self.assertRaises(ValueError):common.generated_output('/tmp/should-not-write-sales.csv')

if __name__=='__main__':unittest.main()
