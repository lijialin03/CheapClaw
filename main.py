# main.py
import argparse

from llm.qwen import QwenClient
from bot import Agent, Assembler, Memory
from ui import RichCLI


def main():
    parser = argparse.ArgumentParser(description="通义千问终端对话")
    parser.add_argument("--ui", choices=["rich"], default="rich",
                        help="选择 UI 模式 (默认: rich)")
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

    # 4. 编排层
    agent = Agent(client=client, assembler=assembler, memory=memory)

    # 5. 根据参数选择 UI
    if args.ui == "rich":
        cli = RichCLI(agent)
        cli.run()

if __name__ == "__main__":
    main()