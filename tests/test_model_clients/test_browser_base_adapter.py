from pathlib import Path

from cheapclaw.model_clients.browser_base import BrowserFrontendAdapter
from tests.mocks import (
    DummyConfig,
    DummyLogger,
    FakeKeyboard,
    FakeLocator,
    FakePage,
    FakeSelectorConfig,
    FakeSession,
)


class MinimalAdapter(BrowserFrontendAdapter):
    def default_url(self) -> str:
        return "https://example.test"

    def is_logged_in(self) -> bool:
        return True

    def wait_until_ready(self) -> None:
        pass

    def upload_file(self, file_path: Path) -> None:
        pass


def bind_adapter(page, selector_config=None, **scripts):
    adapter = MinimalAdapter()
    for name, value in scripts.items():
        setattr(adapter, name, value)
    adapter._script_text = lambda s: s
    if selector_config is None:
        selector_config = FakeSelectorConfig()
    adapter.bind(
        FakeSession(page),
        DummyConfig(),
        DummyLogger(),
        selector_config=selector_config,
    )
    return adapter


def test_latest_reply_text_uses_configured_script_and_strips():
    page = FakePage(values=["  answer  "])
    adapter = bind_adapter(page, LATEST_REPLY_SCRIPT="latest")

    assert adapter.latest_reply_text() == "answer"
    assert page.scripts and page.scripts[0][0] == "latest"


def test_latest_reply_text_missing_or_exception_returns_empty_string():
    assert bind_adapter(FakePage()).latest_reply_text() == ""
    assert (
        bind_adapter(
            FakePage(exc=True), LATEST_REPLY_SCRIPT="latest"
        ).latest_reply_text()
        == ""
    )


def test_assistant_message_count_converts_numbers_and_numeric_strings():
    assert (
        bind_adapter(
            FakePage(values=[3]), ASSISTANT_COUNT_SCRIPT="count"
        ).assistant_message_count()
        == 3
    )
    assert (
        bind_adapter(
            FakePage(values=["4"]), ASSISTANT_COUNT_SCRIPT="count"
        ).assistant_message_count()
        == 4
    )


def test_assistant_message_count_missing_non_int_or_exception_returns_none():
    assert bind_adapter(FakePage()).assistant_message_count() is None
    assert (
        bind_adapter(
            FakePage(values=["not-int"]), ASSISTANT_COUNT_SCRIPT="count"
        ).assistant_message_count()
        is None
    )
    assert (
        bind_adapter(
            FakePage(exc=True), ASSISTANT_COUNT_SCRIPT="count"
        ).assistant_message_count()
        is None
    )


def test_is_generation_in_progress_converts_truthy_and_falsy_values():
    assert (
        bind_adapter(
            FakePage(values=[1]), GENERATION_IN_PROGRESS_SCRIPT="generating"
        ).is_generation_in_progress()
        is True
    )
    assert (
        bind_adapter(
            FakePage(values=[0]), GENERATION_IN_PROGRESS_SCRIPT="generating"
        ).is_generation_in_progress()
        is False
    )


def test_is_generation_in_progress_missing_or_exception_returns_false():
    assert bind_adapter(FakePage()).is_generation_in_progress() is False
    assert (
        bind_adapter(
            FakePage(exc=True), GENERATION_IN_PROGRESS_SCRIPT="generating"
        ).is_generation_in_progress()
        is False
    )


def test_is_reply_complete_uses_configured_reply_complete_script():
    page = FakePage(values=[True])
    adapter = bind_adapter(
        page,
        REPLY_COMPLETE_SCRIPT="complete",
        GENERATION_IN_PROGRESS_SCRIPT="generating",
    )

    assert adapter.is_reply_complete() is True
    assert page.scripts and page.scripts[0][0] == "complete"


def test_is_reply_complete_falls_back_to_inverse_generation_state():
    assert (
        bind_adapter(
            FakePage(values=[False]), GENERATION_IN_PROGRESS_SCRIPT="generating"
        ).is_reply_complete()
        is True
    )
    assert (
        bind_adapter(
            FakePage(values=[True]), GENERATION_IN_PROGRESS_SCRIPT="generating"
        ).is_reply_complete()
        is False
    )


def test_is_reply_complete_without_scripts_returns_false():
    assert bind_adapter(FakePage()).is_reply_complete() is False


def test_wait_until_sendable_uses_send_button_selector():
    page = FakePage()
    cfg = FakeSelectorConfig(send_button="send")
    adapter = bind_adapter(page, selector_config=cfg)

    adapter.wait_until_sendable(timeout=123)

    assert page.waited_selectors == [("send", {"state": "visible", "timeout": 123})]


def test_wait_until_sendable_uses_fallback_selector_after_send_button_failure():
    page = FakePage(wait_selector_failures={"send"})
    cfg = FakeSelectorConfig(send_button="send", sendable_fallback="composer")
    adapter = bind_adapter(page, selector_config=cfg)

    adapter.wait_until_sendable()

    assert page.waited_selectors == [
        ("send", {"state": "visible", "timeout": 1000}),
        ("composer", {"state": "visible", "timeout": 1000}),
    ]


def test_user_message_count_uses_configured_script_and_falls_back_to_zero():
    assert (
        bind_adapter(
            FakePage(values=["2"]), USER_COUNT_SCRIPT="users"
        )._user_message_count()
        == 2
    )
    assert (
        bind_adapter(
            FakePage(exc=True), USER_COUNT_SCRIPT="users"
        )._user_message_count()
        == 0
    )
    assert bind_adapter(FakePage())._user_message_count() == 0


def test_is_sent_prefers_user_count_advanced_script():
    page = FakePage()
    cfg = FakeSelectorConfig()
    adapter = bind_adapter(
        page,
        selector_config=cfg,
        USER_COUNT_ADVANCED_SCRIPT="advanced",
        COMPOSER_EMPTY_SCRIPT="empty",
    )

    assert adapter._is_sent(previous_user_message_count=3) is True
    assert len(page.waited_functions) == 1
    assert page.waited_functions[0][0] == "advanced"
    assert page.waited_functions[0][1]["timeout"] == 8000
    assert page.waited_functions[0][1]["arg"][1] == 3


def test_is_sent_falls_back_to_composer_empty_for_text_messages():
    page = FakePage(wait_function_failures={"advanced"})
    cfg = FakeSelectorConfig()
    adapter = bind_adapter(
        page,
        selector_config=cfg,
        USER_COUNT_ADVANCED_SCRIPT="advanced",
        COMPOSER_EMPTY_SCRIPT="empty",
    )

    assert adapter._is_sent(previous_user_message_count=3, had_text=True) is True
    assert len(page.waited_functions) == 2
    assert page.waited_functions[0][0] == "advanced"
    assert page.waited_functions[0][1]["timeout"] == 8000
    assert page.waited_functions[0][1]["arg"][1] == 3
    assert page.waited_functions[1][0] == "empty"
    assert page.waited_functions[1][1]["timeout"] == 5000


def test_is_sent_without_text_does_not_fallback_to_composer_empty():
    page = FakePage(wait_function_failures={"advanced"})
    cfg = FakeSelectorConfig()
    adapter = bind_adapter(
        page,
        selector_config=cfg,
        USER_COUNT_ADVANCED_SCRIPT="advanced",
        COMPOSER_EMPTY_SCRIPT="empty",
    )

    assert adapter._is_sent(previous_user_message_count=3, had_text=False) is False
    assert len(page.waited_functions) == 1
    assert page.waited_functions[0][0] == "advanced"
    assert page.waited_functions[0][1]["timeout"] == 8000


def test_fill_prompt_after_upload_uses_composer_and_fallback_insert_text():
    page = FakePage(fill_exc=True)
    cfg = FakeSelectorConfig(composer="composer")
    adapter = bind_adapter(page, selector_config=cfg)

    assert adapter.fill_prompt_after_upload("prompt") == {"had_text": True}
    assert page.locator_waits == [("composer", {"state": "visible", "timeout": 1000})]
    assert page.fills == [("composer", "prompt")]
    assert page.clicks == [("composer", {})]
    assert page.inserted_texts == ["prompt"]


def test_send_current_message_uses_base_flow_and_configured_selectors():
    page = FakePage()
    cfg = FakeSelectorConfig(composer="composer", send_button="send")
    adapter = bind_adapter(
        page,
        selector_config=cfg,
        USER_COUNT_ADVANCED_SCRIPT="advanced",
    )

    adapter.send_current_message(previous_user_message_count=1)

    assert page.presses == [("composer", "Enter")]
    assert len(page.waited_functions) == 1
    assert page.waited_functions[0][0] == "advanced"
    assert page.waited_functions[0][1]["timeout"] == 8000
