"""
Syslog output handler — pushes pre-formatted syslog messages over UDP/TCP.

For TCP transport we apply RFC 6587 octet-counting framing so messages are
unambiguously delimited, which is what most syslog daemons (rsyslog,
syslog-ng) expect on stream connections.
"""

import logging

from random_log_generator.output.socket_output import (
    SocketOutputHandler,
    SUPPORTED_PROTOCOLS,
)


class SyslogOutputHandler(SocketOutputHandler):
    """Send syslog messages to a remote syslog server over UDP or TCP."""

    def __init__(self, host, port=514, protocol='udp',
                 framing='octet-counting', connect_timeout=5.0):
        protocol = (protocol or 'udp').lower()
        if protocol not in SUPPORTED_PROTOCOLS:
            raise ValueError(
                f"Unsupported syslog protocol '{protocol}'. "
                f"Choose one of {SUPPORTED_PROTOCOLS}."
            )
        framing = (framing or 'octet-counting').lower()
        if framing not in ('octet-counting', 'non-transparent'):
            raise ValueError(
                "framing must be 'octet-counting' or 'non-transparent'"
            )
        # For UDP each datagram is naturally framed, so we always use a
        # newline terminator regardless of what the user configured.
        terminator = '\n' if protocol == 'udp' else ''
        super().__init__(host=host, port=port, protocol=protocol,
                         terminator=terminator, connect_timeout=connect_timeout)
        self.framing = framing
        logging.info("SyslogOutputHandler initialised: %s://%s:%d framing=%s",
                     protocol, host, self.port, framing)

    def _send(self, payload, log_lines):
        # UDP path falls back to the parent (datagram per line).
        if self.protocol == 'udp':
            return super()._send(payload, log_lines)

        # TCP framing — overwrite parent behaviour.
        if self.framing == 'octet-counting':
            for line in log_lines:
                encoded = line.encode('utf-8')
                # RFC 6587 §3.4.1: ``MSG-LEN SP MSG``.
                self._sock.sendall(f"{len(encoded)} ".encode('ascii') + encoded)
        else:
            # Non-transparent framing uses a trailing newline per message.
            for line in log_lines:
                self._sock.sendall(line.encode('utf-8') + b'\n')
        return True
