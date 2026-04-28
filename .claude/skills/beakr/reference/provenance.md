# Understanding Provenance

Provenance tracks where knowledge base content came from and how confident the claims are.

## Tools

### kb_blame
Shows paragraph-level source attribution. Use to answer "where did this paragraph come from?"

```
kb_blame "page-slug"
```

Returns: revision number, source title, and text excerpt for each paragraph.

### kb_sources
Lists all source documents that contributed to a page.

```
kb_sources "page-slug"
```

Returns: source type, title, and ID for each source.

### kb_provenance
Shows section-level citations with stance. This is the most powerful provenance tool.

```
kb_provenance "page-slug"
```

Returns citations organized by section, each with a stance:
- **supports** -- the source confirms the claim
- **contradicts** -- the source disagrees with the claim
- **qualifies** -- the source adds nuance or conditions to the claim

## When to use

- User asks "where did this information come from?"
- User questions the accuracy of a claim
- You need to verify before presenting information as fact
- Auditing page quality (see [auditing.md](auditing.md))

## Interpreting stances

- Multiple `supports` citations = high confidence
- A `contradicts` citation = flag it to the user, present both sides
- A `qualifies` citation = include the nuance in your response
- No citations on a section = unsourced, note this to the user
