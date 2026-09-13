"""The `mcp` dependency must exclude 2.x, and the environment must match it.

mcp 2.x renamed `FastMCP` to `MCPServer` and ships `mcp/server/fastmcp.py` as a
tombstone whose import raises `ModuleNotFoundError` with a migration pointer.
Every server in this package does `from mcp.server.fastmcp import FastMCP`, so
an unpinned dependency resolves 2.x on a fresh install and all seven fail to
start — while a machine whose environment predates 2.x keeps working, which is
why this went unnoticed: the defect is invisible exactly where the code runs.

Found from outside, by the claude-code-toolkit session hitting it on a fresh
venv while moving this package's template-sync server into their repo.

The version guard is the point. A future "let's take the latest SDK" edit is a
good intention, and this is the bound that catches it — the same shape as the
0.3.x bound on MIN_SERVER_FOR_V3.
"""

import pathlib
import re
import tomllib

import pytest

PYPROJECT = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"


def _mcp_requirement() -> str:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    reqs = [r for r in data["project"]["dependencies"] if r.split("[")[0].strip() == "mcp"]
    assert len(reqs) == 1, f"expected exactly one mcp requirement, got {reqs}"
    return reqs[0]


def test_mcp_dependency_excludes_2x():
    req = _mcp_requirement()
    assert "<2" in req.replace(" ", ""), (
        f"mcp is declared as {req!r} with no upper bound below 2. A fresh install "
        "would resolve mcp 2.x, where mcp.server.fastmcp is a tombstone that raises "
        "on import, and every server in this package would fail to start."
    )


def test_installed_mcp_matches_the_declared_bound():
    """The declaration and the environment must agree.

    Asserted separately from the declaration because this repo's own venv is
    how the defect stayed hidden: an environment installed before 2.x existed
    satisfies an unpinned requirement and proves nothing about a fresh one.
    """
    from importlib.metadata import version

    major = int(version("mcp").split(".")[0])
    assert major < 2, (
        f"installed mcp is {version('mcp')}; this package requires 1.x until every "
        "server is ported from FastMCP to MCPServer."
    )


def test_the_import_every_server_depends_on_still_works():
    """The behavioural arm: the two assertions above are about metadata."""
    from mcp.server.fastmcp import FastMCP

    assert callable(FastMCP)


@pytest.mark.parametrize("spec", ["mcp[cli]", "mcp", "mcp>=1.2"])
def test_the_guard_would_reject_an_unpinned_spec(spec):
    """Control: the assertion must fail for the specs it exists to reject —
    otherwise it is inert and passes for every input."""
    assert "<2" not in spec.replace(" ", "")
