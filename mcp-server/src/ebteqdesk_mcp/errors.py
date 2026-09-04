"""The failure modes, one exception each, every message actionable.

Why this module is as long as it is: these strings ARE the product surface. An
MCP tool failure reaches the user as one line of text inside a chat, with no
stack trace, no server log and no HTTP status code visible. "403 Forbidden" ends
the conversation; "this key was not minted with `kb:read`, mint a new one that
includes it" does not. So every exception here answers three questions — what
happened, why, and what to do next — and none of them ever contains the token.

The corollary, learned the hard way on the 403 below: an exception must not
answer the third question when it does not actually know. Confidently wrong
advice is more expensive than "here is how to find out".

Hierarchy:

    EbteqdeskError
    +- ConfigurationError        environment is wrong; no request was made
    +- LocalFileError            a LOCAL path could not be read; no request was made
    +- TransportError            host unreachable, DNS failure, TLS error, timeout
    +- MalformedResponseError    a reply that is not the JSON this API promises
    +- ApiError                  the server answered, with an error status
       +- AuthenticationError    401  token missing/expired/revoked
       +- ScopeError             403  a scope did not resolve (carries required_scope)
       |  +- KeyScopeError       403  ...because the KEY was not minted with it
       |  +- RoleScopeError      403  ...because the OWNER'S ROLE does not back it
       +- AbilityError           403  the ACCOUNT lacks an ability (required_ability)
       +- TicketNotAssignedError 403  the ticket is real, readable on the shared
       |                              escalation queue, and somebody else's
       +- PermissionError_       403  refused with no scope and no ability named
       +- NotFoundError          404
       +- ConflictError          409  a state conflict. NO ENDPOINT PRODUCES ONE
       |                              today — see the class
       +- PayloadTooLargeError   413  the body is over a ceiling (two endpoints, two causes)
       +- UnsupportedMediaError  415  the attachment is not an image (carries mime_type)
       +- InvalidRequestError    422  parameter validation, carries field errors
       +- RateLimitedError       429
       +- ServerError            5xx

🔴 A 403 CARRYING `required_scope` DOES NOT SAY WHICH HALF FAILED.
A scope resolves only while BOTH the key carries it and the owner's role backs
it, and the server refuses byte-identically either way on purpose — telling the
holder of a stolen key which wall they hit is a probe into the owner's role. So
`api_error_for` builds a plain `ScopeError` carrying the server's sentence
verbatim and infers NOTHING. `EbteqdeskClient` then spends one extra request on
GET /api/v1/user and calls `diagnosed_scope_error` to narrow it, which is the
only supported way to tell the two apart. The remedies are opposite — mint a new
key, versus ask an administrator — so guessing is worse than not knowing.

🔴 A 403 CARRYING `reason: "ticket_not_assigned"` IS NOT A PERMISSION PROBLEM AT
ALL, AND IT IS THE ONE 403 NO CREDENTIAL FIXES. The key resolved, the role holds
everything the endpoint asked for, and the request was refused because the
TICKET belongs to somebody else. Neither `required_scope` nor `required_ability`
is present, because neither gate was reached. There is nothing to mint and
nothing for an administrator to grant — a human has to reassign the ticket, or
its assignee has to act on it. The write surface of /api/v1 is ownership-scoped
in a way no permission overrides, `admin.access` included.

It appears at all only because `list_escalations` is NOT ownership-scoped: it
hands a caller ids from a SHARED queue, and answering "there is no ticket with
the id 4" for a row this same API just served is the API contradicting itself.
Every other unreachable ticket is still a plain 404. See
Api\\V1\\TicketWritesController.

🔴 A 403 CARRYING `required_ability` IS A DIFFERENT ANIMAL, AND CONFLATING THE
TWO SENDS PEOPLE TO THE WRONG PLACE. `required_scope` is about the CREDENTIAL —
one of its two halves failed, and one of the two remedies is "mint a new key".
`required_ability` is about the ACCOUNT: the key's scope resolved fine, the
request got past the scope gate, and the person behind it does not hold the
ability the endpoint then asked for. There is no key half to it, so no key
fixes it and there is nothing to diagnose with a second request — the account's
own permission list is already served in full by GET /api/v1/user, which is
also why the server is willing to NAME this ability while it refuses to say
which half of a scope failed. One remedy, and only one: ask an administrator.

🔴 EVERY DIAGNOSED 403 COSTS TWO REQUESTS, AND THE THROTTLE COUNTS BOTH (N7).

All three diagnostics — `diagnosed_scope_error` for a scope refusal,
`diagnosed_ability_error` for an ability refusal, and `ticket_write_not_found`
for a 404 from a ticket write — are driven by ONE extra GET /api/v1/user, spent
on the error path only. A successful call never pays it, and at most one runs
per refusal.

The account rate limit is 60 requests per minute and it counts the diagnostic
call like any other. So:

  - a refused call is TWO requests, not one;
  - a client looping over ids it may not touch reaches the limit in HALF the
    calls it expects, and the symptom is a 429 that looks unrelated to the 403s
    that caused it;
  - a 429 raised BY the diagnostic is swallowed rather than surfaced — the
    caller gets the original 403 — so the throttle is spent silently.

This is documented rather than changed. The diagnosis is worth its request: the
alternative is a refusal the caller cannot act on, and the account most likely to
hit one has no browser to fall back on. But a client that expects to be refused
often should either cache the identity payload itself or stop probing.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

__all__ = [
    "EbteqdeskError",
    "ConfigurationError",
    "LocalFileError",
    "TransportError",
    "MalformedResponseError",
    "ApiError",
    "AuthenticationError",
    "ScopeError",
    "KeyScopeError",
    "RoleScopeError",
    "AbilityError",
    "TicketNotAssignedError",
    "PermissionError_",
    "NotFoundError",
    "ConflictError",
    "PayloadTooLargeError",
    "UnsupportedMediaError",
    "InvalidRequestError",
    "RateLimitedError",
    "ServerError",
    "api_error_for",
    "diagnosed_scope_error",
    "escalated_comment_error",
    "escalated_note_error",
    "ESCALATION_WRITE_SCOPE",
    "MEDIA_UPLOAD_PATH",
    "NOT_ASSIGNED_REASON",
    "ESCALATION_REPLY_SCOPE",
    "TICKET_WRITE_SCOPE",
    "ticket_write_not_found",
    "RoleAbilityError",
    "TicketAbilityError",
    "diagnosed_ability_error",
]

#: The scope the NOTE endpoint asks for INSTEAD of `ticket:write`, and only when
#: the ticket turns out to be escalated. Named here because
#: `escalated_note_error` branches on it as a VALUE — see that function for why
#: matching this one string is sound and is not prose parsing.
ESCALATION_WRITE_SCOPE = "escalation:write"

#: The scope the REPLY endpoints ask for on an escalated ticket — the public,
#: requester-facing half that was split out of `escalation:write`.
#:
#: 🔴 THE TWO ARE NOT INTERCHANGEABLE AND THE DIFFERENCE IS THE BLAST RADIUS.
#: `escalation:write` files an INTERNAL note (and de-escalates); this one sends
#: a message the REQUESTER receives by email. An ordinary support account holds
#: the first and not the second, because escalation hands the requester
#: relationship to whoever is working the escalation. That the requester is a
#: colleague on an internal desk does not soften it — the mail still leaves.
#:
#: Two endpoints charge it: POST /tickets/{id}/comments, and POST
#: /tickets/{id}/close when the call carries a `body` — because that body IS a
#: requester-facing reply and goes out by email exactly as a comment does.
ESCALATION_REPLY_SCOPE = "escalation:reply"

#: The scope a caller needs to act on an ORDINARY ticket of its own. Named here
#: because `ticket_write_not_found` branches on its ABSENCE — a key without it
#: reaches escalated tickets only, which is what makes "this may be your own
#: ticket" a live explanation for an otherwise bare 404.
TICKET_WRITE_SCOPE = "ticket:write"

#: The `reason` a ticket write carries when the ticket exists, is on the shared
#: escalation queue, and is not the caller's. Verbatim from
#: `Api\V1\TicketWritesController::REASON_NOT_ASSIGNED`.
#:
#: A structured field on purpose, and the branch below compares it as a VALUE.
#: The alternative — matching "assigned to another agent" in `error` — is prose
#: parsing, which this module refuses everywhere else for the reason it has
#: already been burned by: server sentences get rewritten and the client keeps
#: "working" while going quietly wrong.
NOT_ASSIGNED_REASON = "ticket_not_assigned"

#: The one upload path on this API. Named because the 413 branch below is the
#: only place in this module that has to tell TWO endpoints apart by their path
#: rather than by a field in the body — see that branch for why the alternative
#: (one message covering both) would give confidently wrong advice to one of
#: them. Kept as a VALUE next to the client method that sends it, not a regex.
MEDIA_UPLOAD_PATH = "/api/v1/kb/media"


class EbteqdeskError(Exception):
    """Base class for everything this package raises deliberately."""


class ConfigurationError(EbteqdeskError):
    """The environment is not usable. Raised before any request is attempted."""


class LocalFileError(EbteqdeskError):
    """A path on the USER'S OWN MACHINE could not be read. Nothing was sent.

    🔴 THE FIRST FAILURE MODE IN THIS PACKAGE THAT IS NOT ABOUT EBTEQDESK AT
    ALL. Every other error here describes the environment, the network or the
    server; this one describes the caller's filesystem, and it exists because
    `upload_kb_media` is the first tool that reads a local file.

    It is deliberately NOT a ConfigurationError. That class means "the
    environment is wrong" and its remedies are environment-shaped — set
    EBTEQDESK_API_TOKEN, restart the server. The remedy here is to name a
    different path, which is a conversation with the user and not a
    configuration change, and a model that read this as a configuration problem
    would tell somebody to check their token because a screenshot was in
    Downloads and not on the Desktop.

    The path IS interpolated into the message, unlike the token, which never is.
    The user supplied it; showing it back is the only way they can see the typo.
    """

    def __init__(self, file_path: str, reason: str) -> None:
        self.file_path = file_path
        self.reason = reason

        super().__init__(
            f"Cannot upload {file_path!r}: {reason}. NOTHING WAS SENT TO "
            f"EBTEQDESK — this failed on this machine, before any request. Ask "
            f"the user for the correct path rather than guessing at one, and do "
            f"not go looking through nearby directories for something that "
            f"resembles it."
        )


class TransportError(EbteqdeskError):
    """The request never got an HTTP reply: DNS, connection, TLS or timeout.

    The underlying httpx exception is kept on `cause_type` rather than
    interpolated whole, because httpx messages sometimes contain the full
    request URL, and while this client never puts the token in a URL, keeping
    credentials out of messages by construction beats keeping them out by
    remembering to.
    """

    def __init__(self, base_url: str, cause: BaseException, timeout: float) -> None:
        self.base_url = base_url
        self.cause_type = type(cause).__name__
        self.timeout = timeout

        super().__init__(
            f"Could not reach Ebteqdesk at {base_url} ({self.cause_type}). "
            f"Check EBTEQDESK_BASE_URL, that the server is running, and that this "
            f"machine can reach it. The per-request timeout is {timeout:g}s; raise "
            f"it with EBTEQDESK_TIMEOUT if the server is merely slow."
        )


class MalformedResponseError(EbteqdeskError):
    """The server replied, but not with the JSON envelope the API documents.

    In practice this is a proxy, a load balancer or a maintenance page answering
    instead of the application: an HTML 502 from nginx, a captive portal, or a
    Laravel error page leaking through because the request never reached the
    `api` middleware group. Showing the first line of what did arrive is what
    lets the user recognise which of those it is.
    """

    def __init__(
        self,
        status_code: int,
        path: str,
        content_type: str | None,
        body_snippet: str,
    ) -> None:
        self.status_code = status_code
        self.path = path
        self.content_type = content_type
        self.body_snippet = body_snippet

        super().__init__(
            f"Ebteqdesk answered {status_code} for {path} with "
            f"{content_type or 'no content type'} instead of JSON. This usually "
            f"means a proxy or error page answered instead of the application. "
            f"First bytes: {body_snippet!r}"
        )


class ApiError(EbteqdeskError):
    """A JSON error response from /api/v1.

    Attributes mirror the documented error envelope:

        401 {"error": "Unauthenticated."}
        403 {"error": "...", "required_scope": "kb:read"}
        403 {"error": "...", "required_ability": "ticket.close"}
        404 {"error": "..."}
        422 {"error": "...", "errors": {"per_page": ["..."]}}

    `required_scope` and `required_ability` are BOTH carried on the base class,
    and both default to None. The server never sends the two together — they
    come from different refusal points — but a client that had to know which
    subclass it held before it could read a field would make every `except
    ApiError` block start with an isinstance check.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        path: str,
        server_message: str | None = None,
        required_scope: str | None = None,
        required_ability: str | None = None,
        field_errors: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self.status_code = status_code
        self.path = path
        self.server_message = server_message
        self.required_scope = required_scope
        self.required_ability = required_ability
        self.field_errors: dict[str, list[str]] = {
            field: list(messages) for field, messages in (field_errors or {}).items()
        }

        super().__init__(message)


class AuthenticationError(ApiError):
    """401 — the token is absent, malformed, expired or revoked."""


class ScopeError(ApiError):
    """403 carrying `required_scope` — a scope did not resolve.

    Raised bare when the cause could not be established. `diagnosis` is then
    None and the message quotes the server verbatim, because the two possible
    causes need OPPOSITE fixes and a confident wrong answer is worse than an
    honest "here is how to check".

    `EbteqdeskClient` narrows this to `KeyScopeError` or `RoleScopeError` where
    it can. See `diagnosed_scope_error`.
    """

    #: "key", "role", or None when the cause was not established.
    diagnosis: str | None = None


class KeyScopeError(ScopeError):
    """403 — the key was not minted with the scope. Mint a new key.

    Established from GET /api/v1/user: the scope is absent from
    `apiKey.requested`, which is what the key itself carries. Nothing about the
    owner's role is claimed, because `requested` cannot say — this is what is
    known, not everything that might be true.
    """

    diagnosis = "key"


class RoleScopeError(ScopeError):
    """403 — the key carries the scope but the owner's role does not back it.

    Established from GET /api/v1/user: the scope IS in `apiKey.requested` and is
    NOT in `apiKey.scopes`, and `scopes` is the key-∩-role intersection. Minting
    a new key cannot fix this and would fail identically.
    """

    diagnosis = "role"


class AbilityError(ApiError):
    """403 carrying `required_ability` — the ACCOUNT was refused, not the key.

    Raised only by the write endpoints, which are the only ones that check an
    ability of their own after the scope gate has already let the request
    through. `required_ability` is a role-permission key such as
    `ticket.create`, `ticket.reply`, `ticket.close` or `bp_escalation.reply`.

    🔴 NOT A SCOPE PROBLEM, AND THE DISTINCTION IS THE WHOLE REASON THIS CLASS
    EXISTS. Reaching an ability check means the scope ALREADY resolved: the key
    carried it and the owner's role backed it. What failed is a second,
    narrower question the endpoint asked about the account — and unlike a
    scope, an ability has no key half at all. So:

      - The key half is answered already. The two-list comparison in
        `diagnosed_scope_error` answers "key or role?", and here the answer is
        already "role", known from the shape of the body rather than inferred.
      - Minting a new key CANNOT fix it, with any scopes, ever. A user who is
        told "check your scopes" here will mint key after key and hit the same
        wall — which is exactly the failure `RoleScopeError` was written to
        stop, in a second place.

    🔴 BUT "ASK AN ADMINISTRATOR" IS NOT ALWAYS THE REMEDY, AND SAYING SO
    UNCONDITIONALLY WAS ITS OWN VERSION OF THE SAME FAILURE (N1). Two very
    different situations both land here:

      - THE ROLE DOES NOT HOLD THE ABILITY. Sometimes a grant fixes that.
        Sometimes it is what the role IS: a specialist account may hold no
        `ticket.close` or `ticket.review` by design, so telling its owner to go
        and obtain one sends them to an administrator who should say no. See
        `RoleAbilityError`.
      - THE ROLE HOLDS IT AND THIS TICKET WAS REFUSED. The per-ticket policy
        declined — not yours, not your team's, not an escalation you may review
        — and no grant changes anything, because the account already has the
        ability. See `TicketAbilityError`.

    `EbteqdeskClient` separates the two with the SAME one extra request the
    scope path already spends, comparing `required_ability` against
    `permissions` from GET /api/v1/user. Nothing new is disclosed: that list is
    the caller's own, already served in full to any valid key.

    The server NAMES the ability, where it refuses to name which half of a
    scope failed, and that asymmetry is deliberate on its side too: the
    account's own permission list is already served in full by
    GET /api/v1/user, so naming it discloses nothing the caller cannot read.
    """

    #: The role ability the endpoint demanded, e.g. "ticket.close".
    #: Also on the base class; restated here because this is where it is
    #: guaranteed non-None.
    required_ability: str


class RoleAbilityError(AbilityError):
    """403 — the account's ROLE does not hold the required ability at all (N1).

    Established from GET /api/v1/user: `required_ability` is absent from
    `permissions`, the role's full ability list.

    ⚠️ A GRANT MAY OR MAY NOT BE THE ANSWER, and this class does not pretend to
    know which. For an ordinary support account missing `ticket.close`, an
    administrator can grant it. For a developer account refused `ticket.reply`,
    the absence IS the role — that account works escalations and does not own
    tickets — and the honest advice is "this is not your account's job", not
    "go and ask". The message says both and lets the reader tell which they are.

    `except AbilityError` still catches this.
    """

    diagnosis = "role-ability"


class TicketAbilityError(AbilityError):
    """403 — the role HOLDS the ability; THIS TICKET was refused (N1).

    Established from GET /api/v1/user: `required_ability` IS in `permissions`.
    So the refusal came from the per-ticket policy rather than from the account:
    the ticket is not yours, not your team's, or not an escalation you may
    review.

    🔴 THE REMEDY IS A DIFFERENT TICKET OR A REASSIGNMENT, NEVER A GRANT. Asking
    an administrator for an ability the account already holds is the wasted trip
    this class exists to prevent — the exact shape of the wasted trip
    `KeyScopeError` and `RoleScopeError` prevent one layer up.

    `except AbilityError` still catches this.
    """

    diagnosis = "ticket"


class TicketNotAssignedError(ApiError):
    """403 — the ticket exists, you can read it, and it is not yours to write to.

    Raised only by the four ticket WRITE endpoints, and only for a ticket that
    is on the SHARED business-partner escalation queue — the one list on this
    API that is not scoped to the caller. Any other unreachable ticket is a
    plain 404 that does not distinguish "not yours" from "no such id", which is
    what keeps the id space unenumerable.

    🔴 NO CREDENTIAL FIXES THIS, AND THAT IS THE WHOLE REASON IT IS ITS OWN
    CLASS. The other three 403s are all about authority — a key that lacks a
    scope, a role that does not back one, a role that lacks an ability — and
    each has a remedy that ends in somebody granting something. This one is
    about OWNERSHIP, which is not a permission on this surface: a ticket is
    writable by its assignee and by nobody else, `admin.access` included. Minting
    a key does nothing. Granting an ability does nothing. Someone reassigns the
    ticket, or its assignee acts on it.

    The two facts a caller needs, and both are in the message:

      - Do not retry. Nothing about this call will succeed later, so an agent
        that loops on it loops forever.
      - The id was not stale. `list_escalations` returned it correctly; it is a
        shared queue and this row belongs to another agent. Read `assignee` on
        the row before choosing what to act on.

    `retriable` is False and is stated as an attribute as well as in the prose,
    so a caller with a retry policy can branch on it without reading English.
    """

    #: Nothing about the request or the credential can change the outcome.
    retriable = False


class PermissionError_(ApiError):
    """403 with no `required_scope` and no `required_ability` at all.

    Distinct from `ScopeError` (no scope was named, so there is no key/role
    intersection to diagnose) and from `AbilityError` (no ability was named
    either, so there is nothing concrete to ask an administrator for). On
    current Ebteqdesk this is the route-level
    backstop (a v1 route that declares no scope, which fails closed) or the
    escalation report's own belt-and-braces ability check — which the scope
    middleware now shadows, since `escalation-reports:read` is backed by exactly
    the ability that check looks for.

    Trailing underscore because `PermissionError` is a Python builtin, and
    shadowing it in a module people do `from ... import *` on is how you get a
    bug report about `except PermissionError` silently not catching an OSError.
    """


class NotFoundError(ApiError):
    """404 — nothing readable at that identifier.

    ⚠️ ON A TICKET WRITE, "NOT FOUND" DOES NOT MEAN "DOES NOT EXIST", AND IT CAN
    BE THE CALLER'S OWN TICKET. The write rule answers one body for every ticket
    it will not act on, deliberately: a row that is not yours, a row that does
    not exist, and — for a key that does not resolve `ticket:write` — a row that
    IS yours all produce the identical 404. Distinguishing them would let any
    key map the id space, and on the escalation branch it would say which ids
    are escalated.

    So a 404 from `comment_on_ticket`, `add_private_note`, `close_ticket`,
    `set_ticket_status` or the escalate pair means "this key cannot act on that
    id", never "that id is free". Do not report a ticket as deleted on the
    strength of one, and do not retry: nothing about it is transient.

    `ticket_write_not_found()` adds that sentence to the message when the
    account's own scopes make the own-ticket case a live possibility.
    """


class ConflictError(ApiError):
    """409 — the write was refused because of the resource's current state.

    🔴 NOTHING ON /api/v1 PRODUCES ONE TODAY, and this class is deliberately
    kept anyway. Read both halves of that before deleting it or before writing
    a message here that names an endpoint.

    ITS ONE PRODUCER IS GONE. PATCH /api/v1/kb/articles/{reference} answered 409
    for an article that had been PUBLISHED, on the reasoning that unpublishing
    on edit would hand an integration a one-request takedown of any live help
    article. That reasoning still stands and unpublishing is still impossible —
    what changed is that the endpoint can now write the submitted text to a
    pending REVISION without touching the live row, so the request is ACCEPTED
    and staged, answering 202. See EbteqdeskClient.update_kb_article, and
    KbArticleWritesController, whose own docblock says the 409 is gone from that
    surface entirely.

    IT IS KEPT FOR TWO REASONS. It is exported from the package root, so
    removing it breaks an `except ConflictError` in anyone using the HTTP half
    directly; and `api_error_for` must still classify a 409 as something, since
    a proxy or a future endpoint can emit one. What it must NOT do is keep
    teaching the removed rule — a message asserting "the article is published"
    would be a confident, wrong diagnosis of whatever the next 409 actually is.

    So: this is now the generic state conflict. The server's own sentence is
    the only thing that knows what it means; do not retry blindly, and read it.
    """


class PayloadTooLargeError(ApiError):
    """413 — a body was over a ceiling. TWO endpoints raise it for OPPOSITE
    reasons, and the message is the only thing that tells them apart.

    DOWNLOAD — GET /api/v1/attachments/{id} downscales before it returns and
    holds the ENCODED body to a hard ceiling; an image that cannot be reduced
    under it, or whose source is too large to decode safely at all, is refused.
    Worth retrying with a SMALLER `max_dimension`, which is the whole reason
    this is a class of its own rather than the generic ApiError fallthrough.

    UPLOAD — POST /api/v1/kb/media, where the REQUEST was too large for nginx's
    `client_max_body_size` or PHP's `post_max_size` and was cut off before the
    application saw it. There is no argument that changes the outcome and no
    field errors to read: the per-kind cap that produces a helpful 422 lives in
    App\\Kb\\MediaRules, one layer further in, and this request never got there.
    The only remedy is a smaller file.

    🔴 So the remedies are OPPOSITE — "retry with a smaller argument" against
    "there is nothing to retry" — which is why `api_error_for` branches on the
    path. One message for both would send an uploader into a retry loop with an
    argument that endpoint does not have.

    ⚠️ On the upload side a 413 may also arrive as HTML from nginx rather than
    JSON from Laravel, in which case it surfaces as MalformedResponseError
    instead. That is nginx answering before PHP runs at all, and the snippet in
    that message says so.
    """


class UnsupportedMediaError(ApiError):
    """415 — the attachment is not an image, so no bytes are returned.

    Ebteqdesk accepts video attachments (`video/mp4`, `video/quicktime`,
    `video/webm`) alongside images, and this endpoint serves images ONLY. A
    video's bytes are never returned under an image content type: a client that
    base64'd an MP4 into a vision request would get a decode failure at best and
    a confidently wrong answer at worst.

    `mime_type` carries what the row actually is, so a caller can tell "this is
    a video" from "this file is broken" without another request. It is None only
    if the server omitted the field.

    NOT retryable in any form. There is no argument to this endpoint that makes
    a video into an image; the file is readable by a human in the browser at its
    ticket, and that is the only remedy.
    """

    def __init__(self, *args: Any, mime_type: str | None = None, **kwargs: Any) -> None:
        #: The attachment's stored mime type, e.g. "video/mp4". None if absent.
        self.mime_type = mime_type
        super().__init__(*args, **kwargs)


class InvalidRequestError(ApiError):
    """422 — a query parameter was rejected. `field_errors` says which."""


class RateLimitedError(ApiError):
    """429 — the /api throttle (60 requests/minute by default) tripped."""

    def __init__(self, *args: Any, retry_after: str | None = None, **kwargs: Any) -> None:
        self.retry_after = retry_after
        super().__init__(*args, **kwargs)


class ServerError(ApiError):
    """5xx — Ebteqdesk itself failed. Nothing the client can do differently."""


def api_error_for(
    *,
    status_code: int,
    path: str,
    payload: Mapping[str, Any],
    retry_after: str | None = None,
) -> ApiError:
    """Turn one JSON error body into the most specific exception available.

    The 403 fork is the interesting one, it is FOUR ways, and each way is
    NARROWER than it looks.

      - `reason` == "ticket_not_assigned" -> not a permission failure at all.
        The credential resolved and the role was never the problem: the TICKET
        belongs to another agent. Tested FIRST because it is the only 403 with
        no remedy, and answering it with any of the three below sends the
        caller to mint a key or find an administrator for something neither can
        fix. The server sends it with neither of the two fields below, so the
        order is unobservable today and fixed anyway.
      - `required_scope` present -> a scope did not resolve. That is ALL this
        says. It does not say whether the key lacks the scope or the owner's
        role lacks the ability behind it, and the server refuses identically
        either way by design. So this returns a bare `ScopeError` and the
        message quotes the server rather than prescribing a fix; the caller
        narrows it with `diagnosed_scope_error` if it can.
      - `required_ability` present -> the scope resolved and the ACCOUNT was
        then refused. A different question with a different, single remedy —
        see `AbilityError`. Nothing here is diagnosable and nothing should be
        diagnosed: no second request, and never the word "mint".
      - neither present -> refused without naming anything. Today that is the
        route-level backstop (a route that declares no scope) and the
        escalation report's own belt-and-braces ability check.

    `required_scope` is tested FIRST. The server emits exactly one of the two
    fields — they come from different refusal points, EnsureApiScope and
    TicketWritesController::refuseAbility() — so the order is unobservable
    today. It is fixed anyway, and in this direction, because the scope gate is
    the OUTER one: a body somehow carrying both would mean the request never
    got past the credential, and reporting the inner ability check would send
    the user to an administrator for a key problem.

    🔴 The branch is on the PRESENCE OF A FIELD, never on the words in `error`.
    Server prose is not an interface: it has already been rewritten once, and a
    client that regex'd it would have broken silently and started giving
    confidently wrong advice.
    """
    server_message = payload.get("error")
    if not isinstance(server_message, str):
        server_message = None

    required_scope = payload.get("required_scope")
    if not isinstance(required_scope, str) or not required_scope:
        required_scope = None

    required_ability = payload.get("required_ability")
    if not isinstance(required_ability, str) or not required_ability:
        required_ability = None

    field_errors = payload.get("errors")
    if not isinstance(field_errors, Mapping):
        field_errors = {}

    detail = f" Ebteqdesk said: {server_message}" if server_message else ""

    if status_code == 401:
        return AuthenticationError(
            "Ebteqdesk rejected the API token (401). It is missing, expired or has "
            "been revoked. Mint a fresh token and update EBTEQDESK_API_TOKEN, then "
            "restart the MCP server so it picks up the new value."
            + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
        )

    if status_code == 403 and payload.get("reason") == NOT_ASSIGNED_REASON:
        return TicketNotAssignedError(
            # "not assigned to you", NOT "belongs to another agent": the same
            # refusal covers an escalation assigned to NOBODY, which is a row a
            # triage caller most wants flagged. The server's own sentence, which
            # does distinguish the two, is carried verbatim below.
            "This ticket is not assigned to this account, so it cannot be "
            "written to (403). THIS IS NOT A KEY, SCOPE OR ABILITY PROBLEM and there "
            "is nothing to fix: the write endpoints of this API act only on "
            "tickets ASSIGNED to the token's own account, and no permission "
            "overrides that — not `admin.access`, not any scope, not any role. "
            "DO NOT RETRY; the answer will be the same every time. "
            "The id is not stale either: `list_escalations` is a SHARED queue "
            "and returns every escalated ticket in the installation, whoever it "
            "is assigned to, so it correctly handed you an id you cannot act on. "
            "Read `assignee` on the row to see whose it is. To move it forward, "
            "a human reassigns the ticket or its assignee acts on it."
            + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
        )

    if status_code == 403 and required_scope:
        return ScopeError(
            f"The `{required_scope}` scope did not resolve for this request (403). "
            f"A scope works only while BOTH the API key carries it AND the "
            f"account's role grants the ability behind it, and the two need "
            f"opposite fixes. This client could not check which half is missing "
            f"just now — call the `whoami` tool (it needs no scope) and compare "
            f"`apiKey.requested` with `apiKey.scopes`: `{required_scope}` absent "
            f"from `requested` means the key lacks it (mint a new key), present "
            f"in `requested` but absent from `scopes` means the role lacks it "
            f"(ask an administrator)."
            + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
            required_scope=required_scope,
            # Carried, not acted on. The server does not send both today; if it
            # ever starts, discarding the field here would lose it silently,
            # and a caller inspecting the exception should see what arrived.
            required_ability=required_ability,
        )

    if status_code == 403 and required_ability:
        return AbilityError(
            f"This account's role does not hold the `{required_ability}` ability "
            f"(403). THIS IS NOT A KEY OR SCOPE PROBLEM: the API key's scope "
            f"resolved, the request got past the credential gate, and the "
            f"endpoint then refused the ACCOUNT. Minting a new key will not help "
            f"no matter which scopes are ticked, because an ability has no key "
            f"half — only an administrator can grant `{required_ability}` to this "
            f"account's role. Call `whoami` to see the abilities it does hold, in "
            f"`permissions`."
            + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
            required_ability=required_ability,
        )

    if status_code == 403:
        return PermissionError_(
            "Ebteqdesk refused this request (403) without naming a scope or an "
            "ability. The account behind this token does not hold a permission "
            "the endpoint requires, or the endpoint is not reachable by an API "
            "key at all. Run `whoami` to see the account's permissions, and ask "
            "an administrator to grant the ability named below."
            + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
        )

    if status_code == 404:
        return NotFoundError(
            "Ebteqdesk has nothing at that identifier (404)." + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
        )

    if status_code == 409:
        return ConflictError(
            "Ebteqdesk refused this write because of the resource's current "
            "state (409). 🔴 NO DOCUMENTED /api/v1 ENDPOINT ANSWERS 409, so this "
            "is either a proxy in front of Ebteqdesk or an endpoint newer than "
            "this client — read the server's own message below, which is the "
            "only thing that knows what conflicted. Retrying will not help "
            "unless that message says what to change. Note in particular that a "
            "PATCH of a PUBLISHED knowledge base article is NOT this: it no "
            "longer refuses, it stages a pending revision and answers 202."
            + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
        )

    if status_code == 413 and path.startswith(MEDIA_UPLOAD_PATH):
        return PayloadTooLargeError(
            "Ebteqdesk refused this upload before it reached the application "
            "(413). The request body was over the SERVER'S OWN ceiling — nginx's "
            "`client_max_body_size` or PHP's `post_max_size` — so it was cut off "
            "ahead of the knowledge base's per-type limits, and there are no "
            "field errors to read. DO NOT RETRY THE SAME FILE: nothing about "
            "this call changes the outcome, and there is no argument to make it "
            "smaller. Tell the user the file is too large for the server to "
            "accept and ask for a smaller export — a lower-resolution image, or "
            "a shorter or more compressed video. A file that is merely over the "
            "knowledge base's 10 MB image / 50 MB video cap comes back as a 422 "
            "naming the limit instead; a 413 means it did not even get that far."
            + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
        )

    if status_code == 413:
        return PayloadTooLargeError(
            "Ebteqdesk could not return this attachment within its response size "
            "ceiling (413). The endpoint downscales images before returning them "
            "and holds the encoded result to a hard byte limit; this one is still "
            "over it, or its source is too large to decode safely. RETRY WITH A "
            "SMALLER `max_dimension` — that is the one argument that changes this "
            "outcome. The original is readable by a human in the browser at its "
            "ticket."
            + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
        )

    if status_code == 415:
        mime_type = payload.get("mime_type")
        if not isinstance(mime_type, str) or not mime_type:
            mime_type = None

        named = f"`{mime_type}`" if mime_type else "not an image"

        return UnsupportedMediaError(
            f"This attachment is {named}, and that endpoint returns images only "
            f"(415). Ebteqdesk accepts video attachments as well as images, and a "
            f"video's bytes are never served under an image content type — a "
            f"decode failure is the best case and a confidently wrong reading of "
            f"the wrong bytes is the worst. NOTHING RETRIES INTO SUCCESS HERE: no "
            f"argument turns a video into an image. Tell the user the file is "
            f"{mime_type or 'not an image'} and that it has to be opened in the "
            f"browser at its ticket."
            + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
            mime_type=mime_type,
        )

    if status_code == 422:
        summary = "; ".join(
            f"{field}: {' '.join(str(m) for m in messages)}"
            for field, messages in field_errors.items()
        )
        return InvalidRequestError(
            "Ebteqdesk rejected the arguments (422)."
            + detail
            + (f" Fields: {summary}" if summary else ""),
            status_code=status_code,
            path=path,
            server_message=server_message,
            field_errors=field_errors,
        )

    if status_code == 429:
        wait = f" Retry after {retry_after}s." if retry_after else ""
        return RateLimitedError(
            "Ebteqdesk is rate limiting this token (429). The /api throttle is 60 "
            "requests per minute per token by default; wait a moment and try again, "
            "or fetch fewer pages." + wait + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
            retry_after=retry_after,
        )

    if status_code >= 500:
        return ServerError(
            f"Ebteqdesk returned a server error ({status_code}). This is a fault on "
            f"the Ebteqdesk side, not in the request — check the application logs "
            f"on the server." + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
        )

    return ApiError(
        f"Ebteqdesk returned an unexpected {status_code} for {path}." + detail,
        status_code=status_code,
        path=path,
        server_message=server_message,
        required_scope=required_scope,
        required_ability=required_ability,
        field_errors=field_errors,
    )


def diagnosed_scope_error(
    error: ScopeError,
    *,
    requested: Sequence[str],
    scopes: Sequence[str],
) -> ScopeError:
    """Narrow a bare `ScopeError` using the key metadata from GET /api/v1/user.

    `requested` is `apiKey.requested` — what the key itself carries, wildcard
    already expanded by the server. `scopes` is `apiKey.scopes` — the
    key-∩-role intersection, i.e. what actually resolves right now.

        scope not in requested            -> the KEY lacks it      -> new key
        scope in requested, not in scopes -> the ROLE lacks it     -> admin
        scope in both                     -> inconclusive, see below

    The third case is not a fallthrough to be tidied away. It means the scope
    resolved when we asked and did not when the request was refused, which is a
    role or key change landing between the two calls. Asserting either remedy
    there would be a guess, so the original verbatim error is returned unchanged.

    🔴 Nothing here reads `error.server_message`. The cause comes from two JSON
    arrays; the server's sentence is carried to the user but never parsed. That
    is the whole point — the prose has already been rewritten once, and a client
    that matched on it would have kept "working" while going wrong.
    """
    scope = error.required_scope

    if not scope:
        return error

    common = {
        "status_code": error.status_code,
        "path": error.path,
        "server_message": error.server_message,
        "required_scope": scope,
        "required_ability": error.required_ability,
    }
    detail = f" Ebteqdesk said: {error.server_message}" if error.server_message else ""

    if scope not in requested:
        return KeyScopeError(
            f"This API key was not minted with the `{scope}` scope (403). Scopes are "
            f"fixed when a key is created and cannot be added to an existing key: "
            f"mint a NEW key that includes `{scope}` (Settings -> API keys) and set "
            f"EBTEQDESK_API_TOKEN to it. "
            f"⚠️ CHECK YOUR ACCOUNT CAN HOLD `{scope}` BEFORE MINTING. Some "
            f"routes accept ANY ONE OF several scopes and, when none resolves, "
            f"name only the FIRST one they declare — which may be a scope your "
            f"role is not permitted at all, in which case a new key carrying it "
            f"would resolve nothing and fail here identically. "
            f"GET /api/v1/user lists what this account can actually hold; if "
            f"`{scope}` is not among them, the route almost certainly accepts an "
            f"alternative that IS, and the row you addressed was simply not one "
            f"that alternative covers. "
            f"(Checked via GET /api/v1/user: `{scope}` is absent from "
            f"`apiKey.requested`.)"
            + detail,
            **common,
        )

    if scope not in scopes:
        return RoleScopeError(
            f"This API key carries the `{scope}` scope, but it does NOT resolve for "
            f"this account's role (403). A scope resolves only while BOTH halves "
            f"hold, so minting a new key will NOT help — it would fail "
            f"identically. "
            f"⚠️ AN ABILITY GRANT MAY NOT HELP EITHER, and asking for one blind is "
            f"the wasted trip here. Two different things produce this: the role "
            f"may not hold the ability behind `{scope}`, which an administrator "
            f"can grant; or the role may hold that ability and still be refused "
            f"the scope, because some roles are not permitted some scopes on this "
            f"API however they are configured. "
            f"`escalation:write` and `escalation:reply` are the clearest case — "
            f"they are backed by the SAME ability, so an account can hold it, "
            f"resolve the first and never resolve the second. That is not a "
            f"misconfiguration: escalation hands the requester conversation over, "
            f"and the reply half goes with it. "
            f"Compare `permissions` in GET /api/v1/user with the ability behind "
            f"`{scope}` before asking for a grant: if it is already there, no "
            f"grant is coming and the answer is a different account or a "
            f"different approach. "
            f"(Checked via GET /api/v1/user: `{scope}` is in `apiKey.requested` but "
            f"not in `apiKey.scopes`.)"
            + detail,
            **common,
        )

    return error


def diagnosed_ability_error(
    error: AbilityError,
    *,
    permissions: Sequence[str],
) -> AbilityError:
    """Narrow a bare `AbilityError` using `permissions` from GET /api/v1/user (N1).

    `permissions` is the ROLE's full ability list — the caller's own, and served
    to any valid key, so consulting it discloses nothing.

        ability not in permissions -> the ROLE lacks it   -> RoleAbilityError
        ability in permissions     -> THIS TICKET was refused -> TicketAbilityError

    🔴 WHY THIS EXISTS. Every `required_ability` refusal used to produce one
    sentence: "ask an administrator to grant it". That is wrong in both
    directions. It is wrong for a SPECIALIST account refused an ability outside
    its remit — `ticket.close` on a seat that answers tickets without resolving
    them, say — because the advice sends them to an administrator who should
    decline. And it is wrong for an account that HOLDS the ability and was
    refused this particular ticket, where a grant changes nothing at all.

    The unattended case is the one that made this urgent: for a caller working
    through this API there is no browser to fall back on — so a refusal
    it cannot diagnose from the response is a dead end rather than a detour.

    🔴 NOTHING HERE READS `error.server_message`, for the reason
    `diagnosed_scope_error` gives: the cause comes from a structured list, never
    from prose that has already been rewritten once.

    Returns the original error untouched when `required_ability` is absent, so a
    caller can hand every AbilityError through without a guard.
    """
    ability = error.required_ability

    if not ability:
        return error

    common = {
        "status_code": error.status_code,
        "path": error.path,
        "server_message": error.server_message,
        "required_scope": error.required_scope,
        "required_ability": ability,
    }
    detail = f" Ebteqdesk said: {error.server_message}" if error.server_message else ""

    if ability in permissions:
        return TicketAbilityError(
            f"Your account HOLDS the `{ability}` ability, so this refusal is about "
            f"THIS TICKET and not about your permissions (403). Asking an "
            f"administrator for `{ability}` will not change it — you already have "
            f"it. "
            f"WHICH property of the ticket was refused is not something this "
            f"client can tell you: the server names the ability and deliberately "
            f"says no more. The per-ticket check can fail on any of these, and "
            f"more than one may apply — "
            f"the ticket is not yours, not your team's, and not an escalation you "
            f"may review; "
            f"or the ticket is not in the STATE the action needs — de-escalating "
            f"one that was never escalated, or reopening one that is not "
            f"resolved, is refused this way even on your own ticket. "
            f"Read the ticket back (`assignee`, `escalated`, `status`) and compare "
            f"it with what the action needs. "
            f"(Checked via GET /api/v1/user: `{ability}` IS in `permissions`.)"
            + detail,
            **common,
        )

    return RoleAbilityError(
        f"Your account's role does NOT hold the `{ability}` ability (403). No API "
        f"key can supply it — abilities live on the role, not on the key, so "
        f"minting a new one would fail identically. "
        f"Whether that is fixable depends on what your role is FOR: if this is "
        f"work your account is meant to do, an administrator can grant "
        f"`{ability}` in Settings -> Roles. If your account is a SPECIALIST one "
        f"— it answers tickets without resolving or reviewing them, or it works "
        f"the escalation queue — the absence is deliberate, and being refused "
        f"an ability outside that remit is not a misconfiguration: no grant is "
        f"coming. "
        f"GET /api/v1/user reports exactly what your role does hold. "
        f"(Checked via GET /api/v1/user: `{ability}` is absent from `permissions`.)"
        + detail,
        **common,
    )


def ticket_write_not_found(
    error: ApiError,
    *,
    scopes: Sequence[str],
) -> ApiError:
    """Explain a 404 from a ticket WRITE, using the caller's resolved scopes (N20).

    The server cannot say more than "no ticket with that id" — see
    `NotFoundError` for why one body has to cover three cases. But the CLIENT
    knows something the server will not put in the body: whether this key
    resolves `ticket:write` at all.

    If it does NOT, then "your own ticket" is a live explanation for this 404 and
    is the one a caller will never guess. A key holding only the escalation write
    scopes reaches escalated tickets and nothing else, so every ordinary ticket
    it addresses — including ones assigned to the account itself — comes back
    indistinguishable from an id that was never issued. Told only "there is no
    ticket with the id 4", the caller concludes the ticket was deleted.

    Nothing is disclosed by saying so: the sentence is about the CALLER'S OWN
    key, read from GET /api/v1/user, and it is added identically to every 404
    that key receives. It cannot be used to tell one id from another.

    Returns the error untouched for a caller that does resolve `ticket:write`,
    for which the own-ticket case cannot arise, and for anything that is not a
    404.
    """
    if not isinstance(error, NotFoundError) or TICKET_WRITE_SCOPE in scopes:
        return error

    return NotFoundError(
        str(error)
        + " ⚠️ THIS MAY BE YOUR OWN TICKET. This key does not resolve "
        "`ticket:write`, and a ticket write answers the same 404 for a row that "
        "does not exist, a row belonging to somebody else, and a row assigned to "
        "you that this key cannot write to — one body for all three, so that no "
        "key can map the id space. With the scopes this key holds it can act on "
        "ESCALATED tickets only. If you expected to write to an ordinary ticket "
        "of your own, the scope is what is missing, not the ticket: check "
        "GET /api/v1/user, and do not report the ticket as deleted.",
        status_code=error.status_code,
        path=error.path,
        server_message=error.server_message,
        required_scope=error.required_scope,
        required_ability=error.required_ability,
    )


def escalated_comment_error(error: ScopeError) -> ScopeError:
    """Explain the one scope refusal whose CAUSE is the ticket, not the key.

    POST /api/v1/tickets/{id}/comments declares the ANY-OF floor
    `apiScope:ticket:write|escalation:reply` on the route. On an ESCALATED
    ticket the controller then CHARGES `escalation:reply` — a requirement that
    cannot be declared on the route, because route middleware runs before the
    ticket is loaded and cannot see that it is escalated.

    So on that one path, a refusal naming `escalation:reply` means one thing and
    can mean nothing else: THE TICKET IS ESCALATED. The route middleware names
    only the FIRST alternative of its group — `ticket:write` — when it refuses,
    and the controller only reaches its `escalation:reply` check inside
    `if ($escalated)`. See TicketWritesController::comment().

    ⚠️ `escalation:reply` AND NOT `escalation:write`. The two were one scope
    once and were split because a private note and a message the requester
    receives by email are different risks. This endpoint sends the second.

    ⚠️ AND `escalated` IS `level == 1`, WITH NOTHING TO DO WITH STATUS. It stays
    true after the ticket is solved and after it is closed, so this refusal is
    as reachable on a ticket the caller resolved last month as on an open one.

    Since M2-T9 the caller COULD have known in advance — every ticket payload
    carries an `escalated` boolean — which makes this message better advice
    than it used to be rather than redundant: it can now name the field to look
    at next time instead of only explaining an unavoidable surprise.

    Without this, the message a user reads is a perfectly accurate sentence
    about `escalation:write` that says nothing about WHY replying to this
    particular ticket wants an escalation scope. The observed failure is that
    they re-mint with `ticket:write` — the scope the tool documents — and hit
    the identical wall. Since the split there is a second wrong next move, and
    the message rules it out by name: re-minting with `escalation:write`, the
    INTERNAL-note half of the same area, which this endpoint never accepts.

    🔴 The four properties of the scope machinery still hold:

      - Nothing is masked. The narrowed message is PREPENDED to the original,
        whose text (including the key/role diagnosis, if one was reached, and
        the server's own sentence) survives intact underneath.
      - No prose parsing. The branch is `required_scope == "escalation:reply"`
        on this path — a structured field compared to a constant, passed in
        rather than hardcoded so this narrower and the note narrower cannot
        match each other's scope.
      - No recursion and no extra request. This is a pure function of an
        exception that already exists.
      - Nothing on the success path.

    The class is preserved, so a `KeyScopeError` stays a `KeyScopeError` and
    `except RoleScopeError` still catches the role case: the escalated ticket
    is the CONTEXT of the refusal, not a fourth diagnosis, and it composes with
    whichever half `diagnosed_scope_error` established.

    Anything that is not the `escalation:reply` refusal is returned untouched,
    so the caller can hand every ScopeError through this without a guard.
    """
    return _narrowed_escalation_error(
        error,
        "THIS TICKET IS ESCALATED, and replying to the requester on an escalated "
        "ticket needs the `escalation:reply` scope INSTEAD OF `ticket:write` — "
        "the endpoint takes either scope, and an escalated ticket is charged the "
        "escalation one. That is the only way this endpoint can ask for "
        "`escalation:reply`, so this refusal tells you the ticket is escalated. "
        "Next time you can check first: every ticket payload carries an "
        "`escalated` boolean — read that, not the nullable `escalated_at`, and "
        "note that it stays true after the ticket is solved or closed, until "
        "somebody de-escalates it. "
        "The reason is not bureaucratic: on escalation the requester conversation "
        "is HANDED OVER to whoever is working the escalation, so an ordinary "
        "support account keeps reading and noting on the ticket and stops being "
        "the one who emails the requester's address. `escalation:write` is NOT a "
        "substitute — that is the internal-note half of the same area. If you "
        "need to say something to the requester and cannot, add a note for "
        "whoever holds the escalation, or de-escalate the ticket first. "
        "Details of the `escalation:reply` refusal follow. ",
        scope=ESCALATION_REPLY_SCOPE,
    )


def escalated_note_error(error: ScopeError) -> ScopeError:
    """The same narrowing for POST /api/v1/tickets/{id}/notes.

    Every word of `escalated_comment_error`'s argument applies unchanged — the
    notes route also declares an any-of floor
    (`apiScope:ticket:write|escalation:write`) and also charges its escalation
    scope only inside `if ($escalated)` (Api\\V1\\TicketNotesController::store),
    so a refusal naming that scope on THIS path likewise means one thing and can
    mean nothing else: the ticket is escalated.

    ⚠️ THE SCOPE DIFFERS FROM THE COMMENT NARROWER'S. A note costs
    `escalation:write`; a requester-facing reply costs `escalation:reply`. Two
    branches, two constants, and neither function may match the other's scope —
    that is what stops "you cannot note here" being printed for a refused reply.

    🔴 IT IS A SEPARATE FUNCTION BECAUSE THE *REASON* DIFFERS, AND THE REASON IS
    THE HALF THAT IS WORTH READING. The comment endpoint's explanation is
    "Ebteqdesk would silently downgrade your requester-facing reply into a private
    note", which is FALSE HERE — a note is what this endpoint files on purpose,
    and a user who is told that about a note tool concludes the tool is broken.
    The honest reason is the simpler one: the ticket is on the BP queue, and
    writing into a BP thread is a BP act whatever kind of row it writes.

    Reusing `escalated_comment_error` here would have been one line and would
    have printed a paragraph about a downgrade that cannot happen. The shared
    part — the guard, the class preservation, the PREPEND onto the original — is
    genuinely shared, through `_narrowed_escalation_error`.

    Anything that is not the `escalation:write` refusal is returned untouched.
    """
    return _narrowed_escalation_error(
        error,
        "THIS TICKET IS ESCALATED, and filing an internal note on an escalated "
        "ticket needs the `escalation:write` scope INSTEAD OF `ticket:write` — "
        "the endpoint takes either scope, and an escalated ticket is charged the "
        "escalation one. That is the only way this endpoint can ask for "
        "`escalation:write`, so this refusal tells you the ticket is escalated. "
        "Next time you can check first: every ticket payload carries an "
        "`escalated` boolean — read that, not the nullable `escalated_at`, and "
        "note that it stays true after the ticket is solved or closed, until "
        "somebody de-escalates it. "
        "The reason is NOT that your note would be downgraded — a note is what "
        "this endpoint files on purpose, and nothing here reaches the requester. It "
        "is that an escalated ticket belongs to the BP queue, so writing anything "
        "into its thread is a BP act and needs the BP scope, exactly as the "
        "Ebteqdesk browser UI requires `bp_escalation.reply` for the same write. "
        "A key holding only `ticket:write` cannot note here however many times it "
        "is re-minted with that same scope. Details of the `escalation:write` "
        "refusal follow. ",
    )


def _narrowed_escalation_error(
    error: ScopeError,
    lead: str,
    *,
    scope: str = ESCALATION_WRITE_SCOPE,
) -> ScopeError:
    """PREPEND `lead` to an `escalation:write` refusal, preserving everything.

    The mechanism behind both public narrowers above. Nothing is masked: the
    original message — including the key/role diagnosis, if one was reached, and
    the server's own sentence — survives intact underneath, and the CLASS is
    preserved so a `KeyScopeError` stays a `KeyScopeError` and
    `except RoleScopeError` still catches the role case.

    The branch is `required_scope == $scope`, a structured field compared to a
    constant — never prose parsing. Anything else is returned untouched, so a
    caller can hand every ScopeError through without a guard.

    🔴 $scope IS A PARAMETER BECAUSE THE TWO NARROWERS BRANCH ON DIFFERENT
    SCOPES. A note costs `escalation:write`; a requester-facing reply costs
    `escalation:reply`. A hardcoded constant here would print one endpoint's
    explanation for the other's refusal, which is exactly the failure the two
    separate narrowers exist to prevent one level up.
    """
    if error.required_scope != scope:
        return error

    return type(error)(
        lead + str(error),
        status_code=error.status_code,
        path=error.path,
        server_message=error.server_message,
        required_scope=error.required_scope,
        required_ability=error.required_ability,
    )
