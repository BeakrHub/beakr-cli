# Understanding Provenance

Provenance tracks where knowledge base content came from and how well-supported each claim is. All three views are `knowledge_base` commands.

## Commands

### blame
Paragraph-level source attribution. Use to answer "where did this paragraph come from?"

```
knowledge_base(command="blame", arguments={"page": "API Design"})
```

### sources
The source documents behind a page, each with the other pages that draw on it. Also accepts a source title (a cited file's name) as `page` to get that one document's entry.

```
knowledge_base(command="sources", arguments={"page": "API Design"})
```

Each source carries its `source_ref`, filename, provider, URL or path, excerpt and locator, and a connected item carries the call that re-reads it. Reuse `source_ref` when citing that source in a write, or pass it to `read_file`.

### provenance
Per-section citation rollups with stance. The most useful view for judging a claim.

```
knowledge_base(command="provenance", arguments={"page": "API Design"})
```

Each citation carries a stance:
- **support** -- the source confirms the claim
- **contradicts** -- the source disagrees with the claim
- **qualifies** -- the source adds nuance or conditions to the claim

## Reading evidence labels

- An excerpt in quotation marks is the source's own words.
- `Described by Beakr: ...` is Beakr's own wording (a description of a figure, a computed value, or words that were not found in the source as written), not the source's.
- `Added by a person: ...` is a person's attribution, or the words its writer gave for a record in a system outside Beakr.
- A citation with no excerpt keeps its reference and its place in the source.
- "source changed since cited" means the source has a newer version than the one that was cited.

Open the source before quoting text that is not in quotation marks as the source's, or before resting a conclusion on it: a file with `read_file` (its reference, `source_ref` or file id); a connected item (a meeting note, an email, a Slack thread) with the `integrations` call its Sources line prints (`read with integrations command='call' tool=... arguments={...}`), made as printed. A `url` is for a person to open.

## When to use

- User asks "where did this information come from?"
- User questions the accuracy of a claim
- You need to verify before presenting information as fact
- Auditing page quality (see [auditing.md](auditing.md))

## Interpreting stances

- Multiple `support` citations = high confidence
- A `contradicts` citation = flag it to the user, present both sides
- A `qualifies` citation = include the nuance in your response
- No citations on a section = unsourced, note this to the user
