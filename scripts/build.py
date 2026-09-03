#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build.py — サイトのデータを組み立てる

  python build.py --full      履歴も取り直す（1日1回。43銘柄ぶん取得）
  python build.py --live      現値だけ更新（15分ごと。9リクエストで済む）
  python build.py --selftest  ネット無しで全体を検証
  python build.py --mock      架空データで一通り動かし、出力の形を確認する

出力: docs/data/latest.json（サイトが最初に読む。分析結果ぜんぶ）
      docs/data/history.json（日足と四本値。--live で再利用し、ローソク足もここから読む）

以前は docs/data/ohlc.json も書いていたが、app.js は一度も読んでいなかった。
ローソク足は history.json から描いている。毎回作って毎回配信するだけの
死んだファイルだったので、2026-09-03 に生成をやめた。

失敗しても止まらない方針。1銘柄取れなければその銘柄だけ前回値を残し、
どの銘柄がいつから古いかを latest.json の health に必ず書く。
黙って古い値を新しい顔で出すのが一番危ないため。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analytics as A          # noqa: E402
import sources as S            # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "docs", "data")
LATEST = os.path.join(DATA_DIR, "latest.json")
HISTORY = os.path.join(DATA_DIR, "history.json")

CRYPTO = [("BTC", "BTC-USD", "XBTUSD", "bitcoin"),
          ("ETH", "ETH-USD", "ETHUSD", "ethereum"),
          ("SOL", "SOL-USD", "SOLUSD", "solana")]


# ==========================================================================
# 入出力
# ==========================================================================
def read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def write_json(path: str, obj: dict) -> None:
    """一時ファイルに書いてから差し替える。書き込み途中で落ちても壊れないように。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def rnd(v, n=4):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    return round(float(v), n)


# ==========================================================================
# 取得
# ==========================================================================
def fetch_history(prev: dict | None) -> tuple[dict, list[dict]]:
    """43銘柄＋暗号資産3種の日足を取り直す。取れなかったものは前回値を残す。"""
    prev_c = (prev or {}).get("closes", {})
    prev_o = (prev or {}).get("ohlc", {})
    closes, ohlc, health = {}, {}, []

    for key, name, code, cat, unit, cur, major in S.SYMBOLS:
        d = S.yahoo_history(code)
        if d and d["closes"]:
            closes[key] = d["closes"]
            ohlc[key] = d.get("ohlc", {})
            health.append({"key": key, "ok": True, "src": "yahoo",
                           "last": max(d["closes"])})
        elif key in prev_c:
            closes[key] = prev_c[key]
            ohlc[key] = prev_o.get(key, {})
            health.append({"key": key, "ok": False, "src": "前回値",
                           "last": max(prev_c[key]), "note": "取得失敗のため前回値"})
        else:
            health.append({"key": key, "ok": False, "src": "なし",
                           "note": "取得失敗・前回値も無し"})
        time.sleep(0.25)

    for key, cb, kr, _cg in CRYPTO:
        d = S.coinbase_daily(cb, want_ohlc=True)
        src = "coinbase"
        if d:
            closes[key], ohlc[key] = d["closes"], d["ohlc"]
        else:
            d, src = S.kraken_daily(kr), "kraken(控え)"
            if d:
                closes[key], ohlc[key] = d, {}
        if d:
            health.append({"key": key, "ok": True, "src": src, "last": max(closes[key])})
        elif key in prev_c:
            closes[key] = prev_c[key]
            ohlc[key] = prev_o.get(key, {})
            health.append({"key": key, "ok": False, "src": "前回値", "last": max(prev_c[key])})
        else:
            health.append({"key": key, "ok": False, "src": "なし"})

    # 銅÷金。リスク選好指数の材料。景気敏感な銅が金に勝っていればリスクオン。
    if "COPPER" in closes and "GOLD" in closes:
        ks, c, g = A.align(closes["COPPER"], closes["GOLD"])
        closes["COPPER_GOLD"] = {k: cv / gv for k, cv, gv in zip(ks, c, g) if gv}

    return closes, ohlc, health


def fetch_live() -> dict:
    codes = [s[2] for s in S.SYMBOLS]
    spark = S.yahoo_spark(codes)
    out = {}
    for key, name, code, *_ in S.SYMBOLS:
        if code in spark:
            out[key] = spark[code]
    cg = S.coingecko_prices([c[3] for c in CRYPTO])
    if cg:
        for key, _cb, _kr, cgid in CRYPTO:
            if cgid in cg:
                out[key] = {"live": cg[cgid]["usd"],
                            "chg24h": cg[cgid].get("usd_24h_change")}
    return out


# ==========================================================================
# 分析
# ==========================================================================
def build_analytics(closes: dict[str, dict]) -> dict:
    out: dict = {}

    # --- 各銘柄の日次変化 ---
    # 利回りは変化率ではなく差分。4.50%→4.79% を「+6.4%」と書くのは誤り。
    rets = {}
    for key in closes:
        spec = S.SYM_BY_KEY.get(key)
        unit = spec[4] if spec else "price"
        rets[key] = A.diffs(closes[key]) if unit == "rate" else A.returns(closes[key])

    # --- 銘柄ごとの要約 ---
    summary = []
    for key, name, code, cat, unit, cur, major in S.SYMBOLS + [
            ("BTC", "ビットコイン", "BTC-USD", "暗号資産", "price", "USD", True),  # Coinbase
            ("ETH", "イーサリアム", "ETH-USD", "暗号資産", "price", "USD", True),
            ("SOL", "ソラナ", "SOL-USD", "暗号資産", "price", "USD", False)]:
        s = closes.get(key)
        if not s or len(s) < 30:
            continue
        ks = sorted(s)
        r = sorted(rets[key].values(), key=lambda _: 0) and [rets[key][k] for k in sorted(rets[key])]
        ytd_base = next((s[k] for k in ks if k >= f"{ks[-1][:4]}-01-01"), None)
        dd = A.max_drawdown(s)
        summary.append({
            "key": key, "name": name, "cat": cat, "unit": unit, "cur": cur, "major": major,
            "code": code,
            "yahoo": code or None,
            "last": rnd(s[ks[-1]], 4), "last_date": ks[-1],
            "chg1d": rnd(r[-1], 3) if r else None,
            "chg5d": rnd(sum(r[-5:]), 3) if len(r) >= 5 else None,
            "chg20d": rnd(sum(r[-20:]), 3) if len(r) >= 20 else None,
            "ytd": rnd((s[ks[-1]] / ytd_base - 1) * 100, 2) if ytd_base and unit != "rate"
                   else (rnd(s[ks[-1]] - ytd_base, 3) if ytd_base else None),
            "z1d": rnd(A.zscore(r), 2),
            "pctile": rnd(A.percentile_rank(r[-1], r[-504:]), 1) if r else None,
            "vol20": rnd(A.realized_vol(r, 20), 1),
            "vol60": rnd(A.realized_vol(r, 60), 1),
            "drawdown": dd["current_dd"],
            "spark": [rnd(s[k], 4) for k in ks[-30:]],
        })
    out["summary"] = summary

    # --- 異常スキャン ---
    # 値動きと「関係の変化」はスケールがまるで違うので、混ぜずに別々に出す。
    # 一緒に並べると値動きばかりが上位を占め、相関の異常が埋もれてしまうため。
    scan = []
    for row in summary:
        if row["z1d"] is not None:
            scan.append({"label": row["name"], "sub": "日次変化", "z": row["z1d"],
                         "date": row["last_date"],
                         "value": f'{row["chg1d"]:+.2f}' + ("pt" if row["unit"] == "rate" else "%")})
    scan.sort(key=lambda r: -abs(r["z"] or 0))
    out["scan"] = scan[:10]

    scan_rel = []
    pairs = [("BTC", o) for o in ("SPX", "NDX", "GOLD", "DXY", "US10Y")] + \
            [("SPX", "GOLD"), ("SPX", "US10Y"), ("GOLD", "DXY"), ("N225", "USDJPY")]
    for a, b in pairs:
        hist = rolling_series(closes, a, b, 30)
        if len(hist) < 90:
            continue
        vals = [v[1] for v in hist]
        z = A.zscore(vals)
        if z is None:
            continue
        c252 = A.corr(*A.align(rets[a], rets[b])[1:]) if a in rets and b in rets else None
        nm = lambda k: S.SYM_BY_KEY[k][1] if k in S.SYM_BY_KEY else k
        scan_rel.append({"label": f"{nm(a)} × {nm(b)}", "sub": "30日相関",
                         "z": rnd(z, 2), "date": hist[-1][0],
                         "value": f"{vals[-1]:+.2f}"
                                  + (f"（1年 {c252:+.2f}）" if c252 is not None else "")})
    scan_rel.sort(key=lambda r: -abs(r["z"] or 0))
    out["scan_rel"] = scan_rel[:8]

    # --- BTCの分解 ---
    out["decomp"] = decompose(closes, rets)

    # --- ローリング相関 ---
    out["rolling_corr"] = {o: rolling_series(closes, "BTC", o, 30)[-260:]
                           for o in ("SPX", "NDX", "GOLD", "DXY")}
    # 標本30個の相関には大きな誤差がある。その幅を画面に描くために計算しておく。
    c_now = out["rolling_corr"].get("SPX") or []
    out["corr_ci"] = A.corr_ci(c_now[-1][1], 30) if c_now else None

    # --- リード・ラグ ---
    out["lead_lag"] = {}
    for other in ("SPX", "DXY", "GOLD"):
        if "BTC" not in rets or other not in rets:
            continue
        _k, x, y = A.align(rets[other], rets["BTC"])
        if len(x) > 120:
            out["lead_lag"][other] = A.lead_lag(x[-504:], y[-504:], 7)

    # --- 曜日と月 ---
    out["calendar"] = {}
    for key in ("BTC", "SPX", "N225", "GOLD"):
        if key in rets and len(rets[key]) > 200:
            out["calendar"][key] = {"dow": A.calendar_stats(rets[key], "dow"),
                                    "month": A.calendar_stats(rets[key], "month")}

    # --- 分布 ---
    out["distribution"] = {}
    for key in ("BTC", "SPX", "VIX"):
        if key not in rets:
            continue
        vals = [rets[key][k] for k in sorted(rets[key])][-504:]
        if len(vals) < 100:
            continue
        out["distribution"][key] = {
            "values": [rnd(v, 3) for v in vals], "now": rnd(vals[-1], 3),
            "date": sorted(rets[key])[-1],
            "pctile": rnd(A.percentile_rank(vals[-1], vals), 1),
        }

    # --- 相関マトリクス ---
    mkeys = [k for k in ("BTC", "ETH", "SPX", "NDX", "N225", "DAX", "GOLD",
                         "WTI", "DXY", "US10Y", "VIX") if k in rets]
    mx = []
    for a in mkeys:
        row = []
        for b in mkeys:
            if a == b:
                row.append(1.0)
                continue
            _k, x, y = A.align(rets[a], rets[b])
            c = A.corr(x[-30:], y[-30:])
            row.append(rnd(c, 3))
        mx.append(row)
    out["matrix"] = {"keys": mkeys,
                     "names": [S.SYM_BY_KEY[k][1] if k in S.SYM_BY_KEY else k for k in mkeys],
                     "values": mx}

    # --- 相関の壊れ度ランキング ---
    breaks = []
    for i, a in enumerate(mkeys):
        for b in mkeys[i + 1:]:
            _k, x, y = A.align(rets[a], rets[b])
            c30, c252 = A.corr(x[-30:], y[-30:]), A.corr(x[-252:], y[-252:])
            if c30 is None or c252 is None:
                continue
            breaks.append({"a": a, "b": b, "c30": rnd(c30, 3), "c252": rnd(c252, 3),
                           "gap": rnd(c30 - c252, 3)})
    breaks.sort(key=lambda r: -abs(r["gap"]))
    out["corr_breaks"] = breaks[:10]

    # --- リスク選好 ---
    out["risk"] = A.risk_appetite(closes)
    out["risk_series"] = A.risk_appetite_series(closes, 180)

    # --- 類似局面 ---
    out["analog"] = build_analog(closes, rets)

    # --- 通貨換算 ---
    out["fx_adjusted"] = fx_adjusted(closes)

    return out


def rolling_series(closes, a, b, w):
    ra = A.diffs(closes[a]) if S.SYM_BY_KEY.get(a, [None]*5)[4:5] == ["rate"] else A.returns(closes.get(a, {}))
    rb = A.diffs(closes[b]) if S.SYM_BY_KEY.get(b, [None]*5)[4:5] == ["rate"] else A.returns(closes.get(b, {}))
    if not ra or not rb:
        return []
    k, x, y = A.align(ra, rb)
    return A.rolling_corr(k, x, y, w)


def corr_between(closes, a, b, w):
    s = rolling_series(closes, a, b, w)
    return s[-1][1] if s else None


def decompose(closes, rets):
    """今日のBTCの動きを、株式で説明できる分と固有の分に割る。"""
    if "BTC" not in rets or "SPX" not in rets:
        return None
    k, spx, btc = A.align(rets["SPX"], rets["BTC"])
    if len(k) < 120:
        return None
    r = A.ols(btc[-252:], spx[-252:])
    if not r:
        return None
    explained = r["beta"] * spx[-1]
    beta_hist = []
    for i in range(120, len(k), 3):
        rr = A.ols(btc[max(0, i - 90):i], spx[max(0, i - 90):i])
        if rr:
            beta_hist.append([k[i - 1], rnd(rr["beta"], 3)])
    return {"date": k[-1], "btc": rnd(btc[-1], 3), "spx": rnd(spx[-1], 3),
            "beta": rnd(r["beta"], 3), "alpha": rnd(r["alpha"], 4), "r2": rnd(r["r2"], 3),
            "explained": rnd(explained, 3), "idiosyncratic": rnd(btc[-1] - explained, 3),
            "beta_series": beta_hist[-180:]}


def build_analog(closes, rets):
    """いまと似た条件だった日を過去から探し、その後のBTCリターン分布を出す。

    条件が厳しすぎると該当が数日しかなく、そこから分布を描いても意味がない。
    そこで条件を段階的に緩め、独立した局面が十分な数になった時点で止める。
    どの段階を使ったかは必ず出力に残す。
    """
    if "BTC" not in closes or "SPX" not in rets or "VIX" not in closes:
        return None
    corr_s = rolling_series(closes, "BTC", "SPX", 30)
    if len(corr_s) < 200:
        return None
    corr_map = dict(corr_s)
    ma20 = A.sma(closes["BTC"], 20)
    vix = closes["VIX"]

    now_date = max(k for k in corr_map)
    now_corr = corr_map[now_date]
    now_vix = vix.get(max((k for k in vix if k <= now_date), default=""), None)
    if now_vix is None:
        return None
    now_below_ma = (now_date in ma20 and closes["BTC"][now_date] < ma20[now_date])

    def state(d):
        vd = max((k for k in vix if k <= d), default=None)
        if vd is None or d not in ma20 or d not in closes["BTC"]:
            return None
        return corr_map[d], vix[vd], closes["BTC"][d] < ma20[d]

    # 緩い方へ向かう順。近さの判定は「幅」で見る（真偽の一致より情報量が多い）。
    levels = [
        ("相関±0.15・VIX±15%・移動平均の向きが一致",
         lambda c, v, m: abs(c - now_corr) < 0.15 and abs(v - now_vix) / now_vix < 0.15
         and m == now_below_ma),
        ("相関±0.25・VIX±25%・移動平均の向きが一致",
         lambda c, v, m: abs(c - now_corr) < 0.25 and abs(v - now_vix) / now_vix < 0.25
         and m == now_below_ma),
        ("相関±0.25・VIX±25%（移動平均は問わない）",
         lambda c, v, m: abs(c - now_corr) < 0.25 and abs(v - now_vix) / now_vix < 0.25),
        ("相関±0.30のみ", lambda c, v, m: abs(c - now_corr) < 0.30),
    ]

    MIN_CLUSTERS = 15
    chosen, matches = None, []
    for label, fn in levels:
        ms = []
        for d in sorted(corr_map):
            if d >= now_date:
                continue
            st = state(d)
            if st and fn(*st):
                ms.append(d)
        chosen, matches = label, ms
        if A.independent_clusters(ms) >= MIN_CLUSTERS:
            break

    res = A.analogs(closes["BTC"], matches, 20)
    if res:
        res["condition"] = {
            "level": chosen, "min_clusters": MIN_CLUSTERS,
            "corr30": rnd(now_corr, 3), "vix": rnd(now_vix, 2),
            "below_ma20": now_below_ma, "as_of": now_date,
            "reliable": res["n_independent"] >= MIN_CLUSTERS,
        }
    return res


def fx_adjusted(closes):
    """円建てとドル建てで見え方が変わる資産を並べる。日本から見た実質の成績。"""
    out = []
    usdjpy = closes.get("USDJPY")
    if not usdjpy:
        return out
    for key, label in (("N225", "日経平均"), ("SPX", "S&P500"), ("BTC", "ビットコイン"),
                       ("GOLD", "金")):
        s = closes.get(key)
        if not s:
            continue
        ks, sv, fx = A.align(s, usdjpy)
        # Coinbaseの日足は約350暦日ぶんしかなく、営業日に揃えると250日程度になる。
        # 260日で足切りしていたためビットコインが表から落ちていた。
        if len(ks) < 200:
            continue
        native = S.SYM_BY_KEY.get(key, (None, None, None, None, None, "USD"))[5]
        # 円建て系列とドル建て系列を作る
        jpy = [v if native == "JPY" else v * f for v, f in zip(sv, fx)]
        usd = [v / f if native == "JPY" else v for v, f in zip(sv, fx)]
        y0 = next((i for i, k in enumerate(ks) if k >= f"{ks[-1][:4]}-01-01"), 0)
        # 1年前の位置。データが252営業日に満たない銘柄では先頭を使う
        i1 = -252 if len(ks) >= 252 else 0
        out.append({"key": key, "name": label,
                    "ytd_jpy": rnd((jpy[-1] / jpy[y0] - 1) * 100, 2),
                    "ytd_usd": rnd((usd[-1] / usd[y0] - 1) * 100, 2),
                    "y1_days": len(ks) - (len(ks) + i1 if i1 < 0 else 0) if i1 < 0 else len(ks),
                    "y1_jpy": rnd((jpy[-1] / jpy[i1] - 1) * 100, 2),
                    "y1_usd": rnd((usd[-1] / usd[i1] - 1) * 100, 2)})
    return out


# ==========================================================================
# 組み立て
# ==========================================================================
def seed_history(url: str) -> None:
    """前回の履歴を、公開済みサイトから拾ってくる。

    GitHub Actions は毎回まっさらな環境で動くので、何もしないと毎回43銘柄を
    取り直すことになる。公開中の history.json を種にすれば、それを避けられる。
    リポジトリにデータをコミットしないので、履歴が膨らまない利点もある。
    """
    if os.path.exists(HISTORY):
        return
    S.log(f"前回の履歴を取得します: {url}")
    r = S.http(url)
    if r is None or r[0] != 200:
        S.log("  取得できませんでした。履歴を最初から作ります。")
        return
    try:
        obj = json.loads(r[1])
        if not obj.get("closes"):
            raise ValueError("closesが無い")
        write_json(HISTORY, obj)
        S.log(f"  {len(obj['closes'])}銘柄ぶんを再利用します"
              f"（生成 {obj.get('generated_at', '不明')}）")
    except Exception as e:  # noqa: BLE001
        S.log(f"  中身が壊れていました: {type(e).__name__}。履歴を作り直します。")


def decide_mode(prev_hist: dict | None, max_age_min: int = 60) -> str:
    """履歴が古ければ full、新しければ live。

    日足は1日1回しか変わらないが、途中で日付をまたぐので1時間ごとに取り直す。
    15分ごとの更新は現値だけで足りる。
    """
    if not prev_hist or not prev_hist.get("generated_at"):
        return "full"
    try:
        t = datetime.fromisoformat(prev_hist["generated_at"])
        age = (datetime.now(timezone.utc) - t).total_seconds() / 60
    except Exception:  # noqa: BLE001
        return "full"
    return "live" if age < max_age_min else "full"


def run(mode: str) -> int:
    os.makedirs(DATA_DIR, exist_ok=True)
    prev_hist = read_json(HISTORY)
    if mode == "auto":
        mode = decide_mode(prev_hist)
        S.log(f"履歴の鮮度から判断して {mode} モードで実行します。")
    prev_latest = read_json(LATEST)
    t0 = time.time()

    if mode == "full" or prev_hist is None:
        if mode == "live":
            S.log("履歴キャッシュが無いため、履歴も取得します。")
        S.log("履歴を取得します（43銘柄＋暗号資産3種）...")
        closes, ohlc, health = fetch_history(prev_hist)
        # 四本値は history.json に同梱する。app.js もそこから読んでいる。
        write_json(HISTORY, {"generated_at": datetime.now(timezone.utc).isoformat(),
                             "closes": closes, "ohlc": ohlc})
    else:
        closes = prev_hist["closes"]
        health = [{"key": k, "ok": True, "src": "キャッシュ", "last": max(v)}
                  for k, v in closes.items() if v]
        S.log(f"履歴キャッシュを再利用します（{len(closes)}銘柄）")

    S.log("現値を取得します...")
    live = fetch_live()

    S.log("分析を計算します...")
    an = build_analytics(closes)

    S.log("周辺データを取得します...")
    extra = {"crypto_global": S.coingecko_global(),
             "hyperliquid": S.hyperliquid(),
             "fear_greed": S.fear_greed()}

    ok = sum(1 for h in health if h["ok"])
    stale = [h for h in health if not h["ok"]]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "build_seconds": round(time.time() - t0, 1),
        "health": {"ok": ok, "total": len(health), "stale": stale,
                   "sources": {k: (v is not None) for k, v in extra.items()},
                   "log": S.LOG[-40:]},
        "live": live,
        "extra": extra,
        **an,
    }
    # 取得が全滅したときは前回の内容を残す。空のサイトを出すよりましなため。
    if ok == 0 and prev_latest:
        S.log("全銘柄で取得に失敗しました。前回の内容を維持します。")
        prev_latest.setdefault("health", {})["note"] = (
            f"{datetime.now(timezone.utc).isoformat()} の更新は全滅したため、"
            "この内容は古いものです。")
        write_json(LATEST, prev_latest)
        return 1

    S.log(f"完了: {ok}/{len(health)} 銘柄, {payload['build_seconds']}秒")
    if stale:
        S.log("古いまま残った銘柄: " + ", ".join(h["key"] for h in stale))
    # 完了行まで含めたログを画面に出す（書き出す直前に取り直す）
    payload["health"]["log"] = S.LOG[-40:]
    write_json(LATEST, payload)
    return 0


# ==========================================================================
# 架空データでの通し確認
# ==========================================================================
def mock_closes(seed: int = 42) -> dict:
    random.seed(seed)
    keys = [s[0] for s in S.SYMBOLS] + ["BTC", "ETH", "SOL"]
    start = datetime(2024, 1, 1)
    dates = []
    d = start
    while len(dates) < 620:
        if d.weekday() < 5:
            dates.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    common = [random.gauss(0, 1) for _ in dates]     # 市場全体に効く要因
    closes = {}
    for k in keys:
        spec = S.SYM_BY_KEY.get(k)
        unit = spec[4] if spec else "price"
        beta = random.uniform(-1, 1.4)
        vol = 0.02 if unit != "rate" else 0.03
        lvl = 4.5 if unit == "rate" else random.uniform(50, 40000)
        out = {}
        for i, dt in enumerate(dates):
            shock = beta * common[i] + random.gauss(0, 1)
            lvl = lvl + shock * vol if unit == "rate" else lvl * (1 + shock * vol)
            out[dt] = max(0.01, lvl)
        closes[k] = out
    ks, c, g = A.align(closes["COPPER"], closes["GOLD"])
    closes["COPPER_GOLD"] = {k: cv / gv for k, cv, gv in zip(ks, c, g)}
    return closes


def run_mock() -> int:
    print("架空データで一通り計算します（ネット接続なし）")
    print("-" * 70)
    closes = mock_closes()
    t0 = time.time()
    an = build_analytics(closes)
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "mode": "mock",
               "health": {"ok": len(closes), "total": len(closes), "stale": [],
                          "sources": {}, "log": ["架空データ"]},
               "live": {}, "extra": {"crypto_global": None, "hyperliquid": None,
                                     "fear_greed": None}, **an}
    write_json(LATEST, payload)
    size = os.path.getsize(LATEST) / 1024
    print(f"  計算時間 {time.time()-t0:.1f}秒 / 出力 {size:.0f}KB")
    for k in ("summary", "scan", "scan_rel", "rolling_corr", "lead_lag", "calendar",
              "distribution", "matrix", "corr_breaks", "risk_series", "fx_adjusted"):
        v = an.get(k)
        n = len(v) if isinstance(v, (list, dict)) else "-"
        print(f"  {k:<16} {n}")
    print(f"  decomp β={an['decomp']['beta'] if an.get('decomp') else None}")
    print(f"  risk  ={an['risk']['value'] if an.get('risk') else None}")
    print(f"  analog={an['analog']['n_matches'] if an.get('analog') else None} 件該当")
    print(f"\n  出力: {LATEST}")
    return 0


# ==========================================================================
# セルフテスト
# ==========================================================================
def selftest() -> int:
    print("build.py セルフテスト")
    print("-" * 70)
    fails, n = [], [0]

    def ck(name, cond, got=""):
        n[0] += 1
        print(f"  {'OK' if cond else 'NG'}  {name}" + ("" if cond else f"  {got}"))
        if not cond:
            fails.append(name)

    r1 = A.selftest()
    print()
    r2 = S.selftest()
    print()
    ck("analytics のテストが全部通る", r1 == 0)
    ck("sources のテストが全部通る", r2 == 0)

    closes = mock_closes()
    an = build_analytics(closes)
    ck("要約が46件（43銘柄＋暗号資産3）", len(an["summary"]) == 46, len(an["summary"]))
    ck("異常スキャンが出る", len(an["scan"]) > 0, len(an["scan"]))
    ck("スキャンがzの大きい順", all(abs(an["scan"][i]["z"]) >= abs(an["scan"][i+1]["z"])
       for i in range(len(an["scan"])-1)))
    ck("BTCの分解が出る", an["decomp"] is not None)
    ck("分解の合計が元の値に一致",
       an["decomp"] and abs(an["decomp"]["explained"] + an["decomp"]["idiosyncratic"]
                            - an["decomp"]["btc"]) < 0.01, an.get("decomp"))
    ck("ローリング相関が4本", len(an["rolling_corr"]) == 4)
    ck("相関が-1〜1に収まる",
       all(-1 <= v[1] <= 1 for s in an["rolling_corr"].values() for v in s))
    ck("リード・ラグが15点", all(len(v) == 15 for v in an["lead_lag"].values()))
    ck("曜日が7件・月が12件",
       all(len(c["dow"]) == 7 and len(c["month"]) == 12 for c in an["calendar"].values()))
    ck("マトリクスが正方行列",
       all(len(r) == len(an["matrix"]["keys"]) for r in an["matrix"]["values"]))
    ck("マトリクスの対角が1", all(an["matrix"]["values"][i][i] == 1.0
       for i in range(len(an["matrix"]["keys"]))))
    ck("マトリクスが対称", all(
        an["matrix"]["values"][i][j] == an["matrix"]["values"][j][i]
        for i in range(len(an["matrix"]["keys"])) for j in range(len(an["matrix"]["keys"]))))
    ck("リスク指数が-100〜100", an["risk"] and -100 <= an["risk"]["value"] <= 100,
       an.get("risk"))
    ck("リスク推移が出る", len(an["risk_series"]) > 0, len(an["risk_series"]))
    ck("類似局面に独立数が入る",
       an["analog"] is None or "n_independent" in an["analog"])
    ck("類似局面に採用した条件が記録される",
       an["analog"] is None or "level" in an["analog"]["condition"])
    ck("類似局面に信頼できるかの判定が入る",
       an["analog"] is None or isinstance(an["analog"]["condition"]["reliable"], bool))
    ck("関係の変化のスキャンが出る", len(an.get("scan_rel", [])) > 0,
       len(an.get("scan_rel", [])))
    ck("値動きスキャンと関係スキャンが別物",
       all(r["sub"] == "日次変化" for r in an["scan"])
       and all(r["sub"] == "30日相関" for r in an["scan_rel"]))
    ck("スキャンに日付が入る", all("date" in r for r in an["scan"]))
    ck("銘柄一覧にYahooコードが入る",
       all("yahoo" in r for r in an["summary"]))
    ck("相関の信頼区間が出る", an.get("corr_ci") is not None)
    ck("信頼区間が相関を挟んでいる",
       an["corr_ci"]["lo"] <= an["rolling_corr"]["SPX"][-1][1] <= an["corr_ci"]["hi"],
       an.get("corr_ci"))
    ck("分布に日付が入る", all("date" in v for v in an["distribution"].values()))
    ck("要約に外部リンク用のコードが入る", all(r.get("code") for r in an["summary"]))
    fxk = {r["key"] for r in an["fx_adjusted"]}
    ck("円建て比較にビットコインが入る", "BTC" in fxk, sorted(fxk))
    ck("通貨換算が出る", len(an["fx_adjusted"]) > 0, len(an["fx_adjusted"]))

    # 利回りは差分で扱えているか
    y = [r for r in an["summary"] if r["key"] == "US10Y"]
    ck("米10年債が利回り単位で扱われている", y and y[0]["unit"] == "rate", y)

    # モード判定
    now = datetime.now(timezone.utc)
    ck("履歴が無ければfull", decide_mode(None) == "full")
    ck("履歴が新しければlive",
       decide_mode({"generated_at": now.isoformat()}) == "live")
    ck("履歴が2時間前ならfull",
       decide_mode({"generated_at": (now - timedelta(hours=2)).isoformat()}) == "full")
    ck("生成時刻が壊れていればfull", decide_mode({"generated_at": "こわれた値"}) == "full")

    # JSONとして書き出せるか（NaNやInfが混じっていないか）
    try:
        txt = json.dumps(an, ensure_ascii=False, allow_nan=False)
        ck("JSONに書き出せる（NaN混入なし）", True)
        ck("出力サイズが2MB未満", len(txt) < 2_000_000, f"{len(txt)/1024:.0f}KB")
    except ValueError as e:
        ck("JSONに書き出せる（NaN混入なし）", False, str(e))

    print("-" * 70)
    print(f"build.py 単体: {n[0]-len(fails)}/{n[0]} 合格")
    if fails:
        print("失敗:", ", ".join(fails))
    return 1 if fails or r1 or r2 else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="履歴も取り直す")
    ap.add_argument("--live", action="store_true", help="現値だけ更新")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--mock", action="store_true", help="架空データで通し確認")
    ap.add_argument("--auto", action="store_true",
                    help="履歴が古ければfull、新しければliveを自動で選ぶ")
    ap.add_argument("--seed-url", default=None,
                    help="公開済みサイトのhistory.jsonのURL。初回の取得量を減らす")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.mock:
        return run_mock()
    if a.seed_url:
        os.makedirs(DATA_DIR, exist_ok=True)
        seed_history(a.seed_url)
    if a.auto:
        return run("auto")
    return run("full" if a.full or not a.live else "live")


if __name__ == "__main__":
    raise SystemExit(main())
