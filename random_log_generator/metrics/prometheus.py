"""
Prometheus metrics endpoint for Random Log Generator.

Implements a tiny HTTP server (stdlib :mod:`http.server`) that exposes the
generator's runtime metrics in the Prometheus text exposition format. We
intentionally avoid the ``prometheus_client`` dependency so the generator
remains a pure-stdlib package.

Usage::

    server = PrometheusMetricsServer(metrics, host='0.0.0.0', port=9090)
    server.start()
    ...
    server.stop()
"""

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


_PROM_CONTENT_TYPE = 'text/plain; version=0.0.4; charset=utf-8'


def _escape_label(value):
    """Escape a label value per the Prometheus text format spec."""
    return (
        str(value)
        .replace('\\', '\\\\')
        .replace('\n', '\\n')
        .replace('"', '\\"')
    )


def _render_labels(labels):
    """Render a dict of labels as ``{k="v",...}`` (or '' when empty)."""
    if not labels:
        return ''
    return '{' + ','.join(
        f'{k}="{_escape_label(v)}"' for k, v in labels.items()
    ) + '}'


def render_metrics(metrics, extra_labels=None):
    """Render a :class:`Metrics` snapshot as Prometheus exposition text."""
    stats = metrics.get_stats()
    base_labels = dict(extra_labels) if extra_labels else {}
    base = _render_labels(base_labels)

    def with_kind(kind):
        return _render_labels({**base_labels, 'kind': kind})

    lines = [
        '# HELP rlg_logs_total Total number of log lines generated.',
        '# TYPE rlg_logs_total counter',
        f'rlg_logs_total{base} {stats["total_logs"]}',
        '# HELP rlg_bytes_total Total number of bytes generated.',
        '# TYPE rlg_bytes_total counter',
        f'rlg_bytes_total{base} {stats["total_bytes"]}',
        '# HELP rlg_uptime_seconds Time since the metrics collector started.',
        '# TYPE rlg_uptime_seconds gauge',
        f'rlg_uptime_seconds{base} {stats["duration"]:.6f}',
        '# HELP rlg_rate_mb_s Current observed log generation rate (MB/s).',
        '# TYPE rlg_rate_mb_s gauge',
        f'rlg_rate_mb_s{with_kind("avg")} {stats["avg_rate_mb_s"]:.6f}',
        f'rlg_rate_mb_s{with_kind("max")} {stats["max_rate_mb_s"]:.6f}',
        f'rlg_rate_mb_s{with_kind("min")} {stats["min_rate_mb_s"]:.6f}',
    ]
    return '\n'.join(lines) + '\n'


class _PrometheusHandler(BaseHTTPRequestHandler):
    """HTTP handler serving ``/metrics`` (and a tiny ``/`` landing page)."""

    # Set by :meth:`PrometheusMetricsServer.start`.
    metrics = None
    extra_labels = None

    def do_GET(self):  # noqa: N802 — required by BaseHTTPRequestHandler
        if self.path in ('/metrics', '/metrics/'):
            try:
                body = render_metrics(self.metrics,
                                      extra_labels=self.extra_labels).encode('utf-8')
            except Exception as exc:  # pragma: no cover - defensive
                self.send_error(500, f"failed to render metrics: {exc}")
                return
            self.send_response(200)
            self.send_header('Content-Type', _PROM_CONTENT_TYPE)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path in ('/', '/healthz'):
            body = b'random-log-generator: see /metrics\n'
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_error(404, "Not Found")

    # Silence the default per-request stderr logging.
    def log_message(self, format, *args):  # noqa: A002 — signature is fixed
        logging.debug("prom-server %s - %s", self.address_string(), format % args)


class PrometheusMetricsServer:
    """A tiny background HTTP server that exposes ``/metrics``."""

    def __init__(self, metrics, host='0.0.0.0', port=9090, extra_labels=None):
        if metrics is None:
            raise ValueError("PrometheusMetricsServer requires a Metrics instance")
        self.host = host
        self.port = int(port)
        self._metrics = metrics
        self._extra_labels = dict(extra_labels) if extra_labels else None
        self._server = None
        self._thread = None

    @property
    def address(self):
        if self._server is None:
            return None
        return self._server.server_address

    def start(self):
        if self._server is not None:
            return
        # Bind a fresh subclass per server so handler state never leaks
        # between concurrent instances (e.g. in tests).
        handler_cls = type(
            'PrometheusHandler',
            (_PrometheusHandler,),
            {'metrics': self._metrics, 'extra_labels': self._extra_labels},
        )
        self._server = ThreadingHTTPServer((self.host, self.port), handler_cls)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name='rlg-prometheus',
            daemon=True,
        )
        self._thread.start()
        logging.info("Prometheus metrics endpoint listening on %s:%d",
                     *self._server.server_address)

    def stop(self):
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None
