"""Explicit JSON workbench checkpoints, never pickle or executable objects."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from .catalog import ToolError, canonical, fields, instant, require, timestamp
from .tools import ToolWorkbench
from .artifacts import save_in_transaction

ATTRS = ('_task', '_spent', '_attempts', '_observations', '_results', '_plans', '_success',
         '_saved', '_faults', '_active', '_sequence', '_last_estimate')


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def pack(workbench):
    result = {key: deepcopy(getattr(workbench, key)) for key in ATTRS}
    result['_now'] = timestamp(workbench._now)
    result['state_version'] = 1
    return json.loads(canonical(result))


def restore(catalog, task, state, connection):
    try:
        fields(state, [*ATTRS, '_now', 'state_version'])
        require(type(state['state_version']) is int and state['state_version'] == 1, 'Unsupported state')
        require(state['_task'] == task, 'Task identity mismatch')
        w = ToolWorkbench(catalog, task, connection, artifact_writer=save_in_transaction)
        for key in ATTRS:
            setattr(w, key, deepcopy(state[key]))
        w._plans = {int(k): v for k, v in w._plans.items()}
        w._saved = {int(k): v for k, v in w._saved.items()}
        if w._last_estimate is not None:
            w._last_estimate = tuple(w._last_estimate)
        w._now = instant(state['_now'])
        require(type(w._spent) is int and 0 <= w._spent <= task['budget_limit'], 'Invalid spent cost')
        require(type(w._sequence) is int and type(w._active) is int, 'Invalid counters')
        require(w._active == 0 or w._active in w._plans, 'Missing active plan')
        cost = seconds = reserved = 0
        ids = set()
        for a in w._attempts:
            require(a['attempt_id'] not in ids, 'Duplicate attempt')
            ids.add(a['attempt_id'])
            rate = catalog.rate(a['operation'], a['mode_id'])
            require(a['status'] in ('succeeded', 'failed', 'unknown'), 'Unsettled local attempt')
            if a['status'] == 'unknown':
                require(a['cost_actual'] is None and a['duration_actual_seconds'] is None, 'Unknown result charged')
                require(a['reserved_cost'] == max(rate['success_cost'], rate['failure_cost']), 'Invalid reserve')
                reserved += a['reserved_cost']
            else:
                p = 'success' if a['status'] == 'succeeded' else 'failure'
                require(a['cost_actual'] == rate[p+'_cost'] and a['duration_actual_seconds'] == rate[p+'_seconds'], 'Invalid settlement')
                require(a['reserved_cost'] == 0 and a['reserved_seconds'] == 0, 'Settled attempt retains reserve')
                cost += a['cost_actual']
                seconds += a['duration_actual_seconds']
        require(cost == w._spent and cost + reserved <= task['budget_limit'], 'Ledger mismatch')
        require(instant(task['start_time']) + timedelta(seconds=seconds) == w._now, 'Clock mismatch')
        require(w._now <= w._deadline, 'Clock exceeds deadline')
        for steps in w._plans.values():
            w._validate_plan(steps)
        for version, saved in w._saved.items():
            row = connection.execute('SELECT payload FROM artifacts WHERE task_id=? AND plan_version=?', (task['task_id'], version)).fetchone()
            require(row is not None and json.loads(row[0]) == saved['result']['snapshot'], 'Saved artifact mismatch')
        return w
    except (ToolError, KeyError, ValueError, TypeError, OverflowError) as exc:
        raise ToolError('STORAGE_CORRUPT', f'Invalid task checkpoint: {exc}') from exc


def persist_state(conn, task_id, state):
    text = canonical(state) if state is not None else 'null'
    conn.execute('UPDATE tasks SET state_json=?, state_hash=? WHERE task_id=?', (text, digest(text), task_id))


def summary(w, task, task_status, stop_reason, run_id):
    if w is None:
        return {'task_id': task['task_id'], 'task_status': task_status, 'stop_reason': stop_reason,
                'missing_fields': [k for k in ('topic', 'budget_limit', 'deadline') if task[k] is None],
                'active_plan': None, 'steps': [], 'tool_results': {}, 'attempts': [], 'run_id': run_id,
                'budget': {'limit': task['budget_limit'], 'spent_cost': 0, 'reserved_cost': 0,
                           'remaining_available': task['budget_limit'], 'remaining_cost_estimate': None, 'projected_total_cost': None},
                'timing': {'start_time': task['start_time'], 'current_time': task['start_time'], 'deadline': task['deadline'], 'estimated_finish_time': None},
                'saved_plan': None, 'unresolved_attempts': [], 'execution_mode': 'offline_mock'}
    plans = w._plans.get(w._active)
    projection = w._projection(plans) if plans else None
    steps = []
    for s in plans or []:
        current = deepcopy(s)
        done = w._done(s, plans)
        matching = [a for a in w._attempts if a['operation'] == s['kind'] and a.get('payload', {}).get('version') == w._active
                    and (s['kind'] == 'save_plan' or a.get('payload', {}).get('step_id') == s['step_id'])]
        ready = all(w._done(next(p for p in plans if p['step_id'] == d), plans) for d in s['depends_on'])
        current['status'] = 'succeeded' if done else ('unknown' if matching and matching[-1]['status']=='unknown' else
                             'failed' if matching and matching[-1]['status']=='failed' else 'pending' if ready else 'blocked')
        current['attempt_ids'] = [a['attempt_id'] for a in matching]
        steps.append(current)
    unresolved = [a for a in w._attempts if a['status'] == 'unknown']
    reserved = sum(a['reserved_cost'] for a in unresolved)
    return deepcopy({'task_id': task['task_id'], 'task_status': task_status, 'stop_reason': stop_reason,
        'missing_fields': [], 'active_plan': {'plan_id': task['task_id']+':plan', 'plan_version': w._active, 'steps': plans,
            'estimate': projection} if plans else None, 'steps': steps, 'tool_results': w._results, 'attempts': w._attempts,
        'run_id': run_id, 'budget': {'limit': task['budget_limit'], 'spent_cost': w._spent, 'reserved_cost': reserved,
            'remaining_available': task['budget_limit']-w._spent-reserved,
            'remaining_cost_estimate': projection['remaining_cost_estimate'] if projection else None,
            'projected_total_cost': projection['projected_total_cost'] if projection else None},
        'timing': {'start_time': task['start_time'], 'current_time': timestamp(w._now), 'deadline': task['deadline'],
            'estimated_finish_time': projection['estimated_finish_time'] if projection else None},
        'saved_plan': w._saved.get(w._active, {}).get('result'), 'unresolved_attempts': unresolved, 'execution_mode': 'offline_mock'})


def terminal_status(w):
    if w._active in w._saved:
        return 'completed'
    useful = any(r['operation']=='search_materials' and r['materials'] for r in w._results.values())
    return 'partial' if useful else 'incomplete'
