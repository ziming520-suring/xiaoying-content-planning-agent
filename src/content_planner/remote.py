"""Two-phase remote generation: durable reserve -> network -> atomic settlement."""
import json
from .catalog import ToolError,canonical,fields,require,string
from .state import pack,persist_state,digest
from .locking import task_lock


def _row(conn,remote_id):
    row=conn.execute('SELECT * FROM remote_actions WHERE remote_id=?',(remote_id,)).fetchone()
    require(row is not None,'Remote operation missing','STORAGE_CORRUPT')
    return dict(row)


def _prepare(runtime,conn,run_id,action,generation_protocol=None):
    run=runtime._run(conn,run_id)
    require(run['status']=='running' and run['action_count']<run['max_actions'],'Run cannot execute','INVALID_STATE')
    remote_id=f'{run_id}:generation:{run["action_count"]+1}'
    if conn.execute('SELECT 1 FROM remote_actions WHERE remote_id=?',(remote_id,)).fetchone():
        return _row(conn,remote_id)
    try:
        fields(action,['tool','arguments','reason']);string(action['reason'],'reason',1000)
        fields(action['arguments'],['plan_id','plan_version','step_id'])
    except ToolError:
        return None  # Normal action validation records the rejection.
    task_row,task,_,w=runtime._load(conn,run['task_id'])
    before=pack(w);captured={}
    def reserve(topic,mode,materials):
        captured.update(topic=topic,mode=mode,materials=materials)
        if generation_protocol is not None:captured['generation_protocol']=generation_protocol
        raise ToolError('PENDING_RECONCILIATION','Remote generation reserved, awaiting provider result')
    w._script_generator=reserve
    args=action['arguments']
    response=w.generate_script(args['plan_id'],args['plan_version'],args['step_id'])
    if not captured:
        return None  # Reused result or precheck refusal: no network call needed.
    require(response['tool_status']=='unknown','Expected durable pending generation','STORAGE_CORRUPT')
    reserved=pack(w)
    persist_state(conn,task['task_id'],reserved)
    conn.execute('INSERT INTO remote_actions VALUES (?,?,?,?,?,?,?,?,?)',
        (remote_id,run_id,run['action_count']+1,canonical(action),canonical(before),digest(canonical(reserved)),canonical(captured),'pending',None))
    return _row(conn,remote_id)


def _fetch(client,row,*,allow_send):
    p=json.loads(row['payload_json']);key=row['remote_id']
    if not allow_send:
        state=client.outcome(key)
        if state=='unknown':return {'kind':'unknown','code':'MODEL_PENDING'}
        if state=='not_sent':return {'kind':'not_sent','code':'MODEL_NOT_SENT'}
    try:
        protocol=p.get('generation_protocol')
        require(protocol in (None,'source_sentences_v2'),'Unknown generation protocol','MODEL_PROTOCOL_ERROR')
        generate=client.generate_script if protocol else getattr(client,'generate_script_legacy',client.generate_script)
        result=generate(p['topic'],p['mode'],p['materials'],key)
        return {'kind':'succeeded','script':result}
    except Exception as exc:
        code=exc.code if isinstance(exc,ToolError) else 'MODEL_PROTOCOL_ERROR'
        state=client.outcome(key)
        return {'kind':'not_sent' if state=='not_sent' else 'failed' if state=='received' else 'unknown','code':code}


def _finish(runtime,conn,remote_id):
    row=_row(conn,remote_id)
    require(row['status']=='ready','Remote result is not ready','PENDING_RECONCILIATION')
    run=runtime._run(conn,row['run_id'])
    task_row,task,_,w=runtime._load(conn,run['task_id'])
    require(task_row['last_run']==row['run_id'] and run['action_count']==row['sequence']-1,'Remote sequence conflict','STORAGE_CORRUPT')
    require(task_row['state_hash']==row['reserved_hash'],'Reserved state changed','STORAGE_CORRUPT')
    outcome=json.loads(row['outcome_json']);action=json.loads(row['action_json'])
    conn.execute("UPDATE runs SET status='running' WHERE run_id=?",(row['run_id'],))
    if outcome['kind']=='unknown':
        response={'tool_status':'unknown','result':None,'error':{'code':outcome['code'],'message':'Provider outcome unresolved; no automatic resend'}}
        event={'run_id':row['run_id'],'task_id':task['task_id'],'sequence':row['sequence'],'execution_mode':'bailian_live',
               'action':action,'observation':response,'tool_observations':w.observations()[-1:],
               'spent_cost':w._spent,'logical_time':w.snapshot()['logical_time'],'remote_id':remote_id}
        conn.execute('INSERT INTO events VALUES (?,?,?)',(row['run_id'],row['sequence'],canonical(event)))
        conn.execute('UPDATE runs SET action_count=? WHERE run_id=?',(row['sequence'],row['run_id']))
        conn.execute("UPDATE remote_actions SET status='unknown' WHERE remote_id=?",(remote_id,))
        runtime._stop(conn,row['run_id'],'pending_reconciliation')
        return event
    # Replace only this pending reservation with its settled execution. The immutable
    # before-state and exact reserved hash prevent clearing unrelated expenditures.
    persist_state(conn,task['task_id'],json.loads(row['before_state_json']))
    error=ToolError(outcome.get('code','MODEL_PROTOCOL_ERROR'),'Remote generation did not produce a valid result')
    event=runtime._act(conn,row['run_id'],action,{},
                       prepared_script=outcome['script'] if outcome['kind']=='succeeded' else error if outcome['kind']=='failed' else None,
                       external_error=error if outcome['kind']=='not_sent' else None)
    conn.execute("UPDATE remote_actions SET status='applied' WHERE remote_id=?",(remote_id,))
    if outcome['kind']=='not_sent':runtime._stop(conn,row['run_id'],outcome['code'].lower())
    return event


def execute_generation(runtime,run_id,action,client):
    row=runtime.db.write(lambda c:_prepare(runtime,c,run_id,action,getattr(client,'generation_protocol',None)))
    if row is None:
        return runtime.db.write(lambda c:runtime._act(c,run_id,action,{}))
    require(row['status']=='pending','Remote call must be reconciled before continuing','PENDING_RECONCILIATION')
    outcome=_fetch(client,row,allow_send=True)  # Deliberately outside every SQLite transaction.
    runtime.db.write(lambda c:c.execute("UPDATE remote_actions SET status='ready',outcome_json=? WHERE remote_id=?",(canonical(outcome),row['remote_id'])) and {'ready':True})
    return runtime.db.write(lambda c:_finish(runtime,c,row['remote_id']))


def reconcile(runtime,task_id,client):
    """Recover only cached responses; this entry point never sends another request."""
    with task_lock(runtime.db.path,task_id):
        rows=runtime.db.read(lambda c:[dict(r) for r in c.execute("SELECT a.* FROM remote_actions a JOIN runs r ON a.run_id=r.run_id WHERE r.task_id=? AND a.status IN ('pending','ready') ORDER BY a.sequence",(task_id,))])
        for row in rows:
            if row['status']=='pending':
                outcome=_fetch(client,row,allow_send=False)
                runtime.db.write(lambda c:c.execute("UPDATE remote_actions SET status='ready',outcome_json=? WHERE remote_id=?",(canonical(outcome),row['remote_id'])) and {'ready':True})
            runtime.db.write(lambda c:_finish(runtime,c,row['remote_id']))
        # Recovery applies receipts, not a fresh policy loop. Stop until explicit resume.
        current=runtime.status(task_id)
        if current['task_status']=='running':
            runtime.db.write(lambda c:runtime._stop(c,current['run_id'],'interrupted'))
        return runtime.status(task_id)
