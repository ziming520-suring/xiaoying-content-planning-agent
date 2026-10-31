"""Free comparison of fixed and baseline evidence; no provider calls."""
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'));sys.path.insert(0,str(ROOT/'tests'))
from phase5_metrics import inspect_record
from content_planner.grounding import validate


def read(path):return json.loads(path.read_text(encoding='utf8'))


def main():
    rows=[];hashes={};script_reviews=[]
    for group in ('round2','round3','perturbations'):
        ids=[f'P{i:02}' for i in range(1,5)] if group=='perturbations' else [f'L{i:02}' for i in range(1,8)]
        for name in ids:
            folder=ROOT/'examples/phase6/live-reviewed'/group
            case=read(folder/(name+'-input.json'));record=read(folder/(name+'-actual.json'))
            old=ROOT/'examples/phase5/live-evaluation'/group
            assert case==read(old/(name+'-input.json'))
            trace=[json.loads(line) for line in (folder/(name+'-trace.jsonl')).read_text(encoding='utf8').splitlines()]
            checks=inspect_record(record|{'trace':trace},case)
            final=record['final'];checks['expected_status']=final['task_status']==case['expected_status']
            scripts=[s for s in final['tool_results'].values() if s['operation']=='generate_script']
            checks['online']=all(s.get('generator')=='bailian_live' and s.get('content_policy')=='source_sentences_v2' for s in scripts) and (bool(scripts) or final['task_status']!='completed')
            for s in scripts:
                validate({k:s[k] for k in ('title','script','source_ids')},s['sources'])
                script_reviews.append({'group':group,'id':name,'result_id':s['result_id'],'title':s['title'],'script':s['script'],'source_ids':s['source_ids'],'source_sentence_check':True})
            failures=[e for e in trace if e['observation']['tool_status']=='failed']
            injected=[e for e in failures if e['observation']['error']=={'code':'TOOL_FAILED','message':'Injected deterministic tool failure'}]
            rows.append({'group':group,'id':name,'passed':all(checks.values()),'checks':checks,'status':final['task_status'],'non_injected_failures':len(failures)-len(injected),'injected_failures':len(injected),'actions':len(trace)})
            for p in (folder/(name+'-input.json'),folder/(name+'-actual.json'),folder/(name+'-trace.jsonl')):hashes[p.relative_to(ROOT).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
    live=read(ROOT/'examples/phase6/live-reviewed/summary.json')
    unchanged=all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in live['source_sha256'].items())
    release=read(ROOT/'examples/phase6/release-manifest.json')
    release_matches=all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in release['source_sha256'].items())
    result={'tasks':len(rows),'tasks_passed':sum(r['passed'] for r in rows),'non_injected_failures':sum(r['non_injected_failures'] for r in rows),'injected_failures':sum(r['injected_failures'] for r in rows),'scripts_checked':len(script_reviews),'source_unchanged_since_live':unchanged,'release_source_matches':release_matches,'budget':live['budget_after'],'records':rows,'script_reviews':script_reviews,'evidence_sha256':hashes}
    (ROOT/'examples/phase6/comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('records','script_reviews','evidence_sha256')},ensure_ascii=False))
    assert release_matches and len(rows)==18  # A recorded model failure is retained, not a corrupt report.


if __name__=='__main__':main()
