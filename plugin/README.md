# Ebteqdesk agent plugin

Four Claude Code skills for working an Ebteqdesk helpdesk through its MCP server:

| Skill | Covers |
|---|---|
| `ebteqdesk:tickets` | List and read tickets, the full conversation, attachments, reply, internal notes, move a ticket between working states or reopen one, open a ticket, close one |
| `ebteqdesk:escalations` | The shared escalation queue, reading and replying on escalated tickets, escalate, de-escalate, close |
| `ebteqdesk:knowledge-base` | Search and read articles, the category/folder tree, propose and update articles, upload and embed screenshots, review verdicts, create/delete categories and folders, reorder |
| `ebteqdesk:reports` | The account-wide ticket report and the per-category escalation report |

**Verified against `ebteqdesk-mcp` 1.6.0 (32 tools).** Every `SKILL.md` carries
the same line. If the server you are connected to reports a different version in
`serverInfo.version`, treat these skills as possibly stale.

⚠️ **It currently reports 4.2.0 (42 tools), so treat them as stale — but only in
the four places below.** The server in this repository has moved ten tools and
one behaviour ahead of the last re-read of these skills, which is outstanding
work and not something the repository merge closed. See
[Keeping these skills true](#keeping-these-skills-true) for the itemised list.

## What this plugin is not

🔴 **It does not install or configure the MCP server, and it deliberately ships
no `.mcp.json`.**

The Ebteqdesk MCP server is configured with a personal access token. Putting
that token in a file that lives in a repository — or in any file a plugin
installs — is how credentials leak. Registration therefore stays a command the
user runs, which is what **Settings → API keys** on your Ebteqdesk hands you:

```bash
claude mcp add ebteqdesk \
  --env EBTEQDESK_BASE_URL=https://your-ebteqdesk.example.com \
  --env EBTEQDESK_API_TOKEN='<your-api-key>' \
  -- ebteqdesk-mcp
```

The skills assume that server is already connected. If it is not, they say so
rather than guessing.

⚠️ **Installing the plugin gives you the skills, not the tools.** The
`ebteqdesk-mcp` executable the command above invokes is a Python package and has
to be installed separately. It lives beside this plugin, in the same repository,
under [`mcp-server/`](../mcp-server/):

```bash
uv tool install "git+https://github.com/ebteq/claude-plugin-and-ebteqdesk-mcp-server@main#subdirectory=mcp-server"
```

That repository is **private**, so the install needs `git` and a GitHub account
with access to it. Without the server, every tool named in these skills is simply
absent, and the skills will tell you the server is not connected.

## Install

```bash
claude plugin marketplace add ebteq/claude-plugin-and-ebteqdesk-mcp-server
claude plugin install ebteqdesk
```

⚠️ **Upgrading from 1.x?** The plugin, the marketplace, the MCP server key, the
env vars and the console script all changed name in 2.0.0, and the env vars and
the console script changed with **no fallback**. The repository root README has
the full table and the commands; running the install above without doing the
rest leaves you with skills whose tools are absent.

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
claude plugin marketplace add /path/to/your/claude-plugin-and-ebteqdesk-mcp-server/checkout
claude plugin install ebteqdesk@ebteqdesk
claude plugin details ebteqdesk          # component inventory + token cost
# ...
claude plugin marketplace remove ebteqdesk
```

## Keeping these skills true

Every factual claim in a `SKILL.md` — which scope a tool needs, what a default
does, which call emails a customer — comes from the MCP server's own tool
descriptions in
[`mcp-server/src/ebteqdesk_mcp/server.py`](../mcp-server/src/ebteqdesk_mcp/server.py).

✅ **That file is in this repository**, one directory over. The
same-pull-request rule therefore holds again: a tool description and the skill
text that restates it change together, in one commit, reviewed side by side. That
is the mechanism. Do not merge a change to `server.py` that leaves a `SKILL.md`
describing the old behaviour.

The **"Verified against `ebteqdesk-mcp` 1.6.0 (32 tools)"** line at the top of
this README and of every `SKILL.md` is not standing in for that rule and is not a
version stamp. It records **the build a human last read the tool descriptions
against**. Move it in all five files together only *after* doing that reading. A
skill whose line names an older version than the server you are connected to has
not been checked against it — that mismatch is the warning, and bumping the line
as bookkeeping destroys the only signal a reader has.

🔴 **The line is doing its job right now: the server in `mcp-server/` is 4.2.0
with 42 tools, and these skills have not been re-read against it.** That pass is
outstanding work with its own review; the repository merge did not do it. What is
known to be missing or wrong:

| Landed in | What these skills get wrong or miss |
|---|---|
| 2.1.0 | `list_kb_proposals` — a read tool, not mentioned in `knowledge-base` |
| 4.0.0 | 🔴 **An active falsehood.** `update_kb_article` on a **published** article no longer fails with `409`; it succeeds with `202`, stages a pending revision, and returns the **live, unchanged** article — so a caller reading its own edit back finds the old text and can wrongly conclude nothing happened. `knowledge-base/SKILL.md` still documents the refusal |
| 4.1.0 | `propose_kb_article` and `update_kb_article` gained eight optional `en_*` / `zhcn_*` arguments so one call carries both languages. The bilingual guidance here predates them, and against a **published** article the old call-once-per-locale approach now *replaces* instead of accumulating — the second call drops the first language |
| 4.2.0 | the nine agent-provisioning tools: `list_agents`, `get_agent`, `list_roles`, `list_groups`, `list_api_keys`, `create_agent`, `update_agent`, `issue_api_key`, `revoke_api_key`. No skill mentions any of them. Four write, two return a secret exactly once, and they need `admin:read` / `admin:write`, which no key minted before 4.2.0 carries |

No tool that existed at 1.6.0 has been renamed or removed, or changed the meaning
of an argument a 1.6.0-era caller would pass. The rest of what these skills say
still holds; the knowledge base write path is where to be careful.
