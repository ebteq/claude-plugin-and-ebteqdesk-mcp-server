"""The three ticket lists, and the one paging contract they share.

    GET /api/v1/tickets        list_tickets              own, ownership-scoped
    GET /api/v1/{category}     list_tickets_by_category  own, ownership-scoped
    GET /api/v1/escalations    list_escalations          SHARED, everybody's

They select different rows and answer to different scopes, but page size, its
ceiling, its 422 and the envelope are one definition server-side (a single
`PagesTicketLists` trait). Testing them together is the point: a cap asserted in
one place and not the others is a cap that is 20 in one place.

🔴 The shared-queue behaviour of `list_escalations` gets its own section at the
bottom. It is the only ticket list on this API that is not ownership-scoped, and
nothing in the payload distinguishes it — both render the identical
`TicketResource` — so the difference lives entirely in what this client and its
tool description say about it.
"""

from __future__ import annotations

import pytest

from conftest import (
    always_json,
    per_page_refusal,
    scope_refusal,
    ticket_list,
    ticket_row,
)
from ebteqdesk_mcp.errors import InvalidRequestError, KeyScopeError, ScopeError

EMPTY = always_json(200, ticket_list([]))

#: (client call, path) for each of the three, parameterised everywhere the rule
#: under test is one all three obey.
LISTS = [
    (lambda c, **kw: c.list_tickets(**kw), "/api/v1/tickets"),
    (
        lambda c, **kw: c.list_tickets_by_category("bp-task", **kw),
        "/api/v1/bp-task",
    ),
    (lambda c, **kw: c.list_escalations(**kw), "/api/v1/escalations"),
]


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("call, path", LISTS)
async def test_each_list_hits_its_documented_path(make_client, call, path) -> None:
    client, recorder = make_client(EMPTY)

    async with client:
        await call(client)

    assert recorder.last.method == "GET"
    assert recorder.last.url.path == path


async def test_escalations_is_a_literal_not_a_category_alias(make_client) -> None:
    """`/api/v1/escalations` is a single-segment literal declared ABOVE the
    `/api/v1/{category}` wildcard. If a caller passed "escalations" as a
    category slug it would reach the same URL and silently get the shared queue
    where it asked for its own tickets — so the two must not be confusable."""
    client, recorder = make_client(EMPTY)

    async with client:
        await client.list_escalations()
        queue_path = recorder.last.url.path

        await client.list_tickets_by_category("escalations")
        aliased_path = recorder.last.url.path

    # They DO collide as URLs — that is a property of the server's routing, not
    # something this client can fix. What it can do is have a dedicated method
    # so nobody has to reach the queue by guessing a slug.
    assert queue_path == aliased_path == "/api/v1/escalations"


# --------------------------------------------------------------------------- #
# Paging — one contract, three lists
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("call, path", LISTS)
async def test_omitted_paging_sends_no_query_parameters(
    make_client, call, path
) -> None:
    """An absent `per_page` must not become `?per_page=`. The server reads an
    empty one as "use the default" — a cleared form control submits exactly
    that — but sending it makes every log line and cache key noisier for
    nothing."""
    client, recorder = make_client(EMPTY)

    async with client:
        await call(client)

    assert recorder.last.url.query == b""


@pytest.mark.parametrize("call, path", LISTS)
async def test_page_and_per_page_are_both_sent_when_given(
    make_client, call, path
) -> None:
    client, recorder = make_client(EMPTY)

    async with client:
        await call(client, page=2, per_page=5)

    assert dict(recorder.last.url.params) == {"page": "2", "per_page": "5"}


@pytest.mark.parametrize("call, path", LISTS)
async def test_per_page_above_the_ceiling_is_sent_not_clamped(
    make_client, call, path
) -> None:
    """🔴 THE RULE THAT MATTERS MOST HERE. The ceiling is 20 and the server
    ENFORCES it with a 422 rather than clamping, precisely so a client that
    asked for 50 cannot page as though it had 50-row pages while receiving 20.
    A client that clamped locally would recreate exactly the silence the server
    refuses — and would disagree with curl."""
    client, recorder = make_client(per_page_refusal())

    async with client:
        with pytest.raises(InvalidRequestError):
            await call(client, per_page=21)

    assert dict(recorder.last.url.params)["per_page"] == "21"


@pytest.mark.parametrize("call, path", LISTS)
async def test_the_422_names_the_field_and_the_ceiling(make_client, call, path) -> None:
    """The message has to be actionable on its own: a model that reads "invalid"
    retries the same number."""
    client, _ = make_client(per_page_refusal())

    async with client:
        with pytest.raises(InvalidRequestError) as excinfo:
            await call(client, per_page=50)

    error = excinfo.value
    message = str(error)

    assert error.field_errors == {
        "per_page": ["The per page may not be greater than 20."]
    }
    assert "per_page" in message
    assert "greater than 20" in message


async def test_a_per_page_below_one_is_also_the_servers_call(make_client) -> None:
    """0 and negatives are 422s too (`min:1`). Passed through for the same
    reason as the ceiling — one rule, and it lives on the server."""
    client, recorder = make_client(
        always_json(
            422,
            {
                "error": "The given data was invalid.",
                "errors": {"per_page": ["The per page must be at least 1."]},
            },
        )
    )

    async with client:
        with pytest.raises(InvalidRequestError):
            await client.list_escalations(per_page=0)

    assert dict(recorder.last.url.params)["per_page"] == "0"


async def test_the_envelope_reports_the_effective_page_size(make_client) -> None:
    """`meta.per_page` echoes what was actually applied, which is how a caller
    confirms its request rather than assuming it."""
    client, _ = make_client(always_json(200, ticket_list([ticket_row()], per_page=5)))

    async with client:
        result = await client.list_tickets(per_page=5)

    assert result["meta"]["per_page"] == 5
    assert set(result) >= {"data", "links", "meta"}


# --------------------------------------------------------------------------- #
# Escalation state on every row
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("call, path", LISTS)
async def test_rows_carry_both_escalation_fields_verbatim(
    make_client, call, path
) -> None:
    rows = [
        ticket_row(id=1, escalated=True, escalated_at="2026-08-01T09:00:00+00:00"),
        # 🔴 The legacy row: genuinely escalated, no timestamp. A client keying
        # off `escalated_at` reads this one — the OLDEST in the queue — as not
        # escalated.
        ticket_row(id=2, escalated=True, escalated_at=None),
        ticket_row(id=3, escalated=False, escalated_at=None),
    ]
    client, _ = make_client(always_json(200, ticket_list(rows)))

    async with client:
        result = await call(client)

    assert [row["escalated"] for row in result["data"]] == [True, True, False]
    assert [row["escalated_at"] for row in result["data"]] == [
        "2026-08-01T09:00:00+00:00",
        None,
        None,
    ]

    # Nothing is derived, defaulted or filled in. A null timestamp stays null.
    assert result["data"] == rows


async def test_a_null_escalated_at_is_not_turned_into_not_escalated(
    make_client,
) -> None:
    """The whole reason both fields ship. If this client ever "helpfully"
    normalised the pair, the tickets longest in the queue would read as not
    escalated — which is the one wrong answer this field pair exists to
    prevent."""
    client, _ = make_client(
        always_json(200, ticket_list([ticket_row(escalated=True, escalated_at=None)]))
    )

    async with client:
        row = (await client.list_escalations())["data"][0]

    assert row["escalated"] is True
    assert row["escalated_at"] is None


# --------------------------------------------------------------------------- #
# The shared queue
# --------------------------------------------------------------------------- #


async def test_the_queue_returns_rows_assigned_to_other_people(make_client) -> None:
    """🔴 THE DEFINING PROPERTY, and the one a description could quietly get
    wrong. Every other ticket list is `tickets.user_id = caller`; this one is
    the whole installation's. The client must pass those rows through untouched
    — no filtering to the caller, which would silently reimplement the
    ownership scoping the endpoint deliberately does not have."""
    rows = [
        ticket_row(id=1, assignee={"id": 9, "name": "Someone Else",
                                  "email": "other@ebteq.desk"}),
        # The rows that matter most in a triage queue: nobody is on them.
        ticket_row(id=2, assignee=None),
        ticket_row(id=3, assignee={"id": 1, "name": "Admin",
                                   "email": "admin@ebteq.desk"}),
    ]
    client, _ = make_client(always_json(200, ticket_list(rows)))

    async with client:
        result = await client.list_escalations()

    assert len(result["data"]) == 3
    assert result["data"] == rows
    # Unassigned rows survive: a client that dropped them would hide exactly the
    # tickets most needing attention.
    assert result["data"][1]["assignee"] is None


async def test_the_queue_preserves_the_servers_order_including_nulls_last(
    make_client,
) -> None:
    """Ordered `escalated_at ASC NULLS LAST`, `id` as tiebreak. The client must
    not re-sort: a natural client-side sort on `escalated_at` would put the null
    rows FIRST or drop them, and either way the queue stops matching the
    dashboard it is supposed to mirror."""
    rows = [
        ticket_row(id=5, escalated=True, escalated_at="2026-07-01T09:00:00+00:00"),
        ticket_row(id=6, escalated=True, escalated_at="2026-08-01T09:00:00+00:00"),
        ticket_row(id=2, escalated=True, escalated_at=None),
        ticket_row(id=7, escalated=True, escalated_at=None),
    ]
    client, _ = make_client(always_json(200, ticket_list(rows)))

    async with client:
        result = await client.list_escalations()

    assert [row["id"] for row in result["data"]] == [5, 6, 2, 7]


async def test_the_queue_uses_its_own_scope(make_client) -> None:
    """`escalation:read`, not `ticket:read` and not `escalation-reports:read`.
    Until this endpoint existed it gated nothing at all."""
    client, recorder = make_client(
        scope_refusal(
            "escalation:read",
            requested=["ticket:read"],
            scopes=["ticket:read"],
        )
    )

    async with client:
        with pytest.raises(KeyScopeError) as excinfo:
            await client.list_escalations()

    assert excinfo.value.required_scope == "escalation:read"
    assert "mint a NEW key" in str(excinfo.value)
    assert recorder.paths == ["/api/v1/escalations", "/api/v1/user"]


async def test_the_queue_scope_is_not_the_report_scope(make_client) -> None:
    """`escalation:read` and `escalation-reports:read` are one character apart
    and gate different things — the queue versus the counts. A key holding the
    report scope must not reach the queue."""
    client, _ = make_client(
        scope_refusal(
            "escalation:read",
            requested=["escalation-reports:read"],
            scopes=["escalation-reports:read"],
        )
    )

    async with client:
        with pytest.raises(ScopeError) as excinfo:
            await client.list_escalations()

    assert excinfo.value.required_scope == "escalation:read"


async def test_an_empty_queue_is_an_empty_list_not_an_error(make_client) -> None:
    """Nothing escalated is the healthy state and must read as one."""
    client, _ = make_client(always_json(200, ticket_list([])))

    async with client:
        result = await client.list_escalations()

    assert result["data"] == []
    assert result["meta"]["total"] == 0
