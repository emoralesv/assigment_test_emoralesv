"""Read-only projections and bounded, in-memory comparison jobs."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from statistics import pstdev
from threading import Lock
import time
import logging
import uuid
from .domain import candidate_exclusions, validate_plan


def project(state,plan=None):
    rows=deepcopy(state['portfolio']['people'])
    people={r['id']:r for r in rows};records={r['id']:r for r in state['records']}
    assignments=(plan or {}).get('assignments',[])
    zone_known=zone_match=segment_known=segment_match=0
    for item in assignments:
        person=people[item['seller_id']];record=records[item['record_id']]
        person['assigned_count']+=1;person['open_workload']+=1
        if record.get('estimated_revenue') is None:person['missing_amounts']+=1
        else:person['estimated_amount']+=float(record['estimated_revenue']);person['known_amounts']+=1
        if record.get('zone') and person.get('zone'):
            zone_known+=1;zone_match+=record['zone']==person['zone']
        if record.get('segment') and person.get('expert_segment'):
            segment_known+=1;segment_match+=record['segment']==person['expert_segment']
    initial={p['id']:p for p in state['portfolio']['people']}
    for person in rows:
        before=initial[person['id']]
        person['initial_workload']=before['open_workload']
        person['proposed_assignments']=person['open_workload']-before['open_workload']
        cap=person['maximum_capacity']
        person['utilization']=person['open_workload']/cap if cap else None
        person['remaining_capacity']=max(0,cap-person['open_workload']) if cap is not None else None
        person['estimated_amount']=round(person['estimated_amount'],2) if person['known_amounts'] or not person['open_workload'] else None
    # Fixed baseline cohort prevents models improving dispersion by changing who is measured.
    cohort=[p for p in rows if p['available']]
    ratios=[p['utilization'] for p in cohort if p['utilization'] is not None]
    amounts=[p['estimated_amount'] for p in cohort if not p['missing_amounts'] and p['estimated_amount'] is not None]
    amount_ratios=[p['estimated_amount']/p['maximum_capacity'] for p in cohort if p['estimated_amount'] is not None]
    mean_amount=sum(amount_ratios)/len(amount_ratios) if amount_ratios else 0
    metrics=dict(known_amount_relative_cv=pstdev(amount_ratios)/mean_amount if len(amount_ratios)>1 and mean_amount else None,
        missing_amounts=sum(p['missing_amounts'] for p in cohort),assigned=len(assignments),unassigned=len((plan or {}).get('unassigned',[])),
        workload_dispersion=pstdev(ratios) if ratios else None,
        amount_dispersion=pstdev(amounts) if len(amounts)>1 else None,
        amount_comparable_people=len(amounts),balance_cohort=len(cohort),
        capacity_utilization=sum(p['open_workload'] for p in cohort)/sum(p['maximum_capacity'] for p in cohort) if cohort else None,
        zone_compatibility=zone_match/zone_known if zone_known else None,
        zone_observed=zone_known,segment_compatibility=segment_match/segment_known if segment_known else None,
        segment_observed=segment_known,
        mandatory_constraints_respected=validate_plan(plan,state) if plan else True,
        blocked_records=sum(bool(r.get('assignment_exclusions')) for r in state['records']),
        quality_warnings=state['portfolio']['quality_warnings'])
    return {'people':rows,'unattributed':state['portfolio']['unattributed'],'metrics':metrics}


class Simulations:
    def __init__(self,models):
        self.models=models;self.jobs={};self.lock=Lock();self.pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='simulation-fast')
        self.ai_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='simulation-ai')

    def create(self,state,request):
        with self.lock:
            now=time.monotonic()
            self.jobs={k:v for k,v in self.jobs.items() if now-v['created']<3600 or any(x['status'] in ('queued','running') for x in v['results'].values())}
            if len(self.jobs)>=20:raise ValueError('Hay 20 simulaciones retenidas; espera a que caduquen (1 hora).')
            jid=str(uuid.uuid4());job={'id':jid,'created':now,'state':deepcopy(state),'request':deepcopy(request),
                'baseline':project(state),'results':{m:{'status':'queued'} for m in self.models}}
            self.jobs[jid]=job
        for name,model in self.models.items():
            executor=self.ai_pool if name=='ai_assisted' else self.pool
            executor.submit(self.run,jid,name,model)
        return jid

    def run(self,jid,name,model):
        with self.lock:
            job=self.jobs[jid];job['results'][name]={'status':'running'}
            state=deepcopy(job['state']);config=deepcopy(job['request']['configuration'])
        config['effective_date']=state['effective_date']
        try:
            plan=model.preview(state['records'],state['sellers'],config)
            result={'status':'completed','plan':plan,'projection':project(state,plan)}
        except Exception as exc:
            logging.getLogger(__name__).exception('Simulation %s failed for model %s',jid,name)
            result={'status':'failed','error':type(exc).__name__}
        with self.lock:self.jobs[jid]['results'][name]=result

    def get(self,jid,private=False):
        with self.lock:
            if jid not in self.jobs:raise KeyError(jid)
            job=deepcopy(self.jobs[jid])
        if private:return job
        return {k:job[k] for k in ('id','request','baseline','results')}|{'state_hash':job['state']['state_hash'],
            'effective_date':job['state']['effective_date'],'scenario':job['state'].get('scenario','pending'),'complete':all(x['status'] in ('completed','failed') for x in job['results'].values())}


def historical_examples(state):
    """Synthetic replay, never eligible for promotion to a real preview."""
    state=deepcopy(state)
    state['scenario']='historical_examples'
    for seller in state['sellers']:
        seller.update(open_workload=0,assigned_count=0,in_management_count=0,known_estimated_amount=0,missing_amounts=0)
    probe={'status':'nuevo','signals':{},'notes':''}
    sellers={s['id']:s for s in state['sellers']}
    for person in state['portfolio']['people']:
        reasons=candidate_exclusions(probe,sellers[person['id']],{'effective_date':state['effective_date']})
        person.update(open_workload=0,assigned_count=0,in_management_count=0,estimated_amount=0,
                      known_amounts=0,missing_amounts=0,inferred_ownership=0,available=not reasons,availability_reasons=reasons)
    from .domain import record_exclusions
    for record in state['records']:
        record['original_status']=record['status'];record['status']='nuevo'
        record['assignment_exclusions']=record_exclusions(record)
    state['portfolio']['unattributed']={'records':0,'estimated_amount':0,'missing_amounts':0}
    return state
