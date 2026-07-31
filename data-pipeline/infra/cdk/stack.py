from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_cloudwatch as cloudwatch,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_lambda_event_sources as lambda_event_sources,
    aws_s3 as s3,
    aws_s3_notifications as s3_notifications,
    aws_sqs as sqs,
)
from constructs import Construct


class EmailPipelineStack(Stack):
    """Infrastructure for the S3 -> SQS -> Lambda email processing pipeline."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        source_bucket_name = self._context_str("sourceBucketName", "be-cig-vault-ds-raw")
        input_prefix = self._slash_suffix(self._context_str("inputPrefix", "emails/"))
        output_prefix = self._slash_suffix(self._context_str("outputPrefix", "emails-extracted/"))
        dedup_table_name = self._context_str("dedupTableName", "cig-vault-email-dedup")
        queue_name = self._context_str("queueName", "cig-vault-eml-ingest")
        dlq_name = self._context_str("dlqName", "cig-vault-eml-ingest-dlq")
        metrics_namespace = self._context_str("metricsNamespace", "CigVault/EmailPipeline")
        max_attachment_bytes = self._context_str("maxAttachmentBytes", "26214400")
        memory_mb = self._context_int("lambdaMemoryMb", 1024)
        timeout_seconds = self._context_int("lambdaTimeoutSeconds", 120)
        batch_size = self._context_int("lambdaBatchSize", 5)
        batching_window_seconds = self._context_int("lambdaMaxBatchingWindowSeconds", 30)

        source_bucket = s3.Bucket.from_bucket_name(
            self,
            "SourceBucket",
            source_bucket_name,
        )

        dedup_table = dynamodb.Table(
            self,
            "DedupTable",
            table_name=dedup_table_name,
            partition_key=dynamodb.Attribute(
                name="pk",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
        )

        dead_letter_queue = sqs.Queue(
            self,
            "IngestDeadLetterQueue",
            queue_name=dlq_name,
            retention_period=Duration.days(14),
        )
        ingest_queue = sqs.Queue(
            self,
            "IngestQueue",
            queue_name=queue_name,
            visibility_timeout=Duration.seconds(timeout_seconds * 6),
            retention_period=Duration.days(4),
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=5,
                queue=dead_letter_queue,
            ),
        )

        docker_context = Path(__file__).resolve().parents[2]
        build_context = self._stage_docker_context(docker_context)
        processor = lambda_.DockerImageFunction(
            self,
            "EmailProcessorFunction",
            code=lambda_.DockerImageCode.from_image_asset(
                build_context,
                exclude=[
                    ".venv",
                    "venv",
                    "*.egg-info",
                    "cdk.out",
                    "infra/cdk/cdk.out",
                    "__pycache__",
                    ".pytest_cache",
                    ".ruff_cache",
                    ".env",
                    ".env.*",
                    ".git",
                    ".gitignore",
                ],
                ignore_mode=cdk.IgnoreMode.DOCKER,
            ),
            architecture=lambda_.Architecture.X86_64,
            memory_size=memory_mb,
            timeout=Duration.seconds(timeout_seconds),
            environment={
                "SOURCE_BUCKET": source_bucket_name,
                "INPUT_PREFIX": input_prefix,
                "OUTPUT_PREFIX": output_prefix,
                "DEDUP_TABLE": dedup_table.table_name,
                "METRICS_NAMESPACE": metrics_namespace,
                "MAX_ATTACHMENT_BYTES": max_attachment_bytes,
            },
        )

        processor.add_event_source(
            lambda_event_sources.SqsEventSource(
                ingest_queue,
                batch_size=batch_size,
                max_batching_window=Duration.seconds(batching_window_seconds),
                report_batch_item_failures=True,
            )
        )

        ingest_queue.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowS3ToSendObjectCreatedEvents",
                principals=[iam.ServicePrincipal("s3.amazonaws.com")],
                actions=["sqs:SendMessage"],
                resources=[ingest_queue.queue_arn],
                conditions={
                    "ArnLike": {"aws:SourceArn": source_bucket.bucket_arn},
                    "StringEquals": {"aws:SourceAccount": self.account},
                },
            )
        )
        source_bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3_notifications.SqsDestination(ingest_queue),
            s3.NotificationKeyFilter(prefix=input_prefix, suffix=".eml"),
        )

        dedup_table.grant_read_write_data(processor)
        ingest_queue.grant_consume_messages(processor)
        source_bucket.grant_read(processor, f"{input_prefix}*")
        source_bucket.grant_put(processor, f"{output_prefix}*")

        cloudwatch.Alarm(
            self,
            "DeadLetterQueueDepthAlarm",
            metric=dead_letter_queue.metric_approximate_number_of_messages_visible(
                period=Duration.minutes(5),
                statistic="Maximum",
            ),
            threshold=0,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        )
        cloudwatch.Alarm(
            self,
            "LambdaErrorsAlarm",
            metric=processor.metric_errors(
                period=Duration.minutes(5),
                statistic="Sum",
            ),
            threshold=0,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        )

        cdk.CfnOutput(self, "IngestQueueUrl", value=ingest_queue.queue_url)
        cdk.CfnOutput(self, "DeadLetterQueueUrl", value=dead_letter_queue.queue_url)
        cdk.CfnOutput(self, "DedupTableName", value=dedup_table.table_name)
        cdk.CfnOutput(self, "ProcessorFunctionName", value=processor.function_name)

    def _context_str(self, key: str, default: str) -> str:
        value = self.node.try_get_context(key)
        return str(value) if value not in (None, "") else default

    def _context_int(self, key: str, default: int) -> int:
        value = self.node.try_get_context(key)
        if value in (None, ""):
            return default
        return int(value)

    @staticmethod
    def _stage_docker_context(source: Path) -> str:
        """Copy the Docker build context to a temp dir outside OneDrive.

        OneDrive "Files On-Demand" placeholders carry a reparse point that
        Node's ``readdir`` reports as a symlink, which breaks CDK asset
        fingerprinting (``readlink`` fails with ``EINVAL``). Python reads the
        file content normally, so staging into a plain temp directory yields
        regular files without reparse points that CDK can fingerprint.
        """
        exclude_names = {
            ".venv",
            "venv",
            "cdk.out",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            ".git",
            ".gitignore",
            ".env",
        }

        def _ignore(_dir: str, names: list[str]) -> set[str]:
            ignored: set[str] = set()
            for name in names:
                if name in exclude_names or name.endswith(".egg-info") or name.startswith(".env."):
                    ignored.add(name)
            return ignored

        stage_root = Path(tempfile.gettempdir()) / "cig-email-pipeline-docker-context"
        if stage_root.exists():
            shutil.rmtree(stage_root)
        shutil.copytree(source, stage_root, ignore=_ignore)
        return str(stage_root)

    @staticmethod
    def _slash_suffix(value: str) -> str:
        return value if value.endswith("/") else f"{value}/"