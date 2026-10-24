---
name: beakr
description: "Access your organization's knowledge base, research questions with citations, and manage knowledge base pages using Beakr's MCP tools. Use when the user asks about their organization, team, projects, people, decisions, or processes -- or wants to create/update knowledge base content."
---

# Beakr Knowledge Base

Beakr is the team's organizational memory. Teams write decisions, processes, ownership, and dated events here so that 6 months later the answer to "why did we do X" is one search away instead of lost in Slack archives or tribal knowledge.

Use the Beakr MCP tools to search and read existing knowledge — and to add to it when the conversation produces something durable.

## When to use

- User asks about their organization, team, people, decisions, or processes
- User wants to look something up ("What do we know about X?", "Who owns Y?")
- User wants to research a topic across documents and connected services
- User wants to create, update, or audit knowledge base pages
- **The conversation itself surfaces durable information worth capturing** (see "Proactive capture" below)

## Proactive capture

You are not just a read interface. When the conversation produces something durable, **propose adding it — don't wait to be asked**.

**Capture when all of:**
- **Durable**: would still matter in 6 months
- **Shareable**: others would benefit, not just this user right now
- **Not already in Beakr**: check with `knowledge_base` command `sections` first
- **In scope**: the user can write to it (their personal project, or a team project they belong to — confirm with `list_projects`)

**Good candidates:**
- Decisions with rationale ("we chose X over Y because…")
- Named processes ("here's how we onboard a customer")
- Role / ownership assignments ("Alex owns billing")
- Dated events: launches, hires, fundraising rounds, incidents
- External relationships: investors, partners, key vendors
- Recurring questions you just answered

**Skip:**
- Debugging traces, transient state
- Information already in source code, git history, Linear/Jira tickets
- Hot takes, speculation, "thinking out loud"
- Anything the user clearly considers ephemeral

**Etiquette:**
- **Surface the suggestion before writing**: "This decision about X seems worth capturing — want me to propose it for your wiki?"
- Never auto-write. Always propose, always wait for explicit OK -- unless the write result says the user's auto-approve is on; then it is applied automatically, so do not ask them to approve it.
- One capture offer per significant thread — don't pepper the user.
- Cite the current session as a `conversation` source with `meta.excerpt` so it is traceable.

## Tools

| Tool | When to use |
|------|-------------|
| `knowledge_base` | Every read over the wiki, chosen by `command`: `sections`, `grep`, `ls`, `cat`, `hover`, `links`, `references`, `timeline`, `rows` (every map card's Facts, Connections or Findings as one table), `provenance`, `blame`, `sources`, `log`, `diff`, `show`, `ontology`, `suggest_parent`, `diagnostics`, `completions`, `proposals`. |
| `knowledge_base_write` | Every change, chosen by `action`: `edit_section`, `add_fact`, `add_connection`, `add_finding`, `edit_rows`, `sort_table`, `new`, `edit`, `find_replace`, `mv`, `archive`, `merge`, `copy`, `page_type`. Stages a proposal, with two exceptions that apply at once: `sort_table` only reorders a table, and a `page_type` the verifier accepts is active for the project or org at once (a global type waits for a site admin). On a project-map card use the three `add_*` actions to add one row or one dated finding, and `edit_rows` to change, merge or delete rows (Facts, Connections or Findings). A card's table rows change only through these actions: an `edit_section` or `edit` that would change one is refused, and `find_replace` changes the text around the rows and reports the row matches it left for `edit_rows`. A write to one section or row prints its proposal's id and `content_hash`. |
| `documents` | Where available: `grep` and `read` over the original documents themselves, for exact wording a page does not carry. |
| `read_file` | The original document a citation points at. Pass the citation's readable reference (`memo.pdf › p. 7`), the `path › place` from a Sources line (`deals/acme/memo.pdf › p. 7`), or the file id. It reads files and canvases only: for a connected item (Slack, email, calendar, Jira) use its excerpt and open its url. Long files come back in pieces (`offset`). |
| `research` | When your client exposes it: one cited answer across the knowledge base, documents and connected services (Slack, Gmail, Calendar, Jira...). |
| `show_proposal` / `accept_proposal` / `dismiss_proposal` | Review a staged proposal, which names who filed it ("filed by Dana Reyes via claude-code/2.1"); accept only when the user explicitly asks, passing the `content_hash` the write or `show_proposal` printed as `reviewed_hash`. A proposal that changed since it was shown is refused; one that changes a card's table rows is not accepted without the hash. Where the organization or project requires a person to accept what agents write, `accept_proposal` is refused (`person_review_required`) and nothing is applied; the hosted server's `show_proposal` says "Needs a person to accept". Tell the user it waits for them to accept in Beakr, and do not retry. |
| `list_projects` | Discover projects (including the personal one) to use as `scope`. |
| `get_profile`, `wiki_stats`, `wiki_graph` | Org/project profile, counts, and the most-connected pages. |
| `context` | The user's Beakr context for a project (its name or ID): profile, recent activity, relevant memories and skills. |
| `conversations_search` / `import_conversation` | Find past agent sessions and chats that answered something; import a session transcript. |
| `beakr_version` / `update_beakr` | Check for a newer Beakr release; update only when the user asks. |

Every tool is listed from the start; call it directly. Only a server started with `BEAKR_MCP_TOOLS=eager` lists a smaller set plus `discover_tools`, which adds the tools it finds. A tool missing on a self-hosted installation is one that installation does not provide.

Both `knowledge_base` tools take a `command`/`action`, a flat `arguments` object, and an optional `scope`:

```
knowledge_base(command="sections", arguments={"query": "who owns billing"})
knowledge_base(command="cat", arguments={"page": "Billing", "section_id": "sec_owner"})
knowledge_base_write(action="edit_section", scope="Team Wiki", arguments={...})
```

There is no `search` command. Use `sections` (semantic, start here) or `grep` (literal text).

## Reading evidence

- **Start with `sections`.** Each hit shows the document passage (a verbatim excerpt) that supports it. Quote it directly; open the file only when the value you need is not in the excerpt.
- **Four ways to read, combined as the task needs.**

  | Mode | Commands | Use it for | It cannot |
  | --- | --- | --- | --- |
  | File system | `ls`, `cat`, `grep`; `documents` grep and read for originals | browsing, reading a page, exact names and ids, the original document | rank or count by a fact's value |
  | Graph | `links`, `hover`, `references`, `timeline` | a record's parties, instruments and documents, a chain, the order of events | "every" across records it does not reach |
  | Database | `rows` (`beakr kb rows`); `query` (`beakr kb query`, where enabled: one SQL `SELECT`, or a JSON spec) for what rows cannot express | every, how many, latest, largest, compare, as-of, what connects to X; multi-step joins, custom aggregates, X and not Y | see what was never structured into a card's tables: confirm a count with `ls` or `grep` |
  | Meaning | `sections`; `query` with `semantic` (`rank_fields_by`) for field names | something you can describe but cannot name | count or enumerate: it ranks and stops |

  Find by meaning or in the database, walk the graph, confirm in the original. Do not extract the documents and grep them; every document, its date and matter are already indexed.
- **`rows` as a database.** Every row carries its entity (the card's title), entity_type (page type), the table, the field (attribute or relation), the value (or `to`), When and its sources. Filter (`table`, `page_type`, `entity` (one title, or several as a list, `a|b|c` or `^(a|b)$`; the reply names any title that matched no card), `to` for every connection involving a page, `connected_to` for cards linked to a page either way, `attribute`, `query`, `regex: true` for patterns, dates, `current_only`, `as_of` for each card's value on a date), sort (`order_by` any column, or `amount` and `date` for typed values; `descending`) and aggregate (`group_by` entity, entity_type, year or a column: counts, sums, min, max; `group_by: value` groups a category field's consolidated values and any other value by its text). `match` sets how `attribute` finds a field: `exact` (the name or an alias), `similar` (also names holding its words) or `meaning` (the default: also the closest name by meaning, where the rows index is on); the reply says which field it used. `view: fields` lists the field names cards actually use, and `field_id` picks one.
- **0 rows is not absence.** It means no scanned card states the fact under that name. Retry with the names the reply suggests or `view: fields`, then `sections` and the cited documents; never conclude a fact does not exist from an empty `rows`.
- **Results stay under about 6k tokens.** When `next` appears, page with it rather than re-querying.
- **Exact wording:** `sources {page}` lists every cited passage; `read_file` with a citation's reference, a file id or a path opens the original.

## Find, then verify in the file

1. **Find** with the knowledge base (`sections`, `rows`, `cat`). Each citation reads as the document and the place in it, in parentheses, naming the file by its drive path when it is known: `Base rent is $48,000 per year (deals/acme/memo.pdf › p. 7).` A cited message, email, event or comment is dated by its own time, and its url opens that item when the connector gives one. Each Sources line names where a cited document lives, every place cited in it, its `read_file` id and its `source_ref`: `- deals/acme/memo.pdf › p. 7, p. 9 · read_file <id> · source_ref <ref>`.
2. **Follow** that `path › place` with `read_file` (`deals/acme/memo.pdf › p. 7`, `book.xlsx › sheet 2, B4:D9`); it returns the text around that place. When the files are on your local disk, open the path there directly. Where the `documents` tool is available, its `read` of a path in the wrong subfolder opens the one file of that name in the same record folder and says which path it opened, and a spreadsheet read ends with the first 50 rows of each sheet.
3. **Verify** every decisive value (an amount, date, count, name) in the file before you rely on it. When the file and a card disagree, the file wins: say so, and cite the file by its reference.
- **Codex:** print only the result's text, and make one Knowledge call per exec cell.

## Quick decision tree

```
User asks a question about their org, a fact, a decision or a page
  → knowledge_base sections {query}; quote the excerpt, or cat {page, section_id} on the best hit

User asks a broad question and research is available
  → research(query=...)

User asks for the most recent or largest X
  → knowledge_base rows {table: facts, page_type, attribute, order_by: when (or value), descending: true}; cat the top few, then their children for the executed documents

User asks for every X involving a party, client or instrument
  → knowledge_base rows {table: connections, to: <page>} or links {page, page_type, relation}

User asks how many X, or for counts per group
  → knowledge_base ls {page_type} (the whole population, paged), then check each; rows {..., group_by} for counts and totals per group

User asks everything about one record
  → knowledge_base hover {page}, ls {parent: <page>}, links {page, direction: out}, timeline {page}

User needs the exact wording or a value the excerpt lacks
  → read_file {path: "<path> › <place>"} from the Sources line (or the citation's reference)

User wants an exact name, ID, or phrase
  → knowledge_base grep {query}

User wants to browse pages
  → knowledge_base ls {parent?, page_type?, sort_by?}

User wants to know what happened when
  → knowledge_base timeline {timeline_query?, start_date?, end_date?}

User wants to verify sources for a claim
  → knowledge_base provenance {page} or blame {page}

User wants to create/update a page
  → See [reference/writing-pages.md](reference/writing-pages.md)

You established something the organization will want again (after a cited answer)
  → propose it: add_fact {page, attribute, value, evidence} for a sourced value (reuse the attribute names rows view: fields lists); add_connection {page, relation, to, evidence} on the more specific card; add_finding {page, date, kind, text, evidence} for your conclusion. An out-of-date value gets a newer dated row, not a deletion.

User wants to audit knowledge base quality
  → See [reference/auditing.md](reference/auditing.md)
```

## Scoping

- `knowledge_base` and `knowledge_base_write` take `scope`: a project name or ID. Omit it on reads to search everything the user can see.
- `research`, `documents`, `read_file`, `wiki_stats` and `wiki_graph` take `project`: a project name or ID too. On `read_file` it opens only a file in that project.
- A `scope` or `project` that names no project the user can read is refused with the closest names (`scope_not_found`, or `scope_ambiguous` when several share the name); it is never widened to everything. Retry with a name or ID `list_projects` printed.
- The personal project is a project like any other: `list_projects` reports it with type `personal`. Pass its name or ID as the scope.

## Citing sources

Always cite sources when presenting information from the knowledge base:
- Cite with the readable reference a result prints, in parentheses: `(tax-memo.docx › p. 7)`, `(#deals › message 2026-03-04 14:02 UTC)`, `(ENG-12)`, `(Page Title › Section)`; to people, link it when it has a url
- In writes, cite with that readable reference as `reference` (or a read's `source_ref`), and only this conversation (`conversation` with `meta.excerpt`), files the user uploaded, or sources Knowledge already cites; anything else is refused `citation_not_citeable`
- Include the page title so the user can find it
- `research` answers cite the same way, with a Sources list per document -- keep the references when you present them
- An excerpt marked `(paraphrase)`, `(human asserted)`, or `(unverified)` is not a checked quote; open the source before relying on it
- For claims that need verification, use `provenance` to show supporting/contradicting sources
- When writing pages, see [reference/writing-pages.md](reference/writing-pages.md) for citation records

## Page references

`page` accepts any of:
- Title (e.g., `"API Design"`)
- Slug (e.g., `api-design`)
- UUID
- Wiki citation key from a read (e.g., `wiki:8f3a0c21`)

## Advanced workflows

- **Deep research**: See [reference/research-workflow.md](reference/research-workflow.md)
- **Cards or originals**: read cards first; compute from the original files when the answer needs math, exact values or a disputed source. See [reference/research-workflow.md](reference/research-workflow.md#cards-or-the-original-files)
- **Writing pages**: See [reference/writing-pages.md](reference/writing-pages.md)
- **Auditing quality**: See [reference/auditing.md](reference/auditing.md)
- **Understanding provenance**: See [reference/provenance.md](reference/provenance.md)

## Examples

- [examples/answer-org-question.md](examples/answer-org-question.md) -- Answering "Who owns onboarding?"
- [examples/research-topic.md](examples/research-topic.md) -- Deep research on a topic
- [examples/write-decision-page.md](examples/write-decision-page.md) -- Creating a decision record
