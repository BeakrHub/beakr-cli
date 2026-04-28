# Writing Knowledge Base Pages

Use this workflow when the user wants to create or update a page.

## Before writing

1. **Check for duplicates.** Search with `kb_search` across all spaces. If a page exists on this topic, read it with `kb_cat` and decide whether to update or create new.

2. **Find the right parent.** Use `kb_ls` to see the page hierarchy. Place the new page under the most logical parent. If unsure, ask the user.

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
