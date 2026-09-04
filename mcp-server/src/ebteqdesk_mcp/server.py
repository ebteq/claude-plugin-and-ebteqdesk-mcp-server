"""The MCP server: forty-two tools over the Ebteqdesk v1 REST API, stdio transport.

Built against the official `mcp` Python SDK, 2.x, using its high-level helper
`mcp.server.MCPServer` — the 2.0 successor to 1.x's `FastMCP`. `@mcp.tool()`
derives each tool's input schema from the function signature and its description
from the docstring, so the docstrings below are not commentary: they are the
text a model reads before deciding how to call the tool, and the place where the
API's counter-intuitive rules have to live.

TOOL DESCRIPTIONS CARRY THE RULES THE PAYLOAD CANNOT.
A model that sees `{"escalated": 7, "total": 5}` will "correct" it to a
percentage unless it has been told, at the point of use, that those two numbers
range over different date columns. Three such rules exist on the escalation
report and one on the knowledge base; all four are stated in full on the tools
that return them, and none of them is silently worked around in code.

ONE MORE THING ONLY A DESCRIPTION CAN CARRY: `list_escalations` returns a
SHARED queue — every escalated ticket in the installation, not the caller's own
— while every other ticket tool on this server is ownership-scoped. Nothing in
the payload distinguishes the two, since both render the identical
`TicketResource`. A description that said "your escalated tickets" would be
acted on, so that tool's first paragraph says the opposite in as many words.

🔴 TWENTY-ONE OF THE FORTY-TWO TOOLS WRITE, AND THE DESCRIPTION IS THE ONLY
GUARDRAIL. Every write below emails the requester's address, notifies a team, or
files a row
that no endpoint on this API can delete — with ONE exception, `set_ticket_status`,
which contacts nobody and whose first line says so precisely because every other
write tool's first line is a warning and a model skimming has to tell it apart.
Seven of them (`create_kb_category`,
`update_kb_category`, `delete_kb_category`, `create_kb_folder`,
`update_kb_folder`, `delete_kb_folder`, `reorder_kb_children`) restructure a live
knowledge base.

🔴 ONE TOOL READS THE USER'S OWN FILESYSTEM, AND IT IS THE FIRST. `upload_kb_media`
takes a LOCAL PATH, opens it, and sends the bytes to Ebteqdesk. Every other tool
on this server builds its request out of arguments a model already holds, so the
worst a wrong call can do is write the wrong thing to the desk. This one can send
the wrong thing OFF THE MACHINE, which is a different party's risk and is not
something semver, a scope or a status code expresses anywhere. The mitigation is
entirely in that tool's description: upload only a file the user NAMED, never
sweep a directory, never guess a path.

🔴 AND ITS RETURN VALUE IS LOAD-BEARING IN A WAY NO OTHER TOOL'S IS. The `url` it
answers with is the ONLY string that references the uploaded image; a fabricated
`/kb/media/{ULID}` renders as a broken image in a live knowledge base that
signed-out visitors read, silently, because the author is authenticated and sees
it render. A model
that guesses one has not made a formatting mistake — it has published a defect
only readers can see.

🔴 TWO OF THOSE SEVEN DESTROY STRUCTURE. `delete_kb_category` and
`delete_kb_folder` are the only tools on this server that remove anything, and
nothing on this API puts a removed row back. They are REFUSALS rather than
cascades — a category holding folders and a folder holding articles are both
refused, with the count named — which is the property that keeps them from
becoming the article-delete this API deliberately does not offer. A model must
say what it is about to remove BEFORE it calls one, and must not "clear the way"
by deleting children first without being asked to. There is deliberately NO
`dry_run` argument:
Ebteqdesk has no dry-run mode, so a client-side one could only describe the
request it would have sent — validating nothing, consulting no policy, and
reporting success for calls the server would refuse. A fake guardrail that
reads like a real one is worse than none, because it is trusted. What replaces
it is the first line of each write tool's description saying, in words a model
cannot skim past, exactly what happens and to whom. If a write's consequence
changes, that sentence is the thing that must change with it.

⚠️ FIVE TOOLS NEED A WRITE SCOPE AND WRITE NOTHING. `get_kb_article_review` and
`list_kb_proposals` are gated on `kb:write` because their corpus is every
article including drafts — the same corpus PATCH addresses — and `list_kb_tree`,
`list_kb_categories` and `list_kb_folders` because the structure tree carries
ids and `agents`-only folders, i.e. the AUTHORING view. (The last two are
PROJECTIONS over `list_kb_tree`, not endpoints of their own; see the block above
them.) `kb:read` is deliberately the scope with no role side, gating the PUBLIC
help corpus, so mounting any of them on it would widen that corpus — and
`list_kb_proposals` is the loudest case, because it ENUMERATES drafts rather
than reading one the caller already named. Counting scopes and counting
consequences give different answers here, so both numbers are stated: TWENTY-ONE
tools change state, TWENTY-SIX require a write scope. `get_kb_article_review`
exists precisely BECAUSE it changes nothing.

🔴 ONE READ TOOL IS INSTALLATION-WIDE THE WAY `list_escalations` IS, AND FOR THE
SAME UNFIXABLE REASON. `list_kb_proposals` returns every article carrying a
review state in the whole installation — another integration's proposals, and
human-written articles somebody later revised through this API. An API key
identifies an ACCOUNT and not an agent, and two agents may share one, so the
server cannot narrow it and a description saying "your proposals" would be acted
on. That tool's first paragraph says the opposite in as many words.

🔴 ONE WRITE ANSWERS SUCCESS FOR A CHANGE THAT DID NOT HAPPEN, AND THE PAYLOAD
ARGUES AGAINST ITSELF. `update_kb_article` against a PUBLISHED article stages a
pending revision and answers 202 with `{"data", "revision"}` — where `data` is
the LIVE article, unchanged, and NOT the text that was submitted. Every other
2xx on this server echoes what the call did; this one echoes what the call
deliberately did not do. A model that reads `data` back to confirm its edit sees
its own text missing (`data.translations` is routinely `[]` while the submitted
version sits in `revision`) and concludes the call failed — and a resend
REPLACES the staged revision, because there is one row per article. Nothing in
the payload flags this; the only discriminator is the presence of the top-level
`revision` key, and only the description can say so. This replaced a flat 409
refusal, so anything written against the old behaviour is not merely stale but
inverted: what used to be an error is now a success that means less than it
looks like.

⚠️ AND THE READ THAT GOES WITH IT HAS A THREE-WAY ANSWER WHOSE THIRD VALUE IS
SILENT. `get_kb_article_review` now carries `revision` too: `pending` is
waiting, `rejected` carries the note, and `null` is AMBIGUOUS — either nothing
was ever staged or a staged edit was approved, applied, and its row deleted.
`state` is never `"approved"`, so "approved" has no representation at all and
has to be inferred from the article text. That tool's description says so at
length because no field in the payload can.

⚠️ TWO WRITES ARE SAFE TO RETRY, AND ONLY TWO. `reorder_kb_children` assigns
positions by index, so replaying it leaves the same order; `set_ticket_status`
is a no-op when the ticket already holds the status it is sent, which the server
guards and which is why that endpoint is a PUT. The blanket "never retry a write
that timed out" below is otherwise absolute, and both exceptions are named here
so they are documented properties rather than guesses a model makes about some
other tool. `upload_kb_media` is emphatically NOT one of them: every call stores
a new object under a new ULID, so a blind retry leaves a duplicate nothing
references.

🔴 NO DEFAULT ON A WRITE TOOL MAY CARRY THE BIGGER SIDE EFFECT.
`close_ticket` used to default to `status: 4` (SOLVED), which fires Ebteqdesk's
satisfaction survey — a real email to the requester's address — so an agent
that closed a ticket without naming a status mailed somebody as a consequence of
a parameter it never set. It now defaults to `5` (CLOSED), which mails nobody,
and `4` has to be asked for. The rule generalises: where two argument values
differ in who they contact, the default is the quiet one and the loud one is
opt-in. See CLOSE_STATUSES.

🔴 THE SCHEMA HAS TO STAND ON ITS OWN; THE DOCSTRING IS NOT ALWAYS READ.
A tool description is prose a model may skim. The JSON Schema is what a client
VALIDATES against and, for some clients, all they show. So every argument whose
legal values are a fixed set says so in the schema — `requester` as a `oneOf` of
its two real shapes, `priority` and `status` as enums with per-value meanings —
rather than as `{"type": "integer"}` with the truth left in the prose. The
observed failure was exactly that: a first call built from the schema alone,
refused by the API for a value the schema had described as any integer.

🔴 ONE TOOL DOES NOT RETURN JSON. `get_ticket_attachment` returns a real MCP
IMAGE CONTENT BLOCK, preceded by a text block of metadata. It is annotated
`-> Image` rather than `-> list[Any]` and that annotation is load-bearing: an
`Image` return type makes the SDK skip output-schema generation entirely, while
`list[Any]` builds a wrapped output schema and then fails trying to
`model_dump(mode="json")` an `Image`. Do not "tidy" the annotation.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal, Mapping

from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from pydantic import WithJsonSchema

from ._version import __version__
from .client import EbteqdeskClient
from .config import Config
from .errors import EbteqdeskError

__all__ = ["mcp", "run", "WITHDRAWN_METHODS"]

mcp: MCPServer = MCPServer(
    # `serverInfo.name` on the wire.
    #
    # 🔴 THIS IS NOT THE SERVER KEY, AND SETTING IT HERE DOES NOT MOVE THE KEY.
    # The key lives in the HOST's config (`mcpServers.<key>` in
    # `~/.claude.json`), is chosen by whoever registers this process, and is
    # what prefixes the tool names a model sees —
    # `mcp__ebteqdesk__list_tickets`. This process cannot rename it; all it can
    # do is stop printing the old one in the instructions it hands out.
    #
    # The recommended key became `ebteqdesk` in 2.0.0, coordinated with the
    # Claude Code plugin (which renamed in the same release) and with the desk's
    # own Settings -> API keys page, which is what prints the `claude mcp add`
    # command most installs are created from. That coordination was the whole
    # reason the key stayed `warnidesk` in 1.x: the plugin's skills are what call
    # the prefixed names, and the plugin was then a SEPARATE repository, so
    # renaming the key without shipping that repo in the same breath would have
    # silently broken every one of them.
    #
    # ✅ THAT SPLIT IS GONE. The plugin now lives beside this package, at
    # `../../plugin/`, so a change here and the SKILL.md that describes it go in
    # one pull request. A future rename of this name, or of a tool below, no
    # longer needs a two-repository dance — it needs the matching skill edit in
    # the same commit.
    #
    # ⚠️ INSTALLS REGISTERED BEFORE 2.0.0 STILL USE THE `warnidesk` KEY and
    # nothing here changes that — the host config is not ours to edit. The
    # plugin's skills therefore accept either prefix for one release. Do not
    # add a "detect and warn" path for it: this process cannot see its own key.
    name="ebteqdesk",
    # `serverInfo.version` on the wire. Single-sourced from _version.py, which
    # explains why it must move when a TOOL CONTRACT moves and not only when
    # the code does.
    version=__version__,
    instructions=(
        "Access to an Ebteqdesk ticketing install over its REST API: forty-two "
        "tools in total — twenty-one tools that read and twenty-one that WRITE. "
        "⚠️ THIS IS AN INTERNAL DESK, AND THAT DESCRIBES WHO FILES TICKETS — NOT "
        "WHO CAN RECEIVE AN EMAIL. Tickets are raised by staff inside the "
        "organisation, so a ticket's `requester` is a colleague rather than an "
        "outside customer, and no inbound reply from the public is expected. "
        "Outbound mail still leaves on three live paths: every PUBLIC reply "
        "posted with `comment_on_ticket` is emailed to the requester's address "
        "and NO FLAG ANYWHERE STOPS IT, resolving a ticket as SOLVED can email "
        "that address a satisfaction survey, and the requester's own portal link "
        "accepts replies without a login. So never put into a public reply "
        "anything you would not want sent by mail and read on that portal. The "
        "knowledge base is a separate surface again: its `public` articles are "
        "served to signed-out visitors on the help portal. "
        "Start with `whoami` to see which account the configured token belongs "
        "to and which scopes its API key resolves to — almost every other tool "
        "is scoped to that account. Ticket tools see and touch only tickets "
        "ASSIGNED to it, and the knowledge base READ tools return only "
        "published, publicly-visible articles even for an administrator. "
        "🔴 WHETHER AN ARTICLE IS PUBLICLY VISIBLE IS THE ARTICLE'S OWN "
        "SETTING WHERE A HUMAN HAS GIVEN IT ONE, AND ITS FOLDER'S WHERE "
        "THEY HAVE NOT. An article overrides its folder in BOTH "
        "directions, so an `agents` folder in `list_kb_tree` may hold one "
        "article `search_kb_articles` returns, and a `public` folder may "
        "hold one it does not. Never conclude that a particular article is "
        "or is not reachable from the `visibility` of the folder it sits "
        "in — that inference was sound until 2026-08-19 and is not any "
        "more. A folder's level is what its articles INHERIT, and it is "
        "exactly what anything THIS API files there will get, because no "
        "tool here can set a per-article override. "
        "🔴 THE ONE EXCEPTION IS `list_escalations`, which returns the SHARED "
        "escalation queue: every unresolved escalated ticket in the "
        "installation, whoever it is assigned to, including tickets assigned to "
        "nobody. It is not 'your' escalations — read each row's `assignee` to "
        "see which are the token's own. "
        "Every ticket carries an `escalated` boolean; that, and not the "
        "nullable `escalated_at`, is the escalation state. "
        "Ticket lists return at most 20 rows per page; asking for more is an "
        "error rather than a quietly smaller page. "
        "The lists carry NO conversation: to read what was actually said on a "
        "ticket — the requester's message, agent replies, internal notes, the "
        "history and the attachments — call `get_ticket`, which is the one tool "
        "that returns comment bodies. Entries marked `kind: \"note\"` are "
        "PRIVATE INTERNAL NOTES and must never be repeated into a public reply. "
        "🔴 THERE ARE TWO WAYS TO WRITE INTO A TICKET AND THEY ARE NOT "
        "INTERCHANGEABLE: `comment_on_ticket` posts a PUBLIC reply the requester "
        "receives by email, while `add_private_note` files an INTERNAL note that "
        "the requester never sees. If the text is an observation, a finding or a "
        "handover, it is `add_private_note`; reaching for the reply tool and "
        "hoping is how an internal remark gets mailed to the requester. Neither can "
        "be edited or deleted afterwards. "
        "`get_ticket_attachment` returns an attached image as an image block, "
        "downscaled; video attachments are refused rather than returned. "
        "🔴 `propose_kb_article` NEEDS A `kb_folder_id` AND ONLY THE STRUCTURE "
        "TOOLS RETURN ONE — the article payloads carry `{slug, name}` pairs and "
        "no ids at all. `list_kb_tree` is the nested view; `list_kb_categories` "
        "and `list_kb_folders` are FLAT PROJECTIONS OVER THE SAME ONE CALL and "
        "are no cheaper, so pick whichever shape you want to read and call it "
        "ONCE — never in a loop. Call one before filing an article, and call one "
        "to discover that an empty knowledge base needs a category and a folder "
        "first. "
        "🔴 `reorder_kb_children` TAKES THE WHOLE ORDERED SIBLING LIST, NEVER A "
        "DELTA. Post every id in the set, in the order you want them; a partial "
        "list is a 422 and writes nothing, not a partial reorder. It is also one "
        "of the only TWO writes here that are safe to retry, the other being "
        "`set_ticket_status`. "
        "Every tool except `whoami` needs an API key scope: `ticket:read`, "
        "`escalation:read`, `kb:read`, `reports:read`, "
        "`escalation-reports:read` and `admin:read` to read, `ticket:write`, "
        "`escalation:write`, `escalation:reply`, `kb:write` and `admin:write` "
        "to write. "
        "🔴 `escalation:write` AND `escalation:reply` ARE DIFFERENT SCOPES AND "
        "THE DIFFERENCE IS WHO SEES THE RESULT: `write` files an INTERNAL note "
        "on an escalated ticket and hands the ticket back, while `reply` sends "
        "a message the REQUESTER receives by email. Many accounts hold the first "
        "and not the second on purpose — escalation hands the requester "
        "conversation to whoever is working the escalation — so a refusal "
        "naming `escalation:reply` usually means 'note it instead, or "
        "de-escalate', not 'mint a new key'. Counting scopes and counting consequences "
        "give different answers: TWENTY-SIX tools require a write scope while "
        "only TWENTY-ONE change state, because `get_kb_article_review`, "
        "`list_kb_proposals`, "
        "`list_kb_tree`, `list_kb_categories` and `list_kb_folders` are reads "
        "whose corpus — drafts, ids and internal folders — is the authoring "
        "one, and that lives behind `kb:write`. A "
        "key minted without a scope refuses with a message saying which is "
        "missing and whether the fix is a new key, an administrator, or "
        "neither. "
        "⚠️ `kb:write` resolves only while the key carries it AND the account's "
        "role holds `kb.manage`, which is granted to ADMINISTRATOR AND "
        "SUPERVISOR ONLY — so an agent- or developer-role account cannot use "
        "any knowledge base write tool, or `get_kb_article_review`, "
        "`list_kb_proposals`, "
        "`list_kb_tree`, `list_kb_categories` or `list_kb_folders`, whatever "
        "key is minted for it. "
        "🔴 `list_kb_proposals` IS THE SECOND SHARED LIST ON THIS SERVER, after "
        "`list_escalations`: it returns every article awaiting or carrying a "
        "review verdict in the INSTALLATION, including proposals another "
        "integration made and human-written articles somebody later revised "
        "through this API. It is not 'your' proposals — an API key identifies an "
        "account and not an agent — so recognise your own rows by `title` or "
        "`reference`. It is how a rejection is FOUND when the `reference` from "
        "the create response is gone; `get_kb_article_review` reads one you "
        "still hold, and neither ever requires a speculative "
        "`update_kb_article`, which re-queues the article and erases the "
        "reviewer's note. "
        "🔴 THE TWENTY-ONE WRITE TOOLS — `create_ticket`, `comment_on_ticket`, "
        "`add_private_note`, `escalate_ticket`, `de_escalate_ticket`, "
        "`set_ticket_status`, `close_ticket`, `propose_kb_article`, "
        "`update_kb_article`, "
        "`create_kb_category`, `update_kb_category`, `delete_kb_category`, "
        "`create_kb_folder`, `update_kb_folder`, `delete_kb_folder`, "
        "`reorder_kb_children`, `upload_kb_media`, `create_agent`, `update_agent`, "
        "`issue_api_key`, `revoke_api_key` — change a "
        "live helpdesk. They send email to real requesters and notifications to "
        "real agents, and NOTHING they do can be undone through this API: there "
        "is no delete-ticket, no delete-comment, no delete-note and no "
        "delete-article tool, and the two deletes there ARE cannot be reversed. "
        "There is "
        "no dry-run mode and no preview. Confirm with the user before calling "
        "one on their behalf, and never retry one that timed out — a write that "
        "appears to have failed may well have landed. There are exactly TWO "
        "exceptions: `reorder_kb_children`, which assigns positions by index "
        "and is therefore idempotent, and `set_ticket_status`, which the server "
        "treats as a no-op when the ticket already holds the status sent. "
        "🔴 TICKET STATUS IS SPLIT ACROSS TWO TOOLS AND NEITHER CAN REACH THE "
        "OTHER'S VALUES: `set_ticket_status` owns the WORKING states — 1 new, "
        "2 open, 3 pending, 8 waiting on customer — a name this package inherits "
        "from the server-side API enum (`waitingOnCustomer`) and cannot rename; "
        "on this desk it means waiting on the requester — and is also how a resolved "
        "ticket is REOPENED (`status: 2`, which additionally costs the "
        "`ticket.close` ability); `close_ticket` owns 4 (solved) and 5 (closed) "
        "and is the ONLY tool that can send the satisfaction survey. "
        "Asking either for the other's statuses is a 422. `set_ticket_status` "
        "sends no mail and no notification at all. "
        "🔴 TWO OF THE SEVENTEEN DESTROY STRUCTURE: `delete_kb_category` and "
        "`delete_kb_folder` remove a REAL row from the live knowledge base and "
        "nothing on this API puts it back. Both are REFUSALS rather than "
        "cascades — a category still holding folders, or a folder still holding "
        "articles, is refused with the count named, and the row survives. That "
        "refusal is a safety property, not an obstacle to route around: there is "
        "no delete-article tool at all, so a model must never empty a folder or "
        "a category to make a delete go through unless the user asked for "
        "exactly that. Name what is about to be removed, and get the user's "
        "agreement, before calling either. "
        "🔴 OUTBOUND EMAIL TO THE REQUESTER COMES FROM THREE PLACES AND NO DEFAULT "
        "TRIGGERS ONE: `comment_on_ticket` always mails the requester's address "
        "(that is what it is for, and there is no flag that suppresses it), "
        "`close_ticket` mails only when asked for `status: 4` "
        "(its default, 5, is silent), and `create_ticket` mails only if asked to "
        "open a ticket already at `status: 4`. Nothing else on this server sends "
        "mail to the requester — `add_private_note` is the deliberately silent "
        "counterpart to `comment_on_ticket`, and the ten knowledge base "
        "writes mail nobody. An internal desk does not change any of that: the "
        "requester is a colleague, and the mail still goes out. "
        "The two article writes cannot PUBLISH. `propose_kb_article` always "
        "lands a draft held for human review, and `update_kb_article` lands one "
        "too whenever the article is still a DRAFT — re-queuing it even if it "
        "had already been approved. "
        "🔴 BUT `update_kb_article` AGAINST A PUBLISHED ARTICLE DOES SOMETHING "
        "ELSE ENTIRELY, AND IT IS THE MOST MISREADABLE RESPONSE ON THIS SERVER. "
        "It does NOT edit the article and no longer refuses it either (the 409 "
        "that used to answer is gone). The edit is STAGED as a pending revision "
        "for a human to approve, the live article is untouched, and the reply "
        "is 202 carrying `{\"data\", \"revision\"}` where `data` IS THE OLD "
        "LIVE ARTICLE — not what was submitted. `data.translations` is commonly "
        "`[]` on it while the submitted version sits in `revision`. Read the "
        "presence of the top-level `revision` key, never a change in `data`, to "
        "tell a staged edit from an applied one, and tell the user their change "
        "is WAITING rather than done. There is ONE revision row per article, so "
        "a second call REPLACES the first instead of queueing it. "
        "Use `get_kb_article_review` to read either verdict — checking one with "
        "another write destroys the note you were reading, on both surfaces. "
        "On that read, `revision` is `pending` (waiting), `rejected` (read "
        "`note`), or null — and null is AMBIGUOUS: nothing was ever staged, or "
        "a staged edit was APPROVED, applied and its row deleted. It is never "
        "`approved`. Tell the two nulls apart by looking for your text in "
        "`data`, not by assuming. "
        "The structure writes cannot set VISIBILITY: every folder created "
        "through this API is `agents` (internal), there is no argument to change "
        "that on create or update, and nothing filed into such a folder reaches "
        "a reader outside the desk until a human changes it in the Ebteqdesk UI. Renaming a "
        "category or folder RE-DERIVES its slug and changes its portal URL, "
        "unlike an article's, which is frozen at first publish. "
        "🔴 AN ARTICLE WITH A SCREENSHOT TAKES TWO CALLS, IN THIS ORDER. "
        "`upload_kb_media` takes a path to a file ON THE USER'S OWN MACHINE, "
        "sends the bytes, and returns a `url` like `/kb/media/01J…`. Put that "
        "url — exactly as returned — into the article `body` as "
        "`<img src=\"/kb/media/{ulid}\" alt=\"…\">` and then call "
        "`propose_kb_article` or `update_kb_article`; the SAVE is what links "
        "the file to the article, so an upload nobody references stays "
        "unattached and is swept away after seven days. "
        "🔴 NEVER INVENT A `/kb/media/` URL. A ULID you made up renders as a "
        "broken image in a live knowledge base signed-out visitors read and you will not "
        "see it break, because you are signed in. No url from this tool means no "
        "image. "
        "⚠️ IT IS ALSO THE ONE TOOL THAT READS THE LOCAL FILESYSTEM: upload only "
        "a file the user explicitly named, never sweep a directory, and never "
        "retry a timed-out upload — each call stores a fresh copy under a new "
        "ULID. Accepted types are JPG, PNG, WebP, GIF, MP4 and WebM, decided by "
        "reading the file's CONTENT and not its extension, capped at 10 MB for "
        "an image and 50 MB for a video. "
        "🔴 NINE TOOLS DO NOT TOUCH TICKETS OR ARTICLES AT ALL — THEY DECIDE WHO "
        "MAY ACT. `list_agents`, `get_agent`, `list_roles`, `list_groups` and "
        "`list_api_keys` read the account roster; `create_agent`, `update_agent`, "
        "`issue_api_key` and `revoke_api_key` change it. All nine need "
        "`admin:read` or `admin:write`, which resolve only for an account whose "
        "role holds `admin.access` — ADMINISTRATOR ONLY among the built-in roles, "
        "so an agent-, supervisor- or developer-role key is refused all nine "
        "whatever it carries. A LEGACY WILDCARD KEY IS REFUSED ALL NINE TOO: "
        "`*` deliberately does not expand to the agent-provisioning area, "
        "because those keys were minted before it existed. Both admin scopes "
        "have to be ticked explicitly when the key is created. "
        "🔴 TWO OF THEM RETURN A SECRET EXACTLY ONCE AND IT CAN NEVER BE READ "
        "AGAIN: `create_agent` answers with `generatedPassword` when it generated "
        "the password, and `issue_api_key` answers with `plainTextToken`. The desk "
        "stores only a one-way hash of each, no email is sent, and neither this "
        "API nor the Ebteqdesk web UI can show either a second time — so pass the "
        "value to the user in your very next message or it is lost and the account "
        "or key has to be reset by hand. "
        "🔴 `issue_api_key` CAN NEVER GRANT `admin:read` OR `admin:write`, by any "
        "caller, to any agent, however privileged the calling key is. Those come "
        "only from a signed-in human at Settings > API keys in the web UI, for "
        "their own account. A refusal naming `never_issuable` is final: do not "
        "retry with a different agent, role or key. Nor can it grant a scope the "
        "CALLING key does not itself resolve — read `meta.issuableScopes` from "
        "`get_agent` or `list_api_keys`, which is that whole calculation already "
        "done. And it REFUSES rather than narrowing: one unacceptable scope makes "
        "the whole call a 422 that creates nothing, so a success means the key "
        "carries exactly what was asked for. "
        "🔴 AND NO TOOL HERE CAN CREATE OR PROMOTE AN ADMINISTRATOR. Both "
        "`create_agent` and `update_agent` refuse any role granting "
        "`admin.access` with a 422 — `list_roles` marks those rows "
        "`assignable: false`. Such an account would sign in with the password "
        "`create_agent` hands back and mint its own provisioning key, so "
        "allowing it would let an API key create its own successor. Asked to "
        "make somebody an admin, say that a signed-in administrator does it at "
        "Settings > Agents and do not hunt for a role that slips through. "
        "⚠️ THE ROLE IS THE PERMISSIONS DECISION, NOT THE GROUPS. Which scopes a "
        "key can resolve follows the ABILITIES OF THE ROLE ITS OWNER IS ON, so "
        "read `list_roles`' `permissions` before choosing one; groups are purely "
        "organisational and grant nothing. And there is NO delete-agent tool and "
        "no password-reset or email-change tool — deleting an agent reassigns "
        "history across the whole desk and resetting a password reveals it once on "
        "a screen, so all three stay with a person in the Ebteqdesk web UI. "
        "This server offers TOOLS ONLY — no prompts and no resources — and says "
        "so in `initialize`, so do not spend a round trip asking for either."
    ),
)


# --------------------------------------------------------------------------- #
# Argument schemas the function signature cannot express
# --------------------------------------------------------------------------- #
#
# `@mcp.tool()` derives each input schema from the annotations, and for most
# arguments that is exactly right: `page: int | None` really is "an optional
# integer" and nothing is lost. Three arguments are not like that. `requester`
# has TWO legal shapes and the signature can only say `dict`; `priority` and
# `status` accept a fixed handful of integers whose MEANINGS are the whole
# point, and the signature can only say `int`.
#
# The cost of leaving those to the signature was a real first-call failure: a
# client that reads schemas and not docstrings built `{"requester": "a@b.c"}`
# and a priority of 5, because `{"type": "object", "additionalProperties": true}`
# and `{"type": "integer"}` are what the API had told it to expect.
#
# TWO MECHANISMS, AND THEY DO DIFFERENT JOBS.
#
#   Literal[...] in the signature  ENFORCES. Pydantic validates the call against
#                                  it before the tool body runs, so a priority
#                                  of 5 is refused here, by name, instead of
#                                  costing a round trip and coming back as a 422.
#   WithJsonSchema(...)            DESCRIBES. It replaces the schema pydantic
#                                  would have emitted with the flat, documented
#                                  one below — `anyOf: [{enum}, {null}]` is
#                                  correct but is not what a schema-reading
#                                  client finds easy to act on.
#
# 🔴 They are two statements of one fact, so they can drift. `test_server_tools`
# asserts the advertised `enum` equals `typing.get_args()` of the annotation for
# every one of them; adding a value to one and not the other fails there.

#: `Ticket::allPriorities()`. The server's own default is 2 and this client does
#: not restate it — an omitted `priority` is left out of the request body.
PRIORITIES: dict[int, str] = {
    1: "low — no one is blocked",
    2: "normal — the server's default when `priority` is omitted",
    3: "high",
    4: "blocker — work is stopped",
}

#: `Ticket::selectableStatuses()`, in the order the API documents them. Merged
#: (6) and spam (7) are absent because they are outcomes of other actions and
#: the server refuses them on create: a ticket cannot be OPENED as "merged with
#: what?".
#:
#: ⚠️ 8's NAME IS THE SERVER'S, NOT THIS PACKAGE'S. The API enum is
#: `waitingOnCustomer` and is left exactly as the API spells it — this client
#: cannot rename a server-side enum, and a label that did not match what
#: Ebteqdesk returns would be worse than an inherited one. On this internal desk
#: it means waiting on the requester.
CREATE_STATUSES: dict[int, str] = {
    1: "new — the server's default when `status` is omitted",
    2: "open — an agent is on it",
    3: "pending — waiting on somebody internal",
    8: "waiting on customer (note the 8: it is an OPEN state despite sorting above solved)",
    4: "solved — 🔴 RESOLVES THE TICKET ON CREATION AND EMAILS THE REQUESTER'S ADDRESS THE SATISFACTION SURVEY",
    5: "closed — resolves the ticket on creation, sends no survey",
}

#: `Ticket::openStatuses()` — the four WORKING states, and the whole vocabulary
#: PUT /tickets/{id}/status accepts. Same wording as CREATE_STATUSES above,
#: including 8's parenthetical, because they are the same four values meaning the
#: same four things; the server-default note is dropped because this argument has
#: no default and no omitted case.
#:
#: 8's name is the API's own `waitingOnCustomer`, inherited and not renamed here.
#:
#: 🔴 4 AND 5 ARE ABSENT, AND THAT IS THE POINT OF THE TOOL. Resolving a ticket
#: is `close_ticket`, which is the ONE tool that can send the satisfaction
#: survey — and therefore the one place that warning has to live.
#: 6 (merged) and 7 (spam) are absent for CREATE_STATUSES' reason: outcomes of
#: other actions, never a choice.
WORKING_STATUSES: dict[int, str] = {
    1: "new — nobody has picked it up yet",
    2: "open — an agent is on it. Sending this to a solved or closed ticket REOPENS it",
    3: "pending — waiting on somebody internal",
    8: "waiting on customer (note the 8: it is an OPEN state despite sorting above solved)",
}

#: The two statuses POST /tickets/{id}/close accepts, LOUD END FIRST so nobody
#: reads past the safe one. See the 🔴 note in the module docstring for why the
#: tool defaults to 5 while the API still defaults to 4.
CLOSE_STATUSES: dict[int, str] = {
    5: "closed — resolves the ticket and SENDS NOTHING. This tool's default.",
    4: "solved — resolves the ticket AND EMAILS THE REQUESTER'S ADDRESS a "
       "satisfaction survey asking them to rate it. Ask for this only when a "
       "survey is wanted.",
}

#: The tool's own default for `close_ticket`, and the whole of the #132 fix.
#: NOT the API's default, which is still 4 — changing that would move behaviour
#: under the web UI and every other client.
CLOSE_WITHOUT_SURVEY = 5


#: What a `null` means on the two optional enums: "I am not choosing", which is
#: the same as omitting the key. Spelled out because the schema has to admit it —
#: pydantic emits `"default": null` for an argument defaulted to None, and a
#: schema whose `default` its own `enum` rejects is one a strict client is right
#: to complain about.
_OMITTED = "omitted — the server applies its own default"


def _enum_schema(values: Mapping[int, str], lead: str, *, optional: bool = False) -> dict[str, Any]:
    """A flat integer enum that carries what each value MEANS.

    `enum` is the keyword every validator understands, so it is what decides
    valid from invalid. `oneOf`/`const` carries the per-value prose, because
    JSON Schema has no `enumDescriptions` and a client that only renders the
    schema has nowhere else to read it from. Both are emitted: they agree by
    construction (one `const` branch per `enum` member, from one dict), a client
    honouring only `enum` validates correctly, and a client honouring both gets
    the descriptions for free.

    The meanings are ALSO folded into `description`, which is the one field no
    client ignores.

    `optional=True` admits `null` in all three places at once. It is one flag
    rather than three edits precisely so that the `type`, the `enum` and the
    `oneOf` cannot end up disagreeing about whether null is legal.
    """
    described = " ".join(f"{value} = {text.rstrip('.')}." for value, text in values.items())

    schema: dict[str, Any] = {
        "type": "integer",
        "enum": list(values),
        "description": f"{lead} {described}",
        "oneOf": [{"const": value, "description": text} for value, text in values.items()],
    }

    if optional:
        schema["type"] = ["integer", "null"]
        schema["enum"] = [*values, None]
        schema["oneOf"] = [*schema["oneOf"], {"const": None, "description": _OMITTED}]
        schema["description"] += " Omit the key, or send null, to take that default."

    return schema


#: The requester, in the two shapes the API documents — and NOT as
#: `{"type": "object", "additionalProperties": true}`, which is what the
#: signature alone produced and which tells a caller nothing at all.
#:
#: `type: object` stays at the top so a bare string is rejected outright (that
#: much is also enforced by the `dict` annotation, and both matter: one refuses
#: the call, the other tells a schema-reading client not to make it).
REQUESTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "The requester the ticket is about",
    "description": (
        "The person who raised the ticket. Ebteqdesk is an INTERNAL desk, so this "
        "is normally a member of staff rather than an outside customer — but the "
        "address here is a LIVE MAIL DESTINATION either way: every public reply "
        "an agent posts on the ticket is emailed to it, and there is no setting "
        "that suppresses that. "
        "Exactly one of two shapes. `{\"id\": 12}` names an existing contact and "
        "creates nothing — take the id from `requester.id` on any ticket returned "
        "by `list_tickets`. `{\"email\": \"ada@example.com\", \"name\": \"Ada "
        "Lovelace\"}` is find-or-create matched on the EMAIL, so an unknown "
        "address CREATES A CONTACT RECORD; `name` is used only in that case and "
        "will never rename an existing contact. Prefer the id form when you have "
        "an id. If both keys are somehow sent the server takes `id` and ignores "
        "`email`."
    ),
    "oneOf": [
        {
            "title": "By id — an existing contact",
            "description": "Exact, and creates nothing.",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": (
                        "A `customer_contacts.id`, as served in `requester.id` on "
                        "any ticket payload."
                    ),
                },
            },
            "required": ["id"],
        },
        {
            "title": "By email — found, or CREATED",
            "description": (
                "Matched on the address, which is the identity column. If no "
                "contact has it, one is inserted."
            ),
            "properties": {
                "email": {
                    "type": "string",
                    "format": "email",
                    "description": (
                        "The requester's email address — on this internal desk, "
                        "normally a staff mailbox. It is the address Ebteqdesk "
                        "mails every public reply on the ticket to, so an "
                        "unknown one here both CREATES A CONTACT RECORD and "
                        "sets where that mail goes. Max 190 characters."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Applied ONLY when the contact is created. Optional — the "
                        "server files a new contact as \"Unknown\" without it — but "
                        "a mistyped name next to a KNOWN address is silently "
                        "ignored rather than an error, so send it only when it is "
                        "right. Max 190 characters."
                    ),
                },
            },
            "required": ["email"],
        },
    ],
}


# --------------------------------------------------------------------------- #
# Client lifecycle
# --------------------------------------------------------------------------- #
#
# The client is built LAZILY, on the first tool call, and never at import or at
# startup. That is a deliberate UX choice: if the token or base URL is missing,
# a server that validated at startup would exit before the MCP handshake, and
# Claude Code would report only "failed to connect" with the real reason buried
# in a log file the user does not know exists. Deferring it means the server
# always connects, `/mcp` always lists every tool, and the first tool call
# returns the exact sentence naming the missing environment variable.

_client: EbteqdeskClient | None = None
_client_lock = asyncio.Lock()


async def _get_client() -> EbteqdeskClient:
    """The shared client, built on first use.

    The lock matters: MCP tool calls are concurrent, and two racing first calls
    would otherwise build two clients and leak the connection pool of whichever
    one lost the assignment.
    """
    global _client

    async with _client_lock:
        if _client is None:
            _client = EbteqdeskClient(Config.from_env())

        return _client


async def _close_client() -> None:
    global _client

    async with _client_lock:
        if _client is not None:
            await _client.aclose()
            _client = None


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #


@mcp.tool()
async def whoami() -> dict[str, Any]:
    """Identify the Ebteqdesk account and API key behind the configured token.

    Call this first when you are unsure what the token can see: every other tool
    is scoped to this account and to this key's scopes.

    Requires no API key scope — it is the one endpoint every valid key can
    reach, which is what makes it the way to diagnose a scope refusal.

    Returns `{"data": {...}}` with:
      - `id`, `uuid`, `name`, `email` — the account
      - `role` — `{"id", "name", "key"}`
      - `permissions` — the ROLE's abilities, e.g. `bp_escalation.view`
      - `apiKey` — `{"id", "name", "scopes", "requested", "expiresAt"}`, or null
        if the request was not authenticated by a bearer token

    TWO SEPARATE GATES, AND THE DIFFERENCE IS THE WHOLE POINT OF THIS TOOL.
    A scope works only while BOTH the key carries it AND the account's role
    grants the ability behind it:

      - `apiKey.requested` — what the KEY carries, as minted
      - `apiKey.scopes`    — the intersection of that with the role: what
        actually resolves right now

    So a scope present in `requested` but missing from `scopes` is one the
    account's role no longer backs — a new key would not help. A scope missing
    from `requested` is one the key was never minted with — a new key is exactly
    what is needed. `permissions` and `apiKey.scopes` are different vocabularies
    and do not map one-to-one; compare the two `apiKey` lists, not those.

    The other tools already run this check for you when they hit a 403, so you
    normally see the conclusion rather than having to do this yourself.
    """
    return await _call(lambda client: client.whoami())


@mcp.tool()
async def list_tickets(
    page: int | None = None,
    per_page: int | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    """List the tickets ASSIGNED to the token's account, newest first — or, with
    `scope="all"` and an administrator's account, every ticket in Ebteqdesk.

    By default this is the agent's own queue: not every ticket in Ebteqdesk, and
    not tickets the account raised as a requester — visibility is the assignee
    field and nothing else. Resolved and closed tickets are included. For the
    shared escalation queue, which is NOT limited to this account, use
    `list_escalations` — it needs the `escalation:read` scope AND the
    `bp_escalation.view` ability, neither of which an ordinary support account
    necessarily has, so check `whoami` before promising a user that list.

    Requires the `ticket:read` scope. `scope="all"` additionally requires the
    account's ROLE to hold the `ticket_all.view` ability, which only
    Administrator and Supervisor hold — that is a role permission, not a scope,
    so it does not appear in `whoami`'s `apiKey.scopes` list. Look for
    `ticket_all.view` in `whoami`'s `permissions` instead.

    Args:
        page: 1-based page number.
        per_page: Rows per page, 1 to 20. Defaults to 20, which is also the
            maximum — **every pull is at most 20 records**. Asking for more is
            an error, not a silently smaller page, so you can trust the number
            you asked for or you get told. Use it to ask for FEWER.
        scope: Whose tickets to list. Omit it (or pass "mine") for the token's
            own — those two are identical by contract. Pass "all" for every
            ticket in the account, which is REFUSED WITH A 403 unless the
            account holds `ticket_all.view`; if that happens the answer will be
            the same every time, so do not retry — fall back to the default
            list, or ask an administrator. Any other value is a 403 as well:
            the parameter is reserved and unknown values are refused rather
            than ignored. There is no way to list another single agent's
            tickets; it is your own or the whole account.

            🔴 "all" is a WIDE read. Prefer the default unless the task really
            is account-wide, and say so when you use it — an answer built from
            every ticket in the installation is a different claim from one built
            from the tickets this agent is responsible for.

    Returns `{"data": [...], "links": {...}, "meta": {...}}`. Each ticket has
    `id`, `subject`, `status {id,name}`, `priority {id,name}`,
    `category {slug,name}` or null, `requester {id,name,email}`,
    `assignee {id,name,email}`, `escalated`, `escalated_at`, `created_at`,
    `updated_at`.

    `escalated` IS THE ESCALATION STATE; `escalated_at` is only "since when"
    and is null on tickets escalated before that column existed. Never derive
    escalation from `escalated_at` — doing so reads the longest-escalated
    tickets as not escalated, which is exactly backwards.

    With `scope="all"` the rows can include ESCALATED tickets assigned to other
    agents. This list carries header fields only — no ticket list on this API
    returns comment bodies — and reading one of those tickets with `get_ticket`
    still requires the `escalation:read` scope, which listing it here does not
    grant.

    Note the key is `requester` — the person who raised the ticket. Continue
    paging with `links.next` until it is null, or use `meta.last_page`;
    `links.next` carries your `per_page` and your `scope`, so pages stay the
    size AND the width you asked for.
    """
    return await _call(
        lambda client: client.list_tickets(
            page=page, per_page=per_page, scope=scope
        )
    )


@mcp.tool()
async def list_tickets_by_category(
    category: str,
    page: int | None = None,
    per_page: int | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    """List the token's assigned tickets in ONE category.

    Identical in shape and visibility to `list_tickets`, narrowed to a single
    ticket category.

    Requires the `ticket:read` scope.

    Args:
        category: The category slug, e.g. "bp-task". Slugs are resolved against
            the live category table on every request, so a category added in
            Ebteqdesk works immediately. If you do not know the slug, call
            `list_tickets` and read `category.slug` off a ticket, or call
            `get_escalation_report`, which lists every category. An unknown slug
            returns a 404 that names the slug you asked for.
        page: 1-based page number.
        per_page: Rows per page, 1 to 20, default 20. Same rule as
            `list_tickets`: above 20 is an error, never a smaller page.
        scope: Same values and the same rule as `list_tickets` — omit it (or
            "mine") for this account's own tickets, "all" for every ticket in
            the account, which needs the `ticket_all.view` ability and is a 403
            without it. The category filter still applies either way.

    Returns the same envelope as `list_tickets`, `escalated` and `escalated_at`
    included.
    """
    return await _call(
        lambda client: client.list_tickets_by_category(
            category, page=page, per_page=per_page, scope=scope
        )
    )


@mcp.tool()
async def list_escalations(
    page: int | None = None, per_page: int | None = None
) -> dict[str, Any]:
    """The shared business-partner escalation queue — every unresolved escalated
    ticket in the installation, whoever it is assigned to, including tickets
    assigned to nobody.

    🔴 THIS LIST IS NOT YOURS. It is the ONLY ticket list on this API that is not
    limited to the token's own account: `list_tickets`, the category lists and
    every write tool show you only tickets assigned to you, and this one shows
    you everybody's. That is deliberate — an escalation is work that has been
    handed off, and the rows most needing attention are the ones assigned to
    nobody. **Check the `assignee` field to see which are yours.** Do not tell a
    user "you have N escalated tickets" from this list, and do not start working
    a row without checking who owns it.

    Requires the `escalation:read` scope and the `bp_escalation.view` ability.
    Both halves, and an ordinary support account has neither — check `whoami`
    before promising a user this list.

    ⚠️ THIS IS A WORK QUEUE, NOT "EVERY ESCALATED TICKET". A ticket drops off
    this list the moment it is SOLVED or CLOSED, but it stays escalated until
    somebody de-escalates it — so a ticket can be absent here and still report
    `escalated: true`, still cost `escalation:read` on `get_ticket`, and still
    cost `escalation:write` to write into. Never infer "not escalated" from
    absence here; read the ticket's own `escalated` field.

    Args:
        page: 1-based page number.
        per_page: Rows per page, 1 to 20, default 20. Above 20 is an error.

    Returns the same envelope and the SAME per-ticket object as `list_tickets` —
    byte for byte, from one shared serialiser. If you can read one list you can
    read this one; there is no second shape to learn.

    ORDER IS LONGEST-ESCALATED FIRST: `escalated_at` ascending, then `id`. But
    tickets with a null `escalated_at` sort LAST despite being the oldest — they
    were escalated before that column existed, and null means "unknown", not
    "the dawn of time". They are still genuinely escalated; `escalated` is true
    on them.

    ⚠️ A SOLVED ESCALATED TICKET IS NOT ON THIS LIST AT ALL. Solving takes a
    ticket off the BP queue, so a row disappearing from here does NOT mean the
    escalation was answered — it may have been solved, or de-escalated, and this
    list cannot tell you which. To find out what happened to a specific ticket,
    fetch the ticket; do not infer it from absence.
    """
    return await _call(
        lambda client: client.list_escalations(page=page, per_page=per_page)
    )


@mcp.tool()
async def get_ticket(
    ticket_id: int, thread_limit: int | None = None
) -> dict[str, Any]:
    """Read ONE ticket in full, WITH ITS CONVERSATION — the requester's opening
    message, every agent reply, the internal notes, the history events and the
    attachments on each.

    THIS IS THE ONLY TOOL THAT RETURNS WHAT WAS SAID. `list_tickets`,
    `list_tickets_by_category` and `list_escalations` return header fields only
    and carry no message bodies at all, so any question of the form "what is
    this ticket about", "what have we already told them", "why was it
    escalated" needs this tool and cannot be answered from a list.

    🔴 `kind: "note"` IS A PRIVATE INTERNAL NOTE. NEVER REPEAT ONE INTO A PUBLIC
    REPLY. A public reply is mailed to the requester's address, so quoting a note
    into one puts staff-only text in somebody's inbox and on their portal page.
    Each `conversation` entry is one of three kinds:

      - `comment` — a PUBLIC message. Either the requester's or an agent's reply
        to them; the requester has seen it, by email and on the portal.
      - `note`    — a PRIVATE internal note. Staff-only. It may contain
        diagnosis, blunt assessments of the requester, pricing or account
        internals. Use it to inform yourself; never repeat it, quote it, or
        paraphrase it into a reply.
      - `event`   — a history entry such as "Escalated" or "Status updated:
        solved". No `body_html`, no attachments.

    ⚠️ ON AN ESCALATED TICKET THE NOTES ARE USUALLY WHERE THE REAL WORK IS.
    Ebteqdesk silently downgrades an ordinary agent reply on an escalated ticket
    into a private note — that is the same rule that makes `comment_on_ticket`
    demand `escalation:write` there — so an escalated ticket's diagnosis
    typically lives in `note` entries while the `comment` entries are only what
    the requester was actually told. Read both, and keep the difference straight
    when you summarise.

    Requires EITHER the `ticket:read` scope OR the `escalation:read` scope, and
    then the ticket you asked for decides which one it actually costs:

      - assigned to this account         → `ticket:read`
      - ESCALATED (any status)           → `escalation:read`
      - any other ticket in the account  → `ticket:read`, plus the account's
        role holding `ticket_all.view` (Administrator and Supervisor only)

    ⚠️ "ESCALATED" IS THE TICKET'S `escalated` FLAG AND HAS NOTHING TO DO WITH
    ITS STATUS. It stays true after the ticket is solved and after it is closed,
    until somebody de-escalates it — so a resolved escalation still costs
    `escalation:read`, and an account holding `ticket_all.view` but not
    `escalation:read` cannot read it. That is deliberately NOT the same set as
    `list_escalations`, which is a WORK QUEUE and drops a ticket once it is
    resolved. A ticket can be absent from `list_escalations` and still be
    escalated; read `escalated` on the ticket, never the queue's membership.

    The two scopes do NOT substitute for each other. A key with only
    `escalation:read` — an escalation-only key — reads escalated tickets and
    their internal notes, and is refused on an ordinary ticket even one assigned
    to itself. A key with only `ticket:read` reads its
    own tickets and is refused on somebody else's escalation.

    A refusal carries `required_scope`, and a 403 naming `ticket:read` covers
    "no such ticket", "somebody else's" and "yours" alike — it is deliberately
    one answer for all three, so it is NOT evidence the ticket exists. A 404
    means the id is unreachable for an account that does hold `ticket:read`.
    Neither is worth retrying.

    Args:
        ticket_id: The numeric ticket id, from any list tool or any write
            tool's response.
        thread_limit: Optional, 1 to 200. Keep only the NEWEST N entries of the
            conversation. Omit it for the whole thread, which is the default —
            most tickets are short and truncating them loses the requester's
            original question. Reach for it on a long-running ticket where the
            recent state is what matters. Out of range is an error, not a
            clamp.

    Returns `{"data": {...}}` — every field of a `list_tickets` row (`id`,
    `subject`, `status`, `priority`, `category`, `escalated`, `escalated_at`,
    `requester`, `assignee`, `created_at`, `updated_at`) PLUS:

      - `body` / `body_html` — the requester's OPENING message. It is not in
        `conversation`; a summary that skips it starts at the first reply.
      - `attachments` — files on that opening message. Replies carry their own,
        inside their `conversation` entry.
      - `reference_number`, `summary`, `team`
      - `escalated_minutes` — minutes since escalation. NULL means unknown, not
        zero: the stamp is absent on tickets escalated before that column
        existed, and those are the OLDEST ones.
      - `conversation` — the thread, OLDEST FIRST, each entry `{kind, body,
        body_html, author, attachments, created_at, created_at_human}`.

    ⚠️ `conversation_truncated: true` APPEARS AT THE TOP LEVEL OF THE RESPONSE,
    beside `data` and NOT inside it, and ONLY when `thread_limit` actually cut
    something. Its absence means you have the whole thread. If it is present,
    say so when you summarise — you are looking at the tail of a longer
    conversation and the requester's original question may not be in it.

    Each attachment is `{id, name, mime_type, size, url}`. Pass the `id` to
    `get_ticket_attachment` to actually see an image; the `url` is the same
    endpoint and needs the same bearer token, so it is not something to hand to
    a user as a link.
    """
    return await _call(
        lambda client: client.get_ticket(ticket_id, thread_limit=thread_limit)
    )


@mcp.tool()
async def get_ticket_comments(
    ticket_id: int, page: int | None = None, per_page: int | None = None
) -> dict[str, Any]:
    """Page through one ticket's conversation, when it is too long to fetch whole.

    The same entries `get_ticket` returns in `conversation`, in the same order
    (oldest first) and the same shape, wrapped in the standard paged envelope.

    PREFER `get_ticket` FOR ALMOST EVERYTHING. It returns the header fields, the
    requester's opening message and the whole thread in ONE call, and most
    tickets are short enough that paging costs more calls than it saves. Reach
    for this tool when a ticket's thread is genuinely long and you want to walk
    it from the beginning, or when you already have the header and only need
    more of the conversation. If what you want is the most RECENT activity, use
    `get_ticket` with `thread_limit` instead — this tool pages forward from the
    oldest entry.

    🔴 THE REQUESTER'S OPENING MESSAGE IS NOT IN THIS LIST — NOT ON ANY PAGE.
    The problem report that started the ticket lives on the ticket itself, not
    among its comments, so it is returned by `get_ticket` as `body` and by
    nothing here. An agent that pages this endpoint exclusively reads every
    reply about a problem it has never seen stated, and will confidently
    summarise a ticket without knowing what the requester actually asked for.
    Call `get_ticket` at least once for any ticket you intend to reason about.

    ⚠️ THIS IS NOT A COMMENTS-ONLY LIST, despite the name. History events
    (`kind: "event"`) are interleaved chronologically, because the conversation
    IS the merge of messages and events. Read `kind` on every entry; the same
    three values and the same 🔴 rule about `note` apply as on `get_ticket`.

    Requires EITHER `ticket:read` OR `escalation:read` — identical visibility to
    `get_ticket`, including which of the two the ticket you asked for actually
    costs, and including its 403 and 404 rules. See that tool for the full rule.

    Args:
        ticket_id: The numeric ticket id.
        page: 1-based page number.
        per_page: Entries per page, 1 to 20, default 20 — the same ceiling as
            every other list on this API. Above 20 is an error, not a smaller
            page.

    Returns `{"data": [...], "links": {...}, "meta": {...}}`. Page with
    `links.next` until it is null, or use `meta.last_page`.
    """
    return await _call(
        lambda client: client.get_ticket_comments(
            ticket_id, page=page, per_page=per_page
        )
    )


@mcp.tool()
async def get_ticket_attachment(
    attachment_id: int, max_dimension: int | None = None
) -> Image:
    """Fetch one attached IMAGE and return it as an image you can actually look at.

    Use this when a ticket's `conversation` shows an attachment and the answer
    depends on what is in it — a screenshot of an error, a photo of damaged
    hardware, a picture of a receipt. Get the id from `get_ticket`:
    `data.attachments[].id` for the opening message's files, or
    `data.conversation[].attachments[].id` for a reply's or a note's.

    Returns TWO content blocks: a short text block of metadata followed by the
    IMAGE ITSELF as an image block. The metadata carries the attachment id, the
    mime type, the dimensions BOTH ways round (`width`/`height` for what you
    received, `source_width`/`source_height` for the stored original), the byte
    sizes, and `downscaled`.

    READ `downscaled` RATHER THAN COMPARING THE BYTE SIZES. It is the server's
    own verdict about the PIXELS. The bytes can go either way — re-encoding a
    flat-colour screenshot smaller routinely produces a LARGER file — so
    `bytes > source_bytes` is not evidence the image is untouched. A value of
    `null` means the server did not say; treat that as unknown, not as "full
    fidelity".

    🔴 THE IMAGE IS DOWNSCALED, SO DO NOT READ FINE DETAIL OFF IT. The longest
    edge is capped at 1568 pixels by default and the aspect ratio is preserved.
    That is deliberate — full-size attachments can be 25 MB, which is unusable —
    but it means small text, exact pixel values, thin UI chrome and low-contrast
    detail may be illegible or subtly wrong. If you cannot read something,
    SAY you cannot read it and retry with a larger `max_dimension`; do not guess
    at a serial number or an error code from a blurry region. An image already
    smaller than the ceiling is returned untouched and never enlarged, so a
    small image is not made worse.

    ⚠️ VIDEO ATTACHMENTS RETURN AN ERROR, NOT AN IMAGE. Ebteqdesk accepts video
    (`video/mp4`, `video/quicktime`, `video/webm`) as well as images, and this
    tool serves images only — a video answers 415 with its type named. There is
    no argument that changes that: tell the user the file is a video and that it
    has to be opened in Ebteqdesk in a browser. Check `mime_type` on the
    attachment before calling if you want to avoid the round trip.

    An image too large to return within the response ceiling answers 413. That
    one IS worth retrying, with a smaller `max_dimension`.

    Requires EITHER `ticket:read` OR `escalation:read`, decided by the file's
    PARENT TICKET exactly as `get_ticket` decides it: a file on an escalated
    ticket belonging to somebody else costs `escalation:read`, so a key holding
    only that scope — the usual developer shape — can read the screenshots on
    the escalation it is working. An attachment on a ticket this account cannot
    read answers 404 (or a 403 naming the scope the parent would have cost) —
    the 404 is the same one an id that does not exist gets, so it is not
    evidence the file was deleted.

    Args:
        attachment_id: The numeric attachment id, from `get_ticket`.
        max_dimension: Optional, 1 to 4096. The longest edge of the returned
            image, default 1568. Raise it when you need to read detail that is
            illegible at the default; lower it when you only need the gist and
            want to spend less context. Out of range is an error, not a clamp.
            Values above 1568 buy less than they look like they do — most vision
            models downscale to about that anyway — so raise it deliberately
            rather than by default.
    """
    image = await _call(
        lambda client: client.get_ticket_attachment(
            attachment_id, max_dimension=max_dimension
        )
    )

    # A text block of metadata, then the image itself. The metadata is here
    # rather than left implicit because the model otherwise has no way to know
    # it is looking at a downscaled copy — which is exactly the thing that makes
    # "I can't quite read that" the right answer instead of a confident guess.
    #
    # 🔴 `downscaled` is the SERVER'S ANSWER, carried through untouched. It used
    # to be computed here as `len(data) < source_bytes`, which is a byte
    # comparison standing in for a dimension question, and the two disagree on
    # precisely the images this endpoint exists to shrink: re-encoding a
    # flat-colour screenshot at a different compression level makes the file
    # bigger while the pixels shrink (2400x1800 / 23,960 bytes in, 1568x1176 /
    # 55,577 bytes out, measured). That printed `"downscaled": false` directly
    # beside `"width": 1568` — self-contradictory, and worse, it told the model
    # it already held full fidelity so the "retry with a larger max_dimension"
    # line two keys below was dead advice. Only the server holds both the source
    # and output dimensions, so only the server can answer; this reports.
    return [
        {
            "attachment_id": attachment_id,
            "mime_type": image.mime_type,
            "width": image.width,
            "height": image.height,
            "source_width": image.source_width,
            "source_height": image.source_height,
            "bytes": len(image.data),
            "source_bytes": image.source_bytes,
            # None when the server did not say. An honest unknown, never False.
            "downscaled": image.downscaled,
            "note": _image_note(image.downscaled),
        },
        Image(data=image.data, format=_image_format(image.mime_type)),
    ]


@mcp.tool()
async def get_escalation_report(
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """Per-category ticket and escalation counts over an optional date range.

    Requires the `bp_escalation.view` role permission, and the
    `escalation-reports:read` scope.

    Args:
        date_from: Optional ISO 8601 date or date-time lower bound, e.g.
            "2026-03-01". Widened to the START of that day.
        date_to: Optional ISO 8601 upper bound, widened to the END of that day.
            Must not be earlier than `date_from` — a reversed range is rejected
            with an error, never silently widened to all time.
        Omitting both means all time.

    Returns `{"data": {"range", "metricKeys", "totals", "categories"}, "meta": ...}`.

    ROW IDENTITY IS `key`, NEVER `id` OR `slug`. Every row in `data.categories`
    has `key`, and it is the only field guaranteed to be present and unique:
    the "Uncategorised" bucket has `id: null` AND `slug: null`. `key` is the slug
    where one exists, `_type-{id}` where a category has no slug, and
    `_uncategorised` for the null bucket. Join, group and label on `key`.

    THREE RULES ABOUT THE NUMBERS. They are counter-intuitive, the payload cannot
    signal them, and ignoring any of them produces figures that are simply wrong:

      1. `escalated / total` IS NOT A PERCENTAGE, and `escalated` CAN EXCEED
         `total`. The two counts range over different date columns, so within any
         bounded range they count overlapping-but-different sets of tickets. Do
         not present a ratio of them, and do not treat `escalated > total` as
         corrupt data.

      2. `escalatedUndated` IS IDENTICAL IN EVERY RANGE — it counts escalations
         with no date at all, so no filter can move it. Never add it to
         `escalated`; report it separately, or not at all. Two ranges showing the
         same `escalatedUndated` is correct, not a caching bug.

      3. `sum(status.*)` CAN BE LESS THAN `total`. Do not compute a "missing"
         or "other" status bucket from the difference, and do not use `total` as
         the denominator for a status breakdown.

    `meta.filters` echoes what you sent; `data.range` reports the widened instants
    actually measured.
    """
    return await _call(
        lambda client: client.get_escalation_report(
            date_from=date_from, date_to=date_to
        )
    )


@mcp.tool()
async def get_reports_summary(
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """Account-wide ticket volume, response times, resolution times and ratings.

    The whole installation over a date range — not this account's own tickets,
    and not per-category. For per-category escalation counts use
    `get_escalation_report`, which is a different report with a different scope
    and, importantly, DIFFERENT DATE SEMANTICS (see below).

    Requires the `reports:read` scope AND the `admin.access` ability. Both gates
    are live: five of the seven numbers here are admin-only cells on the
    Ebteqdesk reports page, and roles such as Supervisor hold `reports.view`
    without `admin.access`. So this can be refused for an account whose
    `reports:read` scope resolves perfectly — the refusal names `admin.access`,
    and its remedy is an administrator, NEVER a new key.

    Args:
        date_from: Optional ISO 8601 date or date-time lower bound.
        date_to: Optional ISO 8601 upper bound. Must not precede `date_from`.

    🔴 BOTH BOUNDS ARE INCLUSIVE INSTANTS AND NEITHER IS WIDENED TO A WHOLE DAY.
    A bare `date_to="2026-08-31"` means "up to 2026-08-31 00:00:00" and
    EXCLUDES that entire day's tickets — the commonest way to under-report a
    month by a day. Pass `date_to="2026-08-31T23:59:59"` when you mean the full
    day. `data.range` echoes the instants actually measured, so check it before
    reporting a figure as "August".

    ⚠️ `get_escalation_report` DOES widen its bounds to whole days. Two report
    tools, two conventions, each matching the Ebteqdesk page it mirrors. Do not
    carry the habit from one to the other.

    OMITTING BOTH MEANS THE CURRENT CALENDAR MONTH, not all time. If a user asks
    for "all time", you must say what range you actually measured.

    Returns `{"data": {"range", "volume", "times", "quality"}, "meta": {...}}`.

    UNITS, because every one of these is guessable and three of the guesses are
    wrong:
      - `volume.*` (`tickets`, `unanswered`, `open`, `solved`) — counts,
        integers, never null.
      - `times.firstReplyMinutes`, `times.resolutionMinutes` — MINUTES, not
        hours and not seconds. Convert before presenting anything over ~120.
      - `quality.oneTouchResolutionPercent`, `quality.reopenedPercent` — on
        0..100, NOT 0..1. A value of 4.0 is four percent.
      - `quality.averageRating` — 1 to 5 stars.

    🔴 EVERY FIELD UNDER `times` AND `quality` IS NULLABLE, AND NULL MEANS NO
    DATA — not zero, and emphatically not "0%" or "0 minutes". A null
    `reopenedPercent` means nothing was resolved in the range so the ratio has
    no denominator; reporting it as 0% tells the user their reopen rate is
    perfect when it is unmeasured. Say "no data" for a null.
    """
    return await _call(
        lambda client: client.get_reports_summary(
            date_from=date_from, date_to=date_to
        )
    )


@mcp.tool()
async def search_kb_articles(
    query: str | None = None,
    per_page: int | None = None,
    page: int | None = None,
) -> dict[str, Any]:
    """Search the Ebteqdesk knowledge base, or list it.

    Requires the `kb:read` scope.

    THE CORPUS IS PUBLISHED, PUBLICLY-VISIBLE ARTICLES ONLY — always, for every
    token, including an administrator's. Internal runbooks and `agents`-only
    articles that are visible in the Ebteqdesk web UI are NOT reachable here. If
    a user insists an article exists and this returns nothing, that is the likely
    reason; say so rather than concluding the article was deleted.

    🔴 "PUBLICLY VISIBLE" IS THE ARTICLE'S OWN SETTING WHERE IT HAS ONE, AND ITS
    FOLDER'S WHERE IT DOES NOT. An article can carry its own visibility, which
    overrides its folder's in BOTH directions:

        effective visibility = the article's own, or its folder's if it has none

    So a folder marked `agents` in `list_kb_tree` may still hold one article that
    is served here, and a folder marked `public` may hold one that is not. Do NOT
    infer from a folder's `visibility` that a particular article inside it is or
    is not reachable — that inference was correct before 2026-08-19 and is not
    any more. What a folder's level tells you is what its articles inherit.

    Args:
        query: Free-text term matched against title and body. Omitting it, or
            passing an empty/whitespace string, means "no search" and returns the
            whole corpus newest-first — it does NOT mean "match nothing".
        per_page: 1..100, default 25. A value outside that range is rejected with
            an error rather than clamped.
        page: 1-based page number.

    Returns `{"data": [...], "links": {...}, "meta": {...}}`. Each row is a
    SUMMARY — `slug`, `title`, `url`, `category`, `folder`, `tags`,
    `published_at`, `updated_at`, `excerpt` — with no body. `category` and
    `folder` may be null. `excerpt` is truncated plain text; call
    `get_kb_article` with the `slug` for the full article.

    `url` is the public portal page for the article — safe to share outside the
    desk, because the server emits it ONLY for an article a signed-out
    visitor can actually open. Every row from THIS tool has one, since the
    corpus here is already published-and-public by definition. Prefer it over
    hand-building a link from the slug: the portal's path is not part of this
    API's contract and may move.

    ⚠️ `url` IS NULLABLE IN THE SHAPE, so read it defensively rather than
    assuming a string. It is null for an article with no public page — one never
    published, one withdrawn after publication, or one whose effective visibility
    is `agents`/`customers`. None of those can reach you through this tool or
    `get_kb_article`, which is why the null is a shape detail here and not a case
    to handle: the resource is shared with the REST write endpoints
    (`POST /api/v1/kb/articles`), which echo unpublished drafts and which this
    client does not expose. Never synthesise a URL to fill a null: null means
    there is no page to send anyone to, and a fabricated link 404s for whoever
    it was quoted to.

    ⚠️ `folder` IS NULL FOR AN ARTICLE WHOSE FOLDER IS INTERNAL, even though the
    article itself is public. That happens when the article's own visibility
    overrides an `agents` or `customers` folder: the article is reachable, its
    folder is not, and Ebteqdesk withholds the folder's NAME rather than
    disclosing an internal section title on a public surface. `category` is still
    present. Report the article without a folder rather than guessing one, and do
    not tell the user the article is filed nowhere.
    """
    return await _call(
        lambda client: client.search_kb_articles(
            query=query, per_page=per_page, page=page
        )
    )


@mcp.tool()
async def get_kb_article(
    slug: str, locale: Literal["en", "zhcn"] | None = None
) -> dict[str, Any]:
    """Fetch one knowledge base article, with its body, by slug.

    Requires the `kb:read` scope.

    Args:
        slug: The article slug, e.g. "resetting-your-password". Slugs are frozen
            at first publish, so they are permanent identifiers. Get one from
            `search_kb_articles`; there is no lookup by numeric id.
        locale: `"en"` or `"zhcn"` (Simplified Chinese) to read the article AS A
            READER OF THAT LANGUAGE SEES IT. OMITTING IT IS NOT THE SAME AS
            ASKING FOR ENGLISH — see the block below, and do not treat the two
            as interchangeable.

    Returns `{"data": {...}}` — the summary fields, minus `excerpt`, plus:
      - `seo` — `{"title", "description"}`
      - `body_html` — SANITISED HTML, not markdown. Ebteqdesk stores article
        bodies as HTML and there is no markdown form of them. Render or convert
        it; do not present it to a user as markdown source.

    Same corpus and same visibility rule as `search_kb_articles`: published, and
    publicly visible by its OWN setting where it has one, its folder's where it
    does not. `folder` may be null on an article whose folder is internal — see
    that tool for why, and do not fill it in.

    🔴 `locale` — AND WHY OMITTING IT IS NOT THE SAME AS ASKING FOR ENGLISH

    An Ebteqdesk article can carry its own title and body PER LANGUAGE. One that
    does is served from that text rather than from its shared base text, and it
    appears ONLY in the languages it has a version for.

    WITH a locale, this is what a reader of that language gets: `title`,
    `body_html` and `seo` come from that language's version, and an article with
    no version in that language is NOT FOUND — the same answer the public help
    centre gives that reader. Pass a locale to check work you did with
    `update_kb_article(locale=...)`, and to quote an article back to a requester
    in the language they wrote to you in.

    WITHOUT a locale, you are reading the WHOLE corpus rather than one language's
    slice of it. That corpus deliberately INCLUDES articles that exist in only
    one language, and for those it hands back the article's BASE text — which for
    a Chinese-only article is the pre-translation English somebody left behind.
    The article is real and published; the text simply is not the version any
    help centre currently shows. That is the right default for searching and
    reading broadly, and the wrong one for verifying a translation.

    ⚠️ SO DO NOT QUOTE AN ARTICLE BACK IN A LANGUAGE YOU DID NOT ASK
    FOR. If the requester wrote in Chinese, read with `locale="zhcn"`. A
    locale-free read can hand you English text for an article whose Chinese
    version says something else, and nothing in the response distinguishes that
    from an article with no versions at all.

    ⚠️ `"zh-cn"` IS NOT ACCEPTED and is a 422. The supported values are exactly
    `"en"` and `"zhcn"`. Both spellings are real in Ebteqdesk, for different
    things; this is the one the help centre uses.

    A NOT-FOUND RESULT IS AMBIGUOUS ON PURPOSE. An article that exists but is
    unpublished or internal returns exactly the same error as a slug that never
    existed — identical text, and it does not repeat the slug back. This stops
    the endpoint being used to discover the titles of draft and internal
    documentation. Do not try to distinguish the two cases, and do not report to
    the user that "the article exists but is hidden": you cannot know that. Say
    that no published article has that slug. With a `locale`, that same error
    ALSO covers "this article has no version in that language", so the honest
    report is that no published article answers that slug in that language —
    never that the article "is missing its translation", which you cannot know.
    """
    return await _call(lambda client: client.get_kb_article(slug, locale=locale))


@mcp.tool()
async def get_kb_article_review(reference: str) -> dict[str, Any]:
    """Read the review verdict on an article, AND on any edit staged against it
    while it is published — without disturbing either.

    🔴 THIS EXISTS SO THAT CHECKING A VERDICT DOES NOT DESTROY IT. Before this
    tool, the only way to see whether an article had been approved or rejected
    was to `update_kb_article` it again and look at the response — and every
    update RE-QUEUES the article and clears the reviewer's note. Checking the
    state destroyed the state, and the rejection reason you were trying to read
    was the first thing gone. Poll with THIS tool. Never with a write.

    It changes nothing at all: no review state, no timestamps, no content.

    🔴 IT IS ALSO THE ONLY IDEMPOTENT WAY TO READ A STAGED EDIT'S VERDICT.
    `update_kb_article` on a PUBLISHED article stages a revision, and there is
    one revision row per article — so calling it again to "see what happened"
    REPLACES that row and erases the rejection note with it. Same trap as the
    article-level one above, on a second surface. Read here.

    Requires the `kb:write` scope — not `kb:read`, even though this only reads.
    Its corpus is every article including drafts, the same corpus the write
    tools address, while `kb:read` gates the public help corpus and is
    deliberately the one scope with no role requirement behind it.

    Args:
        reference: The article reference. Either the frozen slug, or the
            `id:<n>` form (e.g. "id:42") that `propose_kb_article` returns as
            `reference`. An article created through this API has NO slug until
            a human first publishes it, so `id:<n>` is the normal form — see
            `update_kb_article`.

    Returns `{"data": {...}, "revision": {...}|null}`. TWO SEPARATE REVIEW
    RECORDS COME BACK AND THEY ANSWER DIFFERENT QUESTIONS. Read the right one.

    `data` and `data.review` — THE ARTICLE ITSELF, and the verdict on the
    article as a whole. This is what a DRAFT you proposed goes through.

      - `review.state` — `pending` while it waits, then the reviewer's verdict.
        On an article that was published long ago it is typically `none`; that
        is not a problem and says nothing about a staged edit.
      - `review.note` — THE REJECTION REASON, when there is one. This is the
        field worth reading; act on it by revising and re-proposing with
        `update_kb_article`.
      - `review.reviewed_at`, `review.reviewed_by` — who decided and when.
      - `status` — `draft` until a human publishes. This API cannot publish.
      - `data.title`, `data.body_html`, `data.translations` — WHAT IS LIVE
        RIGHT NOW. Never the text of a pending revision.

    `revision` — THE STAGED EDIT TO A PUBLISHED ARTICLE, or null. This is what
    an `update_kb_article` call against a live article produces, and it is where
    that call's verdict lives. Same fields as `review`, plus `source` (`"api"`
    for one this server staged, `"manual"` for one a human staged in the
    authoring UI).

    🔴 `revision` READS THREE WAYS AND THE THIRD IS NOT OBVIOUS. `state` is
    NEVER `"approved"` — approving a revision APPLIES it and DELETES the row —
    so:

      - `{"state": "pending", ...}` — your edit is waiting. Nobody has looked.
        The live article is still the old text. Do not resend; that replaces it.
      - `{"state": "rejected", ...}` — refused. `note` is the reason and
        `reviewed_by` is the person. Revise and send ONE new
        `update_kb_article` call. Read the note before you do, because that call
        destroys it.
      - `null` — AMBIGUOUS, AND THIS IS THE ONE TO GET RIGHT. It means either
        (a) nothing was ever staged, or (b) something was staged AND APPROVED,
        so the text is live and the row is gone. Nothing in this payload tells
        you which. `data.title` / `data.body_html` / `data.translations` do:
        they are the live article, so if your text is in them it was approved.
        `get_kb_article(slug, locale=…)` shows the same thing as a reader sees
        it. Never report a null `revision` as "still waiting" — waiting is
        `pending`, and null is very often "done".

    A `pending` state, in either block, means no human has looked yet. Do not
    re-submit to "bump" it: on a draft a resubmission resets
    `review_requested_at` and moves the article to the back of the queue it is
    already in, and on a published article it throws away the revision that was
    already queued and stages a new one.
    """
    return await _call(lambda client: client.get_kb_article_review(reference))


@mcp.tool()
async def list_kb_proposals(
    review_state: Literal["pending", "approved", "rejected"] | None = None,
    per_page: int | None = None,
    page: int | None = None,
) -> dict[str, Any]:
    """List the knowledge base articles waiting on a review, approved, or
    REJECTED — with each rejection reason on the row.

    🔴 THIS IS NOT "YOUR" PROPOSALS. It returns EVERY article in the
    installation carrying a review state, whoever proposed it — another
    integration's, and ones a human wrote in the browser and somebody later
    revised through this API. Nothing in the payload distinguishes them, and the
    API genuinely cannot: an API key identifies an ACCOUNT, not an agent, and
    two agents may share one. Recognise your own by `title` or `reference`. Do
    not tell a user "you have three rejections" when what you have is three
    rejections in their knowledge base.

    It exists because a `reference` is not durable. `get_kb_article_review`
    answers "what happened to THIS article" and needs the `id:<n>` string the
    create response returned — which appears nowhere else and does not survive a
    restarted session. Without this tool the only way to find a rejection was to
    `update_kb_article` articles speculatively, which RE-QUEUES each one and
    ERASES the reviewer's note. Use this to find them; use
    `get_kb_article_review` to read one you already hold.

    It changes nothing: no review state, no timestamps, no content.

    Requires the `kb:write` scope — not `kb:read`, even though this only reads.
    Its corpus is drafts, the same corpus the write tools address, while
    `kb:read` gates the public help corpus and is deliberately the one scope
    with no role requirement behind it. `kb:write` resolves only while the key
    carries it AND the account's role holds `kb.manage`, which is ADMINISTRATOR
    AND SUPERVISOR ONLY.

    Args:
        review_state: One of `pending` (waiting on a human), `approved` (a human
            accepted the content — this does NOT mean published) or `rejected`
            (refused, with the reason in `review.note`). Omit for all three.
            🔴 There is no `none` value: `none` is every hand-written article
            that was never submitted, and asking for it is a 422 rather than an
            empty list.
        per_page: 1..100, default 25. Out of range is a 422, never a quietly
            smaller page.
        page: 1-based page number.

    Returns `{"data": [...], "links": {...}, "meta": {...}}`. Each row carries
    `id`, `reference`, `title`, `folder`, `category`, `tags`, `excerpt`,
    `status`, `source` and `review` — the SAME `review` block
    `get_kb_article_review` returns, so one parser reads both.

    ⚠️ THERE IS NO `body_html` ON THESE ROWS, deliberately — twenty-five article
    bodies is a large payload to answer a question the `review` block answers on
    its own. `excerpt` is the first 300 characters of plain text, enough to tell
    two proposals apart. Call `get_kb_article_review` with a row's `reference`
    when you need the full text of one.

    🔴 WHAT TO DO WITH A REJECTION: read `review.note` — that is the reviewer's
    prose and it is the whole point of this list — then REVISE the article and
    send the revision with `update_kb_article`, using the `reference` from the
    row. Never resubmit unchanged to "bump" it: every update restamps
    `review_requested_at`, which moves the article to the BACK of the queue and
    clears the note you just read.

    Rows come back newest submission first, which is the reverse of the order
    the humans work the queue in. `status` is `draft` on everything except an
    article a human has published; this API cannot publish.
    """
    return await _call(
        lambda client: client.list_kb_proposals(
            review_state=review_state, per_page=per_page, page=page
        )
    )


@mcp.tool()
async def list_kb_tree() -> dict[str, Any]:
    """The knowledge base's STRUCTURE — every category, every folder, and THEIR
    IDS.

    🔴 THIS IS WHERE `kb_folder_id` FOR `propose_kb_article` COMES FROM, and
    nothing else on this API returns a folder id at all — `search_kb_articles`
    and `get_kb_article` carry only `{slug, name}` pairs for the folder and the
    category. So if you are about to file an article, CALL THIS FIRST and read
    the `id` off the folder you mean. Guessing an id files an article somewhere
    a human did not expect; there is no delete-article tool to undo it with.

    Call it first for the empty case too: a knowledge base with no categories at
    all answers `{"data": []}`, which means `propose_kb_article` cannot be called
    until somebody creates a category and a folder. `create_kb_category` and
    `create_kb_folder` do that.

    Requires the `kb:write` scope — not `kb:read`, even though this only reads.
    The tree is the AUTHORING structure: it carries ids and internal folders,
    while `kb:read` gates the public help corpus and is deliberately the one
    scope with no role requirement behind it. `kb:write` resolves only while the
    key carries it AND the account's role holds `kb.manage`, which is granted to
    ADMINISTRATOR AND SUPERVISOR ONLY — an agent- or developer-role account
    cannot use this no matter what key is minted for it.

    ⚠️ THESE ARE INTERNAL FOLDERS AND INTERNAL NAMES. Unlike
    `search_kb_articles`, this is NOT filtered to public content — every folder
    is listed whatever its visibility, including `agents`-only ones. Folder and
    category names here are internal organisation, not copy for anywhere outside
    the desk, and may name internal teams, systems or accounts. Use them to
    choose where to file; do not repeat them into a public reply.

    No arguments and no paging: the structure is a few hundred rows at most.

    Returns `{"data": [...]}`, ordered by `position` then `id` at both levels:

      - Each category — `id`, `name`, `slug`, `description`, `position`, and
        `folders`, which is always present and is `[]` for a category with none.
      - Each folder — `id`, `kb_category_id`, `name`, `slug`, `description`,
        `visibility`, `position`, `articles_count`.

    `visibility` is `agents` (internal — the default and what every folder
    created through this API gets), `customers`, or `public`. It is what the
    articles in the folder INHERIT, and it is the level every article this API
    files will have.

    🔴 IT IS NOT A STATEMENT ABOUT THE ARTICLES ALREADY IN THE FOLDER. Since
    2026-08-19 an article can carry its own visibility, overriding its folder's
    in both directions — so a `public` folder may hold an article a signed-out
    visitor cannot read, and an `agents` folder may hold one anybody can. This endpoint lists no
    articles and cannot tell you which. Read a folder's `visibility` to decide
    WHERE TO FILE; do not read it to decide whether some existing article is
    public. If you need that answer for a specific article, `search_kb_articles`
    returning it is the answer — that corpus is already effective-visibility
    filtered.

    That gap is safe for one reason only: NOTHING ON THIS API CAN SET A
    PER-ARTICLE OVERRIDE (`propose_kb_article` and `update_kb_article` have no
    such parameter and silently ignore one), so everything this integration
    creates inherits the folder shown here.

    `articles_count` includes drafts, so a folder showing 4 may have nothing
    published in it.

    The folder `slug` and category `slug` are re-derived whenever a name changes,
    so they are NOT stable identifiers — use `id`.
    """
    return await _call(lambda client: client.list_kb_tree())


# --------------------------------------------------------------------------- #
# The two FLAT views of the tree
# --------------------------------------------------------------------------- #
#
# 🔴 BOTH ARE PROJECTIONS OVER `list_kb_tree`, NOT ENDPOINTS OF THEIR OWN. Each
# makes exactly one HTTP call — GET /api/v1/kb/tree — and then filters the
# result in this process. There is no `GET /api/v1/kb/categories` and no
# `GET /api/v1/kb/folders` on the server, and this file does not pretend
# otherwise: each description says so in its own words, because a model that
# believed `list_kb_folders(kb_category_id=3)` were a narrow lookup would call it
# in a loop over categories and issue one full-tree fetch per iteration.
#
# Why a projection rather than a real endpoint: the tree is a few hundred rows of
# structure with no article bodies in it — the folder rows carry a COUNT, not the
# articles — so the payload a narrow endpoint would save is small, and the one it
# would cost is a fourth KB read route with its own scope declaration, its own
# 404 shape and its own drift surface. If the KB ever grows to where the tree is
# genuinely heavy, `GET /api/v1/kb/folders?category_id=` is the shape to add, and
# these two tools become its front end with no change to their contracts.


@mcp.tool()
async def list_kb_categories() -> dict[str, Any]:
    """Every knowledge base CATEGORY, flat — the top level, without the folders.

    Use it to answer "what categories exist?" and to pick a `kb_category_id` for
    `create_kb_folder`. If you need the folders too, call `list_kb_tree` instead
    of calling this and then walking back — this IS `list_kb_tree` with the
    `folders` key dropped.

    ⚠️ IT IS NOT CHEAPER THAN `list_kb_tree`. There is no categories-only
    endpoint on this API: this tool fetches the WHOLE tree and strips the nested
    folders client-side. One call, same cost. Do not reach for it as an
    optimisation and do not call it in a loop.

    Requires the `kb:write` scope — not `kb:read` — because the tree it reads is
    the AUTHORING structure. It carries ids and internal folders, while `kb:read`
    gates the public help corpus and is deliberately the one scope with no role
    requirement. `kb:write` resolves only while the key carries it AND the
    account's role holds `kb.manage`: ADMINISTRATOR AND SUPERVISOR ONLY.

    ⚠️ THESE ARE INTERNAL NAMES. Category names are internal organisation, not
    copy for anywhere outside the desk, and may name internal teams, systems or
    accounts. Use them to choose where to file; do not repeat them into a public
    reply.

    No arguments and no paging.

    Returns `{"data": [...]}`, ordered by `position` then `id`, each row
    `{"id", "name", "slug", "description", "position"}`. The `slug` is re-derived
    whenever the name changes, so it is NOT a stable identifier — use `id`.
    """
    tree = await _call(lambda client: client.list_kb_tree())

    return {
        "data": [
            {key: value for key, value in category.items() if key != "folders"}
            for category in tree.get("data", [])
        ]
    }


@mcp.tool()
async def list_kb_folders(kb_category_id: int | None = None) -> dict[str, Any]:
    """Every knowledge base FOLDER, flat — all of them, or one category's.

    This is where a `kb_folder_id` for `propose_kb_article` comes from when you
    already know which folder you want and do not need the category structure
    around it. Each row carries its `kb_category_id`, so the tree is still
    reconstructable from the flat list.

    ⚠️ IT IS NOT CHEAPER THAN `list_kb_tree`, AND FILTERING DOES NOT MAKE IT
    CHEAPER. There is no folders endpoint on this API: this tool fetches the
    WHOLE tree and flattens it client-side, and `kb_category_id` filters the
    result AFTER it arrives. Filtering saves you reading rows, not the server
    sending them. Never call this once per category — call it once with no
    argument and group the result yourself.

    Args:
        kb_category_id: Optional. Return only the folders of this category. It
            is spelled the API's way — like `create_kb_folder`'s — because it
            names the same field those rows carry. An id that matches no
            category returns `{"data": []}` rather than an error, because this
            filters a list it has already fetched and has no 404 to give you;
            call `list_kb_categories` if you need to know whether it exists.

    Requires the `kb:write` scope, resolving only while the key carries it AND
    the account's role holds `kb.manage` — ADMINISTRATOR AND SUPERVISOR ONLY.

    Returns `{"data": [...]}`, ordered by category `position` then folder
    `position`, each row `{"id", "kb_category_id", "name", "slug", "description",
    "visibility", "position", "articles_count"}`.

    🔴 `visibility` IS THE ONE FIELD TO READ BEFORE FILING ANYTHING. It is
    `agents` (internal — the default, and what every folder created through this
    API gets), `customers`, or `public`, and it is the level an article filed
    here INHERITS — which is exactly the level everything this API creates gets,
    because nothing on this API can set a per-article override.

    ⚠️ It is NOT a statement about the articles already in the folder: an article
    can carry its own visibility overriding its folder's in both directions. See
    `list_kb_tree` for the full note. `articles_count` includes drafts, so a
    folder showing 4 may have nothing published in it. Folder names are internal
    organisation, not copy for anywhere outside the desk; do not repeat them
    into a public reply.

    `position` is the folder's index WITHIN ITS CATEGORY, so it repeats across
    the flat list — two folders can both be at 0. It is not a global rank.
    """
    tree = await _call(lambda client: client.list_kb_tree())

    folders = [
        folder
        for category in tree.get("data", [])
        for folder in category.get("folders", [])
    ]

    if kb_category_id is not None:
        folders = [
            folder
            for folder in folders
            if folder.get("kb_category_id") == kb_category_id
        ]

    return {"data": folders}


# --------------------------------------------------------------------------- #
# Write tools
# --------------------------------------------------------------------------- #
#
# Each description opens with a WRITES line, in capitals, naming who is
# affected. That placement is the whole design: the model reads the first line
# of a description when choosing between tools and may never read the last one,
# so the consequence goes where a skim cannot miss it and the mechanics follow.
# See the 🔴 note in the module docstring for why there is no `dry_run` here.


@mcp.tool()
async def create_ticket(
    subject: str,
    description: str,
    requester: Annotated[dict[str, Any], WithJsonSchema(REQUESTER_SCHEMA)],
    priority: Annotated[
        Literal[1, 2, 3, 4] | None,
        WithJsonSchema(_enum_schema(PRIORITIES, "How urgent the ticket is.", optional=True)),
    ] = None,
    status: Annotated[
        Literal[1, 2, 3, 8, 4, 5] | None,
        WithJsonSchema(
            _enum_schema(CREATE_STATUSES, "The status to OPEN the ticket in.", optional=True)
        ),
    ] = None,
    category: str | None = None,
    reference_number: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """WRITES TO EBTEQDESK — files a REAL ticket on a live helpdesk.

    The ticket appears in the agent queue immediately, is assigned to the
    token's own account, and MAY CREATE A REQUESTER CONTACT RECORD as a side
    effect (see `requester`). There is no delete-ticket tool here or anywhere
    on this API, so a ticket filed by mistake has to be cleaned up by a human in
    the web UI. Confirm the details with the user before calling this.

    Requires the `ticket:write` scope and the `ticket.create` ability.

    Args:
        subject: The ticket title. 3 to 190 characters, required.
        description: The opening message body, required. This is the requester's
            problem as the ticket records it, not a note to yourself.
        requester: THE REQUESTER — on this internal desk, the member of staff
            who raised it. Required, as an object in one of two forms. The
            address on it is where Ebteqdesk mails every public reply.

            `{"id": 12}` — an existing contact. Get the id from the
            `requester.id` field of a ticket returned by `list_tickets`. Exact,
            and it creates nothing.

            `{"email": "ada@example.com", "name": "Ada Lovelace"}` — find or
            create, matched on the EMAIL, which is the identity. If no contact
            has that address, ONE IS CREATED. `name` is used only when the
            contact is created; it will not rename an existing contact, so a
            mistyped name next to a known address is harmless but also silently
            ignored. Prefer the id form whenever you have an id.

            If both are given, `id` wins and `email` is ignored.

            The schema for this argument is a `oneOf` of those two shapes with
            `required` on each branch, so a client reading the schema alone can
            build a valid one. A bare string is refused before any request.

        priority: 1 low, 2 normal, 3 high, 4 blocker. Defaults to 2. Anything
            else is refused HERE, by name, rather than costing a round trip.
        status: The status to OPEN the ticket in. 1 new (default), 2 open,
            3 pending, 8 waiting on customer (the API's own enum name,
            `waitingOnCustomer`, inherited here and not renamed — on this desk
            it means waiting on the requester), 4 solved, 5 closed. Merged (6)
            and spam (7) are refused — they are outcomes of other actions, never
            a choice — and so is anything outside that list.

            ⚠️ OPENING A TICKET AS 4 (SOLVED) EMAILS THE REQUESTER'S ADDRESS. It
            resolves the ticket immediately, which fires the same satisfaction
            survey `close_ticket` documents. Use 5 to file an already-resolved
            ticket without mailing anybody.
        category: A ticket-type slug such as "bp-task". Read one off
            `category.slug` in `list_tickets`, or take any `key` that is not
            `_uncategorised` from `get_escalation_report`. An unknown slug is
            an error naming the slug; it does not silently file the ticket
            uncategorised.
        reference_number: Free-text external reference, up to 64 characters.
        tags: A list of tag strings, each up to 50 characters.

    You cannot set the assignee, and it is always the token's own account. That
    is not an omission to route around: this API shows you only the tickets
    assigned to you, so a ticket created for someone else would vanish from your
    view the moment it was written.

    Returns 201 `{"data": {...}}` — one ticket in exactly the shape
    `list_tickets` returns, so the new `id` is right there for a follow-up
    `comment_on_ticket` or `close_ticket`.
    """
    return await _call(
        lambda client: client.create_ticket(
            subject=subject,
            description=description,
            requester=requester,
            priority=priority,
            status=status,
            category=category,
            reference_number=reference_number,
            tags=tags,
        )
    )


@mcp.tool()
async def comment_on_ticket(ticket_id: int, body: str) -> dict[str, Any]:
    """WRITES TO EBTEQDESK — posts a PUBLIC reply the requester receives.

    This is not an internal note and there is no flag to make it one: the text
    is EMAILED TO THE REQUESTER'S ADDRESS on the ticket, every time, and it
    cannot be edited or deleted through this API. Ebteqdesk being an internal
    desk changes nothing here — the requester is a colleague and the mail still
    goes out, and no setting anywhere suppresses it. Show the user what you
    intend to send before sending it.

    Requires EITHER the `ticket:write` scope OR the `escalation:write` scope to
    reach the endpoint at all, and then the ticket decides which one it actually
    costs:

      - a NON-escalated ticket assigned to this account → `ticket:write`
      - an ESCALATED ticket assigned to this account    → `escalation:write`
        (plus `escalation:read`, and the `bp_escalation.reply` ability)

    Plus the `ticket.reply` ability on a non-escalated ticket. The two scopes do
    NOT substitute for each other: a key holding only `escalation:write` — the
    usual shape for a developer account — cannot reply on an ordinary ticket
    even one assigned to itself, and a key holding only `ticket:write` cannot
    reply on an escalated one. See below.

    Args:
        ticket_id: The numeric ticket id, from `list_tickets`.
        body: The reply text. Required and non-blank.

    Returns 201 `{"data": {...}, "comment": {"id": …, "created_at": …}}` — the
    whole ticket plus a receipt for the comment. THE COMMENT BODY IS NOT ECHOED
    BACK, by design; this API never serialises comment text.

    ⚠️ `comment.id` CAN BE null, AND THAT MEANS NOTHING WAS POSTED. Ebteqdesk
    discards a reply whose text is identical to the author's saved signature.
    The call still answers 201 and the ticket is still touched, but no comment
    row exists. If you get a null id, tell the user the reply was not filed —
    do not report it as sent.

    🔴 REPLYING TO AN ESCALATED TICKET NEEDS `escalation:write`. Check the
    ticket's `escalated` field before you call: if it is true, a key holding
    only `ticket:write` cannot reply to that ticket at all — re-minting with the
    same scope changes nothing.

    ⚠️ AND `escalated` HAS NOTHING TO DO WITH STATUS. It stays true after the
    ticket is solved and after it is closed, until somebody de-escalates it. So
    an agent whose OWN ticket was escalated keeps paying `escalation:write` on
    it forever — including on a ticket they themselves resolved — and for an
    ordinary support account that scope is usually unobtainable. The way back is
    `de_escalate_ticket` (which itself needs `escalation:write`) or the
    Ebteqdesk browser UI. If a reply on your own resolved ticket is refused
    naming `escalation:write`, this is why; it is not a transient error and
    retrying will not help.

    If you did not check, the refusal tells you: a failure naming
    `escalation:write` from THIS tool means the ticket is escalated, because
    that is the only way this endpoint can ask for that scope.

    ⚠️ THIS TOOL WRITES ONLY TO TICKETS ASSIGNED TO YOUR OWN ACCOUNT, escalated
    or not — unlike `add_private_note`. A ticket on the shared escalation queue
    that belongs to somebody else comes back 403 with `reason:
    "ticket_not_assigned"`, which is not a scope problem: a public reply to the
    requester on somebody else's ticket is theirs to send. Use
    `add_private_note` to record a finding on it instead.

    The reason is not red tape — Ebteqdesk quietly turns an ordinary agent reply
    on an escalated ticket into a private internal note, so rather than file a
    requester-facing reply where the requester will never see it, the API
    refuses.
    """
    return await _call(lambda client: client.comment_on_ticket(ticket_id, body=body))


@mcp.tool()
async def add_private_note(ticket_id: int, body: str) -> dict[str, Any]:
    """WRITES TO EBTEQDESK — files an INTERNAL note. The requester is NOT emailed.

    THIS IS THE SAFE COUNTERPART TO `comment_on_ticket`. That tool emails a
    public reply to the requester's address; this one records a note that only
    agents see. If what you want to write is an observation, a finding, a
    handover, or anything you would not want the requester to read, THIS is the
    tool — reaching for `comment_on_ticket` and hoping is how an internal remark
    gets mailed out.

    The note appears in `get_ticket`'s conversation with `kind: "note"`, and in
    the Ebteqdesk UI to agents. Nothing about it reaches the requester: the
    server skips the requester notification because the row is private. Your
    team, the ticket's assignee, and anyone you `@`-mention ARE still notified,
    so this is quiet, not silent.

    ⚠️ INTERNAL IS NOT REVERSIBLE. There is no delete-comment tool and no
    note-editing tool anywhere on this API — once filed, a note stays on the
    ticket and a person has to remove it in the Ebteqdesk UI, if they can. Never
    retry a call that timed out; read `get_ticket` to find out whether the first
    one landed.

    Requires EITHER the `ticket:write` scope OR the `escalation:write` scope to
    reach the endpoint at all, and then the ticket decides which one it costs:

      - a NON-escalated ticket assigned to this account → `ticket:write`
      - any ESCALATED ticket, whoever it is assigned to → `escalation:write`
        (plus `escalation:read` to reach it)

    Plus the `ticket.reply` ability on a non-escalated ticket, or
    `bp_escalation.reply` on an escalated one — the SAME authority a public reply
    needs, because Ebteqdesk gates both on it. A note is lighter in CONSEQUENCE,
    not in permission, so holding this does not mean you should file notes
    freely. The two scopes do NOT substitute for each other: a key holding only
    `escalation:write` cannot note on an ordinary ticket even one assigned to
    itself.

    Args:
        ticket_id: The numeric ticket id, from `list_tickets` or
            `list_escalations`.
        body: The note text. Required and non-blank.

    Returns 201 `{"data": {...}, "comment": {"id": …, "created_at": …}}` — the
    whole ticket plus a receipt, the same shape `comment_on_ticket` returns. The
    note body is not echoed back; this API never serialises comment text.

    ⚠️ `comment.id` CAN BE null, AND THAT MEANS NOTHING WAS FILED. A body that is
    empty in substance is discarded by the server. The call still answers 201 and
    the ticket is still touched, but no note row exists. If you get a null id,
    tell the user the note was not recorded — do not report it as saved.

    🔴 NOTING ON AN ESCALATED TICKET NEEDS `escalation:write`. Check the
    ticket's `escalated` field before you call: if it is true, a key holding only
    `ticket:write` cannot note on that ticket at all — re-minting with the same
    scope changes nothing. It also needs the `bp_escalation.reply` ability, which
    is the BP surface's.

    That is NOT because your note would be downgraded or exposed — it would not.
    It is that an escalated ticket belongs to the BP surface, and writing
    anything into a BP thread is a BP act, exactly as the Ebteqdesk browser
    requires.

    ⚠️ `escalated` HAS NOTHING TO DO WITH STATUS. It stays true after the ticket
    is solved and after it is closed, until somebody de-escalates it — so an
    agent's OWN resolved-but-escalated ticket still costs `escalation:write`,
    and for an ordinary support account that scope is usually unobtainable. The
    way back is `de_escalate_ticket` or the Ebteqdesk browser UI. This is not a
    transient error and retrying will not help.

    ⚠️ AND THIS TOOL REACHES FURTHER THAN THE OTHER TICKET WRITES. It can note on
    ANY escalated ticket, including one assigned to somebody else, which
    `list_tickets` never shows — and including one that has already been
    resolved, which `list_escalations` no longer shows either. Reaching one that
    way additionally needs `escalation:read`. Use it to record a finding on a
    queue ticket you are reviewing; do not use it to leave notes on other
    people's tickets they have not asked for.

    ⚠️ AN UNREACHABLE TICKET IS A 404 HERE, NOT A SCOPE REFUSAL. If your key
    cannot resolve `escalation:read`, an escalated ticket you may not have comes
    back with the same "There is no ticket with the id N" body as an id that
    does not exist. Do not read that 404 as proof the ticket is absent, and do
    not use this tool to probe whether an id is escalated — read `escalated` on
    a ticket a list tool actually gave you.
    """
    return await _call(lambda client: client.add_private_note(ticket_id, body=body))


@mcp.tool()
async def escalate_ticket(ticket_id: int) -> dict[str, Any]:
    """WRITES TO EBTEQDESK — escalates a ticket and NOTIFIES THE WHOLE BP TEAM.

    Puts the ticket on the escalation queue, stamps its escalation time, writes
    an "Escalated" entry into its history, and sends the `TicketEscalated`
    notification to every Assistant on the install. People get pinged.

    Requires the `escalation:write` scope and the `ticket.reply` ability, and it
    only works on a ticket ASSIGNED TO YOU.

    ⚠️ `ticket.reply` HERE, `bp_escalation.reply` ON `de_escalate_ticket`, AND
    THE ASYMMETRY IS DELIBERATE. Escalating creates work for other people and
    notifies every Assistant on the install; de-escalating closes out work you
    were already doing. An account that WORKS the escalation queue is the
    receiver of escalations, not a source of them, and typically cannot call
    this tool at all while being able to call `de_escalate_ticket` freely.

    🔴 NEVER RETRY THIS CALL BLIND. The stored state is idempotent — a second
    escalation keeps the original timestamp — but THE NOTIFICATION IS NOT. Call
    it twice and the team is alerted twice, with the second alert indicating
    nothing new.

    CHECK FIRST, AND CHECK AGAIN INSTEAD OF RETRYING. Every ticket payload
    carries an `escalated` boolean, so:

      - Before calling, read `escalated` on the ticket (from `list_tickets`,
        `list_tickets_by_category`, or any write tool's response). If it is
        already true, calling this again only re-notifies the team — don't.
      - If a call times out or fails ambiguously, DO NOT repeat it. Fetch the
        ticket and read `escalated` to find out whether the first one landed.

    Read `escalated`, never `escalated_at`. The timestamp is permanently null on
    every ticket escalated before that column existed, so treating "has a
    timestamp" as "is escalated" gets the longest-queued tickets exactly wrong.

    Args:
        ticket_id: The numeric ticket id, from `list_tickets`.

    Returns 200 `{"data": {...}}` — the ticket, in the `list_tickets` shape,
    with `escalated` now true and `escalated_at` stamped.
    """
    return await _call(lambda client: client.escalate_ticket(ticket_id))


@mcp.tool()
async def de_escalate_ticket(ticket_id: int) -> dict[str, Any]:
    """WRITES TO EBTEQDESK — takes a ticket back off the escalation queue.

    Clears the escalation and its timestamp, and writes a "De-Escalated" entry
    into the ticket's history. Unlike `escalate_ticket` this sends no
    notification, so a repeat call is cheaper — but it still appends a second
    history entry, and the ticket's escalation timestamp is GONE once this
    succeeds. Re-escalating afterwards starts the clock again from now, which
    will misreport how long the ticket actually sat escalated.

    Requires the `escalation:write` scope and the `bp_escalation.reply` ability
    — NOT `ticket.reply`, which is what ESCALATING costs.

    🔴 THE TWO DIRECTIONS ARE DELIBERATELY ASYMMETRIC. Escalating creates work
    for other people and notifies every Assistant on the installation, so it is
    gated on being a full participant in the ticket. De-escalating closes out
    work you were already doing: it notifies nobody, and the account best placed
    to say "this no longer needs an escalation" is the one holding it.

    ⚠️ SO THIS REACHES TICKETS `escalate_ticket` CANNOT. You may de-escalate any
    escalated ticket you can read, INCLUDING one assigned to somebody else — an
    account that works the escalation queue is never the assignee. You may only
    ESCALATE a ticket assigned to you.

    As with `escalate_ticket`, check `escalated` on the ticket rather than
    calling speculatively, and read `escalated` to confirm afterwards instead of
    repeating a call that timed out.

    Args:
        ticket_id: The numeric ticket id, from `list_tickets`.

    Returns 200 `{"data": {...}}` — the ticket, in the `list_tickets` shape,
    with `escalated` now false and `escalated_at` cleared to null.
    """
    return await _call(lambda client: client.de_escalate_ticket(ticket_id))


@mcp.tool()
async def set_ticket_status(
    ticket_id: int,
    status: Annotated[
        Literal[1, 2, 3, 8],
        WithJsonSchema(
            _enum_schema(
                WORKING_STATUSES,
                "The WORKING state to move it to. None of these emails anybody. "
                "8's name is the API's own `waitingOnCustomer` and is inherited, "
                "not renamed; on this internal desk it means waiting on the "
                "requester. To resolve a ticket instead, use `close_ticket`.",
            )
        ),
    ],
) -> dict[str, Any]:
    """WRITES TO EBTEQDESK — moves a ticket between working states. Emails nobody.

    The quiet write on this server. It changes which working state a ticket is
    in — new, open, pending, waiting on customer — and sends no mail to the
    requester, no agent notification and no survey. Every other write tool here
    warns you about who it contacts; this one is telling you it contacts nobody.
    (Status 8's name is the server-side API enum `waitingOnCustomer`; this
    package inherits it and cannot rename it. On an internal desk it means
    waiting on the requester.)

    WHAT IT CANNOT DO, AND THAT IS DELIBERATE. It will not mark a ticket SOLVED
    (4) or CLOSED (5) — those are `close_ticket`, which is the ONE tool on this
    server that can send the satisfaction survey, and keeping them there
    keeps that warning in one place instead of two. It will not mark a ticket
    merged (6) or spam (7) either: both are outcomes of other actions and are not
    settable through this API at all. Ask for any of the four and you get a 422
    naming the field, not a silent no-op.

    REOPENING IS `status=2`, AND IT COSTS MORE. A ticket that is currently
    solved, closed, merged or spam is being REOPENED, which needs the
    `ticket.close` ability on top of `ticket.reply` — the same ability resolving
    it cost, because it is the same boundary crossed the other way. So this can
    be refused on a resolved ticket by an account it works fine for on an open
    one, and the refusal names `ticket.close`.

    ⚠️ REVERSIBLE STATE, PERMANENT TRAIL. The status itself is fully reversible —
    call this again with the old value. The HISTORY IS NOT: every real change
    appends a `Status updated: <name>` entry to the ticket's thread, which anyone
    reading the ticket sees in `get_ticket` as an `event`. Flapping a ticket
    between two states leaves a permanent record of the flapping on a real
    requester's ticket. Decide the state you want, then set it once.

    ✅ AND THE NO-OP IS SAFE. Sending the status a ticket already holds writes
    nothing, appends no history entry, and still answers 200 — the server guards
    it, which is why this is a PUT. So unlike every other ticket write here, a
    call you are not sure landed can simply be repeated. (`reorder_kb_children`
    is the only other write on this server with that property.)

    ✅ AN ESCALATED TICKET NEEDS NOTHING EXTRA — and this is worth saying plainly,
    because `comment_on_ticket` and `add_private_note` both demand
    `escalation:write` on an escalated ticket and you may be expecting the same
    here. You are not: `ticket:write` is the whole scope requirement whatever the
    ticket's escalation state. What the BP surface treats differently is who may
    SPEAK to the requester, and this tool sends no message at all.

    Requires the `ticket:write` scope and the `ticket.reply` ability, plus the
    `ticket.close` ability when the ticket is currently resolved (see above).

    Args:
        ticket_id: The numeric ticket id, from `list_tickets`.
        status: 1 new, 2 open, 3 pending, or 8 waiting on customer — the last
            spelled the API's way (`waitingOnCustomer`), inherited rather than
            renamed. Nothing else is accepted, and a value outside those four is
            refused here rather than by the API. Use `close_ticket` for 4 and 5.

    Returns 200 `{"data": {...}}` — the ticket, in the `list_tickets` shape, with
    `status` updated so you can confirm the transition landed. No `comment` key:
    this writes no comment row.
    """
    return await _call(lambda client: client.set_ticket_status(ticket_id, status=status))


@mcp.tool()
async def close_ticket(
    ticket_id: int,
    status: Annotated[
        Literal[4, 5],
        WithJsonSchema(
            _enum_schema(
                CLOSE_STATUSES,
                "How to resolve it. 🔴 ONE OF THESE TWO VALUES EMAILS THE "
                "REQUESTER'S ADDRESS.",
            )
        ),
    ] = CLOSE_WITHOUT_SURVEY,
) -> dict[str, Any]:
    """WRITES TO EBTEQDESK — resolves a ticket and MAY EMAIL THE REQUESTER.

    Moves the ticket off the open queue and records the status change in its
    history.

    🔴 EXACTLY ONE STATUS VALUE SENDS OUTBOUND EMAIL TO THE REQUESTER'S ADDRESS,
    AND IT IS NOT THE DEFAULT ANY MORE.

      `status=5` (CLOSED) — THE DEFAULT. Resolves the ticket. Sends nothing.
      `status=4` (SOLVED) — resolves the ticket AND triggers the satisfaction
                            survey: a real email to the requester's address
                            asking them to rate the ticket.

    So calling this tool without naming a status contacts nobody. Ask for `4`
    only when a survey is genuinely wanted — and check with the user first,
    because the survey cannot be recalled and this is not a safe way to tidy up
    test data. That the requester is a colleague on an internal desk is not a
    reason to relax this: the mail leaves either way.

    ⚠️ THE SURVEY IS ENV-GATED SERVER-SIDE AND YOU CANNOT SEE THE GATE. An
    install may set `EBTEQDESK_RATING_EMAIL_ENABLED=false` to suppress it, but
    the CODE DEFAULT IS ON, nothing in this API's responses reports the setting,
    and this client has no way to read it. So treat `status=4` as sending mail
    unless a human tells you otherwise, and never report "the survey is
    disabled" as a fact you established.

    (The default used to be 4, which mailed the requester as a side effect of an
    argument the caller never set. The API's own default is still 4; this tool
    always sends the status explicitly, so the two cannot disagree.)

    Requires the `ticket:write` scope and the `ticket.close` ability. Note that
    `ticket.close` is a SEPARATE ability from `ticket.reply`: an account allowed
    to answer a requester is not automatically allowed to resolve their ticket,
    so this can be refused when `comment_on_ticket` on the same ticket works.

    Args:
        ticket_id: The numeric ticket id, from `list_tickets`.
        status: 5 to mark it CLOSED (the default, and the one that sends no
            email) or 4 to mark it SOLVED and send the survey. Nothing else is
            accepted — you cannot mark a ticket spam or merge it through this
            tool, and neither is settable through this API at all — and a value
            outside those two is refused here rather than by the API.

    REOPENING IS A DIFFERENT TOOL, NOT AN IMPOSSIBILITY. This tool cannot move a
    ticket back to a working state, but `set_ticket_status` can: it owns 1
    (new), 2 (open), 3 (pending) and 8 (waiting on customer — the API's own
    enum name), and sending it `status=2` reopens a ticket this tool resolved.
    The two are disjoint on purpose — 4 and 5 live here because this is the only
    tool that can email the requester's address a survey, and nothing else may
    reach it.

    CLOSING DOES NOT REPLY. This tool has no message argument, on purpose:
    answering the requester and resolving the ticket are two acts behind two
    different permissions. To do both, call `comment_on_ticket` first and then
    this — in that order, so the requester's last email is your answer rather
    than a survey.

    ⚠️ THE UNDERLYING REST ENDPOINT DOES ACCEPT A `body`, and this tool
    deliberately does not offer it. Sent raw, that field mails the text to the
    requester as a reply and, on an escalated ticket, costs `escalation:reply`
    exactly as `comment_on_ticket` does. It is withheld here because this
    server promises exactly TWO ways to write into a ticket —
    `comment_on_ticket` reaches the requester, `add_private_note` does not — and
    a third way to email somebody, through a tool named *close*, is how a
    message gets sent by an agent that believed it was filing one. Nothing is
    lost: two calls do the same work in an order a reviewer can see.

    Returns 200 `{"data": {...}}` — the ticket, in the `list_tickets` shape,
    with `status` updated so you can confirm the transition landed.
    """
    return await _call(lambda client: client.close_ticket(ticket_id, status=status))


# --------------------------------------------------------------------------- #
# THE PER-LANGUAGE CONTENT ARGUMENTS
# --------------------------------------------------------------------------- #
#
# 🔴 WHY EIGHT FLAT ARGUMENTS AND NOT ONE `translations` OBJECT.
#
# The server's shape is nested — `{"translations": {"en": {...}, "zhcn": {...}}}`
# — and the obvious tool signature is `translations: dict`. It was not taken.
# An MCP tool's arguments are filled in by a LANGUAGE MODEL reading a JSON
# schema, and the schema for a free-form nested map says `{"type": "object",
# "additionalProperties": true}`: no locale keys, no field names, no depth. Every
# way of getting it wrong is silent. A misspelt `zh-cn` VALIDATES (both spellings
# are real locale strings in this codebase; only one is this column's) and is
# then dropped by `Validator::validated()`, so the write returns 2xx having
# stored nothing. A title placed at the wrong depth does the same. This package
# already paid for that lesson once with `requester` and `priority` — see
# "Argument schemas the function signature cannot express" above, where a
# schema-reading client built `{"requester": "a@b.c"}` on a first call.
#
# Flat `en_title` / `zhcn_body` arguments cannot be got wrong that way. The
# locale is part of the ARGUMENT NAME, so it is in the schema's `properties`,
# enumerated by construction and validated by the SDK before the tool body runs
# — a `zh_cn_title` is an unknown argument and is refused by name rather than
# accepted and thrown away server-side. Each value is a plain optional string,
# which is the shape a model fills in most reliably.
#
# THE COST, STATED HONESTLY: the surface is EIGHT arguments per tool where a
# nested object would have been one, and it hardcodes the supported locale set
# in two more places (`PortalLocale::SUPPORTED` is `en` and `zhcn` and has been
# since the feature shipped). If a third language is ever added, this file grows
# four arguments per tool rather than nothing. That is the trade accepted: the
# set is closed, small, and server-enumerated already — `Literal["en", "zhcn"]`
# on `locale` has hardcoded it since the feature landed — and a wrong write here
# is invisible until a reader cannot find an article.
#
# ⚠️ THE NAME IS THE PATH, FLATTENED. `en_seo_title` is `translations.en.
# seo_title`, so the argument-naming rule for this surface still holds: a body
# field keeps the API's own name. Nothing is renamed, only unnested.


def _kb_versions(**flat: Any) -> dict[str, dict[str, Any]]:
    """Fold `en_title=…, zhcn_body=…` into `{"en": {"title": …}, "zhcn": {…}}`.

    A locale appears in the result only if at least one of its fields was given,
    so a call that named no language at all produces `{}` — which
    `_kb_translations` drops, sending NO `translations` key. Absence means "not
    editing versions" to the server and must not become an empty object.

    ⚠️ THIS CANNOT PRODUCE THE `null` DELETE, and that is deliberate rather than
    an omission. `translations.{locale} = null` removes a language version and
    hides the article from that language for every reader; the /api/v1
    controller refuses it and it stays a human act in the authoring screens. The
    client's `_kb_translations` can still express it — the shape must match the
    server's — but no tool here offers a way to say it.
    """
    versions: dict[str, dict[str, Any]] = {}

    for name, value in flat.items():
        if value is None:
            continue

        locale, _, field = name.partition("_")
        versions.setdefault(locale, {})[field] = value

    return versions


@mcp.tool()
async def propose_kb_article(
    kb_folder_id: int,
    title: str,
    body: str | None = None,
    seo_title: str | None = None,
    seo_description: str | None = None,
    tags: list[str] | None = None,
    locale: Literal["en", "zhcn"] | None = None,
    en_title: str | None = None,
    en_body: str | None = None,
    en_seo_title: str | None = None,
    en_seo_description: str | None = None,
    zhcn_title: str | None = None,
    zhcn_body: str | None = None,
    zhcn_seo_title: str | None = None,
    zhcn_seo_description: str | None = None,
) -> dict[str, Any]:
    """WRITES TO EBTEQDESK — files a REAL draft article into a human's review queue.

    A row is created in the live knowledge base and a person is expected to read
    it. It is not published and cannot be read outside the desk, but it is real
    work landing on somebody's desk, and there is no delete-article tool here — a
    draft filed by mistake has to be removed by a human in the authoring UI.
    Show the user the article you intend to file before filing it.

    🔴 THIS CANNOT PUBLISH, AND NOTHING ON THIS API CAN. Every article this
    tool creates lands `status = draft`, `review_state = pending`. Publishing is
    a human's browser session, deliberately: an integration that could publish
    would be able to put unreviewed text on the public help portal, where
    signed-out visitors read it. Do not tell a user their article
    is live, and do not promise when it will be — you cannot know.

    ⚠️ `update_kb_article` CANNOT PUBLISH EITHER, BUT IT NO LONGER ALWAYS LANDS
    A DRAFT. Against an already-PUBLISHED article it stages a pending REVISION
    and leaves the live text alone, answering 202 with the unchanged article. It
    still cannot make anything live; a human approves the revision, and doing so
    changes a page customers are already reading. See that tool.

    Requires the `kb:write` scope, which resolves only while the key carries it
    AND the account's role carries `kb.manage`.

    Args:
        kb_folder_id: REQUIRED, the numeric id of an existing KB folder. 🔴 THE
            ONE FIELD THAT CANNOT BE CHANGED LATER — `update_kb_article` does
            not accept it. The folder decides the article's VISIBILITY (an
            article can carry its own, overriding the folder's, but NOTHING ON
            THIS API CAN SET ONE — see below), so moving an article is a
            visibility change wearing an organisational costume and stays a
            human act. Choose deliberately; if you are unsure which folder is
            right, ask the user rather than guessing.
        title: REQUIRED, max 255 characters.
        body: The article body as HTML, not markdown — Ebteqdesk stores HTML and
            has no markdown form. It is SANITISED on write, so what comes back
            in `body_html` may not be what you sent; read the response rather
            than assuming.
        seo_title: Optional, max 70 characters.
        seo_description: Optional, max 160 characters.
        tags: Optional list of tag strings. REPLACES the whole set — passing
            `[]` clears every tag rather than leaving them alone. Omit the
            argument to leave tags untouched.
        locale: `"en"` or `"zhcn"` (Simplified Chinese), or omitted. The
            ONE-LANGUAGE form: it files `title`/`body` as that single language's
            own version. THIS CHANGES WHAT THE ARTICLE IS, not just where the
            text goes — read the blocks below before using it. For TWO
            languages use the per-language arguments instead; naming the same
            language both ways is refused rather than silently resolved.
        en_title, en_body, en_seo_title, en_seo_description: the ENGLISH
            version's own fields. `en_title` is required for an English version
            (a version with no title cannot be stored).
        zhcn_title, zhcn_body, zhcn_seo_title, zhcn_seo_description: the
            SIMPLIFIED CHINESE version's own fields, same rule for
            `zhcn_title`. 🔴 SEND THESE ALONGSIDE THE `en_*` ONES TO FILE A
            BILINGUAL ARTICLE IN THIS ONE CALL.

    🔴 WHAT THE LANGUAGE ARGUMENTS DO, AND WHAT OMITTING THEM ALL DOES

    NONE OF THEM GIVEN — the article has NO language version and shows in EVERY
    language from one shared text. This is the default and it is what most
    articles are; it is also exactly what this tool did before per-language
    content existed.

    GIVEN — the text becomes that language's OWN version, and an article with a
    version in any language appears ONLY in the languages it has one for. So
    `zhcn_*` alone (or `locale="zhcn"`) files an article the ENGLISH help centre
    will not show at all. That is a legitimate thing to file — a Chinese-only
    notice — but it is a decision, so make it deliberately and say so to the
    user.

    🔴 TO FILE THE ARTICLE IN BOTH LANGUAGES, SEND BOTH IN THIS ONE CALL:
    `en_title`/`en_body` AND `zhcn_title`/`zhcn_body` together. Do NOT file one
    language now and add the other with `update_kb_article` later. That two-call
    habit is documented in older notes and it is wrong: the moment a human
    publishes the article in between, the second call no longer edits it — it
    stages a REVISION, there is one revision row per article, and a revision
    carrying only the second language is refused for taking the article out of
    the first language's help centre. One call, both languages, every time you
    mean both.

    ⚠️ THE LANGUAGE OF THE TEXT IS NOT DETECTED AND CANNOT BE. The argument you
    put the text in is what you are ASSERTING the text is. English prose in
    `zhcn_body` is filed as the Chinese version and nothing will notice —
    readers will, eventually.

    ⚠️ `title` (the plain one) IS STILL REQUIRED AND IS NOT A LANGUAGE VERSION.
    It writes the article's BASE columns, which are NOT NULL and which ten
    surfaces read directly — the review queue, the authoring tree, the slug at
    first publish. When you send an `en_*` version the server mirrors it onto
    those columns anyway, so passing the English text as both is correct and is
    what the client does for `locale=` too.

    You cannot set `source`; it is forced to `api` server-side, so the article
    is permanently identifiable as machine-written. That is not something to
    work around.

    🔴 YOU CANNOT SET `visibility` EITHER, AND SENDING IT IS SILENTLY IGNORED —
    not rejected, so a 201 does not mean it was applied. An article filed here
    always INHERITS its folder, and who may read the knowledge base is decided by
    a human in the Ebteqdesk authoring screens. Do not tell a user you have made
    an article public or internal, and do not retry with the field spelled
    differently: no key of any kind can set it.

    ⚠️ THERE IS NO WAY TO REMOVE A LANGUAGE VERSION THROUGH THIS API, on this
    tool or any other. Removing one hides the article from that language for
    every reader, and it stays a human act in the Ebteqdesk authoring screens —
    the same reason there is no delete-article tool here.

    Returns 201 `{"data": {...}}` with `reference` (e.g. `"id:42"`) — KEEP IT.
    It is what `update_kb_article` and `get_kb_article_review` take, and `slug`
    is null until a human first publishes, so the reference is the only handle
    you have. `data.translations` lists the language versions the article now
    has, each with that language's title and body — read it to confirm your
    `locale` landed where you meant, because `data.title` and `data.body_html`
    are the article's BASE text and are not necessarily what a reader gets.
    """
    return await _call(
        lambda client: client.propose_kb_article(
            kb_folder_id=kb_folder_id,
            title=title,
            body=body,
            seo_title=seo_title,
            seo_description=seo_description,
            tags=tags,
            locale=locale,
            translations=_kb_versions(
                en_title=en_title,
                en_body=en_body,
                en_seo_title=en_seo_title,
                en_seo_description=en_seo_description,
                zhcn_title=zhcn_title,
                zhcn_body=zhcn_body,
                zhcn_seo_title=zhcn_seo_title,
                zhcn_seo_description=zhcn_seo_description,
            ),
        )
    )


@mcp.tool()
async def update_kb_article(
    reference: str,
    title: str | None = None,
    body: str | None = None,
    seo_title: str | None = None,
    seo_description: str | None = None,
    tags: list[str] | None = None,
    locale: Literal["en", "zhcn"] | None = None,
    en_title: str | None = None,
    en_body: str | None = None,
    en_seo_title: str | None = None,
    en_seo_description: str | None = None,
    zhcn_title: str | None = None,
    zhcn_body: str | None = None,
    zhcn_seo_title: str | None = None,
    zhcn_seo_description: str | None = None,
    allow_missing_versions: bool = False,
) -> dict[str, Any]:
    """WRITES TO EBTEQDESK — rewrites a DRAFT article in place, or STAGES an
    edit to a PUBLISHED one for a human to approve.

    Which of the two happens is NOT your choice and is not an argument. It is
    the article's own `status`, which you may not know before you call. The two
    outcomes are told apart by ONE key in the response and by nothing else.

    🔴 THE TWO BRANCHES.

      DRAFT — the article's text is REPLACED in the live knowledge base and the
      article goes back into a human's review queue. HTTP 200. The response has
      exactly ONE top-level key, `data`, and `data` is what you just wrote.

      PUBLISHED — the live article is NOT TOUCHED. Not the title, not the body,
      not `updated_at`, not `status`. Your edit is written to a separate PENDING
      REVISION, and a human approves or rejects it in the authoring UI while
      customers go on reading the old text byte for byte. HTTP 202. The response
      has TWO top-level keys, `data` and `revision`.

    🔴 ON THE PUBLISHED BRANCH `data` IS NOT WHAT YOU SENT — IT IS WHAT
    CUSTOMERS ARE STILL READING. If you take one thing from this description,
    take this. `data.title` is the OLD title, `data.body_html` is the OLD body,
    and `data.translations` may be `[]` while the Chinese version you just
    submitted sits unapplied inside `revision`. A model that reads `data` back
    to confirm its own edit will find none of its text there and conclude that
    nothing happened — and if it then resends, it REPLACES the revision it
    already staged rather than adding anything. `data` answers "what is live
    right now"; `revision` answers "what happened to my edit".

    ⚠️ SO CHECK FOR THE `revision` KEY, NOT FOR CHANGED TEXT IN `data`. Its
    presence is the entire signal: present means STAGED AND NOT APPLIED, absent
    means applied in place. Nothing inside `data` distinguishes the two.

    ⚠️ AND TELL THE USER WHICH ONE HAPPENED. "I updated the article" is false on
    the published branch and a user will act on it. Say the edit is waiting for
    a reviewer and that the live page is unchanged until they accept it. You
    cannot approve it and cannot know when they will.

    🔴 THERE IS NO 409 ON THIS TOOL ANY MORE. A published article used to be
    REFUSED outright — older notes, older client code and older habits all say
    so, and they are wrong now. It is staged instead. If you were built to catch
    that refusal and tell the user "this needs a person in the authoring UI",
    half of that is still true (a person still has to approve it) but the edit
    is now genuinely sitting in their queue rather than lost, so say that.

    There is no undo and no version history through this API, on either branch.

    🔴 ON A DRAFT, EVERY UPDATE RESETS THE REVIEW, WHATEVER STATE IT WAS IN.
    Back to `status = draft`, `review_state = pending`, and ANY PREVIOUS
    APPROVAL OR REJECTION NOTE IS CLEARED. That is not a quirk: without it,
    "approve once, then rewrite freely" would be the whole workflow defeated.

    ⚠️ AND THERE IS ONE REVIEW STATE PER ARTICLE, NOT ONE PER LANGUAGE. Fixing a
    typo in the Chinese version sends the ENGLISH version back to pending too,
    and a human has to look at the whole article again. There is no per-language
    review and no way to ask for one.

    🔴 SO PUT EVERY LANGUAGE IN ONE CALL — `en_title`/`en_body` AND
    `zhcn_title`/`zhcn_body` TOGETHER. On a DRAFT two calls merely put the
    article through review twice. On a PUBLISHED article two calls LOSE A
    LANGUAGE, and this is the single most important thing on this tool:

      1. each call STAGES a revision instead of writing the article;
      2. there is ONE revision row per article, so the second call REPLACES the
         first — English then Chinese leaves a revision holding only Chinese;
      3. that Chinese-only revision is then REFUSED anyway, because applying it
         would take the article out of the ENGLISH help centre;
      4. and `allow_missing_versions=True` gets past the refusal by DOING that
         removal — the exact opposite of "add a Chinese version".

    There is no order of two calls that works. The per-language arguments exist
    because one call is the only shape that can carry a bilingual edit at all.

    🔴 SO DO NOT CALL THIS TO CHECK A VERDICT. Reading the review state through
    an update destroys the review state — including the rejection note you were
    trying to read. Use `get_kb_article_review`, which changes nothing and
    exists precisely for this.

    🔴 THAT APPLIES TWICE OVER ON A PUBLISHED ARTICLE, and it is the one thing
    the staging branch made WORSE rather than better. There is ONE revision row
    per article, so a second call REPLACES the pending revision instead of
    queueing a second one — and it replaces a REJECTED one too, clearing the
    reviewer's note in the same breath. Nothing warns you; you get a 202 and a
    fresh `pending`. Read the verdict with `get_kb_article_review` FIRST, and
    only then send the revised text.

    ⚠️ A MALFORMED EDIT TO A LIVE ARTICLE IS A 422 AND STAGES NOTHING. Validation
    runs before the staging branch, so an invalid body neither touches the live
    article nor disturbs whatever revision was already staged — including a
    rejected one you had not read yet. Fix the payload and send it again. (This
    is where the old 409 used to win the race and answer first; it does not
    exist any more, so the refusal you now get names the actual field.)

    Requires the `kb:write` scope.

    Args:
        reference: REQUIRED. The frozen slug, OR the `id:<n>` form (e.g.
            "id:42") that `propose_kb_article` returns as `reference`. A slug is
            frozen at FIRST PUBLISH, so an article created through this API has
            `slug: null` and `id:<n>` is the normal case — do not try to
            construct a slug from the title.
        title: New title. Max 255.
        body: New body, HTML, sanitised on write.
        seo_title: Max 70.
        seo_description: Max 160.
        tags: REPLACES the whole set; `[]` clears every tag.
        locale: `"en"` or `"zhcn"` (Simplified Chinese) — the ONE-LANGUAGE
            form: `title`/`body` are written as that language's version instead
            of as the shared base text. Unchanged and still right when you mean
            exactly one language. To write TWO languages use the per-language
            arguments below; naming the same language both ways is refused
            rather than silently resolved.
        en_title, en_body, en_seo_title, en_seo_description: the ENGLISH
            version's own fields. Any of them present writes/updates the English
            version. `en_title` is required whenever an English version is
            being written — a version cannot be stored without one.
        zhcn_title, zhcn_body, zhcn_seo_title, zhcn_seo_description: the
            SIMPLIFIED CHINESE version's own fields, same rule for `zhcn_title`.
            🔴 SEND THESE ALONGSIDE THE `en_*` ONES AND BOTH LANGUAGES TRAVEL IN
            ONE REQUEST — the only shape that can edit a published bilingual
            article, and the repair for the 422 below.
        allow_missing_versions: Opt in to taking the article OUT of a language's
            help centre. Leave it False unless a 422 has told you to set it and
            you have decided that removal is what you want. See below.

    🔴 IF THE ARTICLE HAS LANGUAGE VERSIONS, AN EDIT WITH NO LANGUAGE ARGUMENT
    REACHES NOBODY.

    An article that has its own version in a language is served from that version
    and NOT from the shared base text. So a `title=`/`body=` edit with no
    `locale` and no `en_*`/`zhcn_*` on such an article writes text no reader will
    ever see, in either language, and returns 200 while doing it. Nothing in the
    response says so. Check `data.translations` — from a previous call, or from
    `get_kb_article_review` — BEFORE editing: if it is non-empty, your edit needs
    to name its language. If it is `[]`, the article has no versions and the
    plain `title`/`body` are correct.

    ⚠️ THE PLAIN `title`/`body` AND THE `en_*`/`zhcn_*` ONES ARE NOT THE SAME
    EDIT AND CAN BE SENT TOGETHER. The plain ones write the BASE columns; the
    per-language ones write versions. Sending both is legal and is what the
    authoring editor itself posts. `locale=` is the exception: it MOVES the
    plain fields into that one version rather than writing the base, because on
    an article that already has versions the base holds the other language's
    text and overwriting it would corrupt that language.

    🔴 GIVING AN ARTICLE ITS FIRST VERSION REMOVES IT FROM THE OTHER LANGUAGE.

    An article with a version in ANY language appears ONLY in the languages it
    has one for. So adding `locale="zhcn"` to an article that had no versions
    takes it out of the ENGLISH help centre — instantly, for every reader.

    The server REFUSES that write with a 422 naming the language that would lose
    the article. Two ways past it and they are NOT interchangeable:

      - SEND THE OTHER LANGUAGE'S VERSION IN THE SAME CALL. `en_title`/`en_body`
        beside `zhcn_title`/`zhcn_body`. This is the repair, and it is almost
        always what was meant. It has to be the same call: on a published
        article a follow-up call replaces the revision rather than adding to it.
        You need the English text to send it — read it first with
        `get_kb_article_review` (or `get_kb_article`) and pass it back unchanged
        if you are not editing it. Nothing here copies the base text into a
        version for you.
      - `allow_missing_versions=True`. This is "yes, remove it from that help
        centre". Do not set it to get past an error message — on the bilingual
        edit this tool exists for, setting it performs exactly the loss the
        refusal was preventing. Tell the user which language loses the article
        and get their answer first.

    ⚠️ Editing an article that is ALREADY single-language is not refused, because
    nothing is being taken away.

    ⚠️ THERE IS NO WAY TO REMOVE A LANGUAGE VERSION THROUGH THIS API, here or on
    any other tool. Removing one hides the article from that language for every
    reader and cannot be undone from here; it stays a human act in the Ebteqdesk
    authoring screens, like publishing and like deleting an article. So do not
    add a version on the assumption that you can take it back.

    ⚠️ THE LANGUAGE OF YOUR TEXT IS NOT DETECTED. The argument you put the text
    in is what you ASSERT the text is; English prose in `zhcn_body` is stored as
    the Chinese version without complaint.

    OMITTED ARGUMENTS ARE NOT EDITED; an argument passed as an EMPTY STRING is
    an edit that clears the field. So sending only `title` leaves the body
    exactly as it was — you do not need to resend the whole article, and
    resending a stale copy of the body is how an edit silently reverts somebody
    else's. The same rule applies WITHIN each language's version: sending only
    `zhcn_title` leaves the Chinese body alone.

    ⚠️ THE ONE PLACE THAT RULE DOES NOT SAVE YOU is the missing-version repair
    above, where the English version does not exist YET. "Not edited" and "does
    not exist" are different, and creating a version needs at least its title.

    `kb_folder_id` is deliberately absent: an article cannot be moved through
    this API. See `propose_kb_article`.

    🔴 `visibility` IS ABSENT FOR THE SAME REASON, AND SENDING IT IS SILENTLY
    IGNORED. An article can carry its own visibility overriding its folder's, and
    a human may already have set one on this article — a `visibility` key here
    would be an attempt to overwrite that decision, and the server drops it. So a
    200 never means visibility changed. Never tell a user you have made an
    article public or internal through this tool.

    ------------------------------------------------------------------
    WHAT COMES BACK
    ------------------------------------------------------------------

    DRAFT, 200 — `{"data": {...}}` and NO `revision` key. `data` is the written
    shape: your edit, with `review.state` back to `pending` so you can confirm
    the resubmission landed, and `data.translations` the language versions AFTER
    the edit. This is the branch where reading your own text back is meaningful.

    PUBLISHED, 202 — `{"data": {...}, "revision": {...}}`. `data` is the LIVE
    ARTICLE, UNCHANGED: the old title, the old body, the old `updated_at`,
    `status: "published"`, and `translations` as they were — very often `[]`
    even though the version you just sent is the whole point of the call. Do not
    diff it against your submission and do not report it as the result of your
    edit. `revision` is the result of your edit:

      - `revision.state` — `"pending"` here, always, on a fresh stage.
      - `revision.source` — `"api"` for one this tool staged; `"manual"` for one
        a human staged in the authoring UI. If you get `"manual"` back you have
        just overwritten a person's staged draft.
      - `revision.requested_at`, `reviewed_at`, `reviewed_by`, `note` — the
        review trail, empty on a fresh stage. Poll them with
        `get_kb_article_review`, never by calling this tool again.

    ⚠️ `data.review` AND `revision` ARE DIFFERENT BLOCKS AND ONLY ONE OF THEM IS
    ABOUT YOUR EDIT. `data.review` is the ARTICLE's own review record, and on a
    published article it is typically `{"state": "none", ...}` — the article was
    approved and published long ago. It does not become `pending` because you
    staged a revision, and reading it as your verdict is a mistake. `revision`
    is your edit's record.

    On both branches, `data.url` is the article's public page or null; it
    reflects the article's EFFECTIVE visibility (its own where a human set one,
    its folder's otherwise), so it is the field to read if you need to know
    whether the article is reachable — never infer it from the folder's
    `visibility` in `list_kb_tree`.

    `data.translations` carries each language version's own title and body. READ
    IT rather than `data.title` / `data.body_html`, which are the base text: on
    an article with versions those two are not what any reader gets, and
    confirming your edit against them is how a Chinese revision gets reported as
    landed when it went nowhere. 🔴 ON THE 202 IT IS NOT A CONFIRMATION AT ALL —
    it is the live set, which by definition does not include what you just
    staged. To read one language back as a READER sees it, use
    `get_kb_article(slug, locale=…)`; on a published article that is the check
    that actually proves whether a revision has been applied yet.
    """
    return await _call(
        lambda client: client.update_kb_article(
            reference,
            title=title,
            body=body,
            seo_title=seo_title,
            seo_description=seo_description,
            tags=tags,
            locale=locale,
            translations=_kb_versions(
                en_title=en_title,
                en_body=en_body,
                en_seo_title=en_seo_title,
                en_seo_description=en_seo_description,
                zhcn_title=zhcn_title,
                zhcn_body=zhcn_body,
                zhcn_seo_title=zhcn_seo_title,
                zhcn_seo_description=zhcn_seo_description,
            ),
            allow_missing_versions=allow_missing_versions,
        )
    )


@mcp.tool()
async def create_kb_category(
    name: str, description: str | None = None
) -> dict[str, Any]:
    """WRITES TO EBTEQDESK — creates a REAL category in the live knowledge base.

    A category is the top of the knowledge base structure and every agent who
    opens the KB sees it. One created by mistake can be removed with
    `delete_kb_category`, but ONLY WHILE IT IS EMPTY: Ebteqdesk REFUSES to delete
    a category that still holds folders, and there is no undo once it does go.
    Emptying it means deleting each folder, and a folder holding articles is
    refused in turn — there is no delete-article tool on this API at all, so that
    last step needs a person in the Ebteqdesk web UI. Confirm the name with the
    user before calling, and never retry a call that timed out; fetch
    `list_kb_tree` to see whether the first one landed.

    Create one only when `list_kb_tree` shows no suitable category. Filing into
    an existing one is almost always right — a knowledge base whose structure
    grows a category per article is worse than one with none.

    Requires the `kb:write` scope, which resolves only while the key carries it
    AND the account's role holds `kb.manage`. That ability is granted to
    ADMINISTRATOR AND SUPERVISOR ONLY, so an agent- or developer-role account is
    refused here whatever key it holds — and a new key will not fix it.

    Args:
        name: REQUIRED, max 120 characters.
        description: Optional, max 255 characters. Omit it to leave it empty.

    🔴 THE SLUG IS DERIVED FROM THE NAME AND CANNOT BE SET. A collision is a 422
    on `name`, and it is checked against the DERIVED slug rather than the name —
    so "POS" and "  p.o.s!  " COLLIDE even though they are different strings.
    Category slugs are unique GLOBALLY (folder slugs are not — see
    `create_kb_folder`). If you get that 422, a category with that name already
    exists: read `list_kb_tree` and use it rather than inventing a variant.

    Returns 201 `{"data": {...}}` with the new `id`, which is what
    `create_kb_folder` takes as `kb_category_id`. `folders` is `[]` — a new category
    holds nothing, and `propose_kb_article` needs a FOLDER, so this call alone
    does not let you file an article.
    """
    return await _call(
        lambda client: client.create_kb_category(name=name, description=description)
    )


@mcp.tool()
async def update_kb_category(
    category_id: int,
    name: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """WRITES TO EBTEQDESK — renames a REAL category, CHANGING ITS PORTAL URL.

    🔴 RENAMING RE-DERIVES THE SLUG, AND THE SLUG IS PART OF A PUBLIC URL. This
    is the thing that makes "rename" sound harmless when it is not. Unlike an
    ARTICLE — whose slug is frozen at first publish and never moves again — a
    category slug follows its name on every save, and it is a segment of the
    portal address of every folder page beneath it. So renaming "POS" to "Point
    of Sale" moves `/support/kb/pos/...` to `/support/kb/point-of-sale/...` and
    any link a colleague or a signed-out visitor saved stops working. There is
    no redirect.
    Say so before you do it, and check the `slug` in the response to see what it
    became.

    There is no undo: a rename you did not mean is reversed only by renaming it
    back, which moves the URL a second time. `delete_kb_category` removes the
    category itself, but it is REFUSED while the category holds folders and is
    permanent when it is not — it is not a way to undo a rename. And emptying a
    category ends at a folder full of articles, which no tool here can clear:
    there is no delete-article tool on this API, so that step is a person's job
    in the Ebteqdesk web UI. Never retry a call that timed out; read
    `list_kb_tree` to see whether the first one landed.

    Requires the `kb:write` scope, resolving only while the key carries it AND
    the account's role holds `kb.manage` — ADMINISTRATOR AND SUPERVISOR ONLY. An
    agent- or developer-role account is refused here whatever key it holds.

    Args:
        category_id: REQUIRED, the numeric category id from `list_kb_tree`.
            There is no lookup by name or slug.
        name: New name, max 120. OMIT IT to leave the name — and therefore the
            slug and the URL — completely alone.
        description: New description, max 255.

    OMITTED ARGUMENTS ARE NOT EDITED. Passing only `description` leaves the name
    and the slug untouched, which is the safe way to annotate a category. An
    argument passed as an EMPTY STRING is an edit that CLEARS the field, so
    `description=""` removes the description.

    A name colliding on the derived slug with ANOTHER category is a 422 on
    `name`; re-saving a category under its own name is not a collision. An id
    that does not exist is a 404.

    Returns 200 `{"data": {...}}` with the re-derived `slug`.
    """
    return await _call(
        lambda client: client.update_kb_category(
            category_id, name=name, description=description
        )
    )


@mcp.tool()
async def delete_kb_category(category_id: int) -> dict[str, Any]:
    """WRITES TO EBTEQDESK — PERMANENTLY DELETES a REAL category. NO UNDO.

    🔴 THIS DESTROYS STRUCTURE IN A LIVE KNOWLEDGE BASE AND CANNOT BE REVERSED
    THROUGH THIS API. There is no trash, no restore and no version history: once
    this returns, the category is gone and the response is the only record of
    what it was. Nothing on this server can recreate it in place —
    `create_kb_category` would make a NEW row with a new id, at the end of the
    list. NAME THE CATEGORY YOU ARE ABOUT TO DELETE AND GET THE USER'S AGREEMENT
    FIRST. Read `list_kb_tree` if you are not certain the id is
    the one they meant.

    🔴 REFUSED WHILE THE CATEGORY STILL HOLDS FOLDERS. It is a refusal and NEVER
    a cascade: a category with folders under it comes back as a 422 naming the
    count — "This category still holds 2 folders. Move or delete them first." —
    and NOTHING is deleted. To proceed, the folders have to go first, one at a
    time, and each of those is refused in turn while it holds articles. That
    chain is a safety property, not an obstacle to route around: THERE IS NO
    DELETE-ARTICLE TOOL ON THIS API AT ALL, so a category with content in it can
    only be emptied by a person in the Ebteqdesk web UI. Do not start deleting
    folders to make this call succeed unless the user asked for exactly that.

    Never retry a call that timed out — a delete that appears to have failed may
    well have landed, and retrying it would either report a confusing 404 or, if
    the id has been reused, remove something else. Read `list_kb_tree` instead.

    Requires the `kb:write` scope, which resolves only while the key carries it
    AND the account's role holds `kb.manage` — ADMINISTRATOR AND SUPERVISOR
    ONLY. An agent- or developer-role account is refused whatever key it holds,
    and a new key will not fix that.

    Args:
        category_id: REQUIRED, the numeric category id from `list_kb_tree` or
            `list_kb_categories`. There is no lookup by name or slug, and there
            is no other argument — a delete has nothing to move, nothing to
            re-scope and no cascade flag. An id that does not exist is a 404.

    Returns 200 `{"data": {...}}` — a receipt for a row that no longer exists,
    in the same shape `create_kb_category` answers with. `folders` is `[]`, which
    it always is here because a category holding any could not have been deleted.
    `position` is the index the category VACATED: every category that sat after
    it has already moved up by one, so any positions you were holding on to are
    now stale.
    """
    return await _call(lambda client: client.delete_kb_category(category_id))


@mcp.tool()
async def create_kb_folder(
    kb_category_id: int, name: str, description: str | None = None
) -> dict[str, Any]:
    """WRITES TO EBTEQDESK — creates a REAL folder, INTERNAL and invisible
    outside the desk.

    The folder appears in the live knowledge base for every agent. One created by
    mistake can be removed with `delete_kb_folder`, but ONLY WHILE IT IS EMPTY:
    Ebteqdesk REFUSES to delete a folder that holds articles, and there is no
    delete-article tool on this API at all, so a folder that has been filed into
    can only be emptied by a person in the Ebteqdesk web UI. The delete itself
    has no undo. Confirm with the user before calling, and never retry a call
    that timed out; read `list_kb_tree` to find out whether the first one landed.

    🔴 THE FOLDER IS CREATED `agents` — INTERNAL — AND THERE IS NO PARAMETER TO
    CHANGE THAT. Not here and not on `update_kb_folder`. A folder decides who can
    see the articles inside it, and publishing content where anyone outside the
    desk can read it is a human's decision, not an integration's — the same reason nothing on this API can
    publish an article.

    THE CONSEQUENCE YOU MUST TELL THE USER: nothing filed into this folder
    THROUGH THIS API will EVER reach a reader outside the desk until a human
    acts in the Ebteqdesk web UI. Even a published article in it stays internal.
    So do not tell a user their article will be visible outside the desk, and do
    not describe this folder as public — check `visibility` in `list_kb_tree` if you need to
    know where a folder stands.

    ⚠️ A HUMAN HAS TWO WAYS TO CHANGE THAT, AND ONLY ONE OF THEM SHOWS UP IN
    THIS FOLDER'S `visibility`: they can open the folder up, or they can give
    ONE ARTICLE its own visibility, which overrides the folder's. So a folder
    still reading `agents` here may hold an article a human has made public —
    readable by a signed-out visitor on the help portal. That
    is not something this API can do or undo.

    Requires the `kb:write` scope, which resolves only while the key carries it
    AND the account's role holds `kb.manage` — ADMINISTRATOR AND SUPERVISOR
    ONLY. An agent- or developer-role account is refused here whatever key it
    holds, and minting a new key will not change that.

    Args:
        kb_category_id: REQUIRED, the numeric id of an existing category, from
            `list_kb_tree` (or from `create_kb_category`'s response). An id that
            does not exist is a 422 naming this same field, not a 404. It is
            spelled the API's way — like `propose_kb_article`'s `kb_folder_id` —
            because it is a BODY field the server quotes back in its errors; the
            short `folder_id` / `category_id` form is for the ids that go in the
            URL, on the two update tools.
        name: REQUIRED, max 120 characters.
        description: Optional, max 255 characters.

    🔴 THE SLUG IS DERIVED FROM THE NAME AND CANNOT BE SET. A collision is a 422
    on `name`, checked against the DERIVED slug — so "Errors" and "  errors!  "
    COLLIDE. Folder slugs are unique ONLY WITHIN THEIR CATEGORY, unlike category
    slugs which are global: "FAQ" under Billing and "FAQ" under Account are both
    fine, and that is deliberate.

    Returns 201 `{"data": {...}}`. **The `id` in that response is the
    `kb_folder_id` `propose_kb_article` takes**, so you can create a folder and
    file into it in two calls. `visibility` will read `agents`; that is expected,
    not a failure.
    """
    return await _call(
        lambda client: client.create_kb_folder(
            kb_category_id=kb_category_id, name=name, description=description
        )
    )


@mcp.tool()
async def update_kb_folder(
    folder_id: int,
    name: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """WRITES TO EBTEQDESK — renames a REAL folder, CHANGING ITS PORTAL URL.

    🔴 RENAMING RE-DERIVES THE SLUG, AND THE SLUG IS PART OF A PUBLIC URL —
    exactly as on `update_kb_category`, and it is the reason "rename" is not the
    harmless-sounding operation it appears to be. An ARTICLE's slug is frozen at
    first publish; a FOLDER's follows its name on every save, so renaming a
    folder moves the portal address of the pages under it and any saved link
    breaks. There is no redirect. Check the `slug` in the response to see what it
    became, and say so to the user.

    There is no undo: a rename is reversed only by renaming back, which moves the
    URL again. `delete_kb_folder` removes the folder itself, but it is REFUSED
    while the folder holds articles — and there is no delete-article tool, so
    emptying one is a person's job in the Ebteqdesk web UI. Never retry a call
    that timed out; read `list_kb_tree` instead.

    🔴 THIS CANNOT MOVE A FOLDER AND CANNOT CHANGE ITS VISIBILITY. There is no
    argument for either, deliberately. A folder decides who sees the articles
    inside it, so both edits are access-control decisions that stay with a human
    in the Ebteqdesk UI. If a user asks you to make a folder public or to move it
    to another category, tell them it has to be done in Ebteqdesk.

    Requires the `kb:write` scope, resolving only while the key carries it AND
    the account's role holds `kb.manage` — ADMINISTRATOR AND SUPERVISOR ONLY.

    Args:
        folder_id: REQUIRED, the numeric folder id from `list_kb_tree`
            (`folders[].id`). There is no lookup by name or slug.
        name: New name, max 120. OMIT IT to leave the name — and therefore the
            slug and the URL — alone.
        description: New description, max 255.

    OMITTED ARGUMENTS ARE NOT EDITED; an argument passed as an EMPTY STRING is an
    edit that CLEARS the field, so `description=""` removes the description.

    A name colliding on the derived slug with another folder IN THE SAME CATEGORY
    is a 422 on `name`; the same name in a different category is fine. An id that
    does not exist is a 404.

    Returns 200 `{"data": {...}}` with the re-derived `slug` and the unchanged
    `visibility`.
    """
    return await _call(
        lambda client: client.update_kb_folder(
            folder_id, name=name, description=description
        )
    )


@mcp.tool()
async def delete_kb_folder(folder_id: int) -> dict[str, Any]:
    """WRITES TO EBTEQDESK — PERMANENTLY DELETES a REAL folder. NO UNDO.

    🔴 THIS DESTROYS STRUCTURE IN A LIVE KNOWLEDGE BASE AND CANNOT BE REVERSED
    THROUGH THIS API. No trash, no restore, no version history — the response is
    the only record of what the folder was. `create_kb_folder` would make a NEW
    row with a new id, not put this one back, and every article that had been
    filed into the old folder would need re-filing by hand. NAME THE FOLDER YOU
    ARE ABOUT TO DELETE AND GET THE USER'S AGREEMENT FIRST; check `list_kb_tree`
    if there is any doubt about which id it is.

    🔴 REFUSED WHILE THE FOLDER STILL HOLDS ARTICLES — a 422 naming the count,
    "This folder still holds 3 articles. Move or delete them first.", and nothing
    is deleted. It is never a cascade, and that matters more here than one level
    up: THIS API HAS NO DELETE-ARTICLE TOOL, and an article delete in Ebteqdesk
    has no undo, no trash and no version history at all. So a folder with
    articles in it can only be emptied by a person in the Ebteqdesk web UI. If
    you hit this refusal, report the count to the user — do not look for another
    way through.

    Never retry a call that timed out. A delete that looks like it failed may
    have landed; read `list_kb_tree` to find out rather than calling again.

    Requires the `kb:write` scope, resolving only while the key carries it AND
    the account's role holds `kb.manage` — ADMINISTRATOR AND SUPERVISOR ONLY.

    Args:
        folder_id: REQUIRED, the numeric folder id from `list_kb_tree`
            (`folders[].id`) or `list_kb_folders`. There is no lookup by name or
            slug, and there is no second argument: a delete cannot move a folder,
            cannot re-scope it and has no cascade flag. An unknown id is a 404.

    Returns 200 `{"data": {...}}` — a receipt in `create_kb_folder`'s shape.
    `articles_count` is `0`, which it always is here, and `position` is the index
    the folder VACATED inside its category: its later siblings in THAT category
    have moved up by one, while other categories are untouched.
    """
    return await _call(lambda client: client.delete_kb_folder(folder_id))


@mcp.tool()
async def reorder_kb_children(
    scope: Literal["categories", "folders", "articles"],
    ordered_ids: list[int],
    parent_id: int | None = None,
) -> dict[str, Any]:
    """WRITES TO EBTEQDESK — rewrites the order of a REAL knowledge base list.

    Changes what every agent sees when they open the knowledge base, and what a
    signed-out visitor sees wherever the affected content is publicly visible.
    ⚠️ DO NOT ASSUME AN `agents` FOLDER IS INVISIBLE OUTSIDE THE DESK HERE: an
    article can carry
    its own visibility overriding its folder's, and the help centre orders the
    "related articles" beside such an article by exactly the positions this tool
    writes. So reordering inside an internal-looking folder can still change a
    public page. The old order is not stored anywhere and there is no undo:
    putting it back means posting the previous order, which you can only do if
    you read it first. READ THE CURRENT ORDER BEFORE YOU CHANGE IT —
    `list_kb_categories`, `list_kb_folders`, or this tool's own last response.

    🔴 YOU MUST POST EVERY SIBLING ID, NOT A DELTA. `ordered_ids` is THE WHOLE
    LIST, in the order you want it. There is no "move item X to position N" form
    of this tool and there is no partial update:

        WRONG   ordered_ids=[9]            "put 9 first"
        RIGHT   ordered_ids=[9, 7, 3]      the complete list, 9 first

    A LIST THAT IS NOT EXACTLY THE CURRENT SIBLING SET IS A 422 AND NOTHING IS
    WRITTEN — not a partial reorder, not a best effort. Same members, same count,
    no duplicates. Sending a subset is refused, sending an extra or unrelated id
    is refused, sending the same id twice is refused. That is deliberate: it is
    what stops a stale view of the list from silently corrupting the real order,
    and it means a 422 here is information — your list is out of date, re-read it
    and try again — not a bug to work around.

    ⚠️ THIS IS THE ONE WRITE ON THIS SERVER THAT IS SAFE TO RETRY. Positions are
    assigned by index, so posting the same body twice leaves the same order and
    answers 200 both times. Every other write tool must not be retried blind.

    Requires the `kb:write` scope, which resolves only while the key carries it
    AND the account's role holds `kb.manage` — ADMINISTRATOR AND SUPERVISOR
    ONLY. `kb:read` cannot reach this: reordering is authoring.

    Args:
        scope: WHICH sibling list is being reordered.
            - "categories" — the top-level category list. Omit `parent_id`;
              categories have no parent.
            - "folders" — the folders inside ONE category. `parent_id` is that
              CATEGORY's id.
            - "articles" — the articles inside ONE folder. `parent_id` is that
              FOLDER's id.
        ordered_ids: The COMPLETE ordered list of sibling ids. First id ends up
            first. Read the block above before building it.
        parent_id: REQUIRED for "folders" and "articles", and REFUSED for
            "categories". A mismatch is rejected here, before any request is
            sent, because the two mistakes it catches are silent otherwise:
            omitting it on "folders" would build a URL for a different endpoint,
            and passing it on "categories" would be an argument with nowhere to
            go that a caller would read as having taken effect.

    Ids come from `list_kb_categories`, `list_kb_folders`, or `list_kb_tree`.
    ARTICLE ids come from none of them — the tree carries an `articles_count` and
    not the articles — so for `scope="articles"` read the current list from this
    tool's own previous response, or from the folder in the Ebteqdesk UI. Article
    ids include DRAFTS, which hold positions like any other article and must be
    in the list.

    A `parent_id` that does not exist is a 404. An id in `ordered_ids` that
    belongs to a different parent is part of the 422 above — a reorder cannot
    move anything between parents, and there is no argument that would let it.

    Returns 200 `{"data": [...]}` — the reordered list, re-read from the
    database, in its new order:
      - categories -> full category rows, `folders` nested
      - folders    -> full folder rows, `articles_count` included
      - articles   -> `{"id", "title", "position"}`, an ordering receipt only

    `position` in the response is the stored one: dense and 0-based, so the first
    row is always 0. Report THAT back, not the input you sent.
    """
    if scope == "categories":
        if parent_id is not None:
            raise ValueError(
                "parent_id must be omitted when scope='categories'. Categories "
                "are the top level of the knowledge base and have no parent — "
                "there is nothing for parent_id to name. Pass only ordered_ids, "
                "the complete list of category ids in the order you want them."
            )

        return await _call(
            lambda client: client.reorder_kb_categories(ids=ordered_ids)
        )

    if parent_id is None:
        parent = "category" if scope == "folders" else "folder"
        source = (
            "`list_kb_categories` or `list_kb_tree`"
            if scope == "folders"
            else "`list_kb_folders` or `list_kb_tree`"
        )

        raise ValueError(
            f"parent_id is required when scope='{scope}': it is the id of the "
            f"{parent} whose children you are reordering, and without it there "
            f"is no list to reorder. Get it from {source}. "
            f"ordered_ids is the complete ordered list of that {parent}'s "
            f"children — never a delta."
        )

    if scope == "folders":
        return await _call(
            lambda client: client.reorder_kb_folders(parent_id, ids=ordered_ids)
        )

    return await _call(
        lambda client: client.reorder_kb_articles(parent_id, ids=ordered_ids)
    )


@mcp.tool()
async def upload_kb_media(file_path: str) -> dict[str, Any]:
    """WRITES TO EBTEQDESK — uploads a LOCAL FILE into a live knowledge base.

    ⚠️ THIS TOOL READS A PATH ON THE USER'S OWN MACHINE AND SENDS THOSE BYTES TO
    A SERVER. It is the only tool here that touches the local filesystem, and
    that is a risk to the USER rather than to the helpdesk. Upload ONLY a file
    the user has explicitly named. Never sweep or list a directory looking for
    something suitable, never upload a file the user did not ask you to upload,
    and never guess at a path — if the one you were given does not exist, ask.

    Returns `{"ulid": "01J…", "url": "/kb/media/01J…", "kind": "image",
    "mime": "image/png", "width": 1280, "height": 720, "size_bytes": 84213,
    "original_name": "settings.png"}`. `width` and `height` are null for a video
    and for an image whose header could not be read.

    🔴 THE RETURNED `url` IS THE ONLY VALID WAY TO REFERENCE THIS IMAGE. Paste
    it into the article body exactly as it came back:

        <img src="/kb/media/{ulid}" alt="what the picture shows">

    🔴 NEVER INVENT OR GUESS A `/kb/media/` URL. A ULID is 26 random characters;
    one you made up resolves to nothing, and the article renders a BROKEN IMAGE
    in a live knowledge base signed-out visitors read — visible to every reader
    and invisible to you. If you do not have a url from this tool, you do not have
    an image. Do not reuse a url from an earlier conversation either.

    🔴 THE ORDER IS FIXED: UPLOAD FIRST, THEN REFERENCE, THEN SAVE. The upload
    attaches the file to nothing at all — Ebteqdesk derives the article/media
    link from the article BODY every time an article is saved. So:

      1. call this tool and keep the `url` it returns
      2. put that url in the `body` you pass to `propose_kb_article` or
         `update_kb_article`
      3. that save is what links the file to the article

    An upload you never reference in a saved body stays UNATTACHED. It is
    visible to nobody, it belongs to no article, and the server's cleanup
    sweep deletes it once it is seven days old. So do not upload speculatively,
    and if you upload something and then decide not to use it, say so rather
    than leaving the user thinking a file was filed somewhere.

    Accepted types — JPG (`image/jpeg`), PNG (`image/png`), WebP (`image/webp`),
    GIF (`image/gif`), MP4 (`video/mp4`) and WebM (`video/webm`). Nothing else:
    no PDF, no SVG, no zip. Images are capped at 10 MB and video at 50 MB.

    🔴 THE TYPE IS DECIDED BY SNIFFING THE FILE'S CONTENT, NOT ITS EXTENSION.
    Renaming `report.pdf` to `report.png` does not make it uploadable — the
    server reads the bytes and refuses it. So do not rename a file to get it
    past this, and do not tell a user that renaming will help.

    Requires the `kb:write` scope, which resolves only while the API key carries
    it AND the account's role holds `kb.manage` — ADMINISTRATOR OR SUPERVISOR
    ONLY. An agent- or developer-role account cannot upload whatever key it
    holds.

    ⚠️ NEVER RETRY A TIMED-OUT UPLOAD BLIND. Every call stores a NEW copy under
    a NEW ULID, so a retry that "worked the second time" has left a duplicate on
    the server that nothing points at. If a call times out, ask the user rather
    than calling again.

    Args:
        file_path: REQUIRED. An absolute path to a file on the machine this MCP
            server runs on, exactly as the user gave it. `~` is expanded. A
            missing path, a directory or an empty file fails here, before
            anything is sent.

    Errors worth telling apart: a `422` naming `file` means the type is not
    supported or the file is over its cap — the message says which, and the fix
    is a different or smaller file. A `413` means the request was too large for
    the web server itself; there is nothing to retry and the only remedy is a
    smaller export.
    """
    return await _call(lambda client: client.upload_kb_media(file_path))


# --------------------------------------------------------------------------- #
# AGENT PROVISIONING — the tools that decide WHO MAY ACT
# --------------------------------------------------------------------------- #
#
# 🔴 EVERY OTHER TOOL ON THIS SERVER ACTS ON A TICKET OR ON HELP CONTENT. These
# nine act on the DESK ITSELF: they create helpdesk accounts, set what role an
# account holds, and issue another account a bearer credential. The blast radius
# of a mistake is not "the wrong article was filed", it is "the wrong person can
# now do things".
#
# Four rules live only in these descriptions, because nothing in any payload
# carries them:
#
#   1. `create_agent` and `issue_api_key` each return a secret EXACTLY ONCE.
#      A model that does not pass it straight to the user has destroyed it.
#   2. `issue_api_key` can NEVER grant `admin:read` or `admin:write`. A model
#      that reads "you need admin access" and tries to mint it will loop.
#
#      🔴 AND THE SERVER'S RULE IS UNCONDITIONAL, NOT "UNLESS THE CALLER HOLDS
#      IT" — which is the obvious-looking softening, and which re-opens the hole
#      it closes. Those two scopes are the ones that reach these nine tools, so
#      a caller allowed to issue what it holds could issue a key that issues a
#      key: revoking a compromised provisioning key would revoke one link of a
#      chain it had already extended, to accounts the attacker also created, on
#      roles the attacker also chose. A provisioning key cannot mint a successor,
#      and that is what keeps the set of them from growing except through a
#      human's browser session. Do not describe it as conditional here either.
#   3. The server REFUSES rather than narrowing, so a 201 means the key carries
#      exactly what was asked for and a 422 means nothing at all was created.
#   4. There is no delete-agent tool and there will not be one.
#   5. AND NO TOOL HERE CAN CREATE OR PROMOTE AN ADMINISTRATOR. `create_agent`
#      and `update_agent` both refuse a role granting `admin.access`, because
#      such an account signs in with the password `create_agent` returns and
#      mints its own provisioning key — which would make rule 2 bypassable
#      rather than binding. A model asked to "make someone an admin" reports
#      that it is a signed-in administrator's act at Settings > Agents, and
#      does not go looking for a role that slips through.
#   6. A LEGACY `abilities = ['*']` KEY REACHES NONE OF THESE NINE. The wildcard
#      does not expand to the admin area, so a key minted before this surface
#      existed is refused with the ordinary scope 403 — both admin scopes have
#      to be ticked explicitly.
#
# ⚠️ AND THE ROLE IS CHOSEN BEFORE THE KEY, NOT AFTER. Which scopes a key can
# resolve is decided by the ABILITIES OF THE ROLE ITS OWNER IS ON, so an agent
# put on the wrong role cannot be given a working key at all — the mint is
# refused, not narrowed. `list_roles` carries each role's abilities for exactly
# that reason.


@mcp.tool()
async def list_agents(
    search: str | None = None, role_id: int | None = None
) -> dict[str, Any]:
    """List the helpdesk agents on this Ebteqdesk install — the whole roster.

    Read-only. This is the account DIRECTORY, not the ticket surface: it says
    who exists, what role each person is on and which groups they are in, and
    nothing about tickets.

    Requires the `admin:read` scope, which resolves only while the key carries
    it AND the account's role holds `admin.access` — ADMINISTRATOR ONLY among
    the built-in roles. An agent-, supervisor- or developer-role account is
    refused here whatever key it holds, and a new key will not fix that.

    Args:
        search: Optional. Matches name or email, case-insensitively, anywhere in
            the string. Omit it for the whole roster.
        role_id: Optional. Narrows to one role; ids come from `list_roles`.

    Returns `{"data": [...]}`, EVERY agent — this list is not paginated, unlike
    `list_tickets`. Each row carries `id`, `uuid`, `name`, `email`,
    `emailLocal`, `mustChangePassword`, `role` (`{id, name, key, isSystem}`),
    `groups` and timestamps.

    ⚠️ `emailLocal` IS NULL FOR AN ACCOUNT ON A NON-STANDARD DOMAIN, and that
    null is information rather than an omission: it means the account predates
    the locked agent-email suffix and this API will not rewrite its domain.

    NO PASSWORD AND NO TOKEN IS EVER IN THIS PAYLOAD. There is no tool here that
    can read an existing credential — see `create_agent` and `issue_api_key`,
    which are the only places a secret appears and which show it once.
    """
    return await _call(
        lambda client: client.list_agents(search=search, role_id=role_id)
    )


@mcp.tool()
async def get_agent(user_id: int) -> dict[str, Any]:
    """Read one helpdesk agent, and what a key issued to them could carry.

    Read-only. Requires the `admin:read` scope (ADMINISTRATOR ONLY — see
    `list_agents`).

    Args:
        user_id: REQUIRED, the numeric agent id from `list_agents`. There is no
            lookup by name or email. An id that does not exist is a 404.

    Returns `{"data": {...}, "meta": {"issuableScopes": [...]}}`.

    🔴 `meta.issuableScopes` IS THE USEFUL HALF AND IS NOT A PROPERTY OF THE
    AGENT. It is what `issue_api_key` would accept for THIS agent, from the key
    YOU are calling with, right now — the intersection of your own resolved
    scopes with what this agent's role can hold. Read it before calling
    `issue_api_key` and you avoid the refusal entirely; ask for something
    outside it and the whole mint is refused with the reason named.

    `admin:read` and `admin:write` never appear in it, for any agent and any
    caller. See `issue_api_key`.
    """
    return await _call(lambda client: client.get_agent(user_id))


@mcp.tool()
async def list_roles() -> dict[str, Any]:
    """List the roles an agent can be put on, with the abilities each one holds.

    Read-only. Requires the `admin:read` scope (ADMINISTRATOR ONLY).

    Returns `{"data": [{"id", "name", "key", "isSystem", "permissions",
    "agentsCount", "assignable"}]}`.

    🔴 `assignable` IS FALSE FOR ANY ROLE GRANTING ADMIN ACCESS, and those roles
    are refused by `create_agent` and `update_agent` with a 422. Filter on it
    before offering the user a choice, rather than proposing a role and
    discovering the refusal.

    🔴 CALL THIS BEFORE `create_agent` AND BEFORE `issue_api_key`, AND READ
    `permissions`. A role's abilities are what decide which SCOPES a key issued
    to an agent on that role can resolve — an agent on a role without
    `kb.manage` can never hold a working `kb:write` key, however the key is
    ticked, and `issue_api_key` refuses the mint outright rather than issuing a
    narrower one. Choosing the role wrong means the key cannot be issued at all.

    `key` is null for a role an operator created in the Ebteqdesk UI. That null
    matters: an unkeyed role is not governed by the scope policy and keeps
    whatever ceiling its own abilities give it.

    `agentsCount` is how many agents currently sit on the role.
    """
    return await _call(lambda client: client.list_roles())


@mcp.tool()
async def list_groups() -> dict[str, Any]:
    """List the groups (teams) an agent can be added to.

    Read-only. Requires the `admin:read` scope (ADMINISTRATOR ONLY).

    Returns `{"data": [{"id", "name", "membersCount"}]}`. The ids are what
    `create_agent` and `update_agent` take as `groups`.

    ⚠️ GROUPS GRANT NOTHING. Membership is organisational — it routes tickets
    and scopes team views — and cannot add or remove a single ability. If a user
    asks you to "give someone access" by adding them to a group, that is a ROLE
    change (`update_agent` with a `role_id`), not a group change.
    """
    return await _call(lambda client: client.list_groups())


@mcp.tool()
async def create_agent(
    name: str,
    email_local: str,
    role_id: int,
    groups: list[int] | None = None,
    password: str | None = None,
) -> dict[str, Any]:
    """WRITES TO EBTEQDESK — creates a REAL helpdesk account that can sign in.

    🔴 THIS CREATES A PERSON'S ACCESS TO A LIVE HELPDESK. The account can sign
    in immediately, and what it can then do is decided by `role_id`. Confirm the
    name, the address and THE ROLE with the user before calling — a typo in the
    role is a permissions mistake, not a cosmetic one. There is NO delete-agent
    tool on this API: an account created by mistake can only be removed by a
    person in the Ebteqdesk web UI, because deleting one reassigns or clears
    tickets, comments, notes and performance rows across the whole desk.

    🔴 THE PASSWORD COMES BACK EXACTLY ONCE AND CAN NEVER BE READ AGAIN.
    When `password` is omitted, Ebteqdesk generates one and returns it as
    `generatedPassword` in this response and NOWHERE ELSE — the database holds
    only a hash. GIVE IT TO THE USER IN YOUR VERY NEXT MESSAGE. If it is lost,
    the account is unusable until an administrator resets it by hand in the web
    UI; there is no tool here that can. No email is sent, so this response is
    the entire hand-over.

    When `password` IS supplied, `generatedPassword` is null — the user already
    has that string and it is not echoed back.

    Either way the agent is made to change it at first sign-in.

    Never retry a call that timed out. A create that appears to have failed may
    well have landed; call `list_agents` with a `search` for the name and look,
    because a second attempt either creates a duplicate person or comes back as
    a confusing "address already taken".

    Requires the `admin:write` scope, which resolves only while the key carries
    it AND the account's role holds `admin.access` — ADMINISTRATOR ONLY.

    Args:
        name: REQUIRED, the person's display name. Max 255.
        email_local: REQUIRED, and it is HALF AN ADDRESS. Send `"dana"`, not
            `"dana@somewhere.com"` — the desk appends its own agent-email
            domain, which cannot be chosen by a caller. Letters, digits, and
            single `.`/`_`/`-` separators only; an `@` anywhere is refused, as
            is a separate `email` argument (there isn't one). Max 64.
        role_id: REQUIRED, from `list_roles`. This is the permissions decision —
            read that tool's `permissions` before choosing.
            🔴 IT CANNOT BE A ROLE THAT GRANTS ADMIN ACCESS. Those rows come
            back from `list_roles` as `assignable: false` and are a 422 here.
            An account with admin access can sign in with the password this
            tool returns and mint its own provisioning key, so allowing it
            would let an API key create its own successor. If the user wants a
            new administrator, tell them a signed-in administrator does it at
            Settings > Agents in the Ebteqdesk web UI — do not look for
            another role that might work.
        groups: Optional list of team ids from `list_groups`. Omit for none.
        password: Optional. Min 8 characters. OMIT IT to have one generated and
            shown once, which is the normal path.

    Returns 201 `{"data": {...}, "generatedPassword": "…"|null}` with the new
    agent's `id`, which is what `issue_api_key` and `update_agent` take.

    A 422 on `email_local` means that address is already somebody's. A 422 on
    `role_id` or `groups` means an id that does not exist — re-read `list_roles`
    or `list_groups` rather than guessing another number.
    """
    return await _call(
        lambda client: client.create_agent(
            name=name,
            email_local=email_local,
            role_id=role_id,
            groups=groups,
            password=password,
        )
    )


@mcp.tool()
async def update_agent(
    user_id: int,
    name: str | None = None,
    role_id: int | None = None,
    groups: list[int] | None = None,
) -> dict[str, Any]:
    """WRITES TO EBTEQDESK — changes a REAL agent's name, role or groups.

    🔴 CHANGING `role_id` CHANGES WHAT A PERSON CAN DO, IMMEDIATELY AND
    EVERYWHERE. It is not a label. Moving someone to a narrower role takes
    effect on their very next request in the browser AND silently narrows every
    API key they already hold — a key that resolved `kb:write` yesterday resolves
    nothing today, with no error at mint time and no notification to whatever
    integration was using it. Say which role you are moving them to, and what it
    costs them, before you call this.

    ⚠️ MOVING THE LAST ADMINISTRATOR OFF AN ADMIN ROLE IS REFUSED — a 422. It
    would leave the install with nobody able to open Settings and nobody able to
    call these tools, so it is not reachable from here at all. Give another
    agent an admin role first, in the web UI or with this tool.

    🔴 THIS CANNOT CHANGE AN EMAIL OR A PASSWORD, AND THERE IS NO TOOL THAT CAN.
    Both are refused by the server rather than ignored. A password reset ends
    the person's live sessions and reveals the new value once on a screen, which
    has no safe unattended shape; an address change moves a sign-in identity.
    If a user asks for either, tell them it is done in the Ebteqdesk web UI at
    Settings > Agents.

    There is also no delete: see `create_agent`.

    Never retry a call that timed out — read the agent back with `get_agent`.

    Requires the `admin:write` scope (ADMINISTRATOR ONLY).

    Args:
        user_id: REQUIRED, from `list_agents`. An unknown id is a 404.
        name: New display name. OMIT IT to leave the name alone.
        role_id: New role, from `list_roles`. OMIT IT to leave the role alone —
            and omit it whenever you are only fixing a name.
            🔴 IT CANNOT BE A ROLE THAT GRANTS ADMIN ACCESS (`assignable:
            false` on `list_roles`) — a 422, exactly as on `create_agent`. So
            this tool can DEMOTE an administrator, while another remains, and
            can never promote one back. Re-promoting is a signed-in
            administrator's act at Settings > Agents.
        groups: The WHOLE new membership list, from `list_groups`, never a
            delta. OMIT IT to leave memberships alone; pass `[]` to remove the
            agent from every group.

    OMITTED ARGUMENTS ARE NOT EDITED. Returns 200 `{"data": {...}}`.
    """
    return await _call(
        lambda client: client.update_agent(
            user_id, name=name, role_id=role_id, groups=groups
        )
    )


@mcp.tool()
async def list_api_keys(user_id: int) -> dict[str, Any]:
    """List one agent's API keys — what each carries, and what each still works for.

    Read-only, and it reveals NO secret: the plaintext of a key exists only in
    the response to `issue_api_key`, once.

    Requires the `admin:read` scope (ADMINISTRATOR ONLY).

    Args:
        user_id: REQUIRED, from `list_agents`. An unknown id is a 404.

    Returns `{"data": [...], "meta": {...}}`. Each key carries `id`, `name`,
    `scopes`, `effectiveScopes`, `legacy`, `expired`, `expiresAt`, `lastUsedAt`
    and `createdAt`.

    🔴 `scopes` IS WHAT THE KEY CARRIES; `effectiveScopes` IS WHAT IT ACTUALLY
    WORKS FOR. They differ whenever the owner's role stopped backing a scope,
    which happens with no edit to the key at all — somebody was moved to a
    narrower role. A key whose `effectiveScopes` is `[]` authenticates and can
    do nothing. When you report a key's abilities to a user, report
    `effectiveScopes`; reporting `scopes` describes a key that does not exist.

    `lastUsedAt` is how you tell a live integration from an abandoned one before
    revoking anything.

    `meta.issuableScopes` is what `issue_api_key` would accept for this agent
    from your key right now, and never contains an admin scope.
    """
    return await _call(lambda client: client.list_api_keys(user_id))


@mcp.tool()
async def issue_api_key(
    user_id: int,
    name: str,
    scopes: list[str],
    expires_in_days: int | None = None,
) -> dict[str, Any]:
    """WRITES TO EBTEQDESK — issues a REAL bearer credential to another account.

    🔴 THIS HANDS OUT A WORKING CREDENTIAL FOR A LIVE HELPDESK. Whoever holds
    the returned string can act as that agent, within the scopes given, until it
    is revoked or expires. Confirm the agent, the scopes and the expiry with the
    user before calling.

    🔴 THE TOKEN IS RETURNED EXACTLY ONCE AND CAN NEVER BE READ AGAIN.
    `plainTextToken` in this response is the only moment that string exists
    anywhere — the desk stores a one-way hash of it, and neither this API nor
    the Ebteqdesk web UI can show it a second time. GIVE IT TO THE USER IN YOUR
    VERY NEXT MESSAGE. If it is lost the key is dead weight: revoke it with
    `revoke_api_key` and issue a new one. `list_api_keys` shows the key's name
    and scopes and never its secret.

    🔴 `admin:read` AND `admin:write` CAN NEVER BE ISSUED THROUGH THIS TOOL. Not
    by an administrator, not to an administrator, not tucked in beside a
    legitimate scope, and not however privileged the calling key is. Agent
    Provisioning keys — the ones that reach these nine tools — are minted only
    by a signed-in human at Settings > API keys in the Ebteqdesk web UI, for
    their own account. If a user wants an integration that can provision agents,
    say that and point them there; DO NOT retry with a different agent, a
    different role or a wider key, because none of those changes the answer.

    🔴 AND YOU CANNOT GRANT WHAT YOUR OWN KEY DOES NOT ALREADY RESOLVE. The
    issued set is your requested scopes narrowed by four things at once: what
    YOUR key currently resolves, what the target agent's role is permitted to
    hold, what that role's abilities back, and then the admin scopes removed.
    Call `get_agent` or `list_api_keys` first and read `meta.issuableScopes`,
    which is that whole calculation already done.

    ⚠️ THE SERVER REFUSES; IT NEVER QUIETLY ISSUES LESS. Ask for one scope it
    will not grant and the WHOLE call is a 422 that creates nothing, with
    `refusals` naming each scope and why — `caller_key` (your key does not
    resolve it), `owner_role_policy` (this agent's role may not hold it),
    `owner_role_ability` (this agent's role lacks the ability behind it) or
    `never_issuable` (an admin scope; see above). So a 201 means the key carries
    exactly what you asked for, and a 422 means nothing was created and no
    partial key is lying around.

    Never retry a call that timed out — call `list_api_keys` and look for the
    name. A retry that lands twice leaves a live credential nobody has the
    plaintext for.

    Requires the `admin:write` scope (ADMINISTRATOR ONLY).

    Args:
        user_id: REQUIRED, the agent who will OWN the key, from `list_agents`.
            The key acts as this person, not as you. An unknown id is a 404.
        name: REQUIRED, max 60 characters, and it is how this key is told apart
            in a list and in an incident — name it after the integration that
            will hold it. Unique per agent, case-insensitively.
        scopes: REQUIRED, at least one, e.g. `["ticket:read", "kb:read"]`. Read
            `meta.issuableScopes` first.
        expires_in_days: One of 7, 30, 60, 90, 180 or 365. OMIT IT for a key
            that never expires — prefer an expiry, and say which you chose.

    An agent may hold at most 10 unexpired keys; the eleventh is a 422 on
    `name`, as is a duplicate name on the same agent.

    Returns 201 `{"data": {...}, "plainTextToken": "12|…"}`.
    """
    return await _call(
        lambda client: client.issue_api_key(
            user_id,
            name=name,
            scopes=scopes,
            expires_in_days=expires_in_days,
        )
    )


@mcp.tool()
async def revoke_api_key(user_id: int, api_key_id: int) -> dict[str, Any]:
    """WRITES TO EBTEQDESK — PERMANENTLY revokes a REAL API key. NO UNDO.

    🔴 THE KEY STOPS WORKING ON ITS VERY NEXT REQUEST, AND NOTHING PUTS IT BACK.
    The row is deleted rather than flagged, so any integration holding that
    token starts failing immediately — possibly a scheduled job nobody is
    watching. Issuing a replacement produces a NEW secret that whatever was
    using the old one has to be reconfigured with; there is no way to restore
    the revoked one.

    NAME THE KEY YOU ARE ABOUT TO REVOKE AND GET THE USER'S AGREEMENT FIRST.
    Call `list_api_keys` and read it back to them: `name` says what it is for
    and `lastUsedAt` says whether anything is still using it. Revoking on a
    guessed id is how a live integration goes down.

    Nothing else the agent holds is affected — their other keys keep working,
    their password is untouched, and the separate credential the mobile agent
    app uses is not this one.

    Never retry a call that timed out. A revoke that looks like it failed may
    have landed, and a retry either reports a confusing 404 or, if ids have
    moved on, removes something else. Read `list_api_keys` instead.

    Requires the `admin:write` scope (ADMINISTRATOR ONLY).

    Args:
        user_id: REQUIRED, the agent who OWNS the key, from `list_agents`.
        api_key_id: REQUIRED, the numeric key id from `list_api_keys`. There is
            no lookup by name.

    A 404 means either that no key has that id or that it belongs to a different
    agent — the two are deliberately indistinguishable, so do not read one as
    the other and do not go looking for the id on another account.

    Returns 200 `{"data": {"id": 12, "revoked": true}}` — a receipt for a row
    that no longer exists.
    """
    return await _call(lambda client: client.revoke_api_key(user_id, api_key_id))


# --------------------------------------------------------------------------- #
# Capabilities: TOOLS ONLY. NO PROMPTS, NO RESOURCES.
# --------------------------------------------------------------------------- #
#
# 🔴 DO NOT DELETE THIS BECAUSE IT LOOKS LIKE IT DOES NOTHING. It is the only
# thing stopping `initialize` from advertising two capabilities this server has
# nothing behind.
#
# `MCPServer` wires `prompts/*` and `resources/*` handlers unconditionally, and
# the low-level server derives `ServerCapabilities` from WHICH HANDLERS EXIST
# (`Server.get_capabilities`: "if 'prompts/list' in self._request_handlers").
# So a tools-only server built with the high-level helper still announces
# `prompts` and `resources`, every connecting client dutifully calls
# `prompts/list` and `resources/list`, and both come back `[]` — two round trips
# per session, every session, to be told "none".
#
# Ebteqdesk's LARAVEL MCP server made the same decision first and states the
# rationale at length; this is the Python half of one rule, not a second opinion.
# From app/Mcp/WarnideskServer.php on feat/m2-integration-build — the path and
# the class name as they were on that branch, left un-renamed because a citation
# that points at a file which never existed is not a citation:
#
#     TOOLS ONLY. NO PROMPTS, NO RESOURCES.
#     $capabilities is overridden rather than inherited. Laravel\Mcp\Server
#     declares `tools`, `resources` AND `prompts` by default, and advertising a
#     capability this server has nothing behind means every connecting client
#     spends a round trip on `resources/list` and `prompts/list` to be told
#     "none". #105 scopes this to tools; the capability map is where that is
#     enforced on the wire rather than merely left empty.
#
# THE MECHANISM IS REMOVAL, NOT AN EMPTY LIST. Withdrawing the handlers makes
# `prompts/list` answer METHOD_NOT_FOUND — which is the honest answer for a
# method this server does not implement — AND drops the capability from the
# handshake, because the SDK derives one from the other. Leaving the handlers in
# place and returning `[]` is the state this replaces.
#
# ⚠️ `_request_handlers` is private to the SDK, and this is the only place in the
# package that reaches into it. There is no public withdrawal API in mcp 2.0:
# `add_request_handler` exists, its inverse does not, and the constructor's
# `on_*` seams belong to `Server`, which `MCPServer` builds for itself. The
# failure is handled rather than raised — an SDK that moved the attribute would
# otherwise stop the server booting at import, and a session that works with two
# wasted round trips beats a session that does not start. What catches the move
# instead is `test_server_tools.py`, which asserts the capabilities are actually
# gone; that fails in CI rather than in a user's editor.
#
# WHEN PROMPTS OR RESOURCES ARE IMPLEMENTED: delete the corresponding entries
# from the tuple below. Do not delete the mechanism.

#: The method families withdrawn, in the order they are dropped. Both whole
#: families, not just the `list` half: keeping `prompts/get` reachable on a
#: server that serves no prompts would be a method that can only ever fail.
UNIMPLEMENTED_METHODS: tuple[str, ...] = (
    "prompts/list",
    "prompts/get",
    "resources/list",
    "resources/templates/list",
    "resources/read",
    "resources/subscribe",
    "resources/unsubscribe",
)


def _withdraw_unimplemented_capabilities(server: MCPServer) -> tuple[str, ...]:
    """Drop the handlers behind the capabilities this server does not implement.

    Returns the methods actually removed, which is what the tests assert on —
    the tuple above lists everything that COULD be registered, and the SDK does
    not register all of it (`resources/subscribe` has no handler in mcp 2.0),
    so "what we asked for" and "what was there" are deliberately two things.
    """
    lowlevel = getattr(server, "_lowlevel_server", None)
    handlers = getattr(lowlevel, "_request_handlers", None)

    if not isinstance(handlers, dict):
        # The SDK moved it. Say nothing on stdout — this is an MCP server and
        # stdout is the transport — and leave the capabilities as they are.
        return ()

    return tuple(
        method for method in UNIMPLEMENTED_METHODS if handlers.pop(method, None) is not None
    )


#: What the withdrawal actually removed on this SDK version. Exported so a test
#: can assert it is not silently empty.
WITHDRAWN_METHODS: tuple[str, ...] = _withdraw_unimplemented_capabilities(mcp)


# --------------------------------------------------------------------------- #
# Error surface
# --------------------------------------------------------------------------- #


def _image_note(downscaled: bool | None) -> str:
    """The prose beside the flag, and it has to AGREE with the flag.

    A fixed "Downscaled by the server before transfer" printed next to
    `"downscaled": false` is the same class of defect as deriving the flag from
    the byte sizes: two statements in one block that contradict each other, with
    a model left to pick. It read as a warning on a full-resolution image, which
    trains exactly the wrong caution — "I can't be sure of this detail" about a
    picture that is pixel-for-pixel the original.

    The unknown arm says so rather than guessing in either direction, matching
    `AttachmentImage.downscaled`, where None is an honest "the server did not
    say" and never a claim.
    """
    if downscaled is True:
        return (
            "Downscaled by the server before transfer; fine detail may be "
            "illegible. Retry with a larger `max_dimension` rather than "
            "guessing at small text."
        )

    if downscaled is False:
        return (
            "Served at its stored dimensions — this is the full-resolution "
            "original. A larger `max_dimension` cannot produce more detail; if "
            "something is unreadable here, it is unreadable in the file itself."
        )

    return (
        "The server did not report whether this image was reduced. Treat fine "
        "detail as unverified: say so rather than guessing, and a larger "
        "`max_dimension` may or may not help."
    )


def _image_format(mime_type: str) -> str:
    """"image/png" -> "png", for `Image(format=...)`.

    The SDK's `Image` rebuilds the mime type as `f"image/{format}"`, so handing
    it the subtype round-trips the server's own Content-Type rather than this
    client deciding what the image is. A type with no subtype falls back to
    "png", which is what the SDK itself defaults raw binary data to — the header
    check in `EbteqdeskClient._get_image` has already established that this is
    an image, so the fallback is a formatting detail and not a guess about
    content.
    """
    subtype = mime_type.split("/", 1)[1] if "/" in mime_type else ""

    return subtype or "png"


async def _call(operation: Any) -> Any:
    """Run one client call and normalise its failures for an MCP client.

    Returns whatever the client method returns — a decoded JSON dict for
    thirty-two of the thirty-three tools, and an `AttachmentImage` for
    `get_ticket_attachment`. The annotation is `Any` rather than
    `dict[str, Any]` for that one method's sake; the funnel itself cares only
    about the exceptions.

    Every `EbteqdeskError` is re-raised as a plain `RuntimeError` carrying only
    its message. The SDK turns a raised exception into `isError: true` plus that
    text, which is all the user will see — and a bare `raise` would attach this
    package's traceback to it, which says nothing useful about a missing scope
    and quite a lot about the caller's filesystem layout.

    `ValueError` (a bad argument this client refused before sending) is passed
    through the same way, so the model gets "category must be a single slug"
    rather than a Python type name.

    `from None` suppresses the chained traceback for the same reason. The
    messages in errors.py are already complete; the exception CHAIN adds only
    noise, and an httpx exception in the chain can quote a full request URL.
    """
    client = await _get_client()

    try:
        return await operation(client)
    except (EbteqdeskError, ValueError) as exc:
        raise RuntimeError(str(exc)) from None


def run() -> None:
    """Serve MCP over stdio until the client disconnects."""
    try:
        mcp.run("stdio")
    finally:
        # The event loop mcp.run() owned is gone by now, so the client's pooled
        # sockets are already closed by process teardown. This is here for the
        # in-process case (tests, embedding) where run() returns and the process
        # lives on.
        try:
            asyncio.run(_close_client())
        except RuntimeError:
            pass
