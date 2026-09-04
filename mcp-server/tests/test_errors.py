"""Every documented failure mode, mapped to an exception whose message is a fix.

These messages are the product surface: an MCP tool failure reaches the user as
one line of text with no status code, no stack trace and no server log. So the
assertions here are about the WORDS, not only the type.
"""

from __future__ import annotations

import httpx2
import pytest

from conftest import (
    NOT_ASSIGNED_REFUSAL,
    SCOPE_REFUSAL,
    TOKEN,
    always_json,
    identity_payload,
    json_response,
    not_assigned_refusal,
    scope_refusal,
)
from ebteqdesk_mcp.errors import (
    AbilityError,
    ApiError,
    AuthenticationError,
    ConflictError,
    InvalidRequestError,
    KeyScopeError,
    MalformedResponseError,
    NotFoundError,
    PermissionError_,
    RateLimitedError,
    RoleScopeError,
    ScopeError,
    ServerError,
    TicketNotAssignedError,
    api_error_for,
    escalated_comment_error,
    diagnosed_scope_error,
    ticket_write_not_found,
    diagnosed_ability_error,
)


async def test_401_says_the_token_is_bad_and_that_a_restart_is_needed(make_client) -> None:
    client, _ = make_client(always_json(401, {"error": "Unauthenticated."}))

    async with client:
        with pytest.raises(AuthenticationError) as excinfo:
            await client.whoami()

    message = str(excinfo.value)
    assert "401" in message
    assert "revoked" in message
    assert "restart" in message.lower()
    assert excinfo.value.server_message == "Unauthenticated."
    assert TOKEN not in message


# --------------------------------------------------------------------------- #
# The 403 that names a scope, and the diagnosis behind it
# --------------------------------------------------------------------------- #
#
# A scope resolves only while BOTH the key carries it and the owner's role backs
# it, and Ebteqdesk refuses byte-identically either way — deliberately, so a
# stolen key cannot probe its owner's role. The two causes need OPPOSITE fixes,
# so the client spends one extra request on GET /api/v1/user and compares
# `apiKey.requested` (the key) with `apiKey.scopes` (the intersection).
#
# An earlier version of this package inferred "the key lacks it" from the mere
# presence of `required_scope`. That was wrong for every role-caused refusal and
# sent those users to mint a key that failed identically. These tests exist so
# that cannot come back.


async def test_a_scope_the_key_never_carried_is_a_key_problem(make_client) -> None:
    """Absent from `requested` -> the key was minted without it -> new key."""
    client, recorder = make_client(
        scope_refusal("kb:read", requested=["ticket:read"], scopes=["ticket:read"])
    )

    async with client:
        with pytest.raises(KeyScopeError) as excinfo:
            await client.search_kb_articles(query="vpn")

    error = excinfo.value
    message = str(error)

    assert error.diagnosis == "key"
    assert error.required_scope == "kb:read"
    assert "was not minted with the `kb:read` scope" in message
    assert "mint a NEW key" in message
    # It must NOT send them to an administrator — that is the other remedy.
    assert "administrator" not in message.lower()
    # The evidence is named, so the advice is checkable rather than magic.
    assert "apiKey.requested" in message
    # And the server's own sentence is still carried through.
    assert "GET /api/v1/user reports which half is missing" in message

    # The diagnosis cost exactly one extra request, to the identity endpoint.
    assert recorder.paths == ["/api/v1/kb/articles", "/api/v1/user"]


async def test_a_scope_the_role_no_longer_backs_is_a_role_problem(make_client) -> None:
    """In `requested` but not in `scopes` -> the ROLE half refused it.

    ⚠️ "-> admin" USED TO BE THE WHOLE ANSWER AND IS NOW ONE OF TWO (N18). The
    message no longer asserts that an administrator can fix it, because for some
    role/scope pairs nobody can: `escalation:write` and `escalation:reply` are
    backed by the SAME ability, so an account can hold that ability, resolve the
    first and never resolve the second. Telling its owner to go and ask for the
    ability sends them for something they already have.
    """
    client, recorder = make_client(
        scope_refusal(
            "escalation-reports:read",
            requested=["ticket:read", "escalation-reports:read"],
            scopes=["ticket:read"],
        )
    )

    async with client:
        with pytest.raises(RoleScopeError) as excinfo:
            await client.get_escalation_report()

    error = excinfo.value
    message = str(error)

    assert error.diagnosis == "role"
    assert error.required_scope == "escalation-reports:read"
    assert "does NOT resolve for this account's role" in message
    # 🔴 The regression that prompted the original rewrite: it must not tell a
    # role-blocked user to mint a key, which would fail exactly the same way.
    assert "mint a NEW key" not in message
    assert "will NOT help" in message
    assert "apiKey.scopes" in message

    # 🔴 AND IT MUST NOT ASSERT THAT A GRANT IS THE ANSWER (N18). An
    # administrator is offered as ONE possibility, next to the other, with the
    # check that tells them apart — never as the remedy.
    assert "AN ABILITY GRANT MAY NOT HELP EITHER" in message
    assert "Compare `permissions` in GET /api/v1/user" in message

    assert recorder.paths == ["/api/v1/reports/category-metrics", "/api/v1/user"]


async def test_the_two_causes_give_different_advice_for_the_same_scope(
    make_client,
) -> None:
    """Same scope, same 403 body, same status — opposite remedies. This is the
    whole reason the diagnostic exists."""
    scope = "ticket:read"

    key_client, _ = make_client(scope_refusal(scope, requested=[], scopes=[]))
    async with key_client:
        with pytest.raises(ScopeError) as key_exc:
            await key_client.list_tickets()

    role_client, _ = make_client(
        scope_refusal(scope, requested=[scope], scopes=[])
    )
    async with role_client:
        with pytest.raises(ScopeError) as role_exc:
            await role_client.list_tickets()

    assert type(key_exc.value) is not type(role_exc.value)
    assert str(key_exc.value) != str(role_exc.value)
    assert key_exc.value.diagnosis == "key"
    assert role_exc.value.diagnosis == "role"


async def test_the_diagnosis_never_reads_the_server_message(make_client) -> None:
    """The cause comes from two JSON arrays, not from prose.

    Server wording is not an interface — it has already been rewritten once. A
    client that matched on it would keep "working" while going wrong, which is
    the exact failure this replaces. So: garble the sentence completely and the
    diagnosis must be unchanged.
    """
    from ebteqdesk_mcp.client import USER_PATH

    def handler(request):
        if request.url.path == USER_PATH:
            return json_response(200, identity_payload(["kb:read"], []))
        return json_response(
            403,
            {"error": "\u00a1nonsense! \u2603 not a sentence about keys or roles",
             "required_scope": "kb:read"},
        )

    client, _ = make_client(handler)

    async with client:
        with pytest.raises(RoleScopeError) as excinfo:
            await client.get_kb_article("anything")

    assert excinfo.value.diagnosis == "role"


@pytest.mark.parametrize(
    "scope", ["ticket:read", "kb:read", "escalation-reports:read"]
)
async def test_every_scope_is_reported_by_name(make_client, scope: str) -> None:
    client, _ = make_client(scope_refusal(scope, requested=[], scopes=[]))

    async with client:
        with pytest.raises(ScopeError) as excinfo:
            await client.list_tickets()

    assert excinfo.value.required_scope == scope
    assert scope in str(excinfo.value)


# --------------------------------------------------------------------------- #
# The diagnostic must never make things worse
# --------------------------------------------------------------------------- #


async def test_a_rate_limited_diagnostic_does_not_mask_the_403(make_client) -> None:
    """The identity call is throttled like everything else. If it 429s, the
    caller must still get the 403 it actually hit — never a 429 it did not."""
    from ebteqdesk_mcp.client import USER_PATH

    def handler(request):
        if request.url.path == USER_PATH:
            return json_response(429, {"error": "Too Many Attempts."})
        return json_response(
            403,
            {"error": SCOPE_REFUSAL.format(scope="kb:read"), "required_scope": "kb:read"},
        )

    client, _ = make_client(handler)

    async with client:
        with pytest.raises(ScopeError) as excinfo:
            await client.search_kb_articles()

    error = excinfo.value
    assert type(error) is ScopeError  # undiagnosed, not narrowed
    assert error.diagnosis is None
    assert error.status_code == 403
    # The verbatim server sentence, plus how to check by hand.
    assert "GET /api/v1/user reports which half is missing" in str(error)
    assert "apiKey.requested" in str(error)


async def test_a_revoked_token_mid_diagnosis_does_not_mask_the_403(make_client) -> None:
    """401 on the identity call must not surface as an auth error for a request
    that was actually refused for scope."""
    from ebteqdesk_mcp.client import USER_PATH

    def handler(request):
        if request.url.path == USER_PATH:
            return json_response(401, {"error": "Unauthenticated."})
        return json_response(
            403,
            {"error": SCOPE_REFUSAL.format(scope="ticket:read"),
             "required_scope": "ticket:read"},
        )

    client, _ = make_client(handler)

    async with client:
        with pytest.raises(ScopeError) as excinfo:
            await client.list_tickets()

    assert excinfo.value.status_code == 403
    assert excinfo.value.diagnosis is None


async def test_an_unreachable_diagnostic_does_not_mask_the_403(make_client) -> None:
    from ebteqdesk_mcp.client import USER_PATH

    def handler(request):
        if request.url.path == USER_PATH:
            raise httpx2.ConnectError("gone")
        return json_response(
            403,
            {"error": SCOPE_REFUSAL.format(scope="ticket:read"),
             "required_scope": "ticket:read"},
        )

    client, _ = make_client(handler)

    async with client:
        with pytest.raises(ScopeError) as excinfo:
            await client.list_tickets()

    assert excinfo.value.status_code == 403
    assert excinfo.value.diagnosis is None


async def test_an_html_diagnostic_response_does_not_mask_the_403(make_client) -> None:
    from ebteqdesk_mcp.client import USER_PATH

    def handler(request):
        if request.url.path == USER_PATH:
            return httpx2.Response(200, text="<html>login</html>",
                                   headers={"Content-Type": "text/html"})
        return json_response(
            403,
            {"error": SCOPE_REFUSAL.format(scope="kb:read"), "required_scope": "kb:read"},
        )

    client, _ = make_client(handler)

    async with client:
        with pytest.raises(ScopeError) as excinfo:
            await client.search_kb_articles()

    assert excinfo.value.status_code == 403
    assert excinfo.value.diagnosis is None


async def test_a_null_api_key_block_leaves_it_undiagnosed(make_client) -> None:
    """`apiKey` is null when the request was not authenticated by a bearer
    token. Nothing to compare, so nothing is claimed."""
    client, _ = make_client(scope_refusal("kb:read", identity=identity_payload(api_key=None)))

    async with client:
        with pytest.raises(ScopeError) as excinfo:
            await client.search_kb_articles()

    assert excinfo.value.diagnosis is None


async def test_a_malformed_api_key_block_leaves_it_undiagnosed(make_client) -> None:
    """Defensive: `requested`/`scopes` that are not lists must not raise a
    TypeError out of the error path."""
    client, _ = make_client(
        scope_refusal(
            "kb:read",
            identity=identity_payload(api_key={"requested": "kb:read", "scopes": None}),
        )
    )

    async with client:
        with pytest.raises(ScopeError) as excinfo:
            await client.search_kb_articles()

    assert excinfo.value.diagnosis is None


async def test_a_scope_present_in_both_lists_stays_undiagnosed(make_client) -> None:
    """The race: the scope resolved when we asked and did not when the request
    was refused, so a role or key changed between the two calls. Asserting
    either remedy would be a guess — return the verbatim refusal instead."""
    client, _ = make_client(
        scope_refusal("kb:read", requested=["kb:read"], scopes=["kb:read"])
    )

    async with client:
        with pytest.raises(ScopeError) as excinfo:
            await client.search_kb_articles()

    error = excinfo.value
    assert type(error) is ScopeError
    assert error.diagnosis is None


async def test_the_diagnostic_does_not_recurse(make_client) -> None:
    """Every request 403s, including the identity call. The client must make
    exactly two and stop, not spiral."""
    client, recorder = make_client(
        always_json(
            403,
            {"error": SCOPE_REFUSAL.format(scope="kb:read"), "required_scope": "kb:read"},
        )
    )

    async with client:
        with pytest.raises(ScopeError):
            await client.search_kb_articles()

    assert recorder.paths == ["/api/v1/kb/articles", "/api/v1/user"]


async def test_a_successful_call_pays_nothing_for_the_diagnostic(make_client) -> None:
    """The extra request is on the error path only."""
    client, recorder = make_client(always_json(200, {"data": []}))

    async with client:
        await client.list_tickets()

    assert recorder.paths == ["/api/v1/tickets"]


async def test_a_403_naming_no_scope_is_not_diagnosed(make_client) -> None:
    """No scope named means no key/role intersection to check, so no second
    request is worth making."""
    client, recorder = make_client(
        always_json(403, {"error": "This endpoint requires the `bp_escalation.view` ability."})
    )

    async with client:
        with pytest.raises(PermissionError_) as excinfo:
            await client.get_escalation_report()

    assert excinfo.value.required_scope is None
    assert "bp_escalation.view" in str(excinfo.value)
    assert recorder.paths == ["/api/v1/reports/category-metrics"]


async def test_scope_error_and_permission_error_are_distinguishable_types(
    make_client,
) -> None:
    """Both are 403 and a caller branching on the status alone cannot act."""
    assert not issubclass(ScopeError, PermissionError_)
    assert not issubclass(PermissionError_, ScopeError)
    assert issubclass(ScopeError, ApiError) and issubclass(PermissionError_, ApiError)
    # The two diagnoses are siblings, so `except ScopeError` still catches both
    # while `except KeyScopeError` catches only the one with a key remedy.
    assert issubclass(KeyScopeError, ScopeError)
    assert issubclass(RoleScopeError, ScopeError)
    assert not issubclass(KeyScopeError, RoleScopeError)
    assert not issubclass(RoleScopeError, KeyScopeError)


# --------------------------------------------------------------------------- #
# The THIRD 403: a named ability the account's role does not hold
# --------------------------------------------------------------------------- #
#
# `api_error_for` is where the three-way fork lives, so it is tested here as the
# pure function it is. `test_write_client.py` covers the same ground through the
# transport; this covers the mapping itself, including the shapes no endpoint
# emits today and therefore no round-trip test can reach.


def test_the_403_fork_is_three_ways_on_field_presence_alone() -> None:
    """🔴 On the PRESENCE OF A FIELD, never on the words in `error`. The prose
    here is deliberately misleading in each case — a client that read it would
    pick the wrong branch every time."""
    scope = api_error_for(
        status_code=403,
        path="/api/v1/tickets",
        payload={"error": "ability ability ability", "required_scope": "ticket:write"},
    )
    ability = api_error_for(
        status_code=403,
        path="/api/v1/tickets",
        payload={"error": "scope scope scope", "required_ability": "ticket.create"},
    )
    neither = api_error_for(
        status_code=403,
        path="/api/v1/tickets",
        payload={"error": "required_scope required_ability"},
    )

    assert type(scope) is ScopeError
    assert type(ability) is AbilityError
    assert type(neither) is PermissionError_


def test_an_ability_error_is_not_a_scope_error_in_either_direction() -> None:
    """A caller doing `except ScopeError` is asking "is this fixable with a
    different key?". For an ability refusal the answer is no, so it must not be
    caught there."""
    assert not issubclass(AbilityError, ScopeError)
    assert not issubclass(ScopeError, AbilityError)
    assert not issubclass(AbilityError, PermissionError_)
    assert not issubclass(PermissionError_, AbilityError)
    assert issubclass(AbilityError, ApiError)


def test_the_ability_message_answers_all_three_questions() -> None:
    """What happened, why, and what to do — and the third answer here is a
    single thing, unlike the scope case where it is two opposites."""
    error = api_error_for(
        status_code=403,
        path="/api/v1/tickets/42/close",
        payload={
            "error": "This account is not permitted to ticket.close on this ticket.",
            "required_ability": "ticket.close",
        },
    )
    message = str(error)

    assert error.required_ability == "ticket.close"
    assert "`ticket.close`" in message
    assert "403" in message
    assert "administrator" in message
    # It must not offer the scope case's remedies, and must say so out loud
    # rather than merely omitting them.
    assert "mint a NEW key" not in message
    assert "Minting a new key will not help" in message
    assert "no matter which scopes are ticked" in message
    # And it points at the one place the caller can see what it DOES hold.
    assert "`permissions`" in message


def test_a_blank_required_ability_falls_through_to_the_unnamed_refusal() -> None:
    """An empty string is not an ability. Naming `` in a message helps nobody,
    and the `PermissionError_` text already covers "refused, nothing named"."""
    for blank in ("", None, 123, []):
        error = api_error_for(
            status_code=403,
            path="/api/v1/tickets",
            payload={"error": "nope", "required_ability": blank},
        )
        assert type(error) is PermissionError_
        assert error.required_ability is None


# --------------------------------------------------------------------------- #
# The escalated-comment explanation, as a pure function
# --------------------------------------------------------------------------- #


def _scope_error(scope: str) -> ScopeError:
    return ScopeError(
        "original message",
        status_code=403,
        path="/api/v1/tickets/42/comments",
        server_message="server sentence",
        required_scope=scope,
    )


def test_the_escalated_explanation_is_prepended_never_substituted() -> None:
    """Never mask the original. The whole prior message, including whatever
    diagnosis was reached and the server's own sentence, has to survive."""
    original = _scope_error("escalation:reply")

    wrapped = escalated_comment_error(original)

    assert "THIS TICKET IS ESCALATED" in str(wrapped)
    assert str(original) in str(wrapped)
    assert str(wrapped).endswith(str(original))


def test_the_escalated_explanation_preserves_the_class_and_the_diagnosis() -> None:
    """The escalated ticket is the CONTEXT of a refusal, not a fourth diagnosis.
    A KeyScopeError must stay one, or `except RoleScopeError` stops working and
    the key/role remedies get lost."""
    for cls, diagnosis in ((KeyScopeError, "key"), (RoleScopeError, "role")):
        original = cls(
            "original",
            status_code=403,
            path="/api/v1/tickets/42/comments",
            server_message="s",
            required_scope="escalation:reply",
        )

        wrapped = escalated_comment_error(original)

        assert type(wrapped) is cls
        assert wrapped.diagnosis == diagnosis
        assert wrapped.required_scope == "escalation:reply"
        assert wrapped.status_code == 403
        assert wrapped.path == "/api/v1/tickets/42/comments"
        assert wrapped.server_message == "s"


@pytest.mark.parametrize("scope", ["ticket:write", "kb:read", "escalation:read", None])
def test_any_other_scope_passes_through_the_explanation_untouched(scope) -> None:
    """🔴 The guard on the branch, and the reason the caller can hand every
    ScopeError through this without a guard of its own. Only `escalation:reply`
    on the comment path carries the escalation meaning; the route middleware's
    own `ticket:write` refusal knows nothing about the ticket."""
    original = _scope_error(scope) if scope else ScopeError(
        "original", status_code=403, path="/api/v1/tickets/42/comments"
    )

    assert escalated_comment_error(original) is original


def test_the_comment_narrower_ignores_the_note_scope() -> None:
    """🔴 THE TWO HALVES OF THE SPLIT MUST NOT CROSS.

    `escalation:write` is the NOTE endpoint's scope. If the comment narrower
    matched it too, a refused note would be explained with the reply endpoint's
    sentence — "the requester conversation has been handed over" — about a write
    that reaches the requester not at all.
    """
    note_refusal = ScopeError(
        "original",
        status_code=403,
        path="/api/v1/tickets/42/comments",
        required_scope="escalation:write",
    )

    assert escalated_comment_error(note_refusal) is note_refusal


def test_the_explanation_reads_no_prose_from_the_server() -> None:
    """Same rule as the diagnosis: garble the sentence completely and the
    behaviour must be identical, because the branch is a field comparison."""
    garbled = ScopeError(
        "original",
        status_code=403,
        path="/api/v1/tickets/42/comments",
        server_message="¡nonsense! ☃ nothing about escalation",
        required_scope="escalation:reply",
    )

    assert "THIS TICKET IS ESCALATED" in str(escalated_comment_error(garbled))


async def test_404_for_an_unknown_ticket_category_echoes_the_slug(make_client) -> None:
    client, _ = make_client(
        always_json(404, {"error": 'There is no ticket category with the slug "nope".'})
    )

    async with client:
        with pytest.raises(NotFoundError) as excinfo:
            await client.list_tickets_by_category("nope")

    assert "nope" in str(excinfo.value)


async def test_409_no_longer_diagnoses_itself_as_a_published_article(
    make_client,
) -> None:
    """🔴 THE 409 HAS NO PRODUCER ANY MORE. It was the refusal for a PATCH of a
    published knowledge base article; that request now stages a pending revision
    and answers 202 (see test_kb_writes.py). The class is kept because it is
    exported from the package root and because a proxy can still emit a 409 —
    but its message must NOT keep asserting the removed rule, or the next real
    409 gets a confident, wrong diagnosis.

    So this asserts an ABSENCE, which is the only way to state the requirement:
    the words that would re-introduce the false explanation.
    """
    client, _ = make_client(
        always_json(409, {"error": "Row was locked by another process."})
    )

    async with client:
        with pytest.raises(ConflictError) as excinfo:
            await client.update_kb_article("resetting-your-password", title="New")

    message = str(excinfo.value)

    assert "DRAFTS ONLY" not in message
    assert "unpublish" not in message.lower()
    # The server's own sentence is the only thing that knows what conflicted.
    assert "Row was locked by another process." in message
    # And it says outright that a published-article PATCH is not this.
    assert "answers 202" in message


async def test_404_for_a_kb_slug_does_not_echo_the_slug(make_client) -> None:
    """Byte-identical for a hidden article and one that never existed, and it
    does NOT repeat the slug — that is what stops the route being used to
    enumerate draft titles. This client must not 'improve' it by adding it back.
    """
    client, _ = make_client(
        always_json(404, {"error": "There is no published article with that slug."})
    )

    async with client:
        with pytest.raises(NotFoundError) as excinfo:
            await client.get_kb_article("secret-internal-runbook")

    message = str(excinfo.value)
    assert "secret-internal-runbook" not in message
    assert "There is no published article with that slug." in message


async def test_the_two_kb_404s_are_indistinguishable_to_a_caller(make_client) -> None:
    """A hidden article and a nonexistent one must produce identical output."""
    handler = always_json(
        404, {"error": "There is no published article with that slug."}
    )

    messages = []
    for slug in ("an-internal-draft", "never-existed-at-all"):
        client, _ = make_client(handler)
        async with client:
            with pytest.raises(NotFoundError) as excinfo:
                await client.get_kb_article(slug)
        messages.append(str(excinfo.value))

    assert messages[0] == messages[1]


async def test_422_lists_the_offending_fields(make_client) -> None:
    client, _ = make_client(
        always_json(
            422,
            {
                "error": "The request query is not valid.",
                "errors": {"per_page": ["The per page may not be greater than 100."]},
            },
        )
    )

    async with client:
        with pytest.raises(InvalidRequestError) as excinfo:
            await client.search_kb_articles(per_page=999)

    error = excinfo.value
    assert error.field_errors == {
        "per_page": ["The per page may not be greater than 100."]
    }
    assert "per_page" in str(error)
    assert "greater than 100" in str(error)


async def test_422_on_a_reversed_report_range(make_client) -> None:
    client, _ = make_client(
        always_json(
            422,
            {
                "error": "The requested date range is not valid.",
                "errors": {"to": ["The to must be a date after or equal to from."]},
            },
        )
    )

    async with client:
        with pytest.raises(InvalidRequestError) as excinfo:
            await client.get_escalation_report(date_from="2026-03-31", date_to="2026-03-01")

    assert "to" in excinfo.value.field_errors


async def test_429_reports_the_throttle_and_the_retry_after(make_client) -> None:
    client, _ = make_client(
        lambda _r: httpx2.Response(
            429, json={"error": "Too Many Attempts."}, headers={"Retry-After": "37"}
        )
    )

    async with client:
        with pytest.raises(RateLimitedError) as excinfo:
            await client.list_tickets()

    error = excinfo.value
    assert error.retry_after == "37"
    assert "37" in str(error)
    assert "60 requests per minute" in str(error)


async def test_nothing_is_retried_automatically(make_client) -> None:
    """A silent retry turns a rate limit into an unexplained slow response."""
    client, recorder = make_client(
        always_json(429, {"error": "Too Many Attempts."})
    )

    async with client:
        with pytest.raises(RateLimitedError):
            await client.list_tickets()

    assert len(recorder.requests) == 1


async def test_500_blames_the_server_not_the_request(make_client) -> None:
    client, _ = make_client(always_json(500, {"error": "Server Error"}))

    async with client:
        with pytest.raises(ServerError) as excinfo:
            await client.whoami()

    assert "fault on the Ebteqdesk side" in str(excinfo.value)


async def test_non_json_error_page_is_quoted_back(make_client) -> None:
    """An nginx 502, a captive portal or a Laravel error page. The user needs to
    see enough of it to recognise which."""
    html = "<html><head><title>502 Bad Gateway</title></head><body>...</body></html>"
    client, _ = make_client(
        lambda _r: httpx2.Response(
            502, text=html, headers={"Content-Type": "text/html"}
        )
    )

    async with client:
        with pytest.raises(MalformedResponseError) as excinfo:
            await client.whoami()

    error = excinfo.value
    message = str(error)
    assert error.status_code == 502
    assert error.content_type == "text/html"
    assert "502 Bad Gateway" in message
    assert "proxy or error page" in message


async def test_non_json_body_on_a_200_is_also_refused(make_client) -> None:
    """A 200 of HTML means something other than the API answered. Treating it as
    an empty payload would be a silent wrong answer."""
    client, _ = make_client(
        lambda _r: httpx2.Response(200, text="<html>login</html>",
                                   headers={"Content-Type": "text/html"})
    )

    async with client:
        with pytest.raises(MalformedResponseError):
            await client.whoami()


async def test_a_json_array_is_not_silently_boxed(make_client) -> None:
    """Every documented response is an object with `data`. A bare array means
    something else answered; wrapping it would hide that."""
    client, _ = make_client(always_json(200, [1, 2, 3]))

    async with client:
        with pytest.raises(MalformedResponseError):
            await client.whoami()


async def test_an_error_status_with_a_huge_html_body_is_truncated(make_client) -> None:
    """A 2 MB error page must not end up in a chat transcript."""
    client, _ = make_client(
        lambda _r: httpx2.Response(500, text="x" * 100_000,
                                   headers={"Content-Type": "text/html"})
    )

    async with client:
        with pytest.raises(MalformedResponseError) as excinfo:
            await client.whoami()

    assert len(excinfo.value.body_snippet) <= 200


async def test_an_error_with_no_recognisable_body_still_produces_a_message(
    make_client,
) -> None:
    """A JSON object with no `error` key — the shape a future endpoint might
    return. It must not crash on the missing key."""
    client, _ = make_client(always_json(418, {"unexpected": True}))

    async with client:
        with pytest.raises(ApiError) as excinfo:
            await client.whoami()

    assert excinfo.value.status_code == 418
    assert "unexpected 418" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# The FOURTH 403: the ticket is real, readable, and somebody else's (#135)
# --------------------------------------------------------------------------- #
#
# `list_escalations` is the one ticket list on this API that is not
# ownership-scoped, so it hands a caller ids from tickets assigned to other
# agents. Until #135 the write endpoints answered "there is no ticket with the
# id 4" for exactly those rows — the API contradicting a payload it had just
# served. The observed consequence was an agent that retried, or reported the
# escalation list as stale.


async def test_an_unowned_ticket_403_is_its_own_class_not_a_permission_error(
    make_client,
) -> None:
    """It carries neither `required_scope` nor `required_ability`, so without a
    branch on `reason` it would land in `PermissionError_` — whose message sends
    the user to an administrator for a grant that cannot exist."""
    client, _ = make_client(not_assigned_refusal(4))

    async with client:
        with pytest.raises(TicketNotAssignedError) as excinfo:
            await client.close_ticket(4)

    error = excinfo.value

    assert not isinstance(error, PermissionError_)
    assert not isinstance(error, ScopeError)
    assert not isinstance(error, AbilityError)
    assert error.required_scope is None
    assert error.required_ability is None
    assert error.retriable is False


async def test_the_unowned_ticket_message_forbids_the_two_wrong_reactions(
    make_client,
) -> None:
    """Retrying and re-minting. Both are what an agent does with a bare 403, and
    neither can ever work here: ownership is not a permission on this surface."""
    client, _ = make_client(not_assigned_refusal(4))

    async with client:
        with pytest.raises(TicketNotAssignedError) as excinfo:
            await client.escalate_ticket(4)

    text = str(excinfo.value)

    assert "DO NOT RETRY" in text
    assert "NOT A KEY, SCOPE OR ABILITY PROBLEM" in text
    # And the specific wrong conclusion this refusal invites: "my list is out of
    # date". It is not — the queue is shared, and that is why the id is here.
    assert "SHARED queue" in text
    assert "`assignee`" in text
    # Never the advice that fixes the other three 403s.
    assert "mint" not in text.lower()
    # The server's own sentence survives underneath, as it does for every 403.
    assert NOT_ASSIGNED_REFUSAL.format(id=4) in text


async def test_the_unowned_ticket_403_costs_no_diagnostic_request(make_client) -> None:
    """`_diagnose_scope` spends a GET /api/v1/user to tell a key problem from a
    role problem. There is no such question here, so no second request may be
    made — and the recorder is what proves it."""
    client, recorder = make_client(not_assigned_refusal(4))

    async with client:
        with pytest.raises(TicketNotAssignedError):
            await client.comment_on_ticket(4, body="x")

    assert len(recorder.requests) == 1


async def test_a_403_without_the_reason_field_is_unchanged(make_client) -> None:
    """The branch is on the VALUE of `reason`, so nothing else moves. A 403 with
    no `reason`, or with an unrecognised one, keeps the class it always had."""
    for payload in ({"error": "Refused."}, {"error": "Refused.", "reason": "something-else"}):
        client, _ = make_client(always_json(403, payload))

        async with client:
            with pytest.raises(PermissionError_):
                await client.close_ticket(4)


def test_the_reason_branch_is_tested_before_the_scope_branch() -> None:
    """A body carrying BOTH cannot happen today — they come from different
    refusal points — but the order is fixed rather than incidental. Ownership
    wins: it is the only one of the two with no remedy, so reporting the scope
    would send the caller somewhere that cannot help.
    """
    error = api_error_for(
        status_code=403,
        path="/api/v1/tickets/4/close",
        payload={
            "error": "Refused.",
            "reason": "ticket_not_assigned",
            "required_scope": "ticket:write",
        },
    )

    assert isinstance(error, TicketNotAssignedError)


def test_the_404_still_means_unknown_or_invisible() -> None:
    """The carve-out is narrow on purpose. Everything the shared queue does not
    carry — a ticket that is not escalated, one that has been solved off the
    queue, a key that cannot read the queue at all — keeps the byte-identical
    404, which is what stops the id space being enumerable."""
    error = api_error_for(
        status_code=404,
        path="/api/v1/tickets/4/close",
        payload={"error": 'There is no ticket with the id "4".'},
    )

    assert isinstance(error, NotFoundError)
    assert not isinstance(error, TicketNotAssignedError)


async def test_the_unowned_ticket_message_covers_the_unassigned_case_too(
    make_client,
) -> None:
    """The same `reason` is sent for an escalation assigned to NOBODY, so the
    client's own lead sentence must not assert that somebody has it. The server
    distinguishes the two and its sentence is carried through verbatim; this
    client says only what is true of both."""
    unassigned = always_json(
        403,
        {
            "error": "Ticket 4 is on the escalation queue but assigned to nobody "
                     "and cannot be modified by you.",
            "reason": "ticket_not_assigned",
        },
    )
    client, _ = make_client(unassigned)

    async with client:
        with pytest.raises(TicketNotAssignedError) as excinfo:
            await client.close_ticket(4)

    text = str(excinfo.value)

    assert "not assigned to this account" in text
    assert "belongs to another agent" not in text
    # The server's own wording, which IS specific, survives underneath.
    assert "assigned to nobody" in text


# --------------------------------------------------------------------------- #
# N19 / N20 — refusals the server must keep opaque, explained by the client
# --------------------------------------------------------------------------- #


def test_a_key_scope_refusal_warns_that_the_named_scope_may_be_unholdable() -> None:
    """🔴 N19 — an ANY-OF route names its FIRST alternative when none resolves.

    `POST /tickets/{id}/comments` declares `ticket:write|escalation:reply`, and
    EnsureApiScope names `ticket:write` unconditionally — never the alternative
    the caller came closest to holding, because that would vary the refusal with
    the caller's role and turn it into the role oracle the server refuses to be.

    The consequence is that the named scope can be one this account may never
    hold: a developer account is refused `ticket:write`, mints a key carrying
    `ticket:write`, and that key resolves nothing because the ROLE does not back
    it. The advice "mint a new key with the named scope" is then a loop.

    The server must not change. The client says the missing sentence instead.
    """
    error = KeyScopeError(
        "original",
        status_code=403,
        path="/api/v1/tickets/42/comments",
        required_scope="ticket:write",
    )

    narrowed = diagnosed_scope_error(
        error, requested=["escalation:reply"], scopes=["escalation:reply"]
    )

    message = str(narrowed)

    assert "CHECK YOUR ACCOUNT CAN HOLD" in message
    assert "name only the FIRST one they declare" in message
    # It must point at the payload that answers the question, not guess.
    assert "GET /api/v1/user lists what this account can actually hold" in message


def test_a_ticket_write_404_warns_it_may_be_the_callers_own_ticket() -> None:
    """🔴 N20 — the write 404 covers three cases and one of them is "yours".

    A key that does not resolve `ticket:write` reaches ESCALATED tickets only, so
    every ordinary ticket it addresses — including its own — answers with the
    same body as an id that was never issued. Told just "there is no ticket with
    the id 4", a caller concludes the ticket was deleted.

    The client can say so without weakening anything: the fact it adds is about
    the caller's own KEY, and it is appended to every such 404 identically, so it
    still cannot tell one id from another.
    """
    error = NotFoundError(
        'There is no ticket with the id "4".',
        status_code=404,
        path="/api/v1/tickets/4/comments",
    )

    narrowed = ticket_write_not_found(error, scopes=["escalation:read", "escalation:reply"])

    message = str(narrowed)

    assert "THIS MAY BE YOUR OWN TICKET" in message
    assert "do not report the ticket as deleted" in message
    # The original sentence survives underneath.
    assert 'There is no ticket with the id "4".' in message
    assert isinstance(narrowed, NotFoundError)


def test_the_own_ticket_warning_is_withheld_from_a_key_that_can_write_tickets() -> None:
    """The guard, and the reason the sentence is not simply always added.

    For a key that DOES resolve `ticket:write`, the own-ticket case cannot
    arise — its own ordinary tickets are reachable — so the warning would be
    noise pointing at a cause that is not there.
    """
    error = NotFoundError(
        'There is no ticket with the id "4".',
        status_code=404,
        path="/api/v1/tickets/4/comments",
    )

    assert ticket_write_not_found(error, scopes=["ticket:write"]) is error


def test_the_own_ticket_warning_ignores_anything_that_is_not_a_404() -> None:
    """Callers hand every error through without a guard, as with the others."""
    scope_error = ScopeError(
        "original", status_code=403, path="/api/v1/tickets/4/comments"
    )

    assert ticket_write_not_found(scope_error, scopes=[]) is scope_error


def test_the_ticket_ability_refusal_does_not_assert_a_cause_it_cannot_know() -> None:
    """🔴 N11 — the diagnosis gets WHICH HALF right and must not guess WHY.

    An Agent calling DELETE /tickets/{own, open, never-escalated}/escalate is
    refused `bp_escalation.reply` — an ability that account HOLDS. The old
    message concluded the ticket must belong to somebody else and told the
    caller to "check the ticket's assignee, or work a ticket that is yours".
    The ticket WAS theirs. The real cause is that it is not escalated: the
    per-ticket policy for the BP surface requires `isEscalated()`, so the
    de-escalate refusal this branch created is precisely the case the advice got
    wrong.

    This module's own standard — "an exception must not answer the third
    question when it does not actually know" — applies to itself. The server
    names the ability and deliberately says no more, so the client enumerates
    the candidate causes and names none of them as THE cause.
    """
    error = AbilityError(
        "original",
        status_code=403,
        path="/api/v1/tickets/7/escalate",
        required_ability="bp_escalation.reply",
    )

    narrowed = diagnosed_ability_error(
        error, permissions=["bp_escalation.view", "bp_escalation.reply", "ticket.reply"]
    )

    message = str(narrowed)

    # It still gets the half right: the account is not the problem.
    assert "HOLDS the `bp_escalation.reply` ability" in message
    assert "you already have it" in message

    # 🔴 …and it no longer asserts ownership as the cause.
    assert "is not something this client can tell you" in message
    assert "not in the STATE the action needs" in message
    assert "de-escalating one that was never escalated" in message

    # The wrong advice specifically must be gone: it must not send a caller who
    # owns the ticket off to look at the assignee as though that were the answer.
    assert "or work a ticket that is yours" not in message
