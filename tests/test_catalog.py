import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


class CatalogTests(unittest.TestCase):
    def setUp(self):
        from content_planner.catalog import Catalog, ToolError
        self.Catalog, self.ToolError = Catalog, ToolError
        self.catalog = Catalog.load(ROOT / 'data')

    def test_search_hits_and_empty(self):
        self.assertEqual(self.catalog.search('字幕')[0]['material_id'], 'M01')
        self.assertEqual(self.catalog.search('不存在的深海产品'), [])

    def test_search_copy_and_query_validation(self):
        result = self.catalog.search('字幕')
        result[0]['facts'][0] = '伪造效果'
        self.assertNotIn('伪造效果', self.catalog.search('字幕')[0]['facts'])
        for query in ('', '  ', 3, True, 'a' * 501):
            with self.subTest(query=str(query)[:20]), self.assertRaises(self.ToolError):
                self.catalog.search(query)

    def test_rates_and_copy(self):
        rate = self.catalog.rate('generate_script', 'economy')
        self.assertEqual(rate['success_cost'], 8)
        rate['success_cost'] = 0
        self.assertEqual(self.catalog.rate('generate_script', 'economy')['success_cost'], 8)
        with self.assertRaises(self.ToolError):
            self.catalog.rate('generate_script', 'invented')

    def test_invalid_data_rejected(self):
        materials = json.loads((ROOT / 'data/materials.json').read_text(encoding='utf-8'))
        rates = json.loads((ROOT / 'data/tool_costs.json').read_text(encoding='utf-8'))
        mutations = [
            lambda m, r: m['materials'].append(copy.deepcopy(m['materials'][0])),
            lambda m, r: r['rates'][0].update(success_cost=True),
            lambda m, r: r['rates'][0].update(failure_seconds=-1),
            lambda m, r: m['materials'][0].update(facts=[]),
            lambda m, r: r['rates'].append(copy.deepcopy(r['rates'][0])),
        ]
        for mutate in mutations:
            m, r = copy.deepcopy(materials), copy.deepcopy(rates)
            mutate(m, r)
            with self.subTest(mutation=mutate), tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp)
                (p / 'materials.json').write_text(json.dumps(m), encoding='utf-8')
                (p / 'tool_costs.json').write_text(json.dumps(r), encoding='utf-8')
                with self.assertRaises(self.ToolError):
                    self.Catalog.load(p)


if __name__ == '__main__':
    unittest.main()
