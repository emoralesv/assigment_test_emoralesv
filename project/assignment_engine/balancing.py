"""Capacity-constrained, explainable workload and known-amount optimization."""
import numpy as np
from scipy.optimize import Bounds,LinearConstraint,milp
from scipy.sparse import lil_matrix


def balance(records,sellers,compatibility,seed,configuration):
    """Keep maximum coverage; minimize relative load/amount deviations and mismatch."""
    if not seed:return seed,{'status':'empty','missing_amount_records':0}
    edges=sorted(compatibility);n=len(edges)
    profiles={s['id']:s for s in sellers if any(sid==s['id'] for _,sid in edges)}
    ids=sorted(profiles);count=len(ids);k=len(seed)
    by_record={r['id']:r for r in records}
    amount={rid:float(by_record[rid].get('estimated_revenue') or 0) for rid,_ in edges}
    caps={sid:profiles[sid]['maximum_capacity'] for sid in ids};total_cap=sum(caps.values())
    base_amount={sid:float(profiles[sid].get('known_estimated_amount',0)) for sid in ids}
    total_base_amount=sum(base_amount.values())
    # Unknown amounts contribute no observation, not an imputed zero-valued company.
    scale=max((total_base_amount+sum(amount.values())*k/len(amount))/total_cap,1)
    target_load=(sum(profiles[s]['open_workload'] for s in ids)+k)/total_cap
    weight=configuration.get('balance_weight',.60)
    money_weight=configuration.get('amount_weight',.5)
    c=np.zeros(n+2*count)
    for j,edge in enumerate(edges):c[j]=-(1-weight)*compatibility[edge][0]/k+j*1e-12
    c[n:n+count]=weight*(1-money_weight)/count
    c[n+count:]=weight*money_weight/count
    rows=[];lower=[];upper=[]
    def constraint(values,lo=-np.inf,hi=np.inf):rows.append(values);lower.append(lo);upper.append(hi)
    constraint({j:1 for j in range(n)},k,k)
    for rid in sorted(amount):constraint({j:1 for j,(r,_) in enumerate(edges) if r==rid},hi=1)
    for index,sid in enumerate(ids):
        indices=[j for j,(_,s) in enumerate(edges) if s==sid]
        constraint({j:1 for j in indices},hi=caps[sid]-profiles[sid]['open_workload'])
        load={j:1/caps[sid] for j in indices}
        money={j:amount[r]*((1/caps[sid] if s==sid else 0)-1/total_cap)/scale for j,(r,s) in enumerate(edges)}
        for coeff,base,variable in ((load,profiles[sid]['open_workload']/caps[sid]-target_load,n+index),
                (money,(base_amount[sid]/caps[sid]-total_base_amount/total_cap)/scale,n+count+index)):
            constraint(coeff|{variable:-1},hi=-base)
            constraint({j:-v for j,v in coeff.items()}|{variable:-1},hi=base)
    matrix=lil_matrix((len(rows),len(c)))
    for i,row in enumerate(rows):
        for j,value in row.items():matrix[i,j]=value
    matrix=matrix.tocsr();lb=np.array(lower);ub=np.array(upper)
    bounds=Bounds(np.zeros(len(c)),np.r_[np.ones(n),np.full(2*count,np.inf)])
    def vector(pairs):
        x=np.zeros(len(c));chosen=set(pairs)
        for j,e in enumerate(edges):x[j]=e in chosen
        for i,sid in enumerate(ids):
            own=[r for r,s in pairs if s==sid]
            x[n+i]=abs((profiles[sid]['open_workload']+len(own))/caps[sid]-target_load)
            x[n+count+i]=abs((base_amount[sid]+sum(amount[r] for r in own))/caps[sid]-(total_base_amount+sum(amount[r] for r,_ in pairs))/total_cap)/scale
        return x
    seed_vector=vector(seed)
    result=milp(c,integrality=np.r_[np.ones(n),np.zeros(2*count)],bounds=bounds,
                constraints=LinearConstraint(matrix,lb,ub),options={'time_limit':10,'node_limit':1000,'mip_rel_gap':.001})
    selected=seed;status='seed_fallback';objective=float(c@seed_vector)
    if result.x is not None:
        pairs=[edge for j,edge in enumerate(edges) if result.x[j]>.5];x=vector(pairs)
        values=matrix@x
        if len(pairs)==k and np.all(values>=lb-1e-7) and np.all(values<=ub+1e-7) and c@x<=objective+1e-8:
            selected=pairs;objective=float(c@x);status='optimal' if result.status==0 else 'feasible_unproven'
    return selected,{'status':status,'solver_status':int(result.status),'solver_message':result.message,'objective':objective,'seed_objective':float(c@seed_vector),
        'balance_weight':weight,'amount_weight':money_weight,'time_limit_seconds':10,
        'target':'equal load/capacity and known amount/capacity across sellers with eligible candidates',
        'missing_amount_records':sum(by_record[r]['estimated_revenue'] is None for r in amount if 'estimated_revenue' in by_record[r]),
        'amount_basis':'known subtotals only; no value inferred for missing amounts'}
