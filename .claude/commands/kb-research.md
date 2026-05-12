Research the following topic using the Beakr knowledge base and provide a thorough analysis: $ARGUMENTS

Follow this procedure:

1. **Discover the landscape.** Call `list_projects` to understand the available project structure. Use unscoped `kb_stats` for the full accessible knowledge base and project-scoped `kb_stats` when a project is clearly relevant.

2. **Broad search.** Run `kb_search` with multiple query variations. Use unscoped search first, then project-scoped searches for likely relevant projects. Use different phrasings -- the knowledge base uses hybrid semantic + keyword search, so both precise terms and natural language queries work.

3. **Deep read.** Read the top 5-10 most relevant pages with `kb_cat` (in parallel). Pay attention to:
   - Section content and structure
   - `[[links]]` to follow
   - Page type (decision pages have different weight than research_notes)

4. **Trace provenance.** For key claims, use `kb_provenance` to see which sources support, contradict, or qualify each section. Use `kb_sources` to understand what raw documents fed each page.

5. **Follow the graph.** Use `kb_links` (backlinks) on central pages to discover related pages you may have missed. Use `kb_log` to see revision history and understand how knowledge evolved.

6. **Check for conflicts.** Look for provenance entries with stance `contradicts` or `qualifies`. These are the most valuable findings -- they show where the evidence disagrees.

7. **Synthesize.** Write up findings organized by theme, not by page. Include:
   - Key findings with page citations (title + slug)
   - Areas of agreement across sources
   - Contradictions or open questions
   - Gaps -- what the knowledge base does NOT cover that it should
   - Temporal context -- when information was written, whether it may be stale

8. **Recommend.** If the research reveals gaps or stale pages, suggest specific knowledge base updates. Offer to create or edit pages if appropriate.
