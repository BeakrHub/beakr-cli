# Deep Research Workflow

Use this workflow when the user needs a thorough, multi-source analysis of a topic.

## Procedure

### 1. Discover the landscape

Call `list_projects` to understand the org structure, and `wiki_stats` (with `project` when one is clearly relevant) to know where knowledge lives.

### 2. Ask the researcher

Call `research` with the question. It searches the wiki, documents, and connected services and returns a cited answer. Treat it as a starting map, not the final word.

### 3. Broad wiki search

Run `knowledge_base` command `sections` with 2-3 phrasings in parallel -- it is semantic, so natural language works. Use `grep` for exact names, IDs, or phrases; if a literal `grep` misses, the wording may differ, so retry with `sections` rather than concluding the knowledge base lacks it.

### 4. Deep read

Read the top hits with `cat`, narrowest first: `section_id` from the hit, or `outline: true` to see a page's sections. Read whole pages only when you need material the hit did not point at. Pay attention to:
- `[[links]]`, Path, Children, and "Linked from" in the output -- follow those edges
- Page type (`decision` pages carry different weight than `research_note`)

### 5. Trace provenance

For key claims, use `provenance {page}` to see which sources support, contradict, or qualify each section, and `sources {page}` to see what fed the page.

### 6. Follow the graph and history

Use `links {page}` or `references {page}` on central pages to find related pages you missed. Use `timeline {timeline_query}` for how events unfolded, and `log {page}` only when the question is about change over time.

### 7. Check for conflicts

Look for citations with stance `contradicts` or `qualifies`. These are the most valuable findings -- they show where evidence disagrees.

### 8. Synthesize

Write up findings organized by theme, not by page. Include:
- Key findings with page citations (title)
- Areas of agreement across sources
- Contradictions or open questions
- Gaps -- what the knowledge base does NOT cover
- Temporal context -- when information was written, whether it may be stale

### 9. Recommend

If research reveals gaps or stale pages, suggest specific knowledge base updates.
