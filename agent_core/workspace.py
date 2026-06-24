from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class WorkspaceError(ValueError):
    pass


class Workspace:
    """Workspace 路径沙箱，负责限制受控终端只能访问安全的项目路径。"""

    SENSITIVE_NAMES = set()
    SENSITIVE_SUFFIXES = {".pem", ".key"}

    def __init__(self, root: str | Path = PROJECT_ROOT):
        self.root = Path(root).expanduser().resolve()

    def resolve(self, path: str) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        if not self._is_inside_root(resolved):
            raise WorkspaceError(f"路径超出 workspace: {path}")
        self._reject_sensitive(resolved)
        return resolved

    def _reject_sensitive(self, path: Path) -> None:
        relative_parts = (
            path.relative_to(self.root).parts
            if self._is_inside_root(path)
            else path.parts
        )
        if ".git" in relative_parts:
            raise WorkspaceError("拒绝访问 .git 目录")
        if ".cheapclaw" in relative_parts:
            raise WorkspaceError("拒绝访问 .cheapclaw 系统目录")
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
        if len(relative_parts) == 2 and relative_parts[0] == "config":
            name = relative_parts[1]
            if name == "storage_state.json" or (
                name.startswith("storage_state_") and name.endswith(".json")
            ):
                raise WorkspaceError(f"拒绝访问敏感文件: config/{name}")

    def _is_inside_root(self, path: Path) -> bool:
        return path == self.root or self.root in path.parents
