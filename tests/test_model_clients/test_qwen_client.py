from model_clients.qwen import COMPOSER_SELECTOR, SEND_BUTTON_SELECTOR, QwenAdapter
from tests.mocks import DummyConfig, DummyLogger, FakePage, FakeSession

LATEST_REPLY_JS = QwenAdapter.load_js("latest_reply.js")
ASSISTANT_COUNT_JS = QwenAdapter.load_js("assistant_count.js")
USER_COUNT_JS = QwenAdapter.load_js("user_count.js")
USER_COUNT_ADVANCED_JS = QwenAdapter.load_js("user_count_advanced.js")
COMPOSER_EMPTY_JS = QwenAdapter.load_js("composer_empty.js")


def bind_qwen_adapter(page):
    adapter = QwenAdapter()
    adapter.bind(FakeSession(page), DummyConfig(), DummyLogger())
    return adapter


def test_qwen_selectors_are_named_constants():
    assert COMPOSER_SELECTOR == ".message-input-textarea"
    assert (
        SEND_BUTTON_SELECTOR
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

    assert page.evaluate(LATEST_REPLY_JS) == "Hello world"


def test_latest_reply_js_returns_empty_string_without_reply(page):
    page.set_content('<div class="qwen-chat-message-user">hello</div>')

    assert page.evaluate(LATEST_REPLY_JS) == ""


def test_assistant_count_js_counts_response_message_content_nodes(page):
    page.set_content(
        """
        <div class="response-message-content">one</div>
        <div class="response-message-content">two</div>
        <div>not counted</div>
        """
    )

    assert page.evaluate(ASSISTANT_COUNT_JS) == 2


def test_qwen_send_state_scripts_match_current_dom_logic(page):
    page.set_content(
        """
        <div class="qwen-chat-message-user">old</div>
        <textarea class="message-input-textarea"></textarea>
        """
    )

    assert page.evaluate(USER_COUNT_JS) == 1
    assert page.evaluate(USER_COUNT_ADVANCED_JS, 0) is True
    assert page.evaluate(USER_COUNT_ADVANCED_JS, 1) is False
    assert page.evaluate(COMPOSER_EMPTY_JS) is True


def test_qwen_adapter_uses_inherited_script_defaults():
    page = FakePage(values=["  inherited reply  ", "5", True, False])
    adapter = bind_qwen_adapter(page)

    assert adapter.latest_reply_text() == "inherited reply"
    assert adapter.assistant_message_count() == 5
    assert adapter.is_generation_in_progress() is True
    assert adapter.is_reply_complete() is False
    assert page.scripts == [
        LATEST_REPLY_JS,
        ASSISTANT_COUNT_JS,
        QwenAdapter.load_js(QwenAdapter.GENERATION_IN_PROGRESS_SCRIPT),
        QwenAdapter.load_js(QwenAdapter.REPLY_COMPLETE_SCRIPT),
    ]
