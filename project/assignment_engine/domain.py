"""Pure mandatory rules shared by preview generation and database execution."""
from copy import deepcopy
from datetime import date

ACTION_PRIORITY = {'continue':0, 'schedule_later':1, 'validate_first':2, 'duplicate_review':3, 'block':4}


def effective_signals(record):
    result = deepcopy(record.get('signals') or {})
    details = result.setdefault('details', {})
    note = (record.get('notes') or '').casefold()
    # These explicit restrictions cannot be removed by an LLM response.
    if any(text in note for text in ('no insistiéramos','no insistieramos','no contactar','no lo contactemos','dejáramos de contactarle','do not contact')):
        details['do_not_contact'] = True
    if 'posible duplicado' in note or record.get('is_duplicate'):
        details['possible_duplicate'] = True
    return result


def record_exclusions(record):
    signals = effective_signals(record); details = signals.get('details', {})
    reasons = []
    if record.get('status') != 'nuevo': reasons.append('RECORD_NOT_NEW')
    if details.get('do_not_contact'): reasons.append('DO_NOT_CONTACT')
    if details.get('possible_duplicate') or record.get('is_duplicate'): reasons.append('DUPLICATE_REVIEW')
    action = max((signals.get('action','continue'), details.get('assignment_action','continue')),
                 key=lambda a: ACTION_PRIORITY.get(a, 99))
    if action != 'continue': reasons.append('ACTION_' + action.upper())
    if signals.get('needs_review') or details.get('requires_manual_review'): reasons.append('MANUAL_REVIEW_REQUIRED')
    if details.get('sector_expertise_requested') and not record.get('sector'): reasons.append('MISSING_REQUIRED_SECTOR')
    return sorted(set(reasons))


def candidate_exclusions(record, seller, configuration, additional_workload=0):
    reasons = record_exclusions(record)
    if seller.get('active') is not True: reasons.append('SELLER_INACTIVE')
    if seller.get('role') != 'vendedor': reasons.append('ROLE_NOT_SELLER')
    if seller.get('team_id') is None: reasons.append('INVALID_TEAM')
    if seller.get('zone') is None: reasons.append('MISSING_SELLER_ZONE')
    today = date.fromisoformat(configuration['effective_date'])
    for absence in seller.get('absences', []):
        if not absence.get('starts_on') or (absence.get('raw_end_present') and not absence.get('ends_on')):
            reasons.append('ABSENCE_REQUIRES_REVIEW'); continue
        start=date.fromisoformat(absence['starts_on']);end=date.fromisoformat(absence['ends_on']) if absence.get('ends_on') else None
        if end is not None and end < start: reasons.append('ABSENCE_REQUIRES_REVIEW')
        elif start <= today and (end is None or today <= end): reasons.append('SELLER_ABSENT')
    capacity=seller.get('maximum_capacity')
    if capacity is None: reasons.append('CAPACITY_UNDEFINED')
    elif capacity <= 0: reasons.append('ZERO_CAPACITY')
    elif seller.get('open_workload',0)+additional_workload >= capacity: reasons.append('CAPACITY_EXHAUSTED')
    details=effective_signals(record).get('details',{})
    if details.get('seniority_requested') and (seller.get('tenure_years') is None or seller['tenure_years'] < 3):
        reasons.append('SENIORITY_REQUIREMENT_UNMET')
    if details.get('sector_expertise_requested') and not seller.get('sector_experience',{}).get(record.get('sector'),0):
        reasons.append('SECTOR_EXPERIENCE_UNVERIFIED')
    technical=details.get('technical_expertise')
    if technical and technical not in seller.get('technical_skills',[]): reasons.append('TECHNICAL_SKILL_UNVERIFIED')
    if seller.get('availability_override'):
        reasons=[reason for reason in reasons if reason not in ('SELLER_ABSENT','ABSENCE_REQUIRES_REVIEW')]
    return sorted(set(reasons))


def validate_plan(preview, state):
    records={r['id']:r for r in state['records']};sellers={s['id']:s for s in state['sellers']}
    used={};seen=set()
    for assignment in preview['assignments']:
        rid=str(assignment['record_id']);sid=str(assignment['seller_id'])
        if rid not in records or sid not in sellers or rid in seen: raise ValueError('Invalid or duplicated assignment identity')
        reasons=candidate_exclusions(records[rid],sellers[sid],{'effective_date':state['effective_date']},used.get(sid,0))
        enriched=deepcopy(records[rid])
        inferred=preview.get('trace',{}).get('effective_signals',{}).get(rid)
        if inferred is not None:
            enriched['signals']=inferred
            reasons+=candidate_exclusions(enriched,sellers[sid],{'effective_date':state['effective_date']},used.get(sid,0))
        if reasons: raise ValueError('Assignment violates mandatory constraints: '+','.join(reasons))
        seen.add(rid);used[sid]=used.get(sid,0)+1
    return True
