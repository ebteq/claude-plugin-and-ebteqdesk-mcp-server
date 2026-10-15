"""ebteqdesk-mcp — an MCP server over the Ebteqdesk v1 REST API.

Public surface:

    from ebteqdesk_mcp import Config, EbteqdeskClient   # the HTTP client alone
    from ebteqdesk_mcp import mcp, run                  # the MCP server

`EbteqdeskClient` is usable on its own, without MCP, as a plain async REST
client — that is the point of keeping the HTTP layer free of any MCP import.
"""

from __future__ import annotations

from .client import AttachmentImage, EbteqdeskClient
from .config import Config
from .errors import (
    AbilityError,
    ApiError,
    AuthenticationError,
    ConfigurationError,
    ConflictError,
    DirectUploadUnavailableError,
    InvalidRequestError,
    KeyScopeError,
    LocalFileError,
    MalformedResponseError,
    NotAgentAuthoredError,
    NotFoundError,
    PayloadTooLargeError,
    PermissionError_,
    RateLimitedError,
    RoleScopeError,
    ScopeError,
    ServerError,
    StaleAttachmentApiError,
    StaleThreadEntryApiError,
    TicketNotAssignedError,
    TransportError,
    UnsupportedMediaError,
    UploadTransferError,
    EbteqdeskError,
)
from .server import mcp, run

# Single-sourced; `pyproject.toml` reads the same file. See _version.py.
from ._version import __version__

__all__ = [
    "__version__",
    "Config",
    "EbteqdeskClient",
    "AttachmentImage",
    "EbteqdeskError",
    "ConfigurationError",
    "LocalFileError",
    "UploadTransferError",
    "TransportError",
    "MalformedResponseError",
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
    "mcp",
    "run",
]
