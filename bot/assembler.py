# bot/assembler.py
from typing import Optional
from .memory import Memory


class Assembler:
    """
    将系统角色、分层记忆上下文、当前用户输入组装成最终发送给 AI 的文本。
    记忆部分由 Memory.get_context() 自动按优先级组装。
    """
    def __init__(self, system_prompt: Optional[str] = None, memory: Optional[Memory] = None):
        """
        :param system_prompt: 系统角色定义，例如 "你是一个专业的代码工程师，擅长 Python。"
        :param memory: Memory 实例，用于获取分层记忆上下文
        """
        self.system_prompt = system_prompt
        self.memory = memory

    def assemble(self, user_input: str) -> str:
        """
        组装最终的 prompt。

        格式:
            【系统设定】...
            【关键信息】...       ← 由 memory 管理
            【历史摘要】...
            【最近对话】...
            用户: {当前输入}
        """
        parts = []
        if self.system_prompt:
            parts.append(f"【系统设定】{self.system_prompt}")
        if self.memory:
            memory_context = self.memory.get_context()
            if memory_context:
                parts.append(memory_context)
        parts.append(f"用户: {user_input}")
        return "\n".join(parts)

    def update_memory(self, user_input: str, assistant_reply: str):
        """将本轮对话更新到记忆中（添加 user 和 assistant 消息，并持久化）"""
        if self.memory:
            self.memory.add_user_message(user_input)
            self.memory.add_assistant_message(assistant_reply)
            self.memory.save()