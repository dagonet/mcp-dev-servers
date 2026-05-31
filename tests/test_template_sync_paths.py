"""Path-resolution tests for template_sync_mcp.

Regression coverage for the hooks-at-repo-root bug: shared hook scripts live
at <repo>/hooks/, not templates/<variant>/hooks/. Before the fix, the sync
tool resolved every tracked path against templates/<variant>/ and reported
root-tracked hooks as TEMPLATE_DELETED.
"""

import pathlib

from mcp_dev_servers import template_sync_mcp as ts


def _manifest(repo: str, variant: str = "rust-tauri") -> dict:
    return {"templateRepo": repo, "variant": variant, "placeholders": {}, "files": {}}


def test_is_root_tracked_only_for_hooks():
    assert ts._is_root_tracked("hooks/no-push-main.sh")
    assert ts._is_root_tracked("hooks/nested/x.sh")
    assert not ts._is_root_tracked("CLAUDE.md")
    assert not ts._is_root_tracked(".claude/agents/coder.md")
    # A variant-local path that merely contains "hooks" later must not match.
    assert not ts._is_root_tracked(".claude/hooks/x.sh")


def test_hook_path_resolves_to_repo_root(tmp_path):
    repo = tmp_path
    (repo / "hooks").mkdir()
    (repo / "hooks" / "no-push-main.sh").write_text("#!/usr/bin/env bash\n")
    (repo / "templates" / "rust-tauri").mkdir(parents=True)

    m = _manifest(str(repo))
    resolved = ts._template_file_path(m, "hooks/no-push-main.sh")

    assert resolved == (repo / "hooks" / "no-push-main.sh").resolve()
    assert ts._read_file(resolved) is not None  # not seen as deleted


def test_variant_file_resolves_to_variant_dir(tmp_path):
    repo = tmp_path
    vdir = repo / "templates" / "rust-tauri"
    vdir.mkdir(parents=True)
    (vdir / "CLAUDE.md").write_text("# hi\n")

    m = _manifest(str(repo))
    resolved = ts._template_file_path(m, "CLAUDE.md")

    assert resolved == (vdir / "CLAUDE.md").resolve()


def test_git_path_root_tracked_vs_variant():
    m = _manifest("/whatever", variant="java")
    assert ts._template_git_path(m, "hooks/no-push-main.sh") == "hooks/no-push-main.sh"
    assert ts._template_git_path(m, "CLAUDE.md") == "templates/java/CLAUDE.md"


def test_real_toolkit_hooks_not_deleted():
    """If the toolkit checkout is present, every shipped hook must resolve."""
    repo = pathlib.Path("G:/git/claude-code-toolkit")
    if not (repo / "hooks").is_dir():
        import pytest

        pytest.skip("toolkit checkout not present")
    m = _manifest(str(repo))
    for hook in (repo / "hooks").glob("*.sh"):
        rel = f"hooks/{hook.name}"
        assert ts._read_file(ts._template_file_path(m, rel)) is not None, rel
