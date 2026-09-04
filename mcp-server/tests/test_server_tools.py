"""The MCP layer: what tools exist, what their schemas say, and what a failing
tool call actually returns to a client.

The tool DESCRIPTIONS are asserted, not just their existence. They are the only
place a model learns the API's counter-intuitive rules, so a refactor that
shortens one of them is a behaviour change and should fail a test.
"""

from __future__ import annotations

import json as jsonlib

import httpx2
import pytest

from conftest import (
    ability_refusal,
    always_json,
    json_response,
    scope_refusal,
    ticket_payload,
)
from mcp.server.mcpserver.exceptions import ToolError

from ebteqdesk_mcp import server as srv
from ebteqdesk_mcp.client import EbteqdeskClient
from ebteqdesk_mcp.config import Config

READ_TOOL_NAMES = [
    "whoami",
    "list_tickets",
    "list_tickets_by_category",
    "list_escalations",
    "get_ticket",
    "get_ticket_comments",
    "get_ticket_attachment",
    "get_escalation_report",
    "get_reports_summary",
    "search_kb_articles",
    "get_kb_article",
    # The five READS gated on `kb:write`. `get_kb_article_review` and
    # `list_kb_proposals` have the write corpus — every article including
    # drafts; the three structure reads return the AUTHORING view — ids and
    # `agents`-only folders. Counting scopes and counting consequences give
    # different answers, and these are the tools where they differ: TWENTY-TWO
    # tools need a write scope, SEVENTEEN change state.
    "get_kb_article_review",
    # 🔴 THE ONLY READ ON THIS SERVER THAT ENUMERATES DRAFTS, and the second
    # tool after `list_escalations` whose corpus is the whole INSTALLATION
    # rather than the caller's own. Listed as a read because it writes nothing
    # at all — no review state, no timestamp, no content — which is exactly why
    # it must never drift into WRITE_TOOL_NAMES below.
    "list_kb_proposals",
    "list_kb_tree",
    # 🔴 PROJECTIONS OVER `list_kb_tree`, not endpoints of their own. Each
    # makes exactly one call — GET /api/v1/kb/tree — and filters the result
    # locally. Listed as reads because they read; asserted below to SAY they
    # are projections, because a model that thought otherwise would call
    # `list_kb_folders` once per category and fetch the whole tree each time.
    "list_kb_categories",
    "list_kb_folders",
    # 🔴 THE FIVE PROVISIONING READS. They do not read tickets or articles at
    # all — they read the account ROSTER: who exists, what role each person is
    # on, what groups exist, and what API keys an agent holds. All five need
    # `admin:read`, which resolves only for a role holding `admin.access`.
    #
    # `list_api_keys` is listed as a read and reveals NO secret: a key's
    # plaintext exists only in the `issue_api_key` response, once. If a tool
    # ever appears here that can read an existing credential back, that is the
    # thing this roster must catch.
    "list_agents",
    "get_agent",
    "list_roles",
    "list_groups",
    "list_api_keys",
]

#: The three ticket lists share one paging contract server-side (a single
#: PagesTicketLists trait), so they are asserted together — a cap that lives in
#: three places is a cap that is 20 in two of them.
TICKET_LIST_TOOL_NAMES = [
    "list_tickets",
    "list_tickets_by_category",
    "list_escalations",
]

WRITE_TOOL_NAMES = [
    "create_ticket",
    "comment_on_ticket",
    # The SAFE counterpart to the one above it. Both write into a ticket
    # thread; only one of them emails the requester, and the pair is asserted
    # together further down so neither description can drift into the other's
    # claim.
    "add_private_note",
    "escalate_ticket",
    "de_escalate_ticket",
    # The WORKING-STATE change, and the only write on this server that emails
    # nobody. It sits between de-escalate and close because that is where it
    # sits in server.py, and because the pair below it — this and close_ticket —
    # split the status vocabulary between them with no overlap: 1/2/3/8 here,
    # 4/5 there, and neither can reach the other's.
    "set_ticket_status",
    "close_ticket",
    "propose_kb_article",
    "update_kb_article",
    "create_kb_category",
    "update_kb_category",
    # 🔴 THE TWO TOOLS THAT DESTROY ANYTHING. Nothing else on this server
    # removes a row, and nothing on this API puts one back. They are listed
    # here — rather than being the thing this file guards AGAINST, which is
    # what the docstring below used to say — because a delete arriving is a
    # reviewed decision; a delete arriving SILENTLY is what must still fail.
    "delete_kb_category",
    "create_kb_folder",
    "update_kb_folder",
    "delete_kb_folder",
    # ONE tool over THREE endpoints, discriminated by `scope`. The requester
    # asked for one, and one is right: the rule a caller must not get wrong —
    # post the whole sibling set — is identical at all three levels, and three
    # tools would be three places for it to be stated and two places for it to
    # be softened.
    "reorder_kb_children",
    # 🔴 THE ONLY TOOL ON THIS SERVER THAT READS THE USER'S OWN FILESYSTEM.
    # Listed here because it writes, but the reason it needs its own review runs
    # the other way: every other tool's worst case is a wrong write to the DESK,
    # and this one can send a file OFF THE USER'S MACHINE. A second tool taking
    # a local path arriving silently is exactly what this roster must catch.
    "upload_kb_media",
    # 🔴 THE FOUR PROVISIONING WRITES, AND THEY ARE A DIFFERENT KIND OF WRITE
    # FROM EVERYTHING ABOVE THEM. The sixteen writes above change what the desk
    # SAYS — a ticket, a note, an article, a folder. These change WHO MAY ACT:
    # they create accounts that can sign in, move people between roles, and
    # hand out bearer credentials.
    #
    # Two of them return a secret exactly once (`create_agent`'s
    # `generatedPassword`, `issue_api_key`'s `plainTextToken`) and nothing can
    # read either again. A fifth provisioning write arriving silently is what
    # this roster must catch — in particular anything named `delete_agent`,
    # `reset_agent_password` or `change_agent_email`: all three are deliberately
    # browser-only, and a tool by any of those names appearing is the failure.
    "create_agent",
    "update_agent",
    "issue_api_key",
    "revoke_api_key",
]

TOOL_NAMES = READ_TOOL_NAMES + WRITE_TOOL_NAMES


@pytest.fixture
async def tools() -> dict[str, object]:
    return {tool.name: tool for tool in await srv.mcp.list_tools()}


def described(tool) -> str:
    """A tool description with its hard wrapping collapsed.

    Descriptions are docstrings, so every phrase below can straddle a newline
    depending on where the sentence happened to wrap. Asserting on the raw text
    makes these tests fail when a paragraph is re-flowed, which is not a
    behaviour change; asserting on collapsed whitespace makes them fail only
    when the words change, which is.
    """
    return " ".join((tool.description or "").split())


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
# Registration
# --------------------------------------------------------------------------- #


async def test_exactly_the_forty_two_tools_are_registered(tools) -> None:
    assert sorted(tools) == sorted(TOOL_NAMES)
    assert len(TOOL_NAMES) == 42
    assert len(READ_TOOL_NAMES) == 21
    # 🔴 TWENTY-ONE. Agent provisioning took the server from 33 to 42, the READ
    # half from 16 to 21 and the WRITE half from 17 to 21 — the first four
    # writes on this server that change WHO MAY ACT rather than what the desk
    # says. A twenty-second write arriving is what the test below exists to
    # catch.
    assert len(WRITE_TOOL_NAMES) == 21


async def test_the_write_surface_is_exactly_these_twenty_one(tools) -> None:
    """This list was empty until the write endpoints landed, and the test that
    asserted it stay empty was the read-only guard. It is replaced rather than
    deleted: the point was never "no writes", it was that a write tool must be a
    deliberate act with a reviewed description, not something that appears.

    An eighteenth write tool arriving silently — a publish, a bulk action,
    another delete, another tool that reads a local path — should still fail
    here. `publish` in particular: there is
    no publish endpoint on /api/v1 and adding one would defeat the KB review
    workflow, so a tool by that name appearing is exactly the thing this
    catches. So would `delete_kb_article`: THIS API DELETES NO ARTICLE, and the
    two structure deletes that DO exist refuse rather than cascade precisely so
    that neither becomes a back door to one.
    """
    assert sorted(name for name in tools if name in WRITE_TOOL_NAMES) == sorted(
        WRITE_TOOL_NAMES
    )
    assert sorted(set(tools) - set(WRITE_TOOL_NAMES)) == sorted(READ_TOOL_NAMES)


@pytest.mark.parametrize("name", TOOL_NAMES)
async def test_every_tool_has_a_description(tools, name: str) -> None:
    description = described(tools[name])
    assert len(description) > 120, f"{name} is under-documented"


async def test_argument_schemas(tools) -> None:
    def props(name: str) -> dict:
        return tools[name].input_schema.get("properties", {})

    assert props("whoami") == {}
    # `scope` is the OPT-IN account-wide read (R1): "mine" (the default, spelled
    # out) or "all", the latter honoured only for an account holding the
    # `ticket_all.view` ability and a 403 for anybody else. It is a free-form
    # string rather than an enum on purpose — the server owns the vocabulary and
    # answers an unknown value with a 403 that says so, and a local enum would
    # be a second copy of that list to keep in step.
    assert set(props("list_tickets")) == {"page", "per_page", "scope"}
    assert set(props("list_tickets_by_category")) == {
        "category",
        "page",
        "per_page",
        "scope",
    }
    assert set(props("list_escalations")) == {"page", "per_page"}
    assert set(props("get_escalation_report")) == {"date_from", "date_to"}
    assert set(props("search_kb_articles")) == {"query", "per_page", "page"}
    # 🔴 `locale` IS OPTIONAL AND ITS ABSENCE IS NOT `en`. Omitted reads the
    # LOCALE-FREE corpus, which deliberately includes articles that exist in only
    # one language and hands back their BASE text for them; given, it reads what
    # a reader of that language actually sees. Two different questions, and
    # making either the default for the other would break the one it is not.
    assert set(props("get_kb_article")) == {"slug", "locale"}

    assert set(props("create_ticket")) == {
        "subject", "description", "requester", "priority", "status",
        "category", "reference_number", "tags",
    }
    assert set(props("comment_on_ticket")) == {"ticket_id", "body"}
    # The note tool takes exactly what the reply tool takes. 🔴 NO `private`
    # ARGUMENT ANYWHERE: the two are separate tools on separate paths on
    # purpose, and a flag would make the requester-facing behaviour reachable by
    # forgetting a key.
    assert set(props("add_private_note")) == {"ticket_id", "body"}
    assert set(props("escalate_ticket")) == {"ticket_id"}
    assert set(props("de_escalate_ticket")) == {"ticket_id"}
    assert set(props("set_ticket_status")) == {"ticket_id", "status"}
    assert set(props("close_ticket")) == {"ticket_id", "status"}

    # ONE tool over three endpoints. `scope` is the discriminator and is a
    # closed vocabulary, so a typo is caught by the SDK before dispatch.
    assert set(props("reorder_kb_children")) == {"scope", "ordered_ids", "parent_id"}
    assert props("reorder_kb_children")["scope"]["enum"] == [
        "categories",
        "folders",
        "articles",
    ]

    # The two flat projections over `list_kb_tree`.
    assert props("list_kb_categories") == {}
    assert set(props("list_kb_folders")) == {"kb_category_id"}

    # 🔴 A LOCAL PATH AND NOTHING ELSE. No `content`, no `base64`, no `data` —
    # routing a screenshot through the model's context as base64 costs roughly
    # 5.5 MB of tokens to move bytes nothing needed to read, and the server runs
    # on the user's own machine where the path is already the natural handle. No
    # `kb_article_id` either: the upload attaches to nothing, and the link is
    # derived from the article body on save.
    assert set(props("upload_kb_media")) == {"file_path"}


async def test_no_write_tool_offers_a_dry_run(tools) -> None:
    """Ebteqdesk has no dry-run mode, so a client-side one could only describe
    the request it WOULD send — validating nothing and able to report success
    for a call the server would refuse. A fake guardrail is worse than none
    because it gets trusted; the side-effect line in each description is what
    replaces it."""
    for name in WRITE_TOOL_NAMES:
        properties = set(tools[name].input_schema.get("properties", {}))
        assert not properties & {"dry_run", "preview", "confirm", "simulate"}


async def test_the_requester_is_an_object_not_flattened_arguments(tools) -> None:
    """The request vocabulary is the API's own. Flattening `requester` into
    `requester_id`/`requester_email` would be a second request shape that no
    curl of this endpoint matches."""
    schema = tools["create_ticket"].input_schema["properties"]["requester"]

    assert schema.get("type") == "object"


async def test_only_the_genuinely_required_arguments_are_required(tools) -> None:
    def required(name: str) -> set:
        return set(tools[name].input_schema.get("required", []))

    assert required("list_tickets_by_category") == {"category"}
    assert required("get_kb_article") == {"slug"}
    # Everything else is optional: a report with no range means all time, and a
    # KB list with no query means the whole corpus.
    assert required("whoami") == set()
    assert required("get_escalation_report") == set()
    assert required("search_kb_articles") == set()
    assert required("list_tickets") == set()
    assert required("list_escalations") == set()

    # On the write side, "required" is the server's own required list and
    # nothing more. `status` on close is optional because the server defaults it
    # to solved; making it required here would be this client inventing a
    # stricter contract than the endpoint has.
    assert required("create_ticket") == {"subject", "description", "requester"}
    assert required("comment_on_ticket") == {"ticket_id", "body"}
    assert required("add_private_note") == {"ticket_id", "body"}
    assert required("escalate_ticket") == {"ticket_id"}
    assert required("de_escalate_ticket") == {"ticket_id"}
    # 🔴 `status` IS REQUIRED HERE, unlike on close. There is no server-side
    # default to fall through to — the endpoint's rule is `required` — and a
    # tool-side default would have to pick one of four working states on the
    # caller's behalf with no way to be right.
    assert required("set_ticket_status") == {"ticket_id", "status"}
    assert required("close_ticket") == {"ticket_id"}

    # ⚠️ `parent_id` is optional in the SCHEMA and conditionally mandatory in
    # FACT: required for scope="folders"/"articles", refused for
    # scope="categories". JSON Schema cannot express that here, so the tool
    # enforces it itself with a message naming the scope — see
    # test_kb_reorder.py. Marking it required outright would break the
    # categories case; leaving it silent would be the failure this comment
    # exists to prevent.
    assert required("reorder_kb_children") == {"scope", "ordered_ids"}

    # The flat projections: a folder filter is optional, and no argument at all
    # means the whole taxonomy.
    assert required("list_kb_categories") == set()
    assert required("list_kb_folders") == set()


# --------------------------------------------------------------------------- #
# The rules a model has to read
# --------------------------------------------------------------------------- #


async def test_the_report_tool_states_all_three_number_rules(tools) -> None:
    description = described(tools["get_escalation_report"])

    # 1. escalated/total is not a percentage and can exceed total.
    assert "NOT A PERCENTAGE" in description
    assert "CAN EXCEED" in description
    # 2. escalatedUndated is range-independent and never added to escalated.
    assert "escalatedUndated" in description
    assert "IDENTICAL IN EVERY RANGE" in description
    assert "Never add it to `escalated`" in description
    # 3. sum(status.*) can be less than total.
    assert "sum(status.*)` CAN BE LESS THAN `total`" in description


async def test_the_report_tool_states_that_row_identity_is_key(tools) -> None:
    description = described(tools["get_escalation_report"])

    assert "ROW IDENTITY IS `key`, NEVER `id` OR `slug`" in description
    assert "_uncategorised" in description
    assert "_type-{id}" in description


async def test_the_kb_article_tool_states_that_a_404_is_ambiguous_on_purpose(tools) -> None:
    description = described(tools["get_kb_article"])

    assert "AMBIGUOUS ON PURPOSE" in description
    assert "do not report to the user that" in description.lower()
    assert "body_html" in description and "not markdown" in description


async def test_the_kb_search_tool_states_the_corpus_is_public_only(tools) -> None:
    description = described(tools["search_kb_articles"])

    assert "PUBLISHED, PUBLICLY-VISIBLE ARTICLES ONLY" in description
    assert "no search" in description

    # 🔴 AND THAT "PUBLICLY VISIBLE" IS RESOLVED PER ARTICLE (ADR-0007). The
    # corpus is unchanged in spirit and narrower in wording: an article carries
    # its own visibility overriding its folder's, so a model must not conclude
    # anything about an article from the folder level `list_kb_tree` reports.
    assert "the article's own setting where it has one" in description.lower()
    assert "do not infer from a folder's `visibility`" in description.lower()

    # And the shape consequence: an article that overrides an internal folder is
    # served with no folder block at all, so the name is not disclosed.
    assert "`folder` IS NULL FOR AN ARTICLE WHOSE FOLDER IS INTERNAL" in description


async def test_the_kb_tree_tool_says_a_folder_level_is_what_articles_inherit(tools) -> None:
    """ADR-0007: the tree lists no articles and cannot tell a caller which of
    them override their folder. That gap is safe only because nothing on this
    API can SET an override, and the description has to say both halves — the
    first without the second reads as a reason to distrust the tool."""
    description = described(tools["list_kb_tree"])

    assert "IT IS NOT A STATEMENT ABOUT THE ARTICLES ALREADY IN THE FOLDER" in description
    assert "NOTHING ON THIS API CAN SET A" in description
    assert "PER-ARTICLE OVERRIDE" in description
    assert "Read a folder's `visibility` to decide" in description


async def test_the_kb_write_tools_say_visibility_is_dropped_not_rejected(tools) -> None:
    """🔴 SILENTLY IGNORED IS THE DANGEROUS SHAPE. `POST`/`PATCH
    /api/v1/kb/articles` DROP a `visibility` key rather than 422ing on it, so a
    201 or a 200 does not mean it was applied. A model that read the success and
    reported "I made it public" would be wrong with no error anywhere."""
    for name in ("propose_kb_article", "update_kb_article"):
        description = described(tools[name])

        assert "silently ignored" in description.lower()
        assert "visibility" in description.lower()

    assert "no key of any kind can set it" in described(tools["propose_kb_article"]).lower()


async def test_the_ticket_tools_state_that_visibility_is_the_assignee(tools) -> None:
    description = described(tools["list_tickets"])

    assert "ASSIGNED" in description
    assert "not every ticket" in description.lower()
    assert "requester" in description


async def test_each_tool_names_the_scope_it_needs(tools) -> None:
    """The 403 message names the missing scope; the description has to name the
    same string, or a user cannot tell which tool caused the refusal."""
    assert "`ticket:read`" in described(tools["list_tickets"])
    assert "`ticket:read`" in described(tools["list_tickets_by_category"])
    assert "`escalation:read`" in described(tools["list_escalations"])
    assert "`escalation-reports:read`" in described(tools["get_escalation_report"])
    # 🔴 The two are one character apart and gate different things: the queue
    # versus the counts. Neither description may name the other's scope.
    assert "`escalation-reports:read`" not in described(tools["list_escalations"])
    assert "`escalation:read`" not in described(tools["get_escalation_report"])
    assert "`kb:read`" in described(tools["search_kb_articles"])
    assert "`kb:read`" in described(tools["get_kb_article"])
    assert "no API key scope" in described(tools["whoami"])

    assert "`ticket:write`" in described(tools["create_ticket"])
    assert "`ticket:write`" in described(tools["comment_on_ticket"])
    # ⚠️ DELIBERATELY DOES NOT PIN WHICH ESCALATION SCOPE THIS TOOL NAMES.
    #
    # 🔴 An assertion here on the literal string `escalation:write` is what held
    # this tool's description at a contract that had moved underneath it. The
    # endpoint's escalated charge became `escalation:reply` when the
    # requester-facing half was split out, and the description could not be
    # corrected without breaking this line — so it was not corrected, and the
    # test reported green over stale documentation for two rounds.
    #
    # A test that pins documentation TEXT pins the contract that text describes.
    # This one now asserts only that the tool names SOME escalation write scope,
    # which is the durable claim; which one it is belongs to the dedicated test
    # below, where changing it is a visible decision rather than a side effect.
    _comment = described(tools["comment_on_ticket"])
    assert "`escalation:write`" in _comment or "`escalation:reply`" in _comment
    assert "`ticket:write`" in described(tools["add_private_note"])
    assert "`escalation:write`" in described(tools["add_private_note"])
    assert "`escalation:write`" in described(tools["escalate_ticket"])
    assert "`escalation:write`" in described(tools["de_escalate_ticket"])
    assert "`ticket:write`" in described(tools["set_ticket_status"])
    # 🔴 AND ITS SCOPE DOES NOT GROW ON AN ESCALATED TICKET. The absence is the
    # design: unlike the two thread writes, this tool costs `ticket:write` and
    # nothing more, and its description says so as a RELIEF rather than leaving
    # a reader who patterned off `comment_on_ticket` to assume the opposite.
    #
    # ⚠️ THIS ASSERTED THE OPPOSITE OF ITS OWN COMMENT AND TESTED NOTHING. It
    # read `assert "\`escalation:write\`" in described(...)` — a verbatim
    # duplicate of the line above it — so it passed on a description that
    # mentions the scope only in order to say it is NOT required. It would have
    # passed equally on one that required it, which is the sentence the comment
    # exists to forbid.
    #
    # What the comment MEANS is asserted instead: the relief sentence itself.
    assert (
        "`ticket:write` is the whole scope requirement whatever the"
        in described(tools["set_ticket_status"])
    )
    assert "`ticket:write`" in described(tools["close_ticket"])

    # The KB structure tools, reads and write alike, are `kb:write` — the tree
    # is the AUTHORING view. `kb:read` gates the public corpus and must not
    # appear on any of them as the scope they need.
    for name in (
        "list_kb_tree",
        "list_kb_categories",
        "list_kb_folders",
        "reorder_kb_children",
    ):
        assert "`kb:write`" in described(tools[name]), name


async def test_each_write_tool_also_names_the_ability_it_needs(tools) -> None:
    """A `required_ability` refusal names an ability, and its remedy is an
    administrator rather than a key. A user who cannot map that name back to a
    tool cannot ask for the right thing."""
    assert "`ticket.create`" in described(tools["create_ticket"])
    assert "`ticket.reply`" in described(tools["comment_on_ticket"])
    # 🔴 THE SAME ability as a public reply, and the description says so. A note
    # is lighter in CONSEQUENCE, not in permission — Ebteqdesk gates both on
    # `ticket.reply`, and a description implying otherwise would invite a model
    # to treat notes as freely available.
    assert "`ticket.reply`" in described(tools["add_private_note"])
    assert "`bp_escalation.reply`" in described(tools["add_private_note"])
    assert "`ticket.reply`" in described(tools["escalate_ticket"])
    assert "`ticket.reply`" in described(tools["de_escalate_ticket"])
    # BOTH abilities, because this tool's gate is conditional: `ticket.reply`
    # always, `ticket.close` only when the ticket is currently resolved.
    assert "`ticket.reply`" in described(tools["set_ticket_status"])
    assert "`ticket.close`" in described(tools["set_ticket_status"])
    assert "`ticket.close`" in described(tools["close_ticket"])


# --------------------------------------------------------------------------- #
# The write tools' side effects
# --------------------------------------------------------------------------- #
#
# There is no dry_run argument, so the description IS the guardrail. These
# assertions are the review gate on that text: a refactor that trims a write
# tool's warning down to a tidy one-liner is a behaviour change, because the
# behaviour in question is what a model does before calling it.


@pytest.mark.parametrize("name", WRITE_TOOL_NAMES)
async def test_every_write_tool_leads_with_its_side_effect(tools, name: str) -> None:
    """First line, capitalised, unmissable — the model may never read the last
    line of a description but it always reads the first."""
    first_line = (tools[name].description or "").strip().splitlines()[0]

    assert first_line.startswith("WRITES TO EBTEQDESK"), first_line


async def test_the_create_tool_warns_that_a_ticket_cannot_be_deleted(tools) -> None:
    description = described(tools["create_ticket"])

    assert "REAL ticket" in description
    assert "no delete-ticket tool" in description
    # And the non-obvious second write: an unknown email creates a contact.
    assert "ONE IS CREATED" in description
    assert "will not rename an existing contact" in description


async def test_the_comment_tool_says_the_reply_reaches_the_requester(tools) -> None:
    description = described(tools["comment_on_ticket"])

    assert "PUBLIC reply the requester receives" in description
    assert "not an internal note" in description
    # The null-id trap: 201 with nothing filed.
    assert "CAN BE null, AND THAT MEANS NOTHING WAS POSTED" in description
    assert "do not report it as sent" in description


async def test_the_comment_tool_explains_the_escalated_ticket_case(tools) -> None:
    """The failure this closes: a user reads "needs ticket:write", is refused for
    an escalation scope, and re-mints with ticket:write again.

    🔴 THIS TEST NO LONGER PINS THE SCOPE NAME, AND THAT IS THE POINT.

    It used to assert the sentence "REPLYING TO AN ESCALATED TICKET NEEDS
    `escalation:write`" verbatim. When the requester-facing half was split out
    into `escalation:reply`, that sentence became wrong — and this assertion is
    what kept it in place: the description could not be corrected without
    failing here, so it was not corrected, and the suite reported green over
    documentation describing a contract the endpoint no longer had.

    An assertion on documentation TEXT pins the contract that text describes.
    What survives here is the durable shape — that the description names the
    escalated case, says it is checkable in advance, and says re-minting the
    scope you already hold will not help — none of which depends on which scope
    is named. The scope name itself is covered by the separate escalation-scope
    tests, where changing it is a visible decision.
    """
    description = described(tools["comment_on_ticket"])

    # The escalated case is named, whichever escalation scope it costs.
    assert "REPLYING TO AN ESCALATED TICKET NEEDS `escalation:" in description
    # …and that "escalated" outlives resolution, so a reply on the caller's own
    # solved ticket can be refused this way and re-minting will not help.
    assert "stays true after the ticket is solved" in description
    # Since the API grew `escalated`, this IS checkable in advance, and the
    # description has to say so — it used to (correctly) say the opposite.
    assert "Check the ticket's `escalated` field before you call" in description
    assert "re-minting with the same scope changes nothing" in description
    # The refusal is still the fallback for a caller who did not look.
    assert "If you did not check, the refusal tells you" in description


async def test_the_escalate_tool_warns_that_retrying_double_notifies(tools) -> None:
    """State idempotent, side effects not. A model that retries on timeout
    pings a whole team twice."""
    description = described(tools["escalate_ticket"])

    assert "NEVER RETRY THIS CALL BLIND" in description
    assert "THE NOTIFICATION IS NOT" in description
    assert "team is alerted twice" in description
    # "Check first" is now real advice — `escalated` ships on every payload —
    # where it used to be impossible. Both directions must be stated: check
    # before, and re-check instead of retrying after.
    assert "CHECK FIRST, AND CHECK AGAIN INSTEAD OF RETRYING" in description
    assert "DO NOT repeat it" in description
    assert "read `escalated` to find out whether the first one landed" in description


async def test_the_escalation_tools_point_at_the_boolean_not_the_timestamp(
    tools,
) -> None:
    """🔴 The trap the field pair sets. `escalated_at` is permanently null on
    every ticket escalated before that column existed, so deriving state from it
    reads the LONGEST-escalated tickets as not escalated. Any tool that tells a
    model to check must say which field to check."""
    for name in ("escalate_ticket", "list_tickets"):
        description = described(tools[name])
        assert "`escalated`" in description
        assert "`escalated_at`" in description

    assert "Read `escalated`, never `escalated_at`" in described(tools["escalate_ticket"])
    assert (
        "`escalated` IS THE ESCALATION STATE" in described(tools["list_tickets"])
    )


# --------------------------------------------------------------------------- #
# The shared escalation queue
# --------------------------------------------------------------------------- #


async def test_the_escalations_tool_says_the_queue_is_not_the_callers_own(
    tools,
) -> None:
    """🔴 THE one thing this description could quietly get wrong. Every other
    ticket tool on this server is ownership-scoped and this one is not, and
    nothing in the payload distinguishes them — both render the identical
    TicketResource. An agent told the list is its own will treat every row as
    its own to answer."""
    description = described(tools["list_escalations"])

    assert "THIS LIST IS NOT YOURS" in description
    assert "every unresolved escalated ticket in the installation" in description
    assert "whoever it is assigned to" in description
    assert "including tickets assigned to nobody" in description
    # The field that resolves it, named.
    assert "Check the `assignee` field to see which are yours" in description
    # And the specific wrong conclusion, pre-empted.
    assert 'do not tell a user "you have n escalated tickets"' in description.lower()


async def test_the_escalations_tool_states_the_order_and_the_null_trap(tools) -> None:
    description = described(tools["list_escalations"])

    assert "LONGEST-ESCALATED FIRST" in description
    assert "sort LAST despite being the oldest" in description
    # They are still escalated — the null is about the stamp, not the state.
    assert "`escalated` is true on them" in description


async def test_the_escalations_tool_warns_that_absence_is_not_resolution(
    tools,
) -> None:
    """Solving takes a ticket off the BP queue, so a row vanishing from this
    list is ambiguous between solved and de-escalated. An agent watching for
    "did my escalation get answered" would otherwise read absence as success."""
    description = described(tools["list_escalations"])

    assert "A SOLVED ESCALATED TICKET IS NOT ON THIS LIST AT ALL" in description
    assert "does NOT mean the escalation was answered" in description
    assert "fetch the ticket" in description


async def test_the_escalations_tool_says_the_shape_is_already_known(tools) -> None:
    """Same TicketResource as list_tickets. Saying so saves a model inventing a
    second parser for a shape it already has."""
    description = described(tools["list_escalations"])

    assert "SAME per-ticket object as `list_tickets`" in description
    assert "no second shape to learn" in description


# --------------------------------------------------------------------------- #
# Paging, shared by the three ticket lists
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", TICKET_LIST_TOOL_NAMES)
async def test_every_ticket_list_documents_the_20_row_ceiling(tools, name: str) -> None:
    """The cap is a product requirement, not a tuning default, and it is the
    same on all three lists. A model that believes it can pull 50 will page as
    though it had 50-row pages and read the wrong rows."""
    description = described(tools[name])

    assert "1 to 20" in description
    assert "20" in description
    # And that exceeding it FAILS rather than silently shrinking, which is the
    # difference a caller cannot otherwise detect.
    assert "error" in description.lower()


async def test_the_ticket_list_says_the_ceiling_is_also_the_default(tools) -> None:
    description = described(tools["list_tickets"])

    assert "every pull is at most 20 records" in description.lower()
    assert "not a silently smaller page" in description
    assert "Use it to ask for FEWER" in description


async def test_the_de_escalate_tool_states_its_lighter_but_real_cost(tools) -> None:
    description = described(tools["de_escalate_ticket"])

    assert "sends no notification" in description
    assert "timestamp is GONE" in description


async def test_the_close_tool_warns_about_the_survey_email(tools) -> None:
    description = described(tools["close_ticket"])

    assert "MAY EMAIL THE REQUESTER" in description
    assert "satisfaction survey" in description
    assert "not a safe way to tidy up test data" in description
    # And that close is not reply, in both directions.
    assert "CLOSING DOES NOT REPLY" in description
    assert "SEPARATE ability from `ticket.reply`" in description


async def test_the_status_tool_leads_with_the_fact_that_it_mails_nobody(tools) -> None:
    """🔴 THE FIRST LINE, and the reason this tool needs a different one.

    Every other write tool's first line is a warning about who it contacts. A
    model skimming seventeen descriptions has to be able to tell this one apart at
    the same glance, or it treats the quiet write with the caution the loud ones
    need — or, worse, treats a loud one with the caution this one needs.
    """
    first_line = (tools["set_ticket_status"].description or "").strip().splitlines()[0]

    assert first_line.startswith("WRITES TO EBTEQDESK")
    assert "Emails nobody" in first_line


async def test_the_status_tool_refuses_the_resolving_statuses_and_says_where_they_live(
    tools,
) -> None:
    """Half of the PAIR that stops these two tools drifting into contradiction.

    `set_ticket_status` must say it cannot resolve a ticket AND name the tool
    that can — otherwise a model that wants a ticket solved has been refused
    with nowhere to go, and will try `status=4` again.
    """
    description = described(tools["set_ticket_status"])

    assert "WHAT IT CANNOT DO" in description
    assert "SOLVED" in description and "(4)" in description
    assert "CLOSED" in description and "(5)" in description
    assert "`close_ticket`" in description
    # And the other two, refused for a different reason — outcomes, not choices.
    assert "merged (6)" in description
    assert "spam (7)" in description
    # The survey warning is NOT restated here; it is pointed at. Two copies of a
    # safety warning is how one copy goes stale.
    assert "the ONE tool on this server that can send the satisfaction survey" in description


async def test_the_close_tool_no_longer_claims_reopening_is_impossible(tools) -> None:
    """🔴 THE OTHER HALF OF THE PAIR.

    `close_ticket` used to say "you cannot reopen a ticket … through this tool"
    in a way that reads as "nowhere". That was true when it was written and is
    false now, and a description that is false in the direction of "the thing
    you want cannot be done" is the expensive kind: the model stops looking.
    """
    description = described(tools["close_ticket"])

    assert "REOPENING IS A DIFFERENT TOOL, NOT AN IMPOSSIBILITY" in description
    assert "`set_ticket_status`" in description
    assert "`status=2` reopens a ticket this tool resolved" in description
    # The old sentence, gone. It is the exact phrasing that misled.
    assert "you cannot reopen a ticket" not in description


async def test_the_status_tool_is_honest_about_what_is_reversible(tools) -> None:
    """The state is reversible; the history entry is not. A model told only
    "reversible" will flap a real requester's ticket between two states to see
    what happens, and leave a permanent trail of having done so."""
    description = described(tools["set_ticket_status"])

    assert "REVERSIBLE STATE, PERMANENT TRAIL" in description
    assert "Status updated:" in description
    assert "permanent record of the flapping" in description


async def test_the_status_tool_says_the_no_op_is_safe_to_retry(tools) -> None:
    """The second write on this server that may be repeated, and the exception
    has to be stated where the model reads it — the blanket "never retry a
    write" is otherwise absolute."""
    description = described(tools["set_ticket_status"])

    assert "THE NO-OP IS SAFE" in description
    assert "still answers 200" in description
    assert "`reorder_kb_children`" in description


async def test_the_status_tool_states_the_escalated_case_as_a_relief(tools) -> None:
    """🔴 A model that has read `comment_on_ticket` will assume
    `escalation:write` applies here too. It does not, and the description has to
    say so in the affirmative — silence reads as "probably the same"."""
    description = described(tools["set_ticket_status"])

    assert "AN ESCALATED TICKET NEEDS NOTHING EXTRA" in description
    assert "`ticket:write` is the whole scope requirement" in description
    # Named, so the reader knows which expectation is being corrected.
    assert "`comment_on_ticket`" in description
    assert "`add_private_note`" in description


async def test_the_status_tool_states_what_reopening_costs(tools) -> None:
    description = described(tools["set_ticket_status"])

    assert "REOPENING IS `status=2`, AND IT COSTS MORE" in description
    assert "`ticket.close`" in description
    assert "`ticket.reply`" in description


async def test_the_instructions_separate_the_two_escalation_write_scopes() -> None:
    """🔴 `escalation:write` and `escalation:reply` are two scopes and the
    difference is WHO SEES THE RESULT.

    `write` files an INTERNAL note on an escalated ticket and hands the ticket
    back; `reply` sends a message the REQUESTER receives by email. They are
    backed by the same role ability and split only on the key, so an account can
    hold the first and not the second — and an ordinary support account does,
    because escalation hands the requester conversation to whoever is working the
    escalation.

    Asserted on the module INSTRUCTIONS, which every tool call carries, because
    that is where the distinction is settled. The per-tool descriptions are
    covered by their own tests.

    ⚠️ THE FAILURE THIS CLOSES IS A CLIENT RE-MINTING THE WRONG HALF. Refused
    for `escalation:reply`, the obvious next move is a key carrying
    `escalation:write` — the other half of the same area, with a name one word
    apart — which hits the identical wall.
    """
    instructions = " ".join((srv.mcp.instructions or "").split())

    assert "`escalation:write` AND `escalation:reply` ARE DIFFERENT SCOPES" in instructions
    assert "internal note" in instructions.lower()
    assert "REQUESTER receives by email" in instructions
    # …and that a refusal naming the reply half is usually not a key problem.
    assert "not 'mint a new key'" in instructions


async def test_the_instructions_split_the_status_vocabulary_between_the_two_tools(
    tools,
) -> None:
    """One fact a host shows once for the whole server: status is split across
    two tools, neither reaches the other's values, and only one of them can
    email the requester."""
    instructions = " ".join((srv.mcp.instructions or "").split())

    assert "TICKET STATUS IS SPLIT ACROSS TWO TOOLS AND NEITHER CAN REACH THE OTHER'S VALUES" in instructions
    assert "`set_ticket_status` owns the WORKING states" in instructions
    assert "`close_ticket` owns 4 (solved) and 5 (closed)" in instructions
    assert "ONLY tool that can send the satisfaction survey" in instructions
    # And the reopen, which is the non-obvious half of the split.
    assert "REOPENED" in instructions
    assert "`ticket.close`" in instructions


async def test_the_instructions_name_both_retry_safe_writes(tools) -> None:
    """It was ONE for four releases and the instructions said so twice. A count
    that moves in one sentence and not the other is worse than no count."""
    instructions = " ".join((srv.mcp.instructions or "").split())

    assert "exactly TWO exceptions" in instructions
    assert "only TWO writes here that are safe to retry" in instructions
    assert "ONE write here that is safe to retry" not in instructions


async def test_the_server_instructions_flag_the_write_surface(tools) -> None:
    """`instructions` is what a host shows about the server as a whole, so the
    "this can change a live helpdesk" warning belongs there too and not only on
    the individual tools."""
    instructions = " ".join((srv.mcp.instructions or "").split())

    for name in WRITE_TOOL_NAMES:
        assert f"`{name}`" in instructions

    assert "no dry-run mode" in instructions
    assert "never retry one that timed out" in instructions


async def test_whoami_explains_requested_versus_scopes(tools) -> None:
    """`whoami` is the diagnostic behind every scope refusal, so its description
    has to teach the one comparison that distinguishes the two causes."""
    description = described(tools["whoami"])

    assert "`apiKey.requested`" in description
    assert "`apiKey.scopes`" in description
    # Both conclusions a model must be able to draw from those two lists, and
    # they are opposites — this is the pair the old code got wrong.
    assert "a new key would not help" in description
    assert "a new key is exactly what is needed" in description
    # And the trap: `permissions` is a different vocabulary from key scopes.
    assert "do not map one-to-one" in description


# --------------------------------------------------------------------------- #
# Calling through the MCP layer
# --------------------------------------------------------------------------- #
#
# `MCPServer.call_tool` RAISES `ToolError` rather than returning
# `CallToolResult(isError=True)`. The conversion happens one layer down, in the
# protocol kernel, which wraps the raised error as `isError: true` with
# `str(exc)` as the text content. So `str(ToolError)` here is, verbatim, the
# sentence a user reads in their MCP client — which is why these assertions are
# on its wording. `tests/test_stdio.py` closes the loop by driving the real
# subprocess and checking the `isError` result off the wire.


async def test_a_successful_call_returns_the_payload_verbatim(wired) -> None:
    payload = {"data": {"id": 1, "name": "Admin", "permissions": ["ticket.view"]}}
    wired(always_json(200, payload))

    result = await srv.mcp.call_tool("whoami", {})

    assert not result.is_error
    assert result.structured_content == payload


async def test_a_key_missing_a_scope_reaches_the_client_as_readable_text(wired) -> None:
    """No traceback, no bare '403' — the scope, the cause, and the fix."""
    wired(scope_refusal("kb:read", requested=["ticket:read"], scopes=["ticket:read"]))

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool("search_kb_articles", {"query": "vpn"})

    text = str(excinfo.value)
    assert "kb:read" in text
    assert "mint a NEW key" in text
    assert "Traceback" not in text


async def test_a_role_missing_an_ability_reaches_the_client_as_readable_text(
    wired,
) -> None:
    """The same 403 body, the other cause, the opposite advice — through the
    full MCP layer, because that is where a user actually reads it."""
    wired(
        scope_refusal(
            "escalation-reports:read",
            requested=["escalation-reports:read"],
            scopes=[],
        )
    )

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool("get_escalation_report", {})

    text = str(excinfo.value)
    assert "escalation-reports:read" in text
    assert "administrator" in text.lower()
    assert "mint a NEW key" not in text
    assert "Traceback" not in text


async def test_a_bad_argument_is_reported_as_prose_not_a_python_type(wired) -> None:
    wired(always_json(200, {"data": []}))

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool("list_tickets_by_category", {"category": "a/b"})

    assert "single ticket-type slug" in str(excinfo.value)


async def test_a_missing_token_is_reported_on_the_first_call_not_at_startup(
    monkeypatch,
) -> None:
    """The server must connect and list its tools with no token configured, so
    the client shows the real reason instead of 'failed to connect'."""
    monkeypatch.setattr(srv, "_client", None)
    monkeypatch.delenv("EBTEQDESK_API_TOKEN", raising=False)
    monkeypatch.setenv("EBTEQDESK_BASE_URL", "https://ebteqdesk.test")

    # Listing tools works regardless of configuration.
    assert len(await srv.mcp.list_tools()) == len(TOOL_NAMES)

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool("whoami", {})

    assert "EBTEQDESK_API_TOKEN is not set" in str(excinfo.value)


async def test_an_unreachable_host_is_reported_without_a_traceback(wired) -> None:
    def explode(_request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused")

    wired(explode)

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool("whoami", {})

    text = str(excinfo.value)
    assert "Could not reach Ebteqdesk" in text
    assert "Traceback" not in text


async def test_the_token_never_appears_in_a_tool_error(wired) -> None:
    wired(always_json(401, {"error": "Unauthenticated."}))

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool("whoami", {})

    assert "6|t" not in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Calling the WRITE tools through the MCP layer
# --------------------------------------------------------------------------- #


WRITE_CALLS = [
    ("create_ticket", {"subject": "Printer on fire", "description": "It is.",
                       "requester": {"email": "ada@example.com"}}),
    ("comment_on_ticket", {"ticket_id": 42, "body": "Looking into it."}),
    ("escalate_ticket", {"ticket_id": 42}),
    ("de_escalate_ticket", {"ticket_id": 42}),
    ("set_ticket_status", {"ticket_id": 42, "status": 2}),
    ("close_ticket", {"ticket_id": 42}),
]


@pytest.mark.parametrize("name, arguments", WRITE_CALLS)
async def test_every_write_tool_round_trips_through_the_mcp_layer(
    wired, name: str, arguments: dict
) -> None:
    wired(always_json(201, ticket_payload()))

    result = await srv.mcp.call_tool(name, arguments)

    assert not result.is_error
    assert result.structured_content["data"]["id"] == 42


@pytest.mark.parametrize("name, arguments", WRITE_CALLS)
async def test_no_write_tool_is_reachable_without_its_scope(
    wired, name: str, arguments: dict
) -> None:
    """The whole surface, one at a time: a key carrying only the READ scopes
    must not be able to change anything. Every one of these has to come back as
    a refusal, and none of them may be a partial success."""
    wired(
        scope_refusal(
            "ticket:write",
            requested=["ticket:read", "kb:read"],
            scopes=["ticket:read", "kb:read"],
        )
    )

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool(name, arguments)

    text = str(excinfo.value)
    assert "ticket:write" in text
    assert "mint a NEW key" in text


async def test_an_ability_refusal_does_not_send_the_user_to_mint_a_key(wired) -> None:
    """The third 403 flavour, and the one whose wording matters most: no key
    fixes it, so the message must not contain the advice that fixes the other
    two."""
    wired(ability_refusal("ticket.close"))

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool("close_ticket", {"ticket_id": 42})

    text = str(excinfo.value)
    assert "ticket.close" in text
    assert "administrator" in text.lower()
    assert "NOT A KEY OR SCOPE PROBLEM" in text
    # 🔴 The regression guard, in the same shape RoleScopeError uses: the
    # message must not PRESCRIBE minting, and must say outright that it does not
    # help. Silence on the point is not enough — a user who has just been told
    # to mint a key by a scope error will try it again here unless told not to.
    assert "mint a NEW key" not in text
    assert "Minting a new key will not help" in text
    assert "Traceback" not in text


async def test_the_three_403_flavours_give_three_different_answers(wired) -> None:
    """Same status code, three causes, three remedies — mint a key, ask an
    administrator about a SCOPE, ask an administrator about an ABILITY. A user
    who cannot tell them apart tries all three."""
    messages = {}

    for label, handler in (
        ("key", scope_refusal("ticket:write", requested=[], scopes=[])),
        ("role", scope_refusal("ticket:write", requested=["ticket:write"], scopes=[])),
        ("ability", ability_refusal("ticket.close")),
    ):
        wired(handler)
        with pytest.raises(ToolError) as excinfo:
            await srv.mcp.call_tool("close_ticket", {"ticket_id": 42})
        messages[label] = str(excinfo.value)

    assert len(set(messages.values())) == 3

    # Only the key case PRESCRIBES minting. The other two mention it solely to
    # rule it out, which is the wording that stops a user cycling keys.
    assert "mint a NEW key" in messages["key"]
    assert "mint a NEW key" not in messages["role"]
    assert "mint a NEW key" not in messages["ability"]
    assert "will NOT help" in messages["role"]
    assert "will not help" in messages["ability"]

    # The two administrator answers ask for DIFFERENT things: one for the
    # ability behind a scope, one for a named ability outright.
    assert "ticket:write" in messages["role"]
    assert "ticket.close" in messages["ability"]
    assert "required_scope" not in messages["ability"]


async def test_a_non_numeric_ticket_id_is_refused_as_prose(wired) -> None:
    """It would match no route at all — the write routes are whereNumber — and
    a routing miss renders HTML, which this client would report as a proxy
    problem the user does not have."""
    wired(always_json(200, ticket_payload()))

    with pytest.raises(Exception) as excinfo:
        await srv.mcp.call_tool("close_ticket", {"ticket_id": "bp-task"})

    assert "ticket" in str(excinfo.value).lower()


async def test_the_exception_chain_is_suppressed(wired) -> None:
    """`from None` in server._call: an httpx exception in the chain can quote a
    full request URL, and the chain adds nothing to an already-complete message.
    """
    wired(always_json(401, {"error": "Unauthenticated."}))

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool("whoami", {})

    # The ToolError wraps our RuntimeError, and that RuntimeError has no cause.
    assert excinfo.value.__cause__ is not None
    assert excinfo.value.__cause__.__cause__ is None


# --------------------------------------------------------------------------- #
# Capabilities: tools only (#133)
# --------------------------------------------------------------------------- #
#
# `MCPServer` registers `prompts/*` and `resources/*` handlers whether or not the
# server has anything behind them, and the SDK derives ServerCapabilities from
# which handlers exist — so a tools-only server built the obvious way advertises
# two capabilities that answer `[]`, costing every client two round trips per
# session. `server._withdraw_unimplemented_capabilities` removes the handlers.
#
# These tests are the thing that fails when a future SDK moves
# `_request_handlers`: the withdrawal is written to degrade quietly rather than
# stop the server booting, so the loud half has to live here.


def _capabilities(protocol_version: str | None = None):
    return srv.mcp._lowlevel_server.get_capabilities(protocol_version=protocol_version)


def test_initialize_advertises_no_prompts_and_no_resources() -> None:
    capabilities = _capabilities()

    assert capabilities.tools is not None, "tools is the whole point of this server"
    assert capabilities.prompts is None
    assert capabilities.resources is None


def test_the_modern_handshake_advertises_no_prompts_and_no_resources() -> None:
    """The 2026-07-28 derivation is a different code path in the SDK — it turns
    `listChanged` and `resources.subscribe` on from the subscriptions stream — so
    a withdrawal that only worked on the legacy path would pass the test above
    and still advertise both to a current client."""
    capabilities = _capabilities("2026-07-28")

    assert capabilities.tools is not None
    assert capabilities.prompts is None
    assert capabilities.resources is None


def test_the_withdrawal_actually_removed_something() -> None:
    """🔴 The guard against a silent no-op. `_withdraw_unimplemented_capabilities`
    returns () rather than raising if the SDK moves its private handler map, so
    without this assertion an SDK upgrade would quietly restore both empty
    capabilities and every test above would still pass — they would be asserting
    that a thing that never ran had no effect."""
    assert "prompts/list" in srv.WITHDRAWN_METHODS
    assert "resources/list" in srv.WITHDRAWN_METHODS


def test_the_get_half_of_each_family_is_withdrawn_too() -> None:
    """Dropping `prompts/list` alone would leave `prompts/get` reachable on a
    server with no prompts — a method that can only ever fail, and one a client
    might reasonably try after being told the capability is absent."""
    handlers = srv.mcp._lowlevel_server._request_handlers

    assert "prompts/get" not in handlers
    assert "resources/read" not in handlers
    assert "resources/templates/list" not in handlers
    # And the one that is the whole point of the server is untouched.
    assert "tools/call" in handlers
    assert "tools/list" in handlers


async def test_withdrawing_the_capabilities_did_not_disturb_the_tools(tools) -> None:
    """Reaching into a private handler map is the kind of change that can take a
    working server down with it. Every tool, still callable."""
    assert sorted(tools) == sorted(TOOL_NAMES)


def test_the_instructions_say_the_server_is_tools_only() -> None:
    instructions = " ".join((srv.mcp.instructions or "").split())

    assert "TOOLS ONLY" in instructions
    assert "no prompts and no resources" in instructions


# --------------------------------------------------------------------------- #
# Schemas a client can build a call from, without the docstring (#134)
# --------------------------------------------------------------------------- #
#
# The prose said everything; the schema said `{"type": "object",
# "additionalProperties": true}` and `{"type": "integer"}`. A client that reads
# schemas and not docstrings built an invalid first call off exactly that.


def _literal_values(name: str, argument: str) -> list:
    """The values the ANNOTATION accepts, which is what actually validates.

    `WithJsonSchema` replaces what the schema SAYS; the `Literal` decides what
    the call may BE. They are two statements of one fact, so every test below
    that reads a schema is paired with this, and the pair is what stops them
    drifting.
    """
    from typing import get_args, get_type_hints

    hints = get_type_hints(getattr(srv, name), include_extras=False)
    values = [value for value in get_args(hints[argument]) if value is not type(None)]

    # `Literal[1,2] | None` flattens to (Literal[1,2], NoneType) on some
    # versions and to (1, 2, NoneType) on others; normalise both.
    if len(values) == 1 and get_args(values[0]):
        values = list(get_args(values[0]))

    return values


async def test_the_requester_schema_describes_its_two_shapes(tools) -> None:
    """🔴 The failure this closes: `requester` was an untyped object, so a
    schema-only client had no way to know it takes `{"id": n}` or
    `{"email": ..., "name": ...}` and nothing else."""
    schema = tools["create_ticket"].input_schema["properties"]["requester"]

    # Still an object, so a bare string is refused by the schema as well as by
    # the annotation.
    assert schema["type"] == "object"

    branches = schema["oneOf"]

    assert len(branches) == 2
    assert [branch["required"] for branch in branches] == [["id"], ["email"]]
    assert branches[0]["properties"]["id"]["type"] == "integer"
    assert branches[1]["properties"]["email"]["type"] == "string"
    assert set(branches[1]["properties"]) == {"email", "name"}

    # `name` is NOT required: the server files a new contact as "Unknown"
    # without one, and a client that cannot name a requester must still be able
    # to raise their ticket. Requiring it here would make this client stricter
    # than the endpoint.
    assert "name" not in branches[1]["required"]

    # And the side effect a caller has to know before choosing a branch.
    assert "CREATES A CONTACT" in schema["description"]


async def test_a_bare_string_requester_is_refused_before_any_request(wired) -> None:
    """The schema says object; the annotation enforces it. Both matter: one tells
    a client not to make the call, the other stops the call being made."""
    wired(always_json(201, ticket_payload()))

    with pytest.raises(ToolError):
        await srv.mcp.call_tool(
            "create_ticket",
            {"subject": "Printer on fire", "description": "It is.",
             "requester": "ada@example.com"},
        )


@pytest.mark.parametrize(
    "tool, argument, expected",
    [
        ("create_ticket", "priority", [1, 2, 3, 4]),
        ("create_ticket", "status", [1, 2, 3, 8, 4, 5]),
        ("close_ticket", "status", [5, 4]),
        ("set_ticket_status", "status", [1, 2, 3, 8]),
    ],
)
async def test_the_fixed_value_arguments_are_enums_with_per_value_meanings(
    tools, tool: str, argument: str, expected: list
) -> None:
    schema = tools[tool].input_schema["properties"][argument]
    values = [value for value in schema["enum"] if value is not None]

    assert values == expected

    # Every value carries its meaning, machine-readably — JSON Schema has no
    # `enumDescriptions`, so `oneOf`/`const` is where it goes.
    described = {branch["const"]: branch["description"] for branch in schema["oneOf"]}

    for value in expected:
        assert described[value], f"{tool}.{argument} = {value} has no description"
        # …and in `description` too, which is the field no client ignores.
        assert f"{value} = " in schema["description"]


@pytest.mark.parametrize(
    "tool, argument",
    [
        ("create_ticket", "priority"),
        ("create_ticket", "status"),
        ("close_ticket", "status"),
        ("set_ticket_status", "status"),
    ],
)
async def test_the_advertised_enum_matches_what_the_annotation_enforces(
    tools, tool: str, argument: str
) -> None:
    """🔴 THE DRIFT GUARD. The schema is hand-written and the validation comes
    from a `Literal`; adding a value to one and not the other would either
    advertise a value the tool refuses or refuse a value it advertises."""
    schema = tools[tool].input_schema["properties"][argument]
    advertised = {value for value in schema["enum"] if value is not None}

    # Membership, not order. `close_ticket` advertises 5 before 4 on purpose —
    # the safe value leads, so nobody reads past it — and the annotation has no
    # opinion about presentation.
    assert advertised == set(_literal_values(tool, argument))


async def test_the_optional_enums_admit_the_null_their_default_declares(tools) -> None:
    """`priority` and `status` on create default to null — "let the server
    decide". A schema whose own `default` its `enum` rejects is one a strict
    client is right to refuse, so null is legal in all three places at once."""
    for argument in ("priority", "status"):
        schema = tools["create_ticket"].input_schema["properties"][argument]

        assert schema["default"] is None
        assert schema["type"] == ["integer", "null"]
        assert None in schema["enum"]
        assert any(branch["const"] is None for branch in schema["oneOf"])


async def test_close_status_is_not_nullable_because_it_has_a_real_default(tools) -> None:
    """The whole #132 fix: this tool ALWAYS sends a status, so there is no
    "omitted" case to admit and no way to fall through to the API's default."""
    schema = tools["close_ticket"].input_schema["properties"]["status"]

    assert schema["type"] == "integer"
    assert None not in schema["enum"]
    assert schema["default"] == srv.CLOSE_WITHOUT_SURVEY


@pytest.mark.parametrize(
    "tool, argument, rejected",
    [
        ("create_ticket", "priority", 5),
        ("create_ticket", "status", 6),   # merged — an outcome, never a choice
        ("create_ticket", "status", 7),   # spam, likewise
        ("close_ticket", "status", 2),    # an open status is not a close
        ("close_ticket", "status", 6),
        # 🔴 The mirror image, and the pair that keeps the two tools disjoint:
        # a RESOLVING status is not a working-state change, and 4 in particular
        # is the one value that would email the requester a survey.
        ("set_ticket_status", "status", 4),
        ("set_ticket_status", "status", 5),
        ("set_ticket_status", "status", 6),
        ("set_ticket_status", "status", 7),
    ],
)
async def test_a_value_outside_the_enum_is_refused_here_not_by_the_api(
    wired, tool: str, argument: str, rejected: int
) -> None:
    """Acceptance criterion of #134: schema validation, not a 422 a round trip
    later. The recorded traffic proves nothing left the client."""
    sent: list = []
    wired(lambda request: (sent.append(request), json_response(200, ticket_payload()))[1])

    arguments = {
        "create_ticket": {"subject": "Printer on fire", "description": "It is.",
                          "requester": {"id": 7}},
        "close_ticket": {"ticket_id": 42},
        "set_ticket_status": {"ticket_id": 42},
    }[tool] | {argument: rejected}

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool(tool, arguments)

    assert argument in str(excinfo.value)
    assert sent == [], "the value reached the API instead of being refused here"


# --------------------------------------------------------------------------- #
# close_ticket sends no mail to the requester by default (#132)
# --------------------------------------------------------------------------- #
#
# Closing as SOLVED fires Ticket::updateStatus()'s RateTicket survey — a real
# email to the requester's address. That used to be this tool's DEFAULT, so an agent
# closing a ticket mailed somebody as a consequence of a parameter it never set.


async def test_closing_with_no_status_sends_the_no_survey_status_explicitly(
    wired,
) -> None:
    """🔴 THE FIX. Two properties, and both are needed: the value is 5, and it is
    SENT. Omitting the key would let Ebteqdesk apply its own default, which is
    still 4 — the surveying one."""
    recorder = []
    wired(lambda request: (recorder.append(request), json_response(200, ticket_payload()))[1])

    await srv.mcp.call_tool("close_ticket", {"ticket_id": 42})

    assert jsonlib.loads(recorder[0].content) == {"status": 5}


async def test_the_survey_is_reachable_but_only_by_asking_for_it(wired) -> None:
    """Not removed — a close that genuinely wants a satisfaction survey is a real
    thing an agent may be asked for. It just cannot happen by omission."""
    recorder = []
    wired(lambda request: (recorder.append(request), json_response(200, ticket_payload()))[1])

    await srv.mcp.call_tool("close_ticket", {"ticket_id": 42, "status": 4})

    assert jsonlib.loads(recorder[0].content) == {"status": 4}


async def test_the_close_tool_says_which_status_emails_the_requester(tools) -> None:
    """The acceptance criterion in words: the description must name the value
    that mails, not merely mention that mail can happen."""
    description = described(tools["close_ticket"])

    assert "EXACTLY ONE STATUS VALUE SENDS OUTBOUND EMAIL TO THE REQUESTER'S ADDRESS" in description
    assert "`status=5` (CLOSED) — THE DEFAULT" in description
    assert "`status=4` (SOLVED)" in description
    assert "without naming a status contacts nobody" in description


async def test_the_close_schema_says_which_value_emails_the_requester(tools) -> None:
    """And in the schema, for the client that never reads the description."""
    schema = tools["close_ticket"].input_schema["properties"]["status"]
    described_values = {branch["const"]: branch["description"] for branch in schema["oneOf"]}

    assert "EMAILS THE REQUESTER'S ADDRESS" in described_values[4]
    assert "SENDS NOTHING" in described_values[5]


async def test_the_create_tool_flags_the_same_survey_on_status_4(tools) -> None:
    """The second door to the same email. Opening a ticket AT solved resolves it
    on the spot and fires the survey, and nothing in the create schema said so."""
    description = described(tools["create_ticket"])
    schema = tools["create_ticket"].input_schema["properties"]["status"]
    described_values = {branch["const"]: branch["description"] for branch in schema["oneOf"]}

    assert "OPENING A TICKET AS 4 (SOLVED) EMAILS THE REQUESTER'S ADDRESS" in description
    assert "EMAILS THE REQUESTER'S ADDRESS" in described_values[4]


async def test_the_server_instructions_name_every_source_of_requester_email(tools) -> None:
    """Three tools can mail the requester and none of them does it by default.
    An internal desk does not change that — the requester is a colleague and the
    mail still leaves. That belongs in `instructions`, which a host shows once
    for the whole server."""
    instructions = " ".join((srv.mcp.instructions or "").split())

    assert "OUTBOUND EMAIL TO THE REQUESTER COMES FROM THREE PLACES AND NO DEFAULT TRIGGERS ONE" in instructions
    assert "`comment_on_ticket`" in instructions
    assert "`close_ticket` mails only when asked for `status: 4`" in instructions
