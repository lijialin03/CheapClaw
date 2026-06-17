# main.py
import argparse
from pathlib import Path

from model_clients import QwenClient
from agent_core import Agent, Memory
from agent_core.config import load_config
from agent_core.workspace import Workspace
from ui import RichCLI


PROJECT_ROOT = Path(__file__).resolve().parent
MEMORY_PATH = PROJECT_ROOT / ".cheapclaw" / "memory" / "memory.json"

MODEL_CLIENTS = {
    "qwen": QwenClient,
}


def main():
    parser = argparse.ArgumentParser(description="CheapClaw 终端对话助手")
    parser.add_argument("--ui", choices=["rich"], default="rich",
                        help="选择 UI 模式 (默认: rich)")
    parser.add_argument("--model", choices=MODEL_CLIENTS.keys(), default="qwen",
                        help="选择模型前端客户端 (默认: qwen)")
    parser.add_argument("--headed", action="store_true", default=None,
                        help="使用有头浏览器，便于观察登录或前端交互")
    parser.add_argument("--workspace-root", default=str(PROJECT_ROOT),
                        help="Workspace 根目录 (默认: 项目根目录)")
    parser.add_argument("--max-text-chars", type=int, default=None,
                        help="超过该字符数时改用文件上传发送")
    parser.add_argument("--disable-file-transport", action="store_true", default=None,
                        help="禁用长内容自动文件上传")
    parser.add_argument("--disable-tool-orchestration", action="store_true", default=None,
                        help="禁用自然语言只读工具编排")
    parser.add_argument("--max-tool-steps", type=int, default=None,
                        help="单轮自然语言工具调用最大步数")
    args = parser.parse_args()

    app_config = load_config()

    agent_config = app_config.agent
    browser_config = app_config.browser
    headless = browser_config.headless if args.headed is None else not args.headed
    file_transport_enabled = None if args.disable_file_transport is None else not args.disable_file_transport
    tool_orchestration_enabled = None if args.disable_tool_orchestration is None else not args.disable_tool_orchestration

    # 1. 先初始化模型客户端：默认复用对应客户端的 storage_state
    client_cls = MODEL_CLIENTS[args.model]
    client = client_cls(
        config=browser_config,
        headless=headless,
    )
    model_name = getattr(client, "DISPLAY_NAME", args.model)

    # 2. 再初始化记忆系统（需要 client.send_text 做自动压缩摘要）
    memory = Memory(
        persist_path=MEMORY_PATH,
        llm_call=client.send_text,
        config=app_config.memory,
    )

    # 3. Workspace 沙箱和编排层
    workspace = Workspace(root=args.workspace_root)
    agent = Agent(
        client=client,
        memory=memory,
        workspace=workspace,
        config=agent_config,
        max_text_chars=args.max_text_chars,
        file_transport_enabled=file_transport_enabled,
        tool_orchestration_enabled=tool_orchestration_enabled,
        max_tool_steps=args.max_tool_steps,
    )

    # 4. 根据参数选择 UI
    if args.ui == "rich":
        cli = RichCLI(agent, title=f"{model_name} 对话助手")
        cli.run()

if __name__ == "__main__":
    main()