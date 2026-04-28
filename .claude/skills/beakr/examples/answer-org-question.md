# Example: Answering "Who owns onboarding?"

## User prompt
"Who owns the onboarding flow?"

## Workflow

### Step 1: Research
Call `research` with query "who owns the onboarding flow":

```
research(query="who owns the onboarding flow")
```

The response returns a cited answer mentioning Sarah Chen [1] and links to the relevant page.

### Step 2: Verify if needed
If the user wants more detail, read the specific page:

```
kb_cat("onboarding-architecture")
```

### Step 3: Present
"Based on the knowledge base, Sarah Chen owns the onboarding flow [onboarding-architecture]. She took ownership in Q1 2026 as part of the platform team reorganization [team-structure]."

## Key points
- Start with `research` -- it searches everything in one call
- Always cite the page title/slug
- If the answer involves people, check for a `person` page with `kb_cat`
