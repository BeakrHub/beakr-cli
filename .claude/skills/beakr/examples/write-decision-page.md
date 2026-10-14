# Example: Creating a decision record

## User prompt
"Document our decision to switch from REST to GraphQL for the internal API"

## Workflow

### Step 1: Check for duplicates
```
knowledge_base(command="sections", arguments={"query": "REST to GraphQL decision internal API"})
```
No existing page found.

### Step 2: Pick the project and parent
```
list_projects()
knowledge_base(command="suggest_parent", scope="Platform", arguments={"title": "Internal API: REST to GraphQL", "content": "... draft with [[API Architecture]] links ..."})
```
Suggested parent: "API Architecture".

### Step 3: Gather sources
```
research(query="internal API architecture GraphQL REST", project="<platform project id>")
knowledge_base(command="sources", arguments={"page": "API Architecture"})
```
Note each source's citation key and `source_ref`.

### Step 4: Stage the page

```
knowledge_base_write(
  action="new",
  scope="Platform",
  arguments={
    "title": "Internal API: REST to GraphQL",
    "page_type": "decision",
    "summary": "Decision to move internal service-to-service APIs from REST to GraphQL.",
    "parent": "API Architecture",
    "rationale": "The user asked to record this decision; no existing page covers it.",
    "sections": [
      {
        "title": "Summary",
        "body": "The engineering team decided to migrate the internal API from REST to GraphQL, effective Q2 2026 {{gdrive:api-decision-doc}}. The public API remains REST {{gdrive:api-decision-doc}}.",
        "citations": [
          {"key": "gdrive:api-decision-doc", "source_ref": "beakr-source:v1:...", "stance": "support"}
        ]
      },
      {
        "title": "Decision",
        "body": "**Decision maker:** Sarah Chen (CTO) {{gdrive:api-decision-doc}}\n\nAdopt GraphQL for internal service communication using Apollo Federation {{gdrive:api-decision-doc}}. It reduces over-fetching by 60% based on traffic analysis {{conversation:graphql-traffic}}.",
        "event_start": "2026-03-15",
        "date_precision": "day",
        "citations": [
          {"key": "gdrive:api-decision-doc", "source_ref": "beakr-source:v1:...", "stance": "support"},
          {
            "key": "conversation:graphql-traffic",
            "source_type": "conversation",
            "source_title": "Claude Code session on API traffic",
            "stance": "support",
            "meta": {"excerpt": "Traffic analysis showed GraphQL would cut over-fetching by about 60%."}
          }
        ]
      },
      {
        "title": "Alternatives considered",
        "body": "1. **Keep REST, add OpenAPI codegen** -- lower migration cost but doesn't solve over-fetching {{gdrive:api-decision-doc}}\n2. **gRPC** -- better performance but weaker tooling for our stack {{gdrive:api-decision-doc}}",
        "citations": [
          {"key": "gdrive:api-decision-doc", "source_ref": "beakr-source:v1:...", "stance": "support"}
        ]
      },
      {
        "title": "Related Pages",
        "body": "- [[API Architecture]]\n- [[Platform Team]]"
      }
    ]
  }
)
```

### Step 5: Review with the user
```
show_proposal(proposal_id="...")
```
Call `accept_proposal` only after the user explicitly says to apply it.

## Key points
- Use the `decision` page type; include the date (`event_start` + `date_precision`), decision maker, and alternatives
- `sections` is a list of `{title, body}`; Beakr adds the section markers
- Put an inline `{{key}}` token after every factual claim, and a matching citation record in that section
- Reuse `source_ref` for sources Beakr already has; cite this conversation with `source_type: "conversation"` and `meta.excerpt`
- Link related pages with `[[Page Title]]`
