# agent_core/assembler.py
from .memory import Memory


class Assembler:
    """
    将系统角色、分层记忆上下文、当前用户输入组装成最终发送给 AI 的文本。
    记忆内容由 Memory.get_context() 按优先级提供。
    """
    def __init__(self, system_prompt: str | None = None, memory: Memory | None = None):
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
