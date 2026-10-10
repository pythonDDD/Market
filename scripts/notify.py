"""Shared abnormality rules; notification.py owns all delivery and state."""
from __future__ import annotations
import hashlib
from datetime import datetime
import daily as D

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


if __name__ == "__main__":
    import notification
    raise SystemExit(notification.main())
