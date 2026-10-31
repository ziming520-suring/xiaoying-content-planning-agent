"""Free, repeatable audit of saved phase4/5 evidence; never calls a model."""
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tests'))
from phase5_metrics import inspect_record


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def main():
    rows=[];hashes={}
    groups=[('baseline',ROOT/'examples/phase4/live-final')]+[(g,ROOT/'examples/phase5/live-evaluation'/g) for g in ('round2','round3','perturbations')]
    for group,folder in groups:
        expected=[f'P{i:02}' for i in range(1,5)] if group=='perturbations' else [f'L{i:02}' for i in range(1,8)]
        for name in expected:
            actual=folder/(name+'-actual.json');casefile=folder/(name+'-input.json');tracefile=folder/(name+'-trace.jsonl')
            record=read(actual);case=read(casefile)
            trace=[json.loads(line) for line in tracefile.read_text(encoding='utf8').splitlines()]
            record['trace']=trace
            for p in (actual,casefile,tracefile):hashes[p.relative_to(ROOT).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
            checks=inspect_record(record,case)
            failure_events=[e for e in trace if e['observation']['tool_status']=='failed']
            injected=[e for e in failure_events if e['observation']['error']=={'code':'TOOL_FAILED','message':'Injected deterministic tool failure'}]
            scripts=[r for r in record['final']['tool_results'].values() if r['operation']=='generate_script']
            checks['expected_status']=record['final']['task_status']==case['expected_status']
            checks['online_scripts']=all(s.get('generator')=='bailian_live' for s in scripts) and (bool(scripts) or record['final']['task_status']!='completed')
            rows.append({'group':group,'id':name,'status':record['final']['task_status'],
                         'workflow_passed':record['passed'] and all(checks.values()),'checks':checks,
                         'action_count':len(trace),'injected_failures':len(injected),
                         'other_failed_actions':len(failure_events)-len(injected),'script_count':len(scripts),
                         'business_cost':record['final']['budget']['spent_cost']})
    baseline=read(ROOT/'examples/phase4/live-final/summary.json')['source_sha256']
    frozen=read(ROOT/'examples/phase5/live-evaluation/summary.json')['source_sha256']
    product_keys=[p for p in baseline if p.startswith('src/') or p in ('data/materials.json','data/tool_costs.json','data/live_scenarios.json')]
    product_same=all(baseline[p]==hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==frozen[p] for p in product_keys)
    review=read(ROOT/'examples/phase5/content-review.json')
    assert len(review['scripts'])==sum(r['script_count'] for r in rows)
    assert len({(r['group'],r['case'],r['result_id']) for r in review['scripts']})==len(review['scripts'])
    for entry in review['scripts']:
        folder=dict(groups)[entry['group']];record=read(folder/(entry['case']+'-actual.json'))
        script=record['final']['tool_results'][entry['result_id']]
        assert hashlib.sha256((script['title']+'\n'+script['script']).encode()).hexdigest()==entry['text_sha256']
        assert all(quote in script['title']+'\n'+script['script'] for quote in entry['unsupported_quotes'])
    report={'tasks':len(rows),'workflow_passed':sum(r['workflow_passed'] for r in rows),
            'hard_constraint_passed':sum(r['checks']['budget'] and r['checks']['deadline'] and r['checks']['save_state'] for r in rows),
            'product_matches_phase4':product_same,'content_reviewed':len(review['scripts']),
            'content_with_unsupported_factual_claims':sum(bool(r['unsupported_quotes']) for r in review['scripts']),
            'injected_failure_events':sum(r['injected_failures'] for r in rows),
            'other_failed_actions':sum(r['other_failed_actions'] for r in rows),
            'records':rows,'evidence_sha256':hashes,
            'budget':read(ROOT/'examples/phase5/live-evaluation/summary.json')['budget_after']}
    (ROOT/'examples/phase5/aggregate-summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('records','evidence_sha256')},ensure_ascii=False))
    assert product_same


if __name__=='__main__':main()
