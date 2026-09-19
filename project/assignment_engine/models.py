from copy import deepcopy
import math
import numpy as np
from scipy.optimize import linear_sum_assignment
import skfuzzy
from .domain import candidate_exclusions, record_exclusions, effective_signals

DEFAULT_WEIGHTS=dict(zone=.2,segment=.1,seniority=.15,sector_experience=.15,response_speed=.1,complexity=.15,capacity=.15)


def output(method,configuration):
    return dict(method=method,assignments=[],unassigned=[],excluded_candidates=[],trace={
        'configuration':configuration,'policy_version':'v1','effective_date':configuration['effective_date'],
        'mandatory_rules':['active seller','vendedor role','valid team and zone','inclusive absences','defined positive capacity','new record','no duplicate or do-not-contact','verified requested skills'],
        'record_traces':{},'warnings':[]})


def assignment(record,seller,score,reasons,factors=None,rules=None,warnings=None):
    return dict(record_id=record['id'],seller_id=seller['id'],score=round(float(score),6),reasons=reasons,
                warnings=warnings or [],factor_scores=factors or {},activated_rules=rules or [])


class CapacityAware:
    method='capacity_aware'

    def preview(self,records,sellers,configuration):
        result=output(self.method,configuration);used={s['id']:0 for s in sellers}
        for record in sorted(records,key=lambda r:r['id']):
            candidates=[]
            for seller in sorted(sellers,key=lambda s:s['id']):
                reasons=candidate_exclusions(record,seller,configuration,used[seller['id']])
                if reasons:result['excluded_candidates'].append(dict(record_id=record['id'],seller_id=seller['id'],reasons=reasons))
                else:candidates.append(seller)
            if not candidates:
                result['unassigned'].append(dict(record_id=record['id'],reasons=record_exclusions(record) or ['NO_ELIGIBLE_CAPACITY']))
                continue
            seller=min(candidates,key=lambda s:((s['open_workload']+used[s['id']])/s['maximum_capacity'],-(s['maximum_capacity']-s['open_workload']-used[s['id']]),s['id']))
            workload=seller['open_workload']+used[seller['id']];utilization=workload/seller['maximum_capacity']
            item=assignment(record,seller,1-utilization,['LOWEST_RELATIVE_WORKLOAD','CAPACITY_AVAILABLE'],
                            dict(utilization_before=utilization,open_workload_before=workload,maximum_capacity=seller['maximum_capacity']),
                            [dict(rule='choose_minimum_relative_workload',strength=1)])
            result['assignments'].append(item);used[seller['id']]+=1
            result['trace']['record_traces'][record['id']]=item
        result['trace']['projected_additions']=used
        return result


class FuzzyOptimal:
    method='fuzzy_optimal'

    def compatibility(self,record,seller,configuration):
        warnings=[]
        def missing(factor):warnings.append('UNKNOWN_'+factor.upper());return .5
        zone=1.0 if record.get('zone') and record['zone']==seller.get('zone') else 0.0 if record.get('zone') and seller.get('zone') else missing('zone')
        segment=1.0 if record.get('segment') and record['segment']==seller.get('expert_segment') else 0.0 if record.get('segment') and seller.get('expert_segment') else missing('segment')
        seniority=min(seller['tenure_years']/5,1) if seller.get('tenure_years') is not None else missing('seniority')
        experience=min(seller.get('sector_experience',{}).get(record.get('sector'),0)/5,1) if record.get('sector') else missing('sector_experience')
        speed=1/(1+seller['response_days']/7) if seller.get('response_days') is not None else missing('response_speed')
        complexity=effective_signals(record).get('details',{}).get('account_complexity','unknown')
        suitability=(seniority+experience)/2 if complexity=='high' else .8 if complexity=='standard' else missing('complexity')
        capacity=max(0,(seller['maximum_capacity']-seller['open_workload'])/seller['maximum_capacity'])
        factors=dict(zone=zone,segment=segment,seniority=seniority,sector_experience=experience,response_speed=speed,complexity=suitability,capacity=capacity)
        weights=configuration.get('weights') or DEFAULT_WEIGHTS;activated=[];numerator=denominator=0.0
        for factor,value in factors.items():
            for name,degree,consequent in [('low',skfuzzy.trapmf(np.array([value]),[0,0,.25,.5])[0],.1),('medium',skfuzzy.trimf(np.array([value]),[.25,.5,.75])[0],.5),('high',skfuzzy.trapmf(np.array([value]),[.5,.75,1,1])[0],.9)]:
                if degree and weights[factor]:
                    activated.append(dict(rule=f'{factor}_{name}',strength=round(float(degree),6),weight=weights[factor],consequent=consequent))
                    numerator+=float(degree)*weights[factor]*consequent;denominator+=float(degree)*weights[factor]
        return numerator/denominator, factors, activated, warnings

    def preview(self,records,sellers,configuration):
        records=sorted(records,key=lambda r:r['id']);sellers=sorted(sellers,key=lambda s:s['id'])
        result=output(self.method,configuration)
        slots=[(s,k) for s in sellers for k in range(1,min(len(records),max(0,(s.get('maximum_capacity') or 0)-s['open_workload']))+1)]
        costs=np.full((len(records),len(slots)+len(records)),1e6);costs[:,len(slots):]=0
        compatibility={}
        for i,record in enumerate(records):
            for seller in sellers:
                reasons=candidate_exclusions(record,seller,configuration)
                if reasons:result['excluded_candidates'].append(dict(record_id=record['id'],seller_id=seller['id'],reasons=reasons))
                else:compatibility[(record['id'],seller['id'])]=self.compatibility(record,seller,configuration)
            for j,(seller,slot) in enumerate(slots):
                pair=compatibility.get((record['id'],seller['id']))
                if pair:
                    penalty=configuration.get('balance_weight',.60)*(seller['open_workload']+slot)/seller['maximum_capacity']
                    costs[i,j]=-1000-pair[0]+penalty+j*1e-9
        row_indices,column_indices=linear_sum_assignment(costs)
        seed=[(records[i]['id'],slots[j][0]['id']) for i,j in zip(row_indices,column_indices) if j<len(slots) and costs[i,j]<0]
        from .balancing import balance
        pairs,optimization=balance(records,sellers,compatibility,seed,configuration)
        selected=dict(pairs)
        for record in records:
            sid=selected.get(record['id'])
            if sid is None:
                result['unassigned'].append(dict(record_id=record['id'],reasons=record_exclusions(record) or ['NO_ELIGIBLE_CAPACITY_IN_OPTIMAL_BATCH']))
                continue
            seller=next(s for s in sellers if s['id']==sid)
            score,factors,rules,warnings=compatibility[(record['id'],sid)]
            item=assignment(record,seller,score,['FUZZY_COMPATIBILITY','BATCH_MULTI_OBJECTIVE','CAPACITY_AVAILABLE'],factors,rules,warnings)
            result['assignments'].append(item);result['trace']['record_traces'][record['id']]=item
        result['trace']['optimizer']='maximum coverage, then MILP: relative workload + known amounts + fuzzy compatibility'
        result['trace']['optimization']=optimization
        result['trace']['policy_version']='multi-objective-v2'
        if optimization['status'] not in ('optimal','empty'):
            result['trace']['warnings'].append({'code':'OPTIMIZER_'+optimization['status'].upper(),'message':'Feasible proposal; global optimality not proven within solver limit.'})
        result['trace']['profile_limitations']='Historical sector exposure is a proxy, not certification; unknown factors use neutral 0.5 and warnings.'
        return result


class AIAssisted:
    method='ai_assisted'

    def __init__(self,llm):self.llm=llm

    def preview(self,records,sellers,configuration):
        records=deepcopy(records);extractions=[];cache={};warnings=[]
        for record in records:
            if not record.get('notes'):continue
            key=(record['notes'],record.get('sector'))
            if key not in cache:cache[key]=self.llm.extract(record)
            extraction=deepcopy(cache[key]);extraction['record_id']=record['id'];extraction['note_template_id']=record.get('note_template_id');extractions.append(extraction)
            if extraction.get('error'):
                warnings.append(dict(record_id=record['id'],code='LLM_FAILED_DETERMINISTIC_FALLBACK'))
                if record.get('label_source') not in {'approved_golden_with_context_rule','empty_note_default','manual_review'}:
                    record.setdefault('signals',{})['needs_review']=True
            elif record.get('label_source') in {'approved_golden_with_context_rule','manual_review'}:
                if extraction['output']!=record.get('signals'):
                    warnings.append(dict(record_id=record['id'],code='LLM_DISAGREEMENT_APPROVED_REFERENCE_RETAINED'))
            else:
                baseline=effective_signals(record);proposed=deepcopy(extraction['output'])
                for field,value in baseline.get('details',{}).items():
                    if value is True:proposed['details'][field]=True
                if baseline.get('needs_review'):proposed['needs_review']=True
                from .domain import ACTION_PRIORITY
                action=max([baseline.get('action','continue'),baseline.get('details',{}).get('assignment_action','continue'),proposed['action'],proposed['details']['assignment_action']],key=lambda a:ACTION_PRIORITY.get(a,99))
                proposed['action']=proposed['details']['assignment_action']=action
                record['signals']=proposed
        result=FuzzyOptimal().preview(records,sellers,configuration);result['method']=self.method
        result['trace'].update(llm_extractions=extractions,warnings=result['trace']['warnings']+warnings,effective_signals={r['id']:effective_signals(r) for r in records})
        return result
