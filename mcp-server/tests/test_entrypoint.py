"""The console entry point's argument handling and its stdout discipline."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ebteqdesk_mcp.__main__ import USAGE, main
from ebteqdesk_mcp._version import __version__


def test_help_exits_zero_and_writes_usage_to_stderr(capsys) -> None:
    """Without this, `ebteqdesk-mcp --help` starts the server and blocks on a
    TTY forever — indistinguishable from a hang."""
    assert main(["--help"]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""  # stdout is the transport; nothing may touch it
    assert "takes no arguments" in captured.err
    assert "EBTEQDESK_API_TOKEN" in captured.err


def test_an_unknown_argument_exits_non_zero(capsys) -> None:
    assert main(["--token", "6|oops"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "environment-only" in captured.err


def test_usage_does_not_suggest_passing_the_token_as_a_flag() -> None:
    """A `--token` flag would put the credential in the process table, readable
    by every other user on the machine via `ps`."""
    assert "--token" not in USAGE
    assert "--env EBTEQDESK_API_TOKEN" in USAGE


def test_help_via_the_real_subprocess_writes_nothing_to_stdout() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "ebteqdesk_mcp", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0
    assert completed.stdout == ""
    assert "ebteqdesk-mcp" in completed.stderr


def test_version_exits_zero_and_writes_the_version_to_stderr(capsys) -> None:
    """The README's upgrade steps end with `ebteqdesk-mcp --version`.

    "Which package am I actually running?" is the question an operator mid-
    upgrade genuinely has — 2.0.0 removed the `warnidesk-mcp` alias, so a stale
    install and a current one are two different programs — and this is the
    answer."""
    assert main(["--version"]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""  # stdout is the transport, flags included
    assert captured.err.strip() == f"ebteqdesk-mcp {__version__}"


def test_version_short_flag_behaves_the_same(capsys) -> None:
    assert main(["-V"]) == 0
    assert capsys.readouterr().err.strip() == f"ebteqdesk-mcp {__version__}"


def test_the_removed_alias_script_is_not_installed() -> None:
    """`warnidesk-mcp` was a [project.scripts] alias through 1.x. It is gone.

    🔴 THIS IS THE TEST FOR A DELETION, so it is worth saying what it buys.
    Re-adding the alias is a one-line change to `[project.scripts]` and looks
    harmless in review; what it actually does is make the old name work again
    on a fresh install, which quietly re-opens the migration window the 2.0.0
    upgrade notes told everybody had closed. Failing here is the only thing
    that catches that.

    🔴 RESOLVED AGAINST THIS INTERPRETER'S OWN bin/, NEVER THE AMBIENT PATH.
    `shutil.which()` searches $PATH, and any developer machine that ever had
    the 1.x package installed globally still has a `warnidesk-mcp` on it. That
    is somebody else's install, not this tree, and asserting on it would fail
    this test for a reason it is not about.

    `Path(sys.executable).parent` is the bin/ (or Scripts/) directory of the
    interpreter running the tests, which is exactly where `pip install -e .`
    put this checkout's console scripts.
    """
    bindir = Path(sys.executable).parent
    alias = bindir / ("warnidesk-mcp.exe" if os.name == "nt" else "warnidesk-mcp")

    assert not alias.exists(), (
        f"{alias} exists. The pre-rename console script was removed in 2.0.0 and "
        "must not come back — check [project.scripts] in pyproject.toml. (If this "
        "is a stale script left behind by an older editable install in the same "
        "interpreter, reinstall with `pip install -e .` to clear it.)"
    )
