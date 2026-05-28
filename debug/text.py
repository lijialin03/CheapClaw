from playwright.sync_api import sync_playwright

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # 可根据需要改为 True
        page = browser.new_page()
        page.goto("https://chat.qwen.ai/")  # 或您的特定对话链接
        page.wait_for_timeout(5000)

        # 输入问题
        page.wait_for_selector(".message-input-textarea", timeout=10000)
        page.fill(".message-input-textarea", "你是谁")
        
        # 点击发送按钮
        page.click(".send-button")
        print("消息已发送，等待 AI 回复...")

        # 等待回复完成：最后一个助手消息中出现操作按钮容器
        page.wait_for_selector(
            ".qwen-chat-message-assistant:last-child .message-hoc-container",
            timeout=120000  # 最长等待 2 分钟，可根据问题难度调整
        )
        print("AI 回复完成")

        # 提取最新的助手消息中的回复文本
        reply_element = page.locator(
            ".qwen-chat-message-assistant:last-child .response-message-content"
        ).last
        reply_text = reply_element.inner_text()
        print("\n=== AI 回复 ===\n")
        print(reply_text)

        # 可选：截图验证
        page.screenshot(path="reply_success.png")
        browser.close()

run()