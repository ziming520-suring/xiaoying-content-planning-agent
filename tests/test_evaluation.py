import unittest
from phase5_metrics import inspect_record


class EvaluationTests(unittest.TestCase):
    def record(self):
        return {'final':{'task_status':'completed','saved_plan':{'artifact_id':'a'},'budget':{'spent_cost':8,'reserved_cost':0},'timing':{'current_time':'2026-10-30T01:00:10Z','deadline':'2026-10-30T01:01:00Z'},'tool_results':{}},'trace':[]}

    def event(self,feasible):
        return {'action':{'tool':'estimate_plan'},'observation':{'tool_status':'succeeded','result':{'feasible':feasible}},'spent_cost':0,'logical_time':'2026-10-30T01:00:00Z'}

    def test_replan_requires_observed_order(self):
        r=self.record();r['trace']=[self.event(True),self.event(False)]
        self.assertFalse(inspect_record(r,{'budget':10,'require_replan':True})['replan_order'])
        r['trace'].append(self.event(True))
        self.assertTrue(inspect_record(r,{'budget':10,'require_replan':True})['replan_order'])

    def test_deadline_violation_detected(self):
        r=self.record();r['final']['timing']['current_time']='2026-10-30T01:01:01Z'
        self.assertFalse(inspect_record(r,{'budget':10})['deadline'])

    def test_intermediate_overspend_not_hidden(self):
        r=self.record();e=self.event(True);e['spent_cost']=11;r['trace']=[e]
        self.assertFalse(inspect_record(r,{'budget':10})['budget'])

    def test_injection_must_match_operation(self):
        r=self.record();r['trace']=[{'action':{'tool':'search_materials'},'observation':{'tool_status':'failed'},'spent_cost':0,'logical_time':'2026-10-30T01:00:00Z'}]
        self.assertFalse(inspect_record(r,{'budget':10,'test_failures':{'save_plan':1}})['injected_operation_failed'])

    def test_invalid_request_is_not_injected_failure(self):
        r=self.record();r['trace']=[{'action':{'tool':'save_plan'},'observation':{'tool_status':'failed','error':{'code':'INVALID_REQUEST','message':'bad fields'}},'spent_cost':0,'logical_time':'2026-10-30T01:00:00Z'}]
        self.assertFalse(inspect_record(r,{'budget':10,'test_failures':{'save_plan':1}})['injected_operation_failed'])

    def test_changed_deadline_cannot_hide_violation(self):
        r=self.record();r['final']['timing']['deadline']='2026-10-31T01:00:00Z'
        self.assertFalse(inspect_record(r,{'budget':10,'deadline':'2026-10-30T01:00:05Z'})['deadline'])
