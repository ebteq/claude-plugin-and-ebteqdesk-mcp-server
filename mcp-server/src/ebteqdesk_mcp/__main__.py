"""Console entry point: `ebteqdesk-mcp`, and `python -m ebteqdesk_mcp`.

🔴 NOTHING IN THIS PROCESS MAY WRITE TO STDOUT except the MCP protocol itself.
stdout IS the transport — a stray `print()`, a banner, a warning routed to
stdout, or a debugger's output is injected straight into the JSON-RPC stream and
the client drops the connection with a parse error that names no cause. Anything
diagnostic goes to stderr, which the host captures as the server's log.

For the same reason there are no CLI flags. Configuration is environment-only
(see config.py): a flag would invite `ebteqdesk-mcp --token ...`, which puts the
credential in the process table where every other user on the machine can read
it with `ps`.
"""

from __future__ import annotations

import sys

from ._version import __version__
from .server import run


USAGE = """\
ebteqdesk-mcp — an MCP server for Ebteqdesk, spoken over stdio.

It takes no arguments beyond `--help` and `--version`. An MCP client (Claude
Code, etc.) launches it as a subprocess and talks JSON-RPC to its stdin/stdout;
running it by hand just makes it wait for that traffic.

Configuration is environment-only:

  EBTEQDESK_API_TOKEN   required  an Ebteqdesk personal access token
  EBTEQDESK_BASE_URL    required  the site root, e.g. https://help.example.com
  EBTEQDESK_TIMEOUT     optional  per-request timeout in seconds (default 30)

🔴 The pre-rename WARNIDESK_* names are NOT read. They were accepted through
1.x and removed in 2.0.0; a server still passed them starts and then refuses
every call as unconfigured. See config.py.

Register it with Claude Code (the server KEY is `ebteqdesk` as of 2.0.0 — it is
what prefixes the tool names and what the `ebteqdesk` Claude Code plugin drives.
An install registered before the rename keeps working under the `warnidesk` key,
because the key is the host's to choose; the plugin's skills accept either
prefix for one release):

  claude mcp add ebteqdesk \\
    --env EBTEQDESK_BASE_URL=https://help.example.com \\
    --env EBTEQDESK_API_TOKEN='6|your-token' \\
    -- ebteqdesk-mcp
"""


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    # `--version` is the ONE flag, and it exists because the upgrade path needs
    # it: 1.x installed a `warnidesk-mcp` console script, 2.0.0 installs only
    # `ebteqdesk-mcp`, and an interpreter that has seen both can still have the
    # stale script sitting in its bin/. "Run it and read the version back" is
    # how you tell which package is actually on PATH, and the README's upgrade
    # section tells people to run exactly this.
    #
    # It prints to STDERR, like the usage text below and for the same reason
    # (see the module docstring). A version string on stdout would be more
    # pipe-friendly, but the rule here is that this process never writes to
    # stdout at all — one unconditional rule is worth more than a rule with a
    # convenient exception, because the exception is what the next `print()`
    # will point at.
    if argv and argv[0] in ("-V", "--version"):
        print(f"ebteqdesk-mcp {__version__}", file=sys.stderr)
        return 0

    # There are no other options, but a user WILL type `--help`, and without
    # this the process would start the server and sit there blocking on a TTY
    # that is never going to send it JSON-RPC — indistinguishable from a hang.
    # Usage goes to stderr, never stdout, for the reason in the module docstring.
    if argv:
        print(USAGE, file=sys.stderr)
        return 0 if argv[0] in ("-h", "--help") else 2

    try:
        run()
    except KeyboardInterrupt:
        # Ctrl-C and a host tearing down the subprocess arrive the same way.
        # Neither is an error worth a traceback.
        return 0
    except Exception as exc:  # pragma: no cover - process-level guard
        # stderr, never stdout. And the exception TYPE plus message only: a
        # traceback here would be the last thing in the host's log, so keeping
        # it to one line is what makes it readable.
        print(f"ebteqdesk-mcp failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
