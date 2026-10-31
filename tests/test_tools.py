import copy
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def task(**updates):
    data = dict(task_id='T01', topic='介绍字幕编辑功能', budget_limit=10,
                start_time='2026-10-30T09:00:00+08:00',
                deadline='2026-10-30T10:00:00+08:00')
    data.update(updates)
    return data


def plan(ref, mode='economy'):
    return [dict(step_id='generate', kind='generate_script', mode_id=mode,
                 depends_on=[], input_refs=[ref]),
            dict(step_id='save', kind='save_plan', mode_id='default',
                 depends_on=['generate'], input_refs=[])]


class ToolTests(unittest.TestCase):
    def setUp(self):
        from content_planner.catalog import Catalog, ToolError
        from content_planner.tools import ToolWorkbench
        self.Catalog, self.ToolError, self.Workbench = Catalog, ToolError, ToolWorkbench
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'artifacts.sqlite3'
        self.w = ToolWorkbench(Catalog.load(ROOT / 'data'), task(), self.db)

    def prepared(self, mode='economy', workbench=None):
        w = workbench or self.w
        found = w.search_materials('字幕')['result']['result_id']
        response = w.estimate_plan(plan(found, mode))
        return response['result']

    def generate(self, estimate):
        return self.w.generate_script(estimate['plan_id'], estimate['plan_version'], 'generate')

    def save(self, estimate):
        return self.w.save_plan(estimate['plan_id'], estimate['plan_version'])

    def test_normal_generate_and_save(self):
        e = self.prepared()
        self.assertTrue(e['feasible'])
        generated = self.generate(e)
        self.assertEqual(generated['tool_status'], 'succeeded')
        self.assertEqual(generated['result']['generator'], 'offline_mock')
        self.assertIn('M01', generated['result']['source_ids'])
        result = self.save(e)
        self.assertEqual(result['tool_status'], 'succeeded')
        self.assertEqual(result['result']['snapshot']['budget']['spent_cost'], 8)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('select count(*) from artifacts').fetchone()[0], 1)

    def test_budget_replan_12_to_8(self):
        e1 = self.prepared('premium')
        self.assertFalse(e1['feasible'])
        self.assertEqual(e1['projected_total_cost'], 12)
        self.assertEqual(self.generate(e1)['error']['code'], 'BUDGET_EXCEEDED')
        ref = self.w.search_materials('字幕')['result']['result_id']
        e2 = self.w.estimate_plan(plan(ref))['result']
        self.assertEqual(e2['projected_total_cost'], 8)
        self.assertTrue(e2['feasible'])

    def test_estimate_new_id_same_version(self):
        ref = self.w.search_materials('字幕')['result']['result_id']
        steps = plan(ref)
        a = self.w.estimate_plan(steps)['result']
        b = self.w.estimate_plan(steps)['result']
        self.assertEqual(a['plan_version'], b['plan_version'])
        self.assertNotEqual(a['estimate_id'], b['estimate_id'])
        self.assertNotEqual(a['estimated_finish_time'], b['estimated_finish_time'])

    def test_failure_charged_and_sunk_cost_prevents_retry(self):
        e = self.prepared()
        self.w.inject_failure('generate_script')
        failed = self.generate(e)
        self.assertEqual(failed['tool_status'], 'failed')
        self.assertEqual(self.w.snapshot()['spent_cost'], 3)
        ref = self.w.search_materials('字幕')['result']['result_id']
        revised = self.w.estimate_plan(plan(ref))['result']
        self.assertEqual(revised['projected_total_cost'], 11)
        before = self.w.snapshot()
        self.assertEqual(self.generate(revised)['error']['code'], 'BUDGET_EXCEEDED')
        self.assertEqual(before, self.w.snapshot())

    def test_failure_retry_keeps_previous_success(self):
        self.w = self.Workbench(self.Catalog.load(ROOT / 'data'), task(budget_limit=20), self.db)
        e = self.prepared()
        self.w.inject_failure('generate_script')
        self.generate(e)
        self.assertEqual(self.generate(e)['tool_status'], 'succeeded')
        self.assertEqual(self.w.snapshot()['spent_cost'], 11)
        self.assertEqual([a['status'] for a in self.w.snapshot()['attempts']][-2:], ['failed', 'succeeded'])

    def test_deadline_exact_and_one_second_short(self):
        # search 5 + initial estimate 2 + generation 120 + final estimate 2 + save 3 = 132
        for end, feasible in [('09:02:12', True), ('09:02:11', False)]:
            w = self.Workbench(self.Catalog.load(ROOT / 'data'),
                               task(deadline=f'2026-10-30T{end}+08:00'), self.db)
            e = self.prepared(workbench=w)
            self.assertEqual(e['feasible'], feasible)
            if feasible:
                self.assertEqual(w.generate_script(e['plan_id'], e['plan_version'], 'generate')['tool_status'], 'succeeded')
                self.assertEqual(w.save_plan(e['plan_id'], e['plan_version'])['tool_status'], 'succeeded')
                self.assertEqual(w.snapshot()['logical_time'], '2026-10-30T01:02:12Z')

    def test_budget_zero_and_exact(self):
        for budget, feasible in [(0, False), (8, True)]:
            w = self.Workbench(self.Catalog.load(ROOT / 'data'), task(budget_limit=budget), self.db)
            self.assertEqual(self.prepared(workbench=w)['feasible'], feasible)

    def test_empty_material_cannot_generate(self):
        ref = self.w.search_materials('不存在的产品')['result']['result_id']
        e = self.w.estimate_plan(plan(ref))['result']
        self.assertFalse(e['feasible'])
        self.assertIn('DEPENDENCY_UNMET', [gap['code'] for gap in e['gaps']])
        self.assertEqual(self.generate(e)['error']['code'], 'DEPENDENCY_UNMET')

    def test_save_requires_generated_script(self):
        e = self.prepared()
        self.assertEqual(self.save(e)['error']['code'], 'DEPENDENCY_UNMET')
        self.assertFalse(self.db.exists())

    def test_stale_plan_and_repeated_generation(self):
        e = self.prepared()
        first = self.generate(e)
        before = self.w.snapshot()
        self.assertEqual(first, self.generate(e))
        self.assertEqual(before, self.w.snapshot())
        self.prepared('premium')
        self.assertEqual(self.save(e)['error']['code'], 'STALE_PLAN')

    def test_save_repeated_is_same_artifact(self):
        e = self.prepared()
        self.generate(e)
        first = self.save(e)
        before = self.w.snapshot()
        self.assertEqual(first, self.save(e))
        self.assertEqual(before, self.w.snapshot())

    def test_save_failure_preserves_script(self):
        e = self.prepared()
        generated = self.generate(e)
        self.w.inject_failure('save_plan')
        self.assertEqual(self.save(e)['tool_status'], 'failed')
        self.assertEqual(self.generate(e), generated)
        self.assertEqual(self.save(e)['tool_status'], 'succeeded')

    def test_storage_failure_is_not_success(self):
        self.db.mkdir()
        e = self.prepared()
        self.generate(e)
        self.assertEqual(self.save(e)['error']['code'], 'STORAGE_FAILED')
        self.assertEqual(self.w.snapshot()['spent_cost'], 8)

    def test_result_mutation_does_not_change_trusted_state(self):
        e = self.prepared()
        generated = self.generate(e)
        generated['result']['script'] = '伪造'
        saved = self.save(e)
        self.assertNotEqual(saved['result']['snapshot']['scripts'][0]['script'], '伪造')

    def test_invalid_plans_and_foreign_refs(self):
        ref = self.w.search_materials('字幕')['result']['result_id']
        changes = [
            lambda p: p[0].update(depends_on=['save']),
            lambda p: p[0].update(input_refs=['forged']),
            lambda p: p[0].update(mode_id='free'),
            lambda p: p[0].update(cost=0),
            lambda p: p[1].update(step_id='generate'),
            lambda p: p[1].update(depends_on=[]),
        ]
        for change in changes:
            p = plan(ref)
            change(p)
            before = self.w.snapshot()
            with self.subTest(change=change):
                self.assertEqual(self.w.estimate_plan(p)['tool_status'], 'failed')
                self.assertEqual(before, self.w.snapshot())

    def test_task_validation(self):
        for updates in [dict(budget_limit=True), dict(budget_limit=-1), dict(budget_limit=1.5),
                        dict(deadline='2026-10-30T10:00:00'), dict(deadline=None), dict(topic=''),
                        dict(deadline='2026-10-30T08:00:00+08:00')]:
            with self.subTest(updates=updates), self.assertRaises(self.ToolError):
                self.Workbench(self.Catalog.load(ROOT / 'data'), task(**updates), self.db)

    def test_bad_plan_types_return_structured_error(self):
        ref = self.w.search_materials('字幕')['result']['result_id']
        for field, value in [('kind', []), ('kind', {}), ('mode_id', []), ('step_id', True),
                             ('depends_on', [None]), ('input_refs', [{}])]:
            p = plan(ref)
            p[0][field] = value
            with self.subTest(field=field, value=value):
                self.assertEqual(self.w.estimate_plan(p)['tool_status'], 'failed')

    def test_save_is_not_reused_across_plan_versions(self):
        ref = self.w.search_materials('字幕')['result']['result_id']
        e = self.w.estimate_plan(plan(ref))['result']
        self.generate(e)
        self.save(e)
        self.w.estimate_plan(plan(ref, 'premium'))
        revised = self.w.estimate_plan(plan(ref))['result']
        save_step = next(s for s in revised['steps'] if s['step_id'] == 'save')
        self.assertFalse(save_step['reused'])
        self.assertEqual(save_step['seconds'], 3)

    def test_search_and_estimate_failures_are_recorded(self):
        self.w.inject_failure('search_materials')
        self.assertEqual(self.w.search_materials('字幕')['tool_status'], 'failed')
        ref = self.w.search_materials('字幕')['result']['result_id']
        self.w.inject_failure('estimate_plan')
        self.assertEqual(self.w.estimate_plan(plan(ref))['tool_status'], 'failed')
        self.assertEqual(self.w.snapshot()['active_plan_version'], 0)
        self.assertEqual(self.w.snapshot()['logical_time'], '2026-10-30T01:00:08Z')

    def test_estimate_alone_does_not_produce_saved_artifact(self):
        self.prepared()
        self.assertFalse(self.db.exists())

    def test_completed_search_cannot_hide_unfinished_dependency(self):
        ref = self.w.search_materials('字幕')['result']['result_id']
        steps = [
            dict(step_id='a', kind='generate_script', mode_id='economy', depends_on=[], input_refs=[ref]),
            dict(step_id='s', kind='search_materials', mode_id='default', depends_on=['a'], input_refs=[ref]),
            dict(step_id='b', kind='generate_script', mode_id='quick', depends_on=['s'], input_refs=[ref]),
            dict(step_id='save', kind='save_plan', mode_id='default', depends_on=['a', 'b'], input_refs=[]),
        ]
        self.assertEqual(self.w.estimate_plan(steps)['tool_status'], 'failed')

    def test_near_datetime_limit_returns_deadline_error(self):
        w = self.Workbench(self.Catalog.load(ROOT / 'data'),
                           task(start_time='9999-12-31T23:59:57Z', deadline='9999-12-31T23:59:59Z'), self.db)
        self.assertEqual(w.search_materials('字幕')['error']['code'], 'DEADLINE_EXCEEDED')
        self.assertEqual(w.snapshot()['attempts'], [])

    def test_projection_beyond_datetime_limit_is_infeasible(self):
        w = self.Workbench(self.Catalog.load(ROOT / 'data'),
                           task(start_time='9999-12-31T23:59:40Z', deadline='9999-12-31T23:59:59Z'), self.db)
        e = self.prepared(workbench=w)
        self.assertFalse(e['feasible'])
        self.assertIsNone(e['estimated_finish_time'])


if __name__ == '__main__':
    unittest.main()
