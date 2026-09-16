# Writing Knowledge Base Pages

Use this workflow when the user wants to create or update a page. Every change goes through `knowledge_base_write`, which stages a **proposal**; nothing is written until the user asks you to `accept_proposal`.

## Before writing

1. **Check for duplicates.** Search with `knowledge_base` command `sections` (and `grep` for exact names). If a page exists on this topic, read it with `cat` and update it instead of creating a new one.

2. **Pick the project.** Use `list_projects` and pass the project's name or ID as `scope`. Never guess: a page title or filename is not a scope.

3. **Find the right parent.** Use `suggest_parent` with `title` (and your draft as `content`) for a page you are about to create, or `ls {parent}` to walk the hierarchy. If unsure, ask the user.

4. **Choose the page type.** Run `ontology` to see the org's active types. Common ones:

| Type | When to use |
|------|-------------|
| `topic` | Most common. Concepts, systems, processes. |
| `person` | People profiles. |
| `organization` | Companies, teams, departments. |
| `decision` | Decisions with rationale and date. |
| `meeting` | Meeting notes with attendees and outcomes. |
| `overview` | Index/section pages that tie a section together. |
| `research_note` | Ephemeral analysis, not canonical knowledge. |

## Updating an existing page (preferred)

Use `edit_section` to change one section without touching the rest of the page. Read the page first (`cat {page, outline: true}`) to get section IDs. `new_section_body` replaces that section's entire body.

```
knowledge_base_write(
  action="edit_section",
  scope="Platform",
  arguments={
    "page": "API Architecture",
    "section_id": "sec_auth",
    "new_section_body": "Internal services authenticate with mTLS {{conversation:2026-09-16-auth}}.",
    "citations": [
      {
        "key": "conversation:2026-09-16-auth",
        "source_type": "conversation",
        "source_title": "Claude Code session on service auth",
        "stance": "support",
        "meta": {"excerpt": "We agreed to move internal services to mTLS."}
      }
    ],
    "edit_note": "Record the move to mTLS",
    "rationale": "Decision made in this session; the page still described JWT."
  }
)
```

To add a section, pass a new `section_id` plus `section_title` (and `after` to position it). For several changes at once, use `edit` with `patches`.

## Creating a page

Use `new` with `title`, `page_type`, `summary`, `parent`, `rationale`, and `sections`.

`sections` is the page: an ordered list of `{title, body}` objects. **Beakr writes the `<!-- sec:ID -->` markers and section IDs for you** -- do not author markers and do not pass `content`.

Each section can also carry:
- `citations`: records for the inline tokens in its body (see below)
- `event_start`, `event_end`, `date_precision` (day, month, quarter, year, approx) for dated decisions, meetings, launches, incidents, and other timeline-worthy sections. `date_precision` is required whenever `event_start` is set.

## Citations

Put an inline token immediately after every factual claim, table value, date, title, and relationship: `{{key}}`. Then give each token a citation record in that section's `citations`:

- **Re-citing a source Beakr already has:** use the `source_ref` from a `knowledge_base` read (`sources`, `provenance`, `cat`, `sections`) with the token key shown there, e.g. `{"key": "external_item:...-section-3", "source_ref": "beakr-source:v1:...", "stance": "support"}`.
- **A source cited for the first time** (this conversation, a note): give a stable `key` with `source_type` `conversation`, `agent_note`, or `user_note`, a `source_title`, and `meta.excerpt` (or `meta.content` / `meta.text`).

Set `stance` to `support`, `qualifies`, or `contradicts`. It defaults to `support`, and that default is itself a claim about the evidence. A token with no citation record is stored as a bare pointer that can never be verified.

## Content conventions

- Use `[[Page Title]]` to link other pages; `[[Display Text|Page Title]]` when the text differs. Check targets exist first.
- `summary` is a one-line description of what the page is about, never a note about your edit (that is `edit_note`).
- Include a "Related Pages" section with links to connected pages.
- Be specific: name WHO made decisions, WHEN things happened, WHY.
- Use tables for structured data, code blocks for config/commands.
- Do not create stub pages -- every page should have substantive content.

## After proposing

Show the user what you staged with `show_proposal`. Call `accept_proposal` only when the user explicitly asks to apply that proposal. `knowledge_base` command `proposals` lists what is still pending; a proposed page does not exist in `ls`, `cat`, or search until accepted.
