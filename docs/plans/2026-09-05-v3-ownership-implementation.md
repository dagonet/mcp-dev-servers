# template-sync-tools 0.3.0 — three-class ownership (manifest v3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship mcp-dev-servers 0.3.0: the template-sync server reads manifest v3 with `template`/`once`/`project` ownership, replaces CONFLICT with the §7 statuses, migrates v2 manifests through an explicit tool, and keeps every v2 code path byte-stable.

**Architecture:** All v3 logic lives in a new module `src/mcp_dev_servers/template_sync_v3.py` (pure functions, no MCP decorators) that imports the shared helpers from `template_sync_mcp.py`. The existing tools in `template_sync_mcp.py` detect `manifest_version == 3` and dispatch to the v3 functions via a lazy import inside the tool body; a v2 manifest takes exactly the code it takes today. One new tool, `template_migrate_manifest`, is registered in `template_sync_mcp.py` and implemented in the v3 module.

**Tech Stack:** Python 3.11, FastMCP (`mcp.server.fastmcp`), stdlib only (`re`, `difflib`, `json`, `pathlib`, `subprocess` via the existing `_run_git`), pytest. Run tests with `.venv/Scripts/python -m pytest -q` from the repo root (Windows, Git Bash).

**Spec:** `docs/plans/2026-09-05-v3.1-ownership-server-review.md` (this repo; the agreed server contract, §2–§10) and `claude-code-toolkit` `docs/plans/2026-09-03-v3.1-ownership-model-spec.md` @ `2b23958` (branch `docs/v31-specs`, PR #83; batch-7 corrections included). The spec's §7 "Report fields" list names every field the skill and server share — the response keys in this plan must match it verbatim.

## Global Constraints

- Server version after this plan: `0.3.0` in `pyproject.toml` and `src/mcp_dev_servers/__init__.py`; manifest v3 `requires_server` is `">=0.3.0"`.
- Manifest v3 top-level keys the server reads: `manifest_version` (3), `template_version` (tag or null), `template_commit` (sha; `lastSynced` accepted as a read alias), `variant`, `templateRepo`, `placeholders`, `requires_server`, `files`. Unknown top-level keys are preserved on write and reported as `unknown_keys`.
- `files[<project path>]` entries: `{"hash": "sha256:<64hex>", "ownership": "template"}` or `{"ownership": "once"}`. `hash` is the SHA-256 of the placeholder-replaced template content at sync. Read both `sha256:`-prefixed and bare hex; always write prefixed.
- Ownership rules come from `<templateRepo>/templates/ownership.json`: `{"tracked_paths": [...], "rules": [{"pattern", "ownership", "target"?, "audit"?, "required_keys"?, "deprecated_keys"?, "aliases"?}]}`. First matching rule wins. Patterns are matched against template-relative paths (`CLAUDE.md`, `hooks/x.sh`, `.claude/agents/a.md`), never against consumer files.
- v3 statuses: `template` → `IDENTICAL`, `TEMPLATE_UPDATED`, `LOCAL_EDITED`, `TEMPLATE_DELETED`; `once` → `PRESENT`, `MISSING`, `TEMPLATE_DELETED`. `LOCAL_EDITED` wins over `TEMPLATE_UPDATED`.
- The server never deletes a project file, never writes `project.md` twice, never diffs against the current template when reconstructing a migration base, and never executes a consumer's `**Gate**:` command.
- Normalisation before hashing/classifying (review §8): leading BOM stripped, CRLF and lone CR folded to LF, trailing newline NOT normalised. A BOM- or EOL-only difference is `encoding_drift`, never `LOCAL_EDITED`.
- `gate_self_reference` (review §9a) is direct-only: a whitespace token in a required key's value that is a template-class path by the rules. Reported by status, refused by apply and migrate.
- Every write goes through the existing `_write_file_atomic`.
- Tests never call `git add -A`; stage files by name (see memory note `sandbox-pytest-basetemp-in-repo`).
- Commit messages end with:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_014xqkRdJ3H6Fsv66hjTqke4
  ```
  Write each message to a scratch file and commit with `git commit -F <file>` (heredocs trip the command classifier on this machine).

## File Structure

- Create `src/mcp_dev_servers/template_sync_v3.py` — ownership rules, v3 manifest helpers, version gate, v3 status classification, key audit, orphan scan, template_version derivation, migration. Imports `template_sync_mcp` as `core` at module top (the core module does **not** import v3 at top level, so there is no cycle).
- Modify `src/mcp_dev_servers/template_sync_mcp.py` — `template_load_manifest`, `template_compute_status`, `template_apply_file`, `template_finalize_sync`, `template_get_diff` gain a v3 branch each; new tool `template_migrate_manifest`.
- Create `tests/test_template_sync_v3_rules.py` — Task 1.
- Create `tests/test_template_sync_v3_gate.py` — Tasks 2, 3.
- Create `tests/test_template_sync_v3_status.py` — Tasks 4, 5, 6.
- Create `tests/test_template_sync_v3_apply.py` — Task 7.
- Create `tests/test_template_sync_v3_finalize.py` — Task 8.
- Create `tests/test_template_sync_v3_migration.py` — Task 9 (uses a real git repo, like `test_template_sync_cleanup.py` H5).
- Modify `tests/test_template_sync_merge.py` — Task 10 (one test for the `unified` alias).
- Modify `CHANGELOG.md`, `pyproject.toml`, `src/mcp_dev_servers/__init__.py`, `README.md` — Task 11.

Shared test fixture (copied into each new test file that needs it, so every file is self-contained):

```python
import asyncio
import json

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
    """Toolkit repo with templates/general + ownership.json, and a project with
    a v3 manifest. `template` maps template-relative path -> content (root-tracked
    hooks/** land at the repo root). `entries` maps project path -> manifest entry."""
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
```

---

### Task 1: Ownership rules — loader and glob matcher

**Files:**
- Create: `src/mcp_dev_servers/template_sync_v3.py`
- Test: `tests/test_template_sync_v3_rules.py`

**Interfaces:**
- Consumes: `core._normalize_path`, `core._resolve_path`, `core._read_file` from `template_sync_mcp`.
- Produces:
  - `MIN_SERVER_FOR_V3 = "0.3.0"`, `MANIFEST_VERSION_V3 = 3`, `OWNERSHIP_FILE = "templates/ownership.json"`, `PROJECT_MD = ".claude/rules/project.md"`, `CLASSES = ("template", "once", "project")`
  - `glob_to_regex(pattern: str) -> re.Pattern`
  - `class OwnershipRules` with `rules: list[dict]`, `tracked_paths: list[str]`, `warnings: list[str]`; methods `rule_for(template_rel: str) -> dict | None`, `class_of(template_rel: str) -> str | None`, `project_path_for(template_rel: str) -> str`, `template_path_for(project_rel: str) -> str`, `template_class_rules() -> list[dict]`
  - `load_ownership(template_repo: str) -> OwnershipRules | None` (`None` when the file is absent; a rules object with `warnings` when present but malformed entries are skipped)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_template_sync_v3_rules.py
"""Ownership rules: templates/ownership.json loader and glob matcher (review §2.3, §6.6)."""

import json

import pytest

from mcp_dev_servers import template_sync_v3 as v3


def _write_rules(tmp_path, data):
    repo = tmp_path / "toolkit"
    (repo / "templates").mkdir(parents=True)
    (repo / "templates" / "ownership.json").write_text(json.dumps(data), encoding="utf-8")
    return repo


@pytest.mark.parametrize("pattern,path,expected", [
    ("hooks/**", "hooks/gate.sh", True),
    ("hooks/**", "hooks/lib/git-cmd.sh", True),
    ("hooks/**", "hookshot.sh", False),
    (".claude/agents/*.md", ".claude/agents/coder.md", True),
    (".claude/agents/*.md", ".claude/agents/sub/coder.md", False),
    ("CLAUDE.md", "CLAUDE.md", True),
    ("CLAUDE.md", "CLAUDE.local.md", False),
    ("gitignore", "gitignore", True),
    ("*.md", "README.md", True),
    ("*.md", "docs/README.md", False),
])
def test_glob_to_regex(pattern, path, expected):
    assert bool(v3.glob_to_regex(pattern).match(path)) is expected


def test_load_ownership_returns_none_when_file_absent(tmp_path):
    (tmp_path / "toolkit").mkdir()
    assert v3.load_ownership(str(tmp_path / "toolkit")) is None


def test_first_match_wins_and_project_rule_silences(tmp_path):
    repo = _write_rules(tmp_path, {"rules": [
        {"pattern": ".claude/agents/local-*.md", "ownership": "project"},
        {"pattern": ".claude/agents/*.md", "ownership": "template"},
    ]})
    rules = v3.load_ownership(str(repo))
    assert rules.class_of(".claude/agents/local-notes.md") == "project"
    assert rules.class_of(".claude/agents/coder.md") == "template"
    assert rules.class_of("README.md") is None


def test_target_maps_both_directions(tmp_path):
    repo = _write_rules(tmp_path, {"rules": [
        {"pattern": "gitignore", "ownership": "once", "target": ".gitignore"},
        {"pattern": "CLAUDE.md", "ownership": "template"},
    ]})
    rules = v3.load_ownership(str(repo))
    assert rules.project_path_for("gitignore") == ".gitignore"
    assert rules.template_path_for(".gitignore") == "gitignore"
    assert rules.project_path_for("CLAUDE.md") == "CLAUDE.md"
    assert rules.template_path_for("CLAUDE.md") == "CLAUDE.md"


def test_tracked_paths_default_and_warning(tmp_path):
    repo = _write_rules(tmp_path, {"rules": [{"pattern": "hooks/**", "ownership": "template"}]})
    rules = v3.load_ownership(str(repo))
    assert rules.tracked_paths == ["templates", "hooks"]
    assert any("tracked_paths" in w for w in rules.warnings)

    repo2 = _write_rules(tmp_path / "b", {
        "tracked_paths": ["hooks", "scripts", "templates", "user-level-reference"],
        "rules": [],
    })
    rules2 = v3.load_ownership(str(repo2))
    assert rules2.tracked_paths == ["hooks", "scripts", "templates", "user-level-reference"]
    assert rules2.warnings == []


def test_malformed_rule_is_skipped_with_warning(tmp_path):
    repo = _write_rules(tmp_path, {"rules": [
        {"pattern": "CLAUDE.md"},
        {"pattern": "x.md", "ownership": "merge"},
        {"ownership": "template"},
        {"pattern": "hooks/**", "ownership": "template"},
    ]})
    rules = v3.load_ownership(str(repo))
    assert [r["pattern"] for r in rules.rules] == ["hooks/**"]
    assert len(rules.warnings) == 3


def test_template_class_rules_filters(tmp_path):
    repo = _write_rules(tmp_path, {"rules": [
        {"pattern": "hooks/**", "ownership": "template"},
        {"pattern": "PROJECT_CONTEXT.md", "ownership": "once"},
        {"pattern": "notes/*", "ownership": "project"},
    ]})
    rules = v3.load_ownership(str(repo))
    assert [r["pattern"] for r in rules.template_class_rules()] == ["hooks/**"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_template_sync_v3_rules.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_dev_servers.template_sync_v3'`

- [ ] **Step 3: Create the module with the rules implementation**

```python
# src/mcp_dev_servers/template_sync_v3.py
"""
template-sync manifest v3: three-class file ownership.

Pure functions used by the tools in template_sync_mcp.py when a project's
manifest carries `manifest_version: 3`. A v2 manifest never reaches this
module. Contract: docs/plans/2026-09-05-v3.1-ownership-server-review.md.

Imports the shared helpers from template_sync_mcp as `core`; that module
imports this one lazily inside tool bodies, so there is no import cycle.
"""

from __future__ import annotations

import json
import pathlib
import re

from . import template_sync_mcp as core

MIN_SERVER_FOR_V3 = "0.3.0"
MANIFEST_VERSION_V3 = 3
OWNERSHIP_FILE = "templates/ownership.json"
PROJECT_MD = ".claude/rules/project.md"
CLASSES = ("template", "once", "project")


# -------------------------
# Ownership rules
# -------------------------

def glob_to_regex(pattern: str) -> re.Pattern:
    """Translate a PurePosixPath-style glob into an anchored regex.

    `**` matches across `/`, `*` and `?` stay within one segment. The
    pattern is matched against a template-relative path with forward
    slashes (the key space of _scan_template_files).
    """
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if pattern[i:i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + "$")


class OwnershipRules:
    def __init__(self, rules: list[dict], tracked_paths: list[str], warnings: list[str]):
        self.rules = rules
        self.tracked_paths = tracked_paths
        self.warnings = warnings
        self._compiled = [(glob_to_regex(r["pattern"]), r) for r in rules]

    def rule_for(self, template_rel: str) -> dict | None:
        norm = core._normalize_path(template_rel)
        for rx, rule in self._compiled:
            if rx.match(norm):
                return rule
        return None

    def class_of(self, template_rel: str) -> str | None:
        rule = self.rule_for(template_rel)
        return rule["ownership"] if rule else None

    def project_path_for(self, template_rel: str) -> str:
        rule = self.rule_for(template_rel)
        if rule and rule.get("target"):
            return core._normalize_path(rule["target"])
        return core._normalize_path(template_rel)

    def template_path_for(self, project_rel: str) -> str:
        norm = core._normalize_path(project_rel)
        for rule in self.rules:
            if rule.get("target") and core._normalize_path(rule["target"]) == norm:
                return rule["pattern"]
        return norm

    def template_class_rules(self) -> list[dict]:
        return [r for r in self.rules if r["ownership"] == "template"]


def load_ownership(template_repo: str) -> OwnershipRules | None:
    """Read <repo>/templates/ownership.json. None when absent (no v3 template)."""
    path = core._resolve_path(template_repo) / OWNERSHIP_FILE
    raw = core._read_file(path)
    if raw is None:
        return None
    warnings: list[str] = []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return OwnershipRules([], ["templates"], [f"ownership.json is not valid JSON: {e}"])

    rules: list[dict] = []
    for idx, rule in enumerate(data.get("rules", [])):
        pattern = rule.get("pattern") if isinstance(rule, dict) else None
        ownership = rule.get("ownership") if isinstance(rule, dict) else None
        if not pattern or not isinstance(pattern, str):
            warnings.append(f"ownership.json rules[{idx}]: missing pattern -- skipped")
            continue
        if ownership not in CLASSES:
            warnings.append(f"ownership.json rules[{idx}] ({pattern}): ownership must be one of {CLASSES} -- skipped")
            continue
        rules.append(rule)

    tracked = data.get("tracked_paths")
    if not isinstance(tracked, list) or not all(isinstance(t, str) for t in tracked):
        tracked = ["templates"] + [p.rstrip("/") for p in core._ROOT_TRACKED_PREFIXES]
        warnings.append(
            "ownership.json has no tracked_paths -- template_version derivation "
            f"falls back to {tracked}"
        )
    return OwnershipRules(rules, list(tracked), warnings)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_template_sync_v3_rules.py -q`
Expected: `17 passed`

- [ ] **Step 5: Run the whole suite to confirm nothing else moved**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `104 passed` (87 existing + 17)

- [ ] **Step 6: Commit**

```bash
git add src/mcp_dev_servers/template_sync_v3.py tests/test_template_sync_v3_rules.py
git commit -F <scratch file containing:>
feat(template-sync): ownership rules loader and glob matcher for manifest v3

templates/ownership.json is the one format both repos agreed on: first
matching rule wins, PurePosixPath-style globs with **, `target` renames
(gitignore -> .gitignore), tracked_paths for template_version derivation
with a fallback-and-warn when the key is missing.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014xqkRdJ3H6Fsv66hjTqke4
```

---

### Task 2: Manifest v3 helpers and the `requires_server` gate

**Files:**
- Modify: `src/mcp_dev_servers/template_sync_v3.py` (append after the rules section)
- Test: `tests/test_template_sync_v3_gate.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `is_v3(manifest: dict) -> bool`
  - `manifest_commit(manifest: dict) -> str` — `template_commit`, else `lastSynced`, else `""`
  - `parse_hash(value: str) -> str` — bare 64-hex or `""`; `format_hash(hex: str) -> str`
  - `HASH_RE` accepting `sha256:<64hex>` or `<64hex>`
  - `parse_version(s: str) -> tuple[int, int, int]` (raises `ValueError`)
  - `requires_server_satisfied(spec: str, server_version: str) -> tuple[bool, str]` — `(ok, reason)`; only `>=X.Y.Z` is supported, anything else is `(False, reason)`
  - `KNOWN_TOP_LEVEL_V3 = {...}`, `unknown_top_level_keys(manifest: dict) -> list[str]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_template_sync_v3_gate.py
"""Manifest v3 detection, hash forms, and the requires_server gate (review §2.1, §2.2, §2.5, §2.6)."""

import asyncio
import json

import pytest

from mcp_dev_servers import template_sync_mcp as ts
from mcp_dev_servers import template_sync_v3 as v3


def test_is_v3_and_commit_alias():
    assert v3.is_v3({"manifest_version": 3}) is True
    assert v3.is_v3({"version": 2}) is False
    assert v3.is_v3({}) is False
    assert v3.manifest_commit({"template_commit": "abc"}) == "abc"
    assert v3.manifest_commit({"lastSynced": "def"}) == "def"
    assert v3.manifest_commit({"template_commit": "", "lastSynced": "def"}) == "def"
    assert v3.manifest_commit({}) == ""


def test_hash_forms():
    hx = "a" * 64
    assert v3.parse_hash("sha256:" + hx) == hx
    assert v3.parse_hash(hx) == hx
    assert v3.parse_hash("") == ""
    assert v3.parse_hash("sha256:zz") == ""
    assert v3.format_hash(hx) == "sha256:" + hx


@pytest.mark.parametrize("spec,server,ok", [
    (">=0.3.0", "0.3.0", True),
    (">=0.3.0", "0.3.1", True),
    (">=0.3.0", "1.0.0", True),
    (">=0.3.0", "0.2.1", False),
    (">=0.10.0", "0.9.9", False),
    ("", "0.3.0", True),
    ("^0.3.0", "0.3.0", False),
    (">=x.y", "0.3.0", False),
])
def test_requires_server_satisfied(spec, server, ok):
    result, reason = v3.requires_server_satisfied(spec, server)
    assert result is ok
    if not ok:
        assert reason


def test_unknown_top_level_keys():
    m = {"manifest_version": 3, "files": {}, "deletedAcknowledged": [], "variant": "general"}
    assert v3.unknown_top_level_keys(m) == ["deletedAcknowledged"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_template_sync_v3_gate.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'is_v3'`

- [ ] **Step 3: Append the helpers to the v3 module**

```python
# -------------------------
# Manifest v3 helpers
# -------------------------

HASH_RE = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$")

KNOWN_TOP_LEVEL_V3 = {
    "manifest_version", "template_version", "template_commit", "lastSynced",
    "variant", "templateRepo", "placeholders", "requires_server", "files",
    # v2 keys that migration removes; listed so they are never reported as unknown
    "version",
}


def is_v3(manifest: dict) -> bool:
    return manifest.get("manifest_version") == MANIFEST_VERSION_V3


def manifest_commit(manifest: dict) -> str:
    """template_commit, with lastSynced accepted as a read alias (review §2.1)."""
    return manifest.get("template_commit") or manifest.get("lastSynced") or ""


def parse_hash(value: str) -> str:
    m = HASH_RE.match(value or "")
    return m.group(1) if m else ""


def format_hash(hex_digest: str) -> str:
    return "sha256:" + hex_digest


def parse_version(s: str) -> tuple[int, int, int]:
    parts = s.strip().split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"not a X.Y.Z version: {s!r}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def requires_server_satisfied(spec: str, server_version: str) -> tuple[bool, str]:
    """Only the `>=X.Y.Z` form the toolkit writes is supported (review §2.5)."""
    spec = (spec or "").strip()
    if not spec:
        return True, ""
    if not spec.startswith(">="):
        return False, f"requires_server {spec!r}: only the '>=X.Y.Z' form is supported"
    try:
        floor = parse_version(spec[2:])
        have = parse_version(server_version)
    except ValueError as e:
        return False, f"requires_server {spec!r}: {e}"
    if have < floor:
        return False, (
            f"manifest requires server {spec}, this server is {server_version} -- "
            "upgrade mcp-dev-servers and restart the MCP server"
        )
    return True, ""


def unknown_top_level_keys(manifest: dict) -> list[str]:
    return sorted(k for k in manifest if k not in KNOWN_TOP_LEVEL_V3)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_template_sync_v3_gate.py -q`
Expected: `11 passed`

- [ ] **Step 5: Commit**

```bash
git add src/mcp_dev_servers/template_sync_v3.py tests/test_template_sync_v3_gate.py
git commit -F <scratch file>   # "feat(template-sync): manifest v3 helpers and requires_server gate"
```

---

### Task 3: `template_load_manifest` — v3 response, gate, `server_version`, `migration_required`

**Files:**
- Modify: `src/mcp_dev_servers/template_sync_mcp.py:653-718` (`template_load_manifest`)
- Modify: `src/mcp_dev_servers/template_sync_mcp.py:244-259` (`_load_manifest` — v3 required fields)
- Test: `tests/test_template_sync_v3_gate.py` (append)

**Interfaces:**
- Consumes: `v3.is_v3`, `v3.requires_server_satisfied`, `v3.unknown_top_level_keys`, `v3.manifest_commit`, `v3.load_ownership`, `mcp_dev_servers.__version__`.
- Produces: the load response. For v3: `valid`, `manifest_version: 3`, `server_version`, `migration_required: False`, `variant`, `templateRepo`, `template_commit`, `template_version`, `requires_server`, `placeholders`, `files`, `unknown_keys`, `errors`, `warnings`. For v2 (unchanged keys plus): `manifest_version: 2` (or 1), `server_version`, `migration_required` (True only when `templates/ownership.json` exists in the template repo — §9 "v2 with no v3 template behaves exactly as today").

- [ ] **Step 1: Append the failing tests**

```python
# append to tests/test_template_sync_v3_gate.py

def _write_project(tmp_path, manifest: dict, ownership: dict | None):
    repo = tmp_path / "toolkit"
    (repo / "templates" / "general").mkdir(parents=True)
    if ownership is not None:
        (repo / "templates" / "ownership.json").write_text(json.dumps(ownership), encoding="utf-8")
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    manifest = dict(manifest, templateRepo=str(repo))
    (proj / ".claude" / "template-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return proj


def _load(proj):
    return json.loads(asyncio.run(ts.template_load_manifest(str(proj))))


V3 = {
    "manifest_version": 3, "template_version": "3.1.0", "template_commit": "abc1234",
    "variant": "general", "placeholders": {}, "requires_server": ">=0.3.0", "files": {},
}
V2 = {"version": 2, "variant": "general", "lastSynced": "abc1234", "placeholders": {}, "files": {}}


def test_load_v3_reports_server_version_and_no_migration(tmp_path):
    proj = _write_project(tmp_path, dict(V3, stray="x"), {"rules": []})
    res = _load(proj)
    assert res["valid"] is True
    assert res["manifest_version"] == 3
    assert res["migration_required"] is False
    assert res["server_version"] == ts.__version__
    assert res["template_commit"] == "abc1234"
    assert res["unknown_keys"] == ["stray"]


def test_load_v3_refuses_when_requires_server_unsatisfied(tmp_path):
    proj = _write_project(tmp_path, dict(V3, requires_server=">=99.0.0"), {"rules": []})
    res = _load(proj)
    assert res["valid"] is False
    assert res["server_version"] == ts.__version__
    assert any("requires server >=99.0.0" in e for e in res["errors"])


def test_load_v3_missing_required_fields(tmp_path):
    m = {k: v for k, v in V3.items() if k not in ("template_commit", "requires_server")}
    proj = _write_project(tmp_path, m, {"rules": []})
    res = _load(proj)
    assert res["valid"] is False
    assert "Missing required field: template_commit" in res["errors"]
    assert "Missing required field: requires_server" in res["errors"]


def test_load_v2_migration_required_only_with_ownership_file(tmp_path):
    proj = _write_project(tmp_path, V2, None)
    res = _load(proj)
    assert res["valid"] is True
    assert res["version"] == 2
    assert res["manifest_version"] == 2
    assert res["migration_required"] is False
    assert res["server_version"] == ts.__version__

    proj2 = _write_project(tmp_path / "b", V2, {"rules": []})
    res2 = _load(proj2)
    assert res2["migration_required"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_template_sync_v3_gate.py -q`
Expected: 4 new FAIL — `KeyError: 'manifest_version'` / `'server_version'`

- [ ] **Step 3: Add `__version__` import and extend `_load_manifest`**

At the import block of `template_sync_mcp.py` (after `from mcp.server.fastmcp import FastMCP`):

```python
from . import __version__
```

Replace `_load_manifest` (lines 244–259) with:

```python
def _load_manifest(project_path: pathlib.Path) -> tuple[dict | None, list[str]]:
    """Load and parse the manifest file. Returns (manifest, errors).

    v3 manifests (manifest_version == 3) require template_commit and
    requires_server on top of the v2 fields; lastSynced is accepted as an
    alias of template_commit.
    """
    manifest_path = project_path / ".claude" / "template-manifest.json"
    content = _read_file(manifest_path)
    if content is None:
        return None, ["No template manifest found at .claude/template-manifest.json"]
    try:
        manifest = json.loads(content)
    except json.JSONDecodeError as e:
        return None, [f"Invalid JSON in manifest: {e}"]

    errors = []
    required = ["variant", "templateRepo", "placeholders", "files"]
    if manifest.get("manifest_version") == 3:
        required += ["requires_server"]
        if not (manifest.get("template_commit") or manifest.get("lastSynced")):
            errors.append("Missing required field: template_commit")
    for field in required:
        if field not in manifest:
            errors.append(f"Missing required field: {field}")
    return manifest, errors
```

- [ ] **Step 4: Rewrite `template_load_manifest`**

Replace the body from `pp = pathlib.Path(project_path).resolve()` to the end of the function (lines 664–718) with:

```python
    pp = pathlib.Path(project_path).resolve()
    manifest, errors = _load_manifest(pp)
    if manifest is None:
        return json.dumps({"valid": False, "errors": errors, "server_version": __version__}, ensure_ascii=False)

    if errors:
        return json.dumps({"valid": False, "errors": errors, "server_version": __version__}, ensure_ascii=False)

    from . import template_sync_v3 as v3

    warnings = []
    template_dir = _get_template_dir(manifest)
    if not template_dir.is_dir():
        errors.append(
            f"Template directory not found: {template_dir}. "
            f"Update templateRepo in .claude/template-manifest.json."
        )

    if v3.is_v3(manifest):
        ok, reason = v3.requires_server_satisfied(manifest.get("requires_server", ""), __version__)
        if not ok:
            errors.append(reason)
        rules = v3.load_ownership(manifest["templateRepo"]) if not errors else None
        if not errors and rules is None:
            errors.append(
                f"manifest v3 needs {v3.OWNERSHIP_FILE} in the template repo -- "
                "the toolkit checkout predates v3.1"
            )
        if rules is not None:
            warnings.extend(rules.warnings)
        return json.dumps({
            "valid": len(errors) == 0,
            "manifest_version": 3,
            "server_version": __version__,
            "migration_required": False,
            "variant": manifest.get("variant", ""),
            "templateRepo": manifest.get("templateRepo", ""),
            "template_commit": v3.manifest_commit(manifest),
            "template_version": manifest.get("template_version"),
            "requires_server": manifest.get("requires_server", ""),
            "placeholders": manifest.get("placeholders", {}),
            "files": manifest.get("files", {}),
            "unknown_keys": v3.unknown_top_level_keys(manifest),
            "errors": errors,
            "warnings": warnings,
        }, ensure_ascii=False)

    version = manifest.get("version", 1)

    # Auto-migrate v1 -> v2
    if version < 2:
        warnings.append("Migrating v1 manifest to v2 -- will compute localHash and templateRawHash fields")
        placeholders = manifest.get("placeholders", {})
        files = manifest.get("files", {})

        for rel_path, entry in files.items():
            # Compute templateRawHash from current template file
            tpl_content = _read_file(_template_file_path(manifest, rel_path))
            if tpl_content is not None:
                entry["templateRawHash"] = _sha256(tpl_content)
            else:
                entry["templateRawHash"] = ""

            # localHash: if not locally modified, same as templateHash
            # if locally modified, hash the current project file
            if entry.get("locallyModified", False):
                proj_content = _read_file(pp / rel_path)
                entry["localHash"] = _sha256(proj_content) if proj_content else ""
            else:
                entry["localHash"] = entry.get("templateHash", "")

        manifest["version"] = MANIFEST_VERSION
        warnings.append("v1 -> v2 migration complete. Run sync to persist updated manifest.")

    # A v2 manifest migrates to v3 only when the toolkit checkout ships the
    # ownership table -- without it the server behaves exactly as 0.2.x.
    migration_required = v3.load_ownership(manifest["templateRepo"]) is not None

    return json.dumps({
        "valid": len(errors) == 0,
        "version": manifest.get("version", 1),
        "manifest_version": manifest.get("version", 1),
        "server_version": __version__,
        "migration_required": migration_required,
        "variant": manifest.get("variant", ""),
        "templateRepo": manifest.get("templateRepo", ""),
        "lastSynced": manifest.get("lastSynced", ""),
        "placeholders": manifest.get("placeholders", {}),
        "files": manifest.get("files", {}),
        "errors": errors,
        "warnings": warnings,
    }, ensure_ascii=False)
```

Also update the docstring line `Auto-migrates v1 manifests to v2 format by computing missing hashes.` to:

```
    Auto-migrates v1 manifests to v2 format by computing missing hashes.
    A v3 manifest is validated against requires_server and returns
    server_version; a v2 manifest reports migration_required when the
    template repo ships templates/ownership.json (call
    template_migrate_manifest before any other step).
```

- [ ] **Step 5: Run the gate tests and the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_template_sync_v3_gate.py -q && .venv/Scripts/python -m pytest -q`
Expected: `15 passed` then `119 passed` (v2 tests unaffected — `test_template_sync_*` existing files still pass)

- [ ] **Step 6: Commit**

```bash
git add src/mcp_dev_servers/template_sync_mcp.py tests/test_template_sync_v3_gate.py
git commit -F <scratch file>   # "feat(template-sync): template_load_manifest is the v3 gate (server_version, requires_server, migration_required)"
```

---

### Task 4: Key audit

**Files:**
- Modify: `src/mcp_dev_servers/template_sync_v3.py` (append)
- Test: `tests/test_template_sync_v3_status.py` (new file; audit tests first)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `KEY_LINE_RE = re.compile(r"^(?:[-*+][ \t]+)?\*\*(?P<key>[^*\n]+?)\*\*:[ \t]*(?P<val>.*)$", re.M)` — tolerates a list marker (review §11a)
  - `parse_keys(text: str) -> dict[str, str]` — key → value (first occurrence wins)
  - `find_key(consumer_keys: dict, key: str, rule: dict) -> list[str]` — names in the consumer that satisfy `key` (exact, qualified `key (…)`, aliases) — used for OPTIONAL keys
  - `exact_holdings(consumer_keys: dict, key: str, rule: dict) -> list[str]` — the hook's own match: exact `key` or a `deprecated_keys` spelling of it — used for REQUIRED keys (review §11b)
  - `audit_keys(proj_text: str, tpl_text: str, tpl_at_sync_text: str | None, rule: dict, placeholders: dict | None = None) -> dict` with `missing_required`, `qualified_only` (`[{key, held_as, note}]`, review §11b), `optional_absent`, `placeholder_keys`, `deprecated_keys`, `required` (per-key detail: `value`, `matched_as`, `template_value`, `template_value_at_sync`, `template_default_changed`, `consumer_holds_old_default`), `placeholder_key_divergence` (from the rule's `placeholder_map`, review §10b), `warnings`
  - `gate_refs(consumer_keys: dict, rule: dict, rules: OwnershipRules) -> list[dict]` — `[{"key", "path"}]` direct self-references (review §9a)
  - `collect_gate_refs(pp: pathlib.Path, rules: OwnershipRules) -> tuple[list[dict], bool]` — hits over every audited once file, and whether any declares `**Gate**:`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_template_sync_v3_status.py
"""v3 statuses, key audit, and orphans (review §2.9, §5a, §5b, §7a-c)."""

import asyncio
import json

from mcp_dev_servers import template_sync_mcp as ts
from mcp_dev_servers import template_sync_v3 as v3

# (paste the shared fixture block from the plan's File Structure section here:
#  OWNERSHIP, _mk_v3, _tpl_entry, _run)

AUDIT_RULE = OWNERSHIP["rules"][-1]


def test_parse_keys_first_occurrence_wins_and_list_marker():
    text = "# Ctx\n**Gate**: make check\n- **Test**: pytest\n**Gate**: other\nplain line\n"
    assert v3.parse_keys(text) == {"Gate": "make check", "Test": "pytest"}


def test_find_key_exact_qualified_alias():
    keys = {"Build (desktop)": "x", "Gate": "y", "Test Command": "z"}
    assert v3.find_key(keys, "Gate", AUDIT_RULE) == ["Gate"]
    assert v3.find_key(keys, "Build", AUDIT_RULE) == ["Build (desktop)"]
    assert v3.find_key(keys, "Test", AUDIT_RULE) == []


def test_exact_holdings_is_the_hooks_match():
    keys = {"Gate Command": "old", "Test (frontend unit)": "x", "Protected branches": "main"}
    assert v3.exact_holdings(keys, "Gate", AUDIT_RULE) == ["Gate Command"]
    assert v3.exact_holdings(keys, "Test", AUDIT_RULE) == []
    assert v3.exact_holdings(keys, "Protected branches", AUDIT_RULE) == ["Protected branches"]


def test_audit_reports_lists_and_required_detail():
    proj = ("- **Protected branches**: main\n**Gate Command**: old\n**Build (desktop)**: cargo build\n"
            "**Extra**: {{X}}\n**Mine**: 1\n")
    tpl = ("**Protected branches**: main master\n**Gate**: g\n**Test**: cargo test\n"
           "**Build**: cargo build\n**Extra**: {{X}}\n**Notes**: n\n")
    tpl_at_sync = "**Protected branches**: main\n**Gate**: g\n**Test**: cargo test\n**Build**: cargo build\n**Extra**: {{X}}\n"
    res = v3.audit_keys(proj, tpl, tpl_at_sync, AUDIT_RULE)
    assert res["missing_required"] == []                        # Gate held under its deprecated spelling
    assert res["qualified_only"] == []
    assert res["optional_absent"] == ["Test", "Notes"]          # Build satisfied by the qualified form
    assert res["placeholder_keys"] == ["Extra"]
    assert res["deprecated_keys"] == [{"key": "Gate Command", "replacement": "Gate"}]
    pb = res["required"]["Protected branches"]
    assert pb["value"] == "main"
    assert pb["matched_as"] == ["Protected branches"]
    assert pb["template_value"] == "main master"
    assert pb["template_value_at_sync"] == "main"
    assert pb["template_default_changed"] is True
    assert pb["consumer_holds_old_default"] is True
    assert res["required"]["Gate"]["matched_as"] == ["Gate Command"]
    assert res["warnings"] == []


def test_audit_required_qualified_only_is_its_own_state():
    # yutraffic: the hook matches **Test( Command)?**: exactly, so a qualified
    # holding of a REQUIRED key must never read as present (review §11b).
    proj = "**Protected branches**: main\n**Gate (frontend)**: g\n"
    tpl = "**Protected branches**: main\n**Gate**: g\n"
    res = v3.audit_keys(proj, tpl, None, AUDIT_RULE)
    assert res["missing_required"] == []
    assert res["qualified_only"] == [{
        "key": "Gate", "held_as": ["Gate (frontend)"],
        "note": "the hook matches **Gate**:/**Gate Command**: exactly and will not read it",
    }]
    assert "Gate" not in res["required"]
    res2 = v3.audit_keys("**Protected branches**: main\n", tpl, None, AUDIT_RULE)
    assert res2["missing_required"] == ["Gate"] and res2["qualified_only"] == []


def test_audit_without_base_omits_default_flags():
    text = "**Protected branches**: main\n**Gate**: g\n"
    res = v3.audit_keys(text, text, None, AUDIT_RULE)
    assert res["missing_required"] == []
    assert "template_default_changed" not in res["required"]["Gate"]
    assert res["warnings"] == ["audit_base_unavailable"]


def test_audit_placeholder_key_divergence():
    proj = "**Gate**: g\n**Test**: uv run pytest\n**Build (desktop)**: uv sync --extra dev\n"
    tpl = "**Gate**: g\n**Test**: t\n**Build**: b\n"
    res = v3.audit_keys(proj, tpl, None, AUDIT_RULE,
                        placeholders={"BUILD_COMMAND": "uv run pytest", "TEST_COMMAND": "uv  run pytest"})
    assert res["placeholder_key_divergence"] == [{
        "key": "Build (desktop)", "key_value": "uv sync --extra dev",
        "placeholder": "BUILD_COMMAND", "placeholder_value": "uv run pytest",
    }]                                     # Test matches after whitespace normalisation
    res2 = v3.audit_keys(proj, tpl, None, AUDIT_RULE, placeholders={})
    assert [d["placeholder_value"] for d in res2["placeholder_key_divergence"]] == [None, None]


def test_gate_refs_direct_only():
    rules = v3.OwnershipRules(OWNERSHIP["rules"], ["templates"], [])
    keys = {"Gate": "bash hooks/run-gate.sh --strict", "Test": "pytest -q", "Protected branches": "main master"}
    assert v3.gate_refs(keys, AUDIT_RULE, rules) == [{"key": "Gate", "path": "hooks/run-gate.sh"}]
    # ARM A (open-brain): a wrapper outside template class passes the static check.
    assert v3.gate_refs({"Gate": "bash scripts/gate.sh", "Test": "t"}, AUDIT_RULE, rules) == []
    assert v3.gate_refs({"Gate": "none", "Test": "true"}, AUDIT_RULE, rules) == []
    # open-brain's raw line: list marker + backticked value (review §11a).
    keys_bt = v3.parse_keys("- **Gate**: `bash ./hooks/run-gate.sh`\n**Protected branches**: main\n")
    assert v3.gate_refs(keys_bt, AUDIT_RULE, rules) == [{"key": "Gate", "path": "hooks/run-gate.sh"}]
    # Decoration is stripped from the deprecated spelling too.
    assert v3.gate_refs({"Gate Command": "`hooks/run-gate.sh`"}, AUDIT_RULE, rules) == [
        {"key": "Gate Command", "path": "hooks/run-gate.sh"}]


def test_collect_gate_refs(tmp_path):
    repo, proj = _mk_v3(tmp_path, template={},
                        project={"PROJECT_CONTEXT.md": "**Gate**: bash hooks/run-gate.sh\n**Test**: t\n"},
                        entries={})
    rules = v3.load_ownership(str(repo))
    assert v3.collect_gate_refs(proj, rules) == ([{"key": "Gate", "path": "hooks/run-gate.sh"}], True)
    (proj / "PROJECT_CONTEXT.md").write_text("**Test**: t\n", encoding="utf-8")
    assert v3.collect_gate_refs(proj, rules) == ([], False)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_template_sync_v3_status.py -q`
Expected: FAIL — `AttributeError: ... 'parse_keys'`

- [ ] **Step 3: Append the audit to the v3 module**

```python
# -------------------------
# Key audit (once files with "audit": "keys")
# -------------------------

KEY_LINE_RE = re.compile(r"^(?:[-*+][ \t]+)?\*\*(?P<key>[^*\n]+?)\*\*:[ \t]*(?P<val>.*)$", re.M)
PLACEHOLDER_RE = re.compile(r"\{\{.*?\}\}")


def parse_keys(text: str) -> dict[str, str]:
    keys: dict[str, str] = {}
    for m in KEY_LINE_RE.finditer(text or ""):
        key = m.group("key").strip()
        if key not in keys:
            keys[key] = m.group("val").strip()
    return keys


def find_key(consumer_keys: dict[str, str], key: str, rule: dict) -> list[str]:
    """Names in the consumer that satisfy `key`: exact, qualified `key (...)`,
    or a declared alias (review §7b). Exact first, then in document order."""
    matches = []
    if key in consumer_keys:
        matches.append(key)
    qualified = re.compile(r"^" + re.escape(key) + r" \(.+\)$")
    aliases = set((rule.get("aliases") or {}).get(key, []))
    for name in consumer_keys:
        if name == key:
            continue
        if qualified.match(name) or name in aliases:
            matches.append(name)
    return matches


def _norm_ws(s: str) -> str:
    return " ".join((s or "").split())


def exact_holdings(consumer_keys: dict[str, str], key: str, rule: dict) -> list[str]:
    """The hook's own match for a REQUIRED key: exact `**Key**:` or one of its
    deprecated spellings (review §11b). Qualified/alias forms do not count."""
    deprecated_map = dict(rule.get("deprecated_keys") or {})
    out = [key] if key in consumer_keys else []
    out += [old for old, new in deprecated_map.items() if new == key and old in consumer_keys]
    return out


def _hook_note(key: str, rule: dict) -> str:
    deprecated_map = dict(rule.get("deprecated_keys") or {})
    spellings = [f"**{key}**:"] + [f"**{old}**:" for old, new in deprecated_map.items() if new == key]
    return f"the hook matches {'/'.join(spellings)} exactly and will not read it"


def audit_keys(proj_text: str, tpl_text: str, tpl_at_sync_text: str | None, rule: dict,
               placeholders: dict | None = None) -> dict:
    proj = parse_keys(proj_text)
    tpl = parse_keys(tpl_text)
    tpl_sync = parse_keys(tpl_at_sync_text) if tpl_at_sync_text is not None else None
    required = list(rule.get("required_keys") or [])
    deprecated_map = dict(rule.get("deprecated_keys") or {})
    warnings: list[str] = []
    if tpl_sync is None:
        warnings.append("audit_base_unavailable")

    missing_required: list[str] = []
    qualified_only: list[dict] = []
    optional_absent: list[str] = []
    detail: dict[str, dict] = {}

    def _required(key: str) -> None:
        held = exact_holdings(proj, key, rule)
        if not held:
            loose = find_key(proj, key, rule)
            if loose:
                qualified_only.append({"key": key, "held_as": loose, "note": _hook_note(key, rule)})
            else:
                missing_required.append(key)
            return
        info = {
            "value": proj[held[0]],
            "matched_as": held,
            "template_value": tpl.get(key),
        }
        if tpl_sync is not None:
            at_sync = tpl_sync.get(key)
            info["template_value_at_sync"] = at_sync
            info["template_default_changed"] = _norm_ws(tpl.get(key) or "") != _norm_ws(at_sync or "")
            info["consumer_holds_old_default"] = (
                at_sync is not None and _norm_ws(proj[held[0]]) == _norm_ws(at_sync)
            )
        detail[key] = info

    for key in tpl:
        if key in required:
            _required(key)
        elif not find_key(proj, key, rule):
            optional_absent.append(key)

    # Required keys the template itself lacks are still required.
    for key in required:
        if key not in tpl:
            _required(key)

    placeholder_keys = sorted(k for k, val in proj.items() if PLACEHOLDER_RE.search(val))
    deprecated = [
        {"key": k, "replacement": deprecated_map[k]}
        for k in proj if k in deprecated_map
    ]
    # Placeholder VALUE correctness (review §10b): the key's value in the
    # once file versus the manifest placeholder that renders into CLAUDE.md.
    divergence = []
    ph = placeholders or {}
    for key, ph_name in (rule.get("placeholder_map") or {}).items():
        for name in find_key(proj, key, rule):
            ph_value = ph.get(ph_name)
            if ph_value is None or _norm_ws(proj[name]) != _norm_ws(ph_value):
                divergence.append({"key": name, "key_value": proj[name],
                                   "placeholder": ph_name, "placeholder_value": ph_value})
    return {
        "missing_required": missing_required,
        "qualified_only": qualified_only,
        "optional_absent": optional_absent,
        "placeholder_keys": placeholder_keys,
        "deprecated_keys": deprecated,
        "required": detail,
        "placeholder_key_divergence": divergence,
        "warnings": warnings,
    }


def gate_tokens(value: str) -> list[str]:
    """Tokenise a **Gate**:/**Test**: value the way run-gate.sh normalises it
    (review §11a): surrounding backticks off the whole value and each token,
    leading ./ dropped, a leading bash/sh token ignored."""
    value = (value or "").strip().strip("`").strip()
    toks = [core._normalize_path(t.strip("\"'`")) for t in value.split()]
    if toks and toks[0] in ("bash", "sh"):
        toks = toks[1:]
    out = []
    for t in toks:
        while t.startswith("./"):
            t = t[2:]
        if t:
            out.append(t)
    return out


def gate_refs(consumer_keys: dict[str, str], rule: dict, rules: OwnershipRules) -> list[dict]:
    """Direct gate self-reference (review §9a, §11a): a token in a required
    key's value that is a template-class path by the rules. Static and
    direct-only -- a wrapper that calls the hook from elsewhere passes."""
    out = []
    for key in rule.get("required_keys") or []:
        for name in exact_holdings(consumer_keys, key, rule):
            for tok in gate_tokens(consumer_keys[name]):
                if rules.class_of(rules.template_path_for(tok)) == "template":
                    out.append({"key": name, "path": tok})
    return out


def collect_gate_refs(pp: pathlib.Path, rules: OwnershipRules) -> tuple[list[dict], bool]:
    """(gate_self_reference hits, gate declared) over every audited once file
    whose pattern is a literal path."""
    hits: list[dict] = []
    declared = False
    for rule in rules.rules:
        if rule.get("audit") != "keys" or any(c in rule["pattern"] for c in "*?["):
            continue
        text = core._read_file(pp / rules.project_path_for(rule["pattern"]))
        if text is None:
            continue
        keys = parse_keys(text)
        if exact_holdings(keys, "Gate", rule):
            declared = True
        hits.extend(gate_refs(keys, rule, rules))
    return hits, declared
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_template_sync_v3_status.py -q`
Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
git add src/mcp_dev_servers/template_sync_v3.py tests/test_template_sync_v3_status.py
git commit -F <scratch file>   # "feat(template-sync): key audit for once-class files (required/optional/placeholder/deprecated, resolved values, gate self-reference)"
```

---

### Task 5: Orphan scan and `template_notes_changed`

**Files:**
- Modify: `src/mcp_dev_servers/template_sync_v3.py` (append)
- Test: `tests/test_template_sync_v3_status.py` (append)

**Interfaces:**
- Consumes: `OwnershipRules.template_class_rules`, `glob_to_regex`, `core._normalize_path`.
- Produces:
  - `static_prefix(pattern: str) -> str` — path segments before the first wildcard (`hooks/**` → `hooks`, `.claude/agents/*.md` → `.claude/agents`, `CLAUDE.md` → `CLAUDE.md`)
  - `find_orphans(project_root: pathlib.Path, rules: OwnershipRules, manifest_keys: set[str], template_files: set[str]) -> list[str]`
  - `notes_hunks(tpl_at_sync: str, tpl_now: str) -> list[str]` — unified-diff hunks (each hunk one string) whose changed lines contain no key line
  - `read_with_flags(path: pathlib.Path) -> tuple[str | None, dict]` — one `read_bytes`, returns the text exactly as `_read_file` would (BOM stripped, EOL folded) plus `{"bom": bool, "crlf": bool}` (review §8b)
  - `encoding_drift(proj_flags: dict, tpl_flags: dict) -> list[str]` — subset of `["bom", "crlf"]` where the flags differ

- [ ] **Step 1: Append the failing tests**

```python
# append to tests/test_template_sync_v3_status.py

def test_static_prefix():
    assert v3.static_prefix("hooks/**") == "hooks"
    assert v3.static_prefix(".claude/agents/*.md") == ".claude/agents"
    assert v3.static_prefix("CLAUDE.md") == "CLAUDE.md"
    assert v3.static_prefix("*.md") == ""


def test_find_orphans_matches_template_rules_only(tmp_path):
    repo, proj = _mk_v3(
        tmp_path,
        template={"hooks/gate.sh": "g", ".claude/agents/coder.md": "c"},
        project={
            "hooks/gate.sh": "g", "hooks/mine.sh": "m",
            ".claude/agents/coder.md": "c", ".claude/agents/game-tester.md": "t",
            ".claude/agents/local-notes.md": "n",
            ".claude/rules/project.md": "p", "src/main.py": "x",
            "preflight.sh": "gate logic on purpose\n",       # panoscribe's escape hatch (review §10c)
        },
        entries={"hooks/gate.sh": _tpl_entry("g"), ".claude/agents/coder.md": _tpl_entry("c")},
        ownership={"tracked_paths": ["templates", "hooks"],
                   "rules": OWNERSHIP["rules"] + [{"pattern": "*.sh", "ownership": "template"}]},
    )
    rules = v3.load_ownership(str(repo))
    orphans = v3.find_orphans(proj, rules, set(["hooks/gate.sh", ".claude/agents/coder.md"]),
                              set(["hooks/gate.sh", ".claude/agents/coder.md"]))
    assert orphans == [".claude/agents/game-tester.md", "hooks/mine.sh", "preflight.sh"]
    # Listed only: a root-level consumer file matching a template rule with no
    # manifest entry is never in `files`, never diffed, never applied.
    res = _run(ts.template_compute_status(str(proj)))
    assert "preflight.sh" not in res["files"]
    assert "preflight.sh" in res["orphans"]


def test_notes_hunks_skip_key_line_changes():
    old = "# Ctx\n<!-- how Gate is parsed -->\n**Gate**: a\n**Test**: t\n"
    new = "# Ctx\n<!-- how Gate and Test are parsed -->\n**Gate**: b\n**Test**: t\n"
    hunks = v3.notes_hunks(old, new)
    assert len(hunks) == 1
    assert "how Gate and Test are parsed" in hunks[0]
    assert "**Gate**: b" not in hunks[0]


def test_read_with_flags_and_drift(tmp_path):
    p = tmp_path / "f.txt"
    p.write_bytes(b"\xef\xbb\xbfa\r\nb\r\n")
    text, flags = v3.read_with_flags(p)
    assert text == "a\nb\n"
    assert flags == {"bom": True, "crlf": True}
    assert ts._sha256(text) == ts._sha256("a\nb\n")
    assert v3.encoding_drift(flags, {"bom": False, "crlf": False}) == ["bom", "crlf"]
    assert v3.encoding_drift(flags, flags) == []
    assert v3.read_with_flags(tmp_path / "missing") == (None, {"bom": False, "crlf": False})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_template_sync_v3_status.py -q`
Expected: 3 new FAIL — `AttributeError: ... 'static_prefix'`

- [ ] **Step 3: Append the implementation**

```python
# -------------------------
# Orphans and template notes
# -------------------------

def static_prefix(pattern: str) -> str:
    segs = []
    for seg in core._normalize_path(pattern).split("/"):
        if any(c in seg for c in "*?["):
            break
        segs.append(seg)
    return "/".join(segs)


def find_orphans(project_root: pathlib.Path, rules: OwnershipRules,
                 manifest_keys: set[str], template_files: set[str]) -> list[str]:
    """Consumer files that match a template-class rule, sit in no manifest entry
    and are not shipped by the template (review §5b). Informational only."""
    found: set[str] = set()
    for rule in rules.template_class_rules():
        rx = glob_to_regex(rule["pattern"])
        root = project_root / static_prefix(rule["pattern"])
        if root.is_file():
            candidates = [root]
        elif root.is_dir():
            candidates = [p for p in root.rglob("*") if p.is_file()]
        else:
            continue
        for p in candidates:
            rel = core._normalize_path(str(p.relative_to(project_root)))
            if not rx.match(rel):
                continue
            # First match over ALL rules decides; a preceding `project` rule silences.
            if rules.class_of(rel) != "template":
                continue
            if rel in manifest_keys or rel in template_files:
                continue
            found.add(rel)
    return sorted(found)


def notes_hunks(tpl_at_sync: str, tpl_now: str) -> list[str]:
    """Hunks of the template-side diff that touch no **Key**: line (review §7c)."""
    from difflib import unified_diff
    lines = list(unified_diff(
        tpl_at_sync.splitlines(keepends=True), tpl_now.splitlines(keepends=True),
        fromfile="template@sync", tofile="template@now", n=1,
    ))
    hunks: list[list[str]] = []
    for line in lines:
        if line.startswith("@@"):
            hunks.append([line])
        elif hunks and not line.startswith(("---", "+++")):
            hunks[-1].append(line)
    out = []
    for h in hunks:
        changed = [l[1:] for l in h[1:] if l[:1] in "+-"]
        if any(KEY_LINE_RE.match(l.rstrip("\n")) for l in changed):
            continue
        out.append("".join(h))
    return out


NO_FLAGS = {"bom": False, "crlf": False}


def read_with_flags(path: pathlib.Path) -> tuple[str | None, dict]:
    """Read once as bytes; return the text the way _read_file would see it
    (BOM stripped, CRLF/CR folded to LF) plus the encoding flags (review §8)."""
    try:
        data = path.read_bytes()
    except (FileNotFoundError, OSError):
        return None, dict(NO_FLAGS)
    flags = {"bom": data.startswith(b"\xef\xbb\xbf"), "crlf": b"\r\n" in data}
    text = data.decode("utf-8", errors="replace")
    if text.startswith("\ufeff"):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text, flags


def encoding_drift(proj_flags: dict, tpl_flags: dict) -> list[str]:
    return sorted(k for k in ("bom", "crlf") if bool(proj_flags.get(k)) != bool(tpl_flags.get(k)))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_template_sync_v3_status.py -q`
Expected: `13 passed`

- [ ] **Step 5: Commit**

```bash
git add src/mcp_dev_servers/template_sync_v3.py tests/test_template_sync_v3_status.py
git commit -F <scratch file>   # "feat(template-sync): orphan scan under template-class rules; template_notes_changed hunks; encoding flags"
```

---

### Task 6: `template_compute_status` v3 branch

**Files:**
- Modify: `src/mcp_dev_servers/template_sync_v3.py` (append `compute_status_v3`, `template_status`)
- Modify: `src/mcp_dev_servers/template_sync_mcp.py:722-906` (`template_compute_status` — dispatch at the top)
- Test: `tests/test_template_sync_v3_status.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 1–5; `core._read_file`, `core._apply_placeholders`, `core._sha256`, `core._template_file_path`, `core._template_git_path`, `core._scan_template_files`, `core._get_template_dir`, `core._resolve_path`, `core._git_head`, `core._git_show_file`, `core._template_repo_resolved`.
- Produces:
  - `template_status(entry_hash_hex: str, tpl_replaced: str | None, proj_content: str | None) -> tuple[str, str | None]` → `(status, local_diff)`
  - `resolve_base(manifest: dict, rel_path: str) -> tuple[str | None, str, str | None]` → `(placeholder-replaced content or None, base_label, warning)`; chain `template_commit` → `template_version^{commit}` → `("unavailable", "migration_base_unavailable")` (review §6.4)
  - `compute_status_v3(pp: pathlib.Path, manifest: dict, rules: OwnershipRules) -> dict` — the full JSON-able status result: `template_commit`, `template_version`, `last_synced_commit`, `files` (each with `encoding_drift`), `new_template_files`, `unclassified_template_files`, `orphans`, `deleted_template_files`, `gate_self_reference`, `gate_unverified`, `summary` (`identical`, `template_updated`, `local_edited`, `template_deleted`, `present`, `missing`), `warnings`

- [ ] **Step 1: Append the failing tests**

```python
# append to tests/test_template_sync_v3_status.py

def _status(proj):
    return _run(ts.template_compute_status(str(proj)))


def test_template_status_matrix():
    h = ts._sha256("v1\n")
    assert v3.template_status(h, "v1\n", "v1\n") == ("IDENTICAL", None)
    assert v3.template_status(h, "v2\n", "v1\n") == ("TEMPLATE_UPDATED", None)
    status, diff = v3.template_status(h, "v1\n", "v1 edited\n")
    assert status == "LOCAL_EDITED" and "+v1 edited" in diff
    status, diff = v3.template_status(h, "v2\n", "v1 edited\n")
    assert status == "LOCAL_EDITED" and "-v2" in diff and "+v1 edited" in diff
    assert v3.template_status(h, None, "v1\n") == ("TEMPLATE_DELETED", None)
    assert v3.template_status(h, "v1\n", None) == ("TEMPLATE_UPDATED", None)


def test_compute_status_v3_classifies_every_class(tmp_path):
    repo, proj = _mk_v3(
        tmp_path,
        template={
            "CLAUDE.md": "# {{NAME}}\n", "hooks/gate.sh": "g2", ".claude/agents/coder.md": "c",
            ".claude/rules/project.md": "# Project instructions\n",
            "PROJECT_CONTEXT.md": "**Gate**: main\n**Test**: t\n",
            ".claude/agents/new.md": "n", "gitignore": "*.log\n",
            "notes/x.md": "x",
        },
        project={
            "CLAUDE.md": "# Demo\n", "hooks/gate.sh": "g1", ".claude/agents/coder.md": "c edited",
            "PROJECT_CONTEXT.md": "**Gate**: main\n", ".claude/agents/game-tester.md": "t",
            ".claude/settings.json": "{}",
        },
        entries={
            "CLAUDE.md": _tpl_entry("# {{NAME}}\n", {"NAME": "Demo"}),
            "hooks/gate.sh": _tpl_entry("g1"),
            ".claude/agents/coder.md": _tpl_entry("c"),
            ".claude/settings.json": _tpl_entry("{}"),
            ".claude/rules/project.md": {"ownership": "once"},
            "PROJECT_CONTEXT.md": {"ownership": "once"},
        },
        placeholders={"NAME": "Demo"},
        ownership={"tracked_paths": ["templates", "hooks"], "rules": OWNERSHIP["rules"] + [
            {"pattern": "notes/*", "ownership": "project"},
        ]},
    )
    (repo / "templates" / "general" / ".claude" / "settings.json").parent.mkdir(parents=True, exist_ok=True)
    (repo / "templates" / "general" / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    res = _status(proj)
    f = res["files"]
    assert f["CLAUDE.md"]["status"] == "IDENTICAL"
    assert f["hooks/gate.sh"]["status"] == "TEMPLATE_UPDATED"
    assert f[".claude/agents/coder.md"]["status"] == "LOCAL_EDITED"
    assert "+c edited" in f[".claude/agents/coder.md"]["local_diff"]
    assert f[".claude/settings.json"]["status"] == "IDENTICAL"
    assert f[".claude/rules/project.md"]["status"] == "MISSING"
    assert f["PROJECT_CONTEXT.md"]["status"] == "PRESENT"
    assert f["PROJECT_CONTEXT.md"]["key_audit"]["missing_required"] == ["Test"]
    assert "audit_base_unavailable" in f["PROJECT_CONTEXT.md"]["key_audit"]["warnings"]
    assert res["new_template_files"] == [".claude/agents/new.md", ".gitignore"]
    assert res["unclassified_template_files"] == []
    assert res["orphans"] == [".claude/agents/game-tester.md"]
    assert res["summary"] == {
        "identical": 2, "template_updated": 1, "local_edited": 1, "template_deleted": 0,
        "present": 1, "missing": 1,
    }
    assert "CONFLICT" not in json.dumps(res)


def test_compute_status_v3_unclassified_and_deleted(tmp_path):
    repo, proj = _mk_v3(
        tmp_path,
        template={"CLAUDE.md": "x", "weird.toml": "w"},
        project={"CLAUDE.md": "x", "hooks/gone.sh": "old"},
        entries={"CLAUDE.md": _tpl_entry("x"), "hooks/gone.sh": _tpl_entry("old")},
    )
    res = _status(proj)
    assert res["unclassified_template_files"] == ["weird.toml"]
    assert res["files"]["hooks/gone.sh"]["status"] == "TEMPLATE_DELETED"
    assert res["deleted_template_files"] == ["hooks/gone.sh"]
    assert res["summary"]["template_deleted"] == 1
    assert res["gate_self_reference"] == [] and res["gate_unverified"] is False


def test_compute_status_v3_encoding_drift_and_gate(tmp_path):
    repo, proj = _mk_v3(
        tmp_path,
        template={"hooks/run-gate.sh": "g\n", ".prettierrc": "{}\n",
                  "PROJECT_CONTEXT.md": "**Gate**: none\n**Test**: t\n"},
        project={"hooks/run-gate.sh": "g\r\n",
                 "PROJECT_CONTEXT.md": "**Gate**: bash hooks/run-gate.sh\n**Test**: t\n"},
        entries={"hooks/run-gate.sh": _tpl_entry("g\n"), ".prettierrc": {"ownership": "once"},
                 "PROJECT_CONTEXT.md": {"ownership": "once"}},
        ownership={"tracked_paths": ["templates", "hooks"],
                   "rules": OWNERSHIP["rules"] + [{"pattern": ".prettierrc", "ownership": "once"}]},
    )
    (proj / ".prettierrc").write_bytes(b"\xef\xbb\xbf{}\r\n")
    res = _status(proj)
    f = res["files"]
    assert f["hooks/run-gate.sh"]["status"] == "IDENTICAL"          # CRLF-only is never LOCAL_EDITED
    assert f["hooks/run-gate.sh"]["encoding_drift"] == ["crlf"]
    assert f[".prettierrc"]["status"] == "PRESENT"
    assert f[".prettierrc"]["encoding_drift"] == ["bom", "crlf"]
    assert f["PROJECT_CONTEXT.md"]["encoding_drift"] == []
    assert res["gate_self_reference"] == [{"key": "Gate", "path": "hooks/run-gate.sh"}]
    assert res["gate_unverified"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_template_sync_v3_status.py -q`
Expected: 3 new FAIL — `AttributeError: ... 'template_status'`

- [ ] **Step 3: Append `template_status`, `resolve_base`, `compute_status_v3` to the v3 module**

```python
# -------------------------
# v3 status
# -------------------------

def _unified(a: str, b: str, fromfile: str, tofile: str) -> str:
    from difflib import unified_diff
    return "".join(unified_diff(
        a.splitlines(keepends=True), b.splitlines(keepends=True),
        fromfile=fromfile, tofile=tofile,
    ))


def template_status(entry_hash_hex: str, tpl_replaced: str | None,
                    proj_content: str | None) -> tuple[str, str | None]:
    """§7 statuses for a `template` entry. LOCAL_EDITED wins over
    TEMPLATE_UPDATED; local_diff is what the overwrite would discard
    (project -> current template)."""
    if tpl_replaced is None:
        return "TEMPLATE_DELETED", None
    if proj_content is None:
        return "TEMPLATE_UPDATED", None
    if core._sha256(proj_content) != entry_hash_hex:
        return "LOCAL_EDITED", _unified(tpl_replaced, proj_content, "template", "project")
    if core._sha256(tpl_replaced) != entry_hash_hex:
        return "TEMPLATE_UPDATED", None
    return "IDENTICAL", None


def _git_commit_exists(repo: str, ref: str) -> bool:
    return core._run_git(["cat-file", "-e", f"{ref}^{{commit}}"], cwd=repo)["exit_code"] == 0


def resolve_base(manifest: dict, rel_path: str) -> tuple[str | None, str, str | None]:
    """Placeholder-replaced template content of `rel_path` at the held revision.

    Chain (review §6.4): template_commit (alias lastSynced) -> the
    template_version tag's commit -> unavailable. Never the current template.
    Returns (content, base_label, warning).
    """
    repo = core._template_repo_resolved(manifest)
    git_path = core._template_git_path(manifest, rel_path)
    placeholders = manifest.get("placeholders", {})
    candidates = []
    commit = manifest_commit(manifest)
    if commit and commit != "unknown":
        candidates.append(commit)
    tag = manifest.get("template_version")
    if tag:
        candidates.append(str(tag))
    for ref in candidates:
        if not _git_commit_exists(repo, ref):
            continue
        raw = core._git_show_file(repo, ref, git_path)
        if raw is not None:
            return core._apply_placeholders(raw, placeholders), ref, None
    return None, "unavailable", "migration_base_unavailable"


def compute_status_v3(pp: pathlib.Path, manifest: dict, rules: OwnershipRules) -> dict:
    placeholders = manifest.get("placeholders", {})
    repo_root = core._resolve_path(manifest["templateRepo"])
    template_dir = core._get_template_dir(manifest)
    warnings = list(rules.warnings)
    files_status: dict[str, dict] = {}
    summary = {
        "identical": 0, "template_updated": 0, "local_edited": 0,
        "template_deleted": 0, "present": 0, "missing": 0,
    }

    for proj_rel, entry in manifest.get("files", {}).items():
        proj_rel = core._normalize_path(proj_rel)
        tpl_rel = rules.template_path_for(proj_rel)
        ownership = entry.get("ownership") or rules.class_of(tpl_rel) or "template"
        tpl_raw, tpl_flags = read_with_flags(core._template_file_path(manifest, tpl_rel))
        tpl_replaced = core._apply_placeholders(tpl_raw, placeholders) if tpl_raw is not None else None
        proj_content, proj_flags = read_with_flags(pp / proj_rel)
        info: dict = {"ownership": ownership, "template_path": tpl_rel,
                      "project_file_missing": proj_content is None,
                      "encoding_drift": (encoding_drift(proj_flags, tpl_flags)
                                         if proj_content is not None and tpl_raw is not None else [])}

        if ownership == "once":
            if proj_content is not None:
                status = "PRESENT"
                rule = rules.rule_for(tpl_rel) or {}
                if rule.get("audit") == "keys" and tpl_replaced is not None:
                    base, base_label, warn = resolve_base(manifest, tpl_rel)
                    audit = audit_keys(proj_content, tpl_replaced, base, rule, placeholders)
                    audit["base"] = base_label
                    if base is not None:
                        audit["template_notes_changed"] = notes_hunks(base, tpl_replaced)
                    info["key_audit"] = audit
            elif tpl_replaced is None:
                status = "TEMPLATE_DELETED"
            else:
                status = "MISSING"
        else:
            status, local_diff = template_status(parse_hash(entry.get("hash", "")), tpl_replaced, proj_content)
            if local_diff is not None:
                info["local_diff"] = local_diff
            info["template_changed"] = (
                tpl_replaced is not None and core._sha256(tpl_replaced) != parse_hash(entry.get("hash", ""))
            )

        info["status"] = status
        summary[status.lower()] += 1
        files_status[proj_rel] = info

    # New template files: template/once paths absent from the manifest.
    tracked = {core._normalize_path(k) for k in manifest.get("files", {})}
    scanned = core._scan_template_files(template_dir, repo_root)
    gitignore = template_dir / "gitignore"
    if gitignore.is_file():
        scanned.append("gitignore")   # _scan_template_files skips it; the rules decide now
    new_files, unclassified = [], []
    template_files: set[str] = set()
    for tpl_rel in sorted(set(scanned)):
        cls = rules.class_of(tpl_rel)
        proj_rel = rules.project_path_for(tpl_rel)
        template_files.add(proj_rel)
        if proj_rel in tracked:
            continue
        if cls in ("template", "once"):
            new_files.append(proj_rel)
        elif cls is None:
            unclassified.append(tpl_rel)

    orphans = find_orphans(pp, rules, tracked, template_files)
    deleted = [p for p, s in files_status.items() if s["status"] == "TEMPLATE_DELETED"]
    gate_hits, gate_declared = collect_gate_refs(pp, rules)

    return {
        "manifest_version": 3,
        "template_commit": core._git_head(core._template_repo_resolved(manifest)) or "unknown",
        "template_version": manifest.get("template_version"),
        "last_synced_commit": manifest_commit(manifest),
        "files": files_status,
        "new_template_files": sorted(new_files),
        "unclassified_template_files": sorted(unclassified),
        "orphans": orphans,
        "deleted_template_files": deleted,
        "gate_self_reference": gate_hits,
        "gate_unverified": gate_declared,
        "summary": summary,
        "warnings": warnings,
    }
```

- [ ] **Step 4: Dispatch from `template_compute_status`**

In `template_sync_mcp.py`, directly after the `if variant: manifest["variant"] = variant` block (line ~756), insert:

```python
    from . import template_sync_v3 as v3
    if v3.is_v3(manifest):
        rules = v3.load_ownership(manifest["templateRepo"])
        if rules is None:
            return json.dumps({"error": f"manifest v3 needs {v3.OWNERSHIP_FILE} in the template repo"}, ensure_ascii=False)
        return json.dumps(v3.compute_status_v3(pp, manifest, rules), ensure_ascii=False)
```

Extend the docstring's Returns paragraph with:

```
        For a v3 manifest the statuses are IDENTICAL / TEMPLATE_UPDATED /
        LOCAL_EDITED / TEMPLATE_DELETED (template class) and PRESENT / MISSING
        (once class); the result also carries `orphans`,
        `unclassified_template_files`, `local_diff` per LOCAL_EDITED file and
        `key_audit` per audited once file, `encoding_drift` per file (BOM/EOL
        only differences, informational), and `gate_self_reference` /
        `gate_unverified` at top level. CONFLICT never appears for v3.
```

- [ ] **Step 5: Run the tests and the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_template_sync_v3_status.py -q && .venv/Scripts/python -m pytest -q`
Expected: `17 passed` then `135 passed`

- [ ] **Step 6: Commit**

```bash
git add src/mcp_dev_servers/template_sync_v3.py src/mcp_dev_servers/template_sync_mcp.py tests/test_template_sync_v3_status.py
git commit -F <scratch file>   # "feat(template-sync): v3 status classification replaces CONFLICT (class x state, local_diff, orphans, key audit, encoding_drift, gate_self_reference)"
```

---

### Task 7: `template_apply_file` v3 branch with `backup_dir`

**Files:**
- Modify: `src/mcp_dev_servers/template_sync_v3.py` (append `apply_file_v3`)
- Modify: `src/mcp_dev_servers/template_sync_mcp.py:1073-1194` (`template_apply_file` — new `backup_dir` parameter, dispatch)
- Test: `tests/test_template_sync_v3_apply.py`

**Interfaces:**
- Consumes: `template_status`, `parse_hash`, `format_hash`, `OwnershipRules`, `core._write_file_atomic`, `core._read_file`, `core._apply_placeholders`, `core._sha256`, `core._template_file_path`.
- Produces:
  - `write_backup(backup_dir: pathlib.Path, proj_rel: str, pre_image: str, diff: str) -> dict` → `{"pre_sync": str, "diff": str}` (absolute paths written)
  - `apply_file_v3(pp, manifest, rules, file_path, source, content, backup_dir) -> dict` — result keys `file_path`, `action` (`written_from_template` | `written_from_provided` | `kept` | `created_from_template` | `created_from_provided`), `ownership`, `manifest_entry`, `bytes_written`, `backup` (dict or null), `local_edit_overwritten` (bool)
  - `template_apply_file(project_path, file_path, source="template", content="", backup_dir="")` — the tool signature after this task

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_template_sync_v3_apply.py
"""template_apply_file under manifest v3: backup_dir, once, skip refused (review §2.7, §2.8)."""

import asyncio
import json

import pytest

from mcp_dev_servers import template_sync_mcp as ts
from mcp_dev_servers import template_sync_v3 as v3

# (paste the shared fixture block: OWNERSHIP, _mk_v3, _tpl_entry, _run)


def _apply(proj, **kw):
    return _run(ts.template_apply_file(str(proj), **kw))


def test_template_identical_or_updated_writes_without_backup(tmp_path):
    repo, proj = _mk_v3(tmp_path, template={"CLAUDE.md": "v2 {{NAME}}\n"},
                        project={"CLAUDE.md": "v1 Demo\n"},
                        entries={"CLAUDE.md": _tpl_entry("v1 {{NAME}}\n", {"NAME": "Demo"})},
                        placeholders={"NAME": "Demo"})
    res = _apply(proj, file_path="CLAUDE.md")
    assert res["action"] == "written_from_template"
    assert res["backup"] is None
    assert res["local_edit_overwritten"] is False
    assert res["manifest_entry"] == {"hash": "sha256:" + ts._sha256("v2 Demo\n"), "ownership": "template"}
    assert (proj / "CLAUDE.md").read_text(encoding="utf-8") == "v2 Demo\n"


def test_local_edit_refused_without_backup_dir(tmp_path):
    repo, proj = _mk_v3(tmp_path, template={"CLAUDE.md": "v1\n"}, project={"CLAUDE.md": "v1 mine\n"},
                        entries={"CLAUDE.md": _tpl_entry("v1\n")})
    res = _apply(proj, file_path="CLAUDE.md")
    assert "backup_dir" in res["error"]
    assert (proj / "CLAUDE.md").read_text(encoding="utf-8") == "v1 mine\n"


def test_local_edit_backed_up_then_overwritten(tmp_path):
    repo, proj = _mk_v3(tmp_path, template={"hooks/gate.sh": "v1\n"}, project={"hooks/gate.sh": "v1 mine\n"},
                        entries={"hooks/gate.sh": _tpl_entry("v1\n")})
    backup = tmp_path / "backup"
    res = _apply(proj, file_path="hooks/gate.sh", backup_dir=str(backup))
    assert res["local_edit_overwritten"] is True
    assert (backup / "hooks" / "gate.sh.pre-sync").read_text(encoding="utf-8") == "v1 mine\n"
    diff = (backup / "hooks" / "gate.sh.diff").read_text(encoding="utf-8")
    assert "+v1 mine" in diff
    assert res["backup"]["pre_sync"].endswith("gate.sh.pre-sync")
    assert (proj / "hooks" / "gate.sh").read_text(encoding="utf-8") == "v1\n"


def test_skip_refused_on_template_class(tmp_path):
    repo, proj = _mk_v3(tmp_path, template={"CLAUDE.md": "v1\n"}, project={"CLAUDE.md": "v1 mine\n"},
                        entries={"CLAUDE.md": _tpl_entry("v1\n")})
    res = _apply(proj, file_path="CLAUDE.md", source="skip")
    assert "skip" in res["error"] and "template" in res["error"]


def test_provided_on_template_class(tmp_path):
    repo, proj = _mk_v3(tmp_path, template={"CLAUDE.md": "v1\n"}, project={}, entries={})
    res = _apply(proj, file_path="CLAUDE.md", source="provided", content="custom\n")
    assert res["action"] == "created_from_provided"
    assert res["manifest_entry"]["ownership"] == "template"
    assert res["manifest_entry"]["hash"] == "sha256:" + ts._sha256("v1\n")


def test_once_created_when_missing_and_kept_when_present(tmp_path):
    repo, proj = _mk_v3(tmp_path, template={".claude/rules/project.md": "# P\n", "gitignore": "*.log\n"},
                        project={}, entries={})
    res = _apply(proj, file_path=".claude/rules/project.md")
    assert res["action"] == "created_from_template"
    assert res["manifest_entry"] == {"ownership": "once"}
    assert (proj / ".claude" / "rules" / "project.md").read_text(encoding="utf-8") == "# P\n"

    (proj / ".claude" / "rules" / "project.md").write_text("mine\n", encoding="utf-8")
    res2 = _apply(proj, file_path=".claude/rules/project.md")
    assert res2["action"] == "kept"
    assert res2["bytes_written"] == 0
    assert (proj / ".claude" / "rules" / "project.md").read_text(encoding="utf-8") == "mine\n"

    res3 = _apply(proj, file_path=".gitignore")
    assert res3["action"] == "created_from_template"
    assert (proj / ".gitignore").read_text(encoding="utf-8") == "*.log\n"


def test_unclassified_path_refused(tmp_path):
    repo, proj = _mk_v3(tmp_path, template={"weird.toml": "w"}, project={}, entries={})
    res = _apply(proj, file_path="weird.toml")
    assert "no ownership rule" in res["error"]


def test_gate_self_reference_refused_before_any_write(tmp_path):
    repo, proj = _mk_v3(
        tmp_path, template={"hooks/run-gate.sh": "tpl\n"},
        project={"hooks/run-gate.sh": "tpl\n",
                 "PROJECT_CONTEXT.md": "**Gate**: bash hooks/run-gate.sh\n**Test**: t\n"},
        entries={"hooks/run-gate.sh": _tpl_entry("tpl\n"), "PROJECT_CONTEXT.md": {"ownership": "once"}},
    )
    res = _apply(proj, file_path="hooks/run-gate.sh", backup_dir=str(tmp_path / "b"))
    assert res["error"].startswith("gate_self_reference")
    assert "Gate" in res["error"]
    assert (proj / "hooks" / "run-gate.sh").read_text(encoding="utf-8") == "tpl\n"
    assert not (tmp_path / "b").exists()


def test_v2_manifest_path_unchanged(tmp_path):
    # A v2 manifest keeps the 0.2.x behaviour: source="skip" is still allowed.
    repo = tmp_path / "toolkit"
    (repo / "templates" / "general").mkdir(parents=True)
    (repo / "templates" / "general" / "CLAUDE.md").write_text("v1\n", encoding="utf-8")
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    (proj / "CLAUDE.md").write_text("mine\n", encoding="utf-8")
    (proj / ".claude" / "template-manifest.json").write_text(json.dumps({
        "version": 2, "templateRepo": str(repo), "variant": "general", "placeholders": {},
        "lastSynced": "", "files": {"CLAUDE.md": {"templateHash": ts._sha256("v1\n")}},
    }), encoding="utf-8")
    res = _apply(proj, file_path="CLAUDE.md", source="skip")
    assert res["action"] == "skipped"
    assert res["manifest_entry"]["resolution"] == "keep-mine"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_template_sync_v3_apply.py -q`
Expected: 8 FAIL (`TypeError: ... unexpected keyword argument 'backup_dir'` / `KeyError: 'backup'`), 1 PASS (`test_v2_manifest_path_unchanged`)

- [ ] **Step 3: Append `write_backup` and `apply_file_v3` to the v3 module**

```python
# -------------------------
# v3 apply
# -------------------------

def write_backup(backup_dir: pathlib.Path, proj_rel: str, pre_image: str, diff: str) -> dict:
    target = backup_dir / core._normalize_path(proj_rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    pre = target.with_name(target.name + ".pre-sync")
    dif = target.with_name(target.name + ".diff")
    core._write_file_atomic(pre, pre_image)
    core._write_file_atomic(dif, diff)
    return {"pre_sync": str(pre), "diff": str(dif)}


def apply_file_v3(pp: pathlib.Path, manifest: dict, rules: OwnershipRules, file_path: str,
                  source: str, content: str, backup_dir: str) -> dict:
    proj_rel = core._normalize_path(file_path)
    tpl_rel = rules.template_path_for(proj_rel)
    entry = manifest.get("files", {}).get(proj_rel) or manifest.get("files", {}).get(file_path) or {}
    ownership = entry.get("ownership") or rules.class_of(tpl_rel)
    if ownership is None:
        return {"error": f"{proj_rel}: no ownership rule matches {tpl_rel!r} in {OWNERSHIP_FILE} -- not applied"}
    if ownership == "project":
        return {"error": f"{proj_rel}: ownership is 'project'; the server never writes it"}
    if source not in ("template", "provided"):
        if source == "skip":
            return {"error": f"source='skip' is refused under manifest v3 for {ownership}-class files: "
                             "there is no keep-mine class -- fix the template or declare a key"}
        return {"error": f"Unknown source: {source}"}
    if source == "provided" and not content:
        return {"error": "source='provided' requires content parameter"}
    for hit in collect_gate_refs(pp, rules)[0]:
        if hit["path"] == proj_rel:
            return {"error": f"gate_self_reference: **{hit['key']}**: points at template-class {proj_rel}; "
                             "move the logic to a non-template path (e.g. scripts/gate.sh) and point the key there"}

    placeholders = manifest.get("placeholders", {})
    tpl_raw = core._read_file(core._template_file_path(manifest, tpl_rel))
    tpl_replaced = core._apply_placeholders(tpl_raw, placeholders) if tpl_raw is not None else None
    if source == "template" and tpl_replaced is None:
        return {"error": f"Template file not found: {tpl_rel}"}
    target = pp / proj_rel
    proj_existing = core._read_file(target)
    write_content = tpl_replaced if source == "template" else content

    if ownership == "once":
        if proj_existing is not None:
            return {
                "file_path": proj_rel, "action": "kept", "ownership": "once",
                "manifest_entry": {"ownership": "once"}, "bytes_written": 0,
                "backup": None, "local_edit_overwritten": False,
            }
        target.parent.mkdir(parents=True, exist_ok=True)
        core._write_file_atomic(target, write_content)
        return {
            "file_path": proj_rel, "action": f"created_from_{source}", "ownership": "once",
            "manifest_entry": {"ownership": "once"},
            "bytes_written": len(write_content.encode("utf-8")),
            "backup": None, "local_edit_overwritten": False,
        }

    # template class
    backup = None
    local_edit = False
    if proj_existing is not None:
        baseline = parse_hash(entry.get("hash", ""))
        if baseline:
            status, local_diff = template_status(baseline, tpl_replaced, proj_existing)
            local_edit = status == "LOCAL_EDITED"
        else:
            # No baseline (new file the project already has): any difference
            # from what will be written is a local edit.
            local_edit = proj_existing != write_content
            local_diff = _unified(write_content, proj_existing, "template", "project") if local_edit else None
        if local_edit and proj_existing != write_content:
            if not backup_dir:
                return {"error": f"{proj_rel} is LOCAL_EDITED; refusing to overwrite without backup_dir "
                                 "(the pre-image and diff must be saved first)"}
            backup = write_backup(pathlib.Path(backup_dir).resolve(), proj_rel, proj_existing, local_diff or "")
        elif local_edit:
            local_edit = False   # content already equals the target; nothing is lost

    target.parent.mkdir(parents=True, exist_ok=True)
    core._write_file_atomic(target, write_content)
    hash_hex = core._sha256(tpl_replaced) if tpl_replaced is not None else core._sha256(write_content)
    return {
        "file_path": proj_rel,
        "action": ("created" if proj_existing is None else "written") + f"_from_{source}",
        "ownership": "template",
        "manifest_entry": {"hash": format_hash(hash_hex), "ownership": "template"},
        "bytes_written": len(write_content.encode("utf-8")),
        "backup": backup,
        "local_edit_overwritten": local_edit,
    }
```

- [ ] **Step 4: Add `backup_dir` and the dispatch to the tool**

Change the signature of `template_apply_file` to:

```python
async def template_apply_file(
    project_path: str,
    file_path: str,
    source: str = "template",
    content: str = "",
    backup_dir: str = "",
) -> str:
```

Append to its docstring Args:

```
        backup_dir: Manifest v3 only. Directory that receives `<file>.pre-sync`
            and `<file>.diff` before a LOCAL_EDITED template-class file is
            overwritten. Required in that state -- the call is refused without
            it. Ignored for v2 manifests.
```

Directly after `placeholders = manifest.get("placeholders", {})` (line ~1110) insert:

```python
    from . import template_sync_v3 as v3
    if v3.is_v3(manifest):
        rules = v3.load_ownership(manifest["templateRepo"])
        if rules is None:
            return json.dumps({"error": f"manifest v3 needs {v3.OWNERSHIP_FILE} in the template repo"}, ensure_ascii=False)
        return json.dumps(
            v3.apply_file_v3(pp, manifest, rules, file_path, source, content, backup_dir),
            ensure_ascii=False,
        )
```

- [ ] **Step 5: Run the tests and the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_template_sync_v3_apply.py -q && .venv/Scripts/python -m pytest -q`
Expected: `9 passed` then `144 passed`

- [ ] **Step 6: Commit**

```bash
git add src/mcp_dev_servers/template_sync_v3.py src/mcp_dev_servers/template_sync_mcp.py tests/test_template_sync_v3_apply.py
git commit -F <scratch file>   # "feat(template-sync): v3 apply — backup_dir pre-image+diff, once create-if-missing, skip and gate_self_reference refused"
```

---

### Task 8: `template_version` derivation and `template_finalize_sync` v3 writer

**Files:**
- Modify: `src/mcp_dev_servers/template_sync_v3.py` (append `derive_template_version`, `finalize_v3`)
- Modify: `src/mcp_dev_servers/template_sync_mcp.py:1198-1348` (`template_finalize_sync` — dispatch after the JSON parsing of the three arguments)
- Test: `tests/test_template_sync_v3_finalize.py`

**Interfaces:**
- Consumes: `OwnershipRules`, `HASH_RE`, `format_hash`, `unknown_top_level_keys`, `core._run_git`, `core._template_repo_resolved`, `core._write_file_atomic`, `core._template_file_path`, `core._normalize_path`.
- Produces:
  - `derive_template_version(repo: str, commit: str, tracked_paths: list[str]) -> tuple[str | None, str | None]` → `(tag, warning)`; `warning` is `"untagged_template_tree"` or `"template_repo_not_git"`
  - `finalize_v3(pp, manifest, rules, applied: list, new: list, deleted: list) -> dict` — result keys `manifest_path`, `template_commit`, `template_version`, `files_updated`, `files_added`, `files_dropped`, `dropped_entries`, `unknown_keys`, `warnings`, `manifest_written`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_template_sync_v3_finalize.py
"""template_finalize_sync under manifest v3 and template_version derivation (review §2.10, §6.3)."""

import asyncio
import json
import subprocess

import pytest

from mcp_dev_servers import template_sync_mcp as ts
from mcp_dev_servers import template_sync_v3 as v3

# (paste the shared fixture block: OWNERSHIP, _mk_v3, _tpl_entry, _run)


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_template_sync_v3_finalize.py -q`
Expected: FAIL — `AttributeError: ... 'derive_template_version'`

- [ ] **Step 3: Append the implementation to the v3 module**

```python
# -------------------------
# v3 finalize
# -------------------------

def _tree_id(repo: str, ref: str, path: str) -> str | None:
    r = core._run_git(["rev-parse", f"{ref}:{path}"], cwd=repo)
    return r["stdout"].strip() if r["exit_code"] == 0 else None


def derive_template_version(repo: str, commit: str, tracked_paths: list[str]) -> tuple[str | None, str | None]:
    """Nearest reachable tag whose tree over the tracked paths equals the
    tree at `commit` (review §6.3). Never `git describe`."""
    if core._run_git(["rev-parse", "--is-inside-work-tree"], cwd=repo)["exit_code"] != 0:
        return None, "template_repo_not_git"
    r = core._run_git(["tag", "--merged", commit, "--sort=-v:refname"], cwd=repo)
    if r["exit_code"] != 0:
        return None, "untagged_template_tree"
    want = {p: _tree_id(repo, commit, p) for p in tracked_paths}
    for tag in [t.strip() for t in r["stdout"].splitlines() if t.strip()]:
        if all(_tree_id(repo, f"{tag}^{{commit}}", p) == want[p] for p in tracked_paths):
            return tag, None
    return None, "untagged_template_tree"


def finalize_v3(pp: pathlib.Path, manifest: dict, rules: OwnershipRules,
                applied: list, new: list, deleted: list) -> dict:
    invalid: list[str] = []
    for item in applied:
        fp = item.get("file_path", "")
        entry = item.get("manifest_entry", {})
        norm = core._normalize_path(fp)
        if not fp or not norm.strip("/") or ".." in norm.split("/"):
            invalid.append(f"invalid file_path: {fp!r}")
            continue
        if entry.get("ownership") not in ("template", "once"):
            invalid.append(f"{fp}: ownership must be 'template' or 'once'")
        if entry.get("ownership") == "template" and not HASH_RE.match(entry.get("hash", "") or ""):
            invalid.append(f"{fp}: hash is not sha256:<64 lowercase hex>")
    if invalid:
        return {"error": "applied_files validation failed — manifest NOT written", "invalid_entries": invalid}

    files = {core._normalize_path(k): v for k, v in manifest.get("files", {}).items()}
    placeholders = manifest.get("placeholders", {})
    warnings = list(rules.warnings)

    updated = 0
    for item in applied:
        fp = core._normalize_path(item["file_path"])
        entry = item["manifest_entry"]
        if entry["ownership"] == "template":
            files[fp] = {"hash": format_hash(parse_hash(entry["hash"])), "ownership": "template"}
        else:
            files[fp] = {"ownership": "once"}
        updated += 1

    added = 0
    for fp in new:
        fp = core._normalize_path(fp)
        if fp in files:
            continue
        tpl_rel = rules.template_path_for(fp)
        cls = rules.class_of(tpl_rel)
        if cls == "once":
            files[fp] = {"ownership": "once"}
        elif cls == "template":
            tpl_raw = core._read_file(core._template_file_path(manifest, tpl_rel))
            if tpl_raw is None:
                warnings.append(f"new file {fp}: template file {tpl_rel} not found -- skipped")
                continue
            files[fp] = {"hash": format_hash(core._sha256(core._apply_placeholders(tpl_raw, placeholders))),
                         "ownership": "template"}
        else:
            warnings.append(f"new file {fp}: no template/once rule -- skipped")
            continue
        added += 1

    explicit = {core._normalize_path(p) for p in deleted if isinstance(p, str) and p}
    dropped = []
    for fp in list(files):
        if fp in explicit:
            del files[fp]
            dropped.append(fp)
            continue
        if core._template_file_path(manifest, rules.template_path_for(fp)).is_file():
            continue
        if (pp / fp).exists():
            continue
        del files[fp]
        dropped.append(fp)

    repo = core._template_repo_resolved(manifest)
    head = core._run_git(["rev-parse", "HEAD"], cwd=repo)
    commit = head["stdout"].strip() if head["exit_code"] == 0 else manifest_commit(manifest)
    version, warn = derive_template_version(repo, commit, rules.tracked_paths)
    if warn:
        warnings.append(warn)

    out = {k: v for k, v in manifest.items() if k not in ("version", "lastSynced")}
    out["manifest_version"] = MANIFEST_VERSION_V3
    out["template_version"] = version
    out["template_commit"] = commit
    out["requires_server"] = manifest.get("requires_server") or f">={MIN_SERVER_FOR_V3}"
    out["files"] = dict(sorted(files.items()))
    unknown = unknown_top_level_keys(out)

    manifest_path = pp / ".claude" / "template-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    core._write_file_atomic(manifest_path, json.dumps(out, indent=2, ensure_ascii=False))
    return {
        "manifest_path": ".claude/template-manifest.json",
        "manifest_version": 3,
        "template_commit": commit,
        "template_version": version,
        "files_updated": updated,
        "files_added": added,
        "files_dropped": len(dropped),
        "dropped_entries": sorted(dropped),
        "unknown_keys": unknown,
        "warnings": warnings,
        "manifest_written": True,
    }
```

- [ ] **Step 4: Dispatch from `template_finalize_sync`**

Directly after the `deleted = json.loads(deleted_files)` try/except block (line ~1245) insert:

```python
    from . import template_sync_v3 as v3
    if v3.is_v3(manifest):
        rules = v3.load_ownership(manifest["templateRepo"])
        if rules is None:
            return json.dumps({"error": f"manifest v3 needs {v3.OWNERSHIP_FILE} in the template repo"}, ensure_ascii=False)
        return json.dumps(v3.finalize_v3(pp, manifest, rules, applied, new, deleted), ensure_ascii=False)
```

Append to the docstring:

```
    Manifest v3: entries carry `hash` (sha256:-prefixed) and `ownership`;
    `template_commit` is HEAD of the template repo and `template_version` the
    nearest reachable tag whose tracked tree is identical (null when none).
    Unknown top-level keys are preserved and listed in `unknown_keys`.
```

- [ ] **Step 5: Run the tests and the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_template_sync_v3_finalize.py -q && .venv/Scripts/python -m pytest -q`
Expected: `5 passed` then `149 passed`

- [ ] **Step 6: Commit**

```bash
git add src/mcp_dev_servers/template_sync_v3.py src/mcp_dev_servers/template_sync_mcp.py tests/test_template_sync_v3_finalize.py
git commit -F <scratch file>   # "feat(template-sync): v3 finalize writer — prefixed hashes, template_version by tree-identical tag, unknown_keys"
```

---

### Task 9: `template_migrate_manifest`

**Files:**
- Modify: `src/mcp_dev_servers/template_sync_v3.py` (append `build_project_md`, `migrate_v2_to_v3`, `migrate_manifest`)
- Modify: `src/mcp_dev_servers/template_sync_mcp.py` (register the tool after `template_finalize_sync`)
- Test: `tests/test_template_sync_v3_migration.py`

**Interfaces:**
- Consumes: `resolve_base`, `notes_hunks`'s `_unified`, `derive_template_version`, `OwnershipRules`, `core._split_custom_region`, `core._write_file_atomic`, `core._read_file`.
- Produces:
  - `MIGRATION_MARKER = "<!-- template-sync: project-owned; migrated from CLAUDE.md at"`
  - `build_project_md(region: str | None, hunks: str, base_label: str, template_version: str) -> str`
  - `migrate_v2_to_v3(pp, manifest, rules) -> dict` — pure planning: `manifest` (new dict), `dropped_entries`, `redundant_project_file`, `project_md` (content or None when it exists), `project_md_existing`, `hunk_count`, `migration_base`, `region_was_seed` (True / False / None when no base), `gate_self_reference`, `gate_unverified`, `unknown_keys`, `warnings`
  - `migrate_manifest(pp, backup_dir: str, dry_run: bool) -> dict` — tool body; result adds `migrated`, `dry_run`, `project_md_bytes`, `backup` (`{"claude_md": path, "manifest": path}` or None), `written` (list of relative paths). Write mode refuses with `error: gate_self_reference…` before touching anything when `gate_self_reference` is non-empty.
  - Tool `template_migrate_manifest(project_path: str, backup_dir: str = "", dry_run: bool = False) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_template_sync_v3_migration.py
"""v2 -> v3 migration (spec §7 steps 1-5, review §2.4, §2.5, §5c, §6.4, §6.5, §7d)."""

import asyncio
import json
import subprocess

import pytest

from mcp_dev_servers import template_sync_mcp as ts
from mcp_dev_servers import template_sync_v3 as v3

# (paste the shared fixture block: OWNERSHIP, _mk_v3, _tpl_entry, _run)

SEED = "<!-- Project-specific rules go here -->"
# The held template CONTAINS a placeholder (review §10a): the vacuity control
# only proves the base is rendered when there is something to render.
TPL_V2 = (
    "# Project\n\nRules line for {{NAME}}.\n\n"
    f"<!-- PROJECT-CUSTOM:BEGIN -->\n{SEED}\n<!-- PROJECT-CUSTOM:END -->\n"
)
TPL_V2_RENDERED = TPL_V2.replace("{{NAME}}", "Demo")
TPL_V3 = "# Project\n\nProject-specific instructions: .claude/rules/project.md\n\nRules line v3.\n"


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   cwd=str(repo), check=True, capture_output=True)


def _head(repo):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo), check=True,
                          capture_output=True, text=True).stdout.strip()


def _mk_v2(tmp_path, project_claude: str, extra_project: dict | None = None,
           extra_entries: dict | None = None, with_git: bool = True, ownership=True):
    """Toolkit repo whose history holds the v2 CLAUDE.md (tagged v2.3.0) and
    now ships the v3 one, plus a project holding a v2 manifest."""
    repo = tmp_path / "toolkit"
    vdir = repo / "templates" / "general"
    vdir.mkdir(parents=True)
    (vdir / "CLAUDE.md").write_text(TPL_V2, encoding="utf-8", newline="")
    (vdir / "CLAUDE.local.md").write_text("local {{NAME}}\n", encoding="utf-8", newline="")
    (vdir / ".claude").mkdir()
    (vdir / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    commit = ""
    if with_git:
        _git(repo, "init", "-q")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "v2")
        commit = _head(repo)
        _git(repo, "tag", "v2.3.0")
    (vdir / "CLAUDE.md").write_text(TPL_V3, encoding="utf-8", newline="")
    (vdir / "CLAUDE.local.md").unlink()
    (vdir / ".claude" / "rules").mkdir()
    (vdir / ".claude" / "rules" / "project.md").write_text("# Project instructions\n", encoding="utf-8")
    if ownership:
        (repo / "templates" / "ownership.json").write_text(json.dumps(OWNERSHIP), encoding="utf-8")

    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    (proj / "CLAUDE.md").write_text(project_claude, encoding="utf-8", newline="")
    (proj / "CLAUDE.local.md").write_text("local Demo\n", encoding="utf-8", newline="")
    (proj / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    for rel, content in (extra_project or {}).items():
        p = proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="")
    files = {
        "CLAUDE.md": {"templateHash": ts._sha256(TPL_V2_RENDERED), "templateRawHash": ts._sha256(TPL_V2),
                      "localHash": ts._sha256(project_claude), "locallyModified": True},
        "CLAUDE.local.md": {"templateHash": ts._sha256("local Demo\n"), "localHash": ts._sha256("local Demo\n"),
                            "locallyModified": False},
        ".claude/settings.json": {"templateHash": ts._sha256("{}"), "localHash": ts._sha256("{}"),
                                  "locallyModified": False},
    }
    files.update(extra_entries or {})
    (proj / ".claude" / "template-manifest.json").write_text(json.dumps({
        "version": 2, "templateRepo": str(repo), "variant": "general",
        "lastSynced": commit, "placeholders": {"NAME": "Demo"}, "files": files,
        "deletedAcknowledged": ["old.md"],
    }), encoding="utf-8", newline="")
    return repo, proj, commit


PROJ_CLAUDE = (
    "# Project\n\nRules line for Demo.\nMy extra rule.\n\n"
    "<!-- PROJECT-CUSTOM:BEGIN -->\nKeep this region.\n<!-- PROJECT-CUSTOM:END -->\n"
)


def _migrate(proj, **kw):
    return _run(ts.template_migrate_manifest(str(proj), **kw))


def test_migration_region_and_hunks(tmp_path):
    repo, proj, commit = _mk_v2(tmp_path, PROJ_CLAUDE)
    backup = tmp_path / "backup"
    res = _migrate(proj, backup_dir=str(backup))
    assert res["migrated"] is True and res["dry_run"] is False
    assert res["migration_base"] == commit
    assert res["hunk_count"] == 1
    assert res["project_md_bytes"] > 0
    md = (proj / ".claude" / "rules" / "project.md").read_text(encoding="utf-8")
    assert md.startswith("# Project instructions\n<!-- template-sync: project-owned; migrated from CLAUDE.md at ")
    assert f"migration-base: {commit}; rendered: yes -->" in md
    assert "{{NAME}}" not in md and "-Rules line for {{NAME}}" not in md   # base was rendered before diffing
    assert "Keep this region." in md
    assert res["region_was_seed"] is False
    assert res["gate_self_reference"] == [] and res["gate_unverified"] is False
    assert "```diff\n" in md and "+My extra rule." in md
    assert "Project-specific instructions" not in md      # never diffed against the current template
    # backup
    assert (backup / "CLAUDE.md.pre-migration").read_text(encoding="utf-8") == PROJ_CLAUDE
    assert (backup / "template-manifest.json.pre-migration").exists()
    assert res["backup"]["claude_md"].endswith("CLAUDE.md.pre-migration")
    # manifest
    m = json.loads((proj / ".claude" / "template-manifest.json").read_text(encoding="utf-8"))
    assert m["manifest_version"] == 3
    assert m["template_commit"] == commit
    assert m["template_version"] == "v2.3.0"
    assert m["requires_server"] == ">=0.3.0"
    assert m["deletedAcknowledged"] == ["old.md"]
    assert res["unknown_keys"] == ["deletedAcknowledged"]
    assert "version" not in m and "lastSynced" not in m
    assert m["files"]["CLAUDE.md"] == {"hash": "sha256:" + ts._sha256(TPL_V2_RENDERED), "ownership": "template"}
    assert m["files"][".claude/settings.json"]["ownership"] == "template"
    assert "CLAUDE.local.md" not in m["files"]
    assert res["dropped_entries"] == ["CLAUDE.local.md"]
    assert res["redundant_project_file"] == ["CLAUDE.local.md"]
    assert (proj / "CLAUDE.local.md").exists()                # never deleted
    assert (proj / "CLAUDE.md").read_text(encoding="utf-8") == PROJ_CLAUDE   # apply step does that, not migrate


def test_migration_vacuity_control(tmp_path):
    # Consumer holds the RENDERED template: region is the untouched seed, no edits.
    repo, proj, commit = _mk_v2(tmp_path, TPL_V2_RENDERED)
    res = _migrate(proj, backup_dir=str(tmp_path / "b"))
    assert res["hunk_count"] == 0
    assert res["region_was_seed"] is True
    md = (proj / ".claude" / "rules" / "project.md").read_text(encoding="utf-8")
    assert "```diff" not in md
    assert SEED not in md                       # the toolkit's seed is not the consumer's content
    assert md.count("\n") <= 3                  # header only


def test_migration_region_only_no_migrated_heading(tmp_path):
    # penumbra's shape (spec §8): consumer region, zero out-of-region edits.
    region_only = TPL_V2_RENDERED.replace(SEED, "Line one.\nLine two.")
    repo, proj, commit = _mk_v2(tmp_path, region_only)
    res = _migrate(proj, backup_dir=str(tmp_path / "b"))
    assert res["hunk_count"] == 0 and res["region_was_seed"] is False
    md = (proj / ".claude" / "rules" / "project.md").read_text(encoding="utf-8")
    assert "Line one.\nLine two." in md
    assert "Migrated from CLAUDE.md" not in md and "```diff" not in md


def test_migration_idempotent_and_keeps_existing_project_md(tmp_path):
    repo, proj, commit = _mk_v2(tmp_path, PROJ_CLAUDE, extra_project={".claude/rules/project.md": "mine\n"})
    res = _migrate(proj, backup_dir=str(tmp_path / "b"))
    assert res["project_md_existing"] is True
    assert (proj / ".claude" / "rules" / "project.md").read_text(encoding="utf-8") == "mine\n"
    res2 = _migrate(proj, backup_dir=str(tmp_path / "b"))
    assert res2["migrated"] is False
    assert "already" in res2["reason"]


def test_migration_dry_run_writes_nothing(tmp_path):
    repo, proj, commit = _mk_v2(tmp_path, PROJ_CLAUDE)
    res = _migrate(proj, dry_run=True)
    assert res["dry_run"] is True and res["migrated"] is False
    assert res["hunk_count"] == 1 and "+My extra rule." in res["project_md"]
    assert res["manifest"]["manifest_version"] == 3
    assert not (proj / ".claude" / "rules" / "project.md").exists()
    assert json.loads((proj / ".claude" / "template-manifest.json").read_text(encoding="utf-8"))["version"] == 2


def test_migration_refuses_write_without_backup_dir(tmp_path):
    repo, proj, commit = _mk_v2(tmp_path, PROJ_CLAUDE)
    res = _migrate(proj)
    assert "backup_dir" in res["error"]
    assert not (proj / ".claude" / "rules" / "project.md").exists()


def test_migration_base_unavailable_emits_no_hunks(tmp_path):
    repo, proj, commit = _mk_v2(tmp_path, PROJ_CLAUDE, with_git=False)
    res = _migrate(proj, backup_dir=str(tmp_path / "b"))
    assert res["migration_base"] == "unavailable"
    assert "migration_base_unavailable" in res["warnings"]
    assert res["hunk_count"] == 0
    md = (proj / ".claude" / "rules" / "project.md").read_text(encoding="utf-8")
    assert "migration-base: unavailable; rendered: no -->" in md
    assert "Keep this region." in md
    assert res["region_was_seed"] is None
    assert res["redundant_project_file"] == []


def test_migration_falls_back_to_tag_commit(tmp_path):
    repo, proj, commit = _mk_v2(tmp_path, PROJ_CLAUDE)
    m = json.loads((proj / ".claude" / "template-manifest.json").read_text(encoding="utf-8"))
    m["lastSynced"] = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    m["template_version"] = "v2.3.0"
    (proj / ".claude" / "template-manifest.json").write_text(json.dumps(m), encoding="utf-8")
    res = _migrate(proj, backup_dir=str(tmp_path / "b"))
    assert res["migration_base"] == "v2.3.0"
    assert res["hunk_count"] == 1


def test_migration_requires_ownership_file_and_skips_v3(tmp_path):
    repo, proj, commit = _mk_v2(tmp_path, PROJ_CLAUDE, ownership=False)
    res = _migrate(proj, dry_run=True)
    assert "ownership.json" in res["error"]

    repo2, proj2 = _mk_v3(tmp_path / "v3", template={"CLAUDE.md": "x"}, project={}, entries={})
    res2 = _migrate(proj2, dry_run=True)
    assert res2["migrated"] is False and "already" in res2["reason"]


def test_migration_refuses_gate_self_reference(tmp_path):
    repo, proj, commit = _mk_v2(tmp_path, PROJ_CLAUDE, extra_project={
        "PROJECT_CONTEXT.md": "**Gate**: bash hooks/run-gate.sh\n**Test**: t\n",
        "hooks/run-gate.sh": "x\n",
    })
    res = _migrate(proj, backup_dir=str(tmp_path / "b"))
    assert res["error"].startswith("gate_self_reference")
    assert not (proj / ".claude" / "rules" / "project.md").exists()
    assert not (tmp_path / "b").exists()
    assert json.loads((proj / ".claude" / "template-manifest.json").read_text(encoding="utf-8"))["version"] == 2
    dry = _migrate(proj, dry_run=True)
    assert dry["gate_self_reference"] == [{"key": "Gate", "path": "hooks/run-gate.sh"}]
    assert dry["gate_unverified"] is True


def test_migration_open_brain_manifest_shape(tmp_path):
    # v2, two hand-written unknown keys, 26 clean entries, 3 keep-mine resolutions (review §9d).
    entries = {
        f".claude/agents/a{i:02d}.md": {"templateHash": ts._sha256(f"a{i}\n"), "localHash": ts._sha256(f"a{i}\n"),
                                       "locallyModified": False}
        for i in range(26)
    }
    for i in range(3):
        entries[f"hooks/h{i}.sh"] = {"templateHash": ts._sha256(f"h{i}\n"), "localHash": ts._sha256("mine\n"),
                                     "locallyModified": True, "resolution": "keep-mine"}
    repo, proj, commit = _mk_v2(tmp_path, PROJ_CLAUDE, extra_entries=entries)
    m = json.loads((proj / ".claude" / "template-manifest.json").read_text(encoding="utf-8"))
    m["lastSyncedVersion"] = "v2.3.0"
    m["lastSyncedVersionOf"] = "claude-code-toolkit"
    (proj / ".claude" / "template-manifest.json").write_text(json.dumps(m), encoding="utf-8")

    res = _migrate(proj, backup_dir=str(tmp_path / "b"))
    assert res["migrated"] is True
    assert sorted(res["unknown_keys"]) == ["deletedAcknowledged", "lastSyncedVersion", "lastSyncedVersionOf"]
    out = json.loads((proj / ".claude" / "template-manifest.json").read_text(encoding="utf-8"))
    assert out["template_version"] == "v2.3.0"                 # server-written
    assert out["lastSyncedVersion"] == "v2.3.0" and out["lastSyncedVersionOf"] == "claude-code-toolkit"
    assert out["files"]["hooks/h0.sh"] == {"hash": "sha256:" + ts._sha256("h0\n"), "ownership": "template"}
    assert "resolution" not in json.dumps(out["files"])
    assert len(out["files"]) == 26 + 3 + 2                       # agents, hooks, CLAUDE.md, settings.json
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_template_sync_v3_migration.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'template_migrate_manifest'`

- [ ] **Step 3: Append the migration to the v3 module**

```python
# -------------------------
# v2 -> v3 migration
# -------------------------

MIGRATION_MARKER = "<!-- template-sync: project-owned; migrated from CLAUDE.md at"


def build_project_md(region: str | None, hunks: str, base_label: str, template_version: str) -> str:
    rendered = "no" if base_label == "unavailable" else "yes"
    out = [
        "# Project instructions",
        f"{MIGRATION_MARKER} {template_version}; migration-base: {base_label}; rendered: {rendered} -->",
        "",
    ]
    region_text = (region or "").strip("\n")
    if region_text:
        out += [region_text, ""]
    if hunks.strip():
        out += [
            "## Migrated from CLAUDE.md — review, then keep or delete",
            "```diff",
            hunks.rstrip("\n"),
            "```",
            "",
        ]
    return "\n".join(out)


def _region_body(region_block: str | None) -> str | None:
    """Inner text of a PROJECT-CUSTOM block (markers stripped)."""
    if region_block is None:
        return None
    lines = region_block.splitlines()
    inner = [l for l in lines if core.CUSTOM_REGION_BEGIN not in l and core.CUSTOM_REGION_END not in l]
    return "\n".join(inner)


def migrate_v2_to_v3(pp: pathlib.Path, manifest: dict, rules: OwnershipRules) -> dict:
    warnings = list(rules.warnings)
    placeholders = manifest.get("placeholders", {})
    repo = core._template_repo_resolved(manifest)

    # Steps 1-2: region + out-of-region hunks against the held base.
    proj_claude = core._read_file(pp / "CLAUDE.md") or ""
    proj_part, proj_region = core._split_custom_region(proj_claude)
    base, base_label, warn = resolve_base(manifest, "CLAUDE.md")
    if warn:
        warnings.append(warn)
    hunks = ""
    hunk_count = 0
    region_body = _region_body(proj_region)
    region_was_seed = None
    if base is not None:
        base_part, base_region = core._split_custom_region(base)
        hunks = _unified(base_part, proj_part, f"CLAUDE.md@{base_label}", "CLAUDE.md@project")
        hunk_count = sum(1 for l in hunks.splitlines() if l.startswith("@@"))
        # The toolkit's own seed is not the consumer's content (review §9b).
        if region_body is not None and base_region is not None:
            region_was_seed = _norm_ws(region_body) == _norm_ws(_region_body(base_region) or "")
            if region_was_seed:
                region_body = None
    gate_hits, gate_declared = collect_gate_refs(pp, rules)

    # Step 4: the v3 manifest.
    files: dict[str, dict] = {}
    dropped: list[str] = []
    redundant: list[str] = []
    for proj_rel, entry in manifest.get("files", {}).items():
        proj_rel = core._normalize_path(proj_rel)
        tpl_rel = rules.template_path_for(proj_rel)
        cls = rules.class_of(tpl_rel)
        if cls == "template":
            hex_digest = parse_hash(entry.get("templateHash", ""))
            if hex_digest:
                files[proj_rel] = {"hash": format_hash(hex_digest), "ownership": "template"}
            else:
                tpl_raw = core._read_file(core._template_file_path(manifest, tpl_rel))
                files[proj_rel] = {"hash": format_hash(core._sha256(core._apply_placeholders(tpl_raw or "", placeholders))),
                                   "ownership": "template"}
                warnings.append(f"{proj_rel}: no templateHash in v2 entry -- baseline set to the current template")
        elif cls == "once":
            files[proj_rel] = {"ownership": "once"}
        else:
            dropped.append(proj_rel)
            on_disk = core._read_file(pp / proj_rel)
            if on_disk is not None:
                held, _label, _w = resolve_base(manifest, tpl_rel)
                if held is not None and held == on_disk:
                    redundant.append(proj_rel)

    commit = manifest_commit(manifest)
    version, vwarn = derive_template_version(repo, commit, rules.tracked_paths) if commit else (None, "untagged_template_tree")
    if vwarn:
        warnings.append(vwarn)
    new_manifest = {k: v for k, v in manifest.items() if k not in ("version", "lastSynced", "files")}
    new_manifest["manifest_version"] = MANIFEST_VERSION_V3
    new_manifest["template_version"] = version
    new_manifest["template_commit"] = commit
    new_manifest["requires_server"] = f">={MIN_SERVER_FOR_V3}"
    new_manifest["files"] = dict(sorted(files.items()))

    # Step 3: project.md, unless the consumer already has one.
    existing = core._read_file(pp / PROJECT_MD)
    project_md = None
    if existing is None:
        project_md = build_project_md(region_body, hunks, base_label, "v3.1.0")

    return {
        "manifest": new_manifest,
        "dropped_entries": sorted(dropped),
        "redundant_project_file": sorted(redundant),
        "project_md": project_md,
        "project_md_existing": existing is not None,
        "hunk_count": hunk_count,
        "migration_base": base_label,
        "region_was_seed": region_was_seed,
        "gate_self_reference": gate_hits,
        "gate_unverified": gate_declared,
        "unknown_keys": unknown_top_level_keys(new_manifest),
        "warnings": warnings,
    }


def migrate_manifest(pp: pathlib.Path, backup_dir: str, dry_run: bool) -> dict:
    manifest, errors = core._load_manifest(pp)
    if manifest is None:
        return {"error": errors[0]}
    if errors:
        return {"error": "; ".join(errors)}
    if is_v3(manifest):
        return {"migrated": False, "dry_run": dry_run, "reason": "manifest is already v3 -- nothing to migrate"}
    rules = load_ownership(manifest["templateRepo"])
    if rules is None:
        return {"error": f"cannot migrate: {OWNERSHIP_FILE} not found in the template repo -- "
                         "the toolkit checkout predates v3.1"}
    plan = migrate_v2_to_v3(pp, manifest, rules)
    plan["dry_run"] = dry_run
    plan["project_md_bytes"] = len((plan["project_md"] or "").encode("utf-8"))
    if dry_run:
        plan["migrated"] = False
        plan["backup"] = None
        plan["written"] = []
        return plan
    if plan["gate_self_reference"]:
        hit = plan["gate_self_reference"][0]
        return {"error": f"gate_self_reference: **{hit['key']}**: points at template-class {hit['path']}; "
                         "move the logic to a non-template path (e.g. scripts/gate.sh) and point the key "
                         "there, then migrate", "gate_self_reference": plan["gate_self_reference"]}
    if not backup_dir:
        return {"error": "backup_dir is required to migrate (pre-migration CLAUDE.md and manifest are copied there); "
                         "use dry_run=true to preview"}

    bdir = pathlib.Path(backup_dir).resolve()
    bdir.mkdir(parents=True, exist_ok=True)
    claude_bak = bdir / "CLAUDE.md.pre-migration"
    manifest_bak = bdir / "template-manifest.json.pre-migration"
    core._write_file_atomic(claude_bak, core._read_file(pp / "CLAUDE.md") or "")
    core._write_file_atomic(manifest_bak, core._read_file(pp / ".claude" / "template-manifest.json") or "")

    written = []
    if plan["project_md"] is not None:
        target = pp / PROJECT_MD
        target.parent.mkdir(parents=True, exist_ok=True)
        core._write_file_atomic(target, plan["project_md"])
        written.append(PROJECT_MD)
    core._write_file_atomic(pp / ".claude" / "template-manifest.json",
                            json.dumps(plan["manifest"], indent=2, ensure_ascii=False))
    written.append(".claude/template-manifest.json")

    plan["migrated"] = True
    plan["backup"] = {"claude_md": str(claude_bak), "manifest": str(manifest_bak)}
    plan["written"] = written
    return plan
```

- [ ] **Step 4: Register the tool in `template_sync_mcp.py`**

Insert directly before the `@mcp.tool()` that precedes `template_reverse_placeholders` (line ~1351):

```python
@mcp.tool()
async def template_migrate_manifest(
    project_path: str,
    backup_dir: str = "",
    dry_run: bool = False,
) -> str:
    """
    Migrate a v2 template manifest to v3 (three-class ownership). Call it at
    step 1 of a sync when template_load_manifest reports migration_required.

    Steps (toolkit spec §7): extract the PROJECT-CUSTOM region from CLAUDE.md;
    diff the remainder against the template CLAUDE.md at the held revision
    (template_commit, else the template_version tag, never the current
    template); write .claude/rules/project.md with the region verbatim and the
    out-of-region hunks fenced as ```diff; rewrite the manifest as v3 (entries
    classified by templates/ownership.json, project-class entries dropped,
    unknown top-level keys preserved). Idempotent: an existing project.md is
    never overwritten and a v3 manifest is skipped. CLAUDE.md itself is not
    touched here -- the apply step overwrites it in the same sync.

    Args:
        project_path: Path to the project root directory
        backup_dir: Receives CLAUDE.md.pre-migration and
            template-manifest.json.pre-migration. Required unless dry_run.
        dry_run: Compute and return everything (project_md content, manifest,
            hunk_count, redundant_project_file, warnings) without writing.

    Returns:
        JSON with migrated, dry_run, migration_base, hunk_count, project_md,
        project_md_bytes, project_md_existing, region_was_seed (the region
        was the toolkit's untouched seed and is omitted), dropped_entries,
        redundant_project_file (byte-identical copies of files that are now
        project-owned; suggestion only, never deleted), gate_self_reference
        (a **Gate**:/**Test**: value pointing at a template-class path --
        write mode refuses), gate_unverified (a **Gate**: is declared and this
        tool did not run it), unknown_keys, warnings, backup, written.
    """
    from . import template_sync_v3 as v3
    pp = pathlib.Path(project_path).resolve()
    return json.dumps(v3.migrate_manifest(pp, backup_dir, dry_run), ensure_ascii=False)


```

- [ ] **Step 5: Run the tests and the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_template_sync_v3_migration.py -q && .venv/Scripts/python -m pytest -q`
Expected: `11 passed` then `160 passed`

- [ ] **Step 6: Commit**

```bash
git add src/mcp_dev_servers/template_sync_v3.py src/mcp_dev_servers/template_sync_mcp.py tests/test_template_sync_v3_migration.py
git commit -F <scratch file>   # "feat(template-sync): template_migrate_manifest — v2 to v3 with region extraction, fenced hunks, dry_run, base fallback, seed detection, gate refusal"
```

---

### Task 10: `template_get_diff(diff_type="unified")` alias

**Files:**
- Modify: `src/mcp_dev_servers/template_sync_mcp.py:938-1067` (`template_get_diff`)
- Test: `tests/test_template_sync_merge.py` (append one test)

**Interfaces:**
- Produces: `diff_type="unified"` behaves exactly as `"full"`; the response reports `"diff_type": "unified"` and the field stays `unified_diff`.

- [ ] **Step 1: Append the failing test**

```python
# append to tests/test_template_sync_merge.py

def test_get_diff_accepts_unified_as_alias_of_full(tmp_path):
    repo = tmp_path / "toolkit"
    (repo / "templates" / "general").mkdir(parents=True)
    (repo / "templates" / "general" / "CLAUDE.md").write_text("a\nb\n", encoding="utf-8", newline="")
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    (proj / "CLAUDE.md").write_text("a\nc\n", encoding="utf-8", newline="")
    (proj / ".claude" / "template-manifest.json").write_text(json.dumps({
        "version": 2, "templateRepo": str(repo), "variant": "general", "placeholders": {},
        "lastSynced": "", "files": {"CLAUDE.md": {"templateHash": ts._sha256("a\nb\n")}},
    }), encoding="utf-8")
    full = json.loads(asyncio.run(ts.template_get_diff(str(proj), "CLAUDE.md", "full")))
    uni = json.loads(asyncio.run(ts.template_get_diff(str(proj), "CLAUDE.md", "unified")))
    assert uni["diff_type"] == "unified"
    assert uni["unified_diff"] == full["unified_diff"]
    assert "-b" in uni["unified_diff"] and "+c" in uni["unified_diff"]
```

(Check the top of `tests/test_template_sync_merge.py` already imports `asyncio`, `json` and `ts`; add whichever is missing.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_template_sync_merge.py -q -k unified_as_alias`
Expected: FAIL — `KeyError: 'unified_diff'` (the tool returned `{"error": "Unknown diff_type: unified"}`)

- [ ] **Step 3: Add the alias**

In `template_get_diff`, change `elif diff_type == "full":` (line ~1022) to:

```python
    elif diff_type in ("full", "unified"):
```

Update the docstring lines:

```
    - full: template-current vs project-current ("unified" is an alias)
    ...
        diff_type: One of: template_changes, local_changes, full, unified, three_way
```

- [ ] **Step 4: Run the test and the full suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `161 passed`

- [ ] **Step 5: Commit**

```bash
git add src/mcp_dev_servers/template_sync_mcp.py tests/test_template_sync_merge.py
git commit -F <scratch file>   # "feat(template-sync): accept diff_type=unified as an alias of full"
```

---

### Task 11: Version 0.3.0, CHANGELOG, README

**Files:**
- Modify: `pyproject.toml:7`, `src/mcp_dev_servers/__init__.py:1`
- Modify: `CHANGELOG.md` (`[Unreleased]` section)
- Modify: `README.md` (template-sync-tools tool list — find it with `grep -n "template_finalize_sync" README.md`)

- [ ] **Step 1: Bump both version sites**

`pyproject.toml` line 7: `version = "0.3.0"`. `src/mcp_dev_servers/__init__.py`: `__version__ = "0.3.0"`.

- [ ] **Step 2: Verify the gate uses the new version**

Run: `.venv/Scripts/python -c "from mcp_dev_servers import template_sync_v3 as v; print(v.requires_server_satisfied('>=0.3.0', __import__('mcp_dev_servers').__version__))"`
Expected: `(True, '')`

- [ ] **Step 3: CHANGELOG entry**

Replace the `## [Unreleased]` line and the empty line after it with:

```markdown
## [Unreleased]

## [0.3.0] — <date of release>

### Compatibility

- **`claude-code-toolkit` v3.1.0 expects `template-sync-tools` 0.3.0** and writes `requires_server: ">=0.3.0"` into every v3 manifest. `template_load_manifest` refuses a manifest whose floor this server does not meet, with a named reason; a server older than 0.3.0 returns no `server_version` field at all, and the v3.1 skill treats that absence as "too old" (spec §9).
- **Restart the MCP server after upgrading.** Every change lives in the server process.
- **Breaking for v3 manifests only:** `template_compute_status` statuses are `IDENTICAL` / `TEMPLATE_UPDATED` / `LOCAL_EDITED` / `TEMPLATE_DELETED` (template class) and `PRESENT` / `MISSING` (once class). `CONFLICT`, `AUTO_UPDATE`, `PROJECT_CUSTOM` and `UP_TO_DATE` never appear for v3. `template_apply_file(source="skip")` is refused on template-class files under v3 — there is no keep-mine class; a former keep-mine entry migrates to `template` and is overwritten on the first v3 sync with its diff saved to `backup_dir`.
- **v2 manifests are untouched.** Every v2 code path is byte-identical to 0.2.1; `template_load_manifest` merely adds `manifest_version`, `server_version` and `migration_required` (true only when the template repo ships `templates/ownership.json`).

### Added

- `template-sync-tools`: **manifest v3 — three-class ownership** (`template` / `once` / `project`) per the toolkit's ownership spec (`docs/plans/2026-09-03-v3.1-ownership-model-spec.md` @ 2b23958; server contract in `docs/plans/2026-09-05-v3.1-ownership-server-review.md`). Entries carry `hash` (`sha256:`-prefixed, the placeholder-replaced template hash at sync) and `ownership`; top-level `manifest_version`, `template_version` (nearest reachable tag whose tracked tree is identical to `template_commit`, never `git describe`; `null` + `untagged_template_tree` when none), `template_commit` (`lastSynced` read as an alias), `requires_server`. Classes come from `<templateRepo>/templates/ownership.json` (first matching rule wins, `**` globs, `target` renames such as `gitignore` → `.gitignore`, `tracked_paths`); a template path with no rule is listed under `unclassified_template_files` and never applied.
- `template-sync-tools`: `template_migrate_manifest(project_path, backup_dir, dry_run)` — explicit v2→v3 migration: PROJECT-CUSTOM region of `CLAUDE.md` and the out-of-region edits (diffed against the template at `template_commit`, falling back to the `template_version` tag, never the current template) go to `.claude/rules/project.md` with the hunks fenced as ```` ```diff ````; the header records `migration-base`. Idempotent; an existing `project.md` is never overwritten; `dry_run` returns everything without writing; `redundant_project_file` names byte-identical copies of files that became project-owned (suggestion only, never deleted); unknown top-level keys are preserved and reported as `unknown_keys`.
- `template-sync-tools`: `template_apply_file` gains `backup_dir`; a `LOCAL_EDITED` template-class file is refused without it and otherwise saved as `<file>.pre-sync` + `<file>.diff` before the overwrite. `once` files are created when missing and never touched again.
- `template-sync-tools`: `template_compute_status` for v3 reports `local_diff` per `LOCAL_EDITED` file, `orphans` (consumer files matching a template-class rule with no manifest entry; informational only, silenced by an explicit `project` rule), and a `key_audit` for `once` rules with `"audit": "keys"`: `missing_required`, `optional_absent`, `placeholder_keys`, `deprecated_keys`, resolved values with `template_default_changed` / `consumer_holds_old_default`, qualified-key and alias matching, and `template_notes_changed` hunks.
- `template-sync-tools`: `template_get_diff` accepts `diff_type="unified"` as an alias of `full`; `template_load_manifest` returns `server_version`.
- `template-sync-tools`: **normalisation stated** — before hashing and classifying, a leading BOM is stripped and CRLF/CR fold to LF (this was already the 0.2.x behaviour; a trailing newline is not normalised). A BOM- or EOL-only difference is reported per file as `encoding_drift` (`["bom"]`, `["crlf"]`, both), informational, never `LOCAL_EDITED`. After an overwrite the file is byte-identical to the template (LF, no BOM).
- `template-sync-tools`: **`gate_self_reference`** — a `**Gate**:`/`**Test**:` value in an audited `once` file whose path token is a template-class file (the toolkit's `run-gate.sh` refuses to invoke itself, so such a sync breaks merges). Reported by `template_compute_status`, refused before any write by `template_apply_file` on that path and by `template_migrate_manifest`. Direct references only — a wrapper elsewhere that calls the hook passes. `gate_unverified: true` on status and migrate responses whenever `**Gate**:` is declared: the server never executes it.
- `template-sync-tools`: migration reports `region_was_seed` and omits the PROJECT-CUSTOM body from `project.md` when it is byte-identical (normalised) to the toolkit's own seed at the base; the header records `rendered: yes|no` beside `migration-base`.
- `template-sync-tools`: key audit gains `placeholder_key_divergence` — an audited rule's `placeholder_map` pairs a `**Key**:` with the manifest placeholder that renders it (`"Build Command": "BUILD_COMMAND"`); a whitespace-normalised mismatch, or a missing placeholder, is reported per pair. Catches a wrong value stored at setup that CLAUDE.md would otherwise re-render forever. Never writes.
```

- [ ] **Step 4: README tool list**

Find the template-sync-tools tool list (`grep -n "template_finalize_sync" README.md`) and add, in the same list style, one line for `template_migrate_manifest` — "v2→v3 manifest migration (region + fenced hunks to `.claude/rules/project.md`, `dry_run`)" — and note beside `template_apply_file` that it takes `backup_dir` under manifest v3. If the README states a tool count for the package, raise it by one.

- [ ] **Step 5: Full suite, then commit**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `160 passed`

```bash
git add pyproject.toml src/mcp_dev_servers/__init__.py CHANGELOG.md README.md
git commit -F <scratch file>   # "release: v0.3.0 — manifest v3 three-class ownership for template-sync-tools"
```

Do **not** tag or publish here — the release PR, tag `v0.3.0` and the GitHub release follow the merge, the same way v0.2.1 did (PR → squash merge → annotated tag → `github_release_create`), and only after the toolkit controller confirms the combined spec amendment sha so the CHANGELOG can cite it.

---

## Execution notes (2026-09-05)

- The version bump to 0.3.0 was pulled forward from Task 11 to before Task 3: the
  `requires_server` gate is only testable once the server reports that version.
- Task 5's `notes_hunks` filters key-line changes out of each hunk instead of dropping
  mixed hunks: with one line of context a note change and a key change share a hunk.
- Task 10's test lives in `tests/test_template_sync_diff_alias.py` (self-contained)
  rather than appended to the merge test file.
- `tests/test_smoke.py` pins the tool count per server; `template_sync_mcp` went 8 → 9.
- Batch 7 (read-back corrections, spec `2b23958`) landed before Task 4 was built and is
  in the code as planned; batch 8 (per-file unknown keys preserved + `unknown_file_keys`)
  landed as a follow-up commit after Task 10.
- Full suite at the end of Task 10 + batch 8: 163 passed.

## Self-review

**Spec coverage** (review §2–§7 → task):
- 2.1 fields, `lastSynced` alias → Task 2, 3. 2.2 hash form + precedence → Task 2, 6. 2.3 ownership table, `target`, unclassified → Task 1, 6. 2.4 base = `lastSynced`, no diff against current, header records base → Task 6 (`resolve_base`), 9. 2.5 explicit migrate tool, load as gate → Task 3, 9. 2.6 `server_version` load-bearing → Task 3. 2.7 `backup_dir`, refuse, `local_diff` → Task 7, 6. 2.8 `skip` refused → Task 7. 2.9 key audit opt-in → Task 4, 6. 2.10 unknown keys preserved + reported → Task 8, 9. 2.11 `unified` alias, `server_version` → Task 10, 3.
- 5a three lists + deprecated → Task 4. 5b orphans by template-class rules, `project` rule silences → Task 5, 6. 5c `redundant_project_file` → Task 9. 5d skill-side — no task.
- 6.1 base path via `_template_git_path` → Task 6. 6.2 placeholder-replaced base → Task 6. 6.3 `template_version` by tree-identical tag, `tracked_paths` with fallback → Task 1, 8. 6.4 fallback chain → Task 6. 6.5 `dry_run` → Task 9. 6.6 classification scope → Task 5, 6.
- 7a resolved values + flags → Task 4. 7b qualified/aliases → Task 4. 7c `template_notes_changed` → Task 5, 6. 7d `project_md_bytes` → Task 9. 7e — no task.
- 8a/8b normalisation stated, `encoding_drift` → Task 5 (`read_with_flags`), 6, 11 (CHANGELOG).
- 9a `gate_self_reference` direct-only, three places, `gate_unverified` → Task 4 (`gate_refs`, `collect_gate_refs`), 6 (status), 7 (apply refusal), 9 (migrate refusal + dry_run report). 9b `region_was_seed` → Task 9. 9c — no task. 9d manifest-shape test → Task 9.
- 10a rendered baseline, `rendered:` in header, placeholder-bearing vacuity fixture → Task 9. 10b `placeholder_key_divergence` → Task 4, 6. 10c root-level consumer file stays `project` → Task 5 (orphan test row).
- Spec §8 matrix: template × {identical, updated, edited} and once × {present, missing} → Task 6; migration with region + edits, vacuity control, run-twice, existing `project.md`, v3 skip → Task 9. Spec §9 rollout: v2 without ownership.json behaves as today → Task 3 (`migration_required` gate) and every dispatch is behind `is_v3`.

**Placeholder scan:** none of the forbidden phrases; every code step carries the code. The README step names the exact grep and the exact text to add.

**Type consistency:** `OwnershipRules.class_of / rule_for / project_path_for / template_path_for / template_class_rules / tracked_paths / warnings` used identically in Tasks 5–9; `template_status(entry_hash_hex, tpl_replaced, proj_content)` in Tasks 6 and 7; `resolve_base(manifest, rel_path) -> (content, label, warning)` in Tasks 6 and 9; `derive_template_version(repo, commit, tracked_paths) -> (tag, warning)` in Tasks 8 and 9; `_unified(a, b, fromfile, tofile)` defined in Task 6 and used in Tasks 7 and 9 (Task 6 must land before 7 and 9 — the task order already guarantees it); `format_hash` / `parse_hash` / `HASH_RE` from Task 2 everywhere; `read_with_flags` / `encoding_drift` (Task 5) used in Task 6; `gate_refs` / `collect_gate_refs` (Task 4) used in Tasks 6, 7, 9; `_norm_ws` (Task 4) used in Task 9. Test counts assume the 87-test baseline of v0.2.1.
