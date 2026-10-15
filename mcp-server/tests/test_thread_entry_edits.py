"""The four thread-entry writes (5.3.0) — editing and deleting a reply or a note.

    edit_ticket_comment    PATCH  /api/v1/tickets/{id}/comments/{comment_id}
    delete_ticket_comment  DELETE /api/v1/tickets/{id}/comments/{comment_id}
    edit_private_note      PATCH  /api/v1/tickets/{id}/notes/{note_id}
    delete_private_note    DELETE /api/v1/tickets/{id}/notes/{note_id}

Mocked at the socket (`httpx2.MockTransport`), like every other write test here.
The contract is the desk's `Api\\V1\\TicketThreadEntriesController` on its
`feature/comment-edit-delete` branch, and the bodies below are copied from that
controller and its feature test (`TicketThreadEntryEditsTest`), not invented.

---------------------------------------------------------------------------
Why the DESCRIPTION assertions are the important half of this file — again
---------------------------------------------------------------------------
The transport is four requests. The risk is a model reading "edit" or "delete"
as "undo" — telling a user their wrong reply was "taken back" when the
requester may have read it on their ticket page since it was posted — or
sending a two-word
"correction" that replaces a whole reply, or deleting a colleague's note
because the id was nearby. Every one of those is a sentence in a description,
so the sentences are asserted, and the ones that USED to be true elsewhere
("no delete-comment tool") are asserted gone.
"""

from __future__ import annotations

import json as jsonlib

import httpx2
import pytest

from conftest import (
    ABILITY_REFUSAL,
    ability_refusal,
    always_json,
    identity_payload,
    json_response,
    scope_refusal,
    thread_entry,
    ticket_detail,
    ticket_list,
)
from mcp.server.mcpserver.exceptions import ToolError

from ebteqdesk_mcp import server as srv
from ebteqdesk_mcp.client import USER_PATH, EbteqdeskClient
from ebteqdesk_mcp.config import Config
from ebteqdesk_mcp.errors import (
    AbilityError,
    InvalidRequestError,
    KeyScopeError,
    NotAgentAuthoredError,
    NotFoundError,
    PermissionError_,
    ScopeError,
    StaleAttachmentApiError,
    StaleThreadEntryApiError,
    api_error_for,
    escalated_comment_edit_error,
    escalated_note_edit_error,
    thread_entry_kind,
)

KEY = srv.UNTRUSTED_CONTENT_KEY
NOTICE = srv.UNTRUSTED_CONTENT_NOTICE_KEY

#: The four tools, each with the path segment it must hit and its id argument.
TOOLS = {
    "edit_ticket_comment": ("PATCH", "comments", "comment_id"),
    "delete_ticket_comment": ("DELETE", "comments", "comment_id"),
    "edit_private_note": ("PATCH", "notes", "note_id"),
    "delete_private_note": ("DELETE", "notes", "note_id"),
}
REPLY_TOOLS = ("edit_ticket_comment", "delete_ticket_comment")
NOTE_TOOLS = ("edit_private_note", "delete_private_note")
EDIT_TOOLS = ("edit_ticket_comment", "edit_private_note")
DELETE_TOOLS = ("delete_ticket_comment", "delete_private_note")

#: The server's own sentences, verbatim from TicketThreadEntriesController.
NOT_AGENT_AUTHORED = {
    "error": (
        "Entry 812 was not written by an agent: it is the customer's message or an "
        "entry the system wrote, and it cannot be edited or deleted."
    ),
    "reason": "not_agent_authored",
}
NO_REPLY = {"error": 'There is no public reply with the id "815" on this ticket.'}
NO_NOTE = {"error": 'There is no private note with the id "812" on this ticket.'}
NO_TICKET = {"error": 'There is no ticket with the id "42".'}
#: What Laravel answers for a route that does not exist — no `error` key. This is
#: an older desk meeting this client.
NO_ROUTE = {"message": "The route api/v1/tickets/42/comments/812 could not be found."}


def edited(kind: str = "comment", body: str = "<p>Corrected: Friday.</p>") -> dict:
    """PATCH's 200 — ONE element of GET …/comments, re-read after the save."""
    return {"data": thread_entry(kind, body, id=812, body_html=body, author="Bo")}


def receipt(kind: str = "comment", entry_id: int = 812) -> dict:
    """DELETE's 200 — a receipt for a row that no longer exists."""
    return {"data": {"id": entry_id, "kind": kind, "deleted": True}}


def body_of(request: httpx2.Request) -> dict:
    return jsonlib.loads(request.content)


def described(tool) -> str:
    """A description with its hard wrapping collapsed — see
    test_server_tools.described()."""
    return " ".join((tool.description or "").split())


async def call_client(client: EbteqdeskClient, name: str, *, ticket_id=42, entry_id=812, body="x"):
    """Call one of the four client methods by tool name."""
    method = getattr(client, name)
    if name in EDIT_TOOLS:
        return await method(ticket_id, entry_id, body=body)
    return await method(ticket_id, entry_id)


def arguments(name: str, *, ticket_id=42, entry_id=812, body="x") -> dict:
    """The MCP arguments for one of the four tools."""
    args = {"ticket_id": ticket_id, TOOLS[name][2]: entry_id}
    if name in EDIT_TOOLS:
        args["body"] = body
    return args


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
# Transport — the verb, the path, the body, and nothing else
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", list(TOOLS))
async def test_each_method_hits_its_own_verb_and_path(make_client, name: str) -> None:
    """🔴 THE PATH IS THE KIND. `/comments/` is a public reply and `/notes/` a
    private note, and the desk addresses each on its own route — so a reply
    tool that reached `/notes/` (or the reverse) would be acting on the wrong
    kind of entry. Asserted on the wire, not trusted from the method name."""
    verb, segment, _ = TOOLS[name]
    kind = "comment" if segment == "comments" else "note"
    answer = edited(kind) if verb == "PATCH" else receipt(kind)
    client, recorder = make_client(always_json(200, answer))

    async with client:
        await call_client(client, name)

    assert recorder.last.method == verb
    assert recorder.last.url.path == f"/api/v1/tickets/42/{segment}/812"
    # Exactly one request. A write is never retried here.
    assert len(recorder.requests) == 1


@pytest.mark.parametrize("name", EDIT_TOOLS)
async def test_an_edit_sends_the_body_and_nothing_else(make_client, name: str) -> None:
    """`{"body": …}` and no `private`, no `kind`, no `append`: the desk's rule is
    the create rule, and a client that added keys would be inventing a request
    shape — the day the desk grew a real `private` key, an invented one would
    start meaning something."""
    client, recorder = make_client(always_json(200, edited()))

    async with client:
        await call_client(client, name, body="<p>Corrected: <strong>Friday</strong>.</p>")

    assert body_of(recorder.last) == {"body": "<p>Corrected: <strong>Friday</strong>.</p>"}


@pytest.mark.parametrize("name", DELETE_TOOLS)
async def test_a_delete_sends_no_body_at_all(make_client, name: str) -> None:
    """No body and no `Content-Type`, not even `{}` — the escalate DELETE's rule.
    A delete has nothing to send."""
    client, recorder = make_client(always_json(200, receipt()))

    async with client:
        await call_client(client, name)

    assert not recorder.last.content
    assert "content-type" not in recorder.last.headers


@pytest.mark.parametrize("name", list(TOOLS))
async def test_the_payload_is_returned_verbatim(make_client, name: str) -> None:
    """The client half passes the desk's bytes through, as everywhere else. The
    envelope is the MCP layer's, asserted further down."""
    answer = edited() if name in EDIT_TOOLS else receipt()
    client, _ = make_client(always_json(200, answer))

    async with client:
        assert await call_client(client, name) == answer


@pytest.mark.parametrize("name", list(TOOLS))
@pytest.mark.parametrize("bad", ["812", 0, -3, True, 8.5, None])
async def test_a_bad_entry_id_is_refused_before_any_request(
    make_client, name: str, bad
) -> None:
    """🔴 REFUSED HERE, AND FOR A REASON THE OTHER IDS DO NOT HAVE. The routes
    carry `whereNumber`, so a word builds a URL that matches no route — and a
    no-route 404 on these paths is exactly what `StaleThreadEntryApiError`
    reads as "this desk predates the feature". `True` is an int in Python and
    would address entry 1."""
    client, recorder = make_client(always_json(200, receipt()))

    async with client:
        with pytest.raises(ValueError) as raised:
            await call_client(client, name, entry_id=bad)

    assert recorder.requests == []
    assert TOOLS[name][2] in str(raised.value)


@pytest.mark.parametrize("name", list(TOOLS))
async def test_the_id_message_says_where_the_id_comes_from(make_client, name: str) -> None:
    client, _ = make_client(always_json(200, receipt()))

    async with client:
        with pytest.raises(ValueError) as raised:
            await call_client(client, name, entry_id="the typo one")

    message = str(raised.value)
    wanted = '`kind: "comment"`' if name in REPLY_TOOLS else '`kind: "note"`'

    assert "`get_ticket`" in message
    assert wanted in message
    assert "`comment.id`" in message


@pytest.mark.parametrize("name", list(TOOLS))
async def test_a_bad_ticket_id_is_refused_before_any_request(make_client, name: str) -> None:
    client, recorder = make_client(always_json(200, receipt()))

    async with client:
        with pytest.raises(ValueError):
            await call_client(client, name, ticket_id="forty-two")

    assert recorder.requests == []


# --------------------------------------------------------------------------- #
# Which path is an entry route
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "path, kind",
    [
        ("/api/v1/tickets/42/comments/812", "comment"),
        ("/api/v1/tickets/42/notes/812", "note"),
        # The list and the two POSTs are NOT entry routes.
        ("/api/v1/tickets/42/comments", None),
        ("/api/v1/tickets/42/notes", None),
        # 🔴 The attachment routes share the `notes/` prefix and must never be
        # read as an entry — their 404 means something else entirely.
        ("/api/v1/tickets/42/notes/attachments", None),
        ("/api/v1/tickets/42/notes/uploads", None),
        ("/api/v1/tickets/42/notes/uploads/9/complete", None),
        ("/api/v1/tickets/abc/comments/812", None),
        ("/api/v1/tickets/42/escalate", None),
        ("/api/v1/kb/folders/7", None),
        ("/api/v1/admin/agents/3/keys/12", None),
    ],
)
def test_thread_entry_kind_recognises_exactly_the_four_routes(path: str, kind) -> None:
    assert thread_entry_kind(path) == kind


# --------------------------------------------------------------------------- #
# The escalated-ticket refusals — narrowed, and about the act that was refused
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", REPLY_TOOLS)
async def test_an_escalation_reply_refusal_on_a_reply_is_narrowed(make_client, name: str) -> None:
    """🔴 On the reply routes a refusal naming `escalation:reply` means the
    ticket is escalated — the desk charges it only inside `if ($escalated)`,
    exactly as on POST …/comments. The sentence names EDITING OR DELETING, not
    replying: a user fixing a typo who is told about replying concludes they
    called the wrong tool."""
    client, _ = make_client(scope_refusal("escalation:reply"))

    async with client:
        with pytest.raises(ScopeError) as raised:
            await call_client(client, name)

    message = str(raised.value)

    assert "THIS TICKET IS ESCALATED" in message
    assert "editing or deleting a public reply" in message
    assert "`escalation:reply` scope INSTEAD OF `ticket:write`" in message
    assert "`escalated` boolean" in message
    # `escalation:write` is named as NOT the fix, and pointed at the notes.
    assert "`escalation:write` is NOT a substitute" in message
    assert "`edit_private_note`" in message
    assert "NOTHING WAS CHANGED" in message
    # Not the POST route's sentence about replying.
    assert "replying to the requester on an escalated ticket" not in message
    # Nothing masked: the diagnosis and the server's sentence survive.
    assert raised.value.required_scope == "escalation:reply"
    assert raised.value.server_message


@pytest.mark.parametrize("name", NOTE_TOOLS)
async def test_an_escalation_write_refusal_on_a_note_is_narrowed(make_client, name: str) -> None:
    """The note routes run the note POST's charge: `escalation:write` on an
    escalated ticket. The reason given is the BP-queue one, never that
    anything reaches the requester — nothing a note edit does reaches anyone."""
    client, _ = make_client(scope_refusal("escalation:write"))

    async with client:
        with pytest.raises(ScopeError) as raised:
            await call_client(client, name)

    message = str(raised.value)

    assert "THIS TICKET IS ESCALATED" in message
    assert "editing or deleting an internal note" in message
    assert "`escalation:write` scope INSTEAD OF `ticket:write`" in message
    assert "belongs to the BP queue" in message
    assert "NOTHING WAS CHANGED" in message
    assert "downgrade" not in message
    assert "filing an internal note on an escalated ticket" not in message
    assert raised.value.required_scope == "escalation:write"


@pytest.mark.parametrize(
    "name, other_scope",
    [(name, "escalation:write") for name in REPLY_TOOLS]
    + [(name, "escalation:reply") for name in NOTE_TOOLS]
    + [(name, "ticket:write") for name in TOOLS],
)
async def test_any_other_scope_refusal_is_left_alone(make_client, name: str, other_scope: str) -> None:
    """🔴 NEITHER NARROWER MATCHES THE OTHER'S SCOPE. A reply route never asks
    for `escalation:write` and a note route never asks for `escalation:reply`;
    if one did, printing "you cannot change a note here" for a refused reply is
    the failure the separate narrowers exist to prevent. And the floor refusal
    (`ticket:write`, the first scope of each any-of group) is the ordinary one."""
    client, _ = make_client(scope_refusal(other_scope))

    async with client:
        with pytest.raises(ScopeError) as raised:
            await call_client(client, name)

    assert "THIS TICKET IS ESCALATED" not in str(raised.value)


def test_the_narrowers_keep_the_diagnosed_class() -> None:
    """A `KeyScopeError` stays a `KeyScopeError`: the escalated ticket is the
    CONTEXT of the refusal, not a fourth diagnosis."""
    for narrow, scope in (
        (escalated_comment_edit_error, "escalation:reply"),
        (escalated_note_edit_error, "escalation:write"),
    ):
        error = KeyScopeError(
            "original",
            status_code=403,
            path="/api/v1/tickets/42/comments/812",
            required_scope=scope,
        )

        narrowed = narrow(error)

        assert type(narrowed) is KeyScopeError
        assert str(narrowed).endswith("original")


@pytest.mark.parametrize("name", list(TOOLS))
async def test_an_ability_refusal_is_not_narrowed(make_client, name: str) -> None:
    """`ticket.reply` / `bp_escalation.reply` refusals are the account's, with
    the opposite advice — never dressed up as the escalation case."""
    client, _ = make_client(ability_refusal("bp_escalation.reply"))

    async with client:
        with pytest.raises(AbilityError) as raised:
            await call_client(client, name)

    assert "THIS TICKET IS ESCALATED" not in str(raised.value)
    assert raised.value.required_ability == "bp_escalation.reply"
    assert ABILITY_REFUSAL.format(ability="bp_escalation.reply") in str(raised.value)


# --------------------------------------------------------------------------- #
# The 403 nobody can fix: the requester's own message, or a system note
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", list(TOOLS))
async def test_not_agent_authored_is_its_own_error_and_costs_one_request(
    make_client, name: str
) -> None:
    """🔴 NO SCOPE, NO ABILITY, NOTHING TO DIAGNOSE. Without its own branch this
    403 fell through to `PermissionError_`, whose message says "ask an
    administrator to grant the ability" — for something no grant on earth
    changes. Exactly one request: there is nothing to look up."""
    client, recorder = make_client(always_json(403, NOT_AGENT_AUTHORED))

    async with client:
        with pytest.raises(NotAgentAuthoredError) as raised:
            await call_client(client, name)

    error = raised.value
    message = str(error)

    assert not isinstance(error, PermissionError_)
    assert error.reason == "not_agent_authored"
    assert error.retriable is False
    assert error.required_scope is None and error.required_ability is None
    assert len(recorder.requests) == 1

    assert "because an AGENT DID NOT WRITE IT" in message
    assert "requester's own message" in message
    assert "NOT A KEY, SCOPE OR ABILITY PROBLEM" in message
    assert "NOTHING WAS CHANGED" in message
    assert "DO NOT RETRY" in message
    # It names the flag that predicts it, rather than an inference from the
    # author's name — the guidance the first cut of 5.3.0 carried.
    assert "`agent_authored: false`" in message
    assert "whose `agent_authored` is `true`" in message
    assert "requester.name" not in message
    # The server's own sentence is carried, never parsed.
    assert NOT_AGENT_AUTHORED["error"] in message
    assert "ask an administrator" not in message.lower()


def test_the_reason_is_compared_as_a_value_not_read_from_the_prose() -> None:
    """A rewritten sentence with the same `reason` is still this error; the same
    sentence without it is not."""
    rewritten = api_error_for(
        status_code=403,
        path="/api/v1/tickets/42/notes/812",
        payload={"error": "Nope.", "reason": "not_agent_authored"},
    )
    unreasoned = api_error_for(
        status_code=403,
        path="/api/v1/tickets/42/notes/812",
        payload={"error": NOT_AGENT_AUTHORED["error"]},
    )

    assert isinstance(rewritten, NotAgentAuthoredError)
    assert not isinstance(unreasoned, NotAgentAuthoredError)


# --------------------------------------------------------------------------- #
# The two 404s, and the desk that has no such route
# --------------------------------------------------------------------------- #


def _not_found_then_identity(body: dict, scopes: list[str]):
    """A 404 on the entry route, and the identity endpoint answering for the
    ticket-write 404 diagnosis (N20), which runs on every non-GET ticket path."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == USER_PATH:
            return json_response(200, identity_payload(scopes, scopes))
        return json_response(404, body)

    return handler


@pytest.mark.parametrize("name", REPLY_TOOLS)
async def test_a_reply_route_404_states_both_meanings(make_client, name: str) -> None:
    """The ticket's 404 and the entry's differ only in the server's sentence,
    which is carried and never parsed — so the message states both, and names
    the NOTE tools as where a note's id belongs."""
    client, _ = make_client(_not_found_then_identity(NO_REPLY, ["ticket:write"]))

    async with client:
        with pytest.raises(NotFoundError) as raised:
            await call_client(client, name, entry_id=815)

    message = str(raised.value)

    assert "NOTHING WAS CHANGED" in message
    assert "If it names the TICKET" in message
    assert "If it names the PUBLIC REPLY" in message
    assert "a private note's id (`edit_private_note` and `delete_private_note` take those)" in message
    assert "repeating a delete that timed out or failed, it means the first one landed" in message
    assert '`kind: "comment"`' in message
    assert NO_REPLY["error"] in message
    # A key that resolves `ticket:write` cannot be refused its own ticket.
    assert "THIS MAY BE YOUR OWN TICKET" not in message


@pytest.mark.parametrize("name", NOTE_TOOLS)
async def test_a_note_route_404_points_a_reply_id_at_the_reply_tools(make_client, name: str) -> None:
    client, _ = make_client(_not_found_then_identity(NO_NOTE, ["ticket:write"]))

    async with client:
        with pytest.raises(NotFoundError) as raised:
            await call_client(client, name)

    message = str(raised.value)

    assert "If it names the PRIVATE NOTE" in message
    assert "a public reply's id (`edit_ticket_comment` and `delete_ticket_comment` take those)" in message
    assert '`kind: "note"`' in message


async def test_the_own_ticket_sentence_is_still_added_for_an_escalation_only_key(
    make_client,
) -> None:
    """The ticket-write 404 diagnosis composes with the entry message: a key
    without `ticket:write` may be looking at its OWN ordinary ticket."""
    client, recorder = make_client(
        _not_found_then_identity(NO_TICKET, ["escalation:read", "escalation:write"])
    )

    async with client:
        with pytest.raises(NotFoundError) as raised:
            await client.delete_private_note(42, 812)

    assert "THIS MAY BE YOUR OWN TICKET" in str(raised.value)
    assert recorder.paths == ["/api/v1/tickets/42/notes/812", USER_PATH]


@pytest.mark.parametrize("name", list(TOOLS))
async def test_a_desk_without_the_routes_is_named_as_such(make_client, name: str) -> None:
    """🔴 A 404 WITH NO `error` KEY IS THE FRAMEWORK, NOT EBTEQDESK. This
    client ships ahead of the desk again; against an older one every call here
    would otherwise read as a missing ticket or a missing reply. Not a
    NotFoundError — so no "this may be your own ticket" — and not the
    attachment routes' error either, whose message is about attachments."""
    client, recorder = make_client(always_json(404, NO_ROUTE))

    async with client:
        with pytest.raises(StaleThreadEntryApiError) as raised:
            await call_client(client, name)

    message = str(raised.value)

    assert not isinstance(raised.value, NotFoundError)
    assert not isinstance(raised.value, StaleAttachmentApiError)
    assert raised.value.retriable is False
    assert "no endpoint for editing or deleting a reply or a note" in message
    assert "NOT A MISSING TICKET OR A MISSING ENTRY" in message
    assert "NOTHING WAS CHANGED" in message
    assert "attachment" not in message.lower()
    # No diagnostic request — it is not a NotFoundError.
    assert len(recorder.requests) == 1


def test_the_attachment_routes_404_is_not_read_as_an_entry() -> None:
    """The reverse: the new branch must not capture the attachment routes,
    which share the `notes/` prefix."""
    error = api_error_for(
        status_code=404,
        path="/api/v1/tickets/42/notes/attachments",
        payload=NO_ROUTE,
    )

    assert isinstance(error, StaleAttachmentApiError)
    assert not isinstance(error, StaleThreadEntryApiError)


@pytest.mark.parametrize("name", EDIT_TOOLS)
async def test_a_blank_body_is_the_desks_422(make_client, name: str) -> None:
    """Validated by the desk, not here — the create rule, unchanged — so the
    sentence the user reads is the desk's, naming the field."""
    client, _ = make_client(
        always_json(
            422,
            {
                "error": "The given data was invalid.",
                "errors": {"body": ["The body field is required."]},
            },
        )
    )

    async with client:
        with pytest.raises(InvalidRequestError) as raised:
            await call_client(client, name, body="   ")

    assert raised.value.field_errors == {"body": ["The body field is required."]}
    assert "body: The body field is required." in str(raised.value)


# --------------------------------------------------------------------------- #
# The MCP layer — registration and annotations
# --------------------------------------------------------------------------- #


async def test_the_four_tools_take_a_ticket_an_entry_and_on_an_edit_a_body(tools) -> None:
    for name, (_, _, id_argument) in TOOLS.items():
        schema = tools[name].input_schema
        expected = {"ticket_id", id_argument} | ({"body"} if name in EDIT_TOOLS else set())

        assert set(schema["properties"]) == expected, name
        assert set(schema["required"]) == expected, name
        assert schema["properties"][id_argument]["type"] == "integer"
        # No flag that picks the kind, and no dry run.
        assert not set(schema["properties"]) & {"private", "kind", "dry_run", "append"}


@pytest.mark.parametrize("name", list(TOOLS))
async def test_the_annotations_say_destructive_closed_world_and_not_retry_safe(
    tools, name: str
) -> None:
    """Destructive: an edit overwrites the body (no revision is kept) and a
    delete removes the entry. Closed world: none of the four sends anything.
    NOT idempotent, on purpose — see `_writes` in server.py."""
    wire = tools[name].model_dump(by_alias=True, exclude_none=True)

    assert wire["annotations"] == {
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    }


# --------------------------------------------------------------------------- #
# The MCP layer — what the descriptions must say
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", list(TOOLS))
async def test_the_first_line_is_the_consequence(tools, name: str) -> None:
    first_line = (tools[name].description or "").strip().splitlines()[0]

    assert first_line.startswith("WRITES TO EBTEQDESK")

    if name == "edit_ticket_comment":
        assert "PUBLIC reply the requester can see on their ticket page" in first_line
        assert "Nothing is sent to them, and they are not told it changed" in first_line
    elif name == "delete_ticket_comment":
        assert "PERMANENTLY DELETES" in first_line
        assert "from the requester's ticket page" in first_line
        assert "NO UNDO" in first_line and "they are not told" in first_line
    elif name == "edit_private_note":
        assert "INTERNAL note" in first_line and "Nobody is notified" in first_line
    else:
        assert "PERMANENTLY DELETES" in first_line and "NO UNDO" in first_line
        assert "every file on it" in first_line


@pytest.mark.parametrize("name", REPLY_TOOLS)
async def test_the_reply_tools_say_the_requester_may_already_have_read_it(
    tools, name: str
) -> None:
    """🔴 THE ASSERTION THIS FILE EXISTS FOR. A model that reads "delete" as
    "unsend" tells a user their wrong reply was taken back, when the requester
    may have been reading it on their ticket page since it was posted."""
    description = described(tools[name])

    assert "ticket page" in description
    assert "may already have read" in description or "MAY ALREADY HAVE READ" in description
    assert "not told" in description
    assert "`comment_on_ticket`" in description


@pytest.mark.parametrize("name", list(TOOLS))
async def test_no_tool_claims_an_email_is_sent_or_recalled(tools, name: str) -> None:
    """🔴 Ebteqdesk PR #342 removed every email to a requester, and these
    routes exist only on desks that carry it — so on every desk these tools can
    reach, there is no email to recall. A first cut of 5.3.0 said "the email is
    NOT recalled"; that framing must not come back."""
    description = described(tools[name]).lower()

    assert "email" not in description
    assert "mailed" not in description
    assert "inbox" not in description


async def test_delete_never_reads_as_unsend(tools) -> None:
    description = described(tools["delete_ticket_comment"])

    assert "THE REQUESTER MAY ALREADY HAVE READ IT" in description
    assert "What they already read stays read" in description
    assert "Never describe a delete as taking back, retracting or unsending a reply" in description


async def test_edit_says_the_correction_is_not_announced(tools) -> None:
    description = described(tools["edit_ticket_comment"])

    assert "THIS IS NOT AN UNDO" in description
    assert "they may already have read the ORIGINAL" in description
    assert "they are not told the reply changed" in description
    assert "a silent edit is not a correction" in description


@pytest.mark.parametrize("name", EDIT_TOOLS)
async def test_the_edits_say_the_body_replaces_everything(tools, name: str) -> None:
    """The commonest misuse: sending the corrected word, not the corrected reply."""
    description = described(tools[name])

    assert "REPLACES THE WHOLE" in description
    assert "send ALL of it back" in description
    assert "untrusted_content" in description
    assert "Markdown is NOT rendered" in description
    # The entry keeps its author and time, with no edited marker.
    assert "STILL READS AS ITS AUTHOR'S" in description
    assert "puts your words under their name" in description


@pytest.mark.parametrize("name", DELETE_TOOLS)
async def test_the_deletes_say_permanent_files_included_and_confirm_first(
    tools, name: str
) -> None:
    description = described(tools[name])

    assert "PERMANENT AND IRREVERSIBLE" in description
    assert "every attachment on it and those files are deleted" in description
    assert "no restore" in description
    assert "GET THEIR AGREEMENT FIRST" in description
    assert "NEVER RETRY A DELETE THAT TIMED OUT" in description
    assert "ALREADY GONE, not something that never existed" in description
    assert '"deleted": true' in description


async def test_the_note_delete_is_how_an_attached_file_comes_off(tools) -> None:
    description = described(tools["delete_private_note"])

    assert "`attach_file_to_ticket`" in description
    assert "the ONLY way such a file comes off a ticket" in description


@pytest.mark.parametrize("name", list(TOOLS))
async def test_each_says_how_to_find_its_id_and_which_tool_takes_the_other_kind(
    tools, name: str
) -> None:
    description = described(tools[name])
    own_kind = '`kind: "comment"`' if name in REPLY_TOOLS else '`kind: "note"`'
    other_tools = NOTE_TOOLS if name in REPLY_TOOLS else REPLY_TOOLS

    assert "HOW TO FIND" in description
    assert "`get_ticket`" in description
    assert own_kind in description
    assert "Never guess one" in description
    assert "is a 404 here" in description
    # The counterpart for the other kind is named — the one with the same verb.
    counterpart = next(t for t in other_tools if t.split("_")[0] == name.split("_")[0])
    assert f"`{counterpart}`" in description


@pytest.mark.parametrize("name", list(TOOLS))
async def test_each_says_check_agent_authored_and_any_agents_entry(tools, name: str) -> None:
    """Two facts, both about authorship and pointing opposite ways. The desk
    marks which entries it will accept (`agent_authored`, the same predicate
    as the 403), so a model is told to CHECK it rather than call and be
    refused. And owner decision 2026-10-15, no author check: a model must know
    it CAN change a colleague's words, and that it should not unless asked."""
    description = described(tools[name])
    verb = "CHANGED" if name in EDIT_TOOLS else "DELETED"

    assert f"CHECK `agent_authored` ON THE ENTRY FIRST — ONLY `true` CAN BE {verb}" in description
    assert "one with `agent_authored: true`" in description
    assert "`not_agent_authored`" in description
    assert "so do not call this on one" in description
    # The flag is about the entry, never the caller.
    assert "not that YOU may — the scopes below still apply" in description
    assert "since removed from the desk" in description
    assert "NOT ONLY YOURS" in description
    assert "only when the user asked for that specific" in description


@pytest.mark.parametrize("name", list(TOOLS) + ["get_ticket", "get_ticket_comments"])
async def test_no_description_infers_authorship_from_the_author_name(tools, name: str) -> None:
    """The first cut of 5.3.0 told a model a `comment` whose `author` matched
    `requester.name` was "usually" the requester's. The desk now says so
    outright, so the guess is gone and must not come back beside the flag."""
    description = described(tools[name])

    assert "is usually the ticket's `requester.name`" not in description
    assert "`author` is usually" not in description


@pytest.mark.parametrize("name", REPLY_TOOLS)
async def test_the_reply_pair_costs_escalation_reply(tools, name: str) -> None:
    description = described(tools[name])

    assert "`ticket:write`" in description and "`ticket.reply`" in description
    assert "`escalation:read` + `escalation:reply`" in description
    assert "`bp_escalation.reply`" in description
    assert "`escalation:write` is NOT enough on an escalated ticket" in description
    assert "`ticket_all.view`" in description


@pytest.mark.parametrize("name", NOTE_TOOLS)
async def test_the_note_pair_costs_escalation_write(tools, name: str) -> None:
    description = described(tools[name])

    assert "`ticket:write`" in description and "`ticket.reply`" in description
    assert "`escalation:read` + `escalation:write`" in description
    assert "`bp_escalation.reply`" in description
    assert "whoever it is assigned to" in description
    assert "`escalation:reply`" not in description.replace("NOT `escalation:reply`", "")


@pytest.mark.parametrize("name", NOTE_TOOLS)
async def test_the_note_tools_never_claim_to_reach_the_requester(tools, name: str) -> None:
    """The pair-confusion guard from test_private_notes, for the edit pair."""
    description = described(tools[name])

    assert "PUBLIC reply" not in description.split("HOW TO FIND")[0]
    assert "never sees" in description or "never saw" in description


# --------------------------------------------------------------------------- #
# The MCP layer — round trips
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name, kind", [("edit_ticket_comment", "comment"), ("edit_private_note", "note")])
async def test_an_edit_round_trips_as_one_enveloped_entry(wired, name: str, kind: str) -> None:
    """The edited entry is a thread entry, so its text is wrapped like any
    other — and its `id` is NOT: an identifier is passed back bare."""
    wired(always_json(200, edited(kind)))

    result = await srv.mcp.call_tool(name, arguments(name, body="<p>Corrected: Friday.</p>"))

    assert not result.is_error
    structured = result.structured_content

    assert next(iter(structured)) == NOTICE
    assert structured["data"]["id"] == 812
    assert structured["data"]["kind"] == kind
    # A boolean, bare — not text anyone typed, so never enveloped.
    assert structured["data"]["agent_authored"] is True
    assert structured["data"]["body"] == {KEY: "<p>Corrected: Friday.</p>"}
    assert structured["data"]["body_html"] == {KEY: "<p>Corrected: Friday.</p>"}
    assert structured["data"]["author"] == {KEY: "Bo"}
    assert structured["data"]["created_at"] == "2026-08-12T05:09:30+00:00"


@pytest.mark.parametrize("name, kind", [("delete_ticket_comment", "comment"), ("delete_private_note", "note")])
async def test_a_delete_round_trips_its_receipt_verbatim(wired, name: str, kind: str) -> None:
    """No text, so no envelope and no notice — this layer adds a marking where
    there is something to mark, never a shape."""
    wired(always_json(200, receipt(kind)))

    result = await srv.mcp.call_tool(name, arguments(name))

    assert not result.is_error
    assert result.structured_content == receipt(kind)
    assert NOTICE not in result.structured_content


async def test_the_requesters_message_is_refused_in_readable_text(wired) -> None:
    """`str(ToolError)` is verbatim what a user reads in their MCP client."""
    wired(always_json(403, NOT_AGENT_AUTHORED))

    with pytest.raises(ToolError) as raised:
        await srv.mcp.call_tool("delete_ticket_comment", arguments("delete_ticket_comment"))

    message = str(raised.value)

    assert "because an AGENT DID NOT WRITE IT" in message
    assert "Traceback" not in message
    assert "ebteqdesk_mcp/" not in message


async def test_an_older_desk_is_named_in_readable_text(wired) -> None:
    wired(always_json(404, NO_ROUTE))

    with pytest.raises(ToolError) as raised:
        await srv.mcp.call_tool("edit_private_note", arguments("edit_private_note"))

    assert "no endpoint for editing or deleting a reply or a note" in str(raised.value)


async def test_a_bad_id_is_refused_in_readable_text_before_anything_is_sent(wired) -> None:
    """The schema says integer, so a word is refused by the SDK; a zero gets
    through the schema and is refused by the client — either way, no request."""
    sent: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return json_response(200, receipt())

    wired(handler)

    with pytest.raises(ToolError) as raised:
        await srv.mcp.call_tool(
            "delete_private_note", {"ticket_id": 42, "note_id": 0}
        )

    assert "note_id must be a positive integer" in str(raised.value)
    assert sent == []


# --------------------------------------------------------------------------- #
# The thread serves the id — and the two read tools say what it is for
# --------------------------------------------------------------------------- #


async def test_get_ticket_passes_each_entry_id_through_bare(wired) -> None:
    """🔴 The desk added `id` to every entry with the routes. It reaches the
    model unwrapped (an identifier, like every other id), null on an event."""
    conversation = [
        thread_entry("comment", "Printer on fire.", id=810, author="Ada", agent_authored=False),
        thread_entry("note", "Vendor RMA pending.", id=812),
        thread_entry("event", "Escalated"),
    ]
    wired(always_json(200, ticket_detail(conversation)))

    result = await srv.mcp.call_tool("get_ticket", {"ticket_id": 42})
    entries = result.structured_content["data"]["conversation"]

    assert [entry["id"] for entry in entries] == [810, 812, None]
    # 🔴 `agent_authored` too: a bare boolean, in the desk's key order, on every
    # entry — False on the requester's message and on the event.
    assert [entry["agent_authored"] for entry in entries] == [False, True, False]
    assert all(type(entry["agent_authored"]) is bool for entry in entries)
    assert [list(entry)[:3] for entry in entries] == [["id", "kind", "agent_authored"]] * 3
    assert entries[1]["body"] == {KEY: "Vendor RMA pending."}


async def test_get_ticket_comments_passes_each_entry_id_through_bare(wired) -> None:
    wired(
        always_json(
            200,
            ticket_list(
                [
                    thread_entry("note", "x", id=812),
                    thread_entry("comment", "From the requester.", id=813, agent_authored=False),
                ]
            ),
        )
    )

    result = await srv.mcp.call_tool("get_ticket_comments", {"ticket_id": 42})
    rows = result.structured_content["data"]

    assert [row["id"] for row in rows] == [812, 813]
    assert [row["agent_authored"] for row in rows] == [True, False]


@pytest.mark.parametrize("name", ["get_ticket", "get_ticket_comments"])
async def test_the_thread_reads_say_what_the_id_is_for(tools, name: str) -> None:
    description = described(tools[name])

    for tool in TOOLS:
        assert f"`{tool}`" in description, tool
    assert "null" in description
    assert "requester's own messages" in description
    assert "`agent_authored`" in description
    assert "before calling an edit or delete tool" in description


async def test_get_ticket_lists_id_in_the_entry_shape(tools) -> None:
    description = described(tools["get_ticket"])

    assert (
        "`{id, kind, agent_authored, body, body_html, author, attachments, created_at, created_at_human}`"
        in description
    )
    assert "never guess one" in description
    assert "AN `id` DOES NOT MAKE AN ENTRY EDITABLE — `agent_authored` SAYS WHICH ARE" in description
    assert "It describes the ENTRY, not you" in description
    assert "`id` and `agent_authored` arrive bare, never wrapped" in description
    assert "An Ebteqdesk older than the edit routes serves neither" in description


async def test_read_then_edit_uses_the_served_id(wired) -> None:
    """The workflow the descriptions teach, end to end against the fake: read
    the thread, take the note's id, edit it. Mirrors the desk's own
    `the_thread_serves_the_id_the_edit_routes_take`."""
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        if request.method == "GET":
            return json_response(
                200,
                ticket_list(
                    [
                        thread_entry("comment", "Hi", id=810, agent_authored=False),
                        thread_entry("note", "A note.", id=812),
                    ]
                ),
            )
        return json_response(200, edited("note", "Edited via the served id."))

    wired(handler)

    listed = await srv.mcp.call_tool("get_ticket_comments", {"ticket_id": 42})
    # What the descriptions teach: the entry's kind AND its flag, never a guess.
    note = next(
        e for e in listed.structured_content["data"] if e["kind"] == "note" and e["agent_authored"]
    )

    result = await srv.mcp.call_tool(
        "edit_private_note", {"ticket_id": 42, "note_id": note["id"], "body": "Edited via the served id."}
    )

    assert not result.is_error
    assert seen[-1].url.path == "/api/v1/tickets/42/notes/812"


# --------------------------------------------------------------------------- #
# What stopped being true elsewhere — pinned ABSENT
# --------------------------------------------------------------------------- #


def _instructions() -> str:
    return " ".join((srv.mcp.instructions or "").split())


def test_the_instructions_introduce_the_four_and_say_none_is_an_undo() -> None:
    instructions = _instructions()

    for name in TOOLS:
        assert f"`{name}`" in instructions, name

    assert "EITHER CAN BE CHANGED AFTERWARDS, AND THAT IS NOT AN UNDO" in instructions
    assert "REPLACES THE WHOLE BODY and sends nothing" in instructions
    assert "replaces the old on the requester's ticket page, they are not told it changed" in instructions
    assert "they may already have read the original" in instructions
    assert (
        "A delete is PERMANENT, takes the entry's attachments with it, and takes a "
        "public reply off that page without a word to the requester"
    ) in instructions
    # The first cut's email framing, wrong on every desk with these routes.
    assert "recalls no email" not in instructions
    assert "is NOT sent the correction" not in instructions
    assert "an email already sent stays sent" not in instructions
    assert "Only an entry with `agent_authored: true` can be changed — check it first" in instructions
    assert "ANY agent's such entry on a ticket you may write to" in instructions
    assert "the four edit-and-delete tools send nothing to anyone" in instructions


def test_the_instructions_no_longer_say_a_thread_cannot_be_changed() -> None:
    instructions = _instructions()

    for untrue in (
        "Neither can be edited or deleted afterwards",
        "no delete-comment",
        "no delete-note",
        "Neither the note nor the file can be removed afterwards",
        "the two deletes there ARE cannot be reversed",
    ):
        assert untrue not in instructions, untrue

    assert "the four deletes there ARE cannot be reversed" in instructions
    assert "through `delete_private_note`, and permanently" in instructions


async def test_comment_on_ticket_no_longer_says_it_cannot_be_edited(tools) -> None:
    """It used to say "it cannot be edited or deleted through this API", which
    stopped being true. What stays true — and is now the sentence — is that
    neither tool can unread a reply the requester may already have seen.

    ⚠️ Only the sentence 5.3.0 added is pinned here. The tool's older claim
    that the reply is EMAILED is untrue since Ebteqdesk PR #342 and is left for
    the follow-up that rewrites `comment_on_ticket` as a whole."""
    description = described(tools["comment_on_ticket"])

    assert "cannot be edited or deleted through this API" not in description
    assert "THAT EMAIL CANNOT BE RECALLED" not in description
    assert "can change or remove it on the requester's ticket page afterwards, but not unread it" in description
    assert "`edit_ticket_comment`" in description and "`delete_ticket_comment`" in description
    assert "`comment_id`" in description


async def test_attach_file_to_ticket_names_the_one_way_its_file_comes_off(tools) -> None:
    description = described(tools["attach_file_to_ticket"])

    assert "There is no tool that removes an attachment or a note" not in description
    assert "`delete_private_note`" in description
    assert "No tool removes the file alone" in description
