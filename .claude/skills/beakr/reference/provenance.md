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

- An unmarked excerpt is a verbatim quote from the source.
- `(normalized)` is the source's words with whitespace or formatting normalized.
- `(paraphrase)` is the compiler's wording, not the source's.
- `(human asserted)` was supplied by a person without checking the source text.
- `(unverified)` was never checked against the source.
- "source changed since cited" means the source has a newer version than the one the claim was checked against.

Open the source before quoting a paraphrase or unverified excerpt, or before resting a conclusion on it: a file with `read_file` (its reference, `source_ref` or file id); a connected item (a meeting note, an email, a Slack thread) with the `integrations` call its Sources line prints (`read with integrations command='call' tool=... arguments={...}`), made as printed. A `url` is for a person to open.

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
