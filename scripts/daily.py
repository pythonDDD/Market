#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily.py — 毎朝9時（日本時間）に、その日の市況を自分のLINEへ1通だけ送る

やっていることは turtle_v2 の tbot/notify.py と同じです。
LINE Messaging API の push に、自分のユーザーID宛で1通投げるだけ。
違うのは、文字だけでなく「潮目の一日をまとめた画像を1枚」添えることです。

  python daily.py --test         いますぐ1通だけテスト送信する（設定の確認用）
  python daily.py --tick         いまの時刻で「描く／送る／何もしない」を決める（本番）
  python daily.py --render       画像とメッセージを組み立て、docs/data に置く
  python daily.py --send         組み立て済みのものを送る（Pages配信の *後* に実行）
  python daily.py --dry-run      組み立てて中身を表示する。送らない
  python daily.py --carry        公開中の保存物を docs/data に引き継ぐ
  python daily.py --mock         架空データで画像だけ作る（ネット不要）
  python daily.py --flex         Flexの中身だけを出す（LINEのシミュレータ貼り付け用）
  python daily.py --selftest     ネット無しで検証

必要な環境変数（送信するときだけ）:
  LINE_TOKEN      Messaging API のチャネルアクセストークン（長期）
  LINE_USER_ID    送信先。自分のユーザーID（U で始まる33文字）
  turtle_v2 と同じ名前にしてあります。
  古い名前 LINE_CHANNEL_TOKEN / LINE_TO も受け付けます。
  どちらも未設定なら、送信せずログに出して正常終了します。

なぜ2回に分けるのか（--tick の考え方）:
  LINEの画像メッセージは「公開済みのHTTPSのURL」しか受け付けません。
  画像がPagesに載る前に送っても、相手には出ません。
  そこで15分ごとの update.yml に相乗りし、
    8時40分以降の実行 → 画像を描いて docs/data に置く（その回の配信で公開される）
    9時00分以降の実行 → 公開されたURLを確かめてから送る
  という2段構えにしています。専用のワークフローを別に立てると、
  「送った」という記録を配信物に残せず、翌日以降の重複判定が効きません。

  画像の公開を確かめられないときは、何度か様子を見たうえで、
  最後は画像を諦めて文字だけ送ります（黙って落とさない）。

  したがって到着はおおむね9時00分〜9時20分です。GitHubの定期実行は
  混雑時に遅れるため、9時ちょうどは保証できません。

通数について（実測ではなく公式ドキュメントの記載）:
  1回のリクエストに複数の吹き出しを入れても、通数は「送信対象の人数」で数える。
  つまり Flex + 画像 の2吹き出しでも 1通。無料枠は月200通。
  https://developers.line.biz/ja/docs/messaging-api/pricing/
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
IMG_PREV = os.path.join(DATA, "daily_prev.png")
MSG = os.path.join(DATA, "daily_msg.json")
STATE = os.path.join(DATA, "notify_state.json")

SITE = os.environ.get("SITE_URL", "https://pythonddd.github.io/Market").rstrip("/")
JST = timezone(timedelta(hours=9))

# 配信をまたいで残したいファイル。update.yml 側でも引き継ぐ。
CARRY = ["notify_state.json", "daily.png", "daily_prev.png", "daily_msg.json"]

# 無料枠は月200通。通知（notify.py）と朝の便り（これ）で分け合う。
# 朝の便りを必ず通したいので、こちらの上限を高く取る。
MONTHLY_CAP_DAILY = 190

# 何時から描き始め、何時から送るか（日本時間・分で持つ）
RENDER_AFTER = 8 * 60 + 40      # 8:40 これ以降に描き始める
SEND_AFTER = 9 * 60             # 9:00 これ以降に送る
# これを過ぎたら、その日はもう送らない。
# 上限が無いと「9時前に一度も動かなかった日」の救済が効きすぎて、
# 夕方に「朝の便り」が飛ぶ。実際に飛んだので閉じた。
DAY_DEADLINE = 11 * 60          # 11:00
MAX_IMAGE_ATTEMPTS = 4          # 画像の公開を待つ回数。超えたら文字だけで送る

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


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def http_get(url: str, timeout: int = 20) -> tuple[int, bytes] | None:
    req = urllib.request.Request(url, headers={"User-Agent": "market-daily/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=ssl.create_default_context()) as r:
            return r.getcode(), r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:  # noqa: BLE001
        return None


def load_state() -> dict:
    """保存状態を読む。ローカルに無ければ公開中のサイトから拾う。

    GitHub Actions のランナーは毎回まっさらなので、これをしないと
    「今日はもう送った」も「今月の通数」も残らない。
    """
    st = read_json(STATE)
    if st is None:
        r = http_get(f"{SITE}/data/notify_state.json")
        if r and r[0] == 200:
            try:
                st = json.loads(r[1])
                print("公開中の保存状態を引き継ぎました。")
            except Exception:  # noqa: BLE001
                st = None
    return migrate(st or {})


def migrate(st: dict) -> dict:
    """古い形（通知の情報が最上位にあった）を、区画に分けた形へ移す。"""
    st = dict(st)
    if "alert" not in st and ("fingerprint" in st or "sent_at" in st):
        st["alert"] = {"fingerprint": st.get("fingerprint"),
                       "sent_at": st.get("sent_at"),
                       "reasons": st.get("reasons", [])}
    st.setdefault("alert", {})
    st.setdefault("daily", {})
    st.setdefault("month", "")
    st.setdefault("count", 0)
    return st


def month_count(st: dict, now: datetime) -> int:
    return st.get("count", 0) if st.get("month") == now.strftime("%Y-%m") else 0


def save_state(st: dict) -> None:
    write_json(STATE, st)


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
def credentials() -> tuple[str, str]:
    """トークンと宛先を環境変数から取る。turtle_v2 と同じ名前を優先する。"""
    token = (os.environ.get("LINE_TOKEN")
             or os.environ.get("LINE_CHANNEL_TOKEN") or "").strip()
    to = (os.environ.get("LINE_USER_ID")
          or os.environ.get("LINE_TO") or "").strip()
    return token, to


def explain_http(code: int) -> str:
    """LINEが返した番号の意味。原因が分からないと直せないので書き出す。"""
    return {401: "トークンが無効です。チャネルアクセストークンを再発行してください",
            400: "宛先IDが不正か、そのBotを友だち追加していません",
            403: "そのチャネルにMessaging APIの権限がありません",
            429: "送信数の上限に達しています（無料枠は月200通）",
            500: "LINE側の一時的な障害です"}.get(code, "")


def push(messages: list) -> tuple[bool, int, str]:
    token, to = credentials()
    if not token or not to:
        print("LINE_TOKEN / LINE_USER_ID が未設定のため、送信しません。")
        return False, 0, "未設定"
    if not to.startswith("U"):
        # LINE ID（@から始まるもの）を貼ってしまう間違いが多い
        print(f"宛先が 'U' で始まっていません（{to[:3]}…）。"
              "LINE ID(@xxxx)ではなく、ユーザーIDが必要です。")
        return False, 0, "宛先が不正"
    body = json.dumps({"to": to, "messages": messages}).encode()
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=25,
                                    context=ssl.create_default_context()) as r:
            print(f"送信しました（HTTP {r.getcode()}）")
            return True, r.getcode(), ""
    except urllib.error.HTTPError as e:
        # 応答本文にトークンは含まれない。原因が分からないと直せないので出す。
        msg = e.read().decode("utf-8", "replace")[:400]
        print(f"送信失敗 HTTP {e.code}: {msg}")
        hint = explain_http(e.code)
        if hint:
            print(f"  → {hint}")
        return False, e.code, msg
    except Exception as e:  # noqa: BLE001
        print(f"送信失敗: {type(e).__name__}: {e}")
        return False, 0, str(e)


def verify_url(url: str, expect_bytes: int, tries: int = 8, wait: int = 15) -> bool:
    """画像が本当に公開されているかを確かめる。中身の大きさまで一致を見る。

    URLが見えているだけでは足りない。CDNが古い画像を返している場合があるため。
    """
    for i in range(tries):
        r = http_get(url, timeout=25)
        if r and r[0] == 200:
            got = len(r[1])
            if abs(got - expect_bytes) <= 8:
                print(f"画像の公開を確認しました（{got:,}バイト・{i+1}回目）")
                return True
            print(f"  {i+1}回目: 大きさが違います（公開 {got:,} / 手元 "
                  f"{expect_bytes:,}）。CDNの入れ替わり待ちとみて再試行します。")
        else:
            code = r[0] if r else "接続不可"
            print(f"  {i+1}回目: まだ見えません（{code}）")
        if i < tries - 1:
            time.sleep(wait)
    return False


# ==========================================================================
# 各コマンド
# ==========================================================================
def cmd_render(stamp: str | None = None) -> int:
    d = read_json(LATEST)
    if d is None:
        print(f"{LATEST} がありません。先に build.py を実行してください。")
        return 1
    now = datetime.now(JST)
    cv = render(d)
    n1 = cv.save(IMG)
    n2 = cv.scaled(3).save(IMG_PREV)
    stamp = stamp or now.strftime("%Y%m%d%H%M")
    url = f"{SITE}/data/daily.png?v={stamp}"
    prev = f"{SITE}/data/daily_prev.png?v={stamp}"
    msg = {"stamp": stamp, "date_jst": now.strftime("%Y-%m-%d"),
           "image_url": url, "preview_url": prev,
           "image_bytes": n1, "preview_bytes": n2,
           "flex": build_flex(d, now, True),
           "flex_no_image": build_flex(d, now, False),
           "text": build_text(d, now, True),
           "text_no_image": build_text(d, now, False)}
    write_json(MSG, msg)
    print(f"画像 {n1/1024:.0f}KB / プレビュー {n2/1024:.0f}KB を書き出しました。")
    print(f"  {IMG}\n  {IMG_PREV}\n  {MSG}")
    if n1 > 10 * 1024 * 1024 or n2 > 1024 * 1024:
        print("警告: LINEの上限（本体10MB・プレビュー1MB）を超えています。")
        return 1
    return 0


def decide(state: dict, now: datetime) -> tuple[str, str]:
    """いまの時刻と記録から、この回にやることを決める。

    純粋な関数にしてある。時刻をずらした検証をセルフテストで行うため。
    戻り値は ("render" | "send" | "idle", 理由)。
    """
    today = now.strftime("%Y-%m-%d")
    dd = state.get("daily") or {}
    minutes = now.hour * 60 + now.minute
    if dd.get("sent_date") == today:
        return "idle", f"{today} はすでに送信済みです"
    if minutes >= DAY_DEADLINE:
        # 朝の便りは朝でなければ意味がない。遅れた日は翌朝に送る。
        return "idle", (f"今日の配信時間帯（{RENDER_AFTER//60}:{RENDER_AFTER%60:02d}"
                        f"〜{DAY_DEADLINE//60}:{DAY_DEADLINE%60:02d}）を過ぎました")
    if dd.get("render_date") != today:
        if minutes >= RENDER_AFTER:
            return "render", "今日ぶんの画像がまだありません"
        return "idle", (f"描画は {RENDER_AFTER//60}:{RENDER_AFTER%60:02d} 以降です"
                        f"（いま {now:%H:%M} JST）")
    if minutes >= SEND_AFTER:
        return "send", "画像は用意できています"
    return "idle", (f"画像は用意済み。送信は {SEND_AFTER//60}:{SEND_AFTER%60:02d} 以降です"
                    f"（いま {now:%H:%M} JST）")


def cmd_tick(dry: bool = False) -> int:
    """15分ごとの実行から呼ばれる本番の入口。

    どの枝を通っても、最後に必ず保存状態を書き出す。
    書き出さないと docs/data に残らず、次の配信で公開中のものごと消える。
    """
    now = datetime.now(JST)
    st = load_state()
    what, why = decide(st, now)
    if what == "send" and not os.path.exists(MSG):
        # 引き継ぎに失敗すると起こりうる。黙って送らないのではなく描き直す。
        print("組み立て済みのメッセージが見当たりません。描き直します。")
        what, why = "render", "引き継ぎに失敗したため作り直します"
    print(f"朝の便り: {what}（{why}）")

    if what == "render":
        if cmd_render() != 0:
            save_state(st)
            return 1
        msg = read_json(MSG) or {}
        st["daily"] = {**(st.get("daily") or {}),
                       "render_date": now.strftime("%Y-%m-%d"),
                       "rendered_at": now.isoformat(),
                       "stamp": msg.get("stamp"), "attempts": 0}
        save_state(st)
        print("次回の実行（配信後）に送ります。")
        return 0

    if what == "send":
        rc = cmd_send(dry=dry, state=st, now=now)
        save_state(st)      # どの枝でも記録を配信物に残す
        return rc

    save_state(st)
    return 0


def cmd_send(dry: bool = False, force: bool = False, skip_verify: bool = False,
             state: dict | None = None, now: datetime | None = None) -> int:
    msg = read_json(MSG)
    if msg is None:
        print(f"{MSG} がありません。先に --render を実行してください。")
        return 1
    now = now or datetime.now(JST)
    today = now.strftime("%Y-%m-%d")
    st = state if state is not None else load_state()
    dd = st.get("daily") or {}

    if not force and dd.get("sent_date") == today:
        print(f"{today} はすでに送信済みです。何もしません。")
        save_state(st)
        return 0
    used = month_count(st, now)
    if not force and used >= MONTHLY_CAP_DAILY:
        print(f"今月すでに {used} 通送っており、上限 {MONTHLY_CAP_DAILY} に達しています。")
        save_state(st)
        return 0

    attempts = int(dd.get("attempts") or 0) + 1
    image_ok = False
    if skip_verify:
        image_ok = True
    elif os.path.exists(IMG) or msg.get("image_bytes"):
        image_ok = verify_url(msg["image_url"], msg.get("image_bytes", -1),
                              tries=2, wait=10)
        if image_ok and msg.get("preview_bytes"):
            image_ok = verify_url(msg["preview_url"], msg["preview_bytes"],
                                  tries=2, wait=10)
    if not image_ok:
        if attempts < MAX_IMAGE_ATTEMPTS and not force and not dry:
            # まだ待つ。回数を記録して、次の実行でもう一度見にいく。
            st["daily"] = {**dd, "attempts": attempts}
            save_state(st)
            print(f"画像の公開を確認できませんでした（{attempts}/{MAX_IMAGE_ATTEMPTS}回目）。"
                  "次の実行でもう一度確かめます。")
            return 0
        print("画像の公開を確認できませんでした。文字だけで送ります。")

    messages = [msg["flex"] if image_ok else msg["flex_no_image"]]
    fallback = [{"type": "text",
                 "text": (msg["text"] if image_ok else msg["text_no_image"])[:4900]}]
    if image_ok:
        messages.append({"type": "image",
                         "originalContentUrl": msg["image_url"],
                         "previewImageUrl": msg["preview_url"]})

    if dry:
        print("--- 送る内容（--dry-run のため送信しません）---")
        print(fallback[0]["text"])
        print(f"\n画像: {'あり ' + msg['image_url'] if image_ok else 'なし'}")
        print(f"吹き出し {len(messages)}個（通数は1通）／今月 {used} 通")
        return 0

    ok, code, _err = push(messages)
    if not ok and code in (400, 500):
        # Flexの作りが原因なら、文字だけなら通る。朝の便りを落とさないための保険。
        print("Flexが弾かれたため、文字だけで送り直します。")
        ok, code, _err = push(fallback)
    if not ok:
        st["daily"] = {**dd, "attempts": attempts}
        save_state(st)
        return 0 if code == 0 else 1

    st["month"] = now.strftime("%Y-%m")
    st["count"] = used + 1
    st["daily"] = {**dd, "sent_date": today, "sent_at": now.isoformat(),
                   "with_image": image_ok, "attempts": attempts}
    save_state(st)
    print(f"今月の通数: {st['count']} / {MONTHLY_CAP_DAILY}")
    return 0


def cmd_test() -> int:
    """いますぐ1通だけ送って、設定が正しいかを確かめる。

    turtle_v2 の test_line() と同じ役目です。ここが通らなければ
    朝の便りも絶対に届きません。まずこれを通してください。
    """
    token, to = credentials()
    print("LINEの設定を確認します。")
    print(f"  トークン : {'あり（' + str(len(token)) + '文字）' if token else 'なし'}")
    print(f"  宛先     : {to[:5] + '…' + to[-4:] if len(to) > 10 else (to or 'なし')}")
    if not token or not to:
        print("環境変数 LINE_TOKEN / LINE_USER_ID を設定してください。")
        return 1
    now = datetime.now(JST)
    ok, code, _ = push([{"type": "text", "text":
                         f"【潮目】テスト送信です。\n"
                         f"これが届けば設定は正しいです。\n"
                         f"{now.year}年{now.month}月{now.day}日 "
                         f"{now.hour:02d}:{now.minute:02d} JST"}])
    if ok:
        print("スマホを確認してください。")
        return 0
    return 1


def cmd_carry() -> int:
    """公開中の保存物を docs/data に引き継ぐ。

    このリポジトリは docs をまるごと配信し直すので、
    引き継がないと前回の画像も保存状態も配信のたびに消える。
    """
    os.makedirs(DATA, exist_ok=True)
    got = []
    for name in CARRY:
        path = os.path.join(DATA, name)
        if os.path.exists(path):
            continue
        r = http_get(f"{SITE}/data/{name}")
        if r and r[0] == 200 and r[1]:
            with open(path, "wb") as f:
                f.write(r[1])
            got.append(f"{name}({len(r[1])/1024:.0f}KB)")
    print("引き継ぎました: " + (", ".join(got) if got else "なし（手元が優先）"))
    return 0


def cmd_mock() -> int:
    """架空データで画像だけ作る。実測値ではない。"""
    sys.path.insert(0, HERE)
    import build as B  # noqa: E402
    closes = B.mock_closes()
    an = B.build_analytics(closes)
    d = {"generated_at": datetime.now(timezone.utc).isoformat(), "mode": "mock",
         "health": {"ok": len(closes), "total": len(closes), "stale": []},
         "extra": {"fear_greed": {"value": 41, "label": "Fear"},
                   "crypto_global": {"btc_dominance": 57.3},
                   "hyperliquid": {"BTC": {"funding_annual_pct": 9.6}}},
         **an}
    cv = render(d)
    n = cv.save("/tmp/daily_mock.png")
    cv.scaled(3).save("/tmp/daily_mock_prev.png")
    now = datetime.now(JST)
    print(build_text(d, now))
    print(f"\n架空データの見本: /tmp/daily_mock.png（{n/1024:.0f}KB）")
    print("※この画像の数値はすべて架空です。実測ではありません。")
    return 0


# ==========================================================================
def selftest() -> int:
    print("daily.py セルフテスト")
    print("-" * 70)
    fails, n = [], [0]

    def ck(name, cond, got=""):
        n[0] += 1
        print(f"  {'OK' if cond else 'NG'}  {name}" + ("" if cond else f"  {got}"))
        if not cond:
            fails.append(name)

    now = datetime(2026, 9, 3, 9, 0, tzinfo=JST)
    d = {
        "generated_at": "2026-09-03T00:02:00+00:00", "mode": "mock",
        "risk": {"value": -63.2, "components": []},
        "risk_series": [[f"2026-0{1+i//28}-{1+i%28:02d}", (i % 40) - 20]
                        for i in range(120)],
        "summary": [
            {"key": "BTC", "name": "ビットコイン", "unit": "price", "last": 64210.5,
             "chg1d": -3.42, "z1d": -2.8, "spark": [60000 + i * 40 for i in range(30)]},
            {"key": "US10Y", "name": "米10年債", "unit": "rate", "last": 4.51,
             "chg1d": 0.09, "z1d": 2.1, "spark": [4.4] * 30},
            {"key": "GOLD", "name": "金", "unit": "price", "last": 2410.0,
             "chg1d": 0.8, "z1d": 1.2, "spark": [2400] * 30}],
        "rolling_corr": {"SPX": [["2026-08-01", 0.31], ["2026-09-02", -0.12]],
                         "GOLD": [["2026-08-01", 0.1], ["2026-09-02", 0.22]]},
        "decomp": {"beta": 1.23},
        "health": {"ok": 45, "total": 46, "stale": [{"key": "TOPX"}]},
        "extra": {"fear_greed": {"value": 33, "label": "Fear"},
                  "crypto_global": {"btc_dominance": 58.1},
                  "hyperliquid": {"BTC": {"funding_annual_pct": -4.2}}},
    }

    # ---- 画像 ----
    cv = render(d)
    ck("画像の寸法が指定どおり", (cv.w, cv.h) == (W, H), (cv.w, cv.h))
    png = cv.png_bytes()
    ck("PNGとして書き出せる", png[:8] == b"\x89PNG\r\n\x1a\n")
    ck("本体がLINEの上限(10MB)内", len(png) < 10 * 1024 * 1024, len(png))
    prev = cv.scaled(3).png_bytes()
    ck("プレビューがLINEの上限(1MB)内", len(prev) < 1024 * 1024, len(prev))
    ck("プレビューの方が小さい", len(prev) < len(png))
    ck("データが空でも描画で落ちない", render({}).w == W)
    ck("架空データには印を出す",
       render({**d, "mode": "mock"}).png_bytes()
       != render({**d, "mode": "live"}).png_bytes())

    # ---- 文字の版 ----
    t = build_text(d, now)
    ck("見出しに日付が入る", "9月3日" in t and "（木）" in t, t[:40])
    ck("見出しを「きのう」と書かない", "きのう" not in t, t)
    ck("リスク選好が入る", "-63" in t and "リスクオフ" in t, t[:120])
    ck("ビットコインの値が入る", "64,210" in t, t[:200])
    ck("利回りはptで書く", "+0.09pt" in t, [l for l in t.split("\n") if "米10年" in l])
    ck("価格は%で書く", "-3.42%" in t)
    ck("取得できなかった銘柄を明記", "TOPX" in t)
    ck("末尾にURL", t.strip().endswith("/"), t[-40:])
    ck("画像なしのときは断りを入れる", "間に合わなかった" in build_text(d, now, False))
    ck("4900字に収まる", len(t) < 4900, len(t))

    # ---- Flex ----
    f = build_flex(d, now, True)
    ck("Flexの型", f["type"] == "flex" and f["contents"]["type"] == "bubble")
    ck("代替テキストがある", 0 < len(f["altText"]) <= 400, f["altText"])
    ck("図をheroに入れない（二重表示の防止）", "hero" not in f["contents"])
    ck("図が無いときは断りを入れる",
       "間に合わなかった" in json.dumps(build_flex(d, now, False), ensure_ascii=False))
    hdr = f["contents"]["header"]["contents"][1]["text"]
    ck("見出しの日付（Windowsでも落ちない書き方）",
       hdr == "2026年9月3日（木）09:00 JST", hdr)
    js = json.dumps(f, ensure_ascii=False)
    ck("Flex全体が30KB未満", len(js.encode()) < 30000, len(js.encode()))
    ck("空のboxを作っていない", '"contents": []' not in js and '"contents":[]' not in js)

    def walk(o, path="$"):
        bad = []
        if isinstance(o, dict):
            if o.get("type") == "box":
                c = o.get("contents")
                if not isinstance(c, list) or not c:
                    bad.append(path + ".contents")
                if o.get("layout") not in ("vertical", "horizontal", "baseline"):
                    bad.append(path + ".layout")
            if o.get("type") == "text" and not str(o.get("text", "")):
                bad.append(path + ".text")
            for k, v in o.items():
                bad += walk(v, f"{path}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                bad += walk(v, f"{path}[{i}]")
        return bad

    ck("boxに中身とlayoutが必ずある", walk(f) == [], walk(f)[:3])
    sizes = {"xxs", "xs", "sm", "md", "lg", "xl", "xxl", "3xl", "4xl", "5xl"}

    def walk_size(o):
        bad = []
        if isinstance(o, dict):
            if o.get("type") == "text" and o.get("size") and o["size"] not in sizes:
                bad.append(o["size"])
            for v in o.values():
                bad += walk_size(v)
        elif isinstance(o, list):
            for v in o:
                bad += walk_size(v)
        return bad

    ck("文字の大きさが規定の語だけ", walk_size(f) == [], walk_size(f))

    # ---- 保存状態 ----
    old = {"fingerprint": "abc", "sent_at": "2026-09-01T00:00:00+00:00",
           "month": "2026-09", "count": 4}
    m = migrate(old)
    ck("古い形から通知の区画へ移せる", m["alert"]["fingerprint"] == "abc")
    ck("移しても通数は残る", m["count"] == 4 and m["month"] == "2026-09")
    ck("朝の便りの区画ができる", m["daily"] == {})
    ck("今月の通数を数える", month_count({"month": "2026-09", "count": 7},
                                    datetime(2026, 9, 3, tzinfo=JST)) == 7)
    ck("月が違えば0から", month_count({"month": "2026-08", "count": 7},
                                 datetime(2026, 9, 3, tzinfo=JST)) == 0)

    # ---- いつ何をするかの判断 ----
    def at(h, m, st=None):
        return decide(st or {}, datetime(2026, 9, 3, h, m, tzinfo=JST))

    ck("早朝はまだ何もしない", at(6, 0)[0] == "idle", at(6, 0))
    ck("8時39分はまだ描かない", at(8, 39)[0] == "idle", at(8, 39))
    ck("8時40分から描く", at(8, 40)[0] == "render", at(8, 40))
    rendered = {"daily": {"render_date": "2026-09-03", "attempts": 0}}
    ck("描いた直後はまだ送らない", at(8, 50, rendered)[0] == "idle", at(8, 50, rendered))
    ck("9時から送る", at(9, 0, rendered)[0] == "send", at(9, 0, rendered))
    ck("少し遅れても送る", at(10, 30, rendered)[0] == "send")
    sent = {"daily": {"render_date": "2026-09-03", "sent_date": "2026-09-03"}}
    ck("同じ日に二度送らない", at(9, 30, sent)[0] == "idle", at(9, 30, sent))
    ck("翌日はまた描く",
       decide(sent, datetime(2026, 9, 4, 8, 45, tzinfo=JST))[0] == "render")
    old_r = {"daily": {"render_date": "2026-09-02"}}
    ck("前日の画像は使い回さない", at(9, 30, old_r)[0] == "render", at(9, 30, old_r))
    ck("9時前に起動しなかった日も、時間内なら描ける",
       at(10, 0)[0] == "render", at(10, 0))
    ck("夕方には描かない", at(18, 6)[0] == "idle", at(18, 6))
    ck("夕方には送らない", at(18, 6, rendered)[0] == "idle", at(18, 6, rendered))
    ck("11時ちょうどで打ち切る", at(11, 0, rendered)[0] == "idle", at(11, 0, rendered))
    ck("10時59分ならまだ送る", at(10, 59, rendered)[0] == "send")
    ck("遅れた日も翌朝には描く",
       decide({"daily": {"render_date": "2026-09-03"}},
              datetime(2026, 9, 4, 8, 45, tzinfo=JST))[0] == "render")
    ck("待つ回数に上限がある", MAX_IMAGE_ATTEMPTS >= 2)
    ck("描く時刻が送る時刻より前", RENDER_AFTER < SEND_AFTER)

    # ---- 設定 ----
    ck("日本時間で判断している", JST.utcoffset(None) == timedelta(hours=9))
    ck("引き継ぐファイルに画像と状態が入っている",
       "daily.png" in CARRY and "notify_state.json" in CARRY)
    ck("上限が無料枠(200通)を超えていない", MONTHLY_CAP_DAILY < 200)
    ck("公開URLがHTTPS", SITE.startswith("https://"), SITE)

    for k in ("LINE_TOKEN", "LINE_CHANNEL_TOKEN", "LINE_USER_ID", "LINE_TO"):
        os.environ.pop(k, None)
    ck("未設定なら送信しない", push([{"type": "text", "text": "x"}])[0] is False)
    os.environ["LINE_TOKEN"] = "dummy"
    os.environ["LINE_USER_ID"] = "@abcdef"
    ck("宛先がLINE IDなら送らずに教える",
       push([{"type": "text", "text": "x"}])[2] == "宛先が不正")
    os.environ["LINE_CHANNEL_TOKEN"] = "old"
    os.environ.pop("LINE_TOKEN")
    ck("古い名前でも読める", credentials()[0] == "old")
    for k in ("LINE_TOKEN", "LINE_CHANNEL_TOKEN", "LINE_USER_ID", "LINE_TO"):
        os.environ.pop(k, None)
    ck("401の意味を説明できる", "再発行" in explain_http(401))
    ck("400の意味を説明できる", "友だち" in explain_http(400))

    src = open(__file__, encoding="utf-8").read().split("def selftest()")[0]
    import re as _re
    leaks = [l for l in src.splitlines()
             if _re.search(r"print\(.*\{\s*token", l) or _re.search(r"print\(\s*token\b", l)]
    ck("トークンをログに出していない", not leaks, leaks[:1])

    print("-" * 70)
    print(f"結果: {n[0] - len(fails)}/{n[0]} 合格")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true",
                    help="いますぐ1通だけテスト送信する")
    ap.add_argument("--tick", action="store_true",
                    help="時刻を見て描画/送信/待機を自動で決める（本番の入口）")
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--carry", action="store_true")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--flex", action="store_true",
                    help="Flexの中身をJSONで出す。LINEのシミュレータで見た目を確認できる")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--force", action="store_true", help="送信済みでも送る")
    ap.add_argument("--skip-verify", action="store_true",
                    help="画像の公開確認を省く（手元での確認用）")
    ap.add_argument("--stamp", default=None, help="URLに付ける版番号")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if a.carry:
        return cmd_carry()
    if a.mock:
        return cmd_mock()
    if a.flex:
        d = read_json(LATEST)
        if d is None:
            print(f"{LATEST} がありません。先に build.py を実行してください。")
            return 1
        f = build_flex(d, datetime.now(JST),
                       f"{SITE}/data/daily.png?v=preview")
        print(json.dumps(f["contents"], ensure_ascii=False, indent=2))
        return 0
    if a.test:
        return cmd_test()
    if a.tick:
        return cmd_tick()
    if a.render:
        return cmd_render(a.stamp)
    if a.dry_run:
        if cmd_render(a.stamp) != 0:
            return 1
        return cmd_send(dry=True, skip_verify=True)
    if a.send:
        return cmd_send(force=a.force, skip_verify=a.skip_verify)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
