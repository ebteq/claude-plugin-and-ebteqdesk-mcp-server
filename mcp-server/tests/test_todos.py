"""Tests for To-Do Kanban polling and manipulation tools (SCRUM-22)."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx2
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from ebteqdesk_mcp import server as srv
from ebteqdesk_mcp.client import (
    TODO_PRIORITIES,
    TODO_READ_SCOPE,
    TODO_STATUSES,
    TODO_WRITE_SCOPE,
    EbteqdeskClient,
)
from ebteqdesk_mcp.config import Config
from ebteqdesk_mcp.errors import (
    ConfigurationError,
    InvalidRequestError,
    LocalFileError,
    ScopeError,
    diagnosed_scope_error,
)

from conftest import BASE_URL, TOKEN, always_json, json_response, scope_refusal


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + bytes(range(64))


def todo_payload(todo_id: int = 1, **overrides: Any) -> dict[str, Any]:
    payload = {
        "id": todo_id,
        "title": "Follow up on salon subscription inquiry",
        "description": "Customer asked about Salon V3 plan renewal pricing.",
        "status": "todo",
        "priority": "normal",
        "labels": ["whatsapp", "renewal"],
        "whatsapp_context": {
            "phone": "+60123456789",
            "sender_name": "Jane Doe",
            "message_id": "wa_msg_001",
        },
        "comments": [
            {
                "id": 10,
                "body": "Drafted preliminary reply with pricing chart.",
                "author": "Agent Smith",
                "source": "grok",
                "created_at": "2026-11-13T10:00:00Z",
            }
        ],
        "attachments": [
            {
                "id": 20,
                "name": "pricing_slip.png",
                "mime_type": "image/png",
                "size": 1024,
            }
        ],
        "created_at": "2026-11-13T09:00:00Z",
        "updated_at": "2026-11-13T10:00:00Z",
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# Scope diagnosis tests
# --------------------------------------------------------------------------- #


def test_todo_scope_diagnosis_names_backing_abilities() -> None:
    base_read = ScopeError(
        "refused", status_code=403, path="/api/v1/todos", required_scope=TODO_READ_SCOPE
    )
    err_read = diagnosed_scope_error(
        base_read, requested=[TODO_READ_SCOPE], scopes=[]
    )
    assert isinstance(err_read, ScopeError)
    assert "todo:read" in str(err_read)
    assert "todo.view" in str(err_read)

    base_write = ScopeError(
        "refused", status_code=403, path="/api/v1/todos", required_scope=TODO_WRITE_SCOPE
    )
    err_write = diagnosed_scope_error(
        base_write, requested=[TODO_WRITE_SCOPE], scopes=[]
    )
    assert isinstance(err_write, ScopeError)
    assert "todo:write" in str(err_write)
    assert "todo.manage" in str(err_write)


# --------------------------------------------------------------------------- #
# Client method tests
# --------------------------------------------------------------------------- #


async def test_list_todos_client_sends_query_parameters(make_client) -> None:
    client, recorder = make_client(
        always_json(200, {"data": [todo_payload(1), todo_payload(2)]})
    )

    result = await client.list_todos(
        status="in_progress", tag="whatsapp", search="renewal", limit=25
    )

    assert len(result["data"]) == 2
    assert recorder.last.method == "GET"
    assert recorder.last.url.path == "/api/v1/todos"
    query = recorder.last.url.query.decode()
    assert "status=in_progress" in query
    assert "tag=whatsapp" in query
    assert "search=renewal" in query
    assert "limit=25" in query


async def test_get_todo_client_validates_id(make_client) -> None:
    client, _ = make_client(always_json(200, {"data": todo_payload(5)}))

    with pytest.raises(ValueError):
        await client.get_todo(0)

    with pytest.raises(ValueError):
        await client.get_todo(-1)

    result = await client.get_todo(5)
    assert result["data"]["id"] == 5


async def test_create_todo_client_validates_priority_and_posts_body(make_client) -> None:
    client, recorder = make_client(always_json(201, {"data": todo_payload(9)}))

    result = await client.create_todo(
        title="Payment issue",
        description="Bank transfer receipt attached",
        labels=["billing", "urgent"],
        whatsapp_context={"phone": "+60123456789"},
        priority="high",
    )

    assert result["data"]["id"] == 9
    assert recorder.last.method == "POST"
    assert recorder.last.url.path == "/api/v1/todos"
    body = json.loads(recorder.last.content)
    assert body["title"] == "Payment issue"
    assert body["description"] == "Bank transfer receipt attached"
    assert body["labels"] == ["billing", "urgent"]
    assert body["whatsapp_context"] == {"phone": "+60123456789"}
    assert body["priority"] == "high"


async def test_update_todo_status_validates_status_and_patches_card(make_client) -> None:
    client, recorder = make_client(
        always_json(200, {"data": todo_payload(3, status="ready_to_reply")})
    )

    result = await client.update_todo_status(3, "ready_to_reply")

    assert result["data"]["status"] == "ready_to_reply"
    assert recorder.last.method == "PATCH"
    assert recorder.last.url.path == "/api/v1/todos/3"
    assert json.loads(recorder.last.content) == {"status": "ready_to_reply"}


async def test_comment_on_todo_posts_comment(make_client) -> None:
    client, recorder = make_client(
        always_json(
            201,
            {
                "data": {
                    "id": 15,
                    "body": "Replied via template",
                    "author": "Agent Grok",
                    "source": "grok",
                }
            },
        )
    )

    result = await client.comment_on_todo(3, body="Replied via template", source="grok")

    assert result["data"]["id"] == 15
    assert recorder.last.method == "POST"
    assert recorder.last.url.path == "/api/v1/todos/3/comments"
    assert json.loads(recorder.last.content) == {
        "body": "Replied via template",
        "source": "grok",
    }


async def test_attach_screenshot_to_todo_enforces_upload_root(tmp_path) -> None:
    inside_file = tmp_path / "inside.png"
    inside_file.write_bytes(PNG_BYTES)

    outside_file = tmp_path.parent / "outside.png"
    outside_file.write_bytes(PNG_BYTES)

    # 1. Unset upload root raises ConfigurationError
    config_unset = Config(base_url=BASE_URL, token=TOKEN, timeout=5.0, upload_root=None)
    client_unset = EbteqdeskClient(config_unset, transport=httpx2.MockTransport(always_json(200, {})))
    with pytest.raises(ConfigurationError) as exc_unset:
        await client_unset.attach_screenshot_to_todo(1, str(inside_file))
    assert "EBTEQDESK_UPLOAD_ROOT" in str(exc_unset.value)

    # 2. File outside root raises LocalFileError
    config_set = Config(base_url=BASE_URL, token=TOKEN, timeout=5.0, upload_root=str(tmp_path))
    client_set = EbteqdeskClient(
        config_set,
        transport=httpx2.MockTransport(
            always_json(201, {"data": {"id": 100, "name": "inside.png"}})
        ),
    )
    with pytest.raises(LocalFileError) as exc_outside:
        await client_set.attach_screenshot_to_todo(1, str(outside_file))
    assert "outside" in str(exc_outside.value).lower()

    # 3. File inside root succeeds
    receipt = await client_set.attach_screenshot_to_todo(1, str(inside_file))
    assert receipt["data"]["name"] == "inside.png"


async def test_get_todo_attachment_returns_attachment_image(make_client) -> None:
    headers = {
        "Content-Type": "image/png",
        "X-Image-Width": "800",
        "X-Image-Height": "600",
        "X-Image-Source-Width": "1600",
        "X-Image-Source-Height": "1200",
        "X-Image-Source-Bytes": "2048",
        "X-Image-Downscaled": "1",
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/v1/todos/attachments/20"
        assert request.url.query.decode() == "max_dimension=1200"
        return httpx2.Response(200, content=PNG_BYTES, headers=headers)

    client, recorder = make_client(handler)

    image = await client.get_todo_attachment(20, max_dimension=1200)

    assert image.mime_type == "image/png"
    assert image.width == 800
    assert image.height == 600
    assert image.source_width == 1600
    assert image.source_height == 1200
    assert image.downscaled is True
    assert image.data == PNG_BYTES


# --------------------------------------------------------------------------- #
# MCP Tool Layer Tests
# --------------------------------------------------------------------------- #


async def test_list_todos_mcp_tool_envelopes_text(wired) -> None:
    wired(always_json(200, {"data": [todo_payload(1), todo_payload(2)]}))

    result = await srv.mcp.call_tool("list_todos", {"status": "todo", "limit": 10})

    assert not result.is_error
    data = result.structured_content["data"]
    assert len(data) == 2
    assert data[0]["title"] == {"untrusted_content": "Follow up on salon subscription inquiry"}
    assert data[0]["description"] == {
        "untrusted_content": "Customer asked about Salon V3 plan renewal pricing."
    }
    assert "untrusted_content_notice" in result.structured_content


async def test_get_todo_mcp_tool_envelopes_comments(wired) -> None:
    wired(always_json(200, {"data": todo_payload(1)}))

    result = await srv.mcp.call_tool("get_todo", {"todo_id": 1})

    assert not result.is_error
    data = result.structured_content["data"]
    assert data["title"] == {"untrusted_content": "Follow up on salon subscription inquiry"}
    comment = data["comments"][0]
    assert comment["body"] == {
        "untrusted_content": "Drafted preliminary reply with pricing chart."
    }
    assert comment["author"] == {"untrusted_content": "Agent Smith"}


async def test_get_todo_attachment_mcp_tool_returns_image_block(wired) -> None:
    headers = {
        "Content-Type": "image/png",
        "X-Image-Width": "800",
        "X-Image-Height": "600",
        "X-Image-Source-Width": "1600",
        "X-Image-Source-Height": "1200",
        "X-Image-Source-Bytes": "2048",
        "X-Image-Downscaled": "1",
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=PNG_BYTES, headers=headers)

    config = Config(base_url=BASE_URL, token=TOKEN, timeout=5.0)
    client = EbteqdeskClient(config, transport=httpx2.MockTransport(handler))
    wired(lambda req: handler(req))

    result = await srv.mcp.call_tool("get_todo_attachment", {"attachment_id": 20})

    assert not result.is_error
    content = result.content
    assert len(content) == 2
    # Metadata block
    meta = json.loads(content[0].text)
    assert meta["attachment_id"] == 20
    assert meta["width"] == 800
    assert meta["downscaled"] is True
    # Image block
    assert [block.type for block in content] == ["text", "image"]
    assert content[1].mime_type == "image/png"
    assert base64.b64decode(content[1].data) == PNG_BYTES


async def test_create_todo_mcp_tool_round_trips(wired) -> None:
    wired(always_json(201, {"data": todo_payload(42, title="New task")}))

    result = await srv.mcp.call_tool(
        "create_todo",
        {"title": "New task", "priority": "high", "labels": ["test"]},
    )

    assert not result.is_error
    data = result.structured_content["data"]
    assert data["id"] == 42
    assert data["title"] == {"untrusted_content": "New task"}


async def test_update_todo_status_mcp_tool_round_trips(wired) -> None:
    wired(always_json(200, {"data": todo_payload(42, status="done")}))

    result = await srv.mcp.call_tool(
        "update_todo_status",
        {"todo_id": 42, "status": "done"},
    )

    assert not result.is_error
    assert result.structured_content["data"]["status"] == "done"


async def test_comment_on_todo_mcp_tool_round_trips(wired) -> None:
    wired(
        always_json(
            201,
            {
                "data": {
                    "id": 99,
                    "body": "Note added via Grok",
                    "author": "Grok AI",
                    "source": "grok",
                }
            },
        )
    )

    result = await srv.mcp.call_tool(
        "comment_on_todo",
        {"todo_id": 42, "body": "Note added via Grok"},
    )

    assert not result.is_error
    data = result.structured_content["data"]
    assert data["id"] == 99
    assert data["body"] == {"untrusted_content": "Note added via Grok"}


async def test_todo_tools_refuse_without_proper_scopes(wired) -> None:
    wired(scope_refusal(TODO_READ_SCOPE, requested=["ticket:read"], scopes=["ticket:read"]))

    with pytest.raises(ToolError) as exc_read:
        await srv.mcp.call_tool("list_todos", {})
    assert "todo:read" in str(exc_read.value)

    wired(scope_refusal(TODO_WRITE_SCOPE, requested=["todo:read"], scopes=["todo:read"]))

    with pytest.raises(ToolError) as exc_write:
        await srv.mcp.call_tool("create_todo", {"title": "Task"})
    assert "todo:write" in str(exc_write.value)
