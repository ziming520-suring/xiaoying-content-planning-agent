"""One offline tool demonstration; explicit sequence, no model decision claims."""
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'tests'))
from record_phase2 import execute

if __name__ == '__main__':
    scenario = json.loads((ROOT / 'data/scenarios.json').read_text(encoding='utf-8'))['scenarios'][3]
    with tempfile.TemporaryDirectory() as temp:
        result = execute(scenario, Path(temp) / 'artifacts.sqlite3')
    print(json.dumps({'mode': result['execution_mode'], 'actual': result['actual'],
                      'saved_plan': result['saved_plan']}, ensure_ascii=False, indent=2))
