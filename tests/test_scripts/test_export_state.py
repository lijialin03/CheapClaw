import json
import sys
from pathlib import Path

import pytest

import cheapclaw.scripts.export_state as export_state


class FakePage:
    def __init__(self, status=None):
        self.status = status or {
            "href": export_state.DEEPSEEK_URL,
            "isSignInUrl": False,
            "hasComposer": True,
            "authButtons": [],
        }
        self.scripts = []
        self.goto_args = None
        self.init_scripts = []
        self.waits = []

    def evaluate(self, script):
        self.scripts.append(script)
        return self.status

    def add_init_script(self, script):
        self.init_scripts.append(script)

    def goto(self, *args, **kwargs):
        self.goto_args = (args, kwargs)

    def wait_for_timeout(self, timeout):
        self.waits.append(timeout)


class FakeContext:
    def __init__(self, status=None, raise_indexed_db=False):
        self.page = FakePage(status=status)
        self.pages = [self.page]
        self.kwargs = None
        self.closed = False
        self.raise_indexed_db = raise_indexed_db
        self.storage_calls = []

    def new_page(self):
        self.pages.append(self.page)
        return self.page

    def storage_state(self, **kwargs):
        self.storage_calls.append(kwargs)
        if kwargs.get("indexed_db") and self.raise_indexed_db:
            raise TypeError("indexed_db unsupported")
        path = Path(kwargs["path"])
        path.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, context):
        self.context = context
        self.launch_kwargs = None

    def launch_persistent_context(self, **kwargs):
        self.launch_kwargs = kwargs
        self.context.kwargs = kwargs
        return self.context


class FakePlaywrightManager:
    def __init__(self, context):
        self.context = context
        self.chromium = FakeChromium(context)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_parse_args_default_model(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["export_state.py"])

    args = export_state.parse_args()

    assert args.model == "qwen"
    assert args.output is None
    assert args.user_data_dir is None
    assert args.timeout == 300
    assert args.browser is None


def test_parse_args_explicit_model_and_overrides(monkeypatch, tmp_path):
    output = tmp_path / "state.json"
    profile = tmp_path / "profile"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "export_state.py",
            "--model",
            "deepseek",
            "--output",
            str(output),
            "--user-data-dir",
            str(profile),
            "--timeout",
            "5",
            "--browser",
            "/bin/browser",
        ],
    )

    args = export_state.parse_args()

    assert args.model == "deepseek"
    assert args.output == str(output)
    assert args.user_data_dir == str(profile)
    assert args.timeout == 5
    assert args.browser == "/bin/browser"


def test_model_registry_contains_supported_models():
    assert set(export_state.EXPORT_MODELS) == {"qwen", "deepseek"}

    qwen = export_state.EXPORT_MODELS["qwen"]
    assert qwen.url == "https://chat.qwen.ai/"
    assert qwen.default_output.as_posix().endswith("config/storage_state_qwen.json")
    assert qwen.default_user_data_dir.as_posix().endswith("config/login_profile_qwen")

    deepseek = export_state.EXPORT_MODELS["deepseek"]
    assert deepseek.url == "https://chat.deepseek.com"
    assert deepseek.default_output.as_posix().endswith("config/storage_state_ds.json")
    assert deepseek.default_user_data_dir.as_posix().endswith("config/login_profile_ds")


def test_deepseek_login_js_uses_dom_without_fetch():
    page = FakePage()
    status = export_state.EXPORT_MODELS["deepseek"].check_login(page)

    assert status["hasComposer"] is True
    script = page.scripts[0]
    assert "sign_in" in script
    assert "hasComposer" in script
    assert "authButtons" in script
    assert "fetch(" not in script


def test_qwen_login_js_uses_auth_fetch():
    page = FakePage(status={"authStatus": 200})
    status = export_state.EXPORT_MODELS["qwen"].check_login(page)

    assert status["authStatus"] == 200
    script = page.scripts[0]
    assert "/api/v1/auths/" in script
    assert "fetch(" in script


def test_summarize_storage_state_reports_keys_not_values(tmp_path, capsys):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "domain": ".deepseek.com",
                        "name": "session",
                        "value": "secret-cookie",
                    }
                ],
                "origins": [
                    {
                        "origin": "https://chat.deepseek.com",
                        "localStorage": [{"name": "token", "value": "secret-token"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    export_state.summarize_storage_state(
        state_path, export_state.EXPORT_MODELS["deepseek"]
    )
    out = capsys.readouterr().out

    assert "cookies_count=1" in out
    assert "session" in out
    assert "deepseek_localStorage_count=1" in out
    assert "token" in out
    assert "secret-cookie" not in out
    assert "secret-token" not in out


def test_main_success_path_for_selected_model(monkeypatch, tmp_path):
    output = tmp_path / "ds_state.json"
    profile = tmp_path / "profile"
    context = FakeContext()
    manager = FakePlaywrightManager(context)
    summarized = []

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "export_state.py",
            "--model",
            "deepseek",
            "--output",
            str(output),
            "--user-data-dir",
            str(profile),
        ],
    )
    monkeypatch.setattr(export_state, "sync_playwright", lambda: manager)
    monkeypatch.setattr(
        export_state,
        "summarize_storage_state",
        lambda path, spec: summarized.append((path, spec.model)),
    )

    export_state.main()

    assert manager.chromium.launch_kwargs["headless"] is False
    assert (
        "--disable-blink-features=AutomationControlled"
        in manager.chromium.launch_kwargs["args"]
    )
    assert context.page.goto_args[0][0] == export_state.DEEPSEEK_URL
    assert context.storage_calls[0]["indexed_db"] is True
    assert output.exists()
    assert summarized == [(output.resolve(), "deepseek")]
    assert context.closed is True


def test_main_falls_back_when_indexed_db_is_unsupported(monkeypatch, tmp_path):
    output = tmp_path / "ds_state.json"
    profile = tmp_path / "profile"
    context = FakeContext(raise_indexed_db=True)
    manager = FakePlaywrightManager(context)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "export_state.py",
            "--model",
            "deepseek",
            "--output",
            str(output),
            "--user-data-dir",
            str(profile),
        ],
    )
    monkeypatch.setattr(export_state, "sync_playwright", lambda: manager)
    monkeypatch.setattr(
        export_state, "summarize_storage_state", lambda path, spec: None
    )

    export_state.main()

    assert len(context.storage_calls) == 2
    assert context.storage_calls[0]["indexed_db"] is True
    assert "indexed_db" not in context.storage_calls[1]
    assert context.closed is True


def test_main_timeout_closes_context(monkeypatch, tmp_path):
    output = tmp_path / "ds_state.json"
    profile = tmp_path / "profile"
    context = FakeContext(
        status={"isSignInUrl": True, "hasComposer": False, "authButtons": ["Sign in"]}
    )
    manager = FakePlaywrightManager(context)
    times = iter([0, 2])

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "export_state.py",
            "--model",
            "deepseek",
            "--output",
            str(output),
            "--user-data-dir",
            str(profile),
            "--timeout",
            "1",
        ],
    )
    monkeypatch.setattr(export_state, "sync_playwright", lambda: manager)
    monkeypatch.setattr(export_state.time, "time", lambda: next(times))

    with pytest.raises(TimeoutError):
        export_state.main()

    assert context.closed is True
