import pytest

from agent_core.workspace import Workspace, WorkspaceError


def test_relative_path_inside_workspace_resolves_successfully(tmp_path):
    workspace = Workspace(tmp_path)

    assert workspace.resolve("src/file.txt") == tmp_path / "src" / "file.txt"


def test_absolute_path_inside_workspace_resolves_successfully(tmp_path):
    workspace = Workspace(tmp_path)
    path = tmp_path / "src" / "file.txt"

    assert workspace.resolve(str(path)) == path


def test_parent_traversal_outside_workspace_is_rejected(tmp_path):
    workspace = Workspace(tmp_path)

    with pytest.raises(WorkspaceError):
        workspace.resolve("../outside")


@pytest.mark.parametrize(
    "path",
    [
        ".git/config",
        ".cheapclaw/state.json",
        ".env",
        ".env.local",
        "secret.pem",
        "secret.key",
        "id_rsa",
        "id_rsa.pub",
        "credentials.json",
        "credentials.prod.json",
        "config/storage_state.json",
    ],
)
def test_sensitive_paths_are_rejected(tmp_path, path):
    workspace = Workspace(tmp_path)

    with pytest.raises(WorkspaceError):
        workspace.resolve(path)
