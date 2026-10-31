"""Explicitly paid entry point. Never invoked by unittest discovery."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from content_planner.api_budget import BudgetLedger
from content_planner.bailian import BailianClient,BailianPolicy
from content_planner.catalog import ToolError
from content_planner.runtime import Runtime
from record_phase3 import fingerprint,dump,req


def evaluate(case,runtime,policy):
    task=dict(task_id=case['id'],topic=case['topic'],budget_limit=case['budget'],
              start_time='2026-10-30T09:00:00+08:00',deadline=case['deadline'])
    creation=runtime.request(req('task.create','create',**task))
    if creation['status']!='succeeded':raise ToolError('SETUP_ERROR','Task creation failed')
    run=runtime.request(req('task.run','run',task_id=case['id'],max_actions=16))
    if run['status']!='succeeded':raise ToolError('SETUP_ERROR','Run creation failed')
    final=runtime.drive(run['data']['run_id'],policy=policy,test_failures=case.get('test_failures'))
    trace=runtime.trace(case['id'])
    estimates=[e['observation']['result'] for e in trace if e['action'].get('tool')=='estimate_plan' and e['observation']['tool_status']=='succeeded']
    failures=[e for e in trace if e['observation']['tool_status']=='failed']
    scripts=[r for r in final['tool_results'].values() if r['operation']=='generate_script']
    checks={'status':final['task_status']==case['expected_status'],
            'budget':final['budget']['spent_cost']+final['budget']['reserved_cost']<=case['budget'],
            'online_scripts':all(s['generator']=='bailian_live' for s in scripts),
            'saved_iff_completed':bool(final['saved_plan'])==(final['task_status']=='completed')}
    if case.get('require_replan'):
        checks['observed_infeasible_then_replanned']=any(not e['feasible'] for e in estimates) and any(e['feasible'] for e in estimates)
    if case.get('require_failure'):checks['injected_failure_observed']=bool(failures)
    return {'case':case['id'],'execution_mode':'bailian_live','passed':all(checks.values()),'checks':checks,
            'input':task,'create_response':creation,'run_response':run,'final':final,'trace':trace,
            'failed_action_count':len(failures),'estimates':[{'version':e['plan_version'],'feasible':e['feasible'],'total':e['projected_total_cost']} for e in estimates]}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--cases',nargs='+',default=['L01','L02','L03','L04','L05','L06','L07'])
    args=parser.parse_args()
    cases=json.loads((ROOT/'data/live_scenarios.json').read_text(encoding='utf-8'))['scenarios']
    selected=[c for c in cases if c['id'] in args.cases]
    if len(selected)!=len(set(args.cases)):raise ValueError('Unknown case')
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    state_root=ROOT/'var'/('live-'+output.name)
    state_root.mkdir(parents=True,exist_ok=False)
    before=fingerprint();ledger=BudgetLedger();policy=BailianPolicy(BailianClient(ledger=ledger))
    report={'execution_mode':'bailian_live','started_utc':datetime.now(timezone.utc).isoformat(),
            'budget_before':ledger.summary(),'scenarios':[],'source_sha256':before}
    dump(output/'summary.json',report)
    print(json.dumps({'starting':args.cases,'budget':ledger.summary()},ensure_ascii=False),flush=True)
    for case in selected:
        require_unchanged=before==fingerprint()
        if not require_unchanged:raise RuntimeError('Frozen sources changed; stopping')
        dump(output/f'{case["id"]}-input.json',case)
        runtime=Runtime(state_root/(case['id']+'.sqlite3'),ROOT/'data')
        try:
            record=evaluate(case,runtime,policy)
        except ToolError as exc:
            record={'case':case['id'],'passed':False,'fatal_error':exc.code,'trace':runtime.trace(case['id']) if runtime.db.path.exists() else []}
        trace=record.pop('trace')
        dump(output/f'{case["id"]}-actual.json',record)
        (output/f'{case["id"]}-trace.jsonl').write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in trace),encoding='utf-8')
        report['scenarios'].append({'id':case['id'],'passed':record['passed'],'status':record.get('final',{}).get('task_status'),'stop_reason':record.get('final',{}).get('stop_reason')})
        report.update(budget_after=ledger.summary(),source_unchanged=before==fingerprint())
        dump(output/'summary.json',report)
        print(json.dumps(report['scenarios'][-1]|{'api_accounted_yuan':report['budget_after']['accounted_yuan']},ensure_ascii=False),flush=True)
        stop=record.get('final',{}).get('stop_reason') or ''
        if 'fatal_error' in record or stop.startswith('model_') or not report['source_unchanged']:break
    report.update(scenario_count=len(report['scenarios']),scenario_passed=sum(s['passed'] for s in report['scenarios']),finished_utc=datetime.now(timezone.utc).isoformat())
    dump(output/'summary.json',report)
    return 0 if len(report['scenarios'])==len(selected) and all(s['passed'] for s in report['scenarios']) else 1


if __name__=='__main__':raise SystemExit(main())
