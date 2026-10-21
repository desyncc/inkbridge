"""
Unattended syncs (the daemon, the dashboard's auto-sync) tell the user when
something needs them, once, and when it is fixed. The last result is kept
for `status`.
"""

import json
import os
import stat
import types

import httpx
import pytest

import companion
import viwoods.config as config_module
import viwoods.health as health
import viwoods.server as server
from viwoods.client import AuthError
from viwoods.config import Config
from viwoods.vault import VaultNotFoundError

URL = "https://ntfy.example.com/my-secret-topic"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "HEALTH_FILE", tmp_path / "health.json")
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.json")


@pytest.fixture
def sent(monkeypatch):
    """Records every notification instead of sending it."""
    calls = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return httpx.Response(200)

    monkeypatch.setattr(health.httpx, "post", fake_post)
    return calls


def cfg(url=URL):
    return Config(token="t", notify_url=url)


def text(call):
    return call["content"].decode() if "content" in call else json.dumps(call["json"])


def test_an_expired_sign_in_alerts_immediately_and_only_once(sent):
    health.report_failure(cfg(), AuthError("expired"))
    health.report_failure(cfg(), AuthError("expired"))
    health.report_failure(cfg(), AuthError("expired"))

    assert len(sent) == 1
    assert "login" in text(sent[0])
    assert sent[0]["url"] == URL


def test_other_failures_alert_on_the_second_in_a_row(sent):
    health.report_failure(cfg(), RuntimeError("connection reset"))
    assert sent == []

    health.report_failure(cfg(), RuntimeError("connection reset"))
    health.report_failure(cfg(), RuntimeError("connection reset"))

    assert len(sent) == 1
    assert "connection reset" in text(sent[0])


def test_a_success_in_between_resets_the_count(sent):
    health.report_failure(cfg(), RuntimeError("blip"))
    health.report_success(cfg(), {"held_back": []})
    health.report_failure(cfg(), RuntimeError("blip"))

    assert sent == []


def test_a_missing_vault_alerts_immediately(sent):
    health.report_failure(cfg(), VaultNotFoundError("Obsidian vault not found: no folder exists at /x"))

    assert len(sent) == 1
    assert "/x" in text(sent[0])


def test_the_user_is_told_when_it_is_working_again(sent):
    health.report_failure(cfg(), AuthError("expired"))
    health.report_success(cfg(), {"held_back": []})
    health.report_success(cfg(), {"held_back": []})

    assert len(sent) == 2
    assert "working again" in text(sent[1]).lower()


def test_no_recovery_message_when_nothing_was_alerted(sent):
    health.report_failure(cfg(), RuntimeError("blip"))
    health.report_success(cfg(), {"held_back": []})

    assert sent == []


def test_held_back_deletions_alert_once_and_clear(sent):
    held = {"held_back": [{"name": "A", "folder": "Paper"}, {"name": "B", "folder": "Paper"}]}

    health.report_success(cfg(), held)
    health.report_success(cfg(), held)
    assert len(sent) == 1
    assert "2 notebook" in text(sent[0])
    assert "--confirm-deletions" in text(sent[0])
    assert health.load_health()["status"] == "attention"

    health.report_success(cfg(), {"held_back": []})
    assert len(sent) == 2
    assert health.load_health()["status"] == "ok"


def test_nothing_is_sent_without_a_url_but_the_result_is_recorded(sent):
    health.report_failure(cfg(url=""), AuthError("expired"))

    assert sent == []
    state = health.load_health()
    assert state["status"] == "failing" and state["kind"] == "auth"


def test_a_manual_run_records_without_alerting(sent):
    health.report_failure(cfg(), AuthError("expired"), alert=False)
    health.report_success(cfg(), {"held_back": [{"name": "A", "folder": ""}]}, alert=False)

    assert sent == []
    assert health.load_health()["status"] == "attention"


def test_a_failed_delivery_is_retried_next_time_and_never_raises(monkeypatch, capsys):
    attempts = []

    def failing_post(url, **kwargs):
        attempts.append(url)
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(health.httpx, "post", failing_post)

    health.report_failure(cfg(), AuthError("expired"))
    health.report_failure(cfg(), AuthError("expired"))

    assert len(attempts) == 2
    assert "my-secret-topic" not in capsys.readouterr().out


def test_ntfy_style_urls_get_a_plain_text_post_with_a_title(sent):
    health.send_notification(URL, "InkBridge: sign in again", "Run login.")

    call = sent[0]
    assert call["content"] == b"Run login."
    assert call["headers"]["Title"] == "InkBridge: sign in again"


def test_discord_and_slack_webhooks_get_json(sent):
    health.send_notification("https://discord.com/api/webhooks/1/abc", "Title", "Body")
    health.send_notification("https://hooks.slack.com/services/T/B/x", "Title", "Body")

    assert "Title" in sent[0]["json"]["content"] and "Body" in sent[0]["json"]["content"]
    assert "Body" in sent[1]["json"]["text"]


def test_only_http_urls_are_used(sent):
    assert health.send_notification("file:///etc/passwd", "t", "m") is False
    assert health.send_notification("ftp://example.com/x", "t", "m") is False
    assert sent == []


def test_the_health_file_is_private():
    health.report_failure(cfg(), AuthError("expired"))
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(health.HEALTH_FILE).st_mode) == 0o600


def test_status_text_describes_each_state():
    assert "no sync" in health.describe_health({}).lower()

    health.report_failure(cfg(url=""), AuthError("expired"))
    assert "login" in health.describe_health(health.load_health())

    health.report_success(cfg(url=""), {"held_back": []})
    assert "ok" in health.describe_health(health.load_health()).lower()


# --- the daemon --------------------------------------------------------------

class FakeEngine:
    def __init__(self, outcome):
        self.config = Config(token="old")
        self.outcome = outcome
        self.refresh_checks = 0
        self.client = self
        self.ocr = types.SimpleNamespace()
        self.sync_calls = []

    def refresh_if_expiring(self):
        self.refresh_checks += 1

    def sync_all(self, **kwargs):
        self.sync_calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture
def disk_config(monkeypatch):
    on_disk = Config(token="fresh-from-login", notify_url=URL)
    monkeypatch.setattr(companion, "load_config", lambda: on_disk)
    return on_disk


def test_a_daemon_cycle_picks_up_a_token_saved_by_login(disk_config, sent):
    engine = FakeEngine({"held_back": []})

    wait, failures = companion._daemon_cycle(engine, interval=360, failures=0)

    assert engine.config.token == "fresh-from-login"
    assert engine.refresh_checks == 1
    assert (wait, failures) == (360, 0)
    assert health.load_health()["status"] == "ok"


def test_a_failed_cycle_backs_off_and_alerts(disk_config, sent, capsys):
    engine = FakeEngine(RuntimeError("down"))

    wait, failures = companion._daemon_cycle(engine, interval=30, failures=0)
    assert (wait, failures) == (30, 1)
    wait, failures = companion._daemon_cycle(engine, interval=30, failures=failures)
    assert (wait, failures) == (60, 2)

    assert len(sent) == 1


def test_an_auth_failure_is_retried_soon_so_a_new_login_is_noticed(disk_config, sent, capsys):
    engine = FakeEngine(AuthError("expired"))

    wait, failures = companion._daemon_cycle(engine, interval=360, failures=3)

    assert wait == companion.AUTH_RETRY_MINUTES
    assert failures == 4
    assert len(sent) == 1


def test_a_held_back_deletion_in_a_daemon_cycle_alerts(disk_config, sent):
    engine = FakeEngine({"held_back": [{"name": "A", "folder": "Paper"}]})

    companion._daemon_cycle(engine, interval=360, failures=0)

    assert len(sent) == 1
    assert "--confirm-deletions" in text(sent[0])


# --- the dashboard's auto-sync ----------------------------------------------

class ServerEngine(FakeEngine):
    def __init__(self, outcome):
        super().__init__(outcome)
        self.config = Config(token="t", notify_url=URL)


def test_an_unattended_dashboard_sync_alerts_on_failure(monkeypatch, sent, capsys):
    monkeypatch.setattr(server, "get_engine", lambda: ServerEngine(AuthError("expired")))

    server.run_sync_task("all", force=False, unattended=True)

    assert len(sent) == 1
    assert health.load_health()["status"] == "failing"


def test_a_sync_started_from_the_dashboard_records_but_does_not_alert(monkeypatch, sent):
    monkeypatch.setattr(server, "get_engine", lambda: ServerEngine(AuthError("expired")))

    server.run_sync_task("all", force=False)

    assert sent == []
    assert health.load_health()["status"] == "failing"


def test_a_partial_sync_does_not_change_the_recorded_health(monkeypatch, sent):
    health.report_failure(cfg(url=""), AuthError("expired"))
    engine = ServerEngine(0)
    engine.sync_recent_journals = lambda **kw: 0
    monkeypatch.setattr(server, "get_engine", lambda: engine)

    server.run_sync_task("journals", force=False, unattended=True)

    assert health.load_health()["status"] == "failing"


# --- the CLI -----------------------------------------------------------------

def test_status_shows_the_last_sync_result(monkeypatch, capsys):
    health.report_failure(cfg(url=""), AuthError("expired"))
    config_module.save_config(Config(token=""))

    companion.cmd_status()

    out = capsys.readouterr().out
    assert "Last sync" in out and "login" in out


def test_test_notify_sends_a_message(monkeypatch, sent, capsys):
    config_module.save_config(Config(notify_url=URL))

    companion.cmd_test_notify()

    assert len(sent) == 1
    assert "my-secret-topic" not in capsys.readouterr().out


def test_test_notify_explains_when_no_url_is_set(monkeypatch, sent, capsys):
    config_module.save_config(Config())

    with pytest.raises(SystemExit) as exit_info:
        companion.cmd_test_notify()

    assert exit_info.value.code == 1
    assert sent == []
    assert "notify_url" in capsys.readouterr().err


def test_test_notify_is_wired_into_the_cli(monkeypatch):
    called = []
    monkeypatch.setattr(companion, "cmd_test_notify", lambda: called.append(True))
    monkeypatch.setattr(companion.sys, "argv", ["companion.py", "test-notify"])
    companion.main()
    assert called == [True]


# --- the dashboard -------------------------------------------------------------

def test_the_alert_url_is_never_sent_back_to_the_dashboard(monkeypatch):
    from fastapi.testclient import TestClient

    config_module.save_config(Config(token="t", notify_url=URL))
    monkeypatch.delenv(server.ALLOWED_HOSTS_ENV, raising=False)
    client = TestClient(server.app, base_url="http://127.0.0.1:8765")

    body = client.get("/api/config").json()["config"]

    assert "notify_url" not in body
    assert body["has_notify_url"] is True
    assert "my-secret-topic" not in json.dumps(body)
    assert body["notify_url_masked"] == "ntfy.example.com"


def test_the_alert_url_can_be_saved_from_the_dashboard(monkeypatch):
    from fastapi.testclient import TestClient

    config_module.save_config(Config(token="t"))
    monkeypatch.delenv(server.ALLOWED_HOSTS_ENV, raising=False)
    client = TestClient(server.app, base_url="http://127.0.0.1:8765")

    response = client.post("/api/config", json={"notify_url": URL},
                           headers={"Origin": "http://127.0.0.1:8765"})

    assert response.status_code == 200
    assert config_module.load_config().notify_url == URL


def test_the_dashboard_reports_the_real_version(monkeypatch):
    import viwoods

    stub = types.SimpleNamespace(
        client=types.SimpleNamespace(get_devices=lambda: []),
        state={"notes": {}, "last_full_sync": None},
        vault=types.SimpleNamespace(describe_daily_notes=lambda: {"source": "config", "folder": "", "format": "", "template": ""}),
    )
    config_module.save_config(Config(token="t"))
    monkeypatch.setattr(server, "get_engine", lambda: stub)
    monkeypatch.delenv(server.ALLOWED_HOSTS_ENV, raising=False)
    from fastapi.testclient import TestClient

    body = TestClient(server.app, base_url="http://127.0.0.1:8765").get("/api/status").json()

    assert body["version"] == viwoods.__version__
