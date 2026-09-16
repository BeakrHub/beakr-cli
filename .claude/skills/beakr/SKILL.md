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
- Never auto-write. Always propose, always wait for explicit OK.
- One capture offer per significant thread — don't pepper the user.
- Cite the current session as a `conversation` source with `meta.excerpt` so it is traceable.

## Tools

| Tool | When to use |
|------|-------------|
| `research` | First choice for broad questions. Searches the knowledge base, documents, and connected services (Slack, Gmail, Calendar, Jira...) in one call. Returns a cited answer. |
| `knowledge_base` | Every read over the wiki, chosen by `command`: `sections`, `grep`, `ls`, `cat`, `hover`, `links`, `references`, `timeline`, `provenance`, `blame`, `sources`, `log`, `diff`, `show`, `ontology`, `suggest_parent`, `diagnostics`, `completions`, `proposals`. |
| `knowledge_base_write` | Every change, chosen by `action`: `edit_section`, `new`, `edit`, `find_replace`, `mv`, `archive`, `merge`, `copy`, `page_type`. Always stages a proposal. |
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

## Quick decision tree

```
User asks a question about their org
  → research(query=...)

User asks about a specific fact, decision, or page
  → knowledge_base sections {query}, then cat {page, section_id} on the best hit

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
- **Writing pages**: See [reference/writing-pages.md](reference/writing-pages.md)
- **Auditing quality**: See [reference/auditing.md](reference/auditing.md)
- **Understanding provenance**: See [reference/provenance.md](reference/provenance.md)

## Examples

- [examples/answer-org-question.md](examples/answer-org-question.md) -- Answering "Who owns onboarding?"
- [examples/research-topic.md](examples/research-topic.md) -- Deep research on a topic
- [examples/write-decision-page.md](examples/write-decision-page.md) -- Creating a decision record
