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
            warnings.append(
                f"ownership.json rules[{idx}] ({pattern}): ownership must be one of {CLASSES} -- skipped"
            )
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
