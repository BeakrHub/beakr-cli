# Deep Research Workflow

Use this workflow when the user needs a thorough, multi-source analysis of a topic.

## Procedure

### 1. Discover the landscape

Call `list_projects` to understand the org structure. Then `kb_stats` on relevant projects to know where knowledge lives.

### 2. Broad search

Run `kb_search` with multiple query variations in parallel. The knowledge base uses hybrid semantic + keyword search, so both precise terms and natural language work. Try 2-3 phrasings.

### 3. Deep read

Read the top 5-10 most relevant pages with `kb_cat` (in parallel). Pay attention to:
- Section content and structure
- `[[links]]` to follow for related pages
- Page type (`decision` pages carry different weight than `research_note`)

### 4. Trace provenance

For key claims, use `kb_provenance` to see which sources support, contradict, or qualify each section. Use `kb_sources` to understand what raw documents fed each page.

### 5. Follow the graph

Use `kb_links` (backlinks) on central pages to discover related pages you may have missed. Use `kb_log` to see revision history and understand how knowledge evolved.

### 6. Check for conflicts

Look for provenance entries with stance `contradicts` or `qualifies`. These are the most valuable findings -- they show where evidence disagrees.

### 7. Synthesize

Write up findings organized by theme, not by page. Include:
- Key findings with page citations (title + slug)
- Areas of agreement across sources
- Contradictions or open questions
- Gaps -- what the knowledge base does NOT cover
- Temporal context -- when information was written, whether it may be stale

### 8. Recommend

If research reveals gaps or stale pages, suggest specific knowledge base updates.
