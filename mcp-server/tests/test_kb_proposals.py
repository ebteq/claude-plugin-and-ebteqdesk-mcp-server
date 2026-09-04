"""`list_kb_proposals` — finding a rejection when the `reference` is gone.

Three properties here are silent when they break, and all three are properties
of the DESCRIPTION as much as of the code:

  - THE LIST IS INSTALLATION-WIDE. It returns every article carrying a review
    state, whoever proposed it — the same shape of claim `list_escalations`
    carries, for the same unfixable reason: an API key identifies an ACCOUNT and
    not an agent. A description saying "your proposals" would be acted on, and
    the payload cannot contradict it.

  - IT IS A READ ON A WRITE SCOPE. `kb:write` gates it because its corpus is
    drafts, and `kb:read` is deliberately the one scope with no role side. A
    tool that drifted into the write roster would be counted as a state change
    it does not make; a tool that drifted onto `kb:read` would hand the public
    help-corpus scope an enumeration over the whole knowledge base.

  - `review_state=none` IS A 422, NOT AN EMPTY LIST. `none` is every
    hand-written article that was never submitted. An empty list would read as
    "you have no unreviewed articles" — false, and unactionable.

The `per_page` value is passed through UNVALIDATED on purpose, for
`search_kb_articles`'s reason: clamping locally would hide a caller's mistake and
make this client disagree with curl.
"""

from __future__ import annotations

import inspect

import httpx2
import pytest

from conftest import (
    always_json,
    kb_proposal_list,
    kb_proposal_row,
    kb_review_state_refusal,
    scope_refusal,
)
from mcp.server.mcpserver.exceptions import ToolError

from ebteqdesk_mcp import server as srv
from ebteqdesk_mcp.client import EbteqdeskClient
from ebteqdesk_mcp.config import Config


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


# --------------------------------------------------------------------------- #
# The client
# --------------------------------------------------------------------------- #


async def test_it_gets_the_two_segment_literal_path(make_client) -> None:
    """🔴 `/api/v1/kb/proposals`, NOT `/api/v1/kb/articles/proposals`. The
    second segment is a literal on both sides of the comparison with
    `kb/articles/{slug}`, which is the structural reason the two routes can
    never collide however the server registers them."""
    client, recorder = make_client(always_json(200, kb_proposal_list()))

    await client.list_kb_proposals()

    assert recorder.last.method == "GET"
    assert recorder.last.url.path == "/api/v1/kb/proposals"


async def test_no_filter_sends_no_parameters_at_all(make_client) -> None:
    """An omitted filter means all three listable states — the SERVER's default,
    expressed by sending nothing. A client that helpfully sent
    `review_state=pending` would silently hide every rejection, which is the one
    thing this tool exists to surface."""
    client, recorder = make_client(always_json(200, kb_proposal_list()))

    await client.list_kb_proposals()

    assert dict(recorder.last.url.params) == {}


@pytest.mark.parametrize("state", ["pending", "approved", "rejected"])
async def test_each_legal_state_is_sent_verbatim(make_client, state: str) -> None:
    client, recorder = make_client(always_json(200, kb_proposal_list()))

    await client.list_kb_proposals(review_state=state)

    assert dict(recorder.last.url.params) == {"review_state": state}


async def test_paging_parameters_are_sent(make_client) -> None:
    client, recorder = make_client(always_json(200, kb_proposal_list()))

    await client.list_kb_proposals(review_state="rejected", per_page=100, page=3)

    assert dict(recorder.last.url.params) == {
        "review_state": "rejected",
        "per_page": "100",
        "page": "3",
    }


async def test_per_page_is_not_clamped_locally(make_client) -> None:
    """🔴 SENT AS GIVEN. Out of range is the server's 422, never a silent clamp —
    clamping here would hide the caller's mistake and make this client disagree
    with curl about the same request."""
    client, recorder = make_client(always_json(200, kb_proposal_list()))

    await client.list_kb_proposals(per_page=500)

    assert dict(recorder.last.url.params)["per_page"] == "500"


async def test_an_illegal_review_state_is_the_servers_422_not_a_local_guess(
    make_client,
) -> None:
    """The vocabulary lives on the server. `none` is refused THERE, with a body
    naming the three legal values, rather than being second-guessed here — one
    place owning the list is what keeps the client and curl agreeing."""
    from ebteqdesk_mcp.errors import EbteqdeskError

    client, _ = make_client(kb_review_state_refusal())

    with pytest.raises(EbteqdeskError) as excinfo:
        await client.list_kb_proposals(review_state="none")

    assert "review_state" in str(excinfo.value) or "review state" in str(excinfo.value)


async def test_the_rows_come_back_verbatim(make_client) -> None:
    client, _ = make_client(always_json(200, kb_proposal_list()))

    body = await client.list_kb_proposals(review_state="rejected")

    row = body["data"][0]

    assert row["reference"] == "id:42"
    assert row["review"]["state"] == "rejected"
    assert row["review"]["note"] == "Name the exact error message the user sees."
    assert row["review"]["reviewed_by"] == {"id": 3, "name": "Dana Reyes"}
    assert body["meta"]["per_page"] == 25


async def test_a_row_carries_no_body_and_no_seo(make_client) -> None:
    """The shape is deliberately narrower than the single-article read.
    Twenty-five HTML bodies is a page measured in hundreds of kilobytes going
    into a model's context to answer a question the `review` block answers on
    its own."""
    client, _ = make_client(always_json(200, kb_proposal_list()))

    row = (await client.list_kb_proposals())["data"][0]

    assert "body_html" not in row
    assert "seo" not in row
    assert row["excerpt"].startswith("Open the portal")


async def test_the_client_offers_no_way_to_scope_the_list_to_the_caller(
    make_client,
) -> None:
    """🔴 THERE IS NO `mine` OR `author_id` PARAMETER, and the absence is
    deliberate rather than pending. `author_id` is the KEY OWNER — a real human,
    written identically by the web authoring UI and not moved by a PATCH — so a
    filter on it would look like an identity boundary while being none. If
    per-integration identity ever ships it arrives as a new argument, not as a
    change to this default."""
    signature = inspect.signature(EbteqdeskClient.list_kb_proposals)

    assert set(signature.parameters) == {"self", "review_state", "per_page", "page"}


# --------------------------------------------------------------------------- #
# The tool
# --------------------------------------------------------------------------- #


async def test_the_tool_is_registered_and_reads(tools) -> None:
    assert "list_kb_proposals" in tools


async def test_the_review_state_argument_is_an_enum_in_the_schema(tools) -> None:
    """🔴 THE SCHEMA HAS TO STAND ON ITS OWN. A description is prose a model may
    skim; the JSON Schema is what a client VALIDATES against and, for some
    clients, all they show. A fixed-value argument typed as a bare `str` is
    exactly how a first call gets built from the schema alone and refused by the
    API for a value the schema described as any string."""
    schema = tools["list_kb_proposals"].input_schema
    properties = schema.get("properties", {})

    assert set(properties) == {"review_state", "per_page", "page"}

    rendered = str(properties["review_state"])

    for value in ("pending", "approved", "rejected"):
        assert value in rendered

    # `none` is not an argument value at all — it is a 422.
    assert "'none'" not in rendered and '"none"' not in rendered


async def test_the_description_says_it_is_not_your_proposals(tools) -> None:
    """The `list_escalations` problem, a second time: nothing in the payload
    distinguishes the caller's own rows, so a description that said "your
    proposals" would be acted on."""
    description = described(tools["list_kb_proposals"])

    assert "THIS IS NOT \"YOUR\" PROPOSALS" in description
    assert "EVERY article in the" in description
    assert "installation" in description
    assert "`title` or `reference`" in description


async def test_the_description_says_it_needs_a_write_scope_and_writes_nothing(
    tools,
) -> None:
    description = described(tools["list_kb_proposals"])

    assert "Requires the `kb:write` scope" in description
    assert "not `kb:read`" in description
    assert "changes nothing" in description
    assert "ADMINISTRATOR AND SUPERVISOR ONLY" in description


async def test_the_description_names_the_note_and_the_action_to_take(tools) -> None:
    """🔴 The whole point of the list. A model that reads `review.note` and then
    resubmits unchanged has restamped `review_requested_at`, moved the article to
    the back of the queue, and erased the note it just read."""
    description = described(tools["list_kb_proposals"])

    assert "`review.note`" in description
    assert "`update_kb_article`" in description
    assert "back" in description and "queue" in description


async def test_the_description_says_there_is_no_none_state(tools) -> None:
    description = described(tools["list_kb_proposals"])

    assert "no `none` value" in description
    assert "422" in description


async def test_the_description_says_the_rows_carry_no_body(tools) -> None:
    """A model told the rows are the full article will not fetch the one it
    means, and will report an `excerpt` as the article's text."""
    description = described(tools["list_kb_proposals"])

    assert "no `body_html` on these rows" in description.lower()
    assert "`get_kb_article_review`" in description


async def test_the_tool_passes_its_arguments_through(wired) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200, json=kb_proposal_list([kb_proposal_row(id=7, reference="id:7")])
        )

    wired(handler)

    result = await srv.mcp.call_tool(
        "list_kb_proposals",
        {"review_state": "rejected", "per_page": 10, "page": 2},
    )

    assert seen[-1].url.path == "/api/v1/kb/proposals"
    assert dict(seen[-1].url.params) == {
        "review_state": "rejected",
        "per_page": "10",
        "page": "2",
    }
    assert "id:7" in str(result)


async def test_the_tool_needs_the_kb_write_scope(wired) -> None:
    """A `kb:read`-only key is refused, and the refusal names the scope so the
    remedy — a new key, not an administrator — is legible."""
    wired(scope_refusal("kb:write", requested=["kb:read"], scopes=["kb:read"]))

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool("list_kb_proposals", {})

    assert "kb:write" in str(excinfo.value)


async def test_the_instructions_carry_the_shared_list_warning() -> None:
    """`instructions` is read once, before any tool is chosen. The
    installation-wide claim belongs there as well as on the tool, because a
    model that has already decided what the list means will not re-read the
    description."""
    instructions = " ".join((srv.mcp.instructions or "").split())

    assert "`list_kb_proposals`" in instructions
    assert "SECOND SHARED LIST" in instructions
    assert "TWENTY-SIX tools require a write scope" in instructions
    assert "twenty-one tools that read and twenty-one that WRITE" in instructions
