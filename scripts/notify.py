#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notify.py — latest.json を見て、条件に当たったときだけ通知を送る

  python notify.py --dry-run     送信せず、何が飛ぶかだけ表示する
  python notify.py --selftest    ネット無しで判定ロジックを検証
  python notify.py               実際に送る（環境変数が必要）

必要な環境変数（GitHub Actions では Secrets に入れる）:
  LINE_TOKEN      LINE Messaging API のチャネルアクセストークン
  LINE_USER_ID    送信先。自分のユーザーID（turtle_v2 と同じ名前）
  古い名前 LINE_CHANNEL_TOKEN / LINE_TO も受け付ける。
  どちらも未設定なら、送信はせずログに出すだけで正常終了する。

LINE について正直に:
  以前広く使われていた LINE Notify は 2025年3月末で終了したと記憶していますが、
  この点は必ずご自身で確認してください。本スクリプトは後継の Messaging API を
  使う形にしています。既に別の通知手段をお持ちなら、send() を差し替えるだけで
  そちらに流せます。

連投しない工夫:
  同じ内容を15分ごとに送りつけないよう、前回送った内容の指紋を
  docs/data/notify_state.json に残し、変化が無ければ黙ります。

保存状態について（2026-09-03に直した点）:
  以前は「送るものが無い回」に保存状態を書き出していませんでした。
  このリポジトリは docs をまるごと配信し直すため、書き出さない回の配信で
  公開中の notify_state.json が消え、連投防止も月間上限も実質効いていませんでした。
  いまは、どの枝を通っても最後に必ず書き出します。
  読み書きの実体は daily.py に置き、朝の便りと同じ1つのファイルを分け合います。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily as D          # noqa: E402  保存状態の読み書きを共有する

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LATEST = os.path.join(ROOT, "docs", "data", "latest.json")
# 状態は docs/data/ に置く。GitHub Actions のランナーは毎回まっさらで、
# ここに置いて Pages で公開しないと「前回送った内容」が残らないため。
# 実体は daily.py と同じ1ファイル（notify_state.json）。
STATE = D.STATE

# ---- 通知の条件。ここを触れば挙動が変わる ----
RULES = {
    "z_threshold": 2.5,        # 値動きが何σを超えたら知らせるか
    "corr_z_threshold": 2.0,   # 相関の異常が何σを超えたら知らせるか
    "corr_cross_zero": True,   # BTC×S&P500の相関が0を跨いだら知らせる
    "risk_threshold": 60,      # リスク選好が±これを超えたら知らせる
    "stale_alert": True,       # 取得失敗が出たら知らせる
    "max_lines": 12,           # 1通に詰め込む最大行数
    "cooldown_min": 90,        # 前回送信から最低これだけ空ける（分）
    # 1か月あたりの送信上限。無料枠は月200通で、朝の便り（daily.py）と分け合う。
    # 朝の便りが月31通ぶん必ず通るよう、こちらは低めに止める。
    "monthly_cap": 120,
}


def load_state() -> dict:
    """保存状態を読む。実体は daily.py と共有（notify_state.json 1つ）。"""
    return D.load_state()


def save_state(st: dict) -> None:
    D.save_state(st)


def gate(alert: dict, fp: str, now: datetime, used: int,
         rules: dict = RULES) -> tuple[bool, str]:
    """送ってよいかを判定する。(送ってよいか, 理由) を返す。

    alert は保存状態のうち通知の区画。used は今月すでに送った通数。
    """
    if used >= rules["monthly_cap"]:
        return False, (f"今月すでに {used} 通送っており、上限 "
                       f"{rules['monthly_cap']} 通に達しています")
    last = (alert or {}).get("sent_at")
    if last:
        try:
            elapsed = (now - datetime.fromisoformat(last)).total_seconds() / 60
        except Exception:  # noqa: BLE001
            elapsed = 1e9
        if alert.get("fingerprint") == fp:
            return False, f"前回と同じ内容です（{elapsed:.0f}分前に送信済み）"
        if elapsed < rules["cooldown_min"]:
            return False, (f"前回の送信から {elapsed:.0f} 分しか経っていません"
                           f"（最低 {rules['cooldown_min']} 分空けます）")
    return True, "送信します"


def bump(st: dict, fp: str, now: datetime, reasons: list[str]) -> dict:
    """送った記録を保存状態に書き込む。通数は朝の便りと共通で数える。"""
    st = dict(st)
    used = D.month_count(st, now)
    st["month"] = now.strftime("%Y-%m")
    st["count"] = used + 1
    st["alert"] = {"fingerprint": fp, "sent_at": now.isoformat(), "reasons": reasons}
    return st


def send(text: str) -> bool:
    """LINE Messaging API に push する。未設定なら送らずに False を返す。

    認証情報の取り出しと番号の説明は daily.py と共有している。
    """
    ok, _code, _err = D.push([{"type": "text", "text": text[:4900]}])
    return ok


def build_message(d: dict, rules: dict = RULES) -> tuple[str, list[str]]:
    """通知本文と、判定に使った項目の一覧を返す。該当なしなら本文は空。"""
    lines, reasons = [], []

    # 1) 値動きの異常
    for r in (d.get("scan") or []):
        z = r.get("z")
        if z is not None and abs(z) >= rules["z_threshold"]:
            lines.append(f"・{r['label']} {r['value']}（{z:+.1f}σ）")
            reasons.append(f"z:{r['label']}")

    # 2) 関係の変化
    for r in (d.get("scan_rel") or []):
        z = r.get("z")
        if z is not None and abs(z) >= rules["corr_z_threshold"]:
            lines.append(f"・{r['label']} {r['value']}（{z:+.1f}σ）")
            reasons.append(f"corr:{r['label']}")

    # 3) BTC×S&P500の相関が0を跨いだ
    if rules["corr_cross_zero"]:
        s = (d.get("rolling_corr") or {}).get("SPX") or []
        if len(s) >= 2:
            prev, now = s[-2][1], s[-1][1]
            if prev is not None and now is not None and (prev >= 0) != (now >= 0):
                ci = d.get("corr_ci") or {}
                span = (f"　95%幅 {ci['lo']:+.2f}〜{ci['hi']:+.2f}" if ci else "")
                lines.append(f"・BTC×S&P500の30日相関が0を{'割りました' if now < 0 else '超えました'}"
                             f"（{prev:+.2f} → {now:+.2f}）{span}")
                reasons.append("cross0")

    # 4) リスク選好の振れ
    risk = d.get("risk") or {}
    if risk.get("value") is not None and abs(risk["value"]) >= rules["risk_threshold"]:
        v = risk["value"]
        lines.append(f"・リスク選好 {v:+.0f}（{'強いリスクオン' if v > 0 else '強いリスクオフ'}）")
        reasons.append("risk")

    # 5) 取得の失敗
    stale = ((d.get("health") or {}).get("stale") or [])
    if rules["stale_alert"] and stale:
        lines.append(f"・データ取得に失敗: {', '.join(s['key'] for s in stale[:8])}"
                     f"{' ほか' if len(stale) > 8 else ''}")
        reasons.append("stale")

    if not lines:
        return "", reasons

    if len(lines) > rules["max_lines"]:
        rest = len(lines) - rules["max_lines"]
        lines = lines[:rules["max_lines"]] + [f"・ほか {rest} 件"]

    gen = d.get("generated_at", "")
    # 見出しは日本時間で出す。UTCのまま出していて9時間ずれて見えた。
    try:
        _t = datetime.fromisoformat(gen).astimezone(D.JST)
        head = (f"【潮目】{_t.year}年{_t.month}月{_t.day}日 "
                f"{_t.hour:02d}:{_t.minute:02d} JST")
    except Exception:  # noqa: BLE001
        head = "【潮目】"
    tail = "https://pythonddd.github.io/Market/"
    return "\n".join([head, ""] + lines + ["", tail]), reasons


def fingerprint(reasons: list[str]) -> str:
    """同じ内容の連投を止めるための指紋。理由の組み合わせだけで作る。

    数値そのものを含めると、わずかな変動で毎回別物と判定され連投になるため。
    """
    return hashlib.sha256("|".join(sorted(set(reasons))).encode()).hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="送信せず内容だけ表示")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--force", action="store_true", help="連投防止を無視して送る")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    if not os.path.exists(LATEST):
        print(f"{LATEST} がありません。先に build.py を実行してください。")
        return 1
    with open(LATEST, encoding="utf-8") as f:
        d = json.load(f)

    # 月の区切りは日本時間で数える。朝の便りと同じ物差しにするため。
    now = datetime.now(D.JST)
    state = load_state()
    used = D.month_count(state, now)

    text, reasons = build_message(d)
    if not text:
        print("しきい値に触れた項目はありません。通知しません。")
        # 送らない回でも必ず書き出す。書き出さないと、この回の配信で
        # 公開中の保存状態ごと消えてしまう（過去に実際そうなっていた）。
        save_state(state)
        return 0

    fp = fingerprint(reasons)
    ok_to_send, why = gate(state.get("alert") or {}, fp, now, used)

    print(text)
    print(f"\n判定: {why}")
    print(f"今月の送信数: {used} / {RULES['monthly_cap']}（朝の便りと共通）")

    if a.dry_run:
        print("--dry-run のため送信しませんでした。")
        return 0
    if not ok_to_send and not a.force:
        save_state(state)
        return 0

    if send(text):
        save_state(bump(state, fp, now, reasons))
    else:
        save_state(state)
    return 0


# ==========================================================================
def selftest() -> int:
    print("notify.py セルフテスト")
    print("-" * 70)
    fails, n = [], [0]

    def ck(name, cond, got=""):
        n[0] += 1
        print(f"  {'OK' if cond else 'NG'}  {name}" + ("" if cond else f"  {got}"))
        if not cond:
            fails.append(name)

    quiet = {"generated_at": "2026-09-03T03:29:20+00:00",
             "scan": [{"label": "金", "value": "+0.4%", "z": 0.8}],
             "scan_rel": [{"label": "BTC × S&P500", "value": "+0.3", "z": 0.5}],
             "rolling_corr": {"SPX": [["2026-09-01", 0.3], ["2026-09-02", 0.28]]},
             "risk": {"value": 12}, "health": {"stale": []}}
    t, r = build_message(quiet)
    ck("平常時は通知しない", t == "", t[:60])

    loud = json.loads(json.dumps(quiet))
    loud["scan"][0]["z"] = -3.4
    t, r = build_message(loud)
    ck("3.4σで通知する", "金" in t and "-3.4σ" in t.replace("−", "-"), t[:80])
    ck("見出しが日本時間になっている", "2026年9月3日 12:29 JST" in t, t[:40])
    ck("末尾にURLが入る", "pythonddd.github.io" in t)

    cross = json.loads(json.dumps(quiet))
    cross["rolling_corr"]["SPX"] = [["2026-09-01", 0.05], ["2026-09-02", -0.03]]
    cross["corr_ci"] = {"lo": -0.38, "hi": 0.33}
    t, r = build_message(cross)
    ck("相関が0を割ったら通知", "0を割りました" in t, t[:90])
    ck("誤差幅も一緒に送る", "95%幅" in t, t[:120])

    up = json.loads(json.dumps(quiet))
    up["rolling_corr"]["SPX"] = [["2026-09-01", -0.05], ["2026-09-02", 0.03]]
    ck("0を超えた場合も通知", "0を超えました" in build_message(up)[0])

    st = json.loads(json.dumps(quiet))
    st["health"]["stale"] = [{"key": "N225"}, {"key": "GOLD"}]
    t, _ = build_message(st)
    ck("取得失敗を通知", "取得に失敗" in t and "N225" in t)

    risky = json.loads(json.dumps(quiet))
    risky["risk"]["value"] = -72
    ck("リスク選好の振れを通知", "リスクオフ" in build_message(risky)[0])

    many = json.loads(json.dumps(quiet))
    many["scan"] = [{"label": f"銘柄{i}", "value": "+1%", "z": 3.0} for i in range(30)]
    t, _ = build_message(many)
    ck("行数を上限で打ち切る", "ほか" in t and t.count("・") <= RULES["max_lines"] + 1,
       t.count("・"))

    # 送信の可否判定
    from datetime import timedelta as _td
    now = datetime(2026, 9, 3, 12, 0, tzinfo=D.JST)
    ck("初回は送れる", gate({}, "aaa", now, 0)[0])
    same = {"fingerprint": "aaa", "sent_at": (now - _td(minutes=200)).isoformat()}
    ck("同じ内容なら時間が経っても送らない", not gate(same, "aaa", now, 1)[0],
       gate(same, "aaa", now, 1)[1])
    ck("内容が変われば送る", gate(same, "bbb", now, 1)[0])
    fresh = {"fingerprint": "aaa", "sent_at": (now - _td(minutes=30)).isoformat()}
    ck("90分経っていなければ送らない", not gate(fresh, "bbb", now, 1)[0],
       gate(fresh, "bbb", now, 1)[1])
    ck("月間上限に達したら送らない",
       not gate({}, "y", now, RULES["monthly_cap"])[0])
    ck("上限の1つ手前なら送れる", gate({}, "y", now, RULES["monthly_cap"] - 1)[0])

    st0 = {"month": "2026-09", "count": 3, "alert": {}, "daily": {"sent_date": "x"}}
    st1 = bump(st0, "z", now, ["r"])
    ck("送信数が積み上がる", st1["count"] == 4, st1["count"])
    ck("朝の便りの記録を壊さない", st1["daily"] == {"sent_date": "x"}, st1["daily"])
    ck("月が変われば1から数え直す",
       bump({"month": "2026-08", "count": 50}, "z", now, [])["count"] == 1)
    ck("通知の記録は alert 区画に入る", st1["alert"]["fingerprint"] == "z")
    ck("状態は公開フォルダに置く", "docs" in STATE and "data" in STATE, STATE)
    ck("朝の便りと同じファイルを使う", STATE == D.STATE)
    ck("上限は無料枠(200通)の範囲",
       RULES["monthly_cap"] + 31 <= 200, RULES["monthly_cap"])

    ck("同じ理由なら指紋が一致", fingerprint(["a", "b"]) == fingerprint(["b", "a"]))
    ck("理由が違えば指紋も違う", fingerprint(["a"]) != fingerprint(["a", "b"]))

    for _k in ("LINE_TOKEN", "LINE_CHANNEL_TOKEN", "LINE_USER_ID", "LINE_TO"):
        os.environ.pop(_k, None)
    ck("未設定なら送信せずFalse", send("テスト") is False)

    # このテスト自身が文字列を含むので、セルフテスト部より前だけを検査する
    src = open(__file__, encoding="utf-8").read().split("def selftest()")[0]
    # 変数名が文中に出るのは問題ない。中身を print に流していないかだけを見る。
    import re as _re
    leaks = [l for l in src.splitlines()
             if _re.search(r"print\(.*\{\s*token", l) or _re.search(r"print\(\s*token\b", l)]
    ck("トークンの中身をログに出していない", not leaks, leaks[:1])

    print("-" * 70)
    print(f"結果: {n[0] - len(fails)}/{n[0]} 合格")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
