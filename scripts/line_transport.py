"""LINE Messaging API transport. No credentials or recipient IDs in logs/status."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

API = "https://api.line.me/v2/bot"
HINTS = {
    "missing_config": "LINE_TOKEN と LINE_USER_ID を Actions Secrets に設定してください。旧名にも対応しています。",
    "invalid_recipient": "宛先は U/C/R で始まる33文字のIDです。@から始まるLINE IDではありません。",
    "unauthorized": "チャネルアクセストークンが無効です。Messaging API のトークンを再発行してください。",
    "forbidden": "Messaging API の権限・チャネル設定を確認してください。",
    "bad_request": "宛先IDまたはメッセージ形式が不正です。IDとチャネルの組合せを確認してください。",
    "quota_exceeded": "LINE側の月間送信上限に達しました。利用プラン・利用数を確認してください。",
    "rate_limited": "LINE側がリクエストを制限しています。時間をおいて再実行してください。",
    "network_error": "LINEとの通信に失敗しました。同じ再試行キーで次回再試行します。",
    "server_error": "LINE側の一時的な障害です。同じ再試行キーで次回再試行します。",
}


@dataclass
class Result:
    ok: bool
    code: str
    http_status: int = 0
    data: dict | None = None
    request_id: str = ""


def credentials() -> tuple[str, str]:
    token = next((os.getenv(k, "").strip() for k in
                  ("LINE_TOKEN", "LINE_CHANNEL_TOKEN", "LINE_CHANNEL_ACCESS_TOKEN")
                  if os.getenv(k, "").strip()), "")
    target = next((os.getenv(k, "").strip() for k in
                   ("LINE_USER_ID", "LINE_TO") if os.getenv(k, "").strip()), "")
    return token, target


def validate_config(token: str, target: str) -> Result:
    if not token or not target:
        return Result(False, "missing_config")
    if not re.fullmatch(r"[UCR][0-9a-fA-F]{32}", target):
        return Result(False, "invalid_recipient")
    return Result(True, "configured")


class LineClient:
    def __init__(self, token: str, target: str, opener=None, sleeper=None):
        self.token, self.target = token, target
        self.open = opener or urllib.request.urlopen
        self.sleep = sleeper or time.sleep

    def request(self, path: str, payload=None, retry_key: str = "") -> Result:
        headers = {"Authorization": "Bearer " + self.token,
                   "Content-Type": "application/json", "User-Agent": "shiome/2"}
        if retry_key:
            headers["X-Line-Retry-Key"] = retry_key
        body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        for attempt in range(3):
            req = urllib.request.Request(API + path, data=body, headers=headers,
                                         method="POST" if body is not None else "GET")
            try:
                with self.open(req, timeout=20) as response:
                    raw = response.read()
                    data = json.loads(raw) if raw else {}
                    return Result(True, "accepted" if body else "ok", response.status,
                                  data, response.headers.get("x-line-request-id", ""))
            except urllib.error.HTTPError as exc:
                status = exc.code
                try:
                    detail = json.loads(exc.read()).get("message", "").lower()
                except (ValueError, AttributeError):
                    detail = ""
                accepted = exc.headers.get("x-line-accepted-request-id", "")
                if status == 409 and retry_key and accepted:
                    return Result(True, "already_accepted", 409, {}, accepted)
                code = {400: "bad_request", 401: "unauthorized", 403: "forbidden"}.get(status, "http_error")
                if status == 429:
                    code = "quota_exceeded" if "monthly" in detail or "quota" in detail else "rate_limited"
                if status >= 500:
                    code = "server_error"
                result = Result(False, code, status)
                # Retry only transient failures, with identical body + UUID.
                if status < 500:
                    return result
            except (OSError, ValueError):
                result = Result(False, "network_error")
            if attempt < 2:
                self.sleep(2 ** attempt)
        return result

    def diagnose(self) -> tuple[Result, dict]:
        bot = self.request("/info")
        if not bot.ok:
            return bot, {}
        limit = self.request("/message/quota")
        used = self.request("/message/quota/consumption")
        quota = {}
        if limit.ok and used.ok:
            quota = {"type": (limit.data or {}).get("type"),
                     "limit": (limit.data or {}).get("value"),
                     "used": (used.data or {}).get("totalUsage")}
            if quota["type"] == "limited" and isinstance(quota["limit"], (int, float)) and isinstance(quota["used"], (int, float)):
                quota["remaining"] = max(0, quota["limit"] - quota["used"])
                if quota["remaining"] == 0:
                    return Result(False, "quota_exceeded"), quota
        else:
            quota["unavailable"] = True
        return Result(True, "configured"), quota

    def push(self, messages: list, key: str) -> Result:
        return self.request("/message/push", {"to": self.target, "messages": messages}, key)
