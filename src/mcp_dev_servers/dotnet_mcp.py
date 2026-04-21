"""
.NET/C# MCP Server Tools

A comprehensive set of tools for .NET development including:
- Build and test execution with error parsing
- NuGet package management and vulnerability scanning
- Project/solution analysis
- Entity Framework Core migrations
- Code metrics and quality analysis
- Code coverage reporting
- Stack trace parsing
"""

import os
import json
import time
import re
import subprocess
import fnmatch
import xml.etree.ElementTree as ET
from pathlib import Path
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("dotnet-tools")

# Cross-platform helper for subprocess creation flags
_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


# -------------------------
# Helpers
# -------------------------

def run_cmd(cmd: list[str], cwd: str | None = None, timeout: int = 1800, env: dict | None = None) -> dict:
    """
    Run a shell command and capture output.

    Args:
        cmd: Command and arguments as a list
        cwd: Working directory for the command
        timeout: Maximum execution time in seconds (default 30 minutes)
        env: Optional environment variables dict

    Returns:
        Dict with exit_code, stdout, stderr, and duration_s
    """
    start = time.time()
    try:
        p = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            creationflags=_SUBPROCESS_FLAGS,
            env=env,
        )
        return {
            "exit_code": p.returncode,
            "stdout": p.stdout[-200_000:] if p.stdout else "",
            "stderr": p.stderr[-200_000:] if p.stderr else "",
            "duration_s": round(time.time() - start, 3),
        }
    except subprocess.TimeoutExpired:
        return {
            "exit_code": 124,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "duration_s": round(time.time() - start, 3),
        }
    except FileNotFoundError as e:
        return {
            "exit_code": 127,
            "stdout": "",
            "stderr": f"Command not found: {e}",
            "duration_s": round(time.time() - start, 3),
        }


def _get_dotnet_env() -> dict:
    """Get environment with English locale for dotnet CLI."""
    env = os.environ.copy()
    env["DOTNET_CLI_UI_LANGUAGE"] = "en"
    return env


def _find_solution_or_project(root: Path) -> Path | None:
    """Find a .sln or .csproj file in the given directory."""
    # Prefer .sln files
    slns = list(root.glob("*.sln"))
    if slns:
        return slns[0]
    # Fall back to .csproj
    csprojs = list(root.glob("*.csproj"))
    if csprojs:
        return csprojs[0]
    return None


# -------------------------
# Build & Test Tools (moved from server.py)
# -------------------------

@mcp.tool()
async def build_and_extract_errors(project_or_sln: str, configuration: str = "Debug") -> str:
    """
    Build a .NET project or solution and extract structured errors/warnings.

    Args:
        project_or_sln: Path to .csproj or .sln file
        configuration: Build configuration (Debug/Release)

    Returns:
        JSON with exit_code, errors[], warnings[], duration_s
    """
    path = Path(project_or_sln)
    cwd = path.parent if path.is_file() else path

    result = run_cmd(
        ["dotnet", "build", str(path), "-c", configuration, "--nologo"],
        cwd=str(cwd),
    )

    msbuild_re = re.compile(
        r"^(?P<file>.*?)(\((?P<line>\d+)(,(?P<col>\d+))?\))?:\s*"
        r"(?P<level>error|warning)\s*(?P<code>[A-Z]{2,}\d{4})?:\s*(?P<msg>.*)$",
        re.IGNORECASE,
    )

    errors, warnings = [], []

    for line in (result["stdout"] + "\n" + result["stderr"]).splitlines():
        m = msbuild_re.match(line.strip())
        if not m:
            continue

        item = {
            "file": m.group("file"),
            "line": int(m.group("line")) if m.group("line") else None,
            "col": int(m.group("col")) if m.group("col") else None,
            "code": m.group("code"),
            "message": m.group("msg"),
        }

        if m.group("level").lower() == "error":
            errors.append(item)
        else:
            warnings.append(item)

    return json.dumps(
        {
            "exit_code": result["exit_code"],
            "duration_s": result["duration_s"],
            "errors": errors,
            "warnings": warnings,
            "stdout_tail": result["stdout"][-2000:],
            "stderr_tail": result["stderr"][-2000:],
        },
        ensure_ascii=False,
    )


@mcp.tool()
async def run_tests_summary(project_or_sln: str, configuration: str = "Debug") -> str:
    """
    Run .NET tests and parse TRX results for a summary.

    Args:
        project_or_sln: Path to .csproj or .sln file
        configuration: Build configuration (Debug/Release)

    Returns:
        JSON with passed, failed, skipped, total counts and failure details
    """
    path = Path(project_or_sln)
    cwd = path.parent if path.is_file() else path

    results_dir = cwd / "TestResults_MCP"

    # Clean previous results
    if results_dir.exists():
        for f in results_dir.rglob("*"):
            if f.is_file():
                try:
                    f.unlink()
                except Exception:
                    pass

    results_dir.mkdir(exist_ok=True)

    trx = results_dir / "mcp.trx"

    result = run_cmd(
        [
            "dotnet", "test", str(path),
            "-c", configuration,
            "--logger", f"trx;LogFileName={trx.name}",
            "--results-directory", str(results_dir),
            "--nologo",
        ],
        cwd=str(cwd),
        timeout=3600,
    )

    summary = {
        "exit_code": result["exit_code"],
        "duration_s": result["duration_s"],
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "total": 0,
        "failures": [],
    }

    if trx.exists():
        try:
            tree = ET.parse(trx)
            root = tree.getroot()

            counters = root.find(".//{*}Counters")
            if counters is not None:
                summary["total"] = int(counters.attrib.get("total", 0))
                summary["passed"] = int(counters.attrib.get("passed", 0))
                summary["failed"] = int(counters.attrib.get("failed", 0))
                summary["skipped"] = int(counters.attrib.get("notExecuted", 0))

            for r in root.findall(".//{*}UnitTestResult"):
                if r.attrib.get("outcome") == "Failed":
                    msg = ""
                    m = r.find(".//{*}Message")
                    if m is not None and m.text:
                        msg = m.text.strip()
                    summary["failures"].append(
                        {"test": r.attrib.get("testName"), "message": msg[:2000]}
                    )
        except ET.ParseError:
            summary["trx_parse_error"] = True

    return json.dumps(summary, ensure_ascii=False)


@mcp.tool()
async def analyze_namespace_conflicts(root: str, pattern: str = "*.cs") -> str:
    """
    Find duplicate type definitions (classes, structs, interfaces, etc.) in C# code.

    Args:
        root: Root directory to scan
        pattern: Glob pattern for files (default: *.cs)

    Returns:
        JSON with conflicts[] containing name and locations[]
    """
    root_path = Path(root).resolve()
    exclude = {".git", ".vs", "bin", "obj"}

    type_re = re.compile(r"\b(class|struct|interface|record|enum)\s+([A-Za-z_]\w*)\b")
    found = {}

    for p in root_path.rglob(pattern):
        if any(part in exclude for part in p.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        for _, name in type_re.findall(text):
            found.setdefault(name, []).append(str(p.relative_to(root_path)))

    conflicts = [
        {"name": k, "locations": v}
        for k, v in found.items()
        if len(v) > 1
    ]

    return json.dumps({"conflicts": conflicts}, ensure_ascii=False)


# -------------------------
# NuGet Tools
# -------------------------

@mcp.tool()
async def nuget_list_outdated(project_or_sln: str, include_transitive: bool = False) -> str:
    """
    List outdated NuGet packages in a project or solution.

    Args:
        project_or_sln: Path to .csproj or .sln file
        include_transitive: Include transitive dependencies

    Returns:
        JSON with outdated packages grouped by project
    """
    path = Path(project_or_sln)
    cwd = path.parent if path.is_file() else path

    cmd = ["dotnet", "list", str(path), "package", "--outdated"]
    if include_transitive:
        cmd.append("--include-transitive")

    # Force English output for consistent parsing
    result = run_cmd(cmd, cwd=str(cwd), timeout=300, env=_get_dotnet_env())

    # Parse the dotnet list package output
    packages = []
    current_project = None

    # Regex patterns for parsing (handles both quoted styles)
    project_re = re.compile(r"^Project\s+[`'\"](.+?)[`'\"]")
    # Match: > PackageName    CurrentVersion    LatestVersion
    package_re = re.compile(r"^\s*>\s+(\S+)\s+(\S+)\s+\S+\s+(\S+)")

    for line in result["stdout"].splitlines():
        proj_match = project_re.match(line)
        if proj_match:
            current_project = proj_match.group(1)
            continue

        pkg_match = package_re.match(line)
        if pkg_match and current_project:
            packages.append({
                "project": current_project,
                "package": pkg_match.group(1),
                "current": pkg_match.group(2),
                "latest": pkg_match.group(3),
            })

    return json.dumps({
        "exit_code": result["exit_code"],
        "outdated_count": len(packages),
        "packages": packages,
        "stdout_tail": result["stdout"][-2000:] if not packages else "",
    }, ensure_ascii=False)


@mcp.tool()
async def nuget_check_vulnerabilities(project_or_sln: str, include_transitive: bool = True) -> str:
    """
    Check for known security vulnerabilities in NuGet packages.

    Args:
        project_or_sln: Path to .csproj or .sln file
        include_transitive: Include transitive dependencies (recommended)

    Returns:
        JSON with vulnerable packages and severity levels
    """
    path = Path(project_or_sln)
    cwd = path.parent if path.is_file() else path

    cmd = ["dotnet", "list", str(path), "package", "--vulnerable"]
    if include_transitive:
        cmd.append("--include-transitive")

    # Force English output for consistent parsing
    result = run_cmd(cmd, cwd=str(cwd), timeout=300, env=_get_dotnet_env())

    vulnerabilities = []
    current_project = None

    project_re = re.compile(r"^Project\s+[`'\"](.+?)[`'\"]")
    # Match: > PackageName    Version    Severity    Advisory URL
    vuln_re = re.compile(r"^\s*>\s+(\S+)\s+(\S+)\s+(Low|Moderate|High|Critical)\s+(\S+)?", re.IGNORECASE)

    for line in result["stdout"].splitlines():
        proj_match = project_re.match(line)
        if proj_match:
            current_project = proj_match.group(1)
            continue

        vuln_match = vuln_re.match(line)
        if vuln_match and current_project:
            vulnerabilities.append({
                "project": current_project,
                "package": vuln_match.group(1),
                "version": vuln_match.group(2),
                "severity": vuln_match.group(3),
                "advisory_url": vuln_match.group(4) if vuln_match.group(4) else None,
            })

    # Check for "no vulnerable packages" message
    has_vulnerabilities = len(vulnerabilities) > 0 or "has the following vulnerable packages" in result["stdout"]

    return json.dumps({
        "exit_code": result["exit_code"],
        "has_vulnerabilities": has_vulnerabilities,
        "vulnerability_count": len(vulnerabilities),
        "vulnerabilities": vulnerabilities,
        "stdout_tail": result["stdout"][-2000:] if not vulnerabilities else "",
    }, ensure_ascii=False)


@mcp.tool()
async def nuget_dependency_tree(project_or_sln: str, include_transitive: bool = True) -> str:
    """
    Get the full dependency tree for NuGet packages.

    Args:
        project_or_sln: Path to .csproj or .sln file
        include_transitive: Include transitive dependencies

    Returns:
        JSON with dependency tree grouped by project and framework
    """
    path = Path(project_or_sln)
    cwd = path.parent if path.is_file() else path

    cmd = ["dotnet", "list", str(path), "package"]
    if include_transitive:
        cmd.append("--include-transitive")

    # Force English output for consistent parsing
    result = run_cmd(cmd, cwd=str(cwd), timeout=300, env=_get_dotnet_env())

    projects = []
    current_project = None
    current_framework = None
    top_level = []
    transitive = []

    project_re = re.compile(r"^Project\s+[`'\"](.+?)[`'\"]")
    framework_re = re.compile(r"^\s*\[(\S+)\]")
    top_level_re = re.compile(r"^\s*>\s+(\S+)\s+(\S+)\s+(\S+)?")
    transitive_re = re.compile(r"^\s*>\s+(\S+)\s+(\S+)")

    in_top_level = False
    in_transitive = False

    for line in result["stdout"].splitlines():
        proj_match = project_re.match(line)
        if proj_match:
            # Save previous project if exists
            if current_project:
                projects.append({
                    "project": current_project,
                    "framework": current_framework,
                    "top_level": top_level,
                    "transitive": transitive,
                })
            current_project = proj_match.group(1)
            current_framework = None
            top_level = []
            transitive = []
            in_top_level = False
            in_transitive = False
            continue

        fw_match = framework_re.match(line)
        if fw_match:
            current_framework = fw_match.group(1)
            continue

        if "Top-level" in line:
            in_top_level = True
            in_transitive = False
            continue
        if "Transitive" in line:
            in_transitive = True
            in_top_level = False
            continue

        pkg_match = top_level_re.match(line)
        if pkg_match:
            pkg = {
                "name": pkg_match.group(1),
                "requested": pkg_match.group(2),
                "resolved": pkg_match.group(3) if pkg_match.group(3) else pkg_match.group(2),
            }
            if in_top_level:
                top_level.append(pkg)
            elif in_transitive:
                transitive.append({"name": pkg_match.group(1), "version": pkg_match.group(2)})

    # Don't forget last project
    if current_project:
        projects.append({
            "project": current_project,
            "framework": current_framework,
            "top_level": top_level,
            "transitive": transitive,
        })

    return json.dumps({
        "exit_code": result["exit_code"],
        "project_count": len(projects),
        "projects": projects,
    }, ensure_ascii=False)


# -------------------------
# Project/Solution Analysis Tools
# -------------------------

@mcp.tool()
async def parse_csproj(csproj_path: str) -> str:
    """
    Parse a .csproj file and extract key information.

    Args:
        csproj_path: Path to the .csproj file

    Returns:
        JSON with target framework, package references, project references, etc.
    """
    path = Path(csproj_path)
    if not path.exists():
        return json.dumps({"error": f"File not found: {csproj_path}"}, ensure_ascii=False)

    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except ET.ParseError as e:
        return json.dumps({"error": f"XML parse error: {e}"}, ensure_ascii=False)

    # Handle both old-style and SDK-style csproj (with or without namespace)
    def find_all(tag):
        # Try without namespace first (SDK-style)
        results = root.findall(f".//{tag}")
        if not results:
            # Try with MSBuild namespace (old-style)
            ns = {"ms": "http://schemas.microsoft.com/developer/msbuild/2003"}
            results = root.findall(f".//ms:{tag}", ns)
        return results

    def find_text(tag, default=None):
        elems = find_all(tag)
        return elems[0].text if elems and elems[0].text else default

    # Extract properties
    target_framework = find_text("TargetFramework")
    target_frameworks = find_text("TargetFrameworks")
    output_type = find_text("OutputType", "Library")
    nullable = find_text("Nullable", "disable")
    implicit_usings = find_text("ImplicitUsings", "disable")
    root_namespace = find_text("RootNamespace")
    assembly_name = find_text("AssemblyName")
    version = find_text("Version")
    authors = find_text("Authors")
    description = find_text("Description")

    # Extract package references
    package_refs = []
    for pkg in find_all("PackageReference"):
        name = pkg.attrib.get("Include")
        version = pkg.attrib.get("Version") or pkg.find("Version")
        if version is not None and hasattr(version, "text"):
            version = version.text
        package_refs.append({"name": name, "version": version})

    # Extract project references
    project_refs = []
    for proj in find_all("ProjectReference"):
        include = proj.attrib.get("Include", "")
        project_refs.append(include.replace("\\", "/"))

    # Extract compile items (if explicit)
    compile_items = []
    for comp in find_all("Compile"):
        include = comp.attrib.get("Include", "")
        if include:
            compile_items.append(include)

    return json.dumps({
        "path": str(path),
        "sdk_style": root.attrib.get("Sdk") is not None,
        "sdk": root.attrib.get("Sdk"),
        "target_framework": target_framework,
        "target_frameworks": target_frameworks.split(";") if target_frameworks else None,
        "output_type": output_type,
        "nullable": nullable,
        "implicit_usings": implicit_usings,
        "root_namespace": root_namespace,
        "assembly_name": assembly_name,
        "version": version,
        "authors": authors,
        "description": description,
        "package_references": package_refs,
        "project_references": project_refs,
        "explicit_compile_items": compile_items[:50] if compile_items else [],
    }, ensure_ascii=False)


@mcp.tool()
async def analyze_project_references(solution_or_dir: str) -> str:
    """
    Analyze inter-project dependencies in a solution or directory.

    Args:
        solution_or_dir: Path to .sln file or directory containing projects

    Returns:
        JSON with project dependency graph and potential issues
    """
    path = Path(solution_or_dir)

    # Find all csproj files
    if path.suffix == ".sln":
        root_dir = path.parent
    else:
        root_dir = path

    csproj_files = list(root_dir.rglob("*.csproj"))

    # Exclude common non-source directories
    exclude = {"bin", "obj", ".git", ".vs", "node_modules", "packages"}
    csproj_files = [p for p in csproj_files if not any(x in p.parts for x in exclude)]

    projects = {}

    for csproj in csproj_files:
        try:
            tree = ET.parse(csproj)
            root = tree.getroot()

            proj_name = csproj.stem
            proj_refs = []

            for ref in root.findall(".//ProjectReference"):
                include = ref.attrib.get("Include", "")
                # Normalize and extract project name
                ref_path = Path(include.replace("\\", "/"))
                ref_name = ref_path.stem
                proj_refs.append(ref_name)

            target_fw = None
            tf_elem = root.find(".//TargetFramework")
            if tf_elem is not None and tf_elem.text:
                target_fw = tf_elem.text
            else:
                tfs_elem = root.find(".//TargetFrameworks")
                if tfs_elem is not None and tfs_elem.text:
                    target_fw = tfs_elem.text.split(";")[0]

            projects[proj_name] = {
                "path": str(csproj.relative_to(root_dir)),
                "target_framework": target_fw,
                "references": proj_refs,
            }
        except Exception as e:
            projects[csproj.stem] = {"error": str(e)}

    # Detect circular references
    def find_cycles(graph, start, visited=None, path=None):
        if visited is None:
            visited = set()
        if path is None:
            path = []

        visited.add(start)
        path.append(start)

        cycles = []
        for neighbor in graph.get(start, {}).get("references", []):
            if neighbor in path:
                cycle_start = path.index(neighbor)
                cycles.append(path[cycle_start:] + [neighbor])
            elif neighbor not in visited and neighbor in graph:
                cycles.extend(find_cycles(graph, neighbor, visited.copy(), path.copy()))

        return cycles

    all_cycles = []
    for proj in projects:
        cycles = find_cycles(projects, proj)
        for cycle in cycles:
            cycle_key = tuple(sorted(cycle[:-1]))
            if cycle_key not in [tuple(sorted(c[:-1])) for c in all_cycles]:
                all_cycles.append(cycle)

    # Find orphan projects (no incoming references)
    referenced = set()
    for proj_data in projects.values():
        if isinstance(proj_data, dict) and "references" in proj_data:
            referenced.update(proj_data["references"])

    orphans = [p for p in projects if p not in referenced and projects[p].get("references")]

    return json.dumps({
        "root": str(root_dir),
        "project_count": len(projects),
        "projects": projects,
        "circular_references": all_cycles,
        "potential_entry_points": orphans,
    }, ensure_ascii=False)


@mcp.tool()
async def check_framework_compatibility(solution_or_dir: str) -> str:
    """
    Check for target framework mismatches between projects.

    Args:
        solution_or_dir: Path to .sln file or directory

    Returns:
        JSON with framework versions and compatibility issues
    """
    path = Path(solution_or_dir)
    root_dir = path.parent if path.suffix == ".sln" else path

    csproj_files = list(root_dir.rglob("*.csproj"))
    exclude = {"bin", "obj", ".git", ".vs", "node_modules", "packages"}
    csproj_files = [p for p in csproj_files if not any(x in p.parts for x in exclude)]

    frameworks = {}
    issues = []

    for csproj in csproj_files:
        try:
            tree = ET.parse(csproj)
            root = tree.getroot()

            proj_name = csproj.stem

            tf_elem = root.find(".//TargetFramework")
            tfs_elem = root.find(".//TargetFrameworks")

            if tf_elem is not None and tf_elem.text:
                frameworks[proj_name] = {
                    "frameworks": [tf_elem.text],
                    "path": str(csproj.relative_to(root_dir)),
                }
            elif tfs_elem is not None and tfs_elem.text:
                frameworks[proj_name] = {
                    "frameworks": tfs_elem.text.split(";"),
                    "path": str(csproj.relative_to(root_dir)),
                }

            # Check project references for compatibility
            for ref in root.findall(".//ProjectReference"):
                include = ref.attrib.get("Include", "")
                ref_name = Path(include.replace("\\", "/")).stem

                if ref_name in frameworks and proj_name in frameworks:
                    proj_fws = set(frameworks[proj_name]["frameworks"])
                    ref_fws = set(frameworks[ref_name]["frameworks"])

                    # Simple compatibility check
                    if not proj_fws.intersection(ref_fws):
                        issues.append({
                            "type": "framework_mismatch",
                            "project": proj_name,
                            "project_frameworks": list(proj_fws),
                            "references": ref_name,
                            "reference_frameworks": list(ref_fws),
                        })
        except Exception:
            continue

    # Group by framework for summary
    framework_groups = {}
    for proj, data in frameworks.items():
        for fw in data.get("frameworks", []):
            framework_groups.setdefault(fw, []).append(proj)

    return json.dumps({
        "root": str(root_dir),
        "project_count": len(frameworks),
        "frameworks_used": list(framework_groups.keys()),
        "framework_groups": framework_groups,
        "projects": frameworks,
        "compatibility_issues": issues,
    }, ensure_ascii=False)


# -------------------------
# Entity Framework Core Tools
# -------------------------

@mcp.tool()
async def ef_migrations_status(project_path: str, context: str | None = None, startup_project: str | None = None) -> str:
    """
    List Entity Framework Core migrations and their status.

    Args:
        project_path: Path to the project containing migrations
        context: DbContext class name (if multiple contexts)
        startup_project: Path to the startup project (if different)

    Returns:
        JSON with list of migrations and their applied status
    """
    path = Path(project_path)
    cwd = path.parent if path.is_file() else path

    cmd = ["dotnet", "ef", "migrations", "list"]

    if path.is_file():
        cmd.extend(["--project", str(path)])

    if context:
        cmd.extend(["--context", context])

    if startup_project:
        cmd.extend(["--startup-project", startup_project])

    # Force English output for consistent parsing
    result = run_cmd(cmd, cwd=str(cwd), timeout=120, env=_get_dotnet_env())

    migrations = []
    pending_count = 0
    applied_count = 0

    for line in result["stdout"].splitlines():
        line = line.strip()
        if not line or line.startswith("Build") or line.startswith("Done"):
            continue

        # EF Core outputs migrations with (Pending) suffix for unapplied
        is_pending = "(Pending)" in line
        migration_name = line.replace("(Pending)", "").strip()

        if migration_name and not migration_name.startswith("info:"):
            migrations.append({
                "name": migration_name,
                "pending": is_pending,
            })
            if is_pending:
                pending_count += 1
            else:
                applied_count += 1

    return json.dumps({
        "exit_code": result["exit_code"],
        "migration_count": len(migrations),
        "applied": applied_count,
        "pending": pending_count,
        "migrations": migrations,
        "stderr": result["stderr"][:1000] if result["exit_code"] != 0 else "",
    }, ensure_ascii=False)


@mcp.tool()
async def ef_pending_migrations(project_path: str, context: str | None = None, startup_project: str | None = None) -> str:
    """
    Check if database has pending migrations that need to be applied.

    Args:
        project_path: Path to the project containing migrations
        context: DbContext class name (if multiple contexts)
        startup_project: Path to the startup project (if different)

    Returns:
        JSON with pending migration status and list
    """
    # Reuse migrations_status and filter
    status_json = await ef_migrations_status(project_path, context, startup_project)
    status = json.loads(status_json)

    pending = [m for m in status.get("migrations", []) if m.get("pending")]

    return json.dumps({
        "exit_code": status.get("exit_code", 0),
        "has_pending": len(pending) > 0,
        "pending_count": len(pending),
        "pending_migrations": pending,
        "recommendation": "Run 'dotnet ef database update' to apply pending migrations" if pending else "Database is up to date",
    }, ensure_ascii=False)


@mcp.tool()
async def ef_dbcontext_info(project_path: str, context: str | None = None, startup_project: str | None = None) -> str:
    """
    Get information about a DbContext including provider and connection info.

    Args:
        project_path: Path to the project containing the DbContext
        context: DbContext class name (if multiple contexts)
        startup_project: Path to the startup project (if different)

    Returns:
        JSON with DbContext details
    """
    path = Path(project_path)
    cwd = path.parent if path.is_file() else path

    cmd = ["dotnet", "ef", "dbcontext", "info"]

    if path.is_file():
        cmd.extend(["--project", str(path)])

    if context:
        cmd.extend(["--context", context])

    if startup_project:
        cmd.extend(["--startup-project", startup_project])

    # Force English output for consistent parsing
    result = run_cmd(cmd, cwd=str(cwd), timeout=120, env=_get_dotnet_env())

    info = {}

    for line in result["stdout"].splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip().lower().replace(" ", "_")
            value = value.strip()
            if key and value:
                info[key] = value

    return json.dumps({
        "exit_code": result["exit_code"],
        "context_info": info,
        "raw_output": result["stdout"][:2000] if not info else "",
        "stderr": result["stderr"][:1000] if result["exit_code"] != 0 else "",
    }, ensure_ascii=False)


# -------------------------
# Code Metrics Tools
# -------------------------

@mcp.tool()
async def analyze_method_complexity(root: str, threshold: int = 10) -> str:
    """
    Analyze cyclomatic complexity of methods in C# files using heuristics.

    Uses decision point counting (if, else, case, while, for, foreach, &&, ||, ?:, catch)
    as an approximation of cyclomatic complexity.

    Args:
        root: Root directory to scan
        threshold: Minimum complexity to report (default: 10)

    Returns:
        JSON with methods exceeding the complexity threshold
    """
    root_path = Path(root).resolve()
    exclude = {".git", ".vs", "bin", "obj", "node_modules"}

    # Regex patterns
    method_re = re.compile(
        r"(?:public|private|protected|internal|static|async|virtual|override|sealed|\s)*"
        r"(?:[\w<>\[\],\s]+)\s+"  # Return type
        r"(\w+)\s*"  # Method name
        r"\([^)]*\)\s*"  # Parameters
        r"(?:where\s+[^{]+)?"  # Generic constraints
        r"\s*\{",  # Opening brace
        re.MULTILINE
    )

    # Decision points that increase complexity
    decision_keywords = [
        r"\bif\s*\(",
        r"\belse\b",
        r"\bcase\s+",
        r"\bwhile\s*\(",
        r"\bfor\s*\(",
        r"\bforeach\s*\(",
        r"\bcatch\s*\(",
        r"\?\?",  # Null coalescing
        r"\?(?!=)",  # Ternary (not ?.  operator)
        r"&&",
        r"\|\|",
    ]
    decision_re = re.compile("|".join(decision_keywords))

    complex_methods = []
    files_scanned = 0

    for p in root_path.rglob("*.cs"):
        if any(part in exclude for part in p.parts):
            continue

        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
            files_scanned += 1
        except Exception:
            continue

        # Find methods and estimate their complexity
        lines = content.split("\n")

        for match in method_re.finditer(content):
            method_name = match.group(1)
            start_pos = match.end()

            # Find method body by matching braces
            brace_count = 1
            end_pos = start_pos

            for i, char in enumerate(content[start_pos:], start_pos):
                if char == "{":
                    brace_count += 1
                elif char == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        end_pos = i
                        break

            method_body = content[start_pos:end_pos]

            # Count decision points
            decisions = len(decision_re.findall(method_body))
            complexity = decisions + 1  # Base complexity is 1

            if complexity >= threshold:
                # Find line number
                line_num = content[:match.start()].count("\n") + 1

                complex_methods.append({
                    "file": str(p.relative_to(root_path)),
                    "method": method_name,
                    "line": line_num,
                    "complexity": complexity,
                    "body_lines": method_body.count("\n"),
                })

    # Sort by complexity descending
    complex_methods.sort(key=lambda x: x["complexity"], reverse=True)

    return json.dumps({
        "root": str(root_path),
        "files_scanned": files_scanned,
        "threshold": threshold,
        "methods_found": len(complex_methods),
        "methods": complex_methods[:100],  # Limit to top 100
    }, ensure_ascii=False)


@mcp.tool()
async def find_large_files(root: str, line_threshold: int = 500, pattern: str = "*.cs") -> str:
    """
    Find source files exceeding a line count threshold.

    Args:
        root: Root directory to scan
        line_threshold: Minimum lines to report (default: 500)
        pattern: Glob pattern for files (default: *.cs)

    Returns:
        JSON with files exceeding the threshold
    """
    root_path = Path(root).resolve()
    exclude = {".git", ".vs", "bin", "obj", "node_modules", "packages"}

    large_files = []
    total_files = 0
    total_lines = 0

    for p in root_path.rglob(pattern):
        if any(part in exclude for part in p.parts):
            continue

        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
            total_files += 1
            line_count = content.count("\n") + 1
            total_lines += line_count

            if line_count >= line_threshold:
                # Count code vs blank/comment lines
                code_lines = 0
                blank_lines = 0
                comment_lines = 0
                in_block_comment = False

                for line in content.split("\n"):
                    stripped = line.strip()

                    if in_block_comment:
                        comment_lines += 1
                        if "*/" in stripped:
                            in_block_comment = False
                    elif not stripped:
                        blank_lines += 1
                    elif stripped.startswith("//"):
                        comment_lines += 1
                    elif stripped.startswith("/*"):
                        comment_lines += 1
                        if "*/" not in stripped:
                            in_block_comment = True
                    else:
                        code_lines += 1

                large_files.append({
                    "file": str(p.relative_to(root_path)),
                    "total_lines": line_count,
                    "code_lines": code_lines,
                    "blank_lines": blank_lines,
                    "comment_lines": comment_lines,
                })
        except Exception:
            continue

    # Sort by line count descending
    large_files.sort(key=lambda x: x["total_lines"], reverse=True)

    return json.dumps({
        "root": str(root_path),
        "pattern": pattern,
        "threshold": line_threshold,
        "total_files_scanned": total_files,
        "total_lines": total_lines,
        "large_file_count": len(large_files),
        "files": large_files,
    }, ensure_ascii=False)


@mcp.tool()
async def find_god_classes(root: str, method_threshold: int = 20, field_threshold: int = 15) -> str:
    """
    Find classes with too many methods or fields (potential god classes).

    Args:
        root: Root directory to scan
        method_threshold: Max methods before flagging (default: 20)
        field_threshold: Max fields before flagging (default: 15)

    Returns:
        JSON with classes exceeding thresholds
    """
    root_path = Path(root).resolve()
    exclude = {".git", ".vs", "bin", "obj", "node_modules"}

    # Patterns
    class_re = re.compile(r"\b(?:class|struct|record)\s+(\w+)")
    method_re = re.compile(
        r"(?:public|private|protected|internal|static|async|virtual|override|abstract|sealed|\s)+"
        r"(?:[\w<>\[\],\?\s]+)\s+"
        r"(\w+)\s*\([^)]*\)\s*[{;]"
    )
    field_re = re.compile(
        r"(?:public|private|protected|internal|static|readonly|const|\s)+"
        r"(?:[\w<>\[\],\?\s]+)\s+"
        r"(\w+)\s*[;=]"
    )
    property_re = re.compile(
        r"(?:public|private|protected|internal|static|virtual|override|abstract|\s)+"
        r"(?:[\w<>\[\],\?\s]+)\s+"
        r"(\w+)\s*\{\s*(?:get|set)"
    )

    god_classes = []
    files_scanned = 0

    for p in root_path.rglob("*.cs"):
        if any(part in exclude for part in p.parts):
            continue

        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
            files_scanned += 1
        except Exception:
            continue

        # Simple class detection - find class and count members until next class
        class_matches = list(class_re.finditer(content))

        for i, match in enumerate(class_matches):
            class_name = match.group(1)
            start = match.end()

            # End at next class or end of file
            end = class_matches[i + 1].start() if i + 1 < len(class_matches) else len(content)
            class_body = content[start:end]

            # Count members
            methods = len(method_re.findall(class_body))
            fields = len(field_re.findall(class_body))
            properties = len(property_re.findall(class_body))

            if methods >= method_threshold or fields >= field_threshold:
                line_num = content[:match.start()].count("\n") + 1

                god_classes.append({
                    "file": str(p.relative_to(root_path)),
                    "class": class_name,
                    "line": line_num,
                    "methods": methods,
                    "fields": fields,
                    "properties": properties,
                    "total_members": methods + fields + properties,
                    "issues": [
                        issue for issue in [
                            f"Too many methods ({methods})" if methods >= method_threshold else None,
                            f"Too many fields ({fields})" if fields >= field_threshold else None,
                        ] if issue
                    ],
                })

    # Sort by total members descending
    god_classes.sort(key=lambda x: x["total_members"], reverse=True)

    return json.dumps({
        "root": str(root_path),
        "files_scanned": files_scanned,
        "method_threshold": method_threshold,
        "field_threshold": field_threshold,
        "god_class_count": len(god_classes),
        "classes": god_classes[:50],  # Limit to top 50
    }, ensure_ascii=False)


# -------------------------
# Debugging/Diagnostics Tools
# -------------------------

@mcp.tool()
async def parse_stack_trace(stack_trace: str) -> str:
    """
    Parse a .NET stack trace and extract structured information.

    Args:
        stack_trace: The raw stack trace text

    Returns:
        JSON with parsed frames including file, line, method, and type information
    """
    # .NET stack trace patterns
    # at Namespace.Class.Method(params) in file:line
    frame_re = re.compile(
        r"^\s*at\s+"
        r"(?P<fullmethod>(?P<namespace>[\w.]+)\.(?P<method>\w+))"
        r"\s*\((?P<params>[^)]*)\)"
        r"(?:\s+in\s+(?P<file>[^:]+):line\s+(?P<line>\d+))?",
        re.MULTILINE
    )

    # Exception type pattern
    exception_re = re.compile(
        r"^(?P<type>[\w.]+Exception):\s*(?P<message>.+)$",
        re.MULTILINE
    )

    frames = []
    exceptions = []

    # Parse exception info
    for match in exception_re.finditer(stack_trace):
        exceptions.append({
            "type": match.group("type"),
            "message": match.group("message").strip(),
        })

    # Parse frames
    for match in frame_re.finditer(stack_trace):
        frame = {
            "full_method": match.group("fullmethod"),
            "namespace": match.group("namespace"),
            "method": match.group("method"),
            "params": match.group("params"),
        }

        if match.group("file"):
            frame["file"] = match.group("file")
            frame["line"] = int(match.group("line")) if match.group("line") else None

        frames.append(frame)

    # Find the likely cause (first frame with file info, or first frame)
    likely_cause = None
    for frame in frames:
        if "file" in frame:
            likely_cause = frame
            break
    if not likely_cause and frames:
        likely_cause = frames[0]

    return json.dumps({
        "exception_count": len(exceptions),
        "exceptions": exceptions,
        "frame_count": len(frames),
        "frames": frames,
        "likely_cause": likely_cause,
        "has_source_info": any("file" in f for f in frames),
    }, ensure_ascii=False)


# -------------------------
# Code Coverage Tools
# -------------------------

@mcp.tool()
async def parse_coverage_report(report_path: str) -> str:
    """
    Parse a code coverage report (Cobertura XML format).

    This format is produced by coverlet with:
    dotnet test --collect:"XPlat Code Coverage"

    Args:
        report_path: Path to the coverage.cobertura.xml file

    Returns:
        JSON with coverage statistics by package/class
    """
    path = Path(report_path)

    if not path.exists():
        return json.dumps({"error": f"File not found: {report_path}"}, ensure_ascii=False)

    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except ET.ParseError as e:
        return json.dumps({"error": f"XML parse error: {e}"}, ensure_ascii=False)

    # Overall coverage
    overall = {
        "line_rate": float(root.attrib.get("line-rate", 0)),
        "branch_rate": float(root.attrib.get("branch-rate", 0)),
        "lines_covered": int(root.attrib.get("lines-covered", 0)),
        "lines_valid": int(root.attrib.get("lines-valid", 0)),
        "branches_covered": int(root.attrib.get("branches-covered", 0)),
        "branches_valid": int(root.attrib.get("branches-valid", 0)),
    }

    # Per-package coverage
    packages = []
    for pkg in root.findall(".//package"):
        pkg_name = pkg.attrib.get("name", "")

        classes = []
        for cls in pkg.findall(".//class"):
            class_info = {
                "name": cls.attrib.get("name", ""),
                "filename": cls.attrib.get("filename", ""),
                "line_rate": float(cls.attrib.get("line-rate", 0)),
                "branch_rate": float(cls.attrib.get("branch-rate", 0)),
            }

            # Find uncovered lines
            uncovered = []
            for line in cls.findall(".//line"):
                if line.attrib.get("hits", "0") == "0":
                    uncovered.append(int(line.attrib.get("number", 0)))

            if uncovered:
                class_info["uncovered_lines"] = uncovered[:20]  # Limit
                class_info["uncovered_count"] = len(uncovered)

            classes.append(class_info)

        # Sort classes by coverage (ascending - worst first)
        classes.sort(key=lambda x: x["line_rate"])

        packages.append({
            "name": pkg_name,
            "line_rate": float(pkg.attrib.get("line-rate", 0)),
            "branch_rate": float(pkg.attrib.get("branch-rate", 0)),
            "class_count": len(classes),
            "classes": classes[:20],  # Limit to worst 20
        })

    # Sort packages by coverage (ascending - worst first)
    packages.sort(key=lambda x: x["line_rate"])

    # Find files with zero coverage
    zero_coverage = [
        {"name": c["name"], "file": c["filename"]}
        for p in packages
        for c in p.get("classes", [])
        if c["line_rate"] == 0
    ]

    return json.dumps({
        "overall": overall,
        "overall_line_percent": round(overall["line_rate"] * 100, 2),
        "overall_branch_percent": round(overall["branch_rate"] * 100, 2),
        "package_count": len(packages),
        "packages": packages[:10],  # Limit to worst 10
        "zero_coverage_count": len(zero_coverage),
        "zero_coverage_files": zero_coverage[:20],
    }, ensure_ascii=False)


@mcp.tool()
async def run_coverage(project_or_sln: str, configuration: str = "Debug") -> str:
    """
    Run tests with code coverage collection and return summary.

    Args:
        project_or_sln: Path to .csproj or .sln file
        configuration: Build configuration (Debug/Release)

    Returns:
        JSON with test results and coverage summary
    """
    path = Path(project_or_sln)
    cwd = path.parent if path.is_file() else path

    results_dir = cwd / "TestResults_MCP"

    # Clean previous results
    if results_dir.exists():
        for f in results_dir.rglob("*"):
            if f.is_file():
                try:
                    f.unlink()
                except Exception:
                    pass

    results_dir.mkdir(exist_ok=True)

    result = run_cmd(
        [
            "dotnet", "test", str(path),
            "-c", configuration,
            "--collect:XPlat Code Coverage",
            "--results-directory", str(results_dir),
            "--nologo",
        ],
        cwd=str(cwd),
        timeout=3600,
    )

    # Find coverage report
    coverage_report = None
    for cov_file in results_dir.rglob("coverage.cobertura.xml"):
        coverage_report = cov_file
        break

    summary = {
        "exit_code": result["exit_code"],
        "duration_s": result["duration_s"],
        "coverage_collected": coverage_report is not None,
    }

    if coverage_report:
        coverage_json = await parse_coverage_report(str(coverage_report))
        coverage_data = json.loads(coverage_json)
        summary["coverage"] = coverage_data
        summary["coverage_file"] = str(coverage_report)
    else:
        summary["note"] = "No coverage report found. Ensure coverlet.collector is installed."

    return json.dumps(summary, ensure_ascii=False)


# -------------------------
# Project Structure Tool (moved from server.py for completeness)
# -------------------------

@mcp.tool()
async def map_dotnet_structure(root: str, max_files: int = 2000) -> str:
    """
    Map the structure of a .NET project directory, focusing on relevant files.

    Args:
        root: Root directory to scan
        max_files: Maximum number of files to return

    Returns:
        JSON with categorized file lists (source, tests, config, etc.)
    """
    root_path = Path(root).resolve()
    exclude = {".git", ".vs", "bin", "obj", "node_modules", "TestResults", "TestResults_MCP", "packages"}

    categories = {
        "solutions": [],
        "projects": [],
        "source_files": [],
        "test_files": [],
        "config_files": [],
        "migrations": [],
        "other": [],
    }

    count = 0
    for p in root_path.rglob("*"):
        if count >= max_files:
            break
        if p.is_dir():
            continue
        if any(part in exclude for part in p.parts):
            continue

        rel = str(p.relative_to(root_path)).replace("\\", "/")
        ext = p.suffix.lower()
        name = p.name.lower()

        count += 1

        if ext == ".sln":
            categories["solutions"].append(rel)
        elif ext == ".csproj":
            categories["projects"].append(rel)
        elif ext == ".cs":
            if "test" in rel.lower() or "spec" in rel.lower():
                categories["test_files"].append(rel)
            elif "migrations" in rel.lower():
                categories["migrations"].append(rel)
            else:
                categories["source_files"].append(rel)
        elif name in ["appsettings.json", "appsettings.development.json", "launchsettings.json", "web.config", "app.config", "nuget.config", ".editorconfig"]:
            categories["config_files"].append(rel)
        elif ext in [".json", ".xml", ".config", ".yaml", ".yml"] and "test" not in rel.lower():
            categories["config_files"].append(rel)

    # Sort all lists
    for key in categories:
        categories[key].sort()

    return json.dumps({
        "root": str(root_path),
        "total_files": count,
        **{k: {"count": len(v), "files": v} for k, v in categories.items()},
    }, ensure_ascii=False)


# -------------------------
# Entry point
# -------------------------

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
