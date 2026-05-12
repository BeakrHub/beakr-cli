Search the Beakr knowledge base for information about: $ARGUMENTS

Follow this procedure:

1. **Discover scope.** Call `list_projects` to see available projects. If the user mentioned a specific project, use that project ID. Otherwise run an unscoped search across all accessible knowledge.

2. **Search broadly.** Run `kb_search` with the query. If the user likely means one or more specific projects, run additional project-scoped `kb_search` calls with those project IDs.

3. **Read the top hits.** For the most relevant results (up to 3-5 pages), call `kb_cat` to read the full content. Do these reads in parallel.

4. **Follow links.** If the pages contain `[[links]]` to other relevant pages, read those too. Use `kb_links` (backlinks) to find pages that reference a key page.

5. **Check provenance if needed.** If the user needs to know where information came from, use `kb_sources` or `kb_provenance` on the relevant pages.

6. **Synthesize and cite.** Present findings with page titles and slugs so the user can navigate to them. Quote specific sections when relevant. Note any contradictions found in provenance (stance: contradicts/qualifies).

Do NOT just return search results -- read the actual pages and answer the question.
