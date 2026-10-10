"""Durable outbox on a separate GitHub branch; local file for local execution.

No token or recipient ID is stored. A public repository also makes this branch
public: only public market message bodies, timestamps and hashes belong here.
"""
from __future__ import annotations

import base64
import json
import hashlib
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


class StateError(RuntimeError):
    pass


def write_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


class LocalStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else ROOT / ".state" / "notification-state.json"

    def load(self):
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, OSError) as exc:
            raise StateError("ローカルの通知状態を読めません。送信を中止します。") from exc

    def save(self, state):
        write_json(self.path, state)


class GitHubStore:
    branch = "market-state"
    path = "notification-state.json"

    def __init__(self, repo=None, token=None, opener=None):
        self.repo = repo or os.getenv("GITHUB_REPOSITORY", "")
        self.token = token or os.getenv("GITHUB_TOKEN", "")
        self.open = opener or urllib.request.urlopen
        self.sha = None
        self.branch_exists = False
        if not self.repo or not self.token:
            raise StateError("GITHUB_TOKEN / GITHUB_REPOSITORY がありません。")

    def api(self, path, method="GET", body=None):
        req = urllib.request.Request("https://api.github.com/repos/" + self.repo + path,
            method=method, data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                     "Content-Type": "application/json", "User-Agent": "shiome/2",
                     "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with self.open(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, {}
        except (OSError, ValueError) as exc:
            raise StateError("通知状態の通信に失敗しました。重複送信を防ぐため送信を中止します。") from exc

    def load(self):
        code, item = self.api("/contents/" + self.path + "?ref=" + self.branch)
        if code == 200:
            self.sha, self.branch_exists = item["sha"], True
            try:
                state = json.loads(base64.b64decode(item["content"]))
                if not isinstance(state, dict):
                    raise ValueError()
                return state
            except (ValueError, KeyError) as exc:
                raise StateError("通知状態が壊れています。送信を中止します。") from exc
        if code != 404:
            raise StateError(f"通知状態を取得できません（GitHub HTTP {code}）。Actionsのcontents権限を確認してください。")
        code, _ = self.api("/git/ref/heads/" + self.branch)
        if code == 200:
            # Existing branch without the ledger can indicate accidental deletion.
            raise StateError("market-state に通知状態が見つかりません。自動初期化せず停止します。")
        if code != 404:
            raise StateError(f"状態ブランチを確認できません（HTTP {code}）。")
        # One-time migration from the former, publicly published ledger.
        site = os.getenv("SITE_URL", "https://pythonddd.github.io/Market").rstrip("/")
        try:
            with self.open(site + "/data/notify_state.json", timeout=15) as r:
                old = json.loads(r.read())
            if not isinstance(old, dict):
                raise ValueError()
            old["migrated_from_v1"] = True
            return old
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {}
            raise StateError("旧版の通知状態を確認できません。時間をおいて再実行してください。") from exc
        except (OSError, ValueError) as exc:
            raise StateError("旧版の通知状態を確認できません。安全な移行のため送信を中止します。") from exc

    def save(self, state):
        raw = json.dumps(state, ensure_ascii=False, allow_nan=False).encode()
        if not self.branch_exists:
            # Create the first ledger and branch atomically from the reader's
            # perspective: a branch without its file is never published.
            code, tree = self.api("/git/trees", "POST", {"tree": [{"path": self.path,
                "mode": "100644", "type": "blob", "content": raw.decode()}]})
            if code != 201:
                raise StateError(f"通知状態の初期化に失敗しました（HTTP {code}）。contents: write を確認してください。")
            code, commit = self.api("/git/commits", "POST", {
                "message": "Initialize notification outbox [skip ci]", "tree": tree["sha"], "parents": []})
            if code != 201:
                raise StateError(f"通知状態の初期化に失敗しました（HTTP {code}）。")
            code, _ = self.api("/git/refs", "POST", {"ref": "refs/heads/" + self.branch, "sha": commit["sha"]})
            if code != 201:
                raise StateError(f"通知状態ブランチを作れません（HTTP {code}）。contents: write を確認してください。")
            self.branch_exists = True
            self.sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            return
        content = base64.b64encode(raw).decode()
        body = {"message": "Persist notification outbox [skip ci]", "branch": self.branch, "content": content}
        if self.sha:
            body["sha"] = self.sha
        code, result = self.api("/contents/" + self.path, "PUT", body)
        if code not in (200, 201):
            raise StateError(f"通知状態を保存できません（HTTP {code}）。再試行キーを変えず、次回再確認します。")
        self.sha = result["content"]["sha"]


def bootstrap(store):
    """Migrate the old ledger BEFORE replacing Pages, even if LINE is unconfigured."""
    state = store.load()
    if not store.branch_exists:
        store.save({"version": 2, "initialized_at": datetime.now(timezone.utc).isoformat(), **state})


if __name__ == "__main__":
    try:
        bootstrap(GitHubStore())
        print("通知状態を確認しました。LINE送信は行っていません。")
    except (StateError, ValueError, KeyError, OSError) as exc:
        print(str(exc) if isinstance(exc, StateError) else type(exc).__name__)
        write_json(ROOT / "docs" / "data" / "line_status.json", {
            "version": 2, "state": "state_error", "checked_at": datetime.now(timezone.utc).isoformat(),
            "message": "通知状態の移行・取得に失敗しました。Actionsの通知状態ステップを確認してください。",
            "delivery_confirmed": False})
        raise SystemExit(1)
