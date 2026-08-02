"""
Factory helpers for constructing log formatters from configuration.

The factory keeps :mod:`random_log_generator.core.generator` ignorant of the
specific formatter classes, so adding a new format only requires updating
this module and the ``formatters/__init__.py`` re-exports.
"""

import logging

from random_log_generator.formatters.http import HTTPFormatter
from random_log_generator.formatters.custom import CustomFormatter
from random_log_generator.formatters.json_formatter import JSONFormatter
from random_log_generator.formatters.syslog_formatter import SyslogFormatter
from random_log_generator.formatters.logfmt import LogfmtFormatter
from random_log_generator.formatters.clf import CLFFormatter, CombinedLogFormatter


# Public mapping. Keys are the values accepted by the ``log_format`` config key.
SUPPORTED_FORMATS = (
    'http', 'custom', 'json', 'ndjson',
    'syslog', 'syslog5424', 'syslog3164',
    'logfmt', 'clf', 'combined',
)


def _select_format_name(config_params):
    """Determine the formatter name from config (back-compatible)."""
    explicit = config_params.get('log_format')
    if explicit:
        return str(explicit).lower()
    # Legacy boolean toggle.
    return 'http' if config_params.get('http_format_logs') else 'custom'


def create_formatter(config_params, http_status_codes, log_levels):
    """
    Create a :class:`LogFormatter` instance from configuration.

    Args:
        config_params (dict): The ``CONFIG`` block of the YAML configuration.
        http_status_codes (dict): HTTP status code → message mapping.
        log_levels (list): List of textual log levels.

    Returns:
        LogFormatter: Instantiated formatter.

    Raises:
        ValueError: If ``log_format`` references an unknown formatter.
    """
    name = _select_format_name(config_params)
    formatter_opts = config_params.get('formatter_options', {}) or {}
    app_names = config_params.get('custom_app_names') or []

    if name == 'http':
        return HTTPFormatter(http_status_codes)

    if name == 'custom':
        return CustomFormatter(
            config_params.get('custom_log_format', '${timestamp} ${log_level} ${message}'),
            app_names,
        )

    if name in ('json', 'ndjson'):
        return JSONFormatter(
            app_names=app_names,
            extra_fields=formatter_opts.get('extra_fields'),
            sort_keys=formatter_opts.get('sort_keys', False),
            ensure_ascii=formatter_opts.get('ensure_ascii', False),
        )

    if name in ('syslog', 'syslog5424'):
        return SyslogFormatter(
            rfc='5424',
            facility=formatter_opts.get('facility', 1),
            hostname=formatter_opts.get('hostname'),
            app_name=formatter_opts.get('app_name', 'random-log-generator'),
            proc_id=formatter_opts.get('proc_id'),
            enterprise_id=formatter_opts.get('enterprise_id'),
            include_bom=formatter_opts.get('include_bom', False),
        )

    if name == 'syslog3164':
        return SyslogFormatter(
            rfc='3164',
            facility=formatter_opts.get('facility', 1),
            hostname=formatter_opts.get('hostname'),
            app_name=formatter_opts.get('app_name', 'random-log-generator'),
            proc_id=formatter_opts.get('proc_id'),
        )

    if name == 'logfmt':
        return LogfmtFormatter(
            app_names=app_names,
            extra_fields=formatter_opts.get('extra_fields'),
        )

    if name == 'clf':
        return CLFFormatter(
            status_codes=http_status_codes,
            paths=formatter_opts.get('paths'),
        )

    if name == 'combined':
        return CombinedLogFormatter(
            status_codes=http_status_codes,
            paths=formatter_opts.get('paths'),
        )

    raise ValueError(
        f"Unsupported log_format '{name}'. Supported formats: {SUPPORTED_FORMATS}"
    )


def initialize_formatters(config_params, http_status_codes, log_levels):
    """Backwards-compatible wrapper used by older call sites and tests."""
    formatter = create_formatter(config_params, http_status_codes, log_levels)
    logging.debug("Initialised formatter %s", type(formatter).__name__)
    return formatter, log_levels
