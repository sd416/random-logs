"""
Factory helpers for constructing output handlers (sinks) from configuration.

Configuration shape (all keys live inside the ``CONFIG`` block):

    write_to_file: true            # legacy file/console toggle (kept)
    log_file_path: 'logs.txt'
    log_rotation_enabled: true
    log_rotation_size: 50

    sinks:                         # NEW: optional list of additional sinks
      - type: http
        url: https://example.com/ingest
        mode: loki                 # generic | loki | splunk_hec
        auth_token: ${TOKEN}
      - type: socket
        host: 127.0.0.1
        port: 5170
        protocol: tcp
      - type: syslog
        host: syslog.local
        port: 514
        protocol: udp

The legacy ``write_to_file``/console behaviour is preserved: when no ``sinks``
list is given the factory returns a single :class:`FileOutputHandler` or
:class:`ConsoleOutputHandler` exactly as before.
"""

import logging

from random_log_generator.output.base import OutputHandler
from random_log_generator.output.console_output import ConsoleOutputHandler
from random_log_generator.output.file_output import FileOutputHandler
from random_log_generator.output.http_output import HTTPOutputHandler
from random_log_generator.output.socket_output import SocketOutputHandler
from random_log_generator.output.syslog_output import SyslogOutputHandler
from random_log_generator.output.multi_output import MultiOutputHandler


SUPPORTED_SINK_TYPES = ('file', 'console', 'http', 'socket', 'syslog')


def _build_sink(spec):
    """Instantiate a single sink from its dict spec."""
    if not isinstance(spec, dict):
        raise ValueError(f"Sink specification must be a mapping, got {type(spec).__name__}")
    sink_type = (spec.get('type') or '').lower()
    if sink_type not in SUPPORTED_SINK_TYPES:
        raise ValueError(
            f"Unsupported sink type '{sink_type}'. "
            f"Choose one of {SUPPORTED_SINK_TYPES}."
        )

    if sink_type == 'file':
        return FileOutputHandler(
            file_path=spec.get('path') or spec.get('file_path', 'logs.txt'),
            rotation_enabled=spec.get('rotation_enabled', False),
            rotation_size=spec.get('rotation_size', 10),
        )

    if sink_type == 'console':
        return ConsoleOutputHandler()

    if sink_type == 'http':
        return HTTPOutputHandler(
            url=spec['url'],
            mode=spec.get('mode', 'generic'),
            headers=spec.get('headers'),
            auth_token=spec.get('auth_token'),
            timeout=spec.get('timeout', 5.0),
            max_retries=spec.get('max_retries', 3),
            backoff_seconds=spec.get('backoff_seconds', 0.5),
            verify_tls=spec.get('verify_tls', True),
            gzip_enabled=spec.get('gzip_enabled', True),
            payload_key=spec.get('payload_key', 'logs'),
            loki_labels=spec.get('loki_labels'),
            splunk_index=spec.get('splunk_index'),
            splunk_source=spec.get('splunk_source'),
            splunk_sourcetype=spec.get('splunk_sourcetype'),
            splunk_host=spec.get('splunk_host'),
        )

    if sink_type == 'socket':
        return SocketOutputHandler(
            host=spec['host'],
            port=spec['port'],
            protocol=spec.get('protocol', 'tcp'),
            terminator=spec.get('terminator', '\n'),
            connect_timeout=spec.get('connect_timeout', 5.0),
        )

    # syslog
    return SyslogOutputHandler(
        host=spec['host'],
        port=spec.get('port', 514),
        protocol=spec.get('protocol', 'udp'),
        framing=spec.get('framing', 'octet-counting'),
        connect_timeout=spec.get('connect_timeout', 5.0),
    )


def create_output_handler(config_params):
    """
    Build the output handler tree from configuration.

    Args:
        config_params (dict): The ``CONFIG`` block of the YAML configuration.

    Returns:
        OutputHandler: A single handler (possibly a :class:`MultiOutputHandler`).
    """
    sinks_spec = config_params.get('sinks')

    if not sinks_spec:
        # Legacy single-handler path.
        if config_params.get('write_to_file', True):
            return FileOutputHandler(
                file_path=config_params['log_file_path'],
                rotation_enabled=config_params.get('log_rotation_enabled', False),
                rotation_size=config_params.get('log_rotation_size', 10),
            )
        return ConsoleOutputHandler()

    if not isinstance(sinks_spec, list) or not sinks_spec:
        raise ValueError("'sinks' must be a non-empty list of sink specifications")

    handlers = []
    for spec in sinks_spec:
        handler = _build_sink(spec)
        if not isinstance(handler, OutputHandler):  # pragma: no cover - defensive
            raise TypeError(
                f"Sink builder returned non-OutputHandler: {type(handler).__name__}"
            )
        handlers.append(handler)

    if len(handlers) == 1:
        return handlers[0]

    require_all = config_params.get('sinks_require_all_success', True)
    logging.info("MultiOutputHandler created with %d sink(s)", len(handlers))
    return MultiOutputHandler(handlers, require_all_success=require_all)
