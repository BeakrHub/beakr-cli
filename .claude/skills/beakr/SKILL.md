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
- **Not already in Beakr**: check with `kb_search` first
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
- Cite the current session as `conversation:<short-id>` with `meta.excerpt` so the source is traceable.

## Core tools

| Tool | When to use |
|------|-------------|
| `research` | First choice for broad questions. Searches knowledge base, docs, Slack, Gmail, Calendar, Jira in one call. Returns cited answers. |
| `kb_search` | Find specific pages by semantic or keyword search. |
| `kb_cat` | Read a page's full content by slug, title, or UUID. |
| `kb_ls` | Browse pages. Filter by type, parent, or scope. |
| `kb_timeline` | Find decisions, meetings, and events by date range. |
| `kb_graph` | Understand how pages connect. Shows top linked pages. |

## Quick decision tree

```
User asks a question about their org
  → Use `research` (it searches everything)

User asks about a specific page
  → Use `kb_cat` with the page title/slug

User wants to browse or list pages
  → Use `kb_ls` (optionally with --type, --roots, --parent)

User wants to know what happened on a date
  → Use `kb_timeline` with date range

User wants to verify sources for a claim
  → Use `kb_provenance` or `kb_blame` on the relevant page

User wants to create/update a page
  → See [reference/writing-pages.md](reference/writing-pages.md)

User wants to audit knowledge base quality
  → See [reference/auditing.md](reference/auditing.md)
```

## Scoping

All tools accept an optional `project` parameter:
- Omit it to search across the entire organization
- Pass a project ID to scope results (use `list_projects` to discover IDs)
- Pass `personal_only=true` to limit to the user's personal project

## Citing sources

Always cite sources when presenting information from the knowledge base:
- Include the page title so the user can find it
- For `research` results, the response includes numbered citations -- present them
- For claims that need verification, use `kb_provenance` to show supporting/contradicting sources
- When writing wiki pages, put inline citation tokens like `{{rag:abc123}}` or
  `{{!rag:abc123}}` immediately after every factual claim, table row/value,
  date, title, and relationship. Also include the same keys in
  `sections[].citations` with `support`, `qualifies`, or `contradicts` stance.
  Add `event_start`, `event_end`, and `date_precision` for dated decisions,
  meetings, milestones, and other timeline-worthy sections. For current-session
  or other not-yet-indexed sources, cite a stable key with `source_type`
  `conversation`, `agent_note`, or `user_note`, plus `source_title` and
  `meta.excerpt`, `meta.content`, or `meta.text`.

## Page references

Pages can be referenced by any of:
- UUID (exact match)
- Slug (exact match, e.g., `api-design`)
- Title (search, e.g., `"API Design"`)

## Advanced workflows

- **Deep research**: See [reference/research-workflow.md](reference/research-workflow.md)
- **Writing pages**: See [reference/writing-pages.md](reference/writing-pages.md)
- **Auditing quality**: See [reference/auditing.md](reference/auditing.md)
- **Understanding provenance**: See [reference/provenance.md](reference/provenance.md)

## Examples

- [examples/answer-org-question.md](examples/answer-org-question.md) -- Answering "Who owns onboarding?"
- [examples/research-topic.md](examples/research-topic.md) -- Deep research on a topic
- [examples/write-decision-page.md](examples/write-decision-page.md) -- Creating a decision record
