"""
Tests for the new formatters, sinks, and Prometheus metrics endpoint.
"""

import gzip
import io
import json
import socket
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from random_log_generator.formatters.clf import CLFFormatter, CombinedLogFormatter
from random_log_generator.formatters.factory import (
    SUPPORTED_FORMATS,
    create_formatter,
)
from random_log_generator.formatters.json_formatter import JSONFormatter
from random_log_generator.formatters.logfmt import LogfmtFormatter
from random_log_generator.formatters.syslog_formatter import (
    SEVERITY_MAP,
    SyslogFormatter,
)
from random_log_generator.metrics.collector import Metrics
from random_log_generator.metrics.prometheus import (
    PrometheusMetricsServer,
    render_metrics,
)
from random_log_generator.output.factory import (
    SUPPORTED_SINK_TYPES,
    create_output_handler,
)
from random_log_generator.output.http_output import HTTPOutputHandler
from random_log_generator.output.multi_output import MultiOutputHandler
from random_log_generator.output.socket_output import SocketOutputHandler
from random_log_generator.output.syslog_output import SyslogOutputHandler
from random_log_generator.config.validators import validate_config


# ---------------------------------------------------------------------------
# Formatter tests
# ---------------------------------------------------------------------------


class TestJSONFormatter(unittest.TestCase):
    def test_basic_record_is_valid_json(self):
        formatter = JSONFormatter()
        line = formatter.format_log("2024-01-01T00:00:00Z", "INFO", "hello")
        record = json.loads(line)
        self.assertEqual(record["timestamp"], "2024-01-01T00:00:00Z")
        self.assertEqual(record["level"], "INFO")
        self.assertEqual(record["message"], "hello")
        # Compact serialization (no spaces).
        self.assertNotIn(", ", line)
        self.assertNotIn(": ", line)

    def test_extra_fields_and_app_names(self):
        formatter = JSONFormatter(
            app_names=["only-one"], extra_fields={"env": "prod"}
        )
        record = json.loads(formatter.format_log("ts", "WARN", "msg"))
        self.assertEqual(record["env"], "prod")
        self.assertEqual(record["app"], "only-one")

    def test_top_level_keys_promoted(self):
        formatter = JSONFormatter()
        record = json.loads(
            formatter.format_log(
                "ts", "INFO", "m",
                ip_address="1.2.3.4",
                trace_id="abc",
                custom="ctx-value",
            )
        )
        self.assertEqual(record["ip_address"], "1.2.3.4")
        self.assertEqual(record["trace_id"], "abc")
        # Non-promoted kwargs land in context.
        self.assertEqual(record["context"], {"custom": "ctx-value"})

    def test_unicode_message_round_trips(self):
        formatter = JSONFormatter()
        record = json.loads(formatter.format_log("ts", "INFO", "héllo 🌍"))
        self.assertEqual(record["message"], "héllo 🌍")


class TestSyslogFormatter(unittest.TestCase):
    def test_rfc5424_basic(self):
        f = SyslogFormatter(
            rfc="5424", hostname="host1", app_name="myapp", proc_id="42"
        )
        line = f.format_log("2024-01-01T00:00:00+00:00", "INFO", "hello")
        # PRI = facility(1)*8 + severity(6) = 14
        self.assertTrue(line.startswith("<14>1 2024-01-01T00:00:00+00:00 host1 myapp 42 - - hello"))

    def test_rfc5424_with_structured_data(self):
        f = SyslogFormatter(
            rfc="5424", hostname="h", app_name="a", proc_id="1",
            enterprise_id="32473",
        )
        line = f.format_log(
            "2024-01-01T00:00:00+00:00", "ERROR", "boom",
            structured_data={"key": 'value with "quote'},
        )
        self.assertIn('[meta@32473 key="value with \\"quote"]', line)

    def test_rfc3164_format(self):
        f = SyslogFormatter(rfc="3164", hostname="h", app_name="a", proc_id="9")
        line = f.format_log("ignored", "WARN", "msg")
        # PRI for facility=1, severity(WARN)=4 -> 12
        self.assertTrue(line.startswith("<12>"))
        self.assertIn(" h a[9]: msg", line)

    def test_severity_mapping_unknown_defaults_to_info(self):
        self.assertEqual(SEVERITY_MAP["INFO"], 6)
        f = SyslogFormatter(rfc="5424", hostname="h", proc_id="1")
        line = f.format_log("2024-01-01T00:00:00Z", "WEIRD", "m")
        # facility(1)*8 + severity(6) = 14
        self.assertTrue(line.startswith("<14>"))

    def test_invalid_rfc_raises(self):
        with self.assertRaises(ValueError):
            SyslogFormatter(rfc="9999")


class TestLogfmtFormatter(unittest.TestCase):
    def test_simple_pairs(self):
        f = LogfmtFormatter()
        line = f.format_log("ts", "INFO", "hello")
        self.assertEqual(line, 'timestamp=ts level=INFO message=hello')

    def test_quoting_and_escaping(self):
        f = LogfmtFormatter()
        line = f.format_log("t s", "INFO", 'has "quotes" and spaces')
        self.assertIn('timestamp="t s"', line)
        self.assertIn('message="has \\"quotes\\" and spaces"', line)

    def test_extra_fields(self):
        f = LogfmtFormatter(extra_fields={"region": "us-east-1"})
        line = f.format_log("ts", "INFO", "m", request_id="abc")
        self.assertIn("region=us-east-1", line)
        self.assertIn("request_id=abc", line)


class TestCLFFormatters(unittest.TestCase):
    def test_clf_shape(self):
        f = CLFFormatter()
        line = f.format_log(
            "2024-01-01T12:00:00+00:00", "INFO",
            ip_address="10.0.0.1", method="GET", path="/", status_code="200",
            response_size=42,
        )
        # Shape: ip - - [date] "GET / HTTP/1.1" 200 42
        self.assertTrue(line.startswith("10.0.0.1 - - ["))
        self.assertIn('"GET / HTTP/1.1" 200 42', line)
        self.assertIn("/Jan/2024:", line)

    def test_combined_appends_referer_and_ua(self):
        f = CombinedLogFormatter()
        line = f.format_log(
            "2024-01-01T12:00:00+00:00", "INFO",
            ip_address="10.0.0.1", method="GET", path="/", status_code=404,
            response_size=10, referer="https://ref/", user_agent="UA-1",
        )
        self.assertTrue(line.endswith('"https://ref/" "UA-1"'))
        self.assertIn(" 404 10 ", line)


class TestFormatterFactory(unittest.TestCase):
    def test_factory_supports_all_advertised_formats(self):
        codes = {"200 OK": ["ok"]}
        for name in SUPPORTED_FORMATS:
            cfg = {"log_format": name, "custom_log_format": "${timestamp}"}
            formatter = create_formatter(cfg, codes, ["INFO"])
            line = formatter.format_log("2024-01-01T00:00:00+00:00", "INFO", "msg")
            self.assertIsInstance(line, str)
            self.assertGreater(len(line), 0)

    def test_factory_rejects_unknown(self):
        with self.assertRaises(ValueError):
            create_formatter({"log_format": "totally-bogus"}, {}, ["INFO"])

    def test_legacy_boolean_toggle_still_selects_http(self):
        formatter = create_formatter(
            {"http_format_logs": True}, {"200 OK": ["ok"]}, ["INFO"]
        )
        self.assertEqual(type(formatter).__name__, "HTTPFormatter")


# ---------------------------------------------------------------------------
# Output / sink tests
# ---------------------------------------------------------------------------


class _EchoingHTTPHandler(BaseHTTPRequestHandler):
    """Minimal request handler that records requests for assertions."""

    received = []  # class-level: shared across handler instances

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        self.received.append({
            "path": self.path,
            "headers": {k: v for k, v in self.headers.items()},
            "body": body,
        })
        if self.path == "/fail-once":
            # Succeed on the second attempt onward.
            already_seen = sum(
                1 for r in self.received if r["path"] == "/fail-once"
            )
            if already_seen == 1:
                self.send_error(503, "try again")
                return
        if self.path == "/permanent-fail":
            self.send_error(404, "nope")
            return
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):  # silence
        pass


def _start_http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EchoingHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class TestHTTPOutputHandler(unittest.TestCase):
    def setUp(self):
        _EchoingHTTPHandler.received = []
        self.server, self.thread = _start_http_server()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _url(self, path="/"):
        host, port = self.server.server_address
        return f"http://{host}:{port}{path}"

    def test_generic_mode_posts_json_array(self):
        handler = HTTPOutputHandler(
            url=self._url("/ingest"), mode="generic",
            gzip_enabled=False,
        )
        ok = handler.write(["line-1", "line-2"])
        self.assertTrue(ok)
        self.assertEqual(len(_EchoingHTTPHandler.received), 1)
        body = json.loads(_EchoingHTTPHandler.received[0]["body"])
        self.assertEqual(body, {"logs": ["line-1", "line-2"]})

    def test_loki_mode_payload_shape(self):
        handler = HTTPOutputHandler(
            url=self._url("/loki/api/v1/push"), mode="loki",
            loki_labels={"job": "rlg", "env": "test"},
            gzip_enabled=False,
        )
        self.assertTrue(handler.write(["line-1"]))
        body = json.loads(_EchoingHTTPHandler.received[0]["body"])
        self.assertIn("streams", body)
        self.assertEqual(body["streams"][0]["stream"], {"job": "rlg", "env": "test"})
        self.assertEqual(len(body["streams"][0]["values"]), 1)
        ts, line = body["streams"][0]["values"][0]
        self.assertTrue(ts.isdigit())
        self.assertEqual(line, "line-1")

    def test_splunk_hec_mode_uses_splunk_authorization(self):
        handler = HTTPOutputHandler(
            url=self._url("/services/collector/event"),
            mode="splunk_hec", auth_token="SECRET",
            splunk_index="main", splunk_sourcetype="rlg:json",
            gzip_enabled=False,
        )
        self.assertTrue(handler.write(["{\"event\":1}", "{\"event\":2}"]))
        rec = _EchoingHTTPHandler.received[0]
        self.assertEqual(rec["headers"].get("Authorization"), "Splunk SECRET")
        # NDJSON envelopes -> two lines
        envelopes = [json.loads(line) for line in rec["body"].splitlines()]
        self.assertEqual(len(envelopes), 2)
        self.assertEqual(envelopes[0]["index"], "main")
        self.assertEqual(envelopes[0]["sourcetype"], "rlg:json")

    def test_gzip_compression_applied_for_large_payload(self):
        big_lines = ["x" * 1024 for _ in range(10)]  # ~10 KB raw
        handler = HTTPOutputHandler(
            url=self._url("/big"), mode="generic", gzip_enabled=True,
        )
        self.assertTrue(handler.write(big_lines))
        rec = _EchoingHTTPHandler.received[0]
        self.assertEqual(rec["headers"].get("Content-Encoding"), "gzip")
        decoded = json.loads(gzip.decompress(rec["body"]))
        self.assertEqual(decoded["logs"], big_lines)

    def test_retries_on_5xx_then_succeeds(self):
        handler = HTTPOutputHandler(
            url=self._url("/fail-once"), mode="generic",
            max_retries=3, backoff_seconds=0.01, gzip_enabled=False,
        )
        self.assertTrue(handler.write(["x"]))
        # one failure + one success = 2 received
        paths = [r["path"] for r in _EchoingHTTPHandler.received]
        self.assertEqual(paths, ["/fail-once", "/fail-once"])

    def test_permanent_4xx_is_not_retried(self):
        handler = HTTPOutputHandler(
            url=self._url("/permanent-fail"), mode="generic",
            max_retries=5, backoff_seconds=0.01, gzip_enabled=False,
        )
        self.assertFalse(handler.write(["x"]))
        # Only one attempt should have been made for a 404.
        self.assertEqual(
            sum(1 for r in _EchoingHTTPHandler.received if r["path"] == "/permanent-fail"),
            1,
        )

    def test_invalid_constructor_args(self):
        with self.assertRaises(ValueError):
            HTTPOutputHandler(url="", mode="generic")
        with self.assertRaises(ValueError):
            HTTPOutputHandler(url="http://x", mode="bogus")


class TestSocketOutputHandler(unittest.TestCase):
    def test_udp_round_trip(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        sock.settimeout(2.0)
        host, port = sock.getsockname()
        try:
            handler = SocketOutputHandler(
                host=host, port=port, protocol="udp",
            )
            handler.write(["hello", "world"])
            received = []
            for _ in range(2):
                data, _addr = sock.recvfrom(4096)
                received.append(data.decode("utf-8").strip())
            self.assertEqual(sorted(received), ["hello", "world"])
            handler.close()
        finally:
            sock.close()

    def test_tcp_round_trip(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        srv.settimeout(2.0)
        host, port = srv.getsockname()
        received = []

        def accept():
            conn, _ = srv.accept()
            with conn:
                conn.settimeout(2.0)
                buf = b""
                while True:
                    try:
                        chunk = conn.recv(4096)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    buf += chunk
                received.append(buf)

        t = threading.Thread(target=accept, daemon=True)
        t.start()
        try:
            handler = SocketOutputHandler(host=host, port=port, protocol="tcp")
            handler.write(["a", "b", "c"])
            handler.close()
        finally:
            t.join(timeout=3)
            srv.close()
        self.assertEqual(received, [b"a\nb\nc\n"])

    def test_unsupported_protocol(self):
        with self.assertRaises(ValueError):
            SocketOutputHandler(host="127.0.0.1", port=1, protocol="sctp")


class TestSyslogOutputHandler(unittest.TestCase):
    def test_tcp_octet_counting_framing(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        srv.settimeout(2.0)
        host, port = srv.getsockname()
        received = []

        def accept():
            conn, _ = srv.accept()
            with conn:
                conn.settimeout(2.0)
                buf = b""
                while True:
                    try:
                        chunk = conn.recv(4096)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    buf += chunk
                received.append(buf)

        t = threading.Thread(target=accept, daemon=True)
        t.start()
        try:
            handler = SyslogOutputHandler(
                host=host, port=port, protocol="tcp",
                framing="octet-counting",
            )
            handler.write(["<14>1 ts h a 1 - - hi", "<14>1 ts h a 1 - - bye"])
            handler.close()
        finally:
            t.join(timeout=3)
            srv.close()
        self.assertEqual(
            received,
            [b"21 <14>1 ts h a 1 - - hi22 <14>1 ts h a 1 - - bye"],
        )


class TestMultiOutputHandler(unittest.TestCase):
    def test_fan_out_and_close(self):
        h1 = mock.MagicMock()
        h2 = mock.MagicMock()
        h1.write.return_value = True
        h2.write.return_value = True
        multi = MultiOutputHandler([h1, h2])
        self.assertTrue(multi.write(["x"]))
        h1.write.assert_called_once_with(["x"])
        h2.write.assert_called_once_with(["x"])
        multi.close()
        h1.close.assert_called_once()
        h2.close.assert_called_once()

    def test_require_all_success_semantics(self):
        good = mock.MagicMock(); good.write.return_value = True
        bad = mock.MagicMock(); bad.write.return_value = False
        self.assertFalse(
            MultiOutputHandler([good, bad], require_all_success=True).write(["x"])
        )
        self.assertTrue(
            MultiOutputHandler([good, bad], require_all_success=False).write(["x"])
        )


class TestOutputFactory(unittest.TestCase):
    def test_legacy_console_path(self):
        handler = create_output_handler({
            "write_to_file": False,
            "log_file_path": "ignored",
        })
        self.assertEqual(type(handler).__name__, "ConsoleOutputHandler")

    def test_sinks_list_returns_multi(self):
        handler = create_output_handler({
            "sinks": [
                {"type": "console"},
                {"type": "console"},
            ],
        })
        self.assertIsInstance(handler, MultiOutputHandler)

    def test_supported_sink_types_constant(self):
        for name in ("file", "console", "http", "socket", "syslog"):
            self.assertIn(name, SUPPORTED_SINK_TYPES)


# ---------------------------------------------------------------------------
# Validator tests
# ---------------------------------------------------------------------------


def _baseline_config():
    """Return a minimally-valid full config dict for validator tests."""
    return {
        "CONFIG": {
            "duration_normal": 1, "duration_peak": 1,
            "rate_normal_min": 0.0, "rate_normal_max": 0.1, "rate_peak": 0.5,
            "log_line_size_estimate": 100, "user_agent_pool_size": 10,
            "max_segment_duration_normal": 5,
            "base_exit_probability": 0.05,
            "rate_change_probability": 0.1,
            "rate_change_max_percentage": 0.1,
            "write_to_file": False,
            "log_file_path": "logs.txt",
            "log_rotation_enabled": False,
            "log_rotation_size": 50,
            "http_format_logs": True,
            "stop_after_seconds": -1,
            "custom_app_names": [],
            "custom_log_format": "${timestamp} ${log_level} ${message}",
            "logging_level": "INFO",
        },
    }


class TestValidatorExtensions(unittest.TestCase):
    def test_baseline_passes(self):
        validate_config(_baseline_config())

    def test_log_format_value_validated(self):
        cfg = _baseline_config()
        cfg["CONFIG"]["log_format"] = "json"
        validate_config(cfg)
        cfg["CONFIG"]["log_format"] = "bogus"
        with self.assertRaises(ValueError):
            validate_config(cfg)

    def test_sinks_validated(self):
        cfg = _baseline_config()
        cfg["CONFIG"]["sinks"] = [{"type": "http", "url": "http://x"}]
        validate_config(cfg)

        cfg["CONFIG"]["sinks"] = [{"type": "http"}]  # missing url
        with self.assertRaises(ValueError):
            validate_config(cfg)

        cfg["CONFIG"]["sinks"] = [{"type": "socket", "host": "h"}]  # missing port
        with self.assertRaises(ValueError):
            validate_config(cfg)

    def test_prometheus_validated(self):
        cfg = _baseline_config()
        cfg["CONFIG"]["prometheus"] = {"enabled": True, "port": 9090}
        validate_config(cfg)
        cfg["CONFIG"]["prometheus"] = {"port": "nope"}
        with self.assertRaises(ValueError):
            validate_config(cfg)


# ---------------------------------------------------------------------------
# Prometheus endpoint tests
# ---------------------------------------------------------------------------


class TestPrometheus(unittest.TestCase):
    def test_render_metrics_text_format(self):
        m = Metrics()
        m.update(5, 1024)
        text = render_metrics(m, extra_labels={"job": "rlg"})
        self.assertIn("# TYPE rlg_logs_total counter", text)
        self.assertIn('rlg_logs_total{job="rlg"} 5', text)
        self.assertIn('rlg_rate_mb_s{job="rlg",kind="avg"}', text)

    def test_endpoint_serves_metrics(self):
        m = Metrics()
        m.update(3, 3000)
        server = PrometheusMetricsServer(m, host="127.0.0.1", port=0)
        server.start()
        try:
            host, port = server.address
            with urllib.request.urlopen(f"http://{host}:{port}/metrics", timeout=2) as resp:
                body = resp.read().decode("utf-8")
                self.assertEqual(resp.status, 200)
                self.assertIn("rlg_logs_total", body)
                self.assertIn("rlg_bytes_total", body)
            with urllib.request.urlopen(f"http://{host}:{port}/", timeout=2) as resp:
                self.assertEqual(resp.status, 200)
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(f"http://{host}:{port}/missing", timeout=2)
        finally:
            server.stop()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
