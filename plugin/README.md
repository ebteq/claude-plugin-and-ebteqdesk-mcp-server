# Warnidesk agent plugin

Four Claude Code skills for working a Warnidesk helpdesk through its MCP server:

| Skill | Covers |
|---|---|
| `warnidesk:tickets` | List and read tickets, the full conversation, attachments, reply, internal notes, move a ticket between working states or reopen one, open a ticket, close one |
| `warnidesk:escalations` | The shared escalation queue, reading and replying on escalated tickets, escalate, de-escalate, close |
| `warnidesk:knowledge-base` | Search and read articles, the category/folder tree, propose and update articles, upload and embed screenshots, review verdicts, create/delete categories and folders, reorder |
| `warnidesk:reports` | The account-wide ticket report and the per-category escalation report |

**Verified against `warnidesk-mcp` 1.6.0 (32 tools).** Every `SKILL.md` carries
the same line. If the server you are connected to reports a different version in
`serverInfo.version`, treat these skills as possibly stale.

## What this plugin is not

🔴 **It does not install or configure the MCP server, and it deliberately ships
no `.mcp.json`.**

The Warnidesk MCP server is configured with a personal access token. Putting
that token in a file that lives in a repository — or in any file a plugin
installs — is how credentials leak. Registration therefore stays a command the
user runs, which is what **Settings → API keys** on your Warnidesk hands you:

```bash
claude mcp add warnidesk \
  --env WARNIDESK_BASE_URL=https://your-warnidesk.example.com \
  --env WARNIDESK_API_TOKEN='<your-api-key>' \
  -- warnidesk-mcp
```

The skills assume that server is already connected. If it is not, they say so
rather than guessing.

⚠️ **The `warnidesk-mcp` server itself is distributed from a private
repository.** This plugin is public; the server is not. Installing the plugin
gives you the skills, not the tools — the `warnidesk-mcp` executable the command
above invokes has to be installed separately, from a repository you need access
to. Without it every tool named in these skills is simply absent, and the skills
will tell you the server is not connected.

## Install

```bash
claude plugin marketplace add ebteq/claude-plugin
claude plugin install warnidesk
```

## Scopes

The skills only do what the key allows. The **read-only** key the API page
recommends by default (`ticket:read`, `kb:read`, `escalation:read`,
`reports:read`, `escalation-reports:read`) makes the ticket, escalation and
report reads here work and every write refuse with a 403.

⚠️ **It does not cover every read.** Four knowledge base tools that write nothing
— `list_kb_tree`, `list_kb_categories`, `list_kb_folders` and
`get_kb_article_review` — sit behind `kb:write` **plus** the `kb.manage` role
ability, because their corpus is the authoring one: ids, drafts and internal
folders. A read-only key gets a 403 on those *reads*. See the
`knowledge-base` skill.

To let an agent actually **work** the desk — reply, note, escalate, close, change
ticket status, propose articles, upload media — the key additionally needs
`ticket:write`, `kb:write` and `escalation:write`. Those tools email real
customers, notify the whole team, upload local files, and cannot be undone
through this API. Mint that key deliberately.

Two gates, not one: a scope resolves only while the **key** carries it and the
**account's role** grants the ability behind it. `kb.manage` is granted to
administrators and supervisors only. `whoami` shows both lists —
`apiKey.requested` (what the key was minted with) and `apiKey.scopes` (what
actually resolves) — and the difference tells you whether a new key would help.

Scopes are fixed when a key is created. A key missing one has to be replaced,
not widened. A **role** missing an ability is not fixed by a new key at all.

## Working on these skills

Test a change without pushing it, by adding the repo as a local marketplace:

```bash
claude plugin marketplace add /path/to/your/claude-plugin/checkout
claude plugin install warnidesk@warnidesk
claude plugin details warnidesk          # component inventory + token cost
# ...
claude plugin marketplace remove warnidesk
```

## Keeping these skills true

Every factual claim in a `SKILL.md` — which scope a tool needs, what a default
does, which call emails a customer — comes from the MCP server's own tool
descriptions in `clients/python/src/warnidesk_mcp/server.py`.

⚠️ **That file lives in a separate, private repository.** It is the source of
truth, and it is not in this one, so the same-pull-request rule that used to keep
the two in step cannot hold across a repository boundary.

What replaces it is the **"Verified against `warnidesk-mcp` 1.6.0 (32 tools)"**
line at the top of this README and of every `SKILL.md`. When the server ships a
new version, re-read its tool descriptions, correct whatever moved, and bump that
line in all five files together. A skill whose line names an older version than
the server you are connected to has not been checked against it — treat the
mismatch as the warning it is.
