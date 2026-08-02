"""
Common Log Format (CLF) and Combined Log Format formatters (Apache/Nginx).

Reference: https://httpd.apache.org/docs/current/logs.html#accesslog
"""

import datetime
import random

from random_log_generator.formatters.base import LogFormatter
from random_log_generator.utils.ip_generator import generate_ip_address
from random_log_generator.utils.user_agents import generate_random_user_agent


_CLF_MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
               'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')

_HTTP_METHODS = ('GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD')
_DEFAULT_PATHS = ('/', '/api/v1/users', '/api/v1/orders', '/health',
                  '/metrics', '/login', '/static/app.js', '/products')


def _format_clf_time(timestamp):
    """Return ``timestamp`` formatted as ``[10/Oct/2000:13:55:36 -0700]``."""
    if isinstance(timestamp, datetime.datetime):
        dt = timestamp
    else:
        try:
            dt = datetime.datetime.fromisoformat(str(timestamp))
        except (TypeError, ValueError):
            dt = datetime.datetime.now(datetime.timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    tz = dt.strftime('%z') or '+0000'
    return (
        f"[{dt.day:02d}/{_CLF_MONTHS[dt.month - 1]}/{dt.year}:"
        f"{dt.strftime('%H:%M:%S')} {tz}]"
    )


def _status_to_int(status_code):
    """Best-effort conversion of ``'200 OK'`` / ``'200'`` / ``200`` to an int."""
    if status_code is None:
        return 200
    if isinstance(status_code, int):
        return status_code
    text = str(status_code).strip().split()[0]
    try:
        return int(text)
    except ValueError:
        return 200


class CLFFormatter(LogFormatter):
    """Apache/Nginx Common Log Format (CLF) formatter."""

    def __init__(self, status_codes=None, paths=None):
        self.status_code_list = list(status_codes.keys()) if status_codes else []
        self.paths = list(paths) if paths else list(_DEFAULT_PATHS)

    def _build_request(self, kwargs):
        method = kwargs.get('method') or random.choice(_HTTP_METHODS)
        path = kwargs.get('path') or random.choice(self.paths)
        protocol = kwargs.get('protocol') or 'HTTP/1.1'
        return f"{method} {path} {protocol}"

    def _select_status(self, kwargs):
        status = kwargs.get('status_code')
        if status is None and self.status_code_list:
            status = random.choice(self.status_code_list)
        return _status_to_int(status)

    def format_log(self, timestamp, log_level, message=None, **kwargs):
        ip = kwargs.get('ip_address') or generate_ip_address()
        ident = kwargs.get('ident') or '-'
        user = kwargs.get('user') or '-'
        ts = _format_clf_time(timestamp)
        request = self._build_request(kwargs)
        status = self._select_status(kwargs)
        size = kwargs.get('response_size')
        if size is None:
            size = random.randint(20, 8192)
        return f'{ip} {ident} {user} {ts} "{request}" {status} {size}'


class CombinedLogFormatter(CLFFormatter):
    """Apache/Nginx Combined Log Format (CLF + Referer + User-Agent)."""

    def format_log(self, timestamp, log_level, message=None, **kwargs):
        base = super().format_log(timestamp, log_level, message, **kwargs)
        referer = kwargs.get('referer') or '-'
        user_agent = kwargs.get('user_agent') or generate_random_user_agent()
        return f'{base} "{referer}" "{user_agent}"'
