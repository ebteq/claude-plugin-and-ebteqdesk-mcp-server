"""Tests for ticket creation parity and contact/options discovery tools (SCRUM-22)."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import httpx2
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from ebteqdesk_mcp import server as srv
from ebteqdesk_mcp.errors import ScopeError

from conftest import always_json, json_response, scope_refusal, ticket_payload


# --------------------------------------------------------------------------- #
# Contact search tests
# --------------------------------------------------------------------------- #


async def test_search_contacts_client_sends_query_and_limit(make_client) -> None:
    client, recorder = make_client(
        always_json(
            200,
            {
                "data": [
                    {"id": 10, "name": "Jane Doe", "email": "jane@example.com", "notes": "VIP client"},
                    {"id": 11, "name": "John Doe", "email": "john@example.com", "notes": None},
                ]
            },
        )
    )

    result = await client.search_contacts("Doe", limit=5)

    assert len(result["data"]) == 2
    assert recorder.last.method == "GET"
    assert recorder.last.url.path == "/api/v1/contacts"
    assert recorder.last.url.query.decode() == "search=Doe&limit=5"


async def test_search_contacts_mcp_tool_envelopes_untrusted_fields(wired) -> None:
    wired(
        always_json(
            200,
            {
                "data": [
                    {"id": 10, "name": "Jane Doe", "email": "jane@example.com", "notes": "VIP client"},
                ]
            },
        )
    )

    result = await srv.mcp.call_tool("search_contacts", {"search": "Jane", "limit": 8})

    assert not result.is_error
    data = result.structured_content["data"]
    assert len(data) == 1
    assert data[0]["id"] == 10
    assert data[0]["email"] == "jane@example.com"
    # Name and notes are enveloped
    assert data[0]["name"] == {"untrusted_content": "Jane Doe"}
    assert data[0]["notes"] == {"untrusted_content": "VIP client"}
    # Top-level notice present
    assert "untrusted_content_notice" in result.structured_content


async def test_search_contacts_refuses_without_ticket_read_scope(wired) -> None:
    wired(scope_refusal("ticket:read", requested=["kb:read"], scopes=["kb:read"]))

    with pytest.raises(ToolError) as excinfo:
        await srv.mcp.call_tool("search_contacts", {"search": "Ada"})

    assert "ticket:read" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Ticket options tests
# --------------------------------------------------------------------------- #


async def test_get_ticket_options_client_requests_endpoint(make_client) -> None:
    client, recorder = make_client(
        always_json(
            200,
            {
                "data": {
                    "categories": [{"id": 1, "name": "Bug", "slug": "bug"}],
                    "priorities": [{"id": 1, "name": "Low"}, {"id": 2, "name": "Normal"}],
                    "statuses": [{"id": 1, "name": "New"}, {"id": 2, "name": "Open"}],
                    "groups": [{"id": 3, "name": "Support Tier 2"}],
                }
            },
        )
    )

    result = await client.get_ticket_options()

    assert recorder.last.method == "GET"
    assert recorder.last.url.path == "/api/v1/ticket-options"
    assert "categories" in result["data"]
    assert "groups" in result["data"]


async def test_get_ticket_options_mcp_tool_returns_data(wired) -> None:
    wired(
        always_json(
            200,
            {
                "data": {
                    "categories": [{"id": 1, "name": "Bug", "slug": "bug"}],
                    "groups": [{"id": 3, "name": "Support Tier 2"}],
                }
            },
        )
    )

    result = await srv.mcp.call_tool("get_ticket_options", {})

    assert not result.is_error
    assert result.structured_content["data"]["groups"][0]["name"] == "Support Tier 2"


# --------------------------------------------------------------------------- #
# Ticket creation parity tests
# --------------------------------------------------------------------------- #


async def test_create_ticket_with_contact_id_sends_explicit_null_agent_id(make_client) -> None:
    client, recorder = make_client(always_json(201, ticket_payload(id=101)))

    result = await client.create_ticket(
        subject="Issue with terminal",
        description="Frozen on boot",
        contact_id=42,
    )

    assert result["data"]["id"] == 101
    assert recorder.last.method == "POST"
    assert recorder.last.url.path == "/api/v1/tickets"
    body = json.loads(recorder.last.content)
    assert body["subject"] == "Issue with terminal"
    assert body["description"] == "Frozen on boot"
    assert body["contact_id"] == 42
    assert body["agent_id"] is None
    assert "requester" not in body


async def test_create_ticket_with_group_id_sends_explicit_null_agent_id(make_client) -> None:
    client, recorder = make_client(always_json(201, ticket_payload(id=102)))

    await client.create_ticket(
        subject="Billing question",
        description="Invoice mismatch",
        contact_id=12,
        group_id=4,
    )

    body = json.loads(recorder.last.content)
    assert body["group_id"] == 4
    assert body["contact_id"] == 12
    assert body["agent_id"] is None


async def test_create_ticket_with_group_and_explicit_agent_id(make_client) -> None:
    client, recorder = make_client(always_json(201, ticket_payload(id=103)))

    await client.create_ticket(
        subject="Specific handover",
        description="Assigned to Agent 7",
        contact_id=12,
        group_id=4,
        agent_id=7,
    )

    body = json.loads(recorder.last.content)
    assert body["group_id"] == 4
    assert body["agent_id"] == 7


async def test_create_ticket_with_source_external_ref_and_resolution_note(make_client) -> None:
    client, recorder = make_client(always_json(201, ticket_payload(id=104)))

    await client.create_ticket(
        subject="WhatsApp lead inquiry",
        description="Inquired via WhatsApp business",
        contact_id=99,
        source="whatsapp",
        external_ref={"channel": "whatsapp", "wa_message_id": "WAMID-1234"},
        resolution_note="Handled immediately on WhatsApp",
    )

    body = json.loads(recorder.last.content)
    assert body["source"] == "whatsapp"
    assert body["external_ref"] == {"channel": "whatsapp", "wa_message_id": "WAMID-1234"}
    assert body["resolution_note"] == "Handled immediately on WhatsApp"


async def test_create_ticket_auto_generates_uuid_idempotency_key(make_client) -> None:
    client, recorder = make_client(always_json(201, ticket_payload(id=105)))

    await client.create_ticket(
        subject="Printer broken",
        description="Paper jam",
        requester={"email": "ada@example.com"},
    )

    header = recorder.last.headers.get("Idempotency-Key")
    assert header is not None
    # Verify it is a valid UUIDv4 string
    parsed = uuid.UUID(header)
    assert parsed.version == 4


async def test_create_ticket_reuses_provided_idempotency_key(make_client) -> None:
    client, recorder = make_client(always_json(201, ticket_payload(id=106)))
    custom_key = "custom-idempotency-key-12345"

    await client.create_ticket(
        subject="Printer broken again",
        description="Paper jam",
        requester={"id": 12},
        idempotency_key=custom_key,
    )

    assert recorder.last.headers.get("Idempotency-Key") == custom_key


async def test_create_ticket_mcp_tool_supports_parity_parameters(wired) -> None:
    wired(always_json(201, ticket_payload(id=107, subject="Terminal down")))

    result = await srv.mcp.call_tool(
        "create_ticket",
        {
            "subject": "Terminal down",
            "description": "Black screen",
            "contact_id": 55,
            "group_id": 2,
            "source": "whatsapp",
            "resolution_note": "Rebooted successfully",
        },
    )

    assert not result.is_error
    data = result.structured_content["data"]
    assert data["id"] == 107
    # Wrapped subject
    assert data["subject"] == {"untrusted_content": "Terminal down"}
