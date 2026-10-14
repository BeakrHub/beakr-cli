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
| `knowledge_base_write` | Every change, chosen by `action`: `edit_section`, `add_fact`, `add_connection`, `add_finding`, `edit_rows`, `new`, `edit`, `find_replace`, `mv`, `archive`, `merge`, `copy`, `page_type`. Always stages a proposal. On a project-map card use the three `add_*` actions to add one row or one dated finding, and `edit_rows` to change, merge or delete rows. |
| `read_file` | The original document a citation points at. Pass the `path › place` from a Sources line (`deals/acme/memo.pdf › p. 7`), the whole `{{artifact:...}}` token, or the file id. It reads files and canvases only: for a connected item (Slack, email, calendar, Jira) use its excerpt and open its url. Long files come back in pieces (`offset`). |
| `research` | When your client exposes it: one cited answer across the knowledge base, documents and connected services (Slack, Gmail, Calendar, Jira...). |
| `show_proposal` / `accept_proposal` / `dismiss_proposal` | Review a staged proposal; accept only when the user explicitly asks. |
| `list_projects` | Discover projects (including the personal one) to use as `scope`. |
| `get_profile`, `wiki_stats`, `wiki_graph` | Org/project profile, counts, and the most-connected pages. |
| `beakr_version` / `update_beakr` | Check for a newer Beakr release; update only when the user asks. |

Both `knowledge_base` tools take a `command`/`action`, a flat `arguments` object, and an optional `scope`:

```
knowledge_base(command="sections", arguments={"query": "who owns billing"})
knowledge_base(command="cat", arguments={"page": "Billing", "section_id": "sec_owner"})
knowledge_base_write(action="edit_section", scope="Team Wiki", arguments={...})
```

There is no `search` command. Use `sections` (semantic, start here) or `grep` (literal text).

## Reading evidence

- **Start with `sections`.** Each hit shows the document passage (a verbatim excerpt) that supports it. Quote it directly; open the file only when the value you need is not in the excerpt.
- **The knowledge base replaces a corpus grep.** For "every X", counts and superlatives use `ls` (by `page_type`) or `rows` (one table of every card's facts). Do not extract the documents and grep them; every document, its date and matter are already indexed.
- **Results stay under about 6k tokens.** When `next` appears, page with it rather than re-querying.
- **Exact wording:** `sources {page}` lists every cited passage; `read_file` with a token, a file id or a path opens the original.

## Find, then verify in the file

1. **Find** with the knowledge base (`sections`, `rows`, `cat`). Each Sources line names where a cited document lives and where each passage sits: `- deals/acme/memo.pdf: {{artifact:...}} › p. 7`.
2. **Follow** that `path › place` with `read_file` (`deals/acme/memo.pdf › p. 7`, `book.xlsx › sheet 2, B4:D9`); it returns the text around that place. When the files are on your local disk, open the path there directly.
3. **Verify** every decisive value (an amount, date, count, name) in the file before you rely on it. When the file and a card disagree, the file wins: say so, and cite the file's token.
- **Codex:** print only the result's text, and make one Knowledge call per exec cell.

## Quick decision tree

```
User asks a question about their org, a fact, a decision or a page
  → knowledge_base sections {query}; quote the excerpt, or cat {page, section_id} on the best hit

User asks a broad question and research is available
  → research(query=...)

User asks for every X, a count or a superlative
  → knowledge_base ls {page_type} or rows {table: facts, ...}

User needs the exact wording or a value the excerpt lacks
  → read_file {path: "<path> › <place>"} from the Sources line (or the {{token}})

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

User wants to audit knowledge base quality
  → See [reference/auditing.md](reference/auditing.md)
```

## Scoping

- `knowledge_base` and `knowledge_base_write` take `scope`: a project name or ID. Omit it on reads to search everything the user can see.
- `research`, `wiki_stats`, and `wiki_graph` take `project` (a project ID).
- The personal project is a project like any other: `list_projects` reports it with type `personal`. Pass its name or ID as the scope.

## Citing sources

Always cite sources when presenting information from the knowledge base:
- To people, cite inline with the readable reference a result prints (`tax-memo.docx › p. 7`, `#deals › message 2026-03-04 14:02 UTC`), linked when it has a url, or research's `[n]`
- In writes, cite with the `{{token}}`, a read's `source_ref` or that readable reference, and only this conversation (`conversation` with `meta.excerpt`), files the user uploaded, or sources Knowledge already cites; anything else is refused `citation_not_citeable`
- Include the page title so the user can find it
- For `research` results, the response includes numbered citations with their `{{token}}` keys -- present them
- An excerpt marked `(paraphrase)`, `(human asserted)`, or `(unverified)` is not a checked quote; open the source before relying on it
- For claims that need verification, use `provenance` to show supporting/contradicting sources
- When writing pages, see [reference/writing-pages.md](reference/writing-pages.md) for inline tokens and citation records

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
