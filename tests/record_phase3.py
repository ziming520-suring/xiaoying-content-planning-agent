"""Record real local runtime executions. Offline policy is not real-model evidence."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from content_planner.runtime import Runtime


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def req(op, key, **payload):
    return dict(schema_version=1, request_id=key, operation=op, payload=payload)


def execute(case, db):
    runtime = Runtime(db, ROOT/'data')
    task = dict(task_id=case['id'], topic='字幕', budget_limit=10, start_time='2026-10-30T09:00:00+08:00', deadline='2026-10-30T10:00:00+08:00')
    task.update(case['overrides'])
    commands, intermediate, checks = [], [], {}
    def submit(command):
        reply = runtime.request(command)
        commands.append({'request': command, 'response': reply})
        if reply['status'] != 'succeeded':
            raise AssertionError(reply)
        return reply
    submit(req('task.create', 'create', **task))
    intermediate.append(runtime.status(case['id']))
    if 'complete_input' in case:
        checks['missing_fields_detected'] = intermediate[-1]['missing_fields'] == ['budget_limit', 'deadline']
        submit(req('task.complete_input', 'fill', task_id=case['id'], **case['complete_input']))
    run_request = req('task.run', 'run', task_id=case['id'], max_actions=case.get('max_actions', 24))
    start = submit(run_request)
    run_id = start['data']['run_id']
    if case.get('crash'):
        crashed = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT/'tests/runtime_worker.py'), str(db), str(ROOT/'data'), run_id, case['crash']], capture_output=True, text=True, encoding='utf-8')
        checks['subprocess_exited_during_save'] = crashed.returncode == 24
        intermediate.append(runtime.status(case['id']))
        checks['saved_plan_not_visible_before_commit'] = intermediate[-1]['saved_plan'] is None
        reply = submit(req('task.resume', 'recover', task_id=case['id']))
        final = Runtime(db, ROOT/'data').drive(reply['data']['run_id'])
    else:
        final = runtime.drive(run_id, test_failures=case.get('test_failures'))
    if case.get('resume'):
        intermediate.append(final)
        checks['limited_run_stopped'] = final['stop_reason']=='step_limit'
        reply = submit(req('task.resume', 'resume', task_id=case['id']))
        final = Runtime(db, ROOT/'data').drive(reply['data']['run_id'])
    if case.get('repeat'):
        checks['request_replayed'] = submit(run_request) == start
        checks['repeated_drive_unchanged'] = Runtime(db, ROOT/'data').drive(run_id) == final
    checks['expected_status'] = final['task_status']==case['expected_status']
    checks['expected_spent'] = final['budget']['spent_cost']==case['expected_spent']
    checks['fresh_reader_same_result'] = Runtime(db, ROOT/'data').status(case['id']) == final
    checks['saved_iff_completed'] = bool(final['saved_plan']) == (final['task_status']=='completed')
    trace = runtime.trace(case['id'])
    return {'case': case['id'], 'execution_mode': 'offline_mock', 'passed': all(checks.values()), 'checks': checks,
            'commands': commands, 'intermediate': intermediate, 'final': final, 'trace': trace}


def fingerprint():
    paths = [p for folder in ('src', 'data', 'tests') for p in (ROOT/folder).rglob('*') if p.suffix in ('.py', '.json') and '__pycache__' not in p.parts]
    paths.extend([ROOT/'run_demo.py', ROOT/'run_tools_demo.py'])
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    before = fingerprint()
    test_output = io.StringIO()
    tests = unittest.TextTestRunner(stream=test_output, verbosity=2).run(unittest.defaultTestLoader.discover(str(ROOT/'tests'), pattern='test_*.py'))
    (args.output/'test-results.txt').write_text(test_output.getvalue(), encoding='utf-8')
    cases = json.loads((ROOT/'data/runtime_scenarios.json').read_text(encoding='utf-8'))['scenarios']
    statuses = []
    for case in cases:
        dump(args.output/f'{case["id"]}-input.json', case)
        with tempfile.TemporaryDirectory(prefix='phase3-record-') as temp:
            record = execute(case, Path(temp)/'runtime.sqlite3')
        trace = record.pop('trace')
        dump(args.output/f'{case["id"]}-actual.json', record)
        (args.output/f'{case["id"]}-trace.jsonl').write_text(''.join(json.dumps(e, ensure_ascii=False)+'\n' for e in trace), encoding='utf-8')
        statuses.append({'id':case['id'], 'passed':record['passed']})
    report = {'execution_mode': 'offline_mock', 'tests_run': tests.testsRun, 'tests_passed': tests.wasSuccessful(),
              'scenario_count': len(cases), 'scenario_passed': sum(s['passed'] for s in statuses), 'scenarios': statuses,
              'source_unchanged': before==fingerprint(), 'source_sha256': before, 'real_model_calls': 0}
    dump(args.output/'summary.json', report)
    print(json.dumps({k:v for k,v in report.items() if k!='source_sha256'}, ensure_ascii=False))
    return 0 if tests.wasSuccessful() and all(s['passed'] for s in statuses) and report['source_unchanged'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
