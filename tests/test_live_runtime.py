from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from content_planner.runtime import Runtime
from content_planner.offline import OfflinePolicy
from content_planner.catalog import ToolError


class LiveRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/'runtime.sqlite3'
        self.r=Runtime(self.db,ROOT/'data')
        self.r.request(dict(schema_version=1,request_id='create',operation='task.create',payload=dict(task_id='L1',topic='字幕',budget_limit=10,start_time='2026-10-30T09:00:00+08:00',deadline='2026-10-30T10:00:00+08:00')))
        self.run=self.r.request(dict(schema_version=1,request_id='run',operation='task.run',payload={'task_id':'L1'}))['data']['run_id']

    def policy(self,client):
        class SimulatedLive(OfflinePolicy):
            name='bailian_live'
        result=SimulatedLive();result.client=client
        return result

    def test_remote_generation_outside_write_transaction(self):
        db=self.db
        class Client:
            def generate_script(self,topic,mode,materials,key):
                with closing(sqlite3.connect(db,timeout=0.1)) as c:
                    c.execute('BEGIN IMMEDIATE')
                return {'title':'在线脚本','script':'支持编辑字幕文字。','source_ids':['M01'], 'generator':'bailian_live','model':'fake-test','api_call_key':key}
        final=self.r.drive(self.run,policy=self.policy(Client()))
        self.assertEqual(final['task_status'],'completed')
        self.assertEqual(final['execution_mode'],'bailian_live')
        self.assertEqual(final['saved_plan']['snapshot']['scripts'][0]['generator'],'bailian_live')
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT status FROM remote_actions').fetchone()[0],'applied')

    def test_unknown_remote_retains_business_reserve_and_blocks_resume(self):
        class Client:
            def generate_script(self,*args):raise ToolError('MODEL_UNAVAILABLE','unknown')
            def outcome(self,key):return 'unknown'
        final=self.r.drive(self.run,policy=self.policy(Client()))
        self.assertEqual(final['stop_reason'],'pending_reconciliation')
        self.assertEqual(final['budget']['reserved_cost'],8)
        self.assertEqual(final['budget']['spent_cost'],0)
        reply=self.r.request(dict(schema_version=1,request_id='resume',operation='task.resume',payload={'task_id':'L1'}))
        self.assertEqual(reply['error']['code'],'PENDING_RECONCILIATION')

    def test_budget_rejection_before_send_does_not_charge_business(self):
        class Client:
            def generate_script(self,*args):raise ToolError('MODEL_BUDGET_EXCEEDED','not sent')
            def outcome(self,key):return 'not_sent'
        final=self.r.drive(self.run,policy=self.policy(Client()))
        self.assertEqual(final['budget']['reserved_cost'],0)
        self.assertEqual(final['budget']['spent_cost'],0)
        self.assertEqual(final['stop_reason'],'model_budget_exceeded')

    def test_explicit_v1_upgrade_preserves_results(self):
        before=self.r.drive(self.run)
        with closing(sqlite3.connect(self.db)) as c:
            c.execute('DROP TABLE remote_actions')
            c.execute('ALTER TABLE runs DROP COLUMN mode')
            c.execute('UPDATE runtime_meta SET version=1');c.commit()
        with self.assertRaises(ToolError):self.r.status('L1')
        self.r.upgrade()
        self.assertEqual(self.r.status('L1'),before)

    def test_crash_after_remote_receipt_recovers_without_second_send(self):
        from content_planner import remote
        class Client:
            calls=0
            def generate_script(self,topic,mode,materials,key):
                self.calls+=1
                return {'title':'字幕','script':'支持编辑字幕文字。','source_ids':['M01'], 'generator':'bailian_live','model':'fake-test','api_call_key':key}
        client=Client()
        # The response is journaled as ready before simulated process interruption.
        with patch.object(remote,'_finish',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):self.r.drive(self.run,policy=self.policy(client))
        self.assertEqual(self.r.status('L1')['budget']['reserved_cost'],8)
        recovered=remote.reconcile(self.r,'L1',client)
        self.assertEqual(client.calls,1)
        self.assertEqual(recovered['budget']['spent_cost'],8)
        self.assertEqual(recovered['budget']['reserved_cost'],0)
        self.assertTrue(any(r['generator']=='bailian_live' for r in recovered['tool_results'].values() if r['operation']=='generate_script'))

    def test_cached_provider_receipt_recovers_before_business_journal(self):
        import json
        from content_planner import remote
        from content_planner.api_budget import BudgetLedger
        from content_planner.bailian import BailianClient
        from test_bailian import provider_result
        sends=[]
        def transport(*args,**kwargs):
            sends.append(1)
            return provider_result(content=json.dumps({'title':'功能介绍','script':'支持编辑字幕文字。','source_ids':['M01']}))
        client=BailianClient(api_key='test',ledger=BudgetLedger(Path(self.tmp.name)/'api.sqlite3'),transport=transport)
        original=remote._fetch
        def interrupt_after_receipt(*args,**kwargs):
            original(*args,**kwargs)
            raise KeyboardInterrupt()
        with patch.object(remote,'_fetch',side_effect=interrupt_after_receipt):
            with self.assertRaises(KeyboardInterrupt):self.r.drive(self.run,policy=self.policy(client))
        recovered=remote.reconcile(self.r,'L1',client)
        self.assertEqual(len(sends),1)
        self.assertEqual(recovered['budget']['spent_cost'],8)
        self.assertEqual(recovered['budget']['reserved_cost'],0)
        self.assertEqual(recovered['stop_reason'],'interrupted')

    def test_reconcile_before_send_releases_only_business_reservation(self):
        from content_planner import remote
        from content_planner.api_budget import BudgetLedger
        from content_planner.bailian import BailianClient
        ledger=BudgetLedger(Path(self.tmp.name)/'api.sqlite3')
        client=BailianClient(api_key='test',ledger=ledger,transport=lambda *a,**k:self.fail('must not send'))
        with patch.object(remote,'_fetch',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):self.r.drive(self.run,policy=self.policy(client))
        recovered=remote.reconcile(self.r,'L1',client)
        self.assertEqual(recovered['budget']['spent_cost'],0)
        self.assertEqual(recovered['budget']['reserved_cost'],0)
        self.assertEqual(recovered['stop_reason'],'model_not_sent')
        self.assertEqual(ledger.summary()['calls'],0)


if __name__=='__main__':unittest.main()
