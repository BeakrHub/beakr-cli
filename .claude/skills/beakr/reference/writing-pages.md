# Writing Knowledge Base Pages

Use this workflow when the user wants to create or update a page.

## Before writing

1. **Check for duplicates.** Search with `kb_search` across all accessible projects. If a page exists on this topic, read it with `kb_cat` and decide whether to update or create new.

2. **Find the right parent.** Use `list_projects` to choose the target project, then use `kb_ls` in that project to see the page hierarchy. Place the new page under the most logical parent. If unsure, ask the user.

3. **Choose the right page type:**

| Type | When to use |
|------|-------------|
| `topic` | Most common. Concepts, systems, processes. |
| `person` | People profiles. |
| `organization` | Companies, teams, departments. |
| `decision` | Decisions with rationale and date. |
| `meeting` | Meeting notes with attendees and outcomes. |
| `overview` | Index/section pages that tie a section together. |
| `research_note` | Ephemeral analysis, not canonical knowledge. |

## Content conventions

### Section markers (required)
Add `<!-- sec:OPAQUE_ID -->` before each major section. These enable section-level provenance tracking.

```markdown
<!-- sec:auth_overview -->
## Authentication Overview

Our authentication uses JWT tokens issued by Clerk...

<!-- sec:auth_flow -->
## Authentication Flow

1. User submits credentials to Clerk...
```

### Inline citations
Every factual claim, table row/value, date, title, and relationship should carry
an inline citation token immediately after the supported claim or value. Tokens
must look like `{{source_type:source_id}}` or `{{!source_type:source_id}}`.

```markdown
Revenue was $12.4M {{rag:abc123}} and net retention was 118% {{gdrive:def456}}.

| Company | Revenue |
| --- | --- |
| Acme | $12.4M {{rag:abc123}} |
```

Use the same citation keys in proposal section metadata so provenance can roll
up support, qualification, and contradiction. Include event metadata for dated
decisions, meetings, milestones, incidents, launches, and other timeline-worthy
sections:

```json
[
  {
    "id": "financials",
    "title": "Financials",
    "event_start": "2026-04-01",
    "date_precision": "day",
    "citations": [
      { "key": "rag:abc123", "stance": "support" },
      { "key": "gdrive:def456", "stance": "qualifies" }
    ]
  }
]
```

For citations that are not already wiki/page sources, provide source metadata in
the same citation object. Current-session citations should use `conversation`,
`agent_note`, or `user_note` and include `source_title` plus `meta.excerpt`,
`meta.content`, or `meta.text`.

```json
{
  "key": "agent_note:launch-risk",
  "source_type": "agent_note",
  "source_title": "Agent synthesis from current conversation",
  "stance": "support",
  "meta": {
    "excerpt": "The team decided to keep the launch date but add a rollback gate."
  }
}
```

### Cross-references
Use `[[Page Title]]` to link to other KB pages. Verify targets exist first with `kb_search`.

Use `[[Display Text|slug]]` when display text differs from the page title.

### Structure
- Include a "Related Pages" section at the bottom with links to connected pages
- Be specific: name WHO made decisions, WHEN things happened, WHY
- Use tables for structured data, code blocks for config/commands
- Do not create stub pages -- every page should have substantive content

## After writing

Read the page back with `kb_cat` to verify it rendered correctly.
