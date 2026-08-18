---
name: escalations
description: Work the Warnidesk business-partner escalation queue — see every unresolved escalated ticket in the installation, read an escalated ticket and its internal notes, reply to the customer on one, escalate a ticket, take one back off the queue, move it between working states, and close it. Use whenever the user asks about escalations, the escalation queue, escalated or urgent tickets, escalating something, de-escalating, or why a reply to an escalated ticket was refused.
---

# Warnidesk escalations

Verified against `warnidesk-mcp` 1.6.0 (32 tools).

The escalation queue is the shared, install-wide view of what has gone wrong.
Acting on it notifies real people.

Call `whoami` first to learn which account you are and which scopes the key
resolves to. A `403` here almost always means the key lacks `escalation:read`
(for the queue) or `escalation:write` (to write into an escalated ticket);
scopes are fixed at key creation, so the fix is a new key. A `403` naming
`escalation:write` from `comment_on_ticket` also *tells you the ticket is
escalated* — that is the only way that endpoint can ask for that scope.

## The order things happen in

An escalated ticket is somebody else's bad day already in progress. The sequence
matters more here than anywhere else on the desk.

1. `list_escalations` to see the queue — then **read each row's `assignee`**.
2. `get_ticket(ticket_id)` for the one you are acting on. The notes are where
   the real work is.
3. Decide: is what you are about to write for the **customer**
   (`comment_on_ticket`) or for the **team** (`add_private_note`)? On an
   escalated ticket it is usually the team.
4. Draft it, **show the user**, send only on their yes.
5. Resolve last: `close_ticket(ticket_id)` — status 5 unless the user asked for
   the survey.

### 🔴 Reply BEFORE you close. Never the other way round.

`close_ticket` **has no message argument**, on purpose. From the tool itself:
*"To do both, call `comment_on_ticket` first and then this — in that order, so
the customer's last email is your answer rather than a survey."*

Get it backwards with `status=4` and an already-unhappy customer's final email
is a satisfaction survey for a problem nobody visibly answered. It cannot be
recalled.

### 🔴 The scope asymmetry: you may be able to close what you cannot answer

On an escalated ticket the two tools that **speak** need an extra scope. The two
that only move **state** do not:

| Tool | Ordinary ticket | Escalated ticket |
|---|---|---|
| `comment_on_ticket` | `ticket:write` + `ticket.reply` | `ticket:write` **+ `escalation:write`** |
| `add_private_note` | `ticket:write` + `ticket.reply` | `ticket:write` **+ `escalation:write`** (and the `bp_escalation.reply` ability) |
| `close_ticket` | `ticket:write` + `ticket.close` | **identical — no escalation branch at all** |
| `set_ticket_status` | `ticket:write` + `ticket.reply` | **identical — no escalation branch at all** |

**So an account holding only `ticket:write` can close an escalated ticket but
cannot reply to it.** That combination is deliberate, not a broken tool: the
business-partner surface gates who may **speak** on a handed-off ticket, and
neither `close_ticket` nor `set_ticket_status` sends any message. Tell the user
that plainly rather than letting it read as an outage.

Reaching a queue ticket **assigned to somebody else** — one `list_tickets` would
never show you — additionally needs `escalation:read` for the read itself.

### Working states: `set_ticket_status`

`set_ticket_status(ticket_id, status)` moves a ticket between the four **working**
states and contacts nobody at all — no customer email, no agent notification, no
survey:

- **`1` new**, **`2` open** (this is also how a resolved ticket is **reopened**),
  **`3` pending** (waiting on somebody internal), **`8` waiting on customer**.

It cannot reach `4` or `5`; that is `close_ticket`, the only tool that can send
the survey. ✅ Sending a status the ticket already holds is a **no-op and safe to
retry** — one of only two writes on this server with that property. Reopening
(`status=2`) additionally costs the `ticket.close` ability.

**An escalated ticket needs nothing extra here.** See the table above.

### ⚠️ A row leaving this queue does not mean it was answered

A **solved** escalated ticket is not on `list_escalations` at all — resolving
takes it off the business-partner queue. So a row that disappeared may have been
solved, or it may have been **de-escalated**, and this list cannot tell you
which.

Never report "that escalation was handled" from absence. Fetch the ticket with
`get_ticket` and read its `status` and `escalated` for what actually happened.

## 🔴 This queue is not yours

`list_escalations` is **the only ticket list on this API that is not limited to
your own account**. It returns every unresolved escalated ticket in the
installation — whoever it is assigned to, **including tickets assigned to
nobody**.

Every other list (`list_tickets`, the category lists) and every write tool shows
you only tickets assigned to your account.

So: **read each row's `assignee`** before you say anything about ownership.
Reporting this queue as "your escalations" is wrong and will mislead the user
about their own workload. Lists cap at **20 rows per page**.

Order is **longest-escalated first** (`escalated_at` ascending, then `id`) — but
rows with a null `escalated_at` sort **last** despite being the oldest. They were
escalated before that column existed; null means "unknown", not "the dawn of
time". They are genuinely escalated, and `escalated` is true on them.

## Escalation state is a boolean

Read **`escalated`** (a boolean, always accurate) to know whether a ticket is
escalated. Do **not** infer it from `escalated_at`, which is nullable and is
only a timestamp. `escalated_minutes` tells you how long it has been on the
queue.

## Reading an escalated ticket

Use `get_ticket(ticket_id)` — same shape as any ticket: `conversation` entries
of `kind` `comment` (public), `note` (private, staff-only) and `event`
(history), oldest first, with the customer's opening message in `data.body`.

⚠️ **On an escalated ticket, the notes are usually where the real work is.** The
public thread often shows only the customer's frustration; the diagnosis, the
internal disagreement and the actual plan are in `kind: "note"` entries.

**Read them. Never repeat them to the customer.** They routinely contain blunt
assessments, pricing and account internals. Nothing in the API stops you pasting
a note into a public reply — only you do.

## Replying on an escalated ticket

`comment_on_ticket(ticket_id, body)` posts a **public** reply that the customer
receives **by email**. It cannot be edited or deleted afterwards.

⚠️ **On an escalated ticket this needs both `ticket:write` and
`escalation:write`.** A key with only `ticket:write` replies fine to ordinary
tickets and gets a 403 here — that is the scope combination, not a broken tool.

`add_private_note(ticket_id, body)` is the internal alternative and notifies no
customer. On an escalated ticket it is usually what you want: record the finding
for the team rather than narrating progress to an already-unhappy customer.

⚠️ **A note on an escalated ticket needs the same two scopes** — plus the
`bp_escalation.reply` ability. Not because the note would be exposed; it would
not. Writing anything into a business-partner thread is a business-partner act.
It is also the one ticket write that reaches **beyond your own queue**: it can
note on any ticket `get_ticket` can read, including a shared-queue ticket
assigned to somebody else, which additionally needs `escalation:read`. Use that
to record a finding on a ticket you are reviewing — not to leave notes on other
people's tickets they did not ask for.

Draft, show the user, then send.

## Escalating

`escalate_ticket(ticket_id)` puts the ticket on this queue, stamps its
escalation time, writes an "Escalated" history entry, and **sends the
`TicketEscalated` notification to every Assistant on the installation. People
get pinged.**

🔴 **Never retry this call blind.** The stored state is idempotent — a second
escalation keeps the original timestamp — but **the notification is not**. A
retry after an apparent timeout pings the whole team twice. If a call looks like
it failed, read the ticket back with `get_ticket` and check `escalated` before
doing anything else.

Confirm with the user before escalating. It is a decision about other people's
attention, not a status flag.

## De-escalating

`de_escalate_ticket(ticket_id)` takes the ticket back off the queue, clears the
escalation and its timestamp, and writes a "De-Escalated" history entry. It
sends **no** notification, so a repeat call is cheaper than a repeat escalation.

⚠️ But **the escalation timestamp is gone once this succeeds**, and re-escalating
afterwards restarts the clock from now. Anything measuring how long the ticket
sat escalated loses that history. Do not de-escalate to "reset" a ticket.

## Closing

`close_ticket(ticket_id, status)`:

- **`status=5` (CLOSED) — the default.** Resolves it. Emails nobody.
- **`status=4` (SOLVED).** Resolves it **and** emails the customer a
  satisfaction survey.

Default to 5. On a ticket that was escalated — where the customer has already
had a bad experience — sending an unrequested satisfaction survey is a decision
the user should make explicitly, not one you make for them.

Neither value needs `escalation:write`; `close_ticket` has no escalation branch.
And it takes **no message argument** — reply first, then close.

**Resolving is not de-escalating.** Closing takes the ticket off this queue as a
side effect of resolving it; `de_escalate_ticket` takes it off while leaving it
open. Pick the one that matches what the user actually meant.
