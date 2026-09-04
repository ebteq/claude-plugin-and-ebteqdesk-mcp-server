"""`reorder_kb_children`, `list_kb_categories`, `list_kb_folders`.

Three tools that close the last gaps in the KB surface: one write over three
endpoints, and two flat READS that are projections over an endpoint that already
existed.

---------------------------------------------------------------------------
What is actually at risk here, and therefore what is asserted
---------------------------------------------------------------------------
1. THE WHOLE-SET RULE. `ordered_ids` must be every sibling id, never a delta.
   The server enforces it with a 422, so the client cannot get it wrong in a way
   that corrupts data — but it CAN get it wrong in a way that makes the tool
   useless, by not telling the model the rule. The description assertions below
   are the review gate on that text.

2. THE `parent_id` MISMATCH. One tool fronts three endpoints with different
   arities. Omitting `parent_id` on "folders" would build a URL that is a
   different endpoint; passing it on "categories" would be an argument with
   nowhere to go that reads to a caller as having taken effect. Both are refused
   CLIENT-SIDE, before any request, and both refusals are asserted to have sent
   nothing.

3. THE PROJECTION HONESTY. `list_kb_categories` and `list_kb_folders` each make
   exactly ONE call — to `/api/v1/kb/tree` — because there is no categories or
   folders endpoint on this API. A model that believed otherwise would call
   `list_kb_folders` once per category. So the request COUNT and the request PATH
   are asserted, not just the returned shape.
"""

from __future__ import annotations

import json as jsonlib

import httpx2
import pytest

from conftest import (
    always_json,
    kb_category_row,
    kb_folder_row,
    kb_tree_payload,
    scope_refusal,
)
from mcp.server.mcpserver.exceptions import ToolError

from ebteqdesk_mcp import server as srv
from ebteqdesk_mcp.client import EbteqdeskClient
from ebteqdesk_mcp.config import Config
from ebteqdesk_mcp.errors import InvalidRequestError, ScopeError


def body_of(request: httpx2.Request) -> dict:
    return jsonlib.loads(request.content)


def described(tool) -> str:
    return " ".join((tool.description or "").split())


@pytest.fixture
async def tools() -> dict[str, object]:
    return {tool.name: tool for tool in await srv.mcp.list_tools()}


@pytest.fixture
def wired(monkeypatch):
    def install(handler):
        config = Config(base_url="https://ebteqdesk.test", token="6|t", timeout=5.0)
        client = EbteqdeskClient(config, transport=httpx2.MockTransport(handler))
        monkeypatch.setattr(srv, "_client", client)
        return client

    yield install

    monkeypatch.setattr(srv, "_client", None)


#: The server's 422 for a posted set that is not the current one, verbatim from
#: Api\V1\KbReorderController::rejectBody() and App\Kb\Ordering::assertSameSet().
#: Nothing under test parses it; it is carried to the user.
STALE_ORDER = always_json(
    422,
    {
        "error": "The request body is not valid.",
        "errors": {
            "ids": [
                "The order must list every item exactly once. "
                "It is out of date — reload and try again."
            ]
        },
    },
)

CATEGORIES_OK = always_json(200, {"data": [kb_category_row(id=3), kb_category_row(id=5)]})
FOLDERS_OK = always_json(200, {"data": [kb_folder_row(id=7), kb_folder_row(id=9)]})
ARTICLES_OK = always_json(
    200,
    {
        "data": [
            {"id": 88, "title": "Resetting your password", "position": 0},
            {"id": 90, "title": "Two-factor codes", "position": 1},
        ]
    },
)


# --------------------------------------------------------------------------- #
# Transport — verbs, paths, bodies
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "call, expected_path, handler",
    [
        (
            lambda c: c.reorder_kb_categories(ids=[5, 3]),
            "/api/v1/kb/categories/order",
            CATEGORIES_OK,
        ),
        (
            lambda c: c.reorder_kb_folders(3, ids=[9, 7]),
            "/api/v1/kb/categories/3/folders/order",
            FOLDERS_OK,
        ),
        (
            lambda c: c.reorder_kb_articles(7, ids=[90, 88]),
            "/api/v1/kb/folders/7/articles/order",
            ARTICLES_OK,
        ),
    ],
)
async def test_each_reorder_is_a_put_to_its_own_path(
    make_client, call, expected_path, handler
) -> None:
    """PUT, not POST or PATCH — a reorder REPLACES the whole list, which is what
    PUT means, and the server routes only that verb. A wrong verb would 405 with
    a plausible-looking error and nothing else here would notice."""
    client, recorder = make_client(handler)

    async with client:
        await call(client)

    assert recorder.last.method == "PUT"
    assert recorder.last.url.path == expected_path
    assert len(recorder.requests) == 1


async def test_the_body_is_the_ids_list_and_nothing_else(make_client) -> None:
    """🔴 `{"ids": [...]}` and NO delta fields. There is no `position`, no
    `move_to`, no `after_id` on the server, so a client that sent one would be
    inventing a request shape the API never described — and the day one of those
    names becomes real, the invented one starts meaning something."""
    client, recorder = make_client(CATEGORIES_OK)

    async with client:
        await client.reorder_kb_categories(ids=[5, 3])

    assert body_of(recorder.last) == {"ids": [5, 3]}


async def test_the_posted_order_is_preserved_exactly(make_client) -> None:
    """The list is sent in the caller's order and is NOT sorted, deduplicated or
    otherwise tidied. The order IS the payload; a client that sorted it would
    silently reorder the knowledge base alphabetically by id."""
    client, recorder = make_client(CATEGORIES_OK)

    async with client:
        await client.reorder_kb_categories(ids=[9, 3, 7, 1])

    assert body_of(recorder.last)["ids"] == [9, 3, 7, 1]


async def test_the_payload_is_returned_verbatim(make_client) -> None:
    client, _ = make_client(ARTICLES_OK)

    async with client:
        result = await client.reorder_kb_articles(7, ids=[90, 88])

    assert result["data"][0] == {
        "id": 88,
        "title": "Resetting your password",
        "position": 0,
    }


# --------------------------------------------------------------------------- #
# Client-side argument refusals
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "ids",
    [
        7,  # the "move item 7" shape a caller reaching for a delta writes
        "7,3,9",  # iterable, and would arrive as a list of characters
        [],  # every reorderable sibling set has at least one member
        [7, True],  # a bool is an int in Python and would become id 1
        [7, 0],
        [7, -1],
        [7, "3"],
        [7, None],
    ],
)
async def test_a_malformed_ids_argument_is_refused_before_any_request(
    make_client, ids
) -> None:
    """⚠️ SHAPE ONLY. Whether the list is the whole set is the server's call and
    cannot be anyone else's — it depends on rows that may change between this
    call and the UPDATE. What is worth refusing here is the shape, because each
    of these produces a confusing server message instead of a clear one."""
    client, recorder = make_client(CATEGORIES_OK)

    async with client:
        with pytest.raises(ValueError):
            await client.reorder_kb_categories(ids=ids)  # type: ignore[arg-type]

    assert recorder.requests == []


async def test_the_shape_refusal_states_the_whole_set_rule(make_client) -> None:
    """A caller who got the SHAPE wrong is very likely about to get the SET
    wrong too, so the local message teaches the rule rather than only naming a
    type."""
    client, _ = make_client(CATEGORIES_OK)

    async with client:
        with pytest.raises(ValueError) as raised:
            await client.reorder_kb_categories(ids=7)  # type: ignore[arg-type]

    message = str(raised.value)

    assert "WHOLE ordered sibling set" in message
    assert "never a delta" in message
    assert "422" in message


async def test_a_bad_parent_id_is_refused_before_any_request(make_client) -> None:
    """`_kb_structure_id` guards both parents — a bool would silently become
    category 1 or folder 1, which on a live install is a real row somebody's
    articles are under."""
    client, recorder = make_client(FOLDERS_OK)

    async with client:
        for bad in (True, 0, -1, "3", None):
            with pytest.raises(ValueError):
                await client.reorder_kb_folders(bad, ids=[7])  # type: ignore[arg-type]

    assert recorder.requests == []


# --------------------------------------------------------------------------- #
# The server's refusals
# --------------------------------------------------------------------------- #


async def test_a_stale_set_surfaces_as_the_servers_422(make_client) -> None:
    """🔴 THE LOAD-BEARING REFUSAL. A list that is not exactly the current
    sibling set is refused and NOTHING is written — the property that turns a
    stale caller into an error instead of a corrupted order. The client passes
    the server's own sentence through; it does not paraphrase it."""
    client, _ = make_client(STALE_ORDER)

    async with client:
        with pytest.raises(InvalidRequestError) as raised:
            await client.reorder_kb_categories(ids=[3])

    assert "every item exactly once" in str(raised.value)
    assert "out of date" in str(raised.value)


async def test_a_scope_refusal_is_not_narrowed_into_the_escalation_story(
    make_client,
) -> None:
    """The KB routes ask for `kb:write` and never for `escalation:write`, so the
    ticket-side narrowers must not fire here. They branch on `required_scope`
    against a constant, so this is a guard against a future `on_scope_error`
    being attached to the wrong method."""
    client, _ = make_client(scope_refusal("kb:write"))

    async with client:
        with pytest.raises(ScopeError) as raised:
            await client.reorder_kb_categories(ids=[3])

    assert raised.value.required_scope == "kb:write"
    assert "ESCALATED" not in str(raised.value)


# --------------------------------------------------------------------------- #
# The one tool over three endpoints
# --------------------------------------------------------------------------- #


async def test_the_tool_is_registered_with_the_three_arguments(tools) -> None:
    tool = tools["reorder_kb_children"]
    properties = tool.input_schema.get("properties", {})

    assert set(properties) == {"scope", "ordered_ids", "parent_id"}

    # `parent_id` is optional in the SCHEMA because it is required for two of the
    # three scopes and refused for the third — a condition JSON Schema cannot
    # express here, and which the tool therefore enforces itself with a message
    # naming the scope. See the two tests below.
    assert set(tool.input_schema.get("required", [])) == {"scope", "ordered_ids"}

    # `scope` is a closed vocabulary, so a typo is caught by the SDK before any
    # of this file's code runs.
    assert properties["scope"]["enum"] == ["categories", "folders", "articles"]


@pytest.mark.parametrize(
    "scope, parent_id, path",
    [
        ("categories", None, "/api/v1/kb/categories/order"),
        ("folders", 3, "/api/v1/kb/categories/3/folders/order"),
        ("articles", 7, "/api/v1/kb/folders/7/articles/order"),
    ],
)
async def test_each_scope_dispatches_to_its_endpoint(
    wired, scope, parent_id, path
) -> None:
    """One tool, three endpoints, and the discriminator is `scope`. A dispatch
    that sent "articles" to the folders endpoint would answer 200 against a
    folder id that happened to exist and reorder the wrong list."""
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"data": []})

    wired(handler)

    arguments = {"scope": scope, "ordered_ids": [1, 2]}

    if parent_id is not None:
        arguments["parent_id"] = parent_id

    result = await srv.mcp.call_tool("reorder_kb_children", arguments)

    assert not result.is_error
    assert len(seen) == 1
    assert seen[0].method == "PUT"
    assert seen[0].url.path == path


async def test_categories_refuses_a_parent_id_before_sending_anything(wired) -> None:
    """🔴 Categories are the top level and have no parent, so there is nothing
    for `parent_id` to name and no URL segment to put it in. Silently ignoring it
    would read to a caller as having scoped the reorder — the most dangerous
    outcome available here, since the un-scoped call rewrites EVERY category."""
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"data": []})

    wired(handler)

    with pytest.raises(ToolError) as raised:
        await srv.mcp.call_tool(
            "reorder_kb_children",
            {"scope": "categories", "ordered_ids": [1, 2], "parent_id": 3},
        )

    assert "must be omitted when scope='categories'" in str(raised.value)
    assert "have no parent" in str(raised.value)

    # NOTHING was sent. A refusal after the request would be no refusal at all.
    assert seen == []


@pytest.mark.parametrize(
    "scope, parent, source",
    [
        ("folders", "category", "`list_kb_categories`"),
        ("articles", "folder", "`list_kb_folders`"),
    ],
)
async def test_the_other_two_scopes_require_a_parent_id(
    wired, scope, parent, source
) -> None:
    """Omitting it would build a URL for a different endpoint entirely. The
    message names the scope, what the id IS, and where to get one — a bare
    "parent_id is required" leaves a model guessing which id it means."""
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"data": []})

    wired(handler)

    with pytest.raises(ToolError) as raised:
        await srv.mcp.call_tool(
            "reorder_kb_children", {"scope": scope, "ordered_ids": [1, 2]}
        )

    message = str(raised.value)

    assert f"required when scope='{scope}'" in message
    assert f"the id of the {parent}" in message
    assert source in message

    assert seen == []


# --------------------------------------------------------------------------- #
# The description — the only guardrail
# --------------------------------------------------------------------------- #


async def test_it_leads_with_its_side_effect(tools) -> None:
    first_line = (
        tools["reorder_kb_children"].description or ""
    ).strip().splitlines()[0]

    assert first_line.startswith("WRITES TO EBTEQDESK")
    assert "REAL knowledge base list" in first_line


async def test_it_states_the_whole_set_rule_loudly(tools) -> None:
    """🔴 THE ASSERTION THIS TOOL LIVES OR DIES BY.

    "Move item X to the top" is the operation a model has in mind, and a
    one-element `ordered_ids` is what that intent produces. The description has
    to say the rule in capitals, show the wrong call next to the right one, and
    say what happens when it is broken — 422, nothing written, not a partial
    reorder. A refactor that trims this to a tidy one-liner is a behaviour
    change, because the behaviour is what a model does before calling.
    """
    description = described(tools["reorder_kb_children"])

    assert "YOU MUST POST EVERY SIBLING ID, NOT A DELTA" in description
    assert "THE WHOLE LIST" in description

    # The worked example, both halves.
    assert "WRONG ordered_ids=[9]" in description
    assert "RIGHT ordered_ids=[9, 7, 3]" in description

    # And the consequence of getting it wrong, in the same breath.
    assert "IS A 422 AND NOTHING IS WRITTEN" in description
    assert "not a partial reorder" in description
    assert "Sending a subset is refused" in description


async def test_it_warns_that_an_internal_looking_folder_can_still_be_public(
    tools,
) -> None:
    """🔴 THE BLAST RADIUS IS WIDER THAN THE FOLDER'S OWN `visibility` (ADR-0007).

    This said "changes what every agent, and ON A PUBLIC FOLDER every customer,
    sees" — which told a model that reordering an `agents` folder is invisible
    outside the desk. That is now false. An article can override its folder's
    visibility, and the help centre orders the "related articles" beside such an
    article by exactly the positions this tool writes, from the same
    effective-visibility corpus. So a reorder inside an internal-looking folder
    can change a page anonymous visitors read.

    The old wording was wrong in the direction that invites a careless call, so
    the correction is asserted rather than left to the docstring's good
    intentions.
    """
    description = described(tools["reorder_kb_children"])

    assert "DO NOT ASSUME AN `agents` FOLDER IS INVISIBLE OUTSIDE THE DESK HERE" in description
    assert "an article can carry its own visibility overriding its folder's" in description

    # The mechanism, named — a warning a model cannot connect to anything is a
    # warning it discounts.
    assert '"related articles"' in description
    assert "can still change a public page" in description

    # And the old, narrower claim is gone rather than merely supplemented.
    assert "on a public folder every customer" not in description


async def test_it_explains_that_a_422_is_information(tools) -> None:
    """A model that reads the 422 as a bug will retry it unchanged forever. It is
    a statement about the caller's view of the list being stale."""
    description = described(tools["reorder_kb_children"])

    assert "your list is out of date" in description
    assert "re-read it" in description


async def test_it_tells_the_caller_to_read_the_order_first(tools) -> None:
    """There is no undo and the previous order is not stored anywhere, so the
    only way back is an order the caller already read."""
    description = described(tools["reorder_kb_children"])

    assert "there is no undo" in description.lower()
    assert "READ THE CURRENT ORDER BEFORE YOU CHANGE IT" in description


async def test_it_documents_the_parent_id_rule_per_scope(tools) -> None:
    description = described(tools["reorder_kb_children"])

    assert "Omit `parent_id`; categories have no parent" in description
    assert "`parent_id` is that CATEGORY's id" in description
    assert "`parent_id` is that FOLDER's id" in description
    assert "REQUIRED for \"folders\" and \"articles\", and REFUSED for" in description


async def test_it_says_where_article_ids_come_from(tools) -> None:
    """⚠️ The gap a caller falls into: `list_kb_tree` gives category and folder
    ids but NOT article ids — it carries an `articles_count`. Without this
    sentence, `scope="articles"` is a tool with no documented source for its
    required argument."""
    description = described(tools["reorder_kb_children"])

    assert "ARTICLE ids come from none of them" in description
    assert "include DRAFTS" in description


async def test_it_names_the_scope_and_the_retry_exception(tools) -> None:
    description = described(tools["reorder_kb_children"])

    assert "`kb:write`" in description
    assert "ADMINISTRATOR AND SUPERVISOR" in description
    assert "`kb:read` cannot reach this" in description

    # The one write on this server that IS safe to retry — stated because the
    # module-level rule is otherwise absolute.
    assert "SAFE TO RETRY" in description


async def test_no_reorder_tool_offers_a_dry_run(tools) -> None:
    properties = set(tools["reorder_kb_children"].input_schema.get("properties", {}))

    assert not properties & {"dry_run", "preview", "confirm", "simulate"}


# --------------------------------------------------------------------------- #
# The two flat projections
# --------------------------------------------------------------------------- #


async def test_list_kb_categories_makes_one_tree_call_and_drops_the_folders(
    wired,
) -> None:
    """🔴 ONE call, to `/api/v1/kb/tree`. There is no categories endpoint on this
    API, and the request PATH is asserted rather than the result shape alone —
    the shape would keep passing if somebody added a second round trip."""
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            json=kb_tree_payload(
                [
                    kb_category_row(id=3, folders=[kb_folder_row(id=7)]),
                    kb_category_row(id=5, name="Billing", slug="billing", folders=[]),
                ]
            ),
        )

    wired(handler)

    result = await srv.mcp.call_tool("list_kb_categories", {})

    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/api/v1/kb/tree"

    rows = result.structured_content["data"]

    assert [row["id"] for row in rows] == [3, 5]
    # The folders are dropped — that is the whole projection.
    assert all("folders" not in row for row in rows)
    # …and nothing else is. The remaining keys are the tree's own.
    assert set(rows[0]) == {"id", "name", "slug", "description", "position"}


async def test_list_kb_folders_flattens_every_category(wired) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            json=kb_tree_payload(
                [
                    kb_category_row(
                        id=3,
                        folders=[
                            kb_folder_row(id=7, kb_category_id=3),
                            kb_folder_row(id=8, kb_category_id=3, position=1),
                        ],
                    ),
                    kb_category_row(
                        id=5,
                        name="Billing",
                        slug="billing",
                        folders=[kb_folder_row(id=9, kb_category_id=5)],
                    ),
                ]
            ),
        )

    wired(handler)

    result = await srv.mcp.call_tool("list_kb_folders", {})

    assert len(seen) == 1
    assert seen[0].url.path == "/api/v1/kb/tree"

    rows = result.structured_content["data"]

    assert [row["id"] for row in rows] == [7, 8, 9]
    # The full folder shape survives — this flattens, it does not narrow.
    assert set(rows[0]) == {
        "id",
        "kb_category_id",
        "name",
        "slug",
        "description",
        "visibility",
        "position",
        "articles_count",
    }


async def test_list_kb_folders_filters_by_category_after_the_fetch(wired) -> None:
    """⚠️ The filter is applied to a tree that has ALREADY arrived. Filtering
    saves the caller reading rows, not the server sending them — which is why the
    description forbids calling this once per category."""
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            json=kb_tree_payload(
                [
                    kb_category_row(id=3, folders=[kb_folder_row(id=7, kb_category_id=3)]),
                    kb_category_row(
                        id=5,
                        name="Billing",
                        slug="billing",
                        folders=[kb_folder_row(id=9, kb_category_id=5)],
                    ),
                ]
            ),
        )

    wired(handler)

    result = await srv.mcp.call_tool("list_kb_folders", {"kb_category_id": 5})

    assert [row["id"] for row in result.structured_content["data"]] == [9]

    # The WHOLE tree was still fetched, and there is no query string pretending
    # otherwise.
    assert len(seen) == 1
    assert seen[0].url.path == "/api/v1/kb/tree"
    assert seen[0].url.query in (b"", None)


async def test_an_unknown_category_filter_is_an_empty_list_not_an_error(wired) -> None:
    """It filters a list it already holds and has no 404 to give. Documented
    rather than faked into one — a synthesised 404 would claim knowledge this
    tool does not have."""
    wired(always_json(200, kb_tree_payload([kb_category_row(id=3)])))

    result = await srv.mcp.call_tool("list_kb_folders", {"kb_category_id": 999})

    assert result.structured_content["data"] == []


async def test_an_empty_knowledge_base_projects_to_empty_lists(wired) -> None:
    wired(always_json(200, {"data": []}))

    assert (await srv.mcp.call_tool("list_kb_categories", {})).structured_content == {
        "data": []
    }

    assert (await srv.mcp.call_tool("list_kb_folders", {})).structured_content == {
        "data": []
    }


async def test_both_projections_say_they_are_not_cheaper_than_the_tree(tools) -> None:
    """🔴 THE ASSERTION THAT KEEPS THESE TWO HONEST.

    They look like narrow lookups and are not. A model that thought
    `list_kb_folders(kb_category_id=3)` were a scoped query would call it in a
    loop over categories and fetch the entire tree once per iteration. Both
    descriptions must say so in their own words, and both must forbid the loop.
    """
    categories = described(tools["list_kb_categories"])
    folders = described(tools["list_kb_folders"])

    assert "IT IS NOT CHEAPER THAN `list_kb_tree`" in categories
    assert "There is no categories-only endpoint on this API" in categories
    assert "do not call it in a loop" in categories.lower()

    assert "IT IS NOT CHEAPER THAN `list_kb_tree`" in folders
    assert "There is no folders endpoint on this API" in folders
    assert "Never call this once per category" in folders


async def test_the_projections_name_their_scope_and_the_visibility_trap(tools) -> None:
    for name in ("list_kb_categories", "list_kb_folders"):
        description = described(tools[name])

        # `kb:write` and NOT `kb:read` — the tree is the AUTHORING structure.
        assert "`kb:write`" in description
        assert "ADMINISTRATOR AND SUPERVISOR ONLY" in description
        # Internal names must not be repeated into a public reply.
        assert "do not repeat them into a public reply" in description

    # The folder tool carries the rule that decides whether content is ever seen.
    folders = described(tools["list_kb_folders"])

    # 🔴 SINCE ADR-0007 THE FOLDER'S LEVEL IS WHAT ARTICLES INHERIT, NOT A
    # STATEMENT ABOUT THE ONES ALREADY IN IT. An article can carry its own
    # visibility overriding its folder's in both directions, so the old wording
    # ("ONLY A `public` FOLDER'S PUBLISHED ARTICLES REACH A CUSTOMER") is now
    # false in both directions and a model acting on it would tell a user an
    # article is internal when it is public. What stays true — and is what this
    # tool's own writes get — is the INHERIT half.
    assert "the level an article filed here INHERITS" in folders
    assert "nothing on this API can set a per-article override" in folders
    assert "NOT a statement about the articles already in the folder" in folders
    assert "`articles_count` includes drafts" in folders
    # …and the trap in the flat view specifically: position repeats.
    assert "It is not a global rank" in folders


async def test_the_projections_are_not_write_tools(tools) -> None:
    """They read. A description that opened with a WRITES line would be false,
    and `test_server_tools.test_every_write_tool_leads_with_its_side_effect`
    parametrises over the write roster — so this is the other half of that
    guard."""
    for name in ("list_kb_categories", "list_kb_folders"):
        assert not (tools[name].description or "").strip().startswith("WRITES")
