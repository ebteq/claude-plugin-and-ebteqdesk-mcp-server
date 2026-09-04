"""The package version, in ONE place.

Four things quote it and they must never disagree:

  - `pyproject.toml`'s `[project] version`, read from this file by Hatchling
    (`[tool.hatch.version]`), so the wheel and the source agree by construction
    rather than by somebody remembering.
  - `ebteqdesk_mcp.__version__`, the public attribute.
  - `MCPServer(version=…)`, which a client reads out of `initialize` as
    `serverInfo.version`. It is what lets a host tell a renamed tool argument
    from an outage, so it has to move when a tool's CONTRACT moves — not only
    when the code does.
  - the `User-Agent` this client sends, which is how an Ebteqdesk operator
    identifies MCP traffic in an access log.

Before this module those were three hardcoded strings and a fourth in the
manifest; the User-Agent had already drifted from the manifest once. Nothing
imports the package root to read it (that would be circular — `__init__`
imports `client` and `server`), which is why this is its own module and not a
constant in one of them.

Semantics: this is the SERVER's version. Bump the minor when a tool is added or
a description materially changes, the major when an existing tool's arguments or
defaults change in a way a caller can observe.

⚠️ READ "arguments or defaults" AS SHORTHAND, NOT AS THE LIST. The rule was
written when those were the only observable surfaces a tool had; the governing
question, which every entry below actually applies, is whether a caller written
against the previous version CAN BE BROKEN. A tool that changes which STATUS it
answers, or that turns an error into a success, or that grows a response key a
caller must branch on, is a major on that question and a minor on the literal
wording. See 4.0.0, which is the first release where the two readings disagreed.
"""

from __future__ import annotations

__all__ = ["__version__"]

#: 1.0.0 was the release promoted out of the M2 integration branches. It was a
#: major bump from 0.1.0 rather than a 0.2: `close_ticket`'s default `status`
#: changed from 4 (SOLVED, which emails the requester's address a satisfaction survey) to
#: 5 (CLOSED, which emails nobody), and a caller relying on the old default now
#: gets a different outcome. That is a breaking contract change on a write tool,
#: and semver says so out loud so nobody has to read a changelog to find it.
#:
#: 1.1.0 — the ticket-thread surface. A MINOR bump, and that is the claim being
#: made: tools were ADDED (`get_ticket` and its conversation, the attachment
#: image block, the account-wide report) and not one existing tool's arguments,
#: defaults or return shape moved. `close_ticket` still defaults to 5. A caller
#: written against 1.0.0 keeps working unchanged against 1.1.0, which is exactly
#: what the minor promises; the day that stops being true, this goes to 2.0.0.
#:
#: 1.2.0 — the KB structure surface. A MINOR bump on the same claim as 1.1.0:
#: five tools were ADDED (`list_kb_tree`, and create/update for both categories
#: and folders) and nothing existing moved. Checked rather than assumed — every
#: tool present in 1.1.0 carries the same arguments, the same defaults and the
#: same return shape here, `close_ticket` included.
#:
#: 🔴 THIS BUMP IS NOT OPTIONAL BOOKKEEPING, because this branch merges to
#: `staging` on its own. Between it landing and the next one, a host handshakes
#: against a server exposing 24 tools; if that server still announced 1.1.0 —
#: which is 19 tools — `serverInfo.version` would be answering a question about
#: capability with a stale string, and the docstring above says that string is
#: exactly how a host tells a changed contract from an outage.
#:
#: 1.3.0 — private notes and KB reordering. Four tools ADDED (`add_private_note`,
#: `reorder_kb_children`, `list_kb_categories`, `list_kb_folders`), taking the
#: server to 28, and again nothing existing moved: same arguments, same defaults,
#: same return shapes, `close_ticket` still 5.
#:
#: ⚠️ `add_private_note` is a WRITE, and a new write tool is still only a minor.
#: The major is reserved for a change a caller can be BROKEN by, and nothing here
#: can break one — code written against 1.2.0 cannot be calling a tool that did
#: not exist in it. The risk a new write carries is to the DESK, not to the
#: caller's code, and semver does not encode that; the tool's own description
#: does, which is where the survey-email warning on `close_ticket` lives too.
#:
#: 1.4.0 — the KB structure DELETE. Two tools ADDED (`delete_kb_category`,
#: `delete_kb_folder`), taking the server from 28 to 30, and nothing existing
#: moved: every tool present in 1.3.0 carries the same arguments, the same
#: defaults and the same return shapes here, `close_ticket` still defaulting to
#: 5. Checked against the tool list rather than assumed.
#:
#: ⚠️ A DESTRUCTIVE WRITE IS STILL ONLY A MINOR, and this is the loudest case of
#: the rule `add_private_note` recorded at 1.3.0, so it is worth restating rather
#: than pointing at. The major is reserved for a change a caller can be BROKEN
#: by, and nothing here can break one: code written against 1.3.0 cannot be
#: calling a tool that did not exist in it, so no existing call site behaves
#: differently after this bump. The risk these two tools carry is to the DESK —
#: a category or a folder removed with no undo — and not to the caller's code.
#: Semver has no way to encode that, and pretending it does by bumping the major
#: would say "your calls may have broken", which is false, while saying nothing
#: about the thing that actually changed. What carries the danger is the tools'
#: own descriptions, which is where `close_ticket`'s survey-email warning lives
#: too, and the server `instructions`, which now name fifteen write tools and
#: single out these two as the only ones that destroy anything.
#:
#: 🔴 AND THE BUMP IS STILL NOT OPTIONAL. `serverInfo.version` is how a host
#: tells a changed contract from an outage; a server announcing 1.3.0 while
#: exposing a delete tool would be answering a question about capability with a
#: stale string.
#:
#: 1.5.0 — the working-state change. ONE tool ADDED (`set_ticket_status`), taking
#: the server from 30 to 31 and its write half from 15 to 16, and nothing
#: existing moved. Checked against the tool list rather than assumed: every tool
#: present in 1.4.0 carries the same arguments, the same defaults and the same
#: return shapes here, `close_ticket` still defaulting to 5. `close_ticket`'s
#: DESCRIPTION changed — it used to say reopening was impossible and now points
#: at the new tool — but its signature, its default and its response did not, so
#: no existing call site behaves differently. A description change is exactly
#: what the docstring above calls a reason to move the minor.
#:
#: ⚠️ AN INVERSION WORTH RECORDING. 1.3.0 and 1.4.0 both argued that a new write
#: tool is only a minor because "the risk is to the DESK, not to the caller's
#: code". This is the first write tool whose risk to the desk is genuinely
#: SMALL: it sends no mail to the requester, no agent notification and no domain event,
#: and the server treats a repeat call as a no-op, so it is also the first write
#: on this server that is safe to retry apart from `reorder_kb_children`. That
#: is precisely why it is tempting to under-document — a tool that cannot mail
#: anybody reads as a tool that needs no warning.
#:
#: 🔴 IT DOES NEED ONE, AND ITS HAZARD IS QUEUE DISRUPTION. `status=2` on a
#: solved ticket REOPENS it: the row goes back on somebody's queue, and if the
#: ticket is escalated it reappears on the SHARED BP queue carrying its original
#: `escalated_at`, so it sorts to the top of a list ordered longest-first. The
#: status is reversible; the `Status updated:` history entry each change appends
#: is not. Nothing here mails the requester — the danger is putting work back in
#: front of a human who thought it was finished.
#:
#: 1.6.0 — the media upload. ONE tool ADDED (`upload_kb_media`), taking the
#: server from 31 to 32 and its write half from 16 to 17, and nothing existing
#: moved. Checked against the tool list rather than assumed: every tool present
#: in 1.5.0 carries the same arguments, the same defaults and the same return
#: shapes here, `close_ticket` still defaulting to 5.
#:
#: 🔴 WHAT IS GENUINELY NEW HERE IS NOT THE WRITE. It is that this is the FIRST
#: TOOL ON THIS SERVER THAT READS THE USER'S OWN FILESYSTEM. Every tool before
#: it built its request entirely out of arguments the model already held, so the
#: blast radius of a wrong call was always the DESK: a ticket filed, an email
#: sent, a category removed. `upload_kb_media` opens a path on the machine the
#: server runs on and puts those bytes on the wire, so a wrong call can move a
#: file OFF that machine — a risk to the CALLER rather than to the helpdesk, and
#: the first of its kind in this package.
#:
#: ⚠️ SEMVER CANNOT SAY THAT, AND THIS IS THE THIRD TIME THIS FILE HAS HAD TO
#: RECORD SOMETHING SEMVER CANNOT SAY (1.3.0's new write, 1.4.0's destructive
#: one, and now this). It is still a MINOR, on the same rule: the major is
#: reserved for a change a caller can be BROKEN by, and code written against
#: 1.5.0 cannot be calling a tool that did not exist in it, so no existing call
#: site behaves differently. Bumping the major to signal "be careful" would say
#: "your calls may have broken", which is false, while saying nothing about the
#: thing that actually changed. What carries this hazard is the tool's own
#: description — upload only a file the user NAMED, never sweep a directory —
#: and the server `instructions`, which now name seventeen write tools and
#: single this one out as the only one that touches the local filesystem.
#:
#: ⚠️ IT IS ALSO NOT SAFE TO RETRY, and that is worth stating beside 1.5.0's
#: inversion. `set_ticket_status` was the first write whose danger was small;
#: this one goes back the other way for a reason peculiar to it — a retried
#: upload does not fail and does not duplicate an action a human sees, it
#: silently stores a SECOND COPY under a NEW ULID that no article references and
#: that the prune sweep will not touch for seven days. The retry looks like it
#: worked. Only two writes here remain safe to replay: `reorder_kb_children` and
#: `set_ticket_status`.
#:
#: 🔴 AND THE BUMP IS STILL NOT OPTIONAL. `serverInfo.version` is how a host
#: tells a changed contract from an outage; a server announcing 1.5.0 while
#: exposing a tool that reads local files would be answering a question about
#: capability with a stale string.
#:
#: 2.0.0 — the Ebteqdesk rename. THE FIRST MAJOR SINCE 1.0.0, AND THE FIRST BUMP
#: ON THIS SERVER THAT ADDS NO TOOL AT ALL: the tool list is the same thirty-two,
#: with the same arguments, the same defaults and the same return shapes, and
#: `close_ticket` still defaults to 5. Every previous entry in this file argued
#: that a change which cannot break a caller's code is a minor. This one is the
#: inverse case, and it is why the major exists.
#:
#: 🔴 WHAT BROKE IS THE PACKAGE'S TWO NON-TOOL INTERFACES, AND BOTH ARE PUBLIC.
#: (1) The distribution, the import module and the console script were renamed
#: `warnidesk-mcp`/`warnidesk_mcp` -> `ebteqdesk-mcp`/`ebteqdesk_mcp`, so an
#: existing `~/.claude.json` pointing at `"command": "warnidesk-mcp"` and any
#: `import warnidesk_mcp` stop resolving on a clean install. (2) The environment
#: variables moved from `WARNIDESK_*` to `EBTEQDESK_*`. The exported classes
#: moved with them (`WarnideskClient` -> `EbteqdeskClient`, `WarnideskError` ->
#: `EbteqdeskError`), which breaks anyone using the HTTP half as a plain client.
#:
#: ⚠️ THE FALLBACKS SOFTEN (2) BUT DO NOT UNDO IT, and that distinction is the
#: whole reason this is not a minor. `config.py` still reads `WARNIDESK_*` when
#: the new name is absent, and `[project.scripts]` still ships a `warnidesk-mcp`
#: alias — so an operator who upgrades and changes nothing but the install still
#: gets a working server. But a fallback is a deprecation window, not a
#: guarantee: it emits a stderr notice, it is documented as removable at the
#: next minor, and the import name and the class names have no fallback at all.
#: Announcing 1.7.0 here would tell a host "nothing you depend on moved", which
#: is false for anyone importing the module or pinning the distribution.
#:
#: 🔴 AND `serverInfo.name` MOVED TOO, from "warnidesk" to "ebteqdesk". That is
#: separate from the MCP server KEY in a host's config, which stays `warnidesk`
#: — it is what prefixes the tool names (`mcp__warnidesk__*`) and what the
#: Claude Code plugin is named. AT 2.0.0 that plugin lived in a DIFFERENT
#: repository, so renaming the key was a coordinated change across two repos and
#: deliberately not part of this one. (That is history: the two repositories were
#: merged afterwards and the plugin now sits beside this package, at
#: `../../plugin/` — see the 4.2.0 note below. The reasoning here is preserved as
#: the record of why the key lagged the rename, not as a description of today.)
#: See the README.
#:
#: 2.1.0 — the proposal list. ONE tool ADDED (`list_kb_proposals`), taking the
#: server from thirty-two to thirty-three and its READ half from fifteen to
#: sixteen. THE WRITE HALF DOES NOT MOVE: it is still seventeen, and the new
#: tool changes no review state, no timestamp and no content. Checked against
#: the tool list rather than assumed — every tool present in 2.0.0 carries the
#: same arguments, the same defaults and the same return shapes here, and
#: `close_ticket` still defaults to 5.
#:
#: ⚠️ A MINOR IMMEDIATELY AFTER A MAJOR, AND THE CLAIM IS THE ORDINARY ONE. 2.0.0
#: was the inverse case this file exists to record — a bump that added no tool
#: and broke two non-tool interfaces. This is the normal shape again: code
#: written against 2.0.0 cannot be calling a tool that did not exist in it, so
#: no existing call site behaves differently. Nothing in the 2.0.0 deprecation
#: window closes here either — `config.py` still reads `WARNIDESK_*`, and
#: `[project.scripts]` still ships the `warnidesk-mcp` alias. Removing that alias
#: is its own decision and is deliberately not folded into this bump.
#:
#: 🔴 WHAT THE NEW TOOL CARRIES THAT SEMVER CANNOT SAY — the fourth time this
#: file has had to write that sentence, and the first time for a READ. Every
#: previous entry's unsayable risk was a write's: a row filed, an email sent, a
#: category removed, a local file put on the wire. This one is a DISCLOSURE
#: SHAPE. `list_kb_proposals` is the first tool on this server that ENUMERATES
#: drafts: every other KB read either serves the public corpus or answers about
#: one article the caller already named. It hands a `kb:write` key every draft
#: title, excerpt and rejection note in the installation, including proposals
#: another integration made.
#:
#: That widens nothing the same credential could not already reach — it can read
#: any article by reference and rewrite any draft — but it converts "must already
#: hold a reference" into "can list", and that conversion IS the feature: a
#: reference lives only in the create response, so an integration restarted with
#: no memory could not find its own rejections at all. It is bounded by a scope
#: whose role side is `kb.manage` (Administrator and Supervisor), i.e. people
#: already looking at the whole review queue in a browser.
#:
#: ⚠️ AND IT IS SHARED, THE WAY `list_escalations` IS. The list is
#: installation-wide and cannot be narrowed: an API key identifies an ACCOUNT,
#: not an agent. A description saying "your proposals" would be acted on, so
#: that tool's first paragraph says the opposite in as many words — the same
#: mitigation, in the same place, as the one `list_escalations` has carried
#: since 1.0.0.
#:
#: 🔴 AND THE BUMP IS STILL NOT OPTIONAL. `serverInfo.version` is how a host
#: tells a changed contract from an outage; a server announcing 2.0.0 while
#: exposing thirty-three tools would be answering a question about capability
#: with a stale string.
#:
#: 3.0.0 — the deprecation window CLOSES. THE SECOND MAJOR THAT ADDS NO TOOL,
#: and like 2.0.0 it is a major for what it took away rather than what it
#: shipped. The tool list is the same thirty-three as 2.1.0, with the same
#: arguments, the same defaults and the same return shapes; `close_ticket` still
#: defaults to 5.
#:
#: 🔴 WHAT BROKE IS EVERY COMPATIBILITY SHIM 2.0.0 PROMISED WOULD BE TEMPORARY,
#: removed together rather than one per release. (1) `config.py` NO LONGER READS
#: `WARNIDESK_*`. (2) `[project.scripts]` no longer ships the `warnidesk-mcp`
#: console script. Both were documented in the 2.0.0 entry above as a
#: deprecation window; this is that window closing, on purpose and in one bump
#: so an operator has one upgrade to do rather than two.
#:
#: ⚠️ BOTH FAILURES ARE QUIET AND NEITHER NAMES THE RENAME, which is the part
#: worth writing down. A stale `"command": "warnidesk-mcp"` fails as `command
#: not found`, which a host reports as a server that crashed at startup. A stale
#: `WARNIDESK_API_TOKEN` is worse: the server STARTS, completes the handshake,
#: and then refuses every call as unconfigured, so it presents as a broken
#: server rather than a stale config. The only breadcrumb either operator gets
#: is the text of the "is not set" errors in `config.py`, which is why those
#: messages name the removed variable explicitly and why a test asserts they
#: keep doing so.
#:
#: 🔴 AND THE SERVER KEY MOVED, WHICH IS THE ONE PART THIS PACKAGE CANNOT
#: ENFORCE. The 2.0.0 entry above recorded the key staying `warnidesk` as a
#: coordinated change deliberately deferred across two repositories. This is
#: that coordination landing: the plugin repository (then
#: `github.com/ebteq/claude-plugin`, since merged into this one and renamed
#: `claude-plugin-and-ebteqdesk-mcp-server`) renamed its
#: plugin to `ebteqdesk` in its own 2.0.0, and the desk's Settings -> API keys
#: page now prints `claude mcp add ebteqdesk`. The key lives in the HOST's
#: config, so an install created before this keeps working under `warnidesk`
#: and nothing here can change that — the plugin's skills accept either prefix
#: for one release, and that is the entire migration mechanism.
#:
#: ⚠️ THE VERSION LINE IN THE PLUGIN'S SKILLS IS NOT AUTOMATICALLY TRUE OF THIS
#: RELEASE. Those files carry "Verified against ebteqdesk-mcp <version>", set by
#: whoever last re-read the tool descriptions in this file's sibling server.py.
#: A rename does not re-verify them. If that line names an older version than
#: this one, it is doing its job — treat it as the staleness signal it was built
#: to be, not as bookkeeping to tidy up.
#:
#: 🔴 AND DO NOT BUMP IT WITHOUT DOING THE READING. Those files now live in this
#: repository (`../../plugin/skills/*/SKILL.md`), so bumping the line is a
#: one-line edit in the same pull request — which makes it easy to bump as
#: bookkeeping and thereby destroy the only signal a reader has. The line asserts
#: that a human re-read server.py against the skills at that version. Move it
#: only when that happened.
#:
#: 4.0.0 — the published-article edit STOPS BEING A REFUSAL. THE THIRD MAJOR
#: THAT ADDS NO TOOL, and the first one whose break is inside a TOOL rather than
#: beside it. The tool list is the same thirty-three as 3.0.0, with the same
#: arguments and the same defaults; `close_ticket` still defaults to 5. Nothing
#: was renamed and no shim was removed.
#:
#: 🔴 WHY THIS IS NOT A MINOR, GIVEN THE RULE AT THE TOP OF THIS FILE. That rule
#: says minor for a description change, major for "an existing tool's arguments
#: or defaults chang[ing] in a way a caller can observe". Read literally, no
#: argument and no default moved here and this would be a 3.1.0. Read for what
#: every entry above actually applied — the major is for a change a caller can
#: be BROKEN by — it is unambiguously a major, and the literal reading is the
#: one that is wrong. The rule's wording names arguments and defaults because
#: those were the only observable surfaces a tool had when it was written; this
#: is the first release where a tool's OUTCOME moved without its signature
#: moving, and the wording simply did not anticipate it.
#:
#: WHAT MOVED: `update_kb_article` against a PUBLISHED article used to fail —
#: 409, surfaced as `ConflictError`, reaching an MCP host as `is_error` with
#: "Retrying will not help". It now SUCCEEDS: 202, staging a pending revision
#: for a human, with a new top-level `revision` key beside `data`. An error
#: became a success and the response grew a key. Every branch a caller wrote
#: against the old behaviour is now dead code, and the code path that runs
#: instead is one it was never written for.
#:
#: 🔴 AND THE NEW SUCCESS IS QUIETER THAN THE OLD FAILURE, WHICH IS THE ACTUAL
#: HAZARD. The 409 was loud and unmissable. The 202 hands back `data` — the LIVE
#: article, byte-for-byte unchanged, NOT the submitted text — so a caller that
#: reads `data` back to confirm its edit sees the old title, the old body, and
#: very often `translations: []` where its new Chinese version should be. The
#: honest reading of that payload is "nothing happened", and it is wrong. A
#: caller that acts on it and resends does not queue a second revision either:
#: there is one row per article, so the resend REPLACES what was staged, and
#: replaces a rejected revision's note along with it. A client that used to be
#: told "no" now silently reports success for an edit that has not happened yet.
#: That is the exact shape of break the major exists to announce.
#:
#: ⚠️ SEMVER STILL CANNOT SAY THE INTERESTING PART — the fifth time this file has
#: had to write that sentence, and the first time the major is the RIGHT number
#: anyway. 4.0.0 says "your calls may have broken", which is true. It does not
#: say "and the replacement behaviour looks like a no-op", which is the part
#: that costs a user a live help article going un-updated for a week. That lives
#: where every previous unsayable hazard has: in `update_kb_article`'s own
#: description, which now opens on the two branches and states outright that
#: `data` on a 202 is what customers are still reading; in
#: `get_kb_article_review`, which teaches the three-way `revision` reading; and
#: in the server `instructions`, which name the 202 as the most misreadable
#: response on this server.
#:
#: ⚠️ `get_kb_article_review` CHANGED TOO, AND THAT HALF IS ONLY A MINOR'S WORTH.
#: It grew the same `revision` key, unconditionally, null when there is no
#: staged edit. Purely additive: every field a 3.0.0 caller read is still there
#: with the same meaning. It rides along on this major rather than earning one.
#:
#: 🔴 THE THIRD READING OF `revision` IS THE ONE THAT WILL BE GOT WRONG.
#: `revision.state` is never `"approved"` — approving APPLIES the revision and
#: DELETES the row — so `null` is ambiguous between "nothing was ever staged"
#: and "staged, approved, and now live". A caller that reads null as "still
#: waiting" will poll forever on work that finished. No field disambiguates it;
#: the article's own text does. Both tool descriptions say so in as many words.
#:
#: ⚠️ `ConflictError` SURVIVES WITH NO PRODUCER, deliberately. It is exported
#: from the package root, so deleting it would break an `except ConflictError`
#: in anyone using the HTTP half as a plain client — a second break, in the same
#: release, for no benefit. Its docstring and `api_error_for`'s 409 message no
#: longer assert the removed rule: a message that confidently said "the article
#: is published" would be a wrong diagnosis of whatever the next 409 actually
#: is. It is now the generic state conflict, and it says so.
#:
#: 🔴 AND THE BUMP IS STILL NOT OPTIONAL. `serverInfo.version` is how a host
#: tells a changed contract from an outage; a server announcing 3.0.0 while
#: answering 202 to a call that release documented as a hard 409 would be
#: answering a question about capability with a stale string — and here the
#: stale string would point at the OPPOSITE behaviour, not merely an older one.
#:
#: 4.1.0 — the BILINGUAL edit becomes expressible. A MINOR, and the claim is the
#: ordinary one this file has made since 1.1.0: nothing an existing caller wrote
#: can break. The tool list is the same thirty-three, `close_ticket` still
#: defaults to 5, and `locale=` on `propose_kb_article` / `update_kb_article`
#: builds a byte-identical request body to the one it built at 4.0.0 — asserted
#: by test, not assumed. Everything added is an OPTIONAL argument defaulting to
#: None, and eight arguments nobody passes are eight keys nobody sends.
#:
#: 🔴 WHAT WAS BROKEN, AND WHY IT WAS ONLY VISIBLE AFTER 4.0.0. Both tools took
#: ONE `locale` per call, so two languages meant two calls, and the per-locale
#: rows simply accumulated on a draft. 4.0.0 made a published article's edit
#: STAGE A REVISION instead of failing — and `kb_article_revisions` is
#: `unique(kb_article_id)`. Two calls therefore stopped accumulating and started
#: REPLACING: `en` then `zhcn` leaves a revision holding only `zhcn`, which the
#: missing-version guard correctly refuses because applying it would take the
#: article out of the English help centre, and the one flag that gets past that
#: guard performs that removal. There was no sequence of calls on this server
#: that added a Chinese version to a published English article.
#:
#: ⚠️ THE IRONY IS RECORDED HERE ON PURPOSE. 4.0.0 was a careful rewrite of
#: exactly these two descriptions — it documented the 202, the misreadable
#: `data`, the one-revision-per-article rule — and it did not notice that the
#: one workflow the feature existed to enable was unreachable through its own
#: tool. A release can be right about every sentence it writes and still ship a
#: surface that cannot do the thing. The fix is an argument, not a paragraph.
#:
#: WHAT MOVED: `en_title`, `en_body`, `en_seo_title`, `en_seo_description`,
#: `zhcn_title`, `zhcn_body`, `zhcn_seo_title`, `zhcn_seo_description` on both
#: write tools, folded into ONE `translations` object so a single request — and
#: therefore a single revision — carries both languages. `locale=` stays as the
#: one-language form. On the client half, `translations=` takes the server's own
#: nested mapping, including the `null` DELETE the API refuses, because the
#: client's shape must be able to say what the endpoint's shape can say.
#:
#: ⚠️ EIGHT FLAT ARGUMENTS RATHER THAN ONE NESTED `translations` DICT, AND THAT
#: IS THE DESIGN DECISION IN THIS RELEASE. A tool's arguments are filled in by a
#: model reading a JSON schema, and a free-form object schema names no locale
#: key, no field and no depth; a misspelt `zh-cn` validates, is dropped by the
#: server's validator, and returns 2xx having stored nothing. Putting the locale
#: in the ARGUMENT NAME puts it in the schema, where the SDK refuses an unknown
#: one by name. The cost is a wider surface and the supported locale set
#: hardcoded in two more places — the same bet `Literal["en", "zhcn"]` already
#: made on `locale`. Written up beside `_kb_versions` in server.py.
#:
#: 🔴 AND THE DESCRIPTIONS CHANGED IN A WAY THE RULE AT THE TOP CALLS A MINOR ON
#: ITS OWN. Every sentence that taught "call it once per locale" is gone, on
#: both tools. That advice was merely wasteful on a draft and is a silent
#: language loss on a published article, so leaving it while shipping the fix
#: would have kept the bug alive in the only documentation a model reads.
#:
#: 4.2.0 — AGENT PROVISIONING. NINE TOOLS AND A NEW SCOPE AREA, AND STILL A
#: MINOR. The claim is the ordinary one this file has made since 1.1.0: nothing
#: an existing caller wrote can break. Every tool that existed at 4.1.0 keeps
#: the same name, arguments, defaults and return shape; the nine are additive
#: and reach endpoints no earlier release could call.
#:
#: WHAT MOVED: `list_agents`, `get_agent`, `list_roles`, `list_groups` and
#: `list_api_keys` read the account roster; `create_agent`, `update_agent`,
#: `issue_api_key` and `revoke_api_key` change it. The tool list goes
#: thirty-three -> forty-two, reads sixteen -> twenty-one, writes seventeen ->
#: twenty-one. Two new scopes, `admin:read` and `admin:write`, both backed by
#: the `admin.access` ability.
#:
#: 🔴 AND A MINOR IS THE RIGHT SIZE DESPITE THESE BEING THE MOST DANGEROUS TOOLS
#: ON THE SERVER, which is worth stating out loud because the instinct says
#: otherwise. The rule at the top of this file is about an OBSERVABLE CONTRACT
#: MOVING, not about blast radius — 1.2.0 added the KB structure writes and
#: 2.2.0 added two irreversible deletes, both on this same reasoning, and the
#: paragraph at 84 already argues it for the deletes. An existing integration
#: that never calls the nine new tools cannot tell 4.2.0 from 4.1.0 on the wire.
#:
#: ⚠️ WHAT AN OPERATOR STILL HAS TO DO DELIBERATELY, and it is why the release
#: note is not "nothing to see": these tools need a key carrying `admin:read` or
#: `admin:write`, and NO KEY IN EXISTENCE CARRIES EITHER. Scopes are fixed when
#: a key is minted, so every key issued before this release lists nine tools it
#: is refused. The new scopes are also the only ones on the server that CANNOT
#: be obtained from the API itself — `issue_api_key` subtracts them
#: unconditionally, so they come from a signed-in human at Settings > API keys
#: and from nowhere else.
__version__ = "4.2.0"
