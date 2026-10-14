# Deep Research Workflow

Use this workflow when the user needs a thorough, multi-source analysis of a topic.

## Procedure

### 1. Discover the landscape

Call `list_projects` to understand the org structure, and `wiki_stats` (with `project` when one is clearly relevant) to know where knowledge lives.

### 2. Broad wiki search

Run `knowledge_base` command `sections` with 2-3 phrasings -- it is semantic, so natural language works. Each hit carries the verbatim document passage that supports it: quote that rather than opening the file. Use `grep` for exact names, IDs, or phrases; if a literal `grep` misses, the wording may differ, so retry with `sections` rather than concluding the knowledge base lacks it. For "every X", counts and superlatives, list the population with `ls` (by `page_type`) or `rows` instead of extracting and grepping the documents.

### 3. Ask the researcher (when available)

If your client exposes `research`, call it with the question. It searches the wiki, documents, and connected services and returns a cited answer. Treat it as a starting map, not the final word.

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

## Cards or the original files

Cards and pages are summaries written from the files a team uploaded. They tell you what is known, who owns it and where it came from, and they are the right first read. They are not the data. Go back to the original file when the answer depends on the file itself:

- **You need to compute.** Totals, averages, a re-fit, a recount, a comparison across rows: the card gives you at most the numbers someone chose to write down. Compute from the source table, not from figures copied into prose.
- **You need exact wording or a value.** A quote, a dose, a threshold, a date, a cell in a table.
- **The card is not enough.** It is marked partial or incomplete, its `coverage` says results were cut off, it lacks the field you need, or two sources disagree (`contradicts` / `qualifies`) and you have to see which is right.

How to get there:

1. Find the file the claim rests on: `sources {page}` or `provenance {page}` list the cited files with their `source_ref` and file id.
2. Read its text with the `read_file` tool (`artifact_id`, then `offset` to page through), or download the file itself for analysis: `beakr files download <artifact_id>` or `beakr.files.download(...)` in the SDK.
3. Compute locally from the downloaded file (spreadsheet, CSV, PDF tables) and keep the calculation reproducible: say what you computed and from which rows.

Then record what you learned so the next agent does not redo it: add it as a finding (`knowledge_base_write` with `action: add_finding`, or `beakr kb add-finding`) citing the original file's `source_ref`, and say in the finding that the value was computed from the file, not copied from a card. If the card itself is wrong or stale, propose an edit to it with the same citation instead.
