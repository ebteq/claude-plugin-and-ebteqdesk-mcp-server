"""The knowledge base WRITE surface: `propose_kb_article`, `update_kb_article`
and the read that exists to keep them honest, `get_kb_article_review`.

Three properties here are silent when they break, and all three are properties
of the DESCRIPTIONS as much as of the code:

  - THESE TOOLS CANNOT PUBLISH, and nothing on /api/v1 can. A model that tells a
    user "your article is live" has said something false about what is readable
    outside the desk. Publishing is a human's browser session, deliberately.

  - EVERY UPDATE RE-QUEUES THE ARTICLE and clears the reviewer's note. The trap
    that follows is the reason `get_kb_article_review` exists: before it, the
    only way to read a rejection was to PATCH the article, which destroyed the
    rejection you were reading. A description that does not say so re-creates
    the trap.

  - A PUBLISHED ARTICLE IS 202, AND THE 202 LOOKS LIKE A NO-OP. It used to be a
    409; now the edit is STAGED as a pending revision and `data` comes back as
    the LIVE, UNCHANGED article — not the submission. A description that does
    not say so leaves a model reading its own text back, not finding it, and
    reporting that nothing happened. The tests below assert on the fixture's
    live text precisely so that a payload echoing the request would fail them.

  - `revision` READS THREE WAYS AND ONE OF THEM IS SILENT. `pending`,
    `rejected`, or absent/null — and null is ambiguous between "never staged"
    and "staged, approved, applied, row deleted", because `state` is never
    `"approved"`. Only a description can carry that.

`get_kb_article_review` is gated on `kb:write` while changing nothing — and so
are `list_kb_proposals`, `list_kb_tree`, `list_kb_categories` and
`list_kb_folders` (tests/test_kb_proposals.py and tests/test_kb_structure.py),
which is why the tool roster in test_server_tools counts SEVENTEEN writes and
TWENTY-TWO write-scoped tools. That asymmetry is asserted rather than left as a
comment.
"""

from __future__ import annotations

import json

import httpx2
import pytest

from conftest import (
    always_json,
    article_review,
    kb_article_payload,
    kb_article_translated,
    scope_refusal,
    staged_revision,
)
from mcp.server.mcpserver.exceptions import ToolError

from ebteqdesk_mcp import server as srv
from ebteqdesk_mcp.client import EbteqdeskClient
from ebteqdesk_mcp.config import Config
from ebteqdesk_mcp.errors import InvalidRequestError


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


def _enum_of(schema: dict) -> list:
    """The enum on a `Literal[...] | None` argument.

    Pydantic renders an optional Literal as `anyOf: [{enum: [...]}, {null}]`
    rather than a bare `enum`, so the assertion has to look through the union —
    otherwise "the vocabulary is closed" would silently assert nothing.
    """
    if "enum" in schema:
        return list(schema["enum"])

    for branch in schema.get("anyOf", []):
        if "enum" in branch:
            return list(branch["enum"])

    raise AssertionError(f"no enum in {schema!r}")


# --------------------------------------------------------------------------- #
# propose_kb_article — the client
# --------------------------------------------------------------------------- #


async def test_propose_posts_the_documented_body(make_client) -> None:
    client, recorder = make_client(always_json(201, kb_article_payload()))

    await client.propose_kb_article(
        kb_folder_id=3,
        title="Resetting your VPN password",
        body="<p>Hold the power button.</p>",
        tags=["vpn"],
    )

    assert recorder.last.method == "POST"
    assert recorder.last.url.path == "/api/v1/kb/articles"
    assert sent(recorder) == {
        "kb_folder_id": 3,
        "title": "Resetting your VPN password",
        "body": "<p>Hold the power button.</p>",
        "tags": ["vpn"],
    }


async def test_propose_omits_absent_fields_rather_than_nulling_them(
    make_client,
) -> None:
    """🔴 The server's update semantics: an ABSENT key is not edited, a key
    present and empty IS an edit. Sending None as JSON null would turn "leave
    this alone" into "set this to null"."""
    client, recorder = make_client(always_json(201, kb_article_payload()))

    await client.propose_kb_article(kb_folder_id=3, title="Title only")

    assert sent(recorder) == {"kb_folder_id": 3, "title": "Title only"}


async def test_an_empty_tag_list_is_sent_because_it_means_clear_them(
    make_client,
) -> None:
    """`tags` REPLACES the whole set, so `[]` is a meaningful instruction. A
    truthiness test rather than `is not None` would drop it and make clearing
    tags impossible."""
    client, recorder = make_client(always_json(201, kb_article_payload()))

    await client.propose_kb_article(kb_folder_id=3, title="T", tags=[])

    assert sent(recorder)["tags"] == []


async def test_propose_never_sends_source(make_client) -> None:
    """`source` is forced to `api` server-side so machine-written articles stay
    identifiable. This client must not offer a way to relabel them, and the
    absence is asserted on the WIRE rather than on the signature — the
    signature would still pass if a **kwargs crept in."""
    client, recorder = make_client(always_json(201, kb_article_payload()))

    await client.propose_kb_article(kb_folder_id=3, title="T")

    assert "source" not in sent(recorder)


async def test_propose_returns_the_reference_verbatim(make_client) -> None:
    """`id:42`, because `slug` is null until a human first publishes — so the
    reference is the only handle the caller has."""
    client, _ = make_client(always_json(201, kb_article_payload()))

    body = await client.propose_kb_article(kb_folder_id=3, title="T")

    assert body["data"]["reference"] == "id:42"
    assert body["data"]["slug"] is None
    assert body["data"]["status"] == "draft"
    assert body["data"]["review"]["state"] == "pending"


# --------------------------------------------------------------------------- #
# update_kb_article — the client
# --------------------------------------------------------------------------- #


async def test_update_patches_the_reference_route(make_client) -> None:
    client, recorder = make_client(always_json(200, kb_article_payload()))

    await client.update_kb_article("id:42", title="Revised")

    assert recorder.last.method == "PATCH"
    assert recorder.last.url.path == "/api/v1/kb/articles/id:42"
    assert sent(recorder) == {"title": "Revised"}


async def test_update_accepts_a_frozen_slug_as_well(make_client) -> None:
    """Either form. `:` is a character a slug can never contain, which is what
    makes the two unambiguous without anybody parsing them apart."""
    client, recorder = make_client(always_json(200, kb_article_payload()))

    await client.update_kb_article("resetting-your-password", body="<p>New.</p>")

    assert recorder.last.url.path == "/api/v1/kb/articles/resetting-your-password"


async def test_an_empty_string_is_an_edit_and_is_sent(make_client) -> None:
    """`body=""` clears the body. Only None is dropped — that is the whole
    distinction the server draws between "not edited" and "cleared"."""
    client, recorder = make_client(always_json(200, kb_article_payload()))

    await client.update_kb_article("id:42", body="")

    assert sent(recorder) == {"body": ""}


async def test_update_offers_no_way_to_move_an_article(make_client) -> None:
    """A folder carries VISIBILITY, so a move is a visibility change wearing an
    organisational costume. The server refuses `kb_folder_id` on update; this
    client must not have a parameter for it at all."""
    import inspect

    signature = inspect.signature(EbteqdeskClient.update_kb_article)

    assert "kb_folder_id" not in signature.parameters


async def test_a_published_article_is_staged_and_does_not_raise(
    make_client,
) -> None:
    """🔴 THE 202 IS A SUCCESS, not the 409 it replaced. Unpublishing on edit
    would still hand an integration a one-request takedown of any live help
    article, so the live row is untouched — but the edit is now HELD rather than
    refused, and this client must return it rather than raise."""
    client, _ = make_client(staged_revision())

    payload = await client.update_kb_article(
        "resetting-your-password", title="New"
    )

    assert payload["revision"]["state"] == "pending"
    assert payload["revision"]["source"] == "api"


async def test_the_staged_echo_is_the_live_article_and_not_the_submission(
    make_client,
) -> None:
    """🔴 THE TRAP THIS WHOLE FEATURE CARRIES. A caller that reads `data` back to
    confirm its edit finds the OLD text. This asserts the client hands that
    through unaltered rather than "helpfully" merging the submission in — the
    payload has to be the same bytes a curl would show."""
    client, _ = make_client(staged_revision())

    data = (await client.update_kb_article("resetting-your-password", title="New"))[
        "data"
    ]

    assert data["title"] == "Resetting your VPN password"
    assert data["status"] == "published"
    assert data["translations"] == []
    # And the ARTICLE's own review record is untouched by staging a revision.
    assert data["review"]["state"] == "none"


async def test_the_revision_key_is_the_200_202_discriminator(make_client) -> None:
    """Nothing inside `data` distinguishes an applied edit from a staged one, so
    the presence of the top-level key is the entire signal. Both halves are
    asserted, because the useful claim is the ABSENCE on the draft path."""
    client, _ = make_client(always_json(200, kb_article_payload()))
    applied = await client.update_kb_article("id:42", title="New")

    assert "revision" not in applied

    client, _ = make_client(staged_revision())
    staged = await client.update_kb_article("resetting-your-password", title="New")

    assert "revision" in staged


async def test_a_malformed_edit_to_a_live_article_is_a_422_not_a_conflict(
    make_client,
) -> None:
    """The refusal ordering changed with the 409's removal: validation runs
    before the staging branch, so the caller is told which FIELD is wrong rather
    than being told the article is published."""
    client, _ = make_client(
        always_json(
            422,
            {
                "error": "The request body is not valid.",
                "errors": {
                    "translations.zhcn.body": [
                        "中文: This article references 1 image or video that no "
                        "longer exists. Re-upload or remove it: 01JZZ"
                    ]
                },
            },
        )
    )

    with pytest.raises(InvalidRequestError) as excinfo:
        await client.update_kb_article(
            "resetting-your-password", body="<p>x</p>", locale="zhcn"
        )

    assert excinfo.value.status_code == 422
    assert "translations.zhcn.body" in str(excinfo.value)


@pytest.mark.parametrize("reference", ["", "   ", "a/b", "/", 42, None])
async def test_a_bad_reference_is_refused_before_sending(
    make_client, reference
) -> None:
    """It would build a URL matching a DIFFERENT route, and the caller would get
    a confusing 404 about the wrong thing."""
    client, recorder = make_client(always_json(200, kb_article_payload()))

    with pytest.raises(ValueError, match="reference"):
        await client.update_kb_article(reference, title="T")

    assert recorder.requests == []


# --------------------------------------------------------------------------- #
# get_kb_article_review — the client
# --------------------------------------------------------------------------- #


async def test_the_review_read_is_a_get_with_no_body(make_client) -> None:
    """🔴 The whole point: NO side effects. A PATCH here would re-queue the
    article and clear the note being read."""
    client, recorder = make_client(always_json(200, kb_article_payload()))

    await client.get_kb_article_review("id:42")

    assert recorder.last.method == "GET"
    assert recorder.last.url.path == "/api/v1/kb/articles/id:42/review"
    assert not recorder.last.content


async def test_the_review_read_surfaces_a_staged_revision(make_client) -> None:
    """The `revision` block is emitted UNCONDITIONALLY on this endpoint, unlike
    the write path, so `null` is a real answer and not a missing key."""
    client, _ = make_client(
        article_review(
            {
                "state": "rejected",
                "source": "api",
                "requested_at": "2026-08-28T03:47:00+00:00",
                "reviewed_at": "2026-08-28T03:47:25+00:00",
                "reviewed_by": {"id": 1, "name": "Admin"},
                "note": "Chinese copy needs the legal disclaimer.",
            }
        )
    )

    payload = await client.get_kb_article_review("id:42")

    assert payload["revision"]["state"] == "rejected"
    assert payload["revision"]["note"] == "Chinese copy needs the legal disclaimer."
    assert payload["revision"]["reviewed_by"]["name"] == "Admin"
    # 🔴 And the ARTICLE's own review block is a DIFFERENT record. A test that
    # only read one of them would pass with the two collapsed into one.
    assert payload["data"]["review"]["state"] == "pending"


async def test_the_review_read_reports_no_staged_revision_as_null(
    make_client,
) -> None:
    """🔴 null is AMBIGUOUS — never staged, or staged AND APPROVED (the row is
    deleted on approval, and `state` is never "approved"). The client must hand
    the null through rather than dropping the key, because a caller that cannot
    see it cannot even know the question exists."""
    client, _ = make_client(article_review(None))

    payload = await client.get_kb_article_review("id:42")

    assert "revision" in payload
    assert payload["revision"] is None


async def test_the_review_read_surfaces_the_rejection_note(make_client) -> None:
    client, _ = make_client(
        always_json(
            200,
            kb_article_payload(
                state="rejected",
                note="Contradicts the current pricing page.",
                reviewed_at="2026-08-13T11:00:00+00:00",
                reviewed_by={"id": 7, "name": "Dana"},
            ),
        )
    )

    review = (await client.get_kb_article_review("id:42"))["data"]["review"]

    assert review["state"] == "rejected"
    assert review["note"] == "Contradicts the current pricing page."
    assert review["reviewed_by"]["name"] == "Dana"


# --------------------------------------------------------------------------- #
# Through the MCP layer
# --------------------------------------------------------------------------- #


async def test_the_kb_write_tools_round_trip_through_mcp(wired) -> None:
    wired(always_json(201, kb_article_payload()))

    for name, arguments in (
        ("propose_kb_article", {"kb_folder_id": 3, "title": "T"}),
        ("update_kb_article", {"reference": "id:42", "title": "T"}),
        ("get_kb_article_review", {"reference": "id:42"}),
    ):
        result = await srv.mcp.call_tool(name, arguments)

        assert not result.is_error
        assert result.structured_content["data"]["reference"] == "id:42"


async def test_no_kb_write_tool_is_reachable_without_kb_write(wired) -> None:
    """Including the review READ, which is gated on `kb:write` because its
    corpus is every article including drafts."""
    wired(scope_refusal("kb:write", requested=["kb:read"], scopes=["kb:read"]))

    for name, arguments in (
        ("propose_kb_article", {"kb_folder_id": 3, "title": "T"}),
        ("update_kb_article", {"reference": "id:42", "title": "T"}),
        ("get_kb_article_review", {"reference": "id:42"}),
    ):
        with pytest.raises(ToolError) as excinfo:
            await srv.mcp.call_tool(name, arguments)

        text = str(excinfo.value)
        assert "kb:write" in text
        assert "mint a NEW key" in text


async def test_a_staged_202_reaches_the_mcp_client_as_a_SUCCESS(wired) -> None:
    """🔴 It used to arrive as `is_error`. A host that branched on that now gets
    a normal result, which is the observable break behind the 4.0.0 major."""
    wired(staged_revision())

    result = await srv.mcp.call_tool(
        "update_kb_article", {"reference": "resetting-your-password", "title": "T"}
    )

    assert not result.is_error
    assert result.structured_content["revision"]["state"] == "pending"
    # The live text, not the submission — through the whole MCP layer.
    assert result.structured_content["data"]["title"] == "Resetting your VPN password"


async def test_the_review_tool_carries_the_revision_through_mcp(wired) -> None:
    wired(article_review({"state": "pending", "source": "api", "note": None}))

    result = await srv.mcp.call_tool("get_kb_article_review", {"reference": "id:42"})

    assert not result.is_error
    assert result.structured_content["revision"]["state"] == "pending"


# --------------------------------------------------------------------------- #
# The rules only a description can carry
# --------------------------------------------------------------------------- #


async def test_both_kb_writes_lead_with_their_side_effect(tools) -> None:
    for name in ("propose_kb_article", "update_kb_article"):
        first_line = (tools[name].description or "").strip().splitlines()[0]

        assert first_line.startswith("WRITES TO EBTEQDESK"), first_line


async def test_the_kb_writes_say_they_cannot_publish(tools) -> None:
    """A model that tells a user their article is live has said something false
    about what is readable outside the desk."""
    description = described(tools["propose_kb_article"])

    assert "THIS CANNOT PUBLISH, AND NOTHING ON THIS API CAN" in description
    assert "status = draft" in description
    assert "Do not tell a user their article is live" in description


async def test_the_update_tool_says_every_edit_re_queues_the_article(tools) -> None:
    description = described(tools["update_kb_article"])

    assert "EVERY UPDATE RESETS THE REVIEW" in description
    assert "ANY PREVIOUS APPROVAL OR REJECTION NOTE IS CLEARED" in description


async def test_the_update_tool_forbids_using_it_to_check_a_verdict(tools) -> None:
    """🔴 The trap `get_kb_article_review` replaces. Reading the review state
    through a write destroys the review state."""
    description = described(tools["update_kb_article"])

    assert "DO NOT CALL THIS TO CHECK A VERDICT" in description
    assert "destroys the review state" in description
    assert "`get_kb_article_review`" in description


async def test_the_review_tool_says_it_changes_nothing(tools) -> None:
    description = described(tools["get_kb_article_review"])

    assert "CHECKING A VERDICT DOES NOT DESTROY IT" in description
    assert "It changes nothing at all" in description
    assert "Never with a write" in description
    # And the field actually worth reading.
    assert "`review.note`" in description


async def test_the_review_tool_explains_its_unusual_scope(tools) -> None:
    """`kb:write` for a read. A user who saw `kb:read` on the sibling tools and
    minted accordingly needs to be told why this one refuses."""
    description = described(tools["get_kb_article_review"])

    assert "`kb:write` scope — not `kb:read`" in description
    assert "including drafts" in description


async def test_the_update_tool_explains_the_two_branches(tools) -> None:
    """The draft/published split is not an argument and cannot be read off the
    signature, so the description is the only place it can live."""
    description = described(tools["update_kb_article"])

    assert "THE TWO BRANCHES" in description
    assert "HTTP 200" in description and "HTTP 202" in description
    assert "PUBLISHED — the live article is NOT TOUCHED" in description


async def test_the_update_tool_says_the_202_echo_is_the_live_article(tools) -> None:
    """🔴 THE ONE SENTENCE THIS FEATURE CANNOT SHIP WITHOUT. A model that reads
    `data` back to confirm its edit concludes nothing happened."""
    description = described(tools["update_kb_article"])

    assert (
        "ON THE PUBLISHED BRANCH `data` IS NOT WHAT YOU SENT — IT IS WHAT "
        "CUSTOMERS ARE STILL READING" in description
    )
    assert "`data.translations` may be `[]`" in description
    assert "CHECK FOR THE `revision` KEY" in description


async def test_the_update_tool_retracts_the_409(tools) -> None:
    """Older notes, older client code and older habits all say 409. A
    description that merely stopped mentioning it would leave a model to fall
    back on what it already believed."""
    description = described(tools["update_kb_article"])

    assert "THERE IS NO 409 ON THIS TOOL ANY MORE" in description


async def test_the_update_tool_says_a_second_call_replaces_the_revision(
    tools,
) -> None:
    description = described(tools["update_kb_article"])

    assert "ONE revision row per article" in description
    assert "REPLACES the pending revision instead of queueing a second one" in (
        description
    )


async def test_the_review_tool_teaches_the_three_way_revision_reading(tools) -> None:
    """🔴 `null` is the reading that will be got wrong: it is "never staged" OR
    "approved and applied", and no field separates them."""
    description = described(tools["get_kb_article_review"])

    assert "READS THREE WAYS" in description
    assert 'never `"approved"`' in description.replace("NEVER", "never")
    assert "AMBIGUOUS" in description
    assert "Never report a null `revision` as \"still waiting\"" in description


async def test_the_review_tool_is_named_as_the_way_to_read_a_rejection(
    tools,
) -> None:
    """A PATCH would replace the revision row and take the note with it."""
    description = described(tools["get_kb_article_review"])

    assert "ONLY IDEMPOTENT WAY TO READ A STAGED EDIT'S VERDICT" in description


async def test_the_kb_tools_explain_the_reference_format(tools) -> None:
    """`{reference}` is not always a slug — an API-created article has none
    until a human publishes, so `id:<n>` is the normal case. A model that tried
    to build a slug from the title would 404 forever."""
    for name in ("update_kb_article", "get_kb_article_review"):
        description = described(tools[name])

        assert "`id:<n>`" in description
        assert "frozen" in description or "first publish" in description.lower()

    assert "do not try to construct a slug from the title" in described(
        tools["update_kb_article"]
    ).lower()


async def test_the_propose_tool_flags_the_folder_as_permanent(tools) -> None:
    description = described(tools["propose_kb_article"])

    assert "THE ONE FIELD THAT CANNOT BE CHANGED LATER" in description
    assert "VISIBILITY" in description
    assert "ask the user rather than guessing" in description


async def test_the_kb_tools_say_tags_replace_rather_than_merge(tools) -> None:
    for name in ("propose_kb_article", "update_kb_article"):
        description = described(tools[name])

        assert "REPLACES the whole set" in description


async def test_the_kb_write_tools_expose_exactly_their_documented_arguments(
    tools,
) -> None:
    def props(name: str) -> set:
        return set(tools[name].input_schema.get("properties", {}))

    def required(name: str) -> set:
        return set(tools[name].input_schema.get("required", []))

    # The language arguments are FLAT here and NESTED on the wire. An MCP tool
    # is called by a language model and a nested `translations` map is a shape a
    # model gets subtly wrong — a misspelt locale key VALIDATES and is then
    # dropped server-side, so the write returns 2xx having stored nothing. The
    # server contract stays single and the client builds it.
    #
    # 🔴 EIGHT PER-LANGUAGE ARGUMENTS, NOT ONE `translations` OBJECT, and the
    # count is asserted so that a future "tidy-up" into a dict has to argue with
    # this test. Putting the locale in the ARGUMENT NAME puts it in the schema's
    # `properties`, where the SDK refuses an unknown one before dispatch.
    LANGUAGE_FIELDS = {
        "en_title", "en_body", "en_seo_title", "en_seo_description",
        "zhcn_title", "zhcn_body", "zhcn_seo_title", "zhcn_seo_description",
    }

    assert props("propose_kb_article") == {
        "kb_folder_id", "title", "body", "seo_title", "seo_description", "tags",
        "locale",
    } | LANGUAGE_FIELDS
    # 🔴 No `kb_folder_id`: an article cannot be moved through this API.
    #
    # 🔴 AND NO `allow_missing_versions` ON PROPOSE — it is on update only. The
    # guard it opts out of cannot fire on a create (a new article has never been
    # in a help centre, so nothing can be taken out of one), and an argument that
    # can never do anything is an argument a model will eventually set.
    assert props("update_kb_article") == {
        "reference", "title", "body", "seo_title", "seo_description", "tags",
        "locale", "allow_missing_versions",
    } | LANGUAGE_FIELDS

    # 🔴 AND NOT A `translations` ARGUMENT ON EITHER. The client method has one
    # — it is the server's own nested shape and it is how the tools pass what
    # they assemble — but a model never fills it in.
    for name in ("propose_kb_article", "update_kb_article"):
        assert "translations" not in props(name), name
    assert props("get_kb_article_review") == {"reference"}

    assert required("propose_kb_article") == {"kb_folder_id", "title"}
    assert required("update_kb_article") == {"reference"}
    assert required("get_kb_article_review") == {"reference"}

    # 🔴 A CLOSED VOCABULARY, ENFORCED BY THE SDK BEFORE DISPATCH. `zhcn` and
    # `zh-cn` are BOTH real locale strings in Ebteqdesk and only one of them is
    # the help centre's; a row written with the other matches no reader and the
    # article vanishes from both languages. A free-form string here would put
    # that mistake one typo away.
    for name in ("propose_kb_article", "update_kb_article"):
        schema = tools[name].input_schema["properties"]["locale"]
        assert _enum_of(schema) == ["en", "zhcn"], name


async def test_no_tool_is_called_publish(tools) -> None:
    """The one name that must never appear on this server. A publish endpoint
    would defeat the review workflow entirely, and there is none to wrap."""
    assert not [name for name in tools if "publish" in name]


# --------------------------------------------------------------------------- #
# PER-LANGUAGE CONTENT — `locale`, and the guard that stops an integration
# silently deleting an article from a help centre
# --------------------------------------------------------------------------- #
#
# ---------------------------------------------------------------------------
# 🔴 THE SHAPE MISMATCH THESE TESTS PIN, AND WHY IT IS DELIBERATE
# ---------------------------------------------------------------------------
# The TOOL takes `locale="zhcn"` flat. The SERVER takes a nested
# `translations: {"zhcn": {...}}` — the same three-valued object the authoring
# editor posts, one writer, one vocabulary. The flat/nested translation happens
# in exactly one function (`_kb_translations`), and these tests assert the
# resulting BODY rather than the arguments, because a client whose payload
# drifted from the server's contract fails silently: an unknown key is dropped
# by `Validator::validated()` and the write returns 201 having stored nothing.
#
# ---------------------------------------------------------------------------
# 🔴 AND THE ASYMMETRY BETWEEN CREATE AND UPDATE IS THE POINT, NOT AN OVERSIGHT
# ---------------------------------------------------------------------------
# A CREATE sends the text twice — to the base columns and to the version —
# because `kb_articles.title` is NOT NULL and ten surfaces read the base columns
# directly. An UPDATE sends it ONLY to the version, because on an existing
# article the base columns already hold the OTHER language's text and
# overwriting them would corrupt it. Two tests below assert each half; either
# one alone would let the other regress.


async def test_propose_with_a_locale_sends_both_the_base_and_the_version(
    make_client,
) -> None:
    client, recorder = make_client(always_json(201, kb_article_translated()))

    await client.propose_kb_article(
        kb_folder_id=3,
        title="重置 VPN 密码",
        body="<p>打开门户。</p>",
        locale="zhcn",
    )

    body = sent(recorder)

    # The version — nested under the locale, which is the server's shape.
    assert body["translations"] == {
        "zhcn": {"title": "重置 VPN 密码", "body": "<p>打开门户。</p>"}
    }

    # 🔴 And the base columns too. `title` is NOT NULL server-side and a new
    # article whose base columns were empty would be a blank row on the review
    # queue, the authoring tree and this API's own echo.
    assert body["title"] == "重置 VPN 密码"
    assert body["body"] == "<p>打开门户。</p>"


async def test_update_with_a_locale_sends_only_the_version(make_client) -> None:
    """🔴 THE ONE THAT PROTECTS THE OTHER LANGUAGE.

    On an article that already has versions the base columns hold the OTHER
    language's text. A PATCH that put the Chinese title at the top level would
    overwrite the English an author wrote, on a request that returned 200.
    """
    client, recorder = make_client(always_json(200, kb_article_translated()))

    await client.update_kb_article(
        "id:42", title="重置 VPN 密码", body="<p>打开门户。</p>", locale="zhcn"
    )

    body = sent(recorder)

    assert body["translations"] == {
        "zhcn": {"title": "重置 VPN 密码", "body": "<p>打开门户。</p>"}
    }
    assert "title" not in body
    assert "body" not in body


async def test_omitting_the_locale_sends_no_translations_key_at_all(
    make_client,
) -> None:
    """⚠️ ABSENCE IS NOT AN EMPTY OBJECT.

    To the server an ABSENT `translations` means "not editing versions", which is
    what every pre-feature client sends and what must keep leaving existing
    versions exactly as they are. A `"translations": {}` would be a client
    inventing a fourth value for a three-valued key.
    """
    client, recorder = make_client(always_json(200, kb_article_payload()))

    await client.update_kb_article("id:42", title="Renamed")

    body = sent(recorder)

    assert "translations" not in body
    assert body == {"title": "Renamed"}


async def test_a_none_field_is_omitted_from_the_version_not_nulled(
    make_client,
) -> None:
    """⚠️ INSIDE A VERSION, `null` IS THE DELETE SIGNAL.

    `{"zhcn": null}` is the editor's "remove the Chinese version" gesture, and
    the server refuses it on this surface. A client that nulled unset fields
    would send a delete every time a caller passed no body.
    """
    client, recorder = make_client(always_json(200, kb_article_translated()))

    await client.update_kb_article("id:42", title="重置 VPN 密码", locale="zhcn")

    assert sent(recorder)["translations"] == {"zhcn": {"title": "重置 VPN 密码"}}


async def test_allow_missing_versions_is_sent_only_when_true(make_client) -> None:
    """The flag is an act, not a field. A False on the wire would ride along on
    every ordinary edit until nobody read it any more."""
    client, recorder = make_client(always_json(200, kb_article_translated()))

    await client.update_kb_article("id:42", title="x", locale="zhcn")
    assert "allow_missing_versions" not in sent(recorder)

    await client.update_kb_article(
        "id:42", title="x", locale="zhcn", allow_missing_versions=True
    )
    assert sent(recorder)["allow_missing_versions"] is True


async def test_the_hiding_refusal_reaches_the_caller_with_its_reasoning(
    make_client,
) -> None:
    """🔴 THE REFUSAL IS THE FEATURE, AND ITS TEXT IS ITS INTERFACE.

    An integration has no warning dialog. The 422 is the only place it can be
    told that this write removes the article from a help centre, so the message
    has to survive the client layer intact — a client that replaced it with
    "validation failed" would leave the caller with no way to choose between the
    repair and the deliberate removal.
    """
    refusal = {
        "error": "The request body is not valid.",
        "errors": {
            "translations": [
                "Saving this would take the article out of the English help centre. "
                "It would have a version in 中文 and none in English, and an article "
                "with a version in any language appears only in the languages it has "
                "one for. Send the translations.en version in this same request, or "
                'pass "allow_missing_versions": true to take it out of English '
                "deliberately."
            ]
        },
    }

    client, _ = make_client(always_json(422, refusal))

    with pytest.raises(InvalidRequestError) as raised:
        await client.update_kb_article("id:42", title="重置", locale="zhcn")

    message = str(raised.value)

    assert "English help centre" in message
    assert "translations.en" in message
    assert "allow_missing_versions" in message


# --------------------------------------------------------------------------- #
# The DESCRIPTIONS. These are the only documentation an LLM caller ever reads,
# so the dangerous facts are asserted rather than trusted to survive an edit.
# --------------------------------------------------------------------------- #


async def test_the_write_tools_say_what_a_locale_costs(tools) -> None:
    propose = described(tools["propose_kb_article"])
    update = described(tools["update_kb_article"])

    # 🔴 Omitted vs given, stated on both.
    for text in (propose, update):
        assert "zhcn" in text
        assert "appears ONLY in the languages it has" in text or (
            "appears ONLY in the languages it has one for" in text
        )

    # 🔴 The refusal, and BOTH ways past it — a docstring that named only the
    # opt-in would train a model to set it rather than to send the other
    # version, which is the difference between translating an article and
    # deleting it from a help centre.
    assert "REFUSES" in update
    assert "allow_missing_versions=True" in update
    assert "the repair" in update

    # 🔴 An edit without a locale on an article that HAS versions reaches nobody.
    assert "REACHES NOBODY" in update


async def test_the_write_tools_say_deletion_is_not_an_api_operation(tools) -> None:
    """There is no delete-article tool and there is no delete-version tool, for
    the same reason. A model that thinks it can undo a version it added will add
    one on the assumption it is reversible."""
    for name in ("propose_kb_article", "update_kb_article"):
        text = described(tools[name])
        assert "NO WAY TO REMOVE A LANGUAGE VERSION" in text.upper()
        assert "authoring screens" in text


async def test_update_says_one_review_state_covers_every_language(tools) -> None:
    """A schema fact with a workflow consequence: fixing a Chinese typo sends the
    English version back to a human too. A model that does not know this will
    make two calls where one would do and put the article through review twice."""
    text = described(tools["update_kb_article"])

    assert "ONE REVIEW STATE PER ARTICLE, NOT ONE PER LANGUAGE" in text


async def test_the_write_tools_point_at_translations_for_verification(tools) -> None:
    """`data.title` is the BASE text. On an article with versions it is not what
    any reader gets, so confirming an edit against it is how a Chinese revision
    gets reported as landed when it went nowhere."""
    for name in ("propose_kb_article", "update_kb_article"):
        assert "data.translations" in described(tools[name])


# --------------------------------------------------------------------------- #
# TWO LANGUAGES, ONE REQUEST
# --------------------------------------------------------------------------- #
#
# ---------------------------------------------------------------------------
# 🔴 WHAT THESE TESTS EXIST FOR, IN ONE PARAGRAPH
# ---------------------------------------------------------------------------
# Before this, both tools took ONE `locale` per call, and on a DRAFT calling
# twice worked — the per-locale rows accumulated. The published-article feature
# then made an edit STAGE A REVISION, and `kb_article_revisions` is
# `unique(kb_article_id)`. Two calls stopped accumulating and started REPLACING:
# `en` then `zhcn` leaves a revision holding only `zhcn`, the missing-version
# guard refuses it for taking the article out of the English help centre, and
# the flag that gets past that guard PERFORMS that removal. There was no
# sequence of calls that added a Chinese version to a published English article.
#
# So the property under test is not "the argument exists". It is THE BODY THAT
# REACHES THE SERVER — one request, one `translations` object, both locales in
# it — which is why every assertion below reads the recorded request rather than
# the arguments, and why the tool-layer ones count the requests.


async def test_a_bilingual_update_sends_ONE_request_carrying_BOTH_locales(
    make_client,
) -> None:
    """🔴 THE BUG, PINNED. One PATCH, one `translations` object, two keys in it —
    so one revision row holds both languages and neither can be replaced by the
    other."""
    client, recorder = make_client(always_json(202, kb_article_translated()))

    await client.update_kb_article(
        "id:49",
        translations={
            "en": {"title": "Resetting your VPN password", "body": "<p>Hold it.</p>"},
            "zhcn": {"title": "重置 VPN 密码", "body": "<p>打开门户。</p>"},
        },
    )

    assert len(recorder.requests) == 1

    body = sent(recorder)

    assert body["translations"] == {
        "en": {"title": "Resetting your VPN password", "body": "<p>Hold it.</p>"},
        "zhcn": {"title": "重置 VPN 密码", "body": "<p>打开门户。</p>"},
    }

    # ⚠️ AND NO `allow_missing_versions`. The repair and the removal are
    # different acts: sending both versions is what stops the guard firing, and
    # a client that reached for the flag here would be deleting a language to
    # get past an error message.
    assert "allow_missing_versions" not in body


async def test_a_bilingual_propose_sends_both_versions_beside_the_base_columns(
    make_client,
) -> None:
    """The create asymmetry survives more than one language: the base columns
    are still written, because `kb_articles.title` is NOT NULL and ten surfaces
    read them."""
    client, recorder = make_client(always_json(201, kb_article_translated()))

    await client.propose_kb_article(
        kb_folder_id=3,
        title="Resetting your VPN password",
        body="<p>Hold it.</p>",
        translations={
            "en": {"title": "Resetting your VPN password", "body": "<p>Hold it.</p>"},
            "zhcn": {"title": "重置 VPN 密码", "body": "<p>打开门户。</p>"},
        },
    )

    body = sent(recorder)

    assert set(body["translations"]) == {"en", "zhcn"}
    assert body["title"] == "Resetting your VPN password"
    assert body["body"] == "<p>Hold it.</p>"


async def test_per_language_versions_leave_the_flat_fields_where_the_caller_put_them(
    make_client,
) -> None:
    """⚠️ ONLY `locale=` MOVES THE FLAT FIELDS INTO A VERSION.

    `locale=` has to: it names one language and the base columns hold the other
    language's text, so leaving `title` at the top level would corrupt it. The
    per-language form names its languages explicitly, so a flat `title=` beside
    it is a separate statement about the BASE columns — which the server accepts
    in the same payload and the authoring editor posts that way.
    """
    client, recorder = make_client(always_json(200, kb_article_translated()))

    await client.update_kb_article(
        "id:49",
        tags=["vpn"],
        translations={"zhcn": {"title": "重置 VPN 密码"}},
    )

    body = sent(recorder)

    assert body["tags"] == ["vpn"]
    assert body["translations"] == {"zhcn": {"title": "重置 VPN 密码"}}


async def test_naming_one_language_twice_is_refused_before_anything_is_sent(
    make_client,
) -> None:
    """🔴 THE ONE REFUSAL, AND IT COSTS NO ROUND TRIP.

    In a dict the two spellings of the same locale silently overwrite each
    other, and the caller never learns which half was thrown away. A ValueError
    reaches an MCP host as readable prose (see `_call`), so the model is told
    which language it said twice.
    """
    client, recorder = make_client(always_json(200, kb_article_translated()))

    with pytest.raises(ValueError) as raised:
        await client.update_kb_article(
            "id:49",
            title="重置 VPN 密码",
            locale="zhcn",
            translations={"zhcn": {"title": "重置 VPN 密码"}},
        )

    assert "zhcn" in str(raised.value)
    assert "twice" in str(raised.value)
    assert recorder.requests == []


async def test_an_empty_versions_mapping_sends_no_translations_key(
    make_client,
) -> None:
    """⚠️ ABSENCE IS NOT AN EMPTY OBJECT, and this is the path that would break
    it: the tools build the mapping from arguments that are all None on an
    ordinary edit, so `{}` arrives here on every call that names no language."""
    client, recorder = make_client(always_json(200, kb_article_payload()))

    await client.update_kb_article("id:42", title="Renamed", translations={})

    assert sent(recorder) == {"title": "Renamed"}


async def test_a_whole_version_of_none_survives_as_the_delete_signal(
    make_client,
) -> None:
    """🔴 THE THIRD VALUE OF A THREE-VALUED KEY.

    `translations.{locale} === null` is the editor's "remove this language
    version" gesture. The API controller refuses it with its own 422 and no MCP
    tool offers it — but the client's SHAPE has to be able to say what the
    server's shape can say, or it is a different contract. Absent, null and an
    object are three distinct statements and this pins the difference between
    the first two.
    """
    client, recorder = make_client(always_json(422, {"error": "no"}))

    with pytest.raises(InvalidRequestError):
        await client.update_kb_article("id:49", translations={"zhcn": None})

    assert sent(recorder)["translations"] == {"zhcn": None}


async def test_the_single_locale_form_still_builds_exactly_the_body_it_did(
    make_client,
) -> None:
    """The compatibility claim behind the MINOR version bump, asserted rather
    than assumed: a caller written against the one-language form sends the same
    bytes it always sent, with no `translations` key it did not ask for and no
    empty object where there was nothing."""
    client, recorder = make_client(always_json(200, kb_article_translated()))

    await client.update_kb_article(
        "id:42", title="重置 VPN 密码", body="<p>打开门户。</p>", locale="zhcn"
    )

    assert sent(recorder) == {
        "translations": {"zhcn": {"title": "重置 VPN 密码", "body": "<p>打开门户。</p>"}}
    }


# --------------------------------------------------------------------------- #
# ...through the real tool layer, which is where the bug actually lived
# --------------------------------------------------------------------------- #


async def test_the_TOOL_turns_the_flat_language_arguments_into_one_request(
    wired,
) -> None:
    """🔴 THE REGRESSION THAT MATTERS. The client method could always have been
    given a nested mapping; what could not be done was saying it THROUGH THE
    TOOL, which is the only surface a model has. This drives
    `srv.mcp.call_tool` and reads the request that came out the other end."""
    requests: list = []

    responder = staged_revision()

    def handler(request):
        requests.append(request)
        return responder(request)

    wired(handler)

    result = await srv.mcp.call_tool(
        "update_kb_article",
        {
            "reference": "resetting-your-password",
            "en_title": "Resetting your VPN password",
            "en_body": "<p>Hold the power button.</p>",
            "zhcn_title": "重置 VPN 密码",
            "zhcn_body": "<p>打开门户。</p>",
        },
    )

    assert not result.is_error
    assert len(requests) == 1

    body = json.loads(requests[0].content)

    assert body["translations"] == {
        "en": {
            "title": "Resetting your VPN password",
            "body": "<p>Hold the power button.</p>",
        },
        "zhcn": {"title": "重置 VPN 密码", "body": "<p>打开门户。</p>"},
    }


async def test_the_propose_TOOL_files_both_languages_in_one_request(wired) -> None:
    requests: list = []

    responder = always_json(201, kb_article_translated())

    def handler(request):
        requests.append(request)
        return responder(request)

    wired(handler)

    result = await srv.mcp.call_tool(
        "propose_kb_article",
        {
            "kb_folder_id": 3,
            "title": "Resetting your VPN password",
            "en_title": "Resetting your VPN password",
            "zhcn_title": "重置 VPN 密码",
        },
    )

    assert not result.is_error
    assert len(requests) == 1
    assert set(json.loads(requests[0].content)["translations"]) == {"en", "zhcn"}


async def test_the_TOOL_sends_no_translations_key_when_no_language_is_named(
    wired,
) -> None:
    """The ordinary edit, unchanged. Eight arguments nobody passed are eight
    keys nobody sends — and an empty `translations` object would be this client
    inventing a fourth value for a three-valued key."""
    requests: list = []

    responder = always_json(200, kb_article_payload())

    def handler(request):
        requests.append(request)
        return responder(request)

    wired(handler)

    await srv.mcp.call_tool(
        "update_kb_article", {"reference": "id:42", "title": "Renamed"}
    )

    assert json.loads(requests[0].content) == {"title": "Renamed"}


async def test_the_TOOL_refuses_one_language_named_twice_without_sending_it(
    wired,
) -> None:
    """The contradiction is caught before the socket. Only the request count is
    asserted, not the message text: what an MCP host shows for a raised
    exception is the SDK's business and varies by version, while "nothing went
    on the wire" is this package's."""
    requests: list = []

    responder = always_json(200, kb_article_translated())

    def handler(request):
        requests.append(request)
        return responder(request)

    wired(handler)

    with pytest.raises(ToolError):
        await srv.mcp.call_tool(
            "update_kb_article",
            {
                "reference": "id:49",
                "title": "重置 VPN 密码",
                "locale": "zhcn",
                "zhcn_title": "重置 VPN 密码",
            },
        )

    assert requests == []


# --------------------------------------------------------------------------- #
# The description, which is where the bug was actually reachable from
# --------------------------------------------------------------------------- #


async def test_the_write_tools_teach_ONE_CALL_for_two_languages(tools) -> None:
    """A model reads the description and nothing else. Both tools have to name
    the per-language arguments and say they travel together."""
    propose = described(tools["propose_kb_article"])
    update = described(tools["update_kb_article"])

    for text in (propose, update):
        assert "en_title" in text
        assert "zhcn_body" in text

    assert "TO FILE THE ARTICLE IN BOTH LANGUAGES, SEND BOTH IN THIS ONE CALL" in propose
    assert "SO PUT EVERY LANGUAGE IN ONE CALL" in update


async def test_no_write_tool_still_teaches_the_two_call_pattern(tools) -> None:
    """🔴 THE ADVICE THAT HAD TO GO, ASSERTED AS ABSENT.

    "File one language, then add the other with `update_kb_article`" was
    correct while both calls edited a draft in place. Against a PUBLISHED
    article the second call stages a revision that REPLACES the first, so the
    old advice now loses a language — silently, on a 202 that looks like it
    worked. Leaving one sentence of it behind would keep the bug alive in the
    only documentation a model reads.
    """
    propose = described(tools["propose_kb_article"])
    update = described(tools["update_kb_article"])

    assert "call this once with one locale" not in propose
    assert "in a second call" not in update
    assert "two calls to fix two languages" not in update


async def test_the_update_tool_explains_why_two_calls_cannot_work(tools) -> None:
    """Not just "do it in one call" — WHY, because a model that knows only the
    rule will route around it the first time one call looks awkward."""
    text = described(tools["update_kb_article"])

    assert "ONE revision row per article" in text
    assert "REPLACES" in text
    assert "There is no order of two calls that works" in text
