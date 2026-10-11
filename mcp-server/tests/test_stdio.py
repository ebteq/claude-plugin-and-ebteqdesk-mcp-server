"""Drives the REAL `ebteqdesk-mcp` subprocess over stdio.

Everything else in this suite calls Python objects in-process. That proves the
tools are correct and proves nothing at all about whether the thing an MCP host
launches actually speaks the protocol — a stray `print()`, a broken entry point
or a banner on stdout would pass every other test in this directory and fail on
first contact with a client.

So this module spawns the installed console script as a subprocess, completes
the MCP handshake over its stdin/stdout, and calls a tool. No Ebteqdesk is
needed: the base URL points at a closed port, and the assertion is that the
failure comes back as a well-formed `isError` result rather than as a crash or a
protocol parse error.
"""

from __future__ import annotations

import shutil
import sys

import pytest
from mcp import ClientSession, StdioServerParameters, stdio_client

pytestmark = pytest.mark.skipif(
    shutil.which("ebteqdesk-mcp") is None,
    reason="ebteqdesk-mcp is not on PATH; run `pip install -e .` first",
)

#: The full roster, asserted over the wire. It duplicates `test_server_tools`'s
#: list on purpose: that one proves what the Python object registered, this one
#: proves what a real client is actually served. A tool that registers but does
#: not survive the subprocess would pass there and fail here.
EXPECTED_TOOLS = {
    # Reads
    "whoami",
    "list_tickets",
    "list_tickets_by_category",
    "list_escalations",
    "get_ticket",
    "get_ticket_comments",
    "get_ticket_attachment",
    "get_escalation_report",
    "get_reports_summary",
    "search_kb_articles",
    "get_kb_article",
    "get_kb_article_review",
    # A READ, over the wire as well as in-process. It is the only tool here
    # that ENUMERATES drafts, so a roster that quietly lost it would leave an
    # integration back where it started — able to read a verdict only for an
    # article whose `reference` it still held.
    "list_kb_proposals",
    "list_kb_tree",
    "list_kb_categories",
    "list_kb_folders",
    # Writes
    "create_ticket",
    "comment_on_ticket",
    "add_private_note",
    "escalate_ticket",
    "de_escalate_ticket",
    # One of the two ticket writes that email nobody. Over the wire as well
    # as in-process: it and `close_ticket` split the status vocabulary between
    # them, and a client served only one of the pair would be able to resolve a
    # ticket and never reopen it.
    "set_ticket_status",
    # The other quiet ticket write (4.4.0): sets or clears the ticket TYPE by
    # slug, and leaves no history entry at all.
    "set_ticket_category",
    "close_ticket",
    "propose_kb_article",
    "update_kb_article",
    "create_kb_category",
    "update_kb_category",
    # The only two tools on this server that destroy anything. Over the wire as
    # well as in-process: a delete that registered but was not served would be
    # invisible to `test_server_tools` and visible here.
    "delete_kb_category",
    "create_kb_folder",
    "update_kb_folder",
    "delete_kb_folder",
    "reorder_kb_children",
    # The only tool that reads the user's own filesystem. Over the wire as well
    # as in-process: a host that was served this tool is a host that can be
    # asked to send a local file somewhere, so its presence is a fact worth
    # asserting on the bytes rather than on the Python object.
    "upload_kb_media",
    # Agent provisioning — reads
    #
    # 🔴 THE FIVE READS AND THE FOUR WRITES BELOW ARE THE ONLY TOOLS ON THIS
    # SERVER THAT ACT ON THE DESK'S ACCOUNTS RATHER THAN ON ITS CONTENT, and
    # that is exactly why they are asserted on the BYTES and not only on the
    # Python object. A host that is served `issue_api_key` is a host that can be
    # asked to hand somebody a working credential for a live helpdesk; a host
    # that is served `create_agent` can be asked to create a person's access to
    # one. Which tools actually reach a client is the fact that matters there,
    # and it is the fact `test_server_tools` cannot see.
    "list_agents",
    "get_agent",
    "list_roles",
    "list_groups",
    "list_api_keys",
    # Agent provisioning — writes
    #
    # ⚠️ AND THE SET IS ASSERTED WHOLE, so a tenth provisioning tool arriving
    # over the wire fails here. `delete_agent`, `reset_agent_password` and
    # `change_agent_email` are deliberately absent from this API; any of them
    # appearing on the wire is the failure this roster exists to catch.
    "create_agent",
    "update_agent",
    "issue_api_key",
    "revoke_api_key",
}

# Port 1 is reserved and nothing listens on it, so a connection attempt fails
# immediately rather than hanging for the duration of the timeout.
UNREACHABLE = "http://127.0.0.1:1"


def _params(**env: str) -> StdioServerParameters:
    return StdioServerParameters(
        command="ebteqdesk-mcp",
        args=[],
        # A minimal environment on purpose. Inheriting the developer's shell
        # would let a real EBTEQDESK_API_TOKEN leak into a test that then makes
        # live requests from the unit suite.
        env={"PATH": _path(), "EBTEQDESK_TIMEOUT": "5", **env},
    )


def _path() -> str:
    """The subprocess PATH, with THIS interpreter's scripts directory first.

    🔴 Without that, `ebteqdesk-mcp` resolves to whatever is first on the
    developer's PATH — typically a `uv tool`/`pipx` install of an OLDER release
    — and this whole module silently tests that binary instead of the code
    under test. Found during 5.0.0: run without the venv activated, these tests
    were driving an installed 4.3.0 and passing.
    """
    import os

    scripts = os.path.dirname(sys.executable)

    return os.pathsep.join([scripts, os.environ.get("PATH", "")])


async def test_the_subprocess_completes_the_handshake_and_lists_every_tool() -> None:
    async with stdio_client(_params(EBTEQDESK_BASE_URL=UNREACHABLE)) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()

            assert init.server_info.name == "ebteqdesk"

            tools = await session.list_tools()

            assert {tool.name for tool in tools.tools} == EXPECTED_TOOLS


async def test_the_handshake_advertises_tools_and_nothing_else() -> None:
    """🔴 #133, proven on the wire rather than against the SDK's own accessor.

    `MCPServer` registers `prompts/*` and `resources/*` handlers whether or not
    anything is behind them, and the capability block is derived from which
    handlers exist — so the obvious tools-only server tells every client to go
    and fetch two empty lists. The in-process tests assert the withdrawal;
    this asserts the bytes a real client reads out of `initialize`.
    """
    async with stdio_client(_params(EBTEQDESK_BASE_URL=UNREACHABLE)) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()

    assert init.capabilities.tools is not None
    assert init.capabilities.prompts is None
    assert init.capabilities.resources is None


async def test_the_tool_annotations_reach_a_real_client(  # 5.0.0
) -> None:
    """In-process tests prove what was registered; this proves what a host
    actually reads out of `tools/list` — the hints it would gate approval on."""
    async with stdio_client(_params(EBTEQDESK_BASE_URL=UNREACHABLE)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}

    assert all(tool.annotations is not None for tool in tools.values())
    assert tools["get_ticket"].annotations.read_only_hint is True
    assert tools["comment_on_ticket"].annotations.read_only_hint is False
    assert tools["comment_on_ticket"].annotations.destructive_hint is True
    assert tools["comment_on_ticket"].annotations.open_world_hint is True


async def test_a_remote_plain_http_base_url_is_refused_over_the_wire() -> None:
    """5.0.0: the token is never sent in the clear to a host that is not this
    machine. The server still starts and lists tools — the refusal arrives on
    the first call, naming the fix, like every other configuration error."""
    async with stdio_client(
        _params(
            EBTEQDESK_BASE_URL="http://help.example.com",
            EBTEQDESK_API_TOKEN="6|not-a-real-token",
        )
    ) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool("whoami", {})

    text = _text(result)
    assert result.is_error
    assert "plain http://" in text
    assert "EBTEQDESK_ALLOW_INSECURE_HTTP=1" in text
    assert "not-a-real-token" not in text


async def test_the_insecure_http_warning_never_touches_stdout() -> None:
    """🔴 The override prints a warning, and stdout is the transport. If it
    went to stdout the session below would fail to parse the next message;
    instead the call completes with an ordinary transport error.

    192.0.2.1 is TEST-NET-1 (RFC 5737): routable nowhere, so the connection
    fails within the short timeout rather than reaching anything.
    """
    async with stdio_client(
        _params(
            EBTEQDESK_BASE_URL="http://192.0.2.1:1",
            EBTEQDESK_API_TOKEN="6|not-a-real-token",
            EBTEQDESK_ALLOW_INSECURE_HTTP="1",
            EBTEQDESK_TIMEOUT="0.5",
        )
    ) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool("whoami", {})

            # Still in sync after the warning was printed.
            assert await session.send_ping() is not None

    assert result.is_error
    assert "plain http://" not in _text(result)


async def test_the_server_starts_and_lists_tools_with_no_token_configured() -> None:
    """A server that validated configuration at startup would exit before the
    handshake, and the host would report only 'failed to connect'."""
    async with stdio_client(_params(EBTEQDESK_BASE_URL=UNREACHABLE)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            assert len((await session.list_tools()).tools) == len(EXPECTED_TOOLS)


async def test_a_missing_token_comes_back_as_an_is_error_result_over_the_wire() -> None:
    async with stdio_client(_params(EBTEQDESK_BASE_URL=UNREACHABLE)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool("whoami", {})

    assert result.is_error
    assert "EBTEQDESK_API_TOKEN is not set" in _text(result)


async def test_an_unreachable_host_comes_back_as_an_is_error_result() -> None:
    async with stdio_client(
        _params(EBTEQDESK_BASE_URL=UNREACHABLE, EBTEQDESK_API_TOKEN="6|not-a-real-token")
    ) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool("whoami", {})

    text = _text(result)
    assert result.is_error
    assert "Could not reach Ebteqdesk" in text
    # And the credential is not echoed back through the transport.
    assert "not-a-real-token" not in text


async def test_stdout_carries_only_protocol_traffic() -> None:
    """If anything printed to stdout, the handshake below would fail to parse.

    This is the assertion that a `print()` added during debugging cannot survive
    review — it is not a style rule, it is the transport.
    """
    async with stdio_client(_params(EBTEQDESK_BASE_URL=UNREACHABLE)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # A round trip after the handshake: any junk injected into stdout
            # during startup would desynchronise the stream by now.
            assert await session.send_ping() is not None


async def test_the_legacy_env_names_no_longer_configure_the_real_subprocess() -> None:
    """🔴 THE 2.0.0 BREAK, PROVEN ON THE TRANSPORT RATHER THAN IN A UNIT TEST.

    `test_config.py` asserts `WARNIDESK_*` is no longer read. This asserts the
    shape of the resulting failure, which is the part an operator actually
    meets: the server STARTS (a missing variable is not fatal until it is read),
    completes the handshake, and then refuses the first tool call as
    unconfigured. That is why the break is easy to misdiagnose as a broken
    server rather than a stale config, and why the error text below has to name
    the variable to set.
    """
    async with stdio_client(
        _params(WARNIDESK_BASE_URL=UNREACHABLE, WARNIDESK_API_TOKEN="6|legacy-token")
    ) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()

            assert init.server_info.name == "ebteqdesk"
            assert await session.send_ping() is not None

            result = await session.call_tool("whoami", {})

    text = _text(result)
    # The legacy names were ignored entirely, so this fails as UNCONFIGURED —
    # not as the unreachable host it would have reported while the fallback
    # existed.
    assert result.is_error
    assert "EBTEQDESK_BASE_URL" in text
    assert "is not set" in text
    assert "legacy-token" not in text


def _text(result) -> str:
    return "\n".join(
        block.text for block in result.content if getattr(block, "text", None)
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
