"""`get_reports_summary` — the account-wide ticket report.

Everything worth pinning about this tool is a way of being confidently wrong
rather than a way of failing:

  - THE BOUNDS ARE INCLUSIVE INSTANTS AND ARE NOT WIDENED. A bare
    `date_to="2026-08-31"` means midnight and excludes that whole day. The
    sibling `get_escalation_report` DOES widen its bounds, so the habit carried
    from one to the other under-reports a month by a day, silently.

  - NULL MEANS NO DATA, NOT ZERO. A null `reopenedPercent` reported as 0% tells
    a user their reopen rate is perfect when it is unmeasured.

  - UNITS. `times.*` are minutes, `*Percent` are 0..100 not 0..1. Both are
    guessable and both guesses are wrong in a way a payload cannot signal.

  - TWO LIVE GATES. `reports:read` resolving is not enough; the endpoint also
    demands `admin.access`, so a Supervisor's perfectly valid key is refused
    with an ABILITY error whose remedy is an administrator and never a new key.
    That is the one place on this API where the controller's own ability check
    is genuinely reachable.
"""

from __future__ import annotations

import httpx2
import pytest

from conftest import ability_refusal, always_json, scope_refusal
from mcp.server.mcpserver.exceptions import ToolError

from ebteqdesk_mcp import server as srv
from ebteqdesk_mcp.client import EbteqdeskClient
from ebteqdesk_mcp.config import Config
from ebteqdesk_mcp.errors import InvalidRequestError


SUMMARY = {
    "data": {
        "range": {
            "from": "2026-08-01T00:00:00+00:00",
            "to": "2026-08-31T23:59:59+00:00",
        },
        "volume": {"tickets": 128, "unanswered": 4, "open": 22, "solved": 96},
        "times": {"firstReplyMinutes": 37.5, "resolutionMinutes": 612.0},
        "quality": {
            "oneTouchResolutionPercent": 62.5,
            "reopenedPercent": None,
            "averageRating": 4.25,
        },
    },
    "meta": {
        "filters": {"from": "2026-08-01", "to": None},
        "generatedAt": "2026-08-13T14:22:07+00:00",
    },
}


@pytest.fixture
def wired(monkeypatch):
    def install(handler):
        config = Config(base_url="https://ebteqdesk.test", token="6|t", timeout=5.0)
        client = EbteqdeskClient(config, transport=httpx2.MockTransport(handler))
        monkeypatch.setattr(srv, "_client", client)
        return client

    yield install

    monkeypatch.setattr(srv, "_client", None)


@pytest.fixture
async def tools() -> dict[str, object]:
    return {tool.name: tool for tool in await srv.mcp.list_tools()}


def described(tool) -> str:
    return " ".join((tool.description or "").split())


# --------------------------------------------------------------------------- #
# The client
# --------------------------------------------------------------------------- #


async def test_it_requests_the_summary_route(make_client) -> None:
    client, recorder = make_client(always_json(200, SUMMARY))

    await client.get_reports_summary()

    assert recorder.last.url.path == "/api/v1/reports/summary"
    # No bounds sent means the server's own default — the current calendar
    # month — rather than this client inventing one.
    assert recorder.last.url.query == b""


async def test_the_bounds_go_out_as_from_and_to(make_client) -> None:
    """The API's own parameter names, not the Python argument names. A request
    vocabulary that differs from the documented one costs a reader the ability
    to compare a tool call against a curl."""
    client, recorder = make_client(always_json(200, SUMMARY))

    await client.get_reports_summary(
        date_from="2026-08-01", date_to="2026-08-31T23:59:59"
    )

    query = recorder.last.url.query.decode()
    assert "from=2026-08-01" in query
    assert "to=2026-08-31T23%3A59%3A59" in query or "to=2026-08-31T23:59:59" in query


async def test_the_payload_passes_through_verbatim_nulls_included(make_client) -> None:
    """🔴 A null must arrive as a null. Anything that helpfully defaulted it to
    0 would turn "unmeasured" into "perfect"."""
    client, _ = make_client(always_json(200, SUMMARY))

    body = await client.get_reports_summary()

    assert body == SUMMARY
    assert body["data"]["quality"]["reopenedPercent"] is None
    assert body["data"]["volume"]["tickets"] == 128


async def test_a_reversed_range_is_a_422_from_the_server(make_client) -> None:
    """Passed through unvalidated on purpose: a local rule would be a second,
    differently-worded one and would make this client disagree with curl."""
    client, _ = make_client(
        always_json(
            422,
            {
                "error": "The given data was invalid.",
                "errors": {"to": ["The to must be a date after or equal to from."]},
            },
        )
    )

    with pytest.raises(InvalidRequestError) as excinfo:
        await client.get_reports_summary(date_from="2026-08-31", date_to="2026-08-01")

    assert "to" in excinfo.value.field_errors


# --------------------------------------------------------------------------- #
# Through the MCP layer
# --------------------------------------------------------------------------- #


async def test_it_round_trips_through_mcp(wired) -> None:
    wired(always_json(200, SUMMARY))

    result = await srv.mcp.call_tool("get_reports_summary", {})

    assert not result.is_error
    assert result.structured_content["data"]["volume"]["tickets"] == 128


async def test_a_key_without_reports_read_is_refused(wired) -> None:
    wired(scope_refusal("reports:read", requested=[], scopes=[]))

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool("get_reports_summary", {})

    text = str(excinfo.value)
    assert "reports:read" in text
    assert "mint a NEW key" in text


async def test_a_supervisor_without_admin_access_gets_an_ability_refusal(
    wired,
) -> None:
    """🔴 THE REACHABLE SECOND GATE. Unlike its two neighbours, this endpoint's
    own ability check is not shadowed by the scope: `reports:read` is backed by
    `reports.view` alone, and the controller additionally wants `admin.access`.
    So a key whose scope resolves perfectly is still refused — and the remedy is
    an administrator, NEVER a new key."""
    wired(ability_refusal("admin.access"))

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool("get_reports_summary", {})

    text = str(excinfo.value)
    assert "admin.access" in text
    assert "administrator" in text.lower()
    assert "mint a NEW key" not in text
    assert "Minting a new key will not help" in text


# --------------------------------------------------------------------------- #
# The rules only a description can carry
# --------------------------------------------------------------------------- #


async def test_it_states_that_bounds_are_inclusive_instants(tools) -> None:
    description = described(tools["get_reports_summary"])

    assert "BOTH BOUNDS ARE INCLUSIVE INSTANTS AND NEITHER IS WIDENED" in description
    assert "EXCLUDES that entire day's tickets" in description
    assert "2026-08-31T23:59:59" in description


async def test_it_warns_that_the_sibling_report_widens_and_this_one_does_not(
    tools,
) -> None:
    """Two report tools, two conventions. The habit carried from one to the
    other under-reports by a day and nothing signals it."""
    description = described(tools["get_reports_summary"])

    assert "`get_escalation_report` DOES widen" in description
    assert "Do not carry the habit from one to the other" in description


async def test_it_states_the_current_month_default(tools) -> None:
    description = described(tools["get_reports_summary"])

    assert "OMITTING BOTH MEANS THE CURRENT CALENDAR MONTH, not all time" in description
    assert "you must say what range you actually measured" in description


async def test_it_states_every_unit(tools) -> None:
    description = described(tools["get_reports_summary"])

    assert "MINUTES, not hours and not seconds" in description
    assert "0..100, NOT 0..1" in description
    assert "1 to 5 stars" in description


async def test_it_states_that_null_is_not_zero(tools) -> None:
    description = described(tools["get_reports_summary"])

    assert "NULL MEANS NO DATA" in description
    assert "not zero" in description
    assert 'Say "no data" for a null' in description


async def test_it_names_both_gates_and_the_right_remedy(tools) -> None:
    description = described(tools["get_reports_summary"])

    assert "`reports:read`" in description
    assert "`admin.access`" in description
    assert "an administrator, NEVER a new key" in description


async def test_it_distinguishes_itself_from_the_escalation_report(tools) -> None:
    """Two report tools with similar names. A model picking the wrong one gets
    plausible numbers that answer a different question."""
    description = described(tools["get_reports_summary"])

    assert "not this account's own tickets" in description
    assert "not per-category" in description


async def test_it_exposes_exactly_its_documented_arguments(tools) -> None:
    schema = tools["get_reports_summary"].input_schema

    assert set(schema.get("properties", {})) == {"date_from", "date_to"}
    assert set(schema.get("required", [])) == set()
