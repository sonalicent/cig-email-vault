# Design Decisions — Email .eml → text Lambda pipeline

Status: In implementation. Original planning date: 2026-07-27.

Feature: S3-triggered Lambda that converts `.eml` emails under `emails/` in
`be-cig-vault-ds-raw` into per-email + per-attachment Markdown (+ JSON sidecar) files
under `emails-extracted/`.

Format: each decision = Context → Decision → Rationale → Alternatives → Status.

---

## D1. Compute: AWS Lambda (not ECS Fargate)
- Context: Bloom standard stack prefers ECS Fargate for long-running/stateful work.
- Decision: Use Lambda, triggered per new `.eml` file.
- Rationale: Requirement is explicitly event-driven per-file; short-lived, stateless,
  bursty. Well within Lambda limits with tuned memory/timeout.
- Alternatives: Fargate task / batch job (rejected: overkill for per-file event).
- Status: Confirmed (user requirement).

## D2. Packaging: Container image Lambda (ECR)
- Context: Attachment parsers (pdfplumber, python-pptx, openpyxl, python-docx) plus
  transitive deps are large.
- Decision: Package as a container image on `public.ecr.aws/lambda/python:3.12`.
- Rationale: 10GB image limit comfortably fits heavy parsing deps; zip layers hit the
  250MB unzipped limit and are harder to manage.
- Alternatives: Zip + Lambda Layers (rejected: size/limit risk, layer sprawl).
- Status: Confirmed by user.

## D3. Runtime: Python 3.12
- Decision: Python 3.12.
- Rationale: Matches Bloom stack; strongest ecosystem for MIME + document parsing
  (stdlib `email`, pdfplumber, python-pptx, openpyxl, python-docx).
- Status: Confirmed by user.

## D4. Input format: real .eml / MIME
- Context: cigvault samples are `.txt` thread exports; actual inputs differ.
- Decision: Treat inputs as real RFC 822/MIME `.eml` with binary attachments.
- Rationale: Enables stdlib `email` parsing of headers, bodies, and attachment parts;
  the `.txt` samples are only used to model thread-splitting heuristics/fixtures.
- Alternatives: Support mixed .eml/.txt (deferred; not needed now).
- Status: Confirmed by user.

## D5. IaC: AWS CDK (Python)
- Decision: Define Lambda (DockerImageFunction), S3 event notification, IAM, and
  concurrency via AWS CDK in Python under `infra/cdk/`.
- Rationale: Matches Bloom broader standard; single-language repo (Python); good fit
  for DockerImageFunction + S3 notifications.
- Alternatives: AWS SAM (lighter but narrower), Terraform (also standard), code-only
  (rejected: manual wiring not reproducible).
- Status: Confirmed by user. Implemented in `infra/cdk/`.

## D6. Dedup registry: DynamoDB table (SUPERSEDES earlier S3-manifest choice)
- Context: Same email recurs across forwards; must not be re-saved. Original plan used an
  S3 `_manifest.json`; user revised to DynamoDB.
- Decision: Use a **DynamoDB table** keyed by email identity hash; dedup via a
  **conditional PutItem** (`attribute_not_exists(pk)`), first-writer-wins.
- Rationale: Matches Bloom standard (DynamoDB for low-latency lookups); atomic idempotency
  removes the manifest read-modify-write race and the concurrency=1 bottleneck; no
  unbounded growing JSON.
- Alternatives: S3 manifest JSON (superseded); month-partitioned manifest (unnecessary
  with DynamoDB).
- Status: Confirmed by user (revised).

## D7. Concurrency control: none required (DynamoDB conditional writes)
- Context: With DynamoDB atomic conditional writes, parallel invocations are safe.
- Decision: No reserved-concurrency=1 constraint; rely on conditional PutItem for
  idempotency. Lambda concurrency can scale with SQS.
- Rationale: Removes the serialization bottleneck that the S3 manifest required.
- Status: Decided (planning), supersedes prior D7.

## D8. Email identity for dedup
- Context: Only the top MIME message carries a real `Message-ID`; quoted older emails
  in the body do not.
- Decision: Identity = RFC 5322 `Message-ID` when present; otherwise deterministic
  SHA-256 over normalized (From + Date + Subject + normalized body prefix).
- Rationale: Stable, collision-resistant identity that works for both real headers and
  quoted-only emails.
- Alternatives: Subject+Date only (rejected: too weak, collisions); full-body hash
  (rejected: quoting/whitespace variance breaks equality).
- Status: Decided (planning).

## D9. Thread splitting strategy
- Context: A single `.eml` body contains newest email + quoted older emails.
- Decision: Regex-detect quoted header blocks (`From:` followed within a few lines by
  `Sent:`/`Date:`, `To:`, `Subject:`) to segment the body into ordered units. Unit 0 =
  top email (real MIME headers); units 1..n = quoted emails (headers parsed from text).
- Rationale: Matches the observed structure in cigvault samples; deterministic and
  testable via fixtures.
- Alternatives: Third-party thread parsers (rejected for now: added dependency, less
  control); ML-based segmentation (overkill).
- Status: Decided (planning). Flagged as the highest-risk component.

## D10. Body text extraction preference (updated: BS4 for HTML-only)
- Context: Many Outlook emails have NO `text/plain` part (HTML-only). Plain-only parsing
  would yield empty outputs.
- Decision: Prefer non-empty `text/plain`; otherwise convert the `text/html` part to
  Markdown/text using **BeautifulSoup4 (+ markdownify)**.
- Rationale: Guarantees real body content for HTML-only emails.
- Status: Confirmed by user (revised).

## D11. Attachment extraction dispatch
- Decision: Extract text by type — .pdf→pdfplumber, .pptx→python-pptx (slides+notes),
  .xlsx→openpyxl (sheet rows), .docx→python-docx, .txt/.csv→decode. Unknown types are
  skipped and logged.
- Rationale: Covers required formats (pdf/pptx/xlsx) plus common office docs; graceful
  degradation on unsupported types.
- Excluded: OCR of scanned/image PDFs, embedded-image extraction (out of scope now).
- Status: Decided (planning).

## D12. Output layout & naming (updated: date-partitioned, Markdown + JSON)
- Decision:
  - Individual emails → `emails-extracted/content/{yyyy-mm-dd}/{subject-slug}__{hash8}.md`
    (+ `.json` sidecar: headers/metadata + attachment refs).
  - Attachments → `emails-extracted/attachments/{yyyy-mm-dd}/{eml-stem}__{attachment-slug}.md`
    (xlsx also emits `.json` rows).
  - **Mirror input date partitioning** rather than a flat directory. Global dedup
    enforced by DynamoDB (D6), not by flat identity-keyed filenames.
- Rationale: Date partitioning aids browsing/crawling; Markdown preserves structure
  (tables) vs. flat `.txt`; JSON sidecar carries structured metadata for downstream.
- Alternatives: flat `.txt` (superseded); per-thread folders (rejected).
- Status: Confirmed by user (revised).

## D13. Lambda resources
- Decision: Memory ~1024MB, timeout ~120s (tune after profiling).
- Rationale: Headroom for parsing multi-slide/multi-page attachments without OOM/timeout.
- Status: Decided (planning); to be validated during verification.

## D14. IAM: least privilege
- Decision: Grant read on `emails/*` and read/write on `emails-extracted/*` only, within
  `be-cig-vault-ds-raw`.
- Rationale: Principle of least privilege; scope to required prefixes.
- Status: Decided (planning).

## D15. Handle both direct emails and "Fw:" forwards (real input reality)
- Context: Actual `.eml` in `s3://be-cig-vault-ds-raw/emails/<date>/` are Outlook emails
  that may be **direct emails to the inbox OR forwards** — both occur. Forwards have a
  wrapper whose outer body is often near-empty, with real content quoted below.
- Decision: Splitter handles both — a direct email produces exactly one unit; a forward
  produces the wrapper (dropped if empty) plus split-out quoted emails. If no divider/
  header boundary is found, treat the whole body as one email.
- Rationale: Avoids empty "forward" files and works for the simple direct-email case.
- Status: Decided (planning), based on inspecting real files.

## D16. Split on Outlook divider lines + quoted header blocks
- Context: Real forwards separate emails with `________________________________` divider
  lines above quoted `From:/Sent:/To:/Subject:` blocks; `EXTERNAL EMAIL` banners appear.
- Decision: Use both the divider lines and header-block regex as split signals.
  **Preserve `EXTERNAL EMAIL` banners** as a useful external-origin marker (do not strip).
- Rationale: More robust segmentation than header regex alone (refines D9); the banner
  is a meaningful signal that content came from outside Bloom, valuable downstream.
- Status: Decided (planning).

## D17. Recursive input prefix (date subfolders)
- Context: Files are organized under date subfolders (e.g. `emails_eml/2026-07-27/`).
- Decision: Trigger/scan matches `.eml` suffix recursively under the input prefix, not
  just top-level; handle nested keys.
- Rationale: Matches real S3 layout with per-day folders.
- Status: Decided (planning).

## D18. Source filename handling (Outlook EntryIDs)
- Context: `.eml` filenames are opaque Outlook EntryIDs containing URL-unsafe chars
  (`=`, `+`, `/`).
- Decision: Do not reuse raw filenames in output keys; sanitize/slug them, and use the
  wrapper `Message-ID` as the canonical source identifier.
- Rationale: Avoids invalid/awkward S3 keys and ties outputs to a stable source id.
- Status: Decided (planning).

## D19. Inline-image exclusion (refines D11)
- Context: Real emails embed signature/logo images as inline MIME parts
  (`Content-Disposition: inline`, has `Content-ID`, names like `image001.png`,
  `Outlook-*.png`).
- Decision: Extract text only from parts with `Content-Disposition: attachment`; skip
  inline `Content-ID` images entirely.
- Rationale: Prevents junk "attachment" text files from logos/signatures; only real
  documents are processed.
- Status: Decided (planning), confirmed against real files.

## D20. MIME decode via stdlib
- Context: Bodies are quoted-printable with iso-8859-1/utf-8 charsets; nested
  multipart/mixed > related > alternative.
- Decision: Use Python stdlib `email` with `get_content()` / policy=default to decode
  transfer-encoding and charset; prefer text/plain, fall back to HTML (D10).
- Rationale: No extra dependency needed for correct decoding.
- Status: Decided (planning).

## D21. Trigger via S3 → SQS → Lambda with DLQ
- Context: Direct S3→Lambda has no retry/DLQ for poison messages.
- Decision: Route S3 `ObjectCreated` (prefix `emails/`, suffix `.eml`) to an **SQS queue**
  that the Lambda consumes; failures retry then land in a **dead-letter queue**.
- Rationale: Standard-aligned (SQS for decoupling/resilience); safe handling of malformed
  `.eml`; smooths bursts (e.g., backfill).
- Status: Confirmed by user.

## D22. Output format: Markdown + JSON (replaces plain .txt)
- Context: Flat `.txt` loses structure (tables, metadata).
- Decision: Emit each email/attachment as **Markdown** (`.md`) plus a **JSON** sidecar for
  structured metadata; xlsx attachments also emit JSON rows.
- Rationale: Markdown preserves tables and is RAG/human friendly; JSON carries queryable
  metadata.
- Alternatives: plain `.txt` (superseded).
- Status: Confirmed by user.

## D23. Date-partitioned outputs (mirror input)
- Context: A flat `emails-extracted/content/` would accumulate thousands of files.
- Decision: Partition outputs by date to mirror input:
  `emails-extracted/content/{yyyy-mm-dd}/` and `emails-extracted/attachments/{yyyy-mm-dd}/`.
- Rationale: Easier to browse and crawl; bounded prefix sizes.
- Status: Confirmed by user.

## D24. HTML-only body handling via BeautifulSoup4
- Context: Many Outlook emails have no `text/plain` part.
- Decision: Use **BeautifulSoup4 (+ markdownify)** to convert the `text/html` body to
  Markdown/text when plain text is missing/empty (see D10).
- Rationale: Prevents empty outputs for HTML-only emails.
- Status: Confirmed by user.

## D25. Security considerations documented, handled later
- Decision: Record (not yet implement) — SSE-KMS on outputs + scoped KMS perms; untrusted-
  file parsing hardening (max size, time budget, patched libs, XML-entity safety); optional
  in-VPC/no-egress; least-privilege IAM; PII/data-classification handling downstream.
- Rationale: Aligns with "security by design"; deferred per user to a later hardening pass.
- Status: Documented; deferred.

## D26. Observability: CloudWatch custom metrics
- Decision: Emit CloudWatch custom metrics (EMF) — emails processed, individual emails
  written, dedup skips, attachments extracted, extraction failures, DLQ arrivals; add
  structured JSON logs and alarms (DLQ depth, error rate).
- Deferred to productionization: X-Ray, OpenSearch, CodePipeline/CodeBuild, ECR lifecycle.
- Status: Confirmed by user (metrics now; rest deferred).

---

## Open items to revisit at implementation
- Signature/footer stripping: keep minimal initially to avoid dropping content.
- DynamoDB item schema versioning (add a `version`/`schema` attribute for forward-compat).
- Record source-`.eml` key + thread membership in the dedup item for audit.
- Compute revisit: move to Fargate/Batch if large-attachment runs approach Lambda limits.
- Productionization: CI-CD pipeline, X-Ray/OpenSearch, KMS + parser hardening (D25).

## Implementation scope note (this pass)
- This pass implements Phases 0–2 + tests: the Python package (parsing, extraction,
  formatting, dedup, handler) and unit tests with synthetic fixtures.
- Deferred to a later pass: `infra/cdk/` (D5, D21) and `scripts/backfill.py`.
