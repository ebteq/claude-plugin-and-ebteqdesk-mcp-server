---
name: knowledge-base
description: Read and write the Ebteqdesk knowledge base — search published help articles, read one in full (including per-language versions), walk the category and folder tree across Ebteqdesk's two knowledge bases (Salon V3 and Warni), propose a new article or edit an existing one for human review, check a review verdict or the proposal queue, upload a screenshot and embed it in an article, create categories and folders, delete an empty one, and reorder them. Use whenever the user asks about the knowledge base, help articles, documentation for customers, the KB tree, writing or updating an article, adding a screenshot to an article, or where an article should live.
---

# Ebteqdesk knowledge base

Verified against `ebteqdesk-mcp` 4.3.0 (42 tools).

Two halves that behave very differently: reading is a plain query, and
**writing can never publish**. Both halves now span **two separate knowledge
bases**, and picking the wrong one is the one mistake here that cannot be
undone through this API.

## 🔴 Two knowledge bases: Salon V3 and Warni

Ebteqdesk hosts two separate public help sites behind one authoring surface —
**Salon V3** (`salonv3`) and **Warni** (`warni`). A category belongs to exactly
one of them; every folder and article inherits it through its category. There
is no separate "which knowledge base" question to answer when you write an
article — **choosing a folder chooses the knowledge base**, because the folder
id already decided it.

🔴 **Filing into the wrong product's folder puts the article on the wrong
public help site — permanently.** `update_kb_article` has no argument that
moves an article to a different folder (see below), and there is no
delete-article tool anywhere on this API. The only fix is a human, in the
Ebteqdesk authoring UI, recreating the article under the correct folder and
removing the misfiled one. **If it is not already obvious which product an
article is for, ask before picking its folder.**

**Finding the right folder:** `list_kb_tree(portal="salonv3" | "warni" |
"all")`, and the flat projections `list_kb_categories(portal=...)` /
`list_kb_folders(kb_category_id, portal=...)`, all take the same `portal`
argument. 🔴 **Omitting `portal` on every read tool below means `"salonv3"`,
never `"all"`** — a caller that does not ask sees exactly the one knowledge
base that existed before Warni did. Pass `portal="all"` deliberately when you
actually want both interleaved. With `"all"`, `position` is dense **per
portal** — both commonly have a category at `0` — so the two portals'
categories interleave rather than sitting in two contiguous blocks; group by
`portal` yourself if you need them apart.

**`create_kb_category` takes a `portal`; nothing else does.**
`create_kb_category(name, description, portal)` — `"salonv3"` or `"warni"`,
defaulting to `"salonv3"`, and 🔴 **`"all"` is refused here with a 422** (a
category has to belong to exactly one knowledge base). It is **write-once**:
there is no way to move a category to the other knowledge base afterwards.
`update_kb_category`, `create_kb_folder`, `update_kb_folder`,
`propose_kb_article` and `update_kb_article` take **no** `portal` argument at
all, deliberately — a folder id already carries it, and a second argument
would be a second, conflicting source of truth.

⚠️ **A stale Ebteqdesk install answers a `portal` filter with an error, not a
wrong result.** Passing `portal` to a read tool against an installation that
predates two-knowledge-base support raises rather than silently handing back
both knowledge bases mixed together while looking filtered (Laravel drops a
query parameter no route declares, which is a worse failure than an error
would be). If you hit this, the fix is deploying the current Ebteqdesk
release, not retrying with a different value.

## 🔴 Scopes: a read-only key gets a 403 on a READ here

This is the trap. There are **two different corpora** behind these tools, and
the scope follows the corpus, not the verb:

| Tool | Scope | Also needs `kb.manage`? |
|---|---|---|
| `search_kb_articles` | `kb:read` | No |
| `get_kb_article` | `kb:read` | No |
| `list_kb_tree` | **`kb:write`** | **Yes** |
| `list_kb_categories` | **`kb:write`** | **Yes** |
| `list_kb_folders` | **`kb:write`** | **Yes** |
| `get_kb_article_review` | **`kb:write`** | **Yes** |
| `list_kb_proposals` | **`kb:write`** | **Yes** |
| every write tool below | `kb:write` | Yes |

**Five tools need a WRITE scope and write nothing.** `list_kb_tree`,
`list_kb_categories`, `list_kb_folders`, `get_kb_article_review` and
`list_kb_proposals` are reads, but their corpus is the **authoring** one —
ids, drafts, and `agents`-only internal folders. `kb:read` deliberately gates
the *public help corpus* and is the one scope with no role requirement behind
it, so mounting the authoring view on it would widen that corpus.

🔴 **`list_kb_proposals` is the loudest case: it is the only read on this
server that enumerates drafts.** It returns every article carrying a review
state — pending, approved, or rejected with the reason — across the **whole
installation**, whoever proposed it, not just what this key filed. Do not
tell a user "you have three rejections" when what you have is three
rejections somewhere in their knowledge base.

⚠️ **`kb.manage` is a ROLE ability, and it is administrator/supervisor only.**
`kb:write` resolves only while the key carries it **and** the account's role
holds `kb.manage`. An agent- or developer-role account cannot walk the tree, pick
a folder id, check a review, or write anything — **whatever key is minted for
it**. Minting a new key does not fix a missing ability.

So a `403` from a knowledge base tool has two possible causes and they need
opposite answers. Call `whoami` and compare:

- the scope is missing from **`apiKey.requested`** → the key was never minted
  with it. **A new key fixes this.**
- the scope is in `requested` but absent from **`apiKey.scopes`** → the
  account's role does not back it. **A new key changes nothing**; the account
  needs the role.

## Reading

- `search_kb_articles(query, page, per_page, portal)` — full-text search, or
  the whole corpus newest-first when `query` is omitted. `per_page` is 1..100,
  default 25. `portal` is `"salonv3"` (the default if omitted), `"warni"` or
  `"all"`.
- `get_kb_article(slug, locale, portal)` — one article in full, with
  `body_html`.

⚠️ **These two return only published, public articles — even for an
administrator.** This is the same corpus an anonymous visitor to the help portal
sees. Internal runbooks and `agents`-only articles that are plainly visible in
the Ebteqdesk web UI are **not** reachable through them.

🔴 **"Public" is the article's own setting where it has one, and its folder's
only where it does not — this stopped being simply "the folder's setting" on
2026-08-19.** An article can carry its own visibility, overriding its folder's
in either direction: a folder marked `agents` in `list_kb_tree` may still hold
one article `search_kb_articles` returns, and a `public` folder may hold one
that never shows up here. Read a folder's `visibility` to decide **where to
file** something new; never read it to decide whether some *existing* article
is public. Nothing on this API can set that per-article override, so it never
changes what filing a new article does — only how you should interpret a
folder's contents that a human may already have touched. If you need a
specific article's real, effective visibility, ask `search_kb_articles` /
`get_kb_article` for it directly — that corpus is already
effective-visibility filtered.

⚠️ **`list_kb_tree` is the exception, and it is not filtered.** It returns
**ids** and **every folder whatever its visibility**, internal ones included —
that is exactly why it sits behind `kb:write` + `kb.manage`. Do not repeat the
"published and public only" rule about the tree; it is false there.

⚠️ **The tree's names are internal.** Folder and category names are staff
organisation, not customer-facing copy, and may name internal teams, systems or
accounts. Use them to choose where to file; do not quote them to a customer.

⚠️ **A not-found result is ambiguous on purpose.** An article that exists but is
unpublished or internal returns *exactly* the same error as a slug that never
existed. So you cannot tell "there is no such article" from "there is one and
you may not read it" — and you must not tell the user you can. Say the article
is not in the published knowledge base, and leave it there. With a `locale`,
the same not-found result *also* covers "no version of this article exists in
that language" — report that no published article answers the slug (in that
language, if one was asked for), never that the article "is missing its
translation", which you cannot know.

`body_html` is **sanitised HTML, not markdown.** Ebteqdesk has no markdown form
of an article. Render or convert it; never hand it to a user as markdown source.

🔴 **An article can carry its own text per language, and omitting `locale` is
not the same as asking for English.** `get_kb_article(slug, locale="en" |
"zhcn")` reads the article **as a reader of that language sees it** —
`"zh-cn"` is not accepted, only `"zhcn"`. Without a `locale` you get the
article's shared **base** text, which for a Chinese-only article can be
pre-translation English someone left behind: real and published, just not what
either help centre currently shows. **Never quote an article back to a user in
a language they did not write to you in** — pass `locale` to match them.

## Structure, and where a folder id comes from

- `list_kb_tree(portal)` — every category, every folder, and **their ids**,
  for one knowledge base (default `"salonv3"`) or both interleaved (`"all"`).
- `list_kb_categories(portal)` / `list_kb_folders(kb_category_id, portal)` —
  flat views over the same tree, same `portal` vocabulary and same default.
  Each folder row carries its own `portal`, copied down from its category.

🔴 **`propose_kb_article` needs a `kb_folder_id`, and only these three tools
return one.** Article payloads carry `{slug, name}` pairs and no ids at all. So
call one **before** filing an article, and read the `id` off the folder you
mean. **A folder id also picks the knowledge base** (see above) — so guessing
one, or picking a folder from the wrong product, does not just file an article
somewhere a human did not expect: it can put it on the **wrong product's
public help site**, and **there is no delete-article tool to undo it with.**

Call one for the empty case too: a knowledge base with no categories answers
`{"data": []}`, which means somebody has to create a category **and** a folder
before an article can be filed at all — in that portal.

⚠️ `list_kb_categories` and `list_kb_folders` are **flat projections over
`list_kb_tree`**, not scoped queries — each fetches the *whole* tree (for the
requested portal) and filters it client-side. `list_kb_folders(kb_category_id=3)`
is not cheaper than the full call. **Never loop them per category**; call once
and group the result yourself.

`slug` on a category or a folder is **re-derived every time the name changes**,
so it is not a stable identifier. Use `id`.

## 🔴 Writing cannot publish. Nothing here can.

Every article write lands `status: draft`, `review_state: pending` on a new
article. Publishing is a human's browser session, deliberately — an
integration that could publish could put unreviewed text in front of
customers.

**Do not tell a user their article is live, and do not promise when it will be.
You cannot know.**

`propose_kb_article(kb_folder_id, title, body, seo_title, seo_description,
tags, locale, en_title, en_body, en_seo_title, en_seo_description, zhcn_title,
zhcn_body, zhcn_seo_title, zhcn_seo_description)` files a **real draft into a
human's review queue**. That is 15 arguments, not 6 — the last nine exist so
one call can carry a bilingual article; see below. A row appears in the live
knowledge base and a person is expected to read it.

Keep the `reference` it returns (e.g. `"id:42"`). `slug` is **null until a human
first publishes**, so the reference is the only handle you have — do not try to
construct a slug from the title.

`update_kb_article(reference, title, body, seo_title, seo_description, tags,
locale, en_*, zhcn_*, allow_missing_versions)` rewrites a **draft** in place,
or **stages a revision** against a **published** article for a human to
approve — which of the two happens is the article's own status, not your
choice, and the two outcomes are told apart by one thing: whether the response
carries a top-level `revision` key.

🔴 **There is no 409 on this tool any more.** Older notes, older client code
and older habits say a published article is refused outright with `409` —
that stopped being true. Against a **published** article, `update_kb_article`
now succeeds with **202**, staging a pending revision, and returns the
**live, unchanged** article under `data`: same old title, same old body, same
`updated_at`. **Check for the `revision` key, not for changed text in
`data`.** Its presence is the whole signal: present means your edit is staged
and not applied; absent means it was applied in place (the ordinary
draft/200 case). A model that reads `data` back to "confirm" its own edit on a
published article will find none of its text there and can wrongly conclude
nothing happened — say instead that the edit is waiting for a reviewer and the
live page is unchanged until they accept it. You cannot approve it and cannot
know when they will.

`tags` **replaces the whole set** — `[]` clears every tag. Omitted arguments
are not edited; an argument passed as an **empty string** is an edit that
clears the field.

🔴 `kb_folder_id` **cannot be changed later** — `update_kb_article` does not
accept it, on either branch. A folder carries the article's visibility **and**
its knowledge base (see "Two knowledge bases", above), so moving an article is
both a visibility change and a portal change wearing an organisational
costume, and both stay a human act. Choose the folder deliberately; if you are
unsure, ask.

**Show the user the article you intend to file — title, folder, and the full
body — and file it only once they agree.**

### Bilingual content: one call, both languages

`propose_kb_article` and `update_kb_article` each take `locale` plus eight
per-language fields: `en_title`, `en_body`, `en_seo_title`,
`en_seo_description`, and the same four spelled `zhcn_*`.

- **None of them given** — the article has no language version and shows in
  every language from the shared base text. This is the default, and what
  most existing articles are.
- **`locale="en"` or `locale="zhcn"`** — the one-language form: the plain
  `title`/`body` become that single language's own version.
- **The `en_*` and `zhcn_*` fields together, in ONE call** — files or edits a
  bilingual article. 🔴 **Do not file one language now and add the other with
  a later `update_kb_article` call.** On a draft that merely sends the article
  through review twice. On a **published** article it *loses a language*:
  there is one revision row per article, so a second call **replaces** the
  first, and a revision that would drop a language out of its current help
  centre is refused with a 422 unless you pass
  `allow_missing_versions=True` — which performs that removal, the opposite of
  adding a language. There is no order of two calls that works; send both
  languages in one call whenever you mean both.

⚠️ **The language of the text is not detected.** Whatever argument you put text
in is what you are asserting the text is — English prose in `zhcn_body` files
as the Chinese version, and nothing will notice until a reader does.

⚠️ **You cannot set `visibility` on any article write**, and sending it is
silently ignored — a 201/200/202 does not mean it applied. Visibility is
always inherited from the folder unless a human has separately set a
per-article override in the authoring UI (see "Reading", above), which
nothing on this API can create, change or remove.

### 🔴 Checking a verdict does not mean updating it

Use **`get_kb_article_review(reference)`** to see whether a proposal was
approved or rejected, and to read `review.note` — the rejection reason. On a
**published** article it also returns the `revision` block — the staged
edit's own verdict, a separate record from the article's own `review` — so
this is the one tool that safely reads either kind of pending change.

⚠️ **On a published article, `revision` reads three ways and the third is not
obvious.** It is never `"approved"` — approving a revision *applies* it and
*deletes* the row — so: `{"state": "pending", ...}` means your edit is
waiting and the live text is still old; `{"state": "rejected", "note": ...}`
means it was refused, and you should revise and send **one** new
`update_kb_article` call after reading the note (since that call erases it
again); and `null` means either nothing was ever staged, or something was
staged **and approved**. Nothing in the payload tells those two `null` cases
apart — compare `data.title` / `data.body_html` (or read
`get_kb_article(slug, locale=...)`, which is what an actual reader sees)
against your edit to find out which.

**Lost the `reference`?** `list_kb_proposals(review_state, per_page, page,
portal)` lists every article carrying a review state — newest submission
first — across the whole installation, not just this key's own proposals, and
cannot be narrowed to "mine". Recognise your own by `title`. Use it to find a
`reference`, then `get_kb_article_review` to read that one article safely.

**Do not call `update_kb_article` to look at the state.** Every update
**re-queues a draft** or **replaces a published article's pending revision**,
clearing the reviewer's note either way — so checking that way destroys the
very rejection reason you were trying to read, and puts the article back in
front of a reviewer who had already dealt with it. `get_kb_article_review` and
`list_kb_proposals` exist precisely because update-to-check used to be the
only way.

A `pending` state means no human has looked yet. Do not re-submit to "bump" it
— on a draft, a resubmission moves the article to the back of the queue it is
already in; on a published article, it discards the revision already queued
and stages a fresh one.

## Creating an article to the house standard

This is how articles on Salon V3 are actually written. Follow it unless the
user asks for something else.

⚠️ **This convention has only been observed on Salon V3 content.** Nothing
here says whether Warni's help centre should follow the same bilingual
pattern, and nothing on this API enforces either choice. Do not invent a
Warni house style, and do not assume Salon V3's applies there — ask which
convention applies before writing a Warni article, or read a few of Warni's
existing articles once there are some to follow.

### Naming is bilingual, and the separator differs per level

| Level | Pattern | Example |
|---|---|---|
| Category | `中文 English` — a **space** | `报表与对账 Reports & Reconciliation` |
| Folder | `中文 English` — a **space** | `销售报告 Sales Reports` |
| Article title | `中文 - English` — a **spaced hyphen** | `如何导出月份销售报告 - How to export monthly sales report` |

The **slug is derived from the English half**, so
`报表与对账 Reports & Reconciliation` becomes `/reports-reconciliation`. You do
not set the slug and you cannot; it follows the name.

### Body: a one-line summary, then step → screenshot pairs

The body is **HTML, not markdown**. One short summary sentence, then each step as
a short imperative sentence in **English, then the same sentence in Chinese**,
then the screenshot for that step:

```html
<p>Steps to export a full month of sales from the sales report screen.</p>
<p>Go to sales report.</p>
<p>进入销售报告。</p>
<p><br></p>
<img src="/kb/media/01M09GF82WHB69ZGRHAMRF9ANY" alt="Sales report screen">
```

Every `<img>` carries an `alt` describing what the picture shows.

⚠️ **Standardise on the bilingual body** for a Salon V3 article. The two
published articles observed on that portal disagreed with each other on this;
the bilingual one is the model. Do not copy the English-only one.

### SEO fields are ENGLISH ONLY

- `seo_title` — **≤ 70 characters**, English, no Chinese.
- `seo_description` — **≤ 160 characters**, English, one sentence stating the
  outcome the reader gets.

### 🔴 The media sequence, and it must be exact

An article with a screenshot takes **two calls, in this order**:

1. **`upload_kb_media(file_path)`** → returns
   `{"ulid": "01J…", "url": "/kb/media/01J…", "kind": "image", "mime": …,
   "width": …, "height": …, "size_bytes": …, "original_name": …}`
2. Put **the returned `url`, exactly as it came back**, into the body as
   `<img src="/kb/media/01J…" alt="what the picture shows">`
3. **Save the article** — `propose_kb_article` or `update_kb_article`. The save
   is what links the file to the article: Ebteqdesk derives the media↔article
   link **from the saved body**, not from the upload.

🔴 **NEVER invent or guess a `/kb/media/` URL.** A ULID is 26 random characters.
One you made up resolves to nothing and renders a **broken image in a live
knowledge base** — visible to every reader and invisible to you, because you are
signed in. No url from the tool means no image. Do not reuse a url from an
earlier conversation either.

⚠️ An upload never referenced in a saved body is an **orphan**: attached to
nothing, belonging to no article, and swept away by the server's cleanup once it
is seven days old. Do not upload speculatively. If you upload something and then
decide not to use it, say so rather than leaving the user thinking a file was
filed somewhere.

⚠️ **`upload_kb_media` reads the user's own filesystem.** It is the only tool
here that does, and that is a risk to the **user**, not to the helpdesk. Upload
**only files the user explicitly named**. Never sweep or list a directory looking
for something suitable, and never guess at a path — if the one you were given
does not exist, ask.

⚠️ **Never retry a timed-out upload.** Every call stores a *new* copy under a
*new* ULID, so a retry that "worked the second time" has left a duplicate nobody
references. Ask the user instead.

Accepted: JPG, PNG, WebP, GIF, MP4, WebM. Nothing else — no PDF, no SVG, no zip.
Images cap at 10 MB, video at 50 MB. **The type is decided by sniffing the file's
content, not its extension**, so renaming `report.pdf` to `report.png` does not
get it past the server. Do not suggest that it will.

### 🔴 Two things you will otherwise report wrongly

**1. Every folder created through this API is `visibility: agents` — INTERNAL —
and no argument changes it.** Not on `create_kb_folder` and not on
`update_kb_folder`. Nothing filed into such a folder reaches a customer until a
**human** changes the visibility in the Ebteqdesk web UI.

**So never tell a user their article will be visible to customers.** Check
`visibility` in `list_kb_tree(portal=...)`, in the product's own knowledge
base, if you need to know where a folder actually stands: only a
**`public`** folder's **published** articles reach a customer (subject to the
per-article override noted under "Reading", above).

**2. Published ≠ public, and this can differ by knowledge base.** Publishing
an article never moves it out of an internal folder — check a folder's
`visibility` in `list_kb_tree(portal=...)` for the product the article
belongs to, rather than assuming it is reachable, and check that product's
portal specifically rather than assuming the other one behaves the same way.
Salon V3's knowledge base has been observed with published articles sitting
entirely inside an `agents` folder, so its public portal showed *"There is
nothing in this section yet."* — a useful worked example when a user asks why
their live article is not showing, but a snapshot of one portal's state at one
point in time, not a guaranteed property of either knowledge base going
forward. Re-check `list_kb_tree` rather than assuming.

(`articles_count` on a folder includes drafts, so a folder showing 4 may have
nothing published in it at all.)

## Structure writes

- `create_kb_category(name, description, portal)` — name ≤ 120, description
  ≤ 255, `portal` is `"salonv3"` (default) or `"warni"` — **not** `"all"`, and
  write-once (see "Two knowledge bases", above).
- `update_kb_category(category_id, name, description)` — **no `portal`
  argument**, deliberately: a category cannot be moved to the other knowledge
  base through this API, ever.
- `delete_kb_category(category_id)`
- `create_kb_folder(kb_category_id, name, description)` — also no `portal`;
  the folder's knowledge base is fixed by its category's.
- `update_kb_folder(folder_id, name, description)`
- `delete_kb_folder(folder_id)`

Read `list_kb_tree(portal=...)` before creating anything, **in the knowledge
base you mean**, so you are extending that product's taxonomy rather than
duplicating a category that already exists there under a different name. A
knowledge base that grows a category per article is worse than one with none.

🔴 **The slug is derived from the name and cannot be set**, and a collision is
checked against the **derived** slug — so `POS` and `  p.o.s!  ` collide even
though they are different strings. 🔴 **Three slug scopes, not two:** category
slugs are unique **per knowledge base** — Salon V3 and Warni may each have
their own "POS" — folder slugs are unique only **within their category**
(`FAQ` under Billing and `FAQ` under Account are both fine), and article slugs
are unique **globally**, across both portals — the widest of the three.

🔴 **Renaming a category or a folder re-derives its slug and CHANGES ITS
PORTAL URL** — `/support/salonv3-kb/{category}/{folder}` or
`/support/warni-kb/{category}/{folder}`, depending which knowledge base the
category belongs to. Unlike an article's slug, which is frozen at first
publish, these follow the name on every save, and there is no redirect on a
rename. ⚠️ **Do not generalise from the fact that the old single-portal path,
`/support/kb/...`, now 301s** — that is a one-time route migration for the
old links, not a standing redirect mechanism; a category or folder rename
still gets no redirect of its own, before or after that migration. Say so
before you rename, and read the `slug` back out of the response. Omit `name`
entirely to leave the URL alone.

`update_kb_folder` **cannot move a folder, cannot change its visibility, and
cannot change its knowledge base.** All three stay access-control or
structural decisions for a human in the Ebteqdesk UI.

### 🔴 The deletes are a REFUSAL, never a cascade

`delete_kb_category` and `delete_kb_folder` are the **only** tools on this server
that remove anything, and nothing on this API puts a removed row back. No trash,
no restore, no version history — the response is the only record of what it was.

- A category still holding folders → **422 naming the count**: *"This category
  still holds 2 folders. Move or delete them first."* Nothing is deleted.
- A folder still holding articles → **422 naming the count**: *"This folder
  still holds 3 articles. Move or delete them first."* Nothing is deleted.

🔴 **That refusal is a safety property, not an obstacle to route around. There is
NO delete-article tool anywhere on this API** — so a folder that has been filed
into can only be emptied by a person in the Ebteqdesk web UI.

**Never delete folders to "clear the way" for a category delete, and never ask a
human to empty a folder so a delete goes through, unless the user asked for
exactly that.** If you hit the refusal, report the count and stop.

**Name what you are about to remove and get the user's agreement first.** Never
retry a delete that timed out — it may have landed, and if the id has been reused
a retry removes something else. Read `list_kb_tree` instead.

`position` in a delete receipt is the index the row **vacated**: everything that
sat after it has already moved up by one, so any positions you were holding are
stale.

## Reordering

`reorder_kb_children(scope, ordered_ids, parent_id)` where `scope` is
`"categories"`, `"folders"` or `"articles"`.

Four rules, and getting any of them wrong is a refusal rather than a silent
mess — which is the point:

1. **`ordered_ids` is the WHOLE ordered sibling set, never a delta.** Positions
   are dense and 0-based; the server rewrites all of them.
2. **A stale set is a 422.** If the posted ids are not exactly the current
   sibling set — one missing, one extra, one duplicated — the call is refused
   with "the order must list every item exactly once". Re-read `list_kb_tree()`
   and post the current set. Do not try to work around it.
3. **`parent_id` is required for `folders` and `articles`, and refused for
   `categories`.** Categories have no parent; passing one there would look like
   a scoped reorder while rewriting every category in the installation, so it is
   rejected before the request is sent.
4. 🔴 **On `scope="categories"`, `ordered_ids` must be ONE knowledge base's
   whole category list — never both portals' together.** Before Salon V3 and
   Warni existed, "the complete sibling set" meant every category in the
   installation, and a caller written for that world still posts exactly that
   — it is now refused: 422 on `errors.ids`, *"The order must list categories
   from a single knowledge base portal. Reorder each portal in its own
   request."* The fix is **not** a shorter list — it is still that portal's
   **whole** category set, one portal per call. Read one portal's categories
   with `list_kb_tree(portal="salonv3")` or `list_kb_tree(portal="warni")`,
   reorder that list, then repeat for the other portal if both need
   reordering. `scope="folders"` and `scope="articles"` need no such split: a
   folder's siblings are one category's folders, an article's siblings are one
   folder's articles, and a category belongs to exactly one knowledge base
   already — neither sibling set could ever span two portals.

🔴 **Read the current order before you change it, and keep it.** The old order
is not stored anywhere and there is no undo — putting it back means posting the
previous list, which you can only do if you read it first. Reordering changes
what every agent sees, and on a public folder what every customer sees.

✅ It is, however, **idempotent**: positions are assigned by index, so replaying
the same call leaves the same order. It and `set_ticket_status` are the only two
writes on this server that are safe to retry.

Show the user the before-and-after order, and send only once they agree.
