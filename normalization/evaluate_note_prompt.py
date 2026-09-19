"""Real Ollama evaluation; no deterministic lookup substitutes for model output."""
import argparse
import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from .common import OUT, SERVICE, dump_json, read_jsonl, write_csv, generated_output, digest
from .notes import VALIDATOR, SCHEMA, neutral


def request_payload(prompt, example, model):
    context=example['input']
    return {'model':model,'stream':False,'format':SCHEMA,'options':{'temperature':0,'seed':42,'num_ctx':4096,'num_predict':1200,'num_thread':4},'messages':[{'role':'system','content':prompt},{'role':'user','content':json.dumps({k:context.get(k) for k in ('record_id','note','sector')},ensure_ascii=False)}]}


def flatten(value, prefix=''):
    if isinstance(value,dict):
        return {k:v for key,item in value.items() for k,v in flatten(item,prefix+('.' if prefix else '')+key).items()}
    return {prefix:sorted(value) if isinstance(value,list) else value}


def compare(expected, actual):
    expected=flatten(expected);actual=flatten(actual)
    matches=[];mismatches=[]
    for key in sorted(set(expected)|set(actual)):
        if key in expected and key in actual and type(expected[key]) is type(actual[key]) and expected[key]==actual[key]:matches.append(key)
        else:mismatches.append({'field':key,'expected':expected.get(key),'actual':actual.get(key),'missing':key not in actual,'extra':key not in expected})
    return matches,mismatches


def strict_object(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError('Duplicate JSON key')
        result[key]=value
    return result


def reject_constant(value):
    raise ValueError('Non-finite JSON number')


def validate_response(text):
    try: parsed=json.loads(text, object_pairs_hook=strict_object, parse_constant=reject_constant)
    except (ValueError,TypeError):return None,False,False
    return parsed,True,not list(VALIDATOR.iter_errors(parsed))


def unsupported(expected,actual):
    if not isinstance(actual,dict):return ['invalid_output']
    exp=flatten(expected);act=flatten(actual);defaults=flatten(neutral());errors=[]
    for key,value in act.items():
        if key not in exp:errors.append(key)
        elif value!=exp[key]:
            if isinstance(value,list):
                if any(item not in exp[key] for item in value):errors.append(key)
            elif value is not None and value is not False and value!=defaults.get(key):errors.append(key)
    return errors


def evaluate(prompt_path, examples_path, output, api_url, model, timeout):
    if not api_url or not model:raise ValueError('Set LLM_API_URL and LLM_MODEL explicitly')
    output=generated_output(output);examples_path=Path(examples_path);prompt_path=Path(prompt_path)
    examples=read_jsonl(examples_path)
    if not examples:raise ValueError('Empty evaluation set')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    history=OUT/'evaluation_runs'/f'{prompt_path.stem}-{examples_path.stem}-{stamp}'
    history.mkdir(parents=True,exist_ok=False)
    (history/'prompt.md').write_bytes(prompt_path.read_bytes());(history/'examples.jsonl').write_bytes(examples_path.read_bytes());dump_json(history/'schema.json',SCHEMA)
    model_metadata=None
    try:
        with urllib.request.urlopen(api_url.rstrip('/')+'/api/tags',timeout=10) as response:
            catalog=json.load(response)
        model_metadata=next((m for m in catalog.get('models',[]) if m.get('name') in {model,model+':latest'}),None)
    except (urllib.error.URLError,TimeoutError,ValueError):
        pass
    dump_json(history/'runtime.json',{'model':model,'model_metadata':model_metadata,'timeout_seconds':timeout})
    rows=[];prompt=prompt_path.read_text();total_fields=0;total_matches=0;invented=[]
    for index,e in enumerate(examples,1):
        started=time.perf_counter();raw='';error=None;parsed=None;jvalid=svalid=False
        payload=request_payload(prompt,e,model)
        try:
            request=urllib.request.Request(api_url.rstrip('/')+'/api/chat',json.dumps(payload).encode(),{'Content-Type':'application/json'})
            with urllib.request.urlopen(request,timeout=timeout) as response:result=json.load(response)
            raw=result['message']['content'];parsed,jvalid,svalid=validate_response(raw)
            if not result.get('done'):error='Incomplete generation'
            if not svalid:error=error or 'Invalid JSON or schema'
        except (urllib.error.URLError,TimeoutError,KeyError,ValueError) as exc:error=str(exc)
        matches,mismatches=compare(e['expected_output'],parsed if isinstance(parsed,dict) else {})
        hard=isinstance(parsed,dict) and parsed.get('action')==e['expected_output']['action'] and isinstance(parsed.get('details'),dict) and parsed['details'].get('assignment_action')==e['expected_output']['details']['assignment_action']
        facts=unsupported(e['expected_output'],parsed);invented.extend({'case':e.get('case_id',e['note_template_id']),'field':f} for f in facts)
        row=dict(prompt_version=prompt_path.stem,note_template_id=e['note_template_id'],case_id=e.get('case_id',e['note_template_id']),source=e['source'],raw_note=e['raw_note'],expected_output=e['expected_output'],model_output=raw,json_valid=jvalid,schema_valid=svalid,field_matches=matches,field_mismatches=mismatches,hard_decision_match=hard,latency=round(time.perf_counter()-started,3),error=error,unsupported_fields=facts)
        rows.append(row);total_fields+=len(flatten(e['expected_output']));total_matches+=len(matches)
        write_csv(history/'results.csv',rows)
        print(f'{index}/{len(examples)} {row["case_id"]}: schema={svalid}, hard={hard}, mismatches={len(mismatches)}, seconds={row["latency"]}',flush=True)
    def recall(field):
        positive=[r for r in rows if r['expected_output']['details'][field]]
        if not positive:return None
        return sum(bool(validate_response(r['model_output'])[0].get('details',{}).get(field) is True) for r in positive if r['schema_valid'])/len(positive)
    metrics=dict(json_validity=sum(r['json_valid'] for r in rows)/len(rows),schema_validity=sum(r['schema_valid'] for r in rows)/len(rows),do_not_contact_recall=recall('do_not_contact'),possible_duplicate_recall=recall('possible_duplicate'),assignment_action_exact_match=sum(r['hard_decision_match'] for r in rows)/len(rows),field_accuracy=total_matches/total_fields,unsupported_invented_fields=len(invented),errors=sum(r['error'] is not None for r in rows))
    accepted=metrics['json_validity']==metrics['schema_validity']==metrics['assignment_action_exact_match']==1 and metrics['do_not_contact_recall']==metrics['possible_duplicate_recall']==1 and metrics['field_accuracy']>=.95 and not invented and not metrics['errors']
    summary=dict(prompt_version=prompt_path.stem,prompt_sha256=digest(prompt_path),examples_sha256=digest(examples_path),schema_sha256=digest(history/'schema.json'),model=model,model_metadata=model_metadata,api_url=api_url,timeout_seconds=timeout,options=payload['options'],evaluation_set=examples[0]['evaluation_set'],example_count=len(rows),metrics=metrics,acceptance_thresholds_satisfied=accepted,unsupported_details=invented,history=str(history.relative_to(OUT)),interpretation='Development-set performance, not proof of generalization. Synthetic results are separate.',model_limitation=None)
    dump_json(history/'summary.json',summary);write_csv(output,rows);dump_json(output.with_suffix('.summary.json'),summary)
    status_path=OUT/'prompt_review_status.json';status=json.loads(status_path.read_text());status['evaluation_run']=True;status['latest_evaluation']=str(output.relative_to(OUT));dump_json(status_path,status)
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    return summary


def main():
    p=argparse.ArgumentParser();p.add_argument('--prompt',type=Path,default=SERVICE/'promps/note_extraction_v1.md');p.add_argument('--examples',type=Path,default=OUT/'note_evaluation.jsonl');p.add_argument('--output',type=Path,default=OUT/'note_extraction_results.csv');a=p.parse_args()
    summary=evaluate(a.prompt,a.examples,a.output,os.environ.get('LLM_API_URL'),os.environ.get('LLM_MODEL'),float(os.environ.get('LLM_TIMEOUT_SECONDS','600')))
    raise SystemExit(0 if summary['acceptance_thresholds_satisfied'] else 1)
if __name__=='__main__':main()
