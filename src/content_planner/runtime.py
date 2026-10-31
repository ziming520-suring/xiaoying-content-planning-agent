"""Durable local task runtime. Decision calls never run in a write transaction."""
from copy import deepcopy
import json
from pathlib import Path
import uuid

from .catalog import Catalog, ToolError, canonical, fields, instant, require, string
from .contracts import normalize, response
from .database import Database
from .locking import task_lock
from .offline import OfflinePolicy
from .state import digest, pack, persist_state, restore, summary, terminal_status
from .tools import ToolWorkbench
from .artifacts import save_in_transaction


class Runtime:
    def __init__(self, database, data_directory):
        self.db = Database(database)
        self.data_directory = Path(data_directory)

    def _load(self, conn, task_id):
        row = conn.execute('SELECT * FROM tasks WHERE task_id=?', (task_id,)).fetchone()
        require(row is not None, 'Unknown task', 'TASK_NOT_FOUND')
        for name in ('input', 'catalog', 'state'):
            require(digest(row[name+'_json']) == row[name+'_hash'], 'Checkpoint checksum mismatch', 'STORAGE_CORRUPT')
        try:
            task = json.loads(row['input_json'])
            catalog_data = json.loads(row['catalog_json'])
            catalog = Catalog(catalog_data['materials'], catalog_data['costs'])
            saved = json.loads(row['state_json'])
            w = restore(catalog, task, saved, conn) if saved is not None else None
            require(task['task_id'] == task_id, 'Task identity mismatch', 'STORAGE_CORRUPT')
            require(row['status'] in {'waiting_input', 'ready', 'running', 'completed', 'partial', 'incomplete'}, 'Invalid task status', 'STORAGE_CORRUPT')
            require((w is None) == any(task[k] is None for k in ('topic', 'budget_limit', 'deadline')), 'Missing state', 'STORAGE_CORRUPT')
            if row['status']=='completed':
                require(w is not None and w._active in w._saved, 'Completion without saved plan', 'STORAGE_CORRUPT')
            return row, task, catalog, w
        except (KeyError, ValueError, TypeError) as exc:
            if isinstance(exc, ToolError) and exc.code == 'STORAGE_CORRUPT':
                raise
            raise ToolError('STORAGE_CORRUPT', f'Invalid persisted task: {exc}') from exc

    def _status(self, conn, task_id):
        row, task, _, w = self._load(conn, task_id)
        result=summary(w, task, row['status'], row['stop_reason'], row['last_run'])
        if row['last_run']:
            result['execution_mode']=self._run(conn,row['last_run'])['mode']
        return result

    def upgrade(self):return self.db.upgrade()

    def status(self, task_id):
        string(task_id, 'task_id', 100)
        return self.db.read(lambda c: self._status(c, task_id))

    def trace(self, task_id):
        def read(conn):
            self._load(conn, task_id)
            return [json.loads(r[0]) for r in conn.execute('SELECT e.payload FROM events e JOIN runs r ON e.run_id=r.run_id WHERE r.task_id=? ORDER BY e.rowid', (task_id,))]
        return self.db.read(read)

    def _replay(self, conn, req):
        row = conn.execute('SELECT normalized,response FROM replays WHERE operation=? AND request_id=?', (req['operation'], req['request_id'])).fetchone()
        if row:
            require(row[0] == canonical(req), 'Same request key with different parameters', 'REQUEST_CONFLICT')
            return json.loads(row[1])
        return None

    def request(self, raw):
        req = raw if isinstance(raw, dict) else {}
        try:
            req = normalize(raw)
            if self.db.path.exists() and not (req['operation']=='task.create' and self.db.empty()):
                replay = self.db.read(lambda c: self._replay(c, req))
                if replay is not None:
                    return replay
            def submit(conn):
                replay = self._replay(conn, req)
                if replay is not None:
                    return replay
                data = self._submit(conn, req)
                result = response(req, data)
                conn.execute('INSERT INTO replays VALUES (?,?,?,?)', (req['operation'], req['request_id'], canonical(req), canonical(result)))
                return result
            if req['operation'] in ('task.run', 'task.resume'):
                require(self.db.path.is_file(), 'Task database does not exist', 'TASK_NOT_FOUND')
                with task_lock(self.db.path, req['payload']['task_id']):
                    return self.db.write(submit)
            return self.db.write(submit, initialize=req['operation']=='task.create')
        except (ToolError, OSError, RecursionError) as exc:
            error = exc if isinstance(exc, ToolError) else ToolError('STORAGE_FAILED' if isinstance(exc, OSError) else 'INVALID_REQUEST', str(exc))
            return response(req, error=error)

    def _submit(self, conn, req):
        op, p = req['operation'], req['payload']
        task_id = p['task_id']
        if op == 'task.create':
            previous = conn.execute('SELECT creation_json FROM tasks WHERE task_id=?', (task_id,)).fetchone()
            if previous:
                require(previous[0] == canonical(p), 'Task identity already bound to different inputs', 'TASK_CONFLICT')
                return self._status(conn, task_id)
            catalog = Catalog.load(self.data_directory)
            task_json, cat_json = canonical(p), canonical(catalog.export())
            missing = any(p[k] is None for k in ('topic', 'budget_limit', 'deadline'))
            conn.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (task_id, task_json, task_json, digest(task_json), cat_json, digest(cat_json), 'null', digest('null'),
                 'waiting_input' if missing else 'ready', None, None))
            if not missing:
                w = ToolWorkbench(catalog, p, conn, artifact_writer=save_in_transaction)
                persist_state(conn, task_id, pack(w))
            return self._status(conn, task_id)
        row, task, catalog, w = self._load(conn, task_id)
        if op == 'task.complete_input':
            require(row['status']=='waiting_input', 'Task is not waiting for input', 'INVALID_STATE')
            for key, value in p.items():
                if key != 'task_id':
                    require(task[key] is None, f'{key} is already fixed', 'INVALID_STATE')
                    task[key] = value
            if task['deadline'] is not None:
                require(instant(task['deadline']) > instant(task['start_time']), 'Deadline must be after start')
            missing = any(task[k] is None for k in ('topic', 'budget_limit', 'deadline'))
            text = canonical(task)
            conn.execute('UPDATE tasks SET input_json=?,input_hash=?,status=? WHERE task_id=?',
                         (text, digest(text), 'waiting_input' if missing else 'ready', task_id))
            if not missing:
                persist_state(conn, task_id, pack(ToolWorkbench(catalog, task, conn, artifact_writer=save_in_transaction)))
            return self._status(conn, task_id)
        require(w is not None, 'Complete missing inputs before running', 'INVALID_STATE')
        last = conn.execute('SELECT * FROM runs WHERE run_id=?', (row['last_run'],)).fetchone() if row['last_run'] else None
        if last and last['status']=='running':
            # The caller holds the OS lock, so no live driver owns this running row.
            self._stop(conn, last['run_id'], 'interrupted')
            row, task, catalog, w = self._load(conn, task_id)
            last = conn.execute('SELECT * FROM runs WHERE run_id=?', (row['last_run'],)).fetchone()
        if op == 'task.run' and last:
            return {'task_id': task_id, 'run_id': last['run_id']}
        if op == 'task.resume':
            require(not last or last['status'] != 'pending', 'A resume is already pending; drive that run first', 'INVALID_STATE')
            require(row['status'] in ('partial', 'incomplete'), 'Resume requires a stopped incomplete task', 'INVALID_STATE')
            require(not any(a['status']=='unknown' for a in w._attempts), 'Unresolved attempts block resume', 'PENDING_RECONCILIATION')
        else:
            require(row['status']=='ready', 'Task is not ready', 'INVALID_STATE')
        run_id = str(uuid.uuid4())
        conn.execute('INSERT INTO runs(run_id,task_id,status,max_actions,action_count) VALUES (?,?,?,?,?)', (run_id, task_id, 'pending', p['max_actions'], 0))
        conn.execute('UPDATE tasks SET last_run=?,stop_reason=NULL WHERE task_id=?', (run_id, task_id))
        return {'task_id': task_id, 'run_id': run_id}

    def _run(self, conn, run_id):
        row = conn.execute('SELECT * FROM runs WHERE run_id=?', (run_id,)).fetchone()
        require(row is not None, 'Unknown run', 'TASK_NOT_FOUND')
        return row

    def _stop(self, conn, run_id, reason):
        run = self._run(conn, run_id)
        row, task, _, w = self._load(conn, run['task_id'])
        status = terminal_status(w)
        if any(a['status']=='unknown' for a in w._attempts):
            reason = 'pending_reconciliation'
        conn.execute('UPDATE runs SET status=? WHERE run_id=?', ('completed' if status=='completed' else 'stopped', run_id))
        conn.execute('UPDATE tasks SET status=?,stop_reason=? WHERE task_id=?', (status, None if status=='completed' else reason, task['task_id']))
        return self._status(conn, task['task_id'])

    def _context(self, conn, run_id):
        run = self._run(conn, run_id)
        row, task, catalog, w = self._load(conn, run['task_id'])
        result = summary(w, task, row['status'], row['stop_reason'], row['last_run'])
        result.update(topic=task['topic'], plan_history=list(w._plans.values()), observations=w.observations(),
                      next_action_sequence=run['action_count']+1,remaining_actions=run['max_actions']-run['action_count'],
                      available_modes=catalog.export()['costs']['rates'],
                      history=[json.loads(r[0]) for r in conn.execute('SELECT e.payload FROM events e JOIN runs r ON e.run_id=r.run_id WHERE r.task_id=? ORDER BY e.rowid',(task['task_id'],))])
        return dict(run), result

    def _act(self, conn, run_id, action, test_failures, *, prepared_script=None, external_error=None):
        run = self._run(conn, run_id)
        require(run['status']=='running' and run['action_count'] < run['max_actions'], 'Run cannot execute more actions', 'INVALID_STATE')
        row, task, _, w = self._load(conn, run['task_id'])
        finished, stop_reason = False, None
        previous_observations = len(w._observations)
        try:
            if external_error is not None:
                raise external_error
            fields(action, ['tool', 'arguments', 'reason'])
            string(action['reason'], 'reason', 1000)
            name, args = action['tool'], action['arguments']
            require(isinstance(name, str) and name in {'search_materials', 'estimate_plan', 'generate_script', 'save_plan', 'finish'}, 'Unknown action')
            expected = {'search_materials': ['query'], 'estimate_plan': ['steps'], 'generate_script': ['plan_id', 'plan_version', 'step_id'], 'save_plan': ['plan_id', 'plan_version'], 'finish': ['stop_reason']}
            fields(args, expected[name])
            if name == 'finish':
                string(args['stop_reason'], 'stop_reason', 100)
                finished, stop_reason = True, args['stop_reason']
                result = {'tool_status': 'succeeded', 'result': {'stopped': True}, 'error': None}
            else:
                if test_failures.get(name, 0):
                    w.inject_failure(name)
                    test_failures[name] -= 1
                if name == 'generate_script':
                    if prepared_script is not None:
                        def prepared(*args):
                            if isinstance(prepared_script,ToolError):raise prepared_script
                            return deepcopy(prepared_script)
                        w._script_generator=prepared
                    result = w.generate_script(args['plan_id'], args['plan_version'], args['step_id'])
                elif name == 'save_plan':
                    result = w.save_plan(args['plan_id'], args['plan_version'])
                else:
                    result = getattr(w, name)(**args)
        except ToolError as exc:
            result = {'tool_status': 'failed', 'result': None, 'error': {'code': exc.code, 'message': str(exc)}}
        count = run['action_count'] + 1
        persist_state(conn, task['task_id'], pack(w))
        event = {'run_id': run_id, 'task_id': task['task_id'], 'sequence': count, 'execution_mode': run['mode'],
                 'action': action, 'observation': result, 'tool_observations': w.observations()[previous_observations:],
                 'spent_cost': w._spent, 'logical_time': w.snapshot()['logical_time']}
        conn.execute('INSERT INTO events VALUES (?,?,?)', (run_id, count, canonical(event)))
        conn.execute('UPDATE runs SET action_count=? WHERE run_id=?', (count, run_id))
        if w._active in w._saved:
            finished, stop_reason = True, 'completed'
        if result['tool_status']=='unknown':
            finished, stop_reason = True, 'pending_reconciliation'
        if finished or count >= run['max_actions']:
            self._stop(conn, run_id, stop_reason if finished else 'step_limit')
        return event

    def drive(self, run_id, policy=None, *, test_failures=None, after_action=None):
        """test_failures/after_action are trusted test hooks, never CLI/model inputs."""
        string(run_id, 'run_id', 200)
        run = self.db.read(lambda c: dict(self._run(c, run_id)))
        policy = policy or OfflinePolicy()
        failures = deepcopy(test_failures or {})
        with task_lock(self.db.path, run['task_id']):
            def begin(conn):
                current = self._run(conn, run_id)
                if current['status']=='running':
                    self._stop(conn, run_id, 'interrupted')
                    return False
                if current['status']!='pending':
                    return False
                task_row, _, _, _ = self._load(conn, current['task_id'])
                require(task_row['last_run']==run_id, 'Old run is not active', 'INVALID_STATE')
                conn.execute("UPDATE runs SET status='running' WHERE run_id=?", (run_id,))
                conn.execute('UPDATE runs SET mode=? WHERE run_id=?',(getattr(policy,'name','offline_mock'),run_id))
                conn.execute("UPDATE tasks SET status='running' WHERE task_id=?", (current['task_id'],))
                return True
            if not self.db.write(begin):
                return self.status(run['task_id'])
            while True:
                current, context = self.db.read(lambda c: self._context(c, run_id))
                if current['status']!='running':
                    return self.status(run['task_id'])
                try:
                    action = policy.next_action(deepcopy(context))  # No write transaction held.
                    canonical(action)
                except Exception as exc:
                    reason=exc.code.lower() if isinstance(exc,ToolError) else 'model_failed'
                    return self.db.write(lambda c: self._stop(c, run_id, reason))
                try:
                    if getattr(policy,'name','offline_mock')=='bailian_live' and isinstance(action,dict) and action.get('tool')=='generate_script' and not failures.get('generate_script',0):
                        from .remote import execute_generation
                        event=execute_generation(self,run_id,action,policy.client)
                    else:
                        event = self.db.write(lambda c: self._act(c, run_id, action, failures))
                except ToolError as exc:
                    if exc.code == 'PENDING_RECONCILIATION':
                        raise  # Never blindly overwrite possibly committed action state.
                    return self.db.write(lambda c: self._stop(c, run_id, 'storage_failed'))
                if after_action:
                    after_action(event)
