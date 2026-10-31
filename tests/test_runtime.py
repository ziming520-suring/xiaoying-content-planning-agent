from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def request(op, key, **payload):
    return dict(schema_version=1, request_id=key, operation=op, payload=payload)


def creation(key='create-1', **changes):
    p = dict(task_id='T1', topic='字幕', budget_limit=10,
             start_time='2026-10-30T09:00:00+08:00', deadline='2026-10-30T10:00:00+08:00')
    p.update(changes)
    return request('task.create', key, **p)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        from content_planner.runtime import Runtime
        from content_planner.catalog import ToolError
        self.Runtime, self.ToolError = Runtime, ToolError
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'tasks.sqlite3'
        self.r = Runtime(self.db, ROOT / 'data')

    def start(self, **options):
        self.assertEqual(self.r.request(creation())['status'], 'succeeded')
        response = self.r.request(request('task.run', 'run-1', task_id='T1', **options))
        self.assertEqual(response['status'], 'succeeded')
        return response['data']['run_id']

    def test_missing_input_and_complete_only_missing(self):
        self.r.request(creation(budget_limit=None, deadline=None))
        s = self.r.status('T1')
        self.assertEqual(s['task_status'], 'waiting_input')
        self.assertEqual(set(s['missing_fields']), {'budget_limit', 'deadline'})
        bad = self.r.request(request('task.complete_input', 'bad', task_id='T1', topic='changed'))
        self.assertEqual(bad['status'], 'failed')
        good = self.r.request(request('task.complete_input', 'good', task_id='T1', budget_limit=10,
                                      deadline='2026-10-30T10:00:00+08:00'))
        self.assertEqual(good['status'], 'succeeded')
        self.assertEqual(self.r.status('T1')['task_status'], 'ready')

    def test_invalid_request_no_database_side_effect(self):
        for req in [creation(budget_limit=True), creation(start_time='no'),
                    {**creation(), 'unknown': 1}, {**creation(), 'schema_version': True}]:
            self.assertEqual(self.r.request(req)['status'], 'failed')
            self.assertFalse(self.db.exists())

    def test_replay_and_business_identity(self):
        first = self.r.request(creation())
        self.assertEqual(first, self.Runtime(self.db, ROOT / 'data').request(creation()))
        self.assertEqual(self.r.request(creation(topic='不同'))['error']['code'], 'REQUEST_CONFLICT')
        self.assertEqual(self.r.request(creation(key='another', topic='不同'))['error']['code'], 'TASK_CONFLICT')
        self.assertEqual(self.r.request(creation(key='same-business'))['status'], 'succeeded')

    def test_timezone_normalization_replays(self):
        first = self.r.request(creation())
        equivalent = creation(start_time='2026-10-30T01:00:00Z', deadline='2026-10-30T02:00:00Z')
        self.assertEqual(first, self.r.request(equivalent))

    def test_complete_offline_run_persists(self):
        run_id = self.start()
        final = self.r.drive(run_id)
        self.assertEqual(final['task_status'], 'completed')
        self.assertEqual(final['budget']['spent_cost'], 8)
        self.assertIsNotNone(final['saved_plan'])
        self.assertEqual(self.Runtime(self.db, ROOT / 'data').status('T1'), final)
        self.assertTrue(self.r.trace('T1'))
        self.assertTrue(all(s['status'] == 'succeeded' for s in final['steps']))

    def test_run_replay_does_not_restart(self):
        run_id = self.start()
        first_reply = self.r.request(request('task.run', 'run-1', task_id='T1'))
        final = self.r.drive(run_id)
        self.assertEqual(first_reply, self.r.request(request('task.run', 'run-1', task_id='T1')))
        self.assertEqual(self.r.drive(run_id), final)
        other = self.r.request(request('task.run', 'another-run', task_id='T1'))
        self.assertEqual(other['data']['run_id'], run_id)
        self.assertEqual(self.r.status('T1'), final)

    def test_action_limit_and_explicit_resume(self):
        run_id = self.start(max_actions=1)
        partial = self.r.drive(run_id)
        self.assertEqual(partial['task_status'], 'partial')
        self.assertEqual(partial['stop_reason'], 'step_limit')
        self.assertEqual(len(partial['attempts']), 1)
        response = self.r.request(request('task.resume', 'resume-1', task_id='T1'))
        final = self.Runtime(self.db, ROOT / 'data').drive(response['data']['run_id'])
        self.assertEqual(final['task_status'], 'completed')
        self.assertEqual(sum(a['operation'] == 'search_materials' for a in final['attempts']), 1)

    def test_tool_failure_retains_material_and_fee(self):
        run_id = self.start()
        state = self.r.drive(run_id, test_failures={'generate_script': 1})
        self.assertEqual(state['task_status'], 'partial')
        self.assertEqual(state['budget']['spent_cost'], 3)
        self.assertTrue(state['tool_results'])
        self.assertIn('failed', [a['status'] for a in state['attempts']])

    def test_policy_error_retains_success(self):
        from content_planner.offline import OfflinePolicy
        base = OfflinePolicy()
        class Broken:
            def next_action(self, context):
                if context['tool_results']:
                    raise RuntimeError('model unavailable')
                return base.next_action(context)
        final = self.r.drive(self.start(), policy=Broken())
        self.assertEqual(final['task_status'], 'partial')
        self.assertEqual(final['stop_reason'], 'model_failed')

    def test_invalid_actions_count_towards_limit(self):
        class Invalid:
            def next_action(self, context):
                return {'tool': 'delete_everything', 'arguments': {}, 'reason': 'bad'}
        final = self.r.drive(self.start(max_actions=2), policy=Invalid())
        self.assertEqual(final['task_status'], 'incomplete')
        self.assertEqual(final['stop_reason'], 'step_limit')
        self.assertEqual(len(self.r.trace('T1')), 2)

    def test_no_material_is_incomplete(self):
        self.r.request(creation(topic='不存在的深海产品'))
        reply = self.r.request(request('task.run', 'run', task_id='T1'))
        final = self.r.drive(reply['data']['run_id'])
        self.assertEqual(final['task_status'], 'incomplete')
        self.assertEqual(final['stop_reason'], 'no_material')

    def test_corrupt_snapshot_is_not_silently_rebuilt(self):
        self.r.drive(self.start(max_actions=1))
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("UPDATE tasks SET state_json='{}' WHERE task_id='T1'")
            conn.commit()
        with self.assertRaises(self.ToolError):
            self.r.status('T1')

    def test_catalog_is_pinned_to_task(self):
        self.r.request(creation())
        altered = Path(self.tmp.name) / 'data'
        altered.mkdir()
        for name in ('materials.json', 'tool_costs.json'):
            text = (ROOT/'data'/name).read_text(encoding='utf-8')
            (altered/name).write_text(text.replace('支持编辑字幕文字', '改变后的事实'), encoding='utf-8')
        changed = self.Runtime(self.db, altered)
        reply = changed.request(request('task.run', 'run', task_id='T1'))
        final = changed.drive(reply['data']['run_id'])
        script = final['saved_plan']['snapshot']['scripts'][0]['script']
        self.assertIn('支持编辑字幕文字', script)
        self.assertNotIn('改变后的事实', script)

    def test_save_and_state_roll_back_together(self):
        from content_planner import runtime
        run_id = self.start()
        original = runtime.persist_state
        def fail_save(conn, task_id, state):
            if state['_saved']:
                raise sqlite3.OperationalError('Injected checkpoint write failure')
            return original(conn, task_id, state)
        with patch.object(runtime, 'persist_state', fail_save):
            final = self.r.drive(run_id)
        self.assertNotEqual(final['task_status'], 'completed')
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM artifacts').fetchone()[0], 0)
        self.assertIsNone(final['saved_plan'])
        self.assertTrue(any(r['operation']=='generate_script' for r in final['tool_results'].values()))

    def test_real_process_interruption_and_resume(self):
        run_id = self.start()
        worker = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT/'tests/runtime_worker.py'),
                                 str(self.db), str(ROOT/'data'), run_id, 'crash'], capture_output=True)
        self.assertEqual(worker.returncode, 23, worker.stderr)
        # Read-only status reports persisted running state; explicit resume performs recovery.
        self.assertEqual(self.r.status('T1')['task_status'], 'running')
        reply = self.r.request(request('task.resume', 'resume-after-crash', task_id='T1'))
        self.assertEqual(reply['status'], 'succeeded')
        final = self.r.drive(reply['data']['run_id'])
        self.assertEqual(final['task_status'], 'completed')
        self.assertEqual(sum(a['operation']=='search_materials' for a in final['attempts']), 1)

    def test_missing_database_read_does_not_create(self):
        with self.assertRaises(self.ToolError):
            self.r.status('missing')
        self.assertFalse(self.db.exists())

    def test_only_one_pending_resume(self):
        self.r.drive(self.start(max_actions=1))
        first = self.r.request(request('task.resume', 'resume1', task_id='T1'))
        second = self.r.request(request('task.resume', 'resume2', task_id='T1'))
        self.assertEqual(second['status'], 'failed')
        self.assertEqual(self.r.drive(first['data']['run_id'])['task_status'], 'completed')

    def test_process_lock_blocks_competing_driver_and_resume(self):
        run_id = self.start()
        ready, release = Path(self.tmp.name)/'ready', Path(self.tmp.name)/'release'
        worker = subprocess.Popen([sys.executable, '-X', 'utf8', str(ROOT/'tests/runtime_worker.py'),
                                   str(self.db), str(ROOT/'data'), run_id, 'hold', str(ready), str(release)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            end = time.monotonic() + 10
            while not ready.exists() and worker.poll() is None and time.monotonic() < end:
                time.sleep(0.05)
            self.assertTrue(ready.exists())
            with self.assertRaises(self.ToolError) as caught:
                self.r.drive(run_id)
            self.assertEqual(caught.exception.code, 'TASK_BUSY')
            reply = self.r.request(request('task.resume', 'competing', task_id='T1'))
            self.assertEqual(reply['error']['code'], 'TASK_BUSY')
        finally:
            release.write_text('go', encoding='utf-8')
            out, err = worker.communicate(timeout=15)
        self.assertEqual(worker.returncode, 0, err)
        self.assertEqual(self.r.status('T1')['task_status'], 'completed')

    def test_kill_inside_save_transaction_then_resume(self):
        run_id = self.start()
        worker = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT/'tests/runtime_worker.py'),
                                 str(self.db), str(ROOT/'data'), run_id, 'crash_save'], capture_output=True)
        self.assertEqual(worker.returncode, 24, worker.stderr)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM artifacts').fetchone()[0], 0)
        before = self.r.status('T1')
        self.assertEqual(before['budget']['spent_cost'], 8)
        self.assertIsNone(before['saved_plan'])
        reply = self.r.request(request('task.resume', 'recover-save', task_id='T1'))
        final = self.r.drive(reply['data']['run_id'])
        self.assertEqual(final['task_status'], 'completed')
        self.assertEqual(final['budget']['spent_cost'], 8)
        self.assertEqual(sum(a['operation']=='generate_script' for a in final['attempts']), 1)

    def test_deleted_saved_artifact_is_not_reported_completed(self):
        self.r.drive(self.start())
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('DELETE FROM artifacts')
            conn.commit()
        with self.assertRaises(self.ToolError) as caught:
            self.r.status('T1')
        self.assertEqual(caught.exception.code, 'STORAGE_CORRUPT')

    def test_trace_includes_internal_final_estimate(self):
        self.r.drive(self.start())
        event = next(e for e in self.r.trace('T1') if e['action']['tool']=='save_plan')
        self.assertEqual([o['tool'] for o in event['tool_observations']], ['estimate_plan', 'save_plan'])

    def test_partial_input_fill_can_be_replayed_after_ready(self):
        self.r.request(creation(budget_limit=None, deadline=None))
        budget = request('task.complete_input', 'budget', task_id='T1', budget_limit=10)
        first = self.r.request(budget)
        self.assertEqual(first['data']['task_status'], 'waiting_input')
        self.r.request(request('task.complete_input', 'deadline', task_id='T1', deadline='2026-10-30T02:00:00Z'))
        self.assertEqual(self.r.request(budget), first)
        self.assertEqual(self.r.status('T1')['task_status'], 'ready')

    def test_empty_database_after_failed_initialization_can_retry(self):
        bad = self.Runtime(self.db, Path(self.tmp.name)/'missing-catalog')
        self.assertEqual(bad.request(creation())['status'], 'failed')
        self.assertEqual(self.r.request(creation())['status'], 'succeeded')
        self.assertEqual(self.r.status('T1')['task_status'], 'ready')

    def test_unpaired_unicode_rejected_before_database(self):
        for req in (creation(topic='\ud800'), creation(key='\ud800')):
            self.assertEqual(self.r.request(req)['error']['code'], 'INVALID_REQUEST')
            self.assertFalse(self.db.exists())


if __name__ == '__main__':
    unittest.main()
