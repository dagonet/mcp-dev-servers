"""v3 statuses, key audit, and orphans (review §2.9, §5a, §5b, §7a-c, §8, §9a, §10, §11)."""

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


AUDIT_RULE = OWNERSHIP["rules"][-1]


# --- Task 4: key audit --------------------------------------------------------


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


# --- Task 5: orphans, template notes, encoding flags --------------------------


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
