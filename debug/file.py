from playwright.sync_api import sync_playwright

def run():
    with sync_playwright() as p:
        # 启动浏览器（headless=False 便于观察，服务器上可改为 True）
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        
        # 打开通义千问（可替换为您的具体对话链接）
        page.goto("https://chat.qwen.ai/")
        # 等待页面基本加载
        page.wait_for_timeout(5000)
        
        # 1. 定位文件上传 input（隐藏元素，但仍可操作）
        file_input = page.locator("#filesUpload")
        # 确保元素存在
        file_input.wait_for(state="attached", timeout=10000)
        
        # 2. 上传文件（请替换为实际存在的文件路径，内容为长文本）
        file_path = "/path/to/your/long_text.txt"  # 例如 "C:/data/long_text.txt" 或 "./long_text.txt"
        file_input.set_input_files(file_path)
        print(f"已上传文件: {file_path}")
        
        # 3. 等待文件上传并解析完成
        # 通义千问上传文件后，通常会出现文件列表项或“解析成功”提示
        # 以下选择器仅供参考，请根据实际页面元素调整（例如等待文件卡片出现）
        try:
            # 等待文件出现在文件列表中（示例类名，可能需要您用浏览器检查实际类名）
            page.wait_for_selector(".ant-upload-list-item", timeout=15000)
            print("文件已显示在附件列表中")
        except:
            print("未找到文件列表元素，可能页面结构不同，继续等待几秒...")
            page.wait_for_timeout(5000)
        
        # 可选：等待输入框中的提示文字变为可发送（上传完成后发送按钮可能变为可用）
        # 发送按钮初始状态可能为 disabled（因为输入框为空），上传文件后通常变为可用
        page.wait_for_selector(".send-button:not([disabled])", timeout=15000)
        print("发送按钮已可用")
        
        # 4. 点击发送按钮
        page.click(".send-button")
        print("已点击发送按钮，等待 AI 回复...")
        
        # 5. 等待 AI 回复完成（检测最新助手消息中的操作按钮容器）
        page.wait_for_selector(
            ".qwen-chat-message-assistant:last-child .message-hoc-container",
            timeout=120000
        )
        print("AI 回复完成")
        
        # 6. 提取并打印回复内容
        reply_element = page.locator(
            ".qwen-chat-message-assistant:last-child .response-message-content"
        ).last
        reply_text = reply_element.inner_text()
        print("\n=== AI 回复 ===\n")
        print(reply_text)
        
        # 可选：保存回复到文件或截图
        page.screenshot(path="upload_reply.png")
        browser.close()

if __name__ == "__main__":
    run()