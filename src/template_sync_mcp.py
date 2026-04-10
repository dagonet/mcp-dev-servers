"""
Template Sync MCP Server Tools

Tools for synchronizing project files with claude-code-toolkit templates:
- Load and validate template manifests (v1/v2)
- Compute sync status across all tracked files
- Generate diffs (two-way and three-way merge)
- Apply file updates with placeholder replacement
- Reverse placeholders for upstream contribution
- Cross-variant propagation
"""

import hashlib
import json
import os
import pathlib
import subprocess
import shutil
from difflib import SequenceMatcher, unified_diff
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("template-sync-tools")

_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# Manifest schema version produced by this server
MANIFEST_VERSION = 2

# Files that are always project-specific (never auto-updated)
ALWAYS_PROJECT_SPECIFIC = {"PROJECT_CONTEXT.md"}

# Known variant directories
KNOWN_VARIANTS = ["general", "dotnet", "dotnet-maui", "rust-tauri", "java", "python"]


# -------------------------
# Helpers
# -------------------------

def _sha256(content: str) -> str:
    """SHA-256 hash of a string (UTF-8, BOM stripped)."""
    if content.startswith("\ufeff"):
        content = content[1:]
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _read_file(path: pathlib.Path) -> str | None:
    """Read a UTF-8 file, return None if missing."""
    try:
        content = path.read_text(encoding="utf-8")
        if content.startswith("\ufeff"):
            content = content[1:]
        return content
    except (FileNotFoundError, OSError):
        return None


def _write_file_atomic(path: pathlib.Path, content: str) -> None:
    """Write file atomically via temp + rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(str(tmp), str(path))


def _normalize_path(p: str) -> str:
    """Normalize path to forward slashes."""
    return p.replace("\\", "/")


def _resolve_path(p: str) -> pathlib.Path:
    """Resolve a path, converting MSYS /x/... to X:\\ on Windows."""
    import re
    if os.name == "nt" and re.match(r"^/[a-zA-Z]/", p):
        p = p[1].upper() + ":" + p[2:]
    return pathlib.Path(p).resolve()


def _apply_placeholders(content: str, placeholders: dict[str, str]) -> str:
    """Replace {{KEY}} tokens with concrete values."""
    for key, val in placeholders.items():
        content = content.replace("{{" + key + "}}", val)
    return content


def _reverse_placeholders(content: str, placeholders: dict[str, str]) -> tuple[str, list[dict]]:
    """
    Reverse placeholder replacement: concrete values -> {{KEY}}.

    Sorts by value length descending (longest first) to avoid partial matches.
    Returns (reversed_content, list of replacements made).
    """
    sorted_ph = sorted(placeholders.items(), key=lambda x: (-len(x[1]), x[0]))
    replacements = []
    for key, val in sorted_ph:
        if not val:
            continue
        count = content.count(val)
        if count > 0:
            content = content.replace(val, "{{" + key + "}}")
            replacements.append({"placeholder": key, "value": val, "count": count})
    return content, replacements


def _git_exe() -> str:
    """Find the git executable, preferring .exe on Windows."""
    if os.name == "nt":
        p = shutil.which("git.exe")
        if p:
            return p
    p = shutil.which("git")
    return p or ("git.exe" if os.name == "nt" else "git")


def _run_git(args: list[str], cwd: str, timeout_s: int = 10) -> dict:
    """Run a git command safely."""
    env = os.environ.copy()
    env.update({
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "LC_ALL": "C",
    })
    exe = _git_exe()
    try:
        p = subprocess.run(
            [exe] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,
            creationflags=_SUBPROCESS_FLAGS,
        )
        return {"exit_code": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {"exit_code": 1, "stdout": "", "stderr": str(e)}


def _git_head(repo: str) -> str | None:
    """Get short HEAD commit of a repo."""
    r = _run_git(["rev-parse", "--short", "HEAD"], cwd=repo)
    if r["exit_code"] == 0:
        return r["stdout"].strip()
    return None


def _git_show_file(repo: str, commit: str, file_path: str) -> str | None:
    """Retrieve file content at a specific commit via git show."""
    r = _run_git(["show", f"{commit}:{file_path}"], cwd=repo, timeout_s=10)
    if r["exit_code"] == 0:
        content = r["stdout"]
        if content.startswith("\ufeff"):
            content = content[1:]
        return content
    return None


def _load_manifest(project_path: pathlib.Path) -> tuple[dict | None, list[str]]:
    """Load and parse the manifest file. Returns (manifest, errors)."""
    manifest_path = project_path / ".claude" / "template-manifest.json"
    content = _read_file(manifest_path)
    if content is None:
        return None, ["No template manifest found at .claude/template-manifest.json"]
    try:
        manifest = json.loads(content)
    except json.JSONDecodeError as e:
        return None, [f"Invalid JSON in manifest: {e}"]

    errors = []
    for field in ("variant", "templateRepo", "placeholders", "files"):
        if field not in manifest:
            errors.append(f"Missing required field: {field}")
    return manifest, errors


def _template_repo_resolved(manifest: dict) -> str:
    """Get the resolved templateRepo path as a string (handles MSYS paths on Windows)."""
    return str(_resolve_path(manifest["templateRepo"]))


def _get_template_dir(manifest: dict) -> pathlib.Path:
    """Get the template variant directory from manifest."""
    repo = _resolve_path(manifest["templateRepo"])
    return repo / "templates" / manifest["variant"]


def _template_file_path(manifest: dict, rel_path: str) -> pathlib.Path:
    """Get full path to a template file."""
    return _get_template_dir(manifest) / rel_path


def _scan_template_files(template_dir: pathlib.Path) -> list[str]:
    """Scan template directory for all files, return relative paths."""
    files = []
    if not template_dir.is_dir():
        return files
    for p in template_dir.rglob("*"):
        if p.is_file():
            rel = _normalize_path(str(p.relative_to(template_dir)))
            # Skip gitignore (merge-only, not template-owned)
            if rel == "gitignore":
                continue
            files.append(rel)
    return sorted(files)


def _three_way_merge(base: str, theirs: str, ours: str) -> dict:
    """
    Line-based three-way merge.

    base: common ancestor (template at last sync, post-replacement)
    theirs: template current (post-replacement)
    ours: project current

    Returns dict with auto_merged content, has_conflicts, conflict_count.
    """
    base_lines = base.splitlines(keepends=True)
    theirs_lines = theirs.splitlines(keepends=True)
    ours_lines = ours.splitlines(keepends=True)

    # Get opcodes for base->theirs and base->ours
    sm_theirs = SequenceMatcher(None, base_lines, theirs_lines)
    sm_ours = SequenceMatcher(None, base_lines, ours_lines)

    theirs_ops = sm_theirs.get_opcodes()
    ours_ops = sm_ours.get_opcodes()

    # Build change maps: for each base line index, record if theirs/ours changed it
    theirs_changes = {}  # base_idx -> replacement lines
    for tag, i1, i2, j1, j2 in theirs_ops:
        if tag != "equal":
            for i in range(i1, max(i2, i1 + 1)):
                theirs_changes[i] = (tag, i1, i2, j1, j2)

    ours_changes = {}
    for tag, i1, i2, j1, j2 in ours_ops:
        if tag != "equal":
            for i in range(i1, max(i2, i1 + 1)):
                ours_changes[i] = (tag, i1, i2, j1, j2)

    # Simple approach: process by regions from theirs opcodes, detect overlaps
    merged = []
    conflicts = []
    conflict_count = 0
    processed_theirs = set()
    processed_ours = set()

    # Walk through base line by line and decide
    i = 0
    while i < len(base_lines):
        in_theirs = i in theirs_changes
        in_ours = i in ours_changes

        if not in_theirs and not in_ours:
            # No changes, keep base
            merged.append(base_lines[i])
            i += 1
        elif in_theirs and not in_ours:
            # Only template changed this region
            tag, i1, i2, j1, j2 = theirs_changes[i]
            if (i1, i2) not in processed_theirs:
                processed_theirs.add((i1, i2))
                merged.extend(theirs_lines[j1:j2])
            i = max(i + 1, i2) if i >= i1 else i + 1
        elif not in_theirs and in_ours:
            # Only project changed this region
            tag, i1, i2, j1, j2 = ours_changes[i]
            if (i1, i2) not in processed_ours:
                processed_ours.add((i1, i2))
                merged.extend(ours_lines[j1:j2])
            i = max(i + 1, i2) if i >= i1 else i + 1
        else:
            # Both changed -- conflict
            t_tag, t_i1, t_i2, t_j1, t_j2 = theirs_changes[i]
            o_tag, o_i1, o_i2, o_j1, o_j2 = ours_changes[i]

            # Check if changes are identical (both made same edit)
            theirs_new = theirs_lines[t_j1:t_j2]
            ours_new = ours_lines[o_j1:o_j2]

            region_key = (t_i1, t_i2, o_i1, o_i2)
            if region_key not in processed_theirs:
                processed_theirs.add(region_key)
                if theirs_new == ours_new:
                    # Same change on both sides, no conflict
                    merged.extend(ours_new)
                else:
                    conflict_count += 1
                    merged.append("<<<<<<< PROJECT\n")
                    merged.extend(ours_new)
                    merged.append("=======\n")
                    merged.extend(theirs_new)
                    merged.append(">>>>>>> TEMPLATE\n")

            end = max(t_i2, o_i2)
            i = max(i + 1, end) if i >= min(t_i1, o_i1) else i + 1

    # Handle insertions at end (beyond base length)
    # Check if theirs added content after base
    for tag, i1, i2, j1, j2 in sm_theirs.get_opcodes():
        if tag == "insert" and i1 == len(base_lines) and (i1, i2) not in processed_theirs:
            processed_theirs.add((i1, i2))
            merged.extend(theirs_lines[j1:j2])

    for tag, i1, i2, j1, j2 in sm_ours.get_opcodes():
        if tag == "insert" and i1 == len(base_lines) and (i1, i2) not in processed_ours:
            processed_ours.add((i1, i2))
            merged.extend(ours_lines[j1:j2])

    auto_merged = "".join(merged)
    return {
        "auto_merged": auto_merged,
        "has_conflicts": conflict_count > 0,
        "conflict_count": conflict_count,
    }


# -------------------------
# MCP Tools
# -------------------------

@mcp.tool()
async def template_load_manifest(project_path: str) -> str:
    """
    Load and validate the template manifest from a project.
    Auto-migrates v1 manifests to v2 format by computing missing hashes.

    Args:
        project_path: Path to the project root directory

    Returns:
        JSON with manifest data, validation status, errors, and warnings
    """
    pp = pathlib.Path(project_path).resolve()
    manifest, errors = _load_manifest(pp)
    if manifest is None:
        return json.dumps({"valid": False, "errors": errors}, ensure_ascii=False)

    if errors:
        return json.dumps({"valid": False, "errors": errors}, ensure_ascii=False)

    warnings = []
    version = manifest.get("version", 1)

    # Auto-migrate v1 -> v2
    if version < 2:
        warnings.append("Migrating v1 manifest to v2 -- will compute localHash and templateRawHash fields")
        placeholders = manifest.get("placeholders", {})
        template_dir = _get_template_dir(manifest)
        files = manifest.get("files", {})

        for rel_path, entry in files.items():
            # Compute templateRawHash from current template file
            tpl_content = _read_file(template_dir / rel_path)
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

    # Validate template repo exists
    template_dir = _get_template_dir(manifest)
    if not template_dir.is_dir():
        errors.append(
            f"Template directory not found: {template_dir}. "
            f"Update templateRepo in .claude/template-manifest.json."
        )

    return json.dumps({
        "valid": len(errors) == 0,
        "version": manifest.get("version", 1),
        "variant": manifest.get("variant", ""),
        "templateRepo": manifest.get("templateRepo", ""),
        "lastSynced": manifest.get("lastSynced", ""),
        "placeholders": manifest.get("placeholders", {}),
        "files": manifest.get("files", {}),
        "errors": errors,
        "warnings": warnings,
    }, ensure_ascii=False)


@mcp.tool()
async def template_compute_status(
    project_path: str,
    template_repo: str = "",
    variant: str = "",
) -> str:
    """
    Compute sync status for all tracked template files.
    Classifies each file as: UP_TO_DATE, PROJECT_CUSTOM, AUTO_UPDATE, CONFLICT,
    TEMPLATE_DELETED. Also detects new files added to the template.

    Args:
        project_path: Path to the project root directory
        template_repo: Override templateRepo from manifest (optional)
        variant: Override variant from manifest (optional)

    Returns:
        JSON with per-file status, new/deleted file lists, and summary counts
    """
    pp = pathlib.Path(project_path).resolve()
    manifest, errors = _load_manifest(pp)
    if manifest is None:
        return json.dumps({"error": errors[0]}, ensure_ascii=False)

    if template_repo:
        manifest["templateRepo"] = template_repo
    if variant:
        manifest["variant"] = variant

    placeholders = manifest.get("placeholders", {})
    template_dir = _get_template_dir(manifest)
    template_commit = _git_head(_template_repo_resolved(manifest)) or "unknown"
    files_status = {}
    summary = {
        "up_to_date": 0, "project_custom": 0, "auto_update": 0,
        "conflict": 0, "template_deleted": 0,
    }

    for rel_path, entry in manifest.get("files", {}).items():
        tpl_content = _read_file(template_dir / rel_path)

        if tpl_content is None:
            files_status[rel_path] = {"status": "TEMPLATE_DELETED"}
            summary["template_deleted"] += 1
            continue

        # Current template hash (after placeholder replacement)
        tpl_replaced = _apply_placeholders(tpl_content, placeholders)
        tpl_hash_new = _sha256(tpl_replaced)

        # Previous template hash from manifest
        tpl_hash_old = entry.get("templateHash", "")

        # Check if template changed
        template_changed = tpl_hash_new != tpl_hash_old

        # Check if project file changed (compare against localHash)
        proj_content = _read_file(pp / rel_path)
        proj_hash_current = _sha256(proj_content) if proj_content is not None else ""

        # For v1 manifests without localHash, use locallyModified flag
        local_hash_at_sync = entry.get("localHash", "")
        if local_hash_at_sync:
            project_changed = proj_hash_current != local_hash_at_sync
        else:
            project_changed = entry.get("locallyModified", False)

        # Classify
        if not template_changed and not project_changed:
            status = "UP_TO_DATE"
        elif not template_changed and project_changed:
            status = "PROJECT_CUSTOM"
        elif template_changed and not project_changed:
            status = "AUTO_UPDATE"
        else:
            status = "CONFLICT"

        summary[status.lower()] += 1
        files_status[rel_path] = {
            "status": status,
            "template_changed": template_changed,
            "locally_modified": project_changed,
            "template_hash_new": tpl_hash_new,
            "template_hash_old": tpl_hash_old,
            "local_hash_current": proj_hash_current,
            "local_hash_at_sync": local_hash_at_sync,
        }

    # Detect new template files not in manifest
    all_template_files = _scan_template_files(template_dir)
    tracked = set(manifest.get("files", {}).keys())
    new_files = [f for f in all_template_files if f not in tracked and f not in ALWAYS_PROJECT_SPECIFIC]

    # Detect deleted template files already counted above
    deleted_files = [p for p, s in files_status.items() if s["status"] == "TEMPLATE_DELETED"]

    return json.dumps({
        "template_commit": template_commit,
        "last_synced_commit": manifest.get("lastSynced", ""),
        "files": files_status,
        "new_template_files": new_files,
        "deleted_template_files": deleted_files,
        "summary": summary,
    }, ensure_ascii=False)


@mcp.tool()
async def template_get_diff(
    project_path: str,
    file_path: str,
    diff_type: str = "full",
) -> str:
    """
    Generate a diff for a template-tracked file.

    Supports four diff types:
    - template_changes: what changed in the template since last sync
    - local_changes: what the user changed since last sync
    - full: template-current vs project-current
    - three_way: three-way merge with conflict markers

    For three_way, reconstructs the common ancestor via git show at lastSynced commit.
    Falls back to two-way if git history is unavailable.

    Args:
        project_path: Path to the project root directory
        file_path: Relative path of the file (e.g. "CLAUDE.md")
        diff_type: One of: template_changes, local_changes, full, three_way

    Returns:
        JSON with content versions, unified diff, and merge result (for three_way)
    """
    pp = pathlib.Path(project_path).resolve()
    manifest, errors = _load_manifest(pp)
    if manifest is None:
        return json.dumps({"error": errors[0]}, ensure_ascii=False)

    placeholders = manifest.get("placeholders", {})
    template_dir = _get_template_dir(manifest)
    last_synced = manifest.get("lastSynced", "")

    # Read current template content (post-replacement)
    tpl_raw = _read_file(template_dir / file_path)
    if tpl_raw is None:
        return json.dumps({"error": f"Template file not found: {file_path}"}, ensure_ascii=False)
    tpl_current = _apply_placeholders(tpl_raw, placeholders)

    # Read current project content
    proj_current = _read_file(pp / file_path)
    if proj_current is None:
        return json.dumps({"error": f"Project file not found: {file_path}"}, ensure_ascii=False)

    # Reconstruct base (common ancestor) via git show
    base_content = None
    if last_synced:
        variant = manifest.get("variant", "")
        git_path = f"templates/{variant}/{file_path}"
        base_raw = _git_show_file(_template_repo_resolved(manifest), last_synced, git_path)
        if base_raw is not None:
            base_content = _apply_placeholders(base_raw, placeholders)

    # Fallback: if no base available, use current template as base (two-way)
    fallback_used = False
    if base_content is None:
        base_content = tpl_current
        fallback_used = True

    result = {
        "file_path": file_path,
        "diff_type": diff_type,
        "fallback_to_two_way": fallback_used,
    }

    if diff_type == "template_changes":
        diff = list(unified_diff(
            base_content.splitlines(keepends=True),
            tpl_current.splitlines(keepends=True),
            fromfile=f"{file_path} (base @ {last_synced})",
            tofile=f"{file_path} (template current)",
        ))
        result["unified_diff"] = "".join(diff)
        result["has_changes"] = len(diff) > 0

    elif diff_type == "local_changes":
        diff = list(unified_diff(
            base_content.splitlines(keepends=True),
            proj_current.splitlines(keepends=True),
            fromfile=f"{file_path} (base @ {last_synced})",
            tofile=f"{file_path} (project current)",
        ))
        result["unified_diff"] = "".join(diff)
        result["has_changes"] = len(diff) > 0

    elif diff_type == "full":
        diff = list(unified_diff(
            tpl_current.splitlines(keepends=True),
            proj_current.splitlines(keepends=True),
            fromfile=f"{file_path} (template)",
            tofile=f"{file_path} (project)",
        ))
        result["unified_diff"] = "".join(diff)
        result["has_changes"] = len(diff) > 0

    elif diff_type == "three_way":
        merge = _three_way_merge(base_content, tpl_current, proj_current)
        result["base_content"] = base_content
        result["template_content"] = tpl_current
        result["project_content"] = proj_current
        result["merge_result"] = merge

        # Also include a full diff for context
        diff = list(unified_diff(
            tpl_current.splitlines(keepends=True),
            proj_current.splitlines(keepends=True),
            fromfile=f"{file_path} (template)",
            tofile=f"{file_path} (project)",
        ))
        result["unified_diff"] = "".join(diff)
    else:
        return json.dumps({"error": f"Unknown diff_type: {diff_type}"}, ensure_ascii=False)

    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
async def template_apply_file(
    project_path: str,
    file_path: str,
    source: str = "template",
    content: str = "",
) -> str:
    """
    Apply a template file to the project and return the updated manifest entry.
    Does NOT write the manifest itself -- call template_finalize_sync for that.

    Args:
        project_path: Path to the project root directory
        file_path: Relative path of the file (e.g. "CLAUDE.md")
        source: One of:
            - "template": copy from template with placeholder replacement
            - "provided": use the content parameter as-is
            - "skip": don't change the project file, just update manifest hashes
        content: File content to write (only used when source="provided")

    Returns:
        JSON with the new manifest entry for this file (hashes, modification status)
    """
    pp = pathlib.Path(project_path).resolve()
    manifest, errors = _load_manifest(pp)
    if manifest is None:
        return json.dumps({"error": errors[0]}, ensure_ascii=False)

    placeholders = manifest.get("placeholders", {})
    template_dir = _get_template_dir(manifest)

    # Read current template content
    tpl_raw = _read_file(template_dir / file_path)
    tpl_replaced = _apply_placeholders(tpl_raw, placeholders) if tpl_raw else ""
    tpl_raw_hash = _sha256(tpl_raw) if tpl_raw else ""
    tpl_hash = _sha256(tpl_replaced) if tpl_replaced else ""

    target_path = pp / file_path

    if source == "template":
        if not tpl_raw:
            return json.dumps({"error": f"Template file not found: {file_path}"}, ensure_ascii=False)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        _write_file_atomic(target_path, tpl_replaced)
        local_hash = tpl_hash
        action = "written_from_template"
        locally_modified = False

    elif source == "provided":
        if not content:
            return json.dumps({"error": "source='provided' requires content parameter"}, ensure_ascii=False)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        _write_file_atomic(target_path, content)
        local_hash = _sha256(content)
        action = "written_from_provided"
        locally_modified = local_hash != tpl_hash

    elif source == "skip":
        # Don't touch the project file, just acknowledge the template change
        proj_content = _read_file(target_path)
        local_hash = _sha256(proj_content) if proj_content else ""
        action = "skipped"
        locally_modified = local_hash != tpl_hash

    else:
        return json.dumps({"error": f"Unknown source: {source}"}, ensure_ascii=False)

    manifest_entry = {
        "templateHash": tpl_hash,
        "templateRawHash": tpl_raw_hash,
        "localHash": local_hash,
        "locallyModified": locally_modified,
    }

    return json.dumps({
        "file_path": file_path,
        "action": action,
        "manifest_entry": manifest_entry,
        "bytes_written": len(tpl_replaced.encode("utf-8")) if source == "template"
            else len(content.encode("utf-8")) if source == "provided"
            else 0,
    }, ensure_ascii=False)


@mcp.tool()
async def template_finalize_sync(
    project_path: str,
    applied_files: str,
    new_files: str = "[]",
) -> str:
    """
    Finalize a sync operation by writing the updated manifest.
    This is the ONLY tool that writes .claude/template-manifest.json.

    Args:
        project_path: Path to the project root directory
        applied_files: JSON array of template_apply_file results
            (each must have file_path and manifest_entry)
        new_files: JSON array of new file paths added from template (optional)

    Returns:
        JSON confirmation with counts
    """
    pp = pathlib.Path(project_path).resolve()
    manifest, errors = _load_manifest(pp)
    if manifest is None:
        return json.dumps({"error": errors[0]}, ensure_ascii=False)

    try:
        applied = json.loads(applied_files)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid applied_files JSON: {e}"}, ensure_ascii=False)

    try:
        new = json.loads(new_files)
    except json.JSONDecodeError:
        new = []

    files = manifest.get("files", {})

    # Update entries from applied files
    updated_count = 0
    for item in applied:
        fp = item.get("file_path", "")
        entry = item.get("manifest_entry", {})
        if fp and entry:
            # Preserve reason field if it existed and file is still locally modified
            old_entry = files.get(fp, {})
            new_entry = dict(entry)
            if new_entry.get("locallyModified") and old_entry.get("reason"):
                new_entry["reason"] = old_entry["reason"]
            elif not new_entry.get("locallyModified") and "reason" in new_entry:
                del new_entry["reason"]
            files[fp] = new_entry
            updated_count += 1

    # Add new files
    added_count = 0
    for fp in new:
        if fp not in files:
            # These should already have been applied via template_apply_file
            # but ensure they have an entry
            files[fp] = {
                "templateHash": "",
                "templateRawHash": "",
                "localHash": "",
                "locallyModified": False,
            }
            added_count += 1

    # Update lastSynced
    new_head = _git_head(_template_repo_resolved(manifest)) or manifest.get("lastSynced", "")
    manifest["lastSynced"] = new_head
    manifest["version"] = MANIFEST_VERSION
    manifest["files"] = dict(sorted(files.items()))

    # Write manifest atomically
    manifest_path = pp / ".claude" / "template-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_json = json.dumps(manifest, indent=2, ensure_ascii=False)
    _write_file_atomic(manifest_path, manifest_json)

    return json.dumps({
        "manifest_path": ".claude/template-manifest.json",
        "last_synced": new_head,
        "files_updated": updated_count,
        "files_added": added_count,
        "manifest_written": True,
    }, ensure_ascii=False)


@mcp.tool()
async def template_reverse_placeholders(
    project_path: str,
    file_path: str,
    content: str = "",
) -> str:
    """
    Reverse placeholder replacement in a project file, producing template-ready content.
    Sorts placeholders by value length descending to avoid partial matches.

    Args:
        project_path: Path to the project root directory
        file_path: Relative path of the file to reverse
        content: Content to reverse (if empty, reads the project file)

    Returns:
        JSON with reversed content, replacements made, and replacement order
    """
    pp = pathlib.Path(project_path).resolve()
    manifest, errors = _load_manifest(pp)
    if manifest is None:
        return json.dumps({"error": errors[0]}, ensure_ascii=False)

    placeholders = manifest.get("placeholders", {})

    if not content:
        content = _read_file(pp / file_path)
        if content is None:
            return json.dumps({"error": f"File not found: {file_path}"}, ensure_ascii=False)

    reversed_content, replacements = _reverse_placeholders(content, placeholders)

    # Show the replacement order used
    sorted_order = [k for k, v in sorted(placeholders.items(), key=lambda x: (-len(x[1]), x[0])) if v]

    return json.dumps({
        "file_path": file_path,
        "original_content": content,
        "reversed_content": reversed_content,
        "replacements_made": replacements,
        "replacement_order": sorted_order,
    }, ensure_ascii=False)


@mcp.tool()
async def template_check_cross_variant(
    template_repo: str,
    variant: str,
    file_path: str,
) -> str:
    """
    Check if a file is shared across template variants by comparing content.

    Args:
        template_repo: Path to the claude-code-toolkit repo
        variant: Current variant name
        file_path: Relative path within the variant (e.g. "AGENT_TEAM.md")

    Returns:
        JSON with which variants are identical, which differ, and whether propagation is safe
    """
    repo = pathlib.Path(template_repo).resolve()
    templates_dir = repo / "templates"

    current_content = _read_file(templates_dir / variant / file_path)
    if current_content is None:
        return json.dumps({"error": f"File not found in current variant: {variant}/{file_path}"}, ensure_ascii=False)

    current_hash = _sha256(current_content)
    identical = []
    different = []
    missing = []

    for v in KNOWN_VARIANTS:
        other_content = _read_file(templates_dir / v / file_path)
        if other_content is None:
            missing.append(v)
        elif _sha256(other_content) == current_hash:
            identical.append(v)
        else:
            different.append(v)

    return json.dumps({
        "file_path": file_path,
        "source_variant": variant,
        "is_shared": len(different) == 0 and len(missing) == 0,
        "variants_identical": identical,
        "variants_different": different,
        "variants_missing": missing,
        "can_propagate": len(different) == 0,
    }, ensure_ascii=False)


@mcp.tool()
async def template_propagate_to_variants(
    template_repo: str,
    file_path: str,
    content: str,
    target_variants: str,
) -> str:
    """
    Write template-ready content to multiple variant directories.

    Args:
        template_repo: Path to the claude-code-toolkit repo
        file_path: Relative path within each variant (e.g. "AGENT_TEAM.md")
        content: Template-ready content with {{PLACEHOLDERS}}
        target_variants: JSON array of variant names to update

    Returns:
        JSON with which variants were written, skipped, or errored
    """
    repo = pathlib.Path(template_repo).resolve()
    templates_dir = repo / "templates"

    try:
        variants = json.loads(target_variants)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid target_variants JSON: {e}"}, ensure_ascii=False)

    written = []
    errors_list = []

    for v in variants:
        target = templates_dir / v / file_path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_file_atomic(target, content)
            written.append(v)
        except OSError as e:
            errors_list.append({"variant": v, "error": str(e)})

    return json.dumps({
        "file_path": file_path,
        "written_to": written,
        "errors": errors_list,
    }, ensure_ascii=False)
