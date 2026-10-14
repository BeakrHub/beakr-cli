# Example: Answering "Who owns onboarding?"

## User prompt
"Who owns the onboarding flow?"

## Workflow

### Step 1: Research
Call `research` with the question:

```
research(query="who owns the onboarding flow")
```

The response returns a cited answer mentioning Sarah Chen [1] and the pages it drew on.

### Step 2: Verify if needed
If the user wants more detail, find the exact section and read just that:

```
knowledge_base(command="sections", arguments={"query": "onboarding flow owner"})
knowledge_base(command="cat", arguments={"page": "Onboarding Architecture", "section_id": "sec_ownership"})
```

### Step 3: Present
"Based on the knowledge base, Sarah Chen owns the onboarding flow (Onboarding Architecture). She took ownership in Q1 2026 as part of the platform team reorganization (Team Structure)."

## Key points
- Start with `research` -- it searches everything in one call
- Always cite the page title
- If the answer involves people, check for a `person` page with `sections {query, page_type: "person"}`
