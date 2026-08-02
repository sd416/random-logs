"""
Logfmt formatter (Heroku/Brandur-style ``key=value`` records).
"""

import random

from random_log_generator.formatters.base import LogFormatter


def _quote(value):
    """Return ``value`` quoted only when necessary (per the de-facto logfmt rules)."""
    s = str(value)
    if s == '':
        return '""'
    needs_quote = False
    for ch in s:
        if ch.isspace() or ch in '"=\\':
            needs_quote = True
            break
    if not needs_quote:
        return s
    escaped = s.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'


class LogfmtFormatter(LogFormatter):
    """Format log entries as ``key=value`` pairs separated by spaces."""

    # Order matters for human readability — keep it stable.
    _LEADING_KEYS = ('timestamp', 'level', 'app', 'message')

    def __init__(self, app_names=None, extra_fields=None):
        self.app_names = list(app_names) if app_names else []
        self.extra_fields = dict(extra_fields) if extra_fields else {}

    def format_log(self, timestamp, log_level, message, **kwargs):
        record = {
            'timestamp': timestamp,
            'level': log_level,
            'message': message,
        }

        if self.app_names:
            record['app'] = random.choice(self.app_names)

        record.update(self.extra_fields)
        for key, value in kwargs.items():
            if value is not None:
                record[key] = value

        ordered_keys = [k for k in self._LEADING_KEYS if k in record]
        ordered_keys += [k for k in record if k not in self._LEADING_KEYS]
        return ' '.join(f"{k}={_quote(record[k])}" for k in ordered_keys)
