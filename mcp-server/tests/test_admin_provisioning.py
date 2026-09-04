"""Agent provisioning: `list_agents`, `get_agent`, `list_roles`, `list_groups`,
`create_agent`, `update_agent`, `list_api_keys`, `issue_api_key` and
`revoke_api_key`.

═══════════════════════════════════════════════════════════════════════════════
Why these nine tools are tested differently from the other thirty-three
═══════════════════════════════════════════════════════════════════════════════
Every other tool on this server acts on a ticket or on help content. These act
on the DESK ITSELF — they create accounts that can sign in, decide what role a
person holds, and hand out bearer credentials. So the properties worth pinning
are not "the right URL was called"; they are the four things a model can only
learn from a DESCRIPTION, each of which is silent when it breaks:

  1. A SECRET COMES BACK EXACTLY ONCE. `create_agent`'s `generatedPassword` and
     `issue_api_key`'s `plainTextToken` exist in that one response and nowhere
     else, ever. A model that treats either as re-readable has destroyed it: the
     account or the key has to be reset by a human. Nothing in the payload says
     so — a `plainTextToken` key looks exactly like any other field.

  2. `issue_api_key` CAN NEVER GRANT `admin:read` OR `admin:write`. A model that
     reads a 403 saying "you need admin access" and reaches for this tool to
     mint it will loop forever, because the answer does not change with a
     different agent, a different role or a wider key. Only the description can
     stop that, so the description is asserted.

  3. THE SERVER REFUSES RATHER THAN NARROWING. One unacceptable scope makes the
     whole call a 422 that creates nothing. A model that assumed partial success
     would report a key that does not exist.

  4. THERE IS NO DELETE-AGENT, NO PASSWORD RESET AND NO EMAIL CHANGE, and the
     absence has to be visible in the SCHEMA rather than only in prose — an
     argument a model can see is an argument it will try.

⚠️ THE DESCRIPTIONS ARE ASSERTED, NOT ONLY THE CALLS. A refactor that shortens
one of these docstrings is a behaviour change on this surface, exactly as it is
for `upload_kb_media` and the two KB deletes.
"""

from __future__ import annotations

import inspect

import httpx2
import pytest

from conftest import always_json, scope_refusal
from mcp.server.mcpserver.exceptions import ToolError

from ebteqdesk_mcp import server as srv
from ebteqdesk_mcp.client import EbteqdeskClient
from ebteqdesk_mcp.config import Config
from ebteqdesk_mcp.errors import InvalidRequestError

#: The nine tools, and a minimal valid call for each.
#:
#: 🔴 THE ABSENT ARGUMENTS ARE PART OF THE CONTRACT. `create_agent` takes
#: `email_local` and never `email`; `update_agent` takes no `password` and no
#: address of any kind; `revoke_api_key` takes two path ids and nothing else —
#: no `force`, no cascade, no dry run. All three absences are asserted against
#: the GENERATED SCHEMA further down, because the schema is what a model reads.
ADMIN_TOOLS: dict[str, dict] = {
    "list_agents": {},
    "get_agent": {"user_id": 12},
    "list_roles": {},
    "list_groups": {},
    "create_agent": {"name": "Dana Ortega", "email_local": "dana", "role_id": 3},
    "update_agent": {"user_id": 12, "name": "Dana O."},
    "list_api_keys": {"user_id": 12},
    "issue_api_key": {"user_id": 12, "name": "bot", "scopes": ["ticket:read"]},
    "revoke_api_key": {"user_id": 12, "api_key_id": 7},
}

READ_TOOLS = ["list_agents", "get_agent", "list_roles", "list_groups", "list_api_keys"]
WRITE_TOOLS = ["create_agent", "update_agent", "issue_api_key", "revoke_api_key"]


def agent_row(**overrides) -> dict:
    return {
        "id": 12,
        "uuid": "9c1f…",
        "name": "Dana Ortega",
        "email": "dana@ebteq.desk",
        "emailLocal": "dana",
        "mustChangePassword": True,
        "role": {"id": 3, "name": "Agent", "key": "agent", "isSystem": True},
        "groups": [{"id": 1, "name": "Support"}],
        "createdAt": "2026-09-01T09:00:00+00:00",
        "updatedAt": "2026-09-01T09:00:00+00:00",
    } | overrides


def key_row(**overrides) -> dict:
    return {
        "id": 7,
        "name": "iris-bot",
        "scopes": ["ticket:read"],
        "effectiveScopes": ["ticket:read"],
        "legacy": False,
        "expired": False,
        "expiresAt": None,
        "lastUsedAt": None,
        "createdAt": "2026-09-01T09:00:00+00:00",
    } | overrides


@pytest.fixture
async def tools() -> dict[str, object]:
    return {tool.name: tool for tool in await srv.mcp.list_tools()}


def described(tool) -> str:
    return " ".join((tool.description or "").split())


@pytest.fixture
def wired(monkeypatch):
    def install(handler):
        config = Config(base_url="https://ebteqdesk.test", token="6|t", timeout=5.0)
        client = EbteqdeskClient(config, transport=httpx2.MockTransport(handler))
        monkeypatch.setattr(srv, "_client", client)
        return client

    yield install

    monkeypatch.setattr(srv, "_client", None)


# --------------------------------------------------------------------------- #
# The wire
# --------------------------------------------------------------------------- #


async def test_the_reads_call_the_endpoints_they_document(make_client) -> None:
    client, recorder = make_client(always_json(200, {"data": []}))

    await client.list_agents()
    assert recorder.last.url.path == "/api/v1/admin/agents"
    assert recorder.last.url.params == httpx2.QueryParams()

    await client.list_agents(search="kaur", role_id=3)
    assert recorder.last.url.params["search"] == "kaur"
    assert recorder.last.url.params["role_id"] == "3"

    await client.get_agent(12)
    assert recorder.last.url.path == "/api/v1/admin/agents/12"

    await client.list_roles()
    assert recorder.last.url.path == "/api/v1/admin/roles"

    await client.list_groups()
    assert recorder.last.url.path == "/api/v1/admin/groups"

    await client.list_api_keys(12)
    assert recorder.last.url.path == "/api/v1/admin/agents/12/keys"

    await client.aclose()


async def test_create_agent_sends_only_the_fields_it_was_given(make_client) -> None:
    """🔴 `email` IS NEVER SENT AND `password` IS OMITTED, NOT NULLED.

    Omitting `password` is what asks the server to generate one, so a client
    that sent `"password": null` would be relying on the server reading null the
    same way — a second copy of a default, in the place defaults drift.
    """
    import json as jsonlib

    client, recorder = make_client(always_json(201, {"data": agent_row()}))

    await client.create_agent(name="Dana Ortega", email_local="dana", role_id=3)

    body = jsonlib.loads(recorder.last.content)

    assert recorder.last.method == "POST"
    assert recorder.last.url.path == "/api/v1/admin/agents"
    assert body == {"name": "Dana Ortega", "email_local": "dana", "role_id": 3}
    assert "email" not in body
    assert "password" not in body
    assert "groups" not in body

    # …and an EMPTY groups list IS sent, because `[]` is a real instruction.
    await client.create_agent(
        name="Sam", email_local="sam", role_id=3, groups=[], password="s3cret-enough"
    )
    body = jsonlib.loads(recorder.last.content)
    assert body["groups"] == []
    assert body["password"] == "s3cret-enough"

    await client.aclose()


async def test_update_agent_omits_what_it_was_not_given(make_client) -> None:
    import json as jsonlib

    client, recorder = make_client(always_json(200, {"data": agent_row()}))

    await client.update_agent(12, name="Dana O.")

    assert recorder.last.method == "PATCH"
    assert recorder.last.url.path == "/api/v1/admin/agents/12"
    assert jsonlib.loads(recorder.last.content) == {"name": "Dana O."}

    # `groups=[]` is an edit that clears memberships; None would not be sent.
    await client.update_agent(12, groups=[])
    assert jsonlib.loads(recorder.last.content) == {"groups": []}

    await client.aclose()


async def test_issue_and_revoke_hit_the_nested_key_routes(make_client) -> None:
    import json as jsonlib

    client, recorder = make_client(
        always_json(201, {"data": key_row(), "plainTextToken": "12|abc"})
    )

    await client.issue_api_key(
        12, name="iris-bot", scopes=["ticket:read", "kb:read"], expires_in_days=30
    )

    assert recorder.last.method == "POST"
    assert recorder.last.url.path == "/api/v1/admin/agents/12/keys"
    assert jsonlib.loads(recorder.last.content) == {
        "name": "iris-bot",
        "scopes": ["ticket:read", "kb:read"],
        "expires_in_days": 30,
    }

    # Omitted expiry means "never" — the server's branch, not a null this
    # client would then have to interpret.
    await client.issue_api_key(12, name="forever", scopes=["ticket:read"])
    assert "expires_in_days" not in jsonlib.loads(recorder.last.content)

    await client.revoke_api_key(12, 7)
    assert recorder.last.method == "DELETE"
    assert recorder.last.url.path == "/api/v1/admin/agents/12/keys/7"
    assert not recorder.last.content

    await client.aclose()


async def test_the_payloads_are_returned_unchanged(make_client) -> None:
    """Nothing is renamed on the way out — the payload keys are the contract.

    `generatedPassword` and `plainTextToken` in particular: a client that
    "tidied" either into some friendlier name would be a second, undocumented
    contract for the one string that cannot be fetched again.
    """
    client, _ = make_client(
        always_json(201, {"data": agent_row(), "generatedPassword": "Aa1Bb2Cc3Dd4Ee5F"})
    )

    created = await client.create_agent(name="Dana", email_local="dana", role_id=3)

    assert created["generatedPassword"] == "Aa1Bb2Cc3Dd4Ee5F"
    assert created["data"]["emailLocal"] == "dana"

    await client.aclose()

    client, _ = make_client(
        always_json(201, {"data": key_row(), "plainTextToken": "12|abcdef"})
    )

    issued = await client.issue_api_key(12, name="bot", scopes=["ticket:read"])

    assert issued["plainTextToken"] == "12|abcdef"
    assert issued["data"]["effectiveScopes"] == ["ticket:read"]

    await client.aclose()


# --------------------------------------------------------------------------- #
# Local argument guards
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", [True, False, 0, -1, "12", 1.0, None])
async def test_a_bad_user_id_is_refused_before_anything_is_sent(
    make_client, bad
) -> None:
    """🔴 `True` IS AN `int` IN PYTHON, AND HERE IT WOULD ADDRESS USER 1 — very
    often the administrator seeded at install time. Revoking that account's key
    or moving it off its role by type confusion is a different order of mistake
    from reordering the wrong folder, which is why the bool case is refused by
    name rather than left to the server.
    """
    client, recorder = make_client(always_json(200, {"data": []}))

    with pytest.raises(ValueError) as excinfo:
        await client.get_agent(bad)

    assert "user_id" in str(excinfo.value)
    assert "`list_agents`" in str(excinfo.value)
    assert recorder.requests == [], "nothing may be sent for a refused id"

    await client.aclose()


async def test_scopes_must_be_a_list_and_not_a_bare_string(make_client) -> None:
    """A bare `str` is iterable and would arrive as fourteen single characters,
    coming back as fourteen separate validation failures against indexes the
    caller has to map back by hand."""
    client, recorder = make_client(always_json(201, {"data": key_row()}))

    with pytest.raises(ValueError) as excinfo:
        await client.issue_api_key(12, name="bot", scopes="ticket:read")

    assert "list of scope strings" in str(excinfo.value)
    assert "meta.issuableScopes" in str(excinfo.value)

    with pytest.raises(ValueError):
        await client.issue_api_key(12, name="bot", scopes=[])

    assert recorder.requests == []

    await client.aclose()


async def test_the_client_never_strips_an_admin_scope_locally(make_client) -> None:
    """🔴 A LOCAL STRIP WOULD TURN AN EXPLICIT REFUSAL INTO A SILENT NARROWING.

    `admin:write` can never be issued, and it is tempting to drop it here and
    save a round trip. That would hand the caller a key that is quietly not what
    they asked for — the exact failure the SERVER refuses to commit. The request
    goes out whole and the server names the refusal.
    """
    import json as jsonlib

    client, recorder = make_client(
        always_json(
            422,
            {
                "error": "This key cannot be issued with the scopes requested.",
                "errors": {"scopes.1": ["admin:write can never be issued…"]},
                "refusals": {"admin:write": ["never_issuable"]},
            },
        )
    )

    with pytest.raises(InvalidRequestError):
        await client.issue_api_key(
            12, name="successor", scopes=["ticket:read", "admin:write"]
        )

    assert jsonlib.loads(recorder.last.content)["scopes"] == [
        "ticket:read",
        "admin:write",
    ]

    await client.aclose()


async def test_groups_must_be_a_list_of_positive_ints(make_client) -> None:
    client, recorder = make_client(always_json(201, {"data": agent_row()}))

    for bad in ([True], [0], ["1"], 3, "1,2"):
        with pytest.raises(ValueError) as excinfo:
            await client.create_agent(
                name="X", email_local="x", role_id=3, groups=bad
            )

        assert "groups" in str(excinfo.value)

    assert recorder.requests == []

    await client.aclose()


# --------------------------------------------------------------------------- #
# Registration and schemas
# --------------------------------------------------------------------------- #


async def test_all_nine_tools_are_registered(tools) -> None:
    for name in ADMIN_TOOLS:
        assert name in tools, f"{name} is not registered"


@pytest.mark.parametrize("name", sorted(ADMIN_TOOLS))
async def test_every_provisioning_tool_is_documented_at_length(tools, name) -> None:
    """These carry rules nothing else can carry — see this file's docstring.
    A one-line description here is a defect, not a style preference."""
    assert len(described(tools[name])) > 400, f"{name} is under-documented"


async def test_no_tool_offers_an_email_or_a_password_argument(tools) -> None:
    """🔴 THE ABSENCE HAS TO BE IN THE SCHEMA, NOT ONLY IN THE PROSE.

    An argument a model can SEE is an argument it will try. `create_agent` takes
    `email_local` — half an address — and there is no `email` anywhere, which is
    what makes the domain unforgeable from a client. `update_agent` takes
    neither, and no password: a password reset ends live sessions and reveals
    its value once on a screen, so it has no unattended shape and stays in the
    web UI.
    """

    def props(name: str) -> dict:
        return tools[name].input_schema.get("properties", {})

    assert "email" not in props("create_agent")
    assert "email_local" in props("create_agent")

    for absent in ("email", "email_local", "password"):
        assert absent not in props("update_agent"), (
            f"`update_agent` must not offer `{absent}` — the server refuses it, "
            "and an argument that reads like a working control and does nothing "
            "is worse than its absence."
        )


async def test_revoke_takes_two_ids_and_nothing_else(tools) -> None:
    """No force, no cascade, no dry run. A revoke has nothing to move and
    nothing to re-scope, and an extra flag would imply a reversible mode that
    does not exist."""
    assert sorted(tools["revoke_api_key"].input_schema.get("properties", {})) == [
        "api_key_id",
        "user_id",
    ]


async def test_there_is_no_delete_or_reset_tool(tools) -> None:
    """🔴 THE THREE THAT MUST NEVER APPEAR.

    Deleting an agent reassigns or clears tickets, comments, notes and
    performance rows across the whole desk behind a force flag and three
    separate refusals; a password reset ends live sessions and reveals its value
    once; an email change moves a sign-in identity. All three are browser-only
    by decision, and a tool by any of these names arriving is the failure this
    test exists to catch.
    """
    registered = set(tools)

    for forbidden in (
        "delete_agent",
        "remove_agent",
        "reset_agent_password",
        "set_agent_password",
        "change_agent_email",
        "update_agent_email",
    ):
        assert forbidden not in registered


# --------------------------------------------------------------------------- #
# The descriptions — where the rules actually live
# --------------------------------------------------------------------------- #


async def test_create_agent_says_the_password_is_shown_once(tools) -> None:
    text = described(tools["create_agent"])

    assert "ONLY ONCE" in text.upper() or "EXACTLY ONCE" in text.upper()
    assert "generatedPassword" in text
    assert "never be read again" in text.lower()
    assert "no delete-agent tool" in text.lower()

    # It must also point at the role as the permissions decision, or a model
    # will pick one by name and hand somebody the wrong access.
    assert "`list_roles`" in text


async def test_issue_api_key_says_the_token_is_shown_once(tools) -> None:
    text = described(tools["issue_api_key"])

    assert "plainTextToken" in text
    assert "ONLY MOMENT THAT STRING EXISTS" in text.upper()
    assert "never be read again" in text.lower()


async def test_issue_api_key_says_admin_scopes_are_never_grantable(tools) -> None:
    """🔴 THE RULE A MODEL WILL OTHERWISE LOOP ON.

    A refusal naming `admin:write` reads like a scope problem, and the obvious
    next move — mint a key that carries it — produces a key that will fail
    identically, forever. The description has to say the answer does not change
    with a different agent, role or key.
    """
    text = described(tools["issue_api_key"])

    assert "CAN NEVER BE ISSUED" in text.upper()
    assert "`admin:read`" in text and "`admin:write`" in text
    assert "Settings > API keys" in text
    assert "DO NOT retry" in text


async def test_issue_api_key_says_it_refuses_rather_than_narrowing(tools) -> None:
    text = described(tools["issue_api_key"])

    assert "creates nothing" in text
    assert "meta.issuableScopes" in text

    # The four machine-readable reason codes, so a model can act on which term
    # refused rather than parsing the sentence.
    for code in (
        "caller_key",
        "owner_role_policy",
        "owner_role_ability",
        "never_issuable",
    ):
        assert code in text


async def test_revoke_api_key_says_it_is_immediate_and_final(tools) -> None:
    text = described(tools["revoke_api_key"])

    assert "NO UNDO" in text.upper()
    assert "next request" in text.lower()
    assert "`list_api_keys`" in text
    assert "lastUsedAt" in text


async def test_update_agent_warns_that_a_role_change_narrows_live_keys(tools) -> None:
    """The silent one. Moving somebody to a narrower role takes effect on every
    key they already hold, with no error at mint time and no notification to
    whatever integration was using it."""
    text = described(tools["update_agent"])

    assert "narrows every API key" in text
    assert "last administrator" in text.lower()
    assert "Ebteqdesk web UI" in text


async def test_list_api_keys_distinguishes_carried_from_effective(tools) -> None:
    """A key whose `effectiveScopes` is `[]` authenticates and can do nothing.
    Reporting `scopes` there describes a key that does not exist."""
    text = described(tools["list_api_keys"])

    assert "`scopes`" in text and "`effectiveScopes`" in text
    assert "report `effectiveScopes`" in text


async def test_the_reads_state_the_administrator_only_gate(tools) -> None:
    """All five reads need `admin:read`, which resolves only for a role holding
    `admin.access`. A model told only "you need a scope" will suggest minting a
    key, which cannot help an agent-role account."""
    for name in READ_TOOLS:
        text = described(tools[name])
        assert "admin:read" in text, name

    assert "ADMINISTRATOR ONLY" in described(tools["list_agents"]).upper()


async def test_create_and_update_say_an_admin_role_cannot_be_assigned(tools) -> None:
    """🔴 THE RULE THAT CLOSES THE PRIVILEGE-ESCALATION CHAIN, and the one a
    model will otherwise work around.

    Asked to "make somebody an admin", a model that only knows the call failed
    will try another role, then another. The description has to say that the
    answer does not change and where the operation actually lives — the same
    shape as `issue_api_key`'s never-issuable rule, and for the same reason.
    """
    for name in ("create_agent", "update_agent"):
        text = described(tools[name])

        assert "GRANTS ADMIN ACCESS" in text.upper(), name
        assert "assignable" in text, name
        assert "Settings > Agents" in text, name

    # Create says WHY, because that is where the chain starts: the password it
    # returns is what buys the browser session.
    create = described(tools["create_agent"])
    assert "mint its own provisioning key" in create
    assert "own successor" in create

    # Update says the direction, because demote-then-promote is the obvious
    # second attempt and it is refused.
    update = described(tools["update_agent"])
    assert "DEMOTE" in update
    assert "never promote one back" in update


async def test_list_roles_advertises_which_roles_are_assignable(tools) -> None:
    text = described(tools["list_roles"])

    assert "assignable" in text
    assert "422" in text


async def test_list_groups_says_groups_grant_nothing(tools) -> None:
    text = described(tools["list_groups"])

    assert "GROUPS GRANT NOTHING" in text.upper()
    assert "`update_agent`" in text


# --------------------------------------------------------------------------- #
# Failures through the MCP layer
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", sorted(ADMIN_TOOLS))
async def test_no_provisioning_tool_is_reachable_without_its_scope(
    wired, tools, name
) -> None:
    scope = "admin:read" if name in READ_TOOLS else "admin:write"

    wired(scope_refusal(scope, requested=["ticket:read"], scopes=["ticket:read"]))

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool(name, ADMIN_TOOLS[name])

    assert scope in str(excinfo.value)


async def test_a_refusal_reaches_the_model_as_readable_text(wired) -> None:
    """A 422 from the scope cap has to arrive as prose a model can act on, not
    as a Python repr — including which term refused, which is the difference
    between "mint yourself a wider key" and "this is never possible"."""
    wired(
        always_json(
            422,
            {
                "error": (
                    "This key cannot be issued with the scopes requested, and "
                    "nothing was created."
                ),
                "errors": {
                    "scopes.0": [
                        "Your own API key does not currently resolve kb:write."
                    ]
                },
                "refusals": {"kb:write": ["caller_key"]},
            },
        )
    )

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool(
            "issue_api_key",
            {"user_id": 12, "name": "bot", "scopes": ["kb:write"]},
        )

    message = str(excinfo.value)

    assert "nothing was created" in message
    assert "kb:write" in message
    assert "Traceback" not in message


async def test_a_bad_id_is_reported_as_prose_not_a_python_type(wired) -> None:
    wired(always_json(200, {"data": []}))

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool("get_agent", {"user_id": 0})

    assert "positive integer" in str(excinfo.value)
    assert "`list_agents`" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# The instructions block
# --------------------------------------------------------------------------- #


async def test_the_instructions_carry_the_provisioning_rules() -> None:
    """`instructions` is read once, before any tool is chosen. The two rules a
    model most needs BEFORE it picks a tool belong there: that a secret is shown
    once, and that admin scopes are never issuable."""
    instructions = " ".join((srv.mcp.instructions or "").split())

    assert "NINE TOOLS DO NOT TOUCH TICKETS OR ARTICLES AT ALL" in instructions
    assert "`admin:read`" in instructions and "`admin:write`" in instructions
    assert "RETURN A SECRET EXACTLY ONCE" in instructions
    assert "CAN NEVER GRANT `admin:read` OR `admin:write`" in instructions
    assert "delete-agent tool" in instructions.lower()
    assert "twenty-one tools that read and twenty-one that WRITE" in instructions

    # 🔴 The two rules added after QA drove the escalation chain end to end.
    # Both belong in `instructions` rather than only in a tool description,
    # because a model decides WHICH tool to reach for before it reads one.
    assert "NO TOOL HERE CAN CREATE OR PROMOTE AN ADMINISTRATOR" in instructions
    assert "assignable: false" in instructions
    assert "LEGACY WILDCARD KEY IS REFUSED ALL NINE" in instructions


async def test_the_source_states_why_the_admin_subtraction_is_unconditional() -> None:
    """The reasoning lives in the module, not only in a commit message: a
    conditional version of the rule ("unless the caller holds it") is the
    obvious-looking edit, and it re-opens the chain-of-successors hole."""
    source = inspect.getsource(srv)

    assert "WHO MAY ACT" in source
    assert "cannot mint a successor" in source
