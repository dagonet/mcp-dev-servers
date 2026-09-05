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
    or a declared alias (review §7b). Exact first, then in document order.
    Used for OPTIONAL keys; required keys use exact_holdings."""
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
