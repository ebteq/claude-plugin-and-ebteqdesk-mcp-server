# Ebteqdesk agent plugin

Four Claude Code skills for working an Ebteqdesk helpdesk through its MCP server:

| Skill | Covers |
|---|---|
| `ebteqdesk:tickets` | List and read tickets, the full conversation, attachments, reply, internal notes, move a ticket between working states or reopen one, open a ticket, close one |
| `ebteqdesk:escalations` | The shared escalation queue, reading and replying on escalated tickets, escalate, de-escalate, close |
| `ebteqdesk:knowledge-base` | Search and read articles (including per-language versions), the category/folder tree across Ebteqdesk's two knowledge bases (Salon V3 and Warni), propose and update articles, check a review verdict or the proposal queue, upload and embed screenshots, create/delete categories and folders, reorder |
| `ebteqdesk:reports` | The account-wide ticket report and the per-category escalation report |

**Each `SKILL.md` carries its own "Verified against `ebteqdesk-mcp`" line.**
If the server you are connected to reports a different version in
`serverInfo.version`, treat that skill as possibly stale. They do not all say
the same thing any more — see the table below.

⚠️ **The server in this repository reports `4.3.0` (42 tools).**
`ebteqdesk:knowledge-base` was re-read against it in full and its line now says
`4.3.0 (42 tools)`. `ebteqdesk:tickets`, `ebteqdesk:escalations` and
`ebteqdesk:reports` were spot-checked against the same server and no drift was
found in the tools they cover — none of the releases between 1.6.0 and 4.3.0
changed a ticket, escalation or report tool's contract — but that is **not**
the full re-read this line exists to certify, so their lines were deliberately
left at `1.6.0 (32 tools)` rather than bumped on the strength of a spot check.
See [Keeping these skills true](#keeping-these-skills-true) for what is
outstanding and what was found and fixed.

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

The **"Verified against `ebteqdesk-mcp` … (`n` tools)"** line at the top of
this README and of every `SKILL.md` is not standing in for that rule and is not
a version stamp. It records **the build a human last read that specific file's
tool descriptions against**. Move a file's line only *after* doing that
reading for that file — the five lines do not have to move together, and as of
this pass they no longer say the same thing. A skill whose line names an older
version than the server you are connected to has not been checked against it —
that mismatch is the warning, and bumping the line as bookkeeping destroys the
only signal a reader has. **Do not bump a line on the strength of a spot check
or of another skill's re-read** — see `ebteqdesk:tickets` / `ebteqdesk:escalations`
/ `ebteqdesk:reports` below, which were checked but not bumped.

✅ **`ebteqdesk:knowledge-base` was fully re-read against `4.3.0` and its line
now says so.** The items below it used to get wrong or miss are fixed; they are
kept here, struck through, as the record of what the re-read actually found —
removing them silently would be indistinguishable from never having looked.

| Landed in | What `knowledge-base/SKILL.md` got wrong or missed | Status |
|---|---|---|
| 2.1.0 | ~~`list_kb_proposals` — a read tool, not mentioned~~ | ✅ fixed — documented, with its "not yours, not durable, no `body_html`" caveats |
| 4.0.0 | ~~🔴 An active falsehood: `update_kb_article` on a **published** article no longer fails with `409`; it succeeds with `202`, stages a pending revision, and returns the **live, unchanged** article, so a caller reading its own edit back finds the old text and can wrongly conclude nothing happened. The skill still documented the refusal~~ | ✅ fixed — the 409 claim is gone, the `revision` key and the three-way read of it are documented |
| 4.1.0 | ~~`propose_kb_article` and `update_kb_article` gained eight optional `en_*` / `zhcn_*` arguments so one call carries both languages; the bilingual guidance predated them, and on a **published** article the old call-once-per-locale habit now *replaces* the first language instead of adding to it~~ | ✅ fixed — one-call bilingual filing, `locale=`, and `allow_missing_versions` are documented |
| 4.3.0 | Ebteqdesk grew a second knowledge base, Warni, alongside the pre-existing Salon V3; a folder id is now also a portal choice, and filing into the wrong product's folder is unfixable through this API | ✅ addressed as part of this pass — see the skill's "Two knowledge bases" section |
| 4.2.0 | the nine agent-provisioning tools: `list_agents`, `get_agent`, `list_roles`, `list_groups`, `list_api_keys`, `create_agent`, `update_agent`, `issue_api_key`, `revoke_api_key`. No skill mentions any of them — they don't belong to any of the four existing skills' subject matter. Four write, two return a secret exactly once, and they need `admin:read` / `admin:write`, which no key minted before 4.2.0 carries | 🔴 still outstanding — no skill owns this surface yet |

No tool that existed at the version a skill's own line names has been renamed
or removed, or changed the meaning of an argument a caller from that version
would pass — the ordinary minor/major discipline `mcp-server/README.md`
describes. `ebteqdesk:tickets`, `ebteqdesk:escalations` and `ebteqdesk:reports`
still name `1.6.0 (32 tools)`; a spot check of the tools they cover against the
current `4.3.0` server found no drift (none of 2.0.0 through 4.3.0 touched a
ticket, escalation or report tool's arguments, defaults or return shape), but
spot-checking is not the full re-read this line certifies, so their lines stay
where they are until that reading happens. The rest of what those three skills
say still holds; the knowledge base write path was where to be careful, and
now is documented as such.
