import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db = self.dir/'runtime.sqlite3'

    def call(self, *args):
        env = dict(os.environ, PYTHONPATH=str(ROOT/'src'))
        result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'content_planner', '--db', str(self.db),
                                 '--data', str(ROOT/'data'), *map(str, args)], env=env, cwd=self.dir,
                                capture_output=True, text=True, encoding='utf-8')
        return result, json.loads(result.stdout)

    def write_request(self, name, op, payload):
        path = self.dir/(name+'.json')
        path.write_text(json.dumps(dict(schema_version=1, request_id=name, operation=op, payload=payload)), encoding='utf-8')
        return path

    def test_create_run_status_and_trace(self):
        create = self.write_request('create', 'task.create', dict(task_id='C1', topic='字幕', budget_limit=10,
            start_time='2026-10-30T09:00:00+08:00', deadline='2026-10-30T10:00:00+08:00'))
        result, body = self.call('execute', create)
        self.assertEqual(result.returncode, 0, result.stderr)
        run = self.write_request('run', 'task.run', dict(task_id='C1'))
        result, body = self.call('execute', run)
        self.assertEqual(body['task']['task_status'], 'completed')
        _, again = self.call('execute', run)
        self.assertEqual(body, again)
        _, status = self.call('status', 'C1')
        self.assertEqual(status['budget']['spent_cost'], 8)
        output = self.dir/'trace.jsonl'
        result, body = self.call('trace', 'C1', '--output', output)
        self.assertEqual(result.returncode, 0)
        self.assertGreater(len(output.read_text(encoding='utf-8').splitlines()), 2)
        result, body = self.call('trace', 'C1', '--output', output)
        self.assertNotEqual(result.returncode, 0)

    def test_duplicate_json_rejected_before_db(self):
        path = self.dir/'bad.json'
        path.write_text('{"schema_version":1,"schema_version":1}', encoding='utf-8')
        result, body = self.call('execute', path)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(body['error']['code'], 'INVALID_REQUEST')
        self.assertFalse(self.db.exists())

    def test_reconcile_incomplete_exit_code(self):
        import io
        from contextlib import redirect_stdout
        from unittest.mock import patch
        sys.path.insert(0,str(ROOT/'src'))
        from content_planner.__main__ import main
        with patch('content_planner.bailian.BailianPolicy'), patch('content_planner.remote.reconcile',return_value={'task_status':'partial','stop_reason':'pending_reconciliation'}):
            with redirect_stdout(io.StringIO()):
                code=main(['--db',str(self.db),'reconcile','C1'])
        self.assertEqual(code,3)

    def test_read_missing_db_no_side_effect(self):
        result, body = self.call('status', 'missing')
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.db.exists())

    def test_bad_file_encoding_is_structured_error(self):
        path = self.dir/'bad-encoding.json'
        path.write_bytes(b'\xff\xfe\xff')
        result, body = self.call('execute', path)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(body['error']['code'], 'INVALID_REQUEST')
        self.assertFalse(self.db.exists())

    def test_unpaired_unicode_json_is_structured_error(self):
        path = self.write_request('unicode', 'task.create', dict(task_id='C1', topic='\ud800', budget_limit=10,
            start_time='2026-10-30T09:00:00+08:00', deadline='2026-10-30T10:00:00+08:00'))
        result, body = self.call('execute', path)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(body['error']['code'], 'INVALID_REQUEST')
        self.assertFalse(self.db.exists())


if __name__ == '__main__':
    unittest.main()
