"""Shared fixtures.

The mock sits at the SOCKET (`httpx2.MockTransport`), not over the client's
methods. That is the whole point: a double that replaced `EbteqdeskClient._get`
would prove the tests call the tests. With the transport faked, every assertion
below runs through the real header construction, the real URL building, the real
JSON decoding and the real error mapping — the only thing that does not happen
is a TCP connection.
"""

from __future__ import annotations

from typing import Any, Callable

import httpx2
import pytest

from ebteqdesk_mcp.client import EbteqdeskClient
from ebteqdesk_mcp.config import Config

BASE_URL = "https://ebteqdesk.test"
TOKEN = "6|test-token-value"


@pytest.fixture
def config() -> Config:
    return Config(base_url=BASE_URL, token=TOKEN, timeout=5.0)


class Recorder:
    """Captures the requests a test's client actually made."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []

    @property
    def last(self) -> httpx2.Request:
        assert self.requests, "no request was made"
        return self.requests[-1]

    @property
    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


@pytest.fixture
def make_client(
    config: Config,
) -> Callable[[Callable[[httpx2.Request], httpx2.Response]], tuple[EbteqdeskClient, Recorder]]:
    """Build a client whose socket is `handler`, plus a Recorder of its traffic."""

    def factory(
        handler: Callable[[httpx2.Request], httpx2.Response],
    ) -> tuple[EbteqdeskClient, Recorder]:
        recorder = Recorder()

        def recording_handler(request: httpx2.Request) -> httpx2.Response:
            recorder.requests.append(request)
            return handler(request)

        client = EbteqdeskClient(
            config, transport=httpx2.MockTransport(recording_handler)
        )

        return client, recorder

    return factory


def json_response(status_code: int, payload: Any, **kwargs: Any) -> httpx2.Response:
    return httpx2.Response(status_code, json=payload, **kwargs)


def always(response: httpx2.Response) -> Callable[[httpx2.Request], httpx2.Response]:
    """A handler that answers every request identically."""
    return lambda _request: response


def always_json(status_code: int, payload: Any, **kwargs: Any):
    return lambda _request: json_response(status_code, payload, **kwargs)


#: The server's real refusal sentence, copied verbatim from
#: App\Http\Middleware\EnsureApiScope::refuseScope(). It is here so the tests
#: read what a client actually receives — but note that NOTHING under test may
#: parse it. It is carried to the user and never inspected.
SCOPE_REFUSAL = (
    "This API key is not permitted to {scope}. A scope resolves only while both "
    "the key and its owner's role carry it — GET /api/v1/user reports which half "
    "is missing."
)

#: The OTHER 403, verbatim from
#: Api\V1\TicketWritesController::refuseAbility(). Same status, different field,
#: opposite advice — and unlike the scope refusal this one NAMES the thing that
#: failed, because the account's own permission list is already public to it via
#: GET /api/v1/user. Nothing under test parses this either.
ABILITY_REFUSAL = (
    "This account is not permitted to {ability} on this ticket. "
    "GET /api/v1/user reports what it holds."
)


def ability_refusal(ability: str, **kwargs: Any):
    """A handler that refuses everything with a `required_ability` 403.

    Note what is NOT here: an identity-endpoint arm. An ability refusal is not
    diagnosable — there is nothing to compare — so a client that made a second
    request on this path would be spending a round trip to learn nothing, and
    this handler would answer that request with another 403. That is deliberate:
    if the diagnostic ever starts firing on ability errors, the request count
    assertions in the write tests catch it.
    """
    return always_json(
        403,
        {
            "error": ABILITY_REFUSAL.format(ability=ability),
            "required_ability": ability,
        },
        **kwargs,
    )


#: The ownership 403, verbatim from
#: Api\V1\TicketWritesController::refuseUnassigned(). The FOURTH flavour of 403
#: on this API and the only one no credential fixes: the key resolved, the role
#: was never asked, and the ticket simply belongs to somebody else.
#:
#: It exists because GET /api/v1/escalations is a SHARED queue — it hands a
#: caller ids from tickets assigned to other agents — so answering "there is no
#: ticket with the id 4" for one of them made the API contradict a payload it
#: had just served. Nothing under test parses this sentence; the branch is on
#: `reason`.
NOT_ASSIGNED_REFUSAL = (
    "Ticket {id} is assigned to another agent and cannot be modified by you. "
    "This API writes only to tickets assigned to your own account; "
    "GET /api/v1/escalations is a SHARED queue and reports each row's "
    "`assignee`. Ask the assignee, or have the ticket reassigned."
)


def not_assigned_refusal(ticket_id: int = 4, **kwargs: Any):
    """A handler that refuses everything with the ownership 403.

    No identity-endpoint arm, for the same reason `ability_refusal` has none and
    with one more reason on top: there is nothing to diagnose here at all. A
    second request would spend a round trip to learn something that could not
    change the answer, and the request-count assertions in the write tests catch
    it if one ever appears.
    """
    return always_json(
        403,
        {
            "error": NOT_ASSIGNED_REFUSAL.format(id=ticket_id),
            "reason": "ticket_not_assigned",
        },
        **kwargs,
    )


#: One ticket, in the shape TicketResource emits — the SAME shape the write
#: endpoints AND /escalations return, which is the contract worth pinning: a
#: create or a queue row that answered with a differently-shaped ticket than the
#: list endpoint would make every client carry two parsers.
#:
#: `escalated` and `escalated_at` are BOTH here because both ship, and they are
#: not interchangeable: `escalated` is the state, `escalated_at` is "since when"
#: and is permanently null on tickets escalated before that column existed.
def ticket_payload(**overrides: Any) -> dict[str, Any]:
    ticket = {
        "id": 42,
        "subject": "Printer on fire",
        "status": {"id": 1, "name": "new"},
        "priority": {"id": 2, "name": "normal"},
        "category": {"slug": "bp-task", "name": "BP Task"},
        "requester": {"id": 7, "name": "Ada", "email": "ada@example.com"},
        "assignee": {"id": 1, "name": "Admin", "email": "admin@ebteq.desk"},
        "escalated": False,
        "escalated_at": None,
        "created_at": "2026-08-12T05:09:30+00:00",
        "updated_at": "2026-08-12T05:09:30+00:00",
    }
    ticket.update(overrides)

    return {"data": ticket}


def ticket_row(**overrides: Any) -> dict[str, Any]:
    """One ticket as it appears INSIDE a list's `data` array."""
    return ticket_payload(**overrides)["data"]


def ticket_list(rows: list[dict[str, Any]], *, per_page: int = 20, **meta: Any):
    """A paginated ticket list, in the envelope all three ticket lists share.

    `per_page` defaults to 20 because that is both the server's default and its
    ceiling — the old fixed 25 is gone, and a fixture still saying 25 would let
    a test claiming to pin the cap pass against the wrong number.
    """
    return {
        "data": rows,
        "links": {"first": "…", "last": "…", "prev": None, "next": None},
        "meta": {
            "current_page": 1,
            "per_page": per_page,
            "last_page": 1,
            "total": len(rows),
            **meta,
        },
    }


#: The server's 422 for a `per_page` above the ceiling, copied from
#: PagesTicketLists::rejectBadPerPage(). Hand-built there rather than thrown, so
#: a curl with no Accept header gets JSON instead of a 302 to nowhere.
def per_page_refusal(maximum: int = 20):
    return always_json(
        422,
        {
            "error": "The given data was invalid.",
            "errors": {
                "per_page": [f"The per page may not be greater than {maximum}."]
            },
        },
    )


# --------------------------------------------------------------------------- #
# The ticket DETAIL surface
# --------------------------------------------------------------------------- #


def thread_entry(
    kind: str = "comment",
    body: str = "A reply.",
    *,
    attachments: list[dict[str, Any]] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """One conversation entry, in the shape TicketThreadResource emits.

    Every key is always present, including the two an EVENT has nothing to put
    in — `body_html` is null and `attachments` is empty rather than absent, so a
    client relies on the shape instead of probing it per `kind`. The fixture
    mirrors that rather than omitting them, or a test would pass against a
    client that only handled the comment case.

    ⚠️ snake_case, and `attachments[].mime_type` with it. The server's Inertia
    surfaces emit `mimeType`; the /api/v1 boundary renames it. A fixture that
    kept the camelCase key would let a client that reads the wrong one pass.
    """
    entry = {
        "kind": kind,
        "body": body,
        "body_html": None if kind == "event" else f"<p>{body}</p>",
        "author": "Bo",
        "attachments": attachments or [],
        "created_at": "2026-08-12T05:09:30+00:00",
        "created_at_human": "2 hours ago",
    }
    entry.update(overrides)

    return entry


def attachment_payload(**overrides: Any) -> dict[str, Any]:
    """One attachment, as /api/v1 speaks it — snake_case, API-route URL."""
    payload = {
        "id": 12,
        "name": "screenshot.png",
        "mime_type": "image/png",
        "size": 28667,
        "url": "https://ebteqdesk.test/api/v1/attachments/12",
    }
    payload.update(overrides)

    return payload


def ticket_detail(
    conversation: list[dict[str, Any]] | None = None,
    *,
    truncated: bool = False,
    **overrides: Any,
) -> dict[str, Any]:
    """GET /api/v1/tickets/{id} — the list row PLUS the detail fields.

    Built ON TOP of `ticket_payload()` deliberately: the server's
    TicketDetailResource SUBCLASSES TicketResource, so a header field that
    appears on the list appears here too. A hand-written second dict would let
    the two drift in the fixtures exactly the way subclassing stops them
    drifting in the app.

    🔴 `conversation_truncated` is TOP-LEVEL, beside `data`, and present ONLY
    when the thread was actually cut. Its position is the thing most likely to
    be got wrong by a client, so the fixture puts it where the server does.
    """
    detail = ticket_payload()["data"]
    detail.update(
        {
            "body": "The printer is emitting smoke.",
            "body_html": "The printer is emitting smoke.",
            "attachments": [],
            "reference_number": "INV-4471",
            "summary": None,
            "team": {"id": 3, "name": "Support"},
            "escalated_minutes": None,
            "conversation": conversation if conversation is not None else [],
        }
    )
    detail.update(overrides)

    body: dict[str, Any] = {"data": detail}

    if truncated:
        body["conversation_truncated"] = True

    return body


#: A real 4x3 PNG. Tiny, but genuinely decodable, so a test asserting "the bytes
#: survived the round trip" is asserting something an image viewer would agree
#: with rather than comparing two opaque blobs.
PNG_4X3 = __import__("base64").b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAIAAAA7ljmRAAAAFElEQVR4nGP8//8/AzZgYsAlM"
    "cIkAJ7cAxKM7hHqAAAAAElFTkSuQmCC"
)


def image_response(
    content: bytes = PNG_4X3,
    *,
    content_type: str = "image/png",
    width: str | None = "4",
    height: str | None = "3",
    source_width: str | None = "12",
    source_height: str | None = "9",
    source_bytes: str | None = "9999",
    downscaled: str | None = "1",
):
    """A handler answering with image bytes and the endpoint's X-Image-* headers.

    The headers are strings because that is what comes off the wire; the client
    is responsible for parsing them, and passing ints here would test a
    conversion that never happens in production.

    🔴 `downscaled` IS ITS OWN PARAMETER AND IS NOT DERIVED FROM THE OTHERS.
    That mirrors the server, which states the flag outright rather than leaving
    it to be inferred, and it is what lets a test construct the case that
    matters: an image whose PIXELS shrank while its BYTES GREW. Re-encoding a
    flat-colour screenshot at a different compression level does exactly that,
    and a client deriving the flag from `len(body) < source_bytes` reads it as
    untouched — the opposite of the truth, and the reason
    `downscaled_screenshot_response()` below exists.
    """
    headers = {"Content-Type": content_type}

    for name, value in (
        ("X-Image-Width", width),
        ("X-Image-Height", height),
        ("X-Image-Source-Width", source_width),
        ("X-Image-Source-Height", source_height),
        ("X-Image-Source-Bytes", source_bytes),
        ("X-Image-Downscaled", downscaled),
    ):
        if value is not None:
            headers[name] = value

    return lambda _request: httpx2.Response(200, content=content, headers=headers)


def downscaled_screenshot_response():
    """The real-world case a byte comparison gets BACKWARDS.

    Measured on the stack: a 2400×1800 screenshot stored at 23,960 bytes came
    back 1568×1176 and 55,577 bytes — a third of the pixels and more than twice
    the file, because resampling hard UI edges to a non-integer ratio destroys
    the flat-colour runs PNG was compressing.

    Modelled here with the numbers inverted around the tiny fixture body:
    `source_bytes` is 40 while the body is 78, so `len(body) < source_bytes` is
    FALSE and any client inferring the flag from bytes answers "not downscaled"
    for an image that plainly was. The server's header says `1`, and that is
    what a client must read.
    """
    return image_response(
        width="1568",
        height="1176",
        source_width="2400",
        source_height="1800",
        source_bytes="40",
        downscaled="1",
    )


# --------------------------------------------------------------------------- #
# The knowledge base WRITE surface
# --------------------------------------------------------------------------- #


def kb_article_payload(**review: Any) -> dict[str, Any]:
    """The written shape all three KB write-surface endpoints return.

    `reference` is `id:<n>` and `slug` is null, which is the NORMAL case for
    anything this API creates — a slug is frozen at first publish and this
    surface cannot publish. A fixture with a slug would quietly test the rare
    case.
    """
    block = {
        "state": "pending",
        "requested_at": "2026-08-13T09:14:22+00:00",
        "reviewed_at": None,
        "reviewed_by": None,
        "note": None,
    }
    block.update(review)

    return {
        "data": {
            "id": 42,
            "reference": "id:42",
            "slug": None,
            "title": "Resetting your VPN password",
            "category": None,
            "folder": {"slug": "vpn", "name": "VPN"},
            "tags": ["vpn"],
            "published_at": None,
            "updated_at": "2026-08-13T09:14:22+00:00",
            "seo": {"title": None, "description": None},
            "body_html": "<p>Hold the power button.</p>",
            "status": "draft",
            "source": "api",
            # 🔴 THE ARTICLE'S LANGUAGE VERSIONS, and `[]` is the fixture's
            # default because it is the ordinary case: no language has its own
            # version, so `title`/`body_html` above ARE what every reader gets.
            # An article WITH versions is a different fixture —
            # kb_article_payload_translated() — because on one of those the two
            # fields above are precisely NOT what a reader gets, and a fixture
            # that blurred the two would let a test pass on the wrong one.
            "translations": [],
            "review": block,
        }
    }


def kb_article_translated(locale: str = "zhcn", **overrides: Any) -> dict[str, Any]:
    """The written shape for an article that HAS a language version.

    The base `title`/`body_html` stay ENGLISH here on purpose. That is what the
    server really returns for a Chinese version written onto an article whose
    base columns hold English — the mirror runs for `en` and only for `en` — and
    it is the exact shape in which a client that reads `data.title` to confirm
    its Chinese edit gets a confident, wrong answer.
    """
    payload = kb_article_payload()

    version = {
        "locale": locale,
        "title": "重置 VPN 密码",
        "body_html": "<p>打开门户并选择“忘记密码”。</p>",
        "seo": {"title": None, "description": None},
    }
    version.update(overrides)

    payload["data"]["translations"] = [version]

    return payload


def kb_proposal_row(**overrides: Any) -> dict[str, Any]:
    """One row of GET /api/v1/kb/proposals — KbArticleResource::proposal().

    🔴 THERE IS NO `body_html` AND NO `seo` HERE, and that absence is the
    fixture's job. This shape is `kb_article_payload()`'s MINUS the body, PLUS
    `excerpt`; a fixture that carried a body would let a test claiming to pin
    the narrower row pass against the wider one.
    """
    row = {
        "id": 42,
        "reference": "id:42",
        "slug": None,
        "title": "Resetting your VPN password",
        "url": None,
        "category": {"slug": "getting-started", "name": "Getting Started"},
        "folder": {"slug": "agent-runbooks", "name": "Agent runbooks"},
        "tags": ["vpn"],
        "published_at": None,
        "updated_at": "2026-08-24T16:31:02+00:00",
        "excerpt": "Open the portal and choose Forgot password.",
        "status": "draft",
        "source": "api",
        "review": {
            "state": "rejected",
            "requested_at": "2026-08-24T09:00:00+00:00",
            "reviewed_at": "2026-08-24T11:12:00+00:00",
            "reviewed_by": {"id": 3, "name": "Dana Reyes"},
            "note": "Name the exact error message the user sees.",
        },
    }
    row.update(overrides)

    return row


def kb_proposal_list(
    rows: list[dict[str, Any]] | None = None, *, per_page: int = 25, **meta: Any
) -> dict[str, Any]:
    """The proposal list envelope.

    `per_page` defaults to 25 because that is the SERVER's default here — the KB
    lists page 25/100 and the ticket lists 20/20, and a fixture borrowing the
    ticket number would let a test claiming to pin this default pass against the
    wrong one.
    """
    rows = [kb_proposal_row()] if rows is None else rows

    return {
        "data": rows,
        "links": {"first": "…", "last": "…", "prev": None, "next": None},
        "meta": {
            "current_page": 1,
            "per_page": per_page,
            "last_page": 1,
            "total": len(rows),
            **meta,
        },
    }


#: The server's 422 for an illegal `?review_state=`, copied from
#: Api\V1\KbProposalsController::rejectQuery(). `none` lands here rather than
#: answering an empty list: it is the entire hand-written corpus, and an empty
#: list would read as "you have no unreviewed articles".
def kb_review_state_refusal():
    return always_json(
        422,
        {
            "error": "The request query is not valid.",
            "errors": {
                "review_state": ["The selected review state is invalid."]
            },
        },
    )


# --------------------------------------------------------------------------- #
# The knowledge base STRUCTURE surface
# --------------------------------------------------------------------------- #


def kb_folder_row(**overrides: Any) -> dict[str, Any]:
    """One folder, in the shape KbFolderResource emits.

    🔴 `visibility` DEFAULTS TO "agents" HERE BECAUSE IT DOES ON THE SERVER, and
    that is the fixture's most load-bearing line. Every folder created through
    /api/v1 gets the column default — the endpoints accept no `visibility` key
    at all — so a fixture that defaulted to "public" would let a test claiming to
    prove "an integration cannot publish content outside the desk" pass against
    a client that had grown a way to.

    `articles_count` is on every row because the server always counts. It
    includes DRAFTS, so it is not "how many people can read it".
    """
    folder = {
        "id": 7,
        "kb_category_id": 3,
        "name": "Errors",
        "slug": "errors",
        "description": None,
        "visibility": "agents",
        "position": 0,
        "articles_count": 12,
    }
    folder.update(overrides)

    return folder


def kb_category_row(
    folders: list[dict[str, Any]] | None = None, **overrides: Any
) -> dict[str, Any]:
    """One category, in the shape KbCategoryResource emits.

    `folders` is ALWAYS present and is `[]` for a category with none — the
    server never omits the key, so a client has no branch and neither does this.
    """
    category = {
        "id": 3,
        "name": "POS",
        "slug": "pos",
        "description": None,
        "position": 0,
        "folders": folders if folders is not None else [kb_folder_row()],
    }
    category.update(overrides)

    return category


def kb_tree_payload(categories: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """GET /api/v1/kb/tree — `{"data": [...]}`, no paging envelope.

    There is no `links`/`meta` block here and that is not an omission: the tree
    is a few hundred rows of structure and the endpoint does not page. A fixture
    that added one would let a client grow paging code for an endpoint that has
    none.
    """
    return {"data": categories if categories is not None else [kb_category_row()]}


def kb_slug_collision(field: str = "name", *, entity: str = "category"):
    """The server's 422 for a name whose DERIVED slug is already taken.

    Verbatim from Api\\V1\\KbCategoryWritesController / KbFolderWritesController.
    The failure lands on `name` — not on `slug` — because `name` is the field
    the caller actually sent and the only one it can change.
    """
    message = (
        "A category with that name already exists."
        if entity == "category"
        else "A folder with that name already exists in this category."
    )

    return always_json(
        422,
        {"error": "The request body is not valid.", "errors": {field: [message]}},
    )


def kb_children_refusal(entity: str = "category", count: int = 2):
    """The server's 422 for deleting a row that still holds children.

    Verbatim from App\\Kb\\CategoryWriter::delete() / FolderWriter::delete(),
    rendered by the API controllers in the same envelope every other KB write
    refusal uses. 🔴 IT IS A REFUSAL AND NOT A CASCADE: nothing is deleted, the
    row survives, and the count is in the message so the caller can report what
    is in the way. The field is `category` / `folder` — the level being deleted,
    not the children — because that is where the web form renders it.
    """
    child = "folder" if entity == "category" else "article"
    plural = child if count == 1 else child + "s"

    return always_json(
        422,
        {
            "error": "The request body is not valid.",
            "errors": {
                entity: [
                    f"This {entity} still holds {count} {plural}. "
                    "Move or delete them first."
                ]
            },
        },
    )


def kb_structure_not_found(entity: str = "category"):
    """The 404 for an id that names nothing. Constant body, does not echo the id."""
    return always_json(
        404,
        {"error": f"There is no Knowledge Base {entity} with that id."},
    )


#: The 202 an edit to a PUBLISHED article gets: the edit was STAGED as a pending
#: revision and the live article was NOT touched.
#:
#: 🔴 `data` HERE IS THE LIVE ARTICLE AND NOT THE SUBMISSION, which is the whole
#: reason this fixture exists rather than being spelled inline. It carries
#: `status: "published"`, the OLD title and body, `translations: []`, and
#: `review.state: "none"` — the article was approved and published long ago and
#: staging a revision does not disturb that record. A fixture that echoed the
#: request would let every "the caller must not read `data` back" test pass
#: against a payload that never had the problem.
#:
#: Captured from a real 202 against a local stack on 2026-08-28.
def staged_revision(**revision: Any):
    block = {
        "state": "pending",
        "source": "api",
        "requested_at": "2026-08-28T03:46:37+00:00",
        "reviewed_at": None,
        "reviewed_by": None,
        "note": None,
    }
    block.update(revision)

    live = kb_article_payload(state="none", requested_at=None)["data"]
    live.update(
        {
            "slug": "resetting-your-password",
            "status": "published",
            "source": "manual",
            "published_at": "2026-08-13T09:14:22+00:00",
            "url": "https://ebteqdesk.test/support/kb/articles/resetting-your-password",
        }
    )

    return always_json(202, {"data": live, "revision": block})


#: The same article read back through {reference}/review, where `revision` is
#: emitted UNCONDITIONALLY — null when nothing is staged. That asymmetry with
#: the write path (which omits the key entirely on a 200) is the discriminator
#: `update_kb_article` tells its callers to branch on, so both halves of it are
#: fixtures rather than assumptions.
def article_review(revision: dict | None = None, **review: Any):
    payload = kb_article_payload(**review)
    payload["revision"] = revision

    return always_json(200, payload)


def identity_payload(
    requested: list[str] | None = None,
    scopes: list[str] | None = None,
    *,
    api_key: Any = ...,
) -> dict[str, Any]:
    """A GET /api/v1/user body, shaped like the real one.

    `api_key=None` models a request Sanctum authenticated without a bearer
    token, which the server documents as possible-in-principle and which must
    leave a scope refusal undiagnosed rather than crash.
    """
    block: Any
    if api_key is ...:
        block = {
            "id": 29,
            "name": "py-test",
            "scopes": scopes if scopes is not None else [],
            "requested": requested if requested is not None else [],
            "expiresAt": None,
        }
    else:
        block = api_key

    return {
        "data": {
            "id": 1,
            "uuid": "d56d75a3-0020-4706-ac57-0af77be8c89c",
            "name": "Admin",
            "email": "admin@ebteq.desk",
            "role": {"id": 1, "name": "Administrator", "key": "administrator"},
            "permissions": ["ticket.view"],
            "apiKey": block,
        }
    }


def scope_refusal(
    scope: str,
    *,
    requested: list[str] | None = None,
    scopes: list[str] | None = None,
    identity: Any = ...,
    identity_status: int = 200,
):
    """A handler that refuses everything for lack of `scope`, except the
    identity endpoint, which answers so the client's diagnostic can run.

    This shape is the point: the diagnosis costs a SECOND request to a DIFFERENT
    path, so a handler that answered uniformly could not tell a working
    diagnostic from a broken one.
    """
    from ebteqdesk_mcp.client import USER_PATH

    body = (
        identity_payload(requested, scopes)
        if identity is ...
        else identity
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == USER_PATH:
            return json_response(identity_status, body)

        return json_response(
            403,
            {
                "error": SCOPE_REFUSAL.format(scope=scope),
                "required_scope": scope,
            },
        )

    return handler
