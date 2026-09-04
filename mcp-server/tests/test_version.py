"""One version string, quoted in four places, asserted to be one string.

Before `_version.py` these were four hardcoded literals — the manifest, the
package attribute, `MCPServer(version=…)` and the outgoing `User-Agent` — and
the last had already drifted. A drifted `serverInfo.version` is not cosmetic:
it is what a host uses to tell a renamed tool argument from an outage, and a
drifted User-Agent turns an operator's "which client version is hammering
/api/v1?" from no answer into a wrong one.
"""

from __future__ import annotations

import importlib.metadata

import httpx2
import pytest

import ebteqdesk_mcp
from ebteqdesk_mcp import server as srv
from ebteqdesk_mcp._version import __version__
from ebteqdesk_mcp.client import EbteqdeskClient
from ebteqdesk_mcp.config import Config


def test_the_package_attribute_is_the_single_source() -> None:
    assert ebteqdesk_mcp.__version__ == __version__


def test_the_installed_distribution_reports_the_same_version() -> None:
    """`[tool.hatch.version]` reads `_version.py`, so a bump in one place is a
    bump everywhere. If this fails after an edit, the wheel was built before the
    edit — reinstall — but if it fails on a clean install, the manifest has gone
    back to a hardcoded `version = "..."`."""
    assert importlib.metadata.version("ebteqdesk-mcp") == __version__


def test_the_wire_version_a_client_reads_is_the_same_version() -> None:
    """`serverInfo.version` in `initialize`."""
    assert srv.mcp.version == __version__


async def test_the_user_agent_carries_the_same_version() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"data": {}})

    client = EbteqdeskClient(
        Config(base_url="https://ebteqdesk.test", token="6|t", timeout=5.0),
        transport=httpx2.MockTransport(handler),
    )

    async with client:
        await client.whoami()

    assert seen[0].headers["user-agent"] == f"ebteqdesk-mcp/{__version__}"


@pytest.mark.parametrize("part", __version__.split("."))
def test_the_version_is_three_numeric_parts(part: str) -> None:
    """Semver, because the version means something here: the major moves when a
    tool's observable contract does. `close_ticket`'s default status changing
    from 4 to 5 is exactly such a move and is why this is 1.0.0 and not 0.2.0.
    """
    assert part.isdigit(), f"{__version__} is not a plain semver triple"


def test_the_version_has_three_parts() -> None:
    assert len(__version__.split(".")) == 3
