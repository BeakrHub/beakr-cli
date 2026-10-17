# Public API changelog

Changes to the public Beakr API, newest first. Every entry names the operations
it touches. Additive changes ship in a minor version; a breaking change ships
only in a new major version, announced here with its migration path and the
date the old behavior is retired (see [Versioning](./versioning.md)).

<!-- The developer docs (BeakrHub/beakr-docs) publish this file. Write it for API
users: name operations, scopes, fields and error codes. Keep internal notes,
decision records, repository paths and review identifiers out of it; they
belong in the internal technical docs. tests/unit/test_public_docs_hygiene.py
enforces this. -->

<!-- contract-stamp: public-openapi sha256=ba8132085bc515e0ec1082fed2103bf894f12082b9a5d4e44e07a6666d459905 changelog sha256=b52dd2ebca43c3195a219cdb19b372e5d12ca0cb31e5bc70185cce9bdf90b0ff -->
## 1.0.0 (unreleased)

The first public version.

- `POST /v1/knowledge/wiki/command` answers 400 when the body nests the
  command's options under `arguments` or `args`: the options go at the top
  level of the body, next to `command`. They used to be ignored, so the read
  ran without them.

- `POST /v1/knowledge/wiki/command` `query` answers open with what the query
  was understood as: for `sql`, a `Translated to spec:` line with the spec as
  JSON; then the total and the query in words; a `Resolved:` line naming the
  stored field and card type each condition matched, how (exact, alias, words,
  meaning) and the exact name's card count, with a near name held by many more
  cards when the exact name is sparse; and an `Applied:` line listing what was
  defaulted, capped or applied (a default LIMIT or ORDER BY, AS OF per card, an
  added COUNT, a sum split by unit). The `query` object adds the same as
  `resolved` and `applied`, and `unit` (`row`, `group` or `field`). A `sql`
  LIMIT above 200 is capped to 200 and said, instead of refused. A SUM or AVG
  of `value_amount` or `value_number` is split by a `unit` column (the
  currency, `%`, or the word after the number), so different units and
  currencies are never added into one total.

- `POST /v1/knowledge/wiki/command` `query` takes `sql`: one SELECT, such as
  `SELECT value, COUNT(DISTINCT entity) AS cards FROM facts AS OF CURRENT_DATE
  WHERE entity_type = 'matter' AND field = 'Current status' GROUP BY value ORDER
  BY cards DESC`. The text is parsed into the same query a `spec` describes and
  never runs as SQL: it reads facts, connections or findings, joins along
  `SAME_CARD(a)` or `TARGET_CARD(a)` (up to two), filters with WHERE (AND, OR,
  NOT, parentheses; `field =` the exact name, `LIKE` by words, `~` by meaning;
  `value` text, `IN`, `CONTAINS` and typed `>`, `>=`, `<`, `<=`, `BETWEEN`),
  `AS OF` a day or `CURRENT_DATE`, groups with GROUP BY, aggregates with
  `COUNT(*)`, `COUNT(DISTINCT key)`, `MIN`, `MAX`, `SUM`, `AVG`, and takes
  ORDER BY and LIMIT. Text outside the grammar answers `invalid_arguments`
  stating the grammar and an example; sending both `sql` and `spec` is refused.
  Every `query` answer adds a `query` object: the `spec` that ran, the query
  in words (`ran`), `notices`, the window's `columns` and `rows`, `total` and
  `next_cursor` (send it as `cursor` with the same `sql` or `spec`) and
  `elapsed_ms`. A field asked by a name few cards hold while a near name
  holds it on many more is named on the answer's first line and in `notices`. In a `spec`, `as_of`
  takes `today`, and an `aggregate` without `group_by` answers one row over
  every match (it used to be ignored).

- `POST /v1/knowledge/wiki/command` adds the `query` command (where enabled for
  the organization): `spec` is a JSON object, not SQL, run over every map
  card's rows under the caller's access and the request's scope. `from` is
  facts, connections or findings; `where` is an and/or/not tree of conditions
  on entity, entity_type, field (`match` exact, similar or meaning), value (eq,
  ne, contains, in, and gt, gte, lt, lte, between on typed numbers and dates),
  when, as_of, to and connected_to; `join` takes up to two joins, on
  `same_card` or `target_card`; `group_by` takes field, value, entity_type or
  entity; `aggregate` takes count, count_distinct, min, max, sum and avg;
  `order_by`, `limit` (default 50, at most 200) and `cursor` page the result;
  `semantic` ranks field names by meaning (`rank_fields_by`) or keeps rows
  whose field means about a text (`field_near`). Every answer gives the total,
  one window of rows or groups, the query described in words, and a cursor when
  more follow. A refused spec answers `invalid_arguments` naming the key, what
  is allowed and, for an unknown field, the closest field names; where the
  feature is off, `feature_disabled`.

- `POST /v1/knowledge/wiki/command` `rows` takes `as_of` (Facts: each card's
  value on that date, the row with the latest When on or before it),
  `connected_to` (only cards linked to that page, in either direction) and
  `order_by` `amount` or `date` (Facts: money values with their scale, and
  date values, in order). Where the rows index is enabled, a `rows` read
  covers every card in scope with no card scan cap, `view` `fields` with a
  `query` ranks the field names by meaning, an `attribute` that no field is
  named resolves to the closest field by words or meaning (the reply's first
  line names the field it used, and a field meaning the opposite is never
  used), a `query` that no row matches in full matches the rows holding most
  of its words, and an empty reply lists the closest fields with their card
  counts and an example value. `rows` also takes `match`: `exact` (the field
  name or a recorded alias, and no retry of a `query` by words), `similar`
  (also a field holding its words) or `meaning` (the default: also the closest
  field by meaning); without the rows index, `similar` and `meaning` match the
  phrase or every word, as before.

- `POST /v1/knowledge/wiki/command` `rows` takes `view` `conflicts` (Facts):
  each pair of rows on one card that gives one field different values with
  overlapping or missing When, both sides with their citations. A different
  value with a later When is an update and is not listed.

- **Search documents by meaning.** `POST /v1/knowledge/documents` (behind the
  documents feature) adds the `search` command: passages of the original
  documents ranked by meaning and wording, grouped by document, best first.
  `arguments` takes `query`, `limit` (documents per page, default 8, max 25),
  `offset`, `passages` (per document, default 2, max 5), and the same `path`,
  `glob`, `type`, `document` and `project` filters as `grep`. Besides `content`
  and `sources`, a search answers `results`: one source record per matching
  document (`source_ref`, `name`, `filename`, `location` with `display_path`,
  `file_id` and `version_id`), its `match` (`strong`, `moderate` or `weak`) and
  `score`, and its best `passages`, each with its own `source_ref`,
  `unit_locator`, `line_start`/`line_end` (the lines `read` takes), `section`,
  a brief `excerpt`, `match` and `score`. A search is a ranked selection, not a
  census: use `grep` to find or count every mention.
- `POST /v1/knowledge/documents` takes a project name as well as an id in
  `project_id` (or `project`); a name may match several projects you can read.
  Without one, a command covers everything the caller can read, and a
  project-restricted credential now covers all of its projects (it was refused
  with `403 insufficient_scope` before). A restricted credential naming a
  project outside its list by name answers `404 not_found`, the same as a name
  that matches nothing.
- **Download a file from any source record in one step.**
  `POST /v1/artifacts/download-url` (`projects:read`) takes the `reference`
  `POST /v1/artifacts/read` takes, including a source record's `source_ref`,
  and answers a short-lived signed `url` for the file's original bytes, with
  `expires_in`, `filename`, `mime_type` and the `file` (`id`, `version_id`,
  `title`, `display_path`). A reference that names a version downloads that
  version; any other, the current one. `inline: true` asks a browser to display
  the file. A Knowledge page's reference answers `400 knowledge_page`, a
  connected item's `400 not_a_file`, a Beakr document with no stored original
  `409 no_original_file`, and a file the caller cannot read `404 not_found`,
  the same as no file.
- `POST /v1/knowledge/research` takes `quick`: `true` answers within 15
  seconds from one search of Knowledge and uploaded files, with a short cited
  answer. Connected integrations are not searched. The response's `engine` is
  `quick`, and `stopped_early` is `true` when the answer is the cited results
  without a written summary. Leaving `quick` out keeps research as before;
  a `quick` that is not a boolean is `422 invalid_quick`.
- A map card's Facts, Connections and Findings rows change only through row
  operations. `POST /v1/knowledge/wiki/write` refuses an `edit_section`,
  `edit` or `find_replace` that would change one of their rows as markdown
  (`invalid_arguments`, naming the row action to use); `add_fact`,
  `add_connection`, `add_finding`, `edit_rows` and `sort_table` change them,
  and `find_replace` may still retarget a `[[link]]` in a row. `edit_rows`
  takes `table` `findings`, naming each finding by `date` and `text`. No
  write may store a table row that does not parse. `GET /v1/knowledge/wiki/pages/{page_id}` adds
  `protected_sections`, and `GET /v1/knowledge/wiki/proposals` and
  `GET /v1/knowledge/wiki/proposals/{proposal_id}` add `table_changes`: each
  changed table's `added`, `removed` and `changed` rows (`changed_cells` names
  what changed) with their citations, whether the table was `reordered`, and
  the `reasons` given for the edit; `table_changes_complete` is `false` when
  part of the change is not expressed as rows. Those protected sections hold
  only their rows, in one canonical form: a write that puts other text in them
  is refused with `noncanonical_table`. Proposal reads also add
  `content_hash`, and `POST /v1/knowledge/wiki/proposals/{proposal_id}/accept`
  takes `reviewed_hash`, the `content_hash` echoed back: when the stored
  proposal no longer hashes to it, nothing is applied and the answer is `409`
  with `code` `proposal_changed`. Accepting a proposal that changes a card's
  table rows may require it (`422` `review_required`); proposal reads say so
  with `changes_card_tables`.
- `GET /v1/knowledge/wiki/pages/{page_id}` adds, for a map card,
  `contradicting_citations` (per protected section, the citation keys its
  sources cite as contradicting what the card states) and `disputed_rows` (per
  protected section, the `row_id`s of the rows those citations mark as
  disputing, the ids the table and proposal reads use). A card's text does not
  carry a citation's stance; read it here. Both are empty on any other page.
- `POST /v1/knowledge/wiki/command` adds to the Knowledge reads:
  `links` with `direction` `out` lists the pages a page links to, with the
  relation it states (`limit`, `offset`); `rows` takes `to`, `regex`,
  `group_by` (counts, sums, minimum and maximum per `entity`, `entity_type`,
  `year` or a column) and `view` `fields` with `field_id` (the field names
  cards use, with counts, types and examples); `timeline` takes `page` (that
  page and every page beneath it); `links` takes `offset` to page past one
  window of incoming links; `rows` takes `after_card` to continue a card scan
  that stopped at its cap (one project per scan); `hover` and `cat` with `outline` report a
  page's parent, children, incoming links by type and outgoing link count.
  `rows` filters (`attribute`, `to`, `query`) match their words in any order
  with plurals folded, as well as the phrase; nothing that matched before
  stops matching, and an empty filtered reply names the field names the
  scanned cards use. `rows` with `view` `groups` and a `page_type` lists that
  card type's field names that likely mean one field, grouped, with counts
  and example values.

- `GET /v1/jobs/{job_id}` keeps answering for an upload whose file was rejected
  as a duplicate of one already in the same place: the `artifact_index` job is
  `failed` with `error.code` `duplicate` and `error.duplicate_of`
  (`artifact_id`, `title`) naming the copy that was kept, and `resource_url`
  points at that copy. It answered `404 not_found` before, once the rejected
  file was removed.
- `POST /v1/artifacts/read` accepts a source record's `source_ref` from any
  Knowledge read as `reference` (optionally followed by ` › <unit>` or
  `#<unit>`), and opens
  the file it names at the cited passage. A `source_ref` that names a Knowledge
  page answers `400 knowledge_page` (read the page with the knowledge_base `cat`
  command); one that names a connected item (Slack, email, calendar, Jira)
  answers `400 not_a_file`. A `source_ref` for a source the caller cannot read
  answers `404 not_found`, the same as one that names nothing.
- **Know when a project's Knowledge is ready.**
  `GET /v1/projects/{project_id}/knowledge-summary` (`projects:read`) is now
  documented, under Projects. Alongside its coverage counts it reports `queued` and `running`
  files (together `syncing`) and `failed` files (part of `eligible`), `ready`
  (true when nothing is queued or running), and the most
  recently failed files in `failed_files` (`failed_limit`, default 20), each with
  `artifact_id`, `filename`, `path`, a public `error_code`
  (`processing_failed`, `timed_out`, `concurrent_update` or `compile_failed`),
  a `reason` safe to show a person, `failed_at` and `retryable`. A project the
  caller cannot read now answers `404 not_found`, the same as one that does not
  exist (it answered `403` before).
- `GET /v1/projects/{project_id}/knowledge/failed-files` (`projects:read`) pages
  through every failed file, newest first; `failed_files_next_cursor` from the
  summary is its first `cursor`.
- `POST /v1/projects/{project_id}/knowledge/retry-failed` (`uploads:write`)
  sends the project's failed files, or the `artifact_ids` named, to Knowledge
  again, up to 500 per call, and reports each file's `outcome` (`queued`,
  `already_queued`, `not_failed`, `not_found` or `skipped`). Repeating it is
  safe: files already queued or running are left alone. It needs permission to
  add files to the project (`403 forbidden` otherwise) and is limited to 10
  requests per minute.
- `GET /v1/projects` takes `include_knowledge=true` to add each project's
  `knowledge` (`queued`, `running`, `failed`, `ready`).
- `GET /v1/bulk/{batch_id}/status` adds each file's `knowledge_status` and a
  `knowledge` breakdown for the batch (`queued`, `running`, `created`, `failed`,
  `skipped`, `other`, `not_requested`, `ready`).

- Knowledge reads (`POST /v1/knowledge/wiki/command`: `sections`, `cat`, `grep`,
  `rows`, `sources` and the rest) and answers (`POST /v1/knowledge/research`
  `text`, and a finished run's `answer.text`) no longer show
  `{{key}}` citation tokens. Each citation reads as its readable reference in
  parentheses: `Base rent is $48,000 per year (memo.pdf › p. 7).`, several
  passages of one document `(memo.pdf › p. 7, p. 9)`, a Slack message
  `(#deals › message 2026-03-04 14:02 UTC)`, an email
  `(Q3 budget · from ana@example.com · 2026-03-01 14:02)`, an issue `(PROJ-12)`,
  a Knowledge page `(Page Title › Section)`. Research `text` uses this form in
  place of `[1]`, `[2]`; the raw `answer` keeps its tokens. Source records gain
  `cited_places` (every place the text cites in the document) and `reference`
  (the name the text uses, when it is not `filename` or `name`). `citation_key`
  is unchanged. `POST /v1/knowledge/wiki/write` accepts every reference a read
  prints as a citation's `reference`, Knowledge pages (`Page Title › Section`)
  included; a short file name that several readable files share resolves to the
  one Knowledge cites. A write's prose may cite inline the same way: a
  reference in parentheses after a claim (`Rent is $5 (memo.pdf › p. 7).`) is
  stored as that citation, and one that names nothing the caller may cite is
  refused with `citation_not_citeable`. A `conversation`, `agent_note` or
  `user_note` citation may carry a `key` in its own namespace
  (`conversation:call-with-ana`) and be cited inline as `{{conversation:call-with-ana}}`.
  `POST /v1/knowledge/documents` results and `POST /v1/knowledge/wiki/write`
  results name citations the same readable way. `ResearchResult` fields `checked`, `not_checked`,
  `sources_consulted`, `subtasks`, `stopped_early`, `engine` and `lead_model`
  now say what they hold.
- `POST /v1/knowledge/wiki/command` supports continuing a chronological timeline without
  `timeline_query` using `cursor`. Timeline coverage distinguishes dated candidates
  examined (`scanned`) from results shown, reports whether an unqueried scan reached
  the end (`scan_complete`), and supplies `next_cursor` when another chronological
  window remains. Repeat the same filters when continuing. Semantic queries select
  relevant sections first and remain non-exhaustive; they do not return a cursor.
  Command guidance also states that the `rows` query is a literal substring filter.
  Compact/full source-sidecar Knowledge reads can report `coverage.truncated` when
  their response budget omits text; other reads may omit this field.
- `POST /v1/knowledge/research` now documents what its coverage reports, subtask
  count, early-stop indicator, engine and lead-model fields mean. Response behavior
  is unchanged.
- `POST /v1/knowledge/documents` searches (`grep`), reads line windows (`read`),
  and lists (`ls`) original indexed files. It requires `projects:read`; a
  project-restricted credential must supply one of its authorized projects.
  The feature returns 404 until enabled. Scan notices identify incomplete results.
- `POST /v1/knowledge/research` supports an optional research engine with
  `checked`, `not_checked`, `stopped_early`, `subtasks`, `engine`, `lead_model`,
  and `sources_consulted` fields. Its optional stronger lead shares the same
  project restrictions. The caller rate limit is now 30 requests per minute;
  concurrent-run and usage limits still apply.

### Documented surface

- **The contract documents a smaller core.** The published contract
  (`GET /v1/public/openapi.json`), the SDKs, `beakr api` and this reference now
  cover the core operations only: account and access (`/v1/me`, API keys,
  revoking a token, `GET /v1/jobs/{job_id}`), organization members and
  service accounts, projects and their members, files (artifacts, resumable
  uploads, the `/v1/bulk` batch upload of many files, folders, and
  `GET /v1/artifacts/versions/{version_id}/download-url` for a file's original
  bytes), the knowledge base (wiki command,
  search, pages, writes and proposals), Ask (`POST /v1/knowledge/research` and
  `/v1/runs`), sendable interviews and their links, conversations and threads,
  and connected integrations. Every other operation earlier drafts of this
  changelog name is now unlisted: it keeps answering the same credentials and
  scopes, but it is not documented, is not in the SDKs or `beakr api`, and
  carries no stability promise; it may change or be removed without notice.
  Deprecated paths are no longer listed; call their documented replacements. Scope names are unchanged.
- **Field descriptions.** Fields whose description only repeated their name now
  say what they hold, for example `ResearchResult` `confidence`, `entities`,
  `gaps`, `turns`, `duration_s` and `cost_usd`; `ConversationImported`
  `created`, `appended`, `pending_compound`, `compound_queued` and `stored`;
  `IntegrationOut` `connection_role` and `health_status`; and the Knowledge page,
  search and revision fields. Fields that are never null no longer say they
  can be: `expires_at` on `UploadSessionOut`, `UploadPartUrl`,
  `BulkPrepareResponse`, `CreatedLinkOut` and `SendableLinkOut`, and `error` on
  `ErrorEnvelope` and `FailedFileOut`. Behavior is unchanged.
- **Documented Knowledge request bodies.** `POST /v1/knowledge/research`
  (`query`, optional `project_id`), `POST /v1/knowledge/wiki/command` (`command`
  and its arguments, flat, plus `scope`, `detail` and `help` with `topic`) and
  `POST /v1/knowledge/wiki/write` (`action` and its fields, flat, plus `scope`
  and `help` with `topic`) now declare request schemas instead of a bare
  object. They accept the same inputs as before, still ignore keys they do not
  read, and answer the same errors. `ResearchResult` lists `text` and
  `citations` first, and the operation's description introduces them; `answer`
  is described as the raw form with `{{key}}` tokens for callers that render
  their own citations.
- **Short operation names and realistic examples.** Every documented
  operation's `summary` is now a short imperative phrase (for example "Remove a
  project member" for
  `DELETE /v1/projects/{project_id}/members/{principal_type}/{principal_id}`,
  "List project members" for `GET /v1/projects/{project_id}/members`); any
  detail the old summary carried opens its `description`. Component schemas,
  parameters and the free-form bodies of `POST /v1/knowledge/research`,
  `POST /v1/knowledge/wiki/command` and `POST /v1/knowledge/wiki/write` carry
  `examples` drawn from one consistent sample organization. Behavior is
  unchanged.
- **Public area names.** Each documented operation carries one public tag,
  and the contract lists every tag with a display name and description
  (`tags`) and groups them (`x-tagGroups`): Account & access (`account`,
  `api-keys`), Organization & people (`organizations`, `service-accounts`,
  `labels`), Projects (`projects`, `folders`), Files & uploads (`files`,
  `uploads`, `jobs`), Knowledge (`knowledge`), Ask & runs (`runs`, `threads`),
  Interviews (`interviews`), Conversations (`conversations`), Integrations
  (`integrations`), Building into your product (`external-users`,
  `end-user-tokens`) and API contract (`openapi`). The tag is the area of
  `beakr api <area> <action>` and of the SDKs' generated `api` namespaces, so
  these are renamed too: for example `beakr api files ...` for the
  `/v1/artifacts` operations, `beakr api runs ...` and `beakr api threads ...`
  for `/v1/runs` and the thread reads, `beakr api interviews ...` for
  `/v1/interview` links and sendables, `beakr api service-accounts ...` for
  `/v1/organizations/{org_id}/service-accounts`, `beakr api end-user-tokens ...`
  for `/v1/auth/end-user-tokens`, and `beakr api uploads ...` for the `/v1/bulk`
  batch upload. Paths, scopes and payloads are unchanged.
- **Clearer operation text.** Summaries and descriptions were rewritten for
  `PATCH /v1/artifacts/{artifact_id}`, `GET /v1/artifacts/{artifact_id}/content`,
  `POST /v1/artifacts/{artifact_id}/knowledge`, `POST /v1/artifacts/{artifact_id}/move`,
  `POST /v1/context`, `GET /v1/conversations/search`, `DELETE /v1/folders/{folder_id}`,
  `POST /v1/knowledge/research`, `GET /v1/knowledge/wiki/pages`,
  `GET /v1/knowledge/wiki/pages/by-title`, `GET /v1/knowledge/wiki/search`,
  `POST /v1/knowledge/wiki/write`, `DELETE /v1/projects/{project_id}`,
  `GET /v1/uploads/{upload_id}` and `GET /v1/threads/{thread_id}`. Behavior is
  unchanged.

### Conventions

- **Examples.** Example addresses in the contract use domains reserved for
  documentation (`example.com`), example paths are neutral (`deals/acme/...`), and
  example people and organizations are fictional.
- **Timestamps and IDs.** Every response timestamp is ISO 8601 UTC and every
  Beakr-created object is identified by a UUID; fields that carry a provider's
  or a document's own identifier are named in the reference. Two Knowledge
  resource schemas were renamed from private-looking names to
  `KnowledgeResourceScope` and `KnowledgeResourceCoverage`; their fields are
  unchanged.
- **Browser clients.** Browser requests may send `Idempotency-Key`,
  `X-Beakr-Client`, `X-Beakr-Agent` and `X-Beakr-Run`, and can read
  `X-Request-ID`, the `X-RateLimit-*` headers, `Retry-After`, `Deprecation`,
  `Link` and `Idempotent-Replayed`. The developer docs' "try it" console may
  call public operations from the docs site with an API key; it never sends or
  receives cookies.
- **Pagination.** Every list answers `{data, next_cursor, has_more}` and takes
  `cursor` and `limit` (1 to 200, default 50). Lists that answered a bare array
  (projects, labels, members, organizations, folders, artifacts, keys and
  more) and lists that named
  their rows (`threads`, `pages`, `events`, `proposals`, `notifications`,
  `sessions`, `templates`, `items`, `sources`, `backlinks`, `revisions`,
  `results`, `folders`) now page the same way. Offset, `after_title`,
  `next_before`, `next_offset` and `page_token` paging is replaced by the
  cursor. A cursor names the list it came from; a cursor from another list, or
  a malformed one, is `400 bad_request`.
- **Errors.** Every error body carries `error.code`, `error.message`,
  `error.request_id`, `error.fix` (how to correct the call) and
  `error.details` beside the existing `detail`. `error.details` is always an
  object: structured context such as `required_scope` or
  `expected_base_index` (still also present beside `code`), and for a
  `422 validation_error` every failing field under `details.errors` (`loc`,
  `msg`, `type`). The contract declares this body as the `ErrorEnvelope`
  schema on every operation's 422, its documented refusal statuses and the
  `4XX`/`5XX` ranges, replacing `HTTPValidationError`.
- **Idempotency.** `Idempotency-Key` is honoured on every create, including
  `POST /v1/folders`, `/v1/artifacts` and `/v1/conversations/import`.
  Every create that returns a one-time secret (an API key, a sendable
  interview link) declares `secret_redacted` and marks the secret optional,
  since a replay omits it.
- **Formats.** Every response timestamp (`*_at`) is declared
  `format: date-time` and every Beakr id `format: uuid`; the few identifiers
  that are not UUIDs (provider item ids, section keys, caller-chosen keys)
  are declared as plain strings. Thread timestamps are always UTC, and an
  unknown source time is `null`, never an empty string.
- **Long operations as jobs.** `GET /v1/jobs/{job_id}` reads any long
  operation (agent run, workflow run, integration sync, webpage import,
  uploaded file processing, Knowledge compile, bulk upload, template
  preparation) as
  `{job_id, kind, status, native_status, progress, result, error, project_id,
  created_at, finished_at, status_url, resource_url}`. Operations that start
  one add `job_id` and `status_url` to their responses (`x-beakr-job` in the
  contract names the kind). A job outside a project-restricted credential's projects
  answers `404 not_found`, the same as an unknown job, whatever its kind.
- **Canonical paths.** `/v1/runs`, `/v1/runs/{run_id}`, `/v1/runs/{run_id}/events`,
  `/v1/runs/{run_id}/cancel`, `/v1/me/organization` and `/v1/me/threads`. The
  paths they replace keep working and answer with a `Deprecation` date header
  and a successor `Link`.
- **Model availability.** A model-backed operation on a server with no provider
  for its model (for example `POST /v1/knowledge/research`) answers
  `503 model_unavailable` instead of `500 internal_error`.

### Authentication and accounts

- Credentials: API keys (`bk_live_`, optionally scoped and restricted to
  projects), service-account keys, end-user tokens (`bk_at_`), and Clerk OAuth
  access tokens for the CLI and MCP clients. The CLI signs in with Clerk's
  loopback PKCE flow; where browser sign-in is not configured, it uses an API
  key instead. Keys and tokens cannot create keys.
- Clerk OAuth tokens act as the person in the organization the request names
  (`X-Org-Id`, else their active organization), within the token's scopes, and
  only in an organization the person is a member of. An `X-Org-Id` that names no
  organization answers `404 unknown_organization` rather than falling back to the active
  organization. A Clerk OAuth token that is malformed or refused is not
  verified again for a short while, and an address that keeps presenting
  refused tokens answers `429 rate_limited` with `Retry-After`.
  An organization can limit which OAuth clients may act in it with the auth
  policy's `allowed_agent_clients` (Clerk client ids or CIMD URLs; refused with
  `403 agent_client_not_allowed`); `allowed_clients` stays the advisory list of
  labels a new API key may record. In an organization that requires single
  sign-on, a Clerk OAuth token works only when the sign-in it was issued to is
  the organization's SSO login.
- `DELETE /v1/me/api-keys/{key_id}`: a key limited by scopes or projects may
  revoke only itself, and answers `403 insufficient_scope` for any other key. A
  key restricted to projects can revoke itself too.
  A web session or a key with every scope may revoke any of the person's keys.
- Keys expire unless the organization allows otherwise: `POST /v1/me/api-keys`
  gives every new key, including one created with only a name, a 90-day
  lifetime unless `expires_in_days` (at most 365) says otherwise.
  `never_expires: true` is refused with `403 non_expiring_keys_disabled` unless
  the organization's auth policy sets `allow_non_expiring_keys`. Key
  listings (`GET /v1/me/api-keys`) add `created_ip` and `legacy` (a full-access
  key with no scope list).
- Every key records the client it is for. `client_name` (1 to 120
  characters, such as `claude-code`, `codex`, `beakr-cli`, `sdk-python` or a
  name of your own) is required on `POST /v1/me/api-keys` and
  `POST /v1/organizations/{org_id}/service-accounts/{user_id}/keys`; a request
  without one, or with a blank one, is refused with `422`. Keys created before
  this change show `client_name: "legacy-unknown"`. Clerk OAuth tokens record
  the OAuth client Clerk issued them to, and end-user tokens their minting
  key's client. Desktop device tokens record `beakr-desktop`.
  `X-Beakr-Client` stays a request label: usage and audit `via.client` report
  it when a request sends one (else the credential's client), and `via`
  carries the credential's recorded client separately as `credential_client`.
- `GET /v1/me` adds `mfa` (`enrolled`, `session_verified`, `enroll_url`).
- `GET /v1/me/organization` returns, for an API key or token, the organization the credential is pinned
  to rather than the person's selected organization in the web app.
- Step-up verification: the auth policy's `require_step_up_mfa` defaults to
  `true`, so sensitive actions taken from a web session (creating keys and
  service accounts, deleting a project, changing a role, transferring
  ownership, changing the auth policy) ask for a recent second factor. A person
  with no second factor is refused with `403 mfa_enrollment_required`, carrying
  `error.enroll_url`. Keys and tokens are refused these actions with
  `403 session_required` while the policy requires step-up; in an organization
  that opts out they act within their scopes.
- End-user tokens, for products that give their own users an agent or session
  over Beakr: `POST /v1/auth/end-user-tokens` takes `subject` (your id for the
  user, 1 to 200 characters), `project_ids`, `scopes` and `ttl_seconds`
  (default 900, at most 3600) and returns a `bk_at_` `token` with `token_id`,
  `expires_at`, `subject`, `scopes` and `project_ids`. Call it with a
  service-account key: a web session is refused with
  `403 programmatic_credential_required`, and any other credential with
  `403 minter_not_supported`. The token is limited
  exactly like a key restricted to those projects and scopes, is never wider
  than the credential that minted it (`403 insufficient_scope` for a scope it
  lacks or a project outside its project list, `404 not_found` for a project
  it cannot read, `422 validation_error` for an unknown scope), has no refresh
  token, and stops working when it expires, when its minting key is revoked or
  expires, or when the service account is disabled. An end-user token cannot
  mint end-user tokens, create keys, or list or revoke the minting principal's
  keys. Because every
  end-user token acts as the same minting principal, it reaches only data in
  its projects. It is refused `POST /v1/context`,
  `POST /v1/interview/sendables/links/{link_id}/knowledge` and, for now, agent
  runs (`/v1/runs`), with `403 insufficient_scope`.
  `GET /v1/auth/end-user-tokens` (optionally `?subject=`) lists the live ones
  and `DELETE /v1/auth/end-user-tokens/{token_id}` revokes one; a key limited
  by scope or project sees and revokes only the tokens it minted. Mints and
  revocations are audited with the subject and the minting credential, and
  every audited request an end-user token makes carries `via.subject`. No
  scope is needed to call these operations.
- Project-restricted keys and end-user tokens can now use Knowledge, research
  and files, confined to their projects. A list, search or create must name one
  of the credential's projects (`project_id`, `target_project_id`,
  `new_home_project_id`); leaving it out, naming another project, or naming a
  `label_id` (or, for `GET /v1/artifacts`, `thread_id`, `thread_outputs` or
  `include_artifact_ids`) answers `403 insufficient_scope`. A page, proposal,
  file, file version, upload, bulk batch or folder in another project answers
  `404`. Covered: `GET /v1/knowledge/wiki/search`, `/wiki/pages`,
  `/wiki/pages/by-title`, `/wiki/pages/{page_id}` and its `/revisions` and
  `/sources`; the wiki proposals list, get, create, accept and dismiss;
  `POST /v1/knowledge/research` (the answer draws only on that project's
  Knowledge and files, never on connected services); `GET /v1/artifacts`,
  `GET`/`PATCH`/`DELETE /v1/artifacts/{artifact_id}`, its `/content` and `/move`,
  `GET /v1/artifacts/versions/{version_id}/download-url`; `/v1/uploads` and its
  parts, completion and abort; `/v1/bulk/prepare`, `/complete` and
  `/{batch_id}/status`; and `/v1/folders` (list, create, get, edit, delete,
  resources). Unrestricted keys, tokens and web sessions are unaffected.

### Organization administration

- Enterprise single sign-on state on organizations: `GET /v1/organizations`,
  `GET /v1/organizations/{org_id}` and `GET /v1/me/organization` return
  `sso_required`, `sso_satisfied` (whether the calling session meets the
  requirement; always `true` when none is required), and, when single sign-on
  is required, `enterprise_sso_connection_id` (the identity provider's
  connection id, not a Beakr UUID) and `sso_provider`. A web session that has
  not signed in through a required connection is refused with `403` and
  `detail.error_type: enterprise_sso_required`; keys and tokens are not
  sessions and keep their own scopes, but in an organization that requires
  single sign-on they answer `403 session_required` to every action that needs
  step-up (creating keys or service accounts, deleting a project, changing
  roles or the authentication policy).
- An organization admin revoking a member's credential, and
  `POST /v1/organizations/{org_id}/service-accounts/{account_id}/disable`, need
  an interactive session (and step-up where the organization requires it); keys
  and tokens answer `403 session_required`, except that a key or token may
  revoke itself.
- `GET /v1/me/organization` answers an end user of your product (an external
  user's token, or an end-user token minted for a `subject`) with only the
  organization's branding in `settings`.
- A personal workspace takes no members: adding a member, changing a role,
  transferring ownership, organization-wide invitations, domain rules and access
  codes for a personal organization answer
  `409 personal_org_membership_refused`. Use a team organization for shared
  work; sharing a single resource from a personal workspace still works.

- External users, for products built on Beakr whose customers are not Beakr
  members (`members:admin`; an organization admin or any service account of
  the organization): `POST /v1/organizations/{org_id}/external-users` takes
  `external_id` (your id for the customer, 1 to 200 characters, unique among
  the external users the calling service account created), an optional `name` and `metadata` (an
  object of at most 16 KB) and returns the external user with `user_id` and
  `created`. It is idempotent on `external_id`: a repeat returns the existing
  external user unchanged with `200` and `created: false`. `GET` lists them
  (`?external_id=` filters, cursor-paged), `GET .../{user_id}` reads one,
  `PATCH .../{user_id}` changes `name` or replaces `metadata`, and
  `DELETE .../{user_id}` deletes one: its tokens stop working, its label and
  project memberships end, its memories, profile, conversations, project-less
  threads and runs and interview links are erased, files and threads in
  projects stay, and its `external_id` can be used again. Other callers get
  `403 forbidden`. An external user never signs in, is not listed by
  `GET /v1/organizations/{org_id}/members`, does not count as a seat, cannot be
  given a role, made owner or removed through the member operations
  (`403 forbidden`), and cannot hold API keys or sign-ins of its own.
- A service account sees and manages only the external users it created, as
  minting an end-user token for one already required:
  `GET /v1/organizations/{org_id}/external-users` lists only those, and
  `GET`, `PATCH` and `DELETE .../external-users/{user_id}` answer another
  service account's external user with `404 not_found`, exactly like an unknown
  id. Each service account has its own `external_id` namespace: two service
  accounts may each create an external user with the same `external_id`, and
  each gets its own. An organization admin, or a service account with the admin
  role, sees and manages every external user of the organization and addresses
  them by `user_id`; its `?external_id=` filter can return one external user per
  creating account, told apart by `created_by`.
- Groups are labels: add external users (or people) to a label with
  `POST /v1/labels/{label_id}/members` and attach the label to projects with
  `POST /v1/labels/{label_id}/projects` (viewer access); an external user can
  also be a direct project member (`POST /v1/projects/{project_id}/members`,
  with a role). `/v1/labels` (list, create, get, update, delete), its
  `/members` (list, add, remove) and `/projects` (list, attach, update,
  detach) are now documented.
- `POST /v1/auth/end-user-tokens` also takes `external_user_id` in place of
  `subject` and `project_ids`: the token then acts as that external user, so
  its conversations, runs, memories and interview links are its own and the
  projects it reaches are the ones its labels and project memberships give it.
  The response's `subject` is the external user's `external_id`, `project_ids`
  is empty, and it carries `external_user_id`. Only an organization admin or
  service account can mint one (`403 forbidden`), with a credential not limited
  to projects (`403 insufficient_scope`); `404 not_found` for an unknown or
  deleted external user. Such a token may call only the operations an end
  user's session needs (identity, reading its projects, Knowledge, files,
  research and runs, its own threads, conversations, context, memories and
  profile, and interview links); any other operation answers
  `403 insufficient_scope`. `GET /v1/auth/end-user-tokens` lists the tokens you
  minted for external users too, and `DELETE` revokes them.
- External users never receive organization-wide access: a resource shared
  with the whole organization, organization-level integrations, the
  organization's profile and skills, and the member directory are not visible
  to them. Give them access through labels or direct project membership.
- Label and project member responses (`GET`/`POST /v1/labels/{label_id}/members`,
  `GET`/`POST /v1/projects/{project_id}/members`) carry `principal_kind`
  (`user`, `service_account` or `external_user`) and, for an external user,
  `external_id` (shown to organization admins and service accounts); an
  external user's `email` is null. Label access to a project is viewer access;
  writing to a project needs a direct project membership.

- `/v1/organizations/{org_id}/service-accounts` (`members:admin`).
- `PATCH /v1/organizations/{org_id}` changes the name and settings (a key or
  token setting `phi_mode` is refused with `session_required`).
- Service accounts record their provenance: `GET
  /v1/organizations/{org_id}/service-accounts` returns `created_by`,
  `disabled_at` and `disabled_by` for each, and creating one returns
  `created_by`. A repeated disable keeps the first record.
- Scopes never lift a person's role: a key or token holding `members:admin`,
  `projects:admin`, `integrations:admin` or `audit:read` can do only what its
  person's organization and project role allow, and is refused with
  `403 forbidden` otherwise. `PATCH /v1/organizations/{org_id}/members/{user_id}`
  now answers `403 forbidden` to a caller who is not an owner or admin;
  previously it answered `200` with the membership unchanged.

### Limits

- Programmatic callers are rate limited per credential and per organization:
  every response carries `X-RateLimit-Limit`, `X-RateLimit-Remaining` and
  `X-RateLimit-Reset`, and a spent budget answers `429 rate_limited` with
  `Retry-After`. Web sessions are not counted.
- Agent and workflow run starts answer `429 concurrency_limited` when a key,
  token or organization has its maximum runs in flight.
- `POST /v1/conversations/import` answers `429 storage_budget_exceeded` once an
  organization's daily import bytes are spent.
- `POST /v1/knowledge/research` counts as a run in flight (`429
  concurrency_limited` when the key or organization is full), refuses a `query`
  longer than 4,000 characters (`422 query_too_long`), and is charged when it
  times out or fails as well as when it answers. A whole research response
  stays within the response budget: an answer too long for it is cut at a
  paragraph or sentence end, and `next` says so.
- `POST /v1/knowledge/research` refused for credits answers
  `429 credits_exhausted` ("This organization has run out of research credits
  for today"), with the balance and `resets_at` in `details` and a `fix` that
  says not to retry research and to use Knowledge reads instead. A timeout
  answers `504 research_timeout`, whose `fix` says to retry once with a
  narrower question and then fall back to Knowledge reads. The hosted MCP
  `research` tool returns the same `error_code`, `message` and `fix`.
- `POST /v1/artifacts/read` accepts an `offset` of at most 100,000,000; past the
  end of the text the window is empty. When too many reads are in progress it
  answers `429 concurrency_limited` with `Retry-After`.
- `POST /v1/interview/links` makes at most 50 links per caller in any 24 hours
  (`429 interview_link_limit`).
- An end-user token is rate limited and counted against the run budget as the
  service-account key that minted it, so every token one key mints shares that
  key's limits.

### Projects, files and uploads

- Every operation that takes a file or version id (`GET`, `PATCH` and `DELETE
  /v1/artifacts/{artifact_id}`, its `/content`, `/versions` and `/move`, and
  `GET /v1/artifacts/versions/{version_id}/download-url`) answers one identical
  `404 not_found` for a file that does not exist, one you cannot read, and one
  outside a project-restricted credential's projects. Previously the wording
  differed between those cases.
- New `POST /v1/artifacts/read` ("Read a file's text"; scope `projects:read`,
  part of the documented core) reads a file Beakr holds -- an upload's
  extracted text, or a canvas's markdown -- by any reference a Knowledge read
  hands out: the file id or a version id, a citation token
  (`{{artifact:<uuid>-p7-layout-4}}`), or the file's path as
  `location.display_path` prints it (or a trailing part of it that names one
  file). Add ` › ` (or `#`) and a place (`p. 7`, `slide 3`, `sheet 2, B4:D9`,
  `lines 10-30`, `block 8`) to get the window around that place instead of the
  start; a citation token opens at its own passage. The response names the
  file (`file.id`, `file.version_id`, `file.display_path`), the place
  (`unit.locator`, `unit.found`, `unit.page`), and how to continue
  (`next_offset`, or `next_reference` for the next page). A reference that
  reaches no file you can read answers `404 not_found` whatever the reason; a
  path that names several of your files answers `409 ambiguous_reference`
  listing each one's `id` and `display_path` (only files you can read).
  Connected items (Slack, email, calendar, Jira) are not stored as files: a
  `{{unit:...}}` or `{{external_item:...}}` token answers `400 not_a_file` from
  its type alone; use the source's excerpt and `location.url`. MCP `read_file`
  uses it and accepts the same references.
- `POST /v1/bulk/complete` (each file) and `POST /v1/uploads/{upload_id}/complete`
  accept an optional `relative_path`: the file's path relative to the folder it
  was uploaded from on your machine, when it differs from the upload's `path` (a
  flattened folder upload, or a root folder whose own name `path` leaves out). It
  never places the file; Knowledge reads name the file by it
  (`location.display_path`) and `POST /v1/artifacts/read` accepts it. It must be
  relative and contain no `..` (`422` otherwise).
- Resumable uploads for large files: `POST /v1/uploads`,
  `GET /v1/uploads/{upload_id}`, `POST /v1/uploads/{upload_id}/parts`,
  `POST /v1/uploads/{upload_id}/complete` and `DELETE /v1/uploads/{upload_id}`
  (`uploads:write`; the read is `projects:read`). Parts go straight to storage
  through presigned URLs; reading an open upload lists the parts that arrived,
  so a client resumes with the rest; completing verifies the assembled size and
  answers with the file and its processing job. Each upload chooses whether
  it is compiled into Knowledge with `ingest_to_wiki` (default `true`).
- `POST /v1/uploads/{upload_id}/complete` is a job operation: its `job_id` is
  read by `GET /v1/jobs/{job_id}` as kind `artifact_index` (the file's
  processing, `uploads:write` or `projects:read`), and `status_url` is
  `/v1/jobs/{job_id}`; it was `/v1/bulk/{upload_id}/status`, which is still
  returned as the new `batch_status_url` field. `GET /v1/uploads/{upload_id}`
  reports the same two URLs once the upload is completed. The file's
  `upload.indexed` event is emitted when the job finishes.
- `POST /v1/bulk/complete` takes `ingest_to_wiki` per file, overriding the
  request's, and each created artifact reports its `job_id` and
  `ingest_to_wiki`; each file's `job_id` is readable at `GET /v1/jobs/{job_id}`
  (kind `artifact_index`).

### Knowledge

- `POST /v1/knowledge/research` answers within the response budget every
  Knowledge read keeps. `text` numbers each cited document once (every passage
  of one file shares its `[n]`), and `citations[n-1]` is that document's
  record with a brief `evidence.excerpt`, plus `cited_passages` and one brief
  entry per passage in `excerpts` when several are cited. `sources` keeps only
  the keys the raw `answer` cites, each with its `source_ref`. When the budget
  shortens or leaves out excerpts, `next` says how to read a passage in full.
- Source records (`POST /v1/knowledge/wiki/command` `sources`, and both MCP
  servers' Sources lists) now say where each source lives and where the cited
  passage sits in it: `location.display_path` is a file's path (its uploader's
  recorded path, else its folder path in its project plus its name), a
  connected file's path in its service, or a connected item's place there
  (`#channel`, `subject · from sender · date`, an issue's key and summary);
  `location.unit_locator` is the passage's place (`p. 7`, `slide 3`,
  `sheet 2, B4:D9`, `block 8`, `lines 12-19`, `message <time>`);
  `location.upload_path` is the relative path the uploader sent, when it
  differs. Compact records carry `location.display_path` and each excerpt's
  `unit_locator`. A location is given only for sources you can read; a
  credential restricted to projects sees it only for files in those projects.
  The compact `sections` text names each hit's documents as `file › place` and
  lists each document as `path › place` beside its token, `sources` names each
  passage's place, and the MCP Sources lists lead each document with its path
  and follow each token with ` › ` and its place. `path › place` (for example
  `tax-memo.docx › p. 7`, `#deals › message 2026-03-04 14:02 UTC`) is what
  `read_file` opens and what to cite inline in an answer to a person. Gmail
  threads are located by subject, first sender and date.
- `POST /v1/knowledge/wiki/write` (and the `facts`, `connections`, `findings`
  and `rows/edit` writes), and `POST /v1/knowledge/wiki/proposals` from an API
  key, end-user or external-user token, OAuth access token or device token,
  accept a citation only when it names your own conversation (`source_type`
  `conversation`, `agent_note` or `user_note`, with `source_title` and
  `meta.excerpt`), a file you uploaded, or a source Knowledge already cites on
  a page you can read (within a project-restricted credential's projects).
  Name it by `source_ref`, by `key` (its `{{token}}`), or by the new
  `reference` field (the readable reference a read printed, such as
  `memo.pdf › p. 7` or `#deals › message 2026-03-04 14:02 UTC`); `{{tokens}}`
  written in the prose are held to the same rule. Anything else is refused:
  `outcome.error_code` `citation_not_citeable` (or `citation_needs_excerpt`) on
  the write door, `400` with `detail.code` on the proposals route. A key that
  resolves to nothing was previously staged as an unrecognised source; it is
  now refused. A conversation, agent_note or user_note citation keeps only its
  words (`meta.excerpt`), `source_title` and `stance`; any `key`, `source_id` or
  ids in its `meta` are dropped.
- `POST /v1/knowledge/wiki/command`: `detail: full` now applies to `cat` only;
  every other command ignores it and returns its compact view (with brief
  excerpts). On `cat` it is held to the same response budget as every other
  read: source records that do not fit are left for the next call, which passes
  `source_offset` (named in `next`). `detail` is no longer suggested in the tool
  descriptions.
- Every source a Knowledge read returns now carries a `source_ref` you can
  pass on a later write, including a cited Knowledge page or section
  (`beakr-source:v1:wiki:...`) and a source known only by its citation key
  (`beakr-source:v1:key:...`); previously a cited page carried only its
  `citation_key`. This covers `POST /v1/knowledge/wiki/command`, both MCP
  servers and `POST /v1/knowledge/research` sources.
  Citing a page or section reference on a write cites that section's own
  evidence when the proposal is accepted; a page is never stored as evidence.
- `POST /v1/knowledge/research`: `sources` is documented as what it has always
  been, an object keyed by the citation key the answer's `{{key}}` tokens use.
- `POST /v1/knowledge/research` no longer presents a partial record as
  complete. A question that asks to list, enumerate or count things, or an
  answer drawn from a Knowledge read that was cut off by a limit, gets a
  closing sentence saying the record may be incomplete, unless the answer
  already says so. The new `completeness` object reports `enumeration`,
  `truncated_reads`, `may_be_incomplete` and `note_added`.
- Every Knowledge read result (`/v1/knowledge/wiki/command` and the resources
  above) carries `coverage` and `next`.
- Public source records (Knowledge reads, `POST /v1/knowledge/research`
  `citations`, `GET /v1/runs/{run_id}` `answer.sources` and both MCP servers)
  now name a cited file Beakr holds: `location.file_id` is its id in the Files
  API (`GET /v1/artifacts/{artifact_id}`, MCP `read_file`), and `provider` is
  `upload` when no connected service is behind it. `stance` (`support`,
  `qualifies` or `contradicts`) is carried when the read recorded one.
  `provider`, `stance` and `location.file_id` are now documented on
  `SourceRecord`.
- `POST /v1/knowledge/research` returns the answer ready to show: `text` has
  each cited source as `[1]`, `[2]` ... in order of first use, and `citations`
  lists those sources in that order as public source records (`source_ref`,
  `citation_key`, `name`, `filename`, `location`, `evidence`), so
  `citations[n-1]` is source `[n]`. `answer` keeps the text with its `{{key}}`
  tokens and `sources` is unchanged. A token that names no source is left out
  of `text`. When the agent ends without writing an answer, `answer` says it
  found nothing in the projects you can access instead of being empty, and
  `confidence` is `low`.
- `POST /v1/knowledge/wiki/command`: `sections` and `cat` return a compact view
  by default. `sections` lists each hit's snippet and the documents its section
  cites; for both commands `sources` holds one record per cited document,
  without excerpts, and its new `cited_passages` field says how many of that
  document's passages the result cites. In a `cat`'s text, a run of adjacent
  citation tokens for one document is shortened to its first token; every token
  shown can still be cited on a write. Pass `detail: "full"` for the previous
  response: every cited passage as its own record with its excerpt.
- `POST /v1/knowledge/wiki/command` with `command: "help"` returns the full
  guidance for every read command, or for one with `topic`;
  `POST /v1/knowledge/wiki/write` with `action: "help"` does the same for the
  write actions and stages nothing. The MCP `knowledge_base` and
  `knowledge_base_write` tool descriptions are now a compact guide that points
  there.

### Conversations and context

- **Transcript storage.** Message text from `POST /v1/conversations/import` is
  stored only with `store_full_text: true`. An import with `compound: true`
  holds the unread text for up to three days, releasing it when the capture
  completes or at that deadline; `hold_text: true` holds it the same way
  without queuing a capture (for multi-batch imports compounded afterwards).
  Any other import stores no message text, so messages sent with none of
  `summary`, `store_full_text`, `compound` or `hold_text` are refused with
  `422 summary_required` (a later batch of a conversation already stored with
  a summary needs none). `POST /v1/compound` on a conversation that kept no text answers
  `nothing_to_compound`.
- **ChatGPT exports.** `POST /v1/conversations/import` accepts the client
  `chatgpt-export` (a conversation from a ChatGPT data export), stored and
  listed as `chatgpt`; the `client` filter of `GET /v1/conversations` accepts it
  too.
- **References at import.** `POST /v1/conversations/import` takes `tools_used`
  (`[{name, count}]`: each tool's name and how many times the session has called
  it so far; the larger count per tool is kept), returned as `tools_used` on the conversation. The `index` task
  summary (the summary, else the title), the pages it read (as `entities` and
  `sources_read`) and the files it touched are recorded at import, before any
  compounding.
- **Message authorship.** Each message in `POST /v1/conversations/import`
  takes an optional `author` (`human`, `system`, `agent` or `unknown`, the
  default), returned with the message by `GET /v1/conversations/{conversation_id}`.
  `human` is accepted only on a user message (`422` otherwise). Only `human`
  user turns can be quoted as the person's own words when imported sessions
  feed learning.
- **Context.** `/v1/context` entity teasers now include cards whose whole name
  appears in the question, including multiword and short names.
- **Context members and client.** The `project_profile` block of
  `POST /v1/context` lists the project's members by name and role (no email
  addresses; at most 20 named, the rest counted), as the caller could list
  them.
- `POST /v1/conversations/import`, list, get, delete; `POST /v1/compound`;
  `POST /v1/context`.
- Conversation index: imported sessions gain an `index` (task summary,
  entities, sources read with the revision read, outputs, findings) written
  when they are compounded. `GET /v1/conversations/search?q=` searches your own
  imported sessions and Beakr chats (`match=all|any`).
- `POST /v1/conversations/import` accepts `sources_read` (`page_id` with the
  `revision` read, or `source_ref` with `version`): the pages and documents a
  session read. Search hits carry `evidence` and `sources`.
- `POST /v1/context` gains `prior_conversations` (with a `question`), a
  `workboard` block, and `changes_since` (the person's recent activity).
- **Beakr chats as conversations.** `GET /v1/conversations` lists your own
  Beakr chats beside your imported sessions, newest first on one cursor, and
  `GET /v1/conversations/{conversation_id}` shows one. A chat has `kind:
  ask_thread`, `client: beakr`, its thread id as `session_id`, an `href` to its
  messages and the same `index` as an import: `task_summary`, `entities`,
  `sources_read` (a page with the `revision` read, a document with the
  `version_id` read, any other source by `source_ref` with the `version` read;
  a version is null when the read did not record one), `outputs` (with
  `generated_at`) and `findings`. Imported conversations now say `kind:
  imported`. `include_threads=false` lists imported sessions only, and a
  `client` or `project_id` filter leaves chats out. `GET
  /v1/conversations/search` matches a chat by its index as well as its title,
  and its hits carry the chat's index fields (`summary`, `entities`, `sources`,
  `outputs`, `findings`); `evidence` is `findings` for a chat that read nothing
  but staged findings. The `index` object is documented as the
  `ConversationIndex` schema.

### Agents and runs

- `GET /v1/me/threads` (and the shared-threads list) names each thread's newest
  run you can read in `latest_run_id` and `latest_run_status`, so a run can be
  found for `GET /v1/runs/{run_id}` and `POST /v1/runs/{run_id}/cancel`.
- `POST /v1/runs` takes `files`: up to 10 ids of files you can read (from an
  upload), given to the agent the way files attached in Beakr's chat are. A
  file you cannot read, a deleted file or an id that is not a file fails the
  request with `404 not_found` before any run starts; `files` with a decision
  resume (`approval_decision`, `approval_decisions`, `plan_decision`,
  `is_ask_user_response`) is a `422`. Continuing a thread (`thread_id`) adds its
  files to the thread's earlier ones.
- `GET /v1/runs/{run_id}` returns the run's answer (`RunAnswer`) once it has
  completed: `answer.text` with citations numbered `[1]`, `[2]` ...,
  `answer.raw_text` with its `{{key}}` tokens, and `answer.sources`, the cited sources in order as
  public source records. A run paused on a question for the person (`status`
  `interrupted`, `interrupt_kind` `decision`) returns it in `question` (`text`,
  `options`, `context`); answer it by starting a run on the same thread with the
  answer as `message` and `is_ask_user_response: true`.
- A run started with an end-user token never pauses to ask a question or for an
  approval: the person it acts for sees only the answer your product shows. It
  answers with what it could and could not find instead. Runs started with an
  API key keep the paused states above.

### Interviews

- Each of a sendable interview's `key_questions` (`POST /v1/interview/sendables`)
  is at most 1,000 characters.
- **Interview links in one call.** `POST /v1/interview/links`
  (`interviews:write`, idempotent) takes `title`, `goal` and/or `questions`,
  an optional `recipient` (`name`, `email`; a label only) and an optional
  `expires_at` (5 minutes to 90 days, default 14 days). It answers `link_id`,
  `sendable_id`, `url` (the link to share; omitted on an idempotent replay),
  `expires_at` and `result_url`. Beakr sends nothing and calls no model.
  Refusals: `expires_at_invalid` (422) and `outside_interviews_unavailable`
  (403, PHI organizations). Interview links need a key that is not restricted
  to projects (such as a service account key); a restricted credential gets
  `insufficient_scope` (403). The answers reach a project's Knowledge only when
  you send the finished interview there with the operation below.
- `GET /v1/interview/sendables/links/{link_id}/result` adds `link_id`,
  `completed_at` and `expires_at`, and its `transcript` turns are typed
  (`type`: `agent_question` or `user_answer`, and `text`).
- `POST /v1/interview/sendables/links/{link_id}/knowledge`
  (`interviews:write`) is documented: it puts a finished interview into
  projects' Knowledge as a file each project's ingest compiles. A credential
  restricted to projects may send only to its own projects.
- `GET /v1/interview/sendables/links/{link_id}/result` adds `write_up` (the
  interview's shared write-up) and `knowledge` (what it added to Knowledge),
  and a sendable's links report `has_write_up`. The write-up records who last
  edited it (`updated_by`: participant, sender or interviewer).

### Integrations

- `GET /v1/integrations` (connections with the projects each feeds),
  `POST /v1/integrations/{provider}/connect` (a browser connect link; OAuth
  completes in the browser), `GET /v1/integrations/{connector_id}/status`, and
  `DELETE /v1/integrations/{connector_id}` (`integrations:read` /
  `integrations:admin`).
- **Query connected integrations.** `GET /v1/integrations/tools` lists the
  read-only tools Beakr's agent uses for the integrations the caller has
  connected (Slack, Google Drive, Gmail, Calendar, Outlook, Notion, Jira,
  Confluence, GitHub, Linear, Box, Dropbox, SharePoint, Teams and others),
  each with its `description`, `input_schema` and the `connections` it can
  read; filter with `provider` or `connector_id`.
  `POST /v1/integrations/tools/{tool_name}/call` runs one with
  `{"arguments": {...}, "connector_id": optional}` and answers the tool's
  `content`, the `sources` it read and `is_error`. Both need
  `integrations:read`. Calls stay inside each connection's scope setting, and
  a key limited to projects reaches only the integrations attached to them.
  Tools that change the connected account are refused with
  `403 tool_not_read_only`; other refusals are `404 tool_not_found`,
  `404 integration_not_connected`, `422 invalid_tool_arguments`,
  `422 unsupported_provider` and `502 integration_tool_failed`.
