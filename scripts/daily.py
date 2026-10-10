#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""潮目の市場だより本文と既存PNGチャートの描画。
本番通知は notification.py に一本化。画像公開待ちはしません。
--render は任意の手動PNG出力、--selftest は描画検証です。
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chart as C  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "docs", "data")
LATEST = os.path.join(DATA, "latest.json")
IMG = os.path.join(DATA, "daily.png")
SITE = os.environ.get("SITE_URL", "https://pythonddd.github.io/Market").rstrip("/")
JST = timezone(timedelta(hours=9))

PAL = {k: C.rgb(v) for k, v in {
    "bg": "#0E161C", "card": "#18242D", "card2": "#1E2E39", "band": "#16272E",
    "hair": "#293B48", "grid": "#22323D", "axis": "#3B5262",
    "ink": "#E4EFF2", "muted": "#8CA3B2", "deep": "#B0F1F0", "sky": "#84D2F5",
    "cyan": "#00B4D8", "alert": "#FFEB3B", "up": "#3FD68C", "down": "#FF6B6B",
}.items()}

W, H = 900, 1240
DOW_EN = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
DOW_JA = ["月", "火", "水", "木", "金", "土", "日"]


# ==========================================================================
# 共通
# ==========================================================================
def read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def month_count(st: dict, now: datetime) -> int:
    return st.get("count", 0) if st.get("month") == now.strftime("%Y-%m") else 0


# ==========================================================================
# 画像を描く
# ==========================================================================
def _card(cv: C.Canvas, x, y, w, h, title: str, note: str = "") -> None:
    cv.round_rect(x, y, w, h, PAL["card"], 10)
    cv.text(x + 22, y + 18, title, PAL["deep"], 3)
    if note:
        cv.text(x + w - 22, y + 20, note, PAL["muted"], 2, "right")


def _series_of(pairs, n=None):
    """[[日付, 値], ...] から値だけを取り出す。"""
    vals = [p[1] for p in (pairs or []) if isinstance(p, (list, tuple)) and len(p) >= 2]
    return vals[-n:] if n else vals


def render(d: dict) -> C.Canvas:
    """latest.json から朝の1枚を描く。文字はASCIIのみ（日本語は出せない）。"""
    cv = C.Canvas(W, H, PAL["bg"])
    gen = d.get("generated_at", "")
    try:
        gu = datetime.fromisoformat(gen).astimezone(JST)
    except Exception:  # noqa: BLE001
        gu = datetime.now(JST)
    now = datetime.now(JST)

    # ---- 見出し ----
    cv.fill_rect(0, 0, W, 92, PAL["band"])
    cv.fill_rect(0, 92, W, 2, PAL["hair"])
    cv.text(30, 24, "SHIOME", PAL["deep"], 6)
    cv.text(30 + C.text_width("SHIOME", 6) + 20, 34, "MORNING BRIEF", PAL["muted"], 3)
    cv.text(W - 30, 26, now.strftime("%Y-%m-%d ") + DOW_EN[now.weekday()],
            PAL["ink"], 3, "right")
    cv.text(W - 30, 56, "DATA AS OF " + gu.strftime("%m-%d %H:%M JST"),
            PAL["muted"], 2, "right")

    y = 112

    # ---- リスク選好 ----
    risk = d.get("risk") or {}
    rv = risk.get("value")
    _card(cv, 20, y, 860, 186, "RISK APPETITE", "LAST 180 SESSIONS")
    col = PAL["up"] if (rv or 0) > 0 else PAL["down"]
    cv.text(44, y + 62, ("N/A" if rv is None else f"{rv:+.0f}"), col, 9)
    # ゲージ
    gx, gy, gw = 44, y + 148, 330
    cv.fill_rect(gx, gy, gw, 14, PAL["card2"])
    cv.fill_rect(gx + gw // 2 - 1, gy - 5, 2, 24, PAL["axis"])
    if rv is not None:
        frac = max(-1.0, min(1.0, rv / 100.0))
        half = gw / 2
        if frac >= 0:
            cv.fill_rect(gx + half, gy, half * frac, 14, col)
        else:
            cv.fill_rect(gx + half + half * frac, gy, -half * frac, 14, col)
    cv.text(gx, gy + 26, "-100 RISK OFF", PAL["muted"], 2)
    cv.text(gx + gw, gy + 26, "RISK ON +100", PAL["muted"], 2, "right")
    # 推移
    rs = _series_of(d.get("risk_series"), 180)
    if len(rs) > 5:
        p = C.Plot(cv, 470, y + 54, 340, 108, -100, 100)
        p.grid(PAL["grid"], 2, PAL["muted"], 2)
        p.zero_line(PAL["axis"])
        p.series(rs, PAL["sky"], 2)
        p.last_dot(rs, PAL["alert"], 4)
    y += 200

    # ---- BTC ----
    btc = next((r for r in (d.get("summary") or []) if r.get("key") == "BTC"), None)
    _card(cv, 20, y, 860, 252, "BITCOIN / USD",
          (f"LAST {C.fmt_num(btc['last'])}   1D {btc['chg1d']:+.2f}%"
           if btc and btc.get("chg1d") is not None else ""))
    if btc and btc.get("spark"):
        vals = [v for v in btc["spark"] if v is not None]
        lo, hi = C.nice_bounds(min(vals), max(vals))
        p = C.Plot(cv, 130, y + 62, 720, 160, lo, hi)
        p.grid(PAL["grid"], 4, PAL["muted"], 2)
        p.series(vals, PAL["deep"], 3)
        p.last_dot(vals, PAL["alert"], 5)
        cv.text(130, y + 232, f"LAST {len(vals)} SESSIONS", PAL["muted"], 2)
    else:
        cv.text(130, y + 120, "NO DATA", PAL["muted"], 3)
    y += 266

    # ---- 相関 ----
    _card(cv, 20, y, 860, 252, "BTC 30D CORRELATION", "ROLLING WINDOW = 30 SESSIONS")
    rc = d.get("rolling_corr") or {}
    order = [("SPX", PAL["deep"]), ("NDX", PAL["sky"]),
             ("GOLD", PAL["alert"]), ("DXY", PAL["up"])]
    p = C.Plot(cv, 130, y + 62, 720, 150, -1, 1)
    p.grid(PAL["grid"], 4, PAL["muted"], 2)
    p.zero_line(PAL["axis"])
    lx = 130
    for name, col in order:
        vals = _series_of(rc.get(name), 180)
        if len(vals) < 2:
            continue
        p.series(vals, col, 2)
        p.last_dot(vals, col, 3)
        cv.fill_rect(lx, y + 230, 18, 6, col)
        cv.text(lx + 26, y + 227, f"{name} {vals[-1]:+.2f}", PAL["muted"], 2)
        lx += 26 + C.text_width(f"{name} {vals[-1]:+.2f}", 2) + 34
    y += 266

    # ---- 今日の動き ----
    rows = [r for r in (d.get("summary") or []) if r.get("z1d") is not None]
    rows.sort(key=lambda r: -abs(r["z1d"]))
    rows = rows[:8]
    _card(cv, 20, y, 860, 300, "BIGGEST MOVES", "Z-SCORE OF 1D CHANGE")
    if rows:
        zmax = max(3.0, max(abs(r["z1d"]) for r in rows))
        cx, half = 560, 200
        cv.fill_rect(cx, y + 56, 1, 234, PAL["axis"])
        for i, r in enumerate(rows):
            ry = y + 62 + i * 29
            col = PAL["up"] if r["z1d"] > 0 else PAL["down"]
            bw = half * abs(r["z1d"]) / zmax
            cv.fill_rect(cx if r["z1d"] > 0 else cx - bw, ry, bw, 16, col)
            cv.text(40, ry + 2, r["key"][:10], PAL["ink"], 2)
            unit = "PT" if r.get("unit") == "rate" else "%"
            chg = r.get("chg1d")
            cv.text(300, ry + 2,
                    ("-" if chg is None else f"{chg:+.2f}") + unit,
                    PAL["muted"], 2, "right")
            cv.text(W - 40, ry + 2, f"{r['z1d']:+.1f} SD", col, 2, "right")
    y += 314

    # ---- 足元 ----
    ex = d.get("extra") or {}
    fg = (ex.get("fear_greed") or {}).get("value")
    dom = (ex.get("crypto_global") or {}).get("btc_dominance")
    hl = (ex.get("hyperliquid") or {}).get("BTC") or {}
    fund = hl.get("funding_annual_pct")
    hh = d.get("health") or {}
    bits = [f"DATA {hh.get('ok', '?')}/{hh.get('total', '?')}"]
    if fg is not None:
        bits.append(f"FEAR&GREED {fg}")
    if dom is not None:
        bits.append(f"BTC DOM {dom:.1f}%")
    if fund is not None:
        bits.append(f"FUNDING {fund:+.1f}%/YR")
    cv.text(30, H - 52, "   ".join(bits), PAL["muted"], 2)
    stale = hh.get("stale") or []
    if stale:
        cv.text(30, H - 26, f"STALE: {', '.join(s['key'] for s in stale[:6])}",
                PAL["alert"], 2)
    else:
        cv.text(30, H - 26, SITE.replace("https://", ""), PAL["muted"], 2)
    if d.get("mode") == "mock":
        cv.text(W - 30, H - 26, "FICTIONAL DATA", PAL["alert"], 3, "right")
    return cv


# ==========================================================================
# メッセージを組み立てる
# ==========================================================================
def jp_pct(v, unit="price") -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}" + ("pt" if unit == "rate" else "%")


def build_text(d: dict, now: datetime, image_ok: bool = True) -> str:
    """Flexが使えなかったときに送る、文字だけの版。"""
    L = [f"【潮目】{now.month}月{now.day}日（{DOW_JA[now.weekday()]}）朝の便り", ""]
    risk = d.get("risk") or {}
    if risk.get("value") is not None:
        v = risk["value"]
        mood = "リスクオン" if v > 20 else ("リスクオフ" if v < -20 else "中立")
        L.append(f"リスク選好 {v:+.0f}（{mood}）")
    btc = next((r for r in (d.get("summary") or []) if r.get("key") == "BTC"), None)
    if btc:
        L.append(f"ビットコイン {C.fmt_num(btc.get('last'))} USD"
                 f"（前日比 {jp_pct(btc.get('chg1d'))}）")
    corr = _series_of((d.get("rolling_corr") or {}).get("SPX"))
    if corr:
        L.append(f"S&P500との30日相関 {corr[-1]:+.2f}")
    rows = [r for r in (d.get("summary") or []) if r.get("z1d") is not None]
    rows.sort(key=lambda r: -abs(r["z1d"]))
    if rows:
        L += ["", "大きく動いたもの（日次変化）"]
        for r in rows[:5]:
            L.append(f"・{r['name']} {jp_pct(r.get('chg1d'), r.get('unit'))}"
                     f"（{r['z1d']:+.1f}σ）")
    hh = d.get("health") or {}
    stale = hh.get("stale") or []
    L += ["", f"取得 {hh.get('ok', '?')}/{hh.get('total', '?')}銘柄"]
    if stale:
        L.append("※取得できず前回値のまま: "
                 + ", ".join(s["key"] for s in stale[:6]))
    if not image_ok:
        L.append("※図は公開が間に合わなかったため省きました")
    L += ["", SITE + "/"]
    return "\n".join(L)


def _row(label: str, value: str, color: str = "#E4EFF2", bold: bool = False) -> dict:
    return {"type": "box", "layout": "baseline", "contents": [
        {"type": "text", "text": label, "size": "sm", "color": "#8CA3B2", "flex": 6},
        {"type": "text", "text": value, "size": "md" if bold else "sm",
         "color": color, "flex": 5, "align": "end",
         "weight": "bold" if bold else "regular"}]}


def build_flex(d: dict, now: datetime, image_ok: bool = True) -> dict:
    """Flexメッセージ本体。作りは控えめにしてある（凝るほど400で弾かれやすいため）。

    図はここには入れない。別の吹き出しで送る。
    以前は hero にも入れていたが、同じ図が2回並んで読みにくかった。
    別送なら、指で開いて拡大できる利点もある。
    """
    risk = d.get("risk") or {}
    rv = risk.get("value")
    mood = "—"
    if rv is not None:
        mood = "リスクオン" if rv > 20 else ("リスクオフ" if rv < -20 else "中立")
    gauge = 50 if rv is None else int(max(0, min(100, (rv + 100) / 2)))
    rcol = "#3FD68C" if (rv or 0) > 0 else "#FF6B6B"

    body: list = [
        {"type": "text", "text": "リスク選好", "size": "sm", "color": "#8CA3B2"},
        {"type": "box", "layout": "baseline", "contents": [
            {"type": "text", "text": ("—" if rv is None else f"{rv:+.0f}"),
             "size": "xxl", "weight": "bold", "color": rcol, "flex": 0},
            {"type": "text", "text": "  " + mood, "size": "sm",
             "color": "#8CA3B2", "flex": 1}]},
        {"type": "box", "layout": "horizontal", "height": "10px",
         "backgroundColor": "#1E2E39", "cornerRadius": "5px", "margin": "sm",
         "contents": [
             {"type": "box", "layout": "vertical", "width": f"{gauge}%",
              "backgroundColor": rcol, "cornerRadius": "5px",
              "contents": [{"type": "filler"}]}]},
        {"type": "separator", "margin": "lg", "color": "#293B48"},
    ]

    btc = next((r for r in (d.get("summary") or []) if r.get("key") == "BTC"), None)
    if btc:
        col = "#3FD68C" if (btc.get("chg1d") or 0) >= 0 else "#FF6B6B"
        body.append({"type": "box", "layout": "vertical", "margin": "lg", "spacing": "sm",
                     "contents": [
                         _row("ビットコイン",
                              f"{C.fmt_num(btc.get('last'))} USD", "#E4EFF2", True),
                         _row("前日比", jp_pct(btc.get("chg1d")), col)]})
    corr = _series_of((d.get("rolling_corr") or {}).get("SPX"))
    dec = d.get("decomp") or {}
    extras = []
    if corr:
        extras.append(_row("S&P500との相関", f"{corr[-1]:+.2f}"))
    if dec.get("beta") is not None:
        extras.append(_row("株式への感応度 β", f"{dec['beta']:+.2f}"))
    fg = ((d.get("extra") or {}).get("fear_greed") or {})
    if fg.get("value") is not None:
        extras.append(_row("恐怖強欲指数", f"{fg['value']}　{fg.get('label', '')}"))
    if extras:
        body.append({"type": "box", "layout": "vertical", "margin": "md",
                     "spacing": "sm", "contents": extras})

    rows = [r for r in (d.get("summary") or []) if r.get("z1d") is not None]
    rows.sort(key=lambda r: -abs(r["z1d"]))
    if rows:
        body.append({"type": "separator", "margin": "lg", "color": "#293B48"})
        body.append({"type": "text", "text": "大きく動いたもの（日次変化）", "size": "sm",
                     "color": "#8CA3B2", "margin": "lg"})
        mv = []
        for r in rows[:5]:
            col = "#3FD68C" if (r.get("chg1d") or 0) >= 0 else "#FF6B6B"
            mv.append(_row(r["name"][:12],
                           f"{jp_pct(r.get('chg1d'), r.get('unit'))}"
                           f"　{r['z1d']:+.1f}σ", col))
        body.append({"type": "box", "layout": "vertical", "margin": "sm",
                     "spacing": "xs", "contents": mv})

    hh = d.get("health") or {}
    stale = hh.get("stale") or []
    body.append({"type": "separator", "margin": "lg", "color": "#293B48"})
    body.append({"type": "text", "margin": "md", "size": "xs", "wrap": True,
                 "color": ("#FFEB3B" if stale else "#8CA3B2"),
                 "text": (f"取得 {hh.get('ok', '?')}/{hh.get('total', '?')}銘柄"
                          + (("　前回値のまま: "
                              + ", ".join(s["key"] for s in stale[:6]))
                             if stale else ""))})

    bubble: dict = {
        "type": "bubble", "size": "mega",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": "#16272E",
                   "paddingAll": "16px", "contents": [
                       {"type": "text", "text": "潮目 — 朝の便り", "weight": "bold",
                        "size": "lg", "color": "#B0F1F0"},
                       {"type": "text", "size": "xs", "color": "#8CA3B2",
                        # %-m / %-d は Windows の strftime で落ちるので使わない
                        "text": (f"{now.year}年{now.month}月{now.day}日"
                                 f"（{DOW_JA[now.weekday()]}）"
                                 f"{now.hour:02d}:{now.minute:02d} JST")}]},
        "body": {"type": "box", "layout": "vertical", "backgroundColor": "#18242D",
                 "paddingAll": "16px", "contents": body},
        "footer": {"type": "box", "layout": "vertical", "backgroundColor": "#18242D",
                   "paddingAll": "12px", "contents": [
                       {"type": "button", "style": "primary", "height": "sm",
                        "color": "#00B4D8",
                        "action": {"type": "uri", "label": "潮目を開く",
                                   "uri": SITE + "/"}}]},
        "styles": {"header": {"backgroundColor": "#16272E"},
                   "body": {"backgroundColor": "#18242D"},
                   "footer": {"backgroundColor": "#18242D"}},
    }
    if not image_ok:
        body.append({"type": "text", "margin": "sm", "size": "xs", "wrap": True,
                     "color": "#FFEB3B",
                     "text": "図は公開が間に合わなかったため省きました"})
    alt = (f"【潮目】{now.month}月{now.day}日の朝の便り"
           + (f"／リスク選好 {rv:+.0f}" if rv is not None else ""))
    return {"type": "flex", "altText": alt[:400], "contents": bubble}


# ==========================================================================
# 送信
# ==========================================================================
def main():
    if "--selftest" in sys.argv:
        assert render({}).png_bytes().startswith(b"\x89PNG")
        assert len(build_text({}, datetime.now(JST))) < 4900
        print("daily: renderer / message OK")
        return 0
    if "--render" in sys.argv:
        data = read_json(LATEST)
        if not data:
            print("データがありません。build.py を実行してください。")
            return 1
        os.makedirs(DATA, exist_ok=True)
        render(data).save(IMG)
        print("出力: " + IMG)
        return 0
    import notification
    # Retain compatible invocation names for existing local tasks.
    sys.argv = [sys.argv[0]] + [a for a in sys.argv[1:] if a != "--tick"]
    return notification.main()


if __name__ == "__main__":
    raise SystemExit(main())
