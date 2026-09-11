---
name: reports
description: Pull Ebteqdesk reporting and analytics — the account-wide ticket report (volume, response times, resolution times, ratings) and the per-category escalation report (ticket and escalation counts), both over a date range. Use whenever the user asks for ticket metrics, support statistics, response or resolution times, ticket volume, escalation counts by category, a weekly or monthly summary, or how the desk is performing.
---

# Ebteqdesk reports

Verified against `ebteqdesk-mcp` 4.3.0 (42 tools).

Two reports. They cover different things, need different permissions,
**interpret a missing date range differently**, and — the part a payload
literally cannot flag — **return several numbers that are not what they look
like**. Every number in this skill is a rule from the tool's own description;
none of them is derivable from the response shape.

Both are read-only. There is no writable reporting surface anywhere on this
API, by design: a report is computed from tickets, so there is no report row
to create or edit.

Call `whoami` first to see which scopes the key resolves to.

## The two reports

| | `get_reports_summary` | `get_escalation_report` |
|---|---|---|
| Covers | The **whole installation** — volume, response times, resolution times, ratings | **Per-category** ticket and escalation counts |
| Scope | `reports:read` | `escalation-reports:read` |
| Extra gate | **also needs the `admin.access` ability** | **also needs the `bp_escalation.view` ability** |
| Dates, both bounds given | exact instants, not widened | widened to whole days |
| Dates, **both omitted** | 🔴 **the current calendar month** | **all time** |

Neither is "this account's own tickets" — both are installation-wide.

## 🔴 The `reports:read` 403 that looks like a bug

`get_reports_summary` requires the **`reports:read` scope AND the `admin.access`
ability**. Both gates, independently.

So a Supervisor can tick "Reports & Analytics → read" in the key picker, hold a
key that genuinely carries the scope, and still be refused. That is correct
behaviour, not a broken key or a broken tool. If the user hits this, the answer
is that the **account behind the key** is not an administrator — minting another
key will not change it.

`get_escalation_report` has its own version of the same trap: it needs
`escalation-reports:read` **and** the `bp_escalation.view` ability, and the two
gates fail the same way — a key that genuinely carries the scope is still
refused if the account's role does not hold the ability. Check `whoami`'s
`permissions` list for `bp_escalation.view` (or `admin.access`) before telling
a user a new key will fix either 403; it will not.

## 🔴 The date trap: two axes, not one

Getting either wrong under- or over-reports a range, and neither mistake is
visible in the response unless you check `data.range`.

**Axis 1 — how a *given* bound is interpreted.**

**`get_reports_summary` — both bounds are inclusive *instants*, neither widened
to a whole day.** A bare `date_to="2026-08-31"` means "up to 2026-08-31
00:00:00" and therefore **excludes that entire day's tickets**. When you mean
the full day, pass the time:

```
get_reports_summary(date_from="2026-08-01", date_to="2026-08-31T23:59:59")
```

**`get_escalation_report` does widen its bounds to whole days.** `date_from`
becomes the start of that day, `date_to` the end of it, so a bare date behaves
the way people expect.

Because of this, the same pair of bare dates gives the two reports **different
ranges**. If you are presenting both together, say which range each one
actually measured rather than implying they match.

**Axis 2 — what *omitting both* bounds means, and this is not the same answer
for the two tools:**

🔴 **`get_reports_summary` with no dates at all means the CURRENT CALENDAR
MONTH — not all time.** A user asking "how's the desk doing overall" and
getting an unqualified call back is silently looking at this month only,
which reads as a much smaller (or much rosier, or much worse) installation
than "ever" would. State the range you got, always.

**`get_escalation_report` with no dates at all means all time.** The two
reports disagree here exactly as they disagree on axis 1 — do not assume the
same call shape means the same query on both.

`data.range` echoes the instants actually measured on **either** tool. **Check
it before quoting any number** — it is the cheapest way to catch a silently
short (or silently month-scoped) range, and it is the only way to know what
"omit both" resolved to on a given call.

Both accept ISO 8601 dates or date-times, and `date_to` must not precede
`date_from` — a reversed range is rejected, never silently widened.

## 🔴 Reading `get_escalation_report`'s numbers

The payload cannot flag any of this; it is stated here because the tool's own
description states it, in these words, for the same reason.

**Row identity is `key`, never `id` or `slug`.** Every row in
`data.categories` has `key`, and it is the only field guaranteed present and
unique — the "Uncategorised" bucket has `id: null` **and** `slug: null`. `key`
is the slug where one exists, `_type-{id}` where a category has no slug, and
`_uncategorised` for the null bucket. Join, group and label on `key`, never on
`id` or `slug`.

**Three rules about the numbers themselves, and getting any of them wrong
produces figures that are simply incorrect, not just imprecise:**

1. 🔴 **`escalated / total` is NOT a percentage, and `escalated` CAN EXCEED
   `total`.** The two counts range over **different date columns** within the
   same category, so within any bounded range they count overlapping-but-
   different sets of tickets. Never present a ratio of them, and never treat
   `escalated > total` as corrupt data — it is an expected shape, not a bug.
2. **`escalatedUndated` is IDENTICAL in every range you ask for.** It counts
   escalations with no date at all, so no date filter can move it. Never add
   it into `escalated`, and never report two identical values across two
   different ranges as a caching bug — that is the correct, if confusing,
   behaviour.
3. **`sum(status.*)` can be LESS than `total`.** Do not compute a "missing" or
   "other" status bucket from the difference, and do not use `total` as the
   denominator for a status breakdown — it will not add up, by design, not by
   omission.

`meta.filters` echoes what you sent; `data.range` reports the widened instants
actually measured (see the date trap above).

## 🔴 Reading `get_reports_summary`'s numbers

None of these units are what a first guess would assume, and three of the
likely guesses are wrong:

- `volume.*` (`tickets`, `unanswered`, `open`, `solved`) — counts, integers,
  never null.
- `times.firstReplyMinutes`, `times.resolutionMinutes` — **MINUTES**, not
  hours and not seconds. Convert before presenting anything over roughly 120.
- `quality.oneTouchResolutionPercent`, `quality.reopenedPercent` — on
  **0..100, not 0..1**. A value of `4.0` is four percent, not 400%.
- `quality.averageRating` — 1 to 5 stars.

🔴 **Every field under `times` and `quality` is nullable, and null means NO
DATA — never zero, and never "0%" or "0 minutes".** A null `reopenedPercent`
means nothing was resolved in the range, so the ratio has no denominator;
reporting it as `0%` tells the user their reopen rate is perfect when it is
actually unmeasured. Say "no data for this range" for a null, on any field
under either block.

## Presenting results

State the date range you actually measured — from `data.range`, on either
tool — alongside the numbers, not just the numbers. A metric with an unstated
or assumed range is the one people misquote in a status update later, and on
`get_reports_summary` an unstated range may well be "this calendar month"
rather than the "overall" the user asked for.

If the user asks for a comparison across periods, make two calls with explicit
bounds rather than inferring a trend from one — especially across `date_to`
values that need the time-of-day suffix on `get_reports_summary` to include a
whole day.
