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


# -------------------------
# Orphans, template notes, encoding flags
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
        # Key-line changes are the audit's business; drop them from the hunk
        # and keep it only if a non-key change remains (a MIXED hunk keeps
        # its note part -- panoscribe's shape).
        kept = [h[0]] + [
            l for l in h[1:]
            if not (l[:1] in "+-" and KEY_LINE_RE.match(l[1:].rstrip("\n")))
        ]
        if not any(l[:1] in "+-" for l in kept[1:]):
            continue
        out.append("".join(kept))
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
