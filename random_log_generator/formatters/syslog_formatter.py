"""
Syslog formatter for Random Log Generator.

Implements both RFC 5424 (the modern structured syslog format) and the older
RFC 3164 BSD syslog format. The formatter purely produces strings; sending
them over UDP/TCP/TLS is handled by :mod:`random_log_generator.output.syslog_output`.
"""

import datetime
import os
import socket

from random_log_generator.formatters.base import LogFormatter


# Syslog facility codes (subset; see RFC 5424 §6.2.1).
FACILITY_USER = 1

# Mapping of textual log levels to syslog severities (RFC 5424 §6.2.1).
SEVERITY_MAP = {
    'EMERGENCY': 0, 'EMERG': 0,
    'ALERT': 1,
    'CRITICAL': 2, 'CRIT': 2, 'FATAL': 2,
    'ERROR': 3, 'ERR': 3,
    'WARNING': 4, 'WARN': 4,
    'NOTICE': 5,
    'INFO': 6, 'INFORMATIONAL': 6,
    'DEBUG': 7,
}

NILVALUE = '-'

# RFC 3164 month abbreviations (English, fixed-width).
_BSD_MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
               'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def _severity(level):
    """Map an arbitrary log-level string to a syslog severity (default: INFO)."""
    return SEVERITY_MAP.get((level or '').upper(), 6)


def _priority(facility, severity):
    return facility * 8 + severity


class SyslogFormatter(LogFormatter):
    """RFC 5424 / RFC 3164 syslog formatter."""

    SUPPORTED_RFCS = ('5424', '3164')

    def __init__(self, rfc='5424', facility=FACILITY_USER, hostname=None,
                 app_name='random-log-generator', proc_id=None,
                 enterprise_id=None, include_bom=False):
        """
        Args:
            rfc (str): ``'5424'`` (default) or ``'3164'``.
            facility (int): Syslog facility code (0-23).
            hostname (str, optional): Hostname to embed in the message.
                Defaults to :func:`socket.gethostname`.
            app_name (str): APP-NAME field (RFC 5424) / TAG (RFC 3164).
            proc_id (str, optional): PROCID field. Defaults to current PID.
            enterprise_id (str, optional): If provided, structured-data
                payloads are emitted using this IANA enterprise ID.
            include_bom (bool): Prepend the UTF-8 BOM to the MSG field, as
                permitted by RFC 5424 §6.4 to advertise UTF-8 content.
        """
        rfc = str(rfc)
        if rfc not in self.SUPPORTED_RFCS:
            raise ValueError(
                f"Unsupported syslog RFC '{rfc}'. Use one of {self.SUPPORTED_RFCS}."
            )
        if not 0 <= int(facility) <= 23:
            raise ValueError("facility must be between 0 and 23")

        self.rfc = rfc
        self.facility = int(facility)
        self.hostname = hostname or socket.gethostname() or NILVALUE
        self.app_name = app_name or NILVALUE
        self.proc_id = str(proc_id) if proc_id is not None else str(os.getpid())
        self.enterprise_id = enterprise_id
        self.include_bom = bool(include_bom)

    # ---- public API ---------------------------------------------------

    def format_log(self, timestamp, log_level, message, **kwargs):
        severity = _severity(log_level)
        pri = _priority(self.facility, severity)

        if self.rfc == '5424':
            return self._format_5424(pri, timestamp, message, **kwargs)
        return self._format_3164(pri, message, **kwargs)

    # ---- internals ----------------------------------------------------

    def _format_5424(self, pri, timestamp, message, **kwargs):
        ts = self._timestamp_5424(timestamp)
        msgid = kwargs.get('msgid') or NILVALUE
        sd = self._structured_data(kwargs.get('structured_data'))
        msg = message or ''
        if self.include_bom and msg:
            msg = '\ufeff' + msg
        return (
            f"<{pri}>1 {ts} {self.hostname} {self.app_name} "
            f"{self.proc_id} {msgid} {sd} {msg}"
        ).rstrip()

    def _format_3164(self, pri, message, **kwargs):
        # RFC 3164 uses local time with no year and no sub-second precision.
        now = datetime.datetime.now()
        ts = f"{_BSD_MONTHS[now.month - 1]} {now.day:>2d} {now.strftime('%H:%M:%S')}"
        tag = (self.app_name or NILVALUE)[:32]  # RFC 3164 §4.1.3 limits TAG to 32 chars
        return f"<{pri}>{ts} {self.hostname} {tag}[{self.proc_id}]: {message or ''}"

    def _timestamp_5424(self, timestamp):
        """Coerce ``timestamp`` to RFC 5424 / RFC 3339 format."""
        if not timestamp:
            return NILVALUE
        if isinstance(timestamp, datetime.datetime):
            dt = timestamp
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.isoformat()
        # Assume the caller already supplied an ISO-8601 / RFC 3339 string.
        return str(timestamp)

    def _structured_data(self, sd):
        """Render the SD-ELEMENT field. ``sd`` may be a dict or already a string."""
        if not sd:
            return NILVALUE
        if isinstance(sd, str):
            return sd
        if not self.enterprise_id:
            # Without an enterprise ID we cannot legally name a private SD-ID,
            # so we conservatively skip emission.
            return NILVALUE
        sd_id = f"meta@{self.enterprise_id}"
        params = ' '.join(
            f'{k}="{self._escape_sd_value(str(v))}"' for k, v in sd.items()
        )
        return f"[{sd_id} {params}]"

    @staticmethod
    def _escape_sd_value(value):
        # RFC 5424 §6.3.3: PARAM-VALUE must escape ", \ and ].
        return value.replace('\\', '\\\\').replace('"', '\\"').replace(']', '\\]')
