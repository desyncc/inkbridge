"""
What the last unattended sync did, and telling the user when it needs them.

A sync running in a daemon has nobody watching it, so an expired sign-in or a
missing vault would otherwise show up only in a log nobody reads. The result
of the last full sync is kept in health.json (shown by `status`), and when a
problem needs the user, one alert goes to `notify_url`; a second message says
when it is fixed. Alerts name no notebooks or pages, only counts and causes.
"""

from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import httpx

from .client import AuthError
from .config import Config
from .jsonstore import read_json, update_json
from .paths import command, data_path
from .vault import VaultNotFoundError

HEALTH_FILE = data_path("health.json")

# Failures that clear up by themselves (a dropped connection) alert only when
# they repeat; the ones only the user can fix alert at once.
ALERT_AFTER_FAILURES = 2
MAX_MESSAGE_CHARS = 300


def load_health() -> Dict[str, Any]:
    return read_json(HEALTH_FILE, {})


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _classify(exc: BaseException) -> Tuple[str, str, bool]:
    """(kind, message, needs the user right away) for a failed sync."""
    if isinstance(exc, AuthError):
        return ("auth",
                "Your Viwoods sign-in was rejected and could not be renewed. On the machine "
                f"running InkBridge, run: {command('login')}",
                True)
    if isinstance(exc, VaultNotFoundError):
        return ("vault", str(exc), True)
    return ("sync", f"Sync failed: {exc}", False)


def _scrub(message: str, cfg: Config) -> str:
    token = (cfg.token or "").strip()
    if len(token) > 8:
        message = message.replace(token, "***")
    return message[:MAX_MESSAGE_CHARS]


def send_notification(url: str, title: str, message: str, good: bool = False) -> bool:
    """
    Posts a message to an ntfy topic (plain text with a title) or a Discord /
    Slack webhook (JSON). Returns whether it was delivered; never raises, and
    never prints the URL, which is a secret.
    """
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return False
    try:
        if "discord.com/api/webhooks" in url or "discordapp.com/api/webhooks" in url:
            resp = httpx.post(url, json={"content": f"**{title}**\n{message}"}, timeout=10)
        elif "hooks.slack.com" in url:
            resp = httpx.post(url, json={"text": f"*{title}*\n{message}"}, timeout=10)
        else:
            resp = httpx.post(
                url,
                content=message.encode("utf-8"),
                headers={
                    "Title": title,
                    "Tags": "white_check_mark" if good else "warning",
                    "Priority": "default" if good else "high",
                },
                timeout=10,
            )
        if not resp.is_success:
            print(f"[notify] The notification service refused the message (HTTP {resp.status_code}).")
            return False
        return True
    except Exception as e:
        print(f"[notify] Could not deliver the notification ({type(e).__name__}).")
        return False


def _alert(cfg: Config, kind: str, title: str, message: str) -> None:
    """Sends the alert for `kind` and remembers it, so it isn't repeated every sync."""
    if not (cfg.notify_url or "").strip():
        return
    if send_notification(cfg.notify_url, title, message):
        update_json(HEALTH_FILE, lambda d: d.update(alerted_kind=kind), {})


def report_failure(cfg: Config, exc: BaseException, alert: bool = True) -> None:
    """Records a failed sync; `alert=False` for a run the user started and is watching."""
    kind, message, immediate = _classify(exc)
    message = _scrub(message, cfg)
    now = _now()

    def mutate(d: Dict[str, Any]) -> None:
        same = d.get("status") == "failing" and d.get("kind") == kind
        d["consecutive_failures"] = d.get("consecutive_failures", 0) + 1 if same else 1
        d["since"] = d.get("since") or now if same else now
        d.update(status="failing", kind=kind, message=message, last_attempt=now)

    state = update_json(HEALTH_FILE, mutate, {})
    if alert and state.get("alerted_kind") != kind and (
        immediate or state["consecutive_failures"] >= ALERT_AFTER_FAILURES
    ):
        _alert(cfg, kind, "InkBridge: sync needs your attention", message)


def report_success(cfg: Config, result: Optional[Dict[str, Any]] = None, alert: bool = True) -> None:
    """
    Records a finished sync. Deletions held back by the safety limit count as needing
    attention; a clean sync clears an earlier alert and says so.
    """
    held = (result or {}).get("held_back") or []
    now = _now()

    if held:
        message = (
            f"{len(held)} notebook(s) deleted on your tablet were NOT removed from your vault, "
            f"because that is more than the safety limit allows at once. If they really are "
            f"gone, run: {command('sync --confirm-deletions')}"
        )

        def mutate_held(d: Dict[str, Any]) -> None:
            same = d.get("status") == "attention" and d.get("kind") == "held_back"
            d.update(status="attention", kind="held_back", message=message, last_attempt=now,
                     last_success=now, consecutive_failures=0,
                     since=(d.get("since") or now) if same else now)

        state = update_json(HEALTH_FILE, mutate_held, {})
        if alert and state.get("alerted_kind") != "held_back":
            _alert(cfg, "held_back", "InkBridge: deletions waiting for you", message)
        return

    def mutate_ok(d: Dict[str, Any]) -> None:
        d.update(status="ok", kind=None, message="", since=None, consecutive_failures=0,
                 last_attempt=now, last_success=now)

    previous = load_health()
    update_json(HEALTH_FILE, mutate_ok, {})
    if previous.get("alerted_kind"):
        if not (cfg.notify_url or "").strip() or send_notification(
            cfg.notify_url, "InkBridge: working again",
            "InkBridge is working again: the last sync finished normally.", good=True,
        ):
            update_json(HEALTH_FILE, lambda d: d.update(alerted_kind=None), {})


def describe_health(state: Dict[str, Any]) -> str:
    """One line for `status`."""
    status = state.get("status")
    if status == "failing":
        return f"FAILING since {state.get('since')}: {state.get('message')}"
    if status == "attention":
        return f"NEEDS ATTENTION: {state.get('message')}"
    if status == "ok":
        return f"OK (last successful sync {state.get('last_success')})"
    return "No sync recorded yet."
