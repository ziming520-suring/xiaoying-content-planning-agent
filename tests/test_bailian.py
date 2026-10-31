import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


def provider_result(content=None, action=None):
    message={'content':content}
    if action:
        message={'content':None,'tool_calls':[{'id':'call1','type':'function','function':{'name':action['tool'], 'arguments':json.dumps(dict(action['arguments'],reason=action['reason']))}}]}
    return {'id':'response1','model':'qwen-plus','usage':{'prompt_tokens':100,'completion_tokens':20},
            'choices':[{'finish_reason':'tool_calls' if action else 'stop','message':message}]}


class ApiTests(unittest.TestCase):
    def setUp(self):
        from content_planner.api_budget import BudgetLedger
        from content_planner.bailian import BailianClient, BailianPolicy
        from content_planner.catalog import ToolError
        self.Ledger,self.Client,self.Policy,self.Error=BudgetLedger,BailianClient,BailianPolicy,ToolError
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'budget.sqlite3'

    def test_budget_settlement_and_replay(self):
        ledger=self.Ledger(self.path)
        sends=[]
        def transport(request,timeout):
            sends.append(request)
            return provider_result(content='ok')
        client=self.Client(api_key='test-key',ledger=ledger,transport=transport)
        first=client.complete({'messages':[]},'K1')
        self.assertEqual(client.complete({'messages':[]},'K1'),first)
        self.assertEqual(len(sends),1)
        self.assertEqual(ledger.summary()['settled_micro'],160)
        with self.assertRaises(self.Error):
            client.complete({'messages':[{'role':'user','content':'changed'}]},'K1')

    def test_unknown_usage_holds_reservation(self):
        ledger=self.Ledger(self.path)
        def transport(request,timeout):
            return {'id':'bad','choices':[]}
        client=self.Client(api_key='test-key',ledger=ledger,transport=transport)
        with self.assertRaises(self.Error):
            client.complete({'messages':[]},'K1')
        self.assertEqual(ledger.summary()['held_micro'],100000)
        with self.assertRaises(self.Error) as caught:
            client.complete({'messages':[]},'K1')
        self.assertEqual(caught.exception.code,'MODEL_PENDING')

    def test_timeout_no_automatic_retry(self):
        sends=[]
        def transport(request,timeout):
            sends.append(1)
            raise TimeoutError('not logged')
        client=self.Client(api_key='secret-never-log',ledger=self.Ledger(self.path),transport=transport)
        for _ in range(2):
            with self.assertRaises(self.Error) as caught:
                client.complete({'messages':[]},'K1')
            self.assertNotIn('secret',str(caught.exception))
        self.assertEqual(len(sends),1)

    def test_limit_prevents_send(self):
        ledger=self.Ledger(self.path,limit_micro=100000)
        ledger.reserve('existing','hash')
        client=self.Client(api_key='test',ledger=ledger,transport=lambda *a,**k:self.fail('must not send'))
        with self.assertRaises(self.Error) as caught:
            client.complete({'messages':[]},'new')
        self.assertEqual(caught.exception.code,'MODEL_BUDGET_EXCEEDED')

    def test_key_and_address_validation(self):
        ledger=self.Ledger(self.path)
        for url in ('http://dashscope.aliyuncs.com/compatible-mode/v1','https://evil.example/compatible-mode/v1',
                    'https://dashscope.aliyuncs.com@evil.example/compatible-mode/v1'):
            with self.assertRaises(self.Error):
                self.Client(api_key='test',base_url=url,ledger=ledger)
        with self.assertRaises(self.Error):
            self.Client(api_key='',ledger=ledger)

    def test_native_action_parsing(self):
        action={'tool':'search_materials','arguments':{'query':'字幕'},'reason':'查找依据'}
        client=self.Client(api_key='test',ledger=self.Ledger(self.path),transport=lambda *a,**k:provider_result(action=action))
        policy=self.Policy(client)
        context={'run_id':'run','next_action_sequence':1,'topic':'字幕','observations':[], 'history':[],
                 'budget':{'limit':10},'timing':{},'tool_results':{},'active_plan':None,'remaining_actions':24,'available_modes':[]}
        self.assertEqual(policy.next_action(context),action)

    def test_generation_returns_validated_online_content(self):
        content=json.dumps({'title':'功能介绍','script':'支持编辑字幕文字。','source_ids':['M01']},ensure_ascii=False)
        client=self.Client(api_key='test',ledger=self.Ledger(self.path),transport=lambda *a,**k:provider_result(content=content))
        result=client.generate_script('字幕','economy',[{'material_id':'M01','facts':['支持编辑字幕文字']}],'G1')
        self.assertEqual(result['generator'],'bailian_live')
        self.assertEqual(result['source_ids'],['M01'])

    def test_generation_rejects_invented_source(self):
        content=json.dumps({'title':'x','script':'x','source_ids':['FAKE']})
        client=self.Client(api_key='test',ledger=self.Ledger(self.path),transport=lambda *a,**k:provider_result(content=content))
        with self.assertRaises(self.Error):
            client.generate_script('字幕','economy',[{'material_id':'M01'}],'G1')

    def test_null_usage_keeps_reserve(self):
        ledger=self.Ledger(self.path)
        result=provider_result(content='ok');result['usage']=None
        client=self.Client(api_key='test',ledger=ledger,transport=lambda *a,**k:result)
        with self.assertRaises(self.Error) as caught:client.complete({'messages':[]},'null-usage')
        self.assertEqual(caught.exception.code,'MODEL_USAGE_ERROR')
        self.assertEqual(ledger.summary()['held_micro'],100000)

    def test_malformed_choices_returns_protocol_error(self):
        result=provider_result(content='ok');result['choices']=None
        client=self.Client(api_key='test',ledger=self.Ledger(self.path),transport=lambda *a,**k:result)
        with self.assertRaises(self.Error) as caught:client.complete({'messages':[]},'bad-choices')
        self.assertEqual(caught.exception.code,'MODEL_PROTOCOL_ERROR')


if __name__=='__main__':unittest.main()
