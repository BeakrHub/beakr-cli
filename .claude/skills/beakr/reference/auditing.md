# Knowledge Base Audit

Use this workflow to check knowledge base quality and find issues.

## Procedure

### 1. Get the full picture

Call `wiki_stats` for counts and `knowledge_base` command `ls` to list pages (raise `limit` for a full list). Use `ontology` to see which page types the org uses.

### 2. Run diagnostics

Call `knowledge_base` command `diagnostics`. It reports broken links, orphan pages, and disputes directly.

### 3. Check the graph

Call `wiki_graph` for the most-connected pages. Look for:
- **Orphan pages** -- no parent and no incoming links
- **Dead ends** -- pages with no outgoing links (isolated knowledge)
- **Missing index pages** -- overview pages that should tie sections together

Use `links {page}` or `references {page}` to inspect a specific page's neighborhood.

### 4. Spot structural issues

From `ls` results, look for:
- Duplicate or near-duplicate page titles (fix with `merge`, not `archive`)
- Deeply nested hierarchies (more than 3 levels is usually too deep)
- Pages without a parent (use `suggest_parent {page}` to find a home)
- Pages that look like stubs

### 5. Sample content quality

Read 5-10 pages with `cat {page, outline: true}` first, then the sections that matter. Prioritize:
- Heavily edited pages (`log {page}`) -- is the content coherent?
- Recently created pages -- are they stubs or unreviewed?
- Overview/index pages -- are they up to date with their children?

### 6. Check provenance

For key pages, call `provenance {page}` and look for:
- Sections with no citations (unsourced claims)
- Sections with `contradicts` citations (disputed content)
- Excerpts marked `(paraphrase)` or `(unverified)`, or sources marked "source changed since cited"

### 7. Report findings

Organize by severity:
- **Critical** -- broken links, orphaned important pages, contradicted content
- **Moderate** -- missing sections, stale pages, structural issues
- **Minor** -- style inconsistencies, missing Related Pages sections
- **Recommendations** -- specific pages to create, merge, or reorganize

Offer to stage fixes with `knowledge_base_write` (`edit_section`, `merge`, `mv`, `new`) if the user approves.
