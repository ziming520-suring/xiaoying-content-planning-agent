"""CLI: execute normalized commands, inspect status, export durable Trace."""
import argparse
import json
from pathlib import Path
import sys

from .catalog import ToolError
from .contracts import parse_request
from .runtime import Runtime


def main(argv=None):
    parser = argparse.ArgumentParser(description='Content planner with offline and Beijing Bailian execution')
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--data', type=Path, default=Path(__file__).resolve().parents[2]/'data')
    commands = parser.add_subparsers(dest='command', required=True)
    execute = commands.add_parser('execute', help='Submit request and drive a pending run with the selected model')
    execute.add_argument('request_file', type=Path)
    execute.add_argument('--model',choices=['mock','bailian'],default='mock')
    status = commands.add_parser('status', help='Read current task state without modifications')
    status.add_argument('task_id')
    drive = commands.add_parser('drive', help='Drive an already-created pending run')
    drive.add_argument('run_id')
    drive.add_argument('--model',choices=['mock','bailian'],default='mock')
    commands.add_parser('upgrade',help='Explicitly upgrade a v1 runtime database to v2')
    recover=commands.add_parser('reconcile',help='Apply cached remote receipts without resending')
    recover.add_argument('task_id')
    trace = commands.add_parser('trace', help='Export persisted task events, never overwrite')
    trace.add_argument('task_id')
    trace.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    runtime = Runtime(args.db, args.data)
    exit_code = 0
    try:
        policy=None
        if getattr(args,'model',None)=='bailian' or args.command=='reconcile':
            from .bailian import BailianPolicy
            policy=BailianPolicy()
        if args.command == 'execute':
            request = parse_request(args.request_file.read_text(encoding='utf-8-sig'))
            reply = runtime.request(request)
            if reply['status'] != 'succeeded':
                result, exit_code = reply, 2
            else:
                data = reply['data']
                if request['operation'] in ('task.run', 'task.resume'):
                    task = runtime.drive(data['run_id'],policy=policy)
                else:
                    task = runtime.status(data['task_id'])
                result = {'response': reply, 'task': task}
                if task['task_status'] in ('partial', 'incomplete', 'waiting_input'):
                    exit_code = 3
        elif args.command == 'status':
            result = runtime.status(args.task_id)
        elif args.command == 'drive':
            result = runtime.drive(args.run_id,policy=policy)
            exit_code = 0 if result['task_status']=='completed' else 3
        elif args.command=='upgrade':
            result=runtime.upgrade()
        elif args.command=='reconcile':
            from .remote import reconcile
            result=reconcile(runtime,args.task_id,policy.client)
            exit_code=0 if result['task_status']=='completed' else 3
        else:
            events = runtime.trace(args.task_id)
            with args.output.open('x', encoding='utf-8') as handle:
                for event in events:
                    handle.write(json.dumps(event, ensure_ascii=False)+'\n')
            result = {'task_id': args.task_id, 'event_count': len(events), 'trace_path': str(args.output.resolve())}
    except (ToolError, OSError, UnicodeError) as exc:
        result = {'status': 'unknown' if isinstance(exc, ToolError) and exc.code=='PENDING_RECONCILIATION' else 'failed',
                  'error': {'code': exc.code if isinstance(exc, ToolError) else 'INVALID_REQUEST' if isinstance(exc, UnicodeError) else 'IO_ERROR', 'message': str(exc)}}
        exit_code = 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
