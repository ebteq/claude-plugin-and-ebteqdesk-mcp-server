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
    +- UploadTransferError       a file's bytes did not reach the object store, or
    |                            the transfer was abandoned; never quotes the URL
    +- TransportError            host unreachable, DNS failure, TLS error, timeout
    +- MalformedResponseError    a reply that is not the JSON this API promises
    +- StalePortalApiError       a `portal`-filtered read came back from an
    |                            install that predates KB portal support
    +- ApiError                  the server answered, with an error status
       +- AuthenticationError    401  token missing/expired/revoked
       +- ScopeError             403  a scope did not resolve (carries required_scope)
       |  +- KeyScopeError       403  ...because the KEY was not minted with it
       |  +- RoleScopeError      403  ...because the OWNER'S ROLE does not back it
       +- AbilityError           403  the ACCOUNT lacks an ability (required_ability)
       +- TicketNotAssignedError 403  the ticket is real, readable on the shared
       |                              escalation queue, and somebody else's
       +- NotAgentAuthoredError  403  the reply or note to edit or delete is the
       |                              requester's own message or a system note
       +- PermissionError_       403  refused with no scope and no ability named
       +- NotFoundError          404
       +- StaleAttachmentApiError 404 with NO `error` key on a ticket-attachment
       |                              route: the desk predates the feature
       +- StaleThreadEntryApiError 404 with NO `error` key on a reply/note
       |                              edit-or-delete route: same, for that feature
       +- ConflictError          409  a state conflict. Only the ticket-attachment
       |                              routes produce one, each with a `reason`
       +- PayloadTooLargeError   413  the body is over a ceiling (three endpoints,
       |                              three causes)
       +- UnsupportedMediaError  415  a download that is not an image, or an
       |                              upload whose type the desk refuses
       +- InvalidRequestError    422  parameter validation, carries field errors
       +- RateLimitedError       429
       +- DirectUploadUnavailableError 501  large uploads are switched off. NOT a
       |                              ServerError: nothing is broken
       +- ServerError            5xx

🔴 THE TICKET-ATTACHMENT ROUTES BRANCH ON `reason` (5.1.0). POST
/api/v1/tickets/{id}/notes/attachments and the two `…/notes/uploads` routes are
the first on this surface where one status has several meanings a machine must
tell apart, so every refusal there carries a `reason` and `api_error_for`
compares it as a VALUE — the same rule as `ticket_not_assigned`, and for the
same reason: server sentences get rewritten. Three messages that were right for
every other endpoint are wrong for these (the 409 that said no endpoint answers
409, the 413 that prescribed a smaller `max_dimension`, the 415 that said the
endpoint returns images only), and each now has its own branch.

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

⚠️ A 403 CARRYING `reason: "not_agent_authored"` (5.3.0) IS THE SECOND ONE NO
CREDENTIAL FIXES, AND FOR A REASON THAT IS NOT OWNERSHIP EITHER. The four
edit-and-delete routes for a reply or a note reach the ticket exactly as their
POST siblings do and then refuse an entry an agent did not write — the
requester's own message, or a note the desk wrote itself. Nobody may change
those, on any surface. No scope and no ability is named, because neither gate
failed. See `NotAgentAuthoredError`.

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
    "UploadTransferError",
    "TransportError",
    "MalformedResponseError",
    "StalePortalApiError",
    "ApiError",
    "AuthenticationError",
    "ScopeError",
    "KeyScopeError",
    "RoleScopeError",
    "AbilityError",
    "TicketNotAssignedError",
    "NotAgentAuthoredError",
    "PermissionError_",
    "NotFoundError",
    "StaleAttachmentApiError",
    "StaleThreadEntryApiError",
    "ConflictError",
    "PayloadTooLargeError",
    "UnsupportedMediaError",
    "InvalidRequestError",
    "RateLimitedError",
    "DirectUploadUnavailableError",
    "ServerError",
    "api_error_for",
    "diagnosed_scope_error",
    "escalated_comment_error",
    "escalated_note_error",
    "escalated_comment_edit_error",
    "escalated_note_edit_error",
    "ESCALATION_WRITE_SCOPE",
    "MEDIA_UPLOAD_PATH",
    "NOT_ASSIGNED_REASON",
    "NOT_AGENT_AUTHORED_REASON",
    "THREAD_ENTRY_COMMENT",
    "THREAD_ENTRY_NOTE",
    "thread_entry_kind",
    "REASON_KEY_REUSED",
    "REASON_IN_PROGRESS",
    "REASON_UPLOAD_INCOMPLETE",
    "REASON_PAYLOAD_TOO_LARGE",
    "REASON_UNSUPPORTED_MEDIA",
    "REASON_SIZE_MISMATCH",
    "REASON_DIRECT_UNAVAILABLE",
    "REASON_STORAGE_UNAVAILABLE",
    "is_ticket_attachment_path",
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

#: The `reason` the four reply/note EDIT-and-DELETE routes carry when the entry
#: exists on the ticket and an agent did not write it — the requester's own
#: message, or a note the desk wrote itself. Verbatim from
#: `Api\V1\TicketThreadEntriesController::REASON_NOT_AGENT_AUTHORED`, and
#: compared as a VALUE for the reason `NOT_ASSIGNED_REASON` gives.
NOT_AGENT_AUTHORED_REASON = "not_agent_authored"

#: The two kinds of thread entry the edit-and-delete routes address, as the
#: path names them — `…/comments/{id}` is a PUBLIC reply, `…/notes/{id}` a
#: PRIVATE note. Returned by `thread_entry_kind` so a message can say which
#: kind of entry a 404 is about without reading the server's sentence.
THREAD_ENTRY_COMMENT = "comment"
THREAD_ENTRY_NOTE = "note"

#: The one upload path on this API. Named because the 413 branch below is the
#: only place in this module that has to tell TWO endpoints apart by their path
#: rather than by a field in the body — see that branch for why the alternative
#: (one message covering both) would give confidently wrong advice to one of
#: them. Kept as a VALUE next to the client method that sends it, not a regex.
MEDIA_UPLOAD_PATH = "/api/v1/kb/media"

#: The `reason` values the three ticket-attachment routes answer with, verbatim
#: from ADR 0011 §A3. Compared as VALUES, never matched in `error`'s prose — see
#: `NOT_ASSIGNED_REASON` above for why that rule has no exceptions here.
#:
#: Eight of them, and the status alone does not separate them: three share 409
#: and mean "never retry", "wait two seconds" and "send the file again".
REASON_KEY_REUSED = "idempotency_key_reused"
REASON_IN_PROGRESS = "idempotency_in_progress"
REASON_UPLOAD_INCOMPLETE = "upload_incomplete"
REASON_PAYLOAD_TOO_LARGE = "payload_too_large"
REASON_UNSUPPORTED_MEDIA = "unsupported_media_type"
REASON_SIZE_MISMATCH = "upload_size_mismatch"
REASON_DIRECT_UNAVAILABLE = "direct_upload_unavailable"
REASON_STORAGE_UNAVAILABLE = "storage_unavailable"

#: Where the ticket-attachment routes live. Three paths, told apart from every
#: other ticket write by what follows the id:
#:
#:     /api/v1/tickets/{id}/notes/attachments
#:     /api/v1/tickets/{id}/notes/uploads
#:     /api/v1/tickets/{id}/notes/uploads/{upload_id}/complete
#:
#: VALUES, like `MEDIA_UPLOAD_PATH`, and compared with `startswith`/`endswith`
#: rather than a pattern.
_TICKET_PATH_PREFIX = "/api/v1/tickets/"
_NOTE_ATTACHMENTS_SUFFIX = "/notes/attachments"
_NOTE_UPLOADS_SEGMENT = "/notes/uploads"


def is_ticket_attachment_path(path: str) -> bool:
    """True for the three routes `attach_file_to_ticket` calls, and no other.

    The second place this module tells endpoints apart by PATH (the first is
    the 413 on `MEDIA_UPLOAD_PATH`). It is needed for the answers that carry no
    `reason` to branch on: a 404 from a desk that has no such route at all, and
    a 413 or 415 an intermediary produced before the application could add one.
    """
    return path.startswith(_TICKET_PATH_PREFIX) and (
        path.endswith(_NOTE_ATTACHMENTS_SUFFIX) or _NOTE_UPLOADS_SEGMENT in path
    )


def thread_entry_kind(path: str) -> str | None:
    """`"comment"` or `"note"` for the four reply/note edit-and-delete routes
    (5.3.0), None for every other path.

        /api/v1/tickets/{id}/comments/{comment_id}   -> "comment"
        /api/v1/tickets/{id}/notes/{note_id}         -> "note"

    The third place this module tells endpoints apart by PATH, for the reason
    the other two have: a 404 from a desk with no such route carries no field to
    branch on. Compared SEGMENT BY SEGMENT, both ids all digits, so
    `…/notes/attachments` and `…/notes/uploads/{id}/complete` — the attachment
    routes under the same prefix — are never mistaken for an entry, and the
    plain `…/comments` list and POST are not either.
    """
    segments = path.split("/")

    # ["", "api", "v1", "tickets", "{id}", "comments"|"notes", "{entry_id}"]
    if len(segments) != 7 or "/".join(segments[:4]) + "/" != _TICKET_PATH_PREFIX:
        return None

    ticket_id, kind, entry_id = segments[4], segments[5], segments[6]

    if not (ticket_id.isdigit() and entry_id.isdigit()):
        return None

    if kind == "comments":
        return THREAD_ENTRY_COMMENT

    if kind == "notes":
        return THREAD_ENTRY_NOTE

    return None


class EbteqdeskError(Exception):
    """Base class for everything this package raises deliberately."""


class ConfigurationError(EbteqdeskError):
    """The environment is not usable. Raised before any request is attempted."""


class LocalFileError(EbteqdeskError):
    """A path on the USER'S OWN MACHINE could not be read. Nothing was sent.

    🔴 THE FIRST FAILURE MODE IN THIS PACKAGE THAT IS NOT ABOUT EBTEQDESK AT
    ALL. Every other error here describes the environment, the network or the
    server; this one describes the caller's filesystem, and it exists because
    two tools read a local file: `upload_kb_media`, and since 5.1.0
    `attach_file_to_ticket`.

    Raised only BEFORE anything is sent — that is what the message promises. A
    file that changes or disappears once a transfer is under way is an
    `UploadTransferError` instead, because by then "nothing was sent" is false.

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


class UploadTransferError(EbteqdeskError):
    """A file's bytes did not reach the desk's file store, or the transfer was
    abandoned part-way. NOTHING WAS ATTACHED.

    Raised only by `attach_file_to_ticket`. A video over 20 MB does not go
    through /api/v1 at all: the desk hands out a short-lived upload address on
    its object store, this client PUTs the file there, and only then asks the
    desk to attach it. This class is every way that middle step ends without an
    attachment, plus the one failure both transports share — the file changing
    under the read.

    🔴 THE UPLOAD ADDRESS IS A CREDENTIAL AND IT IS NEVER IN HERE. Anyone
    holding that URL can write to the object until it expires, so it appears in
    no message, no attribute and no chained exception of this class. A transport
    failure is described by the TYPE of the exception that caused it, the status
    code if the store answered, and how many bytes had gone — never by
    `str(exc)`, because an HTTP library's messages quote the request URL.
    `TransportError` below makes the same choice for the same reason; here it is
    not caution, it is the control.

    `kind` says which ending it was, as a value a caller can branch on:

        put_failed       the store refused the PUT or the connection failed,
                         again after one fresh attempt
        deadline         the upload had not finished when its address was about
                         to expire (50 minutes at most); it cannot resume
        file_changed     the file's size or modification time moved while it
                         was being read, so it was abandoned and the desk was
                         NOT asked to attach it
        expired          the desk had no pending upload left to complete, twice
        incomplete       the desk saw no file after the store accepted one, twice
        insecure_url     the address was not https://, so nothing was sent
        malformed_start  the desk's answer held no usable address

    Not an ApiError: in five of the seven the desk never answered with an error
    at all, and a caller catching ApiError to read `status_code` would be
    reading the object store's status as if it were the desk's.
    """

    PUT_FAILED = "put_failed"
    DEADLINE = "deadline"
    FILE_CHANGED = "file_changed"
    EXPIRED = "expired"
    INCOMPLETE = "incomplete"
    INSECURE_URL = "insecure_url"
    MALFORMED_START = "malformed_start"

    def __init__(
        self,
        kind: str,
        file_name: str,
        *,
        cause_type: str | None = None,
        status_code: int | None = None,
        bytes_sent: int = 0,
        bytes_total: int = 0,
    ) -> None:
        self.kind = kind
        self.file_name = file_name
        #: The exception TYPE behind a failed PUT, e.g. "ConnectError". Never
        #: its message — see the class docstring.
        self.cause_type = cause_type
        #: What the object store answered, if it answered. Not the desk's.
        self.status_code = status_code
        self.bytes_sent = bytes_sent
        self.bytes_total = bytes_total

        progress = f"{bytes_sent:,} of {bytes_total:,} bytes had been sent"

        if kind == self.PUT_FAILED:
            if status_code is not None:
                cause = f"the file store answered {status_code}"
            elif cause_type:
                cause = f"the connection failed with {cause_type}"
            else:
                cause = "the file store did not accept it"

            body = (
                f"The upload of {file_name!r} to the desk's file store failed, "
                f"and failed again on one fresh attempt ({cause}; {progress}). "
                "NOTHING WAS ATTACHED and no note was written. This is a "
                "network or file-store problem, not a problem with the ticket. "
                "It is safe to call the tool again with exactly the same "
                "arguments — the upload starts again from the beginning — but "
                "if it fails the same way, stop and tell the user rather than "
                "looping."
            )
        elif kind == self.DEADLINE:
            body = (
                f"The upload of {file_name!r} was abandoned because it had not "
                f"finished before its upload address was due to expire "
                f"({progress}). A large upload is allowed 50 minutes at most "
                "and cannot resume. NOTHING WAS ATTACHED and no note was "
                "written. The connection is too slow for a file this size: "
                "about 1.4 Mbit/s sustained is needed for 500 MB. Tell the "
                "user. Calling again starts from zero and meets the same limit "
                "unless the connection is faster or the file is smaller."
            )
        elif kind == self.FILE_CHANGED:
            body = (
                f"The upload of {file_name!r} was abandoned because the file "
                "changed, or could no longer be read, while it was being sent "
                f"— its size or modification time moved ({progress}). NOTHING "
                "WAS ATTACHED: the desk was not asked to attach a file that is "
                "no longer the one that was named. Wait until whatever is "
                "writing the file has finished, then call the tool again; the "
                "file as it is now is sent as a new upload."
            )
        elif kind == self.EXPIRED:
            body = (
                f"The upload of {file_name!r} could not be completed: the desk "
                "had no pending upload left for it, twice in a row — it expired "
                "or was cleared before it could be attached. NOTHING WAS "
                "ATTACHED. Attach the file again by calling the tool again with "
                "the same arguments."
            )
        elif kind == self.INCOMPLETE:
            body = (
                f"The upload of {file_name!r} was sent twice and the desk "
                "reported both times that no file had arrived. NOTHING WAS "
                "ATTACHED. That points at the desk's file store rather than at "
                "the file: tell the user, and have an administrator check it "
                "before trying again."
            )
        elif kind == self.INSECURE_URL:
            body = (
                f"The upload of {file_name!r} was NOT SENT: Ebteqdesk answered "
                "with an upload address that is not `https://`, and this "
                "server sends a file's bytes only over an encrypted connection "
                "— the same care the API token gets. NOTHING WAS SENT OR "
                "ATTACHED. This is a fault in the desk's file-store "
                "configuration and only an administrator can fix it. Do not "
                "retry."
            )
        else:
            body = (
                f"The upload of {file_name!r} was NOT SENT: Ebteqdesk accepted "
                "the request but its answer did not hold a usable upload "
                "address in the documented shape. NOTHING WAS SENT OR "
                "ATTACHED. This desk and this client disagree about the upload "
                "contract — one of them is the wrong version. Do not retry; "
                "tell the user. Files up to 20 MB do not use this path."
            )

        super().__init__(body)


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


class StalePortalApiError(EbteqdeskError):
    """A `portal`-filtered read got a 200, but the shape says the install does
    not understand `portal` at all — so the rows above are NOT what was asked
    for, and handing them back would be a silent wrong answer.

    🔴 WHY THIS EXISTS AT ALL: Laravel ignores a query parameter no route
    declares, rather than rejecting it. Against an Ebteqdesk install that
    predates two-knowledge-base (Salon V3 / Warni) support, `GET
    /api/v1/kb/tree?portal=warni` answers 200 with the SAME unfiltered tree it
    always gave — both knowledge bases interleaved — and nothing about the
    status code or the envelope says so. A caller that only checked for an
    error would believe it got Warni-only categories and hand them to a human
    as if they were. Refusing here, once, is cheaper than that.

    🔴 THE SIGNAL IS `kb_tree`'s OWN SHAPE, NOT A SEPARATE PROBE. An upgraded
    install's `GET /api/v1/kb/tree` puts a `portal` key on every category row;
    an install that has not shipped the change yet has never heard of that
    key and cannot put it there. So `list_kb_tree` looks at the categories
    it already fetched — no second request — and raises this the moment
    `portal` was asked for but at least one category came back without a
    `portal` key of its own. `search_kb_articles`, `get_kb_article` and
    `list_kb_proposals` do NOT get this check: their rows nest a
    `{slug, name}` category pair that has never carried `portal`, upgraded
    install or not, so its absence there proves nothing and checking it would
    misfire on every call. Verify an install with `list_kb_tree(portal=...)`
    before trusting a `portal` argument on those three.

    The fix is entirely server-side — there is nothing this client can do
    except stop pretending the filter worked. Ask an administrator to deploy
    the Ebteqdesk release that adds `kb_categories.portal` and portal-aware
    `GET /api/v1/kb/*` filtering. Omitting `portal` is unaffected: it already
    reproduces this install's only behaviour today, so it never raises this.
    """

    def __init__(self, portal: str) -> None:
        self.portal = portal

        super().__init__(
            f"Asked Ebteqdesk to filter its knowledge base tree by "
            f"portal={portal!r}, but the categories it returned carry no "
            f"`portal` key at all. This Ebteqdesk install predates "
            f"two-knowledge-base (Salon V3 / Warni) support and SILENTLY "
            f"IGNORED the filter rather than rejecting it — Laravel drops an "
            f"unknown query parameter instead of erroring on it — so the tree "
            f"just returned is BOTH knowledge bases interleaved, not only "
            f"{portal!r}'s. Nothing on this client can filter it after the "
            f"fact: ask an administrator to deploy the Ebteqdesk release that "
            f"adds `kb_categories.portal` and portal-aware "
            f"`GET /api/v1/kb/*` filtering, then retry. Omitting `portal` is "
            f"unaffected by this and safe to use against this install today."
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

    `reason` and `retry_after` are carried the same way since 5.1.0. `reason` is
    the body's own `reason` value — `ticket_not_assigned`, and the eight the
    ticket-attachment routes answer with — and `retry_after` is the
    `Retry-After` header, which those routes send on a 409 and a 503 as well as
    on a 429. Both are what a caller branches on instead of the prose.
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
        reason: str | None = None,
        retry_after: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.path = path
        self.server_message = server_message
        self.required_scope = required_scope
        self.required_ability = required_ability
        #: The body's `reason`, verbatim, or None. A value to compare, never
        #: prose to read.
        self.reason = reason
        #: The `Retry-After` header as it arrived (seconds, as a string), or
        #: None.
        self.retry_after = retry_after
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


class NotAgentAuthoredError(ApiError):
    """403 `not_agent_authored` — the entry exists and an agent did not write it.

    Raised only by the four reply/note edit-and-delete routes (5.3.0):

        PATCH|DELETE /api/v1/tickets/{id}/comments/{comment_id}
        PATCH|DELETE /api/v1/tickets/{id}/notes/{note_id}

    The ticket was reached, its charge was paid, and the id named a real entry
    of the right kind on it — which the REQUESTER wrote (their message arrived
    by mail or through the portal) or the DESK wrote itself (a system note).
    Ebteqdesk never lets anyone change either, on any surface: the requester's
    words are the record of what was asked, a system note the record of what
    the application did.

    🔴 NO CREDENTIAL FIXES THIS, for the reason `TicketNotAssignedError` gives
    and with one difference: here nobody at all can make the change, not even
    the ticket's assignee. Neither `required_scope` nor `required_ability` is
    present because neither gate failed, so nothing is diagnosed and no second
    request is spent.

    403 and not 404 because the caller can READ the entry in the thread —
    hiding it would be the API contradicting itself. `retriable` is False.

    ⚠️ AVOIDABLE, AND THE MESSAGE SAYS HOW. Every thread entry carries
    `agent_authored`, read off the same predicate on the desk
    (`Comment::isAgentAuthored`, `comments.user_id` set), so this refusal is
    exactly the entries marked `false` — which also covers a reply or note by
    an agent who has since been deleted from the desk, whose `user_id` the desk
    nulls. A caller that checks the flag first never sees this error.
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
    `set_ticket_status`, `set_ticket_category` or the escalate pair means "this
    key cannot act on that id", never "that id is free". Do not report a ticket
    as deleted on the strength of one, and do not retry: nothing about it is
    transient.

    ⚠️ THE FOUR REPLY/NOTE EDIT-AND-DELETE ROUTES (5.3.0) HAVE A SECOND 404.
    After the ticket's 404 above comes the ENTRY's — "There is no public reply
    with the id N on this ticket" / "… private note …" — for an id that is the
    other kind, belongs to another ticket, or is already gone. The two differ
    only in the server's sentence, which is carried and never parsed, so the
    message for those routes states both (see `api_error_for`).

    ⚠️ ONE MORE CAUSE ON `set_ticket_category` ALONE: an Ebteqdesk install
    that predates PUT /tickets/{id}/category has no such route and 404s (or
    405s) every id. Nothing in the body distinguishes that from the ownership
    404, so it is documented on the tool rather than detected here.

    `ticket_write_not_found()` adds that sentence to the message when the
    account's own scopes make the own-ticket case a live possibility.
    """


class StaleAttachmentApiError(ApiError):
    """404 with NO `error` key on a ticket-attachment route — this Ebteqdesk has
    no such endpoint. It predates the release that added it.

    🔴 THE SIGNAL IS THE SHAPE OF THE BODY, NOT THE STATUS. Every 404 Ebteqdesk
    itself writes on /api/v1 is `{"error": "<sentence>"}` — "There is no ticket
    with the id 4" — because each v1 controller builds its refusals by hand. A
    route that does not exist never reaches a controller: Laravel answers it
    with its own `{"message": "The route … could not be found."}`, which has no
    `error` key at all. So on these three paths, and only on them, a 404 without
    that key means the ROUTE is missing rather than the ticket.

    VERIFIED ON A RUNNING DESK, and pinned by a test on the desk's side: the
    missing-route body is JSON for this client (it always sends `Accept:
    application/json`), a debug build adds `exception`/`file`/`line`/`trace`
    and still no `error`, and every 404 the three routes build themselves has
    `error`. A 404 that is NOT JSON AT ALL is therefore not this — it is
    something in front of the desk answering, and stays a
    MalformedResponseError that says so.

    The same idea as `StalePortalApiError` — a newer client meeting an older
    desk, recognised from the one shape signal there is — and it exists for the
    same reason: this client ships ahead of the desk it talks to. It is an
    ApiError where that one is not, because here the desk did answer with an
    error status and `status_code` and `path` mean what they say.

    🔴 DELIBERATELY NOT A NotFoundError. A ticket write's 404 gets the
    "this may be your own ticket" explanation appended (`ticket_write_not_found`),
    and telling somebody their ticket may exist after all, about a desk that
    simply has no such feature, is the confidently wrong advice this module
    exists to avoid.

    Nothing a caller can change fixes it: an administrator deploys the Ebteqdesk
    release that adds the routes. Never retried.
    """

    #: Nothing about the request can change the outcome.
    retriable = False


class StaleThreadEntryApiError(ApiError):
    """404 with NO `error` key on a reply/note edit-or-delete route (5.3.0) —
    this Ebteqdesk predates the release that added those four routes.

    The same signal, for the same reason, as `StaleAttachmentApiError`: every
    404 Ebteqdesk itself writes on /api/v1 carries `error`, and a route that
    does not exist never reaches a controller to add one. On an older desk
    `PATCH /api/v1/tickets/{id}/comments/{comment_id}` matches nothing at all,
    so this client — which ships ahead of the desk again — would otherwise
    report a missing ticket or a missing reply about a desk that simply has no
    such feature.

    A separate class rather than a second use of `StaleAttachmentApiError`, so a
    caller catching that one is never told a reply edit is about attachments.
    Also deliberately NOT a NotFoundError, so the "this may be your own ticket"
    sentence `ticket_write_not_found` appends to a ticket-write 404 is never
    added to it.

    Nothing a caller can change fixes it. NOTHING WAS CHANGED. Never retried.
    """

    #: Nothing about the request can change the outcome.
    retriable = False


class ConflictError(ApiError):
    """409 — refused because of something already recorded or still running.

    🔴 SINCE 5.1.0 THE TICKET-ATTACHMENT ROUTES ANSWER 409 THREE WAYS, AND
    `reason` IS THE ONLY THING THAT TELLS THEM APART. The remedies are "never",
    "in two seconds" and "send the file again", so one message for all three
    would be wrong for two of them:

      idempotency_key_reused   the upload key was already used for a DIFFERENT
                               file, ticket or note. Final — the same call gets
                               the same answer for as long as the key lives.
      idempotency_in_progress  the same request is still being processed. Wait
                               `retry_after` seconds and repeat it.
      upload_incomplete        the desk was asked to attach an upload whose
                               file has not arrived at the object store.

    `EbteqdeskClient` absorbs the last two inside the transfer — it waits, or
    sends the file again — so `attach_file_to_ticket` normally surfaces only the
    first.

    A 409 CARRYING NO `reason` IS NOT ONE OF THESE, and gets the generic message:
    a proxy in front of Ebteqdesk, or an endpoint newer than this client. The
    server's own sentence is then the only thing that knows what conflicted.

    HISTORY, AND WHY THE GENERIC MESSAGE STILL MENTIONS THE KNOWLEDGE BASE. This
    class's first producer was PATCH /api/v1/kb/articles/{reference}, which
    answered 409 for a PUBLISHED article. That endpoint now stages the edit as a
    pending REVISION and answers 202 (see EbteqdeskClient.update_kb_article), so
    for four releases nothing produced a 409 and the message said so. It must
    not go back to teaching the removed rule: "the article is published" would
    be a confident, wrong diagnosis of whatever the next unexplained 409 is.
    """


class PayloadTooLargeError(ApiError):
    """413 — a body was over a ceiling. THREE endpoints raise it for THREE
    reasons, and the message is the only thing that tells them apart.

    DOWNLOAD — GET /api/v1/attachments/{id} downscales before it returns and
    holds the ENCODED body to a hard ceiling; an image that cannot be reduced
    under it, or whose source is too large to decode safely at all, is refused.
    Worth retrying with a SMALLER `max_dimension`, which is the whole reason
    this is a class of its own rather than the generic ApiError fallthrough.

    KB UPLOAD — POST /api/v1/kb/media, where the REQUEST was too large for
    nginx's `client_max_body_size` or PHP's `post_max_size` and was cut off
    before the application saw it. There is no argument that changes the outcome
    and no field errors to read: the per-kind cap that produces a helpful 422
    lives in App\\Kb\\MediaRules, one layer further in, and this request never
    got there. The only remedy is a smaller file.

    TICKET ATTACHMENT (5.1.0) — the three `…/notes/` attachment routes, where
    the APPLICATION refuses with `reason: "payload_too_large"` and names the
    limit in `max_bytes`: 20,971,520 for a file sent through the API,
    524,288,000 for a video sent to the object store. Nothing to retry here
    either, but unlike the KB upload the number is known and is in the message.

    🔴 So the remedies differ — "retry with a smaller argument" against "there
    is nothing to retry" — which is why `api_error_for` branches on the path and
    on `reason`. One message for all three would send an uploader into a retry
    loop with an argument that endpoint does not have.

    ⚠️ On either upload side a 413 may also arrive as HTML from nginx rather
    than JSON from Laravel, in which case it surfaces as MalformedResponseError
    instead. That is nginx answering before PHP runs at all, and the snippet in
    that message says so.
    """

    def __init__(self, *args: Any, max_bytes: int | None = None, **kwargs: Any) -> None:
        #: The limit the desk named, in bytes. Only the ticket-attachment
        #: routes send one; None everywhere else.
        self.max_bytes = max_bytes
        super().__init__(*args, **kwargs)


class UnsupportedMediaError(ApiError):
    """415 — a type the endpoint will not handle. Two endpoints, two directions.

    DOWNLOAD — GET /api/v1/attachments/{id} serves images ONLY. Ebteqdesk
    accepts video (`video/mp4`, `video/quicktime`, `video/webm`) and, since the
    release that added ticket attachments over the API, documents (PDF, Office,
    CSV, TXT, ZIP) alongside images, and none of those bytes is ever returned
    under an image content type: a client that base64'd an MP4 into a vision
    request would get a decode failure at best and a confidently wrong answer
    at worst. `mime_type` carries what the row actually is, so a caller can tell
    "this is a video" from "this file is broken" without another request. It is
    None only if the server omitted the field.

    UPLOAD (5.1.0) — the ticket-attachment routes refuse a file whose type is
    not on the desk's list, with `reason: "unsupported_media_type"`. `mime_type`
    is None on this side: the desk does not say what it thinks the file is, it
    says what it accepts, in `field_errors` — keyed `file` on the multipart
    route and `mime_type` on the upload route — and that sentence is quoted in
    the message.

    NOT retryable in either direction. No argument to the download makes a
    video into an image. The upload's verdict comes from the file's content —
    and, for a document, from its extension as well: the desk accepts a
    document only when its extension is on its list AND its content is what
    that extension says. So a rename that disguises a file changes nothing,
    and the one rename that helps is the honest one (a text log is accepted as
    `.txt` and refused as `.log`). The completion route's 415 carries no
    `field_errors` at all: it is the verdict on bytes already uploaded.
    """

    def __init__(self, *args: Any, mime_type: str | None = None, **kwargs: Any) -> None:
        #: The attachment's stored mime type, e.g. "video/mp4". None if absent.
        self.mime_type = mime_type
        super().__init__(*args, **kwargs)


class InvalidRequestError(ApiError):
    """422 — a query parameter was rejected. `field_errors` says which."""


class RateLimitedError(ApiError):
    """429 — the /api throttle (60 requests/minute by default) tripped.

    `retry_after` is the header's value. It lived on this class alone until
    5.1.0 and is now carried by `ApiError`, because the ticket-attachment routes
    send the same header on a 409 and a 503.
    """


class DirectUploadUnavailableError(ApiError):
    """501 `direct_upload_unavailable` — large videos are switched off here.

    🔴 A 5xx THAT IS NOT A FAULT, WHICH IS WHY IT IS NOT A ServerError. The
    generic 5xx message says something broke on the Ebteqdesk side and sends
    the reader to the application logs. Nothing broke: sending a video over
    20 MB needs an object store and an operator switch, this deployment has one
    of them missing or off, and the desk said so in a status code that happens
    to start with 5. A user told to check the logs for that would find nothing
    in them.

    The other half of the message matters as much: FILES UP TO 20 MB STILL
    WORK. They take a different route that needs neither, so "attachments are
    unavailable" would be wrong, and a model that concluded it would stop
    offering to attach a PDF.

    Never retried: the answer changes when an administrator changes the
    deployment, not before.
    """

    #: Nothing about the request can change the outcome.
    retriable = False


class ServerError(ApiError):
    """5xx — Ebteqdesk itself failed. Nothing the client can do differently.

    One 503 is narrower than that and says so in `reason`:
    `storage_unavailable`, from the route that attaches an uploaded video, means
    the desk could not reach its object store to verify the file. It is still a
    fault on the desk's side — hence this class — but it is the one 5xx here
    with a `retry_after` and a call that is safe to repeat.
    """


def api_error_for(
    *,
    status_code: int,
    path: str,
    payload: Mapping[str, Any],
    retry_after: str | None = None,
) -> ApiError:
    """Turn one JSON error body into the most specific exception available.

    The 403 fork is the interesting one, it is FIVE ways, and each way is
    NARROWER than it looks.

      - `reason` == "ticket_not_assigned" -> not a permission failure at all.
        The credential resolved and the role was never the problem: the TICKET
        belongs to another agent. Tested FIRST because it is a 403 with no
        remedy, and answering it with any of the three below sends the
        caller to mint a key or find an administrator for something neither can
        fix. The server sends it with neither of the two fields below, so the
        order is unobservable today and fixed anyway.
      - `reason` == "not_agent_authored" (5.3.0) -> the other 403 with no
        remedy, tested right after for the same reason: the entry to edit or
        delete is the requester's own message or a system note.
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

    THE TICKET-ATTACHMENT ROUTES ADD A SECOND FORK, ON `reason` (5.1.0). Those
    three routes answer 409 three ways and reuse 413, 415, 422, 501 and 503 for
    things the older messages do not describe, so each of those statuses tests
    `reason` — a VALUE, compared to a constant — before falling through to what
    it always said. Two answers on those routes carry no `reason` to compare and
    are recognised by the PATH instead, exactly as the KB upload's 413 already
    is: a 404 with no `error` key (the route does not exist on this desk), and a
    413 or 415 from something in front of the application.

    THE REPLY/NOTE EDIT-AND-DELETE ROUTES (5.3.0) ADD A FIFTH 403 AND A PATH
    FORK ON 404. The 403 is `reason: "not_agent_authored"`, compared as a value
    like `ticket_not_assigned` and tested right after it, for the same reason:
    it is a 403 no grant fixes. The 404 is recognised by `thread_entry_kind` —
    with no `error` key it is a desk that predates the routes, with one it is
    the ticket's 404 or the entry's, and the message states both.
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

    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason:
        reason = None

    attachment_route = is_ticket_attachment_path(path)
    entry_kind = thread_entry_kind(path)

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
            reason=reason,
        )

    if status_code == 403 and reason == NOT_AGENT_AUTHORED_REASON:
        return NotAgentAuthoredError(
            "Ebteqdesk refused to change this entry because an AGENT DID NOT "
            "WRITE IT (403). It is the requester's own message or a note the "
            "desk wrote itself — the record of what was asked, or of what the "
            "application did — and nobody can edit or delete either, through "
            "this API or in the Ebteqdesk web UI. THIS IS NOT A KEY, SCOPE OR "
            "ABILITY PROBLEM: the ticket was reached and the permissions were "
            "enough, and nothing anyone grants changes it. NOTHING WAS CHANGED. "
            "DO NOT RETRY, and do not try the other edit or delete tool on the "
            "same id. Every thread entry says in advance whether this happens: "
            "it is exactly the entries with `agent_authored: false` — the "
            "requester's messages, system notes, and replies or notes by an "
            "agent since removed from the desk. If you meant an agent's reply or "
            "note, read the thread again with `get_ticket` and take the `id` of "
            "an entry whose `agent_authored` is `true`, and check that flag "
            "before the next edit or delete."
            + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
            reason=reason,
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

    if status_code == 404 and attachment_route and "error" not in payload:
        # 🔴 The KEY's absence, not the sentence's content. Ebteqdesk's own 404
        # always has `error`; Laravel's answer for a route that does not exist
        # has `message` and no `error`. See StaleAttachmentApiError.
        return StaleAttachmentApiError(
            "This Ebteqdesk has no ticket attachment endpoint. It predates the "
            "release that added it. (It answered 404 for the attachment route "
            "itself, in the framework's own shape and without the `error` "
            "sentence every refusal Ebteqdesk writes carries.) THIS IS NOT A "
            "MISSING TICKET, so do not report the ticket as absent and do not "
            "try another id. NOTHING WAS ATTACHED, and nothing about the call "
            "can change that: an administrator has to deploy the Ebteqdesk "
            "release that adds file attachments to /api/v1. Until then a file "
            "can be attached only by a person, in the browser, on the ticket. "
            "Do not retry.",
            status_code=status_code,
            path=path,
            server_message=server_message,
        )

    if status_code == 404 and attachment_route:
        # WITH the `error` key, so Ebteqdesk's own — and on these routes it has
        # two meanings the body tells apart only in its sentence, which is
        # carried and never parsed. Both are said, so whichever it is, the
        # reader is not sent the wrong way.
        return NotFoundError(
            "Ebteqdesk has nothing to attach this to, or nothing left of an "
            "earlier attachment (404). NOTHING WAS ATTACHED. Its own sentence, "
            "below, says which. If it names the TICKET, this key cannot reach a "
            "ticket with that id — it does not exist, or it is not this "
            "account's and not on the escalation queue; do not retry and do not "
            "report the ticket as deleted. If it says the note and file NO "
            "LONGER EXIST, this exact call succeeded once and what it attached "
            "has since been removed on the desk; the desk still remembers the "
            "call, so repeating it cannot put the file back. To attach it "
            "afresh the call has to differ — a different note, or the file "
            "saved again so that its modification time moves."
            + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
        )

    if status_code == 404 and entry_kind is not None and "error" not in payload:
        # 🔴 The KEY's absence again, exactly as on the attachment routes:
        # Ebteqdesk's own 404 always has `error`. See StaleThreadEntryApiError.
        return StaleThreadEntryApiError(
            "This Ebteqdesk has no endpoint for editing or deleting a reply or "
            "a note. It predates the release that added them. (It answered 404 "
            "for the route itself, in the framework's own shape and without "
            "the `error` sentence every refusal Ebteqdesk writes carries.) THIS "
            "IS NOT A MISSING TICKET OR A MISSING ENTRY, so do not report "
            "either as absent and do not try another id. NOTHING WAS CHANGED, "
            "and nothing about the call can change that: an administrator has "
            "to deploy the Ebteqdesk release that adds editing and deleting "
            "replies and notes. Do not retry.",
            status_code=status_code,
            path=path,
            server_message=server_message,
        )

    if status_code == 404 and entry_kind is not None:
        # WITH the `error` key, so Ebteqdesk's own — and on these routes it has
        # two meanings the body tells apart only in its sentence, which is
        # carried and never parsed. Both are said, so whichever it is, the
        # reader is not sent the wrong way.
        if entry_kind == THREAD_ENTRY_NOTE:
            this_kind, this_entry, other = (
                "PRIVATE NOTE",
                '`kind: "note"`',
                "a public reply's id (`edit_ticket_comment` and "
                "`delete_ticket_comment` take those)",
            )
        else:
            this_kind, this_entry, other = (
                "PUBLIC REPLY",
                '`kind: "comment"`',
                "a private note's id (`edit_private_note` and "
                "`delete_private_note` take those)",
            )

        return NotFoundError(
            f"Ebteqdesk found nothing here to change (404). NOTHING WAS "
            f"CHANGED. Its own sentence, below, says which of two things it "
            f"was. If it names the TICKET, this key cannot reach a ticket with "
            f"that id for this write — it does not exist, or it is neither this "
            f"account's own nor escalated, or it is escalated and this key "
            f"cannot read the escalation queue; an account-wide read does not "
            f"reach it either. Do not report the ticket as deleted. If it names "
            f"the {this_kind}, the ticket was reached and that id is not a "
            f"{this_kind.lower()} on it: it is {other}, an entry on a different "
            f"ticket, or an entry that no longer exists — and if you are "
            f"repeating a delete that timed out or failed, it means the first "
            f"one landed. Read the thread again with `get_ticket` and take the "
            f"`id` of the {this_entry} entry you meant. Do not retry this call "
            f"unchanged."
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

    if status_code == 409 and reason == REASON_KEY_REUSED:
        return ConflictError(
            "Ebteqdesk refused this attachment (409): this file was already "
            "sent with different details, so the desk will not file it again "
            "under the same upload key. DO NOT RETRY — the same call gets the "
            "same answer. The key is made from the ticket, the file's path, "
            "its size, its modification time and the note text, so this means "
            "the file's CONTENT changed while all of those stayed the same, or "
            "an earlier attempt at the same call is recorded differently on "
            "the desk. NOTHING NEW WAS ATTACHED. Read the ticket with "
            "`get_ticket` to see what is already on it, and tell the user. If "
            "the file really is a new version, saving it again — so that its "
            "modification time moves — makes it a new upload."
            + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
            reason=reason,
            retry_after=retry_after,
        )

    if status_code == 409 and reason == REASON_IN_PROGRESS:
        return ConflictError(
            "Ebteqdesk is still processing an earlier copy of this same "
            "request (409), and this client has already waited for it. Nothing "
            "new was written by this call, and nothing has been lost. Call the "
            "tool again with EXACTLY THE SAME ARGUMENTS in a few seconds: the "
            "desk answers with the one finished attachment rather than a "
            "second, and marks it `replayed`."
            + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
            reason=reason,
            retry_after=retry_after,
        )

    if status_code == 409 and reason == REASON_UPLOAD_INCOMPLETE:
        return ConflictError(
            "Ebteqdesk was asked to attach an upload whose file has not "
            "arrived at its file store (409). NOTHING WAS ATTACHED and no note "
            "was written. The file has to be sent again before the upload can "
            "be completed."
            + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
            reason=reason,
            retry_after=retry_after,
        )

    if status_code == 409:
        return ConflictError(
            "Ebteqdesk refused this write because of the resource's current "
            "state (409), and did not say which kind of conflict it was. The "
            "only /api/v1 endpoints documented to answer 409 are the "
            "ticket-attachment ones, and they always name a `reason`; this "
            "body named none this client knows, so it is a proxy in front of "
            "Ebteqdesk or an endpoint newer than this client — read the "
            "server's own message below, which is the only thing that knows "
            "what conflicted. Retrying will not help unless that message says "
            "what to change. Note in particular that a PATCH of a PUBLISHED "
            "knowledge base article is NOT this: it no longer refuses, it "
            "stages a pending revision and answers 202."
            + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
            reason=reason,
            retry_after=retry_after,
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

    if status_code == 413 and (reason == REASON_PAYLOAD_TOO_LARGE or attachment_route):
        max_bytes = payload.get("max_bytes")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            max_bytes = None

        limit = (
            f"The limit is {max_bytes:,} bytes ({max_bytes / 1_048_576:g} MB). "
            if max_bytes
            else ""
        )

        return PayloadTooLargeError(
            "Ebteqdesk refused this file as too large (413). "
            + limit
            + "NOTHING WAS ATTACHED. There is nothing to retry: no argument "
            "makes the file smaller, and sending it again gets the same "
            "answer. Tell the user the limit and ask for a smaller file. Any "
            "accepted type may be up to 20 MB; only a VIDEO may be larger, up "
            "to 500 MB, and only where the desk has large uploads switched on."
            + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
            field_errors=field_errors,
            reason=reason,
            max_bytes=max_bytes,
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

    if status_code == 415 and (reason == REASON_UNSUPPORTED_MEDIA or attachment_route):
        # The desk's own list of what it accepts, quoted rather than restated:
        # a second copy of that list here would be a rule that drifts.
        accepted = " ".join(
            str(message) for messages in field_errors.values() for message in messages
        )

        return UnsupportedMediaError(
            "Ebteqdesk does not accept this file's type (415). "
            + (
                f"{accepted} "
                if accepted
                # The completion route refuses without listing anything: by
                # then the file is on the object store and its CONTENT was not
                # a video, whatever it was called.
                else "It was read after it arrived and its content is not a "
                "video the desk accepts, whatever its name says. "
            )
            + "NOTHING WAS ATTACHED, and nothing retries into success: the "
            "same file gets the same answer. 🔴 THE DESK CHECKS THE FILE'S "
            "CONTENT, AND FOR A DOCUMENT ITS EXTENSION AS WELL. An image or a "
            "video is recognised from what it contains; a document must have "
            "an extension on the list AND content that is what the extension "
            "says. So a file renamed to disguise what it is stays refused — "
            "do not rename one to get it past this, and do not tell the user "
            "that will work. The one rename that helps is the honest one: a "
            "plain-text log is accepted as `.txt` and refused as `.log`. A "
            "file over 20 MB is accepted only if it is a video, and needs a "
            "video extension. Tell the user which types are accepted and ask "
            "for the file in one of them."
            + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
            field_errors=field_errors,
            reason=reason,
        )

    if status_code == 415:
        mime_type = payload.get("mime_type")
        if not isinstance(mime_type, str) or not mime_type:
            mime_type = None

        if mime_type is not None and mime_type.startswith("image/"):
            # 🔴 An IMAGE type on a "not an image" refusal is not a
            # contradiction to paper over: the desk records the type from the
            # file's first bytes when it is attached, and refuses here when the
            # bytes then fail to DECODE. Seen on a running desk with a PNG whose
            # header was sound and whose data was not. The branch is on the
            # `mime_type` FIELD — a value — never on the sentence. Without it
            # the message below would read "this attachment is `image/png`, and
            # that endpoint returns images only".
            return UnsupportedMediaError(
                f"This attachment is recorded as `{mime_type}`, but Ebteqdesk "
                f"could not decode it as an image, so no image was returned "
                f"(415). The file is damaged, cut short, or is not really the "
                f"image its first bytes claim to be. NOTHING RETRIES INTO "
                f"SUCCESS HERE: a different `max_dimension` decodes the same "
                f"bytes. Tell the user the attachment could not be read as an "
                f"image and that the original can be downloaded in the browser "
                f"at its ticket — do not describe what it shows."
                + detail,
                status_code=status_code,
                path=path,
                server_message=server_message,
                mime_type=mime_type,
            )

        named = f"`{mime_type}`" if mime_type else "not an image"

        return UnsupportedMediaError(
            f"This attachment is {named}, and that endpoint returns images only "
            f"(415). Ebteqdesk accepts video and document attachments as well as "
            f"images, and their bytes are never served under an image content "
            f"type — a decode failure is the best case and a confidently wrong "
            f"reading of the wrong bytes is the worst. NOTHING RETRIES INTO "
            f"SUCCESS HERE: no argument turns a video or a document into an "
            f"image. Tell the user the file is {mime_type or 'not an image'} and "
            f"that it has to be opened in the browser at its ticket."
            + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
            mime_type=mime_type,
        )

    if status_code == 422 and reason == REASON_SIZE_MISMATCH:
        return InvalidRequestError(
            "The file that reached the desk's file store was not the size that "
            "was declared for it (422), so the desk discarded it. That is what "
            "a file changing while it is being sent looks like from the other "
            "end. NOTHING WAS ATTACHED and no note was written. Wait until "
            "nothing is still writing the file, then call the tool again."
            + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
            field_errors=field_errors,
            reason=reason,
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

    if status_code == 429 and attachment_route:
        # 🔴 Classified by STATUS, deliberately. A throttled request never
        # reaches a controller, so the body is the framework's
        # `{"message": "Too Many Attempts."}` with no `error` key and no
        # `reason` — there is nothing else to branch on, and nothing is needed.
        wait = f" Wait {retry_after}s first." if retry_after else ""
        return RateLimitedError(
            "Ebteqdesk is rate limiting file attachments for this account "
            "(429). The two routes that take a file have a limit of their own, "
            "per account and lower than the API's general one, so several "
            "files in quick succession reach it before anything else does. "
            "NOTHING WAS ATTACHED by this call. It is safe to make the same "
            "call again once the limit has passed — space further files out "
            "rather than sending them back to back."
            + wait
            + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
            retry_after=retry_after,
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

    if status_code == 501 and (reason == REASON_DIRECT_UNAVAILABLE or attachment_route):
        return DirectUploadUnavailableError(
            "Large videos are switched off on this Ebteqdesk. Files up to "
            "20 MB still work. Do not retry. (501 — this is how the desk says "
            "the feature is off, not a fault: nothing is broken and there is "
            "nothing in its logs to find.) NOTHING WAS SENT OR ATTACHED. A "
            "video over 20 MB goes straight to the desk's object store, and "
            "this deployment either has none or has that path turned off; "
            "only an administrator can change that. Tell the user the file is "
            "too large for this desk as it is configured — a version under "
            "20 MB can be attached now."
            + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
            reason=reason,
        )

    if status_code == 503 and reason == REASON_STORAGE_UNAVAILABLE:
        wait = f" Wait at least {retry_after}s first." if retry_after else ""

        return ServerError(
            "Ebteqdesk could not reach its file store to check the uploaded "
            "file (503), and still could not after this client waited and "
            "asked once more. NOTHING IS ATTACHED YET and no note was written. "
            "This is a fault on the Ebteqdesk side, not in the request or the "
            "file. Calling the tool again with exactly the same arguments is "
            "safe — the file is sent again and attached once, never twice."
            + wait
            + detail,
            status_code=status_code,
            path=path,
            server_message=server_message,
            reason=reason,
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
        reason=reason,
        retry_after=retry_after,
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


def escalated_comment_edit_error(error: ScopeError) -> ScopeError:
    """The narrowing for PATCH and DELETE /api/v1/tickets/{id}/comments/{comment_id}
    (5.3.0) — editing or deleting a PUBLIC reply.

    Those two routes run the POST route's own charge
    (Concerns\\WritesApiV1TicketReplies::ticketForPublicReply on the desk): the
    any-of floor `ticket:write|escalation:reply` on the route, then
    `escalation:reply` charged inside `if ($escalated)`. So, exactly as for
    `escalated_comment_error`, a refusal naming `escalation:reply` on this path
    means one thing and can mean nothing else: the ticket is escalated.

    🔴 A SEPARATE FUNCTION BECAUSE THE SENTENCE WOULD OTHERWISE BE ABOUT THE
    WRONG ACT. `escalated_comment_error` explains why REPLYING needs the scope;
    a user who was trying to fix a typo in an existing reply and is told about
    replying concludes they called the wrong tool. The rule is the same and is
    stated as the same — changing what the requester reads costs what saying it
    costs — but the act it names is the one that was refused.

    Anything that is not the `escalation:reply` refusal is returned untouched.
    """
    return _narrowed_escalation_error(
        error,
        "THIS TICKET IS ESCALATED, and editing or deleting a public reply on an "
        "escalated ticket needs the `escalation:reply` scope INSTEAD OF "
        "`ticket:write` — exactly what posting one costs, because changing or "
        "removing what the requester reads is speaking to the requester. The "
        "endpoint takes either scope, and an escalated ticket is charged the "
        "escalation one; that is the only way it can ask for `escalation:reply`, "
        "so this refusal tells you the ticket is escalated. "
        "Next time you can check first: every ticket payload carries an "
        "`escalated` boolean — read that, not the nullable `escalated_at`, and "
        "note that it stays true after the ticket is solved or closed, until "
        "somebody de-escalates it. "
        "`escalation:write` is NOT a substitute — that is the internal-note half "
        "of the same area, and it changes the NOTES on this ticket "
        "(`edit_private_note`, `delete_private_note`), never its replies. If the "
        "reply has to change and you cannot, ask whoever holds the escalation, "
        "or de-escalate the ticket first. NOTHING WAS CHANGED. "
        "Details of the `escalation:reply` refusal follow. ",
        scope=ESCALATION_REPLY_SCOPE,
    )


def escalated_note_edit_error(error: ScopeError) -> ScopeError:
    """The narrowing for PATCH and DELETE /api/v1/tickets/{id}/notes/{note_id}
    (5.3.0) — editing or deleting a PRIVATE note.

    Those two routes run the note POST's own charge
    (Concerns\\WritesApiV1TicketNotes::ticketForPrivateNote on the desk): the
    any-of floor `ticket:write|escalation:write`, then `escalation:write`
    charged inside `if ($escalated)`. A refusal naming `escalation:write` on
    this path therefore means the ticket is escalated.

    The honest reason is `escalated_note_error`'s — the ticket belongs to the BP
    queue and changing anything in its thread is a BP act — and NOT the
    reply narrower's: nothing a note edit does reaches the requester. Separate
    from `escalated_note_error` for the reason `escalated_comment_edit_error`
    gives: the sentence names the act that was refused.

    Anything that is not the `escalation:write` refusal is returned untouched.
    """
    return _narrowed_escalation_error(
        error,
        "THIS TICKET IS ESCALATED, and editing or deleting an internal note on "
        "an escalated ticket needs the `escalation:write` scope INSTEAD OF "
        "`ticket:write` — exactly what filing one costs. The endpoint takes "
        "either scope, and an escalated ticket is charged the escalation one; "
        "that is the only way it can ask for `escalation:write`, so this refusal "
        "tells you the ticket is escalated. "
        "Next time you can check first: every ticket payload carries an "
        "`escalated` boolean — read that, not the nullable `escalated_at`, and "
        "note that it stays true after the ticket is solved or closed, until "
        "somebody de-escalates it. "
        "The reason is NOT that anything would reach the requester — nothing "
        "here does. It is that an escalated ticket belongs to the BP queue, so "
        "changing anything in its thread is a BP act and needs the BP scope, "
        "exactly as the Ebteqdesk browser UI requires `bp_escalation.reply` for "
        "the same change. A key holding only `ticket:write` cannot change a note "
        "here however many times it is re-minted with that same scope. NOTHING "
        "WAS CHANGED. Details of the `escalation:write` refusal follow. ",
    )


def _narrowed_escalation_error(
    error: ScopeError,
    lead: str,
    *,
    scope: str = ESCALATION_WRITE_SCOPE,
) -> ScopeError:
    """PREPEND `lead` to an `escalation:write` refusal, preserving everything.

    The mechanism behind all four public narrowers above — the two for the POST
    routes and, since 5.3.0, the two for editing and deleting what those POSTs
    filed. Nothing is masked: the
    original message — including the key/role diagnosis, if one was reached, and
    the server's own sentence — survives intact underneath, and the CLASS is
    preserved so a `KeyScopeError` stays a `KeyScopeError` and
    `except RoleScopeError` still catches the role case.

    The branch is `required_scope == $scope`, a structured field compared to a
    constant — never prose parsing. Anything else is returned untouched, so a
    caller can hand every ScopeError through without a guard.

    🔴 $scope IS A PARAMETER BECAUSE THE NARROWERS BRANCH ON DIFFERENT SCOPES.
    A note — filing, editing or deleting one — costs `escalation:write`; a
    requester-facing reply — posting, editing or deleting one — costs
    `escalation:reply`. A hardcoded constant here would print one endpoint's
    explanation for the other's refusal, which is exactly the failure the
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
