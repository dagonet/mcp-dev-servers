# Design: rust-tools MCP Server

## Summary

New MCP server providing reliable Cargo command execution on Windows, following the same patterns as the existing git-tools, github-tools, dotnet-tools, and ollama-tools servers.

## Problem

Claude Code's sandbox bash on Windows has unreliable PATH resolution and missing coreutils. Agents running `cargo build`, `cargo test`, or `cargo clippy` via Bash frequently hit "command not found" errors. The existing MCP servers (git-tools, dotnet-tools) solve this for their respective domains by running commands as native subprocesses with the user's real environment. Rust/Tauri projects lack this.

## Value Proposition

The server's primary value is **reliable execution on Windows**, not output parsing. Cargo already has excellent compiler diagnostics and native `--message-format=json` support. The MCP server adds:

- Correct PATH resolution (`cargo.exe` via `shutil.which`)
- `CREATE_NO_WINDOW` subprocess flag (no console popups on Windows)
- Process tree killing on timeout
- Output truncation to prevent context overflow (200K char limit)
- Consistent JSON response format matching other servers

## Design Decisions

- **Shell-tools rejected**: A generic shell utility server was considered and rejected. Domain-specific MCP servers solve the root cause (PATH resolution). Remaining gaps (file delete/copy) are rare and handled via `python -c "..."` workarounds.
- **Separate tauri-tools rejected**: Tauri is a Cargo subcommand. Separating it adds registration friction for no benefit. Tauri-specific tools are included in rust-tools; they return a clear error if `cargo-tauri` is not installed.
- **YAGNI applied**: Starting with 4 essential tools. `cargo_fmt_check` (trivial single command) and `tauri_build` (too long-running for MCP request/response) were cut. Both can be run via Bash using the absolute cargo path from `cargo_env_info`.
- **`cwd` not `manifest_path`**: Follows the existing convention in dotnet-tools and git-tools where tools accept a working directory, not tool-specific path flags.
- **Light parsing**: Use `--message-format=json` where Cargo offers it natively (build, clippy). Don't build custom parsers for unstable formats (test `--format=json` is nightly-only).

## Tools (4)

### `cargo_env_info`

Diagnostic tool (mirrors `git_env_info`).

- **Parameters**: none
- **Returns**: cargo version, rustc version, default toolchain, installed targets, `cargo-tauri` CLI presence and version
- **Implementation**: Runs `cargo --version`, `rustc --version`, `rustup show`, `cargo tauri --version`

### `cargo_build`

Build a Rust project with structured error/warning output.

- **Parameters**: `cwd: str`, `release: bool = False`, `target: str = ""`, `features: str = ""`
- **Command**: `cargo build --message-format=json [--release] [--target X] [--features X] [--manifest-path cwd/Cargo.toml]`
- **Returns**: JSON with exit_code, error/warning count, parsed diagnostics (level, message, file, line, column), stderr, duration_s, truncated flag
- **Timeout**: 300s

### `cargo_test`

Run tests and return results.

- **Parameters**: `cwd: str`, `test_name: str = ""`, `release: bool = False`
- **Command**: `cargo test [test_name] [--release] [--manifest-path cwd/Cargo.toml]`
- **Returns**: JSON with exit_code, raw stdout (Claude parses the human-readable output), stderr, duration_s, truncated flag
- **Timeout**: 300s
- **Note**: No custom output parsing. Stable Rust lacks a machine-readable test format. Claude reads "test result: ok. 3 passed; 0 failed" natively.

### `cargo_clippy`

Run clippy linter with structured diagnostics.

- **Parameters**: `cwd: str`, `fix: bool = False`
- **Command**: `cargo clippy --message-format=json [--fix --allow-dirty] -- -D warnings`
- **Returns**: Same structured diagnostics as `cargo_build`
- **Timeout**: 300s

## File Structure

Single file: `src/rust_mcp.py`

Follows the established patterns:
- `FastMCP("rust-tools")`
- `_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0`
- `_kill_process_tree()` for timeout cleanup
- `_cargo_exe()` for executable resolution (mirrors `_git_exe()`)
- Helper `run_cargo(cmd, cwd, timeout)` returning `{exit_code, stdout, stderr, duration_s, timed_out}`
- All tools async, all return `json.dumps(..., ensure_ascii=False)`
- Entry point: `mcp.run(transport="stdio")`

No new dependencies beyond `mcp[cli]` (already in requirements.txt).

## Registration

```bash
claude mcp add --scope user --transport stdio rust-tools \
  -- "/path/to/.venv/Scripts/python" "/path/to/src/rust_mcp.py"
```

User scope. Harmless in non-Rust projects (tools just won't be invoked).

Permission grant in `settings.json`:
```json
{ "permissions": { "allow": ["mcp__rust-tools__*"] } }
```

## Future Expansion (not in v1)

- `cargo_fmt_check` — if agents frequently need it beyond Bash
- `tauri_build` — if long-running MCP support improves or a background pattern emerges
- `cargo_deps` — dependency tree / outdated if `cargo tree` output proves hard for Claude to parse
- `tauri_dev` — if MCP gains long-running process support
