import copy
import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timedelta,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import build as B
import sources as S

class DataTests(unittest.TestCase):
    def test_compound_return_not_sum(self):
        self.assertAlmostEqual(B.period_change({'a':100,'b':110,'c':99},2),-1)
    def test_rate_uses_difference(self):
        self.assertAlmostEqual(B.period_change({'a':4.0,'b':4.5,'c':4.2},2,'rate'),.2)
    def test_missing_history_is_none(self):self.assertIsNone(B.period_change({'a':100},5))
    def test_ytd_uses_previous_year_last_close(self):
        start=datetime(2025,12,1)
        series={(start+timedelta(days=i)).date().isoformat():100.0 for i in range(40)}
        series['2026-01-01']=110;series['2026-01-09']=121
        result=B.build_analytics({'BTC':series})
        row=result['summary'][0]
        self.assertEqual(row['ytd'],21)
    def test_no_prior_year_no_fabricated_ytd(self):
        start=datetime(2026,1,1)
        series={(start+timedelta(days=i)).date().isoformat():100+i for i in range(40)}
        self.assertIsNone(B.build_analytics({'BTC':series})['summary'][0]['ytd'])
    def test_old_cache_schema_forces_refresh(self):
        self.assertEqual(B.decide_mode({'generated_at':datetime.now(timezone.utc).isoformat()}),'full')
    def test_new_cache_schema_allows_reuse(self):
        self.assertEqual(B.decide_mode({'schema_version':2,'generated_at':datetime.now(timezone.utc).isoformat()}),'live')
    def test_kraken_fallback_unpacks_closes_and_ohlc(self):
        backup={'closes':{'2026-10-09':60000},'ohlc':{'2026-10-09':[59000,61000,58000,60000]}}
        with patch.object(S,'SYMBOLS',[]),patch.object(B,'CRYPTO',[('BTC','BTC-USD','XBTUSD','bitcoin')]),patch.object(S,'coinbase_daily',return_value=None),patch.object(S,'kraken_daily',return_value=backup):
            closes,ohlc,health=B.fetch_history(None)
        self.assertEqual(closes['BTC'],backup['closes']);self.assertEqual(ohlc['BTC'],backup['ohlc'])
        self.assertTrue(health[0]['ok'])
    def test_kraken_current_unfinished_bar_removed(self):
        raw={'error':[],'result':{'XXBTZUSD':[[1,'1','2','1','2'],[86401,'2','3','2','3']],'last':86401}}
        with patch.object(S,'http',return_value=(200,json.dumps(raw).encode())):
            out=S.kraken_daily('XBTUSD')
        self.assertEqual(len(out['closes']),1);self.assertEqual(list(out['closes'].values()),[2.0])
    def test_coinbase_ranges_include_both_start_and_end_and_page_back(self):
        import urllib.parse
        calls=[]
        def fetch(url):
            query=urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            self.assertIn('start',query);self.assertIn('end',query)
            start=int(datetime.fromisoformat(query['start'][0]).timestamp())
            end=int(datetime.fromisoformat(query['end'][0]).timestamp())
            calls.append((start,end))
            rows=[[t,1,2,1,2,10] for t in range(start,end+1,86400)]
            return 200,json.dumps(rows).encode()
        with patch.object(S,'http',side_effect=fetch),patch.object(S.time,'sleep'):
            result=S.coinbase_daily('BTC-USD',days=730,want_ohlc=True)
        self.assertEqual(len(result['closes']),730)
        self.assertEqual(len(calls),3)
        self.assertEqual(calls[1][1],calls[0][0]-1)

    def test_yahoo_unfinished_bar_removed(self):
        now=datetime.now(timezone.utc);base=now.replace(hour=0,minute=0,second=0,microsecond=0)
        ts=[int((base-timedelta(days=40-i)).timestamp()) for i in range(41)]
        quote={'close':[100.0]*41,'open':[99.0]*41,'high':[101.0]*41,'low':[98.0]*41}
        raw={'chart':{'result':[{'meta':{'exchangeTimezoneName':'UTC','currentTradingPeriod':{'regular':{'end':time.time()+3600}}},'timestamp':ts,'indicators':{'quote':[quote]}}]}}
        with patch.object(S,'http',return_value=(200,json.dumps(raw).encode())):out=S.yahoo_history('TEST')
        self.assertNotIn(now.date().isoformat(),out['closes']);self.assertEqual(len(out['closes']),40)
    def test_all_sources_fail_preserves_history_and_stops(self):
        with patch.object(B,'read_json',return_value=None),patch.object(B,'fetch_history',return_value=({}, {}, [{'key':'BTC','ok':False}])),patch.object(B,'write_json') as write,patch.object(B,'fetch_live') as live,patch.object(B.S,'log'):
            self.assertEqual(B.run('full'),1);write.assert_not_called();live.assert_not_called()
    def test_cached_failed_symbols_remain_failed(self):
        hist={'schema_version':2,'generated_at':datetime.now(timezone.utc).isoformat(),'closes':{'BTC':{'2026-10-09':60000}},'health':[{'key':'BTC','ok':False,'src':'前回値'}]}
        with patch.object(B,'read_json',side_effect=lambda path: hist if path == B.HISTORY else {'health':{},'generated_at':'old'}),patch.object(B,'write_json') as write,patch.object(B,'fetch_live',return_value={}),patch.object(B,'build_analytics',return_value={}),patch.object(S,'coingecko_global',return_value=None),patch.object(S,'hyperliquid',return_value=None),patch.object(S,'fear_greed',return_value=None),patch.object(S,'hyperliquid_funding_history',return_value=None),patch.object(S,'log'):
            self.assertEqual(B.run('live'),1)

if __name__=='__main__':unittest.main()
