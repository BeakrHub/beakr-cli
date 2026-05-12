Create or update a knowledge base page about: $ARGUMENTS

Follow this procedure:

1. **Check for duplicates.** Search the knowledge base first with `kb_search` across all accessible projects. If a page already exists on this topic, read it with `kb_cat` and decide whether to update it with a proposal or create a new page.

2. **Find the right parent.** Use `list_projects` to pick the target project, then `kb_ls` with that project to see the page hierarchy. Place the new page under the most logical parent (e.g., a topic page under an overview section). If unsure, ask the user.

3. **Choose the right page type.** Pick from: `topic` (most common), `person`, `organization`, `decision`, `meeting`, `overview` (index/section pages), `research_note` (ephemeral analysis).

4. **Write the content** following these conventions:
   - Use `<!-- sec:OPAQUE_ID -->` markers before each major section (e.g., `<!-- sec:auth_overview -->`). These are required for section-level provenance tracking.
   - Cite every factual claim, table row/value, date, title, and relationship with inline citation tokens like `{{rag:abc123}}` or `{{!rag:abc123}}` immediately after the supported claim or value.
   - Pass the same citation keys in the proposal `sections` metadata with `stance` set to `support`, `qualifies`, or `contradicts`.
   - For sources not already in Beakr, use a stable key and source metadata. Current-session citations should use `source_type` `conversation`, `agent_note`, or `user_note`, plus `source_title` and `meta.excerpt`, `meta.content`, or `meta.text`.
   - Add `event_start`, `event_end`, and `date_precision` in section metadata for dated decisions, meetings, milestones, launches, incidents, and other timeline-worthy sections.
   - Use `[[Page Title]]` syntax for cross-references to other knowledge base pages. Check that target pages exist first.
   - Use `[[Display Text|slug]]` for links where the display text differs from the page title.
   - Include a "Related Pages" section at the bottom with links to connected pages.
   - Write for a reader who has no prior context -- be specific, not vague.
   - Attribution matters: name WHO made decisions, WHEN things happened.
   - Use tables for structured data. Use code blocks for config/commands.

5. **Scope the page correctly.** Always create proposals with exactly one scope: `project_id` for shared/team knowledge or `personal=true` for the user's personal project.

6. **Create the page** with `kb_propose_create`, providing title, content, page_type, summary, parent, project/personal scope, and section metadata. Wait for explicit user approval before accepting.

7. **Verify.** Read it back with `kb_cat` to confirm it rendered correctly.

Do NOT create stub pages. Every page should have substantive content.
