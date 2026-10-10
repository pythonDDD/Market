"""Single notification entry point: diagnostics, persisted retries and catch-up.

Python 3.12; stdlib only. Dry run never sends messages or writes notification
state. Success means LINE API acceptance, not confirmation of phone delivery.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import daily
import notify
from line_transport import HINTS, LineClient, credentials, validate_config
from state_store import GitHubStore, LocalStore, StateError, write_json

ROOT = Path(__file__).resolve().parents[1]
JST = timezone(timedelta(hours=9))
TEXT = {
    "accepted": "LINE APIが受け付けました。端末への到着はLINE側で確認してください。",
    "already_accepted": "同じ通知はLINE APIがすでに受け付けています。重複送信を防ぎました。",
    "idle": "次の通知条件を待っています。",
    "state_error": "通知状態の読み書きに失敗しました。Actionsのログとcontents: write権限を確認してください。",
    "configuration_error": "通知設定ファイルが不正です。market.config.json と LINE_DAILY_HOUR を確認してください。",
    "checked": "トークンと送信枠の診断が完了しました。宛先の受信可否はテスト送信で確認してください。",
    "disabled": "LINE_ENABLED=false のため通知を停止しています。",
    "stale_data": "データが古い・不足・デモのため市況通知を見送りました。データ更新を確認してください。",
    "local_cap": "このアプリの月間送信上限に達しました。",
    "retry_expired": "未確定の送信から23時間以上経過しました。LINEで到着を確認し、導入手順の復旧方法を実施してください。",
    "recipient_changed": "未確定の通知と現在の宛先が異なります。導入手順の復旧方法を確認してください。",
    **HINTS,
}


def settings():
    value = json.loads((ROOT / "market.config.json").read_text(encoding="utf-8"))
    cfg = value["notifications"]
    cfg = {**cfg, "daily_hour_jst": int(os.getenv("LINE_DAILY_HOUR") or cfg["daily_hour_jst"])}
    if not 0 <= cfg["daily_hour_jst"] <= 23:
        raise ValueError("daily_hour_jst は0〜23で指定してください")
    for key in ("monthly_cap", "alert_cap", "cooldown_minutes", "max_data_age_minutes"):
        if not isinstance(cfg[key], (int, float)) or cfg[key] <= 0:
            raise ValueError(key + " は正数で指定してください")
    return cfg


def fresh(data, now, minutes):
    try:
        age = (now - datetime.fromisoformat(data["generated_at"])).total_seconds() / 60
        return (data.get("mode") not in ("mock", "demo", "snapshot") and -5 <= age <= minutes
                and bool(data.get("summary")) and data.get("health", {}).get("ok", 0) > 0)
    except (KeyError, TypeError, ValueError):
        return False


def plan(data, state, now, cfg):
    """Return a single daily OR alert message. One scheduled run cannot send both."""
    today = now.date().isoformat()
    used = daily.month_count(state, now)
    if used >= cfg["monthly_cap"]:
        return None, "local_cap"
    if not fresh(data, now, cfg["max_data_age_minutes"]):
        return None, "stale_data"
    rules = {**notify.RULES, "z_threshold": cfg["z_threshold"],
             "corr_z_threshold": cfg["corr_z_threshold"], "risk_threshold": cfg["risk_threshold"]}
    alert_text, reasons = notify.build_message(data, rules)
    fp = notify.fingerprint(reasons)
    if cfg["daily_enabled"] and now.hour >= cfg["daily_hour_jst"] and state.get("daily", {}).get("sent_date") != today:
        text = daily.build_text(data, now).replace("朝の便り", "今日の市場だより")
        if reasons:
            text = text.rsplit("\n", 1)[0] + "\n\n異常スキャン\n" + "\n".join(alert_text.splitlines()[2:-2]) + "\n\n" + daily.SITE + "/"
        return {"kind": "daily", "text": text[:4900], "date": today, "fingerprint": fp if reasons else ""}, "due"
    if cfg["alerts_enabled"] and alert_text and used < cfg["alert_cap"]:
        last = state.get("alert") or {}
        try:
            elapsed = (now - datetime.fromisoformat(last["sent_at"])).total_seconds() / 60
        except (KeyError, TypeError, ValueError):
            elapsed = float("inf")
        if elapsed >= cfg["cooldown_minutes"] and (last.get("fingerprint") != fp or elapsed >= 1440):
            return {"kind": "alert", "text": alert_text[:4900], "date": today, "fingerprint": fp}, "due"
    return None, "idle"


def public_status(state, result, now, cfg, quota=None, http_status=0):
    return {"version": 2, "checked_at": now.isoformat(), "state": result,
            "message": TEXT.get(result, "通知処理でエラーが発生しました。Actionsの実行結果を確認してください。"),
            "http_status": http_status or None, "daily_hour_jst": cfg.get("daily_hour_jst", 9),
            "last_accepted_at": state.get("last_accepted_at") or state.get("daily", {}).get("sent_at"),
            "last_daily_date": state.get("daily", {}).get("sent_date"),
            "app_month_count": daily.month_count(state, now) if state else None, "app_month_cap": cfg.get("monthly_cap", 190),
            "quota": quota or {}, "pending": bool(state.get("pending")),
            "delivery_confirmed": False}


def dispatch(item, state, store, client, now):
    recipient_hash = hashlib.sha256(client.target.encode()).hexdigest()
    pending = state.get("pending")
    if pending:
        if pending.get("recipient_hash") != recipient_hash:
            return "recipient_changed", 0
        age = (now - datetime.fromisoformat(pending["created_at"])).total_seconds()
        if age >= 23 * 3600:
            return "retry_expired", 0
    else:
        pending = {**item, "created_at": now.isoformat(), "retry_key": str(uuid.uuid4()),
                   "recipient_hash": recipient_hash}
        state["pending"] = pending
        # Commit the exact payload and retry key BEFORE any LINE POST.
        store.save(state)
    result = client.push([{"type": "text", "text": pending["text"]}], pending["retry_key"])
    if not result.ok:
        if 400 <= result.http_status < 500 and result.http_status != 409:
            # Explicit rejection means the request was not accepted; allow a fresh
            # current-day message after config/quota has been corrected.
            state.pop("pending", None)
            store.save(state)
        return result.code, result.http_status
    state["count"] = daily.month_count(state, now) + 1
    state["month"] = now.strftime("%Y-%m")
    state["last_accepted_at"] = now.isoformat()
    if pending["kind"] == "daily":
        state["daily"] = {"sent_date": pending["date"], "sent_at": now.isoformat(), "with_image": False}
    if pending.get("fingerprint"):
        state["alert"] = {"sent_at": now.isoformat(), "fingerprint": pending["fingerprint"]}
    state.setdefault("events", []).append({"kind": pending["kind"], "at": now.isoformat(), "result": result.code})
    state["events"] = state["events"][-30:]
    state.pop("pending", None)
    store.save(state)
    return result.code, result.http_status


def main():
    ap = argparse.ArgumentParser()
    modes = ap.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="認証・送信枠だけ診断。送信しない")
    modes.add_argument("--test", action="store_true", help="設定された宛先へテストを1通送る")
    modes.add_argument("--dry-run", action="store_true", help="ネット接続・書き込み・送信なし")
    args = ap.parse_args()
    now = datetime.now(JST)
    state, cfg, quota, code, http = {}, {}, {}, "internal_error", 0
    try:
        cfg = settings()
        latest = ROOT / "docs" / "data" / "latest.json"
        data = json.loads(latest.read_text(encoding="utf-8")) if latest.exists() else {}
        if args.dry_run:
            item, reason = plan(data, {}, now, cfg)
            print(json.dumps({"dry_run": True, "reason": reason, "message": item}, ensure_ascii=False, indent=2))
            return 0
        if os.getenv("LINE_ENABLED", "true").lower() in ("false", "0", "off"):
            code = "disabled"
            return 0
        token, target = credentials()
        result = validate_config(token, target)
        if not result.ok:
            code = result.code
            return 1
        client = LineClient(token, target)
        result, quota = client.diagnose()
        if not result.ok:
            code, http = result.code, result.http_status
            return 1
        store = GitHubStore() if os.getenv("GITHUB_ACTIONS") == "true" else LocalStore()
        state = store.load()
        if args.check:
            code = "checked"
            return 0
        if args.test:
            item = {"kind": "test", "date": now.date().isoformat(), "fingerprint": "",
                    "text": f"潮目 🐚 通知テスト\n{now:%Y/%m/%d %H:%M} JST\nこのメッセージが見えれば受信確認は完了です。\n{daily.SITE}/"}
            if daily.month_count(state, now) >= cfg["monthly_cap"]:
                code = "local_cap"
                return 1
        else:
            item, code = plan(data, state, now, cfg)
        if state.get("pending") or item:
            code, http = dispatch(item, state, store, client, now)
        return 0 if code in ("accepted", "already_accepted", "idle") else 1
    except StateError as exc:
        code = "state_error"
        print(str(exc), file=sys.stderr)
        return 1
    except (ValueError, KeyError, TypeError, OSError) as exc:
        # Do not emit raw response bodies, credentials or arbitrary exception text.
        code = "configuration_error" if not cfg else "internal_error"
        print(f"通知処理を完了できませんでした: {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        if not args.dry_run:
            status = public_status(state, code, now, cfg, quota, http)
            write_json(ROOT / "docs" / "data" / "line_status.json", status)
            print(f"LINE: {code} — {status['message']}")
            if os.getenv("GITHUB_STEP_SUMMARY"):
                with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as out:
                    out.write(f"\n### LINE通知\n- 結果: `{code}`\n- {status['message']}\n- 診断時刻: {now:%Y-%m-%d %H:%M} JST\n")


if __name__ == "__main__":
    raise SystemExit(main())
