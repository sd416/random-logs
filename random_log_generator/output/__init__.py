"""
Output module for Random Log Generator.

This module contains classes for handling log output to different destinations,
such as files or the console, as well as log rotation functionality.
"""

from random_log_generator.output.base import OutputHandler
from random_log_generator.output.file_output import FileOutputHandler
from random_log_generator.output.console_output import ConsoleOutputHandler
from random_log_generator.output.http_output import HTTPOutputHandler
from random_log_generator.output.socket_output import SocketOutputHandler
from random_log_generator.output.syslog_output import SyslogOutputHandler
from random_log_generator.output.multi_output import MultiOutputHandler
from random_log_generator.output.rotation import rotate_log_file
from random_log_generator.output.factory import (
    create_output_handler,
    SUPPORTED_SINK_TYPES,
)

__all__ = [
    'OutputHandler',
    'FileOutputHandler',
    'ConsoleOutputHandler',
    'HTTPOutputHandler',
    'SocketOutputHandler',
    'SyslogOutputHandler',
    'MultiOutputHandler',
    'create_output_handler',
    'rotate_log_file',
    'SUPPORTED_SINK_TYPES',
]