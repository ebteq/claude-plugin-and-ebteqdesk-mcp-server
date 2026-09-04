"""The ticket WRITE endpoints: verbs, paths, request bodies, and refusals.

Layered like the read half — `test_client.py` for the reads, this for the
writes — and mocked at the same seam, `httpx2.MockTransport`. That matters
more here than it does for a GET: these assertions are about the bytes this
client puts ON THE WIRE, and a double that replaced `_request` would let a
malformed body pass every test in this file.

Two things are asserted here that have no equivalent on the read side:

  1. THE VERB. A create that went out as a GET would still 404 or 405 and still
     produce a plausible-looking error, and nothing else in the suite would
     notice.
  2. HOW MANY REQUESTS WERE MADE. A write is not idempotent — the escalation
     notification is the sharp edge — so "exactly one request left this client"
     is a correctness property, not a performance one.
"""

from __future__ import annotations

import json as jsonlib

import httpx2
import pytest

from conftest import (
    ABILITY_REFUSAL,
    SCOPE_REFUSAL,
    ability_refusal,
    always_json,
    json_response,
    not_assigned_refusal,
    scope_refusal,
    ticket_payload,
)
from ebteqdesk_mcp.errors import (
    AbilityError,
    InvalidRequestError,
    KeyScopeError,
    NotFoundError,
    RateLimitedError,
    RoleScopeError,
    ScopeError,
    TicketNotAssignedError,
)

CREATED = always_json(201, ticket_payload())
OK = always_json(200, ticket_payload())


def body_of(request: httpx2.Request) -> dict:
    return jsonlib.loads(request.content)


# --------------------------------------------------------------------------- #
# Verbs and paths
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "call, expected_method, expected_path",
    [
        (
            lambda c: c.create_ticket(
                subject="Printer on fire",
                description="It is.",
                requester={"email": "ada@example.com"},
            ),
            "POST",
            "/api/v1/tickets",
        ),
        (
            lambda c: c.comment_on_ticket(42, body="Looking into it."),
            "POST",
            "/api/v1/tickets/42/comments",
        ),
        (lambda c: c.escalate_ticket(42), "POST", "/api/v1/tickets/42/escalate"),
        (lambda c: c.de_escalate_ticket(42), "DELETE", "/api/v1/tickets/42/escalate"),
        # 🔴 PUT, not POST — the only write on this client that is not one or
        # the other of POST/DELETE, and the verb is load-bearing: the route is
        # declared PUT, so a POST here matches no route and comes back as HTML
        # this client would report as a proxy problem the user does not have.
        (
            lambda c: c.set_ticket_status(42, status=2),
            "PUT",
            "/api/v1/tickets/42/status",
        ),
        (lambda c: c.close_ticket(42), "POST", "/api/v1/tickets/42/close"),
    ],
)
async def test_each_write_hits_its_documented_verb_and_path(
    make_client, call, expected_method: str, expected_path: str
) -> None:
    """Escalate and de-escalate share a path and differ ONLY in the verb, which
    is precisely the pair a client can get wrong without any error surfacing."""
    client, recorder = make_client(CREATED)

    async with client:
        await call(client)

    assert recorder.last.method == expected_method
    assert recorder.last.url.path == expected_path


async def test_every_write_carries_the_bearer_token(make_client) -> None:
    client, recorder = make_client(CREATED)

    async with client:
        await client.escalate_ticket(42)
        await client.close_ticket(42)

    for request in recorder.requests:
        assert request.headers["authorization"].startswith("Bearer ")
        assert request.headers["accept"] == "application/json"


async def test_exactly_one_request_leaves_the_client_per_write(make_client) -> None:
    """No retries, no preflight, no verification read. A POST that timed out may
    already have landed, so anything automatic here can double-file a ticket or
    double-notify a team."""
    client, recorder = make_client(CREATED)

    async with client:
        await client.escalate_ticket(42)

    assert len(recorder.requests) == 1


# --------------------------------------------------------------------------- #
# Request bodies
# --------------------------------------------------------------------------- #


async def test_create_sends_the_required_fields_under_the_api_s_own_names(
    make_client,
) -> None:
    client, recorder = make_client(CREATED)

    async with client:
        await client.create_ticket(
            subject="Printer on fire",
            description="It really is.",
            requester={"id": 7},
        )

    assert body_of(recorder.last) == {
        "subject": "Printer on fire",
        # `description`, not `body` — the create endpoint's field name differs
        # from the comment endpoint's, and the API's names are the contract.
        "description": "It really is.",
        "requester": {"id": 7},
    }


async def test_create_omits_optional_fields_rather_than_sending_null(
    make_client,
) -> None:
    """The server documents defaults for all five. Sending explicit nulls would
    work, but it would also put a second copy of "the default is normal
    priority" in this client, to drift the day the server's changes."""
    client, recorder = make_client(CREATED)

    async with client:
        await client.create_ticket(
            subject="Printer on fire",
            description="It is.",
            requester={"email": "ada@example.com"},
        )

    assert set(body_of(recorder.last)) == {"subject", "description", "requester"}


async def test_create_sends_every_optional_field_when_given(make_client) -> None:
    client, recorder = make_client(CREATED)

    async with client:
        await client.create_ticket(
            subject="Printer on fire",
            description="It is.",
            requester={"email": "ada@example.com", "name": "Ada"},
            priority=3,
            status=2,
            category="bp-task",
            reference_number="INC-9",
            tags=["hardware", "urgent"],
        )

    assert body_of(recorder.last) == {
        "subject": "Printer on fire",
        "description": "It is.",
        "requester": {"email": "ada@example.com", "name": "Ada"},
        "priority": 3,
        "status": 2,
        "category": "bp-task",
        "reference_number": "INC-9",
        "tags": ["hardware", "urgent"],
    }


async def test_the_requester_object_is_passed_through_with_its_shape_intact(
    make_client,
) -> None:
    """Not flattened into requester_id / requester_email. The nested object is
    what the endpoint documents and what a curl of it looks like."""
    client, recorder = make_client(CREATED)

    async with client:
        await client.create_ticket(
            subject="Printer on fire",
            description="It is.",
            requester={"email": "ada@example.com", "name": "Ada Lovelace"},
        )

    assert body_of(recorder.last)["requester"] == {
        "email": "ada@example.com",
        "name": "Ada Lovelace",
    }


async def test_an_unusable_requester_is_sent_and_let_the_server_judge_it(
    make_client,
) -> None:
    """An empty requester is a 422 that names the field. Refusing it locally
    would invent a second, differently-worded rule and make this client
    disagree with curl — the same reasoning as `per_page` on the read half."""
    client, recorder = make_client(
        always_json(
            422,
            {
                "error": "The given data was invalid.",
                "errors": {"requester.id": ["The requester.id field is required when requester.email is not present."]},
            },
        )
    )

    async with client:
        with pytest.raises(InvalidRequestError):
            await client.create_ticket(
                subject="Printer on fire", description="It is.", requester={}
            )

    assert body_of(recorder.last)["requester"] == {}


async def test_comment_sends_the_body_under_the_key_body(make_client) -> None:
    client, recorder = make_client(CREATED)

    async with client:
        await client.comment_on_ticket(42, body="We have ordered a new one.")

    assert body_of(recorder.last) == {"body": "We have ordered a new one."}


async def test_set_ticket_status_sends_the_status_under_the_key_status(
    make_client,
) -> None:
    client, recorder = make_client(OK)

    async with client:
        await client.set_ticket_status(42, status=8)

    assert body_of(recorder.last) == {"status": 8}


async def test_a_resolving_status_is_passed_through_for_the_server_to_refuse(
    make_client,
) -> None:
    """No client-side value validation, following `close_ticket`'s stated rule
    that the server's list stays the only list. The MCP tool one layer up
    carries the `Literal` that refuses 4 before a request is made; this class
    does not keep a second copy of it, so a curl and this client agree."""
    client, recorder = make_client(
        always_json(
            422,
            {
                "error": "The given data was invalid.",
                "errors": {"status": ["This endpoint moves a ticket between WORKING states only."]},
            },
        )
    )

    async with client:
        with pytest.raises(InvalidRequestError) as excinfo:
            await client.set_ticket_status(42, status=4)

    assert body_of(recorder.last) == {"status": 4}
    assert "status" in excinfo.value.field_errors


async def test_close_defaults_to_the_server_s_status_by_omitting_it(
    make_client,
) -> None:
    client, recorder = make_client(OK)

    async with client:
        await client.close_ticket(42)

    assert body_of(recorder.last) == {}


async def test_close_sends_an_explicit_status_when_given(make_client) -> None:
    client, recorder = make_client(OK)

    async with client:
        await client.close_ticket(42, status=5)

    assert body_of(recorder.last) == {"status": 5}


async def test_an_out_of_range_close_status_is_passed_through_not_clamped(
    make_client,
) -> None:
    client, recorder = make_client(
        always_json(
            422,
            {
                "error": "The given data was invalid.",
                "errors": {"status": ["The selected status is invalid."]},
            },
        )
    )

    async with client:
        with pytest.raises(InvalidRequestError) as excinfo:
            await client.close_ticket(42, status=7)

    assert body_of(recorder.last) == {"status": 7}
    assert "status" in excinfo.value.field_errors


async def test_the_escalation_endpoints_send_no_body_at_all(make_client) -> None:
    """They document none. Inventing `{}` would be this client asserting a
    request shape the API never described."""
    client, recorder = make_client(OK)

    async with client:
        await client.escalate_ticket(42)
        assert recorder.last.content == b""

        await client.de_escalate_ticket(42)
        assert recorder.last.content == b""


# --------------------------------------------------------------------------- #
# Ticket ids
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", ["bp-task", "", "42", 0, -1, None, 4.5, True])
async def test_a_ticket_id_that_is_not_a_positive_int_is_refused_before_sending(
    make_client, bad
) -> None:
    """The write routes carry whereNumber, so a non-numeric id matches NO route
    and Laravel answers HTML — which this client would otherwise report as
    "answered with text/html instead of JSON", a true sentence about a proxy
    problem the user does not have.

    `True` is in the list on purpose: it is an `int` in Python and would
    silently address ticket 1, a real ticket with a real requester on it.
    """
    client, recorder = make_client(OK)

    async with client:
        with pytest.raises(ValueError, match="positive integer ticket id"):
            await client.close_ticket(bad)

    assert recorder.requests == []


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.comment_on_ticket("x", body="hi"),
        lambda c: c.escalate_ticket("x"),
        lambda c: c.de_escalate_ticket("x"),
        lambda c: c.set_ticket_status("x", status=2),
        lambda c: c.close_ticket("x"),
    ],
)
async def test_every_ticket_scoped_write_validates_its_id(make_client, call) -> None:
    client, recorder = make_client(OK)

    async with client:
        with pytest.raises(ValueError):
            await call(client)

    assert recorder.requests == []


async def test_an_unknown_ticket_is_a_404_that_names_the_id(make_client) -> None:
    """The 404 still covers BOTH "no such ticket" and "not yours", and this
    client must not try to tell them apart from the 404 itself.

    Since #135 the server carves out one case and answers it 403 instead — a
    ticket on the SHARED escalation queue, to a caller who could have read that
    queue. That is a different status with a `reason` field, handled below; it
    does not make the 404 any more informative than it was.
    """
    client, _ = make_client(
        always_json(404, {"error": 'There is no ticket with the id "999".'})
    )

    async with client:
        with pytest.raises(NotFoundError) as excinfo:
            await client.escalate_ticket(999)

    assert "999" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Payload passthrough
# --------------------------------------------------------------------------- #


async def test_a_created_ticket_is_returned_verbatim(make_client) -> None:
    """The created ticket is the same shape as an element of list_tickets, which
    is the property that stops a client needing two parsers. Nothing is renamed
    on the way out — `requester` in particular."""
    payload = ticket_payload(id=101, subject="New from API")
    client, _ = make_client(always_json(201, payload))

    async with client:
        result = await client.create_ticket(
            subject="New from API",
            description="…",
            requester={"email": "ada@example.com"},
        )

    assert result == payload
    assert "customer_contact" not in result["data"]


async def test_the_comment_receipt_is_carried_beside_the_ticket(make_client) -> None:
    payload = {
        **ticket_payload(),
        "comment": {"id": 500, "created_at": "2026-08-12T05:10:00+00:00"},
    }
    client, _ = make_client(always_json(201, payload))

    async with client:
        result = await client.comment_on_ticket(42, body="hi")

    assert result == payload


async def test_a_null_comment_id_survives_untouched(make_client) -> None:
    """A body identical to the author's signature is discarded by the model: 201
    with no row written. This client must not smooth that into a fake id, or
    drop the key — the null IS the information."""
    payload = {**ticket_payload(), "comment": {"id": None, "created_at": None}}
    client, _ = make_client(always_json(201, payload))

    async with client:
        result = await client.comment_on_ticket(42, body="-- Admin")

    assert result["comment"] == {"id": None, "created_at": None}


async def test_a_201_is_a_success_not_an_unexpected_status(make_client) -> None:
    """The read half only ever sees 200. A transport that treated anything but
    200 as a failure would reject every create."""
    client, _ = make_client(always_json(201, ticket_payload()))

    async with client:
        assert (await client.create_ticket(
            subject="x" * 5, description="…", requester={"id": 1}
        ))["data"]["id"] == 42


# --------------------------------------------------------------------------- #
# The third 403: required_ability
# --------------------------------------------------------------------------- #
#
# A scope refusal is about the CREDENTIAL and has two possible causes needing
# opposite fixes. An ability refusal is about the ACCOUNT, has one cause, and
# has one fix. Conflating them costs the user a round of key minting that cannot
# possibly work — the same failure RoleScopeError exists to prevent, arriving
# through a different door.


async def test_an_ability_refusal_is_its_own_type(make_client) -> None:
    client, _ = make_client(ability_refusal("ticket.close"))

    async with client:
        with pytest.raises(AbilityError) as excinfo:
            await client.close_ticket(42)

    error = excinfo.value
    assert error.status_code == 403
    assert error.required_ability == "ticket.close"
    # And it is NOT a scope error, in either direction.
    assert error.required_scope is None
    assert not isinstance(error, ScopeError)


async def test_an_ability_refusal_names_the_ability_and_the_one_remedy(
    make_client,
) -> None:
    client, _ = make_client(ability_refusal("bp_escalation.reply"))

    async with client:
        with pytest.raises(AbilityError) as excinfo:
            await client.comment_on_ticket(42, body="hi")

    message = str(excinfo.value)

    assert "`bp_escalation.reply`" in message
    assert "administrator" in message.lower()
    assert "NOT A KEY OR SCOPE PROBLEM" in message
    # It must rule minting out explicitly rather than just not mentioning it.
    assert "mint a NEW key" not in message
    assert "Minting a new key will not help" in message
    # And the server's own sentence is carried through, as everywhere else.
    assert ABILITY_REFUSAL.format(ability="bp_escalation.reply") in message


async def test_an_ability_refusal_spends_one_diagnostic_request(make_client) -> None:
    """🔴 INVERTED (N1). It used to assert the OPPOSITE and the reasoning was
    wrong.

    The old docblock said "there is nothing to diagnose... a second request
    would spend a round trip to learn nothing". The key/role question is indeed
    already answered — but that is not the only question. An ability refusal has
    two causes needing OPPOSITE remedies:

        the role does not hold the ability -> maybe a grant, maybe the role's
                                              nature; for a developer account
                                              refused `ticket.*` no grant is
                                              coming
        the role DOES hold it              -> this TICKET was refused; a grant
                                              changes nothing

    `permissions` from GET /api/v1/user separates them, and the caller most
    likely to hit this has no browser to fall back on — /api/v1 is the developer
    role's primary working surface. "Ask an administrator" told that user to go
    and request something they should not be given.

    ⚠️ THE COST IS REAL AND IS THE REASON THIS TEST STILL COUNTS REQUESTS: a
    refused call is now TWO requests against the 60/minute account throttle,
    exactly as a refused SCOPE call already was. A client looping over ids it
    cannot touch reaches the limit in half the calls it expects.
    """
    client, recorder = make_client(ability_refusal("ticket.create"))

    async with client:
        with pytest.raises(AbilityError):
            await client.create_ticket(
                subject="Printer on fire", description="…", requester={"id": 1}
            )

    assert recorder.paths == ["/api/v1/tickets", "/api/v1/user"]


async def test_a_403_with_both_fields_is_treated_as_the_scope_refusal(
    make_client,
) -> None:
    """The server emits one or the other, never both, so this is unobservable
    today. It is pinned anyway and in this direction: the scope gate is the
    OUTER one, so a body carrying both would mean the request never got past
    the credential, and reporting the inner ability check would send the user
    to an administrator for a key problem."""
    client, _ = make_client(
        scope_refusal("ticket:write", requested=[], scopes=[])
    )

    async with client:
        with pytest.raises(ScopeError):
            await client.close_ticket(42)

    hybrid, _ = make_client(
        always_json(
            403,
            {
                "error": "…",
                "required_scope": "ticket:write",
                "required_ability": "ticket.close",
            },
        )
    )

    async with hybrid:
        with pytest.raises(ScopeError) as excinfo:
            await hybrid.close_ticket(42)

    # Still a ScopeError, and the ability is carried rather than discarded.
    assert excinfo.value.required_scope == "ticket:write"
    assert excinfo.value.required_ability == "ticket.close"


# --------------------------------------------------------------------------- #
# The scope diagnosis, on the write half
# --------------------------------------------------------------------------- #


async def test_a_write_scope_the_key_never_carried_is_a_key_problem(
    make_client,
) -> None:
    """The machinery built for the read half has to cover the writes too — the
    scopes are simply different strings."""
    client, recorder = make_client(
        scope_refusal(
            "ticket:write",
            requested=["ticket:read"],
            scopes=["ticket:read"],
        )
    )

    async with client:
        with pytest.raises(KeyScopeError) as excinfo:
            await client.create_ticket(
                subject="Printer on fire", description="…", requester={"id": 1}
            )

    assert excinfo.value.diagnosis == "key"
    assert "mint a NEW key" in str(excinfo.value)
    assert recorder.paths == ["/api/v1/tickets", "/api/v1/user"]


async def test_a_write_scope_the_role_no_longer_backs_is_a_role_problem(
    make_client,
) -> None:
    client, recorder = make_client(
        scope_refusal(
            "escalation:reply",
            requested=["escalation:reply"],
            scopes=[],
        )
    )

    async with client:
        with pytest.raises(RoleScopeError) as excinfo:
            await client.escalate_ticket(42)

    assert excinfo.value.diagnosis == "role"
    assert "administrator" in str(excinfo.value).lower()
    assert "mint a NEW key" not in str(excinfo.value)
    assert recorder.paths == ["/api/v1/tickets/42/escalate", "/api/v1/user"]


async def test_the_write_diagnostic_still_never_masks_the_original(
    make_client,
) -> None:
    from ebteqdesk_mcp.client import USER_PATH

    def handler(request):
        if request.url.path == USER_PATH:
            return json_response(429, {"error": "Too Many Attempts."})
        return json_response(
            403,
            {
                "error": SCOPE_REFUSAL.format(scope="ticket:write"),
                "required_scope": "ticket:write",
            },
        )

    client, _ = make_client(handler)

    async with client:
        with pytest.raises(ScopeError) as excinfo:
            await client.close_ticket(42)

    error = excinfo.value
    assert type(error) is ScopeError
    assert error.diagnosis is None
    assert error.status_code == 403
    assert not isinstance(error, RateLimitedError)


# --------------------------------------------------------------------------- #
# Commenting on an escalated ticket
# --------------------------------------------------------------------------- #
#
# The comment route declares `apiScope:ticket:write`. On an escalated ticket the
# controller ALSO demands `escalation:reply` — a requirement the route cannot
# declare, because middleware runs before the ticket is loaded. So on this one
# path, a refusal naming `escalation:reply` means the ticket is escalated and
# can mean nothing else, and that is the only way a client can learn it: no
# /api/v1 payload carries a ticket's escalation state.


async def test_an_escalated_comment_refusal_says_the_ticket_is_escalated(
    make_client,
) -> None:
    client, _ = make_client(
        scope_refusal(
            "escalation:reply",
            requested=["ticket:write"],
            scopes=["ticket:write"],
        )
    )

    async with client:
        with pytest.raises(ScopeError) as excinfo:
            await client.comment_on_ticket(42, body="Any update?")

    message = str(excinfo.value)

    assert "THIS TICKET IS ESCALATED" in message
    # INSTEAD OF, not IN ADDITION TO — see the note on the same assertion in
    # test_private_notes.py. The comments route takes either scope and charges
    # the escalation one on an escalated ticket.
    # ⚠️ `reply`, not `write`: the comment endpoint sends a message the REQUESTER
    # receives, and that half was split out of `escalation:write` into its own
    # scope. The note endpoint still narrows on `escalation:write`.
    assert "`escalation:reply` scope INSTEAD OF `ticket:write`" in message
    # The reason, so the user does not read it as an arbitrary gate.
    # ⚠️ THE REASON CHANGED WITH THE SCOPE SPLIT, and the old one is gone on
    # purpose. It used to say the refusal existed to stop Ebteqdesk silently
    # DOWNGRADING the reply into a private note. That is still true of
    # Ticket::addComment(), but it is no longer WHY the API refuses: since
    # `escalation:reply` was split out of `escalation:write`, the refusal is
    # the requester handoff — on escalation the requester conversation moves to
    # whoever is working the escalation. Printing the downgrade explanation now
    # would send the reader looking for a data-loss bug that is not there.
    assert "HANDED OVER" in message
    assert "de-escalate" in message
    # …and it must say the sibling scope is not a substitute, or the reader
    # re-mints with `escalation:write` and hits the identical wall.
    assert "`escalation:write` is NOT a substitute" in message
    # 🔴 The failure this closes: re-minting with the scope they already have.
    # ⚠️ The "re-minting will not help" sentence moved into the sibling-scope
    # warning when the reason changed: the reader's wrong next move is no longer
    # re-minting the SAME scope, it is re-minting `escalation:write` — the other
    # half of the split — and being refused identically.
    assert "de-escalate the ticket first" in message


async def test_the_escalated_explanation_does_not_replace_the_diagnosis(
    make_client,
) -> None:
    """Context, not a fourth diagnosis. The key/role answer is still needed —
    `escalation:reply` can be missing from the key OR unbacked by the role, and
    those still need opposite fixes — so the two must compose rather than one
    overwrite the other."""
    client, _ = make_client(
        scope_refusal(
            "escalation:reply",
            requested=["ticket:write"],
            scopes=["ticket:write"],
        )
    )

    async with client:
        with pytest.raises(KeyScopeError) as excinfo:
            await client.comment_on_ticket(42, body="hi")

    error = excinfo.value
    message = str(error)

    # The class and the diagnosis survive the wrapping.
    assert error.diagnosis == "key"
    assert error.required_scope == "escalation:reply"
    # Both halves of the message are present, escalation context first.
    assert message.index("THIS TICKET IS ESCALATED") < message.index("mint a NEW key")
    assert "apiKey.requested" in message
    # And the server's verbatim sentence is still at the end of it.
    assert SCOPE_REFUSAL.format(scope="escalation:reply") in message


async def test_the_escalated_explanation_composes_with_the_role_diagnosis(
    make_client,
) -> None:
    client, _ = make_client(
        scope_refusal(
            "escalation:reply",
            requested=["ticket:write", "escalation:reply"],
            scopes=["ticket:write"],
        )
    )

    async with client:
        with pytest.raises(RoleScopeError) as excinfo:
            await client.comment_on_ticket(42, body="hi")

    message = str(excinfo.value)

    assert excinfo.value.diagnosis == "role"
    assert "THIS TICKET IS ESCALATED" in message
    assert "administrator" in message.lower()
    assert "mint a NEW key" not in message


async def test_an_undiagnosable_escalated_refusal_is_still_explained(
    make_client,
) -> None:
    """The escalation context comes from the endpoint that was called, not from
    the identity call, so it survives the diagnostic failing."""
    from ebteqdesk_mcp.client import USER_PATH

    def handler(request):
        if request.url.path == USER_PATH:
            raise httpx2.ConnectError("gone")
        return json_response(
            403,
            {
                "error": SCOPE_REFUSAL.format(scope="escalation:reply"),
                "required_scope": "escalation:reply",
            },
        )

    client, _ = make_client(handler)

    async with client:
        with pytest.raises(ScopeError) as excinfo:
            await client.comment_on_ticket(42, body="hi")

    assert excinfo.value.diagnosis is None
    assert "THIS TICKET IS ESCALATED" in str(excinfo.value)


async def test_a_plain_ticket_write_refusal_on_comment_is_not_dressed_up(
    make_client,
) -> None:
    """🔴 The guard on the branch. `ticket:write` is refused by the ROUTE
    middleware, which knows nothing about the ticket, so that refusal says
    nothing about escalation and must not claim to."""
    client, _ = make_client(
        scope_refusal("ticket:write", requested=[], scopes=[])
    )

    async with client:
        with pytest.raises(ScopeError) as excinfo:
            await client.comment_on_ticket(42, body="hi")

    assert "ESCALATED" not in str(excinfo.value)


async def test_the_escalated_explanation_is_confined_to_the_comment_endpoint(
    make_client,
) -> None:
    """`escalation:write` is the DECLARED scope of the escalate routes, where a
    refusal means the ordinary thing and nothing about the ticket's state. Only
    the comment endpoint, whose declared scope is `ticket:write`, can learn
    anything from being refused for it."""
    client, _ = make_client(
        scope_refusal("escalation:write", requested=[], scopes=[])
    )

    async with client:
        with pytest.raises(ScopeError) as excinfo:
            await client.escalate_ticket(42)

    assert "ESCALATED" not in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


async def test_a_422_surfaces_every_field_and_its_reason(make_client) -> None:
    """A create can fail on several fields at once, and the message has to carry
    all of them — a model that fixes one and retries burns a request per field
    otherwise."""
    client, _ = make_client(
        always_json(
            422,
            {
                "error": "The given data was invalid.",
                "errors": {
                    "subject": ["The subject must be at least 3 characters."],
                    "description": ["The description field is required."],
                    "requester.email": ["The requester.email must be a valid email address."],
                },
            },
        )
    )

    async with client:
        with pytest.raises(InvalidRequestError) as excinfo:
            await client.create_ticket(
                subject="ab", description="", requester={"email": "nope"}
            )

    error = excinfo.value
    message = str(error)

    assert set(error.field_errors) == {"subject", "description", "requester.email"}
    for fragment in (
        "subject",
        "at least 3 characters",
        "description field is required",
        "requester.email",
        "valid email address",
    ):
        assert fragment in message


async def test_an_unknown_category_slug_is_a_field_error_not_a_silent_default(
    make_client,
) -> None:
    """The server refuses rather than filing the ticket uncategorised, and this
    client must surface that as the field error it is."""
    client, _ = make_client(
        always_json(
            422,
            {
                "error": "The given data was invalid.",
                "errors": {"category": ['There is no ticket category with the slug "nope".']},
            },
        )
    )

    async with client:
        with pytest.raises(InvalidRequestError) as excinfo:
            await client.create_ticket(
                subject="Printer on fire",
                description="…",
                requester={"id": 1},
                category="nope",
            )

    assert "category" in excinfo.value.field_errors
    assert "nope" in str(excinfo.value)


async def test_a_rate_limited_write_is_not_retried(make_client) -> None:
    """Doubly important on this half: a retry that races the throttle can file
    the same ticket twice."""
    client, recorder = make_client(always_json(429, {"error": "Too Many Attempts."}))

    async with client:
        with pytest.raises(RateLimitedError):
            await client.comment_on_ticket(42, body="hi")

    assert len(recorder.requests) == 1


# --------------------------------------------------------------------------- #
# The ownership 403 (#135)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.comment_on_ticket(4, body="x"),
        lambda c: c.escalate_ticket(4),
        lambda c: c.de_escalate_ticket(4),
        lambda c: c.set_ticket_status(4, status=2),
        lambda c: c.close_ticket(4),
    ],
)
async def test_every_ticket_scoped_write_maps_the_ownership_403(make_client, call) -> None:
    """All of them, not just escalate. The server applies the rule on every write
    path that resolves a {ticket}, and a client that recognised it on one of them
    would report the other three as an unfixable permission failure."""
    client, recorder = make_client(not_assigned_refusal(4))

    async with client:
        with pytest.raises(TicketNotAssignedError):
            await call(client)

    # Still exactly one request: there is nothing to diagnose, so the identity
    # endpoint must not be consulted.
    assert len(recorder.requests) == 1


async def test_the_ownership_403_on_a_comment_is_not_rewritten_as_an_escalation_scope(
    make_client,
) -> None:
    """`comment_on_ticket` passes its ScopeErrors through
    `escalated_comment_error`, which prepends "THIS TICKET IS ESCALATED". This
    403 is not a ScopeError, so that hook must not touch it — an unowned ticket
    told the caller to mint `escalation:write` would be exactly the wrong advice
    on the one refusal no key can fix."""
    client, _ = make_client(not_assigned_refusal(4))

    async with client:
        with pytest.raises(TicketNotAssignedError) as excinfo:
            await client.comment_on_ticket(4, body="x")

    text = str(excinfo.value)

    assert "THIS TICKET IS ESCALATED" not in text
    assert "escalation:write" not in text
