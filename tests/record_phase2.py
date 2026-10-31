"""Reproducible scripted tool scenarios, never a claimed model decision loop."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from content_planner.catalog import Catalog
from content_planner.tools import ToolWorkbench


def build_steps(ref, mode):
    return [dict(step_id='generate', kind='generate_script', mode_id=mode, depends_on=[], input_refs=[ref]),
            dict(step_id='save', kind='save_plan', mode_id='default', depends_on=['generate'], input_refs=[])]


def execute(scenario, db):
    task = dict(task_id=scenario['id'], topic='介绍字幕编辑功能', budget_limit=10,
                start_time='2026-10-30T09:00:00+08:00', deadline='2026-10-30T10:00:00+08:00')
    task.update(scenario['task_overrides'])
    w = ToolWorkbench(Catalog.load(ROOT / 'data'), task, db)
    responses = [w.search_materials(scenario['query'])]
    ref = responses[0]['result']['result_id']
    estimate = None
    for action in scenario['actions']:
        if action['op'] == 'fail_next':
            w.inject_failure(action['tool'])
            continue
        if action['op'] == 'estimate':
            response = w.estimate_plan(build_steps(ref, action['mode']))
            if response['tool_status'] == 'succeeded':
                estimate = response['result']
        elif action['op'] == 'generate':
            response = w.generate_script(estimate['plan_id'], estimate['plan_version'], 'generate')
        elif action['op'] == 'save':
            response = w.save_plan(estimate['plan_id'], estimate['plan_version'])
        else:
            raise ValueError(f'Unknown scripted action {action}')
        responses.append(response)
    state = w.snapshot()
    saved = [r['result'] for r in responses if r['tool_status'] == 'succeeded' and 'artifact_id' in r['result']]
    estimates = [r['result'] for r in responses if r['tool_status'] == 'succeeded' and 'estimate_id' in r['result']]
    actual = {'statuses': [r['tool_status'] for r in responses], 'spent_cost': state['spent_cost'],
              'saved': bool(saved), 'logical_time': state['logical_time'],
              'estimate_feasible': [e['feasible'] for e in estimates],
              'estimate_totals': [e['projected_total_cost'] for e in estimates],
              'last_error': responses[-1]['error']['code'] if responses[-1]['error'] else None}
    mismatches = {key: {'expected': value, 'actual': actual.get(key)}
                  for key, value in scenario['expected'].items() if actual.get(key) != value}
    return {'scenario_id': scenario['id'], 'input': task, 'scripted_actions': scenario['actions'],
            'execution_mode': 'scripted_tool_validation_not_agent', 'expected': scenario['expected'],
            'actual': actual, 'passed': not mismatches, 'mismatches': mismatches,
            'responses': responses, 'state': state, 'saved_plan': saved[-1] if saved else None,
            'trace': w.observations()}


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def fingerprint():
    paths = [p for directory in ('src', 'data', 'tests') for p in (ROOT / directory).rglob('*')
             if p.suffix in ('.py', '.json') and '__pycache__' not in p.parts]
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, help='New output directory; existing paths rejected')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    before = fingerprint()
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_*.py'))
    (output / 'test-results.txt').write_text(stream.getvalue(), encoding='utf-8')
    records = []
    scenarios = json.loads((ROOT / 'data/scenarios.json').read_text(encoding='utf-8'))['scenarios']
    for scenario in scenarios:
        # Fault settings and expected values are recorded before execution.
        dump(output / f'{scenario["id"]}-input.json', scenario)
        with tempfile.TemporaryDirectory() as temp:
            record = execute(scenario, Path(temp) / 'artifacts.sqlite3')
        trace = record.pop('trace')
        dump(output / f'{scenario["id"]}-actual.json', record)
        (output / f'{scenario["id"]}-trace.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in trace), encoding='utf-8')
        records.append({'id': scenario['id'], 'passed': record['passed']})
    after = fingerprint()
    summary = {'execution_mode': 'scripted_tool_validation_not_agent', 'tests_run': result.testsRun,
               'tests_passed': result.wasSuccessful(), 'scenario_count': len(records),
               'scenario_passed': sum(r['passed'] for r in records), 'scenarios': records,
               'source_unchanged': before == after, 'source_sha256': before}
    dump(output / 'summary.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k != 'source_sha256'}, ensure_ascii=False))
    return 0 if result.wasSuccessful() and all(r['passed'] for r in records) and before == after else 1


if __name__ == '__main__':
    raise SystemExit(main())
