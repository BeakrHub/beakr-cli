Research the following topic using the Beakr knowledge base and provide a thorough analysis: $ARGUMENTS

Follow this procedure:

1. **Discover the landscape.** Call `list_projects` to understand the available projects. Use `wiki_stats` for the whole accessible knowledge base, and with `project` when a project is clearly relevant.

2. **Ask the researcher.** Call `research` with the topic as `query`. It searches the wiki, documents, and connected services and returns a cited answer. Use it as a starting map.

3. **Broad search.** Run `knowledge_base` command `sections` with `{"query": ...}` using 2-3 phrasings in parallel. Use command `grep` with `{"query": ...}` for exact names or IDs. If a literal grep misses, retry with `sections` before concluding the topic is not covered.

4. **Deep read.** Read the top 5-10 most relevant hits with command `cat`, using `{"page": ..., "section_id": ...}` when a hit points at a section and `{"page": ..., "outline": true}` to see a page's structure. Read in parallel. Pay attention to:
   - Section content and structure
   - `[[links]]`, Children, and "Linked from" to follow
   - Page type (decision pages have different weight than research_notes)

5. **Trace provenance.** For key claims, use command `provenance` with `{"page": ...}` to see which sources support, contradict, or qualify each section. Use command `sources` to see what raw documents fed each page.

6. **Follow the graph.** Use command `links` or `references` with `{"page": ...}` on central pages to discover related pages you may have missed. Use command `timeline` with `{"timeline_query": ...}` to see how events unfolded, and `log` only when the question is about change over time.

7. **Check for conflicts.** Look for citations with stance `contradicts` or `qualifies`. These are the most valuable findings -- they show where the evidence disagrees.

8. **Synthesize.** Write up findings organized by theme, not by page. Include:
   - Key findings with page citations (title)
   - Areas of agreement across sources
   - Contradictions or open questions
   - Gaps -- what the knowledge base does NOT cover that it should
   - Temporal context -- when information was written, whether it may be stale

9. **Recommend.** If the research reveals gaps or stale pages, suggest specific knowledge base updates. Offer to propose pages or edits if appropriate.
