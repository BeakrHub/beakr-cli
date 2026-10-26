# Writing Knowledge Base Pages

Use this workflow when the user wants to create or update a page. Every change goes through `knowledge_base_write`, which stages a **proposal**; nothing is written until the user asks you to `accept_proposal`, unless the write result says the user's auto-approve is on. Two actions apply at once instead: `sort_table`, which only reorders a table, and a project or org `page_type` the verifier accepts (a global one is refused).

## Before writing

1. **Check for duplicates.** Search with `knowledge_base` command `sections` (and `grep` for exact names). If a page exists on this topic, read it with `cat` and update it instead of creating a new one.

2. **Pick the project.** Use `list_projects` and pass the project's name or ID as `scope`. Never guess: a page title or filename is not a scope.

3. **Find the right parent.** Use `suggest_parent` with `title` (and your draft as `content`) for a page you are about to create, or `ls {parent}` to walk the hierarchy. If unsure, ask the user.

4. **Choose the page type: cards first.** Every project is a map of cards. One specific thing (a person, organization, product, system, model, study, decision, event) is a **card**. Before your first `new`, run `ontology` and pick the most specific card type the project has. If none fits, propose one with `page_type` rather than writing a topic. Name the card plainly (`AlphaFold`, not `DeepMind - AlphaFold`), give it a Description of 2 to 8 sentences (what it is, why it matters here, its status: its identity, not a list of facts), and grow it with `add_fact` / `add_connection` / `add_finding`, each with evidence. Common types:

| Type | When to use |
|------|-------------|
| `person` | One person. |
| `organization` | One company, team or department. |
| `product`, `system`, ... | One product, system, model, study or other specific thing: use the most specific card type `ontology` lists. |
| `decision` | One decision, with its rationale and date. |
| `topic` | A theme, concept or field that ties several cards together (`Protein structure prediction`), never one specific system or product. Not a card; needs no card shape. |
| `overview` | Index/section pages that tie a section together. Not a card. |
| `research_note` | Ephemeral analysis, not canonical knowledge. |

One-off messages and meetings are not cards: what they established goes on the cards of the things involved. An analysis across several cards is its own page, linked from each card with an `add_finding`.

A card in two calls, a `new` and then a dated fact:

```
knowledge_base_write(
  action="new",
  scope="Research",
  arguments={
    "title": "AlphaFold",
    "page_type": "system",
    "summary": "DeepMind's protein-structure prediction model.",
    "sections": [{"title": "Description", "body": "AlphaFold predicts a protein's 3D structure from its amino-acid sequence (alphafold-paper.pdf › p. 1)."}],
    "rationale": "A system the team cites often.",
  },
)
knowledge_base_write(
  action="add_fact",
  scope="Research",
  arguments={
    "page": "AlphaFold",
    "attribute": "Release",
    "value": "AlphaFold 2 code released as open source",
    "when": "2021-07",
    "evidence": "(alphafold-paper.pdf › p. 1)",
    "rationale": "Release date from the paper.",
  },
)
```

`when` is when the value held in the world (`YYYY-MM-DD`, `YYYY-MM`, `YYYY-Qn` or `YYYY`), never when it was recorded.

## Adding to a project-map card

A map card has a Description, a Facts table (`| Attribute | Value | When | Evidence |`), a Connections table (`| Relation | To | When | Evidence |`) and dated Findings. Nuance stays in the value's own words. Add to it one row at a time; each is merged into the live card, so never resend a whole table. Its Facts, Connections and Findings rows are protected: they change only through `add_fact`, `add_connection`, `add_finding`, `edit_rows` and `sort_table`. An `edit_section` or `edit` that would change a row is refused; a `find_replace` changes the text outside the tables and reports the row matches it left for `edit_rows`. `edit_rows` takes `table` `facts`, `connections` or `findings` (a finding is named by its `date` and `text`):

```
knowledge_base_write(
  action="add_fact",
  scope="AMR surveillance",
  arguments={
    "page": "FR25-1187",
    "attribute": "Cluster",
    "value": "CLX-21 under SOP r7",
    "when": "2026-06-02",
    "evidence": "{{conversation:2026-10-06-clusters}}",
    "citations": [{"key": "conversation:2026-10-06-clusters", "source_type": "conversation", "source_title": "Session on AMR clusters", "stance": "support", "meta": {"excerpt": "..."}}],
    "rationale": "Re-clustering under SOP r7 moved the isolate.",
  },
)
```

For a source Beakr already has, `evidence` is the readable reference a read printed (`"evidence": "memo.pdf › p. 7"`); Beakr stores it as that citation. A `{{conversation:...}}` token is only for this conversation or a note, with its record in `citations` as above. A value written another way than the row already on the card (`2026-06-02` or `June 2, 2026`, `48,000` or `48000`) joins that row, and the result says so.

Pass `rows=[{...}, ...]` to add several rows in one call; each item inherits the call's `evidence` and `when`. `sort_table` with `table` and `order_by` (e.g. `"attribute, when desc"`) orders a table and keeps it sorted on every later write. `add_connection` takes `relation` (a short verb phrase carrying any detail), `to` (the other page's title) and `when?`. `add_finding` takes `date`, `kind` (result, lesson, decision, question, correction), `text`, and optional `about`, `finding_scope` and `supersedes`: use it for a conclusion you reached, which is not a fact a source states. Keep attribute names stable so rows line up; a new value under the same attribute sits beside the old one as its history, and which one answers a question is decided by its `when` and the scope written in its value. Outside a project map these actions are refused on pages that are not cards; use `edit_section` there.

### Keeping a card clean

To change rows already on a card, use `edit_rows`: `old_rows` names each row as the card shows it (`{attribute, value}` or `{relation, to}`), and `rows` gives what replaces them, at the same place in the table:

```
knowledge_base_write(
  action="edit_rows",
  scope="AMR surveillance",
  arguments={
    "page": "FRP-SOP-0412",
    "table": "facts",
    "old_rows": [{"attribute": "Effective date", "value": "2026-06-11"},
                 {"attribute": "Date effective", "value": "2026-06-11"}],
    "rows": [{"attribute": "Effective date", "value": "2026-06-11"}],
    "rationale": "One date recorded under two attribute names.",
  },
)
```

A new row that gives no `evidence` keeps every citation of the rows it replaces (and their `when`, when they agree), so merging is one call. Deleting rows outright, or leaving a replaced row's citation off, needs a `reason`. Rows that say the same thing become one row; values for different scopes, versions or times stay separate rows with the scope in the value; a row cited `contradicts` stays. Use `add_finding` only for what a row cannot hold: a conclusion or a longer explanation. A document whose versions people track by name gets a card per version under its own card.

## Updating an existing page (preferred)

Use `edit_section` to change one section without touching the rest of the page. Read the page first (`cat {page, outline: true}`) to get section IDs. `new_section_body` replaces that section's entire body.

```
knowledge_base_write(
  action="edit_section",
  scope="Platform",
  arguments={
    "page": "API Architecture",
    "section_id": "sec_auth",
    "new_section_body": "Internal services authenticate with mTLS {{conversation:2026-09-16-auth}}.",
    "citations": [
      {
        "key": "conversation:2026-09-16-auth",
        "source_type": "conversation",
        "source_title": "Claude Code session on service auth",
        "stance": "support",
        "meta": {"excerpt": "We agreed to move internal services to mTLS."}
      }
    ],
    "edit_note": "Record the move to mTLS",
    "rationale": "Decision made in this session; the page still described JWT."
  }
)
```

To add a section, pass a new `section_id` plus `section_title` (and `after` to position it). For several changes at once, use `edit` with `patches`.

## Renaming, merging and replacing text

- **`mv`** renames a page. The result names the final title (`Title (1)` when the one asked for was taken) and lists the pages whose `[[links]]` still show the old title. Those links keep resolving to the renamed page; their text changes only if you stage a `find_replace` for it.
- **`merge`** folds a duplicate (`page`) into the page to keep (`canonical`), carrying its titles over as aliases so links keep resolving. Pass `merge_sections`, the ordered list the survivor leads with (`["canonical:sec_overview", "duplicate:sec_findings"]`): only the duplicate sections it names are moved across, whole with their citations; an unnamed duplicate section is archived with the duplicate and reported in `sections_left_behind`. Read every duplicate section before leaving one out.
- **`find_replace`** replaces text across pages; on a map card it skips the Facts, Connections and Findings rows and reports them, so change those with `edit_rows`.

## Creating a page

Use `new` with `title`, `page_type`, `summary`, `parent`, `rationale`, and `sections`.

`sections` is the page: an ordered list of `{title, body}` objects. **Beakr writes the `<!-- sec:ID -->` markers and section IDs for you** -- do not author markers and do not pass `content`.

Each section can also carry:
- `citations`: records for the sources its body cites (see below)
- `event_start`, `event_end`, `date_precision` (day, month, quarter, year, approx) for dated decisions, meetings, launches, incidents, and other timeline-worthy sections. `date_precision` is required whenever `event_start` is set.

## Citations

Cite every factual claim, table value, date, title, and relationship inline, right after it:

- **A source Beakr already has:** write the readable reference a `knowledge_base` read printed for it, in parentheses, as the read did: `The rate is 21 percent (tax-memo.txt › p. 7).`, `(#deals › message 2026-03-04 14:02 UTC)`, `(ENG-12)`, `(Page Title › Section)`. Beakr stores it as that citation; a reference to something you may not cite is refused (`citation_not_citeable`). You can also list it in `citations` as `{"reference": "memo.pdf › p. 7", "stance": "support"}` or with the `source_ref` from its Sources line.
- **This conversation, or a note** (cited for the first time): put `{{conversation:<label>}}` after the claim and give it a record in that section's `citations` with the same `key`, `source_type` `conversation` (or `agent_note`, `user_note`), a `source_title`, and `meta.excerpt` (or `meta.content` / `meta.text`). The key must start with its own source type.

Set `stance` to `support`, `qualifies`, or `contradicts`. It defaults to `support`, and that default is itself a claim about the evidence. A token with no citation record is stored as a bare pointer that can never be verified.

## Content conventions

- Use `[[Page Title]]` to link other pages; `[[Display Text|Page Title]]` when the text differs. Check targets exist first.
- `summary` is a one-line description of what the page is about, never a note about your edit (that is `edit_note`).
- Include a "Related Pages" section with links to connected pages.
- Be specific: name WHO made decisions, WHEN things happened, WHY.
- Use tables for structured data, code blocks for config/commands.
- Do not create stub pages -- every page should have substantive content.

## After proposing

A write to one section or card row (`edit_section`, `add_fact`, `add_connection`, `add_finding`, `edit_rows`) prints its proposal's id and `content_hash`. Show the user what you staged with `show_proposal`, which prints the hash again and who filed the proposal. Call `accept_proposal` only when the user explicitly asks to apply that proposal, with that hash as `reviewed_hash`: the accept then applies exactly what was shown, and a proposal that changed in between is refused (`proposal_changed`; show it again). Take the hash from the read the user reviewed, never from a fresh read made just before accepting. A proposal that changes a card's table rows is not accepted without it; it stays acceptable when the table is re-sorted or other rows change, and only a change to a row it edits supersedes it. Where the organization or project requires a person to accept what agents write, `accept_proposal` is refused (`person_review_required`) and nothing is applied, and auto-approve does not apply either: tell the user the proposal waits for them to accept in Beakr, and do not retry. `knowledge_base` command `proposals` lists pending proposals by default; `status` also takes `accepted`, `dismissed`, `superseded` (each with the reason it can no longer apply) and `resolved`, and a page that stops short names the `offset` that continues it; a proposed page does not exist in `ls`, `cat`, or search until accepted.
