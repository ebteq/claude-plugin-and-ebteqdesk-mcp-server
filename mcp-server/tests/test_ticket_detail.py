"""The ticket DETAIL surface: `get_ticket`, `get_ticket_comments` and
`get_ticket_attachment`, at the client layer and through MCP.

These three are the first tools on this server that return what was actually
SAID on a ticket, and two of the things they carry are dangerous to get wrong in
ways no status code reveals:

  - A `note` entry is a PRIVATE INTERNAL NOTE. The payload distinguishes it from
    a public reply with one string, `kind`, and nothing else; a model that does
    not know what that string means will repeat staff-only text into a public
    reply, which Ebteqdesk mails to the requester's address.
    So the tests here assert the DESCRIPTION says so, not only that the field
    survives the round trip.

  - `get_ticket_attachment` returns a real MCP image block, and the image is
    DOWNSCALED. A model reading a serial number off a blurred screenshot reports
    a confident wrong answer. The description has to say the image is reduced
    and that "I cannot read this" is the correct response.

The image tool is also the only place on this server where the SDK's content
plumbing matters: it is annotated `-> Image`, which suppresses output-schema
generation. Annotated `-> list[Any]` instead, the SDK builds a wrapped schema
and then fails trying to `model_dump()` the `Image`. That is asserted directly,
because the failure mode is a 500 at call time rather than anything visible in a
review diff.
"""

from __future__ import annotations

import base64

import httpx2
import pytest

from conftest import (
    PNG_4X3,
    always_json,
    downscaled_screenshot_response,
    attachment_payload,
    image_response,
    per_page_refusal,
    scope_refusal,
    thread_entry,
    ticket_detail,
    ticket_list,
    ticket_row,
)
from mcp.server.mcpserver.exceptions import ToolError

from ebteqdesk_mcp import server as srv
from ebteqdesk_mcp.client import AttachmentImage
from ebteqdesk_mcp.config import Config
from ebteqdesk_mcp.errors import (
    MalformedResponseError,
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaError,
)


@pytest.fixture
def wired(monkeypatch):
    """Install a client whose socket is `handler` as the server's shared client."""
    from ebteqdesk_mcp.client import EbteqdeskClient

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
    """A description with its hard wrapping collapsed — see test_server_tools."""
    return " ".join((tool.description or "").split())


# --------------------------------------------------------------------------- #
# get_ticket — the client
# --------------------------------------------------------------------------- #


async def test_get_ticket_requests_the_detail_route(make_client) -> None:
    client, recorder = make_client(always_json(200, ticket_detail()))

    await client.get_ticket(42)

    assert recorder.last.url.path == "/api/v1/tickets/42"
    assert recorder.last.method == "GET"
    # No query string when no limit was asked for: an absent `thread_limit`
    # means the whole thread, and sending an empty one makes every log line
    # noisier for nothing.
    assert recorder.last.url.query == b""


async def test_get_ticket_passes_the_thread_limit_through_unvalidated(
    make_client,
) -> None:
    """Out of range is a 422 naming the field. Validating locally would invent a
    second, differently-worded rule and make this client disagree with curl."""
    client, recorder = make_client(always_json(200, ticket_detail()))

    await client.get_ticket(42, thread_limit=999)

    assert "thread_limit=999" in recorder.last.url.query.decode()


async def test_get_ticket_returns_the_conversation_verbatim(make_client) -> None:
    conversation = [
        thread_entry("comment", "The printer is on fire."),
        thread_entry("event", "Escalated"),
        thread_entry("note", "Churn risk — handle gently."),
    ]
    client, _ = make_client(always_json(200, ticket_detail(conversation)))

    body = await client.get_ticket(42)

    assert [entry["kind"] for entry in body["data"]["conversation"]] == [
        "comment",
        "event",
        "note",
    ]
    # Nothing renamed, nothing flattened, nothing derived — including the
    # nulls an event legitimately carries.
    assert body["data"]["conversation"] == conversation
    assert body["data"]["conversation"][1]["body_html"] is None


async def test_get_ticket_surfaces_the_top_level_truncation_flag(make_client) -> None:
    """🔴 It is beside `data`, not inside it, and only present when the thread
    was actually cut. A client that looked for it under `data` would silently
    always believe it had the whole conversation."""
    client, _ = make_client(
        always_json(200, ticket_detail([thread_entry()], truncated=True))
    )

    body = await client.get_ticket(42, thread_limit=1)

    assert body["conversation_truncated"] is True
    assert "conversation_truncated" not in body["data"]


async def test_get_ticket_refuses_a_non_numeric_id_before_sending(make_client) -> None:
    client, recorder = make_client(always_json(200, ticket_detail()))

    with pytest.raises(ValueError, match="positive integer ticket id"):
        await client.get_ticket("bp-task")

    assert recorder.requests == []


# --------------------------------------------------------------------------- #
# get_ticket_comments — the client
# --------------------------------------------------------------------------- #


async def test_get_ticket_comments_requests_the_nested_route(make_client) -> None:
    client, recorder = make_client(always_json(200, ticket_list([])))

    await client.get_ticket_comments(42, page=2, per_page=5)

    assert recorder.last.url.path == "/api/v1/tickets/42/comments"
    query = recorder.last.url.query.decode()
    assert "page=2" in query and "per_page=5" in query


async def test_get_ticket_comments_shares_the_ticket_list_paging_ceiling(
    make_client,
) -> None:
    """One PagesTicketLists trait server-side, so the 422 is the same one the
    three ticket lists give. Asserted so a local clamp cannot be added here."""
    from ebteqdesk_mcp.errors import InvalidRequestError

    client, _ = make_client(per_page_refusal())

    with pytest.raises(InvalidRequestError) as excinfo:
        await client.get_ticket_comments(42, per_page=21)

    assert "per_page" in excinfo.value.field_errors


# --------------------------------------------------------------------------- #
# get_ticket_attachment — the client
# --------------------------------------------------------------------------- #


async def test_the_attachment_client_returns_bytes_not_json(make_client) -> None:
    """The one method whose success path does not go through `_request`. Sent
    through the JSON decoder these bytes would be MalformedResponseError."""
    client, recorder = make_client(image_response())

    image = await client.get_ticket_attachment(12)

    assert isinstance(image, AttachmentImage)
    assert image.data == PNG_4X3
    assert image.mime_type == "image/png"
    assert recorder.last.url.path == "/api/v1/attachments/12"


async def test_the_attachment_client_reads_the_dimension_headers(make_client) -> None:
    """They are the only way a caller learns what it actually got without
    decoding the image, and decoding it would mean this client growing an image
    library to re-answer a question the server already answered."""
    client, _ = make_client(image_response(width="1568", height="1176"))

    image = await client.get_ticket_attachment(12)

    assert (image.width, image.height) == (1568, 1176)
    assert image.source_bytes == 9999


async def test_missing_or_garbled_headers_degrade_to_unknown(make_client) -> None:
    """An intermediary that strips unknown headers is a configuration the user
    cannot see. It must not fail a download that succeeded — and it must not be
    reported as zero pixels either."""
    client, _ = make_client(image_response(width=None, height="not-a-number"))

    image = await client.get_ticket_attachment(12)

    assert image.width is None
    assert image.height is None
    assert image.data == PNG_4X3


@pytest.mark.parametrize("header", [None, "", "true", "yes", "2"])
async def test_an_absent_or_garbled_downscaled_header_is_unknown_not_false(
    make_client, header
) -> None:
    """🔴 None, never False. False is a CLAIM — "this image was not reduced" —
    and the wrong claim is what stops an agent retrying with a larger
    `max_dimension` when it cannot read the image. An older server, or a proxy
    that strips unknown headers, must produce "I don't know" rather than a
    confident wrong answer."""
    client, _ = make_client(image_response(downscaled=header))

    image = await client.get_ticket_attachment(12)

    assert image.downscaled is None


async def test_the_client_reports_the_servers_downscale_verdict(make_client) -> None:
    """Read, never derived — including when the byte sizes say the opposite."""
    client, _ = make_client(downscaled_screenshot_response())

    image = await client.get_ticket_attachment(12)

    assert image.downscaled is True
    assert (image.source_width, image.source_height) == (2400, 1800)
    assert (image.width, image.height) == (1568, 1176)
    # The trap, stated: a byte comparison would answer False here.
    assert len(image.data) > image.source_bytes

    client, _ = make_client(image_response(downscaled="0"))

    assert (await client.get_ticket_attachment(12)).downscaled is False


async def test_max_dimension_is_passed_through_unvalidated(make_client) -> None:
    client, recorder = make_client(image_response())

    await client.get_ticket_attachment(12, max_dimension=9000)

    assert "max_dimension=9000" in recorder.last.url.query.decode()


async def test_a_video_attachment_raises_unsupported_media(make_client) -> None:
    """🔴 Ebteqdesk accepts video attachments and this endpoint serves images
    only. Video bytes under an image content type give a vision model a decode
    failure at best and a confident misreading at worst."""
    client, _ = make_client(
        always_json(
            415,
            {
                "error": "Attachment 12 is `video/mp4`, which this endpoint does "
                "not serve — it returns images only.",
                "mime_type": "video/mp4",
                "attachment_id": 12,
            },
        )
    )

    with pytest.raises(UnsupportedMediaError) as excinfo:
        await client.get_ticket_attachment(12)

    error = excinfo.value
    assert error.mime_type == "video/mp4"
    assert error.status_code == 415
    # The remedy, and the anti-remedy: nothing retries into success.
    assert "NOTHING RETRIES INTO SUCCESS HERE" in str(error)
    assert "browser" in str(error)


async def test_an_oversized_image_raises_payload_too_large_and_says_to_retry_smaller(
    make_client,
) -> None:
    """The one refusal on this route that IS retryable, and the argument that
    makes it work is named. Left as the generic ApiError fallthrough the message
    would say "unexpected 413" and prescribe nothing."""
    client, _ = make_client(
        always_json(413, {"error": "Too large.", "attachment_id": 12})
    )

    with pytest.raises(PayloadTooLargeError) as excinfo:
        await client.get_ticket_attachment(12)

    assert "max_dimension" in str(excinfo.value)


async def test_a_missing_file_is_the_same_404_as_an_unknown_id(make_client) -> None:
    """One body for "no such row", "not your ticket" and "bytes are gone", so
    the id space cannot be probed for which tickets carry files. A 404 here is
    therefore NOT evidence the file was deleted."""
    client, _ = make_client(
        always_json(404, {"error": 'There is no attachment with the id "12".'})
    )

    with pytest.raises(NotFoundError):
        await client.get_ticket_attachment(12)


async def test_html_on_the_image_route_is_malformed_not_an_image(make_client) -> None:
    """A 200 carrying HTML is a proxy or a login wall answering instead of the
    application. Handing those bytes back as an "image" would produce a broken
    image in a chat with no explanation anywhere."""
    client, _ = make_client(
        lambda _r: httpx2.Response(
            200, content=b"<html>login</html>", headers={"Content-Type": "text/html"}
        )
    )

    with pytest.raises(MalformedResponseError) as excinfo:
        await client.get_ticket_attachment(12)

    assert "instead of JSON" in str(excinfo.value)


async def test_the_image_route_still_diagnoses_a_scope_refusal(make_client) -> None:
    """The binary path shares `_error_for` with the JSON path rather than
    classifying its own failures — so the key/role diagnosis, which costs a
    second request to a DIFFERENT endpoint, reaches it too. A copied classifier
    would have quietly skipped this."""
    client, recorder = make_client(
        scope_refusal("escalation:read", requested=[], scopes=[])
    )

    with pytest.raises(Exception) as excinfo:
        await client.get_ticket_attachment(12)

    assert "mint a NEW key" in str(excinfo.value)
    # Two requests: the refusal, then the identity endpoint.
    assert recorder.paths == ["/api/v1/attachments/12", "/api/v1/user"]


async def test_the_attachment_client_refuses_a_non_numeric_id_before_sending(
    make_client,
) -> None:
    client, recorder = make_client(image_response())

    with pytest.raises(ValueError, match="positive integer attachment id"):
        await client.get_ticket_attachment("screenshot.png")

    assert recorder.requests == []


# --------------------------------------------------------------------------- #
# Through the MCP layer
# --------------------------------------------------------------------------- #


async def test_get_ticket_round_trips_through_mcp(wired) -> None:
    wired(always_json(200, ticket_detail([thread_entry("note", "Internal.")])))

    result = await srv.mcp.call_tool("get_ticket", {"ticket_id": 42})

    assert not result.is_error
    assert result.structured_content["data"]["conversation"][0]["kind"] == "note"


async def test_get_ticket_comments_round_trips_through_mcp(wired) -> None:
    wired(always_json(200, ticket_list([thread_entry()])))

    result = await srv.mcp.call_tool("get_ticket_comments", {"ticket_id": 42})

    assert not result.is_error
    assert result.structured_content["data"][0]["kind"] == "comment"


async def test_the_attachment_tool_returns_a_real_image_content_block(wired) -> None:
    """🔴 THE ONE TOOL WHOSE OUTPUT IS NOT JSON. A text block of metadata, then
    an actual image block whose base64 decodes to the bytes the server sent."""
    wired(image_response())

    result = await srv.mcp.call_tool("get_ticket_attachment", {"attachment_id": 12})

    assert not result.is_error
    assert [block.type for block in result.content] == ["text", "image"]

    image = result.content[1]
    assert image.mime_type == "image/png"
    assert base64.b64decode(image.data) == PNG_4X3


async def test_the_attachment_tool_declares_no_output_schema(tools) -> None:
    """🔴 The `-> Image` annotation is load-bearing and invisible in a diff.

    With it the SDK generates no output schema and no structured-output
    validation runs. Annotated `-> list[Any]` instead, the SDK builds a wrapped
    schema and then fails trying to `model_dump(mode="json")` the `Image` — a
    500 at call time, not a review-visible mistake. So the absence of the schema
    is asserted directly.
    """
    assert tools["get_ticket_attachment"].output_schema is None

    # And every other tool DOES have one, so this is a property of that tool
    # rather than of the SDK version.
    assert tools["get_ticket"].output_schema is not None


async def test_the_metadata_block_says_the_image_was_downscaled(wired) -> None:
    """The model otherwise has no way to know it is looking at a reduced copy —
    which is exactly what makes "I cannot read that" the right answer instead of
    a confident guess at a serial number."""
    wired(image_response(width="1568", height="1176"))

    result = await srv.mcp.call_tool("get_ticket_attachment", {"attachment_id": 12})

    text = result.content[0].text
    assert '"width": 1568' in text
    assert '"source_width": 12' in text
    assert '"downscaled": true' in text
    assert "illegible" in text


async def test_downscaling_is_reported_even_when_the_bytes_grew(wired) -> None:
    """🔴 THE REGRESSION, and the one case the old fixture could not express.

    Downscaling is a DIMENSION operation. The flag used to be derived from
    `len(body) < source_bytes`, which is a BYTE comparison, and the two disagree
    exactly where it matters: re-encoding a flat-colour screenshot at a
    different compression level routinely makes the file BIGGER while the pixels
    shrink. Measured on the stack — 2400×1800 / 23,960 bytes in, 1568×1176 /
    55,577 bytes out.

    The old derivation reports `"downscaled": false` sitting immediately beside
    `"width": 1568`, which contradicts it — and worse, it defeats the
    instruction printed in the same block: an agent told it already holds full
    fidelity will not retry with a larger `max_dimension`, so it guesses at the
    serial number it cannot read instead of asking for a sharper copy.

    The fixture inverts the byte sizes around the tiny body deliberately, so
    this test is RED against a byte comparison and GREEN against the header.
    """
    wired(downscaled_screenshot_response())

    result = await srv.mcp.call_tool("get_ticket_attachment", {"attachment_id": 12})

    text = result.content[0].text

    assert '"downscaled": true' in text
    # The trap, asserted rather than assumed: the returned body really is bigger
    # than the source it came from, so a byte comparison cannot pass this.
    assert '"bytes": 78' in text
    assert '"source_bytes": 40' in text
    # And the dimensions are reported both ways round, so a client can say
    # "2400×1800 -> 1568×1176" rather than only "reduced".
    assert '"width": 1568' in text
    assert '"source_width": 2400' in text
    assert '"source_height": 1800' in text


@pytest.mark.parametrize(
    "flag, expected, forbidden",
    [
        ("1", "fine detail may be illegible", "full-resolution original"),
        ("0", "full-resolution original", "Downscaled by the server"),
        (None, "did not report whether", "full-resolution original"),
    ],
)
async def test_the_note_agrees_with_the_flag(wired, flag, expected, forbidden) -> None:
    """The prose and the boolean are read by the same model in the same block,
    so they must not contradict each other.

    A fixed "Downscaled by the server before transfer" beside
    `"downscaled": false` is the same class of defect as deriving the flag from
    the bytes: it reads as a caution about a picture that is pixel-for-pixel the
    original, and trains the model to hedge where it should be confident. The
    unknown arm says it does not know rather than guessing either way.
    """
    wired(image_response(downscaled=flag))

    result = await srv.mcp.call_tool("get_ticket_attachment", {"attachment_id": 12})

    text = result.content[0].text

    assert expected in text
    assert forbidden not in text


async def test_a_video_reaches_the_mcp_client_as_readable_text(wired) -> None:
    wired(
        always_json(
            415,
            {"error": "Not an image.", "mime_type": "video/quicktime", "attachment_id": 12},
        )
    )

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool("get_ticket_attachment", {"attachment_id": 12})

    text = str(excinfo.value)
    assert "video/quicktime" in text
    assert "images only" in text
    assert "Traceback" not in text


async def test_the_detail_tools_need_the_ticket_read_scope(wired) -> None:
    wired(scope_refusal("ticket:read", requested=[], scopes=[]))

    for name, arguments in (
        ("get_ticket", {"ticket_id": 42}),
        ("get_ticket_comments", {"ticket_id": 42}),
        ("get_ticket_attachment", {"attachment_id": 12}),
    ):
        with pytest.raises(ToolError) as excinfo:
            await srv.mcp.call_tool(name, arguments)

        assert "ticket:read" in str(excinfo.value)


async def test_an_escalation_scope_refusal_is_reported_as_a_role_or_key_problem(
    wired,
) -> None:
    """A ticket on the shared queue that is not this account's own costs
    `escalation:read` at request time, which route middleware cannot declare. The
    refusal still goes through the ordinary diagnosis."""
    wired(
        scope_refusal(
            "escalation:read",
            requested=["escalation:read", "ticket:read"],
            scopes=["ticket:read"],
        )
    )

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool("get_ticket", {"ticket_id": 42})

    text = str(excinfo.value)
    assert "escalation:read" in text
    assert "administrator" in text.lower()
    assert "mint a NEW key" not in text


# --------------------------------------------------------------------------- #
# The rules only a description can carry
# --------------------------------------------------------------------------- #


async def test_get_ticket_declares_that_notes_are_private(tools) -> None:
    """🔴 THE SAFETY PROPERTY. `kind` is one string and the payload cannot
    explain it; a model that does not know what `note` means will repeat
    staff-only text back into a public reply, which is mailed out."""
    description = described(tools["get_ticket"])

    assert "PRIVATE INTERNAL NOTE" in description
    assert "NEVER REPEAT ONE INTO A PUBLIC REPLY" in description
    # All three kinds enumerated, so a model can classify every entry it sees.
    assert "`comment`" in description
    assert "`note`" in description
    assert "`event`" in description


async def test_get_ticket_explains_why_escalated_tickets_are_full_of_notes(
    tools,
) -> None:
    """Ebteqdesk silently downgrades an agent reply on an escalated ticket into
    a private note, so the diagnosis lives in `note` entries while `comment`
    entries are only what the requester was told. A summary that misses that gets
    the story backwards."""
    description = described(tools["get_ticket"])

    assert "ON AN ESCALATED TICKET THE NOTES ARE USUALLY WHERE THE REAL WORK IS" in description
    assert "downgrades an ordinary agent reply" in description


async def test_get_ticket_says_the_lists_carry_no_conversation(tools) -> None:
    """The reason this tool exists. A model that thinks `list_tickets` already
    told it what the ticket says will answer from a subject line."""
    description = described(tools["get_ticket"])

    assert "THIS IS THE ONLY TOOL THAT RETURNS WHAT WAS SAID" in description
    assert "carry no message bodies" in description


async def test_get_ticket_states_where_the_truncation_flag_appears(tools) -> None:
    """Top level, not inside `data`, and only when something was cut. Both
    halves matter: the position, and that its ABSENCE means the whole thread."""
    description = described(tools["get_ticket"])

    assert "APPEARS AT THE TOP LEVEL OF THE RESPONSE" in description
    assert "NOT inside it" in description
    assert "ONLY when `thread_limit` actually cut something" in description
    assert "Its absence means you have the whole thread" in description


async def test_get_ticket_points_at_the_opening_message(tools) -> None:
    """`body` is not in `conversation`; a summary built from the thread alone
    starts at the first reply and never sees the requester's question."""
    description = described(tools["get_ticket"])

    assert "OPENING message" in description
    assert "It is not in `conversation`" in description


async def test_the_attachment_tool_warns_that_the_image_is_downscaled(tools) -> None:
    description = described(tools["get_ticket_attachment"])

    assert "THE IMAGE IS DOWNSCALED, SO DO NOT READ FINE DETAIL OFF IT" in description
    assert "1568" in description
    # The behaviour that replaces guessing.
    assert "SAY you cannot read it" in description
    assert "do not guess" in description
    # And that a small image is not made worse.
    assert "never enlarged" in description


async def test_the_attachment_tool_warns_that_video_is_an_error(tools) -> None:
    description = described(tools["get_ticket_attachment"])

    assert "VIDEO ATTACHMENTS RETURN AN ERROR, NOT AN IMAGE" in description
    assert "video/mp4" in description
    assert "413" in description


async def test_the_attachment_tool_says_it_returns_an_image_block(tools) -> None:
    description = described(tools["get_ticket_attachment"])

    assert "IMAGE ITSELF as an image block" in description
    assert "attachments[].id" in description


async def test_the_comments_tool_says_it_is_not_comments_only(tools) -> None:
    """The path says "comments" and the payload contains events. A model that
    trusted the name would report a ticket as having no escalation history."""
    description = described(tools["get_ticket_comments"])

    assert "THIS IS NOT A COMMENTS-ONLY LIST" in description
    assert "interleaved chronologically" in description
    assert "PREFER `get_ticket` FOR ALMOST EVERYTHING" in description


async def test_the_comments_tool_says_the_opening_message_is_not_in_it(tools) -> None:
    """🔴 The requester's problem report lives on `tickets.body`, not in the
    comments — correct server-side, and invisible from this endpoint. An agent
    paging only here reads every reply to a question it has never seen, and
    summarises the ticket without knowing what was asked."""
    description = described(tools["get_ticket_comments"])

    assert "THE REQUESTER'S OPENING MESSAGE IS NOT IN THIS LIST" in description
    assert "NOT ON ANY PAGE" in description
    assert "Call `get_ticket` at least once" in description


async def test_the_detail_tools_name_both_scopes(tools) -> None:
    """`ticket:read` is the floor and `escalation:read` is the state-dependent
    half. A user who reads only the first will re-mint the same key."""
    for name in ("get_ticket", "get_ticket_comments", "get_ticket_attachment"):
        description = described(tools[name])

        assert "`ticket:read`" in description
        assert "`escalation:read`" in description


async def test_the_new_tools_expose_exactly_their_documented_arguments(tools) -> None:
    def props(name: str) -> set:
        return set(tools[name].input_schema.get("properties", {}))

    def required(name: str) -> set:
        return set(tools[name].input_schema.get("required", []))

    assert props("get_ticket") == {"ticket_id", "thread_limit"}
    assert props("get_ticket_comments") == {"ticket_id", "page", "per_page"}
    assert props("get_ticket_attachment") == {"attachment_id", "max_dimension"}

    assert required("get_ticket") == {"ticket_id"}
    assert required("get_ticket_comments") == {"ticket_id"}
    assert required("get_ticket_attachment") == {"attachment_id"}


async def test_the_conversation_shape_is_snake_case_including_attachments(
    make_client,
) -> None:
    """🔴 The /api/v1 boundary renames the Inertia surfaces' `mimeType` to
    `mime_type`. A client reading the camelCase key would silently see None on
    every attachment and never be able to tell an image from a video before
    fetching it."""
    entry = thread_entry("note", "See attached.", attachments=[attachment_payload()])
    client, _ = make_client(always_json(200, ticket_detail([entry])))

    body = await client.get_ticket(42)
    attachment = body["data"]["conversation"][0]["attachments"][0]

    assert set(attachment) == {"id", "name", "mime_type", "size", "url"}
    assert "mimeType" not in attachment
    assert "/api/v1/attachments/" in attachment["url"]


async def test_a_ticket_row_and_a_ticket_detail_share_their_header_fields(
    make_client,
) -> None:
    """The server's detail resource SUBCLASSES the list resource, so every
    header field on a list row is on the detail payload. Pinned here because a
    client that parsed them separately would be the second place that has to be
    kept in step."""
    client, _ = make_client(always_json(200, ticket_detail()))

    detail = (await client.get_ticket(42))["data"]

    for field in ticket_row():
        assert field in detail, f"the detail payload is missing `{field}`"
