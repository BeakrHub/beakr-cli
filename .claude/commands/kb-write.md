Create or update a knowledge base page about: $ARGUMENTS

Follow this procedure:

1. **Check for duplicates.** Search the knowledge base first with `kb_search` across all spaces. If a page already exists on this topic, read it with `kb_cat` and decide whether to update it (via `kb_edit`) or create a new page.

2. **Find the right parent.** Use `kb_ls` with the target space to see the page hierarchy. Place the new page under the most logical parent (e.g., a topic page under an overview section). If unsure, ask the user.

3. **Choose the right page type.** Pick from: `topic` (most common), `person`, `organization`, `decision`, `meeting`, `overview` (index/section pages), `research_note` (ephemeral analysis).

4. **Write the content** following these conventions:
   - Use `<!-- sec:OPAQUE_ID -->` markers before each major section (e.g., `<!-- sec:auth_overview -->`). These are required for section-level provenance tracking.
   - Use `[[Page Title]]` syntax for cross-references to other knowledge base pages. Check that target pages exist first.
   - Use `[[Display Text|slug]]` for links where the display text differs from the page title.
   - Include a "Related Pages" section at the bottom with links to connected pages.
   - Write for a reader who has no prior context -- be specific, not vague.
   - Attribution matters: name WHO made decisions, WHEN things happened.
   - Use tables for structured data. Use code blocks for config/commands.

5. **Scope the page correctly.** Always pass the `space` parameter when creating. If the page belongs to a project, pass `project` too.

6. **Create the page** with `kb_create`, providing title, content, page_type, summary, parent_page, and space/project.

7. **Verify.** Read it back with `kb_cat` to confirm it rendered correctly.

Do NOT create stub pages. Every page should have substantive content.
