"""Configuration, read from the environment exactly once per process.

Three variables, no config file, no CLI flags:

    EBTEQDESK_API_TOKEN   required   an Ebteqdesk personal access token
    EBTEQDESK_BASE_URL    required   the site root, e.g. https://help.example.com
    EBTEQDESK_TIMEOUT     optional   per-request timeout in seconds (default 30)

🔴 THE PRE-RENAME `WARNIDESK_` SPELLINGS ARE NO LONGER READ. They were accepted
with a deprecation notice through 1.x and were removed, not deprecated further,
in 2.0.0. The failure this produces is quiet and does not name the rename: the
server starts (a missing variable is only fatal when it is read), and then every
call fails as unconfigured. An operator who reports "it connected and then
nothing worked" after an upgrade has almost certainly still got
`WARNIDESK_API_TOKEN` in their host config. The errors below name the variable
they should be setting; that is the only breadcrumb, so it must stay accurate.

There is deliberately no default for the base URL. A wrong-by-default host is
worse than a missing one: it turns a configuration mistake into a connection
error against somebody else's server, and the user has no way to tell which of
the two happened.

THE TOKEN IS NEVER LOGGED, NEVER REPEATED IN AN ERROR AND NEVER PUT IN A URL.
`Config` has no `__repr__` that can leak it (see `__repr__` below), errors carry
`token_fingerprint` at most, and the token reaches the wire only as an
`Authorization` header set on the HTTP client.

🔴 ANY DIAGNOSTIC THIS MODULE EVER GROWS GOES TO STDERR, NEVER STDOUT. stdout is
the MCP stdio transport (see `__main__.py`) and one stray byte on it corrupts
the JSON-RPC framing, killing the session with a parse error that names no
cause. A friendly `print()` telling an operator to fix a variable is exactly the
shape of that bug — it is how the removed deprecation notices were written, and
the rule outlived them.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Mapping

from .errors import ConfigurationError

#: The one prefix. There is no longer a second one — see the module docstring.
ENV_PREFIX = "EBTEQDESK_"

#: The suffixes, which did NOT change in the rename; only the prefix did. They
#: stay separate constants because the messages below interpolate them.
SUFFIX_TOKEN = "API_TOKEN"
SUFFIX_BASE_URL = "BASE_URL"
SUFFIX_TIMEOUT = "TIMEOUT"

ENV_TOKEN = ENV_PREFIX + SUFFIX_TOKEN
ENV_BASE_URL = ENV_PREFIX + SUFFIX_BASE_URL
ENV_TIMEOUT = ENV_PREFIX + SUFFIX_TIMEOUT

DEFAULT_TIMEOUT_SECONDS = 30.0

#: Suffixes stripped off a pasted base URL. Every endpoint path this client
#: builds already starts with `/api/v1`, so a base URL that also ends in it
#: would produce `/api/v1/api/v1/user` — a 404 with nothing in it to explain
#: why. Trimming is unambiguous (no Ebteqdesk install is *served* under a path
#: ending in `/api/v1`) and it is documented in the README rather than silent.
_REDUNDANT_SUFFIXES = ("/api/v1", "/api")


@dataclass(frozen=True)
class Config:
    """Everything the client needs to make a request."""

    base_url: str
    """Site root, no trailing slash. Endpoint paths are appended to it."""

    token: str
    """The bearer token. Never render this — see `token_fingerprint`."""

    timeout: float
    """Per-request timeout in seconds, applied to connect, read and write."""

    @property
    def token_fingerprint(self) -> str:
        """A stable, non-reversible 8-hex-char tag for the token.

        This exists so a user with three keys can tell from a log line WHICH one
        a failing server is rejecting, without the log line containing a
        credential. It is a SHA-256 prefix, so it cannot be turned back into the
        token and two different tokens practically never collide.
        """
        return hashlib.sha256(self.token.encode("utf-8")).hexdigest()[:8]

    def __repr__(self) -> str:
        """Redacted on purpose.

        The default dataclass repr would print the token, and this object ends
        up inside tracebacks, pytest assertion diffs and `logging` calls with
        `%r`. Overriding it is the only way to make leaking the token require
        deliberate effort rather than being the accident-shaped path.
        """
        return (
            f"Config(base_url={self.base_url!r}, "
            f"token=<redacted:{self.token_fingerprint}>, "
            f"timeout={self.timeout!r})"
        )

    def url(self, path: str) -> str:
        """Absolute URL for an API path such as ``/api/v1/user``."""
        return f"{self.base_url}/{path.lstrip('/')}"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        """Build a Config, or raise ConfigurationError naming the fix.

        Raises:
            ConfigurationError: a variable is missing, empty or unusable. The
                message always names the variable and what a good value looks
                like — this is the single most common failure and the user is
                usually reading it inside a Claude Code tool result, with no
                stack trace and no server logs to fall back on.
        """
        env = os.environ if env is None else env

        return cls(
            base_url=_read_base_url(env),
            token=_read_token(env),
            timeout=_read_timeout(env),
        )


def _read(env: Mapping[str, str], suffix: str) -> str:
    """Read one `EBTEQDESK_<suffix>`, stripped. Absent and empty are the same.

    Whitespace-only is treated as absent deliberately: `--env FOO=` and a
    variable set to a stray space are both configuration mistakes, and the
    "is not set" errors below say something useful about them, whereas a value
    of `""` would sail through and fail much later against the server.
    """
    return (env.get(ENV_PREFIX + suffix) or "").strip()


def _read_token(env: Mapping[str, str]) -> str:
    raw = _read(env, SUFFIX_TOKEN)

    if not raw:
        raise ConfigurationError(
            f"{ENV_TOKEN} is not set. Create an Ebteqdesk API token "
            "(Settings -> API keys, or `php artisan tinker` on a local stack) and "
            f"set {ENV_TOKEN} to it — for Claude Code, pass it with "
            f"`claude mcp add ... --env {ENV_TOKEN}=<token>`. If you are "
            "upgrading from 1.x, the old WARNIDESK_API_TOKEN name is no longer "
            "read; rename it, the value is unchanged."
        )

    # A token pasted from a shell that ate the quotes around `6|abc...` arrives
    # truncated at the pipe, which the server then answers with a bare 401 and
    # the user spends an hour on it. Catching the shape here names the actual
    # problem. This is a HINT, not a format check: the server owns token format
    # and this must not start rejecting tokens Ebteqdesk considers valid, so it
    # only fires on the specific mangled shape.
    if raw.endswith("|"):
        raise ConfigurationError(
            f"{ENV_TOKEN} ends with '|', so it looks truncated. An Ebteqdesk "
            "token is `<id>|<secret>`; quote it when exporting it from a shell, "
            f"because '|' is a pipe: export {ENV_TOKEN}='6|abc...'."
        )

    return raw


def _read_base_url(env: Mapping[str, str]) -> str:
    raw = _read(env, SUFFIX_BASE_URL)

    if not raw:
        raise ConfigurationError(
            f"{ENV_BASE_URL} is not set. Point it at the ROOT of your Ebteqdesk "
            "install — for example http://localhost:8086 or "
            "https://help.example.com — not at an /api/v1 path. If you are "
            "upgrading from 1.x, the old WARNIDESK_BASE_URL name is no longer "
            "read; rename it, the value is unchanged."
        )

    url = raw.rstrip("/")

    if "://" not in url:
        raise ConfigurationError(
            f"{ENV_BASE_URL}={raw!r} has no scheme. Include http:// or https:// — "
            "a bare host is ambiguous and would silently be sent in the clear."
        )

    scheme = url.split("://", 1)[0].lower()
    if scheme not in ("http", "https"):
        raise ConfigurationError(
            f"{ENV_BASE_URL}={raw!r} uses the {scheme!r} scheme. "
            "Only http and https are supported."
        )

    for suffix in _REDUNDANT_SUFFIXES:
        if url.lower().endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
            break

    if not url.split("://", 1)[1]:
        raise ConfigurationError(
            f"{ENV_BASE_URL}={raw!r} has no host. Expected something like "
            "http://localhost:8086."
        )

    return url


def _read_timeout(env: Mapping[str, str]) -> float:
    raw = _read(env, SUFFIX_TIMEOUT)

    # Absence is not an error here — the default applies either way, so there is
    # no upgrade note: an operator whose stale WARNIDESK_TIMEOUT stopped being
    # read gets the default, not a failure, which is the correct outcome for an
    # optional tuning knob.
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS

    try:
        timeout = float(raw)
    except ValueError:
        raise ConfigurationError(
            f"{ENV_TIMEOUT}={raw!r} is not a number. It is a timeout in SECONDS, "
            f"for example {ENV_TIMEOUT}=60."
        ) from None

    if timeout <= 0:
        raise ConfigurationError(
            f"{ENV_TIMEOUT}={raw!r} must be greater than zero. Omit it entirely "
            f"for the default of {DEFAULT_TIMEOUT_SECONDS:g}s; there is no "
            "'wait forever' setting, because an MCP tool that never returns "
            "hangs the conversation with no way to tell it is stuck."
        )

    return timeout
