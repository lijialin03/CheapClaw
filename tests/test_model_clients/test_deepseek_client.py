import inspect

from cheapclaw import cli
from cheapclaw.model_clients.deepseek import (
    UPLOAD_BUTTON_SELECTOR,
    DeepSeekAdapter,
    DeepSeekClient,
)
from cheapclaw.model_clients.deepseek.client import (
    DeepSeekAdapter as _DeepSeekAdapter,  # for source inspection
)
from cheapclaw.model_clients.selector_config import load_selector_config
from tests.mocks import (
    DummyConfig,
    DummyLogger,
    FakePage,
    FakeSelectorConfig,
    FakeSession,
)

DEEPSEEK_SELECTORS = load_selector_config("deepseek")
LOGIN_STATE_JS = DeepSeekAdapter.load_js("login_state.js")
LATEST_REPLY_JS = DeepSeekAdapter.load_js("latest_reply.js")
ASSISTANT_COUNT_JS = DeepSeekAdapter.load_js("assistant_count.js")
UPLOADED_FILE_CARD_JS = DeepSeekAdapter.load_js("uploaded_file_card.js")


def bind_adapter(page, selector_config=None):
    adapter = DeepSeekAdapter(selector_config=selector_config)
    adapter.bind(
        FakeSession(page),
        DummyConfig(),
        DummyLogger(),
        selector_config=adapter.selector_config,
    )
    return adapter


def test_deepseek_default_url():
    assert DeepSeekAdapter().default_url() == "https://chat.deepseek.com"


def test_deepseek_client_instantiates_without_playwright():
    client = DeepSeekClient(logger=DummyLogger())

    assert isinstance(client.adapter, DeepSeekAdapter)
    assert client.DISPLAY_NAME == "DeepSeek"
    assert client.config.storage_state_path.name == "storage_state_ds.json"


def test_deepseek_client_has_selector_config():
    client = DeepSeekClient(logger=DummyLogger())
    cfg = client.adapter.selector_config
    assert cfg.composer == 'textarea[name="search"]'
    assert "[role='button']" in cfg.send_button
    assert cfg.reply_content == ".ds-assistant-message-main-content"


def test_deepseek_client_accepts_cleanup_session_flag():
    client = DeepSeekClient(logger=DummyLogger(), cleanup_session=True)

    assert client.cleanup_session_on_close is True


def test_login_state_guidance_mentions_export_script_and_path():
    guidance = DeepSeekAdapter().login_state_guidance("config/storage_state_ds.json")

    assert "python -m cheapclaw.scripts.export_state --model deepseek" in guidance
    assert "storage_state_ds.json" in guidance
    assert "config/storage_state_ds.json" in guidance


def test_composer_selector_uses_deepseek_search_textarea():
    cfg = load_selector_config("deepseek")
    assert cfg.composer == 'textarea[name="search"]'
    assert (
        "composers" in LOGIN_STATE_JS
    )  # JS is parameterized, uses selectors.reply_content
    assert "textarea:not" not in cfg.composer
    assert "textarea:not" not in LOGIN_STATE_JS
    assert "placeholder" not in cfg.composer.lower()


def test_send_button_selector_uses_deepseek_role_button_classes():
    cfg = load_selector_config("deepseek")
    assert "[role='button']" in cfg.send_button
    assert "ds-button--primary" in cfg.send_button
    assert "ds-button--circle" in cfg.send_button
    assert ":not(.ds-button--disabled)" in cfg.send_button
    assert "button[aria-label]" not in cfg.send_button
    assert "has-text('发送')" not in cfg.send_button
    assert "has-text('Send')" not in cfg.send_button


def test_upload_button_selector_uses_stable_ds_button_classes():
    assert "[role='button']" in UPLOAD_BUTTON_SELECTOR
    assert "ds-button--iconLabelPrimary" in UPLOAD_BUTTON_SELECTOR
    assert "ds-button--icon" in UPLOAD_BUTTON_SELECTOR
    assert "ds-button--capsule" in UPLOAD_BUTTON_SELECTOR
    assert "ds-button--s" in UPLOAD_BUTTON_SELECTOR
    assert "f02f0e25" not in UPLOAD_BUTTON_SELECTOR


def test_sign_in_url_is_not_logged_in_without_dom_evaluation():
    page = FakePage(url="https://chat.deepseek.com/sign_in", exc=True)
    adapter = bind_adapter(page)

    assert adapter.is_logged_in() is False
    assert page.scripts == []


def test_login_state_with_composer_and_no_auth_buttons_is_logged_in():
    page = FakePage(values=[{"hasComposer": True, "authButtons": []}])
    adapter = bind_adapter(page)

    assert adapter.is_logged_in() is True


def test_login_state_with_composer_ignores_auth_button_text():
    page = FakePage(values=[{"hasComposer": True, "authButtons": ["Sign in"]}])
    adapter = bind_adapter(page)

    assert adapter.is_logged_in() is True


def test_login_state_without_composer_is_not_logged_in():
    page = FakePage(values=[{"hasComposer": False, "authButtons": []}])
    adapter = bind_adapter(page)

    assert adapter.is_logged_in() is False


def test_login_state_js_does_not_match_localized_login_button_text():
    assert "Sign in|Sign up|Log in|Login" not in LOGIN_STATE_JS
    assert "登录|注册|登入|立即登录" not in LOGIN_STATE_JS


def test_login_state_js_is_valid_javascript(page):
    page.set_content('<textarea name="search"></textarea>')

    state = page.evaluate(LOGIN_STATE_JS, DEEPSEEK_SELECTORS.model_dump())

    assert state["hasComposer"] is True


def test_wait_until_ready_waits_for_composer_selector():
    page = FakePage()
    cfg = load_selector_config("deepseek")
    adapter = bind_adapter(page, selector_config=cfg)

    adapter.wait_until_ready()

    assert page.waited_selectors == [(cfg.composer, {"timeout": 1000})]
    assert page.waited_timeouts == [1000]


def test_current_conversation_id_extracts_deepseek_chat_id():
    adapter = bind_adapter(
        FakePage(url="https://chat.deepseek.com/a/chat/s/abc123?foo=bar")
    )

    assert adapter.current_conversation_id() == "abc123"


def test_current_conversation_id_returns_none_without_chat_id():
    adapter = bind_adapter(FakePage(url="https://chat.deepseek.com/"))

    assert adapter.current_conversation_id() is None


def test_after_message_sent_records_new_conversation_id():
    logger = DummyLogger()
    adapter = DeepSeekAdapter()
    adapter.bind(
        FakeSession(FakePage(url="https://chat.deepseek.com/a/chat/s/new-id")),
        DummyConfig(),
        logger,
    )

    adapter.after_message_sent()

    assert adapter.conversation_id == "new-id"
    assert logger.debug_messages[-1] == "DeepSeek 当前会话 ID: new-id"


def test_after_message_sent_ignores_missing_or_unchanged_conversation_id():
    logger = DummyLogger()
    adapter = DeepSeekAdapter()
    adapter.bind(
        FakeSession(FakePage(url="https://chat.deepseek.com/")), DummyConfig(), logger
    )

    adapter.after_message_sent()

    assert adapter.conversation_id is None
    assert logger.debug_messages == []

    adapter.session.page.url = "https://chat.deepseek.com/a/chat/s/same-id"
    adapter.after_message_sent()
    adapter.after_message_sent()

    assert adapter.conversation_id == "same-id"
    assert logger.debug_messages == ["DeepSeek 当前会话 ID: same-id"]


def test_cleanup_session_skips_without_recorded_conversation_id():
    logger = DummyLogger()
    adapter = DeepSeekAdapter()
    adapter.bind(FakeSession(FakePage()), DummyConfig(), logger)

    adapter.cleanup_session()

    assert logger.debug_messages == ["DeepSeek 无已记录会话 ID，跳过会话清理"]
    assert logger.warning_messages == []


def test_cleanup_session_warns_when_ui_delete_fails():
    logger = DummyLogger()
    adapter = DeepSeekAdapter()
    adapter.conversation_id = "missing-id"
    adapter.bind(FakeSession(FakePage()), DummyConfig(), logger)

    adapter.cleanup_session()

    assert logger.warning_messages
    assert "DeepSeek 会话清理失败" in logger.warning_messages[-1]


def test_delete_conversation_selectors_do_not_depend_on_localized_text():
    source = inspect.getsource(_DeepSeekAdapter.delete_recorded_conversation)
    confirm_source = inspect.getsource(
        _DeepSeekAdapter._confirm_delete_conversation_if_needed
    )

    assert "has_text" not in source
    assert "has-text" not in confirm_source
    cfg = load_selector_config("deepseek")
    assert ".ds-dropdown-menu-option--error" in cfg.conversation_delete_option
    assert ".ds-button--error" in cfg.confirm_dialog_delete_button


def test_latest_reply_text_strips_and_fails_closed():
    assert bind_adapter(FakePage(values=["  answer  "])).latest_reply_text() == "answer"
    assert bind_adapter(FakePage(exc=True)).latest_reply_text() == ""


def test_latest_reply_js_uses_deepseek_assistant_content_and_removes_citations(page):
    page.set_content(
        """
        <div class="ds-markdown ds-assistant-message-main-content">
            <p>old reply</p>
        </div>
        <div class="ds-markdown ds-assistant-message-main-content">
            <p>Hello<span class="ds-markdown-cite">8</span> world</p>
        </div>
        <span class="ds-markdown-cite">9</span>
        """
    )

    assert (
        page.evaluate(LATEST_REPLY_JS, DEEPSEEK_SELECTORS.model_dump()) == "Hello world"
    )
    assert page.evaluate(ASSISTANT_COUNT_JS, DEEPSEEK_SELECTORS.model_dump()) == 2


def test_assistant_count_js_uses_virtual_list_key_when_available(page):
    page.set_content(
        """
        <div data-virtual-list-item-key="3">
            <div class="ds-message">
                <div class="ds-markdown ds-assistant-message-main-content">old</div>
            </div>
        </div>
        <div data-virtual-list-item-key="8">
            <div class="ds-message">
                <div class="ds-markdown ds-assistant-message-main-content">new</div>
            </div>
        </div>
        """
    )

    assert page.evaluate(ASSISTANT_COUNT_JS, DEEPSEEK_SELECTORS.model_dump()) == 8


def test_uploaded_file_card_js_prefers_stable_deepseek_card_container(page):
    page.set_content(
        """
        <div>dismiss.txt in unrelated page text</div>
        <div class="ds-animated-size-item">
            <div tabindex="0">
                <div>
                    <div>cheapclaw_upload_test.txt</div>
                    <div>TXT 32B</div>
                </div>
            </div>
        </div>
        """
    )

    assert (
        page.evaluate(
            UPLOADED_FILE_CARD_JS,
            [DEEPSEEK_SELECTORS.model_dump(), "cheapclaw_upload_test.txt"],
        )
        is True
    )
    assert (
        page.evaluate(
            UPLOADED_FILE_CARD_JS, [DEEPSEEK_SELECTORS.model_dump(), "missing.txt"]
        )
        is False
    )


def test_generation_and_completion_fail_closed_sensibly():
    assert bind_adapter(FakePage(values=[True])).is_generation_in_progress() is True
    assert bind_adapter(FakePage(exc=True)).is_generation_in_progress() is False
    assert bind_adapter(FakePage(values=[False])).is_reply_complete() is True
    assert (
        bind_adapter(FakePage(values=[RuntimeError("boom")])).is_reply_complete()
        is True
    )


def test_deepseek_registered_in_cli_model_clients():
    assert cli.MODEL_CLIENTS["deepseek"] is DeepSeekClient
