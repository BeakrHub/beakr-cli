Audit the Beakr knowledge base for quality issues: $ARGUMENTS

If no specific focus is given, run a general health check. Pass a project's name or ID as `scope` when the user named one. Follow this procedure:

1. **Get the full picture.** Call `wiki_stats` for counts and `knowledge_base` command `ls` (raise `limit` in `arguments` for a full list). Use command `ontology` to see which page types are in use.

2. **Run diagnostics.** Call `knowledge_base` command `diagnostics`. It reports broken links, orphan pages, and disputes.

3. **Check the graph.** Call `wiki_graph` for the most-connected pages. Look for:
   - **Orphan pages** -- pages with no parent and no incoming links
   - **Dead ends** -- pages with no outgoing links (isolated knowledge)
   - **Missing index pages** -- overview pages that should tie a section together

4. **Spot structural issues.** From `ls` results, look for:
   - Duplicate or near-duplicate page titles
   - Deeply nested hierarchies (more than 3 levels is usually too deep)
   - Pages without a parent (command `suggest_parent` with `{"page": ...}` finds a home)
   - Pages that look like stubs

5. **Sample content quality.** Read 5-10 pages with command `cat` (start with `{"page": ..., "outline": true}`), prioritizing:
   - Heavily edited pages (command `log`) -- is content coherent?
   - Recently created pages (sort `ls` by `created_at`) -- stubs or unreviewed?
   - Overview/index pages -- are they up to date with their children?

6. **Check provenance.** For key pages, call command `provenance` and look for:
   - Sections with no citations (unsourced claims)
   - Sections with `contradicts` citations (disputed content)
   - Excerpts marked (paraphrase) or (unverified), or sources that changed since cited

7. **Report findings** organized as:
   - **Critical** -- broken links, orphaned important pages, contradicted content
   - **Moderate** -- missing sections, stale pages, structural issues
   - **Minor** -- style inconsistencies, missing Related Pages sections
   - **Recommendations** -- specific pages to create, merge, or reorganize

Offer to stage fixes with `knowledge_base_write` (actions `edit_section`, `merge` for duplicates, `mv`, `new`) if the user approves. Never call `accept_proposal` unless the user explicitly asks.
