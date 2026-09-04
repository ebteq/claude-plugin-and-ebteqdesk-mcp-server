"""The knowledge base STRUCTURE surface: `list_kb_tree`, `create_kb_category`,
`update_kb_category`, `delete_kb_category`, `create_kb_folder`,
`update_kb_folder` and `delete_kb_folder`.

Four properties here are silent when they break, and every one of them is a
property of the DESCRIPTIONS as much as of the code:

  - `list_kb_tree` IS THE ONLY SOURCE OF `kb_folder_id`. Nothing else on this API
    returns a folder id — the article payloads carry `{slug, name}` pairs — so
    before this tool existed `propose_kb_article` was uncallable against a
    knowledge base whose structure the agent could not see, and uncallable at all
    against an empty one. The first test below is that ids come through, and it
    is not a formality.

  - NO TOOL HERE HAS A `visibility` ARGUMENT. Not optional, not with a default —
    the argument must not EXIST, so a model cannot try. Every folder created
    through this API is `agents` (internal), which means nothing filed into one
    reaches a reader outside the desk until a human changes it in the Ebteqdesk
    UI. A model that told a user otherwise would have said something false about
    what is readable outside the desk. Asserted against the GENERATED SCHEMA rather than the source text,
    because the schema is what a model actually reads.

  - `update_kb_folder` CANNOT MOVE A FOLDER. `kb_category_id` is absent for the
    same reason `visibility` is: a folder decides who sees the articles inside
    it, so both edits are access-control decisions wearing organisational
    costumes. The server IGNORES both keys rather than rejecting them, which is
    exactly why an argument would be worse than its absence — it would read as a
    working control and do nothing.

  - RENAMING RE-DERIVES THE SLUG, so it CHANGES THE PORTAL URL. Unlike an
    article's, which freezes at first publish. "Rename" sounds harmless; this one
    breaks saved links, with no redirect.

  - THE TWO DELETES REFUSE RATHER THAN CASCADE, and that is the fifth property
    and the newest. `delete_kb_category` and `delete_kb_folder` are the only
    tools on this server that destroy anything and nothing on this API puts a row
    back. A category still holding folders and a folder still holding articles
    are both a 422 with the count named, and NOTHING is deleted — which is what
    keeps either from becoming the article delete this API deliberately does not
    offer. The refusal is asserted as a behaviour AND as a sentence in both
    descriptions, because a model that read the refusal as an obstacle would
    start deleting children to get past it.
"""

from __future__ import annotations

import inspect
import json

import httpx2
import pytest

from conftest import (
    always_json,
    kb_children_refusal,
    kb_article_payload,
    kb_category_row,
    kb_folder_row,
    kb_slug_collision,
    kb_structure_not_found,
    kb_tree_payload,
    scope_refusal,
)
from mcp.server.mcpserver.exceptions import ToolError

from ebteqdesk_mcp import server as srv
from ebteqdesk_mcp.client import EbteqdeskClient
from ebteqdesk_mcp.config import Config
from ebteqdesk_mcp.errors import InvalidRequestError, NotFoundError

#: The seven tools this file is about, and what a minimal valid call looks like.
#:
#: 🔴 THE TWO DELETES TAKE THE PATH ID AND NOTHING ELSE. No move, no visibility,
#: no cascade flag, no dry run — asserted against the generated schema further
#: down, because a `cascade=True` appearing here would quietly turn a refusal
#: into the article delete this API does not have.
STRUCTURE_TOOLS = {
    "list_kb_tree": {},
    "create_kb_category": {"name": "POS"},
    "update_kb_category": {"category_id": 3, "name": "Point of Sale"},
    "delete_kb_category": {"category_id": 3},
    "create_kb_folder": {"kb_category_id": 3, "name": "Errors"},
    "update_kb_folder": {"folder_id": 7, "name": "Error codes"},
    "delete_kb_folder": {"folder_id": 7},
}

WRITE_TOOLS = [name for name in STRUCTURE_TOOLS if name != "list_kb_tree"]


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


def sent(recorder) -> dict:
    return json.loads(recorder.last.content or b"{}")


def schema(tool) -> dict:
    return tool.input_schema.get("properties", {})


# --------------------------------------------------------------------------- #
# list_kb_tree — the ids are the point
# --------------------------------------------------------------------------- #


async def test_the_tree_is_a_get_with_no_body_and_no_paging(make_client) -> None:
    client, recorder = make_client(always_json(200, kb_tree_payload()))

    await client.list_kb_tree()

    assert recorder.last.method == "GET"
    assert recorder.last.url.path == "/api/v1/kb/tree"
    assert not recorder.last.content
    # No `page` or `per_page`: the endpoint does not page, and sending them
    # would be a client inventing an envelope the server does not have.
    assert recorder.last.url.params == httpx2.QueryParams()


async def test_the_tree_path_is_not_kb_categories(make_client) -> None:
    """⚠️ `/kb/tree`, deliberately. The server RESERVES `GET /kb/categories` for
    a future PUBLIC read tree on `kb:read`. A client pointed at that path would
    start silently reading a different corpus the day it ships."""
    client, recorder = make_client(always_json(200, kb_tree_payload()))

    await client.list_kb_tree()

    assert recorder.last.url.path != "/api/v1/kb/categories"


async def test_the_tree_surfaces_folder_ids(make_client) -> None:
    """🔴 THE WHOLE REASON THIS TOOL EXISTS. `propose_kb_article` requires
    `kb_folder_id` and nothing else on this API returns one."""
    client, _ = make_client(always_json(200, kb_tree_payload()))

    tree = await client.list_kb_tree()

    category = tree["data"][0]
    folder = category["folders"][0]

    assert category["id"] == 3
    assert folder["id"] == 7
    assert folder["kb_category_id"] == 3
    # And the count, which is what makes this cheaper than the authoring payload
    # the Ebteqdesk web UI loads.
    assert folder["articles_count"] == 12


async def test_the_tree_reports_internal_folders_with_their_visibility(
    make_client,
) -> None:
    """NOT filtered to public content, unlike `search_kb_articles`. An
    `agents`-only folder is listed, and the field says so — which is how a
    caller knows an article filed there reaches nobody outside."""
    client, _ = make_client(
        always_json(
            200,
            kb_tree_payload(
                [
                    kb_category_row(
                        folders=[
                            kb_folder_row(id=7, visibility="agents"),
                            kb_folder_row(id=8, slug="setup", visibility="public"),
                        ]
                    )
                ]
            ),
        )
    )

    folders = (await client.list_kb_tree())["data"][0]["folders"]

    assert [folder["visibility"] for folder in folders] == ["agents", "public"]


async def test_an_empty_knowledge_base_is_an_empty_list_not_an_error(
    make_client,
) -> None:
    """The case that made the write tools unusable: an agent cannot bootstrap a
    KB it cannot enumerate, so "nothing here yet" has to be a successful read."""
    client, _ = make_client(always_json(200, kb_tree_payload([])))

    assert (await client.list_kb_tree())["data"] == []


async def test_a_category_with_no_folders_still_carries_the_key(make_client) -> None:
    """One shape, never two — a client has no branch to get wrong."""
    client, _ = make_client(
        always_json(200, kb_tree_payload([kb_category_row(folders=[])]))
    )

    assert (await client.list_kb_tree())["data"][0]["folders"] == []


# --------------------------------------------------------------------------- #
# The four writes — the wire
# --------------------------------------------------------------------------- #


async def test_create_category_posts_the_documented_body(make_client) -> None:
    client, recorder = make_client(
        always_json(201, {"data": kb_category_row(folders=[])})
    )

    await client.create_kb_category(name="POS", description="Tills and terminals")

    assert recorder.last.method == "POST"
    assert recorder.last.url.path == "/api/v1/kb/categories"
    assert sent(recorder) == {"name": "POS", "description": "Tills and terminals"}


async def test_create_folder_posts_the_api_field_name(make_client) -> None:
    """The argument name and the wire name are the SAME string, so there is no
    mapping step left to drift. `kb_category_id` is a BODY field and the server
    quotes it back verbatim in its 422 — a local `category_id` would make the
    refusal name a field the caller never sent."""
    client, recorder = make_client(always_json(201, {"data": kb_folder_row()}))

    await client.create_kb_folder(kb_category_id=3, name="Errors")

    assert recorder.last.method == "POST"
    assert recorder.last.url.path == "/api/v1/kb/folders"
    assert sent(recorder) == {"kb_category_id": 3, "name": "Errors"}


async def test_the_updates_patch_the_id_route(make_client) -> None:
    client, recorder = make_client(always_json(200, {"data": kb_category_row()}))

    await client.update_kb_category(3, name="Point of Sale")

    assert recorder.last.method == "PATCH"
    assert recorder.last.url.path == "/api/v1/kb/categories/3"
    assert sent(recorder) == {"name": "Point of Sale"}

    await client.update_kb_folder(7, description="Till error codes")

    assert recorder.last.method == "PATCH"
    assert recorder.last.url.path == "/api/v1/kb/folders/7"
    assert sent(recorder) == {"description": "Till error codes"}


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.create_kb_category(name="POS"),
        lambda c: c.create_kb_folder(kb_category_id=3, name="Errors"),
        lambda c: c.update_kb_category(3, name="POS"),
        lambda c: c.update_kb_folder(7, name="Errors"),
    ],
)
async def test_absent_fields_are_omitted_rather_than_nulled(make_client, call) -> None:
    """🔴 The server's update semantics, shared with the article writes: an
    ABSENT key is not edited, a key present and EMPTY is an edit. Sending None
    as JSON null would turn "leave the description alone" into "set it to null"
    — and on a PATCH that is a silent clobber of somebody's text."""
    client, recorder = make_client(always_json(200, {"data": kb_folder_row()}))

    await call(client)

    assert "description" not in sent(recorder)


async def test_an_empty_string_description_is_an_edit_and_is_sent(
    make_client,
) -> None:
    """`description=""` CLEARS it — the server's mutator collapses blank to
    NULL. Only None is dropped, which is the whole distinction between "not
    edited" and "cleared"."""
    client, recorder = make_client(always_json(200, {"data": kb_folder_row()}))

    await client.update_kb_folder(7, description="")

    assert sent(recorder) == {"description": ""}


async def test_an_update_with_no_name_sends_no_name(make_client) -> None:
    """And therefore does not re-derive the slug. A PATCH that carried the name
    back unchanged would still rewrite the slug server-side, which is fine — but
    a PATCH that carried a STALE name would silently rename and move the URL."""
    client, recorder = make_client(always_json(200, {"data": kb_category_row()}))

    await client.update_kb_category(3, description="Tills")

    assert sent(recorder) == {"description": "Tills"}
    assert "name" not in sent(recorder)


# --------------------------------------------------------------------------- #
# 🔴 visibility, and the move, are absent from the WIRE and from the SIGNATURES
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "method",
    [
        "create_kb_category",
        "update_kb_category",
        "create_kb_folder",
        "update_kb_folder",
    ],
)
async def test_no_structure_write_has_a_visibility_parameter(method: str) -> None:
    """A folder carries its articles' visibility, so a `visibility` argument
    would be the one way a machine could publish content outside the desk — and the
    article surface exists precisely because it may not.

    The server IGNORES the key rather than rejecting it, which is why absence is
    the requirement: a parameter that was silently discarded would read to a
    model as a working control."""
    signature = inspect.signature(getattr(EbteqdeskClient, method))

    assert "visibility" not in signature.parameters


async def test_update_folder_offers_no_way_to_move_a_folder() -> None:
    """`kb_category_id` is absent for `visibility`'s reason: a move is an
    access-control change wearing an organisational costume."""
    signature = inspect.signature(EbteqdeskClient.update_kb_folder)

    assert "kb_category_id" not in signature.parameters
    assert "category_id" not in signature.parameters


async def test_no_visibility_key_ever_reaches_the_wire(make_client) -> None:
    """Asserted on the BYTES and not only on the signature — a signature check
    alone would still pass the day a `**kwargs` crept in."""
    client, recorder = make_client(always_json(201, {"data": kb_folder_row()}))

    await client.create_kb_folder(kb_category_id=3, name="Errors", description="d")
    assert "visibility" not in sent(recorder)

    await client.update_kb_folder(7, name="Errors", description="d")
    assert "visibility" not in sent(recorder)
    assert "kb_category_id" not in sent(recorder)


async def test_a_hallucinated_visibility_argument_never_reaches_the_wire(
    wired,
) -> None:
    """🔴 THE FAILURE MODE THE MISSING PARAMETER IS DEFENDING AGAINST: a model
    that has read about visibility elsewhere and passes it anyway.

    The SDK builds its argument model from the function signature, so a key that
    is not a parameter is dropped before the tool body runs — it never becomes a
    keyword argument, never reaches the client, and never reaches the body. The
    folder is `agents` regardless. Asserted through the MCP layer and on the
    BYTES, because the signature test above would not catch a future change that
    started forwarding unknown arguments through.
    """
    recorded: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        recorded.append(request)
        return httpx2.Response(201, json={"data": kb_folder_row()})

    wired(handler)

    result = await srv.mcp.call_tool(
        "create_kb_folder",
        {"kb_category_id": 3, "name": "Public notes", "visibility": "public"},
    )

    assert not result.is_error
    assert "visibility" not in json.loads(recorded[-1].content or b"{}")
    assert result.structured_content["data"]["visibility"] == "agents"


async def test_a_hallucinated_move_never_reaches_the_wire(wired) -> None:
    """The same defence for `kb_category_id` on a folder update."""
    recorded: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        recorded.append(request)
        return httpx2.Response(200, json={"data": kb_folder_row()})

    wired(handler)

    result = await srv.mcp.call_tool(
        "update_kb_folder",
        {"folder_id": 7, "name": "Errors", "kb_category_id": 99, "visibility": "public"},
    )

    assert not result.is_error

    body = json.loads(recorded[-1].content or b"{}")
    assert body == {"name": "Errors"}


async def test_a_created_folder_comes_back_internal(make_client) -> None:
    """The fixture defaults to the column default, because the server does. A
    caller reading this response learns the folder is internal."""
    client, _ = make_client(always_json(201, {"data": kb_folder_row()}))

    folder = (await client.create_kb_folder(kb_category_id=3, name="Errors"))["data"]

    assert folder["visibility"] == "agents"


# --------------------------------------------------------------------------- #
# The two deletes — the only tools here that destroy anything
# --------------------------------------------------------------------------- #


async def test_deleting_a_category_is_a_delete_with_no_body(make_client) -> None:
    """🔴 NO BODY AT ALL, not even `{}`. A delete has nothing to move, nothing to
    re-scope and no cascade flag, and inventing an empty object would be this
    client asserting a request shape the API never described."""
    client, recorder = make_client(
        always_json(200, {"data": kb_category_row(folders=[])})
    )

    payload = await client.delete_kb_category(3)

    assert recorder.last.method == "DELETE"
    assert recorder.last.url.path == "/api/v1/kb/categories/3"
    assert not recorder.last.content

    # The receipt, in the same shape the create answers with — `folders` is
    # always [] because a category holding any could not have been deleted.
    assert payload["data"]["id"] == 3
    assert payload["data"]["folders"] == []


async def test_deleting_a_folder_is_a_delete_with_no_body(make_client) -> None:
    # `articles_count: 0` is not decoration on this fixture: the server can only
    # answer a delete for a folder that was empty, so 0 is the only value this
    # receipt can carry.
    client, recorder = make_client(
        always_json(200, {"data": kb_folder_row(articles_count=0)})
    )

    payload = await client.delete_kb_folder(7)

    assert recorder.last.method == "DELETE"
    assert recorder.last.url.path == "/api/v1/kb/folders/7"
    assert not recorder.last.content

    assert payload["data"]["id"] == 7
    assert payload["data"]["articles_count"] == 0


@pytest.mark.parametrize("bad", ["3", None, 0, -1, True, 3.0])
async def test_a_bad_id_is_refused_before_a_delete_is_sent(make_client, bad) -> None:
    """The same guard the updates carry, and it matters MORE here: `True` is an
    `int` in Python and would silently address row 1 — deleting the wrong
    category rather than merely renaming it."""
    client, recorder = make_client(always_json(200, {"data": kb_category_row()}))

    with pytest.raises(ValueError, match="category_id"):
        await client.delete_kb_category(bad)

    with pytest.raises(ValueError, match="folder_id"):
        await client.delete_kb_folder(bad)

    assert recorder.requests == []


async def test_a_category_holding_folders_is_a_422_naming_the_count(
    make_client,
) -> None:
    """🔴 REFUSAL, NOT CASCADE, and the COUNT has to survive into the message the
    caller sees — "some validation error" would leave a model with nothing to
    report and no reason not to retry."""
    client, _ = make_client(kb_children_refusal("category", count=2))

    with pytest.raises(InvalidRequestError) as excinfo:
        await client.delete_kb_category(3)

    text = str(excinfo.value)

    assert excinfo.value.status_code == 422
    assert "category" in excinfo.value.field_errors
    assert "This category still holds 2 folders. Move or delete them first." in text


async def test_a_folder_holding_articles_is_a_422_naming_the_count(
    make_client,
) -> None:
    """The stronger half: past this refusal is an article delete, and this API
    has none."""
    client, _ = make_client(kb_children_refusal("folder", count=3))

    with pytest.raises(InvalidRequestError) as excinfo:
        await client.delete_kb_folder(7)

    assert "This folder still holds 3 articles. Move or delete them first." in str(
        excinfo.value
    )


async def test_deleting_an_unknown_id_is_a_404(make_client) -> None:
    client, _ = make_client(kb_structure_not_found("category"))

    with pytest.raises(NotFoundError) as excinfo:
        await client.delete_kb_category(999999)

    assert excinfo.value.status_code == 404


async def test_the_refusal_reaches_the_model_through_the_mcp_layer(wired) -> None:
    """The message is only useful if it survives the tool boundary. A ToolError
    carrying "422" and nothing else would read as a transient failure."""
    wired(kb_children_refusal("folder", count=1))

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool("delete_kb_folder", {"folder_id": 7})

    assert "This folder still holds 1 article. Move or delete them first." in str(
        excinfo.value
    )


async def test_neither_delete_is_reachable_without_kb_write(wired) -> None:
    """The destructive verb sits behind the IDENTICAL gate as create and update —
    `kb:write`, which resolves only while the key carries it AND the owner's role
    holds `kb.manage`. Covered by the surface-wide test above too; asserted
    separately because a looser gate on a delete is the one that would matter."""
    wired(scope_refusal("kb:write", requested=["kb:read"], scopes=["kb:read"]))

    for name, arguments in (
        ("delete_kb_category", {"category_id": 3}),
        ("delete_kb_folder", {"folder_id": 7}),
    ):
        with pytest.raises(ToolError) as excinfo:
            await srv.mcp.call_tool(name, arguments)

        assert "kb:write" in str(excinfo.value), name


@pytest.mark.parametrize("name", ["delete_kb_category", "delete_kb_folder"])
async def test_both_delete_tools_lead_with_the_destruction_and_the_absent_undo(
    tools, name: str
) -> None:
    """🔴 THE FIRST LINE IS THE GUARDRAIL. A model choosing between tools reads
    the opening of a description and may never reach the end, so "permanently
    deletes" and "no undo" belong in the first sentence rather than in a note
    further down."""
    first_line = (tools[name].description or "").strip().splitlines()[0]
    description = described(tools[name])

    assert first_line.startswith("WRITES TO EBTEQDESK"), first_line
    assert "PERMANENTLY DELETES" in first_line
    assert "NO UNDO" in first_line

    assert "CANNOT BE REVERSED" in description, name
    assert "no trash" in description.lower(), name
    # Ask before destroying: the one instruction that has to be unmissable.
    assert "get the user's agreement" in description.lower(), name


@pytest.mark.parametrize("name", ["delete_kb_category", "delete_kb_folder"])
async def test_both_delete_tools_state_the_refusal_and_forbid_routing_around_it(
    tools, name: str
) -> None:
    """The refusal is a SAFETY PROPERTY, and a description that stated it as a
    mere error would invite a model to clear the children and try again — which,
    one level down, is a stack of articles nothing on this API can restore."""
    description = described(tools[name])

    assert "REFUSED WHILE" in description, name
    assert "never a cascade" in description.lower() or "It is never a cascade" in description, name
    assert "no delete-article tool" in description.lower(), name
    assert "unless" in description.lower() or "do not" in description.lower(), name


@pytest.mark.parametrize("name", ["delete_kb_category", "delete_kb_folder"])
async def test_both_delete_tools_warn_against_retrying_a_timeout(
    tools, name: str
) -> None:
    """Sharper than on a create: a replayed delete either reports a confusing 404
    or, if ids have been reused, removes something else."""
    description = described(tools[name])

    assert "never retry" in description.lower(), name
    assert "`list_kb_tree`" in description, name


# --------------------------------------------------------------------------- #
# Ids are validated before they build a URL
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", ["3", None, 0, -1, True, 3.0])
async def test_a_bad_category_id_is_refused_as_prose_before_sending(
    make_client, bad
) -> None:
    """🔴 `True` matters most: it is an `int` in Python and would silently become
    category 1 — a real row on a live install, whose rename breaks a portal URL
    by type confusion."""
    client, recorder = make_client(always_json(200, kb_tree_payload()))

    with pytest.raises(ValueError, match="category_id"):
        await client.update_kb_category(bad, name="X")

    with pytest.raises(ValueError, match="kb_category_id"):
        await client.create_kb_folder(kb_category_id=bad, name="X")

    assert recorder.requests == []


@pytest.mark.parametrize("bad", ["7", None, 0, True])
async def test_a_bad_folder_id_is_refused_as_prose_before_sending(
    make_client, bad
) -> None:
    client, recorder = make_client(always_json(200, kb_tree_payload()))

    with pytest.raises(ValueError, match="folder_id") as excinfo:
        await client.update_kb_folder(bad, name="X")

    # The provenance sentence is the useful half of the message.
    assert "`list_kb_tree`" in str(excinfo.value)
    assert recorder.requests == []


# --------------------------------------------------------------------------- #
# Failures
# --------------------------------------------------------------------------- #


async def test_a_derived_slug_collision_is_a_422_naming_the_name_field(
    make_client,
) -> None:
    """The collision is on the DERIVED slug, so "POS" and "  p.o.s!  " clash even
    though they are different strings — and the failure lands on `name`, which is
    the field the caller sent."""
    client, _ = make_client(kb_slug_collision())

    with pytest.raises(InvalidRequestError) as excinfo:
        await client.create_kb_category(name="  p.o.s!  ")

    text = str(excinfo.value)
    assert excinfo.value.status_code == 422
    assert "name" in excinfo.value.field_errors
    assert "A category with that name already exists." in text


async def test_a_folder_collision_names_the_category_it_is_scoped_to(
    make_client,
) -> None:
    """Folder slugs are unique PER CATEGORY, unlike category slugs which are
    global — so the message has to say "in this category" or a caller reads it as
    a global clash and renames unnecessarily."""
    client, _ = make_client(kb_slug_collision(entity="folder"))

    with pytest.raises(InvalidRequestError) as excinfo:
        await client.create_kb_folder(kb_category_id=3, name="FAQ")

    assert "already exists in this category" in str(excinfo.value)


async def test_an_unknown_id_is_a_404(make_client) -> None:
    client, _ = make_client(kb_structure_not_found("folder"))

    with pytest.raises(NotFoundError) as excinfo:
        await client.update_kb_folder(999999, name="X")

    assert excinfo.value.status_code == 404


# --------------------------------------------------------------------------- #
# Through the MCP layer
# --------------------------------------------------------------------------- #


async def test_the_tree_tool_round_trips_through_mcp(wired) -> None:
    wired(always_json(200, kb_tree_payload()))

    result = await srv.mcp.call_tool("list_kb_tree", {})

    assert not result.is_error
    # 🔴 The id an agent needs for `propose_kb_article`, surviving the whole
    # MCP round trip rather than only the client one.
    assert result.structured_content["data"][0]["folders"][0]["id"] == 7


async def test_the_structure_write_tools_round_trip_through_mcp(wired) -> None:
    wired(always_json(201, {"data": kb_folder_row()}))

    for name in WRITE_TOOLS:
        result = await srv.mcp.call_tool(name, STRUCTURE_TOOLS[name])

        assert not result.is_error, name
        assert result.structured_content["data"]["id"] == 7


async def test_no_structure_tool_is_reachable_without_kb_write(wired) -> None:
    """Including the TREE, which only reads: it is the authoring structure, so it
    is gated on `kb:write` like `get_kb_article_review`. A `kb:read` key that can
    search the public corpus cannot enumerate the folders."""
    wired(scope_refusal("kb:write", requested=["kb:read"], scopes=["kb:read"]))

    for name, arguments in STRUCTURE_TOOLS.items():
        with pytest.raises(ToolError) as excinfo:
            await srv.mcp.call_tool(name, arguments)

        text = str(excinfo.value)
        assert "kb:write" in text, name
        # Actionable: this key was never minted with it, so a NEW key is the fix.
        assert "mint a NEW key" in text, name


async def test_a_role_without_kb_manage_is_not_sent_to_mint_a_key(wired) -> None:
    """🔴 THE LIKELIEST SUPPORT QUESTION, and the wrong answer is expensive. The
    key HOLDS `kb:write` and it still does not resolve, because the account's
    role lacks `kb.manage` — granted to administrator and supervisor only. A new
    key changes nothing; the remedy is an administrator."""
    wired(
        scope_refusal(
            "kb:write",
            requested=["kb:write"],
            scopes=[],
        )
    )

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool("list_kb_tree", {})

    text = str(excinfo.value)
    assert "kb:write" in text
    assert "mint a NEW key" not in text
    assert "administrator" in text.lower()


async def test_a_collision_reaches_the_mcp_client_as_readable_text(wired) -> None:
    wired(kb_slug_collision())

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool("create_kb_category", {"name": "POS"})

    text = str(excinfo.value)
    assert "A category with that name already exists." in text
    assert "Traceback" not in text


# --------------------------------------------------------------------------- #
# 🔴 The generated SCHEMA, which is what a model actually reads
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", sorted(STRUCTURE_TOOLS))
async def test_no_structure_tool_offers_a_visibility_argument(
    tools, name: str
) -> None:
    """Asserted against the GENERATED INPUT SCHEMA and not the source text. The
    schema is what the model sees; a source-level grep would miss a parameter
    introduced any other way, and would also pass on a docstring that merely
    mentions the word."""
    properties = schema(tools[name])

    assert "visibility" not in properties, name
    assert not set(properties) & {"public", "is_public", "level", "agents_only"}, name


async def test_the_argument_naming_rule_holds_across_the_whole_kb_surface(
    tools,
) -> None:
    """🔴 ARGUMENT NAMES ARE A MODEL-FACING CONTRACT, and the rule is stated once
    in client.py above the structure writes:

      - A BODY field keeps the API's own name — `kb_folder_id`, `kb_category_id`.
        The server quotes those strings back in its 422 `errors` map, so a local
        alias would make a refusal name a field the caller never sent.
      - An IDENTIFIER naming the thing being ACTED ON takes the short form,
        because it is a PATH segment and no server message can disagree with it —
        `folder_id`, `category_id`, and the existing `ticket_id`,
        `attachment_id`.

    The failure this catches is subtle and expensive: a model that has just read
    `propose_kb_article(kb_folder_id=...)` reaches for `kb_category_id` on the
    very next call, and one surface spelling it two ways costs a retry every
    time. Asserted against the GENERATED SCHEMAS, which is what a model reads.
    """
    body_fields = {
        "propose_kb_article": "kb_folder_id",
        "create_kb_folder": "kb_category_id",
    }

    for tool, field in body_fields.items():
        properties = schema(tools[tool])

        assert field in properties, tool
        # And the short alias must NOT also be offered — two names for one
        # field is the ambiguity this rule exists to remove.
        assert field.removeprefix("kb_") not in properties, tool

    path_ids = {
        "update_kb_category": "category_id",
        "update_kb_folder": "folder_id",
        "delete_kb_category": "category_id",
        "delete_kb_folder": "folder_id",
        "get_ticket": "ticket_id",
        "get_ticket_attachment": "attachment_id",
    }

    for tool, field in path_ids.items():
        properties = schema(tools[tool])

        assert field in properties, tool
        assert f"kb_{field}" not in properties, tool


async def test_no_structure_tool_offers_a_move(tools) -> None:
    """`update_kb_folder` may not relocate a folder, so neither id is an
    argument, in either spelling. `create_kb_folder` legitimately takes
    `kb_category_id` — choosing the parent at creation is a stated choice a
    human sees in the tree."""
    assert "kb_category_id" not in schema(tools["update_kb_folder"])
    assert "category_id" not in schema(tools["update_kb_folder"])

    assert "kb_category_id" in schema(tools["create_kb_folder"])


async def test_the_structure_tools_expose_exactly_their_documented_arguments(
    tools,
) -> None:
    def required(name: str) -> set:
        return set(tools[name].input_schema.get("required", []))

    assert set(schema(tools["list_kb_tree"])) == set()
    assert set(schema(tools["create_kb_category"])) == {"name", "description"}
    assert set(schema(tools["update_kb_category"])) == {
        "category_id", "name", "description",
    }
    assert set(schema(tools["create_kb_folder"])) == {
        "kb_category_id", "name", "description",
    }
    assert set(schema(tools["update_kb_folder"])) == {
        "folder_id", "name", "description",
    }

    assert required("list_kb_tree") == set()
    assert required("create_kb_category") == {"name"}
    assert required("create_kb_folder") == {"kb_category_id", "name"}
    # 🔴 Only the id. `name` optional is what makes a description-only PATCH
    # possible, which is the one edit that does NOT move a portal URL.
    assert required("update_kb_category") == {"category_id"}
    assert required("update_kb_folder") == {"folder_id"}
    # 🔴 The whole request. A delete has nothing to move and nothing to
    # re-scope, so anything else appearing here is a widened surface.
    assert required("delete_kb_category") == {"category_id"}
    assert required("delete_kb_folder") == {"folder_id"}


@pytest.mark.parametrize("name", sorted(STRUCTURE_TOOLS))
async def test_no_structure_tool_offers_a_dry_run(tools, name: str) -> None:
    properties = set(schema(tools[name]))

    assert not properties & {"dry_run", "preview", "confirm", "simulate"}


# --------------------------------------------------------------------------- #
# The rules only a description can carry
# --------------------------------------------------------------------------- #


async def test_the_tree_tool_leads_with_being_the_source_of_folder_ids(tools) -> None:
    """An agent filing an article has to call this FIRST, and the reason has to
    be in the first paragraph — a model reads the opening of a description when
    choosing between tools and may never reach the end."""
    description = described(tools["list_kb_tree"])

    assert "THIS IS WHERE `kb_folder_id` FOR `propose_kb_article` COMES FROM" in description
    assert "nothing else on this API returns a folder id" in description
    assert "CALL THIS FIRST" in description


async def test_the_tree_tool_warns_that_the_names_are_internal(tools) -> None:
    """It lists `agents`-only folders, so the names are staff organisation and
    may name internal teams or systems. A model repeating one into a public
    reply has leaked internal structure."""
    description = described(tools["list_kb_tree"])

    assert "THESE ARE INTERNAL FOLDERS AND INTERNAL NAMES" in description
    assert "not copy for anywhere outside the desk" in description
    assert "do not repeat them into a public reply" in description


@pytest.mark.parametrize("name", sorted(WRITE_TOOLS))
async def test_every_structure_write_leads_with_its_side_effect(
    tools, name: str
) -> None:
    first_line = (tools[name].description or "").strip().splitlines()[0]

    assert first_line.startswith("WRITES TO EBTEQDESK"), first_line


async def test_the_folder_create_tool_states_that_the_folder_is_internal(
    tools,
) -> None:
    """🔴 The property the whole group turns on. A model that reports "your
    article will be visible outside the desk" after filing into a folder created
    here has said something false about what is readable outside the desk."""
    description = described(tools["create_kb_folder"])

    assert "INTERNAL" in description
    assert "THERE IS NO PARAMETER TO CHANGE THAT" in description
    assert (
        "nothing filed into this folder THROUGH THIS API will EVER reach a "
        "reader outside the desk until a human acts in the Ebteqdesk web UI" in description
    )
    assert "do not tell a user their article will be visible outside the desk" in description.lower()


async def test_the_folder_create_tool_says_a_human_has_two_levers_and_one_is_invisible_here(
    tools,
) -> None:
    """🔴 THE OTHER HALF OF THE PROPERTY ABOVE, AND ADR-0007 IS WHY IT EXISTS.

    Until 2026-08-19 the promise was total: a folder's `visibility` was the ONLY
    thing standing between an article and a reader outside the desk, so "nothing
    filed here reaches a reader outside the desk until a human changes THE
    FOLDER'S VISIBILITY" was exactly true and this test asserted that sentence.

    It is not true any more. An article can carry its own visibility overriding
    its folder's in both directions, so a human has TWO levers and only one of
    them moves the value `list_kb_tree` reports for this folder. The old sentence
    is now a promise the feature falsifies: a model acting on it would tell a
    user their article is internal while a human has made it public.

    So the create tool's guarantee is narrowed to what this API can still
    guarantee — nothing IT files becomes readable outside the desk on its own — and the
    caveat is stated rather than left for a model to discover. That caveat is the
    load-bearing half for a client: a folder still reading `agents` may hold an
    article anybody can read, and no field this API returns says so.

    ⚠️ The narrowing must not be read as a loosening. The tool STILL cannot set
    visibility, on the folder or on an article; what changed is only what may be
    concluded from a folder's level, not what a key may do.
    """
    description = described(tools["create_kb_folder"])

    assert "A HUMAN HAS TWO WAYS TO CHANGE THAT" in description
    assert "ONLY ONE OF THEM SHOWS UP IN THIS FOLDER'S `visibility`" in description
    assert "give ONE ARTICLE its own visibility, which overrides the folder's" in description

    # The consequence spelled out, not merely implied by the mechanism.
    assert (
        "a folder still reading `agents` here may hold an article a human has "
        "made public" in description
    )

    # And the boundary is unchanged: this is a narrowing of what may be
    # CONCLUDED, never of what a key may DO.
    assert "That is not something this API can do or undo" in description


async def test_the_folder_update_tool_refuses_the_move_and_the_re_scope(
    tools,
) -> None:
    description = described(tools["update_kb_folder"])

    assert "THIS CANNOT MOVE A FOLDER AND CANNOT CHANGE ITS VISIBILITY" in description
    assert "tell them it has to be done in Ebteqdesk" in description


@pytest.mark.parametrize("name", ["update_kb_category", "update_kb_folder"])
async def test_both_update_tools_warn_that_renaming_moves_the_url(
    tools, name: str
) -> None:
    """"Rename" sounds harmless. Unlike an ARTICLE's slug — frozen at first
    publish — a category or folder slug follows its name on every save, so a
    rename changes a public URL and there is no redirect."""
    description = described(tools[name])

    assert "RENAMING RE-DERIVES THE SLUG" in description
    assert "PART OF A PUBLIC URL" in description
    assert "frozen at first publish" in description
    assert "no redirect" in description.lower()


@pytest.mark.parametrize("name", sorted(WRITE_TOOLS))
async def test_every_structure_write_states_the_delete_rule(tools, name: str) -> None:
    """🔴 EVERY WRITE ON THIS SURFACE HAS TO CARRY THE SAME TWO FACTS, because a
    model reads ONE description before it acts and may never read the other six:
    a row can only be deleted while it is EMPTY (Ebteqdesk refuses otherwise),
    and the last step of emptying one is a person's job because this API deletes
    no article. The four creates and renames used to say "there is no delete
    tool", which stopped being true — a description that is merely stale is worse
    than one that is missing, since it is read as current."""
    description = described(tools[name])
    lowered = description.lower()

    assert "refus" in lowered, name
    assert "delete-article tool" in lowered, name
    assert "Ebteqdesk UI" in description or "web UI" in description, name


@pytest.mark.parametrize("name", ["create_kb_category", "create_kb_folder"])
async def test_the_create_tools_warn_against_retrying_a_timeout(
    tools, name: str
) -> None:
    """The server's standing rule for every write on this surface: a call that
    appears to have failed may well have landed."""
    description = described(tools[name])

    assert "never retry" in description.lower(), name
    assert "`list_kb_tree`" in description, name


@pytest.mark.parametrize("name", sorted(STRUCTURE_TOOLS))
async def test_every_structure_tool_names_the_scope_and_the_role_limit(
    tools, name: str
) -> None:
    """🔴 The likeliest support question. `kb:write` resolves only when the key
    carries it AND the owner's role holds `kb.manage` — administrator and
    supervisor only — so an agent-role account is refused whatever key is minted.
    A description that named only the scope sends that user to mint a key, which
    cannot possibly help."""
    description = described(tools[name])

    assert "`kb:write`" in description, name
    assert "`kb.manage`" in description, name
    assert "ADMINISTRATOR AND SUPERVISOR ONLY" in description, name


@pytest.mark.parametrize("name", ["create_kb_category", "create_kb_folder"])
async def test_the_create_tools_explain_the_derived_slug_collision(
    tools, name: str
) -> None:
    """A 422 on `name` for a name the caller has never used before is baffling
    unless the tool has said the check is on the DERIVED slug."""
    description = described(tools[name])

    assert "THE SLUG IS DERIVED FROM THE NAME AND CANNOT BE SET" in description, name
    assert "422" in description, name
    assert "COLLIDE" in description, name


async def test_the_two_slug_scopes_are_stated_and_not_confused(tools) -> None:
    """Category slugs are GLOBAL; folder slugs are unique only WITHIN their
    category. Getting it the wrong way round makes a caller rename a folder that
    did not need renaming, or expect a clash that never comes."""
    category = described(tools["create_kb_category"])
    folder = described(tools["create_kb_folder"])

    assert "unique GLOBALLY" in category
    assert "ONLY WITHIN THEIR CATEGORY" in folder
    assert '"FAQ" under Billing and "FAQ" under Account are both fine' in folder


async def test_the_update_tools_state_the_absent_versus_empty_rule(tools) -> None:
    """Shared with `update_kb_article`, and it is the rule most likely to be got
    wrong: omitting is not clearing, and clearing is an empty string."""
    for name in ("update_kb_category", "update_kb_folder"):
        description = described(tools[name])

        assert "OMITTED ARGUMENTS ARE NOT EDITED" in description, name
        assert "EMPTY STRING is an edit that CLEARS the field" in description, name


async def test_the_folder_create_tool_points_back_at_propose(tools) -> None:
    """The two-call path this whole feature exists to enable: create a folder,
    then file into it with the id from the response."""
    description = described(tools["create_kb_folder"])

    assert "is the `kb_folder_id` `propose_kb_article` takes" in description


async def test_the_server_instructions_state_the_effective_visibility_rule(
    tools,
) -> None:
    """🔴 ADR-0007 NAMES THE INSTRUCTION BLOCK, NOT ONLY THE DOCSTRINGS.

    The block is read ONCE, before any tool is chosen, so a false claim in it is
    the most expensive kind: it frames every later call and a model has no
    reason to revisit it. It said the KB tools "return only published, public
    articles", which was a per-FOLDER statement — true while a folder was the
    only place visibility lived, and false the moment an article could override
    it in both directions.

    This test exists because that sentence survived the first pass of this
    feature: the tool docstrings were updated and the block was not, and nothing
    in this suite asserted its visibility claim, so nothing failed. The
    docstring-level tests cannot cover it — `described()` reads a TOOL, and the
    block belongs to no tool.
    """
    instructions = " ".join((srv.mcp.instructions or "").split())

    # The corpus claim, narrowed from "public" to "publicly-visible", because
    # the resolution is no longer a property of the folder alone.
    assert "publicly-visible articles even for an administrator" in instructions

    # And the rule spelled out, in both directions — one direction alone would
    # let a model conclude the other is impossible.
    assert (
        "an `agents` folder in `list_kb_tree` may hold one article "
        "`search_kb_articles` returns" in instructions
    )
    assert "a `public` folder may hold one it does not" in instructions

    # 🔴 THE INFERENCE A MODEL MUST NOT MAKE, stated as a prohibition rather
    # than left to be deduced from the mechanism.
    assert (
        "Never conclude that a particular article is or is not reachable from "
        "the `visibility` of the folder it sits in" in instructions
    )

    # ⚠️ And why the gap is nonetheless safe for THIS client: a folder's level
    # is exactly what this API's own writes get. Without this half the block
    # would read as a reason to distrust `list_kb_tree` altogether.
    assert "what its articles INHERIT" in instructions
    assert "no tool here can set a per-article override" in instructions


async def test_the_server_instructions_carry_the_new_surface(tools) -> None:
    """The instructions block is read once, before any tool is chosen, so the
    two rules a model most needs up front belong there: where folder ids come
    from, and that these writes cannot set visibility."""
    instructions = " ".join((srv.mcp.instructions or "").split())

    assert "twenty-one tools that read and twenty-one that WRITE" in instructions

    # ⚠️ This used to assert "`list_kb_tree` IS THE ONLY PLACE ONE COMES FROM",
    # and that sentence is no longer TRUE: `list_kb_categories` and
    # `list_kb_folders` return the same ids. The claim was updated rather than
    # dropped, because the rule it protects is unchanged — the ARTICLE payloads
    # still carry no ids, so a folder id still comes only from a structure tool
    # and never from a search result.
    assert "ONLY THE STRUCTURE TOOLS RETURN ONE" in instructions
    assert "`{slug, name}` pairs and no ids at all" in instructions

    # …and the new pair are documented as projections, not as cheap lookups.
    assert "FLAT PROJECTIONS OVER THE SAME ONE CALL" in instructions
    assert "never in a loop" in instructions

    assert "cannot set VISIBILITY" in instructions
    assert "ADMINISTRATOR AND SUPERVISOR ONLY" in instructions

    for name in STRUCTURE_TOOLS:
        assert f"`{name}`" in instructions, name


async def test_exactly_two_tools_delete_anything_and_neither_touches_an_article(
    tools,
) -> None:
    """🔴 THE INVERSE OF THE TEST THAT USED TO STAND HERE, which asserted no tool
    was called `delete` at all. Two now are, and the property worth guarding
    moved rather than disappeared: the destructive surface is EXACTLY these two
    structure-level tools, and an article, a ticket, a comment or a note delete
    appearing would be a new and much larger claim about what this server can
    destroy. An article delete in Ebteqdesk has no undo, no trash and no version
    history, which is precisely why the two below refuse rather than cascade."""
    every = {tool.name for tool in await srv.mcp.list_tools()}

    destructive = {
        name for name in every if "delete" in name or "destroy" in name or "remove" in name
    }

    assert destructive == {"delete_kb_category", "delete_kb_folder"}


async def test_the_article_tools_still_do_not_mention_a_visibility_argument(
    tools,
) -> None:
    """Cross-check: adding a structure surface must not have grown the ARTICLE
    tools a way to place content publicly either."""
    for name in ("propose_kb_article", "update_kb_article"):
        assert "visibility" not in tools[name].input_schema.get("properties", {})

    # And the article payload fixture is still the draft case.
    assert kb_article_payload()["data"]["status"] == "draft"
