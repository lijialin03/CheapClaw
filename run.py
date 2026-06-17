# run.py
import argparse
from pathlib import Path

from model_clients import QwenClient
from agent_core import Agent, Memory
from agent_core.config import load_config
from agent_core.workspace import Workspace
from ui import RichCLI


DEFAULT_WORKSPACE_ROOT = Path.cwd()

MODEL_CLIENTS = {
    "qwen": QwenClient,
}


def main():
    parser = argparse.ArgumentParser(description="CheapClaw 终端对话助手")
    parser.add_argument("--model", choices=MODEL_CLIENTS.keys(), default="qwen",
                        help="选择模型前端客户端 (默认: qwen；当前版本仅支持 qwen)")
    parser.add_argument("--config", default=None,
                        help="配置文件路径 (默认: config/default_config.json；不存在则使用内置默认值)")
    parser.add_argument("--storage-state", default=None,
                        help="Playwright storage_state.json 路径 (默认: config/storage_state.json)")
    parser.add_argument("--headed", action="store_true", default=None,
                        help="使用有头浏览器，便于观察登录或前端交互")
    parser.add_argument("--workspace-root", default=str(DEFAULT_WORKSPACE_ROOT),
                        help="Workspace 根目录 (默认: 当前目录)")
    args = parser.parse_args()

    app_config = load_config(args.config)

    workspace_root = Path(args.workspace_root).expanduser().resolve()
    memory_path = workspace_root / ".cheapclaw" / "memory" / "memory.json"
    agent_config = app_config.agent
    browser_config = app_config.browser
    headless = browser_config.headless if args.headed is None else not args.headed

    # 1. 先初始化模型客户端：默认复用对应客户端的 storage_state
    client_cls = MODEL_CLIENTS[args.model]
    client = client_cls(
        config=browser_config,
        headless=headless,
        storage_state_path=args.storage_state,
    )
    model_name = getattr(client, "DISPLAY_NAME", args.model)

    # 2. 再初始化记忆系统（需要 client.send_text 做自动压缩摘要）
    memory = Memory(
        persist_path=memory_path,
        llm_call=client.send_text,
        config=app_config.memory,
    )

    # 3. Workspace 沙箱和编排层
    workspace = Workspace(root=workspace_root)
    agent = Agent(
        client=client,
        memory=memory,
        workspace=workspace,
        config=agent_config,
    )

    # 4. 启动 UI
    cli = RichCLI(agent, title=f"{model_name} 对话助手")
    cli.run()

if __name__ == "__main__":
    main()