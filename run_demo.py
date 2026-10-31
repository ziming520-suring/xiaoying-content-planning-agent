"""Persistent phase 3 offline demo. Re-running uses the same task and request keys."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT/'src'))
from content_planner.runtime import Runtime


if __name__ == '__main__':
    runtime = Runtime(ROOT/'var/demo.sqlite3', ROOT/'data')
    create = dict(schema_version=1, request_id='demo-create', operation='task.create', payload=dict(
        task_id='DEMO', topic='字幕', budget_limit=10, start_time='2026-10-30T09:00:00+08:00', deadline='2026-10-30T10:00:00+08:00'))
    created = runtime.request(create)
    if created['status'] != 'succeeded':
        print(json.dumps(created, ensure_ascii=False, indent=2))
        raise SystemExit(2)
    run = runtime.request(dict(schema_version=1, request_id='demo-run', operation='task.run', payload={'task_id':'DEMO'}))
    if run['status'] != 'succeeded':
        print(json.dumps(run, ensure_ascii=False, indent=2))
        raise SystemExit(2)
    print(json.dumps(runtime.drive(run['data']['run_id']), ensure_ascii=False, indent=2))
