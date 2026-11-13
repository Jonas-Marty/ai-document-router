# Redesign handover: Inbox, Lab, and field-based naming

Brief for the agent implementing this redesign. It records what the owner decided in a design
interview on 2026-11-13, what was found in the code, and a proposed build order. Read
`CLAUDE.md` first; its rules still hold, with the exceptions listed under "Conflicts with
SPEC.md".

## 1. Why

The app proposes a filename and folder for scanned documents in a Nextcloud watch folder. Two
problems drive this redesign:

1. **Local models give weak filenames.** The owner's GPU (RTX 3060, 12 GB, Ollama, reached
   over Tailscale, not always switched on) produces names like
   `WIR Bank Genossenschaft KONTOERÖFFNUNGSBESTÄTIGUNG 239.953-48 BK007` where the wanted name
   is `2026.05.13 WIR Bank 738.953-48.5800 Kontoeröffnungsbestätigung`. That one example
   contains three separate failures: the pattern was ignored (no date, wrong order), a number
   was misread (239 for 738), and an irrelevant footer code was included (BK007). Hosted
   models at Infomaniak give good results.
2. **The UI mixes daily filing with model exploration.** It is hard to see what ran against
   which model, hard to iterate on instructions/models/context, and the Compare popup loses
   its results on close, shows no progress, and cannot choose which models to test. The queue
   is a 20-item list without search.

## 2. Vocabulary

Use these terms in code, UI and docs.

| Term | Meaning |
|---|---|
| Connection | An OpenAI-compatible endpoint (today's `AiEndpoint`). Any number may exist. Never call one "local" or "hosted" in UI wording; use its name. |
| Configuration | A named recipe: a reading step and a filing step, each bound to a connection + model, with its own prompt and parameters. |
| Default configuration | The one configuration used for new documents. Exactly one, global per user. |
| Reading | Turning a document into text (PDF text layer, Tesseract, or a vision model). Stored and reused. |
| Filing | Extracting the name fields and choosing the folder from a reading. |
| Archive knowledge | What is true of the owner's archive regardless of model: name template, sender short names, known accounts, filing rules, examples. Shared by all configurations. |
| Model prompt | The wording addressed to one model. Owned by a configuration. |
| Test set | A saved list of documents with known correct name and folder. |
| Run | One test set executed against one or more configurations, with stored, scored results. |
| Inbox | The daily-use area: all documents in four groups. |
| Lab | The exploration area: test sets, configurations, runs. |

## 3. Decisions

### Routing and connections

- Local first. Other connections are used for availability and speed, not mainly quality.
- **No automatic fallback.** The per-task fallback chains (`AiTaskStep`, `run_chain` walking
  several steps, the cooldown logic) go away. Fallback is manual only.
- A document waits while a connection its configuration needs is unreachable. Nothing
  switches connection on its own.
- "Analyse with…" lets the owner pick any other configuration for one document, several, or
  all waiting ones. It applies only to those documents.
- **Steps run independently.** Reading runs as soon as the reading connection is reachable
  (text-layer reading needs none); filing runs when the filing connection is reachable. A
  waiting document says what it waits for, e.g. "Read · waiting for Tower to file".
- If a connection drops mid-analysis, the document returns to "To analyse" with the reason.
  No retry elsewhere.
- Every document may be sent to any configured connection; there is no privacy restriction
  per folder or document class.
- Provider-specific values live on the connection: reply timeout, context size limit,
  supported JSON mode, optional price per million tokens (input/output).
- Cost: show an estimate before a paid run (from earlier runs) and the actual cost after,
  computed from reported tokens and the connection's price. Without a price, show tokens only.

### One GPU

- Only one model can be loaded at a time on the local GPU, and switches are slow. All work for
  one connection goes through a single queue that groups by model: all readings with model A,
  then all filings with model B. The poller already batches reading before filing
  (`_read_all` in `backend/app/jobs/poller.py`); keep that property.
- A running Lab run pauses Inbox analysis on the connections it uses until it finishes.
- One to three minutes per document on the local GPU is acceptable.

### Name from fields

The model extracts fields; the app assembles the name. Template (editable in Archive
knowledge):

```
date sender [account] title [period] [number]
```

| Field | Required | Examples |
|---|---|---|
| date | yes | `2026.01.09` (format `YYYY.MM.DD`) |
| sender | yes | `ZKB`, `yuh`, `Rega`, `green`, `WIR Bank` (short name, not the legal name) |
| account | no | `frankly 3a CH33 0070 0114 8034 4224 8`, `738.953-48.5800` (IBAN, account, contract or policy the owner holds with the sender) |
| title | yes | `Abrechnung von Wertschriften`, `Ihre Gönnerschaft 2026`, `Rechnung` (as printed, document's language, normal case) |
| period | no | `01.09.2026 - 30.09.2026`, `May 21 - Juni 21` |
| number | no | `9291491`, `JONVJOLF-0008` (invoice or document number) |

Reference names from the owner's archive:

```
2022.10.07 ZKB frankly 3a CH33 0070 0114 8034 4224 8 Abrechnung von Wertschriften
2026.01.09 ZKB frankly 3a CH33 0070 0114 8034 4224 8 Wertschriften Abrechnung 12
2026.01.09 yuh CH26 0878 1000 2629 4620 0 Coupon Dividend and withholding tax statement as per 31.12.2025
2026.01.08 Rega Ihre Gönnerschaft 2026
2026.09.14 green Rechnung 01.09.2026 - 30.09.2026 9291491
2026.05.21 Lovable Labs Incorporated Invoice May 21 - Juni 21 JONVJOLF-0008
2026.05.13 WIR Bank 738.953-48.5800 Kontoeröffnungsbestätigung
```

- The boundary between sender and account ("ZKB" + "frankly 3a …" versus "ZKB frankly" +
  "3a …") is defined by what the owner confirms, not by a rule.
- The model must leave an optional field empty rather than fill it with something nearby.
- In review, the six fields are edited separately with the assembled name shown live. A
  checkbox allows writing the whole name by hand; such a name is still saved as an example,
  without field values.

### Learning as you go

- **Sender short names** and **accounts** are lists learned from approvals, never entered
  beforehand and never added silently. When an approved document has a sender mapping or
  account not yet known, the app asks ("Remember 738.953-48.5800 for WIR Bank?") and the
  owner may decline.
- Once known, the model is given only the entries that appear in the document text and
  matches against them instead of transcribing digits. A value that nearly matches a known
  account is flagged in review.
- Approved documents (text excerpt → approved fields and folder) are used as examples, within
  a size budget set per configuration.

### Configurations and the Lab

- A configuration contains: reading step (method, connection, model, prompt, parameters);
  filing step (connection, model, strategy, prompt, parameters: temperature, context size,
  output limit); context options (number of examples, maximum folders listed, whether only
  matching senders/accounts are sent). A configuration may mix connections.
- Filing strategy: "one call" (fields and folder together) or "two calls" (fields, then
  folder), same model for both.
- Ship two starting configurations to duplicate: one shaped for a small local model, one for
  a large hosted model. "Duplicate and change one thing" and "Duplicate to another
  connection" are the main ways to iterate.
- Full prompts, system prompt included, are editable per configuration.
- **Readings are stored per document and reading setup** and reused, so changing only the
  filing step never re-reads pages.
- Test sets draw from Done documents and from any already-filed file in the owner's Nextcloud
  folders (its current name and folder are the correct answer; the Lab only reads those
  files).
- A run stores results. Each result is scored per field (date, sender, account, title, period,
  number, folder: match or not) against the correct answer; the owner can overrule a verdict.
  Per configuration, show fields correct, folder correct, time per document, tokens, cost.
- Runs execute in the background with per-document progress and a time estimate.
- "Set as default" makes a configuration the default. On change: documents still in "To
  analyse" use the new default; documents in "Ready for review" keep their proposal. Confirm
  the change and show the last run of both configurations side by side.
- Every proposal records which configuration produced it, how it was read, and how it was
  filed.

### Inbox and Review

- Four groups: **To analyse** (waiting, running, failed), **Ready for review**, **Done**,
  **Trashed**. "Rejected" means trashed. "Later" (today's skip) keeps the document in Ready
  with a mark.
- Inbox layout: tabs for the four groups with counts; a card grid; each card shows a
  thumbnail of the first page, the proposed name (or original filename), folder, a flag when
  something needs a check, the configuration and upload time. A banner summarises what is
  waiting when another tab is open.
- Search over original filename, proposed name and document text. Filters by sender and
  folder. Sort by newest upload (default), document date, sender, needs-a-check.
- Multi-select actions: analyse, "Analyse with…", re-analyse, trash. **Approval stays one
  document at a time.** No bulk approve.
- Connection reachability is visible in the header.
- Review layout: three panes. Left, the Ready-for-review list only (cards as rows, same
  search/filter/sort controls). Middle, the page preview. Right, the six fields, assembled
  name, hand-written-name checkbox, folder, remember prompt, Approve / Later / Trash, and a
  "Produced by" block with Re-analyse, "Re-analyse with…" and "Add to a Lab test set".
- Re-analysing moves the document to "To analyse" and opens the next Ready document. Proposed
  by the designer, not yet confirmed by the owner: field edits are kept until the new proposal
  arrives.
- Done and Trashed replace the History page; revert is available from the card. Activity
  stays as the audit log.
- Desktop is the main device; the phone is for occasional approvals. Everything still has to
  work at phone width (`CLAUDE.md`).

### Navigation

`Inbox · Lab · Archive knowledge · Activity · Settings`. Lab has `Runs · Configurations ·
Test sets`. Settings keeps connections, Nextcloud, folders, OCR. The AI task chains and the
two global instruction boxes move into configurations and archive knowledge.

### Removed

Compare popup (`services/compare.py`, `MethodComparison.tsx`), the re-run model picker
(`RerunDialog.tsx`, `Document.rerun_endpoint_id/_model_name`), the History page, the task
chains, the single free-text name proposal.

## 4. Mockups

Canvas: https://claude.ai/artifact/AvRbfg4FmV461s4AkZQdXw (private to the owner; ask them to
share or export it if you cannot open it). Six screens: Inbox, Review, "Analyse with…" dialog,
Lab run, Lab configurations list, Lab configuration editor. Section 3 describes all of them;
the canvas shows layout. Its numbers, folder names and configuration names are sample data.
Not drawn: Archive knowledge, Test sets, Done/Trashed tabs, multi-select, phone layouts.

## 5. Findings in the current code

About how models are called. Items 1 to 3 are the likeliest causes of weak local results.

1. **No sampling or size parameters are sent.** `_payload` (`backend/app/services/ai.py:495`)
   and `request_completion` send only `model`, `messages`, and sometimes `response_format`.
   No temperature, `max_tokens`, seed or context size.
2. **Probable silent context overflow on Ollama.** The filing prompt carries up to 250
   folders, 8 sample names, instructions and 6000 characters of text; reading adds page
   images. Ollama's OpenAI-compatible endpoint does not take a per-request context size and
   truncates instead of erroring. Not confirmed on the owner's machine: check whether
   `prompt_tokens` in stored usage plateaus at a round number for the local connection, and
   check current Ollama documentation for how to set the context size (native API options or
   a Modelfile) before choosing an approach.
3. **JSON mode order.** `RESPONSE_FORMATS` (`ai.py:468`) tries `json_object` before
   `json_schema`. Schema-constrained output is the stronger option for small models, and the
   folder can be an `enum` of the real folder list.
4. **One call does three jobs** (date, folder, formatted name). `align_date_prefix` in
   `services/naming.py` patches one symptom.
5. **Examples are weak.** `folders.prompt_context` sends the 8 newest filenames from anywhere,
   without their folder or source text.
6. **Document text is last and hard-cut** at 6000 characters (`extraction.MAX_TEXT_CHARS`),
   so it is what truncation removes first.
7. **Reading is heavier than needed.** Vision reading sends 150 DPI PNGs, two pages per
   request, although a survey noted in `extraction.py` found 49 of 52 scans had a usable
   text layer.
8. **Prompts are constants** (`ai.SYSTEM_PROMPT`, `transcribe.SYSTEM_PROMPT`); only an
   appended instruction text is editable.
9. **Thinking models.** Some local models emit reasoning before the answer, which costs time
   and output tokens. Whether and how to disable it per model is unverified; treat it as a
   per-configuration parameter to investigate.

About the UI and data:

- `compare.compare` is one synchronous request running three fixed reading methods against
  the same filing chain; results are returned, not stored (only an audit entry and usage).
- `GET /queue` returns up to 20-100 documents with no search or filter.
- Existing states map onto the four groups: `DocumentStatus` (pending, skipped, moved,
  trashed, failed) and `ProposalStatus` (pending, ready, failed).
- `Document.extracted_markdown` already caches one reading per document; the redesign needs
  one per reading setup.
- `services/progress.py` already reports per-document stage text; reuse it for run progress.
- Page rendering for thumbnails can use `extraction.render_pages` (pypdfium2 is already a
  dependency).

## 6. Conflicts with SPEC.md, and constraints

`SPEC.md` is the source of truth and this redesign contradicts it in several places: §4 data
model, §5 API, §6.2 poller, §6.3 and §6.3a (single proposal call, chains), §6.4a comparison,
§8.1 routes, §8.3-8.4 review, §8.6 history, §8.7 settings, §8.8 queue. **Update `SPEC.md`
for a milestone before building it**, and record smaller choices in `DECISIONS.md`. The owner
has approved the direction in section 3; ask before deviating from it.

Unchanged rules that this work touches:

- Never delete from WebDAV; never overwrite on move; validate every path against
  `allowed_root_folders` in the service layer. The Lab reads archive files only.
- Scope every query by `user_id`: configurations, test sets, runs, results, readings, sender
  and account lists are all owned data.
- The assembled name must still pass `naming.validate_stem` on the backend; the frontend
  mirrors it for feedback.
- Never advance the review list on a failed approve.
- API keys stay write-only. Prices and context limits are not secrets.
- No new runtime dependency without asking. Every schema change gets an Alembic migration.
- A model reply is either fully valid or a readable failure (current SPEC 6.3 principle);
  keep it for field extraction.

## 7. Proposed build order

Each milestone should leave the app working and pass `just check`. Order chosen so the owner
gets better local results early and can measure them.

1. **Call parameters and connection capabilities.** Temperature, output limit and context
   size on every call; per-connection context limit, JSON mode and price; prefer
   `json_schema`; warn before a prompt exceeds the context size instead of letting it be cut.
   Resolve finding 2 first.
2. **Field-based proposals.** Fields in the proposal schema and table, name assembled in
   code from the template, six-field review form with free-name override. Migrate existing
   proposals as free-name.
3. **Configurations replace chains.** Configuration tables, default configuration, per-step
   prompts and parameters, stored readings per reading setup, independent step scheduling,
   waiting states, removal of automatic fallback. Migrate each user's current chains and
   instructions into one starting configuration marked default.
4. **Archive knowledge.** Template editor, sender short names and accounts with the
   remember-on-approve prompt, filing rules, examples from Done within a budget.
5. **Inbox.** Document list endpoint with search, filter, sort and paging; thumbnails; four
   tabs; cards; multi-select; "Analyse with…" with estimate; connection status; Done and
   Trashed with revert; remove the History page.
6. **Review in three panes** with Re-analyse actions and the "Produced by" block.
7. **Lab.** Test sets (from Done and from the archive), background runs on the shared
   per-connection queue with progress, per-field scoring with override, results table,
   "Set as default" with confirmation. Remove Compare and the re-run picker.

## 8. Open points

- Whether field edits survive a re-analyse (see Inbox and Review above): confirm with the
  owner.
- How "needs a check" is decided for sorting and flags (new sender, new account, near-match
  account, missing required field are the cases named so far).
- Matching rules for per-field scoring (exact after trimming and case folding is the simplest
  start; the owner can overrule).
- How a hand-written name in a test set's correct answer is scored when it has no field
  values (suggested: whole-name comparison only).
- Which local models to recommend for 12 GB is deliberately not decided here; the Lab exists
  to answer it.
