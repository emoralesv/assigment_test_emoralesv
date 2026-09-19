"""Preserve raw fields and attach normalized values plus rule provenance."""
import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from .common import ROOT, OUT, TABLES, read_csv, write_csv, dump_json, digest

ZONES = {'centro':'Centro','costa':'Costa','occidente':'Occidente','antioquia':'Antioquia','ant':'Antioquia','bogotá':'Centro'}
STATUS = {'nuevo', 'asignado', 'en_gestion', 'descartado'}
DATES = {'usuarios':{'fecha_ingreso'},'registros':{'fecha_creacion'},'ausencias':{'desde','hasta'},'actividad':{'fecha'}}
NUMBERS = {'empleados', 'ingresos_estimados', 'capacidad_maxima'}
CATEGORIES = {'rol','segmento_experto','sector','fuente','tipo','motivo'}
EXPECTED = {'nuevo':71,'en_gestion':46,'asignado':30,'descartado':20,'duplicate_nit_groups':3,'future_records':2,'users_team_99':1,'missing_notes':27,'missing_zones':8,'missing_sectors':6,'missing_employees':10,'missing_revenue':5,'open_ended_absences':1,'inferred_owners':96,'no_owners':71,'unique_notes':15}


def clean(value):
    if value is None: return None
    return unicodedata.normalize('NFC', str(value)).strip() or None


def norm_zone(value, city=None, city_rules=None):
    value = clean(value)
    if value is not None:
        return ZONES.get(value.casefold(), value), 'explicit_zone_alias' if value.casefold() in ZONES else 'unknown_zone', False
    rule = (city_rules or {}).get((clean(city) or '').casefold())
    if rule is not None:
        if rule not in set(ZONES.values()): raise ValueError('Configured city zone is not canonical')
        return rule, 'configured_city_to_zone', True
    return None, 'missing', False


def normalize(table, rows, effective, city_rules, issues):
    result=[]
    def issue(row, field, kind, original, normalized=None, severity='error', blocking=True):
        issues.append({'table_name':table,'row_id':row.get('id'),'source_row':row['_source_row'],'field_name':field,'issue_type':kind,'severity':severity,'original_value':original,'normalized_value':normalized,'message':f'{table}.{field}: {kind}','recommended_action':'Review source and business rule; retain row','is_blocking':blocking})
    for index, source in enumerate(rows, 2):
        row=dict(source, _source_row=index)
        for field, raw in source.items():
            value=clean(raw); rule='trim_unicode_nfc'; inferred=False
            if field=='zona': value,rule,inferred=norm_zone(raw,source.get('ciudad'),city_rules)
            elif field=='estado':
                value=value.casefold() if value else None; rule='trim_casefold_status'
                if value is not None and value not in STATUS: issue(row,field,'unknown_status',raw,value)
            elif field in CATEGORIES or field=='email': value=value.casefold() if value else None; rule='trim_unicode_casefold'
            elif field=='nit':
                value=''.join(c for c in value if c.isalnum()).casefold() if value else None; rule='nit_remove_formatting'
            elif field in NUMBERS:
                rule='nonnegative_decimal'
                if value is not None:
                    try:
                        number=Decimal(value)
                        if not number.is_finite() or number<0 or (field!='ingresos_estimados' and number!=number.to_integral_value()): raise InvalidOperation
                        value=int(number) if number==number.to_integral_value() else str(number)
                    except InvalidOperation:
                        issue(row,field,'invalid_numeric',raw); value=None; rule='invalid_numeric_preserved'
            elif field in DATES.get(table,set()):
                rule='iso_date'
                if value is not None:
                    try:
                        parsed=date.fromisoformat(value); value=parsed.isoformat()
                        if parsed>effective: issue(row,field,'future_date',raw,value,'warning',False)
                    except ValueError: issue(row,field,'invalid_date',raw); value=None;rule='invalid_date_preserved'
                elif table=='ausencias' and field=='hasta': rule='open_ended_absence'
            elif field=='activo':
                rule='explicit_boolean'
                if value is not None and value.casefold() in {'true','false'}: value=value.casefold()=='true'
                else: issue(row,field,'invalid_boolean',raw);value=None
            row[field+'_original']=raw
            row[field+('_normalizada' if field=='zona' else '_normalizado')]=value
            row[field+'_normalization_rule']=rule if value is not None or rule in {'invalid_numeric_preserved','invalid_date_preserved','open_ended_absence'} else 'missing'
            row[field+'_was_inferred']=inferred
            if value is None and field not in {'equipo_id','segmento_experto'}:
                if not (table=='ausencias' and field=='hasta'):
                    issue(row,field,'missing_value',raw,None,'warning',field in {'id','zona','sector','capacidad_maxima','desde'})
            if field=='zona' and rule=='unknown_zone': issue(row,field,'unknown_zone',raw,value)
        if table=='ausencias':
            start=row['desde_normalizado'];end=row['hasta_normalizado']
            valid=bool(start) and (clean(source['hasta']) is None or end is not None) and (not end or start<=end)
            if start and end and start>end:issue(row,'hasta','invalid_absence_interval',source['hasta'],end)
            active=valid and start<=effective.isoformat() and (end is None or effective.isoformat()<=end)
            row.update(absence_is_active=active,absence_is_active_original=None,absence_is_active_rule='inclusive_interval_effective_date',absence_is_active_was_inferred=False,absence_interval_valid=valid)
        result.append(row)
    return result


def val(row, field): return row.get(field+('_normalizada' if field=='zona' else '_normalizado'))


def analyze(tables, effective):
    issues=[];duplicates=[]
    def add(table,row,field,kind,value=None):
        issues.append(dict(table_name=table,row_id=row.get('id'),source_row=row['_source_row'],field_name=field,issue_type=kind,severity='error',original_value=row.get(field),normalized_value=value,message=f'{table}.{field}: {kind}',recommended_action='Review; do not merge or delete',is_blocking=True))
    for table,rows in tables.items():
        for field in ['id']+(['nit'] if table=='registros' else [])+(['email'] if table=='usuarios' else []):
            groups=defaultdict(list)
            for row in rows:
                if val(row,field) is not None: groups[val(row,field)].append(row)
            for key,members in groups.items():
                if len(members)>1:
                    duplicates.append(dict(group_id=f'DUP-{len(duplicates)+1:03}',table_name=table,field_name=field,normalized_value=key,row_ids=[r['id'] for r in members],source_rows=[r['_source_row'] for r in members],count=len(members)))
                    for r in members:add(table,r,field,'duplicate_'+field,key)
    for table,field,target in [('usuarios','equipo_id','equipos'),('equipos','lider_id','usuarios'),('ausencias','usuario_id','usuarios'),('actividad','usuario_id','usuarios'),('actividad','registro_id','registros')]:
        ids=Counter(val(r,'id') for r in tables[target])
        for row in tables[table]:
            fk=val(row,field)
            if fk is None:
                if not (table=='usuarios' and val(row,'rol')=='admin'):add(table,row,field,'missing_foreign_key')
            elif not ids[fk]:add(table,row,field,'orphan_reference',fk)
            elif ids[fk]>1:add(table,row,field,'ambiguous_reference',fk)
    activity=defaultdict(set)
    for a in tables['actividad']:
        if val(a,'registro_id') is not None and val(a,'usuario_id') is not None:activity[val(a,'registro_id')].add(val(a,'usuario_id'))
    ownership=[];owners={}
    record_ids=Counter(val(r,'id') for r in tables['registros'])
    for r in tables['registros']:
        users=sorted(activity[val(r,'id')]); owner=users[0] if len(users)==1 and record_ids[val(r,'id')]==1 else None
        state='inferred' if owner else 'ambiguous' if users else 'no_activity'
        ownership.append(dict(record_id=val(r,'id'),source_row=r['_source_row'],historical_owner_original=None,historical_owner_id=owner,activity_user_ids=users,ownership_status=state,was_inferred=owner is not None,inference_rule='one_distinct_activity_user; duplicate_record_id_blocks_inference'))
        if owner:owners[val(r,'id')]=owner
        if state=='ambiguous':add('registros',r,'id','ambiguous_ownership',users)
        if 'posible duplicado' in (val(r,'notas') or '').casefold():add('registros',r,'notas','possible_duplicate_note',val(r,'notas'))
    userids=Counter(val(u,'id') for u in tables['usuarios']);teamids={val(t,'id') for t in tables['equipos']}
    workload=[];unattributed=[]
    for r in tables['registros']:
        if val(r,'estado') in {'asignado','en_gestion'} and (val(r,'id') not in owners or userids[owners[val(r,'id')]]!=1):unattributed.append(val(r,'id'))
    for u in tables['usuarios']:
        if val(u,'rol') not in {'vendedor','lider'}:continue
        uid=val(u,'id');counts=Counter(val(r,'estado') for r in tables['registros'] if owners.get(val(r,'id'))==uid and userids[uid]==1)
        assigned=counts['asignado']; managing=counts['en_gestion']; total=assigned+managing;capacity=val(u,'capacidad_maxima');reasons=[]
        absent=any(val(a,'usuario_id')==uid and a['absence_is_active'] for a in tables['ausencias'])
        unknown_absence=any(val(a,'usuario_id')==uid and not a['absence_interval_valid'] for a in tables['ausencias'])
        if val(u,'activo') is not True:reasons.append('INACTIVE_OR_UNKNOWN')
        if absent:reasons.append('ABSENT')
        if unknown_absence:reasons.append('INVALID_ABSENCE_REVIEW')
        if capacity is None:reasons.append('CAPACITY_UNDEFINED')
        elif capacity==0:reasons.append('ZERO_CAPACITY')
        elif total>=capacity:reasons.append('CAPACITY_EXHAUSTED')
        if val(u,'equipo_id') not in teamids:reasons.append('INVALID_TEAM')
        if not val(u,'zona'):reasons.append('MISSING_ZONE')
        if userids[uid]!=1:reasons.append('DUPLICATE_USER_ID')
        workload.append(dict(seller_id=uid,assigned_count=assigned,in_management_count=managing,open_workload=total,maximum_capacity=capacity,remaining_capacity=None if capacity is None else max(0,capacity-total),utilization=total/capacity if capacity and capacity>0 else None,availability_status='inactive' if val(u,'activo') is not True else 'absent' if absent else 'review_required' if unknown_absence else 'available',eligibility_reasons=reasons,workload_original=None,workload_was_inferred=True,workload_rule='active_record_statuses_at_inferred_historical_owner; not activity count',capacity_original=u['capacidad_maxima_original'],capacity_rule=u['capacidad_maxima_normalization_rule']))
    return issues,duplicates,ownership,workload,unattributed


def run(source=ROOT/'data',effective=date(2026,9,18),city_rules=None):
    source=Path(source);OUT.mkdir(exist_ok=True)
    paths={t:source/(t+'.csv') for t in TABLES}
    if source.resolve().is_relative_to(OUT.resolve()):raise ValueError('Source cannot be normalized output')
    before={t:digest(p) for t,p in paths.items()}
    issues=[];tables={t:normalize(t,read_csv(p),effective,city_rules or {},issues) for t,p in paths.items()}
    extra,duplicates,owners,workload,unattributed=analyze(tables,effective);issues+=extra
    for n,issue in enumerate(issues,1):issue['issue_id']=f'DQ-{n:05}'
    for t,rows in tables.items():write_csv(OUT/(t+'.csv'),rows)
    write_csv(OUT/'historical_ownership.csv',owners)
    write_csv(OUT/'seller_workload.csv',workload)
    write_csv(OUT/'data_quality_issues.csv',issues,['issue_id','table_name','row_id','source_row','field_name','issue_type','severity','original_value','normalized_value','message','recommended_action','is_blocking'])
    write_csv(OUT/'duplicate_groups.csv',duplicates,['group_id','table_name','field_name','normalized_value','row_ids','source_rows','count'])
    records=tables['registros']; observed=dict(Counter(val(r,'estado') for r in records))
    observed.update(duplicate_nit_groups=sum(g['field_name']=='nit' for g in duplicates),future_records=sum(bool(val(r,'fecha_creacion')) and val(r,'fecha_creacion')>effective.isoformat() for r in records),users_team_99=sum(val(u,'equipo_id')=='99' for u in tables['usuarios']),missing_notes=sum(clean(r['notas_original']) is None for r in records),missing_zones=sum(clean(r['zona_original']) is None for r in records),missing_sectors=sum(clean(r['sector_original']) is None for r in records),missing_employees=sum(clean(r['empleados_original']) is None for r in records),missing_revenue=sum(clean(r['ingresos_estimados_original']) is None for r in records),open_ended_absences=sum(clean(a['hasta_original']) is None for a in tables['ausencias']),inferred_owners=sum(o['ownership_status']=='inferred' for o in owners),no_owners=sum(o['ownership_status']=='no_activity' for o in owners),unique_notes=len({val(r,'notas') for r in records if val(r,'notas')}))
    checks={k:{'expected':v,'observed':observed[k],'matches':observed[k]==v} for k,v in EXPECTED.items()}
    after={t:digest(p) for t,p in paths.items()}
    if before!=after:raise RuntimeError('Source files changed during run')
    report=dict(effective_date=effective.isoformat(),source_hashes=before,source_unchanged=True,row_counts={t:len(r) for t,r in tables.items()},checks=checks,issue_counts=dict(Counter(i['issue_type'] for i in issues)),unattributed_active_records=unattributed,city_to_zone_rules=city_rules or {},limitations=['Ownership and workload are inferred from activity, not verified current assignments.','Leaders and sellers are reported separately by source role; no seller assignment is performed.','No activity, ambiguous ownership and invalid owners are not attributed; see unattributed_active_records.','Missing capacity has no implicit default; zero capacity prohibits new records.'])
    dump_json(OUT/'normalization_report.json',report)
    summary='# Normalización\n\nFecha efectiva: '+effective.isoformat()+'. CSV originales conservados y hashes verificados.\n\n| Comprobación | Esperado | Observado | Coincide |\n|---|---:|---:|---|\n'
    summary+=''.join(f"| {k} | {c['expected']} | {c['observed']} | {c['matches']} |\n" for k,c in checks.items())
    summary+='\n## Límites\n\n'+'\n'.join('- '+x for x in report['limitations'])+'\n\nEvaluación del LLM: consultar `note_extraction_results*.summary.json`; estos controles no prueban precisión del modelo.\n'
    (OUT/'normalization_summary.md').write_text(summary,encoding='utf-8')
    return report


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=ROOT/'data');p.add_argument('--effective-date',type=date.fromisoformat,default=date(2026,9,18));p.add_argument('--config',type=Path)
    a=p.parse_args();config=json.loads(a.config.read_text()) if a.config else {};print(json.dumps(run(a.source,a.effective_date,config.get('city_to_zone',{})),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
