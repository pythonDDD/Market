import base64
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import notification as N
import line_transport as L
from state_store import LocalStore, GitHubStore, StateError, bootstrap

NOW = datetime(2026, 10, 10, 11, 46, tzinfo=N.JST)
CFG = N.settings()
TARGET = 'U' + '1' * 32
DATA = {'generated_at': NOW.isoformat(), 'mode': 'live', 'health': {'ok': 1, 'total': 1},
        'summary': [{'key': 'BTC', 'name': 'Bitcoin', 'last': 60000, 'chg1d': 1, 'z1d': 0}]}
ITEM = {'kind': 'daily', 'date': '2026-10-10', 'text': '市場だより', 'fingerprint': ''}

class Response:
    def __init__(self, data=None, status=200, headers=None):
        self.data, self.status, self.headers = data or {}, status, headers or {}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return json.dumps(self.data).encode()


def http_error(code, message='', headers=None):
    return urllib.error.HTTPError('https://api.line.me/example', code, 'error', headers or {},
                                  io.BytesIO(json.dumps({'message': message}).encode()))

class MemoryStore:
    def __init__(self): self.saved = []
    def save(self, value): self.saved.append(copy.deepcopy(value))

class PlanTests(unittest.TestCase):
    def test_delayed_run_after_11_sends_in_one_run(self):
        state = {'daily': {'render_date': '2026-10-10', 'sent_date': '2026-09-27'}}
        item, reason = N.plan(DATA, state, NOW, CFG)
        self.assertEqual(item['kind'], 'daily')
        self.assertEqual(reason, 'due')
        self.assertNotIn('画像', item['text'])
    def test_late_evening_catchup(self):
        now = NOW.replace(hour=23)
        item, _ = N.plan({**DATA, 'generated_at': now.isoformat()}, {}, now, CFG)
        self.assertEqual(item['kind'], 'daily')
    def test_before_daily_hour_waits(self):
        now = NOW.replace(hour=8)
        self.assertEqual(N.plan({**DATA, 'generated_at': now.isoformat()}, {}, now, CFG)[1], 'idle')
    def test_sent_today_does_not_repeat(self):
        self.assertEqual(N.plan(DATA, {'daily': {'sent_date': '2026-10-10'}}, NOW, CFG)[1], 'idle')
    def test_new_day_sends(self):
        self.assertEqual(N.plan(DATA, {'daily': {'sent_date': '2026-10-09'}}, NOW, CFG)[0]['kind'], 'daily')
    def test_local_month_cap(self):
        self.assertEqual(N.plan(DATA, {'month': '2026-10', 'count': 190}, NOW, CFG)[1], 'local_cap')
    def test_month_rollover(self):
        self.assertEqual(N.plan(DATA, {'month': '2026-09', 'count': 999}, NOW, CFG)[0]['kind'], 'daily')
    def test_mock_never_sent(self):
        self.assertEqual(N.plan({**DATA, 'mode': 'mock'}, {}, NOW, CFG)[1], 'stale_data')
    def test_future_or_stale_never_sent(self):
        for hours in (-4, 1):
            d={**DATA, 'generated_at': (NOW+timedelta(hours=hours)).isoformat()}
            self.assertEqual(N.plan(d, {}, NOW, CFG)[1], 'stale_data')
    def test_empty_data_never_sent(self):
        self.assertEqual(N.plan({}, {}, NOW, CFG)[1], 'stale_data')
    def test_identical_alert_event_can_recur_next_day(self):
        d={**DATA, 'scan':[{'label':'BTC','value':'-8%', 'z':-3}]}
        cfg={**CFG,'daily_enabled':False}
        item,_=N.plan(d,{},NOW,cfg)
        state={'alert':{'fingerprint':item['fingerprint'],'sent_at':(NOW-timedelta(hours=25)).isoformat()}}
        self.assertEqual(N.plan(d,state,NOW,cfg)[0]['kind'],'alert')
        state['alert']['sent_at']=(NOW-timedelta(hours=2)).isoformat()
        self.assertIsNone(N.plan(d,state,NOW,cfg)[0])
    def test_dry_run_no_network_or_state_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch.object(N,'ROOT',root),patch.object(N,'settings',return_value=CFG),patch.object(sys,'argv',['notification','--dry-run']),patch.object(N,'LineClient') as client,patch.object(N,'write_json') as write,patch('builtins.print'):
                self.assertEqual(N.main(),0)
                client.assert_not_called();write.assert_not_called()
    def test_status_contains_no_recipient_or_token(self):
        state={'pending':{'text':'not public diagnostic','recipient_hash':'hash'}, 'secret':'private'}
        status=json.dumps(N.public_status(state,'accepted',NOW,CFG))
        self.assertNotIn('private',status);self.assertNotIn('not public diagnostic',status)
        self.assertFalse(json.loads(status)['delivery_confirmed'])

class TransportTests(unittest.TestCase):
    def test_secret_aliases_and_whitespace(self):
        with patch.dict(os.environ,{'LINE_CHANNEL_ACCESS_TOKEN':' old ', 'LINE_TO':' '+TARGET},clear=True):
            self.assertEqual(L.credentials(),('old',TARGET))
    def test_empty_config(self): self.assertEqual(L.validate_config('','').code,'missing_config')
    def test_reject_display_line_id(self): self.assertEqual(L.validate_config('token','@myline').code,'invalid_recipient')
    def test_user_group_and_room_allowed(self):
        for prefix in 'UCR':self.assertTrue(L.validate_config('token',prefix+'a'*32).ok)
    def test_500_retries_identical_payload_and_key(self):
        opener=Mock(side_effect=[http_error(500),Response()])
        c=L.LineClient('secret',TARGET,opener,lambda _:None)
        self.assertTrue(c.push([{'type':'text','text':'hello'}],'fixed-key').ok)
        reqs=[x.args[0] for x in opener.call_args_list]
        self.assertEqual(reqs[0].data,reqs[1].data)
        self.assertEqual(reqs[0].get_header('X-line-retry-key'),'fixed-key')
        self.assertEqual(reqs[0].headers,reqs[1].headers)
    def test_timeout_retries_identical_key(self):
        opener=Mock(side_effect=[TimeoutError(),Response()])
        self.assertTrue(L.LineClient('secret',TARGET,opener,lambda _:None).push([], 'key').ok)
        self.assertEqual(opener.call_count,2)
    def test_409_only_success_with_accepted_header(self):
        c=L.LineClient('secret',TARGET,Mock(side_effect=http_error(409,headers={'x-line-accepted-request-id':'receipt'})),lambda _:None)
        self.assertEqual(c.push([], 'key').code,'already_accepted')
        c=L.LineClient('secret',TARGET,Mock(side_effect=http_error(409)),lambda _:None)
        self.assertFalse(c.push([], 'key').ok)
    def test_401_not_retried(self):
        opener=Mock(side_effect=http_error(401))
        self.assertEqual(L.LineClient('x',TARGET,opener,lambda _:None).push([], 'key').code,'unauthorized')
        self.assertEqual(opener.call_count,1)
    def test_monthly_429_not_retried(self):
        opener=Mock(side_effect=http_error(429,'You have reached your monthly limit.'))
        self.assertEqual(L.LineClient('x',TARGET,opener,lambda _:None).push([], 'key').code,'quota_exceeded')
        self.assertEqual(opener.call_count,1)
    def test_rate_429_classified_separately(self):
        c=L.LineClient('x',TARGET,Mock(side_effect=http_error(429,'rate limit')),lambda _:None)
        self.assertEqual(c.push([], 'key').code,'rate_limited')
    def test_quota_is_real_account_usage(self):
        opener=Mock(side_effect=[Response(),Response({'type':'limited','value':200}),Response({'totalUsage':200})])
        result,quota=L.LineClient('x',TARGET,opener,lambda _:None).diagnose()
        self.assertEqual(result.code,'quota_exceeded');self.assertEqual(quota['remaining'],0)

class OutboxTests(unittest.TestCase):
    def client(self,result):
        c=Mock();c.target=TARGET;c.push.return_value=result;return c
    def test_persist_before_post_and_record_after(self):
        store=MemoryStore();state={};client=self.client(L.Result(True,'accepted',200))
        def send(*args):
            self.assertIn('pending',store.saved[0]);return L.Result(True,'accepted',200)
        client.push.side_effect=send
        N.dispatch(ITEM,state,store,client,NOW)
        self.assertNotIn('pending',store.saved[-1]);self.assertEqual(state['count'],1)
        self.assertEqual(state['daily']['sent_date'],'2026-10-10')
        self.assertNotIn(TARGET,json.dumps(store.saved))
    def test_cannot_write_state_means_no_send(self):
        store=Mock();store.save.side_effect=StateError('no');client=self.client(L.Result(True,'accepted'))
        with self.assertRaises(StateError):N.dispatch(ITEM,{},store,client,NOW)
        client.push.assert_not_called()
    def test_uncertain_send_keeps_outbox_for_same_key(self):
        store=MemoryStore();state={};client=self.client(L.Result(False,'network_error'))
        N.dispatch(ITEM,state,store,client,NOW)
        pending=copy.deepcopy(state['pending'])
        self.assertNotIn('count',state)
        client.push.return_value=L.Result(True,'already_accepted',409)
        N.dispatch({**ITEM,'text':'changed'},state,store,client,NOW+timedelta(minutes=5))
        args=client.push.call_args.args
        self.assertEqual(args[1],pending['retry_key']);self.assertEqual(args[0][0]['text'],pending['text'])
        self.assertEqual(state['count'],1)
    def test_rejection_clears_pending(self):
        state={};client=self.client(L.Result(False,'unauthorized',401))
        N.dispatch(ITEM,state,MemoryStore(),client,NOW)
        self.assertNotIn('pending',state);self.assertNotIn('count',state)
    def test_retry_expired_never_posted(self):
        state={};store=MemoryStore();client=self.client(L.Result(False,'network_error'))
        N.dispatch(ITEM,state,store,client,NOW)
        client.push.reset_mock()
        self.assertEqual(N.dispatch(None,state,store,client,NOW+timedelta(hours=24))[0],'retry_expired')
        client.push.assert_not_called()
    def test_changed_recipient_stops_pending_send(self):
        state={};store=MemoryStore();client=self.client(L.Result(False,'network_error'))
        N.dispatch(ITEM,state,store,client,NOW);client.target='U'+'2'*32;client.push.reset_mock()
        self.assertEqual(N.dispatch(None,state,store,client,NOW)[0],'recipient_changed')
        client.push.assert_not_called()
    def test_failure_after_api_acceptance_recovers_with_409(self):
        store=MemoryStore();state={};client=self.client(L.Result(True,'accepted',200))
        original=store.save
        def fail_second(st):
            if len(store.saved)==1:raise StateError('interrupted')
            original(st)
        store.save=fail_second
        with self.assertRaises(StateError):N.dispatch(ITEM,state,store,client,NOW)
        restored=copy.deepcopy(store.saved[0]);store.save=original
        client.push.return_value=L.Result(True,'already_accepted',409)
        N.dispatch(None,restored,store,client,NOW+timedelta(minutes=5))
        self.assertEqual(restored['count'],1)

class StateStoreTests(unittest.TestCase):
    def test_bootstrap_preserves_previous_ledger_before_deployment(self):
        store=Mock();store.branch_exists=False
        store.load.return_value={'daily':{'sent_date':'2026-10-10'},'count':5}
        bootstrap(store)
        self.assertEqual(store.save.call_args.args[0]['daily']['sent_date'],'2026-10-10')
        self.assertEqual(store.save.call_args.args[0]['count'],5)
    def test_bootstrap_does_not_rewrite_existing_ledger(self):
        store=Mock();store.branch_exists=True;store.load.return_value={'pending':{'text':'pending'}}
        bootstrap(store);store.save.assert_not_called()
    def test_local_round_trip_and_corrupt_file(self):
        with tempfile.TemporaryDirectory() as folder:
            store=LocalStore(Path(folder)/'state.json');self.assertEqual(store.load(),{})
            store.save({'count':1});self.assertEqual(store.load()['count'],1)
            store.path.write_text('not json')
            with self.assertRaises(StateError):store.load()
    def test_github_conflict_is_not_overwritten(self):
        store=GitHubStore('a/b','token');store.sha='old';store.branch_exists=True
        store.api=Mock(return_value=(409,{}))
        with self.assertRaises(StateError):store.save({'count':1})
        self.assertEqual(store.api.call_count,1)
        self.assertEqual(store.api.call_args.args[2]['sha'],'old')
    def test_atomic_initial_branch_has_state_and_no_source_files(self):
        store=GitHubStore('a/b','token')
        store.api=Mock(side_effect=[(201,{'sha':'tree'}),(201,{'sha':'commit'}),(201,{})])
        store.save({'count':0})
        calls=store.api.call_args_list
        self.assertEqual(calls[0].args[2]['tree'][0]['path'],'notification-state.json')
        self.assertEqual(calls[1].args[2]['parents'],[])
        self.assertEqual(calls[2].args[2]['sha'],'commit')
        self.assertTrue(store.branch_exists)
    def test_existing_branch_missing_ledger_stops(self):
        store=GitHubStore('a/b','token');store.api=Mock(side_effect=[(404,{}),(200,{})])
        with self.assertRaises(StateError):store.load()
    def test_migrates_previous_sent_date(self):
        store=GitHubStore('a/b','token',opener=Mock(return_value=Response({'daily':{'sent_date':'2026-10-10'}})))
        store.api=Mock(side_effect=[(404,{}),(404,{})])
        self.assertEqual(store.load()['daily']['sent_date'],'2026-10-10')

if __name__=='__main__':unittest.main()
