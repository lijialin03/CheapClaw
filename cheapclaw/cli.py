import argparse
from pathlib import Path

from cheapclaw.agent_core import Agent, Memory
from cheapclaw.agent_core.config import load_config
from cheapclaw.agent_core.workspace import Workspace
from cheapclaw.model_clients import DeepSeekClient, QwenClient
from cheapclaw.ui import RichCLI

MODEL_CLIENTS = {
    "qwen": QwenClient,
    "deepseek": DeepSeekClient,
}


def main():
    parser = argparse.ArgumentParser(description="CheapClaw 终端对话助手")
    parser.add_argument(
        "--model",
        choices=MODEL_CLIENTS.keys(),
        default="deepseek",
        help="选择模型前端客户端 (默认: deepseek；支持 qwen, deepseek)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="配置文件路径 (默认: config/default_config.json；不存在则使用内置默认值)",
    )
    parser.add_argument(
        "--storage-state",
        default=None,
        help="Playwright 登录态 JSON 路径 (默认: 对应模型客户端的登录态路径)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        default=None,
        help="使用有头浏览器，便于观察登录或前端交互",
    )
    parser.add_argument(
        "--workspace-root",
        default=None,
        help="Workspace 根目录 (默认: 当前目录)",
    )
    parser.add_argument(
        "--cleanup-session",
        action="store_true",
        help="退出时清理本次运行创建的网页会话（当前仅 DeepSeek 支持）",
    )
    args = parser.parse_args()

    app_config = load_config(args.config)

    workspace_root = Path(args.workspace_root or Path.cwd()).expanduser().resolve()
    agent_config = app_config.agent
    browser_config = app_config.browser
    headless = browser_config.headless if args.headed is None else not args.headed

    client_cls = MODEL_CLIENTS[args.model]
    client = client_cls(
        config=browser_config,
        headless=headless,
        storage_state_path=args.storage_state,
        cleanup_session=args.cleanup_session,
    )
    model_name = getattr(client, "DISPLAY_NAME", args.model)

    key_info_path = workspace_root / ".cheapclaw" / "key_info.yaml"
    session_dir = workspace_root / ".cheapclaw" / "memory"
    memory = Memory(
        key_info_path=key_info_path,
        session_dir=session_dir,
        llm_call=client.send_text,
        config=app_config.memory,
    )

    workspace = Workspace(root=workspace_root)
    agent = Agent(
        client=client,
        memory=memory,
        workspace=workspace,
        config=agent_config,
    )

    cli = RichCLI(agent, title=f"{model_name} 对话助手")
    cli.run()


if __name__ == "__main__":
    main()
