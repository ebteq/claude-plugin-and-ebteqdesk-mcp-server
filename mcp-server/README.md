# ebteqdesk-mcp

An [MCP](https://modelcontextprotocol.io) server that gives Claude Code — or any
MCP client — access to a **Ebteqdesk** install over its REST API: your assigned
tickets and their full conversations, the shared escalation queue, the reports,
and the knowledge base — its articles *and* its category/folder structure — to
read; and creating tickets, replying, escalating, moving a ticket between
working states, closing, proposing knowledge base articles and building the
categories and folders they are filed into to write. Since 4.2.0 it also
provisions the desk itself: creating helpdesk agents, setting their role and
groups, and issuing them their own scoped API keys.

It is a **client**. It holds no database connection, imports no Laravel code,
and does nothing but HTTP against `/api/v1`. It talks to an Ebteqdesk you already
run; it is not an Ebteqdesk.

> ⚠️ **Ebteqdesk is an INTERNAL desk, and that describes who files tickets — not
> who can receive an email.** Tickets are raised by staff inside the
> organisation, so a ticket's `requester` is a colleague rather than an outside
> customer, and no inbound reply from the public is expected. Mail still leaves
> on three live paths: every public reply is emailed to the requester's address
> (no flag anywhere suppresses it), resolving a ticket as SOLVED can email that
> address a satisfaction survey, and the requester's own portal link accepts
> replies without a login. The knowledge base is a separate surface again — its
> `public` articles are served to signed-out visitors.

> ⚠️ **Twenty-one of the forty-two tools write to a live helpdesk.** They send
> email to real requesters, notifications to real agents, drafts into a human's
> review queue, and new categories, folders and orderings into the knowledge
> base, and **nothing they do can be undone through this API** — there is no
> delete-ticket, no delete-comment, no delete-note and no delete-article
> endpoint.
>
> 🔴 **Two of the twenty-one destroy structure.** `delete_kb_category` and
> `delete_kb_folder` remove a real row from the knowledge base and **there is no
> undo, no trash and no restore**. Both are *refusals* rather than cascades —
> a category still holding folders, or a folder still holding articles, is
> refused with the count named and nothing is deleted — which is what keeps them
> from becoming the article delete this API deliberately does not offer.
> There is no dry-run mode; see
> [Write tools](#write-tools) for why one would be a lie rather than a safeguard.
> If you want a read-only deployment, mint a key without `ticket:write`,
> `escalation:write`, `kb:write` and `admin:write`: the twenty-one write tools
> still appear in the tool list, and every one of them refuses.
>
> **No tool mails the requester by default.** `comment_on_ticket` always does —
> that is what it is for — and `close_ticket` and `create_ticket` do only when
> asked for `status: 4`. The ten knowledge base writes mail nobody, and neither
> does `set_ticket_status`, which is the only ticket write that contacts no one
> at all. See
> [Which tools can email the requester](#which-tools-can-email-the-requester).

> 🔴 **Four of the twenty-one do not touch tickets or articles at all — they
> decide who may act.** `create_agent` creates a real account that can sign in,
> `update_agent` changes what role a person holds, `issue_api_key` hands another
> account a working bearer credential, and `revoke_api_key` takes one away
> immediately and permanently. They are gated on `admin:write`, which resolves
> only for an account whose role holds `admin.access`.
>
> 🔴 **Two of them return a secret exactly once.** `create_agent`'s
> `generatedPassword` and `issue_api_key`'s `plainTextToken` exist in that one
> response and nowhere else — the desk stores a one-way hash of each, no mail is
> sent, and neither this API nor the Ebteqdesk UI can show either again. Lose it
> and the account or the key has to be reset by a human.
>
> 🔴 **`issue_api_key` can never grant `admin:read` or `admin:write`** — not to
> any agent, by any caller, however privileged. A provisioning key cannot mint a
> successor to itself, so the only supply is a signed-in human at
> **Settings → API keys**. And it **refuses rather than narrowing**: one scope
> the cap will not issue makes the whole call a 422 that creates nothing.
>
> 🔴 **And no tool here can create or promote an administrator.** `create_agent`
> and `update_agent` both refuse a role granting `admin.access` with a `422`;
> `list_roles` marks those rows `assignable: false`. Such an account would sign
> in with the password `create_agent` returns and mint its own provisioning key
> from the browser, so allowing it would let an API key create its own
> successor. Promoting somebody is a signed-in administrator's act at
> **Settings → Agents**.
>
> ⚠️ **There is no delete-agent tool, no password reset and no email change.**
> All three stay in the Ebteqdesk web UI. Deleting an agent reassigns or clears
> tickets, comments, notes and performance rows across the whole desk; a
> password reset ends live sessions and reveals its value once on a screen.

> 🔴 **`get_ticket` returns internal notes.** It is the only tool that returns
> what was actually *said* on a ticket, and the conversation it returns includes
> `kind: "note"` entries — **private, staff-only** notes that must never be
> repeated into a public reply, which Ebteqdesk mails out. On an escalated
> ticket that is where most of the real
> content is, because Ebteqdesk silently downgrades an agent reply there into a
> note. Mint without `ticket:read` if that is not acceptable for your
> deployment.

Transport is **stdio** — your MCP client launches `ebteqdesk-mcp` as a
subprocess and speaks JSON-RPC over its stdin/stdout.

---

## Requirements

- **Python 3.10 or newer.** That is the floor of the official `mcp` SDK, so it
  cannot be lowered; nothing here needs anything newer, so it is not raised.
- An Ebteqdesk install reachable over HTTP, and a personal access token for it.

Three runtime dependencies, all resolved for you by `pip`. The ranges are
deliberately ranges rather than exact pins — this is a tool people install
alongside other tools, and an exact pin turns any co-installed package into a
resolver conflict. The right-hand column is what this release was actually
verified against, end to end, against a live Ebteqdesk:

| Package | Declared | Verified against |
|---|---|---|
| `mcp` | `>=2.0,<3` | 2.0.0 |
| `httpx2` | `>=2.5,<3` | 2.10.0 |
| `pydantic` | `>=2.7,<3` | 2.13.4 |
| Python | `>=3.10` | 3.13 |

⚠️ `httpx2` is the **2.x line**, a different PyPI distribution from `httpx` 0.x.
Installing the wrong one gets you two HTTP stacks in one environment.

`ebteqdesk-mcp` itself is versioned with semver, and the version means
something: **the major moves when a tool's observable contract does.** This is
`4.2.0`; `serverInfo.version` in the MCP handshake reports the same string, so a
host can tell a renamed tool argument from an outage.

**4.2.0 adds nine tools — agent provisioning — and moves nothing else.** Every
tool that existed at 4.1.0 keeps the same name, arguments, defaults and return
shape, so an integration that never calls the new nine cannot tell the two
releases apart on the wire. The list goes thirty-three → forty-two: five reads
(`list_agents`, `get_agent`, `list_roles`, `list_groups`, `list_api_keys`) and
four writes (`create_agent`, `update_agent`, `issue_api_key`, `revoke_api_key`).

🔴 **They need two scopes no existing key can have — including a wildcard one.**
`admin:read` and `admin:write` resolve only for an Administrator-role account,
and they are the only scopes on this server that cannot be obtained through the
API: `issue_api_key` refuses them unconditionally, so a provisioning key is
minted by a signed-in human at **Settings → API keys** and nowhere else.

⚠️ **A legacy `abilities = ['*']` key does NOT gain them.** The wildcard means
"everything your role backs" and, as of 4.2.0, *except agent provisioning* —
because those keys were minted before this surface existed and their holders
consented to nothing. Adding an area must never widen a credential that already
exists. Every other scope the wildcard covers is unchanged, so nothing an
existing `*` key could do stops working.

Scopes are fixed at mint time, so every key issued before 4.2.0 lists the nine
tools and is refused all nine. That is the one thing to do before upgrading
expectations.

🔴 **4.0.0 turns one tool's error into a success, and adds no tool.** The list is
the same thirty-three, with the same arguments and the same defaults.
`update_kb_article` against a **published** article used to fail with `409`
(`ConflictError`, reaching a host as an error result); it now succeeds with
`202`, staging a pending revision, and the response grows a top-level
`revision` key. `get_kb_article_review` grows the same key, always present and
null when nothing is staged — that half is purely additive.

⚠️ **The new success is quieter than the old failure, which is the reason to
read the update tool's description before upgrading.** A `202` hands back the
**live, unchanged** article under `data`, so a caller that reads its own edit
back finds the old text and can reasonably conclude the call did nothing. It
did not — the edit is in a human's queue. See
[the knowledge base write surface](#the-knowledge-base-write-surface-propose-never-publish).

**2.1.0 adds one tool and moves nothing else.** `list_kb_proposals` takes the
server to thirty-three; the write half stays at seventeen, because the new tool
reads. Every tool that existed in 2.0.0 keeps the same arguments, the same
defaults and the same return shape, `close_ticket` included.

🔴 **3.0.0 removes every `warnidesk` compatibility shim, and adds no tool.** The
list is the same thirty-three as 2.1.0, with the same arguments, the same
defaults and the same return shapes. What broke is what 2.0.0 promised was
temporary: `WARNIDESK_*` environment variables are **no longer read**, and the
`warnidesk-mcp` console script is **no longer installed**. Both failures are
quiet and neither names the rename — read
[Upgrading from `warnidesk-mcp`](#upgrading-from-warnidesk-mcp) **before**
installing, not after something stops working.

⚠️ **2.0.0 was the Ebteqdesk rename, and it added no tool either.** The tool list
was unchanged at thirty-two. What moved is the package's other public surface —
the distribution name, the import module, the console script and the environment
variables — which is why it was a major rather than a minor. 3.0.0 is the
follow-through: the same rename, with the fallbacks taken away.

## Install

**One channel: git over HTTPS, from the `mcp-server/` subdirectory of this
repository.** The desk-hosted download that used to be the primary install has
been retired — see
[Where this package is distributed](#where-this-package-is-distributed-and-why).

```bash
pip install "git+https://github.com/ebteq/claude-plugin-and-ebteqdesk-mcp-server#subdirectory=mcp-server"
```

⚠️ **This repository is private.** Installing needs `git` on the machine and a
GitHub account with access to it. If you are not on the Ebteqdesk team this
package is readable but not installable, and there is no longer a second channel
that avoids that — the public download from the desk is gone.

No ref is named, so this takes the repository's default branch, `main`.

Prefer an isolated install so the server's dependencies never collide with a
project's:

```bash
uv tool install "git+https://github.com/ebteq/claude-plugin-and-ebteqdesk-mcp-server#subdirectory=mcp-server"
# or
pipx install "git+https://github.com/ebteq/claude-plugin-and-ebteqdesk-mcp-server#subdirectory=mcp-server"
```

⚠️ **`#subdirectory=mcp-server` is load-bearing.** The repository root is a
Claude Code plugin marketplace, not a Python package. Drop the fragment and
`pip` clones the repo, finds no `pyproject.toml` at the top level, and fails
before it has read a line of this package.

### Reproducible installs — pin a ref

```bash
pip install "git+https://github.com/ebteq/claude-plugin-and-ebteqdesk-mcp-server@<commit-sha>#subdirectory=mcp-server"
```

🔴 **A ref-less URL is version-blind, and pinning is the answer to that.**
Naming no ref — or naming `@main` — installs whatever the default branch points
at right now, so the same command run on two machines a month apart installs two
different builds and says nothing about it. That is the right default for a
person following the setup guide and the wrong one for a container image, a
shared machine, or a `requirements.txt`. Pin there.

⚠️ **This repository carries no release tags yet**, so a commit SHA is what
there is to pin today. A `@v4.2.0`-style pin does not resolve and fails at
clone time; it starts working the day the first tag is pushed.

### Upgrading

```bash
pip install "git+https://github.com/ebteq/claude-plugin-and-ebteqdesk-mcp-server#subdirectory=mcp-server"
```

A **version bump is picked up with no flag at all** — pip clones the ref, builds
the metadata, reads the version out of it, sees it is newer than the installed
one and replaces the install. `--upgrade` is not the missing ingredient; do not
reach for it. What pip does not notice is a **rebuild at the same version
number**: it finds `ebteqdesk-mcp==<same>` already satisfied and installs
nothing.

🔴 **On this channel that same-version case is the common one, not the edge
case.** There is no versioned archive URL any more. A branch ref is a moving
target: every merge into `main` changes what the URL resolves to while the URL
itself, and the version number until the next release, both stay exactly as they
were. So re-running the command above is silently a no-op far more often than it
used to be. Force it whenever you are picking up work that did not bump the
version:

```bash
pip install --force-reinstall "git+https://github.com/ebteq/claude-plugin-and-ebteqdesk-mcp-server#subdirectory=mcp-server"
uv tool install --force "git+https://github.com/ebteq/claude-plugin-and-ebteqdesk-mcp-server#subdirectory=mcp-server"
pipx install --force "git+https://github.com/ebteq/claude-plugin-and-ebteqdesk-mcp-server#subdirectory=mcp-server"
```

`pipx upgrade ebteqdesk-mcp` re-runs the stored spec and shares the same
same-version blind spot; `pipx install --force` is the one that always rebuilds.

### After installing

You end up with an `ebteqdesk-mcp` executable on your `PATH`. Note where it is —
you need the absolute path for the registration command below if it is not on the
`PATH` your MCP client inherits:

```bash
which ebteqdesk-mcp
```

### Where this package is distributed, and why

**Decision: git over HTTPS from this repository's `mcp-server/` subdirectory.
Not PyPI, not an internal package index, and — since this package moved here —
no longer a download from the desk.**

| Channel | Verdict |
|---|---|
| **git+https from this repository** ✅ | The install. No new infrastructure, no credentials to distribute, no publish step to forget; `pip`, `pipx` and `uv tool` all consume it directly. It is also the channel that keeps the server and the Claude Code plugin describing it in one artefact, now that both live here. The cost is real, and it is now the cost every installer pays rather than one they could opt out of: no `pip index versions`, a same-version rebuild needs `--force-reinstall`, `git` has to be on the machine, and **it needs a GitHub account with access to a private repository**. |
| **Internal package index** | Still not worth it. The case is narrow: something needing to resolve `ebteqdesk-mcp` as a **transitive dependency** of another internal package. That is not true today, and a private index is a service with credentials, uptime and a publish pipeline attached. The one thing that would reopen it is the private-repo requirement above turning into a real obstacle for installers. |
| **PyPI** | Not appropriate. This package is useless without an Ebteqdesk install and an API token for it, so a public release would be a global namespace claim on a name with no public audience, plus a permanent obligation not to yank. Revisit only if Ebteqdesk itself becomes a public product. |

🔴 **The desk-hosted channel is retired, and it is not coming back.**
`https://<desk>/mcp/ebteqdesk-mcp.tar.gz` packed an archive at request time out
of the desk's own deployed tree, which made it the one channel where the client
and the API it mirrors could not drift apart. That stopped being possible when
this package moved out of the Ebteqdesk repository: the desk has no
`clients/python/` left to pack, and the `/mcp` routes and landing page were
removed in the same change. A command still pointing at that URL fails at
download — reinstall from git.

The drift the desk channel used to make impossible is now yours to manage. This
repository and the Ebteqdesk application are separate repositories with separate
release cadences, so a client built from this repository's tip can be ahead of
the desk it is pointed at (tools that desk does not answer) or behind it (desk
endpoints no tool calls). Either way it surfaces later as an unexplained
tool-not-found, far from the install that caused it. When that matters, pin the
client to the commit whose tool list matches the desk it is talking to — the
desk's **Settings → API keys** page prints the install command with that ref
already filled in.

**The version is single-sourced** in `src/ebteqdesk_mcp/_version.py` and read
from there by the build backend, so publishing to an index later needs no change
to how the version is declared.

## Upgrading from `warnidesk-mcp`

This package was called **`warnidesk-mcp`** through 1.6.0. 2.0.0 renamed the
distribution, the import module, the console script and the environment
variables to **`ebteqdesk-mcp`** / `EBTEQDESK_*`, keeping fallbacks for the last
two. **3.0.0 removed those fallbacks.** If you have never installed
`warnidesk-mcp`, skip this section.

🔴 **Read this before upgrading, because neither failure names the rename.**

| If you skip | What you see |
|---|---|
| Step 1 or 2 (`"command": "warnidesk-mcp"`) | `command not found` — your host reports a server that **crashed at startup** |
| Step 3 (`WARNIDESK_*` still set) | The server **starts and connects**, then every tool call fails as unconfigured |

The second is the dangerous one: a healthy handshake followed by uniform
failures looks like a broken server, not a stale config file.

### 1. Uninstall the old package first

```bash
uv tool uninstall warnidesk-mcp
uv tool install "git+https://github.com/ebteq/claude-plugin-and-ebteqdesk-mcp-server@main#subdirectory=mcp-server"
which ebteqdesk-mcp && ebteqdesk-mcp --version    # expect 4.0.0 or newer
```

There is only one channel to reinstall from now — git — and it is the same
command as [§ Install](#install). `uv tool install` needs no `--from` here: the
distribution and the console script are both `ebteqdesk-mcp`, so uv reads the
name out of the built metadata and exposes the right executable. (`uvx` is the
one that does need `--from`, because it has to be told which script to *run*;
see [the self-installing registration](#alternative-a-self-installing-registration).)

⚠️ **The uninstall is still not optional, for a changed reason.** Through 2.x it
was because both distributions claimed a `warnidesk-mcp` console script and `uv
tool install` refuses to overwrite a script another distribution owns. 3.0.0
ships no such script, so the collision is gone — but the old package's script
is not, and it stays on `PATH` until the old package is removed. Leave it there
and a config still pointing at `warnidesk-mcp` keeps launching **1.6.0**, at
which point you are debugging a version you thought you had upgraded past.
`ebteqdesk-mcp --version` is how you confirm which one you have.

`pipx` users: `pipx uninstall warnidesk-mcp` then `pipx install ...`. `pip`
users: `pip uninstall warnidesk-mcp` then `pip install ...`.

### 2. Point your MCP host at the new command

In `~/.claude.json` (or whatever config your host uses), the **`"command"` must
change** — there is no longer an alias covering the old one:

```json
{
  "mcpServers": {
    "ebteqdesk": {
      "command": "ebteqdesk-mcp",
      "env": {
        "EBTEQDESK_BASE_URL": "https://help.example.com",
        "EBTEQDESK_API_TOKEN": "6|your-token-here"
      }
    }
  }
}
```

**The key became `ebteqdesk` in 3.0.0, and unlike everything else here that
change is optional.** The key is yours, not the server's: it lives in your
config, and it is what prefixes the tool names a model sees. An install left on
`warnidesk` keeps working, and the Claude Code plugin's skills accept either
`mcp__ebteqdesk__*` or `mcp__warnidesk__*` for one release. Rename it while you
are in the file anyway — the plugin drops the old prefix next release. See
[Register with Claude Code](#register-with-claude-code).

Restart your MCP client afterwards — a changed `"command"` reaches only a newly
started session.

### 3. Rename the env keys — this one is required

🔴 **`WARNIDESK_API_TOKEN`, `WARNIDESK_BASE_URL` and `WARNIDESK_TIMEOUT` are no
longer read.** Through 2.x they fell back with a stderr notice; 3.0.0 removed
the fallback. A config file you do not touch produces a server that starts,
connects, and then refuses every call as unconfigured.

**Token values are unchanged.** Nothing was reissued and nothing needs
reminting; the same string works under the new variable name.

| Old — no longer read | New |
|---|---|
| `WARNIDESK_API_TOKEN` | `EBTEQDESK_API_TOKEN` |
| `WARNIDESK_BASE_URL` | `EBTEQDESK_BASE_URL` |
| `WARNIDESK_TIMEOUT` | `EBTEQDESK_TIMEOUT` |

`EBTEQDESK_TIMEOUT` is optional, so a stale `WARNIDESK_TIMEOUT` fails silently
in the other direction: you get the 30-second default back rather than an error.

### 4. If you import the package as a library

There is **no fallback** here and never was — the module and the exported class
names moved in 2.0.0:

| Old | New |
|---|---|
| `import warnidesk_mcp` | `import ebteqdesk_mcp` |
| `WarnideskClient` | `EbteqdeskClient` |
| `WarnideskError` | `EbteqdeskError` |

### 5. Update the Claude Code plugin

The plugin renamed in its own 2.0.0. It is a separate *installation* — a plugin,
not a Python package — but no longer a separate repository: it ships from
[`../plugin/`](../plugin/), the same one this package installs from.

```bash
claude plugin uninstall warnidesk
claude plugin marketplace remove warnidesk
claude plugin marketplace add ebteq/claude-plugin-and-ebteqdesk-mcp-server
claude plugin install ebteqdesk
```

### What did not change

- The **tools you already call**: same names, same arguments, same defaults,
  same return shapes. `close_ticket` still defaults to `status: 5`. Neither
  rename added or removed one; `list_kb_proposals` arrived in 2.1.0, taking the
  list to thirty-three.
- Your **API tokens**, and the `/api/v1` endpoints behind them.
- The **server key**, if you choose not to touch it — see step 2.

## Get a token — with the right scopes

In Ebteqdesk: **Settings → API keys → create a key**. Copy it immediately; it is
shown once.

⚠️ **Tick every scope you intend to use**, or those tools will refuse:

| Scope | Unlocks | |
|---|---|---|
| `ticket:read` | `list_tickets`, `list_tickets_by_category`, `get_ticket`, `get_ticket_comments`, `get_ticket_attachment` | read |
| `escalation:read` | `list_escalations` — the **shared** queue — and `get_ticket` / `get_ticket_comments` / `get_ticket_attachment` / `add_private_note` **on any escalated ticket** | read |
| `kb:read` | `search_kb_articles`, `get_kb_article` | read |
| `reports:read` | `get_reports_summary` — **also needs the `admin.access` ability** | read |
| `escalation-reports:read` | `get_escalation_report` | read |
| `ticket:write` | `create_ticket`, `comment_on_ticket`, `add_private_note`, `set_ticket_status`, `close_ticket` | **write** |
| `escalation:write` | `escalate_ticket`, `de_escalate_ticket`, and `add_private_note` **on an escalated ticket** — internal only | **write** |
| `escalation:reply` | `comment_on_ticket` **on an escalated ticket**, and `close_ticket` **with a `body` on one** — **emails the requester's address** | **write** |
| `kb:write` | `propose_kb_article`, `update_kb_article`, `create_kb_category`, `update_kb_category`, `create_kb_folder`, `update_kb_folder`, and the three reads `get_kb_article_review`, `list_kb_proposals` and `list_kb_tree` | **write** |
| `admin:read` | `list_agents`, `get_agent`, `list_roles`, `list_groups`, `list_api_keys` — the account roster. **Also needs the `admin.access` ability**, i.e. an Administrator-role account. 🔴 **Never covered by a legacy `*` key** | read |
| `admin:write` | `create_agent`, `update_agent`, `issue_api_key`, `revoke_api_key` — creates accounts and hands out credentials. **Also needs `admin.access`**. 🔴 **Never covered by a legacy `*` key** | **write** |

⚠️ **`escalation:read` and `escalation-reports:read` are different scopes** and
one character apart in the picker. The first reads the escalation **queue** —
actual tickets, including other people's. The second reads only per-category
**counts**. An analytics integration should hold the second and not the first.

⚠️ **`escalation:read` grants visibility beyond your own tickets.** It is the
only read scope on this API that does — `list_escalations` returns every
unresolved escalated ticket in the installation, whoever it is assigned to.
Tick it when you want that; leave it off when you do not.

Only `whoami` works without any scope. A key minted with none is the most common
bad first run: the server connects, all forty-two tools appear, and forty-one
of the forty-two then refuse. **Scopes are fixed when a key is minted and cannot be added
to an existing key** — getting this wrong means minting a second one.

**Ticking the four write scopes is a decision, not a formality.** They are what
let an agent email your requesters, file drafts into a reviewer's queue, and —
with `admin:write` — create accounts and hand out credentials. Omit them and you
have a read-only deployment that still lists all forty-two tools and refuses the
twenty-one writes.

🔴 **`admin:read` and `admin:write` are the two scopes to think hardest about,
and the two an existing key cannot have.** They gate the account roster and the
provisioning writes, they resolve only for an Administrator-role account, and
they are the only scopes on this server that **cannot be obtained through the
API at all** — `issue_api_key` subtracts them unconditionally, so a key carrying
either is minted by a signed-in human at Settings → API keys and nowhere else.
Every key issued before 4.2.0 lists the nine provisioning tools and is refused
all nine.

⚠️ **`kb:write` also gates five reads.** `get_kb_article_review` and
`list_kb_proposals` change nothing, but their corpus is every article including
drafts — the same corpus `update_kb_article` addresses. `list_kb_tree`,
`list_kb_categories` and `list_kb_folders` change nothing either, but they are
the *authoring* structure: ids, and `agents`-only folders. `kb:read` gates the
*public* help corpus and is deliberately the one scope with no role requirement
behind it, so mounting any of those reads on it would widen that corpus.
Twenty-six tools need a write scope and twenty-one of them actually write.

🔴 **`list_kb_proposals` is installation-wide, like `list_escalations`.** It
lists every article carrying a review state in the whole install — another
integration's proposals included — because an API key identifies an *account*
and not an agent, so the server has nothing to narrow it by. It is the only KB
read that *enumerates* drafts; every other one either serves the public corpus
or answers about a single article the caller named.

🔴 **`kb:write` needs `kb.manage`, which is administrator and supervisor only.**
Unlike `kb:read`, which has no role side at all, `kb:write` resolves only while
the key carries it **and** the account's role holds `kb.manage`. An agent- or
developer-role account therefore cannot use *any* knowledge base write tool, or
`list_kb_tree`, no matter what key is minted for it — and minting another one
cannot help. That is the likeliest surprise on this surface; `whoami` tells you
which half is missing.

Note the two write rows: `comment_on_ticket` and `add_private_note` each take
**either** `ticket:write` **or** `escalation:write`, and the ticket decides which
one it costs — a non-escalated ticket of your own costs `ticket:write`, an
escalated one costs `escalation:write` (plus `escalation:read` to reach it).
Neither substitutes for the other, so a key holding only `escalation:write`
cannot write to an ordinary ticket even one assigned to it, and a key holding
only `ticket:write` cannot write to an escalated one. Every ticket payload
carries an `escalated` boolean, so that requirement **is** checkable in advance —
read it before replying. See
[Troubleshooting](#this-ticket-is-escalated-and-replying-to-an-escalated-ticket-needs-the-escalationwrite-scope).

🔴 **`escalation:write` and `escalation:reply` are two scopes, not one.** `write`
files an **internal note** on an escalated ticket and hands the ticket back;
`reply` sends a message the **requester receives by email**. They are backed by
the same role ability and split only on the key, so an account can be given the
first without the second — and normally is. Escalation is a **handoff of the
requester relationship**: the assigned agent stays involved internally but stops
speaking to the requester, and whoever is working the escalation takes over
until de-escalation.

So a refusal naming `escalation:reply` usually means *"note it instead, or
de-escalate"*, not *"mint a new key"*. Minting one with `escalation:write` is the
common wrong next move and hits the identical wall.

⚠️ **De-escalating costs `bp_escalation.reply`, not `ticket.reply`**, and works
on any escalated ticket you can read. **Escalating** costs `ticket.reply` and
only works on your own. Escalating creates work for other people and notifies
every Assistant on the install; de-escalating closes out work you were already
doing.

🔴 **`escalated` is not a status and does not clear when a ticket is resolved.**
It stays true until somebody de-escalates, so an agent's own ticket that was
escalated keeps costing the escalation scopes to write on — after it is solved,
after it is closed, and after it is reopened.

That is **not a lockout**. An **Agent**-role account holds `escalation:read`,
`escalation:write` and `escalation-reports:read`, so it goes on reading its
escalated ticket, reading the internal notes on it, filing notes of its own, and
it can call `de_escalate_ticket` to take the ticket back. The one scope it does
**not** hold is `escalation:reply` — the requester-facing half — because
escalation hands the requester conversation to whoever is working the
escalation. So the only thing an agent loses on its own escalated ticket is the
ability to email the requester's address, and de-escalating gives that back.

⚠️ **`list_escalations` is not the same set as "escalated".** The queue is a
**work list** and drops a ticket once it is resolved; the escalation **state**
lasts until de-escalation. A ticket can be absent from `list_escalations` and
still cost the escalation scopes on `get_ticket`, `comment_on_ticket` and
`add_private_note`. Read `escalated` on the ticket, never the queue's
membership.

A scope also needs the account's *role* to grant the ability behind it. If your
role is narrower than the scopes you tick, the key will still be refused for
those — see [Troubleshooting](#this-api-key-was-not-minted-with-the-kbread-scope-403).

On a local development stack you can mint one from the container:

```bash
# Read-only, own tickets only — no escalation:read, so no shared queue.
./docker/dev artisan tinker --execute='
  use App\Auth\ApiScope;
  echo App\User::where("email","admin@ebteq.desk")->first()->issueApiKey("mcp", [
      ApiScope::TICKET_READ, ApiScope::KB_READ, ApiScope::ESCALATION_REPORTS_READ,
  ])->plainTextToken;
'

# Everything. Reads the shared escalation queue AND can email your requesters.
./docker/dev artisan tinker --execute='
  use App\Auth\ApiScope;
  echo App\User::where("email","admin@ebteq.desk")->first()->issueApiKey("mcp-rw", [
      ApiScope::TICKET_READ, ApiScope::ESCALATION_READ, ApiScope::KB_READ,
      ApiScope::ESCALATION_REPORTS_READ,
      ApiScope::TICKET_WRITE, ApiScope::ESCALATION_WRITE,
  ])->plainTextToken;
'
```

A token looks like `6|GcTeinYL8u3...`. **Quote it** whenever it goes near a
shell — the `|` is a pipe, and an unquoted token is silently truncated at it,
which then shows up as an unexplained 401.

## Configure

Three environment variables. There is no config file and there are no CLI flags
(a `--token` flag would put your credential in the process table, where any
other user on the machine can read it with `ps`).

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `EBTEQDESK_API_TOKEN` | yes | — | Personal access token. Sent as `Authorization: Bearer …`. |
| `EBTEQDESK_BASE_URL` | yes | — | The **site root**, e.g. `http://localhost:8086` or `https://help.example.com`. Not an `/api/v1` path. |
| `EBTEQDESK_TIMEOUT` | no | `30` | Per-request timeout in seconds. |

🔴 **The pre-rename `WARNIDESK_*` names are no longer read.** They were accepted
with a stderr deprecation notice through 2.x; 3.0.0 removed the fallback.

The resulting failure is quiet, so it is worth knowing its shape in advance: a
missing variable is not fatal at startup, only when it is read. The server
launches, completes the handshake and reports as connected, and then **every
tool call fails as unconfigured**. That presents as a broken server rather than
a stale config file, which is why the "is not set" errors name the old variable
explicitly — that text is the only breadcrumb pointing at the rename. See
[Upgrading from `warnidesk-mcp`](#upgrading-from-warnidesk-mcp).

`EBTEQDESK_BASE_URL` has no default on purpose: a default host would turn a
configuration mistake into a connection error against somebody else's server,
with nothing to tell the two apart. If you paste a URL ending in `/api/v1` or
`/api`, that suffix is trimmed — every path this client builds already starts
with `/api/v1`, and the doubled prefix would otherwise 404 with no explanation.

**The token is never logged, never echoed in an error and never put in a URL.**
It reaches the wire only as a request header. Errors identify a token by an
8-character SHA-256 fingerprint when they need to identify one at all.

## Register with Claude Code

```bash
claude mcp add ebteqdesk \
  --env EBTEQDESK_BASE_URL=http://localhost:8086 \
  --env EBTEQDESK_API_TOKEN='6|your-token-here' \
  -- ebteqdesk-mcp
```

> ⚠️ **`claude mcp add <key> -- <command>` takes two names and they are not the
> same string.** `ebteqdesk-mcp` is the **executable**; `ebteqdesk` is the
> **key** this server is registered under, and the key is what prefixes every
> tool name a model sees — `mcp__ebteqdesk__list_tickets`,
> `mcp__ebteqdesk__close_ticket`, and forty more.
>
> **The key was `warnidesk` through 2.x.** It stayed behind during the rename
> because those prefixed names are called from the Claude Code plugin, which at
> the time lived in a different repository — so moving the key needed two
> repositories to ship together, and that coordination was the whole reason to
> defer it. It landed: the plugin renamed to `ebteqdesk` in its own 2.0.0, and
> the desk's **Settings → API keys** page prints `claude plugin install
> ebteqdesk`. **That coordination problem no longer exists at all** — the plugin
> now lives in this repository, at [`../plugin/`](../plugin/), so the server and
> the skills that call it move in one pull request.
>
> 🔴 **The key is yours, not the server's.** It is whatever you type here, the
> server cannot see it, and registering under a different one produces no error
> — just skills calling tools that, under your prefix, do not exist. An install
> created before 3.0.0 is still on `warnidesk` and still works; the plugin's
> skills accept either prefix for one release and drop `warnidesk` in the next.
> New installs should use `ebteqdesk`.

Add `--scope user` to make it available in every project rather than just the
current one. If `ebteqdesk-mcp` is not on the `PATH` Claude Code inherits, use
its absolute path (`which ebteqdesk-mcp`) in place of the bare name.

Verify:

```bash
claude mcp list          # -> ebteqdesk: ebteqdesk-mcp - ✓ Connected
```

and inside Claude Code, `/mcp` lists the server and its forty-two tools.

> 🔴 **The tools do not appear in the session you registered the server from.
> Restart the client.**
>
> `claude mcp add` writes configuration; MCP servers are launched and their
> tool lists fetched when a session **starts**. So the session you were in when
> you ran the command keeps the tool list it booted with — `/mcp` shows nothing,
> the forty-two tools are unavailable, and everything looks broken while the
> configuration is in fact correct.
>
> Quit Claude Code and start it again. `claude mcp list` works from any shell
> and is the quick way to confirm the registration landed **before** you
> restart: it launches the server itself rather than reading a running session's
> state. The same applies after `--env` changes and after upgrading the package
> — a new token or a new version reaches only a newly started session.

To remove it:

```bash
claude mcp remove ebteqdesk
```

<details>
<summary>Registering with a client that uses a JSON config file</summary>

```json
{
  "mcpServers": {
    "ebteqdesk": {
      "command": "ebteqdesk-mcp",
      "env": {
        "EBTEQDESK_BASE_URL": "http://localhost:8086",
        "EBTEQDESK_API_TOKEN": "6|your-token-here"
      }
    }
  }
}
```

The key (`ebteqdesk`) and the command (`ebteqdesk-mcp`) are different strings on
purpose — see the note above. Upgrading from `warnidesk-mcp` means changing the
`"command"` for certain, and the key only if you want to.

</details>

### Alternative: a self-installing registration

🔴 **The command above registers a command; it does not install one.** On a
machine where `ebteqdesk-mcp` was never installed, that bare name does not
resolve and Claude Code reports the server as **failed at startup** — a
registration that looks correct in `~/.claude.json` and a server that never
connects. The two-step above (install, *then* register) is still the primary
route precisely because its failure is the obvious one: `pip install` either
worked or told you why.

If you would rather have one step, `uvx` can fetch and run the package itself:

```bash
claude mcp add ebteqdesk \
  --env EBTEQDESK_BASE_URL=https://help.example.com \
  --env EBTEQDESK_API_TOKEN='6|your-token-here' \
  -- uvx --from "git+https://github.com/ebteq/claude-plugin-and-ebteqdesk-mcp-server@main#subdirectory=mcp-server" ebteqdesk-mcp
```

Nothing needs to be installed first: on the agent's first start `uvx` clones the
repository, builds the package out of `mcp-server/`, resolves its dependencies
into a throwaway environment and runs the server out of it. `--from <spec>`
names what to install and the trailing `ebteqdesk-mcp` names the console script
to run out of it — they are the same two names as ever, and the `--` still
matters, now more than before, because everything after it is Claude Code's
payload rather than its own flags.

⚠️ **It carries the same private-repository requirement as every other install
here**, and at a worse moment: the git credentials have to be available to the
agent's own process at startup, not to a shell you were sitting in front of.

`claude mcp list` reports this one as `ebteqdesk: uvx --from … ebteqdesk-mcp -
✓ Connected` rather than the bare executable.

**Four things to know before you choose it.**

**1. It needs `uv` on the machine** (that is what provides `uvx`). If it is
missing, nothing complains at registration time — the failure arrives later, at
agent startup, as a server that will not connect, which is a much worse place to
learn it than a "command not found" from an install you ran deliberately.

**2. The agent's first start is slow.** That start is doing a build and a full
dependency resolution — 29 packages — before the server says a word. Later
starts reuse the environment and are fast. If the agent looks hung the first
time, it is not.

**3. 🔴 It inherits the version-blind ref, with a second cache in front of it.**
This is the same trap as [§ Upgrading](#upgrading) — `@main` always resolves to
whatever the default branch points at now, so a **rebuild at the same version
number** is invisible to anything caching by version — except that here it is
`uv`'s cache doing the caching, not `pip`'s resolver, and it holds on harder. A
branch that moved without a version bump keeps launching the old client
indefinitely, and nothing anywhere reports a problem. Registering a pinned
commit instead of `@main` trades this away for an install you have to bump by
hand.

Measured against `uv 0.11.8`, with a source change at an unchanged version
number:

| What was tried | Did it pick up the new build? |
|---|---|
| `uv cache clean ebteqdesk-mcp` | ❌ no — it removes package entries, not the environment `uvx` reuses |
| `uvx --refresh --from <url> …` | ❌ no |
| `uvx --refresh-package ebteqdesk-mcp --from <url> …` | ❌ no |
| **`uv cache prune`** | ✅ **yes** — and this is the one to use |
| `uv cache clean` (no package name) | ✅ yes, but it empties the whole cache |

The first three rows are there because they are what a reader reaches for first,
and all three fail. **The fix is `prune`:**

```bash
uv cache prune --force
```

`prune` evicts the ephemeral environment `uvx` had been reusing — measured, that
is ~30 MB and one entry under `environments-v2` — and leaves the rest of the
cache alone. `uv cache clean` with no argument also works and is listed for
completeness, but it discards **every** package `uv` has ever cached for
**every** project on the machine, which is routinely tens of gigabytes. Reach for
it only if `prune` somehow does not do it.

⚠️ `--force` is not optional here in practice. On a machine that is *running* any
uv-launched MCP server — which, if you registered this way, includes this one —
both `prune` and `clean` wait 300 seconds on the uv cache lock and then fail with
`Cache is currently in-use`. `--force` overrides that in-use check; quitting
every MCP client first works too, and is the same thing done the slow way.

⚠️ **Do not put `--refresh` inside the registered command** to avoid all this. It
would re-resolve the whole dependency set on *every* agent start, paying that
cost forever — and per the table above it does not even fix the staleness.

**4. 🔴 An already-installed copy silently wins.** If
`ebteqdesk-mcp` is already installed as a persistent uv tool — which it is if you
followed the primary route with `uv tool install` — then `uvx` **reuses that
environment and never fetches the URL at all**. uv says so in `uvx -v` output
(`Found existing environment for tool 'ebteqdesk-mcp'`), and the consequence is
that the `--from <spec>` in your registration is decoration: you keep launching
whatever is installed, and merging to `main` changes nothing. Check and clear it
with:

```bash
uv tool list                      # does it list ebteqdesk-mcp?
uv tool uninstall ebteqdesk-mcp   # if you meant to run from the git spec
```

Pick one route or the other. Running both is how you end up debugging a version
you are certain you upgraded past — the same failure the
[`warnidesk-mcp` upgrade notes](#1-uninstall-the-old-package-first) describe, for
the same reason.

---

## Tools

Forty-two tools: twenty-one that read, twenty-one that write.

The **Scope** column is the API key scope each tool needs; the **Ability**
column is the role permission the *account* needs on top of it. Those are two
separate gates with two different remedies — a missing scope is fixed by a new
key, a missing ability only by an administrator. See
[Troubleshooting](#a-403-naming-a-scope--and-why-there-are-two-of-them).

### Read tools

| Tool | Endpoint | Scope | Visibility |
|---|---|---|---|
| `whoami` | `GET /api/v1/user` | none | the token's account |
| `list_tickets` | `GET /api/v1/tickets` | `ticket:read` | assigned to you |
| `list_tickets_by_category` | `GET /api/v1/{category}` | `ticket:read` | assigned to you |
| `list_escalations` | `GET /api/v1/escalations` | `escalation:read` | 🔴 **the whole installation** |
| `get_ticket` | `GET /api/v1/tickets/{id}` | `ticket:read` **or** `escalation:read` | assigned to you, **or escalated** |
| `get_ticket_comments` | `GET /api/v1/tickets/{id}/comments` | `ticket:read` **or** `escalation:read` | same as `get_ticket` |
| `get_ticket_attachment` | `GET /api/v1/attachments/{id}` | `ticket:read` **or** `escalation:read` | via the file's parent ticket |
| `get_escalation_report` | `GET /api/v1/reports/category-metrics` | `escalation-reports:read` | all categories (counts) |
| `get_reports_summary` | `GET /api/v1/reports/summary` | `reports:read` **+ `admin.access`** | the whole account |
| `search_kb_articles` | `GET /api/v1/kb/articles` | `kb:read` | published + public only |
| `get_kb_article` | `GET /api/v1/kb/articles/{slug}` | `kb:read` | published + public only |
| `get_kb_article_review` | `GET /api/v1/kb/articles/{ref}/review` | `kb:write` | every article, drafts included |
| `list_kb_proposals` | `GET /api/v1/kb/proposals` | `kb:write` | 🔴 **every held, approved and rejected article in the installation** |
| `list_kb_tree` | `GET /api/v1/kb/tree` | `kb:write` | 🔴 **every category and folder, internal ones included** |
| `list_kb_categories` | `GET /api/v1/kb/tree` (projection) | `kb:write` | every category, flat — no folders nested |
| `list_kb_folders` | `GET /api/v1/kb/tree` (projection) | `kb:write` | 🔴 every folder, flat — internal ones included |
| `list_agents` | `GET /api/v1/admin/agents` | `admin:read` **+ `admin.access`** | 🔴 **the whole agent roster** — not paginated |
| `get_agent` | `GET /api/v1/admin/agents/{id}` | `admin:read` **+ `admin.access`** | one agent, plus `meta.issuableScopes` |
| `list_roles` | `GET /api/v1/admin/roles` | `admin:read` **+ `admin.access`** | every role, with the abilities each holds and an `assignable` flag |
| `list_groups` | `GET /api/v1/admin/groups` | `admin:read` **+ `admin.access`** | every group, with member counts |
| `list_api_keys` | `GET /api/v1/admin/agents/{id}/keys` | `admin:read` **+ `admin.access`** | one agent's keys — **never their secrets** |

⚠️ **`list_kb_categories` and `list_kb_folders` are projections, not
endpoints.** There is no `GET /api/v1/kb/categories` and no
`GET /api/v1/kb/folders` on this API. Each of those two tools fetches the
**whole tree** and filters it in the client, so neither is cheaper than
`list_kb_tree` and `list_kb_folders(kb_category_id=…)` is not a scoped query —
the filter is applied after the tree has arrived. Call one of them **once**;
never call `list_kb_folders` in a loop over categories. They exist because a
flat list is the shape most callers want, not because it is a smaller read.

If the tree ever grows heavy enough that a folder lookup needs its own route,
`GET /api/v1/kb/folders?category_id=` is the shape to add and these two tools
become its front end with no change to their arguments or their output.

#### The ticket detail surface

The three ticket **lists** carry no message bodies at all — deliberately, so a
list reachable by any `ticket:read` key cannot leak a private note. `get_ticket`
is the bounded exception: one ticket at a time, addressed by id.

Its `conversation` is the whole thread, oldest first, each entry carrying a
`kind`:

| `kind` | What it is |
|---|---|
| `comment` | A **public** message — the requester's, or an agent's reply to them |
| `note` | 🔴 A **private internal note**. Staff-only. Never repeat it into a public reply |
| `event` | A history entry ("Escalated", "Status updated: solved"). No body_html, no attachments |

⚠️ **On an escalated ticket the notes are usually where the real work is.**
Ebteqdesk silently downgrades an ordinary agent reply on an escalated ticket
into a private note — the same rule that makes `comment_on_ticket` demand
`escalation:write` there — so the diagnosis lives in `note` entries while the
`comment` entries are only what the requester was actually told.

`thread_limit` (1–200) keeps the **newest** N entries and adds
`conversation_truncated: true` **at the top level of the response**, beside
`data` and not inside it. It is present only when the thread was actually cut,
so its absence means you have the whole conversation.

`get_ticket_attachment` returns a real **image content block**, not JSON. The
image is **downscaled** — longest edge 1568 by default, `max_dimension` 1–4096 —
with a text block of metadata in front of it carrying the dimensions both ways
round (`width`/`height` and `source_width`/`source_height`) and an explicit
`downscaled` flag. ⚠️ **Read the flag; do not compare the byte sizes.**
Re-encoding a flat-colour screenshot at a smaller size routinely produces a
*larger* file, so `bytes > source_bytes` is not evidence the image is untouched.
`downscaled: null` means the server did not say — unknown, not "full fidelity".

so fine print may be illegible; ask for a larger dimension rather than guessing.
An image already smaller than the ceiling is returned untouched and never
enlarged. **Video attachments return a 415 error, not an image**, naming the
type; there is no argument that changes that.

**Visibility is wider here than on the write surface, and the widening is
charged for.** Your own tickets need only `ticket:read`. A ticket that is
somebody else's but sits on the shared escalation queue additionally needs
`escalation:read`, checked at request time. Anything else is a `404` — the same
`404` as an id that never existed.

The three ticket lists — `list_tickets`, `list_tickets_by_category`,
`list_escalations` — share **one paging contract**: `per_page` is 1–20 and
defaults to 20. **Every pull is at most 20 records.** Asking for 21 is a `422`
naming the field, never a silently smaller page, so a page size you asked for is
a page size you got. `links.next` carries `per_page` forward.

### Write tools

| Tool | Endpoint | Scope | Ability | Side effect |
|---|---|---|---|---|
| `create_ticket` | `POST /api/v1/tickets` | `ticket:write` | `ticket.create` | Files a ticket; may create a requester contact record |
| `comment_on_ticket` | `POST /api/v1/tickets/{id}/comments` | `ticket:write` **or** `escalation:reply` — the escalated ticket costs `escalation:reply` **+ `escalation:read`** to reach it | `ticket.reply`, or `bp_escalation.reply` if escalated | **Emails the requester's address** |
| `add_private_note` | `POST /api/v1/tickets/{id}/notes` | `ticket:write` **or** `escalation:write` — the escalated ticket costs `escalation:write` **+ `escalation:read`** to reach it | `ticket.reply`, or `bp_escalation.reply` if escalated | Files an **internal** note. **No email to the requester** |
| `escalate_ticket` | `POST /api/v1/tickets/{id}/escalate` | `escalation:write` | `ticket.reply` | **Notifies every Assistant.** Your **own** tickets only |
| `de_escalate_ticket` | `DELETE /api/v1/tickets/{id}/escalate` | `escalation:write` | **`bp_escalation.reply`** | Clears the escalation timestamp. Works on **any** escalated ticket you can read |
| `set_ticket_status` | `PUT /api/v1/tickets/{id}/status` | `ticket:write` | `ticket.reply`, **+ `ticket.close` when the ticket is currently resolved** | Moves it between working states (1/2/3/8). **Emails nobody, notifies nobody** |
| `close_ticket` | `POST /api/v1/tickets/{id}/close` | `ticket:write` **+ `escalation:reply` if a `body` is sent on an escalated ticket** | `ticket.close` | Resolves the ticket. **Emails a rating survey only if asked for `status: 4`** |
| `propose_kb_article` | `POST /api/v1/kb/articles` | `kb:write` | `kb.manage` | Files a **draft** into a human's review queue |
| `update_kb_article` | `PATCH /api/v1/kb/articles/{ref}` | `kb:write` | `kb.manage` | Rewrites a draft and **re-queues it for review** |
| `create_kb_category` | `POST /api/v1/kb/categories` | `kb:write` | `kb.manage` | Creates a real KB category |
| `update_kb_category` | `PATCH /api/v1/kb/categories/{id}` | `kb:write` | `kb.manage` | Renames it — **re-deriving its slug and changing its portal URL** |
| `delete_kb_category` | `DELETE /api/v1/kb/categories/{id}` | `kb:write` | `kb.manage` | 🔴 **Destroys the category. No undo.** Refused while it holds folders |
| `create_kb_folder` | `POST /api/v1/kb/folders` | `kb:write` | `kb.manage` | Creates a folder, always **`agents` (internal)** |
| `update_kb_folder` | `PATCH /api/v1/kb/folders/{id}` | `kb:write` | `kb.manage` | Renames it — **re-deriving its slug and changing its portal URL** |
| `delete_kb_folder` | `DELETE /api/v1/kb/folders/{id}` | `kb:write` | `kb.manage` | 🔴 **Destroys the folder. No undo.** Refused while it holds articles |
| `reorder_kb_children` | `PUT /api/v1/kb/{categories\|categories/{id}/folders\|folders/{id}/articles}/order` | `kb:write` | `kb.manage` | Rewrites the order of a sibling list. 🔴 **Whole list, never a delta** |
| `upload_kb_media` | `POST /api/v1/kb/media` | `kb:write` | `kb.manage` | ⚠️ **Reads a LOCAL file** and stores it. Returns the only valid `/kb/media/{ulid}` reference; **attached to nothing until an article body cites it** |
| `create_agent` | `POST /api/v1/admin/agents` | `admin:write` | `admin.access` | 🔴 **Creates a real account that can sign in.** Returns `generatedPassword` **once**. Refuses a role granting `admin.access`. No delete exists |
| `update_agent` | `PATCH /api/v1/admin/agents/{id}` | `admin:write` | `admin.access` | Changes name, role or groups. 🔴 **A role change narrows every key that agent already holds.** Refuses a role granting `admin.access` |
| `issue_api_key` | `POST /api/v1/admin/agents/{id}/keys` | `admin:write` | `admin.access` | 🔴 **Hands another account a working credential.** Returns `plainTextToken` **once**. Never grants `admin:*` |
| `revoke_api_key` | `DELETE /api/v1/admin/agents/{id}/keys/{key}` | `admin:write` | `admin.access` | 🔴 **Kills a key on its next request. No undo** |

⚠️ **`upload_kb_media` is the one tool that touches your own filesystem.**
Every other tool builds its request out of arguments; this one opens a path on
the machine the server runs on and sends the bytes. The risk is to *you*, not to
the desk, and the mitigation is entirely in the tool description: it must upload
only a file you named, never sweep a directory and never guess at a path.

🔴 **Its returned `url` is the only valid way to reference the image.** Paste it
into the article body as `<img src="/kb/media/{ulid}" alt="…">`. A fabricated
ULID renders as a broken image in the live knowledge base and an author never
sees the breakage, because a signed-in agent is served media that belongs to no
article at all.

**The order is upload → reference → save.** The upload attaches the file to
nothing; Ebteqdesk derives the article/media link from the article **body** on
every save. An upload no saved body ever cites stays unattached and is deleted
by the server's cleanup sweep once it is seven days old. It is also the one
write here that is **least** safe to retry: each call stores a new copy under a
new ULID, so a blind retry leaves a duplicate nothing points at.

Accepted types are JPG, PNG, WebP, GIF, MP4 and WebM — decided by **sniffing the
file's content**, never its extension, so renaming a `.pdf` to `.png` does not
get it past the server. Images cap at 10 MB, video at 50 MB. A file over the cap
is a `422` naming the limit; a request too large for the web server itself is a
`413` with nothing to retry.

#### The agent-provisioning surface

🔴 **These nine are the only tools that act on the desk rather than on its
content.** Everything else here reads or writes a ticket or a help article;
these read and write *who may act*. Four of them are the most consequential
writes on the server, and three properties are worth reading before the first
call:

**A secret comes back exactly once.** `create_agent` returns
`generatedPassword` when it generated one — omit `password` and it does — and
`issue_api_key` returns `plainTextToken`. Each exists in that one response and
nowhere else: the desk stores a one-way hash, no mail is sent, and there is no
read endpoint on this API or in the browser that produces either again. Hand it
over immediately or reset the account by hand.

**`issue_api_key` can never grant `admin:read` or `admin:write`.** Not to any
agent, by any caller, however privileged — the server subtracts the whole area
last and unconditionally, so a provisioning key cannot mint a successor to
itself and a compromised one cannot extend a chain that revoking the original
would miss. A refusal coded `never_issuable` is final; the answer does not
change with a different agent, a different role or a wider key.

**It refuses rather than narrowing.** What it will issue is

```
requested ∩ the CALLING key's resolved scopes
          ∩ what the OWNER's role may hold
          ∩ what the OWNER's role abilities back
          − {admin:read, admin:write}
```

and one scope any term rejects makes the **whole call** a `422` that creates
nothing, with `refusals` keyed by scope carrying `caller_key`,
`owner_role_policy`, `owner_role_ability` or `never_issuable`. So a `201` means
the key carries exactly what you asked for. Read `meta.issuableScopes` from
`get_agent` or `list_api_keys` first — that is the same arithmetic, already
done — and skip the round trip.

⚠️ **The role is the permissions decision, not the groups.** Which scopes a key
can resolve follows the abilities of the role its owner is on, so an agent put
on the wrong role cannot be issued a working key at all. Read `list_roles`'
`permissions` before choosing. Groups are organisational and grant nothing.

🔴 **And the role can never be one granting `admin.access`.** Refused on create
*and* on update — `list_roles` reports `assignable: false` on those rows. This is
the second half of the never-issuable rule above and it is not redundant with it:
without it, an `admin:write` key would not need an issued admin scope at all. It
would create an account on an administrator role, read that account's
`generatedPassword` out of the `201`, sign in, and mint a provisioning key from
**Settings → API keys** like any human — a chain with no admin scope anywhere in
it and no upper bound on its length. Guarding only create would be one `PATCH`
away from useless, which is why `update_agent` refuses it too. So the API can
demote an administrator (while another remains) and can never promote one back.

⚠️ **Stated plainly, because this is still a widening:** an `admin:write` key
*can* create non-admin accounts and obtain browser sessions for them, bounded by
those roles' abilities. What it cannot do is manufacture an account that reaches
Settings. The property is that the chain does not self-perpetuate — not that the
API cannot obtain a session.

⚠️ **There is no delete-agent, no password reset and no email change**, and none
is coming. Deleting an agent reassigns or clears tickets, comments, notes and
performance rows across the whole desk behind a force flag and three separate
refusals; a password reset ends live sessions and reveals its value once on a
screen. Both stay with a person in the Ebteqdesk web UI, as does changing a
sign-in address. `create_agent` takes `email_local` — half an address — because
the desk owns the domain and a caller cannot choose it.

#### Two ways to write into a ticket, and they are not interchangeable

`comment_on_ticket` posts a **public reply the requester receives by email**.
`add_private_note` files an **internal note the requester never sees**. That is
the whole difference and it is the one an agent most needs to get right: if the
text is an observation, a finding or a handover, it is the note tool. There is no
`private` flag on either — two paths, one job each, so a tool call can be read
back and understood from its name alone.

The note is quiet, not silent: your team, the ticket's assignee and anyone
`@`-mentioned in the body are still notified. Only the requester is not, and that
is a property of the stored row (`comments.private`) rather than something the
client suppresses.

⚠️ **Internal is not reversible.** There is no delete-comment tool and no
note-editing tool anywhere on this API. A note filed by mistake stays on the
ticket until a person removes it in the Ebteqdesk UI.

⚠️ **`add_private_note` reaches further than every other ticket write.** The
other four resolve only tickets **assigned to you**; this one adds **any
escalated ticket**, whoever it is assigned to and whatever its status — so it can
annotate a ticket `list_tickets` never shows, and one `list_escalations` no
longer shows either because it has been resolved. Reaching one that way
additionally needs `escalation:read`. That is the point (a reviewer records a
finding on a queue ticket) and it is also a reason not to use it casually on
other people's tickets.

⚠️ **`comment_on_ticket` does NOT reach that far, deliberately.** A public reply
on somebody else's escalation comes back `403` with `reason:
"ticket_not_assigned"` — not a scope problem, and re-minting will not fix it. A
requester-facing reply on a ticket assigned to another agent is theirs to send;
record what you found with `add_private_note` instead.

⚠️ **An escalated ticket you cannot reach is a `404`, not a scope refusal.** If
your key cannot resolve `escalation:read`, both write tools answer an escalated
ticket with the same "There is no ticket with the id N" body as an id that does
not exist. That is on purpose — otherwise the pair of answers would tell any key
which ids are escalated — so do not read the `404` as proof the ticket is
absent, and do not use these tools to probe for escalations.

Both tools return the same receipt: `{"data": <ticket>, "comment": {"id",
"created_at"}}`. 🔴 **A null `comment.id` means nothing was filed** — the call
still answers `201` and the ticket is still touched, but no row exists. Do not
report it as sent or saved.

#### Reordering: the whole list, never a delta

`reorder_kb_children(scope, ordered_ids, parent_id=None)` is **one tool over
three endpoints**, chosen by `scope`:

| `scope` | Reorders | `parent_id` |
|---|---|---|
| `"categories"` | every category | **must be omitted** — categories have no parent |
| `"folders"` | one category's folders | that **category** id |
| `"articles"` | one folder's articles | that **folder** id |

A mismatch is refused in the client, before any request is sent.

🔴 **`ordered_ids` must be every sibling id, in the order you want them.** There
is no "move item X to position N" form, and a partial list is **not** a partial
reorder:

```python
# WRONG — a delta. 422, and nothing is written.
reorder_kb_children(scope="folders", parent_id=3, ordered_ids=[9])

# RIGHT — the complete list, 9 first.
reorder_kb_children(scope="folders", parent_id=3, ordered_ids=[9, 7, 3])
```

The posted set must be **exactly** the current sibling set: same members, same
count, no duplicates. A subset, an extra id, an id from another parent, or a
repeat is a `422` on `ids` with nothing written. That refusal is information, not
a bug to work around — it means your view of the list is stale. Re-read it and
post again.

Positions are dense and 0-based, so the first row of the response is always
`position: 0`; report the response's positions, not the input.

⚠️ **This is one of only two writes on this server that are safe to retry** —
the other is `set_ticket_status`, whose no-op the server guards. Positions are
assigned by index, so replaying the same body leaves the same order and answers
`200`. Every other write tool must not be retried blind.

Article ids come from none of the structure tools — `list_kb_tree` carries an
`articles_count`, not the articles — so for `scope="articles"` read the current
order from this tool's own previous response or from the folder in the Ebteqdesk
UI. Drafts hold positions like any other article and must be in the list.

#### Which tools can email the requester

Three of the twenty-one, and **no default triggers one**. Ebteqdesk being an
internal desk does not change this list — the requester is a colleague, and the
mail still leaves:

| Tool | Mails the requester | When |
|---|---|---|
| `comment_on_ticket` | always | that is what it is for — the reply *is* the email |
| `close_ticket` | only on `status: 4` | its default is `5`, which sends nothing |
| `create_ticket` | only on `status: 4` | opening a ticket already resolved fires the same survey |

The nine provisioning tools mail nobody either — `create_agent` sends no
invite and no welcome, which is exactly why its generated password comes back in
the response and has to be handed over out of band.

The ten knowledge base writes mail nobody, and neither do `add_private_note`,
`escalate_ticket`, `de_escalate_ticket` or `set_ticket_status`. The two article
writes land a draft
for a human, and a draft is readable nowhere outside the desk; the four
structure writes only create or rename `agents`-visibility categories and
folders, which nobody outside the desk sees at all.

⚠️ **The survey is env-gated server-side and this client cannot see the gate.**
An install may set `EBTEQDESK_RATING_EMAIL_ENABLED=false` to suppress it, but
**the code default is on**, nothing in the API's responses reports the setting,
and no tool here can read it. Treat `status: 4` as sending mail unless a human
tells you otherwise.

`close_ticket` used to default to `4` (solved), which sent the satisfaction
survey — so an agent closing a ticket mailed the requester as a side effect of
an argument it never set. Since **1.0.0** the default is `5` (closed) and the tool
always sends the status explicitly. **This is a breaking change**: if you were
relying on `close_ticket` marking tickets *solved*, pass `status: 4` from now on.

#### The knowledge base write surface: propose, never publish

🔴 **These tools cannot publish, and nothing on `/api/v1` can.** Every write
lands `status = draft`, `review_state = pending` — on the create *and* on every
update, whatever the article's previous review state was. Publishing is a
human's browser session, deliberately: an integration that could publish could
put unreviewed text on the public help portal, where signed-out visitors read
it.

🔴 **Every update clears the reviewer's note**, so **do not `update_kb_article`
to find out what a reviewer said** — checking the verdict through a write
destroys the verdict. `get_kb_article_review` exists exactly for that and has no
side effects at all.

🔴 **And when you no longer hold the `reference`, `list_kb_proposals` is how you
find it again.** A `reference` appears in exactly one place — the create
response — and nothing persists it across agent sessions, so an integration
restarted with no memory used to have no way at all to ask *which of my
proposals were rejected, and why*. Call `list_kb_proposals(review_state="rejected")`,
read `review.note` off each row, revise, and send the revision with
`update_kb_article` using that row's `reference`.

⚠️ **That list is installation-wide and is not "your" proposals.** It returns
every article carrying a review state, whoever proposed it — including
human-written articles somebody later revised through this API — because an API
key identifies an *account*, not an agent, and two agents may share one. Match
your own rows by `title` or `reference`. It is also deliberately narrower than
the single-article read: **no `body_html`**, only a 300-character `excerpt`, so
that a page of twenty-five rejections is a payload you can actually read.

⚠️ **Never resubmit unchanged to "bump" a `pending` article.** Every update
restamps `review_requested_at`, which moves it to the *back* of the queue it is
already in.

🔴 **A published article is no longer refused — the edit is STAGED.** This used
to be a flat `409`; it is not any more, and anything you remember about "this
surface edits drafts only" is now wrong for the update half.

| article | status | body |
|---|---|---|
| draft | `200` | `{"data": …}` — edited in place, re-queued for review |
| **published** | **`202`** | `{"data": …, "revision": {…}}` — **`data` is the LIVE, UNCHANGED article** |

The live article keeps serving customers byte-for-byte while a human approves or
rejects the revision in the authoring UI. Nothing here can approve it.

🔴 **On a `202`, `data` is what customers are still reading — not what you
sent.** `data.title` is the old title, `data.body_html` is the old body, and
`data.translations` is very often `[]` while the version you just submitted sits
in `revision`. Read `data` back to confirm your edit and you will conclude,
wrongly, that nothing happened. **The discriminator is the presence of the
top-level `revision` key**: a `200` never carries it, a `202` always does.

⚠️ **A second call replaces the staged revision; it does not queue another.**
There is one revision row per article (`unique(kb_article_id)`), and a resend
overwrites a *rejected* revision too — taking the reviewer's note with it. Read
the verdict with `get_kb_article_review` **first**.

⚠️ **A malformed edit to a live article is a `422` that stages nothing.**
Validation runs before the staging branch, so the refusal now names the field
that is actually wrong instead of the article's published state.

🔴 **Two languages go in ONE call.** Both write tools take per-language
arguments — `en_title`, `en_body`, `en_seo_title`, `en_seo_description` and the
`zhcn_*` four — and everything given lands in a single request, so a single
revision carries both languages. `locale` still works and is still right for a
one-language edit.

The older "file one language, then add the other with `update_kb_article`"
advice is **wrong on a published article** and loses a language silently: the
second call stages a revision that *replaces* the first (one row per article),
a revision holding only the second language is then refused for taking the
article out of the first language's help centre, and
`allow_missing_versions: true` gets past that refusal by performing exactly that
removal. Send both languages together and none of it applies. Adding a language
to a published article means resending the existing one unchanged — read it
first with `get_kb_article_review`.

`get_kb_article_review` carries `revision` too, always, and it reads **three**
ways — `state` is never `"approved"`, because approving a revision applies it
and deletes the row:

| `revision` | means |
|---|---|
| `{"state": "pending", …}` | waiting on a human; the live page is unchanged |
| `{"state": "rejected", …}` | refused — read `note` |
| `null` | **ambiguous**: nothing was ever staged, *or* a staged edit was approved and is now live |

Tell the two nulls apart by looking for your text in `data` (or in
`get_kb_article(slug, locale=…)`), never by assuming the first.

`{ref}` is the frozen slug **or** the `id:<n>` form the create response returns
as `reference`. A slug is frozen at first publish, so everything this API
creates has `slug: null` and `id:<n>` is the normal case.

`kb_folder_id` is required on create and **not accepted on update**: a folder
carries the article's visibility, so moving an article stays a human act.

#### The knowledge base structure surface: `list_kb_tree` first

🔴 **`propose_kb_article` needs a `kb_folder_id`, and `list_kb_tree` is the only
place one comes from.** Nothing else on `/api/v1` returns a folder id — the
article payloads carry `{slug, name}` pairs and no ids at all — so before this
tool the KB write surface was unusable against a knowledge base whose structure
the agent could not see, and unusable *at all* against an empty one. Call it
first; read the `id` off the folder you mean.

**Argument names follow one rule across the whole knowledge base surface**, so a
model never has to guess which spelling a tool wants. A **body** field keeps the
API's own name — `propose_kb_article(kb_folder_id=…)`,
`create_kb_folder(kb_category_id=…)` — because the server quotes those strings
back in its `422` `errors` map. An **identifier naming the thing being acted on**
takes the short form, because it is a path segment — `update_kb_folder(folder_id=…)`,
`update_kb_category(category_id=…)`, and the existing `get_ticket(ticket_id=…)`.

🔴 **Neither `create_kb_folder` nor `update_kb_folder` accepts `visibility`.**
There is no such argument, on purpose — not optional, not with a default. A
folder decides who can see the articles inside it, and publishing content where
anyone outside the desk can read it is a human's decision for the same reason
nothing on this API can publish an article. **Every folder created through this
API is `agents` (internal)**, which means *nothing filed into it reaches a
reader outside the desk until a person changes its visibility in the Ebteqdesk
web UI*. Do not tell a user their article will be visible outside the desk.

🔴 **`update_kb_folder` cannot move a folder either.** `kb_category_id` is absent
for the same reason. The server *ignores* both keys if they are sent, which is
exactly why an argument would be worse than its absence — it would read as a
working control and silently do nothing.

⚠️ **Renaming a category or a folder changes its portal URL.** Unlike an
*article's* slug, which is frozen at first publish and never moves again, a
category or folder slug is re-derived from its name on every save — and both are
segments of the nested portal address `/support/kb/{category}/{folder}`. There is
no redirect. The response carries the new `slug`.

⚠️ **Name collisions are a `422` on `name`, checked against the *derived* slug.**
So "POS" and `  p.o.s!  ` collide. Category slugs are unique **globally**; folder
slugs are unique **only within their category**, so the same folder name under
two categories is fine and is the point.

🔴 **`delete_kb_category` and `delete_kb_folder` are the only tools here that
destroy anything, and there is no undo.** No trash, no restore, no version
history: once the call returns, the row is gone and its `data` receipt is the
only record of what it was. Re-creating gives a **new** id at the end of the
list, not the row back. Name what you are about to delete and get the user's
agreement first.

🔴 **Both are refusals, never cascades.** A category still holding folders is a
`422` on `category` naming the count — *"This category still holds 2 folders.
Move or delete them first."* — and a folder still holding articles is the same
shape on `folder`. Nothing is deleted in either case. That chain is a safety
property rather than an obstacle: **there is no delete-article tool on this API
at all**, and an article delete in Ebteqdesk has no undo, no trash and no version
history, so a category or folder with content in it can only be emptied by a
person in the web UI. Do not delete children to make a parent delete go through
unless that is what was asked for.

Both take the **path id and nothing else** — no move, no visibility, no cascade
flag, no dry run. The response is the row as it last existed, in the same shape
the create answers with: `folders` is `[]` / `articles_count` is `0`, and
`position` is the index the row **vacated**, so every later sibling in that list
has already moved up by one and any positions you were holding are stale.

⚠️ **Never retry a delete that timed out.** It may well have landed; read
`list_kb_tree` to find out instead.

**You may write to exactly the tickets you can read.** Every `{id}` resolves
through the same query `list_tickets` pages over, which is "assigned to you" and
nothing else — for every role, administrators included. A ticket that exists but
is not yours returns the *same* not-found error as an id that never existed, so
the id space cannot be probed.

🔴 **One exception, and it exists because `list_escalations` is a shared queue.**
That tool hands you ids for tickets assigned to other agents, and answering
"there is no ticket with the id 4" for a row it had just served made the API
contradict itself. So a ticket **on the escalation queue**, asked for by a key
that could have read that queue (`escalation:read` + `bp_escalation.view`),
comes back as a `403` saying it belongs to another agent — see
[Troubleshooting](#ticket-4-is-assigned-to-another-agent-and-cannot-be-modified-by-you-403).
Everything else keeps the indistinguishable `404`, so the id space is exactly as
unprobeable as before: you can only learn what `list_escalations` already told
you.

#### There is no `dry_run`, and that is deliberate

Ebteqdesk has no dry-run mode. A client-side one could only describe the request
it *would* have sent: it would validate nothing, consult no policy, and would
cheerfully report success for a call the server refuses. A guardrail that reads
like a real one and is not is worse than none, because it gets trusted.

What is there instead: every write tool's description opens with a capitalised
line naming the side effect and who receives it, because that text is what a
model reads before deciding to call. And nothing is ever retried automatically —
a POST that timed out may well have landed.

### `whoami`

No arguments. Identifies the account *and the API key* behind the token. It is
the only endpoint that needs no scope, which is what makes it the way to diagnose
every scope refusal.

> Who am I in Ebteqdesk?

```json
{
  "data": {
    "id": 1,
    "uuid": "d56d75a3-0020-4706-ac57-0af77be8c89c",
    "name": "Admin",
    "email": "admin@ebteq.desk",
    "role": { "id": 1, "name": "Administrator", "key": "administrator" },
    "permissions": ["admin.access", "bp_escalation.view", "kb.manage", "ticket.view", "…"],
    "apiKey": {
      "id": 29,
      "name": "py-full",
      "scopes": ["ticket:read", "kb:read", "escalation-reports:read"],
      "requested": ["ticket:read", "kb:read", "escalation-reports:read"],
      "expiresAt": null
    }
  }
}
```

**Two separate gates, and the two `apiKey` lists are how you tell them apart:**

- `apiKey.requested` — the scopes the **key** carries, as minted
- `apiKey.scopes` — the intersection of those with the **role**: what actually
  resolves right now

So a scope in `requested` but missing from `scopes` is one the account's role no
longer backs. `scopes` can never be wider than `requested`.

`permissions` are role abilities in a *different vocabulary* from key scopes
(`bp_escalation.view` vs `escalation-reports:read`) and do not map one to one —
compare the two `apiKey` lists, not `permissions` against them.

`apiKey` is `null` if the request was not authenticated by a bearer token.

### `list_tickets`

| Argument | Type | Required | Notes |
|---|---|---|---|
| `page` | integer | no | 1-based. |
| `per_page` | integer | no | 1–20, default **20**, which is also the maximum. Above it is a `422`, not a smaller page. Use it to ask for *fewer*. |

Returns the tickets **assigned to** the token's account — the agent's own queue.
Not every ticket in Ebteqdesk, and not tickets the account raised as a requester.
Resolved and closed tickets are included. For the shared escalation queue, which
is *not* limited to this account, see [`list_escalations`](#list_escalations).

> Show me my open tickets.

```json
{
  "data": [
    {
      "id": 2,
      "subject": "Printer on fire",
      "status":   { "id": 2, "name": "open" },
      "priority": { "id": 3, "name": "high" },
      "category": { "slug": "bp-task", "name": "BP Task" },
      "requester": { "id": 1, "name": "Ada Lovelace", "email": "ada@example.com" },
      "assignee":  { "id": 1, "name": "Admin", "email": "admin@ebteq.desk" },
      "escalated": false,
      "escalated_at": null,
      "created_at": "2026-08-11T05:09:30+00:00",
      "updated_at": "2026-08-11T05:09:30+00:00"
    }
  ],
  "links": { "first": "…", "last": "…", "prev": null, "next": null },
  "meta":  { "current_page": 1, "per_page": 20, "last_page": 1, "total": 2, "…": "…" }
}
```

The person who raised the ticket is `requester`. Page with `links.next` until it
is null.

#### 🔴 `escalated` is the state; `escalated_at` is only *since when*

Both fields are on every ticket this API returns, and they are **not**
interchangeable.

- **`escalated`** — boolean, always accurate. This is the escalation state.
- **`escalated_at`** — ISO 8601 or `null`. `null` means *"unknown"*, **not**
  *"not escalated"*: the column was added partway through the product's life, so
  every ticket escalated before that is permanently `null` while still being
  escalated.

Deriving state from `escalated_at !== null` therefore reports the tickets that
have been escalated **longest** as not escalated — the exact rows you least want
to miss. Read `escalated`. The timestamp is for "how long has this been
waiting", and it is the sort key `list_escalations` pages over.

### `list_tickets_by_category`

| Argument | Type | Required | Notes |
|---|---|---|---|
| `category` | string | yes | A ticket-type slug, e.g. `bp-task`. |
| `page` | integer | no | 1-based. |
| `per_page` | integer | no | 1–20, default 20. Same rule as `list_tickets`. |

Same envelope as `list_tickets`, narrowed to one category. Slugs resolve against
the live category table on every request, so a category added in Ebteqdesk is
usable immediately. An unknown slug is an error that names the slug you asked
for.

> List my bp-task tickets.

### `list_escalations`

Scope `escalation:read` · ability `bp_escalation.view`

| Argument | Type | Required | Notes |
|---|---|---|---|
| `page` | integer | no | 1-based. |
| `per_page` | integer | no | 1–20, default 20. |

The shared business-partner escalation queue.

> 🔴 **This list is not yours.** It returns **every unresolved escalated ticket
> in the installation**, whoever it is assigned to, **including tickets assigned
> to nobody**. It is the only ticket list on this API that is not scoped to the
> token's own account — `list_tickets`, the category lists and every write tool
> show you only your own. **Read each row's `assignee`** to see which are yours,
> and never report this list's length as "your escalated tickets".

That is deliberate, not an oversight: an escalation is work that has been handed
*off*, and the rows most needing attention are precisely the unassigned ones. An
API that hid them would let an unattended agent conclude the queue was empty
while it was not. The same `bp_escalation.view` ability shows the same rows on
`/dashboard/bp`, so the two surfaces agree.

Returns the same envelope and **the same per-ticket object** as `list_tickets`,
byte for byte — one serialiser renders both, so there is no second shape to
learn.

**Order is longest-escalated first**: `escalated_at` ascending, then `id`. Rows
with a `null` `escalated_at` sort **last** despite being the oldest — `null` is
"unknown", not "the dawn of time". They are genuinely escalated; `escalated` is
`true` on them.

⚠️ **A solved escalated ticket is not on this list at all.** Solving takes a
ticket off the BP queue, so a row disappearing from here does **not** mean the
escalation was answered — it may have been solved, or de-escalated, and the list
cannot tell you which. To find out what happened to a ticket, fetch the ticket.

> What's in the escalation queue?

### `get_escalation_report`

| Argument | Type | Required | Notes |
|---|---|---|---|
| `date_from` | string | no | ISO 8601 date or date-time. Widened to the **start** of that day. |
| `date_to` | string | no | ISO 8601. Widened to the **end** of that day. Must not precede `date_from`. |

Omitting both means all time. A reversed range is rejected with an error — it
never silently falls back to the full range.

> Give me the escalation report for March 2026.

```json
{
  "data": {
    "range": { "from": null, "to": null },
    "metricKeys": ["total", "status.new", "status.open", "…", "escalated", "escalatedUndated"],
    "totals": { "total": 2, "status.open": 2, "escalated": 0, "escalatedUndated": 0, "…": 0 },
    "categories": [
      { "key": "bp-task", "id": 1, "slug": "bp-task", "name": "BP Task",
        "metrics": { "total": 2, "status.open": 2, "escalated": 0, "…": 0 } },
      { "key": "_uncategorised", "id": null, "slug": null, "name": "Uncategorised",
        "metrics": { "…": 0 } }
    ]
  },
  "meta": { "filters": { "from": null, "to": null }, "generatedAt": "2026-08-11T08:24:00+00:00" }
}
```

**Row identity is `key`, never `id` or `slug`.** The "Uncategorised" bucket has
`id: null` *and* `slug: null`. `key` is the slug where one exists, `_type-{id}`
where a category has none, and `_uncategorised` for the null bucket. Join, group
and label on `key`.

**Three rules about the numbers.** They are counter-intuitive, the payload
cannot signal them, and this client does not "fix" any of them — it reports what
the API returns. They are also stated in full in the tool description, so a model
reading the result has them:

1. **`escalated / total` is not a percentage, and `escalated` can exceed
   `total`.** The two counts range over different date columns, so in a bounded
   range they count overlapping-but-different sets. Don't present a ratio; don't
   treat `escalated > total` as corrupt.
2. **`escalatedUndated` is identical in every range.** It counts escalations with
   no date at all, so no filter moves it. Never add it to `escalated`. Two
   different ranges showing the same value is correct, not a caching bug.
3. **`sum(status.*)` can be less than `total`.** Don't derive an "other" bucket
   from the difference, and don't use `total` as the denominator of a status
   breakdown.

`meta.filters` echoes what you sent; `data.range` reports the instants actually
measured.

### `search_kb_articles`

| Argument | Type | Required | Notes |
|---|---|---|---|
| `query` | string | no | Free text, matched against title and body. |
| `per_page` | integer | no | 1–100, default 25. Out of range is an **error**, not a silent clamp. |
| `page` | integer | no | 1-based. |

Omitting `query`, or passing an empty/whitespace string, means **"no search"** —
the whole corpus, newest first. It does not mean "match nothing".

> Search the knowledge base for password reset.

```json
{
  "data": [
    {
      "slug": "resetting-your-vpn-password",
      "title": "Resetting your VPN password",
      "category": { "slug": "getting-started", "name": "Getting Started" },
      "folder":   { "slug": "accounts", "name": "Accounts" },
      "tags": [],
      "published_at": "2026-07-02T09:00:00+00:00",
      "updated_at": "2026-08-11T06:59:41+00:00",
      "excerpt": "Open the portal and choose Forgot password. Tom & Jerry."
    }
  ],
  "links": { "…": "…" },
  "meta":  { "…": "…" }
}
```

**The corpus is published, `public`-visibility articles only — always, for every
token, including an administrator's.** Internal runbooks and `agents`-only
articles that are visible in the Ebteqdesk web UI are not reachable here. If an
article you can see in the browser does not turn up, that is almost always why.

Rows are summaries with no body. Each carries a `url` — the article's page on
the public knowledge base portal, safe to share outside the desk, because the
server emits it only for an article a signed-out visitor can actually open.
Prefer it over building a link from the slug yourself; the portal's path is not
part of this API's contract.

`url` is nullable in the shape and a client should read it as such, but no row
reachable through this package can carry a null: the corpus here is
published-and-public by definition. The nulls belong to the REST write
endpoints, which echo unpublished drafts and which this package does not wrap.

### `get_kb_article`

| Argument | Type | Required | Notes |
|---|---|---|---|
| `slug` | string | yes | e.g. `resetting-your-password`. Slugs are frozen at first publish. There is no lookup by numeric id. |

> Get the KB article resetting-your-vpn-password.

```json
{
  "data": {
    "slug": "resetting-your-vpn-password",
    "title": "Resetting your VPN password",
    "category": { "slug": "getting-started", "name": "Getting Started" },
    "folder":   { "slug": "accounts", "name": "Accounts" },
    "tags": [],
    "published_at": "2026-07-02T09:00:00+00:00",
    "updated_at": "2026-08-11T06:59:41+00:00",
    "seo": { "title": "Resetting your VPN password", "description": "Open the portal and choose Forgot password." },
    "body_html": "<p>Open the portal and choose <strong>Forgot password</strong>. Tom &amp; Jerry.</p>"
  }
}
```

`body_html` is **sanitised HTML, not markdown**. Ebteqdesk stores article bodies
as HTML and there is no markdown form of them.

**A not-found result is ambiguous on purpose.** An article that exists but is
unpublished or internal returns *exactly* the same error as a slug that never
existed — identical text, and it does not repeat your slug back. That stops the
endpoint being used to enumerate the titles of draft and internal documentation.
This package does not work around it, and neither should you: don't tell a user
"the article exists but is hidden", because you cannot know that.

---

### `create_ticket`

Scope `ticket:write` · ability `ticket.create` · **files a real ticket**

| Argument | Type | Required | Notes |
|---|---|---|---|
| `subject` | string | yes | 3–190 characters. |
| `description` | string | yes | The opening message body. |
| `requester` | object | yes | The requester — on this internal desk, the member of staff who raised it. Its address is where Ebteqdesk mails every public reply. Two forms — see below. |
| `priority` | integer | no | `enum` — 1 low, 2 normal (default), 3 high, 4 blocker. Anything else is refused by the tool, before any request. |
| `status` | integer | no | `enum` — 1 new (default), 2 open, 3 pending, 8 waiting on customer (the API's own enum name `waitingOnCustomer`, inherited here and not renamed — on this desk it means waiting on the requester), 4 solved, 5 closed. Merged (6) and spam (7) are refused. ⚠️ **4 resolves the ticket on creation and emails the requester's address the survey.** |
| `category` | string | no | A ticket-type slug, e.g. `bp-task`. An unknown slug is an **error**, not a silent "uncategorised". |
| `reference_number` | string | no | Up to 64 characters. |
| `tags` | string[] | no | Each up to 50 characters. |

**`requester` has two forms**, and the second one writes:

```jsonc
{ "id": 12 }                                         // an existing contact, by id
{ "email": "ada@example.com", "name": "Ada Lovelace" } // found — or CREATED
```

The email form is find-or-create matched on the address, which is the identity
column. If no contact has that address, **one is created**. `name` applies only
on insert: it will not rename an existing contact, so a mistyped name beside a
known address is harmless and also silently ignored. If both are given, `id`
wins. Prefer `id` whenever you have one — you can read it off `requester.id` in
any `list_tickets` row.

Both shapes are in the tool's **JSON Schema**, as a `oneOf` with `required` on
each branch, so a client that reads schemas and not prose can construct a valid
call without this page. `priority` and `status` are `enum`s with per-value
descriptions for the same reason. That was not always true, and the failure it
caused was a first call built entirely from a schema that said `requester` was
any object and `priority` any integer.

**Two fields are not yours to set.** The source is always `api`, and the
assignee is always the token's own account. The second is not a limitation to
route around: this surface shows you only tickets assigned to you, so a ticket
created for someone else would vanish from your view on the very next request.

> Open a ticket for ada@example.com about the printer being on fire.

```json
{
  "data": {
    "id": 1,
    "subject": "MCP write client end-to-end check",
    "status":   { "id": 1, "name": "new" },
    "priority": { "id": 3, "name": "high" },
    "category": null,
    "requester": { "id": 1, "name": "Ada Lovelace", "email": "ada@example.invalid" },
    "assignee":  { "id": 1, "name": "Admin", "email": "admin@ebteq.desk" },
    "created_at": "2026-08-12T05:22:08+00:00",
    "updated_at": "2026-08-12T05:22:08+00:00"
  }
}
```

That is **the same object `list_tickets` returns**, element for element — one
`TicketResource` renders both, so you never need a second parser for the thing
you just created.

### `comment_on_ticket`

Scope `ticket:write` (**plus `escalation:write` if the ticket is escalated**) ·
ability `ticket.reply` · **emails the requester's address**

| Argument | Type | Required | Notes |
|---|---|---|---|
| `ticket_id` | integer | yes | From `list_tickets`. |
| `body` | string | yes | Non-blank. |

The reply is **public and requester-facing**: it is mailed to the requester's
address and shown on their portal page. There is no private/internal flag —
none, on an internal desk or anywhere else; writing internal notes over this API
is not part of the surface.

> Reply on ticket 1 telling them a replacement is on the way.

```json
{
  "data": { "id": 1, "subject": "…", "…": "…" },
  "comment": { "id": 1, "created_at": "2026-08-12T05:22:08+00:00" }
}
```

The comment body is **not echoed back**, by design — this API has no
comment-body serialisation path at all, so a reply held for review cannot leak
through one.

⚠️ **`comment.id` can be `null`, and that means nothing was posted.** Ebteqdesk
discards a reply whose text is identical to the author's saved signature: you
still get a 201 and the ticket is still touched, but no comment row exists. Treat
a null id as "not sent".

🔴 **On an escalated ticket this also needs `escalation:write`.** Check the
ticket's `escalated` field before calling; if you did not, the refusal tells you
— see the troubleshooting entry
[below](#this-ticket-is-escalated-and-replying-to-an-escalated-ticket-needs-the-escalationwrite-scope).

### `escalate_ticket`

Scope `escalation:write` · ability `ticket.reply` · **notifies every Assistant**

| Argument | Type | Required |
|---|---|---|
| `ticket_id` | integer | yes |

Puts the ticket on the escalation queue, stamps its escalation time, writes an
"Escalated" history entry, and sends the `TicketEscalated` notification.
Returns `{"data": {...}}` — the ticket, in the `list_tickets` shape.

🔴 **Never retry this call blind.** The stored state is idempotent (a second
escalation keeps the original timestamp) but **the notification is not** — call
it twice and the team is alerted twice.

You can now tell whether a first attempt landed, which you could not before
`escalated` shipped: **check `escalated` on the ticket before calling**, and if a
call times out, **fetch the ticket and read `escalated`** rather than repeating
it. Read the boolean, not `escalated_at` — see
[above](#-escalated-is-the-state-escalated_at-is-only-since-when).

### `de_escalate_ticket`

Scope `escalation:write` · ability `ticket.reply`

| Argument | Type | Required |
|---|---|---|
| `ticket_id` | integer | yes |

Takes the ticket off the queue and clears `escalated_at`. Sends **no**
notification, so a repeat call is cheaper than a repeat escalation — but it
still appends a second "De-Escalated" history entry, and the escalation
timestamp is gone once it succeeds. Re-escalating afterwards restarts the clock
from now, which will misreport how long the ticket actually sat escalated.

As with `escalate_ticket`, check `escalated` first rather than calling
speculatively, and re-read it to confirm instead of repeating a call that timed
out.

### `set_ticket_status`

Scope `ticket:write` · ability `ticket.reply`, **plus `ticket.close` when the
ticket is currently resolved** · **emails nobody**

| Argument | Type | Required | Notes |
|---|---|---|---|
| `ticket_id` | integer | yes | From `list_tickets`. |
| `status` | integer | yes | `enum` — `1` new, `2` open, `3` pending, `8` waiting on customer (the API's own enum name `waitingOnCustomer`, inherited, not renamed). Nothing else; `4`/`5`/`6`/`7` are refused by the tool rather than by the API. |

The quiet write. It moves a ticket between **working states** and sends no mail
to the requester, no agent notification and no domain event.

🔴 **It cannot resolve a ticket, and that is the point.** `4` (solved) and `5`
(closed) belong to [`close_ticket`](#close_ticket), which is the only tool on
this server that can send the satisfaction survey — keeping those two
values there keeps that warning in one place. `6` (merged) and `7` (spam) are
outcomes of other actions and are not settable through this API at all.

**Reopening is `status: 2`.** A ticket that is currently solved, closed, merged
or spam is being *reopened*, which costs the `ticket.close` ability on top of
`ticket.reply` — the same ability resolving it cost, because it is the same
boundary crossed the other way. The refusal names `ticket.close`.

⚠️ **Reversible state, permanent trail.** The status is fully reversible; the
`Status updated: <name>` history entry each real change appends is not, and
anyone reading the ticket sees it in `get_ticket` as an `event`. Flapping a
ticket between two states leaves a permanent record of the flapping.

✅ **The no-op is safe to retry.** Sending the status a ticket already holds
writes nothing and appends no history entry, and still answers `200` — the
server guards it, which is why the endpoint is a `PUT`. This and
`reorder_kb_children` are the only two writes here you may safely repeat.

✅ **An escalated ticket needs nothing extra.** Unlike `comment_on_ticket` and
`add_private_note`, this does **not** grow an `escalation:write` requirement on
an escalated ticket. What the BP surface treats differently is who may *speak*
to the requester, and this tool sends no message at all. Reopening a solved
*escalated* ticket does put it back on the shared BP queue — carrying its
original `escalated_at`, so its time-in-escalation reads as full wall-clock
including the period it spent solved.

> Set ticket 1 to waiting on customer.

```json
{
  "data": {
    "id": 1,
    "status": { "id": 8, "name": "waitingOnCustomer" },
    "…": "…"
  }
}
```

### `close_ticket`

Scope `ticket:write` · ability `ticket.close` · **emails the requester's address
only if you ask for `status: 4`**

| Argument | Type | Required | Notes |
|---|---|---|---|
| `ticket_id` | integer | yes | From `list_tickets`. |
| `status` | integer | no | `enum` — `5` closed (**default**, sends nothing) or `4` solved (**sends the survey**). Nothing else; anything else is refused by the tool rather than by the API. |

Moves the ticket off the open queue and records the transition.

🔴 **Exactly one status value sends outbound email to the requester's address.**

| `status` | Result | Requester email |
|---|---|---|
| `5` closed | resolved | none — **this is the default** |
| `4` solved | resolved | the satisfaction survey, asking them to rate the ticket |

So calling this tool without naming a status contacts nobody. Ask for `4` only
when a survey is genuinely wanted, and confirm with the user first — the survey
cannot be recalled, and this is not a safe way to tidy up test data. That the
requester is a colleague on an internal desk is not a reason to relax this: the
mail leaves either way, and the `EBTEQDESK_RATING_EMAIL_ENABLED` gate that could
suppress it defaults to **on** and is invisible from here.

⚠️ **Changed in 1.0.0.** The default used to be `4`. Ebteqdesk's own API default
is still `4`; this tool always sends the status explicitly, so the two cannot
disagree, but a `curl` of the same endpoint with no `status` still solves and
still mails.

`ticket.close` is a **separate ability** from `ticket.reply`: an account allowed
to answer a requester is not automatically allowed to resolve their ticket, so
this can be refused on a ticket where `comment_on_ticket` works.

**Closing does not reply.** This tool takes no message argument: answering the
requester and resolving the ticket are two acts behind two permissions. To do
both, call `comment_on_ticket` first and then this — in that order, so the
requester's last email is your answer rather than a survey.

⚠️ **The REST endpoint does accept a `body`, and this client deliberately does
not send one.** `POST /api/v1/tickets/{id}/close` will take a `body` and mail it
to the requester as a reply. It is not exposed here on purpose: the module
instructions promise exactly **two** ways to write into a ticket —
`comment_on_ticket` for a public reply, `add_private_note` for an internal note
— and a third path that emails the requester from a tool called *close* is how an
agent mails something it thought it was filing. The split also keeps the two
permissions visible: a `body` on close costs `ticket.reply`'s authority on top
of `ticket.close`, and on an escalated ticket it costs `escalation:reply`
exactly as `comment_on_ticket` does. Nothing is lost by the omission — two calls
do the same thing, in an order you can see.

**And closing does not reopen — but something else does.** This tool accepts `4`
and `5` and nothing else. To move a ticket back to a working state, including
reopening one this tool resolved, use
[`set_ticket_status`](#set_ticket_status) with `status: 2`. The two tools are
disjoint on purpose and neither can reach the other's values.

> Close ticket 1.

```json
{
  "data": {
    "id": 1,
    "status": { "id": 5, "name": "closed" },
    "…": "…"
  }
}
```

> Close ticket 1 as solved and send the satisfaction survey.

```json
{
  "data": {
    "id": 1,
    "status": { "id": 4, "name": "solved" },
    "…": "…"
  }
}
```

---

## Troubleshooting

Every failure below produces one actionable sentence in the tool result. If you
are seeing a raw traceback instead, that is a bug — please file it.

### `EBTEQDESK_API_TOKEN is not set`

No token in the environment. Nothing was sent. Note that `claude mcp add
--env …` bakes the value into the registration, so changing your shell's
environment afterwards has no effect — re-register, or edit the entry.

🔴 **Seeing this while `WARNIDESK_API_TOKEN` is set is the expected 3.0.0
failure, not a bug.** The old name stopped being read; the message mentions it
precisely so this error is recognisable as the rename rather than as a missing
token. Rename the variable — the token value is unchanged. See
[Upgrading from `warnidesk-mcp`](#upgrading-from-warnidesk-mcp).

The server starts and lists its tools even with no token configured, on purpose:
a server that validated at startup would exit before the MCP handshake, and your
client would report only "failed to connect" with the real reason in a log you
would have to go looking for. You get the real sentence on the first tool call.

### `EBTEQDESK_API_TOKEN ends with '|', so it looks truncated`

Your shell ate the token at the pipe. Quote it: `EBTEQDESK_API_TOKEN='6|abc…'`.

### `EBTEQDESK_BASE_URL is not set` / `has no scheme`

Point it at the site root, scheme included: `http://localhost:8086`, not
`localhost:8086` and not `http://localhost:8086/api/v1`.

### `Could not reach Ebteqdesk at … (ConnectError)`

The request never got an HTTP reply. In order of likelihood: the server is not
running, the port is wrong, or you are on a different network/VPN than the host.
For a local Docker stack, check the port with `./docker/dev status` — each
worktree gets its own.

`(ReadTimeout)` instead of `(ConnectError)` means the host answered but was too
slow. Raise `EBTEQDESK_TIMEOUT`.

### `Ebteqdesk rejected the API token (401)`

The token is missing, expired or revoked. Mint a fresh one, update
`EBTEQDESK_API_TOKEN`, and **restart the MCP server** so it picks up the new
value — the client is built once per process.

### A 403 naming a scope — and why there are two of them

A scope works only while **both** halves hold: the key carries it, *and* the
account's role grants the ability behind it. Ebteqdesk refuses **identically**
either way — same status, same sentence, byte for byte — on purpose, so that
whoever holds a key cannot probe the owner's role with it.

The two causes need **opposite** fixes, so this client resolves the ambiguity
itself: on a 403 it makes one extra request to `GET /api/v1/user` (the only
unscoped endpoint, so any valid key reaches it) and compares `apiKey.requested`
with `apiKey.scopes`. You get one of the three messages below.

#### `This API key was not minted with the \`kb:read\` scope (403)`

The scope is absent from `apiKey.requested` — the key never had it. **Scopes are
fixed when a key is created and cannot be added to an existing key.** Mint a
*new* key that includes the named scope (Settings → API keys) and point
`EBTEQDESK_API_TOKEN` at it.

Granting the account more permissions will not help here.

#### `This API key carries the \`escalation-reports:read\` scope, but the account's role does not grant the ability behind it (403)`

The scope *is* in `apiKey.requested` and is missing from `apiKey.scopes` — the
key asked for it and the role does not back it. **Minting a new key will not
help; it would fail identically.** Ask an administrator to grant your role the
ability behind that scope (for `escalation-reports:read` that is
`bp_escalation.view`).

#### `The \`…\` scope did not resolve for this request (403)`

The diagnostic could not run — it was itself rate limited, the host became
unreachable, the token was revoked mid-check, or the identity payload was not
what was expected. The refusal you actually hit is reported unchanged, never
replaced by whatever went wrong during the check. Run the `whoami` tool yourself
and compare the two `apiKey` lists as described above.

The extra request happens **only on the 403 path**; successful calls never pay
for it, and a failed check never masks the original error.

### `This account's role does not hold the \`ticket.close\` ability (403)`

**A third 403, and it is not a key problem at all.** The write endpoints check a
role *ability* after the scope gate has already let the request through, and
this is that check failing. The body carries `required_ability` instead of
`required_scope`, which is how this client tells the two apart — never by
reading the message text.

The distinction is the whole point, because the remedies do not overlap:

| | `required_scope` | `required_ability` |
|---|---|---|
| What failed | the **credential** | the **account** |
| Possible causes | two (key, or role) | one |
| Diagnosable | yes, via `whoami` | nothing to diagnose |
| Remedy | a new key, **or** an administrator | an administrator, only |

**Minting a new key cannot fix this, with any scopes ticked, ever** — an ability
has no key half. Run `whoami` and look at `permissions` to see what the role
does hold, then ask an administrator to grant the named ability. The abilities
the write tools ask for are `ticket.create`, `ticket.reply`, `ticket.close` and
`bp_escalation.reply`.

Unlike the scope refusal, the server *names* the ability here. That is not an
inconsistency: your own permission list is already served in full by `whoami`,
so naming it discloses nothing you could not already read.

### `THIS TICKET IS ESCALATED, and replying to an escalated ticket needs the \`escalation:write\` scope`

You called `comment_on_ticket` with `ticket:write` on a ticket that turns out to
be escalated. The endpoint takes **either** `ticket:write` or
`escalation:write`, and an escalated ticket is charged `escalation:write` — so
the scope you hold is the wrong one for this ticket, not merely incomplete.

**Next time you can check first:** every ticket payload carries an `escalated`
boolean. Read that (not the nullable `escalated_at`) before replying — and note
that it stays true after the ticket is solved or closed, until somebody
de-escalates it, so a reply on your own resolved ticket can be refused this way.
This client
still spells the cause out rather than leaving you with an accurate but baffling
sentence about a scope you never asked for — the failure it prevents is
re-minting with `ticket:write`, the scope you already have, and hitting the
identical wall.

The reason is not red tape. Ebteqdesk silently downgrades an ordinary agent
reply on an escalated ticket into a **private internal note**. Rather than file
your requester-facing reply somewhere the requester will never see it, the API
refuses.

Fix: mint a key carrying both `ticket:write` and `escalation:write`. The rest of
the message tells you which half of *that* scope is missing, in the usual way —
it is appended below this explanation, not replaced by it.

### `Ebteqdesk refused this request (403) without naming a scope or an ability`

A 403 with neither field, so there is nothing to check and nothing to name. Run
`whoami` to see the account's permissions, and ask an administrator to grant the
ability named in the message.

### `Ebteqdesk is rate limiting this token (429)`

The `/api` throttle is 60 requests per minute per token by default. Wait and
retry, or fetch fewer pages. Nothing is retried automatically: a silent retry
inside a tool call turns a rate limit into an unexplained slow response.

### `Ebteqdesk answered 502 for /api/v1/user with text/html instead of JSON`

A proxy, load balancer or maintenance page answered instead of the application.
The message quotes the first bytes of what did arrive, which is usually enough to
recognise nginx, a captive portal, or a Laravel error page. If the status is a
`3xx`, something is redirecting `/api/v1` — a login wall or an http→https
upgrade; redirects are deliberately not followed, because following one would
turn this into a confusing HTML `200`.

### `Ebteqdesk rejected the arguments (422)`

A parameter was refused; the message names every offending field and the reason
for each. Most common on the read side: `per_page` above 100 on
`search_kb_articles`, or `date_to` earlier than `date_from` on
`get_escalation_report`. On the write side: `subject` under 3 characters, a
`requester` naming neither `id` nor `email`, a `category` slug that does not
exist, or a `close_ticket` status that is not 4 or 5.

Also common on the three ticket lists: **`per_page` above 20**. The ceiling is
enforced with a `422`, never a silently smaller page, so `errors.per_page` names
the limit and you can trust the size you asked for.

Values are passed through unvalidated on purpose — clamping or pre-checking them
locally would hide the mistake, invent a second differently-worded rule, and make
this client disagree with `curl`. A client that clamped `per_page` to 20 itself
would recreate exactly the silence the server refuses.

### `Ebteqdesk has nothing at that identifier (404)`

An unknown ticket-category slug (the message names it), an unknown KB slug (the
message does not, deliberately — see `get_kb_article` above), or **a ticket id
that is not assigned to you**.

That last case is a 404 rather than a 403 on purpose, and it applies to
administrators too: you may write to exactly the tickets you can read, and a
ticket that exists but is not yours is indistinguishable from one that never
existed. If a write 404s on an id you can see in the web UI, the ticket is
assigned to somebody else.

### `Ticket 4 is assigned to another agent and cannot be modified by you` (403)

**Nothing to fix, and do not retry.** This is the one refusal on the API that no
credential changes: the key resolved, the role was never the problem, and the
ticket simply belongs to somebody else. The write endpoints act only on tickets
assigned to your own account — **no permission overrides that**, `admin.access`
included. Minting a key does nothing; asking an administrator does nothing.

**The id is not stale either.** `list_escalations` is a *shared* queue: it
returns every unresolved escalated ticket in the installation, whoever it is
assigned to, so it correctly handed you an id you cannot act on. Read
`assignee` on the row to see whose it is. To move the ticket forward, a human
reassigns it or its assignee acts on it.

You will see the variant "*Ticket 4 is on the escalation queue but assigned to
nobody*" for an unpicked escalation — the row most worth flagging to a human.

This is the only ticket refusal that is a `403` rather than a `404`; everything
else the queue does not carry is still an indistinguishable
[404](#ebteqdesk-has-nothing-at-that-identifier-404).

### `ticket_id must be a positive integer ticket id such as 42`

Refused by this client before anything was sent. The write routes accept only
digits, so a slug or a name would match **no route at all** and Ebteqdesk would
answer with an HTML 404 — which this client would then report as "answered with
text/html instead of JSON", a true sentence about a proxy problem you do not
have. Ticket ids come from the `id` field of `list_tickets`; there is no lookup
by subject or reference number.

### The server shows as failed in `claude mcp list`

Run it by hand and look at stderr:

```bash
EBTEQDESK_BASE_URL=http://localhost:8086 EBTEQDESK_API_TOKEN='…' ebteqdesk-mcp
```

It should sit there silently waiting for JSON-RPC on stdin. If it exits, the
reason is on stderr. Anything that prints to **stdout** in this process corrupts
the protocol stream — that is why the package never prints there, and why
`ebteqdesk-mcp --help` writes its usage to **stderr**.

`ebteqdesk-mcp` takes no arguments beyond `--help` and `--version`. Passing
anything else prints usage to stderr and exits; it does not start the server.
That is so `--help` cannot leave you staring at a process that is really just
blocked reading a TTY.

```bash
ebteqdesk-mcp --version     # -> ebteqdesk-mcp 4.2.0, on stderr
```

⚠️ **If the command is not found, or the version comes back below 3.0.0, you
still have the old `warnidesk-mcp` package installed.** 3.0.0 ships no
`warnidesk-mcp` console script — the deprecated alias 2.x carried was removed —
so the old name resolving at all means the old *package* is still there, and
whatever your host launches under that name is 1.6.0. Uninstall it: see
[Upgrading from `warnidesk-mcp`](#upgrading-from-warnidesk-mcp).

---

## Development

```bash
cd mcp-server
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The unit suite mocks the HTTP layer with `httpx2.MockTransport` and needs no
running Ebteqdesk. The mock sits at the socket, not over the client's methods, so
the tests exercise real header construction, real URL building and real error
mapping.

The integration suite is marked `integration` and **deselects itself** unless
both variables are set (it reads `EBTEQDESK_*` only — the harness's `WARNIDESK_*`
fallback went with the client's, so the suite cannot run against a variable the
client itself no longer reads):

```bash
EBTEQDESK_BASE_URL=http://localhost:8086 \
EBTEQDESK_API_TOKEN='6|…' \
pytest -m integration
```

The **write** half of the integration suite needs a third variable on top of
those, because it creates tickets it cannot delete afterwards — there is no
delete endpoint — and pointing the read suite at staging must not silently file
tickets there:

```bash
EBTEQDESK_ALLOW_WRITES=1 pytest -m integration    # plus the two above
```

⏱️ **The integration suite paces itself and takes about 100 seconds.** That is
deliberate, not slowness to fix. The `/api` throttle is 60 requests per minute
per token and the suite makes roughly 85, so it sleeps between tests to stay
under it. Retrying instead would be wrong twice over: this client never retries
anywhere, and a write retried after a `429` can file a second ticket. Override
the gap with `EBTEQDESK_TEST_PACE_SECONDS=0` only if you have raised the limit.


Three further optional keys widen it, each skipping cleanly when unset. They
exist because the cases they cover need a *differently scoped* key than the one
under test, and no single key can exercise both sides of a refusal:

| Variable | A key that... |
|---|---|
| `EBTEQDESK_TEST_KEY_MISSING_SCOPE` | holds no write scopes — proves reads are not a write surface |
| `EBTEQDESK_TEST_TICKET_WRITE_ONLY_KEY` | holds `ticket:write` and not `escalation:write` — proves the escalated-comment refusal |
| `EBTEQDESK_TEST_NO_CLOSE_ABILITY_KEY` | holds `ticket:write` but whose **role** lacks `ticket.close` — proves the `required_ability` refusal |

### Layout

```
mcp-server/
├── pyproject.toml
├── README.md
├── src/ebteqdesk_mcp/
│   ├── __init__.py     public surface
│   ├── __main__.py     console entry point (stdout is the transport)
│   ├── _version.py     THE version string; the manifest reads this file too
│   ├── py.typed        PEP 561 marker; this package ships its own types
│   ├── config.py       environment -> Config, with redacting repr
│   ├── errors.py       one exception per failure mode, actionable messages
│   ├── client.py       the forty-two endpoints; payloads passed through verbatim
│   └── server.py       MCPServer + forty-two tools; descriptions carry the API rules
└── tests/
    ├── conftest.py                 shared fixtures; the mock sits at the SOCKET
    ├── test_client.py              the original read endpoints
    ├── test_config.py              environment handling, and the token never rendering
    ├── test_entrypoint.py          argument handling and stdout discipline
    ├── test_errors.py              every failure mode, and the words it produces
    ├── test_ticket_lists.py        the three ticket lists and their shared paging
    ├── test_ticket_detail.py       get_ticket / _comments / _attachment, incl. the image block
    ├── test_write_client.py        the ticket write endpoints
    ├── test_private_notes.py       add_private_note, client and MCP layer
    ├── test_reports_summary.py     the account-wide report and its two live gates
    ├── test_kb_writes.py           propose / update / review, and the staged 202
    ├── test_kb_structure.py        the tree read and the six category/folder writes
    ├── test_kb_reorder.py          reorder_kb_children and the two flat projections
    ├── test_kb_media.py            the one endpoint that reads a LOCAL FILE
    ├── test_kb_proposals.py        list_kb_proposals, and its installation-wide scope
    ├── test_admin_provisioning.py  the nine agent-provisioning tools
    ├── test_server_tools.py        tool registration, schemas, capabilities, descriptions
    ├── test_version.py             one version string, quoted in four places
    ├── test_stdio.py               the real subprocess, over the real protocol
    └── test_integration.py         opt-in, against a live Ebteqdesk
```

Three runtime dependencies, all of which the SDK would have pulled in anyway and
all of which are declared because this package **imports them directly**: a
package that imports a name it does not declare breaks silently the day its
dependency drops one of its own. See [Requirements](#requirements) for the
ranges and the versions this release was verified against.

- `mcp>=2.0,<3` — the official SDK. 2.0 is where the high-level helper became
  `MCPServer`, the successor to 1.x's `FastMCP`.
- `httpx2>=2.5,<3` — the HTTP client. The **2.x line**, a different PyPI
  distribution from `httpx` 0.x.
- `pydantic>=2.7,<3` — for `WithJsonSchema`, which is how the hand-written tool
  argument schemas are attached to a signature that cannot express them.

**The version lives in exactly one file**, `src/ebteqdesk_mcp/_version.py`. The
build backend reads it (`[tool.hatch.version]`), and so do `__version__`,
`serverInfo.version` on the wire, and the outgoing `User-Agent`. `tests/
test_version.py` fails if any of the four drift. Bump the **major** when a tool's
observable contract changes — a renamed argument, a changed default — not only
when the code does.

### Capabilities: tools only

`initialize` advertises `tools` and nothing else. The SDK derives the capability
block from which request handlers are registered, and `MCPServer` registers
`prompts/*` and `resources/*` whether or not anything is behind them — so the
obvious tools-only server announces two capabilities that answer `[]`, and every
client spends two round trips per session finding that out. `server.py` withdraws
those handlers; `prompts/list` and `resources/list` now answer *method not
found*, which is the honest answer for a method this server does not implement.

The Laravel MCP server (`app/Mcp/WarnideskServer.php` on
`feat/m2-integration-build` — the path and class name as they were on that
branch, left un-renamed because a citation that points at a file which never
existed is not a citation) made the same call first and for the same reason. This is one rule with two implementations, not two
opinions. **If prompts or resources are ever implemented here, delete the
corresponding entries from `UNIMPLEMENTED_METHODS` — not the mechanism.**

### Why payloads are passed through verbatim

Nothing is renamed, flattened or enriched on the way out. The API's keys are the
external contract (`requester`, not `customer_contact`; `key`, not `id`), and a
client that tidies them becomes a second, undocumented contract that drifts from
the first. A tool result here should be comparable byte-for-byte with a `curl` of
the same URL.

The same rule applies in the other direction. Request bodies use the API's own
field names, and `requester` is sent as the nested object the endpoint
documents rather than flattened into `requester_id` / `requester_email`
arguments — a request vocabulary that differs from the documented one is the
same drift problem, and it costs you the ability to compare a tool call against
a `curl`.

### The four 403s

Ebteqdesk answers four structurally different refusals with the same status
code, and this client keeps them apart by the **field present in the body** —
never by reading the message, which is not an interface and has already been
rewritten once.

| Body carries | Exception | Cause | Remedy |
|---|---|---|---|
| `reason: "ticket_not_assigned"` | `TicketNotAssignedError` | the ticket belongs to another agent | **none** — reassign the ticket |
| `required_scope` | `KeyScopeError` | the key was never minted with it | mint a new key |
| `required_scope` | `RoleScopeError` | the key has it, the role does not back it | an administrator |
| `required_scope` | `ScopeError` | could not be established | check `whoami` by hand |
| `required_ability` | `AbilityError` | the account's role lacks the ability | an administrator |
| neither | `PermissionError_` | refused without naming anything | check `whoami` by hand |

`TicketNotAssignedError` is the odd one out and is tested for first. It is not
about authority at all — the credential resolved and the role was never asked —
so it has no remedy a caller can act on and **must never be retried**. It exists
only because `list_escalations` is a shared queue that hands out ids the caller
cannot write to; every other unreachable ticket is still a plain `404`.

The first three are one server response split by a **second request** to
`GET /api/v1/user` — the only unscoped endpoint — comparing `apiKey.requested`
(the key) against `apiKey.scopes` (the key∩role intersection). That extra request
happens **only on the 403 path**, never recurses, and can never mask the original
refusal: every way it can fail returns the error you actually hit.

`AbilityError` costs no second request. There is nothing to compare — the answer
is already "the role", known from the shape of the body rather than inferred.
