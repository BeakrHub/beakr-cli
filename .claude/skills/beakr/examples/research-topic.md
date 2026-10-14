# Example: Deep research on API redesign

## User prompt
"Research everything we know about the API redesign and summarize the current state"

## Workflow

### Step 1: Broad search (in parallel)
```
knowledge_base(command="sections", arguments={"query": "API redesign"})
knowledge_base(command="sections", arguments={"query": "API architecture changes"})
knowledge_base(command="grep", arguments={"query": "v2 migration"})
```

### Step 2: Read top hits (in parallel, narrowest read first)
```
knowledge_base(command="cat", arguments={"page": "API Redesign Decision"})
knowledge_base(command="cat", arguments={"page": "API Architecture", "section_id": "sec_current_state"})
knowledge_base(command="cat", arguments={"page": "V2 Migration Plan", "outline": true})
```

### Step 3: Check timeline
```
knowledge_base(command="timeline", arguments={"timeline_query": "API redesign", "start_date": "2026-01-01"})
```

### Step 4: Trace provenance on key claims
```
knowledge_base(command="provenance", arguments={"page": "API Redesign Decision"})
```

### Step 5: Follow links
```
knowledge_base(command="links", arguments={"page": "API Architecture"})
```

### Step 6: Synthesize
Present findings by theme:
- **Decision**: The team decided to migrate to v2 in January 2026 (API Redesign Decision)
- **Current state**: Migration is 60% complete per the March update (V2 Migration Plan)
- **Open questions**: Authentication approach still under discussion -- two proposals exist with contradicting provenance (API Architecture)
- **Gap**: No page covers the data migration strategy

## Key points
- Use multiple phrasings to cast a wide net; `sections` for meaning, `grep` for exact terms
- Read in parallel, and read sections rather than whole pages when a hit points at one
- Check provenance to surface contradictions
- Organize findings by theme, not by page
- Call out gaps explicitly
