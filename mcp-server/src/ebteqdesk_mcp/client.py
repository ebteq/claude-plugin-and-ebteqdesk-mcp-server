"""The HTTP half: one method per Ebteqdesk /api/v1 endpoint.

This is a pure REST client. It holds no database connection, imports nothing
from the Laravel application, and knows about Ebteqdesk only through the
endpoint paths below. It is
also the reference for what those endpoints return, so it PASSES PAYLOADS
THROUGH VERBATIM — no renaming, no flattening, no derived fields. See `_request`
for why that matters more here than it usually does.

⚠️ TWO METHODS READ A LOCAL FILE. `upload_kb_media` and, since 5.1.0,
`attach_file_to_ticket` are the only methods here that touch the caller's own
filesystem — each opens the path it is given and sends the bytes. Everything
else builds its request entirely out of its arguments, and the difference is a
risk to the USER'S MACHINE rather than to the desk. Both read ONLY inside the
directory named by `EBTEQDESK_UPLOAD_ROOT`, and nothing at all while that is
unset — one folder and one pair of checks (`_upload_root`,
`_inside_upload_root`) for both. See the block comments above those methods.

🔴 ONE REQUEST DOES NOT GO TO EBTEQDESK AT ALL. A video over 20 MB is PUT
straight to the desk's object store, at an address the desk signs for one
upload. That request is made by a SECOND HTTP client which has no base URL, no
default headers and no `Authorization` — the API token must never reach a third
party — and the address itself is a credential that never appears in a return
value, an exception or a log line. See the block comment above
`attach_file_to_ticket`.

⚠️ ONE ENDPOINT DOES NOT RETURN JSON. `get_ticket_attachment` returns image
BYTES, so it is the single method whose success path does not go through
`_request`. Its FAILURE path does, because Ebteqdesk answers every refusal on
that route with the same JSON envelope as everywhere else — see `_error_for`,
which exists so that the two body handlers share one classifier rather than the
binary path quietly missing the next thing added to the JSON one.

REQUESTS ARE BUILT THE SAME WAY, in the other direction: the JSON bodies below
use the API's own field names (`subject`, `description`, `requester`), and the
nested `requester` object is passed through with its shape intact rather than
flattened into `requester_id` / `requester_email` arguments. A request
vocabulary that differs from the documented one is the same drift problem as a
response vocabulary that does, and it costs a reader the ability to compare a
tool call against a curl.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import logging
import math
import mimetypes
import os
import re
import stat
import time
from dataclasses import dataclass
from datetime import date as _date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, AsyncGenerator, AsyncIterator, BinaryIO, Mapping, NamedTuple, Sequence
from urllib.parse import quote, urlsplit

import httpx2

from ._version import __version__
from .config import ENV_UPLOAD_ROOT, Config
from .errors import (
    MEDIA_UPLOAD_PATH,
    REASON_IN_PROGRESS,
    REASON_SIZE_MISMATCH,
    REASON_STORAGE_UNAVAILABLE,
    REASON_UPLOAD_INCOMPLETE,
    AbilityError,
    ApiError,
    ConfigurationError,
    ConflictError,
    EbteqdeskError,
    InvalidRequestError,
    LocalFileError,
    MalformedResponseError,
    ScopeError,
    ServerError,
    StalePortalApiError,
    TransportError,
    UploadTransferError,
    api_error_for,
    NotFoundError,
    diagnosed_ability_error,
    diagnosed_scope_error,
    escalated_comment_error,
    escalated_note_error,
    ticket_write_not_found,
)

__all__ = [
    "EbteqdeskClient",
    "AttachmentImage",
    "TicketAttachment",
    "TransferProgress",
    "MULTIPART_MAX_BYTES",
    "PUT_DEADLINE_SECONDS",
]

#: How much of a non-JSON body to quote back. Enough to recognise an nginx 502
#: or a Laravel error page; short enough that a 2 MB HTML page does not end up
#: in a chat transcript.
_SNIPPET_BYTES = 200

#: The identity endpoint. The only v1 route that needs no scope, which is what
#: makes it usable to diagnose a scope refusal — any valid key can reach it.
USER_PATH = "/api/v1/user"

#: The two tools that read a local file, as the upload-root messages name them.
#: One place, because every one of those messages has to say WHICH tools a
#: missing or unusable folder costs, and a sentence that named one of the two
#: would send a model to try the other.
_LOCAL_FILE_TOOLS = "upload_kb_media and attach_file_to_ticket"
_ONLY_LOCAL_FILE_TOOLS = f"Only {_LOCAL_FILE_TOOLS} are affected"

# --------------------------------------------------------------------------- #
# Ticket attachments — the numbers
# --------------------------------------------------------------------------- #

#: 🔴 THE ONE NUMBER THIS CLIENT HOLDS ABOUT WHAT A DESK ACCEPTS, and it does not
#: decide acceptance: it decides the ROUTE. A file of up to this many bytes is
#: sent through /api/v1 as multipart; one byte more and it is sent to the
#: object store, which the desk allows for video only. Every other rule — the
#: types, the 500 MB ceiling, whether large uploads are on at all — is the
#: desk's, answered by the desk, for the reason `upload_kb_media` gives.
MULTIPART_MAX_BYTES = 20_971_520

#: How long one PUT to the object store may run. The desk signs an upload
#: address for 60 minutes; this client stops at 50, so a body that is still
#: going when the address expires is never the way an upload ends. About
#: 1.4 Mbit/s sustained is what that allows for 500 MB.
PUT_DEADLINE_SECONDS = 50 * 60

#: ...or this long before the address's own `expires_at`, whichever is sooner.
_EXPIRY_MARGIN_SECONDS = 60

#: Bytes per read. The whole point of these two numbers is that neither is the
#: size of the file: a 500 MB video is never held in memory, on either route.
_PUT_CHUNK_BYTES = 256 * 1024
_PART_READ_CEILING_BYTES = 64 * 1024

#: The desk's `Retry-After` is 2 seconds for a request that is still running
#: and 5 for an object store it could not reach; these are used when the header
#: is absent. A header asking for longer than the ceiling is not obeyed —
#: nothing here may park a transfer for minutes on one response's say-so.
_IN_PROGRESS_WAIT_SECONDS = 2.0
_STORAGE_WAIT_SECONDS = 5.0
_WAIT_CEILING_SECONDS = 30.0

#: How many times "still running, try again shortly" is believed before it is
#: reported instead.
_IN_PROGRESS_MAX_WAITS = 15

#: The header the multipart route and the start route require.
_IDEMPOTENCY_HEADER = "Idempotency-Key"

#: What replaces the quoted body when the START route answers something that is
#: not JSON. Every other malformed reply quotes its first bytes so a user can
#: recognise a proxy page; this one reply can contain an upload address.
_WITHHELD = "<not shown: this reply can carry an upload address>"

#: The type DECLARED for a file sent to the object store, from its NAME — the
#: start route requires one before any byte has moved. It is a label for the
#: desk to refuse early, not a verdict: the desk reads the file's own content
#: once it has arrived. Python's built-in table only (never the host's
#: `/etc/mime.types` or registry, which differ by machine), plus the one video
#: extension the desk accepts that the table lacks.
_NAME_TYPES = mimetypes.MimeTypes()
_NAME_TYPES.add_type("video/x-m4v", ".m4v")

# --------------------------------------------------------------------------- #
# The upload address never reaches a log
# --------------------------------------------------------------------------- #
#
# 🔴 httpx2 LOGS EVERY REQUEST'S FULL URL AT INFO — `HTTP Request: PUT https://…
# "HTTP/1.1 200 OK"` — and the MCP SDK configures logging at INFO, to STDERR,
# when the server object is built. For a desk URL that line is harmless: the
# token is never in a URL. For the object-store PUT the URL IS the credential,
# so without this filter every large upload would print a working write address
# for the next hour into whatever collects the host's stderr.
#
# The filter drops records made WHILE THIS TASK IS INSIDE THE PUT, and only
# those: the marker is a context variable set around that one call, so the desk
# client's lines — and another task's — are untouched. It is attached to the
# HTTP stack's loggers by name because a filter on a logger does not apply to
# records logged on its children, and it is installed at import so there is no
# order in which a PUT can run before it.

_SENDING_TO_OBJECT_STORE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "ebteqdesk_mcp_sending_to_object_store", default=False
)

_HTTP_LOGGER_NAMES = (
    "httpx2",
    "httpcore2",
    "httpcore2.connection",
    "httpcore2.http11",
    "httpcore2.http2",
    "httpcore2.proxy",
    "httpcore2.socks",
)


class _UploadAddressLogFilter(logging.Filter):
    """Drop an HTTP-stack log record made during the object-store PUT."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not _SENDING_TO_OBJECT_STORE.get()


_UPLOAD_ADDRESS_LOG_FILTER = _UploadAddressLogFilter()

for _name in _HTTP_LOGGER_NAMES:
    logging.getLogger(_name).addFilter(_UPLOAD_ADDRESS_LOG_FILTER)

#: The Warni Activity vocabulary, copied from App\Reporting\ActivityReport
#: (`PERIODS`, `SORTS`), App\Reporting\ActivityTiers (`TIERS`, `MAX_SCORE`)
#: and App\Activity\ActivityInput (the numeric limits). These are CODE
#: constants on the server and the same for every caller, which is what makes
#: a local copy safe — see the block comment above `activity_list`.
ACTIVITY_PERIODS: tuple[str, ...] = ("day", "week", "month")
ACTIVITY_TIERS: tuple[str, ...] = ("active", "exploring", "dormant")
ACTIVITY_SORTS: tuple[str, ...] = ("score_desc", "score_asc", "name")
ACTIVITY_MAX_SCORE = 5
ACTIVITY_MAX_PER_PAGE = 100
ACTIVITY_QUERY_MAX = 100
ACTIVITY_DORMANT_DEFAULT_DAYS = 3
ACTIVITY_DORMANT_MAX_DAYS = 90

#: The default plan code of every read: `all`, "All businesses". Warni's API
#: exposes no plan, so the server's hourly pull files every business under it.
#: NOT a wildcard — it selects rows stored as `all`. The other legal code today
#: is `free`, the legacy code an old push used; which codes are legal is server
#: CONFIG (`config('activity.plans')`, first entry = default), so only the
#: default is copied here and a wrong code comes back as the server's 422.
ACTIVITY_DEFAULT_PLAN = "all"

#: The route constraint on `{business_id}` — App\Activity\ActivityInput::
#: BUSINESS_ID_PATTERN, `(?!\.\.?$)[A-Za-z0-9_.-]{1,50}`. Case is NOT checked
#: or folded here: the server lower-cases a UUID itself and keeps any other id
#: as written, so the id is sent exactly as given.
_ACTIVITY_BUSINESS_ID = re.compile(r"[A-Za-z0-9_.-]{1,50}")

_ACTIVITY_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class AttachmentImage(NamedTuple):
    """The one non-JSON response on this API: an attachment's image bytes.

    Every field but `data` and `mime_type` is read off an `X-Image-*` response
    header. They are metadata about what was ACTUALLY returned after
    downscaling, which is exactly the thing a caller cannot otherwise learn
    without decoding the image — and the reason they are carried here rather
    than recomputed is that recomputing them would mean this client growing an
    image library to answer a question the server already answered.

    They are Optional because a header can be absent — an intermediary that
    strips unknown headers, or an older server. A missing value is reported as
    unknown; it is never guessed, never defaulted to zero, and — see
    `downscaled` — never defaulted to False.
    """

    #: The image, decoded from the wire. Already downscaled by the server.
    data: bytes

    #: The content type of `data`, e.g. "image/png". Always an image/* type —
    #: anything else raised UnsupportedMediaError instead of reaching here.
    mime_type: str

    #: The dimensions of what was RETURNED.
    width: int | None = None
    height: int | None = None

    #: The dimensions of the STORED original, so a caller can report
    #: "2400x1800 -> 1568x1176" rather than only "reduced".
    source_width: int | None = None
    source_height: int | None = None

    #: The size of the STORED original. ⚠️ NOT a proxy for `downscaled` — see
    #: below. It is here for reporting, not for deriving anything.
    source_bytes: int | None = None

    #: Was the image reduced? Read from `X-Image-Downscaled`, never computed.
    #:
    #: 🔴 THIS MUST NOT BE DERIVED FROM THE BYTE SIZES, and that is not a style
    #: preference — it is the bug this field was rewritten to fix. Downscaling
    #: is a DIMENSION operation, and re-encoding a flat-colour screenshot at a
    #: different compression level routinely makes the FILE BIGGER while the
    #: PIXELS SHRINK: 2400x1800 / 23,960 bytes in, 1568x1176 / 55,577 bytes out,
    #: measured. `len(data) < source_bytes` answers False there, which is the
    #: opposite of the truth — and it tells an agent it already holds full
    #: fidelity, so it will not retry with a larger `max_dimension` and will
    #: guess at the text it cannot read instead.
    #:
    #: None means the server did not say (an absent or unparseable header). That
    #: is an honest "unknown" and must never be collapsed to False, which is a
    #: claim.
    downscaled: bool | None = None


@dataclass(frozen=True)
class TicketAttachment:
    """One local file, checked and ready to attach — nothing has been sent yet.

    What `prepare_ticket_attachment` returns. It exists as its own step because
    the MCP layer needs `key` BEFORE a transfer starts: the key is how a second
    identical call finds the transfer the first one began.
    """

    ticket_id: int

    #: The path as the user named it, made absolute. For messages only.
    file_path: str

    #: The name that is sent — the basename the USER gave, never the directory
    #: and never a symlink's target.
    name: str

    #: The resolved location. Every read uses this and nothing else.
    real_path: Path

    #: From `stat`, when the file was prepared. A transfer that finds either
    #: has moved abandons itself.
    size: int
    mtime_ns: int

    #: The note text, or None for the desk's default. Blank is None: the desk
    #: treats the two alike, so they must not make two keys.
    note: str | None

    #: The `Idempotency-Key`. See `_attachment_key`.
    key: str

    @property
    def direct(self) -> bool:
        """True when the file is too large for the API and goes to the object
        store. Decided by size alone."""
        return self.size > MULTIPART_MAX_BYTES


class TransferProgress:
    """How far one transfer has got. Written by the transfer, read by whoever
    reports on it — the MCP tool, while the transfer runs on without it.

    `snapshot()` is the `transfer` object a tool call returns when the file has
    not finished: the one shape in this package that Ebteqdesk did not send.
    """

    UPLOADING = "uploading"
    COMPLETING = "completing"

    def __init__(self, bytes_total: int, *, clock: Any = time.monotonic) -> None:
        self.state = self.UPLOADING
        self.bytes_sent = 0
        self.bytes_total = bytes_total
        #: Seconds the current send is allowed, from when it began. None on the
        #: multipart route, which has no upload address to outlive.
        self.deadline_seconds: float | None = None

        self._clock = clock
        self._started_at = clock()
        self._sending_since: float | None = None

    def begin_sending(self, allowance: float | None) -> None:
        """A send is starting, from byte zero — the first, or a fresh attempt."""
        self.state = self.UPLOADING
        self.bytes_sent = 0
        self.deadline_seconds = allowance
        self._sending_since = self._clock()

    def sent(self, count: int) -> None:
        self.bytes_sent += count

    def begin_completing(self) -> None:
        """Every byte is at the object store; the desk is being asked to attach."""
        self.state = self.COMPLETING
        self.bytes_sent = self.bytes_total

    def snapshot(self) -> dict[str, Any]:
        now = self._clock()

        eta: int | None = None
        if self.state == self.COMPLETING:
            eta = 0
        elif self._sending_since is not None and self.bytes_sent > 0:
            sending_for = now - self._sending_since
            if sending_for > 0:
                rate = self.bytes_sent / sending_for
                eta = math.ceil((self.bytes_total - self.bytes_sent) / rate)

        return {
            "state": self.state,
            "bytes_sent": self.bytes_sent,
            "bytes_total": self.bytes_total,
            "elapsed_seconds": int(now - self._started_at),
            # None until the first byte has gone: there is no rate to divide by,
            # and a guess here would be read out to a user as a promise.
            "eta_seconds": eta,
            "deadline_seconds": (
                None if self.deadline_seconds is None else int(self.deadline_seconds)
            ),
        }


class _SignedUpload:
    """The desk's answer to "start an upload", with the address kept out of sight.

    🔴 THE ADDRESS IS A WRITE CREDENTIAL UNTIL IT EXPIRES, so this object exists
    to make leaking it take deliberate effort. It has no `__dict__` and its
    `repr` shows no address, which matters because a traceback formatter that
    captures local variables prints the `repr` of every local in every frame —
    and a start response held as a plain dict would print the address in full.
    The raw string is handed out by `address()` and by nothing else, to the one
    function that puts it on the wire.
    """

    __slots__ = ("upload_id", "completed", "received_at", "expires_in", "_address", "_headers")

    def __init__(
        self,
        upload_id: str,
        *,
        completed: bool,
        received_at: float,
        address: str | None = None,
        headers: Mapping[str, str] | None = None,
        expires_in: float | None = None,
    ) -> None:
        self.upload_id = upload_id
        #: True when the desk says this upload was already attached: there is
        #: nothing to send, only a receipt to fetch.
        self.completed = completed
        #: This client's monotonic clock when the answer arrived.
        self.received_at = received_at
        #: Seconds, from `received_at`, until one minute before the address
        #: expires — measured on the DESK's clock. None if it did not say.
        self.expires_in = expires_in
        self._address = address
        self._headers = dict(headers or {})

    def __repr__(self) -> str:
        state = "completed" if self.completed else "pending"
        return f"<_SignedUpload {self.upload_id!r} {state}, address not shown>"

    def address(self) -> str:
        """The upload address. Call it inline, in the argument that sends it;
        never bind the result to a name."""
        assert self._address is not None
        return self._address

    def put_headers(self, size: int) -> dict[str, str]:
        """Exactly the headers the desk listed, plus `Content-Length`."""
        headers = {
            name: value
            for name, value in self._headers.items()
            if name.lower() != "content-length"
        }
        headers["Content-Length"] = str(size)

        return headers


class _PutOutcome(NamedTuple):
    """How one PUT ended. A value, because `_put_file` must not raise — see it."""

    ok: bool = False
    #: An `UploadTransferError` kind when the send was stopped on purpose or
    #: never begun: the deadline, the file changing. No fresh attempt follows.
    abandoned: str | None = None
    #: What the object store answered, if it answered.
    status_code: int | None = None
    #: The TYPE of the exception that ended it, never its message.
    cause_type: str | None = None


class _Abandon(Exception):
    """A reader stopping a send from inside it. Carries nothing and never
    leaves this module."""


class _StartAgain(Exception):
    """The desk's answer to "attach it" means the file has to be sent again.

    Carries the error to raise if that has already been tried once. Never
    leaves this module.
    """

    def __init__(self, final: EbteqdeskError) -> None:
        super().__init__()
        self.final = final


class EbteqdeskClient:
    """An async client for the Ebteqdesk v1 REST API.

    Use it as an async context manager, or call `aclose()` yourself::

        async with EbteqdeskClient(Config.from_env()) as client:
            me = await client.whoami()

    Every method returns the decoded JSON body EXACTLY as Ebteqdesk sent it and
    raises a `EbteqdeskError` subclass otherwise.
    """

    def __init__(
        self,
        config: Config,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        upload_transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config

        # The object-store client is built on first use — see `_upload_client`
        # — so a process that never sends a large file never builds a second
        # connection pool. `upload_transport` is its test seam, kept
        # SEPARATE from `transport` on purpose: a test that hands both clients
        # one fake can see which of them a request actually left through.
        self._upload_transport = upload_transport
        self._upload_http: httpx2.AsyncClient | None = None

        # The two things a transfer waits on, held here so a test can replace
        # them and prove the 50-minute deadline and the `Retry-After` pauses
        # without spending 50 minutes or any seconds at all.
        self._monotonic = time.monotonic
        self._sleep = asyncio.sleep

        # `transport` exists for the test suite, which drives the whole client
        # through httpx2.MockTransport. That is deliberately the ONLY seam: the
        # unit tests exercise this class's real header construction, real URL
        # building and real error mapping, and only the socket is fake. A test
        # double that replaced `_get` would prove nothing about any of that.
        self._http = httpx2.AsyncClient(
            base_url=config.base_url,
            timeout=httpx2.Timeout(config.timeout),
            transport=transport,
            headers={
                # The token goes here and ONLY here. Never in a query string:
                # query strings are logged by every proxy, appear in the
                # Referer header and end up in browser history.
                "Authorization": f"Bearer {config.token}",
                # Without this, Laravel's exception handler branches on
                # `expectsJson()` and a plain request can receive a Blade error
                # PAGE from a JSON endpoint — the trap the controllers in
                # app/Http/Controllers/Api/V1 document at length. Asking for
                # JSON explicitly keeps every failure inside the JSON contract
                # this client knows how to read.
                "Accept": "application/json",
                # Single-sourced from _version.py. A hardcoded literal here
                # had already drifted from the manifest once, which turns an
                # operator's "which client version is hammering /api/v1?" into
                # a wrong answer rather than no answer.
                "User-Agent": f"ebteqdesk-mcp/{__version__}",
            },
            # A redirect is not followed, on purpose. Ebteqdesk answers /api/v1
            # with JSON; a 302 means something in front of it (a login wall, a
            # captive portal, an http->https upgrade) intercepted the request.
            # Following it would turn that into a confusing HTML 200 instead of
            # a visible, diagnosable failure.
            follow_redirects=False,
        )

    @property
    def config(self) -> Config:
        return self._config

    async def __aenter__(self) -> "EbteqdeskClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

        if self._upload_http is not None:
            await self._upload_http.aclose()
            self._upload_http = None

    # ------------------------------------------------------------------ #
    # The thirteen READ endpoints
    # ------------------------------------------------------------------ #

    async def whoami(self) -> dict[str, Any]:
        """GET /api/v1/user — the identity and permissions behind the token.

        Response: ``{"data": {"id", "uuid", "name", "email", "role": {...},
        "permissions": [...], "apiKey": {...}}}``.

        `permissions` is the ROLE's ability list. `apiKey` describes the
        credential itself and carries the two lists that make a scope refusal
        diagnosable:

          - ``apiKey.requested`` — the scopes the KEY carries, wildcard expanded
          - ``apiKey.scopes``    — the key-∩-role intersection, what actually
            resolves right now

        A scope in `requested` but not in `scopes` is one the owner's role no
        longer backs. `apiKey` is null if the request was not authenticated by a
        bearer token.

        This is the only v1 route that needs no scope, which is precisely why
        `_diagnose_scope` can rely on it.
        """
        return await self._get(USER_PATH)

    async def list_tickets(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None,
        scope: str | None = None,
    ) -> dict[str, Any]:
        """GET /api/v1/tickets — tickets ASSIGNED to the token's account, or the
        whole account with ``scope="all"``.

        Visibility defaults to `tickets.user_id` — the assigned agent — and
        nothing else. This is not "all tickets" and not "tickets I raised".

        ``scope`` is passed straight through:

          - ``None`` or ``"mine"`` — the default, the token's own tickets. The
            two are identical by contract; "mine" is the explicit spelling.
          - ``"all"`` — every ticket in the account. Honoured ONLY for a token
            whose account holds the `ticket_all.view` ability (Administrator and
            Supervisor); for anybody else the server answers 403 with the
            reserved-`scope` message. It is OPT-IN by design, so an
            administrator's default list is unchanged and no existing
            integration silently starts receiving other people's rows.
          - anything else — 403. `scope` is a reserved parameter and unknown
            values are refused rather than ignored.

        ⚠️ Rows returned by ``scope="all"`` can include ESCALATED tickets
        belonging to other agents. That is header data only — no list endpoint
        on this API carries comment bodies — and reading one of those tickets
        with `get_ticket` still costs the `escalation:read` scope.

        `per_page` is 1..20, default 20. Out of range is a 422, NEVER a silent
        clamp, so it is passed through unvalidated here for the reason the KB's
        own `per_page` is: validating locally would invent a second,
        differently-worded rule and make this client disagree with curl. Page
        with `links.next` / `meta.last_page`; `links` carry `per_page` forward,
        so page 2 of a 5-row pull is still 5 rows.

        Every row carries `escalated` and `escalated_at` — see
        `list_escalations` for what those mean and which to trust.

        Requires the `ticket:read` scope.
        """
        return await self._get(
            "/api/v1/tickets", params=_list_params(page, per_page, scope)
        )

    async def list_tickets_by_category(
        self,
        category: str,
        *,
        page: int | None = None,
        per_page: int | None = None,
        scope: str | None = None,
    ) -> dict[str, Any]:
        """GET /api/v1/{category} — the same list, narrowed to one category.

        `category` is a `ticket_types.slug`, resolved at request time, so a
        category added to the database is reachable immediately with no deploy.
        An unknown slug is a 404 whose message DOES echo the slug (unlike the KB,
        below) — ticket category names are not secret.

        Same paging rules as `list_tickets`: `per_page` 1..20, default 20, 422
        above it. The category lives in the PATH, so it survives paging without
        being echoed into the query string.

        `scope` behaves exactly as it does on `list_tickets` — the alias is that
        endpoint with one filter applied, not a second endpoint, so "all" widens
        it on the same `ticket_all.view` ability and is refused with the same
        403 without it.

        Requires the `ticket:read` scope.
        """
        slug = category.strip().strip("/")

        # Refused here rather than sent, because an empty or multi-segment slug
        # would build a URL that hits a DIFFERENT route — `/api/v1/` is the
        # index, `/api/v1/escalations` is the queue and `/api/v1/kb/articles` is
        # the KB — and the caller would get a confusing success instead of an
        # error about their argument.
        if not slug or "/" in slug:
            raise ValueError(
                "category must be a single ticket-type slug such as 'bp-task', "
                f"not {category!r}."
            )

        return await self._get(
            f"/api/v1/{slug}", params=_list_params(page, per_page, scope)
        )

    async def get_ticket(
        self, ticket_id: int, *, thread_limit: int | None = None
    ) -> dict[str, Any]:
        """GET /api/v1/tickets/{id} — one ticket AND its conversation.

        200 ``{"data": <ticket detail>}``, plus a TOP-LEVEL
        ``"conversation_truncated": true`` when — and only when — `thread_limit`
        actually cut the thread.

        `<ticket detail>` is every field of a `list_tickets` row (one shared
        TicketResource renders both, and the detail resource SUBCLASSES it) plus
        `body`, `body_html`, `attachments`, `reference_number`, `summary`,
        `team`, `escalated_minutes` and `conversation`.

        🔴 THIS IS THE ONLY PLACE ON /api/v1 WHERE COMMENT BODIES ARE READABLE.
        The three ticket LISTS deliberately carry none — a list reachable by any
        `ticket:read` key must not be able to leak a held or private comment —
        and this endpoint is the bounded exception: one ticket at a time,
        addressed by id, resolved through the same visibility rule as everything
        else here.

        Each `conversation` entry carries `kind`, and `kind == "note"` means a
        PRIVATE INTERNAL NOTE. That distinction is a safety property, not a
        label: notes are staff-only and must never be repeated into a public
        reply, which is mailed to the requester's address.

        VISIBILITY IS WIDER THAN THE WRITE SURFACE'S, and the widening is
        charged for. The caller's own tickets need only `ticket:read`; a ticket
        that is somebody else's but sits on the SHARED escalation queue
        additionally needs `escalation:read`, checked at request time because
        route middleware cannot see that a ticket is escalated. A ticket that is
        neither is a 404 rather than a 403, so the id space stays
        un-enumerable — the write surface's rule, unchanged.

        `thread_limit` is 1..200 and keeps the NEWEST entries; out of range is a
        422, never a silent clamp, so it is passed through unvalidated here for
        the same reason `per_page` is.

        Requires EITHER `ticket:read` OR `escalation:read`; the resolved ticket
        decides which. Own ticket → `ticket:read`. On the shared BP queue →
        `escalation:read`. Any other ticket in the account → `ticket:read` plus
        the `ticket_all.view` ability. The two scopes never substitute for each
        other, and the 403 carries `required_scope` naming the one that failed.
        """
        params: dict[str, Any] = {}
        if thread_limit is not None:
            params["thread_limit"] = thread_limit

        return await self._get(
            f"/api/v1/tickets/{_ticket_id(ticket_id)}", params=params
        )

    async def get_ticket_comments(
        self,
        ticket_id: int,
        *,
        page: int | None = None,
        per_page: int | None = None,
    ) -> dict[str, Any]:
        """GET /api/v1/tickets/{id}/comments — the same conversation, paged.

        Same envelope as the ticket lists (`data` / `links` / `meta`) and the
        SAME per-entry object `get_ticket` embeds in `conversation`, so a client
        that can read one can read the other with no second parser.

        ⚠️ THE ENTRIES ARE NOT ONLY COMMENTS despite the path. History events
        (`kind: "event"`) are interleaved in chronological order, because the
        conversation IS the merge of comments and events and hiding half of it
        here would make the two endpoints disagree about what happened on the
        ticket. Oldest first, so page 1 is the start of the conversation.

        `per_page` is 1..20, default 20 — the same shared ceiling as the ticket
        lists, and above it is a 422 rather than a quietly smaller page.

        Same visibility and the same two scopes as `get_ticket`.
        """
        return await self._get(
            f"/api/v1/tickets/{_ticket_id(ticket_id)}/comments",
            params=_list_params(page, per_page),
        )

    async def get_ticket_attachment(
        self, attachment_id: int, *, max_dimension: int | None = None
    ) -> AttachmentImage:
        """GET /api/v1/attachments/{id} — an attached image, AS BYTES.

        🔴 THE ONE METHOD HERE THAT DOES NOT RETURN JSON. It returns an
        `AttachmentImage`, and its success path deliberately bypasses `_request`
        (which decodes JSON and would reject a PNG as malformed). Its failure
        path does not bypass anything: every refusal on this route is the usual
        JSON envelope and goes through the same `_error_for` classifier as every
        other endpoint.

        THE SERVER DOWNSCALES BEFORE RETURNING and that is the point of the
        endpoint rather than an optimisation. Ebteqdesk accepts uploads up to
        25 MB, and 25 MB of base64 is ~34 MB of an agent's context window — by
        the time a client holds the bytes the damage is done, so the resize
        happens server-side. `max_dimension` is the longest edge, 1..4096,
        default 1568; the aspect ratio is preserved and an image SMALLER than
        the ceiling is returned untouched rather than blown up.

        Three refusals are specific to this route and each has its own class:

          - 415 `UnsupportedMediaError` — the row is a VIDEO (Ebteqdesk accepts
            video attachments) or is otherwise not a decodable image. Never
            retryable; `mime_type` on the exception says what it actually is.
          - 413 `PayloadTooLargeError` — it could not be reduced under the
            response ceiling. Retryable with a smaller `max_dimension`, and the
            only one of the three that is.
          - 404 `NotFoundError` — unknown id, a file whose parent ticket this
            account cannot read, OR a row whose bytes are missing from disk. One
            body for all three, so the id space cannot be probed for which
            tickets carry files.

        Same visibility as `get_ticket`, applied through the file's PARENT
        TICKET — a comment's screenshot is authorised by the ticket the comment
        is on. So a file can never be reachable when its ticket is not.

        Requires EITHER `ticket:read` OR `escalation:read`, decided by the
        PARENT TICKET on exactly the rule `get_ticket` documents.
        """
        params: dict[str, Any] = {}
        if max_dimension is not None:
            params["max_dimension"] = max_dimension

        return await self._get_image(
            f"/api/v1/attachments/{_attachment_id(attachment_id)}", params=params
        )

    async def get_reports_summary(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, Any]:
        """GET /api/v1/reports/summary — the account-wide ticket report.

        200 ``{"data": {"range", "volume", "times", "quality"}, "meta": {...}}``.

        ⚠️ BOTH BOUNDS ARE INCLUSIVE INSTANTS AND NEITHER IS WIDENED. A bare
        ``to="2026-08-31"`` means ``created_at <= 2026-08-31 00:00:00`` and
        therefore EXCLUDES that whole day's tickets. That is not this endpoint's
        invention — it is exactly what the /reports web page does with the same
        strings, and widening it here would make the API and the page disagree
        about the same range. Pass ``to="2026-08-31T23:59:59"`` for a full day.
        `data.range` echoes the instants actually measured.

        🔴 DIFFERENT FROM `get_escalation_report`, WHICH DOES WIDEN. That one
        takes `from`/`to` to the start and END of their days. Two report
        endpoints, two range conventions, and the difference is deliberate on
        the server: each matches the web page it mirrors. Do not carry an
        assumption from one to the other.

        Omitting a bound defaults it to the CURRENT CALENDAR MONTH, not to all
        time — again the web page's own default. A reversed range is a 422.

        UNITS: `volume.*` are counts (integer, never null). `times.*` are
        MINUTES (float). `*Percent` are on 0..100, not 0..1. `averageRating` is
        1..5 stars. Every field under `times` and `quality` is NULLABLE and null
        means NO DATA — not zero, and emphatically not 0%.

        GATED TWICE, AND BOTH GATES ARE LIVE. Requires the `reports:read` scope
        (backed by `reports.view`) AND the `admin.access` ability, checked by
        the controller. That second check is genuinely reachable, unlike its
        neighbours': five of the seven numbers here are admin-only cells on the
        web page, and roles such as Supervisor hold `reports.view` without
        `admin.access`. So a key whose `reports:read` resolves perfectly can
        still be refused, with a body naming `admin.access` — an
        `AbilityError`, whose remedy is an administrator and never a new key.
        """
        params: dict[str, str] = {}
        if date_from:
            params["from"] = date_from
        if date_to:
            params["to"] = date_to

        return await self._get("/api/v1/reports/summary", params=params)

    async def get_kb_article_review(self, reference: str) -> dict[str, Any]:
        """GET /api/v1/kb/articles/{reference}/review — the verdict, unchanged.

        200, `{"data": …, "revision": …|null}`. `data` is the SAME shape the two
        KB writes return, including the `review` block: `state`,
        `requested_at`, `reviewed_at`, `reviewed_by` and `note` (the rejection
        reason).

        🔴 TWO REVIEW RECORDS, AND THEY ARE NOT THE SAME QUESTION. `data.review`
        is the verdict on the ARTICLE — what a proposed draft goes through, and
        typically `state: "none"` on an article published long ago. `revision`
        is the verdict on an edit STAGED against a published article by a 202
        from `update_kb_article`, and carries the same fields plus `source`
        (`api` | `manual`).

        Unlike the writes, this endpoint emits `revision` UNCONDITIONALLY — null
        when the article has no revision row. `written()` on the write path
        never emits the key at all, which is what makes its presence there a
        200/202 discriminator; here it is always present and its VALUE is the
        signal.

        🔴 `revision.state` IS NEVER `"approved"`. Approving a revision applies
        it and DELETES the row, so the three readings are: `pending` (waiting),
        `rejected` (refused, `note` is the reason), and `null` — which is
        AMBIGUOUS between "nothing was ever staged" and "a staged edit was
        approved and applied". Nothing in this payload separates those two;
        `data`'s own text does, because it is the live article.

        ⚠️ THIS IS ALSO THE ONLY IDEMPOTENT WAY TO READ A REJECTED REVISION'S
        NOTE. There is one revision row per article, so a PATCH replaces it —
        including a rejected one — and the note goes with it.

        🔴 READING THE VERDICT USED TO DESTROY IT. Before this route existed,
        `review_state` was readable on no API surface, so an integration could
        learn its article had been rejected only by PATCHing it again — and a
        PATCH re-queues the article and clears the very note it was trying to
        read. Checking the state destroyed the state. This endpoint has NO side
        effects at all; use it, and never a speculative PATCH, to poll a verdict.

        ⚠️ `kb:write`, NOT `kb:read`, even though this only reads. The corpus
        here is every article including drafts — the same corpus PATCH can
        address — while `kb:read` is deliberately the scope with no role side at
        all, gating the PUBLIC help corpus. Mounting a draft read on `kb:read`
        would hand the public-corpus scope the entire knowledge base.

        `{reference}` is the frozen slug or the `id:<n>` form — see
        `update_kb_article` for why an API-created article normally has no slug.
        """
        return await self._get(
            f"/api/v1/kb/articles/{_kb_reference(reference)}/review"
        )

    async def list_kb_proposals(
        self,
        *,
        review_state: str | None = None,
        per_page: int | None = None,
        page: int | None = None,
        portal: str | None = None,
    ) -> dict[str, Any]:
        """GET /api/v1/kb/proposals — every held, approved and rejected article.

        200 ``{"data": [...], "links": {...}, "meta": {...}}``. Each row is the
        write shape MINUS the body: `id`, `reference`, the identity block,
        `excerpt`, `status`, `source`, and the same `review` block
        `get_kb_article_review` returns — byte for byte, one resource renders
        both, so no second parser.

        🔴 THE GAP THIS CLOSES IS DISCOVERY, AND ONLY DISCOVERY.
        `get_kb_article_review` could only be asked about an article whose
        `reference` the caller still held, and a `reference` appears in exactly
        one place — the create response. An integration restarted with no memory
        had no way to ask "which of my proposals were rejected, and why"; its
        only remaining move was a speculative PATCH per article, which is the
        exact thing that read exists to avoid.

        ⚠️ INSTALLATION-WIDE. IT IS NOT "your" proposals. The corpus is every
        article carrying a review state, whoever created it and whatever its
        `source` — it is NOT filtered by the caller. Two reasons the server
        cannot do better: `source` is immutable creation provenance, so
        filtering on it would hide rejections on human-written articles an
        integration later revised; and `author_id` is the KEY OWNER, a real
        human, written identically by the web authoring UI and not moved by a
        PATCH. Recognise your own rows by `title` or `reference`.

        `review_state` is exactly ONE of `pending`, `approved` or `rejected`,
        or omitted for all three. 🔴 `none` IS A 422, not an empty list: it is
        the entire hand-written corpus, and the endpoint refuses rather than
        becoming a full-table walk wearing a review filter.

        Ordered `review_requested_at` DESC with `id` as the tie-break — newest
        submission first, deliberately the reverse of the human review queue's
        oldest-first fairness order. This is a polling client asking what
        happened to what it just submitted, so the answer is on page one.

        `per_page` is 1..100, default 25. Out of range is a 422, never a silent
        clamp, so it is passed through unvalidated for `search_kb_articles`'s
        reason: clamping locally would hide a caller's mistake and make this
        client disagree with curl.

        `portal` — `"salonv3"`, `"warni"` or `"all"` — narrows the queue to one
        knowledge base or interleaves both, OMITTED-MEANS-`"salonv3"` for the
        same reason `search_kb_articles`'s is. Its rows nest a `{slug, name}`
        `category` pair with no `portal` field, upgraded install or not, so —
        same caveat as `search_kb_articles` — this method has no local signal
        for an install that silently ignored the filter; verify with
        `list_kb_tree(portal=...)` first if that matters here.

        ⚠️ `kb:write`, NOT `kb:read`, even though this only reads — and it
        writes nothing at all. Same reasoning as `get_kb_article_review`: the
        corpus is drafts, the corpus PATCH addresses, while `kb:read` is
        deliberately the scope with no role side and gates the PUBLIC help
        corpus. Mounting a draft LIST on it would hand that scope an enumeration
        over the whole knowledge base.
        """
        params: dict[str, Any] = {}
        if review_state is not None:
            params["review_state"] = review_state
        if per_page is not None:
            params["per_page"] = per_page
        if portal is not None:
            params["portal"] = portal
        params.update(_page_params(page))

        return await self._get("/api/v1/kb/proposals", params=params)

    async def list_escalations(
        self, *, page: int | None = None, per_page: int | None = None
    ) -> dict[str, Any]:
        """GET /api/v1/escalations — the SHARED business-partner queue.

        🔴 THE ONE TICKET LIST ON THIS API THAT IS NOT OWNERSHIP-SCOPED. Every
        other ticket route — `list_tickets`, the category aliases, and every
        write — resolves through `tickets.user_id = caller`. This one returns
        every unresolved escalated ticket in the INSTALLATION, whoever it is
        assigned to, including tickets assigned to nobody. That is what the BP
        queue is: escalation is work handed off, and the rows most needing
        attention are exactly the unassigned ones. Read `assignee` to see which
        rows are the caller's own.

        Same envelope and the same per-ticket object as `list_tickets`, byte for
        byte — one `TicketResource` renders both, so no second parser.

        Rows are `level = 1 AND status IN openStatuses()`, ordered
        `escalated_at ASC NULLS LAST` with `id` as the tiebreak. Two
        consequences worth carrying to a caller:

          - A SOLVED escalated ticket is absent entirely, because solving takes
            a ticket off the BP queue. Disappearance from this list is therefore
            NOT evidence an escalation was resolved — fetch the ticket.
          - A ticket escalated before `escalated_at` existed sorts LAST despite
            being the oldest. Its stamp is null, and null is "unknown", not "the
            dawn of time".

        `per_page` is 1..20, default 20; above it is a 422.

        Requires the `escalation:read` scope and the `bp_escalation.view`
        ability. Until this route existed `escalation:read` gated nothing.
        """
        return await self._get(
            "/api/v1/escalations", params=_list_params(page, per_page)
        )

    async def get_escalation_report(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, Any]:
        """GET /api/v1/reports/category-metrics — per-category escalation report.

        Both bounds are optional ISO 8601 dates or date-times; `date_from` is
        widened to the start of its day and `date_to` to the end of its day.
        Omitting both means all time. A reversed range is a 422, never a silent
        full-range fallback.

        THREE CONTRACT RULES apply to the numbers and are restated on the MCP
        tool, because a client that ignores them renders wrong figures the
        payload cannot flag. See `server.py`.

        Row identity is `key`, NOT `id` or `slug`: both of those are null on the
        "Uncategorised" bucket.

        Requires the `escalation-reports:read` scope, which is backed by the
        `bp_escalation.view` role ability — so a refusal here can come from
        either half. `_diagnose_scope` works out which.
        """
        params: dict[str, str] = {}
        if date_from:
            params["from"] = date_from
        if date_to:
            params["to"] = date_to

        return await self._get("/api/v1/reports/category-metrics", params=params)

    async def search_kb_articles(
        self,
        *,
        query: str | None = None,
        per_page: int | None = None,
        page: int | None = None,
        portal: str | None = None,
    ) -> dict[str, Any]:
        """GET /api/v1/kb/articles — the public KB corpus, optionally searched.

        `query` is matched against title and body. An empty or whitespace-only
        query means "no search" (the whole corpus, newest first), not "match
        nothing" — that is the server's rule and it is not second-guessed here.

        `per_page` is 1..100, default 25. Out of range is a 422, never a silent
        clamp, so the value is passed through unvalidated on purpose: clamping
        it locally would hide a caller's mistake and make this client disagree
        with curl.

        `portal` — `"salonv3"`, `"warni"` or `"all"` — narrows the corpus to one
        knowledge base or interleaves both. 🔴 OMITTING IT MEANS `"salonv3"`,
        NOT `"all"`, so a caller written before portals existed keeps its old
        behaviour unchanged. An unrecognised value is a 422 naming the field.

        ⚠️ THIS METHOD DOES NOT GUARD AGAINST AN INSTALL THAT IGNORES `portal`.
        Unlike `list_kb_tree`, nothing in this corpus's row shape carries a
        `portal` field — `data[].category` is the same `{slug, name}` pair it
        has always been — so there is no local signal to tell "filtered to one
        portal" apart from "the install silently returned both". Call
        `list_kb_tree(portal=...)` once to confirm the install understands
        `portal` before trusting a filtered read here.

        The corpus is published, publicly-visible articles ONLY, even for an
        administrator's token. Internal runbooks readable in the web KB are not
        served here, by design.

        🔴 "PUBLICLY VISIBLE" IS RESOLVED PER ARTICLE, NOT PER FOLDER (ADR-0007).
        `kb_articles.visibility` is nullable and overrides `kb_folders.visibility`
        in both directions:

            effective = coalesce(article.visibility, folder.visibility)

        So a folder listed as `agents` by `list_kb_tree` may hold one article
        this corpus serves, and a `public` folder may hold one it does not. There
        is no second query here doing that resolution — the server does it inside
        `KbReadQuery`, which is the only read path — but any code or prose that
        infers an ARTICLE's reachability from its FOLDER's level is now wrong.

        ⚠️ `data[].folder` is null for an article whose folder is not itself
        publicly visible, even though the article is. The server withholds the
        name rather than disclosing an internal section title on a public
        surface; `category` is still present.

        Requires the `kb:read` scope.
        """
        params: dict[str, Any] = {}
        if query is not None and query.strip():
            params["q"] = query.strip()
        if per_page is not None:
            params["per_page"] = per_page
        if portal is not None:
            params["portal"] = portal
        params.update(_page_params(page))

        return await self._get("/api/v1/kb/articles", params=params)

    async def get_kb_article(
        self, slug: str, *, locale: str | None = None, portal: str | None = None
    ) -> dict[str, Any]:
        """GET /api/v1/kb/articles/{slug} — one article with its body.

        `data.body_html` is SANITISED HTML, not markdown — `kb_articles.body`
        stores HTML and there is no markdown form of it anywhere in Ebteqdesk.

        Same corpus and same effective-visibility rule as `search_kb_articles`,
        including the null `folder` on an article that overrides an internal
        folder. See that method.

        `portal` narrows which knowledge base's slug namespace is searched —
        `"salonv3"`, `"warni"` or `"all"` — and is OMITTED-MEANS-`"salonv3"` for
        the same reason `search_kb_articles`'s is. Article slugs are unique
        GLOBALLY across both portals (unlike category slugs, which are now
        unique only per portal — see `create_kb_category`), so `portal` narrows
        which installs' 404 you get more than which article you find; it exists
        for symmetry with the other three reads. Same caveat as
        `search_kb_articles`: this row shape carries no `portal` field either,
        upgraded install or not, so this method cannot detect a stale one on
        its own — verify with `list_kb_tree(portal=...)` first.

        🔴 The 404 is BYTE-IDENTICAL for a hidden article and for a slug that
        never existed, and does not echo the slug back. That is deliberate: an
        echoing or differentiated 404 would turn this route into an enumeration
        oracle over draft and internal article titles. Do not "improve" the
        error by adding the slug to it, here or in a caller.

        ------------------------------------------------------------------
        🔴 `locale` — AND WHAT ITS ABSENCE MEANS, WHICH IS NOT `en`
        ------------------------------------------------------------------
        An Ebteqdesk article may carry its own text per language, and one that
        does is served from that text rather than from the base columns. It also
        appears ONLY in the languages it has a version for.

        WITH a locale (`"en"` or `"zhcn"`), this is the article as a READER of
        that language sees it: `title`, `body_html` and `seo` come from that
        language's version, and an article that has no version in it is a 404 —
        the same answer the public help centre gives, which is the point.

        WITHOUT one, this is the LOCALE-FREE MACHINE CORPUS. It includes articles
        that exist in only one language, and for those it returns the BASE
        columns — which for a Chinese-only article is the pre-translation English
        an author left behind. That is not a fallback bug and not an error: the
        corpus a machine reads to answer a question is deliberately wider than
        one language's slice of it.

        So: to VERIFY something you wrote in a language, pass that locale. To
        search or read the whole corpus, do not. Neither is a default for the
        other, and omitting the argument behaves exactly as this method did
        before per-language content existed.

        ⚠️ The locale is NOT validated here — the server enumerates the
        supported set and 422s on anything else, including `zh-cn`, which is a
        real locale string elsewhere in Ebteqdesk and is not this one.

        Requires the `kb:read` scope.
        """
        clean = slug.strip().strip("/")

        if not clean or "/" in clean:
            raise ValueError(
                "slug must be a single KB article slug such as "
                f"'resetting-your-password', not {slug!r}."
            )

        params: dict[str, str] = {}
        if locale is not None:
            params["locale"] = locale
        if portal is not None:
            params["portal"] = portal

        return await self._get(f"/api/v1/kb/articles/{clean}", params=params)

    async def list_kb_tree(self, *, portal: str | None = None) -> dict[str, Any]:
        """GET /api/v1/kb/tree — the category → folder structure, WITH IDS.

        200 ``{"data": [{"id", "name", "slug", "description", "position",
        "portal", "folders": [{"id", "kb_category_id", "name", "slug",
        "description", "visibility", "position", "articles_count"}]}]}``.
        Ordered `position, id` at both levels; `folders` is always present and
        is `[]` for a category with none.

        🔴 THIS IS THE ONLY SOURCE OF `kb_folder_id`. `propose_kb_article`
        requires one and NOTHING else on this API returns a folder id — the
        article payloads carry `{slug, name}` pairs and no id anywhere. Before
        this route the KB write surface was unusable against a knowledge base
        whose structure the caller could not see, and unusable at all against an
        empty one.

        🔴 THE TREE SPANS TWO KNOWLEDGE BASES, SALON V3 (`"salonv3"`) and WARNI
        (`"warni"`), and every category carries which one it belongs to in its
        own `portal` key — `folders` and the articles filed under them inherit
        it transitively and do not repeat it. Because `kb_folder_id` is the one
        thing `propose_kb_article` can never change afterwards, PICKING A
        FOLDER OFF THIS TREE PICKS A PORTAL, permanently. Read a category's
        `portal` before handing its folders to a caller who has not said which
        product they mean.

        `portal` — one of `"salonv3"`, `"warni"` or `"all"` — filters which
        categories come back. 🔴 OMITTING IT IS NOT `"all"`: it means
        `"salonv3"`, so every caller written before portals existed keeps
        seeing exactly what it always did. Passing `"all"` INTERLEAVES the two
        portals' categories rather than concatenating them, because `position`
        is dense per portal — both commonly have a category at `0` — so do not
        assume a portal's categories are contiguous in the returned list; group
        by `portal` if you need them separated. An unrecognised value is a 422
        naming the field, not a silent fall-through to `"salonv3"`.

        🔴 IF `portal` WAS PASSED AND THE CATEGORIES CAME BACK WITH NO `portal`
        KEY AT ALL, this raises `StalePortalApiError` instead of returning
        them. That shape means the Ebteqdesk install predates two-knowledge-base
        support and SILENTLY IGNORED the filter — Laravel drops a query
        parameter no route declares rather than rejecting it — so what came
        back is BOTH portals unfiltered, not the one that was asked for. See
        that exception; the check costs nothing extra, since it reads the
        response this method already fetched.

        ⚠️ NOT VISIBILITY-FILTERED, unlike `search_kb_articles`. It returns
        `agents`-only folders, because the bound on this surface is the SCOPE and
        not the query: a `kb:write` key's owner already sees the whole structure
        in the browser. Folder and category NAMES here are therefore internal
        organisation, not copy for anywhere outside the desk.

        No paging: the KB is a few hundred rows of structure at most, and a page
        cursor over it would cost a caller a loop and buy nothing.

        ⚠️ `kb:write`, not `kb:read`, for the same reason
        `get_kb_article_review` is — see that method. `kb:read` gates the PUBLIC
        help corpus and has no role side at all.

        The path is `/kb/tree` and deliberately NOT `/kb/categories`, which the
        server reserves for a future PUBLIC read tree on `kb:read`. Two routes at
        one path would be decided by registration order and one of them would be
        silently dead.
        """
        params = {} if portal is None else {"portal": portal}
        data = await self._get("/api/v1/kb/tree", params=params)

        if portal is not None:
            categories = data.get("data") or []
            if categories and any("portal" not in category for category in categories):
                raise StalePortalApiError(portal)

        return data

    # ------------------------------------------------------------------ #
    # The nineteen WRITE endpoints
    # ------------------------------------------------------------------ #
    #
    # All nineteen CHANGE STATE on a live helpdesk that real agents and real
    # requesters are looking at, and none of them can be undone from this API:
    # there is no delete-ticket, no delete-comment, no delete-note, no
    # delete-article and no delete-media endpoint. (The two structure DELETEs
    # below are the exception that proves it — they remove a category or a
    # folder and nothing puts one back.) Two
    # consequences are baked into the code rather than left to the caller.
    #
    #   1. NOTHING IS RETRIED. The read half already refuses to retry (a silent
    #      retry turns a rate limit into an unexplained slow response); on the
    #      write half the stakes are higher, because a POST that timed out may
    #      well have succeeded, and retrying it files a second ticket or a
    #      second reply mailed to the requester. `follow_redirects=False` matters here for
    #      the same reason — httpx would replay a POST body at a new location.
    #
    #      ⚠️ THE THREE REORDER ENDPOINTS ARE ONE EXCEPTION. They assign
    #      positions by INDEX, so replaying the same body is a no-op that
    #      answers 200 with the same list.
    #
    #      ⚠️ PUT /tickets/{id}/status AND PUT /tickets/{id}/category ARE THE
    #      OTHERS, AND THE ONLY ONES ON THE TICKET SURFACE. The server guards
    #      the no-op case on both, so sending a status or a type the ticket
    #      already holds writes nothing and still answers 200. Nothing here
    #      retries any of them automatically — the caller may, the client will
    #      not.
    #
    #   2. THERE IS NO DRY RUN, deliberately. The server has no dry-run mode, so
    #      a client-side one could only describe the request it WOULD send. It
    #      would validate nothing, consult no policy, and cheerfully report
    #      success for a call the server would refuse — a guardrail that reads
    #      like one and is not. What is here instead is the side effect stated
    #      unmissably in each MCP tool's description (server.py), which is the
    #      text a model actually reads before deciding to call.
    #
    # AND ONE THING TO EXPECT BACK. The ones that take a {ticket} answer 404 for
    # a ticket that does not exist AND for one that does but is not assigned to
    # the caller — with ONE exception. A ticket on the SHARED escalation queue
    # (the rows GET /api/v1/escalations serves), asked for by a caller that could
    # have read that queue, comes back 403 with `reason: "ticket_not_assigned"`
    # instead, because pretending a row this API just served does not exist is
    # the API contradicting itself. `errors.TicketNotAssignedError` is that case;
    # it is the one 403 no credential can fix and the one that must never be
    # retried. Everything else keeps the indistinguishable 404.

    async def create_ticket(
        self,
        *,
        subject: str,
        description: str,
        requester: Mapping[str, Any],
        priority: int | None = None,
        status: int | None = None,
        category: str | None = None,
        reference_number: str | None = None,
        tags: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/tickets — file a new ticket. 201 ``{"data": <ticket>}``.

        `<ticket>` is the same shape as an element of GET /api/v1/tickets: the
        server renders both through one TicketResource, so a client never needs
        a second parser for the thing it just created.

        `requester` names THE REQUESTER — on this internal desk, the member of
        staff who raised the ticket — and is required, in one of two forms:

            {"id": 12}                                an existing contact, by id
            {"email": "ada@example.com", "name": "…"} found, or created

        The email form is find-or-create matched on the address, which is the
        identity column. `name` is applied ONLY when the contact is inserted —
        the server will not rename an existing contact because an API client
        mistyped a name next to a known address. `id` wins if both are sent.

        Two fields are NOT client-controllable and are absent by design: the
        source is always "api", and the assignee is always the token's own
        account. That second one is not a limitation to work around — this
        surface shows you only tickets assigned to you, so a ticket created
        with anyone else as assignee would be invisible to its creator on the
        very next request.

        Optional fields are OMITTED from the body when None rather than sent as
        null, so the server applies its own documented defaults (priority 2 /
        normal, status 1 / new, no category, no reference, no tags) instead of
        this client having a second, drifting copy of them.

        `category` is a ticket-type SLUG and is the ONLY way to set the type on
        create. The server refuses the alias keys `type`, `ticket_type` and
        `ticket_type_id` with a 422 naming `category`; this method has no
        parameter for any of them and never sends one. To change the type
        afterwards, `set_ticket_category`.

        Requires the `ticket:write` scope and the `ticket.create` ability.
        """
        body: dict[str, Any] = {
            "subject": subject,
            "description": description,
            # Passed through with its shape intact. Its contents are NOT
            # validated here: an empty or contradictory requester is a 422 that
            # names the field, which is more useful than a locally invented
            # message, and it keeps this client agreeing with curl.
            "requester": dict(requester) if isinstance(requester, Mapping) else requester,
        }

        optional = {
            "priority": priority,
            "status": status,
            "category": category,
            "reference_number": reference_number,
            "tags": list(tags) if tags is not None else None,
        }
        body.update({key: value for key, value in optional.items() if value is not None})

        return await self._request("POST", "/api/v1/tickets", json=body)

    async def comment_on_ticket(self, ticket_id: int, *, body: str) -> dict[str, Any]:
        """POST /api/v1/tickets/{id}/comments — reply to the requester.

        201 ``{"data": <ticket>, "comment": {"id": int|null, "created_at": …}}``.

        THE REPLY IS PUBLIC AND REQUESTER-FACING: it is mailed to the
        requester's address and shown on their portal page. There is no
        private/internal flag on this endpoint — none, on an internal desk or
        anywhere else; writing internal notes over the API is not part
        of the surface.

        `comment.id` is null in exactly one case, inherited from the model: a
        body identical to the author's stored signature is discarded. The
        ticket is still touched, and no comment row is written. A null id there
        means the reply was NOT filed — do not report it as sent.

        🔴 TWO SCOPES, AND THE SECOND ONE DEPENDS ON THE TICKET. Requires
        `ticket:write` always, PLUS `escalation:write` when the ticket is
        escalated. Since M2-T9 that IS checkable in advance — every ticket
        payload carries `escalated` — so the refusal is no longer the only
        signal, but it is still the one a caller who did not look will meet.
        `escalated_comment_error` turns it into a sentence naming the cause and
        pointing at the field; see that function for why matching that one
        scope name is sound.
        """
        return await self._request(
            "POST",
            f"/api/v1/tickets/{_ticket_id(ticket_id)}/comments",
            json={"body": body},
            # The only place a scope error is re-narrowed after diagnosis, and
            # the only place it CAN be: the context that makes the refusal
            # explicable is which endpoint was called, which the transport does
            # not know and the exception does not carry.
            on_scope_error=escalated_comment_error,
        )

    async def add_private_note(self, ticket_id: int, *, body: str) -> dict[str, Any]:
        """POST /api/v1/tickets/{id}/notes — file an INTERNAL note.

        201 ``{"data": <ticket>, "comment": {"id": int|null, "created_at": …}}``
        — the same receipt `comment_on_ticket` returns, key for key, so a caller
        parses both with one branch.

        🔴 THIS IS THE SAFE COUNTERPART TO `comment_on_ticket`. The row is
        `private`, it is visible to agents in the Ebteqdesk UI and in
        `get_ticket`'s thread as `kind: "note"`, and NO EMAIL IS SENT TO THE
        REQUESTER. That is a property of the row and not of this method: the
        server's Comment.notifyNewComment() skips the requester's contact
        precisely because the comment is private, so the same fan-out still notifies the
        team, the assignee and anyone `@`-mentioned in the body.

        ⚠️ IT STILL CANNOT BE EDITED OR DELETED THROUGH THIS API. There is no
        delete-comment endpoint and no note-editing one — "internal" is not
        "reversible". Nothing here retries, for the reason the block comment
        above `create_ticket` gives.

        `comment.id` is null in exactly one case, inherited from the model: a
        body that is empty in substance is discarded and no comment row is
        written. The ticket is still touched and the call still answers 201. A
        null id means the note was NOT filed — do not report it as recorded.

        🔴 TWO SCOPES, AND THE SECOND ONE DEPENDS ON THE TICKET, exactly as on
        `comment_on_ticket`: `ticket:write` always, PLUS `escalation:write` when
        the ticket is escalated. `escalated_note_error` turns that refusal into a
        sentence naming the cause — a DIFFERENT sentence from the comment
        endpoint's, because the reason differs; see that function.

        ⚠️ AND A THIRD, ON ONE PATH ONLY. This endpoint's visibility rule is the
        READ-DETAIL one, not the write surface's: own ticket, else the SHARED BP
        queue. Reaching a BP-queue ticket that is not the caller's own
        additionally costs `escalation:read`, the same scope `get_ticket` needs
        for the same row. So this is the one write on the ticket surface that can
        touch a ticket `list_tickets` does not show.

        No `private` parameter, here or on `comment_on_ticket`: the two are
        separate paths on purpose. A flag would make the requester-facing
        behaviour the default of a shared path, reachable by forgetting a key.
        """
        return await self._request(
            "POST",
            f"/api/v1/tickets/{_ticket_id(ticket_id)}/notes",
            json={"body": body},
            # The second place a scope error is re-narrowed after diagnosis, and
            # the second that CAN be: the context that makes the refusal
            # explicable is which endpoint was called. NOT
            # `escalated_comment_error` — its explanation is about a reply being
            # downgraded to a note, which is false on a tool that files notes.
            on_scope_error=escalated_note_error,
        )

    async def escalate_ticket(self, ticket_id: int) -> dict[str, Any]:
        """POST /api/v1/tickets/{id}/escalate — put a ticket on the BP queue.

        200 ``{"data": <ticket>}``.

        🔴 THE STATE IS IDEMPOTENT; THE SIDE EFFECTS ARE NOT. A second call
        leaves `escalated_at` correct (the model keeps the first timestamp) but
        re-fires the `Escalated` ticket event and re-sends the
        `TicketEscalated` notification to every Assistant. So an agent that
        retries this on a timeout double-notifies a team.

        Since M2-T9 that is at least RECONCILABLE: `escalated` is on every
        ticket payload, so a caller whose request timed out can GET the ticket
        and find out whether the first call landed instead of guessing. Read
        `escalated`, not `escalated_at` — the timestamp is permanently null on
        tickets escalated before that column existed, so `escalated_at is not
        None` reads the longest-queued rows as not escalated.

        Nothing here retries automatically, and the MCP tool description tells
        the caller to check rather than repeat.

        Requires the `escalation:write` scope and the `ticket.reply` ability.
        """
        return await self._request(
            "POST", f"/api/v1/tickets/{_ticket_id(ticket_id)}/escalate"
        )

    async def de_escalate_ticket(self, ticket_id: int) -> dict[str, Any]:
        """DELETE /api/v1/tickets/{id}/escalate — take it back off the queue.

        200 ``{"data": <ticket>}``. Clears `escalated_at`.

        Same shape of caveat as `escalate_ticket`, one notch milder: a repeat
        call re-fires the `De-Escalated` ticket event, adding a second entry to
        the ticket's history, but sends no notification.

        Requires the `escalation:write` scope and the `ticket.reply` ability.
        """
        return await self._request(
            "DELETE", f"/api/v1/tickets/{_ticket_id(ticket_id)}/escalate"
        )

    async def set_ticket_status(
        self, ticket_id: int, *, status: int
    ) -> dict[str, Any]:
        """PUT /api/v1/tickets/{id}/status — move it between WORKING states.

        200 ``{"data": <ticket>}`` and nothing else — no `comment` key, because
        this endpoint writes no comment row.

        `status` is one of the server's `Ticket::openStatuses()`: 1 new, 2 open,
        3 pending, 8 waiting on customer — 8's name is the server-side enum
        `waitingOnCustomer`, mirrored here rather than renamed, and on this
        internal desk it means waiting on the requester. 4, 5, 6 and 7 are a 422
        naming the field. THE VALUE IS PASSED THROUGH UNVALIDATED on purpose, exactly as on
        `close_ticket`: the server's list stays the only list, so a working state
        added there is usable here with no change to this file. The MCP tool one
        layer up carries a `Literal` for the schema's sake; this class does not
        keep a second copy of it.

        🔴 NOTHING IS SENT. No mail to the requester, no agent notification, no
        domain event. That is a consequence of the accepted list rather than of anything
        this method does: the survey (`RateTicket`) is fired by
        `Ticket::updateStatus()` only on status 4, and #137's `TicketClosed`
        only on a transition INTO 4 or 5 — and this endpoint refuses both.

        THE STATUS IS REVERSIBLE; THE HISTORY IS NOT. Every real change appends a
        `Status updated: <name>` entry to the ticket's thread, visible in
        `get_ticket` as an `event`. Calling this again puts the status back and
        appends a SECOND entry — the trail is permanent, on a real requester's
        ticket.

        ⚠️ SENDING THE STATUS THE TICKET ALREADY HOLDS IS A NO-OP, which is why
        this is a PUT: the server guards it, so no row is written and no history
        entry appended, and the call still answers 200. That makes this the one
        ticket write that is safe to repeat.

        `status=2` on a solved or closed ticket is a REOPEN, and costs
        `ticket.close` on top of `ticket.reply` — the same ability resolving it
        cost, because it is the same boundary crossed the other way. A ticket at
        6 (merged) or 7 (spam) can be moved back out the same way.

        No `on_scope_error` hook, unlike `comment_on_ticket` and
        `add_private_note`: this endpoint's scope requirement does NOT grow on an
        escalated ticket, so there is no escalated-ticket refusal to re-narrow
        and a hook here would explain a cause that cannot occur.

        Requires the `ticket:write` scope and the `ticket.reply` ability — plus
        `ticket.close` when the ticket is currently resolved.
        """
        return await self._request(
            "PUT",
            f"/api/v1/tickets/{_ticket_id(ticket_id)}/status",
            json={"status": status},
        )

    async def set_ticket_category(
        self, ticket_id: int, *, category: str | None
    ) -> dict[str, Any]:
        """PUT /api/v1/tickets/{id}/category — set or clear the ticket's TYPE.

        200 ``{"data": <ticket>}`` and nothing else, exactly as
        `set_ticket_status` — the same TicketResource, whose `category` is now
        the `{slug, name}` pair that was set, or null.

        `category` is a `ticket_types.slug` — the same vocabulary as
        `create_ticket`'s `category` and `list_tickets_by_category`'s path — or
        None, which is SENT as JSON null and CLEARS the type. The key is always
        sent: the server requires the key (null being a legal value of it), so
        an omitted key is a 422, and a None that this method dropped from the body the way `create_ticket`
        drops its optionals would turn "clear the type" into that 422. The
        slug is otherwise passed through unvalidated, so the server's list of
        types stays the only list.

        🔴 A BLANK STRING IS REFUSED HERE, before anything is sent. Laravel's
        global TrimStrings and ConvertEmptyStringsToNull middleware run on JSON
        bodies too, so `""` or `"  "` would reach the controller as null and
        CLEAR the type — a destructive reading of what was almost certainly a
        missing value. Clearing has to be asked for with None.

        What the server refuses, all as a 422 on `category`: a slug no type
        carries, a slug whose type has been soft-deleted (retired), and any of
        the alias keys `type`, `ticket_type`, `ticket_type_id` — which this
        method never sends. A type that has NO slug cannot be set through this
        endpoint at all; types created since this endpoint shipped derive one
        from their name ("Admin" -> `admin`), older ones may not have one.

        🔴 NOTHING IS SENT AND NOTHING IS RECORDED. No mail to the requester, no
        agent notification, and — unlike `set_ticket_status` — NO history entry
        either. Sending the type the ticket already holds writes nothing and
        still answers 200, so this is safe to repeat; nothing here repeats it
        automatically.

        Ownership, as on every other ticket write: a ticket not assigned to the
        token's account is a 404, or a 403 with `reason: "ticket_not_assigned"`
        (`TicketNotAssignedError`) when it sits on the shared escalation queue.
        No `on_scope_error` hook, for `set_ticket_status`'s reason: the scope
        requirement does not grow on an escalated ticket.

        ⚠️ ONLY ON SERVERS THAT HAVE THIS ENDPOINT (Ebteqdesk PR #261). An
        older install has no such route and answers 404 or 405 for EVERY id,
        the caller's own tickets included. Nothing here detects that — a route
        miss and a ticket this key cannot write are the same 404 on the wire —
        so the MCP tool's description says it instead.

        Requires the `ticket:write` scope and the `ticket.reply` ability.
        """
        if category is not None and (
            not isinstance(category, str) or not category.strip()
        ):
            raise ValueError(
                f"category must be a ticket-type slug such as 'bp-task', or None "
                f"to clear the ticket's type — not {category!r}. A blank string "
                f"is refused rather than sent, because the server would read it "
                f"as null and clear the type."
            )

        return await self._request(
            "PUT",
            f"/api/v1/tickets/{_ticket_id(ticket_id)}/category",
            json={"category": category},
        )

    async def close_ticket(
        self, ticket_id: int, *, status: int | None = None
    ) -> dict[str, Any]:
        """POST /api/v1/tickets/{id}/close — resolve or close. 200 ``{"data": …}``.

        `status` is 4 (solved) or 5 (closed). Any other value is a 422; it is
        passed through unvalidated on purpose, so the server's list stays the
        only list.

        ⚠️ NO `body` PARAMETER, AND ITS ABSENCE IS A DECISION RATHER THAN A GAP.
        The endpoint accepts one — `POST /tickets/{id}/close` with a `body` mails
        that text to the requester as a reply, and on an escalated ticket charges
        `escalation:reply` exactly as the comments endpoint does. This client
        does not send it, so that path is unreachable from here.

        🔴 WHY IT STAYS UNREACHABLE. The module instructions promise exactly TWO
        ways to write into a ticket: `comment_on_ticket` emails the requester's
        address, `add_private_note` does not. A third way to email the requester, reached
        through a tool named *close*, is precisely the shape that gets an
        internal remark mailed out — and it would be the only one whose name
        does not say it talks to anybody. Two calls in a visible order
        (`comment_on_ticket`, then `close_ticket`) do the same work and leave the
        two permissions — `ticket.reply` and `ticket.close` — separately
        answerable.

        ⚠️ THE SERVER-SIDE FIELD IS NOT DEAD and must keep its charge. A raw
        caller can still use it; `escalation:reply` on the escalated branch is
        what stops it being a way around the requester handoff, which is asserted
        by TicketWritesTest::a_reply_this_endpoint_refuses_is_a_reply_the_
        comments_endpoint_refuses_too on the server side.

        🔴 THE OMITTED-STATUS DEFAULT IS THE SERVER'S, AND IT IS THE ONE THAT
        MAILS. `status=None` sends no `status` key at all, and Ebteqdesk then
        applies `TicketWritesController::DEFAULT_CLOSE_STATUS` — SOLVED (4) —
        which fires the satisfaction survey. That default is not
        restated or overridden here, for the same reason no other default is:
        this class is the API's mirror and a second copy of a default is a copy
        that drifts. The safe default lives one layer up, in the MCP tool, which
        ALWAYS sends an explicit status and defaults it to 5. See
        `server.close_ticket` and `server.CLOSE_WITHOUT_SURVEY`.

        CLOSE DOES NOT REPLY. There is no `body` field: answering the requester
        and resolving the ticket are two acts behind two different abilities.
        To do both, comment first and then close — two calls.

        AND CLOSE DOES NOT REOPEN. 4 and 5 are the only values this endpoint
        accepts; moving a ticket back to a working state is
        `set_ticket_status`, which owns 1/2/3/8 and can take a solved, closed,
        merged or spam ticket back off the shelf. The two endpoints are
        disjoint by design and neither can reach the other's statuses.

        ⚠️ Closing as SOLVED queues the rating survey. On an install
        with no queue worker that survey is sent IMMEDIATELY and synchronously;
        with one it arrives an hour later. Either way a real email goes to the
        requester's address, so this is not a safe way to tidy up test tickets.
        An install may suppress it with `EBTEQDESK_RATING_EMAIL_ENABLED=false`,
        but the code default is ON and nothing in this API's responses reports
        the setting — this client cannot tell which way it is set.

        Requires the `ticket:write` scope and the `ticket.close` ability.
        """
        body = {} if status is None else {"status": status}

        return await self._request(
            "POST", f"/api/v1/tickets/{_ticket_id(ticket_id)}/close", json=body
        )

    async def propose_kb_article(
        self,
        *,
        kb_folder_id: int,
        title: str,
        body: str | None = None,
        seo_title: str | None = None,
        seo_description: str | None = None,
        tags: Sequence[str] | None = None,
        locale: str | None = None,
        translations: Mapping[str, Mapping[str, Any] | None] | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/kb/articles — propose a draft. 201 ``{"data": {...}}``.

        🔴 AN INTEGRATION PROPOSES, A HUMAN DISPOSES. The row lands
        `status = draft`, `review_state = pending`, `review_requested_at = now()`
        — unconditionally, on this and on every subsequent update. There is NO
        publish endpoint on /api/v1 and there will not be one: publishing is a
        human's browser session, and an API that could publish would defeat the
        review workflow entirely.

        The response carries `reference` (`"id:42"`), which is what
        `update_kb_article` and `get_kb_article_review` take. `slug` is null
        until a human first publishes, so `id:<n>` is the normal case for
        everything this endpoint creates — see `update_kb_article`.

        `body` is HTML and is SANITISED on write, so what comes back is not
        necessarily what went in. There is no markdown form anywhere in
        Ebteqdesk.

        `kb_folder_id` is REQUIRED and is the one field that cannot be changed
        later: a folder carries the article's visibility, so a move is a
        visibility change wearing an organisational costume and stays a human
        act. Choose it deliberately.

        `source` is NOT accepted and is forced to `api` server-side, so an
        integration cannot relabel its own work as hand-written. Nothing here
        sends it.

        Optional fields are OMITTED when None rather than sent as null, so the
        server applies its own defaults instead of this client keeping a second,
        drifting copy of them. `tags` REPLACES the whole set; `[]` clears it,
        which is why an empty list is sent through and only None is dropped.

        `locale` NAMES A LANGUAGE VERSION and changes where the text is stored.

        Omitted, this writes the base columns and the article shows in EVERY
        language — the behaviour that shipped before per-language content
        existed, byte for byte. Given, the text ALSO becomes that language's own
        version (`translations.{locale}` in the request body), and the article
        then shows ONLY in the languages it has a version for. See
        `update_kb_article` for the guard that governs the same field there.

        `translations` IS THE SAME THING FOR MORE THAN ONE LANGUAGE — a mapping
        of locale to that locale's own `{"title": …, "body": …}`, merged into the
        one `translations` object `locale` writes into. It is how an article is
        filed BILINGUAL IN A SINGLE REQUEST, and it composes with `locale`
        (naming the same locale in both is a ValueError). See `_kb_translations`
        for why one request rather than two is not a matter of taste: on the
        UPDATE side two calls cannot express a bilingual edit at all, and a
        create that files one language and adds the other later inherits that
        problem the moment a human publishes in between.

        ⚠️ ON A CREATE THE TEXT IS SENT TWICE, TO THE BASE COLUMNS AND TO THE
        VERSION, AND THAT IS DELIBERATE. `kb_articles.title` is NOT NULL and ten
        surfaces read the base columns directly — the slug source at first
        publish, the authoring tree, the review queue, this API's own echo. A new
        article whose base columns were left empty would be a blank row on every
        one of them. For `locale="en"` the server mirrors the version onto the
        base anyway, so the two are identical; for `locale="zhcn"` the base holds
        the Chinese text, which is the only text the article has.

        🔴 `update_kb_article` does NOT do this, and the difference is the point:
        on an update the base columns already hold the OTHER language's text and
        overwriting them would corrupt it.

        Requires the `kb:write` scope, which resolves only while the key carries
        it AND the owner's role carries `kb.manage`.
        """
        payload: dict[str, Any] = {"kb_folder_id": kb_folder_id, "title": title}
        payload.update(
            _kb_optional_fields(
                body=body,
                seo_title=seo_title,
                seo_description=seo_description,
                tags=tags,
            )
        )

        payload.update(
            _kb_translations(
                locale,
                translations,
                title=title,
                body=body,
                seo_title=seo_title,
                seo_description=seo_description,
            )
        )

        return await self._request("POST", "/api/v1/kb/articles", json=payload)

    async def update_kb_article(
        self,
        reference: str,
        *,
        title: str | None = None,
        body: str | None = None,
        seo_title: str | None = None,
        seo_description: str | None = None,
        tags: Sequence[str] | None = None,
        locale: str | None = None,
        translations: Mapping[str, Mapping[str, Any] | None] | None = None,
        allow_missing_versions: bool = False,
    ) -> dict[str, Any]:
        """PATCH /api/v1/kb/articles/{reference} — revise a DRAFT (200), or
        STAGE an edit to a PUBLISHED article (202).

        🔴 TWO OUTCOMES, ONE REQUEST SHAPE, AND THE CALLER DOES NOT CHOOSE. The
        branch is the article's `status` server-side. Both are 2xx, so both
        return normally from here rather than raising.

          200 — the article was a DRAFT. Edited in place. Body is `{"data": …}`
                and `data` is the written shape: what you just sent.

          202 — the article was PUBLISHED. NOT edited. The payload was written
                to `kb_article_revisions` for a human to approve or reject, and
                `kb_articles` was not written at all. Body is
                `{"data": …, "revision": …}`.

        🔴 ON THE 202, `data` IS THE LIVE ARTICLE AS IT STILL STANDS — the old
        title, the old body, `status: "published"`, and `translations` as they
        were, commonly `[]` while the version just submitted sits in `revision`.
        It is deliberately NOT an echo of the request: a caller handed back its
        own payload would read the 202 as a successful edit. `data` answers
        "what are customers reading"; `revision` answers "what happened to my
        edit". See KbArticleWritesController::staged().

        ⚠️ THIS CLIENT DOES NOT ADD A DISCRIMINATOR, and that is the
        module-level pass-through rule, not an oversight. The status code is not
        surfaced as a field because a derived key would be a second contract
        that drifts from the API's. The server's own discriminator is the
        TOP-LEVEL `revision` KEY: `written()` (200, 201) never emits it,
        `staged()` (202) always does. Branch on `"revision" in payload`.

        🔴 ONE REVISION ROW PER ARTICLE — `unique(kb_article_id)`. A second PATCH
        REPLACES the staged revision rather than queueing a second one, and it
        replaces a REJECTED one too, clearing the reviewer's note. So the trap
        below has a second face: on a published article, PATCHing to see what
        happened destroys what happened.

        ⚠️ 409 IS GONE FROM THIS ENDPOINT. It used to be the refusal for a
        published article (`ConflictError`), on the reasoning that unpublishing
        on edit would hand an integration a one-request takedown of any live
        help article. That reasoning stands and unpublishing is still refused;
        what changed is that there is now a row that can hold the new text
        without touching the live one, so the request is accepted and staged
        instead. Nothing on this surface raises `ConflictError` any more.

        ⚠️ VALIDATION STILL RUNS FIRST, so a malformed edit to a live article is
        a 422 (`InvalidRequestError`) that stages nothing and leaves any
        existing revision alone. The refusal ordering changed with the 409's
        removal: what used to be answered "published" is now answered with the
        field that is actually wrong.

        🔴 ON A DRAFT, EVERY UPDATE RE-QUEUES THE ARTICLE FOR REVIEW, whatever
        its previous state was: back to `status = draft`,
        `review_state = pending`, and any previous approval or rejection note is
        cleared. That is not a bug to route around — the alternative, leaving an
        approved article approved after an API rewrite, is "approve once, then
        rewrite freely".

        THE CONSEQUENCE THAT BITES: do not PATCH to find out what a reviewer
        said. That was the trap `get_kb_article_review` exists to replace —
        checking the verdict through a write destroys the verdict, on the
        article's own review block and on a staged revision alike.

        `{reference}` IS NOT ALWAYS A SLUG. A slug is frozen at FIRST PUBLISH,
        so everything the API creates has `slug: null` and could never be
        addressed by one. The segment takes the frozen slug OR the `id:<n>` form
        the create response hands back as `reference`; `:` is a character a slug
        can never contain, so the two forms cannot collide.

        An ABSENT key is not edited; a key present and EMPTY is an edit
        (`body=""` clears the body). That distinction is the server's and it is
        why None is dropped here rather than sent as null.

        `kb_folder_id` is deliberately not a parameter: it is not accepted on
        update at all. See `propose_kb_article`.

        ------------------------------------------------------------------
        🔴 `locale` — AND THE TRAP IT EXISTS TO CLOSE
        ------------------------------------------------------------------
        An article that has its OWN version in any language is served from that
        version and NOT from the base columns. So a PATCH without `locale` on
        such an article writes text no reader will ever see, in either language,
        and returns 200 while doing it.

        With `locale`, the text goes to `translations.{locale}` and nowhere else
        — the flat fields are NOT also sent, because on an existing article the
        base columns hold the other language's text and overwriting them would
        corrupt it. (`propose_kb_article` does send both, for a create's own
        reason; see it.)

        ------------------------------------------------------------------
        🔴 `translations` — MORE THAN ONE LANGUAGE, IN ONE REQUEST
        ------------------------------------------------------------------
        A mapping of locale to that locale's own fields
        (`{"en": {"title": …, "body": …}, "zhcn": {…}}`), merged into the same
        `translations` object `locale` writes into. `locale` still works and is
        unchanged; `translations` is what a bilingual edit needs, because on a
        PUBLISHED article there is no sequence of single-locale calls that adds
        a language:

          1. each call STAGES a revision rather than writing the article;
          2. `kb_article_revisions` is `unique(kb_article_id)`, so the second
             call REPLACES the first — `en` then `zhcn` leaves only `zhcn`;
          3. a `zhcn`-only revision on an article with no versions is refused by
             the missing-version guard, because approving it would take the
             article out of the English help centre;
          4. `allow_missing_versions=True` gets past that guard by PERFORMING
             that removal, which is the opposite of the intent.

        Unlike `locale`, this does NOT strip the flat `title=`/`body=` fields:
        those keep their own meaning (an edit to the article's base columns) and
        the versions ride alongside, exactly as the server's own payload allows.
        A caller writing only versions simply passes no flat fields.

        ⚠️ THE REPAIR NEEDS THE OTHER LANGUAGE'S TEXT, and nothing here invents
        it. Giving a version-less article a `zhcn` version means sending an `en`
        version too, and an upsert requires at least a title, so read the current
        English text first (`get_kb_article_review`, or `get_kb_article`) and
        pass it back unchanged in the same call.

        ⚠️ A None VALUE FOR A WHOLE LOCALE IS THE DELETE and is sent as `null`
        — see `_kb_translations`. The API refuses it with a 422; the shape can
        say it because the server's shape can, and this client does not invent a
        narrower vocabulary than the endpoint it wraps.

        ------------------------------------------------------------------
        🔴 `allow_missing_versions` — REMOVING AN ARTICLE FROM A HELP CENTRE
        ------------------------------------------------------------------
        An article with a version in ANY language appears ONLY in the languages
        it has one for. So giving a version to an article that had none takes it
        OUT of the other language's help centre.

        The server REFUSES that write, 422 on `translations`, naming the language
        that would lose the article. Two ways past it, and they are not
        equivalent: send the other language's version in the SAME call (the
        repair), or pass `allow_missing_versions=True` (the deliberate removal).
        The flag is sent only when True, so an ordinary edit never carries it.

        ⚠️ The refusal fires on the TRANSITION, not on the state: editing an
        article that is ALREADY single-language is not refused, because nothing
        is being taken away. It also cannot fire on `propose_kb_article` — a new
        article has never been in a help centre — which is why that tool has no
        such parameter.

        ⚠️ THERE IS ONE REVIEW STATE PER ARTICLE, NOT ONE PER LANGUAGE. Editing
        the Chinese version re-queues the WHOLE article, English included, and
        clears any previous verdict. That is a schema fact and no argument here
        changes it.

        Requires the `kb:write` scope.
        """
        payload = _kb_optional_fields(
            title=title,
            body=body,
            seo_title=seo_title,
            seo_description=seo_description,
            tags=tags,
        )

        if locale is not None:
            # 🔴 The flat content fields move INTO the version and do not stay
            # at the top level. See the docstring: the base columns are the
            # other language's text on an article that already has versions.
            #
            # ⚠️ ONLY the `locale=` form does this. `translations=` names its
            # languages explicitly, so a flat `title=` beside it is a separate
            # statement about the BASE columns and is left where the caller put
            # it — the server accepts both in one payload and the editor posts
            # them together.
            for field in ("title", "body", "seo_title", "seo_description"):
                payload.pop(field, None)

        payload.update(
            _kb_translations(
                locale,
                translations,
                title=title,
                body=body,
                seo_title=seo_title,
                seo_description=seo_description,
            )
        )

        if allow_missing_versions:
            # Sent only when True. A False that went on the wire would be an
            # inert key on every ordinary edit, and the server drops absent keys
            # rather than defaulting them differently.
            payload["allow_missing_versions"] = True

        return await self._request(
            "PATCH", f"/api/v1/kb/articles/{_kb_reference(reference)}", json=payload
        )

    # ------------------------------------------------------------------ #
    # The KB STRUCTURE writes — categories and folders
    # ------------------------------------------------------------------ #
    #
    # ------------------------------------------------------------------
    # 🔴 THE ARGUMENT-NAMING RULE FOR THIS WHOLE SURFACE. READ IT BEFORE
    #    ADDING A METHOD OR AN MCP TOOL.
    # ------------------------------------------------------------------
    # Argument names on the MCP tools are a MODEL-FACING CONTRACT, not an
    # internal detail: a model that has just read `propose_kb_article(
    # kb_folder_id=...)` will reach for `kb_category_id` on the very next call.
    # An inconsistency inside one surface therefore costs more here than the
    # same inconsistency would in ordinary code. Two cases, one rule each:
    #
    #   A BODY FIELD KEEPS THE API'S OWN NAME.
    #       propose_kb_article(kb_folder_id=...)
    #       create_kb_folder(kb_category_id=...)
    #   It is quoted back verbatim in the server's own 422 (`"errors": {
    #   "kb_category_id": [...]}`), so a different local name would make the
    #   refusal name a field the caller never sent.
    #
    #   AN IDENTIFIER NAMING THE THING BEING ACTED ON TAKES THE SHORT FORM.
    #       update_kb_folder(folder_id=...)     update_kb_category(category_id=...)
    #       get_ticket(ticket_id=...)           get_ticket_attachment(attachment_id=...)
    #   It is a PATH segment, never a body field, so no server message can
    #   disagree with it, and the short form matches the rest of this client.
    #
    # The two cases are distinguishable at a glance: if the id ends up in the
    # JSON body it is the API's name, if it ends up in the URL it is the short
    # one. That is the whole rule, and everything on this surface follows it.
    #
    # 🔴 NEITHER `visibility` NOR A MOVE IS A PARAMETER ON ANY OF THESE, AND
    #    THAT IS THE POINT OF THE WHOLE GROUP.
    #
    # A folder carries the visibility its articles INHERIT, so a `visibility`
    # argument would let an integration make content public — and the article
    # write surface exists precisely because a machine may not do that. The
    # server drops the key rather than rejecting it (matching what
    # `PATCH kb/articles` does with `kb_folder_id`), which means an argument here
    # would be silently ignored: worse than absent, because it would read to a
    # model as a working control. So there is no parameter, on the client or on
    # the MCP tool, and every folder these methods create is `agents` — the
    # column default.
    #
    # ⚠️ SINCE ADR-0007 AN ARTICLE CAN CARRY ITS OWN VISIBILITY, overriding its
    # folder's in both directions — and the same rule applies there for the same
    # reason: `POST` and `PATCH /api/v1/kb/articles` DROP a `visibility` key, so
    # `propose_kb_article` and `update_kb_article` have no such parameter either.
    # Every article this client files inherits its folder, which is the level
    # `list_kb_tree` reports. The consequence worth stating: a folder's
    # `visibility` is what this client's writes will get, and NOT a statement
    # about the articles a human already put there.
    #
    # `kb_category_id` is absent from `update_kb_folder` for the same reason: a
    # move re-derives the slug and reshuffles two ordering lists, and it is an
    # access-control change wearing an organisational costume. Moving a folder
    # stays a human act in the authoring UI.
    #
    # 🔴 THERE IS A DELETE, AND IT IS A REFUSAL RATHER THAN A CASCADE. It used to
    # be absent, and the asymmetry — create through the API, delete only by hand
    # — meant every mistake a key made had to be unpicked by a person. The third
    # verb is what makes the first two usable.
    #
    # What the server will NOT do is cascade: a category holding folders and a
    # folder holding articles are both 422 with the count named, and the row
    # survives. There are no foreign keys anywhere in that schema, so a cascade
    # would orphan or silently hard-delete rows nothing on this API can recreate
    # — there is no delete-article endpoint and an article delete has no undo, no
    # trash and no version history. Move or delete the children first, in that
    # order, from the bottom up.
    #
    # ⚠️ AND THERE IS NO UNDO. `delete_kb_category` and `delete_kb_folder` are the
    # only two methods on this client that destroy anything, and neither can be
    # reversed through this API — a deleted row comes back only from a database
    # backup. The delete takes the PATH id and nothing else: no move, no
    # visibility, no cascade flag, no dry run.

    async def create_kb_category(
        self,
        *,
        name: str,
        description: str | None = None,
        portal: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/kb/categories — a new top-level category. 201.

        ``{"data": {"id", "name", "slug", "description", "position", "portal",
        "folders": []}}`` — the same shape `list_kb_tree` nests, so the new `id`
        is immediately usable as `create_kb_folder`'s `kb_category_id`.

        🔴 THE SLUG IS DERIVED FROM THE NAME AND IS NOT ACCEPTED. It is also
        RE-DERIVED on every rename — see `update_kb_category`.

        ⚠️ UNIQUENESS IS CHECKED ON THE DERIVED SLUG, PER PORTAL, and a
        collision is a 422 on `name`. Two names that differ as strings can be
        one slug: "POS", "pos" and "  p.o.s!  " all slugify to `pos`. Without
        that check the server would silently store `pos-2` and the operator
        would end up with two visually identical categories at two URLs.
        🔴 THE SCOPE NARROWED: before two-knowledge-base support this
        uniqueness was checked GLOBALLY; a "POS" category in Salon V3 and a
        "POS" category in Warni collide with EACH OTHER no longer — the check
        is now per `portal`, so the same name is free to exist once in each
        knowledge base.

        `portal` is `"salonv3"` or `"warni"` — 🔴 `"all"` IS NOT A VALID VALUE
        HERE and is a 422, unlike on the four read tools; a category has to
        belong to exactly one knowledge base to be created. Absent, `null` or
        `""` all mean `"salonv3"`, matching this client's pre-two-portal
        default. The response echoes the `portal` it was actually given.

        🔴 A CATEGORY'S PORTAL IS WRITE-ONCE. There is no way to move an
        existing category to the other knowledge base through this API —
        `update_kb_category` does not accept `portal` at all; see that method.
        Choose it deliberately here, since it decides which public help site
        every folder and article under this category can ever reach.

        `description` is omitted from the body when None so the column default
        (NULL) applies, rather than this client keeping a second copy of it.

        Requires the `kb:write` scope, which resolves only while the key carries
        it AND the owner's role carries `kb.manage`.
        """
        payload: dict[str, Any] = {"name": name}
        payload.update(_kb_optional_fields(description=description, portal=portal))

        return await self._request("POST", "/api/v1/kb/categories", json=payload)

    async def update_kb_category(
        self,
        category_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """PATCH /api/v1/kb/categories/{id} — rename / re-describe. 200.

        🔴 RENAMING RE-DERIVES THE SLUG, WHICH IS PART OF A PORTAL URL. Unlike
        an article's — frozen at first publish and never moved again — a
        category slug follows its name on every save, and it is a segment of
        the nested portal URL `/support/salonv3-kb/{category}/{folder}` or
        `/support/warni-kb/{category}/{folder}`, depending which knowledge base
        the category belongs to. So a rename here changes the URL of every
        folder page beneath it. The response carries the new `slug`, which is
        the only way a caller learns what it broke.

        🔴 THERE IS NO `portal` PARAMETER HERE, ON PURPOSE, AND THAT IS NOT AN
        OVERSIGHT MATCHING `create_kb_category`'s. A category's knowledge base
        is fixed at creation and this endpoint DROPS a `portal` key sent to it
        rather than rejecting it — same silent-ignore contract as `visibility`
        on the folder writes above. A category's portal is write-once; there is
        no move endpoint, through this API or otherwise, so filing into the
        wrong knowledge base at creation is not something a later PATCH can
        undo.

        An ABSENT key is not edited; a key present and EMPTY is an edit
        (`description=""` clears it, via the server's blank-collapses-to-NULL
        mutator). That is why None is dropped here rather than sent as null —
        the same rule `update_kb_article` follows.

        A name colliding on the derived slug is a 422 on `name`, checked
        against categories in the SAME PORTAL ONLY; the row excludes itself, so
        re-saving a category under its own name is not a collision, and nor is
        renaming it to a name already used by a category in the OTHER
        knowledge base.

        404 if no category has that id, as JSON with a constant message that does
        not echo the id back.

        Requires the `kb:write` scope.
        """
        return await self._request(
            "PATCH",
            f"/api/v1/kb/categories/{_kb_structure_id(category_id, argument='category_id', what='category')}",
            json=_kb_optional_fields(name=name, description=description),
        )

    async def delete_kb_category(self, category_id: int) -> dict[str, Any]:
        """DELETE /api/v1/kb/categories/{id} — remove an EMPTY category. 200.

        🔴 IRREVERSIBLE. There is no undo on this API and no trash: the row is
        gone and only the response says what it was.

        🔴 REFUSED WHILE IT HOLDS FOLDERS — 422 on `category`, carrying the
        count: "This category still holds 2 folders. Move or delete them first."
        It is a refusal and NEVER a cascade, because a cascade would take every
        article underneath with it and nothing on this API can put one back.
        Empty it from the bottom up first.

        Returns ``{"data": {...}}`` in the SAME shape `create_kb_category`
        answers with — a receipt for a row that no longer exists, `folders`
        always `[]`. `position` is the index the category VACATED: every sibling
        after it has already shifted down by one, because the server closes the
        ordering gap in the same transaction as the delete.

        `category_id` is the short form because it is a PATH segment — the naming
        rule above `create_kb_category`. There is no body: a delete has nothing
        to move and nothing to re-scope.

        404 if no category has that id, as JSON with a constant message.

        Requires the `kb:write` scope, which resolves only while the key carries
        it AND the owner's role carries `kb.manage`. The same gate as create and
        update; the destructive verb gets no looser one.
        """
        return await self._request(
            "DELETE",
            f"/api/v1/kb/categories/{_kb_structure_id(category_id, argument='category_id', what='category')}",
        )

    async def create_kb_folder(
        self,
        *,
        kb_category_id: int,
        name: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/kb/folders — a new folder inside a category. 201.

        ``{"data": {"id", "kb_category_id", "name", "slug", "description",
        "visibility", "position", "articles_count"}}`` — the same shape
        `list_kb_tree` nests. **The `id` in that response is the
        `kb_folder_id` `propose_kb_article` takes**, so a caller can build a
        folder and file into it in two calls.

        🔴 THE FOLDER IS `agents` AND THERE IS NO ARGUMENT TO CHANGE IT. That is
        the column default, applied because this client sends no `visibility`
        key at all — see the block comment above this method for why an argument
        would be worse than its absence. Nothing filed into a folder created here
        reaches a reader outside the desk until a human acts in the Ebteqdesk UI — either by
        opening the folder up, or by giving one article its own visibility, which
        overrides the folder's and does NOT change the value reported here.

        `kb_category_id` IS THE API'S OWN FIELD NAME, kept because it is a BODY
        field — the naming rule above this method, and the same reason
        `propose_kb_article` takes `kb_folder_id`. The server quotes it back
        verbatim in its 422 (`"errors": {"kb_category_id": [...]}`), so a local
        `category_id` would make the refusal name a field the caller never sent.
        The short form is reserved for PATH ids, which is what
        `update_kb_folder(folder_id)` and `update_kb_category(category_id)` take.

        Uniqueness is PER CATEGORY, not global: `kb_folders` is unique on
        `(kb_category_id, slug)`, so "FAQ" under Billing and "FAQ" under Account
        are both legal and that is the whole point — a folder's URL is nested. A
        collision within one category is a 422 on `name`, checked against the
        DERIVED slug.

        A `kb_category_id` that does not exist is a 422 on the same field, not a
        404: there is no foreign key on `kb_folders.kb_category_id`, so without
        that check the row would be written and be unreachable on every screen.

        Requires the `kb:write` scope.
        """
        payload: dict[str, Any] = {
            # Argument name and wire name are the same string — see the naming
            # rule above. There is no mapping step left to drift.
            "kb_category_id": _kb_structure_id(
                kb_category_id, argument="kb_category_id", what="category"
            ),
            "name": name,
        }
        payload.update(_kb_optional_fields(description=description))

        return await self._request("POST", "/api/v1/kb/folders", json=payload)

    async def update_kb_folder(
        self,
        folder_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """PATCH /api/v1/kb/folders/{id} — rename / re-describe. 200.

        🔴 RENAMING RE-DERIVES THE SLUG and therefore changes the folder's
        portal URL. Same rule as `update_kb_category`; see it.

        🔴 NEITHER `visibility` NOR `kb_category_id` IS A PARAMETER, so this
        cannot re-scope a folder and cannot move one between categories. The
        server IGNORES both keys if they are sent, which is why they are absent
        here rather than passed through — a parameter whose value is silently
        discarded reads to a caller as a working control. See the block comment
        above `create_kb_category`.

        An ABSENT key is not edited; `description=""` clears it. Uniqueness is
        checked on the derived slug within the folder's CURRENT category, with
        the row excluded from its own check.

        404 if no folder has that id, as JSON with a constant message.

        Requires the `kb:write` scope.
        """
        return await self._request(
            "PATCH",
            f"/api/v1/kb/folders/{_kb_structure_id(folder_id, argument='folder_id', what='folder')}",
            json=_kb_optional_fields(name=name, description=description),
        )

    async def delete_kb_folder(self, folder_id: int) -> dict[str, Any]:
        """DELETE /api/v1/kb/folders/{id} — remove an EMPTY folder. 200.

        🔴 IRREVERSIBLE, and the stakes are higher than one level up: the folder
        is what stands between this API and an article delete it does not offer.

        🔴 REFUSED WHILE IT HOLDS ARTICLES — 422 on `folder`, carrying the count:
        "This folder still holds 3 articles. Move or delete them first." Never a
        cascade. An article delete has no undo, no trash and no version history,
        and there is no endpoint on this API that performs one, so the only way
        past this refusal is a human emptying the folder in the Ebteqdesk UI.

        Returns ``{"data": {...}}`` in `create_kb_folder`'s shape — a receipt,
        with `articles_count` always 0 and `position` the index the folder
        vacated inside its category. Its later siblings in THAT category have
        already shifted down by one; other categories are untouched.

        404 if no folder has that id, as JSON with a constant message.

        Requires the `kb:write` scope.
        """
        return await self._request(
            "DELETE",
            f"/api/v1/kb/folders/{_kb_structure_id(folder_id, argument='folder_id', what='folder')}",
        )

    # ------------------------------------------------------------------
    # MEDIA — the first of the two endpoints that read a LOCAL FILE
    # ------------------------------------------------------------------
    #
    # 🔴 EVERY METHOD ON THIS CLASS BUT TWO BUILDS ITS REQUEST OUT OF ARGUMENTS.
    # This one — and `attach_file_to_ticket`, further down, which shares its
    # upload root — opens a path on the machine the MCP server is running on and
    # puts the bytes on the wire. That is a category of risk the rest of this module
    # does not carry — it is a risk to the CALLER'S MACHINE rather than to the
    # desk — and it is why `upload_kb_media`'s tool description says, in as many
    # words, that it must upload only files the user named.
    #
    # A LOCAL PATH RATHER THAN BASE64 CONTENT, deliberately. The server runs on
    # the user's own machine over stdio, so the path is already the natural
    # handle, and routing a 4 MB screenshot through a model's context as base64
    # costs roughly 5.5 MB of tokens to move bytes that never needed to be read.
    #
    # NOTHING ABOUT THE FILE'S CONTENT IS VALIDATED CLIENT-SIDE. No size check,
    # no extension check, no sniffing. The server decides all three, from the
    # file's own bytes, and a second copy of its whitelist here would be a rule
    # that drifts — the same reason `per_page` is not clamped in this client.
    # What IS checked is only what the server cannot answer: WHERE the file is,
    # and whether it exists at all.
    #
    # 🔴 WHERE IT IS: INSIDE `EBTEQDESK_UPLOAD_ROOT`, OR NOWHERE (5.0.0). Before
    # 5.0.0 the path was opened unrestricted, so the only thing standing between
    # a prompt-injected "upload ~/.ssh/id_rsa as a screenshot" and that key
    # leaving the machine was a sentence in a tool description. Now the operator
    # names one directory, the path is resolved — `..` collapsed, every symlink
    # followed — and anything whose REAL location is not under that directory is
    # refused before it is opened. With the variable unset, nothing is read at
    # all: reading the user's disk is an opt-in an operator makes once, not a
    # default a model inherits. The containment check runs BEFORE the existence
    # check, so a refused path is refused identically whether or not the file
    # exists — this tool must not become a way to probe the filesystem.

    async def upload_kb_media(self, file_path: str) -> dict[str, Any]:
        """POST /api/v1/kb/media — upload one LOCAL file. 201.

        Returns the payload UNWRAPPED — this is the one write on the API whose
        body is not `{"data": …}`, because it is
        `Kb\\MediaUploadController::payload()` verbatim, the same eight keys the
        Ebteqdesk editor reads::

            {"ulid", "url", "kind", "mime", "width", "height", "size_bytes",
             "original_name"}

        🔴 `url` IS RELATIVE — `/kb/media/{ulid}` — AND THAT IS THE CONTRACT, not
        an omission. It is the exact string that goes into an article body, so a
        caller must paste it as it arrived. `width` and `height` are null for a
        video and for an image whose header could not be read.

        🔴 THE UPLOAD ATTACHES THE OBJECT TO NOTHING. kb_article_media is
        DERIVED from an article's body on every save, so the link exists only
        once a SAVED body contains this url. Upload, then reference, then save.
        An object nobody references is an orphan from birth and is collected by
        the server's `ebteqdesk:prune-kb-media` sweep once it is seven days old.

        The type is decided by SNIFFING the bytes (finfo), never by the filename
        or by the part's Content-Type — which is why the multipart part below
        sends a deliberately neutral one. Renaming a file changes nothing.

        Requires the `kb:write` scope, which resolves only while the key carries
        it AND the owner's role carries `kb.manage`.

        ⚠️ NOT SAFE TO RETRY. Each call stores a NEW object under a NEW ULID, so
        replaying an upload that timed out leaves a second copy on the disk that
        nothing references and that the sweep will not touch for a week.

        🔴 READS ONLY INSIDE `EBTEQDESK_UPLOAD_ROOT` (5.0.0). The path is
        resolved — `~` expanded, `..` collapsed, symlinks followed — and must
        land inside that directory; a RELATIVE path is taken relative to it.
        Raises `ConfigurationError` before any request when the variable is
        unset or names something that is not a usable directory (only uploads
        are affected — no other method reads it), and `LocalFileError` when the
        file resolves outside it.

        Raises `LocalFileError` before any request when the path is outside the
        upload root, is missing, is a directory, or cannot be read — see that
        class for why it is not a ConfigurationError.

        Errors from the server: 422 with `errors.file` for the wrong type or a
        file over the per-kind cap (10 MB image / 50 MB video), and 413 when the
        request was too large for the web server to accept at all, which is a
        different failure with a different remedy — see `PayloadTooLargeError`.
        """
        name, content = _local_upload(file_path, self._config.upload_root)

        return await self._request(
            "POST",
            MEDIA_UPLOAD_PATH,
            # 🔴 The field name is `file`, matching App\Kb\MediaRules::FIELD.
            # The third element is the part's Content-Type and is deliberately
            # neutral: the server sniffs the bytes and never reads this, and
            # sending a guessed `image/png` would be this client asserting
            # something it did not check.
            files={"file": (name, content, "application/octet-stream")},
        )

    # ------------------------------------------------------------------
    # TICKET ATTACHMENTS — the second method that reads a LOCAL FILE
    # ------------------------------------------------------------------
    #
    # 🔴 EVERYTHING THE MEDIA BLOCK ABOVE SAYS ABOUT A LOCAL PATH APPLIES HERE,
    # THROUGH THE SAME TWO FUNCTIONS. `attach_file_to_ticket` reads only inside
    # `EBTEQDESK_UPLOAD_ROOT`, nothing while that is unset, and a path is
    # resolved and checked for containment before the file is so much as
    # `stat`ed — `_resolved_local_path` is one call shared with
    # `upload_kb_media`, not a copy of it. It matters MORE here: the knowledge
    # base accepts six media types, and a ticket accepts PDF, Office, CSV, TXT
    # and ZIP as well, which is most of what is worth stealing from a disk.
    #
    # THE FILE LANDS ON A NEW PRIVATE NOTE. Three routes, all under
    # `/api/v1/tickets/{id}/notes/` and all authorised exactly as
    # `add_private_note` is: nothing is mailed to the requester, the requester
    # portal does not list the file, and the note records who attached it. One
    # call is one file on one note.
    #
    # TWO TRANSPORTS, CHOSEN BY SIZE ALONE (`MULTIPART_MAX_BYTES`):
    #
    #   up to 20 MB   POST …/notes/attachments, multipart, one request.
    #   over 20 MB    POST …/notes/uploads to START — the desk answers with an
    #                 address on its object store, signed for this one upload —
    #                 then a PUT of the file TO THAT ADDRESS, then
    #                 POST …/notes/uploads/{upload_id}/complete, which verifies
    #                 the object and writes the note. Video only; the desk
    #                 refuses anything else at START, before a byte has moved.
    #
    # 🔴 THE FILE IS NEVER HELD IN MEMORY, ON EITHER ROUTE. `_local_upload`
    # reads its whole file and says so, which is right for a 10 MB screenshot
    # and would be 500 MB of RAM here. Both routes read from an open handle in
    # bounded chunks (`_FilePart`, `_file_chunks`) with the length declared up
    # front. Nothing is base64'd and nothing is hashed: the idempotency key is
    # made from `stat`, not from a pass over the bytes.
    #
    # 🔴 THE PUT GOES THROUGH ITS OWN HTTP CLIENT (`_upload_client`). `_http` is
    # built with the desk's base URL and `Authorization: Bearer <token>` on
    # every request. Sent to an object store, that header hands the API token to
    # a third party AND breaks the signed request. The second client has no
    # base URL, no default headers, no cookies and follows no redirect.
    #
    # 🔴 THE UPLOAD ADDRESS IS A CREDENTIAL. Whoever holds it can write that
    # object until it expires. So: the start response is never returned to a
    # caller (`_SignedUpload` keeps the address out of `repr`); `_put_file`
    # lets no exception out of the frame that holds it, and describes a failure
    # by the exception's TYPE and the status code, never `str(exc)`; a
    # malformed start response is reported without quoting its body; and the
    # HTTP stack's own request log is silenced for that one call (see
    # `_UploadAddressLogFilter`). The address must be `https://` — the file
    # gets the care the token gets.
    #
    # RETRIES HAVE A KEY, WHICH IS WHY THIS WRITE MAY BE REPEATED. Every other
    # write here says "never retry": a POST that timed out may have landed. The
    # two routes that take the file require an `Idempotency-Key`, and this
    # client derives it from the ticket, the resolved path, the size, the
    # modification time and the note — so the same call is the same key, the
    # desk files it once, and a changed file is a different key. What is NOT
    # retried is decided per answer: see `_send_direct` for the one fresh
    # attempt a large upload gets.
    #
    # ONE METHOD FOR A LIBRARY CALLER, TWO FOR THE MCP LAYER.
    # `attach_file_to_ticket` runs a transfer to its end. `server.py` instead
    # calls `prepare_ticket_attachment` (no I/O beyond `stat`, and the key) and
    # then runs `send_ticket_attachment` as a task it does not wait on for more
    # than 30 seconds — a tool call must not depend on staying open for the
    # length of a 500 MB upload.

    def prepare_ticket_attachment(
        self, ticket_id: int, file_path: str, *, note: str | None = None
    ) -> TicketAttachment:
        """Check one local file and derive its key. NOTHING IS SENT OR OPENED.

        The upload root and the containment check run first (`ConfigurationError`
        while the folder is unset or unusable, `LocalFileError` for a path that
        resolves outside it — identically whether or not it exists). Then the
        resolved path is `stat`ed, without being read: a missing path, a
        directory, a device or a pipe, an empty file and an unreadable one are
        each a `LocalFileError` naming the reason.

        `note` is the note's text; None or blank means the desk writes its
        default, `Attached file: <name>`.
        """
        ticket = _ticket_id(ticket_id)

        if note is not None and not isinstance(note, str):
            raise ValueError(
                f"note must be text, or omitted for the desk's default, not {note!r}."
            )

        # Blank is absent, here rather than on the desk: the desk treats the
        # two alike, and the key below must not come out different for them.
        text = note if note is not None and note.strip() else None

        path, real = _resolved_local_path(file_path, self._config.upload_root)
        info = _regular_file(path, real)

        return TicketAttachment(
            ticket_id=ticket,
            file_path=str(path),
            # The name the USER gave, not the symlink target's — as on
            # `upload_kb_media`, and for the same two reasons.
            name=path.name,
            real_path=real,
            size=info.st_size,
            mtime_ns=info.st_mtime_ns,
            note=text,
            key=_attachment_key(ticket, real, info.st_size, info.st_mtime_ns, text),
        )

    async def send_ticket_attachment(
        self,
        attachment: TicketAttachment,
        *,
        progress: TransferProgress | None = None,
    ) -> dict[str, Any]:
        """Send a prepared file and return the desk's receipt, verbatim.

        201, or 200 when the desk had already filed this exact upload::

            {"data": <ticket>, "comment": {"id", "created_at"},
             "attachment": {"id", "name", "mime_type", "size", "url"},
             "replayed": false}

        `data` and `comment` are `add_private_note`'s receipt; `comment.id` is
        never null here, because the desk always supplies a body. `replayed` is
        in the BODY on purpose — this client never turns a status code into a
        derived key (see `_exchange`), so the desk says it in the payload.
        `attachment.url` serves images only.

        `progress`, if given, is updated as the bytes go; it is how a caller
        that is not waiting on this coroutine reports on it.

        Raises what `add_private_note` raises for the same ticket — scope,
        ability and 404 are that endpoint's, through the same narrowers — plus
        `PayloadTooLargeError`, `UnsupportedMediaError`, `ConflictError`,
        `DirectUploadUnavailableError`, `StaleAttachmentApiError` and
        `UploadTransferError`. Each says whether calling again can help.
        """
        if progress is None:
            progress = TransferProgress(attachment.size, clock=self._monotonic)

        # Time has passed since the file was prepared — none at all through
        # `attach_file_to_ticket`, but a detached transfer starts when the event
        # loop gets to it. A file that has already changed is refused before the
        # first request rather than after an upload has been started for it.
        if not _unchanged(attachment):
            raise UploadTransferError(
                UploadTransferError.FILE_CHANGED, attachment.name, bytes_total=attachment.size
            )

        if attachment.direct:
            return await self._send_direct(attachment, progress)

        return await self._send_multipart(attachment, progress)

    async def attach_file_to_ticket(
        self, ticket_id: int, file_path: str, *, note: str | None = None
    ) -> dict[str, Any]:
        """Attach one LOCAL file to a ticket, on a new INTERNAL note.

        `prepare_ticket_attachment` then `send_ticket_attachment`, awaited to
        the end — for a 500 MB video on a slow line that is most of an hour.
        See the block comment above for everything this does and does not do.

        ✅ SAFE TO REPEAT WITH THE SAME ARGUMENTS, unlike `upload_kb_media`: the
        same file, ticket and note is filed once, and a repeat answers with
        `"replayed": true`.

        Requires what `add_private_note` requires: `ticket:write` and
        `ticket.reply` on the caller's own ticket; `escalation:read`,
        `escalation:write` and `bp_escalation.reply` on an escalated one.
        """
        return await self.send_ticket_attachment(
            self.prepare_ticket_attachment(ticket_id, file_path, note=note)
        )

    async def _send_multipart(
        self, attachment: TicketAttachment, progress: TransferProgress
    ) -> dict[str, Any]:
        """POST …/notes/attachments — one file up to 20 MB, in one request."""
        path = f"/api/v1/tickets/{attachment.ticket_id}/notes/attachments"
        waits = 0

        while True:
            changed = False
            wait: str | None = None

            try:
                with _open_unchanged(attachment) as handle:
                    progress.begin_sending(None)

                    payload, _ = await self._attachment_exchange(
                        path,
                        # 🔴 `file`, and a deliberately neutral part type: the
                        # desk sniffs the bytes and never reads this, exactly as
                        # on the KB upload. The second element is a READER, not
                        # bytes — httpx2 takes its length from the handle and
                        # pulls it in chunks.
                        files={
                            "file": (
                                attachment.name,
                                _FilePart(handle, attachment, progress),
                                "application/octet-stream",
                            )
                        },
                        # Omitted, not sent empty, when there is no note.
                        data=None if attachment.note is None else {"note": attachment.note},
                        headers={_IDEMPOTENCY_HEADER: attachment.key},
                    )

                    return payload
            except _Abandon:
                changed = True
            except ConflictError as exc:
                # "The same request is still running": an earlier attempt of
                # this very call. Wait as long as the desk asks and send it
                # again — the key makes the second one a replay, not a copy.
                if exc.reason != REASON_IN_PROGRESS or waits >= _IN_PROGRESS_MAX_WAITS:
                    raise

                wait = exc.retry_after

            if changed:
                raise UploadTransferError(
                    UploadTransferError.FILE_CHANGED,
                    attachment.name,
                    bytes_sent=progress.bytes_sent,
                    bytes_total=attachment.size,
                )

            waits += 1
            await self._pause(wait, _IN_PROGRESS_WAIT_SECONDS)

    async def _send_direct(
        self, attachment: TicketAttachment, progress: TransferProgress
    ) -> dict[str, Any]:
        """Start, PUT, complete — a video over 20 MB, by way of the object store.

        🔴 ONE FRESH ATTEMPT, AND ONLY ONE, WHATEVER WENT WRONG. Four answers
        mean "the file has to be sent again": the PUT itself failing, and the
        desk answering the completion with a 404 (the upload expired), with
        `upload_incomplete` (no object arrived) or with `upload_size_mismatch`
        (the wrong number of bytes arrived, and the desk deleted them). Each is
        answered the same way — START AGAIN, which with the same key returns
        the same upload and a freshly signed address — and the second such
        answer of any kind is final. The budget is shared on purpose: a 500 MB
        upload that could be restarted once per cause could be sent five times.

        What is NOT a fresh attempt: waiting out `idempotency_in_progress`, and
        the one repeat of a completion the desk's object store was unreachable
        for. Those re-ask a question; they do not resend a file.

        What is never retried: the deadline, a file that changed under the
        read, and an address that is not https. See `_put_file`.
        """
        restarted = False

        while True:
            upload = await self._start_upload(attachment)

            if not upload.completed:
                outcome = await self._put_file(upload, attachment, progress)

                if outcome.abandoned is not None:
                    raise UploadTransferError(
                        outcome.abandoned,
                        attachment.name,
                        bytes_sent=progress.bytes_sent,
                        bytes_total=attachment.size,
                    )

                if not outcome.ok:
                    if restarted:
                        raise UploadTransferError(
                            UploadTransferError.PUT_FAILED,
                            attachment.name,
                            cause_type=outcome.cause_type,
                            status_code=outcome.status_code,
                            bytes_sent=progress.bytes_sent,
                            bytes_total=attachment.size,
                        )

                    restarted = True
                    continue

            # Already attached, or just sent: either way the receipt comes from
            # the completion route, which answers an upload it has already
            # completed with the same body and `"replayed": true`.
            progress.begin_completing()

            try:
                return await self._complete_upload(attachment, upload.upload_id)
            except _StartAgain as again:
                final = again.final

            if restarted:
                raise final

            restarted = True

    async def _start_upload(self, attachment: TicketAttachment) -> _SignedUpload:
        """POST …/notes/uploads — start, or resume, one upload.

        201 for a new upload; 200 for the same key again, with the SAME
        `upload_id` and a freshly signed address while it is pending, or a
        `completed` body once it has been attached. No note is written by this
        call: the note exists only once the file does.
        """
        path = f"/api/v1/tickets/{attachment.ticket_id}/notes/uploads"

        body: dict[str, Any] = {
            "filename": attachment.name,
            "mime_type": _declared_type(attachment.name),
            "size": attachment.size,
        }
        if attachment.note is not None:
            body["note"] = attachment.note

        waits = 0

        while True:
            wait: str | None = None

            try:
                # 🔴 The response is unpacked straight into the parser and never
                # bound to a name in this frame: it holds the upload address.
                upload = _signed_upload(
                    *await self._attachment_exchange(
                        path,
                        json=body,
                        headers={_IDEMPOTENCY_HEADER: attachment.key},
                        withhold_body=True,
                    ),
                    self._monotonic(),
                )
                break
            except ConflictError as exc:
                # The key's upload is being removed by the desk's prune.
                if exc.reason != REASON_IN_PROGRESS or waits >= _IN_PROGRESS_MAX_WAITS:
                    raise

                wait = exc.retry_after

            waits += 1
            await self._pause(wait, _IN_PROGRESS_WAIT_SECONDS)

        if isinstance(upload, str):
            raise UploadTransferError(upload, attachment.name, bytes_total=attachment.size)

        return upload

    async def _put_file(
        self,
        upload: _SignedUpload,
        attachment: TicketAttachment,
        progress: TransferProgress,
    ) -> _PutOutcome:
        """PUT the file to the address the desk signed.

        🔴 RETURNS AN OUTCOME AND RAISES NOTHING, and that is the control rather
        than a style. An exception leaving here would carry a traceback through
        the frame that built the request, and an HTTP library's request object
        prints its URL in its `repr` — so anything that formats a traceback
        with local variables would print a working write address. Everything
        that can go wrong is caught inside `_put_once`, reduced to the TYPE of
        the exception and the status code, and dropped there.

        Three ways it ends without a fresh attempt being worth anything:

          - THE DEADLINE. 50 minutes from when this PUT began, or one minute
            before the address expires, whichever is sooner. The lifetime is
            measured on the desk's clock (`_signed_upload`), so a laptop whose
            clock is wrong neither cuts an upload short nor runs past the end.
          - THE FILE CHANGED. Its size and modification time are checked before
            every chunk and once more after the store has answered. A changed
            file is not the file that was named, so the desk is never asked to
            attach it — the object is left for the desk's prune.
          - Neither is retried: the first would meet the same limit, the second
            is a different file.
        """
        began = self._monotonic()

        deadline_at = began + PUT_DEADLINE_SECONDS
        if upload.expires_in is not None:
            deadline_at = min(deadline_at, upload.received_at + upload.expires_in)

        progress.begin_sending(deadline_at - began)

        if deadline_at <= began:
            return _PutOutcome(abandoned=UploadTransferError.DEADLINE)

        try:
            handle = _open_unchanged(attachment)
        except UploadTransferError as exc:
            return _PutOutcome(abandoned=exc.kind)

        stopped: list[str] = []
        chunks = _file_chunks(
            handle, attachment, progress, deadline_at, self._monotonic, stopped
        )

        try:
            status_code, cause_type = await self._put_once(upload, attachment.size, chunks)
        finally:
            # Closed here rather than left to the garbage collector: a send that
            # failed half-way leaves the generator suspended on its `yield`.
            await chunks.aclose()
            handle.close()

        if stopped:
            return _PutOutcome(abandoned=stopped[0])

        if status_code != 200:
            return _PutOutcome(status_code=status_code, cause_type=cause_type)

        if not _unchanged(attachment):
            return _PutOutcome(abandoned=UploadTransferError.FILE_CHANGED)

        return _PutOutcome(ok=True)

    async def _put_once(
        self, upload: _SignedUpload, size: int, body: AsyncIterator[bytes]
    ) -> tuple[int | None, str | None]:
        """One PUT: (the store's status, or the failing exception's TYPE).

        The only frame in this package that holds the upload address in a form
        that prints it. So it is written to let nothing out: every `Exception`
        is caught here and reduced to its class name, the response body is never
        read (an object store's error XML quotes the request it refused), and
        the HTTP stack's request log is dropped while the marker below is set.

        The store answers 200 with an empty body. Anything else — another
        status, a redirect, a dropped connection — is "not accepted".
        """
        client = self._upload_client()
        marker = _SENDING_TO_OBJECT_STORE.set(True)

        try:
            response = await client.send(
                client.build_request(
                    "PUT",
                    upload.address(),
                    # Exactly what the desk listed, plus the length. An explicit
                    # `Content-Length` is also what stops httpx2 falling back to
                    # chunked transfer encoding for a streamed body, which a
                    # signed PUT does not accept.
                    headers=upload.put_headers(size),
                    content=body,
                ),
                stream=True,
            )

            status_code = response.status_code

            try:
                await response.aclose()
            except Exception:
                # The store has already answered; failing to close the stream
                # tidily must not turn an accepted upload into a failed one.
                pass

            return status_code, None
        except Exception as exc:
            return None, type(exc).__name__
        finally:
            _SENDING_TO_OBJECT_STORE.reset(marker)

    def _upload_client(self) -> httpx2.AsyncClient:
        """The HTTP client for the object-store PUT, built on first use.

        🔴 NOT `self._http`, AND IT MUST NEVER BECOME IT. That client carries
        `Authorization: Bearer <token>` on every request it sends; the object
        store is a third party, and a signed PUT that also carries an
        `Authorization` header is refused as two authentications at once. This
        one has no base URL, and its default headers — `Accept`,
        `Accept-Encoding`, `Connection`, `User-Agent` — are CLEARED, so what
        goes out is `Host`, `Content-Length` and whatever the desk listed, and
        nothing else. It follows no redirect: an address that answers 3xx is an
        upload that was not accepted, not an invitation to send the file
        somewhere the desk did not sign.
        """
        if self._upload_http is None:
            client = httpx2.AsyncClient(
                timeout=httpx2.Timeout(self._config.timeout),
                transport=self._upload_transport,
                follow_redirects=False,
            )
            client.headers.clear()

            self._upload_http = client

        return self._upload_http

    async def _complete_upload(
        self, attachment: TicketAttachment, upload_id: str
    ) -> dict[str, Any]:
        """POST …/notes/uploads/{upload_id}/complete — verify and attach.

        No body and no `Idempotency-Key`: the `upload_id` is the identity. This
        is the call that writes the note, so the desk authorises it again, as
        the caller is NOW.

        Returns the receipt, or raises `_StartAgain` for the three answers that
        mean the file has to be sent again (see `_send_direct`). A completion
        that is still running is waited out; an unreachable object store is
        asked once more after the pause the desk names.
        """
        path = (
            f"/api/v1/tickets/{attachment.ticket_id}/notes/uploads/"
            # Opaque, and sent back verbatim — which for the 26 characters the
            # desk issues is what quoting does. Quoted so that it can only ever
            # be ONE path segment.
            f"{quote(upload_id, safe='')}/complete"
        )

        waits = 0
        asked_store_again = False

        while True:
            try:
                payload, _ = await self._attachment_exchange(path)

                return payload
            except NotFoundError:
                # One body for an upload that is unknown, expired, somebody
                # else's or on another ticket. The ticket itself was reachable a
                # moment ago, when the upload was started; if that has changed,
                # starting again says so with the ticket's own 404.
                raise _StartAgain(
                    UploadTransferError(
                        UploadTransferError.EXPIRED,
                        attachment.name,
                        bytes_sent=attachment.size,
                        bytes_total=attachment.size,
                    )
                ) from None
            except ConflictError as exc:
                if exc.reason == REASON_UPLOAD_INCOMPLETE:
                    raise _StartAgain(
                        UploadTransferError(
                            UploadTransferError.INCOMPLETE,
                            attachment.name,
                            bytes_sent=attachment.size,
                            bytes_total=attachment.size,
                        )
                    ) from None

                if exc.reason != REASON_IN_PROGRESS or waits >= _IN_PROGRESS_MAX_WAITS:
                    raise

                waits += 1
                wait, fallback = exc.retry_after, _IN_PROGRESS_WAIT_SECONDS
            except InvalidRequestError as exc:
                if exc.reason != REASON_SIZE_MISMATCH:
                    raise

                raise _StartAgain(exc) from None
            except ServerError as exc:
                if exc.reason != REASON_STORAGE_UNAVAILABLE or asked_store_again:
                    raise

                asked_store_again = True
                wait, fallback = exc.retry_after, _STORAGE_WAIT_SECONDS

            await self._pause(wait, fallback)

    async def _attachment_exchange(
        self, path: str, *, withhold_body: bool = False, **request: Any
    ) -> tuple[dict[str, Any], httpx2.Headers]:
        """One POST to a ticket-attachment route, through `_exchange`.

        Two things all three routes share and no other endpoint needs:

          - `escalated_note_error`, as on `add_private_note`: these routes file
            a note, so a refusal naming `escalation:write` means the ticket is
            escalated, and for the same reason.
          - `withhold_body`, for the START route only: a reply that is not the
            JSON it should be is reported WITHOUT its first bytes, because that
            one reply can contain an upload address.

        ⚠️ A 404 THAT IS NOT JSON IS LEFT AS WHAT IT IS — a proxy or an error
        page answering instead of the application. A desk that simply lacks
        these routes answers JSON, with no `error` key, and `api_error_for`
        recognises that; an HTML 404 is somebody else, and saying "this desk
        predates the feature" about it would be a guess.
        """
        try:
            return await self._exchange(
                "POST", path, on_scope_error=escalated_note_error, **request
            )
        except MalformedResponseError as exc:
            if not withhold_body:
                raise

            # 🔴 Two plain values are taken out and the exception is let go at
            # the end of this block. Its MESSAGE quotes the reply's first bytes,
            # so re-raising it, chaining it, or even keeping it in a local name
            # would carry those bytes along with whatever is raised next.
            status_code, content_type = exc.status_code, exc.content_type

        raise MalformedResponseError(
            status_code=status_code,
            path=path,
            content_type=content_type,
            body_snippet=_WITHHELD,
        )

    async def _pause(self, retry_after: str | None, fallback: float) -> None:
        """Wait as long as the desk asked, within reason. See the constants."""
        await self._sleep(_wait_seconds(retry_after, fallback))

    # ------------------------------------------------------------------
    # REORDER — three endpoints, one rule
    # ------------------------------------------------------------------
    #
    # 🔴 THE WHOLE ORDERED SIBLING SET, NEVER A DELTA. `ids` must be EXACTLY the
    # current sibling set — same members, same count, no duplicates — or the
    # server answers 422 and writes NOTHING. There is no "move item X to index
    # N" form of any of these, and that is the design rather than a gap: a delta
    # would need the server to reconstruct the caller's mental model of the list
    # and reconstruct it identically after a concurrent insert, while a whole
    # list is self-describing. The set-equality check is what turns a stale
    # caller into an error instead of a silently corrupted order.
    #
    # ⚠️ THESE ARE THE ONE SAFE-TO-RETRY WRITE ON THIS API. Positions are
    # assigned by INDEX, so replaying the same body is a no-op that answers 200
    # with the same list. Every other write above must not be retried blind. Said
    # here because it is the exception to this module's loudest rule.
    #
    # The parent is a PATH segment on two of the three, so it takes the short
    # `category_id` / `folder_id` form — the naming rule above
    # `create_kb_category`. It is not a body field on any of them and cannot be:
    # a reorder cannot move anything between parents.
    #
    # ⚠️ AND THE THIRD IS ABSENT FOR A REAL REASON. Categories are the top level
    # and have no parent, so `reorder_kb_categories` takes no parent argument at
    # all. The MCP tool that fronts all three refuses a `parent_id` on that scope
    # client-side rather than sending one the URL has nowhere to put.
    #
    # 🔴 "THE WHOLE SIBLING SET" NARROWED TO ONE PORTAL FOR CATEGORIES ONLY,
    # since two-knowledge-base support. `reorder_kb_categories` now refuses a
    # list spanning both Salon V3 and Warni — see its own docstring for the
    # server's exact wording. `reorder_kb_folders` and `reorder_kb_articles`
    # need no equivalent note: a folder's siblings are one category's folders
    # and an article's siblings are one folder's articles, and a category
    # belongs to exactly one portal, so neither sibling set could ever span two.

    async def reorder_kb_categories(self, *, ids: Sequence[int]) -> dict[str, Any]:
        """PUT /api/v1/kb/categories/order — reorder ONE PORTAL'S categories. 200.

        ``{"data": [ …that portal's categories, in their new order… ]}`` in the
        same shape `list_kb_tree` nests at the top level, `folders` included.

        `ids` is the COMPLETE ordered list of category ids — see the block
        comment above. A partial list, a superset, or a duplicate is a 422 on
        `ids` and nothing is written.

        🔴 BREAKING SINCE TWO-KNOWLEDGE-BASE SUPPORT: `ids` MUST BE ONE
        PORTAL'S WHOLE SET, NEVER BOTH PORTALS'. Before Salon V3 and Warni
        existed, "the complete sibling set" meant every category in the
        installation, and a caller built that way now gets a 422 on `ids`:
        *"The order must list categories from a single knowledge base portal.
        Reorder each portal in its own request."* The fix is not "smaller
        list" — it is still that portal's WHOLE set — read one portal's
        categories with `list_kb_tree(portal="salonv3")` or
        `list_kb_tree(portal="warni")`, reorder it, then repeat the call for
        the other portal if both need reordering. Folder and article reorders
        are unaffected: a folder's siblings are one category's folders and an
        article's siblings are one folder's articles, and neither of those
        sibling sets can span portals in the first place.

        Requires the `kb:write` scope, which resolves only while the key carries
        it AND the owner's role carries `kb.manage`. `kb:read` reaches none of
        the three: reordering IS authoring.
        """
        return await self._request(
            "PUT", "/api/v1/kb/categories/order", json={"ids": _kb_order_ids(ids)}
        )

    async def reorder_kb_folders(
        self, category_id: int, *, ids: Sequence[int]
    ) -> dict[str, Any]:
        """PUT /api/v1/kb/categories/{id}/folders/order — one category's folders. 200.

        ``{"data": [ …that category's folders, in their new order… ]}``, each in
        `list_kb_tree`'s folder shape with `articles_count`.

        `ids` is the COMPLETE ordered list of THAT CATEGORY'S folder ids. An id
        belonging to another category is a superset and is refused — a reorder
        cannot move a folder between categories, and there is no argument that
        would let it.

        A `category_id` that does not exist is a 404 with a constant message,
        answered BEFORE the body is looked at.

        Requires the `kb:write` scope.
        """
        parent = _kb_structure_id(category_id, argument="category_id", what="category")

        return await self._request(
            "PUT",
            f"/api/v1/kb/categories/{parent}/folders/order",
            json={"ids": _kb_order_ids(ids)},
        )

    async def reorder_kb_articles(
        self, folder_id: int, *, ids: Sequence[int]
    ) -> dict[str, Any]:
        """PUT /api/v1/kb/folders/{id}/articles/order — one folder's articles. 200.

        ``{"data": [{"id", "title", "position"}]}`` — a deliberately MINIMAL
        shape, not the article read payload. This is an ordering receipt, and
        shipping every article's body to report three integers would make a
        reorder of a large folder megabytes of JSON.

        `ids` is the COMPLETE ordered list of THAT FOLDER'S article ids —
        including DRAFTS, which have positions like any other row. A folder whose
        `articles_count` in `list_kb_tree` is 12 needs all twelve ids here, and
        `list_kb_tree` does not return them: read them from this endpoint's own
        response, or from the folder's article list in the Ebteqdesk UI.

        A `folder_id` that does not exist is a 404 with a constant message.

        Requires the `kb:write` scope.
        """
        parent = _kb_structure_id(folder_id, argument="folder_id", what="folder")

        return await self._request(
            "PUT",
            f"/api/v1/kb/folders/{parent}/articles/order",
            json={"ids": _kb_order_ids(ids)},
        )

    # ------------------------------------------------------------------
    # AGENT PROVISIONING — /api/v1/admin/*
    # ------------------------------------------------------------------
    #
    # 🔴 THESE ARE NOT LIKE THE REST OF THIS CLASS. Every other method reads or
    # writes a ticket or a help article. These create HELPDESK ACCOUNTS, set
    # what role an account is on, and issue another account a bearer token —
    # they decide WHO MAY ACT rather than what is acted on.
    #
    # Three properties of the server side that a caller has to know and cannot
    # infer from the payloads:
    #
    #   1. A CREATED PASSWORD AND AN ISSUED TOKEN ARE EACH RETURNED EXACTLY
    #      ONCE. `generatedPassword` on the create and `plainTextToken` on the
    #      issue are the only moments those strings exist anywhere; the database
    #      holds a bcrypt hash and a sha256 respectively. There is no read
    #      endpoint that produces either again, on this API or in the browser.
    #
    #   2. `admin:read` AND `admin:write` CAN NEVER BE ISSUED through
    #      `issue_api_key`, by any caller, however privileged. The server
    #      subtracts them last and unconditionally, so a provisioning key cannot
    #      mint a successor. They come only from a signed-in human at
    #      Settings > API keys.
    #
    #   3. THE SERVER REFUSES RATHER THAN NARROWING. A scope the cap will not
    #      issue is a 422 naming it and issuing nothing, never a quietly smaller
    #      key. So a 201 from `issue_api_key` means the key carries exactly what
    #      was asked for.
    #
    #   4. AN `admin:write` KEY CANNOT CREATE AN ADMINISTRATOR. Any `role_id`
    #      whose role grants `admin.access` is refused on create AND on update.
    #      Without that, rule 2 above would be bypassable rather than binding:
    #      the created account's password buys a browser session, and a session
    #      mints a provisioning key. `list_roles` ships `assignable` per row.
    #
    #   5. A LEGACY `abilities = ['*']` KEY REACHES NONE OF THIS. The wildcard
    #      does not expand to the admin area, so a key minted before these
    #      endpoints existed is refused all nine with the ordinary scope 403.
    #      Admin scopes come from an explicit tick and nothing else.
    #
    # ⚠️ THERE IS NO DELETE-AGENT ENDPOINT AND NO METHOD FOR ONE HERE. Deleting
    # an agent reassigns or nulls history across nine tables behind a force flag
    # and three separate refusals; it stays in the Ebteqdesk web UI. A caller
    # asking for it is told so rather than offered a near-miss.
    #
    # `user_id` is the short form on every method below because it is a PATH
    # segment, matching the `category_id` / `folder_id` naming rule the KB
    # structure block states.

    async def list_agents(
        self, *, search: str | None = None, role_id: int | None = None
    ) -> dict[str, Any]:
        """GET /api/v1/admin/agents — the whole agent roster. 200.

        ``{"data": [{"id", "uuid", "name", "email", "emailLocal",
        "mustChangePassword", "role", "groups", "createdAt", "updatedAt"}, …]}``

        🔴 NOT PAGINATED, unlike the ticket lists. An agent roster is bounded by
        the number of people employed, and a caller checking whether an address
        is free needs the whole set — a page-at-a-time walk answers that
        question wrongly by default.

        `emailLocal` is the local part when the account sits on the configured
        agent domain and NULL when it does not, and the null is a discriminator
        rather than a missing value: it says this account predates the locked
        suffix and the API will not rewrite its domain.

        NO CREDENTIAL IS IN THIS PAYLOAD and none ever will be — not the
        password hash, not `users.token`, the legacy plaintext per-agent bearer
        credential the mobile app holds. The server-side resource is an
        allowlist for exactly that reason.

        `search` matches name or email case-insensitively; `role_id` narrows to
        one role. Both are omitted from the query string when None so the server
        applies no filter, rather than this client sending an empty one.

        Requires the `admin:read` scope, which resolves only while the key
        carries it AND the account's role holds `admin.access`.
        """
        params: dict[str, Any] = {}

        if search is not None:
            params["search"] = search

        if role_id is not None:
            params["role_id"] = _agent_id(role_id, argument="role_id", what="role")

        return await self._get("/api/v1/admin/agents", params or None)

    async def get_agent(self, user_id: int) -> dict[str, Any]:
        """GET /api/v1/admin/agents/{id} — one agent. 200.

        ``{"data": {…the list shape…}, "meta": {"issuableScopes": [...]}}``

        `meta.issuableScopes` is what `issue_api_key` would ACCEPT for this
        agent, from THIS key, right now — the four-term cap applied to the whole
        vocabulary. It is advice and not a promise: the server runs the
        arithmetic again on the POST, so a role change in between narrows it.
        `admin:read` and `admin:write` never appear in it.

        404 with a constant message if no user has that id.

        Requires the `admin:read` scope.
        """
        return await self._get(
            f"/api/v1/admin/agents/{_agent_id(user_id, argument='user_id', what='agent')}"
        )

    async def list_roles(self) -> dict[str, Any]:
        """GET /api/v1/admin/roles — every role an agent can be put on. 200.

        ``{"data": [{"id", "name", "key", "isSystem", "permissions",
        "agentsCount", "assignable"}, …]}``

        🔴 `assignable` IS FALSE FOR EVERY ROLE GRANTING `admin.access`, and
        those roles cannot be passed to `create_agent` or `update_agent` at
        all — a 422, not a silent downgrade. Read it before offering a role
        picker; the flag is shipped rather than derived from `permissions` so a
        client is not keeping a second copy of a security rule.

        `permissions` is the flat `area.ability` list, and it is the reason this
        endpoint exists rather than the caller guessing: WHICH SCOPES A KEY
        ISSUED TO AN AGENT CAN RESOLVE IS DECIDED BY THE ABILITIES OF THE ROLE
        THE AGENT IS ON. An agent on a role without `kb.manage` cannot hold a
        working `kb:write` key however it is ticked, and the mint is refused
        rather than silently narrowed — so the role is chosen first.

        `key` is null for an operator-created role, and that null is meaningful:
        an unkeyed role is not governed by the server's scope policy and keeps
        the ceiling its permissions give it.

        Requires the `admin:read` scope.
        """
        return await self._get("/api/v1/admin/roles")

    async def list_groups(self) -> dict[str, Any]:
        """GET /api/v1/admin/groups — the teams an agent can be added to. 200.

        ``{"data": [{"id", "name", "membersCount"}, …]}``

        ⚠️ GROUPS GRANT NOTHING. Membership is organisational — it routes
        tickets and scopes team views — and cannot add or remove a single
        ability. Removing an agent's last group does not touch their access.

        Requires the `admin:read` scope.
        """
        return await self._get("/api/v1/admin/groups")

    async def create_agent(
        self,
        *,
        name: str,
        email_local: str,
        role_id: int,
        groups: Sequence[int] | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/admin/agents — create a helpdesk account. 201.

        ``{"data": {…the agent shape…}, "generatedPassword": "…"|null}``

        🔴 `generatedPassword` IS THE ONLY MOMENT THAT STRING EXISTS. It is
        non-null only when the server chose the password — i.e. when `password`
        was omitted — and there is no way to read it again from any surface. The
        database holds a bcrypt hash. Hand it to the user immediately or it is
        lost and the account needs a reset in the Ebteqdesk web UI.

        When `password` IS given, `generatedPassword` is null: the caller
        already holds that string, and echoing it back would put a credential in
        a response body for no reason.

        Either way the account is marked "must change password", so the agent is
        held on the change screen until they choose their own.

        `email_local` IS HALF AN ADDRESS AND THE DOMAIN IS NOT NEGOTIABLE. Send
        `"dana"`, not `"dana@somewhere"`; the server appends its configured agent
        domain. A full address is refused — `@` is not a legal character in the
        local part — and a separate `email` key is refused outright rather than
        ignored, so the domain cannot be chosen by a caller even by sending one.

        `role_id` is REQUIRED and comes from `list_roles`; `groups` is a list of
        team ids from `list_groups` and is omitted when None. Every column is
        assigned by name server-side, so extra keys in the body reach nothing.

        🔴 THE ROLE MAY NOT GRANT ADMIN ACCESS. A `role_id` whose role holds
        `admin.access` is a 422 on `role_id`, and `list_roles` marks every such
        row `assignable: false`. The reason is this endpoint's own return value:
        an account on an admin role would sign in with the password below and
        mint its own Agent Provisioning key from the browser, so an API key
        could create its own successor. Promoting somebody is a signed-in
        administrator's act at Settings > Agents.

        NO MAIL IS SENT and no invite link is minted — this deployment has no
        reliable mail path and no queue worker, so the credential is handed over
        out of band, exactly as it is in the browser.

        422 on a taken address (under `email_local`), an unknown role or group,
        a password shorter than 8 characters, or a posted `email`.

        Requires the `admin:write` scope, which resolves only while the key
        carries it AND the account's role holds `admin.access`.
        """
        payload: dict[str, Any] = {
            "name": name,
            "email_local": email_local,
            "role_id": _agent_id(role_id, argument="role_id", what="role"),
        }

        # Omitted when None so the server applies its own behaviour — "generate
        # a password" and "leave the memberships empty" respectively — rather
        # than this client keeping a second, drifting copy of either. An EMPTY
        # groups list is sent through, because `[]` is a real instruction.
        if groups is not None:
            payload["groups"] = _agent_group_ids(groups)

        if password is not None:
            payload["password"] = password

        return await self._request("POST", "/api/v1/admin/agents", json=payload)

    async def update_agent(
        self,
        user_id: int,
        *,
        name: str | None = None,
        role_id: int | None = None,
        groups: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        """PATCH /api/v1/admin/agents/{id} — name, role and groups. 200.

        ``{"data": {…the agent shape…}}``

        PARTIAL. An omitted argument is not sent and is not edited, so renaming
        an agent leaves their role and their groups exactly where they were.
        `groups=[]` IS an edit and clears every membership.

        🔴 NOR CAN IT PROMOTE AN ACCOUNT ONTO A ROLE GRANTING `admin.access` —
        a 422 on `role_id`, exactly as on create, and for the same reason:
        otherwise the create refusal is one PATCH away from useless. Demotion is
        allowed (while another administrator remains), so this is one-way over
        the API. `list_roles` reports `assignable`.

        🔴 THIS CANNOT CHANGE AN EMAIL OR A PASSWORD, and both are refused by
        the server rather than ignored. A password change ends the account's
        live sessions and rotates its legacy agent token, which must not be a
        side effect of an unattended rename; it has no unattended-safe shape at
        all, because the browser reveals the new value once on a screen. Both
        stay in the Ebteqdesk web UI.

        ⚠️ MOVING THE LAST ADMINISTRATOR OFF AN ADMIN ROLE IS REFUSED — a 422 on
        `role_id`. It would leave the installation with nobody able to open
        Settings, and nobody able to call these endpoints either, so it is not
        reachable from here by design.

        404 if no user has that id.

        Requires the `admin:write` scope.
        """
        payload: dict[str, Any] = {}

        if name is not None:
            payload["name"] = name

        if role_id is not None:
            payload["role_id"] = _agent_id(role_id, argument="role_id", what="role")

        if groups is not None:
            payload["groups"] = _agent_group_ids(groups)

        return await self._request(
            "PATCH",
            f"/api/v1/admin/agents/{_agent_id(user_id, argument='user_id', what='agent')}",
            json=payload,
        )

    async def list_api_keys(self, user_id: int) -> dict[str, Any]:
        """GET /api/v1/admin/agents/{id}/keys — one agent's API keys. 200.

        ``{"data": [{"id", "name", "scopes", "effectiveScopes", "legacy",
        "expired", "expiresAt", "lastUsedAt", "createdAt"}, …],
        "meta": {"issuableScopes", "liveKeyCount", "maxPerAccount",
        "maxNameLength", "expiryPresetDays"}}``

        🔴 `scopes` IS WHAT THE KEY CARRIES; `effectiveScopes` IS WHAT IT
        RESOLVES TO. They differ exactly when a scope was stored that the
        owner's role no longer backs — which happens with no edit to the key at
        all, the moment somebody is moved to a narrower role. A key whose
        `effectiveScopes` is `[]` authenticates and can do nothing; report that
        rather than the `scopes` list, which reads as working.

        NO SECRET IS IN THIS PAYLOAD. The plaintext existed once, in the
        `issue_api_key` response, and the stored column is a sha256 of it.

        `meta.issuableScopes` is what `issue_api_key` would accept for this
        agent from this key right now. `admin:read` and `admin:write` are never
        in it.

        404 if no user has that id.

        Requires the `admin:read` scope.
        """
        return await self._get(
            f"/api/v1/admin/agents/{_agent_id(user_id, argument='user_id', what='agent')}/keys"
        )

    async def issue_api_key(
        self,
        user_id: int,
        *,
        name: str,
        scopes: Sequence[str],
        expires_in_days: int | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/admin/agents/{id}/keys — issue a key to that agent. 201.

        ``{"data": {…the key shape…}, "plainTextToken": "12|abc…"}``

        🔴 `plainTextToken` IS THE ONLY MOMENT THAT STRING EXISTS ANYWHERE. The
        server stores a sha256 of it and there is no way back, from this API or
        from the browser. Give it to the user in the same breath or the key is
        useless and has to be revoked and re-issued.

        🔴 `admin:read` AND `admin:write` CAN NEVER BE ISSUED HERE, by any
        caller, however privileged — not by an administrator, not to an
        administrator, not alongside a legitimate scope. The server subtracts
        them last and unconditionally so that a provisioning key cannot mint a
        successor to itself. An Agent Provisioning key comes only from a
        signed-in human at Settings > API keys, for their own account.

        🔴 AND A KEY CANNOT GRANT WHAT THE CALLING KEY DOES NOT ITSELF RESOLVE.
        The issued set is
            requested ∩ the CALLING key's resolved scopes
                      ∩ what the OWNER's role is permitted to hold
                      ∩ what the OWNER's role abilities back
                      − {admin:read, admin:write}
        …and a scope any term refuses is a 422 that NAMES IT, with `refusals`
        keyed by scope carrying the term codes `caller_key`,
        `owner_role_policy`, `owner_role_ability` and `never_issuable`. NOTHING
        IS CREATED when any one scope is refused — the server never issues a
        quietly smaller key — so a 201 means the key carries exactly what was
        asked for. Read `meta.issuableScopes` from `list_api_keys` or
        `get_agent` first to avoid the round trip.

        `expires_in_days` must be one of 7, 30, 60, 90, 180 or 365; omit it for
        a key that never expires. An expired key is refused at authentication by
        the server, with no sweeper needed.

        Names are unique per AGENT, case-insensitively, and an agent may hold at
        most 10 unexpired keys. Both are 422s on `name`.

        404 if no user has that id.

        Requires the `admin:write` scope.
        """
        payload: dict[str, Any] = {
            "name": name,
            "scopes": _api_key_scopes(scopes),
        }

        # Omitted when None so the server's "never expires" branch applies,
        # rather than this client sending a null it would have to interpret.
        if expires_in_days is not None:
            payload["expires_in_days"] = expires_in_days

        return await self._request(
            "POST",
            f"/api/v1/admin/agents/{_agent_id(user_id, argument='user_id', what='agent')}/keys",
            json=payload,
        )

    async def revoke_api_key(self, user_id: int, api_key_id: int) -> dict[str, Any]:
        """DELETE /api/v1/admin/agents/{id}/keys/{key} — revoke one key. 200.

        ``{"data": {"id": 12, "revoked": true}}`` — a receipt for a row that no
        longer exists.

        🔴 IRREVERSIBLE AND IMMEDIATE. The row is deleted, not flagged, and the
        server resolves a bearer token by looking the row up — so the key stops
        working on the very next request from any process, and nothing puts it
        back. Re-issuing produces a NEW key with a new secret that whatever was
        using the old one has to be reconfigured with. Name the key and get the
        user's agreement first; `list_api_keys` shows `name` and `lastUsedAt`,
        which is how you tell a dead integration from a live one.

        Nothing else the agent holds is affected — not their other keys, not
        their password, and not `users.token`, the separate legacy credential
        the mobile agent app uses.

        404, with the SAME body for "no such key" and "that key belongs to
        another agent". The two are deliberately indistinguishable so this
        endpoint cannot be used to enumerate which key ids are live.

        Requires the `admin:write` scope.
        """
        owner = _agent_id(user_id, argument="user_id", what="agent")
        key = _agent_id(api_key_id, argument="api_key_id", what="API key")

        return await self._request(
            "DELETE", f"/api/v1/admin/agents/{owner}/keys/{key}"
        )

    # ------------------------------------------------------------------ #
    # WARNI ACTIVITY — how much of the POS each business uses
    # ------------------------------------------------------------------ #
    #
    # Four READS over `/api/v1/activity*`, all on the `activity:read` scope,
    # which is backed by the `reports.view` ability — the same gate as the
    # /reports/activity web page, so the API and the page admit the same
    # people. A legacy `*` key covers it.
    #
    # 🔴 THE FIFTH ROUTE IS DELIBERATELY NOT HERE. `POST /api/v1/activity/daily`
    # is the OLD machine push: it OVERWRITES a whole day of activity for every
    # business in the push, it is backed by `admin.access`, and `*` does not
    # cover it. It is a system integration's endpoint, not an agent's, and a
    # method for it here would be one tool registration away from letting a
    # model rewrite a day of customer data. Do not add one.
    #
    # 🔴 NOR IS THE FETCH. The server now PULLS the data from Warni itself, once
    # an hour; an administrator can also press "Fetch from Warni" on the report
    # page. That is an admin-only web action holding a PHP worker for minutes
    # while it walks Warni's whole directory. It has no API route an agent key
    # could reach, and none should be added for one.
    #
    # PLANS. The default is `all` ("All businesses"), a stored code and not a
    # wildcard; `free` is the legacy code an old push used. Which codes are
    # legal is server config, so only the shape is checked here and a wrong
    # code is the server's 422.
    #
    # THE SCORE, stated once so every docstring below can refer to it:
    #
    #   - Five modules: `checkout`, `appointment`, `member`, `catalog`,
    #     `expense`. On one day a module scores 1 point if it was used at all
    #     (count > 0) and 0 otherwise, so a DAILY score is an integer 0..5.
    #   - Over a week (ISO Mon–Sun) or a calendar month, `points[module]` is
    #     DAYS USED ÷ DAYS REPORTED (0..1, 2dp) — a share of days, NOT a count
    #     and NOT a percentage — and `score` is the AVERAGE DAILY SCORE (0..5,
    #     1dp).
    #   - Only REPORTED dates count (a date the POS pushed for the plan). A
    #     business missing on a reported date scores 0 for that day.
    #   - Tier comes from the ROUNDED score: >= 4 `active`, >= 2 `exploring`,
    #     otherwise `dormant`.
    #
    # VALIDATION. Unlike the ticket lists, these methods DO refuse bad enums and
    # out-of-range numbers locally. The server's vocabulary here is small,
    # fixed in code (App\Reporting\ActivityTiers / ActivityReport) rather than
    # in the database, and identical for every role, so a local copy cannot go
    # stale per caller the way a role-dependent rule would; and a model reaching
    # for `tier="inactive"` or `days=365` is better told the legal set before a
    # round trip than handed a 422. `plan` is the exception: its legal values
    # are a SERVER CONFIG list (`activity.plans`, today "all" and "free"), so
    # it is passed through and a wrong one comes back as the server's 422.

    async def activity_list(
        self,
        *,
        period: str = "day",
        date: str | None = None,
        plan: str = ACTIVITY_DEFAULT_PLAN,
        tier: str | None = None,
        min_score: float | None = None,
        max_score: float | None = None,
        query: str | None = None,
        sort: str | None = None,
        page: int | None = None,
        per_page: int | None = None,
    ) -> dict[str, Any]:
        """GET /api/v1/activity — one row per business for a day, week or month.

        200 ``{"data": [{"business_id", "business_name", "customer_contact_id",
        "points": {module: 0..1}, "days_used": {module: n}, "counts": {module:
        n}, "score", "tier", "days_reported", "days_present",
        "last_activity_date"}, …], "meta": {"plan", "plan_label", "period", "date", "range":
        {"from", "to"}, "days_reported", "pagination": {"total", "per_page",
        "current_page", "last_page"}, "filters": {…}}}``

        `period` is day|week|month; `date` (Y-m-d) is any day inside the period
        and defaults server-side to the latest reported date. `tier`,
        `min_score`/`max_score` (0..5, compared against the ROUNDED score) and
        `query` (business id or name, at most 100 characters, sent as `q`)
        narrow the set; `sort` is score_desc (the server default) | score_asc
        | name. `per_page` 1..100, default 25. Pagination is in
        `meta.pagination`, not a `links` block.

        Requires the `activity:read` scope (backed by `reports.view`).
        """
        params: dict[str, Any] = {
            "period": _activity_choice(period, "period", ACTIVITY_PERIODS),
            "plan": _activity_plan(plan),
        }

        if date is not None:
            params["date"] = _activity_date(date, "date")

        if tier is not None:
            params["tier"] = _activity_choice(tier, "tier", ACTIVITY_TIERS)

        low = None if min_score is None else _activity_score(min_score, "min_score")
        high = None if max_score is None else _activity_score(max_score, "max_score")

        if low is not None and high is not None and low > high:
            raise ValueError(
                f"min_score ({low}) must not be greater than max_score ({high})."
            )

        if low is not None:
            params["min_score"] = low

        if high is not None:
            params["max_score"] = high

        if query is not None:
            if not isinstance(query, str):
                raise ValueError(f"query must be a string, not {query!r}.")

            if len(query) > ACTIVITY_QUERY_MAX:
                raise ValueError(
                    f"query must be at most {ACTIVITY_QUERY_MAX} characters, "
                    f"not {len(query)}."
                )

            params["q"] = query

        if sort is not None:
            params["sort"] = _activity_choice(sort, "sort", ACTIVITY_SORTS)

        if page is not None:
            params["page"] = _activity_int(page, "page", 1, None)

        if per_page is not None:
            params["per_page"] = _activity_int(
                per_page, "per_page", 1, ACTIVITY_MAX_PER_PAGE
            )

        return await self._get("/api/v1/activity", params)

    async def activity_summary(
        self,
        *,
        period: str = "day",
        date: str | None = None,
        plan: str = ACTIVITY_DEFAULT_PLAN,
    ) -> dict[str, Any]:
        """GET /api/v1/activity/summary — the whole plan's totals for a period.

        200 ``{"data": {"plan", "period", "range", "days_reported",
        "reported_dates": [...], "customers", "average_score", "tiers":
        {"active", "exploring", "dormant"}, "modules": {module:
        {"businesses_used", "rate"}}, "average_points": {module}}, "meta":
        {"plan", "plan_label", "period", "date"}}``

        `modules.*.rate` is businesses_used ÷ customers (0..1).
        `average_points` is the mean days-used ratio across businesses.

        Requires the `activity:read` scope (backed by `reports.view`).
        """
        params: dict[str, Any] = {
            "period": _activity_choice(period, "period", ACTIVITY_PERIODS),
            "plan": _activity_plan(plan),
        }

        if date is not None:
            params["date"] = _activity_date(date, "date")

        return await self._get("/api/v1/activity/summary", params)

    async def activity_list_dormant(
        self,
        *,
        days: int = ACTIVITY_DORMANT_DEFAULT_DAYS,
        as_of: str | None = None,
        plan: str = ACTIVITY_DEFAULT_PLAN,
    ) -> dict[str, Any]:
        """GET /api/v1/activity/dormant — businesses dormant N reported days running.

        A business is returned when its DAILY score was <= 1 on EACH of the last
        `days` REPORTED dates up to `as_of` (default: the latest reported date).
        A reported date the business is missing from counts as a 0.

        200 ``{"data": [{"business_id", "business_name", "customer_contact_id",
        "streak_dates": [...], "streak": [{"date", "score", "present"}, …],
        "latest": {"date", "plan", "business_name", "customer_contact_id",
        "counts", "points", "score", "tier"}}, …], "meta": {"plan", "plan_label",
        "days", "as_of", "total"}}``. NOT paginated.

        `days` is 1..90, default 3.

        Requires the `activity:read` scope (backed by `reports.view`).
        """
        params: dict[str, Any] = {
            "days": _activity_int(days, "days", 1, ACTIVITY_DORMANT_MAX_DAYS),
            "plan": _activity_plan(plan),
        }

        if as_of is not None:
            params["as_of"] = _activity_date(as_of, "as_of")

        return await self._get("/api/v1/activity/dormant", params)

    async def activity_get_customer(
        self,
        business_id: str,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        plan: str = ACTIVITY_DEFAULT_PLAN,
    ) -> dict[str, Any]:
        """GET /api/v1/activity/customers/{business_id} — one business, day by day.

        200 ``{"data": [{"date", "present", "plan", "counts", "points": {module:
        0|1}, "score", "tier"}, …], "meta": {"business_id", "business_name",
        "customer_contact_id", "plan", "plan_label", "from", "to", "days_reported",
        "days_present"}}``

        ZERO-FILLED across REPORTED dates: a reported day the business is
        missing from is a row with `present: false`, `plan: null` and a score of
        0 — not an absent row. Days nobody reported are absent entirely.

        `date_from`/`date_to` (Y-m-d, sent as `from`/`to`) default to the 30
        days ending at the latest reported date; at most 366 days, and a
        reversed range is the server's 422. 404 when the business has NEVER had
        a row, for any plan.

        `business_id` must match ``[A-Za-z0-9_.-]{1,50}`` — the route's own
        constraint — and is percent-encoded into the path regardless.
        The server matches a UUID id case-insensitively (it stores it lower-case)
        and any other id, such as ``WRN-10001``, exactly as written; the id is
        sent as given.

        Requires the `activity:read` scope (backed by `reports.view`).
        """
        params: dict[str, Any] = {}

        if date_from is not None:
            params["from"] = _activity_date(date_from, "date_from")

        if date_to is not None:
            params["to"] = _activity_date(date_to, "date_to")

        params["plan"] = _activity_plan(plan)

        segment = quote(_activity_business_id(business_id), safe="")

        return await self._get(f"/api/v1/activity/customers/{segment}", params)

    # ------------------------------------------------------------------ #
    # Transport
    # ------------------------------------------------------------------ #

    async def _get(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        diagnose_scope: bool = True,
    ) -> dict[str, Any]:
        """A GET. See `_request`, which every verb goes through."""
        return await self._request(
            "GET", path, params=params, diagnose_scope=diagnose_scope
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        files: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        diagnose_scope: bool = True,
        on_scope_error: Any = None,
    ) -> dict[str, Any]:
        """One request, its decoded body. See `_exchange`, which is this method
        plus the response headers and is where the work is done."""
        payload, _ = await self._exchange(
            method,
            path,
            params=params,
            json=json,
            files=files,
            data=data,
            headers=headers,
            diagnose_scope=diagnose_scope,
            on_scope_error=on_scope_error,
        )

        return payload

    async def _exchange(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        files: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        diagnose_scope: bool = True,
        on_scope_error: Any = None,
    ) -> tuple[dict[str, Any], httpx2.Headers]:
        """One request, one place where every failure mode is classified.

        Returns the decoded body AND the response headers. Every caller but one
        wants only the first and goes through `_request`; the start of a large
        upload also needs the desk's own `Date`, to measure an upload address's
        lifetime on the clock that signed it rather than on this machine's.

        Reads and writes share this method rather than having a `_get` and a
        `_post` that each classify their own failures. The classification is the
        valuable part and it is identical for both — the write endpoints answer
        with the same error envelope, the same `required_scope`, the same
        throttle — so two copies would mean the write half quietly missing the
        next thing added to the read half, which is exactly how `required_ability`
        would have ended up handled on one verb and not the other.

        The order of checks matters and is not arbitrary:

        1. Transport failure first — there is no status code to reason about.
        2. Then JSON decodability, INCLUDING on error statuses. A 502 from nginx
           and a 403 from Ebteqdesk are both "not 200", but only one of them has
           a `required_scope` in it, and treating an HTML error page as an empty
           JSON body would produce the useless message "403" with no detail.
        3. Then the status. A JSON body on a 4xx/5xx is the documented error
           envelope and is turned into the specific exception for it.
        4. Only then, success — and the body is returned unchanged.

        On (4): nothing is renamed on the way out. The payload keys are the
        external contract (`requester`, not `customer_contact`; `key`, not `id`),
        and a client that "tidies" them becomes a second, undocumented contract
        that drifts from the first. A model reading this output should be able
        to compare it to a curl of the same URL and see the same bytes.

        `on_scope_error` is an optional last pass over an already-built (and,
        where possible, already-diagnosed) ScopeError, for the one endpoint
        whose refusal means something extra that only the CALLER knows — see
        `comment_on_ticket`. It runs after the diagnosis so the two compose, it
        is a pure function, and it is absent everywhere else.

        A body is sent only when `json` is not None, so a DELETE and a
        bodyless POST go out with no `Content-Type` and no `{}` — a bodyless
        POST is what the escalate endpoints document, and inventing an empty
        object for them would be this client asserting a request shape the API
        never described.

        `files` is the multipart form, and two endpoints use it —
        `upload_kb_media` and the ticket-attachment route. It travels through
        this method rather than a private `_post_file` for the reason this
        docstring opens with: the classification is the valuable part, it is
        identical for a multipart request, and a second copy would be the write
        half quietly missing the next thing added to the JSON half. 🔴 `json`
        and `files` are mutually exclusive on the wire — httpx would encode the
        JSON body and drop the parts — so passing both is refused here rather
        than silently losing the upload.

        `data` is the plain form fields that travel beside `files` in the same
        multipart body (the attachment's optional `note`), and `headers` is
        added to this one request — `Idempotency-Key`, on the two routes that
        require it. Neither can displace the client's own `Authorization` or
        `Accept`: no caller passes those names.
        """
        if json is not None and files is not None:
            raise ValueError(
                "A request carries either a JSON body or a multipart form, "
                "never both."
            )

        try:
            response = await self._http.request(
                method,
                path,
                params=dict(params or {}),
                json=None if json is None else dict(json),
                files=None if files is None else dict(files),
                data=None if data is None else dict(data),
                headers=None if headers is None else dict(headers),
            )
        except httpx2.HTTPError as exc:
            raise TransportError(
                self._config.base_url, exc, self._config.timeout
            ) from exc

        payload = _decode_json(response, path)

        if response.status_code >= 400:
            raise await self._error_for(
                response,
                payload,
                path,
                diagnose_scope=diagnose_scope,
                on_scope_error=on_scope_error,
            )

        # A 3xx reaches here because redirects are not followed: `payload` will
        # have failed to decode and _decode_json has already raised. A 2xx that
        # is not 200 falls through as success: the creates answer 201, and
        # `update_kb_article` answers 202 when it stages a revision against a
        # published article instead of editing it.
        #
        # 🔴 THAT 202 IS NOT DISTINGUISHED HERE, ON PURPOSE. Surfacing the
        # status as a derived key would be exactly the second, drifting contract
        # the module docstring forbids. The server discriminates for us — a
        # staged 202 carries a top-level `revision` key and an applied 200 never
        # does — so the caller branches on the payload, the same bytes a curl
        # would show. See update_kb_article.
        return payload, response.headers

    async def _get_image(
        self, path: str, params: Mapping[str, Any] | None = None
    ) -> AttachmentImage:
        """A GET whose SUCCESS body is image bytes rather than JSON.

        The one method that does not go through `_request`, and only for the
        success half: `_request` decodes every body as JSON, so a PNG would come
        back as MalformedResponseError. Everything else is shared —
        `_error_for` classifies the failures, so the scope diagnosis, the
        throttle handling and every future addition to the error surface reach
        this path with no second copy. That sharing is the reason `_error_for`
        was split out of `_request` at all.

        ⚠️ THE CONTENT TYPE IS CHECKED, not assumed. A 200 carrying HTML means a
        proxy or a login wall answered instead of the application — the exact
        case MalformedResponseError describes — and handing those bytes back as
        an "image" would produce a broken image in a chat with no explanation
        anywhere. Ebteqdesk answers this route with image/* on success and JSON
        on every refusal, so anything else is somebody else replying.
        """
        try:
            response = await self._http.request(
                "GET", path, params=dict(params or {})
            )
        except httpx2.HTTPError as exc:
            raise TransportError(
                self._config.base_url, exc, self._config.timeout
            ) from exc

        if response.status_code >= 400:
            # Refusals on this route are the ordinary JSON envelope, so they
            # decode and classify exactly as everywhere else. An HTML error page
            # still becomes MalformedResponseError, from _decode_json.
            raise await self._error_for(
                response, _decode_json(response, path), path
            )

        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()

        if not content_type.startswith("image/"):
            raise _malformed(response, path)

        return AttachmentImage(
            data=response.content,
            mime_type=content_type,
            width=_int_header(response, "X-Image-Width"),
            height=_int_header(response, "X-Image-Height"),
            source_width=_int_header(response, "X-Image-Source-Width"),
            source_height=_int_header(response, "X-Image-Source-Height"),
            source_bytes=_int_header(response, "X-Image-Source-Bytes"),
            # 🔴 READ, never derived. See AttachmentImage.downscaled: the byte
            # sizes give the wrong answer on exactly the images this endpoint
            # exists to shrink.
            downscaled=_flag_header(response, "X-Image-Downscaled"),
        )

    async def _error_for(
        self,
        response: httpx2.Response,
        payload: Mapping[str, Any],
        path: str,
        *,
        diagnose_scope: bool = True,
        on_scope_error: Any = None,
    ) -> ApiError:
        """Build — never raise — the exception for one error response.

        Split out of `_request` when the binary attachment path arrived, and
        split rather than copied for the reason `_request` gives about sharing
        one classifier: two copies would mean the next thing added to one
        quietly missing from the other, which is exactly how `required_ability`
        would have ended up handled on one verb and not the other.

        It RETURNS the error and the caller raises it, so the raise stays at the
        call site where a reader can see it and so no `await` hides a control
        transfer inside a helper.
        """
        error = api_error_for(
            status_code=response.status_code,
            path=path,
            payload=payload,
            retry_after=response.headers.get("Retry-After"),
        )

        if isinstance(error, ScopeError):
            if diagnose_scope:
                error = await self._diagnose_scope(error)

            if on_scope_error is not None:
                error = on_scope_error(error)

        # N1: an ability refusal is diagnosable too, and for the same cost.
        # `diagnose_scope` gates it as well — the flag means "this call may
        # spend one extra request on diagnosis", not "the failure was a scope".
        elif isinstance(error, AbilityError) and diagnose_scope:
            error = await self._diagnose_ability(error)

        # N20: a 404 from a ticket WRITE may be the caller's OWN ticket, and
        # nothing in the body can say so. Only writes — the read paths share
        # some of these URLs and their 404 means what it says.
        elif (
            isinstance(error, NotFoundError)
            and diagnose_scope
            and response.request.method != "GET"
            and path.startswith("/api/v1/tickets/")
        ):
            error = await self._diagnose_ticket_write_404(error)

        return error


    async def _diagnose_ticket_write_404(self, error: ApiError) -> ApiError:
        """Add the own-ticket explanation to a ticket-write 404 (N20).

        The server answers ONE body for "no such ticket", "somebody else's" and
        — for a key that does not resolve `ticket:write` — "yours, but this key
        cannot write to it". That identity is the anti-enumeration property and
        the server must not break it.

        The client can say the third case out loud without breaking anything,
        because the fact it adds is about the CALLER'S OWN KEY rather than about
        any ticket: `apiKey.scopes` from GET /api/v1/user. The sentence is
        appended to every such 404 identically, so it still cannot tell one id
        from another.

        Same failure discipline and the same cost as the other two diagnostics:
        one extra GET, error path only, and every way it can go wrong returns the
        original refusal untouched.
        """
        if error.path == USER_PATH:
            return error

        try:
            payload = await self._get(USER_PATH, diagnose_scope=False)

            api_key = payload.get("data", {}).get("apiKey")

            if not isinstance(api_key, dict):
                return error

            scopes = api_key.get("scopes")

            if not isinstance(scopes, list):
                return error

            return ticket_write_not_found(error, scopes=scopes)
        except Exception:
            # Broad and silent, for the reason `_diagnose_scope` gives.
            return error

    async def _diagnose_ability(self, error: AbilityError) -> AbilityError:
        """Turn "an ability failed" into which KIND of ability failure it is (N1).

        The scope path already spends one extra GET to answer "key or role?".
        An ability refusal has no key half, but it does have two very different
        causes that need opposite remedies:

            the role does not hold it   -> maybe a grant, maybe the role's nature
            the role DOES hold it       -> this TICKET was refused; no grant helps

        `permissions` from GET /api/v1/user answers it outright. Nothing new is
        disclosed — that list is the caller's own and is already served in full
        to any valid key — and the server's 403 body is untouched, which is what
        keeps it a pure function of the ability string rather than a role oracle.

        🔴 WHY IT IS WORTH A REQUEST. The caller most likely to hit this has no
        browser to fall back on, and an account refused an ability its role is
        never meant to hold — `ticket.close` on a specialist seat, say — gets
        the same message as one that is simply missing a grant. Told only "ask
        an administrator", its owner goes and asks for something they should
        not be given.

        Same failure discipline as `_diagnose_scope`: it runs only on an
        already-failing path, and every way it can go wrong returns the original
        refusal untouched. A diagnostic that turned a 403 into a 429 would be
        worse than no diagnostic.

        ⚠️ COST: one extra GET on the error path, so a refused call is TWO
        requests against the 60/minute account throttle rather than one. The
        same is true of `_diagnose_scope`. A client looping over ids it cannot
        touch therefore reaches the limit in half the calls it would expect —
        see the module docstring.
        """
        if not error.required_ability or error.path == USER_PATH:
            return error

        try:
            payload = await self._get(USER_PATH, diagnose_scope=False)

            permissions = payload.get("data", {}).get("permissions")

            if not isinstance(permissions, list):
                return error

            return diagnosed_ability_error(error, permissions=permissions)
        except Exception:
            # Broad and silent, for the reason `_diagnose_scope` gives.
            return error

    async def _diagnose_scope(self, error: ScopeError) -> ScopeError:
        """Spend one extra request to turn "a scope failed" into a remedy.

        A 403 naming a scope does not say WHICH half of `key ∩ role` failed, and
        the server will not say — telling the holder of a stolen key that the
        owner's role changed is a probe into that account. GET /api/v1/user is
        the sanctioned way round it: it is the one route needing no scope, so
        any valid key reaches it, and it returns `apiKey.requested` (the key)
        beside `apiKey.scopes` (the intersection). Comparing those two arrays
        gives the answer with no prose parsing anywhere.

        🔴 THIS MUST NEVER MASK THE ORIGINAL 403. It runs only on a path that is
        already failing, and every way it can go wrong — the identity call is
        itself rate limited, the token gets revoked between the two requests, a
        proxy returns HTML, the payload has no `apiKey` — ends the same way: the
        caller gets the refusal it actually hit, carrying the server's own
        sentence. A diagnostic that can turn a 403 into a 429 would be worse
        than no diagnostic, so the except clause is deliberately broad and
        deliberately silent.

        Cost: one extra GET, on the error path only. A successful call never
        pays it.
        """
        # Only a scope refusal is diagnosable, and only via a DIFFERENT path.
        # `whoami` needs no scope so it cannot raise a ScopeError today; the
        # guard is here so that a future scoped identity route degrades to the
        # undiagnosed message instead of recursing.
        if not error.required_scope or error.path == USER_PATH:
            return error

        try:
            # diagnose_scope=False: one level only. If this call is itself
            # refused, that refusal is returned to nobody — see below.
            payload = await self._get(USER_PATH, diagnose_scope=False)

            api_key = payload.get("data", {}).get("apiKey")

            if not isinstance(api_key, dict):
                return error

            requested = api_key.get("requested")
            scopes = api_key.get("scopes")

            if not isinstance(requested, list) or not isinstance(scopes, list):
                return error

            return diagnosed_scope_error(error, requested=requested, scopes=scopes)
        except Exception:
            # Broad on purpose, and it swallows the diagnostic's own failure
            # rather than chaining it: the user asked about tickets, not about
            # the identity endpoint, and a second error here would bury the
            # first. `except Exception` does not catch CancelledError, so a
            # cancelled task still cancels.
            return error


def _decode_json(response: httpx2.Response, path: str) -> dict[str, Any]:
    """The body as a JSON object, or MalformedResponseError.

    A JSON array or scalar is treated as malformed rather than being wrapped:
    every documented /api/v1 response is an object with a `data` key, so a bare
    array means something other than Ebteqdesk answered, and silently boxing it
    would hide that.
    """
    try:
        decoded = response.json()
    except ValueError:
        raise _malformed(response, path) from None

    if not isinstance(decoded, dict):
        raise _malformed(response, path)

    return decoded


def _malformed(response: httpx2.Response, path: str) -> MalformedResponseError:
    raw = response.content[:_SNIPPET_BYTES]
    snippet = raw.decode("utf-8", errors="replace").strip()

    return MalformedResponseError(
        status_code=response.status_code,
        path=path,
        content_type=response.headers.get("Content-Type"),
        body_snippet=snippet,
    )


def _int_header(response: httpx2.Response, name: str) -> int | None:
    """One `X-Image-*` header as an int, or None.

    None for absent AND for unparseable, deliberately. These are metadata about
    a response whose real payload is the bytes, so a stripped or garbled header
    must degrade to "unknown" rather than fail a download that succeeded — an
    intermediary dropping unknown headers is a configuration a user cannot see
    and should not have to debug to look at a screenshot.
    """
    raw = response.headers.get(name)

    if raw is None:
        return None

    try:
        return int(raw)
    except ValueError:
        return None


def _flag_header(response: httpx2.Response, name: str) -> bool | None:
    """One `X-Image-Downscaled`-style header as a bool, or None.

    "1" is True, "0" is False, and ANYTHING ELSE — absent, empty, "yes", "true",
    a stripped header — is None.

    🔴 The unknown case must not collapse to False. False is a CLAIM ("this
    image was not reduced"), and the whole reason this header exists is that the
    wrong claim there stops an agent retrying with a larger `max_dimension` when
    it cannot read the image. `_int_header` applies the same rule for the same
    reason; the difference is that a bool has a plausible-looking wrong default
    and an int does not, so it is worth saying out loud.
    """
    raw = response.headers.get(name)

    if raw == "1":
        return True

    if raw == "0":
        return False

    return None


def _local_upload(file_path: str, upload_root: str | None) -> tuple[str, bytes]:
    """Read one local file for `upload_kb_media`: (basename, bytes).

    🔴 ONLY A FILE WHOSE REAL LOCATION IS INSIDE `upload_root`. See
    `_upload_root` for the directory's own rules and `_inside_upload_root` for
    the containment check; this function refuses before it opens anything.

    🔴 ONLY THE BASENAME IS SENT. The directory the file came out of is the
    user's own filesystem layout — `/Users/someone/Desktop/clients/acme/…` — and
    it would be stored on the row as `original_name` and shown to every agent
    reading the article. It also cannot be a path component server-side (the
    stored path is a generated ULID; App\\Kb\\MediaStorage is emphatic about
    that), so sending the rest buys nothing and discloses something.

    Every failure is one exception carrying the path back, because the user is
    the only one who can correct it. `is_dir` is checked separately from the
    read: opening a directory raises IsADirectoryError on Linux and
    PermissionError on Windows, and "permission denied" for a folder somebody
    named by mistake is the wrong sentence in both.

    The whole file is read into memory. The server's caps are 10 MB for an image
    and 50 MB for a video, and streaming a body this size would buy nothing
    while making the retry hazard in `upload_kb_media` harder to reason about.
    🔴 That is true of THIS endpoint and no other: `attach_file_to_ticket` may be
    handed 500 MB, shares the path resolution above with this function, and
    must never call it for the bytes.
    """
    path, real = _resolved_local_path(file_path, upload_root)

    if real.is_dir():
        raise LocalFileError(str(path), "that is a directory, not a file")

    if not real.exists():
        raise LocalFileError(str(path), "no such file on this machine")

    try:
        # The RESOLVED path, never `path`: the containment check was made
        # against `real`, so reading anything else would be checking one file
        # and opening another.
        content = real.read_bytes()
    except OSError as exc:
        raise LocalFileError(str(path), f"it could not be read ({exc.strerror or exc})") from None

    if not content:
        # The server would answer 422 on the `mimetypes:` rule with a message
        # about supported types, which is a confusing thing to be told about an
        # empty file. Refused here, where the reason is knowable.
        raise LocalFileError(str(path), "the file is empty")

    # The name the USER gave, not the symlink target's: a link called
    # `settings.png` inside the upload folder is uploaded as `settings.png`.
    return path.name, content


def _regular_file(path: Path, real: Path) -> os.stat_result:
    """`stat` the resolved path and require a regular, non-empty, readable file.

    The second half of a local read for `attach_file_to_ticket`, and the half
    that differs from `_local_upload`: NOTHING IS OPENED AND NOTHING IS READ.
    The size and the modification time that come back are what the idempotency
    key is made from and what a transfer later checks the file against.

    A device, a pipe or a socket is refused by name. `_local_upload` never had
    to: it calls `read_bytes()`, which simply returns whatever such a thing
    yields. A streamed upload of a declared length has no sensible meaning for
    one — and `/dev/zero` inside the upload folder would be 500 MB of nothing.
    """
    try:
        info = os.stat(real)
    except FileNotFoundError:
        raise LocalFileError(str(path), "no such file on this machine") from None
    except OSError as exc:
        raise LocalFileError(
            str(path), f"it could not be read ({exc.strerror or exc})"
        ) from None

    if stat.S_ISDIR(info.st_mode):
        raise LocalFileError(str(path), "that is a directory, not a file")

    if not stat.S_ISREG(info.st_mode):
        raise LocalFileError(
            str(path),
            "that is not a regular file — a device, a pipe or a socket cannot "
            "be attached",
        )

    if info.st_size == 0:
        # The desk refuses an empty file too, with a message about supported
        # types. Refused here, where the reason is knowable.
        raise LocalFileError(str(path), "the file is empty")

    if not os.access(real, os.R_OK):
        raise LocalFileError(str(path), "it could not be read (permission denied)")

    return info


def _attachment_key(
    ticket_id: int, real: Path, size: int, mtime_ns: int, note: str | None
) -> str:
    """The `Idempotency-Key` for one attachment: 64 hex characters.

    The SHA-256 of the ticket id, the RESOLVED path, the size, the modification
    time in nanoseconds and the note text, joined by newlines.

      - STABLE ACROSS A MODEL'S RETRIES. A model that retries after a timeout
        makes a new tool call with nothing to carry over; the same arguments
        are the only handle it has, and they hash to the same key however the
        path was spelled (`~/x`, a relative path and a symlink to the file all
        resolve to one location).
      - DIFFERENT WHEN THE FILE IS. A file saved again has a new modification
        time, so it is a new upload rather than a replay of the old one.
      - FREE. Hashing the content would be a full pass over 500 MB before the
        first byte was sent.

    The path is HASHED AND NEVER SENT: it is the user's filesystem layout. The
    desk treats the key as opaque (1–200 characters, no `:`).

    ⚠️ What this cannot see: content that changed while the size and the
    modification time did not. The desk can, on the multipart route — it binds
    the key to the bytes — and answers `idempotency_key_reused`.
    """
    material = "\n".join(
        (str(ticket_id), str(real), str(size), str(mtime_ns), note or "")
    )

    # `backslashreplace` so that a path with undecodable bytes in it (legal on
    # POSIX, and how Python surfaces them) still hashes rather than raising.
    return hashlib.sha256(material.encode("utf-8", "backslashreplace")).hexdigest()


def _declared_type(name: str) -> str:
    """The `mime_type` declared when starting a large upload, from the NAME.

    See `_NAME_TYPES`. Unknown extensions are declared as what they are —
    unknown — and the desk refuses them at start, before any bytes move.
    """
    extension = os.path.splitext(name)[1].lower()

    return _NAME_TYPES.types_map[True].get(extension) or "application/octet-stream"


def _unchanged(attachment: TicketAttachment) -> bool:
    """Is the file still the one that was prepared? Size and modification time,
    from `stat` on the resolved path — the same call that made the key, so the
    two can never disagree about what "the same file" means. A file that has
    gone, or been replaced by a different one under the same name, is changed.
    """
    try:
        info = os.stat(attachment.real_path)
    except OSError:
        return False

    return (
        stat.S_ISREG(info.st_mode)
        and info.st_size == attachment.size
        and info.st_mtime_ns == attachment.mtime_ns
    )


def _open_unchanged(attachment: TicketAttachment) -> BinaryIO:
    """Open the resolved path for reading, and confirm it is still the file.

    The check comes AFTER the open, so the handle that is read is one the check
    was made with in hand. Raises `UploadTransferError` rather than
    `LocalFileError`: by the time a transfer opens the file a request may
    already have gone, and "nothing was sent" would no longer be true.
    """
    try:
        handle = open(attachment.real_path, "rb")
    except OSError:
        handle = None

    if handle is not None and _unchanged(attachment):
        return handle

    if handle is not None:
        handle.close()

    raise UploadTransferError(
        UploadTransferError.FILE_CHANGED, attachment.name, bytes_total=attachment.size
    )


class _FilePart:
    """The `file` part of the multipart request: a handle that cannot be read
    whole.

    httpx2's multipart encoder takes a file-like object, asks `fileno()` for its
    length — which is how the request gets a `Content-Length` instead of chunked
    encoding — and then calls `read(65536)` until it gets nothing back. This is
    that object, and it differs from the bare handle in three ways:

      - a read NEVER returns more than `_PART_READ_CEILING_BYTES`, whatever is
        asked for, `read()` with no argument included;
      - it stops at the size that was prepared, so a file that grew is not sent
        long;
      - it checks the file is unchanged before every read and counts what it
        hands over, for the progress report.

    Deliberately no `seek` and no `tell`: the encoder rewinds a part that has
    them, and this one is read exactly once.
    """

    def __init__(
        self, handle: BinaryIO, attachment: TicketAttachment, progress: TransferProgress
    ) -> None:
        self._handle = handle
        self._attachment = attachment
        self._progress = progress
        self._remaining = attachment.size

    def fileno(self) -> int:
        return self._handle.fileno()

    def read(self, size: int | None = -1) -> bytes:
        if self._remaining <= 0:
            return b""

        if not _unchanged(self._attachment):
            raise _Abandon()

        want = _PART_READ_CEILING_BYTES
        if size is not None and 0 <= size < want:
            want = size

        chunk = self._handle.read(min(want, self._remaining))

        if not chunk:
            # Shorter than it was a moment ago.
            raise _Abandon()

        self._remaining -= len(chunk)
        self._progress.sent(len(chunk))

        return chunk


async def _file_chunks(
    handle: BinaryIO,
    attachment: TicketAttachment,
    progress: TransferProgress,
    deadline_at: float,
    clock: Any,
    stopped: list[str],
) -> AsyncGenerator[bytes, None]:
    """The body of the object-store PUT: the file, a chunk at a time.

    Exactly `attachment.size` bytes, which is the `Content-Length` that was
    declared — never more if the file grew. Before EVERY chunk, the last
    included, it checks the deadline and that the file has not changed; a send
    that fails either check stops BEFORE its final bytes go, so the object
    store never gets a complete body and never creates the object.

    A stop is reported two ways: `stopped` gets the reason, for `_put_file`, and
    `_Abandon` is raised into the HTTP stack to end the request.
    """
    remaining = attachment.size

    while remaining > 0:
        if clock() >= deadline_at:
            stopped.append(UploadTransferError.DEADLINE)
            raise _Abandon()

        if not _unchanged(attachment):
            stopped.append(UploadTransferError.FILE_CHANGED)
            raise _Abandon()

        chunk = handle.read(min(_PUT_CHUNK_BYTES, remaining))

        if not chunk:
            stopped.append(UploadTransferError.FILE_CHANGED)
            raise _Abandon()

        remaining -= len(chunk)
        progress.sent(len(chunk))

        yield chunk


def _signed_upload(
    payload: Mapping[str, Any], headers: Mapping[str, str], received_at: float
) -> "_SignedUpload | str":
    """Read a start response. Returns a `_SignedUpload`, or — when the response
    is not usable — the `UploadTransferError` kind to raise.

    🔴 IT RETURNS THE FAILURE INSTEAD OF RAISING IT, for the reason
    `_SignedUpload` gives: this frame holds the response, and the response
    holds the address. An exception raised from here would take both into a
    traceback.

    The documented shape, and nothing looser::

        {"data": {"upload_id": "…", "status": "pending",
                  "upload": {"method": "PUT", "url": "https://…",
                             "headers": {"Content-Type": "video/mp4"},
                             "expires_at": "2026-10-09T07:20:00Z"}}}

        {"data": {"upload_id": "…", "status": "completed", …}}

    The address must be `https://`, with a host and without credentials in it.
    Plain http would send the file in the clear, and userinfo would make the
    HTTP client add an `Authorization` header of its own.
    """
    malformed = UploadTransferError.MALFORMED_START

    data = payload.get("data")
    if not isinstance(data, Mapping):
        return malformed

    upload_id = data.get("upload_id")
    if not isinstance(upload_id, str) or not upload_id:
        return malformed

    status = data.get("status")

    if status == "completed":
        return _SignedUpload(upload_id, completed=True, received_at=received_at)

    upload = data.get("upload")
    if status != "pending" or not isinstance(upload, Mapping):
        return malformed

    method, url, listed = upload.get("method"), upload.get("url"), upload.get("headers")

    if not isinstance(method, str) or method.upper() != "PUT":
        return malformed

    if not isinstance(url, str) or not isinstance(listed, Mapping):
        return malformed

    if not all(isinstance(k, str) and isinstance(v, str) for k, v in listed.items()):
        return malformed

    try:
        parts = urlsplit(url)
        host, has_credentials = parts.hostname, bool(parts.username or parts.password)
    except ValueError:
        return malformed

    if parts.scheme.lower() == "http":
        return UploadTransferError.INSECURE_URL

    if parts.scheme.lower() != "https" or not host or has_credentials:
        return malformed

    return _SignedUpload(
        upload_id,
        completed=False,
        received_at=received_at,
        address=url,
        headers=listed,
        expires_in=_seconds_until_expiry(upload.get("expires_at"), headers.get("Date")),
    )


def _seconds_until_expiry(expires_at: Any, date_header: str | None) -> float | None:
    """Seconds from now until ONE MINUTE BEFORE the address expires, or None.

    🔴 MEASURED ON THE DESK'S CLOCK, NOT THIS MACHINE'S. `expires_at` is an
    absolute time on the clock that signed the address, and the response's own
    `Date` header is that same clock saying what time it is now — so the
    difference is the address's remaining life with no reference to a laptop's
    idea of the time. A machine whose clock is an hour fast would otherwise
    abandon every upload before it began, and one an hour slow would trust an
    address long after it had expired. This machine's clock is the fallback
    only when the header is missing.

    None when `expires_at` is absent or unreadable: the 50-minute ceiling then
    stands alone.
    """
    expiry = _instant(expires_at)

    if expiry is None:
        return None

    now = None
    if date_header:
        try:
            now = parsedate_to_datetime(date_header)
        except (TypeError, ValueError):
            now = None

    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    return (expiry - now).total_seconds() - _EXPIRY_MARGIN_SECONDS


def _instant(value: Any) -> datetime | None:
    """An ISO 8601 instant such as `2026-10-09T07:20:00Z`, or None."""
    if not isinstance(value, str) or not value:
        return None

    try:
        # `fromisoformat` learned the `Z` suffix in Python 3.11; this package's
        # floor is 3.10.
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return None

    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _wait_seconds(retry_after: str | None, fallback: float) -> float:
    """A `Retry-After` in seconds, clamped; `fallback` when absent or unusable
    (the header may also be an HTTP date, which is not worth parsing here)."""
    try:
        seconds = float(retry_after) if retry_after is not None else fallback
    except ValueError:
        seconds = fallback

    if not math.isfinite(seconds):
        seconds = fallback

    return min(max(seconds, 0.0), _WAIT_CEILING_SECONDS)


def _resolved_local_path(file_path: str, upload_root: str | None) -> tuple[Path, Path]:
    """Where a tool's `file_path` argument really is: (as named, resolved).

    The first half of every local read on this client, and the SAME half for
    both tools that make one — `upload_kb_media` and `attach_file_to_ticket` —
    so that there is one answer to "may this server read that file" and no
    second copy of it to drift. It opens nothing and reads nothing.

      1. `_upload_root` — the folder, or ConfigurationError while it is unset.
      2. `~` is expanded, and a relative path is taken from that folder.
      3. `_inside_upload_root` — `..` and every symlink resolved, and a real
         location outside the folder refused, before the file is looked at.

    Returns the path as the user named it (for messages, and for the name that
    is sent) and its resolved location (for every read that follows). Reading
    anything but the second would be checking one file and opening another.
    """
    if not isinstance(file_path, str) or not file_path.strip():
        raise LocalFileError(str(file_path), "no path was given")

    root = _upload_root(upload_root)

    path = Path(file_path).expanduser()
    if not path.is_absolute():
        # Relative to the upload directory, NOT to this process's working
        # directory — which, for a server a host launched, is wherever the host
        # happened to be, and nothing a user could predict.
        path = root / path

    return path, _inside_upload_root(path, root)


def _upload_root(configured: str | None) -> Path:
    """`EBTEQDESK_UPLOAD_ROOT`, resolved, or ConfigurationError naming the fix.

    🔴 UNSET MEANS UPLOADS ARE OFF — NOT "ANYWHERE" AND NOT A GUESSED DEFAULT.
    A default such as `~/Downloads` was considered and rejected: it would make
    the opt-in silent, it would still be a directory full of things nobody chose
    to publish, and it would not even cover the commonest case (macOS saves
    screenshots to the Desktop). The cost is one extra `--env` at registration.

    These are ConfigurationErrors and not LocalFileErrors because the remedy is
    the operator's environment, not a different path from the user — the exact
    distinction `LocalFileError`'s docstring draws. Each message says that ONLY
    the two tools that read a local file are affected, and names both, so a
    model does not conclude the whole server is misconfigured — and does not
    conclude either that the other file tool might still work.

    ONE FOLDER FOR BOTH TOOLS (5.1.0). The variable means "the files this
    server may send to the desk", not "the files meant for the knowledge base":
    the control is about which local files may leave the machine, not about
    where on the desk they land. A second variable for ticket attachments was
    considered and rejected for that reason.

    The home directory and the filesystem root are refused as roots outright.
    Either one would satisfy the check below while restoring exactly the access
    this variable exists to take away — `~/.ssh` sits directly under one of them
    — and "set it to ~ to make the error go away" is the obvious wrong fix.
    """
    if not configured:
        raise ConfigurationError(
            f"{_LOCAL_FILE_TOOLS} are turned off: {ENV_UPLOAD_ROOT} is not "
            "set. This server reads local files only from inside that one "
            "directory, and with none configured it reads nothing. NOTHING WAS "
            "SENT TO EBTEQDESK. To enable them, the operator chooses a "
            "dedicated folder (for example ~/EbteqdeskUploads), registers the "
            f"server with `--env {ENV_UPLOAD_ROOT}=<that folder>`, restarts it, "
            f"and puts the file there. {_ONLY_LOCAL_FILE_TOOLS}; every other "
            "tool works."
        )

    root = Path(configured).expanduser()

    if not root.is_absolute():
        raise ConfigurationError(
            f"{ENV_UPLOAD_ROOT}={configured!r} is not an absolute path. A relative "
            "one would be resolved against whatever directory the MCP host "
            "launched this server from. Set it to a full path such as "
            f"~/EbteqdeskUploads. {_ONLY_LOCAL_FILE_TOOLS}."
        )

    try:
        resolved = root.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ConfigurationError(
            f"{ENV_UPLOAD_ROOT}={configured!r} does not exist on this machine. "
            "Create that folder (or point the variable at one that exists) and "
            f"restart the server. {_ONLY_LOCAL_FILE_TOOLS}."
        ) from None

    if not resolved.is_dir():
        raise ConfigurationError(
            f"{ENV_UPLOAD_ROOT}={configured!r} is not a directory. It must name "
            f"the folder uploads are read from. {_ONLY_LOCAL_FILE_TOOLS}."
        )

    try:
        home: Path | None = Path.home().resolve()
    except (OSError, RuntimeError):
        home = None

    if resolved == Path(resolved.anchor) or resolved == home:
        raise ConfigurationError(
            f"{ENV_UPLOAD_ROOT}={configured!r} is the home directory or the "
            "filesystem root, which would let these tools read SSH keys, "
            "browser profiles and every other private file under it. Choose a "
            "dedicated folder that holds only files meant to be sent to the "
            f"desk, such as ~/EbteqdeskUploads. {_ONLY_LOCAL_FILE_TOOLS}."
        )

    return resolved


def _inside_upload_root(path: Path, root: Path) -> Path:
    """The real location of `path`, or LocalFileError if it is not under `root`.

    `resolve()` collapses `..` and follows EVERY symlink, the final component
    included, so `uploads/../.ssh/id_rsa` and a link inside the folder pointing
    at `~/.ssh/id_rsa` both resolve outside and are refused here, before the file
    is touched — and identically whether or not it exists. A link that points
    somewhere else INSIDE the folder is fine.

    Not covered, deliberately: a HARD link inside the folder to a file outside
    it is the same inode under two names and no path check can see it, and a
    symlink swapped into the folder between this check and the read is a race
    no path check closes. Both need write access to the folder on this machine
    — the local attacker this check is not defending against. What it defends
    against is a MODEL being talked into naming the wrong path.
    """
    try:
        real = path.resolve()
    except (OSError, RuntimeError, ValueError):
        raise LocalFileError(str(path), "that path could not be resolved") from None

    if not real.is_relative_to(root):
        raise LocalFileError(
            str(path),
            f"it is outside the upload folder {str(root)!r} ({ENV_UPLOAD_ROOT}), "
            "and this server reads files only from inside that folder — ask the "
            "user to copy the file into it and give you the new path",
        )

    return real


def _kb_optional_fields(**fields: Any) -> dict[str, Any]:
    """The KB write body, with absent fields OMITTED rather than nulled.

    🔴 The distinction is the server's whole update semantics: a key that is
    ABSENT is not edited, a key present and EMPTY is an edit (`"body": ""`
    clears the body). Sending None as JSON null would therefore turn "leave the
    title alone" into "set the title to null" — a 422 at best and a silent
    clobber at worst.

    So None means "do not send" and nothing else does. `tags=[]` is sent, and
    means "clear every tag", which is why the test is `is not None` and not
    truthiness — the bug that phrasing avoids is an empty list being silently
    dropped so that clearing tags becomes impossible.
    """
    body: dict[str, Any] = {}

    for name, value in fields.items():
        if value is None:
            continue

        body[name] = list(value) if name == "tags" else value

    return body


def _kb_translations(
    locale: str | None,
    versions: Mapping[str, Mapping[str, Any] | None] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """The nested `translations` object, from the FLAT arguments above it.

    🔴 THE FLAT SIGNATURE IS FOR THE MODEL; THE NESTED BODY IS FOR THE SERVER.
    An MCP tool is called by a language model, and a nested map is a shape a
    model gets subtly wrong — a misspelt locale key, a title at the wrong depth,
    a `translations` that is a list. The server contract stays SINGLE (one
    writer, one shape, the same one the authoring editor posts) and this
    function is the one place the two meet.

    🔴 TWO FLAT FORMS REACH IT AND THEY MERGE INTO ONE OBJECT.

      `locale=` + the flat `title=`/`body=`/… — ONE language. The original form,
      byte-for-byte unchanged for every caller that already uses it.

      `versions=` — a mapping of locale to that locale's own fields. This is how
      MORE THAN ONE language reaches the server in a SINGLE request, and it is
      what the MCP tools' `en_*` / `zhcn_*` arguments are assembled into.

    They compose: a call carrying both contributes both languages to the same
    `translations` object. Naming ONE locale twice is the only refusal, and it
    is a ValueError raised before anything goes on the wire — in a dict the two
    spellings would silently overwrite each other and the caller would never
    learn which half was thrown away.

    🔴 ONE REQUEST IS NOT AN OPTIMISATION. IT IS THE ONLY WAY TO EDIT A
    PUBLISHED ARTICLE IN TWO LANGUAGES. An edit to a published article is STAGED
    as a revision, and `kb_article_revisions` is `unique(kb_article_id)`: a
    second PATCH REPLACES the staged revision rather than adding to it, so
    "call it once per locale" keeps only the language that went last. And the
    lone second version is refused anyway — a `zhcn`-only revision on an article
    with no versions would take it out of the ENGLISH help centre, which the
    missing-version guard correctly rejects, while the flag that gets past that
    guard performs exactly the removal the caller was trying to avoid. Both
    languages inside one `translations` object is the whole fix, and it is why
    `versions=` exists.

    Returns `{}` when no language was named at all, so a caller that did not ask
    for one sends no `translations` key — and ABSENCE IS NOT AN EMPTY OBJECT to
    the server: absent means "not editing versions", which is what every
    pre-feature client sends and what must keep leaving existing versions alone.
    An EMPTY `versions` mapping says the same thing and is dropped the same way.

    ⚠️ A None FIELD IS OMITTED, not nulled, exactly as `_kb_optional_fields`
    omits it — inside a version too, `null` means DELETE THIS LANGUAGE'S VERSION.
    Sending `{"zhcn": {"title": null}}` by accident, because the caller passed no
    title, would be an attempt to write a nonsense version.

    🔴 A WHOLE VERSION THAT IS None IS PASSED THROUGH AS null, AND THAT IS THE
    THIRD VALUE OF A THREE-VALUED KEY. `versions={"zhcn": None}` is the DELETE
    gesture — the same one the authoring editor posts — and it stays expressible
    here because this function is the one place the request shape is built, and
    a shape that could not SAY the third value would be a different contract
    from the server's. The /api/v1 controller REFUSES the delete with its own
    422 (removing a version hides an article from a language for every reader,
    and that stays a human act in the authoring screens), so no MCP tool offers
    it. "Cannot be said" and "is said and refused" are different things: the
    first is a client that drifted from the API, the second is a client that
    narrows it in one visible place.

    ⚠️ THE LOCALE IS NOT VALIDATED HERE. `zhcn` and `zh-cn` are both real locale
    strings in Ebteqdesk and only one of them is this column's; the server
    enumerates the supported set and DROPS anything else, and a second
    vocabulary in this client would be one more place to keep in step. A caller
    that sends the wrong one gets a response whose `translations` does not
    contain it, which is the honest signal.
    """
    block: dict[str, Any] = {}

    for name, version in (versions or {}).items():
        block[name] = (
            None
            if version is None
            else {key: value for key, value in version.items() if value is not None}
        )

    if locale is not None:
        if locale in block:
            raise ValueError(
                f"the {locale!r} language version was given twice: once as "
                f"locale={locale!r} with the flat title/body arguments, and once "
                f"as its own {locale}_* arguments. Send it one way or the other "
                f"— drop locale= and use the per-language arguments for every "
                f"language you are writing, which is also the only form that can "
                f"carry two languages in one request."
            )

        block[locale] = {
            name: value for name, value in fields.items() if value is not None
        }

    return {"translations": block} if block else {}


def _page_params(page: int | None) -> dict[str, int]:
    return {} if page is None else {"page": page}


def _list_params(
    page: int | None, per_page: int | None, scope: str | None = None
) -> dict[str, int | str]:
    """Paging (and the optional `scope`) for the ticket lists, which share one
    set of rules.

    Each is omitted entirely when None rather than sent empty: the server reads
    an absent `per_page` and an empty `?per_page=` alike (a cleared form control
    submits the latter), but sending one makes every log line and cache key
    noisier for no gain.

    Neither `per_page` nor `scope` is VALIDATED here. The `per_page` ceiling is
    20 and the server answers 21 with a 422 naming the field; `scope` accepts
    "mine" and "all" and answers anything else — including "all" from an account
    without the `ticket_all.view` ability — with a 403 that says so. Surfacing
    the server's answer is strictly better than a local rule that would have to
    be kept in step with it and would make this client disagree with curl. It
    matters more for `scope` than for `per_page`: whether "all" is permitted
    depends on the ROLE behind the token, which this client cannot know and must
    not guess.
    """
    params: dict[str, int | str] = dict(_page_params(page))

    if per_page is not None:
        params["per_page"] = per_page

    if scope is not None:
        params["scope"] = scope

    return params


def _ticket_id(value: Any) -> int:
    """A positive integer ticket id, or a ValueError naming the argument.

    Refused here rather than sent, for the same reason a multi-segment category
    slug is: it would build a URL that matches NO route. The write routes carry
    `->whereNumber('ticket')`, so `/api/v1/tickets/bp-task/close` matches
    nothing, and a routing miss renders through Laravel's exception handler,
    whose only `api/*` arm is the 401 — so the reply is an HTML 404. This client
    would then report "Ebteqdesk answered 404 ... with text/html instead of
    JSON", which is a true sentence about a proxy problem the user does not
    have, for what is really "that is not a ticket id".

    A bool is rejected explicitly: `True` is an `int` in Python and would
    silently become ticket 1, which on this API is a real ticket somebody can
    be replied to on.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"ticket_id must be a positive integer ticket id such as 42, not "
            f"{value!r}. Ticket ids come from the `id` field of `list_tickets`; "
            f"there is no lookup by subject, reference number or slug."
        )

    if value <= 0:
        raise ValueError(
            f"ticket_id must be a positive integer ticket id such as 42, not "
            f"{value!r}."
        )

    return value


def _attachment_id(value: Any) -> int:
    """A positive integer attachment id, or a ValueError naming the argument.

    Same reasoning as `_ticket_id`, and a separate function rather than a shared
    one because the useful half of these messages is the PROVENANCE sentence —
    where the id comes from — and the two come from completely different places.
    A merged helper would have to say "from somewhere" to serve both, which is
    the part a caller actually needs.

    The route carries `->whereNumber('attachment')`, so a non-numeric segment
    matches no route at all and Laravel answers with an HTML 404 that this
    client would faithfully report as a proxy problem the user does not have.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"attachment_id must be a positive integer attachment id such as 12, "
            f"not {value!r}. Attachment ids come from the `attachments[].id` field "
            f"of `get_ticket` — either on the ticket itself or on a conversation "
            f"entry; there is no lookup by filename."
        )

    if value <= 0:
        raise ValueError(
            f"attachment_id must be a positive integer attachment id such as 12, "
            f"not {value!r}."
        )

    return value


def _kb_structure_id(value: Any, *, argument: str, what: str) -> int:
    """A positive integer KB category or folder id, or a ValueError naming it.

    ⚠️ ONE PARAMETERISED HELPER, where `_ticket_id` and `_attachment_id` are
    deliberately two. Their messages were split because the useful half is the
    PROVENANCE sentence — where the id comes from — and theirs come from
    completely different places, so a merged version would have to say "from
    somewhere". Here both provenances are the SAME tool: `list_kb_tree` returns
    the category ids at the top level and the folder ids nested under
    `folders[]`. One sentence is honest for both, so one function is too.

    The routes carry NO `whereNumber()` constraint — unlike the ticket routes —
    so a non-numeric segment would actually REACH the controller and receive a
    proper JSON 404 rather than Laravel's HTML one. This is therefore not the
    "avoid an HTML error page" guard `_ticket_id` is; it is here for the
    provenance sentence and for the bool case.

    🔴 A bool is rejected explicitly. `True` is an `int` in Python and would
    silently become category 1 or folder 1 — on a live install those are real
    rows somebody's articles are filed under, and a rename of the wrong one is
    a portal URL broken by a type confusion.
    """
    provenance = (
        f" {what.capitalize()} ids come from `list_kb_tree` — categories at the "
        f"top level, folders nested under `folders[]`. There is no lookup by "
        f"name or slug."
    )

    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"{argument} must be a positive integer Knowledge Base {what} id such "
            f"as 3, not {value!r}.{provenance}"
        )

    if value <= 0:
        raise ValueError(
            f"{argument} must be a positive integer Knowledge Base {what} id such "
            f"as 3, not {value!r}.{provenance}"
        )

    return value


def _kb_order_ids(ids: Any) -> list[int]:
    """A non-empty list of positive integer ids for a reorder, or a ValueError.

    ⚠️ THIS CHECKS THE SHAPE AND NOTHING ELSE. Whether the list is EXACTLY the
    current sibling set is the server's decision and cannot be anyone else's: it
    depends on rows that may change between this call and the UPDATE, so it is
    re-asserted inside the write transaction, holding the sibling-set lock, and
    comes back as a 422 on `ids`. A client-side "is this the whole set?" would
    need the caller to have read the set first and would still be a guess by the
    time the request landed — a guardrail that reads like one and is not.

    What IS worth refusing locally is the shape, because every one of these
    mistakes produces a confusing server-side message instead of a clear one:

      - A bare `int`. `ids=7` is the shape a caller reaching for "move item 7"
        writes, and `{"ids": 7}` comes back as a generic "must be a list".
        Refusing it here is the one chance to say the rule out loud.
      - A `str`. `"7,3,9"` is iterable and would arrive as a list of characters.
      - A bool anywhere. `True` is an `int` in Python and would become id 1 — on
        a live install that is a real row, and reordering against it is a type
        confusion the server cannot detect.
      - An empty list. Every sibling set that can be reordered has at least one
        member, so `[]` can only be a caller that built the list wrong.

    The message names the whole-set rule rather than only the type, because a
    caller who got the shape wrong is very likely about to get the set wrong too.
    """
    rule = (
        " `ids` is the WHOLE ordered sibling set — every id in the list, in the "
        "order you want them, never a delta and never a partial list. A list "
        "that is not exactly the current set is a 422, not a partial reorder."
    )

    if isinstance(ids, (str, bytes)) or not isinstance(ids, Sequence):
        raise ValueError(f"ids must be a list of integer ids, not {ids!r}.{rule}")

    ordered = list(ids)

    if not ordered:
        raise ValueError(f"ids must not be empty.{rule}")

    for value in ordered:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(
                f"ids must contain only positive integer ids such as 3, not "
                f"{value!r}.{rule}"
            )

    return ordered


def _agent_id(value: Any, *, argument: str, what: str) -> int:
    """A positive integer id on the agent-provisioning surface, or a ValueError.

    One parameterised helper for three kinds of id — an agent, a role and an
    API key — for `_kb_structure_id`'s reason: their PROVENANCE sentences are
    what makes a message useful, and here all three come from tools in this same
    small family (`list_agents`, `list_roles`, `list_api_keys`). One shape of
    sentence is honest for all of them.

    🔴 A bool is rejected explicitly, and it matters more here than anywhere else
    in this module. `True` is an `int` in Python, so `user_id=True` would address
    USER 1 — on a live install very often the first administrator seeded at
    install time. Renaming, re-roling or revoking a key on THAT account by type
    confusion is a different order of mistake from reordering the wrong folder.

    The routes carry no `whereNumber()` constraint, so a non-numeric segment
    would reach the controller and receive a proper JSON 404 rather than
    Laravel's HTML one. This is therefore not an "avoid an HTML error page"
    guard; it is here for the bool case and for the provenance sentence.
    """
    provenance = {
        "agent": " Agent ids come from `list_agents` or `get_agent`. There is no lookup by name or email.",
        "role": " Role ids come from `list_roles`. There is no lookup by name.",
        "API key": " API key ids come from `list_api_keys`. There is no lookup by name.",
    }.get(what, "")

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(
            f"{argument} must be a positive integer {what} id such as 12, not "
            f"{value!r}.{provenance}"
        )

    return value


def _agent_group_ids(groups: Any) -> list[int]:
    """The group memberships to write, as a list of positive integer team ids.

    ⚠️ AN EMPTY LIST IS LEGAL AND IS AN INSTRUCTION. `groups=[]` means "this
    agent is in no groups" and CLEARS every membership on an update — unlike
    every other optional argument in this module, where the empty case is
    expressed by omitting it. That is why this helper does not refuse `[]` the
    way `_kb_order_ids` does: there, an empty sibling set cannot exist; here, an
    agent in no groups is the ordinary state of a new hire.

    A bare `int` and a `str` are refused for `_kb_order_ids`'s reasons — the
    first is the shape somebody reaching for "add to group 3" writes, and the
    second is iterable and would arrive as a list of characters. A bool anywhere
    would become team 1.
    """
    rule = (
        " `groups` is the WHOLE membership set, not a delta: the list you send "
        "replaces whatever the agent was in. `[]` removes them from every group."
    )

    if isinstance(groups, (str, bytes)) or not isinstance(groups, Sequence):
        raise ValueError(
            f"groups must be a list of integer team ids from `list_groups`, not "
            f"{groups!r}.{rule}"
        )

    ids = list(groups)

    for value in ids:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(
                f"groups must contain only positive integer team ids such as 3, "
                f"not {value!r}.{rule}"
            )

    return ids


def _api_key_scopes(scopes: Any) -> list[str]:
    """A non-empty list of scope strings for an issued key, or a ValueError.

    ⚠️ THE SHAPE ONLY. Which scopes are LEGAL is the server's decision and
    cannot be anyone else's: it depends on what the calling key currently
    resolves, what the owner's role is permitted to hold, and what that role's
    abilities back — three facts that can change between this call and the next,
    and one of which (the caller's own resolved set) this process cannot see.
    A client-side allowlist would be a guardrail that reads like one and is not,
    and it would go stale the day a scope is added.

    What is worth refusing locally is what produces a confusing server message:

      - A bare `str`. `scopes="ticket:read"` is what a caller reaching for one
        scope writes, and it is iterable, so it would arrive as a list of
        single characters and come back as fourteen separate `Rule::in`
        failures.
      - An empty list. The server refuses it too, but "choose at least one
        scope" is worth saying before a round trip.
      - A non-string member, which would be cast on the wire and reported
        against an index the caller has to map back by hand.

    🔴 IT DOES NOT STRIP `admin:read` OR `admin:write` EITHER, deliberately.
    Those two can never be issued and the server refuses them by name — dropping
    them here would turn an explicit refusal into a silently narrower key, which
    is the exact failure the server-side cap is built to avoid. A caller that
    asks for one is told, once, that it is never possible.
    """
    rule = (
        " `scopes` is a list of scope strings such as ['ticket:read', "
        "'kb:read']. Read `meta.issuableScopes` from `list_api_keys` or "
        "`get_agent` to see which are accepted for this agent right now."
    )

    if isinstance(scopes, (str, bytes)) or not isinstance(scopes, Sequence):
        raise ValueError(f"scopes must be a list of scope strings, not {scopes!r}.{rule}")

    listed = list(scopes)

    if not listed:
        raise ValueError(
            "scopes must not be empty — a key with no scopes can reach nothing "
            f"but the identity endpoint.{rule}"
        )

    for value in listed:
        if not isinstance(value, str) or value.strip() == "":
            raise ValueError(
                f"scopes must contain only non-empty scope strings, not {value!r}.{rule}"
            )

    return listed


def _kb_reference(reference: Any) -> str:
    """An article reference: a frozen slug, or the `id:<n>` form.

    Refused here rather than sent for `list_tickets_by_category`'s reason — an
    empty or multi-segment value builds a URL that matches a DIFFERENT route.
    `/api/v1/kb/articles//review` and `/api/v1/kb/articles/a/b/review` are not
    this endpoint, and the caller would get a confusing 404 about the wrong
    thing.

    ⚠️ The `id:<n>` form is NOT rewritten or validated into an integer here. It
    is the server's own identifier — `KbArticleResource::REFERENCE_PREFIX` — and
    it is handed back verbatim by the create response, so parsing it apart would
    make this client the second place that knows its format. `:` is a character
    Str::slug() can never emit, which is what makes the two forms unambiguous
    without anybody having to tell them apart.
    """
    if not isinstance(reference, str):
        raise ValueError(
            f"reference must be a Knowledge Base article reference — the frozen "
            f"slug, or the `id:<n>` form the create response returns as "
            f"`reference` — not {reference!r}."
        )

    clean = reference.strip().strip("/")

    if not clean or "/" in clean:
        raise ValueError(
            f"reference must be a single Knowledge Base article reference such as "
            f"'id:42' or 'resetting-your-password', not {reference!r}. An article "
            f"created through this API has no slug until a human first publishes "
            f"it, so `id:<n>` is the normal form."
        )

    return clean


# --------------------------------------------------------------------------- #
# Warni Activity argument checks
# --------------------------------------------------------------------------- #


def _activity_choice(value: Any, argument: str, allowed: Sequence[str]) -> str:
    """One of a fixed server vocabulary, or a ValueError listing it."""
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(
            f"{argument} must be one of {', '.join(allowed)}, not {value!r}."
        )

    return value


def _activity_plan(value: Any) -> str:
    """A non-empty plan name. Which names are LEGAL is server config
    (`activity.plans`), so only the shape is checked here."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"plan must be a plan code such as 'all' or 'free', not {value!r}."
        )

    return value.strip()


def _activity_date(value: Any, argument: str) -> str:
    """A real calendar date in the server's `Y-m-d` format.

    Checked because the server's rule is `date_format:Y-m-d` and nothing
    looser: an ISO date-time that `get_reports_summary` would accept is a 422
    here, and "2026-02-30" is a 422 too. Saying so before the round trip names
    the argument the way the caller spelled it (`date_from`, not `from`).
    """
    if not isinstance(value, str) or not _ACTIVITY_DATE.fullmatch(value):
        raise ValueError(
            f"{argument} must be a date in YYYY-MM-DD form such as '2026-09-29', "
            f"not {value!r}."
        )

    try:
        _date.fromisoformat(value)
    except ValueError:
        raise ValueError(
            f"{argument} is not a real calendar date: {value!r}."
        ) from None

    return value


def _activity_int(value: Any, argument: str, low: int, high: int | None) -> int:
    """An integer in [low, high] (no ceiling when `high` is None).

    A bool is refused explicitly, for the reason `_ticket_id` gives: `True` is
    an `int` in Python and would silently become 1.
    """
    bounds = f"{low}..{high}" if high is not None else f">= {low}"

    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{argument} must be an integer ({bounds}), not {value!r}.")

    if value < low or (high is not None and value > high):
        raise ValueError(f"{argument} must be {bounds}, not {value}.")

    return value


def _activity_score(value: Any, argument: str) -> int | float:
    """A score bound on 0..5. Scores are 1dp floats, so a float is legal."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"{argument} must be a number from 0 to {ACTIVITY_MAX_SCORE}, "
            f"not {value!r}."
        )

    if not 0 <= value <= ACTIVITY_MAX_SCORE:
        raise ValueError(
            f"{argument} must be from 0 to {ACTIVITY_MAX_SCORE} — the score is "
            f"the number of the five modules used — not {value}."
        )

    return value


def _activity_business_id(value: Any) -> str:
    """A business id the route will match, or a ValueError.

    The route constrains `{business_id}` to ``[A-Za-z0-9_.-]{1,50}``, and a
    value outside it does not reach the controller at all: it falls through to
    a routing miss, which renders as an HTML 404 and would be reported as a
    proxy problem. `.` and `..` match the pattern but are PATH SEGMENTS to
    every URL normaliser between here and Laravel — `customers/..` would
    resolve to a different route entirely — so they are refused too.
    """
    rule = (
        "business_id must be the POS business id — 1 to 50 letters, digits, "
        "'_', '.' or '-' — as returned in `business_id` by `activity_list` "
        "or `activity_list_dormant`"
    )

    if not isinstance(value, str) or not _ACTIVITY_BUSINESS_ID.fullmatch(value):
        raise ValueError(f"{rule}, not {value!r}.")

    if value in (".", ".."):
        raise ValueError(f"{rule}; {value!r} is a path segment, not an id.")

    return value
