"""
HTTP / Webhook output handler.

Supports three modes:

* ``generic`` — POST a JSON document containing an array of log lines under a
  configurable key (default: ``logs``).
* ``loki`` — Build a `Grafana Loki push payload
  <https://grafana.com/docs/loki/latest/reference/loki-http-api/#ingest-logs>`_
  with configurable stream labels.
* ``splunk_hec`` — POST events to the `Splunk HTTP Event Collector
  <https://docs.splunk.com/Documentation/Splunk/latest/Data/HECRESTendpoints>`_
  using newline-delimited JSON events.

The handler relies only on :mod:`urllib` to avoid pulling in ``requests`` or
``aiohttp``. It implements bounded retries with exponential backoff and gzip
compression for large payloads.
"""

import gzip
import json
import logging
import time
import urllib.error
import urllib.request

from random_log_generator.output.base import OutputHandler


SUPPORTED_MODES = ('generic', 'loki', 'splunk_hec')

# When request bodies exceed this size we compress them to reduce egress.
_GZIP_THRESHOLD_BYTES = 4096


class HTTPOutputHandler(OutputHandler):
    """POST log batches to an HTTP endpoint with retries and gzip support."""

    def __init__(self, url, mode='generic', headers=None, auth_token=None,
                 timeout=5.0, max_retries=3, backoff_seconds=0.5,
                 verify_tls=True, gzip_enabled=True,
                 # Generic-mode options.
                 payload_key='logs',
                 # Loki options.
                 loki_labels=None,
                 # Splunk HEC options.
                 splunk_index=None, splunk_source=None, splunk_sourcetype=None,
                 splunk_host=None):
        if not url:
            raise ValueError("HTTPOutputHandler requires a non-empty 'url'")
        mode = (mode or 'generic').lower()
        if mode not in SUPPORTED_MODES:
            raise ValueError(
                f"Unsupported HTTP sink mode '{mode}'. Choose one of {SUPPORTED_MODES}."
            )
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds must be >= 0")

        self.url = url
        self.mode = mode
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.backoff_seconds = float(backoff_seconds)
        self.gzip_enabled = bool(gzip_enabled)
        self.payload_key = payload_key
        self.loki_labels = dict(loki_labels) if loki_labels else {'job': 'random-log-generator'}
        self.splunk_index = splunk_index
        self.splunk_source = splunk_source
        self.splunk_sourcetype = splunk_sourcetype
        self.splunk_host = splunk_host

        self._headers = {'Content-Type': 'application/json'}
        if headers:
            self._headers.update(headers)
        if auth_token:
            # Splunk uses ``Splunk <token>``; everything else gets ``Bearer``
            # by default unless the caller supplied an explicit Authorization
            # header in ``headers``.
            if 'Authorization' not in self._headers:
                if mode == 'splunk_hec':
                    self._headers['Authorization'] = f'Splunk {auth_token}'
                else:
                    self._headers['Authorization'] = f'Bearer {auth_token}'

        # ``ssl`` is only imported when the user opts out of verification so
        # that environments without TLS support still load the module.
        if not verify_tls:
            import ssl  # local import on purpose
            self._ssl_context = ssl.create_default_context()
            self._ssl_context.check_hostname = False
            self._ssl_context.verify_mode = ssl.CERT_NONE
        else:
            self._ssl_context = None

        logging.info("HTTPOutputHandler initialised: mode=%s url=%s", mode, url)

    # ---- public API ---------------------------------------------------

    def write(self, log_lines):
        if not log_lines:
            return True
        body = self._build_body(log_lines)
        return self._post_with_retries(body)

    def close(self):
        # urllib opens a fresh connection per call; nothing to release.
        return None

    # ---- payload builders --------------------------------------------

    def _build_body(self, log_lines):
        if self.mode == 'generic':
            return json.dumps({self.payload_key: list(log_lines)},
                              separators=(',', ':')).encode('utf-8')

        if self.mode == 'loki':
            # Loki expects nanosecond timestamps as decimal strings.
            ts_ns = str(time.time_ns())
            values = [[ts_ns, line] for line in log_lines]
            payload = {
                'streams': [{
                    'stream': self.loki_labels,
                    'values': values,
                }]
            }
            return json.dumps(payload, separators=(',', ':')).encode('utf-8')

        # splunk_hec — Splunk's `/services/collector/event` accepts newline-
        # delimited JSON envelopes. Each event is wrapped in its own object.
        envelopes = []
        now = time.time()
        for line in log_lines:
            envelope = {'event': line, 'time': now}
            if self.splunk_index:
                envelope['index'] = self.splunk_index
            if self.splunk_source:
                envelope['source'] = self.splunk_source
            if self.splunk_sourcetype:
                envelope['sourcetype'] = self.splunk_sourcetype
            if self.splunk_host:
                envelope['host'] = self.splunk_host
            envelopes.append(json.dumps(envelope, separators=(',', ':')))
        return ('\n'.join(envelopes)).encode('utf-8')

    # ---- transport ----------------------------------------------------

    def _post_with_retries(self, body):
        headers = dict(self._headers)
        payload = body
        if self.gzip_enabled and len(body) >= _GZIP_THRESHOLD_BYTES:
            payload = gzip.compress(body)
            headers['Content-Encoding'] = 'gzip'

        attempts = self.max_retries + 1
        for attempt in range(1, attempts + 1):
            request = urllib.request.Request(
                self.url, data=payload, headers=headers, method='POST'
            )
            try:
                kwargs = {'timeout': self.timeout}
                if self._ssl_context is not None:
                    kwargs['context'] = self._ssl_context
                with urllib.request.urlopen(request, **kwargs) as response:
                    status = getattr(response, 'status', response.getcode())
                    if 200 <= status < 300:
                        return True
                    logging.warning(
                        "HTTP sink got status %s from %s (attempt %d/%d)",
                        status, self.url, attempt, attempts,
                    )
            except urllib.error.HTTPError as exc:
                # 4xx (except 429) are not retryable.
                if exc.code != 429 and 400 <= exc.code < 500:
                    logging.error(
                        "HTTP sink permanent error %s from %s: %s",
                        exc.code, self.url, exc,
                    )
                    return False
                logging.warning(
                    "HTTP sink transient HTTPError %s (attempt %d/%d): %s",
                    exc.code, attempt, attempts, exc,
                )
            except (urllib.error.URLError, OSError) as exc:
                logging.warning(
                    "HTTP sink network error (attempt %d/%d): %s",
                    attempt, attempts, exc,
                )

            if attempt < attempts:
                time.sleep(self.backoff_seconds * (2 ** (attempt - 1)))

        logging.error("HTTP sink giving up after %d attempts", attempts)
        return False
