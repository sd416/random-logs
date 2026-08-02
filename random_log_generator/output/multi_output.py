"""
Composite output handler that fans batches out to multiple sinks.
"""

import logging

from random_log_generator.output.base import OutputHandler


class MultiOutputHandler(OutputHandler):
    """Write each batch to every wrapped handler.

    Args:
        handlers (Iterable[OutputHandler]): Sinks to fan out to.
        require_all_success (bool): If True (default), :meth:`write` returns
            False unless every handler succeeded. If False, returns True as
            long as at least one handler succeeded — useful when the file
            sink is the source of truth and remote sinks are best-effort.
    """

    def __init__(self, handlers, require_all_success=True):
        self.handlers = list(handlers)
        if not self.handlers:
            raise ValueError("MultiOutputHandler requires at least one handler")
        self.require_all_success = bool(require_all_success)

    def write(self, log_lines):
        results = []
        for handler in self.handlers:
            try:
                results.append(bool(handler.write(log_lines)))
            except Exception as exc:  # pragma: no cover - defensive
                logging.error("Handler %s raised during write: %s",
                              type(handler).__name__, exc)
                results.append(False)
        if self.require_all_success:
            return all(results)
        return any(results)

    def close(self):
        for handler in self.handlers:
            try:
                handler.close()
            except Exception as exc:  # pragma: no cover - defensive
                logging.error("Handler %s raised during close: %s",
                              type(handler).__name__, exc)
