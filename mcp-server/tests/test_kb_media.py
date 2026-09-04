"""POST /api/v1/kb/media — the one endpoint that reads a LOCAL FILE.

Two halves, and they guard different things.

THE CLIENT HALF asserts the wire: the verb, the path, the multipart part's field
name, and that the bytes on the wire are the bytes on disk. The mock sits at the
socket (`httpx2.MockTransport`), so the multipart body under test is the one
httpx really encoded — a double over `_request` would prove the test calls the
test and would say nothing about whether the part is named `file`, which is the
single string the server's validator keys on.

THE DESCRIPTION HALF asserts prose, and that is not decoration here. This tool's
`url` is the ONLY valid reference to an uploaded image, and a model that
fabricates a `/kb/media/{ULID}` publishes a broken image into a live knowledge
base signed-out visitors read — invisible to the author, who is signed in and
sees it render. The sentence forbidding that is the entire mitigation, so it is
asserted like a behaviour.
"""

from __future__ import annotations

import httpx2
import pytest

from conftest import always_json, json_response
from mcp.server.mcpserver.exceptions import ToolError

from ebteqdesk_mcp import server as srv
from ebteqdesk_mcp.errors import (
    InvalidRequestError,
    LocalFileError,
    PayloadTooLargeError,
)

MEDIA_PATH = "/api/v1/kb/media"

#: A real PNG header. Nothing here sniffs it — the SERVER does — but using
#: bytes that are actually a PNG keeps the fixtures honest about what is being
#: moved, and makes the "the bytes arrive unchanged" assertion mean something.
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + bytes(range(64))

#: The 201 body, UNWRAPPED. 🔴 No `data` envelope — this is
#: `Kb\MediaUploadController::payload()` verbatim, the same eight keys the
#: Ebteqdesk editor reads, and the response shape of a stored object must not
#: depend on which door it came through.
UPLOADED = {
    "ulid": "01JAB2CD3EF4GH5JK6MN7PQ8RS",
    "url": "/kb/media/01JAB2CD3EF4GH5JK6MN7PQ8RS",
    "kind": "image",
    "mime": "image/png",
    "width": 1280,
    "height": 720,
    "size_bytes": len(PNG_BYTES),
    "original_name": "settings.png",
}


@pytest.fixture
def png(tmp_path):
    path = tmp_path / "settings.png"
    path.write_bytes(PNG_BYTES)
    return path


# --------------------------------------------------------------------------- #
# The wire
# --------------------------------------------------------------------------- #


async def test_it_posts_the_file_as_multipart_under_the_field_name_file(
    make_client, png
) -> None:
    client, recorder = make_client(always_json(201, UPLOADED))

    async with client:
        payload = await client.upload_kb_media(str(png))

    request = recorder.last

    assert request.method == "POST"
    assert request.url.path == MEDIA_PATH

    content_type = request.headers["Content-Type"]
    assert content_type.startswith("multipart/form-data; boundary=")

    body = request.content

    # 🔴 `name="file"` is the ONE string App\Kb\MediaRules::FIELD keys on. Get it
    # wrong and every upload comes back 422 "Choose a file to upload." with no
    # hint that the part was simply misnamed.
    assert b'name="file"' in body

    # Only the BASENAME travels. The directory is the user's own filesystem
    # layout, it would be stored as `original_name` and shown to every agent
    # reading the article, and it cannot be a path component server-side anyway.
    assert b'filename="settings.png"' in body
    assert str(png.parent).encode() not in body

    # The bytes arrive unchanged. Nothing re-encodes, downscales or base64s.
    assert PNG_BYTES in body

    # And the payload comes back verbatim, unwrapped.
    assert payload == UPLOADED
    assert payload["url"] == "/kb/media/01JAB2CD3EF4GH5JK6MN7PQ8RS"
    assert "data" not in payload


async def test_it_sends_the_bearer_token_and_never_the_path_in_the_url(
    make_client, png
) -> None:
    client, recorder = make_client(always_json(201, UPLOADED))

    async with client:
        await client.upload_kb_media(str(png))

    request = recorder.last

    assert request.headers["Authorization"].startswith("Bearer ")
    assert request.url.query == b""
    assert str(png) not in str(request.url)


async def test_a_tilde_path_is_expanded_rather_than_sent_literally(
    make_client, png, monkeypatch
) -> None:
    """`~/shot.png` is what a user types. Sending it literally would be a
    LocalFileError for a file that is right there."""
    monkeypatch.setenv("HOME", str(png.parent))
    monkeypatch.setenv("USERPROFILE", str(png.parent))

    client, recorder = make_client(always_json(201, UPLOADED))

    async with client:
        await client.upload_kb_media("~/settings.png")

    assert recorder.last.url.path == MEDIA_PATH
    assert PNG_BYTES in recorder.last.content


# --------------------------------------------------------------------------- #
# The local half — no request is made at all
# --------------------------------------------------------------------------- #


async def test_a_missing_path_fails_before_any_request(make_client, tmp_path) -> None:
    client, recorder = make_client(always_json(201, UPLOADED))

    async with client:
        with pytest.raises(LocalFileError) as excinfo:
            await client.upload_kb_media(str(tmp_path / "nope.png"))

    # 🔴 Nothing went out. A failure on this machine must not look like a
    # failure at Ebteqdesk, and must not consume the upload throttle.
    assert recorder.requests == []

    message = str(excinfo.value)
    assert "no such file on this machine" in message
    assert "NOTHING WAS SENT TO EBTEQDESK" in message
    # The remedy is a conversation, not a search of the filesystem.
    assert "Ask the user for the correct path" in message
    assert "nope.png" in message


async def test_a_directory_is_refused_with_its_own_reason(make_client, tmp_path) -> None:
    client, recorder = make_client(always_json(201, UPLOADED))

    async with client:
        with pytest.raises(LocalFileError) as excinfo:
            await client.upload_kb_media(str(tmp_path))

    assert recorder.requests == []
    assert "that is a directory, not a file" in str(excinfo.value)


async def test_an_empty_file_is_refused_here_rather_than_as_a_type_error(
    make_client, tmp_path
) -> None:
    """The server would answer 422 on the `mimetypes:` rule, i.e. a message
    about JPG and WebP for a file that has no content at all. Refused where the
    reason is knowable."""
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")

    client, recorder = make_client(always_json(201, UPLOADED))

    async with client:
        with pytest.raises(LocalFileError) as excinfo:
            await client.upload_kb_media(str(empty))

    assert recorder.requests == []
    assert "the file is empty" in str(excinfo.value)


async def test_nothing_is_validated_client_side_beyond_readability(
    make_client, tmp_path
) -> None:
    """🔴 THE TYPE AND SIZE RULES ARE THE SERVER'S, AND ONLY THE SERVER'S.

    A `.exe` renamed `.png` is SENT and refused by finfo on the other end. This
    client does not sniff, does not check the extension, and does not clamp the
    size — a second copy of the whitelist here would be a rule that drifts, and
    the drift would mean one door accepting what the other refuses. Same reason
    `per_page` is not clamped anywhere in this client.
    """
    disguised = tmp_path / "payload.png"
    disguised.write_bytes(b"MZ\x90\x00\x03\x00\x00\x00")

    client, recorder = make_client(
        always_json(
            422,
            {
                "error": "The request body is not valid.",
                "errors": {"file": ["That file type is not supported."]},
            },
        )
    )

    async with client:
        with pytest.raises(InvalidRequestError):
            await client.upload_kb_media(str(disguised))

    # It really went out. The refusal came from Ebteqdesk, not from here.
    assert len(recorder.requests) == 1
    assert recorder.last.url.path == MEDIA_PATH


# --------------------------------------------------------------------------- #
# The two error surfaces that matter on an upload
# --------------------------------------------------------------------------- #


async def test_a_422_carries_the_servers_field_error_through(make_client, png) -> None:
    client, _ = make_client(
        always_json(
            422,
            {
                "error": "The request body is not valid.",
                "errors": {
                    "file": [
                        "This file is larger than the 10 MB limit. Try exporting "
                        "it at a lower resolution or quality, then upload again."
                    ]
                },
            },
        )
    )

    async with client:
        with pytest.raises(InvalidRequestError) as excinfo:
            await client.upload_kb_media(str(png))

    message = str(excinfo.value)

    # The cap and the way forward both survive to the user. The server's message
    # is the only place the per-kind number is stated, so it must not be
    # swallowed in favour of a generic "invalid arguments".
    assert "10 MB limit" in message
    assert "lower resolution" in message
    assert excinfo.value.field_errors["file"]


async def test_a_413_on_the_upload_path_says_there_is_nothing_to_retry(
    make_client, png
) -> None:
    """🔴 THE SAME STATUS MEANS THE OPPOSITE THING ON THE ATTACHMENT ENDPOINT.

    There a 413 means "retry with a smaller `max_dimension`" — a real remedy
    with a real argument. Here it means the REQUEST was over nginx's or PHP's
    ceiling and never reached the application, so there is no argument to
    change and a retry loop is the wrong instinct. One message covering both
    would send an uploader round that loop.
    """
    client, _ = make_client(
        always_json(413, {"error": "The request body is too large."})
    )

    async with client:
        with pytest.raises(PayloadTooLargeError) as excinfo:
            await client.upload_kb_media(str(png))

    message = str(excinfo.value)

    assert "DO NOT RETRY THE SAME FILE" in message
    assert "smaller export" in message
    # The attachment endpoint's remedy must not appear on this path.
    assert "max_dimension" not in message


async def test_the_attachment_413_keeps_its_own_remedy() -> None:
    """The other side of the branch above, so neither message can quietly
    become the other."""
    from ebteqdesk_mcp.errors import api_error_for

    error = api_error_for(
        status_code=413,
        path="/api/v1/attachments/9",
        payload={"error": "Too large."},
    )

    assert isinstance(error, PayloadTooLargeError)
    assert "SMALLER `max_dimension`" in str(error)
    assert "DO NOT RETRY THE SAME FILE" not in str(error)


# --------------------------------------------------------------------------- #
# Through the MCP layer
# --------------------------------------------------------------------------- #


@pytest.fixture
def wired(monkeypatch):
    """Install a client whose socket is `handler` as the server's shared client."""
    from ebteqdesk_mcp.client import EbteqdeskClient
    from ebteqdesk_mcp.config import Config

    def install(handler):
        config = Config(base_url="https://ebteqdesk.test", token="6|t", timeout=5.0)
        client = EbteqdeskClient(config, transport=httpx2.MockTransport(handler))
        monkeypatch.setattr(srv, "_client", client)
        return client

    yield install

    monkeypatch.setattr(srv, "_client", None)


async def test_the_tool_round_trips_through_the_mcp_layer(wired, png) -> None:
    wired(always_json(201, UPLOADED))

    result = await srv.mcp.call_tool("upload_kb_media", {"file_path": str(png)})

    assert not result.is_error
    assert result.structured_content["url"] == UPLOADED["url"]


async def test_a_local_failure_reaches_the_client_as_readable_text(wired, tmp_path) -> None:
    """`LocalFileError` is an EbteqdeskError, so `_call` normalises it like every
    other failure: the user sees the sentence, not a Python traceback naming
    pathlib."""
    wired(always_json(201, UPLOADED))

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool(
            "upload_kb_media", {"file_path": str(tmp_path / "absent.png")}
        )

    text = str(excinfo.value)

    assert "no such file on this machine" in text
    assert "NOTHING WAS SENT TO EBTEQDESK" in text
    assert "Traceback" not in text


async def test_the_upload_is_refused_without_the_write_scope(wired, png) -> None:
    wired(
        lambda request: json_response(
            403,
            {
                "error": "This API key is not permitted to kb:write.",
                "required_scope": "kb:write",
            },
        )
    )

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool("upload_kb_media", {"file_path": str(png)})

    assert "kb:write" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# The description, which is the whole mitigation
# --------------------------------------------------------------------------- #


def described(tool) -> str:
    return " ".join((tool.description or "").split())


@pytest.fixture
async def upload_tool():
    tools = {tool.name: tool for tool in await srv.mcp.list_tools()}
    return tools["upload_kb_media"]


async def test_the_description_forbids_inventing_a_media_url(upload_tool) -> None:
    """🔴 THE ASSERTION THIS FILE EXISTS FOR.

    A ULID is 26 random characters. A model that guesses one has not made a
    formatting mistake — it has put a broken image into a live knowledge base
    signed-out visitors read, and it cannot see the breakage because an authenticated
    viewer is served media that belongs to no article at all.
    """
    description = described(upload_tool)

    assert "NEVER INVENT OR GUESS A `/kb/media/` URL" in description
    assert "BROKEN IMAGE" in description
    assert '<img src="/kb/media/{ulid}"' in description
    assert "ONLY VALID WAY TO REFERENCE" in description


async def test_the_description_states_the_order_of_operations(upload_tool) -> None:
    """Upload → reference → save. The link is DERIVED from the saved body, so an
    upload that is never referenced is attached to nothing and is swept."""
    description = described(upload_tool)

    assert "UPLOAD FIRST, THEN REFERENCE, THEN SAVE" in description
    assert "`propose_kb_article`" in description
    assert "`update_kb_article`" in description
    assert "seven days" in description
    assert "UNATTACHED" in description


async def test_the_description_carries_the_local_filesystem_warning(upload_tool) -> None:
    """The risk this tool carries is to the USER'S MACHINE, and no scope, status
    code or version number expresses it. The description is the mitigation."""
    description = described(upload_tool)

    assert "READS A PATH ON THE USER'S OWN MACHINE" in description
    assert "Never sweep or list a directory" in description
    assert "never guess at a path" in description


async def test_the_description_quotes_the_types_and_the_caps(upload_tool) -> None:
    description = described(upload_tool)

    for mime in (
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "video/mp4",
        "video/webm",
    ):
        assert f"`{mime}`" in description

    assert "10 MB" in description
    assert "50 MB" in description
    # And what is NOT accepted, named, because "images and video" reads as
    # permission for an SVG.
    assert "no PDF, no SVG" in description


async def test_the_description_says_the_type_comes_from_the_content(
    upload_tool,
) -> None:
    """`mimetypes:` and not `mimes:`, restated where a model reads it: renaming
    a file changes nothing, so do not suggest renaming as a workaround."""
    description = described(upload_tool)

    assert "SNIFFING THE FILE'S CONTENT, NOT ITS EXTENSION" in description
    assert "renaming will help" in description


async def test_the_description_names_the_scope_and_who_holds_it(upload_tool) -> None:
    description = described(upload_tool)

    assert "`kb:write`" in description
    assert "`kb.manage`" in description
    assert "ADMINISTRATOR OR SUPERVISOR ONLY" in description


async def test_the_description_forbids_a_blind_retry(upload_tool) -> None:
    """Unlike the other two retry-safe writes, a replayed upload does not fail
    and does not duplicate anything a human sees. It silently stores a second
    copy under a new ULID that nothing references."""
    description = described(upload_tool)

    assert "NEVER RETRY A TIMED-OUT UPLOAD BLIND" in description
    assert "NEW ULID" in description


async def test_the_instructions_carry_the_same_two_rules() -> None:
    """`instructions` is what a host shows about the server as a whole. The
    invent-a-url rule and the local-filesystem rule are the two a model must not
    have to open a tool description to learn."""
    instructions = " ".join((srv.mcp.instructions or "").split())

    assert "NEVER INVENT A `/kb/media/` URL" in instructions
    assert "reads the local filesystem" in instructions.lower()
    assert "`upload_kb_media`" in instructions
    assert "TWENTY-ONE WRITE TOOLS" in instructions
