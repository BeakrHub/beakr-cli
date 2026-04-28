Audit the Beakr knowledge base for quality issues: $ARGUMENTS

If no specific focus is given, run a general health check. Follow this procedure:

1. **Get the full picture.** Call `kb_ls --all` (or `kb_ls` per space) to get every page. Call `kb_stats` per space for counts.

2. **Check the graph.** Call `kb_graph` to get all nodes and edges. Look for:
   - **Orphan pages** -- pages with no parent and no incoming links
   - **Broken links** -- `[[links]]` that don't resolve to any page
   - **Dead ends** -- pages with no outgoing links (isolated knowledge)
   - **Missing index pages** -- overview pages that should tie a section together

3. **Spot structural issues.** From `kb_ls` results, look for:
   - Duplicate or near-duplicate page titles
   - Pages with slug collisions (same slug across different scopes)
   - Deeply nested hierarchies (more than 3 levels is usually too deep)
   - Pages without a parent (should they be under a section?)
   - Pages with revision 1 that may be stubs

4. **Sample content quality.** Read 5-10 pages with `kb_cat`, prioritizing:
   - Pages with high revision counts (heavily edited -- is content coherent?)
   - Pages with revision 1 (potentially stubs or unreviewed)
   - Overview/index pages (are they up to date with their children?)

5. **Check provenance.** For key pages, call `kb_provenance` and look for:
   - Sections with no citations (unsourced claims)
   - Sections with only `contradicts` citations (disputed content)
   - Stale provenance (old sources, no recent updates)

6. **Report findings** organized as:
   - **Critical** -- broken links, orphaned important pages, contradicted content
   - **Moderate** -- missing sections, stale pages, structural issues
   - **Minor** -- style inconsistencies, missing Related Pages sections
   - **Recommendations** -- specific pages to create, merge, or reorganize

Offer to fix issues directly using `kb_edit`, `kb_move`, or `kb_create` if the user approves.
