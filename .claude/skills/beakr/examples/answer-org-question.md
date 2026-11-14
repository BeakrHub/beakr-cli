# Example: Answering "Who owns onboarding?"

## User prompt
"Who owns the onboarding flow?"

## Workflow

### Step 1: Look it up, narrowest read first
An owner is a fact on a card, so ask the database:

```
knowledge_base(command="rows", arguments={"table": "facts", "entity": "Onboarding", "attribute": "Owner"})
```

One row answers it: Onboarding · Owner · Sarah Chen · 2026-01-15 (Onboarding › Facts).

No row is not "no owner": it means no card states it under that name. Try the exact name, then the meaning:

```
knowledge_base(command="grep", arguments={"query": "onboarding"})
knowledge_base(command="sections", arguments={"query": "who owns the onboarding flow"})
```

### Step 2: Read the section when you need more
```
knowledge_base(command="cat", arguments={"page": "Onboarding Architecture", "section_id": "sec_ownership"})
```

### Step 3: Present
"Sarah Chen owns the onboarding flow (Onboarding › Facts). She took ownership in Q1 2026 as part of the platform team reorganization (Team Structure › Changes)."

## Key points
- A lookup (an owner, a status, a value, a count) is a Knowledge read: `rows`, then `grep`, then `sections`. It is faster than `research` and shows the row itself
- `research` is for an answer composed from several sources, not for a lookup
- Cite with the readable reference the read printed
- If the answer involves people, check for a `person` page with `sections {query, page_type: "person"}`
