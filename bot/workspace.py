# bot/workspace.py
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class WorkspaceEditPlan:
    path: str
    old_text: str
    new_text: str
    reason: str


class WorkspaceError(ValueError):
    pass


class Workspace:
    """Workspace 沙箱，负责本地文件安全访问。"""

    SENSITIVE_NAMES = {"memory.json"}
    SENSITIVE_SUFFIXES = {".pem", ".key"}

    def __init__(self, root: str | Path = PROJECT_ROOT, max_read_bytes: int = 200_000):
        self.root = Path(root).expanduser().resolve()
        self.max_read_bytes = max_read_bytes

    def resolve(self, path: str) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        if not self._is_inside_root(resolved):
            raise WorkspaceError(f"路径超出 workspace: {path}")
        self._reject_sensitive(resolved)
        return resolved

    def read_text(
        self,
        path: str,
        start_line: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> str:
        resolved = self.resolve(path)
        self._ensure_readable_text_file(resolved)
        text = resolved.read_text(encoding="utf-8")
        if start_line is None and limit is None:
            return text

        lines = text.splitlines()
        start_index = max((start_line or 1) - 1, 0)
        end_index = None if limit is None else start_index + max(limit, 0)
        selected = lines[start_index:end_index]
        return "\n".join(selected)

    def stat(self, path: str) -> dict:
        resolved = self.resolve(path)
        stat_result = resolved.stat()
        return {
            "path": self._relative_path(resolved),
            "is_file": resolved.is_file(),
            "is_dir": resolved.is_dir(),
            "size": stat_result.st_size,
            "modified": stat_result.st_mtime,
        }

    def list_dir(self, path: str = ".", max_entries: int = 200) -> list[dict]:
        resolved = self.resolve(path)
        if not resolved.exists():
            raise WorkspaceError(f"路径不存在: {path}")
        if not resolved.is_dir():
            raise WorkspaceError(f"不是目录: {path}")

        entries = []
        for child in sorted(resolved.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            try:
                self._reject_sensitive(child.resolve())
            except WorkspaceError:
                continue
            stat_result = child.stat()
            entries.append({
                "name": child.name,
                "path": self._relative_path(child.resolve()),
                "type": "dir" if child.is_dir() else "file",
                "size": stat_result.st_size,
            })
            if len(entries) >= max_entries:
                break
        return entries

    def write_text(self, path: str, content: str) -> None:
        resolved = self.resolve(path)
        if resolved.exists():
            self._ensure_readable_text_file(resolved)
        else:
            parent = resolved.parent.resolve()
            if not self._is_inside_root(parent):
                raise WorkspaceError(f"路径超出 workspace: {path}")
            self._reject_sensitive(parent)
            parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")

    def assert_editable_text_file(self, path: str) -> Path:
        resolved = self.resolve(path)
        self._ensure_readable_text_file(resolved)
        return resolved

    def apply_edit_plan(self, plan: WorkspaceEditPlan) -> None:
        resolved = self.assert_editable_text_file(plan.path)
        current_text = resolved.read_text(encoding="utf-8")
        matches = current_text.count(plan.old_text)
        if matches != 1:
            raise WorkspaceError(f"old_text 匹配次数为 {matches}，需要唯一匹配后才能应用")
        resolved.write_text(current_text.replace(plan.old_text, plan.new_text, 1), encoding="utf-8")

    def _ensure_readable_text_file(self, path: Path) -> None:
        if not path.exists():
            raise WorkspaceError(f"路径不存在: {self._relative_path(path)}")
        if not path.is_file():
            raise WorkspaceError(f"不是普通文件: {self._relative_path(path)}")
        size = path.stat().st_size
        if size > self.max_read_bytes:
            raise WorkspaceError(f"文件过大: {size} bytes，超过限制 {self.max_read_bytes} bytes")
        self._reject_binary(path)

    def _reject_binary(self, path: Path) -> None:
        sample = path.read_bytes()[:4096]
        if b"\0" in sample:
            raise WorkspaceError(f"拒绝读取二进制文件: {self._relative_path(path)}")
        try:
            sample.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceError(f"拒绝读取非 UTF-8 文本文件: {self._relative_path(path)}") from exc

    def _reject_sensitive(self, path: Path) -> None:
        relative_parts = path.relative_to(self.root).parts if self._is_inside_root(path) else path.parts
        if ".git" in relative_parts:
            raise WorkspaceError("拒绝访问 .git 目录")
        if path.name.startswith(".env"):
            raise WorkspaceError(f"拒绝访问敏感文件: {path.name}")
        if path.name in self.SENSITIVE_NAMES:
            raise WorkspaceError(f"拒绝访问敏感文件: {path.name}")
        if path.suffix.lower() in self.SENSITIVE_SUFFIXES:
            raise WorkspaceError(f"拒绝访问敏感文件: {path.name}")
        if path.name.startswith("id_rsa"):
            raise WorkspaceError(f"拒绝访问敏感文件: {path.name}")
        if path.name.startswith("credentials") and path.suffix.lower() == ".json":
            raise WorkspaceError(f"拒绝访问敏感文件: {path.name}")
        if relative_parts == ("config", "storage_state.json"):
            raise WorkspaceError("拒绝访问敏感文件: config/storage_state.json")

    def _is_inside_root(self, path: Path) -> bool:
        return path == self.root or self.root in path.parents

    def _relative_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)
