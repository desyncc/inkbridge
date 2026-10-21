"""
`doctor`: one report that says what is wrong with an install and how to fix
it, safe to paste into an issue.
"""

import os
import time

import httpx
import pytest

import companion
import viwoods.config as config_module
import viwoods.doctor as doctor
import viwoods.health as health
from viwoods.client import AuthError
from viwoods.config import Config

TOKEN = "eyJ-secret-token-value-123456"
GEMINI_KEY = "AIzaSy-secret-gemini-key"
NOTIFY = "https://ntfy.sh/very-secret-topic-name"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(health, "HEALTH_FILE", tmp_path / "health.json")
    monkeypatch.setattr(doctor, "HOME", tmp_path)


@pytest.fixture
def vault(tmp_path):
    path = tmp_path / "vault"
    (path / ".obsidian").mkdir(parents=True)
    return path


class Cloud:
    """Stands in for ViwoodsClient."""
    devices = [{"modelName": "AiPaper", "machineNumber": "SN1"}]
    error = None

    def __init__(self, config):
        self.config = config

    def get_devices(self):
        if Cloud.error:
            raise Cloud.error
        return Cloud.devices


class Servers:
    """Fake HTTP for the local model servers; `models` is what each one has pulled."""

    def __init__(self):
        self.ollama_models = ["qwen3.5:9b"]
        self.lmstudio_models = ["qwen2.5-vl-7b-instruct"]
        self.down = False
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        if self.down:
            raise httpx.ConnectError("refused")
        if url.endswith("/api/tags"):
            return httpx.Response(200, json={"models": [{"name": n} for n in self.ollama_models]},
                                  request=httpx.Request("GET", url))
        if url.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": n} for n in self.lmstudio_models]},
                                  request=httpx.Request("GET", url))
        return httpx.Response(404, request=httpx.Request("GET", url))


@pytest.fixture
def servers(monkeypatch):
    fake = Servers()
    monkeypatch.setattr(doctor.httpx, "get", fake.get)
    return fake


@pytest.fixture(autouse=True)
def cloud(monkeypatch):
    Cloud.error = None
    Cloud.devices = [{"modelName": "AiPaper", "machineNumber": "SN1"}]
    monkeypatch.setattr(doctor, "ViwoodsClient", Cloud)


def good_config(vault, **overrides):
    values = dict(token=TOKEN, vault_path=str(vault), ocr_engine="ollama", ollama_model="qwen3.5:9b")
    values.update(overrides)
    config_module.save_config(Config(**values))


def check(checks, name):
    matches = [c for c in checks if c.name == name]
    assert matches, f"no check named {name!r}: {[c.name for c in checks]}"
    return matches[0]


def test_a_healthy_install_has_no_problems(vault, servers):
    good_config(vault)

    checks = doctor.run_checks()

    assert not doctor.has_failures(checks), doctor.render(checks)
    assert check(checks, "Viwoods account").status == doctor.OK
    assert check(checks, "Ollama").status == doctor.OK


def test_a_missing_token_says_to_log_in(vault, servers):
    good_config(vault, token="")

    c = check(doctor.run_checks(), "Viwoods account")

    assert c.status == doctor.FAIL
    assert "login" in c.fix


def test_a_rejected_token_says_to_log_in_not_that_the_network_is_down(vault, servers):
    good_config(vault)
    Cloud.error = AuthError("expired")

    c = check(doctor.run_checks(), "Viwoods account")

    assert c.status == doctor.FAIL and "login" in c.fix


def test_a_network_error_is_not_blamed_on_the_login(vault, servers):
    good_config(vault)
    Cloud.error = httpx.ConnectError("no route to host")

    c = check(doctor.run_checks(), "Viwoods account")

    assert c.status == doctor.FAIL
    assert "login" not in c.fix
    assert "internet" in (c.detail + c.fix).lower()


def test_an_account_with_no_tablet_is_a_warning(vault, servers):
    good_config(vault)
    Cloud.devices = []

    assert check(doctor.run_checks(), "Viwoods account").status == doctor.WARN


def _jwt(days):
    import base64
    import json

    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{part({'alg': 'x'})}.{part({'exp': int(time.time() + days * 86400)})}.sig"


@pytest.mark.parametrize("days, status", [(300, doctor.OK), (10, doctor.OK), (-2, doctor.FAIL)])
def test_token_expiry_is_reported(vault, servers, days, status):
    good_config(vault, token=_jwt(days))
    if days < 0:
        Cloud.error = AuthError("expired")

    checks = doctor.run_checks()

    c = check(checks, "Sign-in token")
    assert c.status in (status, doctor.INFO, doctor.WARN)
    assert ("expire" in c.detail.lower())


def test_a_missing_vault_is_a_failure(tmp_path, servers):
    good_config(tmp_path / "nope")

    c = check(doctor.run_checks(), "Obsidian vault")

    assert c.status == doctor.FAIL
    assert "vault_path" in c.fix


def test_the_placeholder_vault_path_is_called_out(servers):
    config_module.save_config(Config(token=TOKEN))

    c = check(doctor.run_checks(), "Obsidian vault")

    assert c.status == doctor.FAIL and "placeholder" in c.detail.lower()


def test_a_folder_that_is_not_an_obsidian_vault_is_a_warning(tmp_path, servers):
    plain = tmp_path / "plain"
    plain.mkdir()
    good_config(plain)

    assert check(doctor.run_checks(), "Obsidian vault").status == doctor.WARN


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs a non-root POSIX user")
def test_a_read_only_vault_is_a_failure(vault, servers):
    good_config(vault)
    os.chmod(vault, 0o500)
    try:
        assert check(doctor.run_checks(), "Obsidian vault").status == doctor.FAIL
    finally:
        os.chmod(vault, 0o700)


def test_ollama_not_running_says_how_to_start_it(vault, servers):
    good_config(vault)
    servers.down = True

    c = check(doctor.run_checks(), "Ollama")

    assert c.status == doctor.FAIL
    assert "ollama serve" in c.fix


def test_a_model_that_is_not_pulled_says_how_to_pull_it(vault, servers):
    good_config(vault, ollama_model="qwen3.5:4b")

    c = check(doctor.run_checks(), "Ollama")

    assert c.status == doctor.FAIL
    assert "ollama pull qwen3.5:4b" in c.fix


def test_a_bare_model_name_matches_its_latest_tag(vault, servers):
    servers.ollama_models = ["llava:latest"]
    good_config(vault, ollama_model="llava")

    assert check(doctor.run_checks(), "Ollama").status == doctor.OK


def test_lm_studio_model_is_checked(vault, servers):
    good_config(vault, ocr_engine="lmstudio", lmstudio_model="missing-model")

    c = check(doctor.run_checks(), "LM Studio")

    assert c.status == doctor.FAIL


def test_gemini_needs_a_key_but_the_key_is_never_shown(vault, servers):
    good_config(vault, ocr_engine="gemini", gemini_api_key="")
    assert check(doctor.run_checks(), "Gemini").status == doctor.FAIL

    good_config(vault, ocr_engine="gemini", gemini_api_key=GEMINI_KEY)
    checks = doctor.run_checks()
    assert check(checks, "Gemini").status == doctor.OK
    assert GEMINI_KEY not in doctor.render(checks)


@pytest.mark.skipif(os.name == "nt", reason="the Windows engine works there")
def test_the_windows_engine_is_a_failure_elsewhere(vault, servers):
    good_config(vault, ocr_engine="windows")

    assert check(doctor.run_checks(), "Windows OCR").status == doctor.FAIL


def test_a_failing_last_sync_is_reported(vault, servers):
    good_config(vault)
    health.report_failure(Config(), RuntimeError("disk full"), alert=False)

    c = check(doctor.run_checks(), "Last sync")

    assert c.status == doctor.INFO or c.status == doctor.WARN
    assert "disk full" in c.detail


def test_alerts_off_is_information_not_a_problem(vault, servers):
    good_config(vault)

    assert check(doctor.run_checks(), "Alerts").status == doctor.INFO


def test_a_broken_config_file_is_one_clear_failure(tmp_path, servers):
    (tmp_path / "config.json").write_text("{ not json", encoding="utf-8")

    checks = doctor.run_checks()

    c = check(checks, "Config file")
    assert c.status == doctor.FAIL and "not valid JSON" in c.detail
    assert check(checks, "Python").status == doctor.OK   # the rest still ran


def test_one_check_crashing_does_not_stop_the_report(vault, servers, monkeypatch):
    good_config(vault)

    def boom(cfg):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(doctor, "check_vault", boom)

    checks = doctor.run_checks()

    assert any(c.status == doctor.FAIL and "unexpected" in c.detail for c in checks)
    assert check(checks, "Python").status == doctor.OK


def test_the_report_contains_no_secrets_and_hides_the_home_directory(vault, servers, tmp_path):
    good_config(vault, gemini_api_key=GEMINI_KEY, notify_url=NOTIFY)

    text = doctor.render(doctor.run_checks())

    for secret in (TOKEN, GEMINI_KEY, NOTIFY, "very-secret-topic-name"):
        assert secret not in text
    assert str(tmp_path) not in text
    assert "~" in text
    assert "safe to paste" in text.lower()


def test_the_summary_counts_problems_and_warnings(vault, servers):
    good_config(vault, token="")

    text = doctor.render(doctor.run_checks())

    assert "problem" in text.lower().splitlines()[-1]


def test_the_command_exits_non_zero_only_when_something_is_broken(vault, servers, capsys):
    good_config(vault)
    companion.cmd_doctor()   # healthy: returns normally

    good_config(vault, token="")
    with pytest.raises(SystemExit) as exit_info:
        companion.cmd_doctor()
    assert exit_info.value.code == 1
    assert "FAIL" in capsys.readouterr().out


def test_doctor_is_wired_into_the_cli(monkeypatch):
    called = []
    monkeypatch.setattr(companion, "cmd_doctor", lambda: called.append(True))
    monkeypatch.setattr(companion.sys, "argv", ["companion.py", "doctor"])
    companion.main()
    assert called == [True]
