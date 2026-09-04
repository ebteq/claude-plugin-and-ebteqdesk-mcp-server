# Ebteqdesk agent plugin

Claude Code skills for working a [Ebteqdesk](https://github.com/ebteq) helpdesk through
its MCP server: tickets, the escalation queue, the knowledge base, and the reports.

```bash
claude plugin marketplace add ebteq/claude-plugin-and-ebteqdesk-mcp-server
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
claude plugin marketplace add ebteq/claude-plugin-and-ebteqdesk-mcp-server
claude plugin install ebteqdesk

# 2. server: uninstall the old package first — 3.0.0 ships no `warnidesk-mcp`
#    script, but the old package's one stays on PATH until it is removed, and a
#    config still pointing at that name keeps silently launching 1.6.0
uv tool uninstall warnidesk-mcp
uv tool install "git+https://github.com/ebteq/claude-plugin-and-ebteqdesk-mcp-server@main#subdirectory=mcp-server"

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

**The server is in this repository**, under [`mcp-server/`](mcp-server/). It is a Python
package and is installed separately from the plugin — installing the plugin gives you the
skills, not the tools:

```bash
uv tool install "git+https://github.com/ebteq/claude-plugin-and-ebteqdesk-mcp-server@main#subdirectory=mcp-server"
```

⚠️ **This repository is private**, so that install needs `git` and a GitHub account with
access to it. If you are not on the Ebteqdesk team, this plugin is readable but not
runnable. Your Ebteqdesk install's **Settings → API keys** page carries the current install
and registration commands, along with the scopes a key needs; the long version is in
[`mcp-server/README.md`](mcp-server/README.md).

## What is in here

| | |
|---|---|
| `.claude-plugin/marketplace.json` | the marketplace manifest — this repo publishes one plugin |
| `plugin/` | the plugin itself: manifest, README, and the four skills |
| `mcp-server/` | the `ebteqdesk-mcp` MCP server those skills call — a Python package installed with `pip`, `pipx` or `uv tool` |

| Skill | Covers |
|---|---|
| `tickets` | read a ticket and its conversation, reply or file an internal note, move it between working states, close it |
| `escalations` | the shared install-wide escalation queue, escalating and de-escalating, replying on an escalated ticket |
| `knowledge-base` | search and read articles, walk the tree, create categories and folders, author an article to the house standard |
| `reports` | the account-wide ticket report and the per-category escalation report, and the date traps that make them disagree |

## Accuracy

Every factual claim in a skill — which scope a tool needs, what a default does, which call
emails a customer — is checked against the MCP server's own tool definitions, in
[`mcp-server/src/ebteqdesk_mcp/server.py`](mcp-server/src/ebteqdesk_mcp/server.py).

**Verified against `ebteqdesk-mcp` 1.6.0 (32 tools).**

That file is now **in this repository**, so a change to a tool description and the skill
change that follows from it land in **one pull request** again. That rule — not a version
string — is what keeps the two in step, and it was simply unavailable for as long as the
server was a separate repository. Ship them together; a PR that edits `server.py` and no
`SKILL.md` should be asked why.

The version line is not a stamp of what is in `mcp-server/`; it names the build a human
last read the tool descriptions against. If it does not match the `serverInfo.version`
your host reports at connect time, treat these skills as possibly stale and trust the
tool descriptions over them.

⚠️ **Right now it does not match, and this is exactly where the gap is.** The server in
this repository is **4.2.0, with 42 tools**. The skills have **not** been re-verified
against it — that is outstanding work in its own right, not something the repository merge
did or could do. The known gaps:

| Landed in | What the skills get wrong or miss |
|---|---|
| 2.1.0 | `list_kb_proposals` — a read tool, not mentioned in `knowledge-base` |
| 4.0.0 | 🔴 **A live wrong claim, not just a hole.** `update_kb_article` on a **published** article stopped failing with `409` and now succeeds with `202`, staging a pending revision and handing back the **live, unchanged** article. `knowledge-base/SKILL.md` still says "A published article is refused with 409, not edited and not unpublished" |
| 4.1.0 | `propose_kb_article` and `update_kb_article` gained eight optional `en_*` / `zhcn_*` arguments so one call carries both languages. The skill's bilingual guidance predates them, and on a **published** article the old one-call-per-locale approach now *replaces* rather than accumulates — the second call drops the first language |
| 4.2.0 | the nine agent-provisioning tools — `list_agents`, `get_agent`, `list_roles`, `list_groups`, `list_api_keys`, `create_agent`, `update_agent`, `issue_api_key`, `revoke_api_key`. No skill mentions any of them; four write, two return a secret exactly once, and none can be undone through the API |

What the skills *do* describe was checked against 1.6.0, and no tool that existed then has
been renamed or removed, or changed the meaning of an argument a 1.6.0-era caller would
pass. So the rest is still usable: the gap is the rows above, not silent drift everywhere.
Treat anything touching the knowledge base write path with particular care until the
re-verification pass happens.

## Licence

MIT — see [LICENSE](LICENSE).
