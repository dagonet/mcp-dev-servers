"""template_finalize_sync under manifest v3 and template_version derivation (review §2.10, §6.3)."""

import asyncio
import json
import subprocess

import pytest

from mcp_dev_servers import template_sync_mcp as ts
from mcp_dev_servers import template_sync_v3 as v3

OWNERSHIP = {
    "tracked_paths": ["hooks", "templates"],
    "rules": [
        {"pattern": ".claude/agents/local-*.md", "ownership": "project"},
        {"pattern": "hooks/**", "ownership": "template"},
        {"pattern": ".claude/agents/*.md", "ownership": "template"},
        {"pattern": "CLAUDE.md", "ownership": "template"},
        {"pattern": ".claude/settings.json", "ownership": "template"},
        {"pattern": ".claude/rules/project.md", "ownership": "once"},
        {"pattern": "gitignore", "ownership": "once", "target": ".gitignore"},
        {
            "pattern": "PROJECT_CONTEXT.md", "ownership": "once", "audit": "keys",
            "required_keys": ["Protected branches", "Gate"],
            "deprecated_keys": {"Gate Command": "Gate", "Test Command": "Test"},
            "aliases": {"Build": ["Build (desktop)"]},
            "placeholder_map": {"Build": "BUILD_COMMAND", "Test": "TEST_COMMAND"},
        },
    ],
}


def _mk_v3(tmp_path, template: dict[str, str], project: dict[str, str],
           entries: dict[str, dict], placeholders: dict | None = None,
           ownership: dict | None = None, requires_server: str = ">=0.3.0"):
    repo = tmp_path / "toolkit"
    (repo / "templates" / "general").mkdir(parents=True)
    (repo / "templates" / "ownership.json").write_text(
        json.dumps(ownership if ownership is not None else OWNERSHIP), encoding="utf-8")
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    m = {"templateRepo": str(repo), "variant": "general"}
    for rel, content in template.items():
        p = ts._template_file_path(m, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="")
    for rel, content in project.items():
        p = proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="")
    manifest = {
        "manifest_version": 3,
        "template_version": "3.1.0",
        "template_commit": "0000000",
        "variant": "general",
        "templateRepo": str(repo),
        "placeholders": placeholders or {},
        "requires_server": requires_server,
        "files": entries,
    }
    (proj / ".claude" / "template-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8", newline="")
    return repo, proj


def _tpl_entry(content: str, placeholders: dict | None = None) -> dict:
    replaced = ts._apply_placeholders(content, placeholders or {})
    return {"hash": "sha256:" + ts._sha256(replaced), "ownership": "template"}


def _run(coro):
    return json.loads(asyncio.run(coro))


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   cwd=str(repo), check=True, capture_output=True)


def _git_out(repo, *args) -> str:
    return subprocess.run(["git", *args], cwd=str(repo), check=True,
                          capture_output=True, text=True).stdout.strip()


def _init_repo(repo):
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return _git_out(repo, "rev-parse", "HEAD")


def test_derive_template_version_tree_identical_reachable_tag(tmp_path):
    repo, proj = _mk_v3(tmp_path, template={"CLAUDE.md": "v1\n", "hooks/g.sh": "g\n"}, project={}, entries={})
    c1 = _init_repo(repo)
    _git(repo, "tag", "v3.1.0")
    # A commit that touches only an untracked path keeps the tracked tree identical.
    (repo / "README.md").write_text("docs\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "docs")
    c2 = _git_out(repo, "rev-parse", "HEAD")
    assert v3.derive_template_version(str(repo), c2, ["templates", "hooks"]) == ("v3.1.0", None)
    # A commit that changes a tracked path is untagged.
    (repo / "hooks" / "g.sh").write_text("g2\n", encoding="utf-8")
    _git(repo, "add", "hooks/g.sh")
    _git(repo, "commit", "-q", "-m", "hook")
    c3 = _git_out(repo, "rev-parse", "HEAD")
    assert v3.derive_template_version(str(repo), c3, ["templates", "hooks"]) == (None, "untagged_template_tree")


def test_derive_template_version_not_git(tmp_path):
    repo, proj = _mk_v3(tmp_path, template={"CLAUDE.md": "v1\n"}, project={}, entries={})
    assert v3.derive_template_version(str(repo), "abc", ["templates"]) == (None, "template_repo_not_git")


def test_finalize_v3_writes_prefixed_hashes_and_reports_unknown_keys(tmp_path):
    repo, proj = _mk_v3(tmp_path,
                        template={"CLAUDE.md": "v1\n", ".claude/rules/project.md": "p\n", "hooks/new.sh": "n\n"},
                        project={"CLAUDE.md": "v1\n", ".claude/rules/project.md": "mine\n", "hooks/new.sh": "n\n"},
                        entries={"CLAUDE.md": _tpl_entry("v0\n")})
    m = json.loads((proj / ".claude" / "template-manifest.json").read_text(encoding="utf-8"))
    m["deletedAcknowledged"] = ["x.md"]
    (proj / ".claude" / "template-manifest.json").write_text(json.dumps(m), encoding="utf-8")
    _init_repo(repo)
    _git(repo, "tag", "v3.1.0")
    head = _git_out(repo, "rev-parse", "HEAD")

    applied = json.dumps([
        {"file_path": "CLAUDE.md", "manifest_entry": {"hash": ts._sha256("v1\n"), "ownership": "template"}},
        {"file_path": ".claude/rules/project.md", "manifest_entry": {"ownership": "once"}},
    ])
    res = _run(ts.template_finalize_sync(str(proj), applied, new_files=json.dumps(["hooks/new.sh"])))
    assert res["manifest_written"] is True
    assert res["template_commit"] == head
    assert res["template_version"] == "v3.1.0"
    assert res["unknown_keys"] == ["deletedAcknowledged"]
    assert res["files_updated"] == 2 and res["files_added"] == 1

    out = json.loads((proj / ".claude" / "template-manifest.json").read_text(encoding="utf-8"))
    assert out["manifest_version"] == 3
    assert out["template_commit"] == head
    assert out["template_version"] == "v3.1.0"
    assert out["requires_server"] == ">=0.3.0"
    assert out["deletedAcknowledged"] == ["x.md"]
    assert "lastSynced" not in out and "version" not in out
    assert out["files"]["CLAUDE.md"] == {"hash": "sha256:" + ts._sha256("v1\n"), "ownership": "template"}
    assert out["files"][".claude/rules/project.md"] == {"ownership": "once"}
    assert out["files"]["hooks/new.sh"] == {"hash": "sha256:" + ts._sha256("n\n"), "ownership": "template"}


def test_finalize_v3_rejects_bad_hash_and_ownership(tmp_path):
    repo, proj = _mk_v3(tmp_path, template={"CLAUDE.md": "v1\n"}, project={"CLAUDE.md": "v1\n"}, entries={})
    applied = json.dumps([
        {"file_path": "CLAUDE.md", "manifest_entry": {"hash": "sha256:nope", "ownership": "template"}},
        {"file_path": "x.md", "manifest_entry": {"hash": "sha256:" + "a" * 64, "ownership": "merge"}},
    ])
    res = _run(ts.template_finalize_sync(str(proj), applied))
    assert "manifest NOT written" in res["error"]
    assert len(res["invalid_entries"]) == 2


def test_finalize_v3_drop_sweep_and_explicit_deletes(tmp_path):
    repo, proj = _mk_v3(tmp_path, template={"CLAUDE.md": "v1\n"},
                        project={"CLAUDE.md": "v1\n", ".claude/agents/kept.md": "k\n"},
                        entries={"CLAUDE.md": _tpl_entry("v1\n"),
                                 "hooks/gone.sh": _tpl_entry("g\n"),
                                 ".claude/agents/kept.md": _tpl_entry("k\n")})
    res = _run(ts.template_finalize_sync(str(proj), "[]", deleted_files=json.dumps([".claude/agents/kept.md"])))
    assert sorted(res["dropped_entries"]) == [".claude/agents/kept.md", "hooks/gone.sh"]
    assert "template_repo_not_git" in res["warnings"]
    out = json.loads((proj / ".claude" / "template-manifest.json").read_text(encoding="utf-8"))
    assert list(out["files"]) == ["CLAUDE.md"]
    assert out["template_version"] is None
