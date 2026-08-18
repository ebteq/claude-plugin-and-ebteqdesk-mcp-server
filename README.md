# Warnidesk agent plugin

Claude Code skills for working a [Warnidesk](https://github.com/ebteq) helpdesk through
its MCP server: tickets, the escalation queue, the knowledge base, and the reports.

```bash
claude plugin marketplace add ebteq/claude-plugin
claude plugin install warnidesk
```

That installs four skills. It does **not** install or configure the MCP server, and it
deliberately ships no `.mcp.json` — that file's job would be to hold an API token, and a
credential in a file a plugin installs is how credentials leak.

## You need the MCP server too

The skills describe tools that come from the `warnidesk-mcp` server. Without it, an agent
loads the skills and finds none of the tools it is told to call.

⚠️ **The server ships from a private repository** and needs access to install. If you are
not on the Warnidesk team, this plugin is readable but not runnable. Your Warnidesk
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

**Verified against `warnidesk-mcp` 1.6.0 (32 tools).**

The server lives in a different, private repository, so a change there and the skill
describing it can no longer land in one pull request. That version line is the mitigation:
if it does not match the `serverInfo.version` your host reports at connect time, treat
these skills as possibly stale and trust the tool descriptions over them.

## Licence

MIT — see [LICENSE](LICENSE).
