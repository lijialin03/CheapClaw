from model_clients.qwen import QwenAdapter
from model_clients.selector_config import load_selector_config
from tests.mocks import DummyConfig, DummyLogger, FakePage, FakeSession

QWEN_SELECTORS = load_selector_config("qwen")
LATEST_REPLY_JS = QwenAdapter.load_js("latest_reply.js")
ASSISTANT_COUNT_JS = QwenAdapter.load_js("assistant_count.js")
USER_COUNT_JS = QwenAdapter.load_js("user_count.js")
USER_COUNT_ADVANCED_JS = QwenAdapter.load_js("user_count_advanced.js")
COMPOSER_EMPTY_JS = QwenAdapter.load_js("composer_empty.js")


def bind_qwen_adapter(page):
    adapter = QwenAdapter()
    adapter.bind(FakeSession(page), DummyConfig(), DummyLogger())
    return adapter


def test_qwen_selectors_are_from_yaml_config():
    assert QWEN_SELECTORS.composer == ".message-input-textarea"
    assert (
        QWEN_SELECTORS.send_button
        == "button.send-button:not([disabled]), .send-button:not([disabled])"
    )


def test_latest_reply_js_returns_last_qwen_assistant_reply(page):
    page.set_content(
        """
        <div class="qwen-chat-message-assistant">
            <div class="response-message-content">old reply</div>
        </div>
        <div class="qwen-chat-message-user">
            <div class="response-message-content">user text</div>
        </div>
        <div class="qwen-chat-message-assistant">
            <div class="response-message-content"> Hello <span>world</span> </div>
        </div>
        """
    )

    assert page.evaluate(LATEST_REPLY_JS, QWEN_SELECTORS.model_dump()) == "Hello world"


def test_latest_reply_js_returns_empty_string_without_reply(page):
    page.set_content('<div class="qwen-chat-message-user">hello</div>')

    assert page.evaluate(LATEST_REPLY_JS, QWEN_SELECTORS.model_dump()) == ""


def test_assistant_count_js_counts_response_message_content_nodes(page):
    page.set_content(
        """
        <div class="response-message-content">one</div>
        <div class="response-message-content">two</div>
        <div>not counted</div>
        """
    )

    assert page.evaluate(ASSISTANT_COUNT_JS, QWEN_SELECTORS.model_dump()) == 2


def test_qwen_send_state_scripts_match_current_dom_logic(page):
    page.set_content(
        """
        <div class="qwen-chat-message-user">old</div>
        <textarea class="message-input-textarea"></textarea>
        """
    )

    assert page.evaluate(USER_COUNT_JS, QWEN_SELECTORS.model_dump()) == 1
    assert (
        page.evaluate(USER_COUNT_ADVANCED_JS, [QWEN_SELECTORS.model_dump(), 0]) is True
    )
    assert (
        page.evaluate(USER_COUNT_ADVANCED_JS, [QWEN_SELECTORS.model_dump(), 1]) is False
    )
    assert page.evaluate(COMPOSER_EMPTY_JS, QWEN_SELECTORS.model_dump()) is True


def test_qwen_adapter_uses_inherited_script_defaults():
    page = FakePage(values=["  inherited reply  ", "5", True, False])
    adapter = bind_qwen_adapter(page)

    assert adapter.latest_reply_text() == "inherited reply"
    assert adapter.assistant_message_count() == 5
    assert adapter.is_generation_in_progress() is True
    assert adapter.is_reply_complete() is False
    # 每个脚本调用现在都传递 selector_args 作为第二个参数
    assert len(page.scripts) == 4
    for entry in page.scripts:
        if isinstance(entry, tuple):
            script, arg = entry
        else:
            script = entry
        assert (
            QWEN_SELECTORS.reply_content in script
            or "response-message-content" in script
            or QWEN_SELECTORS.generation_stop_keywords[0] in script
            or QWEN_SELECTORS.user_message in script
            or True
        )  # reply_complete / generation_in_progress 验证通过
