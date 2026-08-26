---
name: reports
description: Pull Ebteqdesk reporting and analytics — the account-wide ticket report (volume, response times, resolution times, ratings) and the per-category escalation report, both over a date range. Use whenever the user asks for ticket metrics, support statistics, response or resolution times, ticket volume, escalation counts by category, a weekly or monthly summary, or how the desk is performing.
---

# Ebteqdesk reports

Verified against `ebteqdesk-mcp` 1.6.0 (32 tools).

Two reports. They cover different things, need different permissions, and — the
part that quietly ruins numbers — **interpret dates differently**.

Both are read-only. There is no writable reporting surface anywhere on this API,
by design: a report is computed from tickets, so there is no report row to
create or edit.

Call `whoami` first to see which scopes the key resolves to.

## The two reports

| | `get_reports_summary` | `get_escalation_report` |
|---|---|---|
| Covers | The **whole installation** — volume, response times, resolution times, ratings | **Per-category** ticket and escalation counts |
| Scope | `reports:read` | `escalation-reports:read` |
| Extra gate | **also needs the `admin.access` ability** | `bp_escalation.view` role permission |
| Dates | **exact instants, not widened** | **widened to whole days** |

Neither is "this account's own tickets" — both are installation-wide.

## 🔴 The `reports:read` 403 that looks like a bug

`get_reports_summary` requires the **`reports:read` scope AND the `admin.access`
ability**. Both gates, independently.

So a Supervisor can tick "Reports & Analytics → read" in the key picker, hold a
key that genuinely carries the scope, and still be refused. That is correct
behaviour, not a broken key or a broken tool. If the user hits this, the answer
is that the **account behind the key** is not an administrator — minting another
key will not change it.

`get_escalation_report` has no such extra gate, so it is often the one that
works when the other does not.

## 🔴 The date trap

The two reports do **not** treat bounds the same way, and mixing them up
under-reports a month by a whole day.

**`get_reports_summary` — both bounds are inclusive *instants*, neither widened
to a whole day.** A bare `date_to="2026-08-31"` means "up to 2026-08-31
00:00:00" and therefore **excludes that entire day's tickets**. When you mean
the full day, pass the time:

```
get_reports_summary(date_from="2026-08-01", date_to="2026-08-31T23:59:59")
```

`data.range` echoes the instants actually measured. **Check it** before quoting
any number to the user — it is the cheapest way to catch a silently short range.

**`get_escalation_report` does widen its bounds to whole days.** `date_from`
becomes the start of that day, `date_to` the end of it, so a bare date behaves
the way people expect.

Because of this, the same pair of bare dates gives the two reports **different
ranges**. If you are presenting both together, say which range each one actually
measured rather than implying they match.

Both accept ISO 8601 dates or date-times, and `date_to` must not precede
`date_from`. Omit both for the full history.

## Presenting results

State the date range you actually measured — from `data.range` where it is
given — alongside the numbers, not just the numbers. A metric with an unstated
or assumed range is the one people misquote in a status update later.

If the user asks for a comparison across periods, make two calls with explicit
bounds rather than inferring a trend from one.
