"""
JSON / NDJSON formatter for Random Log Generator.

This module provides a structured JSON log formatter. When wired into the
generator each log "line" is a single JSON object; emitting one such object
per line is the NDJSON convention used by tools like Loki, Elasticsearch
``_bulk``, Vector, Fluent Bit and Splunk HEC's "raw" mode.

The formatter is intentionally fast: it uses ``json.dumps`` with
``separators=(",", ":")`` to produce compact output and avoids per-call
allocation of mutable defaults.
"""

import json
import random

from random_log_generator.formatters.base import LogFormatter


class JSONFormatter(LogFormatter):
    """Formats log entries as a single JSON object per line (NDJSON)."""

    # Keys promoted to the top level when supplied via ``kwargs``.
    _TOP_LEVEL_KEYS = (
        'ip_address', 'user_agent', 'status_code',
        'trace_id', 'span_id', 'service', 'host',
    )

    def __init__(self, app_names=None, extra_fields=None, sort_keys=False,
                 ensure_ascii=False):
        """
        Args:
            app_names (list, optional): If provided, a random app name is
                attached to each record under the ``app`` key.
            extra_fields (dict, optional): Static fields merged into every
                emitted record (e.g. ``{"environment": "prod"}``).
            sort_keys (bool, optional): Sort keys for deterministic output.
            ensure_ascii (bool, optional): Forwarded to ``json.dumps``.
        """
        self.app_names = list(app_names) if app_names else []
        self.extra_fields = dict(extra_fields) if extra_fields else {}
        self._sort_keys = bool(sort_keys)
        self._ensure_ascii = bool(ensure_ascii)

    def format_log(self, timestamp, log_level, message, **kwargs):
        record = {
            'timestamp': timestamp,
            'level': log_level,
            'message': message,
        }

        if self.extra_fields:
            record.update(self.extra_fields)

        if self.app_names:
            record['app'] = random.choice(self.app_names)

        for key in self._TOP_LEVEL_KEYS:
            value = kwargs.get(key)
            if value is not None:
                record[key] = value

        # Anything else the caller passed is nested under "context" so we
        # never silently overwrite reserved fields.
        context = {
            k: v for k, v in kwargs.items()
            if k not in self._TOP_LEVEL_KEYS and v is not None
        }
        if context:
            record['context'] = context

        return json.dumps(
            record,
            separators=(',', ':'),
            sort_keys=self._sort_keys,
            ensure_ascii=self._ensure_ascii,
            default=str,
        )
