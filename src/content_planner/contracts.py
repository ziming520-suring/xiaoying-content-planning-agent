"""Public request parsing and normalization, before opening writable storage."""
from copy import deepcopy
import json
from .catalog import ToolError, _pairs, canonical, fields, instant, integer, require, string, timestamp


def parse_request(text):
    try:
        return json.loads(text, object_pairs_hook=_pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, RecursionError) as exc:
        raise ToolError('INVALID_REQUEST', 'Invalid or duplicate-key JSON') from exc


def normalize(request):
    fields(request, ['schema_version', 'request_id', 'operation', 'payload'])
    require(type(request['schema_version']) is int and request['schema_version'] == 1, 'Invalid schema_version')
    string(request['request_id'], 'request_id', 200)
    op = request['operation']
    require(isinstance(op, str) and op in {'task.create', 'task.complete_input', 'task.run', 'task.resume'}, 'Unknown operation')
    p = deepcopy(request['payload'])
    require(isinstance(p, dict), 'payload must be an object')
    allowed = {'task_id', 'topic', 'budget_limit', 'start_time', 'deadline'} if op == 'task.create' else (
        {'task_id', 'topic', 'budget_limit', 'deadline'} if op == 'task.complete_input' else {'task_id', 'max_actions'})
    require(set(p) <= allowed and 'task_id' in p, 'Unexpected or missing payload fields')
    string(p['task_id'], 'task_id', 100)
    if op == 'task.create':
        require('start_time' in p, 'start_time required')
        p['start_time'] = timestamp(instant(p['start_time']))
        for key in ('topic', 'budget_limit', 'deadline'):
            p.setdefault(key, None)
    if op in ('task.create', 'task.complete_input'):
        if 'topic' in p and p['topic'] is not None:
            require(isinstance(p['topic'], str), 'topic must be text')
            if not p['topic'].strip():
                p['topic'] = None
            else:
                string(p['topic'], 'topic', 500)
        if p.get('budget_limit') is not None:
            integer(p['budget_limit'], 'budget_limit')
        if p.get('deadline') is not None:
            p['deadline'] = timestamp(instant(p['deadline']))
        if op == 'task.create' and p['deadline'] is not None:
            require(instant(p['deadline']) > instant(p['start_time']), 'Deadline must be after start')
        if op == 'task.complete_input':
            require(len(p) > 1 and all(v is not None for k, v in p.items() if k != 'task_id'), 'Provide missing input values')
    else:
        p.setdefault('max_actions', 24)
        require(type(p['max_actions']) is int and 1 <= p['max_actions'] <= 100, 'max_actions must be 1..100')
    result = dict(schema_version=1, request_id=request['request_id'], operation=op, payload=p)
    canonical(result)
    return result


def response(req, data=None, error=None):
    def identity(key):
        value = req.get(key)
        if not isinstance(value, str):
            return None
        try:
            value.encode('utf-8')
        except UnicodeError:
            return None
        return value
    return {'schema_version': 1, 'request_id': identity('request_id'), 'operation': identity('operation'),
            'status': ('unknown' if error and error.code == 'PENDING_RECONCILIATION' else 'failed') if error else 'succeeded',
            'data': data, 'error': None if error is None else {'code': error.code, 'message': str(error),
                'field': None, 'retryable': error.code in {'STORAGE_FAILED', 'TASK_BUSY', 'PENDING_RECONCILIATION'}}}
