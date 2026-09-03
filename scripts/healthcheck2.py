#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
healthcheck2.py — 第2次データソース疎通確認

前回判明したこと:
  - Stooq は全滅。HTTP 200 を返すが中身は JavaScript 検証ページ（ボット対策）。
  - Yahoo Finance は日本の家庭IPから問題なく応答した（299ms）。
  - 暗号資産系・米財務省・frankfurter・alternative.me は全て正常。

そこで本スクリプトの目的は 3 つ:
  A. Yahoo Finance を TradFi 側の主軸に据えられるか、銘柄コード単位で実測する
  B. まとめて取得できる API があるか（1銘柄1リクエストだと更新が重い）
  C. 連続リクエストで遮断されないか（更新頻度を決めるのに必須）

使い方:
  python healthcheck2.py               # 全部（3〜4分）
  python healthcheck2.py --selftest    # ネット無しでパーサ検証
  python healthcheck2.py --skip-burst  # 連射テストだけ省く
  python healthcheck2.py --only 日経    # 名前で絞り込み

出力: 標準出力の表 + healthcheck2_result.json + symbols_candidate.json
依存: Python標準ライブラリのみ
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

TIMEOUT = 20
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_RESULT = os.path.join(HERE, "healthcheck2_result.json")
OUT_SYMBOLS = os.path.join(HERE, "symbols_candidate.json")

YQ = "https://query1.finance.yahoo.com"


# ==========================================================================
# HTTP
# ==========================================================================
def http(url: str, method: str = "GET", body: bytes | None = None,
         headers: dict | None = None) -> tuple[int, bytes]:
    h = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "ja,en;q=0.8"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT,
                                    context=ssl.create_default_context()) as r:
            return r.getcode(), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def looks_like_bot_wall(raw: bytes) -> str | None:
    """ボット対策ページを掴まされていないか判定する。

    Stooq は HTTP 200 のまま JavaScript 検証ページを返してきたため、
    ステータスコードだけを見ていると「成功」と誤判定する。
    """
    head = raw[:600].decode("utf-8", "replace").lower()
    for marker, label in (
        ("requires javascript to verify", "JavaScript検証ページ（ボット対策）"),
        ("just a moment", "Cloudflareの待機ページ"),
        ("cf-browser-verification", "Cloudflareのブラウザ検証"),
        ("enable javascript and cookies", "JavaScript/Cookie必須ページ"),
        ("<!doctype html", "HTMLページ（データではない）"),
    ):
        if marker in head:
            return label
    return None


# ==========================================================================
# Yahoo Finance
# ==========================================================================
def yahoo_chart(symbol: str, rng: str = "1mo") -> dict:
    """1銘柄の日足を取得し、要点を辞書で返す。失敗時は例外。"""
    url = f"{YQ}/v8/finance/chart/{urllib.parse.quote(symbol)}?range={rng}&interval=1d"
    code, raw = http(url)

    wall = looks_like_bot_wall(raw)
    if wall:
        raise ValueError(f"{wall} / HTTP {code}")

    j = json.loads(raw)
    chart = j.get("chart") or {}
    if chart.get("error"):
        e = chart["error"]
        raise ValueError(f"{e.get('code')}: {e.get('description')} / HTTP {code}")
    if code != 200:
        raise ValueError(f"HTTP {code}")

    res = (chart.get("result") or [None])[0]
    if not res:
        raise ValueError("resultが空")

    meta = res.get("meta") or {}
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    ts = res.get("timestamp") or []
    closes = q.get("close") or []
    pairs = [(t, c) for t, c in zip(ts, closes) if c is not None]
    if not pairs:
        raise ValueError("終値が1本も無い")

    last_t, last_c = pairs[-1]
    mt = meta.get("regularMarketTime")
    return {
        "symbol": meta.get("symbol", symbol),
        "currency": meta.get("currency"),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "type": meta.get("instrumentType"),
        "bars": len(pairs),
        "last_date": datetime.fromtimestamp(last_t, timezone.utc).strftime("%Y-%m-%d"),
        "last_close": round(float(last_c), 4),
        "market_price": meta.get("regularMarketPrice"),
        "market_time": (datetime.fromtimestamp(mt, timezone.utc)
                        .strftime("%Y-%m-%d %H:%M UTC") if mt else None),
    }


# 世界の株価に並ぶ指標を、Yahooの銘柄コード候補に置き換えたもの。
# 「コードが当たっているか」自体が未検証なので、当てにいくのがこのテストの目的。
YAHOO_TARGETS = [
    # (表示名, コード, カテゴリ, 重要度)
    ("S&P500",            "^GSPC",      "米国株価指数", "必須"),
    ("ダウ平均",           "^DJI",       "米国株価指数", "必須"),
    ("ナスダック総合",      "^IXIC",      "米国株価指数", "必須"),
    ("ナスダック100",      "^NDX",       "米国株価指数", "必須"),
    ("ラッセル2000",       "^RUT",       "米国株価指数", "任意"),
    ("SOX半導体指数",      "^SOX",       "米国株価指数", "任意"),
    ("VIX恐怖指数",        "^VIX",       "米国株価指数", "必須"),

    ("S&P500先物",        "ES=F",       "先物",        "任意"),
    ("ナスダック100先物",   "NQ=F",       "先物",        "任意"),
    ("ダウ先物",           "YM=F",       "先物",        "任意"),
    ("CME日経225先物",     "NKD=F",      "先物",        "任意"),

    ("日経平均",           "^N225",      "アジア株価指数", "必須"),
    ("TOPIX",             "^TOPX",      "アジア株価指数", "任意"),
    ("TOPIX連動ETF",      "1306.T",     "アジア株価指数", "任意"),
    ("香港ハンセン",        "^HSI",       "アジア株価指数", "任意"),
    ("上海総合",           "000001.SS",  "アジア株価指数", "任意"),
    ("韓国KOSPI",         "^KS11",      "アジア株価指数", "任意"),
    ("台湾加権",           "^TWII",      "アジア株価指数", "任意"),
    ("インドSENSEX",       "^BSESN",     "アジア株価指数", "任意"),
    ("豪ASX200",          "^AXJO",      "アジア株価指数", "任意"),

    ("ドイツDAX",          "^GDAXI",     "欧州株価指数", "任意"),
    ("英FTSE100",         "^FTSE",      "欧州株価指数", "任意"),
    ("仏CAC40",           "^FCHI",      "欧州株価指数", "任意"),
    ("ユーロSTOXX50",      "^STOXX50E",  "欧州株価指数", "任意"),

    ("金",                "GC=F",       "商品",        "必須"),
    ("銀",                "SI=F",       "商品",        "任意"),
    ("銅",                "HG=F",       "商品",        "任意"),
    ("プラチナ",           "PL=F",       "商品",        "任意"),
    ("WTI原油",           "CL=F",       "商品",        "必須"),
    ("ブレント原油",        "BZ=F",       "商品",        "任意"),
    ("天然ガス",           "NG=F",       "商品",        "任意"),

    ("米ドル/円",          "JPY=X",      "為替",        "必須"),
    ("ユーロ/米ドル",       "EURUSD=X",   "為替",        "必須"),
    ("ユーロ/円",          "EURJPY=X",   "為替",        "任意"),
    ("英ポンド/米ドル",     "GBPUSD=X",   "為替",        "任意"),
    ("英ポンド/円",        "GBPJPY=X",   "為替",        "任意"),
    ("豪ドル/米ドル",       "AUDUSD=X",   "為替",        "任意"),
    ("米ドル/人民元",       "CNY=X",      "為替",        "任意"),
    ("ドル指数",           "DX-Y.NYB",   "為替",        "必須"),

    ("米2年債利回り",       "^FVX",       "金利",        "任意"),
    ("米10年債利回り",      "^TNX",       "金利",        "必須"),
    ("米30年債利回り",      "^TYX",       "金利",        "任意"),
    ("米13週債利回り",      "^IRX",       "金利",        "任意"),

    ("ビットコイン参考値",   "BTC-USD",    "暗号資産(参考)", "任意"),
]


# ==========================================================================
# その他ソース（前回OKだったものの回帰確認）
# ==========================================================================
def p_coinbase(raw):
    j = json.loads(raw)
    r = j[0]
    return f"{datetime.fromtimestamp(r[0], timezone.utc):%Y-%m-%d} 終値={r[4]}", f"{len(j)}本"


def p_kraken(raw):
    j = json.loads(raw)
    if j.get("error"):
        raise ValueError(str(j["error"]))
    k = [x for x in j["result"] if x != "last"][0]
    rows = j["result"][k]
    return f"{datetime.fromtimestamp(rows[-1][0], timezone.utc):%Y-%m-%d} 終値={rows[-1][4]}", f"{len(rows)}本"


def p_binance(raw):
    j = json.loads(raw)
    if isinstance(j, dict):
        raise ValueError(str(j)[:120])
    r = j[-1]
    return f"{datetime.fromtimestamp(r[0]/1000, timezone.utc):%Y-%m-%d} 終値={r[4]}", f"{len(j)}本"


def p_bybit(raw):
    j = json.loads(raw)
    if j.get("retCode") != 0:
        raise ValueError(str(j.get("retMsg")))
    r = j["result"]["list"][0]
    return f"{datetime.fromtimestamp(int(r[0])/1000, timezone.utc):%Y-%m-%d} 終値={r[4]}", f"{len(j['result']['list'])}本"


def p_cg_price(raw):
    j = json.loads(raw)
    return f"BTC={j['bitcoin']['usd']} / ETH={j['ethereum']['usd']}", "キー無しで成功"


def p_cg_global(raw):
    d = json.loads(raw)["data"]
    return (f"BTCドミナンス={d['market_cap_percentage']['btc']:.2f}% / "
            f"時価総額={d['total_market_cap']['usd']/1e12:.2f}兆USD"), "全体統計"


def p_hl(raw):
    j = json.loads(raw)
    names = [c["name"] for c in j[0]["universe"]]
    i = names.index("BTC")
    c = j[1][i]
    oi = float(c.get("openInterest", 0)) * float(c["markPx"])
    return (f"BTC mark={c['markPx']} funding={c['funding']} 建玉={oi/1e6:.1f}百万USD",
            f"{len(names)}銘柄")


def p_fng(raw):
    d = json.loads(raw)["data"][0]
    return f"{d['value']} ({d['value_classification']})", "恐怖強欲指数"


def p_frank(raw):
    j = json.loads(raw)
    return f"{j['date']} USD/JPY={j['rates']['JPY']}", "ECB日次"


def p_treasury(raw):
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig", "replace"))))
    if not rows:
        raise ValueError("行が0件")
    col = next((k for k in rows[0] if "10 Yr" in k), None)
    if not col:
        raise ValueError(f"10年の列が無い: {list(rows[0])[:6]}")
    return f"{rows[0].get('Date')} 米10年={rows[0][col]}%", f"{len(rows)}日分"


def p_stooq_canary(raw):
    """Stooqが復活していないかを毎回1件だけ見張る。"""
    wall = looks_like_bot_wall(raw)
    if wall:
        raise ValueError(f"依然として遮断されている: {wall}")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"))))
    if not rows or "Close" not in rows[0]:
        raise ValueError("CSVとして読めない")
    return f"復活 / 終値={rows[-1]['Close']}", "主力への再採用を検討する価値あり"


def p_ip(raw):
    j = json.loads(raw)
    country = j.get("country") or j.get("country_name") or "?"
    org = j.get("org") or j.get("asn_org") or j.get("hostname") or ""
    return f"{country} / {str(org)[:40]}", "この実行環境の出口IP"


OTHER_SOURCES = [
    ("環境:出口IP(1)", "診断", "GET", "https://ifconfig.co/json", None, p_ip, "参考"),
    ("環境:出口IP(2)", "診断", "GET", "https://ipinfo.io/json", None, p_ip, "参考"),

    ("stooq:カナリア", "廃止判定", "GET",
     "https://stooq.com/q/d/l/?s=%5Espx&i=d", None, p_stooq_canary, "参考"),

    ("coinbase:BTC日足", "暗号資産", "GET",
     "https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=86400",
     None, p_coinbase, "必須"),
    ("kraken:BTC日足", "暗号資産", "GET",
     "https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1440",
     None, p_kraken, "代替"),
    ("binance:BTC日足", "暗号資産", "GET",
     "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=30",
     None, p_binance, "代替"),
    ("bybit:BTC日足", "暗号資産", "GET",
     "https://api.bybit.com/v5/market/kline?category=spot&symbol=BTCUSDT&interval=D&limit=30",
     None, p_bybit, "代替"),
    ("coingecko:価格", "暗号資産", "GET",
     "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd",
     None, p_cg_price, "必須"),
    ("coingecko:全体統計", "暗号資産", "GET",
     "https://api.coingecko.com/api/v3/global", None, p_cg_global, "必須"),

    ("hyperliquid:板情報", "デリバティブ", "POST",
     "https://api.hyperliquid.xyz/info",
     json.dumps({"type": "metaAndAssetCtxs"}).encode(), p_hl, "必須"),

    ("alternative.me:恐怖強欲", "センチメント", "GET",
     "https://api.alternative.me/fng/?limit=1", None, p_fng, "任意"),
    ("frankfurter:為替", "為替(代替)", "GET",
     "https://api.frankfurter.app/latest?from=USD&to=JPY", None, p_frank, "代替"),
    ("米財務省:利回り曲線", "金利(代替)", "GET",
     "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
     "daily-treasury-rates.csv/{y}/all?type=daily_treasury_yield_curve"
     "&field_tdr_date_value={y}&page&_format=csv".format(y=datetime.now().year),
     None, p_treasury, "代替"),
]


def run_other(name, cat, method, url, body, parser, level) -> dict:
    t0 = time.time()
    rec = {"name": name, "category": cat, "level": level, "ok": False,
           "status": None, "sample": "", "note": ""}
    try:
        headers = {"Content-Type": "application/json"} if body else None
        code, raw = http(url, method, body, headers)
        rec["status"] = code
        wall = looks_like_bot_wall(raw)
        if wall and cat != "廃止判定":
            rec["note"] = f"{wall}（HTTP {code} だが中身はデータではない）"
        elif code != 200:
            rec["note"] = f"HTTP {code}: " + raw.decode("utf-8", "replace")[:110].replace("\n", " ")
        else:
            rec["sample"], rec["note"] = parser(raw)
            rec["ok"] = True
    except Exception as e:  # noqa: BLE001
        rec["note"] = f"{type(e).__name__}: {e}"
    rec["ms"] = int((time.time() - t0) * 1000)
    return rec


# ==========================================================================
# B. まとめ取得 / C. 連射耐性
# ==========================================================================
def test_batch() -> list[dict]:
    """1リクエストで複数銘柄を取れる口があるかを調べる。"""
    out = []
    syms = "^GSPC,^N225,GC=F,JPY=X,^TNX"

    for label, url, kind in [
        ("Yahoo v8 spark(まとめ)", f"{YQ}/v8/finance/spark?symbols={urllib.parse.quote(syms)}&range=1mo&interval=1d", "spark"),
        ("Yahoo v7 quote(まとめ)", f"{YQ}/v7/finance/quote?symbols={urllib.parse.quote(syms)}", "quote"),
    ]:
        t0 = time.time()
        rec = {"name": label, "category": "まとめ取得", "level": "効率", "ok": False,
               "status": None, "sample": "", "note": ""}
        try:
            code, raw = http(url)
            rec["status"] = code
            wall = looks_like_bot_wall(raw)
            if wall:
                rec["note"] = wall
            elif code != 200:
                rec["note"] = f"HTTP {code}: " + raw.decode("utf-8", "replace")[:110].replace("\n", " ")
            else:
                j = json.loads(raw)
                if kind == "spark":
                    got = list(j.keys()) if isinstance(j, dict) else []
                    if not got:
                        raise ValueError(f"想定外の形: {str(j)[:100]}")
                    rec["sample"] = f"{len(got)}銘柄 取得: {', '.join(got[:5])}"
                else:
                    rows = j["quoteResponse"]["result"]
                    if not rows:
                        raise ValueError("resultが空（crumb必須の可能性）")
                    rec["sample"] = "; ".join(
                        f"{r.get('symbol')}={r.get('regularMarketPrice')}" for r in rows[:5])
                rec["ok"] = True
                rec["note"] = "5銘柄を1リクエストで取得できた → 更新が大幅に軽くなる"
        except Exception as e:  # noqa: BLE001
            rec["note"] = f"{type(e).__name__}: {e}"
        rec["ms"] = int((time.time() - t0) * 1000)
        out.append(rec)
    return out


def test_burst(n: int = 15) -> dict:
    """間隔を空けずに連続リクエストして、何回目で遮断されるかを見る。

    更新頻度と待ち時間を決めるための実測。相手に迷惑をかけないよう回数は控えめ。
    """
    print(f"連射テスト: 間隔なしで {n} 回リクエストします ...", flush=True)
    fails, first_fail, lat = 0, None, []
    for i in range(1, n + 1):
        t0 = time.time()
        try:
            code, raw = http(f"{YQ}/v8/finance/chart/%5EGSPC?range=5d&interval=1d")
            ok = (code == 200 and not looks_like_bot_wall(raw))
        except Exception:  # noqa: BLE001
            code, ok = None, False
        lat.append(int((time.time() - t0) * 1000))
        if not ok:
            fails += 1
            if first_fail is None:
                first_fail = i
                print(f"  → {i}回目で失敗 (HTTP {code})")
    return {
        "name": f"Yahoo連射耐性({n}回)", "category": "レート制限", "level": "設計",
        "ok": fails == 0, "status": None, "ms": sum(lat),
        "sample": f"成功 {n - fails}/{n} / 平均 {sum(lat)//len(lat)}ms",
        "note": ("間隔なしでも全て成功。更新間隔の制約は緩い。"
                 if fails == 0 else
                 f"{first_fail}回目で最初の失敗。銘柄ごとに待ち時間を入れる必要がある。"),
    }


# ==========================================================================
# 出力
# ==========================================================================
def print_section(title: str, rows: list[dict]) -> None:
    print(f"\n{'=' * 96}\n{title}\n{'=' * 96}")
    cur = None
    w = max([len(r["name"]) for r in rows] + [10]) + 1
    for r in rows:
        if r.get("category") != cur:
            cur = r.get("category")
            print(f"\n[{cur}]")
        mark = "OK " if r["ok"] else "NG "
        print(f"  {mark} {r['name']:<{w}} {r.get('level', ''):<4} "
              f"{str(r.get('status') or '-'):>4} {r.get('ms', 0):>6}ms  "
              f"{r['sample'] or r['note']}")
        if r["ok"] and r["note"] and r["sample"]:
            print(f"      └ {r['note']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--skip-burst", action="store_true")
    ap.add_argument("--only", default=None)
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    # --- A. Yahoo 銘柄解決 ---
    targets = YAHOO_TARGETS
    if args.only:
        targets = [t for t in YAHOO_TARGETS if args.only in t[0] or args.only in t[1]]

    yahoo_rows = []
    print(f"Yahoo Finance の銘柄コードを {len(targets)} 件確認します（0.8秒間隔）...\n")
    for name, code, cat, level in targets:
        t0 = time.time()
        rec = {"name": name, "code": code, "category": cat, "level": level,
               "ok": False, "status": 200, "sample": "", "note": ""}
        try:
            d = yahoo_chart(code)
            rec["ok"] = True
            rec["detail"] = d
            rec["sample"] = f"{code:<10} {d['last_date']} 終値={d['last_close']:,} {d['currency'] or ''}"
            rec["note"] = f"{d['bars']}本 / {d['exchange']} / 現値{d['market_price']}"
        except Exception as e:  # noqa: BLE001
            rec["note"] = f"{code}: {e}"
        rec["ms"] = int((time.time() - t0) * 1000)
        yahoo_rows.append(rec)
        print(("  OK  " if rec["ok"] else "  NG  ") + f"{name}  {rec['sample'] or rec['note']}")
        time.sleep(0.8)

    print_section("A. Yahoo Finance 銘柄解決", yahoo_rows)

    # --- B. まとめ取得 ---
    batch_rows = test_batch()
    print_section("B. まとめ取得の可否", batch_rows)

    # --- C. 連射耐性 ---
    burst_rows = []
    if not args.skip_burst:
        burst_rows = [test_burst()]
        print_section("C. 連続リクエスト耐性", burst_rows)

    # --- D. その他ソースの回帰確認 ---
    other_rows = []
    print("\nその他ソースを確認します ...")
    for c in OTHER_SOURCES:
        other_rows.append(run_other(*c))
        time.sleep(0.8)
    print_section("D. その他ソースの回帰確認", other_rows)

    # --- 総括 ---
    all_rows = yahoo_rows + batch_rows + burst_rows + other_rows
    ok = sum(1 for r in all_rows if r["ok"])
    ng_req = [f"{r['name']}({r.get('code', '')})" for r in all_rows
              if not r["ok"] and r.get("level") == "必須"]
    print("\n" + "-" * 96)
    print(f"総合: 成功 {ok}/{len(all_rows)}")
    print(f"  Yahoo銘柄: {sum(1 for r in yahoo_rows if r['ok'])}/{len(yahoo_rows)} 解決")
    if ng_req:
        print(f"  必須の失敗: {', '.join(ng_req)}")
    else:
        print("  必須ソースは全て成功。")
    print("-" * 96)

    with open(OUT_RESULT, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(),
                   "yahoo": yahoo_rows, "batch": batch_rows,
                   "burst": burst_rows, "other": other_rows},
                  f, ensure_ascii=False, indent=2)

    # 成功した銘柄だけを、本番用の設定ファイル雛形として書き出す
    cand = {}
    for r in yahoo_rows:
        if r["ok"]:
            cand.setdefault(r["category"], []).append({
                "name": r["name"], "yahoo": r["code"],
                "currency": r["detail"].get("currency"),
                "level": r["level"],
            })
    with open(OUT_SYMBOLS, "w", encoding="utf-8") as f:
        json.dump(cand, f, ensure_ascii=False, indent=2)

    print(f"\n生データ:   {OUT_RESULT}")
    print(f"採用候補:   {OUT_SYMBOLS}")
    return 0


# ==========================================================================
# オフライン・セルフテスト
# ==========================================================================
def _yahoo_fixture(**over) -> bytes:
    base = {"chart": {"error": None, "result": [{
        "meta": {"symbol": "^GSPC", "currency": "USD", "fullExchangeName": "SNP",
                 "instrumentType": "INDEX", "regularMarketPrice": 7665.87,
                 "regularMarketTime": 1787961600},
        "timestamp": [1787875200, 1787961600],
        "indicators": {"quote": [{"close": [7600.0, 7665.87]}]}}]}}
    base["chart"].update(over)
    return json.dumps(base).encode()


def selftest() -> int:
    print("セルフテスト（ネット接続なし）")
    print("-" * 74)
    fails = 0

    def check(no, name, fn):
        nonlocal fails
        try:
            fn()
            print(f"  OK  {no:>2}. {name}")
        except AssertionError as e:
            print(f"  NG  {no:>2}. {name}: {e}")
            fails += 1
        except Exception as e:  # noqa: BLE001
            print(f"  NG  {no:>2}. {name}: 例外 {type(e).__name__}: {e}")
            fails += 1

    # ボット対策ページの検出
    walls = [
        (b'<!DOCTYPE html><html><head><meta charset="utf-8">'
         b'<body><noscript>This site requires JavaScript to verify your browser.',
         "Stooqが返した実際のページ"),
        (b'<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title>',
         "ipapi.coが返した実際のページ"),
    ]
    for i, (raw, label) in enumerate(walls, 1):
        check(i, f"ボット対策ページを検出できる：{label}",
              lambda raw=raw: (_ for _ in ()).throw(AssertionError("検出できず"))
              if looks_like_bot_wall(raw) is None else None)

    check(3, "正常なJSONをボット対策ページと誤判定しない",
          lambda: (_ for _ in ()).throw(AssertionError("誤検出"))
          if looks_like_bot_wall(b'{"chart":{"result":[]}}') is not None else None)

    # Yahooパーサ本体（HTTPを差し替えて検証）
    global http
    original = http

    def with_response(status, raw):
        def fake(url, method="GET", body=None, headers=None):
            return status, raw
        return fake

    def expect_ok():
        globals()["http"] = with_response(200, _yahoo_fixture())
        d = yahoo_chart("^GSPC")
        assert d["last_close"] == 7665.87, d
        assert d["bars"] == 2, d
        assert d["currency"] == "USD", d
        assert d["last_date"] == "2026-08-29", d

    def expect_raise(status, raw, why):
        globals()["http"] = with_response(status, raw)
        try:
            yahoo_chart("XXX")
        except Exception:
            return
        raise AssertionError(f"例外にならなかった: {why}")

    check(4, "Yahoo正常応答を解釈できる", expect_ok)
    check(5, "存在しない銘柄コードをエラーにする",
          lambda: expect_raise(404, json.dumps({"chart": {"error": {
              "code": "Not Found", "description": "No data found, symbol may be delisted"},
              "result": None}}).encode(), "404"))
    check(6, "終値が全部nullならエラーにする",
          lambda: expect_raise(200, json.dumps({"chart": {"error": None, "result": [{
              "meta": {}, "timestamp": [1], "indicators": {"quote": [{"close": [None]}]}}]}}).encode(),
              "null終値"))
    check(7, "ボット対策ページをエラーにする",
          lambda: expect_raise(200, b'<!DOCTYPE html><html><body>'
                               b'<noscript>This site requires JavaScript to verify your browser.',
                               "JS検証ページ"))
    check(8, "HTML断片をエラーにする",
          lambda: expect_raise(200, b'<!doctype html><html>error</html>', "HTML"))

    globals()["http"] = original

    # その他パーサ
    cases = [
        ("coinbase", p_coinbase, json.dumps([[1787961600, 1, 1, 1, 77345.04, 1]]).encode(), "77345.04"),
        ("kraken", p_kraken, json.dumps({"error": [], "result": {
            "XXBTZUSD": [[1756425600, "1", "1", "1", "77336.6", "1", "1", 1]], "last": 1}}).encode(), "77336.6"),
        ("binance", p_binance, json.dumps([[1756425600000, "1", "1", "1", "77380.01", "1"]]).encode(), "77380.01"),
        ("bybit", p_bybit, json.dumps({"retCode": 0, "result": {"list": [
            ["1756425600000", "1", "1", "1", "77377.7", "1", "1"]]}}).encode(), "77377.7"),
        ("coingecko価格", p_cg_price, json.dumps({"bitcoin": {"usd": 77319},
            "ethereum": {"usd": 2800}}).encode(), "77319"),
        ("coingecko統計", p_cg_global, json.dumps({"data": {
            "market_cap_percentage": {"btc": 59.13},
            "total_market_cap": {"usd": 3.68e12}}}).encode(), "59.13"),
        ("hyperliquid", p_hl, json.dumps([{"universe": [{"name": "BTC"}]},
            [{"markPx": "77365.0", "funding": "0.0000125", "openInterest": "2500"}]]).encode(), "77365.0"),
        ("恐怖強欲", p_fng, json.dumps({"data": [{"value": "63",
            "value_classification": "Greed"}]}).encode(), "Greed"),
        ("frankfurter", p_frank, json.dumps({"date": "2026-09-02",
            "rates": {"JPY": 159.6}}).encode(), "159.6"),
        ("米財務省", p_treasury, b"Date,2 Yr,10 Yr,30 Yr\n09/01/2026,3.9,4.79,5.1\n", "4.79"),
        ("出口IP", p_ip, json.dumps({"country": "JP", "org": "NTT"}).encode(), "JP"),
    ]
    for i, (nm, fn, raw, expect) in enumerate(cases, 9):
        def run(fn=fn, raw=raw, expect=expect):
            s, _ = fn(raw)
            assert expect in s, f"'{expect}' が '{s}' に無い"
        check(i, f"{nm} パーサ", run)

    # 異常系
    neg = [
        ("krakenのエラー応答", p_kraken, b'{"error":["EGeneral:Invalid"],"result":{}}'),
        ("binanceのエラー応答", p_binance, b'{"code":-1121,"msg":"Invalid symbol."}'),
        ("bybitのエラー応答", p_bybit, b'{"retCode":10001,"retMsg":"bad"}'),
        ("米財務省の列違い", p_treasury, b"Date,Foo\n09/01/2026,1\n"),
    ]
    for i, (nm, fn, raw) in enumerate(neg, 9 + len(cases)):
        def run(fn=fn, raw=raw):
            try:
                fn(raw)
            except Exception:
                return
            raise AssertionError("素通りした")
        check(i, f"異常系: {nm}", run)

    total = 8 + len(cases) + len(neg)
    print("-" * 74)
    print(f"結果: {total - fails}/{total} 合格")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
