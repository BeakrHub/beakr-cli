Search the Beakr knowledge base for information about: $ARGUMENTS

Follow this procedure:

1. **Discover scope.** If the user mentioned a specific project, call `list_projects` and pass that project's name or ID as `scope`. Otherwise search everything the user can see (omit `scope`).

2. **Search broadly.** Run `knowledge_base` with command `sections` and `arguments: {"query": ...}`; it is semantic, so try one or two phrasings in parallel. For exact names, IDs, or phrases, also run command `grep` with `{"query": ...}`.

3. **Read the top hits.** For the most relevant results (up to 3-5), call `knowledge_base` command `cat` with `{"page": ..., "section_id": ...}` from the hit. Read the whole page only when you need material outside that section. Do these reads in parallel.

4. **Follow links.** If the pages contain `[[links]]` to other relevant pages, read those too. Use command `links` with `{"page": ...}` to find pages that reference a key page.

5. **Check provenance if needed.** If the user needs to know where information came from, use command `sources` or `provenance` with `{"page": ...}`.

6. **Synthesize and cite.** Present findings with page titles so the user can navigate to them. Quote specific sections when relevant. Note any contradictions found in provenance (stance: contradicts/qualifies) and any excerpt marked (paraphrase) or (unverified).

Do NOT just return search results -- read the actual pages and answer the question.
