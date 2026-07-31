"""CloudWatch custom metrics via the Embedded Metric Format (EMF) (D26).

Emitting EMF as a structured log line lets CloudWatch extract metrics without an extra
PutMetricData API call. When not running in Lambda the same counters are still logged.
"""

from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)

EMAILS_PROCESSED = "EmailsProcessed"
EMAILS_WRITTEN = "IndividualEmailsWritten"
DEDUP_SKIPS = "DedupSkips"
ATTACHMENTS_EXTRACTED = "AttachmentsExtracted"
EXTRACTION_FAILURES = "ExtractionFailures"


class Metrics:
    """Accumulates counters for one invocation and flushes them as one EMF log line."""

    def __init__(self, namespace: str) -> None:
        self._namespace = namespace
        self._counters: dict[str, int] = {}

    def increment(self, name: str, value: int = 1) -> None:
        self._counters[name] = self._counters.get(name, 0) + value

    def flush(self) -> None:
        if not self._counters:
            return

        metric_definitions = [{"Name": name, "Unit": "Count"} for name in self._counters]
        document = {
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [
                    {
                        "Namespace": self._namespace,
                        "Dimensions": [[]],
                        "Metrics": metric_definitions,
                    }
                ],
            },
            **self._counters,
        }
        logger.info(json.dumps(document))
        self._counters.clear()
