"""Environment handling, and the guarantee that the token never renders."""

from __future__ import annotations

import pytest

from ebteqdesk_mcp.config import (
    DEFAULT_TIMEOUT_SECONDS,
    ENV_ALLOW_INSECURE_HTTP,
    ENV_BASE_URL,
    ENV_PREFIX,
    ENV_TIMEOUT,
    ENV_TOKEN,
    ENV_UPLOAD_ROOT,
    Config,
)
from ebteqdesk_mcp.errors import ConfigurationError

GOOD_ENV = {
    ENV_BASE_URL: "https://ebteqdesk.test",
    ENV_TOKEN: "6|secret-token-value",
}

def good_env(**overrides: str) -> dict[str, str]:
    """A working environment, plus any overrides (given by bare suffix)."""
    env = {
        ENV_BASE_URL: "https://ebteqdesk.test",
        ENV_TOKEN: "6|secret-token-value",
    }
    env.update({ENV_PREFIX + key: value for key, value in overrides.items()})
    return env


def test_reads_all_three_variables() -> None:
    config = Config.from_env({**GOOD_ENV, ENV_TIMEOUT: "12.5"})

    assert config.base_url == "https://ebteqdesk.test"
    assert config.token == "6|secret-token-value"
    assert config.timeout == 12.5


def test_timeout_defaults_when_absent_or_blank() -> None:
    assert Config.from_env(GOOD_ENV).timeout == DEFAULT_TIMEOUT_SECONDS
    assert Config.from_env({**GOOD_ENV, ENV_TIMEOUT: "   "}).timeout == DEFAULT_TIMEOUT_SECONDS


def test_missing_token_names_the_variable_and_the_fix() -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        Config.from_env({ENV_BASE_URL: "https://ebteqdesk.test"})

    message = str(excinfo.value)
    assert ENV_TOKEN in message
    assert "claude mcp add" in message


def test_missing_base_url_names_the_variable() -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        Config.from_env({ENV_TOKEN: "6|x"})

    assert ENV_BASE_URL in str(excinfo.value)


def test_token_truncated_at_the_shell_pipe_is_diagnosed() -> None:
    """`export TOKEN=6|abc` unquoted leaves `6|`, and the server answers a bare 401."""
    with pytest.raises(ConfigurationError) as excinfo:
        Config.from_env(good_env(API_TOKEN="6|"))

    assert "truncated" in str(excinfo.value)
    assert "quote" in str(excinfo.value).lower()


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://ebteqdesk.test/", "https://ebteqdesk.test"),
        ("https://ebteqdesk.test///", "https://ebteqdesk.test"),
        # Every path this client builds already starts with /api/v1, so a base
        # URL carrying it too would produce /api/v1/api/v1/user.
        ("https://ebteqdesk.test/api/v1", "https://ebteqdesk.test"),
        ("https://ebteqdesk.test/api/v1/", "https://ebteqdesk.test"),
        ("https://ebteqdesk.test/api", "https://ebteqdesk.test"),
        ("  http://localhost:8086  ", "http://localhost:8086"),
        # A sub-path install is left alone — only the API prefixes are trimmed.
        ("https://example.test/helpdesk", "https://example.test/helpdesk"),
    ],
)
def test_base_url_normalisation(raw: str, expected: str) -> None:
    assert Config.from_env(good_env(BASE_URL=raw)).base_url == expected


@pytest.mark.parametrize("raw", ["ebteqdesk.test", "localhost:8086"])
def test_base_url_without_a_scheme_is_refused(raw: str) -> None:
    with pytest.raises(ConfigurationError, match="scheme"):
        Config.from_env(good_env(BASE_URL=raw))


def test_base_url_with_a_non_http_scheme_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="scheme"):
        Config.from_env(good_env(BASE_URL="ftp://ebteqdesk.test"))


@pytest.mark.parametrize("raw", ["soon", "", " abc "])
def test_non_numeric_timeout_is_refused(raw: str) -> None:
    if not raw.strip():
        pytest.skip("blank means 'use the default', covered elsewhere")

    with pytest.raises(ConfigurationError, match="SECONDS|not a number"):
        Config.from_env(good_env(TIMEOUT=raw))


@pytest.mark.parametrize("raw", ["0", "-1"])
def test_non_positive_timeout_is_refused(raw: str) -> None:
    with pytest.raises(ConfigurationError, match="greater than zero"):
        Config.from_env(good_env(TIMEOUT=raw))


# ────────────────────────────────── WHAT REPLACED THE WARNIDESK_ FALLBACK ──
#
# The package was `warnidesk-mcp` through 1.6.0 and its variables were spelled
# `WARNIDESK_*`. 1.x read both names, preferring the new one and warning on
# stderr. 2.0.0 REMOVED that fallback outright.
#
# 🔴 The removal is silent by nature and that is what these tests defend. An
# unset variable is not an error at import time — the server starts, and the
# operator learns something is wrong only when a call fails. The one breadcrumb
# left is the text of the "is not set" errors, which is why the tests below
# assert on their wording rather than just on the exception type.


def test_the_legacy_prefix_is_no_longer_read() -> None:
    """An install still spelled `WARNIDESK_*` must fail, not half-work."""
    with pytest.raises(ConfigurationError) as excinfo:
        Config.from_env(
            {
                "WARNIDESK_BASE_URL": "https://old.test",
                "WARNIDESK_API_TOKEN": "6|old-token",
                "WARNIDESK_TIMEOUT": "42",
            }
        )

    # It fails naming the NEW variable, because that is the one to set.
    assert str(excinfo.value).startswith(ENV_BASE_URL)


def test_missing_variable_errors_name_the_removed_name_as_an_upgrade_hint() -> None:
    """🔴 THE ONLY BREADCRUMB.

    Nothing else in the system says "your variable was renamed". A 1.x operator
    upgrading sees exactly this message and nothing more, so if the reference to
    the old name is ever dropped from these two errors, the rename becomes an
    unfindable configuration bug.
    """
    with pytest.raises(ConfigurationError) as excinfo:
        Config.from_env({})

    message = str(excinfo.value)
    assert message.startswith(ENV_BASE_URL)
    assert "WARNIDESK_BASE_URL" in message
    assert "no longer" in message

    with pytest.raises(ConfigurationError) as excinfo:
        Config.from_env({ENV_BASE_URL: "https://x.test"})

    message = str(excinfo.value)
    assert message.startswith(ENV_TOKEN)
    assert "WARNIDESK_API_TOKEN" in message
    assert "no longer" in message


def test_configuring_the_client_writes_nothing_to_stdout_or_stderr(capsys) -> None:
    """🔴 STDOUT IS THE MCP STDIO TRANSPORT.

    The deprecation notices this module used to emit were the reason the rule
    was written down, and they are gone — but the rule outlived them. One stray
    byte on stdout corrupts the JSON-RPC framing and the host drops the session
    with a parse error naming no cause, so a future diagnostic printed the
    natural way would reintroduce exactly that bug. stderr is asserted empty
    too: there is nothing left to say on a successful load.
    """
    Config.from_env({**GOOD_ENV, ENV_TIMEOUT: "20"})

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"BASE_URL": "ebteqdesk.test"}, "scheme"),
        ({"BASE_URL": "ftp://ebteqdesk.test"}, "scheme"),
        # The `has no host` branch is deliberately absent: it is unreachable on
        # the current `_read_base_url` (a host-less URL trips `has no scheme`
        # first, and the one input that gets past that — `https://api/v1` —
        # raises IndexError instead). That is a PRE-EXISTING defect, unrelated
        # to the rename and not fixed here; it is reported separately.
        ({"BASE_URL": "http://help.example.com"}, "plain http://"),
        ({"API_TOKEN": "6|"}, "truncated"),
        ({"TIMEOUT": "soon"}, "not a number"),
        ({"TIMEOUT": "0"}, "greater than zero"),
    ],
)
def test_errors_quote_the_variable_that_caused_them(
    overrides: dict[str, str], fragment: str
) -> None:
    """Every message names the variable it is about.

    With one spelling left this is no longer the fallback's ambiguity guard, but
    it still catches the ordinary version of that bug: a message that describes
    a base-URL problem while naming the timeout variable sends the reader to the
    wrong line of their config.
    """
    with pytest.raises(ConfigurationError) as excinfo:
        Config.from_env(good_env(**overrides))

    message = str(excinfo.value)
    suffix = next(iter(overrides))

    assert fragment in message
    assert ENV_PREFIX + suffix in message


def test_repr_never_contains_the_token() -> None:
    """This object lands in tracebacks and pytest diffs; the default repr leaks."""
    config = Config.from_env(GOOD_ENV)

    rendered = repr(config)

    assert "secret-token-value" not in rendered
    assert "redacted" in rendered


# ──────────────────────────────── THE BEARER TOKEN AND PLAIN HTTP (5.0.0) ──
#
# 🔴 Every request carries `Authorization: Bearer <token>`. Before 5.0.0 an
# `http://` base URL to ANY host was accepted, so a typo'd scheme or an old
# bookmark put a replayable credential on the wire in the clear. Loopback is the
# one exemption — the bytes never leave the machine and the Docker dev stack is
# served over http — and everything else needs https or a deliberate override.


@pytest.mark.parametrize(
    "raw",
    [
        "http://help.example.com",
        "http://10.0.0.5:8086",
        "http://192.168.1.20",
        "http://0.0.0.0:8086",
        # Names that merely START like a loopback host are not loopback.
        "http://localhost.example.com",
        "http://127.0.0.1.nip.io",
        "HTTP://help.example.com",
    ],
)
def test_plain_http_to_a_remote_host_is_refused(raw: str) -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        Config.from_env(good_env(BASE_URL=raw))

    message = str(excinfo.value)
    assert message.startswith(ENV_BASE_URL)
    assert "https://" in message
    assert "bearer" in message
    # The override is named, so an operator on a private network can find it.
    assert f"{ENV_ALLOW_INSECURE_HTTP}=1" in message


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("http://localhost:8086", "http://localhost:8086"),
        ("http://LOCALHOST:8086/", "http://LOCALHOST:8086"),
        ("http://127.0.0.1", "http://127.0.0.1"),
        # All of 127.0.0.0/8 is loopback, not just .1.
        ("http://127.0.0.2:8000/api/v1", "http://127.0.0.2:8000"),
        ("http://[::1]:8086", "http://[::1]:8086"),
    ],
)
def test_plain_http_to_this_machine_is_still_allowed(raw: str, expected: str) -> None:
    """The local Docker stack is http://localhost:<port>. Breaking it would make
    the safe default unusable for the people most likely to hit it."""
    assert Config.from_env(good_env(BASE_URL=raw)).base_url == expected


def test_the_override_allows_a_remote_http_host_and_says_so_on_stderr(capsys) -> None:
    config = Config.from_env(
        good_env(BASE_URL="http://10.0.0.5:8086", ALLOW_INSECURE_HTTP="1")
    )

    assert config.base_url == "http://10.0.0.5:8086"

    captured = capsys.readouterr()
    # 🔴 STDOUT IS THE MCP TRANSPORT. The warning goes to stderr or nowhere.
    assert captured.out == ""
    assert "UNENCRYPTED" in captured.err
    assert ENV_ALLOW_INSECURE_HTTP in captured.err
    # The warning names the host, never the token.
    assert "10.0.0.5" in captured.err
    assert "secret-token-value" not in captured.err


@pytest.mark.parametrize("value", ["true", "yes", "0", "", " "])
def test_only_the_literal_1_turns_the_override_on(value: str) -> None:
    """A security override is not truthiness-parsed: `=true` stays refused, and
    the refusal tells the operator the exact spelling."""
    with pytest.raises(ConfigurationError, match=f"{ENV_ALLOW_INSECURE_HTTP}=1"):
        Config.from_env(
            good_env(BASE_URL="http://help.example.com", ALLOW_INSECURE_HTTP=value)
        )


def test_https_never_warns_even_with_the_override_set(capsys) -> None:
    Config.from_env(good_env(ALLOW_INSECURE_HTTP="1"))

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


# ─────────────────────────────────────────────── THE UPLOAD ROOT (5.0.0) ──


def test_the_upload_root_is_read_verbatim_and_validated_later() -> None:
    """Validated by the two tools that read files — `upload_kb_media` and
    `attach_file_to_ticket` — not here: a deleted upload folder must cost those
    two, not every tool on the server."""
    config = Config.from_env(good_env(UPLOAD_ROOT="  ~/does-not-exist-yet  "))

    assert config.upload_root == "~/does-not-exist-yet"


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_an_absent_or_blank_upload_root_means_uploads_are_off(raw: str | None) -> None:
    env = good_env() if raw is None else good_env(UPLOAD_ROOT=raw)

    assert Config.from_env(env).upload_root is None
    assert ENV_UPLOAD_ROOT == ENV_PREFIX + "UPLOAD_ROOT"


def test_the_repr_shows_the_upload_root_and_still_not_the_token() -> None:
    rendered = repr(Config.from_env(good_env(UPLOAD_ROOT="/srv/uploads")))

    assert "upload_root='/srv/uploads'" in rendered
    assert "secret-token-value" not in rendered
