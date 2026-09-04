"""Integration suite — runs ONLY against a live Ebteqdesk.

    EBTEQDESK_BASE_URL=http://localhost:8086 \
    EBTEQDESK_API_TOKEN='6|…' \
    pytest -m integration

With either variable unset the whole module is skipped at collection, so a bare
`pytest` runs the unit suite and nothing here. That is why the skip is a
module-level `skipif` rather than a fixture: a fixture-level skip still imports
and collects, and a half-configured environment would produce six confusing
errors instead of one clear skip.

These tests assert on the SHAPE of the contract, never on the data. A local
stack's ticket count changes with every seed, so an assertion about it would be
a test that fails for the wrong reason.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from ebteqdesk_mcp.client import EbteqdeskClient
from ebteqdesk_mcp.config import Config
from ebteqdesk_mcp.errors import (
    AbilityError,
    InvalidRequestError,
    KeyScopeError,
    NotFoundError,
    PermissionError_,
    RoleScopeError,
    ScopeError,
)


def _env(suffix: str) -> str | None:
    """Read `EBTEQDESK_<suffix>`.

    The pre-rename `WARNIDESK_` fallback that used to be here went with the one
    in `config.py`. Keeping it for the harness alone would mean this suite runs
    against a variable the client itself no longer reads — the one arrangement
    guaranteed to disagree with production.
    """
    return os.environ.get("EBTEQDESK_" + suffix)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (_env("BASE_URL") and _env("API_TOKEN")),
        reason="set EBTEQDESK_BASE_URL and EBTEQDESK_API_TOKEN to run the integration suite",
    ),
]


#: Seconds to pause between integration tests, to stay under the API's own
#: throttle.
#:
#: 🔴 THIS SUITE OUTGREW THE RATE LIMIT AND HAD TO BE PACED. The `/api` throttle
#: is 60 requests per minute PER TOKEN, and these tests now make roughly 85 —
#: each write test creates a ticket through the fixture before doing anything
#: else, and several then read a list back. Run flat out, the suite finishes in
#: about two seconds and the last third of it 429s.
#:
#: The fix is NOT to retry: this client refuses to retry anywhere, and a write
#: that is retried after a 429 can file a second ticket. It is not to raise the
#: limit either — the limit is real and a client that only works when throttling
#: is disabled is a client that does not work. So the suite waits, which is what
#: any well-behaved API consumer does.
#:
#: The arithmetic: ~85 requests must span more than 85 seconds to average under
#: 60/min with headroom. At ~34 tests, 3s each gives ~100s of wall clock and
#: about 50 requests/minute. Slow for a test suite, fine for an opt-in one that
#: talks to a real server.
PACE_SECONDS = float(_env("TEST_PACE_SECONDS") or "3")


@pytest.fixture(autouse=True)
async def _pace():
    """Keep the whole module under the per-token throttle. See PACE_SECONDS."""
    yield

    if PACE_SECONDS > 0:
        await asyncio.sleep(PACE_SECONDS)


@pytest.fixture
async def client():
    async with EbteqdeskClient(Config.from_env()) as client:
        yield client


async def test_whoami_identifies_an_account(client) -> None:
    payload = await client.whoami()

    data = payload["data"]
    assert isinstance(data["id"], int)
    assert "@" in data["email"]
    assert set(data["role"]) >= {"id", "name", "key"}
    assert isinstance(data["permissions"], list)


async def test_whoami_reports_the_key_scopes_that_make_a_403_diagnosable(client) -> None:
    """`apiKey.requested` beside `apiKey.scopes` is the entire basis of the
    scope diagnosis, so its shape is pinned against the real server."""
    api_key = (await client.whoami())["data"]["apiKey"]

    assert api_key is not None, "a bearer token must produce an apiKey block"
    assert set(api_key) >= {"id", "name", "scopes", "requested", "expiresAt"}
    assert isinstance(api_key["requested"], list)
    assert isinstance(api_key["scopes"], list)

    # `scopes` is the key-role intersection, so it can never exceed `requested`.
    assert set(api_key["scopes"]) <= set(api_key["requested"])


async def test_whoami_needs_no_scope(client) -> None:
    """The property the whole diagnosis rests on: any valid key reaches it.

    If this ever stops holding, `_diagnose_scope` silently stops working and
    every scope refusal quietly degrades to the undiagnosed message.
    """
    await client.whoami()  # the fixture's key may hold no scopes at all


async def test_tickets_come_back_in_the_documented_envelope(client) -> None:
    payload = await client.list_tickets()

    assert set(payload) >= {"data", "links", "meta"}
    # 20, not the old fixed 25: the ceiling IS the default, because "at most 20
    # records per pull" is a product requirement and a lower default would make
    # `per_page` primarily a way to ask for MORE.
    assert payload["meta"]["per_page"] == 20
    assert set(payload["links"]) >= {"first", "last", "prev", "next"}

    for ticket in payload["data"]:
        # `requester`, never `customer_contact` — that is the external contract.
        assert set(ticket) >= {
            "id", "subject", "status", "priority", "category",
            "requester", "assignee", "escalated", "escalated_at",
            "created_at", "updated_at",
        }
        assert "customer_contact" not in ticket
        # The state is a real boolean, and the stamp is an instant or absent —
        # never a string "false", never a 0/1 from the driver.
        assert isinstance(ticket["escalated"], bool)
        assert ticket["escalated_at"] is None or isinstance(ticket["escalated_at"], str)


async def test_tickets_by_category_matches_the_base_endpoint(client) -> None:
    everything = await client.list_tickets()
    categories = {
        ticket["category"]["slug"]
        for ticket in everything["data"]
        if ticket.get("category")
    }

    if not categories:
        pytest.skip("no categorised tickets on this stack")

    slug = sorted(categories)[0]
    narrowed = await client.list_tickets_by_category(slug)

    assert set(narrowed) >= {"data", "links", "meta"}
    assert all(t["category"]["slug"] == slug for t in narrowed["data"])


async def test_the_ticket_lists_cap_at_twenty_rows(client) -> None:
    """The cap is a product requirement, so it is verified live on all three
    lists rather than trusted from a constant."""
    for call in (
        client.list_tickets(),
        client.list_tickets_by_category("bp-task"),
        client.list_escalations(),
    ):
        payload = await call
        assert payload["meta"]["per_page"] == 20
        assert len(payload["data"]) <= 20


@pytest.mark.parametrize("bad", [21, 50, 0])
async def test_an_out_of_range_per_page_is_a_422_naming_the_field(client, bad) -> None:
    """🔴 Enforced, not clamped — verified against the real server, because
    "clamped" and "enforced" are indistinguishable from a passing client-side
    test and only one of them lets a caller trust its page size."""
    with pytest.raises(InvalidRequestError) as excinfo:
        await client.list_tickets(per_page=bad)

    assert "per_page" in excinfo.value.field_errors


async def test_a_smaller_per_page_is_honoured_and_echoed(client) -> None:
    payload = await client.list_tickets(per_page=1)

    assert payload["meta"]["per_page"] == 1
    assert len(payload["data"]) <= 1


async def test_the_escalation_queue_shares_the_ticket_shape(client) -> None:
    try:
        queue = await client.list_escalations()
    except (ScopeError, PermissionError_) as exc:
        pytest.skip(f"token cannot read the escalation queue: {exc}")

    assert set(queue) >= {"data", "links", "meta"}

    listed = await client.list_tickets()

    if queue["data"] and listed["data"]:
        # Byte-for-byte the same object shape, from one TicketResource. This is
        # what lets the tool description say "no second shape to learn".
        assert set(queue["data"][0]) == set(listed["data"][0])

    for row in queue["data"]:
        # Every row on this list is escalated by construction.
        assert row["escalated"] is True


async def test_an_unknown_category_404s_and_names_the_slug(client) -> None:
    with pytest.raises(NotFoundError) as excinfo:
        await client.list_tickets_by_category("definitely-not-a-category")

    assert "definitely-not-a-category" in str(excinfo.value)


async def test_the_escalation_report_keys_every_row(client) -> None:
    try:
        payload = await client.get_escalation_report()
    except (ScopeError, PermissionError_) as exc:
        pytest.skip(f"token cannot read the escalation report: {exc}")

    data = payload["data"]
    assert set(data) >= {"range", "metricKeys", "totals", "categories"}
    assert "escalatedUndated" in data["metricKeys"]

    keys = [row["key"] for row in data["categories"]]
    # `key` is present and unique on every row — `id` and `slug` are not, they
    # are both null on the Uncategorised bucket.
    assert all(isinstance(key, str) and key for key in keys)
    assert len(keys) == len(set(keys))

    assert set(payload["meta"]) >= {"filters", "generatedAt"}


async def test_escalated_undated_is_the_same_in_every_range(client) -> None:
    """Rule 2, verified against the live server rather than only asserted in a
    docstring: no date filter can move this number."""
    try:
        everything = await client.get_escalation_report()
        narrow = await client.get_escalation_report(
            date_from="2026-01-01", date_to="2026-01-02"
        )
    except (ScopeError, PermissionError_) as exc:
        pytest.skip(f"token cannot read the escalation report: {exc}")

    assert (
        everything["data"]["totals"]["escalatedUndated"]
        == narrow["data"]["totals"]["escalatedUndated"]
    )


async def test_kb_search_and_fetch_round_trip(client) -> None:
    try:
        listing = await client.search_kb_articles(per_page=5)
    except ScopeError as exc:
        pytest.skip(f"token cannot read the knowledge base: {exc}")

    assert set(listing) >= {"data", "links", "meta"}

    if not listing["data"]:
        pytest.skip("no published articles on this stack")

    summary = listing["data"][0]
    assert set(summary) >= {"slug", "title", "url", "published_at", "updated_at", "excerpt"}
    # `url` is the public portal page. Every row of THIS corpus is published and
    # public, so it is a string here — the field is nullable only on the write
    # echo, which this client does not expose.
    assert isinstance(summary["url"], str)
    assert summary["slug"] in summary["url"]

    article = await client.get_kb_article(summary["slug"])
    data = article["data"]

    assert data["slug"] == summary["slug"]
    assert "body_html" in data and "excerpt" not in data
    assert set(data["seo"]) >= {"title", "description"}


async def test_an_unknown_kb_slug_404s_without_echoing_it(client) -> None:
    """The enumeration guard, verified live: the body must not contain the slug."""
    slug = "definitely-not-an-article-slug-9f2c"

    try:
        with pytest.raises(NotFoundError) as excinfo:
            await client.get_kb_article(slug)
    except ScopeError as exc:
        pytest.skip(f"token cannot read the knowledge base: {exc}")

    assert slug not in str(excinfo.value)


# --------------------------------------------------------------------------- #
# The ticket DETAIL surface, against a real server
# --------------------------------------------------------------------------- #
#
# Read-only, and every one of these skips rather than fails when the stack has
# nothing to look at — a freshly seeded install may legitimately have no
# tickets, and a ticket may legitimately have no attachment. A test that failed
# for that reason would be a test of the seed data, not of the client.


async def _first_visible_ticket_id(client) -> int:
    page = await client.list_tickets(per_page=1)
    rows = page["data"]

    if not rows:
        pytest.skip("no tickets are assigned to this token's account")

    return int(rows[0]["id"])


async def test_a_ticket_detail_is_a_superset_of_its_list_row(client) -> None:
    """The server's detail resource SUBCLASSES the list one, so every header
    field on a row is on the detail payload — and the detail fields are the ones
    no list carries."""
    page = await client.list_tickets(per_page=1)

    if not page["data"]:
        pytest.skip("no tickets are assigned to this token's account")

    row = page["data"][0]
    detail = (await client.get_ticket(int(row["id"])))["data"]

    for field in row:
        assert field in detail, f"the detail payload is missing `{field}`"

    for field in (
        "body",
        "body_html",
        "attachments",
        "reference_number",
        "summary",
        "team",
        "escalated_minutes",
        "conversation",
    ):
        assert field in detail, f"the detail payload is missing `{field}`"


async def test_every_conversation_entry_declares_a_known_kind(client) -> None:
    """🔴 `kind` is the safety property — `note` marks a PRIVATE internal note.
    A fourth value appearing, or one going missing, would leave a client
    classifying staff-only text as a public reply."""
    ticket_id = await _first_visible_ticket_id(client)

    conversation = (await client.get_ticket(ticket_id))["data"]["conversation"]

    for entry in conversation:
        assert entry["kind"] in {"comment", "note", "event"}
        # Every key always present, whatever the kind — an event ships a null
        # body_html and an empty attachments list rather than omitting them.
        assert set(entry) == {
            "kind",
            "body",
            "body_html",
            "author",
            "attachments",
            "created_at",
            "created_at_human",
        }

        for attachment in entry["attachments"]:
            assert set(attachment) == {"id", "name", "mime_type", "size", "url"}
            assert "/api/v1/attachments/" in attachment["url"]


async def test_the_truncation_flag_only_appears_when_the_thread_was_cut(
    client,
) -> None:
    """Top level, beside `data`, and absent otherwise. Both halves verified
    against a real payload rather than a fixture."""
    ticket_id = await _first_visible_ticket_id(client)

    whole = await client.get_ticket(ticket_id)

    assert "conversation_truncated" not in whole

    if len(whole["data"]["conversation"]) < 2:
        pytest.skip("no ticket in reach has a thread long enough to truncate")

    capped = await client.get_ticket(ticket_id, thread_limit=1)

    assert capped["conversation_truncated"] is True
    assert len(capped["data"]["conversation"]) == 1
    # The NEWEST entry, i.e. the tail of the thread.
    assert capped["data"]["conversation"][0] == whole["data"]["conversation"][-1]


async def test_an_out_of_range_thread_limit_is_a_422_naming_the_field(client) -> None:
    ticket_id = await _first_visible_ticket_id(client)

    with pytest.raises(InvalidRequestError) as excinfo:
        await client.get_ticket(ticket_id, thread_limit=0)

    assert "thread_limit" in excinfo.value.field_errors


async def test_the_comments_endpoint_pages_the_same_entries(client) -> None:
    ticket_id = await _first_visible_ticket_id(client)

    embedded = (await client.get_ticket(ticket_id))["data"]["conversation"]

    if not embedded:
        pytest.skip("no ticket in reach has a conversation")

    paged = await client.get_ticket_comments(ticket_id, per_page=1)

    assert paged["meta"]["total"] == len(embedded)
    assert paged["data"][0] == embedded[0]


async def test_an_attachment_comes_back_as_a_downscaled_image(client) -> None:
    """The one endpoint on this API whose success body is not JSON. Verified
    against real bytes: the content type is an image type, and asking for a
    smaller `max_dimension` actually produces a smaller image."""
    ticket_id = await _first_visible_ticket_id(client)
    detail = (await client.get_ticket(ticket_id))["data"]

    candidates = list(detail["attachments"])
    for entry in detail["conversation"]:
        candidates.extend(entry["attachments"])

    images = [a for a in candidates if str(a["mime_type"] or "").startswith("image/")]

    if not images:
        pytest.skip("no image attachment is reachable from this token's tickets")

    image = await client.get_ticket_attachment(int(images[0]["id"]))

    assert image.mime_type.startswith("image/")
    assert image.data

    smaller = await client.get_ticket_attachment(
        int(images[0]["id"]), max_dimension=64
    )

    # A ceiling, never a target: an image already under 64px comes back
    # untouched rather than enlarged, so this is <= and not <.
    if image.width is not None and smaller.width is not None:
        assert smaller.width <= image.width
        assert smaller.width <= 64


# --------------------------------------------------------------------------- #
# The scope diagnosis, against a real server
# --------------------------------------------------------------------------- #
#
# These need two purpose-minted keys, because the whole point is that ONE 403
# body has two causes. Mint them on a stack with scoped keys:
#
#   # a key that was never given kb:read
#   $u->issueApiKey('missing-scope', [ApiScope::TICKET_READ])
#
#   # a key that WAS given a scope its owner's role does not back
#   $roleless->issueApiKey('role-blocked', [ApiScope::ESCALATION_REPORTS_READ])
#
# then export EBTEQDESK_TEST_KEY_MISSING_SCOPE / EBTEQDESK_TEST_ROLE_BLOCKED_KEY.

MISSING_SCOPE_KEY = _env("TEST_KEY_MISSING_SCOPE")
ROLE_BLOCKED_KEY = _env("TEST_ROLE_BLOCKED_KEY")


def _client_for(token: str) -> EbteqdeskClient:
    base = Config.from_env()
    return EbteqdeskClient(Config(base_url=base.base_url, token=token, timeout=base.timeout))


@pytest.mark.skipif(
    not MISSING_SCOPE_KEY,
    reason="set EBTEQDESK_TEST_KEY_MISSING_SCOPE to a key minted without kb:read",
)
async def test_a_key_without_the_scope_is_diagnosed_as_a_key_problem() -> None:
    async with _client_for(MISSING_SCOPE_KEY) as client:
        with pytest.raises(KeyScopeError) as excinfo:
            await client.search_kb_articles(query="anything")

    error = excinfo.value
    assert error.diagnosis == "key"
    assert error.required_scope == "kb:read"
    assert "mint a NEW key" in str(error)
    assert "administrator" not in str(error).lower()


@pytest.mark.skipif(
    not ROLE_BLOCKED_KEY,
    reason="set EBTEQDESK_TEST_ROLE_BLOCKED_KEY to a key whose owner's role lacks the ability",
)
async def test_a_role_that_lost_the_ability_is_diagnosed_as_a_role_problem() -> None:
    async with _client_for(ROLE_BLOCKED_KEY) as client:
        with pytest.raises(RoleScopeError) as excinfo:
            await client.get_escalation_report()

    error = excinfo.value
    assert error.diagnosis == "role"
    assert error.required_scope == "escalation-reports:read"
    assert "administrator" in str(error).lower()
    # The regression this replaced: never send a role-blocked user to mint.
    assert "mint a NEW key" not in str(error)


@pytest.mark.skipif(
    not (MISSING_SCOPE_KEY and ROLE_BLOCKED_KEY),
    reason="both diagnostic keys are needed to compare the two causes",
)
async def test_the_server_refuses_both_causes_identically() -> None:
    """The premise of the whole design, verified rather than assumed: the two
    403 bodies are byte-identical, so the cause CANNOT come from the response.
    """
    bodies = []

    for token, call in (
        (MISSING_SCOPE_KEY, lambda c: c.search_kb_articles(query="x")),
        (ROLE_BLOCKED_KEY, lambda c: c.get_escalation_report()),
    ):
        async with _client_for(token) as client:
            with pytest.raises(ScopeError) as excinfo:
                await call(client)
            bodies.append(excinfo.value.server_message)

    # Different scopes, so compare the sentence with the scope factored out.
    assert bodies[0].replace("kb:read", "S") == bodies[1].replace(
        "escalation-reports:read", "S"
    )


# --------------------------------------------------------------------------- #
# The write surface, against a real server
# --------------------------------------------------------------------------- #
#
# 🔴 THESE TESTS CREATE REAL DATA AND CANNOT CLEAN UP AFTER THEMSELVES. There is
# no delete-ticket and no delete-comment endpoint on /api/v1, so every run
# leaves a ticket behind. That is why they are gated on a THIRD variable rather
# than riding along with `-m integration`: someone pointing the integration
# suite at staging to check a read contract must not silently file tickets there
# and email a requester's contact address.
#
#   EBTEQDESK_BASE_URL=http://localhost:8086 \
#   EBTEQDESK_API_TOKEN='6|…' \
#   EBTEQDESK_ALLOW_WRITES=1 \
#   pytest -m integration
#
# The requester below is a deliberately fake address on `.invalid`, which RFC
# 2606 reserves precisely so it can never resolve. A survey or notification
# aimed at it cannot reach a person.

WRITES_ALLOWED = _env("ALLOW_WRITES")

writes = pytest.mark.skipif(
    not WRITES_ALLOWED,
    reason="set EBTEQDESK_ALLOW_WRITES=1 — these create real tickets that cannot be deleted",
)

REQUESTER = {"email": "mcp-integration@example.invalid", "name": "MCP Integration"}


@pytest.fixture
async def created_ticket(client):
    """One ticket per test that needs one, so a failure cannot cascade."""
    payload = await client.create_ticket(
        subject="[ebteqdesk-mcp] integration write check",
        description="Filed by the ebteqdesk-mcp integration suite. Safe to close.",
        requester=REQUESTER,
    )

    return payload["data"]


@writes
async def test_a_created_ticket_matches_the_read_endpoint_shape(client) -> None:
    """The contract worth pinning above all others here: the ticket a write
    returns is the SAME object a read returns. If these two drift, every client
    needs two parsers and one of them will be wrong."""
    created = (
        await client.create_ticket(
            subject="[ebteqdesk-mcp] shape check",
            description="…",
            requester=REQUESTER,
        )
    )["data"]

    listed = await client.list_tickets()
    same = next((t for t in listed["data"] if t["id"] == created["id"]), None)

    assert same is not None, "a ticket created by this token must be visible to it"
    assert set(created) == set(same)
    assert created["requester"]["email"] == REQUESTER["email"]
    # Assignee is forced to the caller and is not client-controllable.
    assert created["assignee"]["id"] == (await client.whoami())["data"]["id"]


@writes
async def test_create_applies_the_servers_defaults_when_fields_are_omitted(
    client,
) -> None:
    """This client omits optional fields rather than sending nulls, so the
    defaults come from the server. Verified live so the two cannot drift."""
    created = (
        await client.create_ticket(
            subject="[ebteqdesk-mcp] defaults check",
            description="…",
            requester=REQUESTER,
        )
    )["data"]

    assert created["status"]["id"] == 1  # new
    assert created["priority"]["id"] == 2  # normal
    assert created["category"] is None


@writes
async def test_create_honours_every_optional_field(client) -> None:
    listing = await client.list_tickets()
    slugs = [t["category"]["slug"] for t in listing["data"] if t.get("category")]
    category = slugs[0] if slugs else None

    created = (
        await client.create_ticket(
            subject="[ebteqdesk-mcp] optional fields",
            description="…",
            requester=REQUESTER,
            priority=3,
            status=2,
            category=category,
            reference_number="MCP-INT-1",
            tags=["mcp-integration"],
        )
    )["data"]

    assert created["priority"]["id"] == 3
    assert created["status"]["id"] == 2
    if category:
        assert created["category"]["slug"] == category


@writes
async def test_commenting_returns_a_receipt_and_no_comment_body(
    client, created_ticket
) -> None:
    result = await client.comment_on_ticket(
        created_ticket["id"], body="Integration suite reply."
    )

    assert set(result) >= {"data", "comment"}
    assert set(result["comment"]) == {"id", "created_at"}
    assert isinstance(result["comment"]["id"], int)
    # 🔴 The API never serialises comment text, so `review_state = pending`
    # cannot leak through this path. Nothing may add a body here.
    assert "body" not in result["comment"]
    assert "comments" not in result["data"]


@writes
async def test_escalated_flips_across_an_escalate_de_escalate_round_trip(
    client, created_ticket
) -> None:
    """The field that made `escalate_ticket` reconcilable.

    This test replaces one that asserted the OPPOSITE — that no escalation field
    existed on either response, so a timed-out escalate was unknowable and the
    tool description had to say "you cannot check first". Both responses were
    byte-identical then. They are not now, and the description changed with it.
    """
    assert created_ticket["escalated"] is False
    assert created_ticket["escalated_at"] is None

    escalated = (await client.escalate_ticket(created_ticket["id"]))["data"]

    assert escalated["escalated"] is True
    assert escalated["escalated_at"] is not None

    # And it is visible on the ordinary list too, which is what makes "check
    # before you call" real advice rather than a slogan.
    listed = await client.list_tickets()
    same = next(t for t in listed["data"] if t["id"] == created_ticket["id"])
    assert same["escalated"] is True

    de_escalated = (await client.de_escalate_ticket(created_ticket["id"]))["data"]

    assert de_escalated["escalated"] is False
    # De-escalating CLEARS the stamp — it does not leave the old one behind.
    assert de_escalated["escalated_at"] is None


@writes
async def test_an_escalated_ticket_appears_on_the_shared_queue(
    client, created_ticket
) -> None:
    try:
        await client.list_escalations()
    except (ScopeError, PermissionError_) as exc:
        pytest.skip(f"token cannot read the escalation queue: {exc}")

    await client.escalate_ticket(created_ticket["id"])

    ids = [row["id"] for row in (await client.list_escalations())["data"]]
    assert created_ticket["id"] in ids

    await client.de_escalate_ticket(created_ticket["id"])

    ids = [row["id"] for row in (await client.list_escalations())["data"]]
    assert created_ticket["id"] not in ids


@writes
async def test_solving_removes_a_ticket_from_the_queue_without_de_escalating(
    client, created_ticket
) -> None:
    """⚠️ The behaviour the tool description warns about: absence from this list
    is NOT evidence the escalation was answered. Solved and de-escalated look
    identical from here, and the ticket is still escalated."""
    try:
        await client.list_escalations()
    except (ScopeError, PermissionError_) as exc:
        pytest.skip(f"token cannot read the escalation queue: {exc}")

    await client.escalate_ticket(created_ticket["id"])
    assert created_ticket["id"] in [
        row["id"] for row in (await client.list_escalations())["data"]
    ]

    solved = (await client.close_ticket(created_ticket["id"]))["data"]

    # Gone from the queue...
    assert created_ticket["id"] not in [
        row["id"] for row in (await client.list_escalations())["data"]
    ]
    # ...while still escalated. Absence proves nothing about the escalation.
    assert solved["escalated"] is True


@writes
async def test_closing_moves_the_status_and_defaults_to_solved(
    client, created_ticket
) -> None:
    closed = await client.close_ticket(created_ticket["id"])

    assert closed["data"]["status"]["id"] == 4  # solved
    assert closed["data"]["status"]["name"]


@writes
async def test_closing_as_closed_rather_than_solved(client, created_ticket) -> None:
    closed = await client.close_ticket(created_ticket["id"], status=5)

    assert closed["data"]["status"]["id"] == 5


@writes
async def test_an_unclosable_status_is_refused_with_a_field_error(
    client, created_ticket
) -> None:
    """Passed through unvalidated on purpose, so the server's list is the only
    list. 7 is spam — an outcome of another action, never a close."""
    with pytest.raises(InvalidRequestError) as excinfo:
        await client.close_ticket(created_ticket["id"], status=7)

    assert "status" in excinfo.value.field_errors


@writes
async def test_a_short_subject_is_refused_with_the_field_named(client) -> None:
    with pytest.raises(InvalidRequestError) as excinfo:
        await client.create_ticket(
            subject="ab", description="…", requester=REQUESTER
        )

    assert "subject" in excinfo.value.field_errors


@writes
async def test_a_ticket_that_is_not_ours_is_a_404_not_a_403(client) -> None:
    """Write visibility is read visibility, so an id we cannot see is
    indistinguishable from one that does not exist."""
    with pytest.raises(NotFoundError):
        await client.escalate_ticket(2_000_000_000)


@writes
async def test_replying_to_an_escalated_ticket_needs_the_escalation_scope(
    client, created_ticket
) -> None:
    """The state-dependent scope, verified live rather than only mocked: the
    SAME token, the SAME endpoint, the SAME ticket — refused after escalation
    and allowed before it, purely because of the ticket's state.

    Skipped when the fixture's key holds `escalation:write`, since then both
    calls simply succeed. `EBTEQDESK_TEST_TICKET_WRITE_ONLY_KEY` is the key that
    makes this meaningful: minted with `ticket:write` and NOT `escalation:write`.
    """
    ticket_write_only = _env("TEST_TICKET_WRITE_ONLY_KEY")

    if not ticket_write_only:
        pytest.skip(
            "set EBTEQDESK_TEST_TICKET_WRITE_ONLY_KEY to a key with ticket:write "
            "and no escalation:write"
        )

    # Before escalation, the narrow key can reply.
    async with _client_for(ticket_write_only) as narrow:
        await narrow.comment_on_ticket(created_ticket["id"], body="Before escalation.")

    await client.escalate_ticket(created_ticket["id"])

    # After escalation, the same call with the same key is refused — and the
    # message has to say why, or the user re-mints with `ticket:write` again.
    async with _client_for(ticket_write_only) as narrow:
        with pytest.raises(ScopeError) as excinfo:
            await narrow.comment_on_ticket(
                created_ticket["id"], body="After escalation."
            )

    error = excinfo.value
    assert error.required_scope == "escalation:write"
    assert "THIS TICKET IS ESCALATED" in str(error)

    await client.de_escalate_ticket(created_ticket["id"])


@writes
async def test_a_key_without_ticket_write_cannot_create(client) -> None:
    """The read scopes must not be a write surface. Uses the read-only
    diagnostic key if one is configured."""
    read_only = _env("TEST_KEY_MISSING_SCOPE")

    if not read_only:
        pytest.skip("set EBTEQDESK_TEST_KEY_MISSING_SCOPE to a read-only key")

    async with _client_for(read_only) as narrow:
        with pytest.raises(ScopeError) as excinfo:
            await narrow.create_ticket(
                subject="[ebteqdesk-mcp] should never exist",
                description="…",
                requester=REQUESTER,
            )

    assert excinfo.value.required_scope == "ticket:write"


@writes
async def test_an_ability_refusal_names_the_ability_and_is_not_a_scope_error() -> None:
    """The third 403 flavour, live. It needs an account whose ROLE lacks
    `ticket.close` while its key carries `ticket:write` — mint one on a stack
    with a narrowed role and export the token.

    🔴 THE TICKET MUST BE CREATED BY THAT SAME ACCOUNT, and the first version of
    this test got it wrong in a way worth recording. Handed a ticket belonging
    to the main fixture's account, the narrow key gets a 404, not a 403 — write
    visibility is read visibility, so a ticket that is not yours does not exist
    to you and the ability check is never reached. That is the API behaving
    correctly; it just means an ability refusal can only be observed on a ticket
    the refused account can actually see.
    """
    token = _env("TEST_NO_CLOSE_ABILITY_KEY")

    if not token:
        pytest.skip(
            "set EBTEQDESK_TEST_NO_CLOSE_ABILITY_KEY to a ticket:write key whose "
            "role lacks ticket.close"
        )

    async with _client_for(token) as narrow:
        own = (
            await narrow.create_ticket(
                subject="[ebteqdesk-mcp] ability refusal fixture",
                description="…",
                requester=REQUESTER,
            )
        )["data"]

        # The same key, the same ticket: reply is allowed, close is not. That
        # pair is what proves the refusal is about the ABILITY and not about
        # the key, the scope, or the ticket.
        await narrow.comment_on_ticket(own["id"], body="Reply is permitted.")

        with pytest.raises(AbilityError) as excinfo:
            await narrow.close_ticket(own["id"])

    error = excinfo.value
    assert error.required_ability == "ticket.close"
    assert error.required_scope is None
    assert not isinstance(error, ScopeError)
    assert "Minting a new key will not help" in str(error)
    assert "administrator" in str(error).lower()
