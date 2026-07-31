# Data Pipeline Verification Checklist

End-to-end checklist to confirm the email `.eml` → Markdown/JSON pipeline is functioning
as intended. Work top-to-bottom: local code + tests first (cheap, fast feedback), then
the deployed AWS stack (SQS → Lambda → DynamoDB → S3), then real end-to-end runs against
the `be-cig-vault-ds-raw` bucket.

Legend: each item is a checkbox. `[CODE]` = inspect source, `[CMD]` = run a command,
`[AWS]` = check in AWS (CLI or Console), `[FILE]` = download/open an output artifact.

## Conventions used below

- Bucket: `be-cig-vault-ds-raw` (input prefix `emails/`, output prefix `emails-extracted/`).
- Queue: `cig-vault-eml-ingest`; DLQ: `cig-vault-eml-ingest-dlq`.
- DynamoDB table: `cig-vault-email-dedup` (partition key `pk`).
- Lambda: the `EmailProcessorFunction` in stack `CigEmailPipelineStack`.
- Region: set once so every command below is copy-paste ready.

```powershell
$env:AWS_REGION   = "us-west-2"          # match your deploy region
$Bucket           = "be-cig-vault-ds-raw"
$Queue            = "cig-vault-eml-ingest"
$Dlq              = "cig-vault-eml-ingest-dlq"
$Table            = "cig-vault-email-dedup"
$Stack            = "CigEmailPipelineStack"
$TestDate         = "2026-07-27"          # a date partition that has sample .eml files
```

---

## 1. Local code review (static correctness)

- [ ] `[CODE]` [config.py](../src/email_pipeline/config.py) — confirm defaults match the
  deployed resources: `SOURCE_BUCKET=be-cig-vault-ds-raw`, `INPUT_PREFIX=emails/`,
  `OUTPUT_PREFIX=emails-extracted/`, `DEDUP_TABLE=cig-vault-email-dedup`,
  `MAX_ATTACHMENT_BYTES=26214400` (25 MiB). Confirm `content_prefix` →
  `emails-extracted/content/` and `attachments_prefix` → `emails-extracted/attachments/`.
- [ ] `[CODE]` Confirm the env-var names in config.py are byte-for-byte identical to the
  `environment={...}` block in [stack.py](../infra/cdk/stack.py). A silent mismatch means
  the Lambda runs on defaults, not context overrides.
- [ ] `[CODE]` [handler.py](../src/email_pipeline/handler.py) `_iter_object_keys` —
  verify it (a) parses the SQS `body` as JSON, (b) short-circuits on
  `Event == "s3:TestEvent"` (the S3→SQS test ping), (c) URL-decodes the object key with
  `unquote_plus` (Outlook EntryID filenames contain `+`, `/`, `=`).
- [ ] `[CODE]` handler.py `_date_from_key` — a key without a `YYYY-MM-DD` segment falls
  back to `"undated"`. Confirm this is acceptable (outputs land in
  `emails-extracted/content/undated/`).
- [ ] `[CODE]` handler.py `process_object` — the order is: download → parse → split →
  per-unit dedup claim → process attachments only after a wrapper unit is claimed →
  write outputs. Confirm attachment refs are attached **only** to the wrapper unit
  (`unit.is_wrapper`), not to every quoted unit.
- [ ] `[CODE]` handler.py `handler` — confirm per-message `try/except` appends
  `{"itemIdentifier": message_id}` to `batchItemFailures` so only the failing SQS message
  is retried (partial-batch response requires `report_batch_item_failures=True` in CDK).
- [ ] `[CODE]` [dedup.py](../src/email_pipeline/dedup.py) — `claim()` writes item with
  key `pk` and `ConditionExpression="attribute_not_exists(pk)"`. Confirm the attribute
  name `pk` matches the table's partition key in stack.py (it does: `name="pk"`).
- [ ] `[CODE]` dedup.py `compute_identity` — Message-ID is used verbatim when present;
  otherwise `sha256:` of normalized `from+date+subject+body[:2000]`. Confirm normalization
  lowercases and collapses whitespace (so trivial reflow doesn't defeat dedup).
- [ ] `[CODE]` [eml_parser.py](../src/email_pipeline/eml_parser.py) `_extract_body` —
  prefers non-empty `text/plain`, else converts `text/html`. Confirm empty-plain-part
  emails still fall through to HTML.
- [ ] `[CODE]` eml_parser.py `_extract_attachments` — only parts with
  `Content-Disposition: attachment` **and** a filename are kept. Inline signature/logo
  images (`Content-Disposition: inline`, `Content-ID`) must be skipped.
- [ ] `[CODE]` [thread_splitter.py](../src/email_pipeline/thread_splitter.py)
  `_find_boundaries` — a boundary requires a `From:` line followed within 5 lines by a
  `Sent:`/`Date:` line. Confirm this avoids false positives on body text containing
  "From:".
- [ ] `[CODE]` thread_splitter.py — empty wrapper units and empty quoted segments are
  dropped; `EXTERNAL EMAIL` banners set `is_external=True` (preserved, not stripped).
- [ ] `[CODE]` [extractors/__init__.py](../src/email_pipeline/extractors/__init__.py) —
  dispatch table covers `.pdf .pptx .xlsx .docx .txt .csv .log .md`; unknown types return
  `None` (skipped, logged); a raised exception in any extractor is caught and returns
  `None` (graceful degradation, no message failure).
- [ ] `[CODE]` [formatter.py](../src/email_pipeline/formatter.py) `slugify` — Outlook
  EntryIDs / subjects are reduced to `[a-z0-9-]`, capped length, never empty
  (`"untitled"` fallback). Confirms opaque/URL-unsafe filenames never reach S3 keys.
- [ ] `[CODE]` [s3_io.py](../src/email_pipeline/s3_io.py) — `put_markdown` sets
  `text/markdown; charset=utf-8`; `put_json` sets `application/json; charset=utf-8` and
  uses `ensure_ascii=False, default=str`.
- [ ] `[CODE]` [Dockerfile](../Dockerfile) — base is `public.ecr.aws/lambda/python:3.12`;
  `pip install` of the package; `CMD ["email_pipeline.handler.handler"]` matches the
  actual module path.

## 2. Unit tests (local)

- [ ] `[CMD]` Create/activate the venv and install dev deps:
  ```powershell
  cd data-pipeline
  python -m venv .venv
  .\.venv\Scripts\Activate.ps1
  python -m pip install -e ".[dev]"
  ```
- [ ] `[CMD]` Run the full suite and confirm **all tests pass**:
  ```powershell
  pytest -ra
  ```
- [ ] `[CMD]` Confirm each module has coverage by running its file explicitly and
  checking it is non-empty / green:
  ```powershell
  pytest tests/test_thread_splitter.py tests/test_eml_parser.py tests/test_dedup.py `
         tests/test_extractors.py tests/test_formatter.py tests/test_html_to_md.py `
         tests/test_handler.py -v
  ```
- [ ] `[CODE]` [tests/test_thread_splitter.py](../tests/test_thread_splitter.py) — verify
  cases exist for: direct email (1 unit), forward (wrapper + quoted), HTML-only body,
  multi-quote chain, empty wrapper dropped, `EXTERNAL EMAIL` preserved.
- [ ] `[CODE]` [tests/test_dedup.py](../tests/test_dedup.py) — verify the second `claim()`
  of the same identity returns `False` (uses `moto` DynamoDB mock). Confirms
  first-writer-wins.
- [ ] `[CODE]` [tests/test_extractors.py](../tests/test_extractors.py) — verify a tiny
  sample per type (pdf/pptx/xlsx/docx/txt) yields non-empty Markdown, and xlsx yields
  `json_data` rows.
- [ ] `[CODE]` [tests/test_handler.py](../tests/test_handler.py) — verify a synthetic
  SQS→S3 event drives `process_object`, writes to a mocked S3, and a poison message
  produces a `batchItemFailures` entry.
- [ ] `[CMD]` Confirm fixtures are synthetic (no real PII):
  ```powershell
  Get-ChildItem tests/fixtures
  ```

## 3. Local container / handler smoke test

- [ ] `[CMD]` Build the Lambda image locally (validates Dockerfile + deps resolve):
  ```powershell
  docker build -t email-pipeline:local .
  ```
- [ ] `[CMD]` Run the image with the Lambda Runtime Interface Emulator and invoke it with
  a synthetic SQS→S3 event (or use a small Python harness that calls
  `email_pipeline.handler.handler(event, None)` directly against a `moto` S3/DynamoDB).
  Confirm it returns `{"batchItemFailures": []}` for a valid fixture.
- [ ] `[CMD]` Invoke with a deliberately malformed event (body not JSON) and confirm it is
  logged + skipped without crashing the whole batch.

## 4. CDK infrastructure — synth & diff

- [ ] `[CMD]` Synthesize and confirm no errors:
  ```powershell
  cd infra/cdk
  python -m venv .venv
  .\.venv\Scripts\Activate.ps1
  python -m pip install -r requirements.txt
  cdk synth
  ```
- [ ] `[CODE]` In the synthesized template (`cdk.out/CigEmailPipelineStack.template.json`)
  confirm these resources exist: `AWS::DynamoDB::Table`, two `AWS::SQS::Queue` (queue +
  DLQ), `AWS::Lambda::Function` (package type `Image`), an
  `AWS::Lambda::EventSourceMapping` (SQS → Lambda), an SQS resource policy allowing
  `s3.amazonaws.com`, and two `AWS::CloudWatch::Alarm`.
- [ ] `[CODE]` Confirm the Lambda `Environment.Variables` in the template list all six
  vars with expected values.
- [ ] `[CODE]` Confirm the DynamoDB table `DeletionPolicy: Retain` (RemovalPolicy.RETAIN)
  so a stack teardown never drops dedup history.
- [ ] `[CODE]` Confirm the event source mapping has `FunctionResponseTypes:
  ["ReportBatchItemFailures"]`, `BatchSize: 5`, and a batching window.
- [ ] `[CMD]` If the stack is already deployed, run `cdk diff` and confirm there is **no
  unexpected drift** between code and the live stack.

## 5. Deployed AWS resources (post-`cdk deploy`)

### 5a. SQS ingest queue & DLQ
- [ ] `[AWS]` Confirm both queues exist and get their URLs:
  ```powershell
  aws sqs get-queue-url --queue-name $Queue
  aws sqs get-queue-url --queue-name $Dlq
  ```
- [ ] `[AWS]` Confirm the ingest queue's redrive policy points to the DLQ with
  `maxReceiveCount = 5`:
  ```powershell
  $qurl = (aws sqs get-queue-url --queue-name $Queue --query QueueUrl --output text)
  aws sqs get-queue-attributes --queue-url $qurl --attribute-names RedrivePolicy VisibilityTimeout
  ```
- [ ] `[AWS]` Confirm the queue policy allows `s3.amazonaws.com` to `sqs:SendMessage`
  scoped to the source bucket ARN and account (from the `Policy` attribute above / add
  `--attribute-names Policy`).
- [ ] `[AWS]` Confirm `VisibilityTimeout` is comfortably larger than the Lambda timeout
  (stack sets it to `timeout * 6` = 720 s for a 120 s function).

### 5b. Lambda function
- [ ] `[AWS]` Get the function config and confirm memory, timeout, package type, and env
  vars:
  ```powershell
  $fn = (aws cloudformation describe-stack-resources --stack-name $Stack `
        --query "StackResources[?ResourceType=='AWS::Lambda::Function'].PhysicalResourceId" --output text)
  aws lambda get-function-configuration --function-name $fn `
        --query "{Mem:MemorySize,Timeout:Timeout,Pkg:PackageType,Env:Environment.Variables,Arch:Architectures}"
  ```
- [ ] `[AWS]` Confirm `MemorySize ~ 1024`, `Timeout ~ 120`, `PackageType = Image`,
  `Architectures = [x86_64]`, and all six env vars present with correct values.
- [ ] `[AWS]` Confirm the SQS event source mapping is **Enabled** and bound to the ingest
  queue:
  ```powershell
  aws lambda list-event-source-mappings --function-name $fn `
        --query "EventSourceMappings[].{State:State,Batch:BatchSize,Src:EventSourceArn,Resp:FunctionResponseTypes}"
  ```

### 5c. Lambda IAM role (least privilege)
- [ ] `[AWS]` Confirm the execution role grants only: `s3:GetObject` on `emails/*`,
  `s3:PutObject` on `emails-extracted/*`, DynamoDB item ops on the dedup table, and SQS
  consume on the ingest queue — nothing bucket-wide or `*`:
  ```powershell
  $role = (aws lambda get-function-configuration --function-name $fn --query Role --output text).Split('/')[-1]
  aws iam list-role-policies --role-name $role
  aws iam get-role-policy --role-name $role --policy-name <inline-policy-name>
  ```
- [ ] `[AWS]` Confirm the S3 read statement resource ends with `emails/*` and the write
  statement resource ends with `emails-extracted/*` (no cross-prefix write access).

### 5d. DynamoDB dedup table
- [ ] `[AWS]` Confirm the table exists, is `ACTIVE`, `PAY_PER_REQUEST`, PK `pk` (String):
  ```powershell
  aws dynamodb describe-table --table-name $Table `
        --query "Table.{Status:TableStatus,Billing:BillingModeSummary.BillingMode,Keys:KeySchema,Attrs:AttributeDefinitions}"
  ```

### 5e. S3 → SQS event notification
- [ ] `[AWS]` Confirm the bucket has a queue notification for `s3:ObjectCreated:*` filtered
  to prefix `emails/` and suffix `.eml`, targeting the ingest queue ARN:
  ```powershell
  aws s3api get-bucket-notification-configuration --bucket $Bucket
  ```
- [ ] `[AWS]` Verify the filter does **not** accidentally include the `emails-extracted/` output
  prefix (which would create a processing loop).

### 5f. CloudWatch alarms
- [ ] `[AWS]` Confirm both alarms exist and are in `OK` (not `INSUFFICIENT_DATA` after
  data has flowed):
  ```powershell
  aws cloudwatch describe-alarms --alarm-name-prefix $Stack `
        --query "MetricAlarms[].{Name:AlarmName,State:StateValue,Metric:MetricName,Threshold:Threshold}"
  ```
- [ ] `[AWS]` Confirm one alarm watches DLQ `ApproximateNumberOfMessagesVisible > 0` and
  the other watches Lambda `Errors > 0`.

## 6. End-to-end functional tests

### 6a. Happy path — new email produces outputs
- [ ] `[CMD]` Pick a sample `.eml` (local mirror in
  `cigvault-samples/emails_eml/2026-07-27/`) and upload it to a **fresh** key under the
  input prefix so it triggers the notification:
  ```powershell
  $src = (Get-ChildItem "..\..\cigvault-samples\emails_eml\$TestDate\*.eml" | Select-Object -First 1).FullName
  aws s3 cp $src "s3://$Bucket/emails/$TestDate/verify-e2e-01.eml"
  ```
- [ ] `[AWS]` Within a minute, confirm the Lambda ran: check its latest log group for a
  new invocation and an EMF line containing `EmailsProcessed`:
  ```powershell
  aws logs tail "/aws/lambda/$fn" --since 5m --follow
  ```
- [ ] `[AWS]` Confirm the SQS queue drained back to 0 in-flight/visible (no stuck message):
  ```powershell
  aws sqs get-queue-attributes --queue-url $qurl `
        --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
  ```
- [ ] `[FILE]` Confirm content output objects were written under the mirrored date
  partition:
  ```powershell
  aws s3 ls "s3://$Bucket/emails-extracted/content/$TestDate/" --recursive
  ```
- [ ] `[FILE]` Confirm each email produced a `.md` **and** a matching `.json` sidecar
  (same stem). Download one pair and inspect:
  ```powershell
  aws s3 cp "s3://$Bucket/emails-extracted/content/$TestDate/<name>.md"   .\out.md
  aws s3 cp "s3://$Bucket/emails-extracted/content/$TestDate/<name>.json" .\out.json
  ```

### 6b. Output content validation (open the downloaded files)
- [ ] `[FILE]` `out.md` starts with `# <subject>`, followed by a header block
  (`**From:** … **To:** … **Date:** …`), a `---` rule, then the body. No raw HTML tags,
  no quoted-printable artifacts (`=20`, `=3D`), no base64 blobs.
- [ ] `[FILE]` If the source was an external email, `out.md` contains the
  `> **EXTERNAL EMAIL**` marker.
- [ ] `[FILE]` `out.json` is valid JSON with `schema_version`, `identity`, `message_id`,
  `source_key` (the input key), `output_key` (this md key), `subject`, `from/to/cc/date`,
  and an `attachments` array. Validate:
  ```powershell
  Get-Content .\out.json -Raw | ConvertFrom-Json | Format-List
  ```
- [ ] `[FILE]` `source_key` in the JSON matches the exact input object you uploaded.

### 6c. Forwarded / threaded email split
- [ ] `[CMD]` Upload a known **forwarded** sample (one containing an Outlook divider +
  quoted `From:/Sent:/To:/Subject:` block).
- [ ] `[FILE]` Confirm it produced **multiple** content `.md` files (one per real email in
  the thread), each with its own header block, and that an empty forward wrapper did
  **not** create a near-empty noise file.

### 6d. Attachment extraction
- [ ] `[CMD]` Upload a sample `.eml` that carries a real attachment
  (`Content-Disposition: attachment`), e.g. a PDF/XLSX/PPTX/DOCX.
- [ ] `[FILE]` Confirm an attachment output exists under the attachments prefix:
  ```powershell
  aws s3 ls "s3://$Bucket/emails-extracted/attachments/$TestDate/" --recursive
  ```
- [ ] `[FILE]` Download the attachment `.md`; confirm it begins with
  `# Attachment: <filename>`, lists source email + content type, and contains real
  extracted text (PDF page headers `## Page N`, pptx `## Slide N`, xlsx/docx Markdown
  tables).
- [ ] `[FILE]` For an **xlsx** attachment, confirm the `.json` sidecar contains a
  `data.sheets[].rows` array (structured tabular data), not just Markdown.
- [ ] `[FILE]` Confirm the corresponding email's `out.json` `attachments[]` array
  references this attachment's `output_key` and `filename`.
- [ ] `[FILE]` Upload an email whose only images are **inline signature logos**
  (`image001.png`, `Outlook-*.png`, `Content-Disposition: inline`). Confirm **no**
  attachment output is produced for them.

### 6e. Deduplication (idempotency)
- [ ] `[AWS]` Note the current item count in the dedup table:
  ```powershell
  aws dynamodb scan --table-name $Table --select COUNT --query Count
  ```
- [ ] `[CMD]` Re-upload the **same** email content under a *different* input key (simulates
  the same email arriving again / inside a later forward):
  ```powershell
  aws s3 cp $src "s3://$Bucket/emails/$TestDate/verify-e2e-01-again.eml"
  ```
- [ ] `[AWS]` Confirm the Lambda logged a `DedupSkips` metric and the content `.md` was
  **not** rewritten (check `LastModified` unchanged):
  ```powershell
  aws s3api head-object --bucket $Bucket --key "emails-extracted/content/$TestDate/<name>.md" --query LastModified
  ```
- [ ] `[AWS]` Confirm the dedup table item count did **not** increase for already-seen
  identities.
- [ ] `[AWS]` Inspect one dedup item and confirm attributes `pk`, `created_at`,
  `schema_version`, `source_key`, `output_key`, `subject`, `date`:
  ```powershell
  aws dynamodb scan --table-name $Table --max-items 1
  ```

### 6f. Poison message → DLQ
- [ ] `[CMD]` Upload an object that will reliably fail processing (e.g. a `.eml` key that
  the Lambda can read but that raises during parse — or temporarily point the function at
  a non-existent object by deleting it right after upload). Prefer a crafted corrupt
  `.eml` so the failure is in `process_object`, not S3 access.
- [ ] `[AWS]` Confirm the message is retried up to `maxReceiveCount = 5` then lands in the
  DLQ:
  ```powershell
  $dlqurl = (aws sqs get-queue-url --queue-name $Dlq --query QueueUrl --output text)
  aws sqs get-queue-attributes --queue-url $dlqurl --attribute-names ApproximateNumberOfMessages
  ```
- [ ] `[AWS]` Confirm the **DLQ depth alarm** transitions to `ALARM`.
- [ ] `[AWS]` Confirm the Lambda **Errors** alarm transitions to `ALARM`.
- [ ] `[CMD]` After verifying, purge the DLQ so alarms clear:
  ```powershell
  aws sqs purge-queue --queue-url $dlqurl
  ```

## 7. Observability

- [ ] `[AWS]` In CloudWatch Metrics namespace `CigVault/EmailPipeline`, confirm these
  custom metrics are populated after a run: `EmailsProcessed`, `IndividualEmailsWritten`,
  `DedupSkips`, `AttachmentsExtracted`, `ExtractionFailures`.
- [ ] `[AWS]` Confirm the counts are internally consistent for a batch, e.g.
  `IndividualEmailsWritten + DedupSkips` reconciles with the number of email units across
  processed objects.
- [ ] `[AWS]` Confirm Lambda logs are structured/JSON and include a per-object line for
  each processed key; failures show a full stack trace (`logger.exception`).
- [ ] `[AWS]` Confirm no `ExtractionFailures` spikes for normal traffic (oversize/corrupt
  attachments are the only expected source).

## 8. Edge cases & security

- [ ] `[CMD]` Upload an email with an oversized attachment (> 25 MiB). Confirm it is
  **skipped** with a warning + `ExtractionFailures` increment, and the email itself still
  produces content output (no crash).
- [ ] `[CMD]` Upload an email with an **unsupported** attachment type (e.g. `.zip`,
  `.dwg`). Confirm it is skipped + logged, no attachment output, no message failure.
- [ ] `[CMD]` Upload an HTML-only email (no `text/plain` part). Confirm the content `.md`
  has real converted text (not empty), with scripts/styles/images stripped.
- [ ] `[CMD]` Upload an email whose subject/filename contains Outlook EntryID characters
  (`+ / =`) or unicode. Confirm the output S3 key is a clean slug (`[a-z0-9-]`) and no
  `NoSuchKey`/invalid-key errors occur.
- [ ] `[CMD]` Send the S3 test event (`{"Event":"s3:TestEvent"}`) or rely on the one S3
  emits at notification setup. Confirm the handler ignores it (no error, no output).
- [ ] `[AWS]` Confirm objects are encrypted at rest (bucket default SSE or SSE-KMS) and
  that the Lambda role has the matching KMS permissions if SSE-KMS is enabled (per the
  deferred security-hardening note — verify current state, flag if missing).
- [ ] `[CODE]` Confirm no secrets/credentials are hard-coded; all config is env-driven.

## 9. Backfill (existing pre-notification objects)

- [ ] `[CODE]` Note: `scripts/backfill.py` is **deferred / not yet implemented**
  (README "Deferred" section). Existing `emails/<date>/*.eml` uploaded *before* the S3
  notification was created will **not** auto-trigger.
- [ ] `[CMD]` To process pre-existing samples today, re-`aws s3 cp` them onto themselves /
  a new key so a fresh `ObjectCreated` fires, or implement + run the backfill script.
  Confirm chosen approach reprocesses the historical partitions.

## 10. Cleanup after verification

- [ ] `[CMD]` Remove the temporary verification objects you uploaded:
  ```powershell
  aws s3 rm "s3://$Bucket/emails/$TestDate/verify-e2e-01.eml"
  aws s3 rm "s3://$Bucket/emails/$TestDate/verify-e2e-01-again.eml"
  ```
- [ ] `[CMD]` Optionally remove the verification outputs they generated under
  `emails-extracted/content/$TestDate/` and `emails-extracted/attachments/$TestDate/` (leave real
  data intact — only delete keys you created).
- [ ] `[AWS]` If you wrote test dedup rows, decide whether to delete them; leaving them is
  harmless (they just mark those identities as processed).
- [ ] `[AWS]` Confirm all alarms are back to `OK` and both queues are empty.

---

### Quick pass/fail summary

The pipeline is functioning as intended when: all unit tests pass; `cdk synth`/`diff` is
clean; the deployed Lambda, queues, table, notification, IAM role and alarms match the
stack; a fresh `.eml` upload yields date-partitioned `.md` + `.json` for each email and
attachment; forwards split correctly; inline signature images are excluded; a re-uploaded
duplicate is skipped via DynamoDB; a poison message reaches the DLQ and raises both
alarms; and CloudWatch custom metrics reconcile with the objects written.
