---
name: knowledge-base
description: Read and write the Warnidesk knowledge base — search published help articles, read one in full, walk the category and folder tree, propose a new article or edit an existing one for human review, check a review verdict, upload a screenshot and embed it in an article, create categories and folders, delete an empty one, and reorder them. Use whenever the user asks about the knowledge base, help articles, documentation for customers, the KB tree, writing or updating an article, adding a screenshot to an article, or where an article should live.
---

# Warnidesk knowledge base

Verified against `warnidesk-mcp` 1.6.0 (32 tools).

Two halves that behave very differently: reading is a plain query, and
**writing can never publish**.

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
| every write tool below | `kb:write` | Yes |

**Four tools need a WRITE scope and write nothing.** `list_kb_tree`,
`list_kb_categories`, `list_kb_folders` and `get_kb_article_review` are reads,
but their corpus is the **authoring** one — ids, drafts, and `agents`-only
internal folders. `kb:read` deliberately gates the *public help corpus* and is
the one scope with no role requirement behind it, so mounting the authoring view
on it would widen that corpus.

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

- `search_kb_articles(query, page, per_page)` — full-text search, or the whole
  corpus newest-first when `query` is omitted. `per_page` is 1..100, default 25.
- `get_kb_article(slug)` — one article in full, with `body_html`.

⚠️ **These two return only published, public articles — even for an
administrator.** This is the same corpus an anonymous visitor to the help portal
sees. Internal runbooks and `agents`-only articles that are plainly visible in
the Warnidesk web UI are **not** reachable through them.

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
is not in the published knowledge base, and leave it there.

`body_html` is **sanitised HTML, not markdown.** Warnidesk has no markdown form
of an article. Render or convert it; never hand it to a user as markdown source.

## Structure, and where a folder id comes from

- `list_kb_tree()` — every category, every folder, and **their ids**.
- `list_kb_categories()` / `list_kb_folders(kb_category_id)` — flat views.

🔴 **`propose_kb_article` needs a `kb_folder_id`, and only these three tools
return one.** Article payloads carry `{slug, name}` pairs and no ids at all. So
call one **before** filing an article, and read the `id` off the folder you
mean. Guessing an id files a real article somewhere a human did not expect, and
**there is no delete-article tool to undo it with.**

Call one for the empty case too: a knowledge base with no categories answers
`{"data": []}`, which means somebody has to create a category **and** a folder
before an article can be filed at all.

⚠️ `list_kb_categories` and `list_kb_folders` are **flat projections over
`list_kb_tree`**, not scoped queries — each fetches the *whole* tree and filters
it client-side. `list_kb_folders(kb_category_id=3)` is not cheaper than the full
call. **Never loop them per category**; call once and group the result yourself.

`slug` on a category or a folder is **re-derived every time the name changes**,
so it is not a stable identifier. Use `id`.

## 🔴 Writing cannot publish. Nothing here can.

Every article write lands `status: draft`, `review_state: pending`. Publishing is
a human's browser session, deliberately — an integration that could publish could
put unreviewed text in front of customers.

**Do not tell a user their article is live, and do not promise when it will be.
You cannot know.**

`propose_kb_article(kb_folder_id, title, body, seo_title, seo_description, tags)`
files a **real draft into a human's review queue**. A row appears in the live
knowledge base and a person is expected to read it.

Keep the `reference` it returns (e.g. `"id:42"`). `slug` is **null until a human
first publishes**, so the reference is the only handle you have — do not try to
construct a slug from the title.

`update_kb_article(reference, ...)` rewrites a draft. Omitted arguments are not
edited; an argument passed as an **empty string** is an edit that clears the
field. `tags` **replaces the whole set** — `[]` clears every tag.

⚠️ **A published article is refused with 409**, not edited and not unpublished.
This surface edits drafts only. Retrying cannot change that. Tell the user the
change needs a person in the authoring UI.

🔴 `kb_folder_id` **cannot be changed later** — `update_kb_article` does not
accept it. A folder carries the article's visibility, so moving an article is a
visibility change wearing an organisational costume, and stays a human act.
Choose the folder deliberately; if you are unsure, ask.

**Show the user the article you intend to file — title, folder, and the full
body — and file it only once they agree.**

### 🔴 Checking a verdict does not mean updating it

Use **`get_kb_article_review(reference)`** to see whether a proposal was
approved or rejected, and to read `review.note` — the rejection reason.

**Do not call `update_kb_article` to look at the state.** Every update
**re-queues the article and clears the reviewer's note** — so checking that way
destroys the very rejection reason you were trying to read, and puts the article
back in front of a reviewer who had already dealt with it.
`get_kb_article_review` exists precisely because that used to be the only way.

A `pending` state means no human has looked yet. Do not re-submit to "bump" it —
a resubmission moves the article to the back of the queue it is already in.

## Creating an article to the house standard

This is how articles on this install are actually written. Follow it unless the
user asks for something else.

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

⚠️ **Standardise on the bilingual body.** The two published articles on the live
install disagree with each other on this; the bilingual one is the model. Do not
copy the English-only one.

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
   is what links the file to the article: Warnidesk derives the media↔article
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
**human** changes the visibility in the Warnidesk web UI.

**So never tell a user their article will be visible to customers.** Check
`visibility` in `list_kb_tree` if you need to know where a folder actually
stands: only a **`public`** folder's **published** articles reach a customer.

**2. Published ≠ public.** On this install both published articles sit in an
`agents` folder, so the public portal shows *"There is nothing in this section
yet."* Publishing an article does not move it out of an internal folder. Use that
as the concrete example when the user asks why their live article is not on the
portal.

(`articles_count` on a folder includes drafts, so a folder showing 4 may have
nothing published in it at all.)

## Structure writes

- `create_kb_category(name, description)` — name ≤ 120, description ≤ 255.
- `update_kb_category(category_id, name, description)`
- `delete_kb_category(category_id)`
- `create_kb_folder(kb_category_id, name, description)`
- `update_kb_folder(folder_id, name, description)`
- `delete_kb_folder(folder_id)`

Read `list_kb_tree()` before creating anything, so you are extending the taxonomy
rather than duplicating a category that already exists under a different name.
A knowledge base that grows a category per article is worse than one with none.

🔴 **The slug is derived from the name and cannot be set**, and a collision is
checked against the **derived** slug — so `POS` and `  p.o.s!  ` collide even
though they are different strings. Category slugs are unique **globally**; folder
slugs only **within their category** (`FAQ` under Billing and `FAQ` under Account
are both fine).

🔴 **Renaming a category or a folder re-derives its slug and CHANGES ITS PORTAL
URL.** Unlike an article's slug, which is frozen at first publish, these follow
the name on every save. Saved links break, and there is no redirect. Say so
before you rename, and read the `slug` back out of the response. Omit `name`
entirely to leave the URL alone.

`update_kb_folder` **cannot move a folder and cannot change its visibility.**
Both are access-control decisions that stay with a human in the Warnidesk UI.

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
into can only be emptied by a person in the Warnidesk web UI.

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

Three rules, and getting any of them wrong is a refusal rather than a silent
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

🔴 **Read the current order before you change it, and keep it.** The old order
is not stored anywhere and there is no undo — putting it back means posting the
previous list, which you can only do if you read it first. Reordering changes
what every agent sees, and on a public folder what every customer sees.

✅ It is, however, **idempotent**: positions are assigned by index, so replaying
the same call leaves the same order. It and `set_ticket_status` are the only two
writes on this server that are safe to retry.

Show the user the before-and-after order, and send only once they agree.
