"""Explicit paid evaluation: two repeats plus four fixed perturbations, no retries."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from content_planner.api_budget import BudgetLedger
from content_planner.bailian import BailianClient,BailianPolicy
from content_planner.runtime import Runtime
from record_live import evaluate
from record_phase3 import fingerprint,dump
from phase5_metrics import inspect_record


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    base=json.loads((ROOT/'data/live_scenarios.json').read_text(encoding='utf8'))['scenarios']
    variants=json.loads((ROOT/'data/phase5_perturbations.json').read_text(encoding='utf8'))['scenarios']
    schedule=[(f'round{r}',c) for r in (2,3) for c in base]+[('perturbations',c) for c in variants]
    ledger=BudgetLedger();policy=BailianPolicy(BailianClient(ledger=ledger))
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    states=ROOT/'var'/('phase5-'+output.name);states.mkdir(parents=True,exist_ok=False)
    frozen=fingerprint()
    report={'started_utc':datetime.now(timezone.utc).isoformat(),'planned_tasks':len(schedule),'source_sha256':frozen,'budget_before':ledger.summary(),'records':[]}
    dump(output/'summary.json',report)
    for group,case in schedule:
        if fingerprint()!=frozen:raise RuntimeError('Frozen source changed; stopped')
        folder=output/group;folder.mkdir(exist_ok=True)
        dump(folder/(case['id']+'-input.json'),case)
        runtime=Runtime(states/(group+'-'+case['id']+'.sqlite3'),ROOT/'data')
        try:
            record=evaluate(case,runtime,policy)
            record['independent_checks']=inspect_record(record,case)
            record['passed']=record['passed'] and all(record['independent_checks'].values())
        except Exception as exc:
            # Persist only a sanitized code. Never emit raw provider data or credentials.
            record={'case':case['id'],'passed':False,'fatal_error':getattr(exc,'code',type(exc).__name__),'trace':runtime.trace(case['id']) if runtime.db.path.exists() else []}
        trace=record.pop('trace')
        dump(folder/(case['id']+'-actual.json'),record)
        (folder/(case['id']+'-trace.jsonl')).write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in trace),encoding='utf8')
        report['records'].append({'group':group,'id':case['id'],'passed':record['passed'],'status':record.get('final',{}).get('task_status'),'stop_reason':record.get('final',{}).get('stop_reason'),'fatal_error':record.get('fatal_error')})
        report.update(budget_after=ledger.summary(),source_unchanged=fingerprint()==frozen)
        dump(output/'summary.json',report)
        print(json.dumps(report['records'][-1]|{'accounted_yuan':report['budget_after']['accounted_yuan']},ensure_ascii=False),flush=True)
        stop=record.get('final',{}).get('stop_reason') or ''
        if 'fatal_error' in record or stop.startswith('model_') or stop=='pending_reconciliation' or not report['source_unchanged']:break
    report.update(finished_utc=datetime.now(timezone.utc).isoformat(),tasks_run=len(report['records']),tasks_passed=sum(r['passed'] for r in report['records']))
    dump(output/'summary.json',report)
    return 0 if report['tasks_run']==len(schedule) and report['tasks_passed']==len(schedule) else 1


if __name__=='__main__':raise SystemExit(main())
