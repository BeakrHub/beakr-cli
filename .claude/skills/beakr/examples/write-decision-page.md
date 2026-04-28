# Example: Creating a decision record

## User prompt
"Document our decision to switch from REST to GraphQL for the internal API"

## Workflow

### Step 1: Check for duplicates
```
kb_search(query="REST GraphQL decision internal API")
```
No existing page found.

### Step 2: Find parent
```
kb_ls(page_type="overview")
```
Found "API Architecture" overview page -- use as parent.

### Step 3: Research context
```
research(query="internal API architecture GraphQL REST")
```
Gather background from existing pages to include context.

### Step 4: Write the page

Create with type `decision`:

```markdown
<!-- sec:summary -->
## Summary

The engineering team decided to migrate the internal API from REST to GraphQL,
effective Q2 2026. This affects service-to-service communication only; the
public API remains REST.

<!-- sec:context -->
## Context

The internal API serves 12 backend services with ~40 endpoints. Pain points
included over-fetching, versioning complexity, and inconsistent error handling.
See [[API Architecture]] for the current state.

<!-- sec:decision -->
## Decision

**Date:** 2026-03-15
**Decision maker:** Sarah Chen (CTO)
**Participants:** Platform team, Backend leads

Adopt GraphQL for internal service communication using Apollo Federation.
Key factors:
- Reduces over-fetching by 60% based on traffic analysis
- Single schema eliminates versioning overhead
- TypeScript codegen provides type safety end-to-end

<!-- sec:alternatives -->
## Alternatives considered

1. **Keep REST, add OpenAPI codegen** -- Lower migration cost but doesn't solve over-fetching
2. **gRPC** -- Better performance but steeper learning curve and weaker tooling for our stack
3. **tRPC** -- Good DX but limited to TypeScript services only

<!-- sec:consequences -->
## Consequences

- 3-month migration timeline for core services
- Training needed for team members new to GraphQL
- Monitoring approach changes (per-field metrics vs per-endpoint)

## Related Pages

- [[API Architecture]]
- [[Platform Team]]
- [[Q2 2026 Roadmap]]
```

### Step 5: Verify
```
kb_cat("rest-to-graphql-decision")
```

## Key points
- Always use `decision` page type for decision records
- Include date, decision maker, and participants
- Document alternatives that were considered and why they were rejected
- Link to related pages with `[[Page Title]]` syntax
- Add section markers for provenance tracking
