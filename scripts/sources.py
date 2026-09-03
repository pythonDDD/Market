#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sources.py — データ取得層

疎通確認で実際に通ったものだけを使う。落ちたものは入れていない。
  使う  : Yahoo Finance / Coinbase / Kraken / CoinGecko / Hyperliquid /
          alternative.me / 米財務省
  使わない: Stooq（ボット対策で遮断）
          Binance（米国IPから451）
          Bybit（米国IPから403）
  GitHub Actions のランナーは米国なので、後者3つは本番で必ず落ちる。

全ての取得関数は「失敗しても例外を投げず、None を返す」方針。
1銘柄の失敗でサイト全体が止まらないようにするため。
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

TIMEOUT = 25
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
YQ = "https://query1.finance.yahoo.com"

LOG: list[str] = []


def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}"
    LOG.append(line)
    print(line, flush=True)


def http(url: str, method: str = "GET", body: bytes | None = None,
         headers: dict | None = None, retries: int = 2) -> tuple[int, bytes] | None:
    """失敗したら少し待って再試行する。それでもだめなら None。"""
    h = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
    if headers:
        h.update(headers)
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=h, method=method)
            with urllib.request.urlopen(req, timeout=TIMEOUT,
                                        context=ssl.create_default_context()) as r:
                return r.getcode(), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except Exception as e:  # noqa: BLE001
            if attempt == retries:
                log(f"  通信失敗 {url[:70]} : {type(e).__name__}")
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def is_bot_wall(raw: bytes) -> bool:
    head = raw[:400].decode("utf-8", "replace").lower()
    return ("requires javascript" in head or "just a moment" in head
            or head.lstrip().startswith("<!doctype html"))


# ==========================================================================
# 銘柄定義
#   疎通確認（healthcheck2）で解決した43銘柄。^TOPX は解決しなかったので入れない。
#   unit は Yahoo の返り値を信用せず、こちらで持つ。
#     price = 価格（変化率で扱う） / rate = 利回り（差分で扱う） / index = 指数
# ==========================================================================
SYMBOLS = [
    # (キー, 表示名, Yahooコード, 分類, 単位, 通貨, 主要か)
    ("SPX",    "S&P500",        "^GSPC",     "米国株",  "index", "USD", True),
    ("DJI",    "ダウ平均",        "^DJI",      "米国株",  "index", "USD", True),
    ("IXIC",   "ナスダック総合",    "^IXIC",     "米国株",  "index", "USD", False),
    ("NDX",    "ナスダック100",   "^NDX",      "米国株",  "index", "USD", True),
    ("RUT",    "ラッセル2000",    "^RUT",      "米国株",  "index", "USD", False),
    ("SOX",    "SOX半導体",       "^SOX",      "米国株",  "index", "USD", False),
    ("VIX",    "VIX恐怖指数",     "^VIX",      "米国株",  "index", "pt",  True),

    ("ES",     "S&P500先物",     "ES=F",      "先物",    "price", "USD", False),
    ("NQ",     "ナスダック100先物", "NQ=F",      "先物",    "price", "USD", False),
    ("YM",     "ダウ先物",         "YM=F",      "先物",    "price", "USD", False),
    ("NKD",    "CME日経225先物",  "NKD=F",     "先物",    "price", "USD", False),

    ("N225",   "日経平均",         "^N225",     "アジア株", "index", "JPY", True),
    ("TOPIXE", "TOPIX連動ETF",   "1306.T",    "アジア株", "price", "JPY", False),
    ("HSI",    "香港ハンセン",      "^HSI",      "アジア株", "index", "HKD", False),
    ("SSEC",   "上海総合",         "000001.SS", "アジア株", "index", "CNY", False),
    ("KS11",   "韓国KOSPI",      "^KS11",     "アジア株", "index", "KRW", False),
    ("TWII",   "台湾加権",         "^TWII",     "アジア株", "index", "TWD", False),
    ("BSESN",  "インドSENSEX",    "^BSESN",    "アジア株", "index", "INR", False),
    ("AXJO",   "豪ASX200",       "^AXJO",     "アジア株", "index", "AUD", False),

    ("DAX",    "ドイツDAX",       "^GDAXI",    "欧州株",  "index", "EUR", True),
    ("FTSE",   "英FTSE100",      "^FTSE",     "欧州株",  "index", "GBP", False),
    ("CAC",    "仏CAC40",        "^FCHI",     "欧州株",  "index", "EUR", False),
    ("SX5E",   "ユーロSTOXX50",   "^STOXX50E", "欧州株",  "index", "EUR", False),

    ("GOLD",   "金",             "GC=F",      "商品",    "price", "USD", True),
    ("SILVER", "銀",             "SI=F",      "商品",    "price", "USD", False),
    ("COPPER", "銅",             "HG=F",      "商品",    "price", "USD", False),
    ("PLAT",   "プラチナ",         "PL=F",      "商品",    "price", "USD", False),
    ("WTI",    "WTI原油",         "CL=F",      "商品",    "price", "USD", True),
    ("BRENT",  "ブレント原油",      "BZ=F",      "商品",    "price", "USD", False),
    ("NGAS",   "天然ガス",         "NG=F",      "商品",    "price", "USD", False),

    ("USDJPY", "米ドル/円",        "JPY=X",     "為替",    "price", "JPY", True),
    ("EURUSD", "ユーロ/米ドル",     "EURUSD=X",  "為替",    "price", "USD", True),
    ("EURJPY", "ユーロ/円",        "EURJPY=X",  "為替",    "price", "JPY", False),
    ("GBPUSD", "英ポンド/米ドル",   "GBPUSD=X",  "為替",    "price", "USD", False),
    ("GBPJPY", "英ポンド/円",      "GBPJPY=X",  "為替",    "price", "JPY", False),
    ("AUDUSD", "豪ドル/米ドル",     "AUDUSD=X",  "為替",    "price", "USD", False),
    ("USDCNY", "米ドル/人民元",     "CNY=X",     "為替",    "price", "CNY", False),
    ("DXY",    "ドル指数",         "DX-Y.NYB",  "為替",    "index", "pt",  True),

    ("US2Y",   "米2年債利回り",     "^FVX",      "金利",    "rate",  "%",   False),
    ("US10Y",  "米10年債利回り",    "^TNX",      "金利",    "rate",  "%",   True),
    ("US30Y",  "米30年債利回り",    "^TYX",      "金利",    "rate",  "%",   False),
    ("US3M",   "米13週債利回り",    "^IRX",      "金利",    "rate",  "%",   False),

    ("BTCY",   "BTC（Yahoo・照合用）", "BTC-USD",  "暗号資産", "price", "USD", False),
]

SYM_BY_KEY = {s[0]: s for s in SYMBOLS}


# ==========================================================================
# Yahoo Finance
# ==========================================================================
def yahoo_history(code: str, rng: str = "2y") -> dict | None:
    """日足の履歴と、取得時点の現値を返す。

    確定した日足と現値は必ず分けて持つ。アジア市場は取得時点で取引中のことが多く、
    最終足（前営業日の終値）と現値が数％ずれる。混ぜると相関計算が壊れる。
    """
    url = f"{YQ}/v8/finance/chart/{urllib.parse.quote(code)}?range={rng}&interval=1d"
    r = http(url)
    if r is None:
        return None
    code_, raw = r
    if code_ != 200 or is_bot_wall(raw):
        log(f"  {code}: HTTP {code_}" + ("（ボット対策ページ）" if is_bot_wall(raw) else ""))
        return None
    try:
        j = json.loads(raw)
        res = (j.get("chart", {}).get("result") or [None])[0]
        if not res:
            return None
        meta = res.get("meta") or {}
        q = (res["indicators"]["quote"] or [{}])[0]
        ts = res.get("timestamp") or []
        cl, op = q.get("close") or [], q.get("open") or []
        hi, lo = q.get("high") or [], q.get("low") or []
        closes, ohlc = {}, {}
        for i, t in enumerate(ts):
            c = cl[i] if i < len(cl) else None
            if c is None:
                continue
            d = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")
            closes[d] = float(c)
            o = op[i] if i < len(op) and op[i] is not None else c
            h = hi[i] if i < len(hi) and hi[i] is not None else max(o, c)
            l = lo[i] if i < len(lo) and lo[i] is not None else min(o, c)
            ohlc[d] = [round(float(o), 4), round(float(h), 4),
                       round(float(l), 4), round(float(c), 4)]
        if len(closes) < 30:
            return None
        return {
            "closes": closes,
            "ohlc": ohlc,
            "live": meta.get("regularMarketPrice"),
            "live_time": meta.get("regularMarketTime"),
            "exchange": meta.get("fullExchangeName"),
        }
    except Exception as e:  # noqa: BLE001
        log(f"  {code}: 解釈失敗 {type(e).__name__}")
        return None


def yahoo_spark(codes: list[str]) -> dict:
    """複数銘柄の現値をまとめて取得する。1リクエスト5銘柄まで。

    15分ごとの更新はこちらだけを使う。履歴の再取得は不要なため。
    """
    out = {}
    for i in range(0, len(codes), 5):
        chunk = codes[i:i + 5]
        url = (f"{YQ}/v8/finance/spark?symbols={urllib.parse.quote(','.join(chunk))}"
               f"&range=5d&interval=1d")
        r = http(url)
        if r is None or r[0] != 200 or is_bot_wall(r[1]):
            log(f"  spark失敗: {','.join(chunk)}")
            continue
        try:
            j = json.loads(r[1])
            for sym, d in j.items():
                cl = [c for c in (d.get("close") or []) if c is not None]
                if cl:
                    out[sym] = {"live": cl[-1], "prev": cl[-2] if len(cl) > 1 else None}
        except Exception:  # noqa: BLE001
            log(f"  spark解釈失敗: {','.join(chunk)}")
        time.sleep(0.3)
    return out


# ==========================================================================
# 暗号資産
# ==========================================================================
def coinbase_daily(product: str, days: int = 300, want_ohlc: bool = False) -> dict | None:
    """Coinbaseの日足。1リクエスト最大300本なので、それ以上は複数回に分ける。

    want_ohlc=True のときは終値だけでなく四本値を返す（ローソク足の描画用）。
    """
    out, ohlc = {}, {}
    end = int(time.time())
    for _ in range(3):
        url = (f"https://api.exchange.coinbase.com/products/{product}/candles"
               f"?granularity=86400&end={end}")
        r = http(url)
        if r is None or r[0] != 200:
            break
        try:
            rows = json.loads(r[1])
        except Exception:  # noqa: BLE001
            break
        if not rows:
            break
        for row in rows:
            # Coinbaseの並びは [時刻, 安値, 高値, 始値, 終値, 出来高]
            d = datetime.fromtimestamp(row[0], timezone.utc).strftime("%Y-%m-%d")
            out[d] = float(row[4])
            ohlc[d] = [round(float(row[3]), 4), round(float(row[2]), 4),
                       round(float(row[1]), 4), round(float(row[4]), 4)]
        end = min(int(row[0]) for row in rows) - 86400
        time.sleep(0.35)
        if len(out) >= days:
            break
    if len(out) < 30:
        return None
    return {"closes": out, "ohlc": ohlc} if want_ohlc else out


def kraken_daily(pair: str) -> dict | None:
    """Coinbaseが落ちたときの控え。720本まで取れる。

    返り値の形は coinbase_daily と揃える。呼び出し側で分岐したくないため。
    """
    r = http(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=1440")
    if r is None or r[0] != 200:
        return None
    try:
        j = json.loads(r[1])
        if j.get("error"):
            return None
        k = [x for x in j["result"] if x != "last"][0]
        closes, ohlc = {}, {}
        for row in j["result"][k]:
            d = datetime.fromtimestamp(row[0], timezone.utc).strftime("%Y-%m-%d")
            closes[d] = float(row[4])          # [時刻, 始値, 高値, 安値, 終値, ...]
            ohlc[d] = [float(row[1]), float(row[2]), float(row[3]), float(row[4])]
        return {"closes": closes, "ohlc": ohlc} if closes else None
    except Exception:  # noqa: BLE001
        return None


def coingecko_global() -> dict | None:
    r = http("https://api.coingecko.com/api/v3/global")
    if r is None or r[0] != 200:
        return None
    try:
        d = json.loads(r[1])["data"]
        return {"btc_dominance": round(d["market_cap_percentage"]["btc"], 2),
                "eth_dominance": round(d["market_cap_percentage"].get("eth", 0), 2),
                "total_mcap_usd": d["total_market_cap"]["usd"],
                "mcap_change_24h": round(d.get("market_cap_change_percentage_24h_usd", 0), 2)}
    except Exception:  # noqa: BLE001
        return None


def coingecko_prices(ids: list[str]) -> dict | None:
    r = http("https://api.coingecko.com/api/v3/simple/price?ids="
             + ",".join(ids) + "&vs_currencies=usd&include_24hr_change=true")
    if r is None or r[0] != 200:
        return None
    try:
        return json.loads(r[1])
    except Exception:  # noqa: BLE001
        return None


def hyperliquid() -> dict | None:
    """資金調達レートと建玉。fundingは1時間あたりの値であることに注意。"""
    r = http("https://api.hyperliquid.xyz/info", "POST",
             json.dumps({"type": "metaAndAssetCtxs"}).encode(),
             {"Content-Type": "application/json"})
    if r is None or r[0] != 200:
        return None
    try:
        j = json.loads(r[1])
        names = [c["name"] for c in j[0]["universe"]]
        out = {}
        for name in ("BTC", "ETH", "SOL"):
            if name not in names:
                continue
            c = j[1][names.index(name)]
            mark = float(c["markPx"])
            out[name] = {
                "mark": mark,
                "funding_hourly_pct": float(c["funding"]) * 100,
                "funding_annual_pct": float(c["funding"]) * 24 * 365 * 100,
                "open_interest_usd": float(c.get("openInterest", 0)) * mark,
                "day_volume_usd": float(c.get("dayNtlVlm", 0)),
            }
        return out or None
    except Exception:  # noqa: BLE001
        return None


def fear_greed() -> dict | None:
    r = http("https://api.alternative.me/fng/?limit=30")
    if r is None or r[0] != 200:
        return None
    try:
        d = json.loads(r[1])["data"]
        return {"value": int(d[0]["value"]), "label": d[0]["value_classification"],
                "week_ago": int(d[7]["value"]) if len(d) > 7 else None,
                "month_ago": int(d[29]["value"]) if len(d) > 29 else None}
    except Exception:  # noqa: BLE001
        return None


# ==========================================================================
# セルフテスト（ネット接続なし）
# ==========================================================================
def selftest() -> int:
    print("sources.py セルフテスト")
    print("-" * 70)
    fails, n = [], [0]

    def ck(name, cond, got=""):
        n[0] += 1
        print(f"  {'OK' if cond else 'NG'}  {name}" + ("" if cond else f"  {got}"))
        if not cond:
            fails.append(name)

    ck("銘柄定義が43件", len(SYMBOLS) == 43, len(SYMBOLS))
    ck("キーが重複していない", len({s[0] for s in SYMBOLS}) == 43)
    ck("Yahooコードが重複していない", len({s[2] for s in SYMBOLS}) == 43)
    ck("解決しなかった^TOPXを含まない", "^TOPX" not in {s[2] for s in SYMBOLS})
    # 疎通確認で落ちたソースを、うっかり呼び出していないか確認する。
    # このテスト自身がドメイン名を含むので、セルフテスト部より前だけを検査する。
    src = open(__file__, encoding="utf-8").read()
    src = src.split("# セルフテスト（ネット接続なし）")[0]
    for host in ("api.binance.com", "api.bybit.com", "stooq.com"):
        ck(f"{host} を呼び出していない", host not in src)

    rates = [s[0] for s in SYMBOLS if s[4] == "rate"]
    ck("利回りが4本ある", sorted(rates) == ["US10Y", "US2Y", "US30Y", "US3M"], rates)
    ck("リスク指数に必要な銘柄が揃っている",
       all(k in SYM_BY_KEY for k in ("SPX", "VIX", "GOLD", "COPPER", "US10Y", "DXY")))

    # Coinbaseの並び順を取り違えると高値と安値が入れ替わる。定数として残しておく。
    ck("Coinbaseの列順の想定が書かれている", "[時刻, 安値, 高値, 始値, 終値" in src)
    src2 = open(__file__, encoding="utf-8").read()
    ck("Yahooが四本値を返す実装になっている", '"ohlc": ohlc' in src2)
    ck("Coinbaseの列順（安値・高値・始値・終値）に対応している",
       "float(row[3]), float(row[2]), float(row[1]), float(row[4])" in src2)
    ck("Krakenの列順（始値・高値・安値・終値）に対応している",
       "float(row[1]), float(row[2]), float(row[3]), float(row[4])" in src2)
    ck("ボット対策ページを検出", is_bot_wall(b"<!DOCTYPE html><body>Just a moment..."))
    ck("正常JSONを誤検出しない", not is_bot_wall(b'{"chart":{"result":[]}}'))

    print("-" * 70)
    print(f"結果: {n[0] - len(fails)}/{n[0]} 合格")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(selftest())
