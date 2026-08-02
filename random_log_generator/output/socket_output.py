"""
Raw TCP/UDP socket output handler — useful for testing log shippers such as
Fluent Bit, Vector and Logstash.
"""

import logging
import socket

from random_log_generator.output.base import OutputHandler


SUPPORTED_PROTOCOLS = ('tcp', 'udp')


class SocketOutputHandler(OutputHandler):
    """Send log lines to a remote endpoint over TCP or UDP."""

    def __init__(self, host, port, protocol='tcp', terminator='\n',
                 connect_timeout=5.0):
        protocol = (protocol or 'tcp').lower()
        if protocol not in SUPPORTED_PROTOCOLS:
            raise ValueError(
                f"Unsupported socket protocol '{protocol}'. "
                f"Choose one of {SUPPORTED_PROTOCOLS}."
            )
        if not host:
            raise ValueError("SocketOutputHandler requires a non-empty host")

        self.host = host
        self.port = int(port)
        self.protocol = protocol
        self.terminator = terminator
        self.connect_timeout = float(connect_timeout)
        self._sock = None
        self._connect()

    # ---- public API ---------------------------------------------------

    def write(self, log_lines):
        if not log_lines:
            return True
        # Concatenate once to minimise syscalls.
        payload = (self.terminator.join(log_lines) + self.terminator).encode('utf-8')
        try:
            return self._send(payload, log_lines)
        except (OSError, socket.error) as exc:
            logging.warning("Socket sink error (%s); reconnecting: %s", self.protocol, exc)
            self._safe_close()
            try:
                self._connect()
                return self._send(payload, log_lines)
            except (OSError, socket.error) as exc2:
                logging.error("Socket sink permanent error: %s", exc2)
                return False

    def close(self):
        self._safe_close()

    # ---- internals ----------------------------------------------------

    def _connect(self):
        if self.protocol == 'tcp':
            self._sock = socket.create_connection(
                (self.host, self.port), timeout=self.connect_timeout,
            )
            # Keep the socket usable for repeated writes; switch back to blocking.
            self._sock.settimeout(None)
        else:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def _send(self, payload, log_lines):
        if self.protocol == 'tcp':
            self._sock.sendall(payload)
            return True
        # UDP: each log line is sent as its own datagram so it survives MTU
        # boundaries — concatenating would risk truncation.
        for line in log_lines:
            self._sock.sendto((line + self.terminator).encode('utf-8'),
                              (self.host, self.port))
        return True

    def _safe_close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            finally:
                self._sock = None
