# Data Pipeline Verification Checklist

End-to-end checklist to confirm the email `.eml` → Markdown/JSON pipeline is functioning
as intended. Work top-to-bottom: local code + tests first (cheap, fast feedback), then

the deployed AWS stack (SQS → Lambda → DynamoDB → S3), then real end-to-end runs against
the `be-cig-vault-ds-raw` bucket.

Legend: each item is a checkbox. `[CODE]` = inspect source, `[CMD]` = run a command in
Windows **cmd** (Command Prompt), `[AWS]` = check in AWS via the CLI, `[CONSOLE]` = check
in the AWS Console (browser), `[FILE]` = download/open an output artifact.

## Conventions used below

- Bucket: `be-cig-vault-ds-raw` (input prefix `emails/`, output prefix `emails-extracted/`).
- Queue: `cig-vault-eml-ingest`; DLQ: `cig-vault-eml-ingest-dlq`.
- DynamoDB table: `cig-vault-email-dedup` (partition key `pk`).
- Lambda: the `EmailProcessorFunction` in stack `CigEmailPipelineStack`.
- Region + names: set once so every `cmd` command below is copy-paste ready. Reference a
  variable as `%Name%`. **Note on `for /f`:** at an interactive prompt use a single `%i`
  loop variable (as written below); inside a saved `.bat`/`.cmd` file, double it to `%%i`.

```bat
set AWS_REGION=us-west-2
set Bucket=be-cig-vault-ds-raw
set Queue=cig-vault-eml-ingest
set Dlq=cig-vault-eml-ingest-dlq
set Table=cig-vault-email-dedup
set Stack=CigEmailPipelineStack
set TestDate=2026-07-27
```

---

## 0. Prerequisites & deployment lifecycle

Confirm tooling and get the stack deployed before any live AWS check. Skip to §5 if the
stack is already deployed and you only need to verify a running environment.

- [ ] `[CMD]` Confirm AWS CLI v2, Docker Desktop (running), Python 3.12+, Node.js, and the
  AWS CDK are installed:
  ```bat
  aws --version
  docker info
  python --version
  cdk --version
  ```
- [ ] `[CMD]` Confirm your credentials/profile resolve to the intended account and region
  (the account that owns `%Bucket%`):
  ```bat
  aws sts get-caller-identity
  echo %AWS_REGION%
  ```
- [ ] `[CONSOLE]` In the AWS Console top-right, confirm the **account id** and **region**
  selector match the values above before any later `[CONSOLE]` step.
- [ ] `[CMD]` One-time per account/region: bootstrap the CDK environment (creates the CDK
  assets bucket + ECR repo used to push the Lambda image):
  ```bat
  cd cig-project-intelligence\data-pipeline\infra\cdk
  cdk bootstrap aws://<account-id>/%AWS_REGION%
  ```
- [ ] `[CMD]` Deploy (or update) the stack. Docker must be running — CDK builds the Lambda
  container image and pushes it to ECR during deploy:
  ```bat
  cdk deploy CigEmailPipelineStack --require-approval never
  ```
- [ ] `[CMD]` Confirm the stack reached a good terminal state (`CREATE_COMPLETE` or
  `UPDATE_COMPLETE`, not `ROLLBACK_*`):
  ```bat
  aws cloudformation describe-stacks --stack-name %Stack% --query "Stacks[0].StackStatus" --output text
  ```
- [ ] `[CONSOLE]` **CloudFormation → Stacks → CigEmailPipelineStack → Events** — latest event
  is `..._COMPLETE`, no red `CREATE_FAILED`/`ROLLBACK` rows. The **Resources** tab lists the
  DynamoDB table, both SQS queues, the Lambda function, and the two CloudWatch alarms.
- [ ] `[CMD]` Confirm the Lambda container image was pushed to ECR (the image the function
  actually runs):
  ```bat
  aws ecr describe-repositories --query "repositories[?contains(repositoryName,'cdk')].repositoryName"
  ```
- [ ] `[CONSOLE]` **ECR → Repositories** — open the CDK asset repo and confirm a recently
  pushed image tag with a timestamp matching your deploy.
- [ ] `[AWS]` For an already-deployed stack, check for drift between code and the live stack
  (also see §4 `cdk diff`):
  ```bat
  aws cloudformation detect-stack-drift --stack-name %Stack%
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
  per-unit dedup claim → process attachments only for the first emitted unit → write
  outputs. Confirm attachment refs are attached to the **first emitted unit**
  (`unit.order == 0`), **not** `unit.is_wrapper` — a pure forward drops the empty wrapper,
  so MIME attachments must associate with the first surviving (often quoted) unit. Confirm
  the per-unit `thread`/`timestamp` computed from `unit.subject`/`unit.date` are passed to
  both `content_key` and `attachment_key`.
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
- [ ] `[CODE]` formatter.py `thread_id` / `normalize_subject` — leading `Re:`/`Fw:`/`Fwd:`
  (and `Aw:`/`Wg:`/`Sv:`/`Vs:`) prefixes are stripped so reply/forward variants of one
  subject yield the same 8-char thread id. Confirm output keys are
  `{thread8}__{timestamp}__{subject-slug}__{hash8}.md` and attachments reuse the thread id +
  timestamp of the first emitted unit (`order == 0`), which owns the MIME attachments.
- [ ] `[CODE]` formatter.py `format_timestamp` — RFC 5322 dates parse to sortable UTC
  `YYYYMMDDThhmmssZ` (offsets converted to UTC); missing/unparseable dates fall back to
  `00000000T000000Z`.
- [ ] `[CODE]` [s3_io.py](../src/email_pipeline/s3_io.py) — `put_markdown` sets
  `text/markdown; charset=utf-8`; `put_json` sets `application/json; charset=utf-8` and
  uses `ensure_ascii=False, default=str`.
- [ ] `[CODE]` [Dockerfile](../Dockerfile) — base is `public.ecr.aws/lambda/python:3.12`;
  `pip install` of the package; `CMD ["email_pipeline.handler.handler"]` matches the
  actual module path.

## 2. Unit tests (local)

- [ ] `[CMD]` Create/activate the venv and install dev deps:
  ```bat
  cd data-pipeline
  python -m venv .venv
  .venv\Scripts\activate.bat
  python -m pip install -e ".[dev]"
  ```
- [ ] `[CMD]` Run the full suite and confirm **all tests pass**:
  ```bat
  pytest -ra
  ```
- [ ] `[CMD]` Confirm each module has coverage by running its file explicitly and
  checking it is non-empty / green:
  ```bat
  pytest tests\test_thread_splitter.py tests\test_eml_parser.py tests\test_dedup.py ^
         tests\test_extractors.py tests\test_formatter.py tests\test_html_to_md.py ^
         tests\test_handler.py -v
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
- [ ] `[CODE]` [tests/test_formatter.py](../tests/test_formatter.py) — verify
  `test_content_key_layout` / `test_attachment_key_layout` assert the
  `{thread8}__{timestamp}__{slug}__{hash8}.md` shape, `test_normalize_subject_*` strips
  `Re:`/`Fw:` prefixes, `test_thread_id_is_stable_across_reply_forward_variants` proves one
  thread id across variants, and `test_format_timestamp_*` normalizes RFC 5322 dates to
  sortable UTC (with the `00000000T000000Z` fallback).
- [ ] `[CODE]` [tests/test_handler.py](../tests/test_handler.py) — verify a synthetic
  SQS→S3 event drives `process_object`, writes to a mocked S3, and a poison message
  produces a `batchItemFailures` entry. Confirm
  `test_reprocessing_same_email_with_attachment_skips_attachment_outputs` and
  `test_attachment_is_extracted_and_inline_skipped` cover the `order == 0` attachment path.
- [ ] `[CMD]` Confirm fixtures are synthetic (no real PII):
  ```bat
  dir tests\fixtures
  ```

## 3. Local container / handler smoke test

- [ ] `[CMD]` Build the Lambda image locally (validates Dockerfile + deps resolve):
  ```bat
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
  ```bat
  cd infra\cdk
  python -m venv .venv
  .venv\Scripts\activate.bat
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
  ```bat
  aws sqs get-queue-url --queue-name %Queue%
  aws sqs get-queue-url --queue-name %Dlq%
  ```
- [ ] `[AWS]` Confirm the ingest queue's redrive policy points to the DLQ with
  `maxReceiveCount = 5`:
  ```bat
  for /f "delims=" %i in ('aws sqs get-queue-url --queue-name %Queue% --query QueueUrl --output text') do set qurl=%i
  aws sqs get-queue-attributes --queue-url %qurl% --attribute-names RedrivePolicy VisibilityTimeout
  ```
- [ ] `[AWS]` Confirm the queue policy allows `s3.amazonaws.com` to `sqs:SendMessage`
  scoped to the source bucket ARN and account (add `--attribute-names Policy` above).
- [ ] `[AWS]` Confirm `VisibilityTimeout` is comfortably larger than the Lambda timeout
  (stack sets it to `timeout * 6` = 720 s for a 120 s function).
- [ ] `[CONSOLE]` **SQS → Queues → cig-vault-eml-ingest** — the **Dead-letter queue** tab
  shows `cig-vault-eml-ingest-dlq` with **Maximum receives = 5**; **Details** shows
  **Visibility timeout = 720**. Open **Access policy** and confirm a statement allowing
  principal `s3.amazonaws.com` action `SendMessage` conditioned on the bucket ARN/account.

### 5b. Lambda function
- [ ] `[AWS]` Get the function config and confirm memory, timeout, package type, and env
  vars:
  ```bat
  for /f "delims=" %i in ('aws cloudformation describe-stack-resources --stack-name %Stack% --query "StackResources[?ResourceType=='AWS::Lambda::Function'].PhysicalResourceId" --output text') do set fn=%i
  aws lambda get-function-configuration --function-name %fn% --query "{Mem:MemorySize,Timeout:Timeout,Pkg:PackageType,Env:Environment.Variables,Arch:Architectures}"
  ```
- [ ] `[AWS]` Confirm `MemorySize ~ 1024`, `Timeout ~ 120`, `PackageType = Image`,
  `Architectures = [x86_64]`, and all six env vars present with correct values.
- [ ] `[AWS]` Confirm the SQS event source mapping is **Enabled** and bound to the ingest
  queue:
  ```bat
  aws lambda list-event-source-mappings --function-name %fn% --query "EventSourceMappings[].{State:State,Batch:BatchSize,Src:EventSourceArn,Resp:FunctionResponseTypes}"
  ```
- [ ] `[CONSOLE]` **Lambda → Functions → (EmailProcessorFunction)** — **Configuration →
  General configuration** (Memory 1024 MB, Timeout 2 min, **Package type: Image**);
  **Configuration → Environment variables** (all six present); **Configuration → Triggers**
  shows SQS `cig-vault-eml-ingest`, state **Enabled**, batch size 5.

### 5c. Lambda IAM role (least privilege)
- [ ] `[AWS]` Confirm the execution role grants only: `s3:GetObject` on `emails/*`,
  `s3:PutObject` on `emails-extracted/*`, DynamoDB item ops on the dedup table, and SQS
  consume on the ingest queue — nothing bucket-wide or `*`:
  ```bat
  for /f "tokens=2 delims=/" %i in ('aws lambda get-function-configuration --function-name %fn% --query Role --output text') do set role=%i
  aws iam list-role-policies --role-name %role%
  aws iam get-role-policy --role-name %role% --policy-name <inline-policy-name>
  ```
- [ ] `[AWS]` Confirm the S3 read statement resource ends with `emails/*` and the write
  statement resource ends with `emails-extracted/*` (no cross-prefix write access).
- [ ] `[CONSOLE]` **Lambda → Configuration → Permissions** → click the **Execution role**
  to open it in **IAM**; expand the inline policy and confirm the four scoped statements
  above, with **no** `Resource: "*"` and no bucket-wide grants.

### 5d. DynamoDB dedup table
- [ ] `[AWS]` Confirm the table exists, is `ACTIVE`, `PAY_PER_REQUEST`, PK `pk` (String):
  ```bat
  aws dynamodb describe-table --table-name %Table% --query "Table.{Status:TableStatus,Billing:BillingModeSummary.BillingMode,Keys:KeySchema,Attrs:AttributeDefinitions}"
  ```
- [ ] `[CONSOLE]` **DynamoDB → Tables → cig-vault-email-dedup → Overview** — Status
  **Active**, Capacity mode **On-demand**, Partition key **`pk` (String)**. Open **Explore
  table items** to view stored dedup rows.

### 5e. S3 → SQS event notification
- [ ] `[AWS]` Confirm the bucket has a queue notification for `s3:ObjectCreated:*` filtered
  to prefix `emails/` and suffix `.eml`, targeting the ingest queue ARN:
  ```bat
  aws s3api get-bucket-notification-configuration --bucket %Bucket%
  ```
- [ ] `[AWS]` Verify the filter does **not** accidentally include the `emails-extracted/` output
  prefix (which would create a processing loop).
- [ ] `[CONSOLE]` **S3 → be-cig-vault-ds-raw → Properties → Event notifications** — confirm
  one notification: event `s3:ObjectCreated:*`, prefix `emails/`, suffix `.eml`,
  destination = SQS `cig-vault-eml-ingest`, and that it does **not** cover
  `emails-extracted/`.

### 5f. CloudWatch alarms
- [ ] `[AWS]` Confirm both alarms exist and are in `OK` (not `INSUFFICIENT_DATA` after
  data has flowed):
  ```bat
  aws cloudwatch describe-alarms --alarm-name-prefix %Stack% --query "MetricAlarms[].{Name:AlarmName,State:StateValue,Metric:MetricName,Threshold:Threshold}"
  ```
- [ ] `[AWS]` Confirm one alarm watches DLQ `ApproximateNumberOfMessagesVisible > 0` and
  the other watches Lambda `Errors > 0`.
- [ ] `[CONSOLE]` **CloudWatch → Alarms** — confirm two alarms (DLQ depth > 0; Lambda
  Errors > 0), both showing **OK** after data has flowed (not **Insufficient data**).

## 6. End-to-end functional tests

### 6.0 Sample → scenario map (which local sample exercises what)

Real sample `.eml` files live in `cigvault-samples\emails_eml\2026-07-27\` with **opaque
Outlook EntryID filenames** that differ only in the trailing characters before `.eml`. The
table maps a scenario to a concrete sample by that trailing suffix; confirm the match with
`findstr` before uploading. Together these cover every processing path in one pass.

| Scenario | Sample (EntryID trailing suffix) | What to confirm |
|---|---|---|
| Direct / simple email | `...CHRj-AAA=.eml` | exactly one content `.md` + `.json` |
| External-banner email | `...CHRkCAAA=.eml`, `...CHRkJAAA=.eml`, `...CHRkNAAA=.eml` | `> **EXTERNAL EMAIL**` marker + `is_external:true` |
| Forward / thread split | `...CHRkLAAA=.eml` (`Fw: Re: [External] Project Jupiter`) | multiple content `.md`, one per quoted email |
| PDF attachment(s) | `...CHRkAAAA=.eml` (2 PDFs), `...CHRkNAAA=.eml` (3 PDFs) | one attachment `.md` per PDF with `## Page N` |
| Unsupported attachment type | `...CHRkEAAA=.eml` (`SMC005.Z Baseline.mpp`) | `.mpp` skipped + logged, no attachment output, email still written |
| Inline signature images only | `...CHRkIAAA=.eml` (`Outlook-*.png` inline) | **no** attachment output for the logos |
| HTML-only body | any sample lacking a `text/plain` part | non-empty converted Markdown, no raw tags |

- [ ] `[CMD]` Confirm a sample carries a real (non-inline) attachment before using it:
  ```bat
  findstr /c:"Content-Disposition: attachment" "..\..\cigvault-samples\emails_eml\%TestDate%\<entryid>.eml"
  ```
- [ ] `[CMD]` Confirm a sample is an external email / find the HTML-only ones:
  ```bat
  findstr /c:"EXTERNAL EMAIL" "..\..\cigvault-samples\emails_eml\%TestDate%\<entryid>.eml"
  findstr /m /c:"Content-Type: text/plain" "..\..\cigvault-samples\emails_eml\%TestDate%\*.eml"
  ```

### 6a. Happy path — new email produces outputs
- [ ] `[CMD]` Pick a sample `.eml` (local mirror in
  `cigvault-samples\emails_eml\2026-07-27\`) and upload it to a **fresh** key under the
  input prefix so it triggers the notification:
  ```bat
  rem pick any sample .eml from the map above and set its path:
  set src=..\..\cigvault-samples\emails_eml\%TestDate%\<entryid>.eml
  aws s3 cp "%src%" "s3://%Bucket%/emails/%TestDate%/verify-e2e-01.eml"
  ```
- [ ] `[AWS]` Within a minute, confirm the Lambda ran: check its latest log group for a
  new invocation and an EMF line containing `EmailsProcessed`:
  ```bat
  aws logs tail "/aws/lambda/%fn%" --since 5m --follow
  ```
- [ ] `[AWS]` Confirm the SQS queue drained back to 0 in-flight/visible (no stuck message):
  ```bat
  aws sqs get-queue-attributes --queue-url %qurl% --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
  ```
- [ ] `[FILE]` Confirm content output objects were written under the mirrored date
  partition:
  ```bat
  aws s3 ls "s3://%Bucket%/emails-extracted/content/%TestDate%/" --recursive
  ```
- [ ] `[FILE]` Confirm each output key follows the layout
  `{thread8}__{timestamp}__{subject-slug}__{hash8}.md` — an 8-hex thread id, a sortable
  `YYYYMMDDThhmmssZ` timestamp, a lowercase `[a-z0-9-]` subject slug, and an 8-hex identity
  hash. Attachment keys mirror this as
  `{thread8}__{timestamp}__{eml-stem}__{attachment-slug}.md`.
- [ ] `[FILE]` Confirm each email produced a `.md` **and** a matching `.json` sidecar
  (same stem). Download one pair and inspect:
  ```bat
  aws s3 cp "s3://%Bucket%/emails-extracted/content/%TestDate%/<name>.md"   out.md
  aws s3 cp "s3://%Bucket%/emails-extracted/content/%TestDate%/<name>.json" out.json
  ```
- [ ] `[CONSOLE]` **Lambda → (function) → Monitor → View CloudWatch logs** — open the
  latest log stream and confirm an invocation plus an EMF line with `EmailsProcessed`.
  Then **S3 → be-cig-vault-ds-raw → emails-extracted/content/<date>/** shows the new
  `.md`/`.json` objects.

### 6b. Output content validation (open the downloaded files)
- [ ] `[FILE]` `out.md` starts with `# <subject>`, followed by a header block
  (`**From:** … **To:** … **Date:** …`), a `---` rule, then the body. No raw HTML tags,
  no quoted-printable artifacts (`=20`, `=3D`), no base64 blobs.
- [ ] `[FILE]` If the source was an external email, `out.md` contains the
  `> **EXTERNAL EMAIL**` marker.
- [ ] `[FILE]` `out.json` is valid JSON with `schema_version`, `identity`, `message_id`,
  `source_key` (the input key), `output_key` (this md key), `subject`, `from/to/cc/date`,
  and an `attachments` array. Validate:
  ```bat
  type out.json
  python -m json.tool out.json
  ```
- [ ] `[FILE]` `source_key` in the JSON matches the exact input object you uploaded.
- [ ] `[FILE]` **Encoding is clean:** open `out.md` in a UTF-8-aware editor and confirm no
  mojibake (e.g. `Â`, `â€™`, `Ã©`) from mis-decoded iso-8859-1/utf-8, and that accented
  names/quotes render correctly. Search for residual quoted-printable soft-break artifacts
  (expect **no** matches):
  ```bat
  findstr /c:"=20" /c:"=3D" out.md
  ```

### 6c. Forwarded / threaded email split
- [ ] `[CMD]` Upload a known **forwarded** sample (one containing an Outlook divider +
  quoted `From:/Sent:/To:/Subject:` block).
- [ ] `[FILE]` Confirm it produced **multiple** content `.md` files (one per real email in
  the thread), each with its own header block, and that an empty forward wrapper did
  **not** create a near-empty noise file.
- [ ] `[FILE]` Confirm every email of the thread shares the **same `{thread8}` prefix**
  (reply/forward `Re:`/`Fw:` variants normalize to one thread id) and that listing the
  partition sorts them **chronologically** by the `{timestamp}` segment:
  ```bat
  aws s3 ls "s3://%Bucket%/emails-extracted/content/%TestDate%/" --recursive | sort
  ```
- [ ] `[FILE]` For a **pure forward with an empty wrapper**, confirm the MIME attachments
  attached to the **first surviving (quoted) email** (`order == 0`), not to a dropped
  wrapper, and that its `out.json` `attachments[]` references them.

### 6d. Attachment extraction
- [ ] `[CMD]` Upload a sample `.eml` that carries a real attachment
  (`Content-Disposition: attachment`), e.g. a PDF/XLSX/PPTX/DOCX.
- [ ] `[FILE]` Confirm an attachment output exists under the attachments prefix:
  ```bat
  aws s3 ls "s3://%Bucket%/emails-extracted/attachments/%TestDate%/" --recursive
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
  ```bat
  aws dynamodb scan --table-name %Table% --select COUNT --query Count
  ```
- [ ] `[CMD]` Re-upload the **same** email content under a *different* input key (simulates
  the same email arriving again / inside a later forward):
  ```bat
  aws s3 cp "%src%" "s3://%Bucket%/emails/%TestDate%/verify-e2e-01-again.eml"
  ```
- [ ] `[AWS]` Confirm the Lambda logged a `DedupSkips` metric and the content `.md` was
  **not** rewritten (check `LastModified` unchanged):
  ```bat
  aws s3api head-object --bucket %Bucket% --key "emails-extracted/content/%TestDate%/<name>.md" --query LastModified
  ```
- [ ] `[AWS]` Confirm the dedup table item count did **not** increase for already-seen
  identities.
- [ ] `[AWS]` Inspect one dedup item and confirm attributes `pk`, `created_at`,
  `schema_version`, `source_key`, `output_key`, `subject`, `date`:
  ```bat
  aws dynamodb scan --table-name %Table% --max-items 1
  ```
- [ ] `[CONSOLE]` **DynamoDB → cig-vault-email-dedup → Explore table items** — confirm the
  re-upload added **no** new item for an already-seen identity, and an existing item carries
  `pk`, `created_at`, `source_key`, `output_key`.

### 6f. Poison message → DLQ
- [ ] `[CMD]` Upload an object that will reliably fail processing (e.g. a `.eml` key that
  the Lambda can read but that raises during parse — or temporarily point the function at
  a non-existent object by deleting it right after upload). Prefer a crafted corrupt
  `.eml` so the failure is in `process_object`, not S3 access.
- [ ] `[AWS]` Confirm the message is retried up to `maxReceiveCount = 5` then lands in the
  DLQ:
  ```bat
  for /f "delims=" %i in ('aws sqs get-queue-url --queue-name %Dlq% --query QueueUrl --output text') do set dlqurl=%i
  aws sqs get-queue-attributes --queue-url %dlqurl% --attribute-names ApproximateNumberOfMessages
  ```
- [ ] `[AWS]` Confirm the **DLQ depth alarm** transitions to `ALARM`.
- [ ] `[AWS]` Confirm the Lambda **Errors** alarm transitions to `ALARM`.
- [ ] `[CONSOLE]` **SQS → cig-vault-eml-ingest-dlq → Send and receive messages → Poll for
  messages** — confirm the poison message is present; **CloudWatch → Alarms** shows both
  alarms in **In alarm**.
- [ ] `[CMD]` After verifying, purge the DLQ so alarms clear:
  ```bat
  aws sqs purge-queue --queue-url %dlqurl%
  ```

## 7. Observability

- [ ] `[AWS]` In CloudWatch Metrics namespace `CigVault/EmailPipeline`, confirm these
  custom metrics are populated after a run: `EmailsProcessed`, `IndividualEmailsWritten`,
  `DedupSkips`, `AttachmentsExtracted`, `ExtractionFailures`.
- [ ] `[AWS]` Confirm the counts are internally consistent for a batch, e.g.
  `IndividualEmailsWritten + DedupSkips` reconciles with the number of email units across
  processed objects.
- [ ] `[AWS]` **Reconciliation worked example:** for a batch of N `.eml` objects with no
  duplicates and no forwards, expect `EmailsProcessed = N`, `IndividualEmailsWritten = N`,
  `DedupSkips = 0`. Re-running the *same* N objects should give `EmailsProcessed = N`,
  `IndividualEmailsWritten = 0`, `DedupSkips = N`. A forward that splits into k emails adds
  k to `IndividualEmailsWritten` (minus any already seen). Confirm the numbers add up.
- [ ] `[AWS]` Confirm Lambda logs are structured/JSON and include a per-object line for
  each processed key; failures show a full stack trace (`logger.exception`).
- [ ] `[AWS]` Confirm no `ExtractionFailures` spikes for normal traffic (oversize/corrupt
  attachments are the only expected source).
- [ ] `[AWS]` Run a Logs Insights query to surface any exceptions across the run (fill in
  epoch seconds for start/end):
  ```bat
  aws logs start-query --log-group-name "/aws/lambda/%fn%" --start-time <epoch-start> --end-time <epoch-end> --query-string "fields @timestamp, @message | filter @message like /Exception|Traceback/ | sort @timestamp desc | limit 50"
  ```
- [ ] `[CONSOLE]` **CloudWatch → Logs → Logs Insights** — select the function's log group
  and run `fields @timestamp, @message | filter @message like /Exception|Traceback/ | sort
  @timestamp desc`; confirm no unexpected stack traces for a healthy run.
- [ ] `[CONSOLE]` **CloudWatch → Metrics → CigVault/EmailPipeline** — graph the five custom
  metrics on one chart and eyeball the reconciliation above after a test batch.

## 8. Edge cases & security

- [ ] `[CMD]` Upload an email with an oversized attachment (> 25 MiB). Confirm it is
  **skipped** with a warning + `ExtractionFailures` increment, and the email itself still
  produces content output (no crash).
- [ ] `[CMD]` Upload an email with an **unsupported** attachment type (e.g. `.zip`,
  `.dwg`). Confirm it is skipped + logged, no attachment output, no message failure.
- [ ] `[CMD]` Upload an HTML-only email (no `text/plain` part). Confirm the content `.md`
  has real converted text (not empty), with scripts/styles/images stripped.
- [ ] `[CMD]` Upload an email known to use `charset=iso-8859-1` (accented characters) and
  confirm the output `.md` renders the accents correctly (round-trips through UTF-8, no
  mojibake) — see the encoding check in §6b.
- [ ] `[CMD]` Upload an email whose subject/filename contains Outlook EntryID characters
  (`+ / =`) or unicode. Confirm the output S3 key is a clean slug (`[a-z0-9-]`) and no
  `NoSuchKey`/invalid-key errors occur.
- [ ] `[CMD]` Send the S3 test event (`{"Event":"s3:TestEvent"}`) or rely on the one S3
  emits at notification setup. Confirm the handler ignores it (no error, no output).
- [ ] `[AWS]` Confirm objects are encrypted at rest (bucket default SSE or SSE-KMS) and
  that the Lambda role has the matching KMS permissions if SSE-KMS is enabled (per the
  deferred security-hardening note — verify current state, flag if missing).
- [ ] `[CONSOLE]` **S3 → be-cig-vault-ds-raw → (an output object) → Properties** — confirm
  **Server-side encryption** shows SSE-S3 (AES-256) or SSE-KMS as expected; if SSE-KMS,
  cross-check the Lambda role has matching KMS `Encrypt/Decrypt` under §5c.
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
  ```bat
  aws s3 rm "s3://%Bucket%/emails/%TestDate%/verify-e2e-01.eml"
  aws s3 rm "s3://%Bucket%/emails/%TestDate%/verify-e2e-01-again.eml"
  ```
- [ ] `[CMD]` Optionally remove the verification outputs they generated under
  `emails-extracted/content/%TestDate%/` and `emails-extracted/attachments/%TestDate%/`
  (leave real data intact — only delete keys you created).
- [ ] `[AWS]` If you wrote test dedup rows, decide whether to delete them; leaving them is
  harmless (they just mark those identities as processed).
- [ ] `[AWS]` Confirm all alarms are back to `OK` and both queues are empty.
- [ ] `[CMD]` (Optional, **non-prod only**) Tear down the stack. **Note:** the DynamoDB
  dedup table has `RemovalPolicy.RETAIN` — it and its items **survive** `cdk destroy` and
  must be deleted manually for a truly clean slate:
  ```bat
  cdk destroy CigEmailPipelineStack
  aws dynamodb describe-table --table-name %Table% --query "Table.TableStatus" --output text
  ```
- [ ] `[AWS]` After a destroy, confirm the S3 → SQS **event notification** was removed from
  the bucket (an orphaned notification pointing at a deleted queue blocks re-deploys):
  ```bat
  aws s3api get-bucket-notification-configuration --bucket %Bucket%
  ```
- [ ] `[AWS]` Confirm no orphaned ECR images accumulate across redeploys (there is no image
  lifecycle policy yet — a deferred hardening item):
  ```bat
  aws ecr describe-images --repository-name <cdk-asset-repo> --query "length(imageDetails)"
  ```

---

### Quick pass/fail summary

The pipeline is functioning as intended when: all unit tests pass; `cdk synth`/`diff` is
clean; the deployed Lambda, queues, table, notification, IAM role and alarms match the
stack; a fresh `.eml` upload yields date-partitioned `.md` + `.json` for each email and
attachment; forwards split correctly; inline signature images are excluded; a re-uploaded
duplicate is skipped via DynamoDB; a poison message reaches the DLQ and raises both
alarms; and CloudWatch custom metrics reconcile with the objects written.
