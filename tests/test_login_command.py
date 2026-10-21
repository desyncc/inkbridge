"""
`companion.py login`: sign in with an email and password (or paste a token)
and store the token, without ever echoing or keeping the secrets.
"""

import pytest

import companion
import viwoods.config as config_module
from viwoods.client import AuthError
from viwoods.config import load_config

PASSWORD = "hunter2-correct-horse"
TOKEN = "eyJ.fake.token-value"


@pytest.fixture(autouse=True)
def config_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.json")


class FakeClient:
    """Stands in for ViwoodsClient; `behaviour` is shared across instances."""

    calls = []
    login_error = None
    devices = [{"modelName": "AiPaper", "machineNumber": "SN123"}]
    devices_error = None

    def __init__(self, config):
        self.config = config

    def login(self, email, password):
        FakeClient.calls.append(("login", email, password))
        if FakeClient.login_error:
            raise FakeClient.login_error
        self.config.token = TOKEN
        return TOKEN

    def get_devices(self):
        FakeClient.calls.append(("get_devices", self.config.token))
        if FakeClient.devices_error:
            raise FakeClient.devices_error
        return FakeClient.devices


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    FakeClient.calls = []
    FakeClient.login_error = None
    FakeClient.devices_error = None
    FakeClient.devices = [{"modelName": "AiPaper", "machineNumber": "SN123"}]
    monkeypatch.setattr(companion, "ViwoodsClient", FakeClient)


def answer(monkeypatch, email="me@example.com", secret=PASSWORD):
    asked = []
    monkeypatch.setattr("builtins.input", lambda prompt="": (asked.append(prompt), email)[1])
    monkeypatch.setattr(companion.getpass, "getpass", lambda prompt="": (asked.append(prompt), secret)[1])
    return asked


def test_signs_in_with_email_and_password_and_stores_the_token(monkeypatch, capsys):
    answer(monkeypatch)

    companion.cmd_login()

    assert ("login", "me@example.com", PASSWORD) in FakeClient.calls
    assert load_config().token == TOKEN
    out = capsys.readouterr()
    assert "AiPaper" in out.out and "SN123" in out.out
    assert PASSWORD not in out.out + out.err
    assert TOKEN not in out.out + out.err


def test_the_password_is_never_saved(monkeypatch):
    answer(monkeypatch)

    companion.cmd_login()

    saved = (config_module.CONFIG_PATH).read_text(encoding="utf-8")
    assert PASSWORD not in saved
    assert "me@example.com" not in saved


def test_an_email_given_on_the_command_line_is_not_asked_for_again(monkeypatch):
    asked = answer(monkeypatch)

    companion.cmd_login(email="given@example.com")

    assert ("login", "given@example.com", PASSWORD) in FakeClient.calls
    assert not any("mail" in prompt.lower() for prompt in asked)


def test_a_failed_sign_in_exits_and_leaves_the_stored_token_alone(monkeypatch, capsys):
    cfg = load_config()
    cfg.token = "old-token"
    config_module.save_config(cfg)
    answer(monkeypatch)
    FakeClient.login_error = RuntimeError("Viwoods API error: wrong password (code: 1001)")

    with pytest.raises(SystemExit) as exit_info:
        companion.cmd_login()

    assert exit_info.value.code == 1
    assert load_config().token == "old-token"
    out = capsys.readouterr()
    assert "wrong password" in out.out + out.err
    assert PASSWORD not in out.out + out.err


@pytest.mark.parametrize("email, secret", [("", PASSWORD), ("me@example.com", "")])
def test_a_blank_email_or_password_is_refused_without_calling_the_api(monkeypatch, email, secret):
    answer(monkeypatch, email=email, secret=secret)

    with pytest.raises(SystemExit):
        companion.cmd_login()

    assert FakeClient.calls == []


def test_a_signed_in_account_with_no_devices_still_succeeds(monkeypatch, capsys):
    answer(monkeypatch)
    FakeClient.devices = []

    companion.cmd_login()

    assert load_config().token == TOKEN
    assert "no tablet" in capsys.readouterr().out.lower()


def test_a_working_pasted_token_is_verified_then_stored(monkeypatch, capsys):
    answer(monkeypatch, secret=f"  {TOKEN}\n")

    companion.cmd_login(use_token=True)

    assert FakeClient.calls == [("get_devices", TOKEN)]
    assert load_config().token == TOKEN
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err


def test_a_rejected_pasted_token_is_not_stored(monkeypatch, capsys):
    cfg = load_config()
    cfg.token = "old-token"
    config_module.save_config(cfg)
    answer(monkeypatch, secret=TOKEN)
    FakeClient.devices_error = AuthError("Viwoods token expired")

    with pytest.raises(SystemExit) as exit_info:
        companion.cmd_login(use_token=True)

    assert exit_info.value.code == 1
    assert load_config().token == "old-token"
    assert TOKEN not in capsys.readouterr().out


def test_a_blank_pasted_token_is_refused(monkeypatch):
    answer(monkeypatch, secret="   ")

    with pytest.raises(SystemExit):
        companion.cmd_login(use_token=True)

    assert FakeClient.calls == []


def test_the_login_command_is_wired_into_the_cli(monkeypatch):
    seen = {}
    monkeypatch.setattr(companion, "cmd_login", lambda **kw: seen.update(kw))
    monkeypatch.setattr(companion.sys, "argv", ["companion.py", "login", "--email", "a@b.c"])
    companion.main()
    assert seen == {"email": "a@b.c", "use_token": False}

    monkeypatch.setattr(companion.sys, "argv", ["companion.py", "login", "--token"])
    companion.main()
    assert seen == {"email": None, "use_token": True}
