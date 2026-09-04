"""The six endpoints: URLs, parameters, headers, and payload passthrough."""

from __future__ import annotations

import httpx2
import pytest

from conftest import BASE_URL, TOKEN, always_json
from ebteqdesk_mcp.errors import TransportError

OK = always_json(200, {"data": {"ok": True}})


# --------------------------------------------------------------------------- #
# Request construction
# --------------------------------------------------------------------------- #


async def test_every_request_carries_the_bearer_token_and_asks_for_json(make_client) -> None:
    client, recorder = make_client(OK)

    async with client:
        await client.whoami()

    headers = recorder.last.headers
    assert headers["authorization"] == f"Bearer {TOKEN}"
    # Without an explicit Accept, Laravel's handler can render a Blade error PAGE
    # from a JSON endpoint — the trap the API controllers document at length.
    assert headers["accept"] == "application/json"
    assert headers["user-agent"].startswith("ebteqdesk-mcp/")


async def test_the_token_is_never_placed_in_the_url(make_client) -> None:
    client, recorder = make_client(OK)

    async with client:
        await client.whoami()
        await client.search_kb_articles(query="vpn")

    for request in recorder.requests:
        assert TOKEN not in str(request.url)


@pytest.mark.parametrize(
    "call, expected_path",
    [
        (lambda c: c.whoami(), "/api/v1/user"),
        (lambda c: c.list_tickets(), "/api/v1/tickets"),
        (lambda c: c.list_tickets_by_category("bp-task"), "/api/v1/bp-task"),
        (
            lambda c: c.get_escalation_report(),
            "/api/v1/reports/category-metrics",
        ),
        (lambda c: c.search_kb_articles(), "/api/v1/kb/articles"),
        (lambda c: c.get_kb_article("resetting-your-password"),
         "/api/v1/kb/articles/resetting-your-password"),
    ],
)
async def test_each_endpoint_hits_its_documented_path(make_client, call, expected_path) -> None:
    client, recorder = make_client(OK)

    async with client:
        await call(client)

    assert recorder.last.url.path == expected_path
    assert str(recorder.last.url).startswith(BASE_URL)


async def test_omitted_arguments_send_no_query_parameters(make_client) -> None:
    """An absent bound must not become `?from=`; the server treats them alike but
    a client that sends empty strings makes every log and cache key noisier."""
    client, recorder = make_client(OK)

    async with client:
        await client.get_escalation_report()
        assert recorder.last.url.query == b""

        await client.search_kb_articles()
        assert recorder.last.url.query == b""

        await client.list_tickets()
        assert recorder.last.url.query == b""


async def test_report_range_is_sent_as_from_and_to(make_client) -> None:
    client, recorder = make_client(OK)

    async with client:
        await client.get_escalation_report(date_from="2026-03-01", date_to="2026-03-31")

    params = dict(recorder.last.url.params)
    assert params == {"from": "2026-03-01", "to": "2026-03-31"}


async def test_kb_search_sends_q_per_page_and_page(make_client) -> None:
    client, recorder = make_client(OK)

    async with client:
        await client.search_kb_articles(query="  vpn  ", per_page=10, page=2)

    params = dict(recorder.last.url.params)
    assert params == {"q": "vpn", "per_page": "10", "page": "2"}


async def test_blank_kb_query_is_dropped_rather_than_sent_empty(make_client) -> None:
    """The server reads an empty `q` as 'no search'; sending it anyway would
    label a full-corpus listing as a search result."""
    client, recorder = make_client(OK)

    async with client:
        await client.search_kb_articles(query="   ")

    assert b"q=" not in recorder.last.url.query


async def test_out_of_range_per_page_is_passed_through_not_clamped(make_client) -> None:
    """Clamping locally would hide the caller's mistake and make this client
    disagree with curl — the server answers 422 on purpose."""
    client, recorder = make_client(always_json(200, {"data": []}))

    async with client:
        await client.search_kb_articles(per_page=999)

    assert dict(recorder.last.url.params)["per_page"] == "999"


async def test_pagination_parameter_is_sent_on_both_ticket_endpoints(make_client) -> None:
    client, recorder = make_client(OK)

    async with client:
        await client.list_tickets(page=3)
        assert dict(recorder.last.url.params) == {"page": "3"}

        await client.list_tickets_by_category("bp-task", page=4)
        assert dict(recorder.last.url.params) == {"page": "4"}


@pytest.mark.parametrize("bad", ["", "   ", "/", "kb/articles", "a/b"])
async def test_multi_segment_or_empty_category_is_refused_before_sending(
    make_client, bad: str
) -> None:
    """A slug with a slash would hit a DIFFERENT route and return a confusing
    success instead of an error about the argument."""
    client, recorder = make_client(OK)

    async with client:
        with pytest.raises(ValueError, match="single ticket-type slug"):
            await client.list_tickets_by_category(bad)

    assert recorder.requests == []


@pytest.mark.parametrize("bad", ["", "  ", "a/b"])
async def test_multi_segment_or_empty_slug_is_refused_before_sending(
    make_client, bad: str
) -> None:
    client, recorder = make_client(OK)

    async with client:
        with pytest.raises(ValueError, match="single KB article slug"):
            await client.get_kb_article(bad)

    assert recorder.requests == []


async def test_redirects_are_not_followed(make_client) -> None:
    """A 302 on /api/v1 means a login wall or an http->https upgrade intercepted
    the request. Following it would yield a confusing HTML 200."""
    client, _ = make_client(
        lambda request: httpx2.Response(302, headers={"Location": "/login"}, text="")
    )

    async with client:
        with pytest.raises(Exception) as excinfo:
            await client.whoami()

    # It surfaces as "not JSON", which is exactly what a redirect body is.
    assert "instead of JSON" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Response handling
# --------------------------------------------------------------------------- #


async def test_payloads_are_returned_verbatim(make_client) -> None:
    """No renaming, no flattening, no derived fields — the API keys ARE the
    contract, and `requester` in particular must not become `customer_contact`."""
    payload = {
        "data": [
            {
                "id": 2,
                "subject": "Printer on fire",
                "status": {"id": 2, "name": "open"},
                "priority": {"id": 3, "name": "high"},
                "category": {"slug": "bp-task", "name": "BP Task"},
                "requester": {"id": 1, "name": "Ada", "email": "ada@example.com"},
                "assignee": {"id": 1, "name": "Admin", "email": "admin@ebteq.desk"},
                "escalated": False,
                "escalated_at": None,
                "created_at": "2026-08-11T05:09:30+00:00",
                "updated_at": "2026-08-11T05:09:30+00:00",
            }
        ],
        "links": {"first": "…", "last": "…", "prev": None, "next": None},
        "meta": {"current_page": 1, "per_page": 20, "total": 1},
    }
    client, _ = make_client(always_json(200, payload))

    async with client:
        assert await client.list_tickets() == payload


async def test_uncategorised_report_row_survives_with_null_id_and_slug(make_client) -> None:
    """Row identity is `key`; `id` and `slug` are both null on that bucket, so a
    client that keyed on either would drop or collide the row."""
    payload = {
        "data": {
            "range": {"from": None, "to": None},
            "metricKeys": ["total", "escalated", "escalatedUndated"],
            "totals": {"total": 3, "escalated": 5, "escalatedUndated": 2},
            "categories": [
                {"key": "bp-task", "id": 1, "slug": "bp-task", "name": "BP Task",
                 "metrics": {"total": 3, "escalated": 5, "escalatedUndated": 2}},
                {"key": "_uncategorised", "id": None, "slug": None,
                 "name": "Uncategorised", "metrics": {"total": 0}},
            ],
        },
        "meta": {"filters": {"from": None, "to": None}, "generatedAt": "…"},
    }
    client, _ = make_client(always_json(200, payload))

    async with client:
        result = await client.get_escalation_report()

    rows = result["data"]["categories"]
    assert [row["key"] for row in rows] == ["bp-task", "_uncategorised"]
    assert rows[1]["id"] is None and rows[1]["slug"] is None

    # And `escalated` > `total` is passed through untouched, not "corrected".
    assert result["data"]["totals"]["escalated"] == 5
    assert result["data"]["totals"]["total"] == 3


# --------------------------------------------------------------------------- #
# Transport failure
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "exception",
    [
        httpx2.ConnectError("nope"),
        httpx2.ReadTimeout("slow"),
        httpx2.ConnectTimeout("slow"),
    ],
)
async def test_unreachable_host_becomes_an_actionable_transport_error(
    make_client, exception: Exception
) -> None:
    def explode(_request: httpx2.Request) -> httpx2.Response:
        raise exception

    client, _ = make_client(explode)

    async with client:
        with pytest.raises(TransportError) as excinfo:
            await client.whoami()

    message = str(excinfo.value)
    assert BASE_URL in message
    assert type(exception).__name__ in message
    assert "EBTEQDESK_TIMEOUT" in message
    assert TOKEN not in message
