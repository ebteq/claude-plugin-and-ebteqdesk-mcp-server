---
name: tickets
description: Work Warnidesk support tickets — list or search the queue, open a ticket and read its full conversation, look at an attached screenshot, draft and send a reply to the customer, file an internal note, move a ticket between working states or reopen a resolved one, open a new ticket, and close one. Use whenever the user asks about their tickets, the ticket queue, a ticket number or reference, replying to a customer, putting a ticket on hold or waiting on the customer, reopening a ticket, or resolving/closing one.
---

# Warnidesk tickets

Verified against `warnidesk-mcp` 1.6.0 (32 tools).

You are working a **live helpdesk**. Real customers receive what you send.

Every tool here comes from the `warnidesk` MCP server. If those tools are not
available, the server is not connected — say so rather than guessing; the user
connects it from **Settings → API keys** on their Warnidesk.

## Before you touch anything

Call `whoami` first in a session. It needs no scope, so it answers whenever the
key and the server are both working, and it tells you which account you are
acting as and which scopes that key actually resolves to. Everything below is
scoped to that account: `list_tickets`, the category lists and every write tool
show you **only tickets assigned to it**.

A `401` means the key is wrong, revoked or expired. A `403` means the key is
fine but lacks the scope that tool needs — and scopes are fixed when a key is
created, so the fix is a new key, never an edit to the current one.

## 🔴 The three irreversible things

Nothing on this API can delete or edit a ticket, a comment, or a note. There is
no undo, no dry run, and no preview. Before any of these, show the user exactly
what you intend to send and get their agreement:

| Tool | What it really does |
|---|---|
| `comment_on_ticket` | Posts a **public** reply. The customer gets it **by email**. There is no flag to make it private. |
| `create_ticket` | Files a real ticket on the live queue, assigned to your account, and may create a customer contact record as a side effect. |
| `close_ticket` with `status=4` | Resolves the ticket **and emails the customer a satisfaction survey**. |

**Never retry a write that appears to have timed out.** A call that looks failed
may well have landed, and a retry sends a second email or files a second ticket.
Read the ticket back instead. (`set_ticket_status` is the one exception on this
skill's surface — see below.)

## The order things happen in

Most ticket work is one of a handful of shapes. Do them in this order; the order
is the part that goes wrong, not the individual calls.

| What you want | Tool | Does the customer get an email? |
|---|---|---|
| Answer the customer | `comment_on_ticket(id, body)` | **Yes, always.** That is what it is for. |
| Record something internal | `add_private_note(id, body)` | No |
| Park it on somebody internal | `set_ticket_status(id, 3)` — pending | No |
| Park it on the customer | `set_ticket_status(id, 8)` — waiting on customer | No |
| Pick it up, or **reopen** a resolved one | `set_ticket_status(id, 2)` — open | No |
| Resolve it quietly | `close_ticket(id)` — defaults to 5 | No |
| Resolve it **and** ask for a rating | `close_ticket(id, status=4)` | **Yes — the satisfaction survey.** |

### 🔴 Reply BEFORE you close. Never the other way round.

`close_ticket` **has no message argument**, on purpose — answering the customer
and resolving the ticket are two acts behind two different abilities
(`ticket.reply` and `ticket.close`). From the tool itself: *"To do both, call
`comment_on_ticket` first and then this — in that order, so the customer's last
email is your answer rather than a survey."*

Get it backwards with `status=4` and the last thing the customer receives is a
satisfaction survey for a problem nobody visibly answered. There is no way to
recall it.

So a normal answer-and-resolve is:

1. `get_ticket(ticket_id)` — read the whole thread, notes included.
2. Draft the reply and **show it to the user**.
3. `comment_on_ticket(ticket_id, body)` once they agree.
4. `close_ticket(ticket_id)` — status 5 unless the user asked for the survey.

### The middle of the lifecycle is `set_ticket_status`

`set_ticket_status(ticket_id, status)` is **the quiet write on this server**. It
takes the four **working** states and nothing else:

- **`1` new** — nobody has picked it up yet
- **`2` open** — an agent is on it. Sending this to a solved or closed ticket
  **REOPENS it**
- **`3` pending** — waiting on somebody internal
- **`8` waiting on customer** (note the 8: it is an open state despite sorting
  above solved)

It emails nobody, notifies nobody, and sends no survey. Asking it for `4` or `5`
is a 422 — resolution lives on `close_ticket` and only there, which is what
keeps the survey warning in one place. `6` (merged) and `7` (spam) are not
settable through this API at all.

**Reopening is `status=2`, and it costs more.** A ticket that is currently
solved, closed, merged or spam needs the `ticket.close` ability on top of
`ticket.reply` — the same ability resolving it cost, because it is the same
boundary crossed the other way. So this can be refused on a resolved ticket by
an account it works fine for on an open one, and the refusal names
`ticket.close`.

✅ **Setting the status a ticket already holds is a no-op and is safe to
retry.** The server guards it: nothing is written, no history entry is appended,
and it still answers 200. That makes this one of only **two** writes on the whole
server you may repeat after an ambiguous timeout (the other is
`reorder_kb_children`). Every other write here is a one-shot.

⚠️ But the trail is permanent. Every *real* change appends a
`Status updated: <name>` event to the ticket's thread, which anyone reading the
ticket sees. Flapping a ticket between two states leaves a permanent record of
the flapping on a real customer's ticket. Decide the state, then set it once.

### 🔴 The escalated-ticket scope asymmetry

This is the one that reads like a broken tool and is not. On an **escalated**
ticket the two tools that *speak* need an extra scope; the two that only move
state do not:

| Tool | Ordinary ticket | Escalated ticket |
|---|---|---|
| `comment_on_ticket` | `ticket:write` + `ticket.reply` | `ticket:write` **+ `escalation:write`** |
| `add_private_note` | `ticket:write` + `ticket.reply` | `ticket:write` **+ `escalation:write`** (and the `bp_escalation.reply` ability) |
| `close_ticket` | `ticket:write` + `ticket.close` | **identical — no escalation branch at all** |
| `set_ticket_status` | `ticket:write` + `ticket.reply` | **identical — no escalation branch at all** |

**So an account holding only `ticket:write` can close an escalated ticket but
cannot reply to it.** Say that plainly to the user if they hit it, because it
otherwise looks like a bug. It is not: what the business-partner surface gates is
who may **speak** on a handed-off ticket, and neither `close_ticket` nor
`set_ticket_status` sends any message.

Check `data.escalated` — the boolean, never the nullable `escalated_at` — before
you reach for a reply tool. If you did not check, the refusal tells you: a 403
naming `escalation:write` from `comment_on_ticket` means the ticket is
escalated, because that is the only way that endpoint can ask for that scope.

## Reading a ticket

`list_tickets` / `list_tickets_by_category(category)` page the queue. Lists cap
at **20 rows per page** — asking for more is an error, not a quietly smaller
page. Page with `page` / `per_page`.

`get_ticket(ticket_id)` is the one that matters. It returns the ticket plus
`conversation`, oldest first, where every entry has a `kind`:

- **`comment`** — a public message. Either the customer's or an agent's reply to
  them. The customer has seen it.
- **`note`** — a **private internal note**. Staff only.
- **`event`** — a history entry, e.g. "Escalated", "Status updated".

Two things people get wrong:

- The customer's **opening message is `data.body`**, not the first entry of
  `conversation`, and it is **not in `get_ticket_comments` on any page**. You
  cannot summarise a ticket from the comments alone — you will be missing what
  the customer actually asked for.
- `thread_limit` keeps only the newest N entries, and when it truncates,
  `conversation_truncated: true` appears at the **top level of the response,
  beside `data` and not inside it**. Omit `thread_limit` for the whole thread;
  most tickets are short.

### 🔴 Internal notes are staff-only, and that is your responsibility

`kind: "note"` may contain diagnosis, blunt assessments of the customer, pricing
or account internals. **Use notes to inform yourself. Never quote, paraphrase or
allude to one in anything the customer will see.** The API will happily let you
paste a note into `comment_on_ticket`; nothing stops you but you.

### Attachments

When the answer depends on what is in a screenshot, call
`get_ticket_attachment(attachment_id)` and actually look at it. Get the id from
`get_ticket`: `data.attachments[].id` for the opening message's files, or
`data.conversation[].attachments[].id` for a reply's or a note's. It returns an
image; it is for images only.

## Replying

Decide which of the two you want **before** you draft:

- `comment_on_ticket(ticket_id, body)` — the customer reads this, by email.
- `add_private_note(ticket_id, body)` — internal. The customer is never
  notified. This is the right tool for a diagnosis, a caveat, or anything you
  would not want a customer to read.

Reaching for `comment_on_ticket` when you meant a note is how an internal remark
gets mailed to a customer. Notes are also permanent — there is no note-editing
or note-deleting tool anywhere on this API.

Draft the reply, show it to the user, and send only once they have said yes.

**If the ticket is escalated** (`data.escalated` is `true`), replying — public
or private — needs the `escalation:write` scope on top of `ticket:write`, while
`close_ticket` and `set_ticket_status` need nothing extra at all. See the
asymmetry table above. The escalation queue also has its own conventions; use
the `escalations` skill for those tickets.

## Closing

`close_ticket(ticket_id, status)` takes exactly two values:

- **`status=5` (CLOSED) — the default.** Resolves the ticket. Emails nobody.
- **`status=4` (SOLVED).** Resolves the ticket **and** triggers the customer
  satisfaction survey — a real email asking them to rate it.

Default to 5. Only pass 4 when the user has explicitly asked to send the survey.
Never use `close_ticket` to tidy up test data.

It takes **no message argument**. Reply first with `comment_on_ticket`, then
close — see "Reply BEFORE you close" above.

**To reopen, use `set_ticket_status(ticket_id, 2)`.** `close_ticket` cannot move
a ticket back to a working state; the two tools are disjoint on purpose.

## Opening a ticket

`create_ticket(subject, description, requester, ...)` — `requester` identifies
the customer, e.g. `{"email": "ada@example.com", "name": "Ada Lovelace"}`, and
may create a contact record if that email is new. Optional: `priority`,
`status`, `category`, `reference_number`, `tags`.

⚠️ **Opening a ticket already at `status=4` emails the customer the satisfaction
survey**, on creation. Leave `status` alone unless the user asked for something
specific.

Confirm subject, description and requester with the user before calling it.
There is no delete-ticket tool — a mistake needs a human in the web UI.
