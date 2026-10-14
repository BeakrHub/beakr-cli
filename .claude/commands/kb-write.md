Create or update a knowledge base page about: $ARGUMENTS

Follow this procedure:

1. **Check for duplicates.** Search with `knowledge_base` command `sections` and `{"query": ...}` across all accessible projects (also `grep` for exact names). If a page already exists on this topic, read it with command `cat` and update it rather than creating a new one.

2. **Pick the project.** Call `list_projects` and pass the target project's name or ID as `scope` on every call below. The personal project is listed with type `personal`. Never guess a scope.

3. **Find the right parent.** For a new page, call command `suggest_parent` with `{"title": ..., "content": <your draft>}`, or walk the hierarchy with command `ls` and `{"parent": ...}`. If unsure, ask the user.

4. **Choose the page type.** Run command `ontology` for the active types. Common ones: `topic` (most common), `person`, `organization`, `decision`, `meeting`, `overview` (index/section pages), `research_note` (ephemeral analysis).

5. **Write the content** following these conventions:
   - The page is `sections`: an ordered list of `{"title": ..., "body": ...}` objects. Beakr writes the `<!-- sec:ID -->` markers and IDs; do not author markers or pass `content`.
   - Put an inline citation token `{{key}}` immediately after every factual claim, table value, date, title, and relationship.
   - Give each token a record in that section's `citations`: `{"key": ..., "source_ref": ..., "stance": ...}` using the `source_ref` from a `knowledge_base` read for sources Beakr already has. For this conversation or a note, use a stable key with `source_type` `conversation`, `agent_note`, or `user_note`, plus `source_title` and `meta.excerpt`.
   - Set `stance` to `support`, `qualifies`, or `contradicts`.
   - Add `event_start`, `event_end`, and `date_precision` (day, month, quarter, year, approx) to dated decisions, meetings, milestones, launches, incidents, and other timeline-worthy sections.
   - Use `[[Page Title]]` for cross-references (check targets exist) and `[[Display Text|Page Title]]` when the text differs.
   - Include a "Related Pages" section at the bottom.
   - Write for a reader with no prior context: name WHO made decisions, WHEN things happened, WHY.
   - Use tables for structured data and code blocks for config/commands.

6. **Stage the proposal** with `knowledge_base_write`:
   - New page: action `new` with `title`, `page_type`, `summary` (one line on what the page is about), `parent`, `sections`, and `rationale`.
   - Existing page: action `edit_section` with `page`, `section_id`, `new_section_body`, `citations`, `edit_note`, and `rationale`. Read the page first; the new body replaces the whole section.

7. **Review.** Show the result with `show_proposal`. Call `accept_proposal` only when the user explicitly asks to apply it.

Do NOT create stub pages. Every page should have substantive content.
