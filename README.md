# Ebteqdesk agent plugin

Claude Code skills for working a [Ebteqdesk](https://github.com/ebteq) helpdesk through
its MCP server: tickets, the escalation queue, the knowledge base, and the reports.

```bash
claude plugin marketplace add ebteq/claude-plugin
claude plugin install ebteqdesk
```

That installs four skills. It does **not** install or configure the MCP server, and it
deliberately ships no `.mcp.json` — that file's job would be to hold an API token, and a
credential in a file a plugin installs is how credentials leak.

## Upgrading from 1.x — everything below is a rename, and three of them break

1.x was published as `warnidesk`. 2.0.0 is the Warnidesk → Ebteqdesk rename landing
across the plugin, the MCP server and the desk's own **Settings → API keys** page in one
release. Four things changed name; the first three need an action from you.

| What | 1.x | 2.0.0 | Breaks? |
|---|---|---|---|
| Environment variables | `WARNIDESK_BASE_URL`, `WARNIDESK_API_TOKEN` | `EBTEQDESK_BASE_URL`, `EBTEQDESK_API_TOKEN` | 🔴 **yes, hard** |
| Console script | `warnidesk-mcp` | `ebteqdesk-mcp` | 🔴 **yes, hard** |
| Plugin + marketplace | `warnidesk` | `ebteqdesk` | 🔴 yes — reinstall |
| MCP server key | `warnidesk` | `ebteqdesk` | no — see below |

🔴 **The environment variables and the console script no longer accept their old
names.** Both previously had a compatibility fallback; both fallbacks were removed in
this release rather than deprecated for another cycle. An `~/.claude.json` still saying
`"command": "warnidesk-mcp"` fails to start with `command not found`, which a host
reports as a server that crashed rather than as a rename. A server still passed
`WARNIDESK_API_TOKEN` starts and then refuses every call as unconfigured. Neither
failure names the rename, so fix both before you look anywhere else.

The **MCP server key** is the one rename that does not break. It is chosen by whoever
registers the server and lives in your host's config, so an old install keeps working
under `warnidesk`; the skills accept either prefix for this release and say so. It is
still worth re-registering, because the compatibility goes away in the next release.

```bash
# 1. plugin: remove the old name, add the new one
claude plugin uninstall warnidesk
claude plugin marketplace remove warnidesk
claude plugin marketplace add ebteq/claude-plugin
claude plugin install ebteqdesk

# 2. server: the console script is a different distribution, so uninstall first —
#    `uv tool install` refuses to overwrite a script owned by another package
uv tool uninstall warnidesk-mcp
uv tool install "git+https://github.com/ebteq/ticketing@master#subdirectory=clients/python"

# 3. registration: new key, new env var names, new command
claude mcp remove warnidesk
claude mcp add ebteqdesk \
  --env EBTEQDESK_BASE_URL=https://your-ebteqdesk.example.com \
  --env EBTEQDESK_API_TOKEN='<your-api-key>' \
  -- ebteqdesk-mcp
```

Your desk's **Settings → API keys** page prints step 2 and step 3 with its own hostname
and a freshly minted key already filled in. Prefer it over the commands above — it also
pins the client to the git ref that matches the API that desk actually answers.

## You need the MCP server too

The skills describe tools that come from the `ebteqdesk-mcp` server. Without it, an agent
loads the skills and finds none of the tools it is told to call.

⚠️ **The server ships from a private repository** and needs access to install. If you are
not on the Ebteqdesk team, this plugin is readable but not runnable. Your Ebteqdesk
install's **Settings → API keys** page carries the current install and registration
commands, along with the scopes a key needs.

## What is in here

| | |
|---|---|
| `.claude-plugin/marketplace.json` | the marketplace manifest — this repo publishes one plugin |
| `plugin/` | the plugin itself: manifest, README, and the four skills |

| Skill | Covers |
|---|---|
| `tickets` | read a ticket and its conversation, reply or file an internal note, move it between working states, close it |
| `escalations` | the shared install-wide escalation queue, escalating and de-escalating, replying on an escalated ticket |
| `knowledge-base` | search and read articles, walk the tree, create categories and folders, author an article to the house standard |
| `reports` | the account-wide ticket report and the per-category escalation report, and the date traps that make them disagree |

## Accuracy

Every factual claim in a skill — which scope a tool needs, what a default does, which call
emails a customer — is checked against the MCP server's own tool definitions.

**Verified against `ebteqdesk-mcp` 1.6.0 (32 tools).**

The server lives in a different, private repository, so a change there and the skill
describing it can no longer land in one pull request. That version line is the mitigation:
if it does not match the `serverInfo.version` your host reports at connect time, treat
these skills as possibly stale and trust the tool descriptions over them.

## Licence

MIT — see [LICENSE](LICENSE).
