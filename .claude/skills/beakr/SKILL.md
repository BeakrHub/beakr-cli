---
name: beakr
description: "Access your organization's knowledge base, research questions with citations, and manage knowledge base pages using Beakr's MCP tools. Use when the user asks about their organization, team, projects, people, decisions, or processes -- or wants to create/update knowledge base content."
---

# Beakr Knowledge Base

Use the Beakr MCP tools to search, read, research, and write to the organization's knowledge base.

## When to use

- User asks about their organization, team, people, decisions, or processes
- User wants to look something up ("What do we know about X?", "Who owns Y?")
- User wants to research a topic across documents and connected services
- User wants to create, update, or audit knowledge base pages

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
- Pass `personal_only=true` to limit to the user's personal space

## Citing sources

Always cite sources when presenting information from the knowledge base:
- Include the page title so the user can find it
- For `research` results, the response includes numbered citations -- present them
- For claims that need verification, use `kb_provenance` to show supporting/contradicting sources

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
