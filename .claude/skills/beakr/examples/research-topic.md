# Example: Deep research on API redesign

## User prompt
"Research everything we know about the API redesign and summarize the current state"

## Workflow

### Step 1: Broad search
```
kb_search(query="API redesign")
kb_search(query="API architecture changes")
kb_search(query="REST API migration")
```

### Step 2: Read top hits
```
kb_cat("api-redesign-decision")
kb_cat("api-architecture")
kb_cat("v2-migration-plan")
```

### Step 3: Check timeline
```
kb_timeline(query="API", start_date="2026-01-01")
```

### Step 4: Trace provenance on key claims
```
kb_provenance("api-redesign-decision")
```

### Step 5: Follow links
```
kb_links("api-architecture")  # what pages link here?
```

### Step 6: Synthesize
Present findings by theme:
- **Decision**: The team decided to migrate to v2 in January 2026 [api-redesign-decision]
- **Current state**: Migration is 60% complete per the March update [v2-migration-plan]
- **Open questions**: Authentication approach still under discussion -- two proposals exist with contradicting provenance [api-architecture]
- **Gap**: No page covers the data migration strategy

## Key points
- Use multiple search queries to cast a wide net
- Read pages in parallel for speed
- Check provenance to surface contradictions
- Organize findings by theme, not by page
- Call out gaps explicitly
