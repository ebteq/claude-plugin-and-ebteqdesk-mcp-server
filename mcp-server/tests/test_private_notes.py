"""`add_private_note` — the internal-note write, client and MCP layer.

Mocked at the socket (`httpx2.MockTransport`), like every other write test here:
the assertions are about the bytes this client puts ON THE WIRE and about the
text a model reads before choosing between this tool and `comment_on_ticket`.

---------------------------------------------------------------------------
Why the DESCRIPTION assertions are the important half of this file
---------------------------------------------------------------------------
The transport half is small — one POST to one path with one field. The risk this
tool actually carries is a model picking the WRONG ONE of two adjacent write
tools, and the only thing standing between it and a mailed-out internal remark is
the first line of each description. So the pair is asserted TOGETHER: the note
tool must say it does not email, the comment tool must say it does, and neither
may claim the other's behaviour. Asserting only one of them would let the two
descriptions converge into a single ambiguous sentence with every test green.
"""

from __future__ import annotations

import json as jsonlib

import httpx2
import pytest

from conftest import (
    ABILITY_REFUSAL,
    ability_refusal,
    always_json,
    scope_refusal,
    ticket_payload,
)
from mcp.server.mcpserver.exceptions import ToolError

from ebteqdesk_mcp import server as srv
from ebteqdesk_mcp.client import EbteqdeskClient
from ebteqdesk_mcp.config import Config
from ebteqdesk_mcp.errors import AbilityError, ScopeError

CREATED = always_json(201, ticket_payload())


def body_of(request: httpx2.Request) -> dict:
    return jsonlib.loads(request.content)


def described(tool) -> str:
    """A tool description with its hard wrapping collapsed. See
    test_server_tools.described() for why every assertion below uses it."""
    return " ".join((tool.description or "").split())


@pytest.fixture
async def tools() -> dict[str, object]:
    return {tool.name: tool for tool in await srv.mcp.list_tools()}


@pytest.fixture
def wired(monkeypatch):
    """Install a client whose socket is `handler` as the server's shared client."""

    def install(handler):
        config = Config(base_url="https://ebteqdesk.test", token="6|t", timeout=5.0)
        client = EbteqdeskClient(config, transport=httpx2.MockTransport(handler))
        monkeypatch.setattr(srv, "_client", client)
        return client

    yield install

    monkeypatch.setattr(srv, "_client", None)


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #


async def test_it_posts_the_body_to_the_notes_path(make_client) -> None:
    """The VERB and the PATH. `/notes` and not `/comments` is the entire
    difference between an internal record and an email to the requester, so it is
    asserted on the wire rather than trusted from the method name."""
    client, recorder = make_client(CREATED)

    async with client:
        await client.add_private_note(42, body="Vendor RMA pending.")

    assert recorder.last.method == "POST"
    assert recorder.last.url.path == "/api/v1/tickets/42/notes"
    assert body_of(recorder.last) == {"body": "Vendor RMA pending."}

    # Exactly one request. A write is not idempotent, and a client that
    # retried would file the note twice with no way to remove either.
    assert len(recorder.requests) == 1


async def test_no_private_flag_is_sent(make_client) -> None:
    """🔴 The body is `{"body": ...}` and NOTHING ELSE.

    The server has no `private` field on this endpoint — the path IS the
    discriminator — so a client that helpfully added one would be inventing a
    request shape the API never described, and the day the server grows a real
    `private` key that invented one would start meaning something.
    """
    client, recorder = make_client(CREATED)

    async with client:
        await client.add_private_note(42, body="Internal.")

    assert set(body_of(recorder.last)) == {"body"}


async def test_the_payload_is_returned_verbatim(make_client) -> None:
    """The receipt is passed through unrenamed, like every other method here."""
    payload = ticket_payload()
    payload["comment"] = {"id": 9, "created_at": "2026-08-15T09:00:00+00:00"}

    client, _ = make_client(always_json(201, payload))

    async with client:
        assert await client.add_private_note(42, body="x") == payload


async def test_a_null_comment_id_is_passed_through_not_smoothed(make_client) -> None:
    """⚠️ A null `comment.id` means NOTHING WAS FILED, and it must survive.

    The server answers 201 either way. A client that turned the null into a
    falsy-but-present value, or raised on it, would either hide the one signal
    that the note did not land or turn a documented outcome into an error. It is
    the description's job to explain it, not this layer's job to change it.
    """
    payload = ticket_payload()
    payload["comment"] = {"id": None, "created_at": None}

    client, _ = make_client(always_json(201, payload))

    async with client:
        result = await client.add_private_note(42, body="x")

    assert result["comment"]["id"] is None


async def test_a_non_numeric_ticket_id_is_refused_before_the_request(make_client) -> None:
    """`_ticket_id` guards this path like every other ticket path — the route
    carries `whereNumber`, so a word would 404 against Laravel's HTML page."""
    client, recorder = make_client(CREATED)

    async with client:
        with pytest.raises(ValueError):
            await client.add_private_note("forty-two", body="x")  # type: ignore[arg-type]

    assert recorder.requests == []


# --------------------------------------------------------------------------- #
# The escalated-ticket refusal
# --------------------------------------------------------------------------- #


async def test_an_escalation_write_refusal_is_narrowed_for_this_endpoint(
    make_client,
) -> None:
    """🔴 A refusal naming `escalation:write` on THIS path means one thing: the
    ticket is escalated. The narrowing turns that into a sentence."""
    client, _ = make_client(scope_refusal("escalation:write"))

    async with client:
        with pytest.raises(ScopeError) as raised:
            await client.add_private_note(42, body="x")

    message = str(raised.value)

    assert "THIS TICKET IS ESCALATED" in message
    # ⚠️ INSTEAD OF, not IN ADDITION TO. The route floor is the any-of
    # `ticket:write|escalation:write`, and an escalated ticket is charged the
    # escalation scope alone — so a user holding `ticket:write` has the WRONG
    # scope for this ticket, not an incomplete set. Saying "in addition"
    # produced the re-mint loop this narrowing exists to stop.
    assert "`escalation:write` scope INSTEAD OF `ticket:write`" in message
    # The advice half: it IS checkable in advance.
    assert "`escalated` boolean" in message
    # Nothing is masked — the original refusal survives underneath.
    assert "escalation:write" in (raised.value.required_scope or "")
    assert raised.value.server_message


async def test_the_note_refusal_does_not_claim_a_reply_would_be_downgraded(
    make_client,
) -> None:
    """🔴 THE REASON DIFFERS FROM THE COMMENT ENDPOINT'S, AND THAT IS WHY THIS IS
    A SEPARATE NARROWER.

    `escalated_comment_error` explains the refusal as "Ebteqdesk would silently
    downgrade your requester-facing reply into a private note". On a tool whose
    whole purpose is to file a private note that is FALSE, and a user told it
    concludes the tool is broken. Reusing that function here would have been one
    line and would have printed exactly that paragraph — so this test exists to
    make the shortcut fail rather than merely be discouraged.
    """
    client, _ = make_client(scope_refusal("escalation:write"))

    async with client:
        with pytest.raises(ScopeError) as raised:
            await client.add_private_note(42, body="x")

    message = str(raised.value)

    assert "downgrades" not in message
    assert "requester-facing reply" not in message

    # …and the honest reason IS given, rather than the paragraph just being cut.
    assert "belongs to the BP queue" in message
    assert "nothing here reaches the requester" in message


async def test_an_unrelated_scope_refusal_is_left_alone(make_client) -> None:
    """The narrowing branches on `required_scope` being exactly
    `escalation:write` — a structured field against a constant, never prose. A
    `ticket:write` refusal on the same path is the ordinary one and must not
    acquire an escalation story."""
    client, _ = make_client(scope_refusal("ticket:write"))

    async with client:
        with pytest.raises(ScopeError) as raised:
            await client.add_private_note(42, body="x")

    assert "THIS TICKET IS ESCALATED" not in str(raised.value)


async def test_an_ability_refusal_is_not_narrowed(make_client) -> None:
    """A `required_ability` 403 is a different condition with opposite advice —
    an administrator, not a new key — and it must not be dressed up as the
    escalation case."""
    client, _ = make_client(ability_refusal("bp_escalation.reply"))

    async with client:
        with pytest.raises(AbilityError) as raised:
            await client.add_private_note(42, body="x")

    assert "THIS TICKET IS ESCALATED" not in str(raised.value)
    assert raised.value.required_ability == "bp_escalation.reply"
    assert ABILITY_REFUSAL.format(ability="bp_escalation.reply") in str(raised.value)


# --------------------------------------------------------------------------- #
# The MCP layer
# --------------------------------------------------------------------------- #


async def test_the_tool_is_registered_with_the_two_arguments(tools) -> None:
    tool = tools["add_private_note"]

    assert set(tool.input_schema.get("properties", {})) == {"ticket_id", "body"}
    assert set(tool.input_schema.get("required", [])) == {"ticket_id", "body"}

    # No dry run, here as everywhere. Ebteqdesk has no such mode, so a
    # client-side one could only describe the request it would have sent.
    assert not set(tool.input_schema["properties"]) & {
        "dry_run",
        "preview",
        "confirm",
        "private",
    }


async def test_it_leads_with_the_fact_that_the_requester_is_not_emailed(tools) -> None:
    """First line, unmissable. A model may never read the last line of a
    description; it always reads the first, and the first is where the whole
    difference from `comment_on_ticket` lives."""
    first_line = (tools["add_private_note"].description or "").strip().splitlines()[0]

    assert first_line.startswith("WRITES TO EBTEQDESK")
    assert "INTERNAL" in first_line
    assert "NOT emailed" in first_line


async def test_the_pair_of_ticket_writes_cannot_be_confused(tools) -> None:
    """🔴 THE ASSERTION THIS FILE EXISTS FOR.

    Two adjacent write tools, one of which emails the requester. Each description
    must state its own behaviour AND point at the other, and neither may make
    the other's claim. Asserted together, because the failure mode is the two
    texts converging — which no single-tool assertion can see.
    """
    note = described(tools["add_private_note"])
    comment = described(tools["comment_on_ticket"])

    # The note says what it is, and names its sibling.
    assert "SAFE COUNTERPART TO `comment_on_ticket`" in note
    assert "only agents see" in note
    assert 'kind: "note"' in note

    # The comment still says it reaches the requester. If this ever stops being
    # asserted, the two tools become interchangeable to a reader.
    assert "PUBLIC reply the requester receives" in comment
    assert "not an internal note" in comment

    # And the note tool does NOT claim to be requester-facing anywhere.
    assert "PUBLIC reply" not in note


async def test_it_says_the_note_cannot_be_removed(tools) -> None:
    """"Internal" reads as "low stakes" and therefore as "reversible". It is
    not: there is no delete-comment tool on this API at all."""
    description = described(tools["add_private_note"])

    assert "INTERNAL IS NOT REVERSIBLE" in description
    assert "no delete-comment tool" in description
    assert "never retry" in description.lower()


async def test_it_states_the_null_id_case(tools) -> None:
    description = described(tools["add_private_note"])

    assert "CAN BE null, AND THAT MEANS NOTHING WAS FILED" in description
    assert "do not report it as saved" in description


async def test_it_states_the_escalated_scope_requirement(tools) -> None:
    """The failure this closes: a user reads "needs ticket:write", is refused
    for escalation:write, and re-mints with ticket:write again."""
    description = described(tools["add_private_note"])

    assert "NOTING ON AN ESCALATED TICKET NEEDS `escalation:write`" in description
    # …and that "escalated" outlives resolution, which is what turns this from
    # a scope note into a lockout a user has to be warned about.
    assert "stays true after the ticket is solved" in description
    assert "Check the ticket's `escalated` field before you call" in description
    assert "re-minting with the same scope changes nothing" in description
    assert "`bp_escalation.reply`" in description

    # …and it gives the HONEST reason, not the reply endpoint's.
    assert "not because your note would be downgraded" in description.lower()


async def test_it_names_its_scope_and_ability(tools) -> None:
    """The 403 names a scope or an ability; the description has to name the same
    strings or a user cannot map a refusal back to a tool."""
    description = described(tools["add_private_note"])

    assert "`ticket:write`" in description
    assert "`ticket.reply`" in description
    assert "`escalation:write`" in description
    # The wider-visibility path costs a READ scope on a write tool, which is
    # surprising enough that it has to be stated.
    assert "`escalation:read`" in description


async def test_it_warns_that_it_reaches_further_than_the_other_ticket_writes(
    tools,
) -> None:
    """⚠️ It can write to a ticket `list_tickets` never shows. That is the point
    (a BP reviewer records a finding) and it is also the thing a model should not
    do casually.

    🔴 THE REACH IS "ANY ESCALATED TICKET", NOT "THE SHARED QUEUE", and the
    difference is now load-bearing rather than pedantic: the queue
    (`list_escalations`) drops a ticket once it is resolved, while the escalation
    STATE lasts until de-escalation. Describing the reach as the queue told a
    model that a resolved escalation was out of reach when it is not — and, the
    other way round, that everything in reach would show up in
    `list_escalations`."""
    description = described(tools["add_private_note"])

    assert "ANY escalated ticket" in description
    assert "assigned to somebody else" in description
    assert "`list_tickets` never shows" in description
    # …and the resolved case specifically, which is the half a queue-shaped
    # description gets wrong.
    assert "`list_escalations` no longer shows" in description
    assert "do not use it to leave notes on other people's tickets" in description


async def test_a_tool_call_round_trips(wired) -> None:
    """Through `call_tool`, not the bare function — the SDK's own dispatch is
    what a client exercises, and it is where the output schema is applied."""
    wired(CREATED)

    result = await srv.mcp.call_tool(
        "add_private_note", {"ticket_id": 42, "body": "Internal."}
    )

    assert not result.is_error
    assert result.structured_content == ticket_payload()


async def test_a_scope_refusal_reaches_the_client_as_readable_text(wired) -> None:
    """`call_tool` raises `ToolError`, and `str()` of it is verbatim the sentence
    a user reads in their MCP client — which is why the narrowing is asserted
    here and not only at the client layer. A traceback is not something a chat
    client can act on."""
    wired(scope_refusal("escalation:write"))

    with pytest.raises(ToolError) as raised:
        await srv.mcp.call_tool("add_private_note", {"ticket_id": 42, "body": "x"})

    message = str(raised.value)

    assert "THIS TICKET IS ESCALATED" in message
    assert "downgraded" not in message.replace("would be downgraded", "")
    # No traceback and no filesystem paths reach the user.
    assert "Traceback" not in message
    assert "ebteqdesk_mcp/" not in message
