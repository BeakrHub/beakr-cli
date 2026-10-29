# Changelog

Releases of `beakr-cli`, the Beakr CLI and MCP server (built on the `beakr-sdk`
Python SDK).
Each release lists the public API changes it supports, taken from the API changelog.

## beakr-cli 0.4.0 (2026-10-25)

### Changes

- `POST /v1/knowledge/research` and the MCP `research` tool no longer name the
  model that answered: `ResearchResult.lead_model` is deprecated and never
  returned, and the MCP tool's `research_usage` no longer carries it. Model and
  provider names are not part of the API. `cost_usd`, what the research cost to
  run in US dollars, stays.
- Both MCP servers, the hosted one and the `beakr` CLI's, add an
  `integrations` tool that reads the caller's connected integrations (Slack,
  Google Drive, Gmail, Jira, Notion, Benchling and the others) with the
  read-only tools of `GET /v1/integrations/tools` and
  `POST /v1/integrations/tools/{tool_name}/call`, under the same rules: the
  `integrations:read` scope, only the caller's own connections and the
  organization's, only the integrations attached to a project-restricted
  credential's projects, each connection's default scope as its limit, and no
  tool that changes data (`tool_not_read_only`). `command: "list"` lists the
  tools one line each, paged with `cursor` and `limit`; `command: "describe"`
  with `tool` gives one tool's full description and input schema;
  `command: "call"` with `tool`, the tool's own options in `arguments` and an
  optional `connector_id` runs it and answers its text followed by a Sources
  list giving each cited item's url and `source_ref`. A failure the tool
  reports itself (the provider refused, the item is outside the connection's
  scope) is an error result with `tool_reported_error`. The tool is in the
  discovery tier: listed by default, and found with `discover_tools` on an
  eager listing.
- `POST /v1/knowledge/wiki/write` and `POST /v1/knowledge/wiki/proposals`: four
  citation fixes for API keys and tokens. A citation named by its `source_ref`
  or `reference` that also carries a `key` of your own, such as
  `{key: "plan", source_ref: ...}`, is cited in the prose by that key: write
  `{{plan}}` and it is stored under the key its source has, as a web citation's
  own key already was. Each card row's `evidence` and `citations` resolve as the
  call's own do, in `add_fact`, `add_connection` and `add_finding` `rows` and in
  the new `rows` of `edit_rows`, so a row's `source_ref` or readable reference
  cites its source instead of being refused or stored as a token that cites
  nothing; a row that names no source still inherits only the call's. A
  `citation_not_citeable` refusal now names the reference you sent, a
  `{{token}}` without its braces (it read `Not citeable: ''`). Evidence that
  names a file Knowledge cites without naming a place in it (`memo.pdf`) cites
  the whole file, keyed `artifact:<version>`, instead of the newest passage of
  it Knowledge cites; name the place (`memo.pdf › p. 7`) to cite that passage.
- `POST /v1/runs` returns a `stream_url` of `GET /v1/runs/{run_id}/events`, the
  documented events path; it named a deprecated path that serves the same
  stream.
- `GET /v1/runs/{run_id}/events`: resuming a finished run with a
  `Last-Event-ID` at or past its last event now ends the stream at once with
  the run's final status; it waited about 30 seconds first.
- `POST /v1/runs` and the other run operations, called with an end-user token
  or a project-limited key, answer `403 insufficient_scope` saying runs are not
  yet available to project-restricted credentials and pointing at
  `POST /v1/knowledge/research`; the message said the operation was not
  addressed by a single project.
- `DELETE /v1/projects/{project_id}/pending-invitations/{invitation_id}`: an
  invitation that does not exist answers `404`; it answered `400`. An invitation
  that cannot be revoked still answers `400`.
- `GET /v1/auth/end-user-tokens` and `DELETE /v1/auth/end-user-tokens/{token_id}`
  called with a personal key or a CLI sign-in answer `403 minter_not_supported`
  with a message about listing and revoking; it said only a service-account key
  can mint.
- `POST /v1/knowledge/documents`: a plain-text or Markdown file uploaded from
  now on keeps its line breaks, so `read` returns its lines as written; such a
  file uploaded earlier read back as one line until it is uploaded again.
- `POST /v1/knowledge/wiki/write`, `POST /v1/knowledge/wiki/proposals` and the
  page and section write operations: an API key or token can cite a web page.
  Send `{source_type: "web", url, source_title, meta: {excerpt}, stance}` in
  `citations` (a section's or the change's) or a card row's `citations`, name
  the URL in a row's `evidence`, or write `(https://...)` or
  `(Title — https://...)` in the prose. Only `http` and `https` URLs are
  accepted; any other is refused with `citation_not_citeable`. Beakr stores the
  citation as you give it and never fetches the page. The fragment and tracking
  parameters (`utm_*`, `fbclid` and similar) are dropped, and one page cited
  several times is one citation, keyed `web:<hash>`. To cite it in the prose by
  a name of your own, give it a `key` such as `web:company-about` and write
  `{{web:company-about}}`; it is stored under the page's one key. The citation
  schema adds `url`. Reads name a web citation `(Title — https://...)` in the
  text, and its source record carries the title, `location.url` and the
  excerpt, as for a web page Ask cited. A markdown link, `[text](https://...)`,
  stays a link.
- `POST /v1/knowledge/wiki/write` and `POST /v1/knowledge/wiki/proposals`: three
  readable references a Knowledge read prints, which were refused with
  `citation_not_citeable`, now resolve. `Page Title › Section` resolves for a
  section whose heading carries a date tag such as `[2026-02-26]`, whether you
  cite the title with or without the tag. A connected file with no drive path
  (a synced Drive or GitHub file) is cited by the name a read prints, such as
  `KG Benchmarking Design.txt › segment 1` or
  `GitHub: org/repo/README.md › para 2`. `Page Title › Section` is stored as a
  citation of that section; it was stored as a citation of the whole page. When
  the page resolves but the section does not, the refusal lists the page's
  section titles.
- `POST /v1/runs`: a caller who can read a project but not save conversations
  to it (a project viewer, or an external user whose label grants the project)
  may now pass that project as `home_project_id`. The run starts with a private
  conversation instead of answering `403`; saving a conversation to a project
  still needs member access.
- `POST /v1/knowledge/documents` is now reachable with a token minted for an
  external user. It covers the projects that user's labels and project
  memberships reach. `coverage` adds `rows_not_searched`: documents in scope
  whose table rows were not searched, only their profile.
- `POST /v1/projects/{project_id}/members` and
  `DELETE /v1/projects/{project_id}/members/{principal_type}/{principal_id}`:
  `principal_type` `service` now names one of the organization's service
  accounts. The member is stored and listed as `principal_type` `user` with
  `principal_kind` `service_account`, and gets the project access its role
  gives; before, it was listed but had no access. `service` with any other id
  is refused with 422 `validation_error` on `principal_id`.
- `POST /v1/knowledge/wiki/write` and `POST /v1/knowledge/wiki/proposals`: a
  file you uploaded can be cited by any reference a documents read
  (`POST /v1/knowledge/documents`) returns for it: its `source_ref`, bare or in
  `{{...}}`, in `evidence` or in `citations[].source_ref`, and its passage key
  (`rag:...`) in `evidence` or `citations[].key`. These were refused with
  `citation_not_citeable`. A `source_ref` in `evidence` that names nothing you
  may cite is now refused with `citation_not_citeable`, naming it, instead of
  being stored as a token that cites nothing.
- `GET /v1/me/threads` adds `notifications`: `only` lists just the
  conversations a notification opened (their `origin` is `notification`), and
  `exclude` leaves them out. Omitted, both are listed, as before. Conversations
  a person started by messaging Beakr (`origin` `slack`, `email` or `text`)
  are never filtered by it. It combines with `q` and `project_ids`, and
  `next_cursor` pages through the filtered list.
- `GET /v1/interview/sendables` and `POST /v1/interview/sendables/{sendable_id}/links`:
  a link's `delivery_channel` can now be `slack`, for a link sent as a Slack
  direct message from the sender's own Slack.
- `GET /v1/skills`, `GET /v1/skills/{skill_id}`, `POST /v1/skills` and
  `POST /v1/me/skills/proposals/{proposal_id}/accept`: a skill's `visibility`
  can be `project`, a personal skill its author shared with the people of one
  project in their organization. `GET /v1/skills` accepts `project` in its
  `visibility` filter. The accepted skill's library entry carries
  `shared_project_id` and `shared_project_name` (null unless it is shared with
  a project, and the name is null when you cannot see that project).
- `POST /v1/runs`: `agent_id` starts the run with an agent, a named helper with
  its own instructions, sources and capabilities, where the organization has
  agents turned on. The thread remembers it, so later runs on the thread that
  name no agent keep it. An agent you cannot use answers `404`.
- `GET /v1/threads/{thread_id}` and thread lists: `agent_id` and `agent_name`
  name the agent the thread talks to, or are null. Thread lists also carry
  `agent_origin`: `chat`, `task` or `trigger` (a schedule or webhook started
  it), or null for a thread with no agent.
- `GET /v1/threads/{thread_id}` messages: `started_by` is set on the first
  message of a thread an agent's schedule or webhook started, with `kind`
  (`schedule` or `webhook`), the trigger's `name`, its `task` text and, for a
  webhook, the `payload` the request sent. Show it as what started the run,
  not as something the person wrote: nobody typed that message. It is null on
  every other message.
- `GET /v1/threads/{thread_id}` messages: `workflow_event` is set on the
  messages a workflow run posts into its thread (started, waiting for an
  answer, waiting for a review, finished, stopped), with the `run_id`, the
  milestone `kind`, and for a wait the step (`node_id`, `step_title`), who is
  asked (`reviewer_ids`) and how it ended (`state`: `waiting`, `approved`,
  `rejected`, `edited` or `answered`, with `decided_by`). A waiting message is
  updated in place when the wait ends; a null `state` on a waiting message
  means the run stopped before anyone decided. `flagged_steps` counts the
  steps that finished with problems on a `finished` message. The message
  `text` stays readable without it, and it is null on every other message.
- `GET /v1/projects/{project_id}/model-refusals/summary`: in
  `failed_by_reason`, an event recorded without a reason is counted under
  `unspecified`.
- `GET /v1/projects/{project_id}/model-refusals/summary` adds
  `failed_by_reason`, counting the events no model recovered by why they
  ended, and `recovered_by_model`, counting the recovered events by the model
  that answered instead. An event's `outcome_reason` (in
  `GET /v1/projects/{project_id}/model-refusals`) may now be
  `no_capable_fallback`: no fallback model could do that step's work, for
  example read a page image or call its tools, so none was used. Refusals in
  background steps (document reading, field cleanup, research and others) are
  now retried on the fallback models and recorded as `recovered` or `failed`
  rather than `skipped`.
- A file a model refused now fails with its own code. In
  `GET /v1/projects/{project_id}/knowledge-summary` (`failed_files`) and
  `GET /v1/projects/{project_id}/knowledge/failed-files`, `error_code` is
  `model_refused` when a model declined the file and no fallback model
  answered; it was `compile_failed`. Its `reason` says a retry is refused
  again unless the model or its fallback changes, and on a self-hosted
  installation also names the settings to change. The file is still sent
  again by `POST /v1/projects/{project_id}/knowledge/retry-failed`. A
  `knowledge_compile` job (`GET /v1/jobs/{job_id}`) that failed this way
  answers `error.code` `model_refused`.
- `GET /v1/me/threads`, `GET /v1/projects/{project_id}/threads` and
  `GET /v1/threads/{thread_id}` return `origin`: null for a
  conversation started in the web app or through the API, or `slack`, `email`
  or `text` for one a person started by messaging Beakr there.
- Model refusals and fallbacks: three new reads, each needing `kb:read` and
  answering `404 not_found` for a project you cannot read.
  `GET /v1/projects/{project_id}/model-refusals/summary` counts the project's
  refusals (a model declined) and fallbacks (another model was tried) over
  `window` (`24h`, `7d` or `30d`, default `7d`), with how they ended
  (`recovered`, `failed`, `skipped`), the number of files affected, and counts
  by stage and by the model that refused, and `fallback_ladder`: the fallback
  models in force, in order, each with whether it is available.
  `GET /v1/projects/{project_id}/model-refusals` lists the events, newest
  first, filtered by `artifact_id`, `outcome` or `stage`; each names the
  `stage`, `event_type` (`refusal` or `fallback`), `refused_model`,
  `refusal_kind` (`stop_reason`, `policy_text`, `empty` or `blocked`),
  `fallback_model`, `outcome`, `outcome_reason` (for example
  `no_available_fallback`) and an `excerpt`: the provider's refusal
  category or explanation, or the model's refusal in words, never the model's
  answer, and often `null`.
  `GET /v1/projects/{project_id}/model-refusals/files` lists the files with
  events in the window, most refusals first. Files in `GET /v1/artifacts`
  and `GET /v1/artifacts/{artifact_id}` carry `model_refusal_count` and
  `compile_needed_fallback`.
- `GET /v1/artifacts` takes a new `knowledge_status` filter. `knowledge_status=waiting_for_person` lists only
  the files held out of Knowledge until a person approves them (with
  `POST /v1/artifacts/{artifact_id}/knowledge/approve`), so a review screen no
  longer pages through every file. Any other value is refused with `422`.
- `POST /v1/artifacts/read` takes `project`, a project id or name: the
  reference then resolves among that project's files only, and a file elsewhere
  answers `404`. `POST /v1/knowledge/documents` now refuses a `project_id` that
  is not a project you can read, where it answered an empty listing. On both, a
  project that names nothing you can read answers `404 not_found`, and the
  message names the closest projects you can read.
- `PUT /v1/knowledge/agent-review` and
  `PUT /v1/knowledge/agent-review/projects/{project_id}` answer `404` with the
  code `feature_disabled` (it was `not_found`) where agent review is not
  available on the installation, as `available: false` on
  `GET /v1/knowledge/agent-review` reports. A caller without the
  `members:admin` scope is refused `403 insufficient_scope` before this.
- `GET /v1/projects/{project_id}/knowledge-summary` adds `incomplete`: files
  whose latest attempt saved only part of its changes to Knowledge
  (`knowledge_status: incomplete`). They were counted in neither `failed` nor
  anything else, so a project could read ready with nothing failed while one
  of its files was incomplete.
  `POST /v1/projects/{project_id}/knowledge/retry-failed` now also resumes
  incomplete files, with or without `artifact_ids`. The `knowledge_status` of
  `GET /v1/artifacts` and `GET /v1/artifacts/{artifact_id}` describes each of
  its values; `created` is the finished state.
- `POST /v1/runs` on a thread whose run is still working, from
  the user who started that run, no longer cancels it when the message is a
  plain follow-up (no files, workflow, pinned skills or decision). The message
  is delivered to the running run instead, and the response sets the new
  `injected_into_run` field to `true`, with that run's `run_id` and
  `stream_url` and the delivered message as `user_message_id`. A run whose
  worker has stopped is still replaced by a new run, as before.
- Agent review, policy writes: `PUT /v1/knowledge/agent-review` and
  `PUT /v1/knowledge/agent-review/projects/{project_id}` merge the fields they
  send; a field left out keeps its value and `null` unsets it. A project
  follows the organization for every field it does not set, and
  `project_override` lists only the project's own fields.
- Agent review, files: the policy on `GET /v1/knowledge/agent-review` and its
  two `PUT`s takes `agent_uploads_need_a_person` (default `false`). With it on,
  a file an API key or token uploads or sends to Knowledge is stored and
  indexed but not compiled:
  `GET /v1/artifacts` and `GET /v1/artifacts/{artifact_id}` report
  `knowledge_status: waiting_for_person`, and
  `POST /v1/artifacts/{artifact_id}/knowledge` answers
  `queue_result: waiting_for_person`. The new
  `POST /v1/artifacts/{artifact_id}/knowledge/approve` compiles it; only a
  signed-in person or a key holding `proposals:accept` may call it (others get
  `403 person_review_required`; a file not waiting gets `409 not_waiting`). It
  is the only way a held file is compiled: asking for it again, or turning the
  setting off, leaves it `waiting_for_person`. With either setting on, an API
  key or token is also refused direct commits to Knowledge, and cannot turn on
  a workflow's schedule (`schedule_enabled`) or an output's knowledge
  auto-sync; a schedule a key set up runs as that key, and so does a schedule
  whose workflow a key last wrote (creating, editing, forking or upgrading a
  workflow with a key carries the key onto the configs that run it). With the files setting
  on, a key's edit of an output holds it (`waiting_for_person`) even when a
  person enrolled it in auto-sync, approving it compiles it without enrolling
  auto-sync, and a key cannot attach, edit or sync a project's connector
  source. An interview write-up sent by a key reports `waiting_for_person`.
  A desktop app's device token is its person's own device: files it syncs are
  that person's and are not held.
- Agent review, proposals: an id in a request that the caller cannot see
  answers `404 not_found`, whatever its project's policy. A citation's
  `source_id` no longer counts as a page the request writes to. Rolling back a
  commit, an activity or a run is gated on the projects it touches, not on
  every project of the organization.
- Agent review: an organization or a project can require a person to accept
  proposals and commit Knowledge. `GET /v1/knowledge/agent-review` reads the
  organization's policy, a project's override (`project_id`) and the
  `effective` one; a project's policy replaces the organization's.
  `PUT /v1/knowledge/agent-review` (organization admins, `members:admin`) and
  `PUT /v1/knowledge/agent-review/projects/{project_id}` (project admins,
  `projects:admin`; `policy: null` follows the organization) change it, and
  answer `403 session_required` to a key or token without `proposals:accept`.
  With `agent_proposals_need_a_person` on, an API key or token without
  `proposals:accept` is refused with `403 person_review_required` when it
  accepts any proposal
  (`POST /v1/knowledge/wiki/proposals/{proposal_id}/accept`) or commits
  Knowledge any other way without an accept (resolving a conflict, editing
  card rows, restoring, reverting or undoing). Keys created without a scope
  list (full access) are refused too. Staging proposals stays open. A
  signed-in person, or a key holding the new `proposals:accept` scope,
  accepts and commits. `proposals:accept` marks a key a person reviews with:
  a key with no scope list never holds it, an OAuth token or an end-user
  token never carries it, and only a signed-in session creates a key with it.
  `GET /v1/knowledge/wiki/proposals` and
  `GET /v1/knowledge/wiki/proposals/{proposal_id}` return
  `needs_person_to_accept`. A person's auto-approve no longer applies in that
  project, except to capture suggestions when the policy sets
  `capture_auto_approve_allowed`.
- `POST /v1/knowledge/wiki/write` with `action: "flag"` returns `proposal.id`,
  the review the flag filed, or the open review it joined, as the other writes
  return the proposal they stage; its text ends with a `Proposal id:` line. On
  findings (`add_finding`, `edit_rows`), an `about` name that is not a page in
  the card's project is stored as plain text instead of a link (quoted when it
  holds a comma, so it reads back as one name), and a resolved
  `supersedes` is stored as the replaced finding's date and opening words. A
  `when` written with one end only, such as `until 2026-04-01`, is still
  refused with `invalid_arguments`; the message now says how to write it.
  A successful write's `outcome.auto_approve` says whether the caller's
  auto-approve setting will apply what it staged (`true`) or it waits for
  review (`false`), and a write that staged a proposal returns its id as a
  top-level `proposal_id` as well as `proposal.id`.
- `GET /v1/knowledge/wiki/proposals` and `GET /v1/knowledge/wiki/proposals/{proposal_id}`:
  a read-only `client` names the client that filed the proposal, as it
  declared itself in `X-Beakr-Client` (for example `sdk-python/0.1.0` or
  `beakr-cli/0.4.0`). `source: "mcp"` is unchanged and means filed through
  `POST /v1/knowledge/wiki/write` by any client.
- The `projects:admin` scope is described as "Create, edit and delete projects;
  manage project members": `DELETE /v1/projects/{project_id}` deletes the
  project permanently, which "archive" did not say. The scope grants the same
  operations as before.
- `POST /v1/knowledge/wiki/command` `rows`: under the default `match`, an
  `attribute` that is a field's exact name returns only that field (Facts,
  Connections and Findings alike), with a line naming the fields it left out
  and their row counts; `match: "similar"` still returns every field whose name holds its
  words.
- `POST /v1/knowledge/wiki/write` and the other Knowledge write routes: a
  citation of a file the caller uploaded, at a place the file does not have (a
  page, slide, sheet, block, paragraph, line or character range past its end,
  such as `notes.md › para 7` in a file of four paragraphs), is refused with
  `citation_not_citeable`, naming the file's extent ("4 paragraphs (para 1 to
  para 4)"). It was staged as a citation of a passage that does not exist.
- Map card rows: a Facts or Connections row's `row_id` names the row with its
  When, so a value a card states at two dates is two rows with two ids. On
  `GET /v1/knowledge/wiki/pages/{page_id}`, `disputed_rows` lists each disputed
  row under its id and under the id it had before. `edit_rows` on
  `POST /v1/knowledge/wiki/write` names one of a value's dated rows with `when`
  in `old_rows`; naming such a value without it is refused, listing its rows. A
  finding `edit_rows` rewrites keeps its `about`, `finding_scope` and
  `supersedes` unless the new row sets them.
- `POST /v1/projects/{project_id}/members`: re-adding an existing member
  without `role` keeps their role (`member` is the default for a new member
  only), and giving the project's last owner another role answers
  `409 last_owner` instead of applying it. The sole owner adding themselves at
  the default role was demoted and could no longer manage members.
- `POST /v1/knowledge/wiki/proposals`: an `edit` patch accepts
  `op: "move_section"` with `section` and an optional `after` (the section to
  place it after; omitted, it moves to the top of the page), as
  `knowledge_base_write` does. It was refused with `422`.
- `GET /v1/bulk/{batch_id}/status`: `summary.duplicates` counts files refused
  because the same file was already uploaded. They are no longer counted in
  `summary.failed`, so a batch whose only refusals are duplicates is
  `completed`, and its `upload_batch` job (`GET /v1/jobs/{job_id}`) succeeds.
  Each such file still reads `status: "index_failed"` with
  `error_code: "duplicate"` and `duplicate_of`. The job's `progress.done` no
  longer counts a skipped file twice.
- Source records (`sources` on `POST /v1/knowledge/wiki/command` reads and
  `POST /v1/knowledge/wiki/write` results): an inline `conversation`,
  `agent_note` or `user_note` citation written with an API credential carries
  `filed_by`: the person's `user_name` (their display name when the record is
  read, absent when they have none), the `agent`, `client` and `run` the
  request declared, the `session` it sent in the new optional
  `X-Beakr-Session` header (the client's own session id, attribution only),
  and a `text` line. It never carries the person's id or email. The
  citation's `meta.filed_by`, which `GET /v1/knowledge/wiki/pages/{page_id}/sources`
  returns, holds the same fields and the person's `user_id`. Browser requests
  may send `X-Beakr-Session`.
- Proposals: a review flag (`POST /v1/knowledge/wiki/write` `flag`) names the
  person and agent who filed it in `filed_by`, and stays visible to every
  project member.
- `GET /v1/knowledge/wiki/pages/by-title`: a title that names no page still
  answers `404 not_found`, and `error.details.suggestions` now lists the closest
  live page titles you can read (empty when none is close).
- `DELETE /v1/projects/{project_id}/members/{principal_type}/{principal_id}` and
  `DELETE /v1/labels/{label_id}/members/{user_id}`: removing the last owner
  answers `409 last_owner` instead of `403`. The caller may manage the members;
  another owner is needed first.
- `DELETE /v1/conversations/{conversation_id}`: also deletes one of the caller's
  own Beakr chats (a conversation of kind `ask_thread`) with its messages. It
  answered `not_found` for them before. Another person's chat still answers
  `not_found`.
- Skills (`skills:read`, `skills:write`) join the documented API.
  `GET /v1/skills` lists every skill you can use or install: your own, your
  organization's, the built-in ones and public skills from anywhere, by name.
  Each skill carries `visibility` (`private`, `org`, `public`), `owner` (`me`,
  `org`, `system`, `anyone`), `origin` (`created`, `mined`, `imported`,
  `system`) and `installed`, and the list filters on each (repeat a parameter
  for more than one value), on `search` and `domain`; `include_content=true`
  adds each skill's `files` and caps a page at 25 skills. Retired skills are
  not listed. `GET /v1/skills/{skill_id}` reads one skill by id or slug, with
  `latest_revision`; a slug resolves to your own skill first, then a built-in,
  then one you installed, then your organization's, and never to a skill from
  another organization you have not installed. Only this read takes a slug.
  `POST /v1/skills` creates a skill from a SKILL.md folder (`files`, path to
  text), answers it as the read does, honours `Idempotency-Key` and answers
  409 when a create running at the same time took the same slug.
  `visibility=public` publishes a skill, every file included, to every Beakr
  organization. `GET` and `PUT /v1/skills/{skill_id}/bundle` read and replace
  its files (`files_sha256` is always present; send it back as
  `expected_sha256`), `GET /v1/skills/{skill_id}/bundle.zip` downloads them as
  a zip, and `DELETE /v1/skills/{skill_id}` deletes a skill you wrote. `POST`
  and `DELETE /v1/me/skills/library/{skill_id}` install and uninstall a skill
  (an uninstalled skill can be installed again), and
  `GET /v1/me/skills/proposals` with `POST .../accept` and `POST .../dismiss`
  review the skills Beakr drafted from your work. A skill you wrote in another
  organization answers 404 here, by id or slug, while it is not installed
  here, and can be installed here only if it was installed here before.
  `marketplace` is not given to a new skill as its slug.
- `GET /v1/interview/sendables`: each link carries who it was sent to and how:
  `recipient_email`, `delivery_channel` (`gmail`, `outlook` or `links`), `sent_at`
  and `delivery_error`. All are null for a link that was not sent.
- Proposal `filed_by.run`: the description says what the label is for: writes
  that share it can be undone together.
- `POST /v1/knowledge/wiki/write` `new`: a `page_type` the project does not
  declare is written as the closest declared type when one answers to that
  name (its key, an alias or its label; `project` is `initiative`), and the
  result says which type was used. A type nothing declared is close to answers
  `invalid_page_type` with the declared types listed. Citations sent in
  `citations` beside `sections` are kept with the sections that use them.
- `POST /v1/knowledge/wiki/write` `mv`: the result names the final title when
  the requested one was taken, and lists the pages whose links still show the
  old title (`[[Old title]]`). Those links keep resolving to the renamed page;
  `find_replace` updates their text.
- `POST /v1/compound/cancel`: stop the queued or running compound of an
  imported conversation (`conversations:write`). A batch already being read
  finishes and its proposals stay; no further batch starts, and the
  conversation's `compound_status` reads `cancelled`. Answers
  `nothing_to_cancel` when no compound is queued or running. `POST /v1/compound`
  resumes from where it stopped.
- `POST /v1/knowledge/wiki/write`: a write that stages a proposal for one
  section or card row (`edit_section`, `add_fact`, `add_connection`,
  `add_finding`, `edit_rows`) answers with `proposal`: its `id` and
  `content_hash`, the value an accept takes back as `reviewed_hash`, and
  `filed_by`. A rejected accept of such a proposal (409) now says why it cannot
  apply.
- `GET /v1/knowledge/wiki/proposals` and `GET /v1/knowledge/wiki/proposals/{proposal_id}`:
  each proposal carries `filed_by`: the person (`user_id`, `user_name`), the
  API `credential`, `client`, `agent` and `run` that filed it, and a `text`
  line such as "filed by Dana Reyes via claude-code/2.1". The
  `POST /v1/knowledge/wiki/command` `proposals` listing prints that line.
  A pending listing's `cursor` (and the `proposals` command's `offset`) now
  reaches every pending proposal: behind 500 proposals that could no longer
  apply, a page used to end the listing early.
- `POST /v1/knowledge/wiki/command` `proposals`: `status` also takes
  `accepted`, `superseded` and `resolved`, besides `pending` (the default) and
  `dismissed`; it is no longer an enum in the schema, and an unknown value
  answers `invalid_arguments` naming every status. `proposals` pages with
  `offset` (and `limit`, at most 200): a page that stops short says which
  `offset` continues it. A superseded proposal is listed with the reason it can no longer
  apply. A pending proposal to a card's Facts, Connections or Findings no longer
  stops applying when the table is re-sorted or another row changes; only a
  change to a row it edits supersedes it, and the first row written to a card
  section the page does not have yet is placed and accepted as it was shown.
- `GET`, `PUT` and `PATCH /v1/projects/{project_id}/knowledge-profile`: read
  and set a project's Knowledge profile, the description, goals, focus areas,
  people, organizations, topics, entity types and extraction guidance that
  steer how Knowledge compiles the project's files. `GET` needs `kb:read`;
  `PUT` (the whole profile; answers 201 when it created the profile, 200 when it
  replaced one) and
  `PATCH` (the fields sent) need `kb:write` and the project's owners. A project
  with no profile answers 404 `knowledge_profile_not_found`. A
  project-restricted credential reaches its own projects' profiles.
- `POST /v1/knowledge/wiki/command` `query`: `GROUP BY value` on `facts`
  counts each value of a cell listing several (`A; B`) in each value's group,
  as `rows` `group_by=value` does; a value repeated in one cell counts once.
- `POST /v1/knowledge/wiki/command` reads (`cat`, `sections`, `grep` and the
  rest) and research answers name a cited file inline by its drive path when
  it is known, `(deals/acme/memo.pdf › p. 7)`, instead of its file name alone,
  in body citations and in each `sections` hit's documents line. The path is
  what `documents` `read` and `POST /v1/artifacts/read` open and what a write
  accepts as a citation `reference`.
- `POST /v1/knowledge/documents` `read`: a path that names no document, whose
  file name is one document's in the same record folder, opens that document,
  and the reply's first line names the path it opened; the same name in other
  folders is suggested. A read of a spreadsheet (.xlsx or .csv) whose indexed
  text is a profile ends with the first 50 rows of each sheet, read from the
  original file.
- `POST /v1/knowledge/wiki/command` `rows`: `entity` takes several titles, as
  a list, `a|b|c`, or `^(a|b)$`, and reads the cards each one names (it was
  read as one title and matched nothing); the reply names any title that
  matched no card, with the closest title, and the `entity` description says
  so. `attribute` with `match=exact` is
  compared by name when `regex` is true. `group_by=value` counts each value of
  a cell listing several (`A; B`) in each value's group. A `page_type` no card
  in scope has names the closest card types. A regex that runs out of time
  says how many rows it read and which filters narrow the read.
- `POST /v1/knowledge/wiki/command` `query`: `JOIN ... ON a.entity = b.entity`
  reads as `SAME_CARD(a)`, and `to_title` is the connection's `to`. A query
  over a scope where only some cards are being reindexed runs, and its first
  line says how many cards are reindexing; only a scope whose every card is
  being reindexed is refused with `index_not_ready`. An empty result for an
  `entity_type` no card in scope has names the closest card types.
- `POST /v1/knowledge/wiki/command` `cat` and `hover`: a reference such as
  `"matter PRJ-118"` opens the page `PRJ-118` names, and when the title given
  after an identifier disagrees with the page it opens, the first line says
  so. Titles suggested on a miss are those led by an identifier sharing its
  first segment. `grep` and `sections` refused for `offset` name `grep`'s
  `after_title` and `after_page_id` (`sections` does not page), and `grep`
  refused for `page` points to `cat`.
- `POST /v1/knowledge/documents` `grep`: a regex with a global inline flag
  after its start, such as `MFN|(?i)most favored`, runs with the flag moved to
  the start (it applies to the whole pattern), and the reply says so. It used
  to be refused.
- `POST /v1/knowledge/documents` `grep` given `query` and no `pattern` uses the
  query as the pattern, and the reply's first line says so (it was refused with
  `pattern is required`). `read` takes a document key carrying the full id, such
  as `artifact:<id>` from a Knowledge source list, with or without braces (it
  was looked up as a title).
- `POST /v1/knowledge/wiki/command` `query` with an `entity_type` and a `field`
  condition, and `rows` with `page_type` and an `attribute` or `field_id`, say
  which cards of that type hold no value for the field, since no filter on the
  field can list them: a line `K of N <type> cards have no <field>: <up to 10
  titles> (open them to check).` The `query` result carries it as `missing`
  (`field`, `page_types`, `missing`, `cards`, `titles`). Only cards you can
  read are counted or named.
- `POST /v1/knowledge/wiki/command` reads `rows` `value` as `query` and `rows`
  `entity_type` as `page_type`, and a `timeline` cursor sent as `offset` as
  `cursor`, instead of answering `400`; the reply's first line names each one
  (for example "Read `entity_type` as page_type (the card type)."). Sent beside
  a different value for that option, it is still refused. `query` reads an
  `offset` beside `sql` or `spec` as the cursor that many rows in (it was
  ignored), said in `Applied:`.
- `POST /v1/knowledge/wiki/command` `cat` and `hover` given a reference that
  names no page but starts with an identifier and a title separator (for
  example `"PRJ-118 — Lease Dispute"`) open the one page that identifier
  names, as its exact title, alias, or the start of its title, and say so on
  the first line. When it names several pages or none, the `page_not_found`
  error lists the titles to retry.
- `POST /v1/knowledge/documents` `ls` with a `glob` lists the matching
  documents, flat, with or without `recursive`. It used to list only the
  folders holding them.
- `POST /v1/knowledge/documents` and `POST /v1/knowledge/wiki/command` accept a
  call that sends every option, as clients using strict function calling do.
  An unset option may be sent as `""`, `0` or `false` on `documents`, and
  `group_by` takes `"none"`; `0` for a count, a limit or a line number means its
  default (it used to be raised to 1, or refused for `end_line`), and `read`
  with `start_line` `0` reads the passage its citation token names. On
  `wiki/command`, `0` for `limit`, `rev`, `rev2`, `matches_per_page` or
  `sources_per_section` means its default, and an option the command does not
  take is accepted when it is `0` or its default (`mode` `text`, `table`
  `facts`, ...). `cat` resolves a page given as a citation token with a missing
  closing brace.
- `POST /v1/knowledge/wiki/command` `query` `sql` reads `SELECT DISTINCT keys`
  as `GROUP BY` those keys, and accepts `LOWER()` and `UPPER()` around a
  condition's column or text (matching is already case-insensitive); the reply's
  `Applied:` line says so, with `adjustment_kinds` `distinct_grouped` and
  `case_function_ignored`. In `LIKE`, `%` and a leading or trailing `_` are
  wildcards: `field LIKE '%ettlement%'` (and `spec` `{"field": {"contains":
  "..."}}`, match `contains`) is every field whose name contains the text, any
  case, each listed in `Resolved:`; `value LIKE 'settle%'` means the value
  contains "settle". A field condition in an OR that matches no field leaves
  that branch out (`adjustment_kinds` `or_branch_dropped`) instead of refusing
  the query.
  `field CONTAINS 'x'` matches field names too (at most 25 fields, the rest
  counted in `Resolved:`); an entity naming no readable card is said in
  `Applied:` (`or_branch_dropped`, `entity_unmatched`). Backticked names,
  `when_end`, `when_date` and `valid_from` (as `when`) are accepted; a
  subquery, `GROUP_CONCAT`, an unsupported JOIN shape, the evidence columns
  and more than 24 conditions are refused with the call that answers each.
  Field-name lookups across a whole project no longer run out of time.
  A `value` text condition alone runs card first; one on too many cards is
  refused at once (`invalid_arguments`) with the narrower call.
- `POST /v1/knowledge/wiki/command` `grep` given a `documents grep` option
  (`path`, `glob`, `document`, ...) is refused with the `documents grep` call
  that takes it.
- `POST /v1/knowledge/wiki/write` `edit_rows`: an `old_rows` entry may give a
  long value by its start, up to a word end or ending in `...`, when exactly one
  row of that field starts that way. A start that several rows share is refused
  with each row's start. Refusals show long values by their start.
- `POST /v1/knowledge/wiki/command` `rows` with `current_only` (and `as_of`):
  rows of one field that one document gives for the same When, or all without a
  When, are a list, and every one of them is current. Only the last such row
  used to be current. A later When, or another document's value, still replaces
  the earlier one.
- `POST /v1/knowledge/research` and `GET /v1/knowledge/wiki/search` no longer
  slow down other requests while they wait on the database. When every
  research worker on a server stays busy for 30 seconds, research answers
  `503 server_busy` with `Retry-After` and is not charged; retry once after
  that wait.
- `POST /v1/knowledge/documents` answers carry their outcome as data. A refused
  call has `error_code` (`invalid_arguments`, `invalid_pattern`,
  `document_not_found`, `timeout`, `scope_unavailable`). A `grep` or `search`
  has `coverage`: `complete`, `reason` (`page`, `time_budget`, `scan_budget`,
  `catalog_cap`), `documents_considered`, `documents_checked`, `total`,
  `resume_after_document`, `next_offset` and `continue_with`, the arguments to
  change in the same call to continue it, as Knowledge reads' `coverage` has. A `grep` whose pattern is made of
  common or short words (for example `Form RW`) no longer times out with no
  answer: it answers within about 20 seconds with the documents it checked,
  marked partial, and how to resume or narrow it. `grep` takes `group_by:
  "folder"` with `folder_depth` (default 2) to count matches per folder with
  `output_mode` `count` or `files_with_matches`. `ls` applies `glob`, which it
  used to ignore.
- `POST /v1/knowledge/wiki/command` answers 400 when the body carries an option
  the command does not take, naming each one, the likely option meant (`rows`
  `value` -> `query` with `attribute`; `pattern` -> `query`; `max_results` ->
  `limit`) and every option the command takes. Such options used to be ignored,
  so a filtered read ran unfiltered. An option sent as `null`, `""`, `[]`, `{}`
  or `false` is still accepted. `query` validates its own input as before.
- `POST /v1/knowledge/wiki/command`: a `rows`, `ls`, `grep`, `sources` or
  `sections` reply cut to fit the response budget is cut between whole rows,
  pages, documents or hits, and its text says what it shows: the range
  (`Showing 1-98 (cut to fit the response budget)`), the NOT ALL notice and the
  continuation (`offset=98`, or `after_title`/`after_page_id`/`after_source_id`
  of the last item shown) all describe the items kept. `coverage.returned` and
  `coverage.next_offset` count what was shown, and the new
  `coverage.continue_with` holds the arguments that continue the read. The
  first `next` step says how to continue. These used to describe the uncut
  window, so the stated offset skipped the rows that were cut.
- `POST /v1/knowledge/wiki/command` `rows` takes `view: "census"`: each row's
  entity and value only (the field's name only when the window holds more than
  one, When only when stated), 300 rows by default (at most 500), with every
  `rows` filter. It is held to the hard response cap rather than the target so
  one field over a few hundred records fits one call. Cite a row as
  `(entity › Facts)`. `group_by` and `include_evidence` are refused with it.
- `POST /v1/knowledge/wiki/command` `grep` in text mode reads a hyphen, a space
  and an en dash alike: `key person` finds `Key-Person`, and
  `patent-infringement` finds `patent infringement`. Regex mode is unchanged.
- The hosted MCP server repairs a call whose shape has one reading (options
  beside `arguments` instead of inside it, `arguments` sent as a JSON string,
  tool-call markup such as `<arg_key>` inside a value, a `knowledge_base`
  `read_file` command, which runs as the `read_file` tool) and says what it
  repaired on the reply's first line. A call with more than one reading is
  refused with `invalid_arguments` and the tool's shape.
- `POST /v1/knowledge/wiki/command`, `POST /v1/knowledge/wiki/write` and
  `POST /v1/knowledge/documents` no longer slow down other requests while one
  of them waits on a long database read. When every reader on a server stays
  busy for 30 seconds they answer `503 server_busy` with `Retry-After`; retry
  the same call once after that wait.
- `POST /v1/knowledge/documents` `search` answers within about 8 seconds when
  the embedding service is slow or failing: it ranks the passages by wording
  alone and says so (the first line reads "ranked by wording only", a notice
  gives the reason, and `results` carry no `match` label), where it used to
  wait for two full attempts or fail. A repeated query reuses its embedding
  for 15 minutes. `grep` `glob` (and `path`) take `{a,b}` alternatives, for
  example `*.{docx,pdf}`, which used to select no document; a comma inside
  braces does not split a comma-separated `glob` list. A `grep` pattern that
  requires two terms with lookaheads, `(?s)(?=.*first)(?=.*second).*` with
  `multiline`, now reads only the passages holding both terms instead of
  scanning every document until the time budget ran out. A `pattern` that
  reads as a file-name glob (`*.docx`) is refused with a pointer to `glob`.
- `POST /v1/knowledge/wiki/command` answers 400 when the body nests the
  command's options under `arguments` or `args`: the options go at the top
  level of the body, next to `command`. They used to be ignored, so the read
  ran without them.
- `POST /v1/knowledge/wiki/command` `query` with `as_of` (`AS OF` in `sql`)
  keeps, for a card whose field has no dated value at all, its current undated
  value instead of leaving the card out; the answer's `Applied:` line (and the
  `query` object's `applied`) says how many cards were answered that way. A
  `spec` sent with its keys at the top level beside `command`, and a SELECT sent
  as `query`, now run, and the answer says how they were read; a guessed
  `{"table": ..., "filters": [...]}` is refused with the `sql` call it most
  likely meant. In a `spec`, `field` also takes `{"equals"|"contains"|"like"|
  "meaning": name}`. Knowledge text that points at a cited file names `read_file`
  to the hosted MCP server and `documents read` to other callers.
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

### CLI and SDK

- **Accept the proposal you reviewed.** `beakr kb proposals show` prints the
  proposal's content hash, and `beakr kb proposals accept --reviewed-hash HASH`
  applies it only if it is unchanged since (a changed proposal exits 5 and
  nothing is applied). At a terminal without `--yes`, accept shows the proposal
  and binds to that read once you confirm. A proposal that changes card table
  rows is not accepted without one of those (exit 2). The MCP server's
  `show_proposal` prints `content_hash`, and `accept_proposal` takes it back as
  `reviewed_hash`.
- **Know when Knowledge is ready.** `beakr projects status -P PROJECT` says
  whether the project's Knowledge is ready, queued or compiling, with counts,
  and lists failed files with their error code and reason;
  `beakr projects retry -P PROJECT [--file FILE_ID]` sends failed files to
  Knowledge again; `beakr upload ... --wait-knowledge` waits for indexing and
  then for the project's Knowledge, printing progress, and exits 1 when an
  uploaded file failed. The MCP server's `list_projects`, like the hosted one,
  names projects whose Knowledge is still building or has failed files.
- **The SDKs moved to their own repositories.** The Python SDK is the
  `beakr-sdk` package (BeakrHub/beakr-python; `import beakr_sdk` is unchanged)
  and `beakr-cli` depends on it instead of shipping it; the TypeScript SDK
  (`@beakr/sdk`) is BeakrHub/beakr-typescript. Their release notes are in those
  repositories.
- **The CLI carries its own private client.** The Python SDK now ships only
  the documented contract. The few undocumented routes the CLI and `beakr mcp`
  call are generated into `beakr_cli._private_api` from the engine's internal
  contract, filtered to a reviewed `operations.allowlist` (67 operations); a
  test fails when a call site and the list disagree.
- **Sign-in through your organization's OAuth issuer.** `beakr auth login`
  opens the issuer's sign-in page (Clerk for the hosted API, named by
  `GET /v1/auth/config`) and completes an authorization-code flow with PKCE on
  a one-time listener at `http://127.0.0.1:<port>/callback`; `--no-browser`
  prints the link. `--device` uses the issuer's device grant when it offers one
  and otherwise exits 3 pointing to `--api-key`. `beakr auth logout` revokes
  the refresh token at the issuer. Beakr's own device flow is gone: sign-ins
  saved by earlier versions ask you to sign in again.
- **Parked.** Local mode and Agent Memory Repos (and the `local` and `export`
  extras), `beakr webhooks`, `beakr events`, `beakr watch`, `beakr export` /
  `beakr import`, `beakr sandbox`, `beakr audit export`,
  `beakr org audit|scim|oauth-clients|retention`,
  `beakr projects trash|restore|purge`, project and organization profile
  claims, and creating, changing, reviewing and syncing project, organization
  and integration skills (and `beakr skills ls --scope`) are parked with the
  engine routes behind them, on the `parked/dev-friendly-full` branch.
  `beakr skills ls`, `sync` and `edit` work on your own library. The
  `org_audit_log` agent tool takes only `limit`, `before` and `cursor`.
- **A smaller, documented surface.** The CLI, both SDKs and `beakr api` now
  cover only the documented API: sign-in and keys, organization members and
  service accounts, external users, labels and end-user tokens, projects,
  files, Knowledge (read, write, proposals, research), runs, interviews,
  conversations and integrations. `beakr --help` lists 101 commands (was 180);
  profile, memory, skills, webhooks, events, export/import, usage/trace,
  workflows, outputs, sandbox, org audit/policy/retention/OAuth/SCIM, org
  invitations, project trash/restore/purge, file versions, integration
  sync/coverage/scope and the REST-backed `kb` history commands (`blame`,
  `links`, `provenance`, `timeline`, `graph`, `stats`, `history`,
  `conflicts`, `undo-run`) are no longer registered. `beakr api` builds one
  command per documented operation (113). The routes behind the removed
  commands still answer for existing scripts and the web app, without a
  stability promise; the hosted and `beakr mcp` tool sets are unchanged.
- **Python SDK.** `Beakr` and `AsyncBeakr` attach only the documented
  resources, and `beakr.api` is generated from the documented contract. The
  code over undocumented routes moved to the private `beakr_sdk._internal`
  package (with `beakr_sdk.webhooks`, now `beakr_sdk._internal.webhooks`);
  it is not exported and not supported for use outside the CLI.
  `interviews.create_link(sendable_id, ...)` is now `interviews.link_for`.
- **TypeScript SDK.** `Beakr` exposes the same documented resources; the
  webhook helpers and the device-flow, usage, events, exports, imports,
  sandbox, profile and workflow resources are no longer exported.
- **Uploads and downloads use documented routes only.** `files.upload` (and
  `uploads.upload` in TypeScript) sends every file through the resumable
  `/v1/uploads` flow, a small file as one part, placing it in a folder with
  `parent_folder_id`; a file with no project goes to your personal project, and
  an empty file is refused. The answer is the upload's completion (`artifact`,
  `upload`, `job_id`, `status_url`) for every size, instead of
  `{artifact, version}` for small files. Downloads read the current version
  from the file and sign it with `GET /v1/artifacts/versions/{version_id}/download-url`,
  now documented. `beakr kb cat --rev` and `beakr kb log` (no page) read through
  the Knowledge door, and `beakr projects show` no longer reads the knowledge
  profile.
- **Many files at once.** `files.upload_many(paths, project_id=, folder_id=,
  wait=)` (TypeScript `uploads.uploadMany`) uploads files and whole directories
  as one `/v1/bulk` batch, keeping subdirectories as folders, with one indexing
  job (`wait=True`, or `jobs.wait(batch)`); `files.batch_status` reads its
  progress, and `bulk_uploads` exposes the batch routes. `beakr files upload`
  is `beakr upload` beside `files ls`; both take several files and folders.
  TypeScript `jobs.wait` takes an uploaded folder item directly.
- **New:** `integrations.tools()` / `call_tool()` (`beakr integrations tools`,
  `beakr integrations call`) query the integrations you have connected the way
  Beakr's agent does; `interviews.create_link(title, goal=..., questions=...)`
  creates an interview and its link in one call, with `link_result` and
  `send_to_projects` (`beakr interviews link create|ls|show|send|revoke`);
  `end_user_tokens.mint|list|revoke` (`beakr end-user-tokens mint|ls|revoke`)
  and `external_users` for building Beakr into a product.
- **`beakr trace row <card> --row <index-or-key>`** traces one value of a card
  (a title or page id; `--table facts|connections|findings`, `--value` to narrow
  a text match, `--project` to scope a title): who introduced and last changed
  it, through which credential, client and agent and in which run, the sources
  it cites with the version read, and its earlier values. The Python SDK adds
  `trace.row(card_id, row=..., table=..., value=...)` on both clients.

- **`interactive` on imports.** The CLI sends `interactive: true` only when the
  repository opted in to learning from sessions (`beakr hooks learning on` or
  `beakr init --learning`, stored as `learn_from_sessions: true`, separate from
  capture consent, off by default, reset on rebinding to another project) and
  the transcript classifies the session as human (Claude Code `entrypoint:
  cli` with a typed prompt; Codex `source` `cli` or `vscode`). Headless runs
  (`claude -p`, the Agent SDK, `codex exec`, MCP-driven and subagent sessions)
  and sessions without markers send `false`; a queued retry never sends more
  than was decided at capture. `beakr conversations import` takes
  `--interactive` (default off), the MCP `import_conversation` tool sends
  `false`, and the Python SDK's `conversations.import_` now defaults
  `interactive` to `false`, matching the API.
- `beakr skills sync` sends a local edit to a project, organization or
  integration skill as a proposal for every caller, owners and admins included
  (who then accept it with `beakr skills accept`); it no longer replaces a
  shared skill's files directly. Edits to your own library are still written
  directly.
- **`--json` on every command.** Every command takes `--json` except `beakr mcp`
  (stdout is the MCP protocol) and `beakr codex` (arguments pass to Codex).
  Long-running commands send progress to stderr under `--json`.
- **No prompts without a terminal.** A command that would ask a question exits
  `2` and names the flag to pass (usually `--yes`) when stdin is not a
  terminal. Declining a confirmation exits `1`.
- **`beakr auth token`** prints the current access token for the configured API
  (`--json` for the expiry and source), refreshing it under the CLI's lock; it
  never prints the refresh token. The TypeScript SDK uses it to reach a CLI
  login kept in the OS keychain or one that has expired.
- **Retries.** Both SDKs retry a create the API replays (it carries an
  `Idempotency-Key`) after `500`, `502`, `503`, `504` or a lost connection, and
  retry safe requests after `500` too.
- **TypeScript SDK credentials.** The client reads the CLI's login (file, or
  through `beakr auth token`) after `apiKey`/`BEAKR_API_KEY`, as the Python SDK
  does, and reads credential files on Node 18.
- **Codex capture notice.** The Beakr block `beakr setup` and the `beakr codex`
  wrapper write into Codex's `AGENTS.md` says whether this repository's sessions
  are captured, and is rewritten when capture is turned on or off or the
  project changes.
- **Conversation import.** `beakr conversations import --client chatgpt-export
  --from <export>` imports a ChatGPT data export, one conversation per session;
  `--full-text/--no-full-text` overrides the per-client setting
  (`beakr conversations full-text`) and leaving both out applies it.
- **`beakr profile apply --file`** sends a batch of claim changes to
  `PATCH /v1/me/profile` (`Profile.apply` in the SDK), all or nothing.
- **Web-app-only commands say so.** `org service-accounts create|key` and
  `org policy set` call routes that need a browser session; their help names the web app page, and against a hosted API they
  are refused with `session_required`.
- `knowledge.stage_row("finding", page, kind=...)` passes the finding's own
  `kind`; `kind` and `page` are positional-only.
- The exact-numbers example refuses `NaN` and `Infinity` cells, naming the file,
  line and column.
- **`Retry-After` as a date.** Both SDKs honor an HTTP-date `Retry-After`
  (IMF-fixdate, RFC 850 and asctime forms) as they do a number of seconds; a
  past date retries at once, a malformed one falls back to normal backoff, and a
  deadline over 60 seconds surfaces the error instead of retrying early.
- **Agent-friendly CLI output.** When stdout is not a terminal, results are one
  tab-separated line per object (id, name, a few `key=value` fields) and a
  `next:` line naming the command to run next, instead of full JSON objects;
  `--json` prints the full objects and `BEAKR_OUTPUT=json|compact|human` forces
  a format. One-time secrets and links are always printed whole.
- **Projects by name.** Every `--project`/`-P` takes a project id, slug or exact
  name, and `--scope` is an alias where the command has no other `--scope`; an
  unknown name is a usage error listing the closest projects.
- **Waiting for indexing.** `beakr jobs show|wait JOB_ID...` and
  `beakr upload --wait`; an upload prints each file's job id.
- **External users and labels in the CLI.** `beakr external-users
  ls|create|show|edit|rm` and `beakr labels ls|create|show|edit|rm`,
  `labels members ls|add|rm`, `labels projects ls|add|rm`.
- **Where a source lives.** `beakr kb sources` lists each cited document once:
  filename, provider and a link or path to the original, or for a file only
  Beakr holds (provider `upload`) its file id and `beakr files download <id>`; `beakr kb cat` ends
  with the same list. `--json`, `knowledge.pages.sources()` (both SDKs),
  `runs.answer` and the MCP server return one public source record shape
  (`beakr_sdk.sources`).
- **Answers without parsing events.** `runs.answer(question, project_id=)`
  (TypeScript `runs.answer({question, projectId})`) returns `{text, sources,
  run_id}` with citations rendered as `[n]`; `runs.stream_text(run_id)`
  (`runs.streamText`) yields the visible text as it streams. For a completed
  run the text and sources are the engine's own (`GET /v1/runs/{id}` `answer`),
  with each Beakr-held file's id filled in from the stream. A failed run
  raises `RunFailedError`; a run that stopped to ask the person something
  raises `RunNeedsInputError` with the run's `question` and `thread_id`.
- **TypeScript SDK reads `BEAKR_API_URL`** after `BEAKR_BASE_URL`, as the Python
  SDK and the CLI do, and its errors name the API host they called.
- **Retired SSO policy fields.** `beakr org policy set --require-sso`,
  `--sso-connection` and `--no-sso-connections`, and `require_sso` /
  `sso_connections` in `org.policy.set` (both SDKs), are removed; the API
  refuses `require_sso`, and enterprise SSO is configured per organization.
- **Readable citations in the stdio MCP server.** `beakr mcp` renders Knowledge
  reads and research answers as the hosted MCP server does: each citation is
  its readable reference in parentheses (`(memo.pdf › p. 7)`), and the Sources
  list is keyed by document with every place cited in it, never by `{{key}}`
  tokens. `beakr research` prints a plain list of cited documents when the
  answer cites by reference, and a numbered one when an engine still numbers it.
- **What a self-hosted installation leaves out, under `--json` too.** A
  command for an area the installation does not provide prints
  `{"error": {"code": "not_provided", "message": "This Beakr installation does
  not provide skills.", ...}}` (exit 4) with `--json`, not the bare 404, and the
  sentence names the area (`organization members` for `beakr members ls`),
  never a route.
- **Knowledge refusals read as refusals.** A refusal the Knowledge door
  answers in its body prints its message and code without "HTTP 200", and one
  of the arguments (`invalid_arguments`, for example a `--supersedes` that names
  no finding) exits 2, as a usage error.
- **Removals.** Piped, `projects members rm` and `labels members rm` print
  `ID<TAB>removed=true|false` on stdout as `members rm` does. `labels members
  rm` and `labels projects rm` take `--yes` and, without a terminal, need it.
- **Box parity in the stdio MCP server.** A result whose coverage names
  `next_offset` or `next_cursor` reports `has_more: true` (as the hosted server
  does). The server instructions name the `skills` tool only where the
  installation lists it. `conversations_search` and `import_conversation` take a
  project name as well as an id, and `import_conversation`'s refusal for an
  import that keeps nothing asks for its own `summary`, not API fields it
  lacks.
- **Membership roles.** `beakr projects members add` sends no role unless
  `--role` is given, so re-adding a member keeps their role (the server makes
  someone new a member). Refusing to demote or remove the last owner
  (`409 last_owner`, exit 5) says to make another member an owner first.
  `--role` takes the API's roles (`owner`, `admin`, `member`, `viewer`) and
  refuses any other before sending; its help named `editor`, which the API
  does not have.
- **The CLI a self-hosted installation bundles.** `beakr version`, `beakr
  update` and the MCP tools `beakr_version` and `update_beakr` no longer say
  "Latest release: unknown (PyPI could not be checked)" there. When the server
  says it is self-hosted (`deployment: "self_hosted"` on `GET /v1/auth/config`)
  and the copy was not installed with uv or pipx, they say it is bundled with
  the installation and is upgraded with it (`install-beakr.py --version <new
  version> upgrade`); `beakr version --json` reports `bundled_with_box: true`.
  Otherwise, with `BEAKR_NO_UPDATE_CHECK` set (a server that sends no
  `deployment`, such as a 0.1.8 box, is unknown), they say "Update checks are
  off (BEAKR_NO_UPDATE_CHECK is set)." (`update_check: "off"`) instead of a PyPI
  line.
  On a self-hosted server, `beakr update` and `update_beakr` with the check off
  name the installation's upgrade instead of "Unset it to look for a newer
  release".
  The server is asked (`GET /v1/auth/config`) only with `BEAKR_NO_UPDATE_CHECK`
  set, and once per command: a hosted install never pays for the request.
- **Areas named by the SDK.** The CLI is pinned to a `beakr-sdk` whose
  `NotProvidedError` names the area a method was after (`members.list` refused
  at its active-organization lookup: "organization members"), and uses that
  area for a lookup refusal when no command names one; every other refusal
  keeps the CLI's own, more precise name (`beakr org audit`: "the organization
  audit log").
- **`--accept` reports the accepted proposal.** `kb propose ... --accept --yes`
  prints the status after the accept (`status=accepted`), not the create's
  `pending`.
- **`beakr-sdk` with `NotProvidedError`.** The CLI is pinned to a `beakr-sdk`
  that raises `NotProvidedError` (a `NotFoundError`) for a route the
  installation lacks, redacts credentials from its errors, sends no role from
  `projects.add_member` unless one is given, keeps `filed_by` on source records
  and counts an upload batch's duplicates apart from its failures; the CLI
  names the area from `NotProvidedError` as before.
- **Cite a conversation in a card row.** `kb add-fact`, `kb add-connection` and
  `kb add-finding` take `--cite-conversation LABEL --excerpt TEXT` (and
  `--source-title`): the row cites `{{conversation:LABEL}}`, declared as an
  inline conversation citation with the words cited, as the MCP tools do.
  `--evidence` is optional with it. `kb rows --to` is described as what it
  returns: the rows pointing at the page.
- **Conversations.** `beakr conversations show` labels a turn the agent host
  wrote `system:` (and an agent's prompt to a subagent `agent:`), not `user:`.
  `conversations import --file` sends `--summary` and `--compound`, and a
  refused import that would keep nothing names those flags and `--full-text`.
- **`kb propose create` takes the page as sections.** `--sections` is the page,
  `[{title, body, citations?}]`, staged through `POST /v1/knowledge/wiki/write`
  `new`: Beakr writes the section markers and ids, and a refusal is the
  engine's, printed once. The door's reply is printed (on stderr when piped):
  auto-approve applying the proposal, citations still owed, a page with a
  matching title, the page type used. Under auto-approve no accept is
  suggested, and `--accept` on a proposal the worker already applied succeeds.
  `--parent` is resolved first: a title that names no page exits 4 with the
  closest titles, as before. With `--content` and one `--sections` entry that
  has no body, the content is that section's body. `--content` alone is staged
  as before. A `--sections` entry without an id is reported once, not again as
  "inline citations missing from sections", and a citation's `stance` may be
  written `supports`, `qualify` or `contradict`.
- **`api knowledge run-wiki-write`.** The project is `-P`/`--project` as well
  as `--scope` (and `-P`/`--project` set an operation's `project_id` across
  `beakr api`, taking a project name or slug as every project option does). `--help` lists each action's fields (`flag`: page, concern,
  reason, details?), and a write that staged a proposal names it on stderr
  (`Proposal: <id>`) after the JSON.
- **`kb rows`.** `--attribute` without `--match` answers the field with exactly
  that name when one has it, and the wider default match only when none does,
  saying so on stderr (`--attribute Enrollment` no longer adds "Enrollment
  target rationale").
  `--view conflicts` lists one card stating different values for a field at
  the same When; `--view fields` and `groups` are the door's other views.
- **`scope` on `wiki_stats`, `wiki_graph` and `documents` (stdio MCP).** These
  tools take the project as `project`; `scope`, as the Knowledge tools name it,
  was dropped and the answer covered every project. It is read as `project`,
  as the hosted server's repair does, and the two given with different values
  are refused.
- **Sources lists name what each source is.** A `kb cat` and the stdio MCP
  server's Knowledge results list the cited documents, conversations and
  notes before the Knowledge pages, count each kind (`1 document, 3
  conversations, 8 Knowledge pages`) and label a conversation or note line
  (`- conversation: ...`), with its excerpt when the server sends one, as the
  hosted server does.
- **Work that waits for a person (agent review).** A refusal with
  `person_review_required` (a project that requires a person to accept
  proposals, commit Knowledge or approve an agent's upload, and a key that is
  not one) exits 6, a new exit code, with its own hint: a person does it in the
  web app, or with a key they created with `proposals:accept`. It applies to
  `kb proposals accept`, `kb propose ... --accept`, the Knowledge write
  commands and `files approve`; `--json` names the code.
- **`beakr files approve FILE_ID`.** Compiles a file an agent's key uploaded
  that waits for a person (`POST /v1/artifacts/{artifact_id}/knowledge/approve`);
  a file not waiting exits 5 with a hint. `files ls` and `files show` show each
  file's Knowledge status (`knowledge=` piped) and, on stderr, which files wait
  and the command that approves one.
- **`beakr kb agent-review show|set`.** `show [-P PROJECT]` prints the agent
  review policy in force and whether the organization or the project sets each
  field; `set --proposals|--uploads|--capture-auto-approve on|off|unset`
  changes the organization's policy, or with `-P` a project's own, and
  `set -P PROJECT --follow-org` drops a project's policy. A credential that may
  not change it (`session_required`) exits 6, naming the `proposals:accept` key
  a person can create. Exit 6 keys on the SDK's `PersonReviewRequiredError`.
  `files approve` is now in the published command tree (it was only in the
  full one).
- **`beakr-sdk` for agent review.** The CLI is pinned to a `beakr-sdk` with
  `files.approve_knowledge`, `knowledge.agent_review` and the typed
  `PersonReviewRequiredError` and `SessionRequiredError`.
- **Auto-approve from the outcome.** `kb propose create` reads
  `outcome.auto_approve` to tell whether the owner's auto-approve applies the
  proposal, and the reply's text only from a server that does not send it.
- **`kb entities` piped.** The count per type (`Types: organization 6  person
  4`) is printed on stderr, as it was on a terminal; stdout stays one line per
  page.

## Unreleased

- `beakr kb query '<json>'` (where enabled for the organization; also `-` for
  stdin or `--file PATH`): structured filters, joins, groups and aggregates over
  the rows of every map card, for what `kb rows` cannot express. The query is a
  JSON spec, never SQL; the reply gives the total, one window, the query in
  plain words and a cursor when more rows follow. The stdio MCP server's guide
  lists it.
- `beakr kb rows` takes `--match` (`exact`, `similar` or `meaning`: how
  `--attribute` finds a field), and its help names what each row carries
  (entity, entity_type, table, field, value, When, sources). The bundled skill
  and the README teach the four ways to read Knowledge (file system, graph,
  database, meaning) and how to combine them.
- `beakr kb rows` takes `--as-of` (each card's value on a date), `--to`,
  `--connected-to` (only cards linked to a page, either way) and `--group-by`,
  and `--order-by amount` or `date` for typed sorts. The MCP `knowledge_base`
  guide says `rows` is a database over every card to use first for every, how
  many, latest, largest and as-of questions.
- `beakr documents search QUERY` (and the MCP `documents` tool's `search`
  command) finds passages of the original documents by meaning, grouped by
  document, each with a source_ref; `--limit`, `--offset`, `--passages`,
  `--path`, `--glob`, `--type` and `--project` (an id or name) narrow it.
- `beakr files download` takes any reference a read hands out (a source
  record's source_ref, a version id, a path) as well as a file id, and signs
  the download in one request. The MCP `documents` tool passes a search's
  `results` through.
- `beakr research` and `beakr kb research` take `--quick`, and the MCP
  `research` tool takes `quick`: a short cited answer within 15 seconds from
  one search of Knowledge and uploaded files, without connected services.
- `beakr api` and the MCP `knowledge_base` read accept `rows` with `view`
  `groups` and a `page_type`: that card type's field names that likely mean
  one field, grouped, with counts and example values.
- The bundled `beakr` skill teaches the three ways into Knowledge (walk the
  graph, query `rows` as one database, read the text), what an empty `rows`
  reply means, and when to propose a fact, connection or finding.
- The MCP server no longer forwards a timeline cursor when it had to cut the
  timeline's text: following it skipped the events the cut dropped. It asks for
  the same window again with a smaller limit instead.
- Preserve API request IDs in MCP error results and readable API error messages,
  including gateway responses that provide only `X-Request-ID`. This makes a
  failed call traceable without retrying it solely to obtain diagnostics.

## Unreleased

- `beakr research` and `beakr kb research` take `--quick`, and the MCP
  `research` tool takes `quick`: a short cited answer within 15 seconds from
  one search of Knowledge and uploaded files, without connected services.
- `beakr api` and the MCP `knowledge_base` read accept `rows` with `view`
  `groups` and a `page_type`: that card type's field names that likely mean
  one field, grouped, with counts and example values.
- The bundled `beakr` skill teaches the three ways into Knowledge (walk the
  graph, query `rows` as one database, read the text), what an empty `rows`
  reply means, and when to propose a fact, connection or finding.
- The MCP server no longer forwards a timeline cursor when it had to cut the
  timeline's text: following it skipped the events the cut dropped. It asks for
  the same window again with a smaller limit instead.
- Preserve API request IDs in MCP error results and readable API error messages,
  including gateway responses that provide only `X-Request-ID`. This makes a
  failed call traceable without retrying it solely to obtain diagnostics.
