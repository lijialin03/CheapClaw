# main.py
import argparse
from pathlib import Path

from llm.qwen import QwenClient
from bot import Agent, Assembler, Memory
from bot.workspace import Workspace
from ui import RichCLI


PROJECT_ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="通义千问终端对话")
    parser.add_argument("--ui", choices=["rich"], default="rich",
                        help="选择 UI 模式 (默认: rich)")
    parser.add_argument("--workspace-root", default=str(PROJECT_ROOT),
                        help="Workspace 根目录 (默认: 项目根目录)")
    parser.add_argument("--max-text-chars", type=int, default=3500,
                        help="超过该字符数时改用文件上传发送 (默认: 3500)")
    parser.add_argument("--disable-file-transport", action="store_true",
                        help="禁用长内容自动文件上传")
    args = parser.parse_args()

    # 1. 先启动浏览器客户端：默认复用 config/storage_state.json
    client = QwenClient(headless=True)

    # 2. 再初始化记忆系统（需要 client.send_text 做自动压缩摘要）
    memory = Memory(
        persist_path="memory.json",
        llm_call=client.send_text,
    )

    # 3. 组装器
    assembler = Assembler(
        system_prompt=(
            "你是一个严谨的代码工程师，擅长生成 Python 代码、解答工程相关的问题。"
            "生成代码时不要添加行号，直接输出代码块。"
        ),
        memory=memory
    )

    # 4. Workspace 沙箱和编排层
    workspace = Workspace(root=args.workspace_root)
    agent = Agent(
        client=client,
        assembler=assembler,
        memory=memory,
        workspace=workspace,
        max_text_chars=args.max_text_chars,
        file_transport_enabled=not args.disable_file_transport,
    )

    # 5. 根据参数选择 UI
    if args.ui == "rich":
        cli = RichCLI(agent)
        cli.run()

if __name__ == "__main__":
    main()