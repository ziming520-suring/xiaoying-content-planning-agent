import json
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from content_planner.bailian import BailianClient,BailianPolicy
from content_planner.api_budget import BudgetLedger
from content_planner.catalog import ToolError
from test_bailian import provider_result


class GroundingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.materials=[{'material_id':'M01','title':'字幕编辑功能','facts':['支持编辑字幕文字','支持调整字幕样式']}]

    def client(self,content):
        return BailianClient(api_key='test',ledger=BudgetLedger(Path(self.tmp.name)/'api.sqlite3'),transport=lambda *a,**k:provider_result(content=json.dumps(content,ensure_ascii=False)))

    def test_reject_unsupported_release_and_options(self):
        for i,sentence in enumerate(('以上功能已在当前版本中上线。','支持调整字幕颜色和位置。','支持100种语言。')):
            with self.subTest(sentence=sentence):
                client=self.client({'title':'功能介绍','script':sentence,'source_ids':['M01']})
                with self.assertRaises(ToolError):client.generate_script('字幕','economy',self.materials,str(i))

    def test_title_cannot_add_claim(self):
        client=self.client({'title':'永久免费字幕编辑','script':'支持编辑字幕文字。','source_ids':['M01']})
        with self.assertRaises(ToolError):client.generate_script('字幕','economy',self.materials,'title')

    def test_grounded_online_composition(self):
        client=self.client({'title':'功能介绍','script':'大家好，欢迎观看功能介绍。\n支持调整字幕样式。\n支持编辑字幕文字。\n感谢观看。','source_ids':['M01']})
        result=client.generate_script('宣传100种语言','economy',self.materials,'good')
        self.assertEqual(result['content_policy'],'source_sentences_v2')
        self.assertIn('支持调整字幕样式',result['script'])

    def test_empty_material_does_not_offer_generation(self):
        captured=[]
        class Client:
            def complete(self,payload,key):
                captured.append(payload)
                return provider_result(action={'tool':'finish','arguments':{'stop_reason':'no_materials'},'reason':'无资料'})
        context={'run_id':'r','next_action_sequence':1,'tool_results':{},'history':[]}
        BailianPolicy(Client()).next_action(context)
        self.assertEqual({t['function']['name'] for t in captured[0]['tools']},{'search_materials','finish'})

    def test_hallucinated_disabled_tool_rejected(self):
        class Client:
            def complete(self,*args):return provider_result(action={'tool':'generate_script','arguments':{'plan_id':'fake','plan_version':1,'step_id':'x'},'reason':'编造'})
        action=BailianPolicy(Client()).next_action({'run_id':'r','next_action_sequence':1,'tool_results':{},'history':[]})
        self.assertEqual(action['tool'],'invalid_model_output')

    def test_malformed_arguments_become_bounded_observation(self):
        captured=[]
        class Client:
            def complete(self,payload,key):
                captured.append(payload)
                response=provider_result(action={'tool':'search_materials','arguments':{'query':'字幕'},'reason':'检索'})
                response['choices'][0]['message']['tool_calls'][0]['function']['arguments']='{"query":"字幕"'
                return response
        policy=BailianPolicy(Client())
        context={'run_id':'r','next_action_sequence':1,'tool_results':{},'history':[]}
        action=policy.next_action(context)
        self.assertEqual(action['tool'],'invalid_model_output')
        context['history']=[{'action':action,'observation':{'tool_status':'failed','error':{'code':'INVALID_REQUEST'}}}]
        context['next_action_sequence']=2
        policy.next_action(context)
        self.assertTrue(any('rejected_model_output' in str(m) for m in captured[-1]['messages']))

    def test_legacy_cached_response_recovers_with_original_prompt(self):
        from content_planner.remote import _fetch
        calls=[]
        def transport(*args,**kwargs):
            calls.append(1)
            return provider_result(content=json.dumps({'title':'旧标题','script':'旧版脚本','source_ids':['M01']}))
        client=BailianClient(api_key='test',ledger=BudgetLedger(Path(self.tmp.name)/'legacy.sqlite3'),transport=transport)
        original=client.generate_script_legacy('字幕','economy',self.materials,'old-call')
        outcome=_fetch(client,{'remote_id':'old-call','payload_json':json.dumps({'topic':'字幕','mode':'economy','materials':self.materials})},allow_send=False)
        self.assertEqual(outcome,{'kind':'succeeded','script':original})
        self.assertEqual(len(calls),1)

    def test_reject_empty_and_duplicate_material_coverage(self):
        for i,content in enumerate(({'title':'功能介绍','script':'感谢观看。','source_ids':['M01']},
                                   {'title':'功能介绍','script':'支持编辑字幕文字。','source_ids':['M01','M01']})):
            with self.subTest(i=i):
                with self.assertRaises(ToolError):self.client(content).generate_script('字幕','economy',self.materials,str(i))

    def test_provider_line_array_normalizes_without_new_facts(self):
        client=self.client({'title':'功能介绍','script':['大家好，欢迎观看功能介绍。','支持编辑字幕文字。','感谢观看。'],'source_ids':['M01']})
        result=client.generate_script('字幕','economy',self.materials,'array')
        self.assertEqual(result['script'],'大家好，欢迎观看功能介绍。\n支持编辑字幕文字。\n感谢观看。')

    def test_provider_line_array_still_rejects_claim(self):
        client=self.client({'title':'功能介绍','script':['支持编辑字幕文字。','当前已全量上线。'],'source_ids':['M01']})
        with self.assertRaises(ToolError):client.generate_script('字幕','economy',self.materials,'bad-array')

    def test_invalid_model_outputs_stop_at_runtime_limit_without_business_fee(self):
        from content_planner.runtime import Runtime
        class Client:
            def complete(self,*args):
                return {'choices':[{'finish_reason':'stop','message':{'content':'invalid'}}]}
        runtime=Runtime(Path(self.tmp.name)/'runtime.sqlite3',ROOT/'data')
        runtime.request({'schema_version':1,'request_id':'c','operation':'task.create','payload':{'task_id':'G','topic':'字幕','budget_limit':10,'start_time':'2026-10-30T09:00:00+08:00','deadline':'2026-10-30T10:00:00+08:00'}})
        reply=runtime.request({'schema_version':1,'request_id':'r','operation':'task.run','payload':{'task_id':'G','max_actions':2}})
        final=runtime.drive(reply['data']['run_id'],policy=BailianPolicy(Client()))
        self.assertEqual(final['stop_reason'],'step_limit')
        self.assertEqual(len(runtime.trace('G')),2)
        self.assertEqual(final['budget']['spent_cost'],0)

    def test_scalar_arguments_become_rejected_observation(self):
        for raw in ('null','true','123','"text"','[]'):
            class Client:
                def complete(self,*args):
                    return {'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[{'function':{'name':'search_materials','arguments':raw}}]}}]}
            with self.subTest(raw=raw):
                result=BailianPolicy(Client()).next_action({'run_id':'r','next_action_sequence':1,'tool_results':{},'history':[]})
                self.assertEqual(result['tool'],'invalid_model_output')
