# Ebteqdesk agent plugin

Four Claude Code skills for working an Ebteqdesk helpdesk through its MCP server:

| Skill | Covers |
|---|---|
| `ebteqdesk:tickets` | List and read tickets (own queue, or the whole account with `scope="all"`), the full conversation, attachments, reply, internal notes, move a ticket between working states or reopen one, open a ticket, close one |
| `ebteqdesk:escalations` | The shared escalation queue, reading and replying on escalated tickets, escalate, de-escalate, close |
| `ebteqdesk:knowledge-base` | Search and read articles (including per-language versions), the category/folder tree across Ebteqdesk's two knowledge bases (Salon V3 and Warni), propose and update articles, check a review verdict or the proposal queue, upload and embed screenshots, create/delete categories and folders, reorder |
| `ebteqdesk:reports` | The account-wide ticket report and the per-category escalation report |

**Each `SKILL.md` carries its own "Verified against `ebteqdesk-mcp`" line.**
If the server you are connected to reports a different version in
`serverInfo.version`, treat that skill as possibly stale.

✅ **All four skills have now been fully re-read against `4.3.0` (42 tools),
and all four lines say so.** The first pass (`knowledge-base` only) left the
other three at `1.6.0` on a spot check that only compared tool *signatures* —
which is exactly the check this project's own retrospective on that pass
warned was not enough, since neither of the two worst items the KB pass found
(the per-article visibility override, the published-article revision
behaviour) would show up in a signature diff either. The second pass re-read
`tickets`, `escalations` and `reports` against the tool **descriptions**, the
way the KB pass did, and found real drift in all three — worst in `reports`
and `escalations`, both of which carried running advice that was actively
wrong rather than merely incomplete — see
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

The **"Verified against `ebteqdesk-mcp` … (`n` tools)"** line at the top of
this README and of every `SKILL.md` is not standing in for that rule and is not
a version stamp. It records **the build a human last read that specific file's
tool descriptions against**. Move a file's line only *after* doing that
reading for that file — the five lines do not have to move together and are
not required to say the same thing at any given moment. A skill whose line
names an older version than the server you are connected to has not been
checked against it — that mismatch is the warning, and bumping the line as
bookkeeping destroys the only signal a reader has. **Never bump a line on the
strength of a tool-signature spot check** — see the second table below for
why that specific shortcut is called out by name: it is exactly the check that
missed real drift in this repository's own history.

✅ **`ebteqdesk:knowledge-base` was fully re-read against `4.3.0` in the first
pass** (this repository's `4a30ad5`), and its line said so from that point.
The items below it used to get wrong or miss are fixed; they are kept here,
struck through, as the record of what the re-read actually found — removing
them silently would be indistinguishable from never having looked.

| Landed in | What `knowledge-base/SKILL.md` got wrong or missed | Status |
|---|---|---|
| 2.1.0 | ~~`list_kb_proposals` — a read tool, not mentioned~~ | ✅ fixed — documented, with its "not yours, not durable, no `body_html`" caveats |
| 4.0.0 | ~~🔴 An active falsehood: `update_kb_article` on a **published** article no longer fails with `409`; it succeeds with `202`, stages a pending revision, and returns the **live, unchanged** article, so a caller reading its own edit back finds the old text and can wrongly conclude nothing happened. The skill still documented the refusal~~ | ✅ fixed — the 409 claim is gone, the `revision` key and the three-way read of it are documented |
| 4.1.0 | ~~`propose_kb_article` and `update_kb_article` gained eight optional `en_*` / `zhcn_*` arguments so one call carries both languages; the bilingual guidance predated them, and on a **published** article the old call-once-per-locale habit now *replaces* the first language instead of adding to it~~ | ✅ fixed — one-call bilingual filing, `locale=`, and `allow_missing_versions` are documented |
| 4.3.0 | Ebteqdesk grew a second knowledge base, Warni, alongside the pre-existing Salon V3; a folder id is now also a portal choice, and filing into the wrong product's folder is unfixable through this API | ✅ addressed as part of the first pass — see the skill's "Two knowledge bases" section |

✅ **`ebteqdesk:tickets`, `ebteqdesk:escalations` and `ebteqdesk:reports` were
then fully re-read too, in a second pass, and all three lines now say `4.3.0`
as well — all four skills are current as of this pull request.** The first
pass's own spot check of these three (tool names, arguments, defaults — a
signature diff) had found nothing, and that was reported honestly rather than
bumped on it. It was also the wrong instrument: neither of the two worst KB
findings (the per-article visibility override, the published-article revision
behaviour) is a signature change, and a second, description-level re-read of
the other three found the same *class* of thing waiting in two of them —
**one of the two is an active falsehood in the running advice, not a missing
mention:**

| Skill | What it got wrong or missed | Status |
|---|---|---|
| `reports` | 🔴 **An active falsehood.** The skill told a reader to "omit both [dates] for the full history" as if that were true of both report tools. It is true of `get_escalation_report`; `get_reports_summary` with no dates returns **the current calendar month**, not all time — so an "overall" question answered without checking `data.range` was silently scoped to the current month | ✅ fixed — the two tools' "omit both" behaviour is now stated separately |
| `reports` | `get_escalation_report`'s three numeric traps — `escalated / total` is not a percentage and `escalated` can exceed `total`; `escalatedUndated` is identical across every range; `sum(status.*)` can be less than `total` — and its `key`-not-`id` row-identity rule were not in the skill at all, despite the server's own module docstring naming these as exactly the kind of rule "the payload cannot signal" | ✅ fixed — all three rules and the row-identity rule are documented |
| `reports` | `get_reports_summary`'s return shape — units (counts vs. **minutes** vs. **0–100** percents vs. 1–5 rating) and the "null means no data, not zero" rule — was not documented at all | ✅ fixed — documented with the same emphasis the tool's own description gives it |
| `escalations` | 🔴 **Materially wrong troubleshooting advice.** The skill's opening paragraph told a reader that a `403` here is a scope problem fixed by a new key. `list_escalations` also needs the `bp_escalation.view` **role ability** (a `get_reports_summary`/`admin.access`-shaped trap this skill did not carry), and both escalated-ticket writes need `bp_escalation.reply` — for either, a new key changes nothing | ✅ fixed — the two-gate pattern is now stated up front, the same way `reports/SKILL.md` states it for its own tools |
| `escalations`, `tickets` | The scope-asymmetry table for `comment_on_ticket` / `add_private_note` on an escalated ticket named only `escalation:write`; the actual requirement is `escalation:write` **+ `escalation:read`**, plus the `bp_escalation.reply` ability | ✅ fixed in both skills |
| `escalations` | `escalate_ticket` only works on a ticket assigned to the caller; `de_escalate_ticket` — asymmetrically — can act on **any** escalated ticket the caller can read. Neither half of that asymmetry was stated | ✅ fixed |
| `escalations`, `tickets` | `comment_on_ticket`'s and `add_private_note`'s `comment.id` can be `null` on a 201 — a signature (or an empty note) is silently discarded and nothing is filed, but the call still reports success | ✅ fixed in both skills |
| `escalations` | `add_private_note` on an escalated ticket the caller cannot reach answers **404**, indistinguishable from a nonexistent id, rather than a scope refusal — not documented | ✅ fixed |
| `tickets` | `list_tickets` / `list_tickets_by_category` take a `scope="all"` argument (every ticket in the account, gated on the `ticket_all.view` role ability) that the skill never mentioned at all | ✅ fixed |
| `tickets` | `create_ticket`'s `requester` accepts an existing-contact `{"id": …}` form; the skill documented only the find-or-create `{"email", "name"}` form | ✅ fixed |
| `tickets`, `escalations` | `get_ticket_attachment`'s `max_dimension` argument, the `downscaled` field, and the byte-size-is-not-fidelity warning were present in `escalations/SKILL.md` but missing from `tickets/SKILL.md` — an asymmetry between two skills describing the same tool | ✅ fixed — both now carry the same content |
| `tickets`, `escalations` | `close_ticket`'s satisfaction-survey email is gated by an installation setting (`EBTEQDESK_RATING_EMAIL_ENABLED`) neither this client nor either skill can observe; neither skill said the client cannot see it | ✅ fixed in both skills |

🔴 **Still outstanding, and owned by neither pass:** the nine agent-provisioning
tools (`list_agents`, `get_agent`, `list_roles`, `list_groups`, `list_api_keys`,
`create_agent`, `update_agent`, `issue_api_key`, `revoke_api_key`) remain
unmentioned by any of the four skills — they don't belong to any of the four
skills' existing subject matter, so fixing a skill could not have picked them
up. See [Agent provisioning: nine tools, no skill](#agent-provisioning-nine-tools-no-skill)
below for the assessment.

No tool that existed at the version a skill's own line names has been renamed
or removed, or changed the meaning of an argument a caller from that version
would pass — the ordinary minor/major discipline `mcp-server/README.md`
describes. What changed between the two passes was not the tool surface; it
was how thoroughly each skill had been read against it.

## Agent provisioning: nine tools, no skill

`list_agents`, `get_agent`, `list_roles`, `list_groups`, `list_api_keys`
(reads) and `create_agent`, `update_agent`, `issue_api_key`, `revoke_api_key`
(writes) manage the account roster itself — who can sign in, what role they
hold, and what bearer credentials exist. They landed in server `4.2.0` and
have never been covered by any skill in this plugin.

**Blast radius is the whole reason this needs a decision before it needs
code.** Every other skill's worst-case write touches the desk — a ticket, a
comment, a category. These nine touch **who may act on the desk at all**:
`create_agent` mints a real sign-in with a password returned exactly once,
`issue_api_key` hands out a bearer credential that acts as another account,
and `update_agent`'s `role_id` can silently narrow every key an agent already
holds, with no notification to whatever integration was using one. Two of the
nine hand back a secret (`generatedPassword`, `plainTextToken`) that can never
be read again. Both `admin:read` and `admin:write` — the scopes gating all
nine — can only be minted by a signed-in human at Settings → API keys; no
existing key gained them automatically, including a legacy `*` key.

**A model using these safely would need, at minimum:** to distinguish
`scopes` from `effectiveScopes`; to read `meta.issuableScopes` before ever
calling `issue_api_key`, rather than discover the refusal; to know
`assignable: false` roles are refused by both create and update; to hand a
generated password or a plaintext token to the user in its very next message,
because neither is retrievable afterwards; to know there is no delete-agent
and no email/password-reset tool, so several mistakes are a human's job in the
web UI; and to treat every one of the four writes as unretryable on a timeout,
the same as everywhere else on this server.

**Whether that is a fifth skill or a section of an existing one is a product
decision, not made here.** It does not fit naturally inside `tickets`,
`escalations`, `reports` or `knowledge-base` — it shares no tool and no
workflow with any of them — which argues for a fifth skill on scope grounds.
Against that: it is the single highest-blast-radius surface in the whole
server, is used far less often than the other four, and a skill that ships
unverified on day one is worse than no skill, per the coordinator's brief. No
skill has been added. If the decision is to proceed, the work is the same
shape as this pass: walk each of the nine tools' descriptions and the
`instructions` block's provisioning section, not just their signatures.
