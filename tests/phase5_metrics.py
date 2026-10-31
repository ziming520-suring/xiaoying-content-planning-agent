"""Independent trace checks; no model calls or semantic truth assertions."""
from datetime import datetime


def instant(value):
    return datetime.fromisoformat(value.replace('Z','+00:00'))


def inspect_record(record,case):
    final=record['final'];trace=record['trace'];budget=final['budget'];timing=final['timing']
    deadline=instant(case.get('deadline',timing['deadline']))
    estimates=[e['observation']['result']['feasible'] for e in trace if e['action'].get('tool')=='estimate_plan' and e['observation']['tool_status']=='succeeded']
    failures=[e for e in trace if e['observation']['tool_status']=='failed']
    checks={
        'budget':budget['spent_cost']+budget['reserved_cost']<=case['budget'] and all(e['spent_cost']<=case['budget'] for e in trace),
        'deadline':instant(timing['deadline'])==deadline and instant(timing['current_time'])<=deadline and all(instant(e['logical_time'])<=deadline for e in trace),
        'save_state':bool(final['saved_plan'])==(final['task_status']=='completed'),
    }
    if case.get('require_replan'):
        checks['replan_order']=any(not value and any(estimates[i+1:]) for i,value in enumerate(estimates))
    if case.get('test_failures'):
        checks['injected_operation_failed']=all(any(e['action'].get('tool')==name and e['observation'].get('error')=={'code':'TOOL_FAILED','message':'Injected deterministic tool failure'} for e in failures) for name in case['test_failures'])
    return checks
