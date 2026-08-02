"""
Core generator module for Random Log Generator.

This module provides functions for generating log entries.
"""

import datetime
import logging
import random
import time
import signal # Moved from main

from random_log_generator.utils.ip_generator import generate_ip_address
from random_log_generator.utils.user_agents import (
    initialize_user_agents,
    initialize_user_agent_pool,
    generate_random_user_agent
)
from random_log_generator.formatters.factory import (
    create_formatter,
)
from random_log_generator.core.rate_limiter import TokenBucket
from random_log_generator.metrics.collector import Metrics
from random_log_generator.output.factory import create_output_handler
from random_log_generator.output.file_output import FileOutputHandler # Moved from main
from random_log_generator.output.console_output import ConsoleOutputHandler # Moved from main
from random_log_generator.core.strategies import ( # Moved from main
    write_logs_random_segments,
    write_logs_random_rate
)


def initialize_formatters(config_params, http_status_codes, log_levels):
    """Initialize the appropriate formatter based on configuration.

    Delegates to :func:`random_log_generator.formatters.factory.create_formatter`
    so all format selection logic lives in one place. Kept as a thin wrapper
    for backwards compatibility with existing tests/imports.
    """
    formatter = create_formatter(config_params, http_status_codes, log_levels)
    return formatter, log_levels


def generate_log_line(formatter, log_levels):
    """
    Generate a single log line with a timestamp and realistic message.
    
    Args:
        formatter: The formatter to use for formatting the log line.
        log_levels (list): List of log levels to choose from.
        
    Returns:
        str: A formatted log line.
    """
    # Generate timestamp
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    # Select random log level
    log_level = random.choice(log_levels)
    
    # Generate a random message
    messages = [
        "User login successful",
        "Database query executed",
        "API request received",
        "File upload completed",
        "Cache updated",
        "Configuration loaded",
        "Session expired",
        "Data validation passed",
        "Background task started",
        "Email notification sent"
    ]
    message = random.choice(messages)
    
    # Format the log line using the formatter
    return formatter.format_log(timestamp, log_level, message)


def _generate_and_write_batch(current_batch_size, formatter, log_levels, output_handler):
    """
    Generates a batch of log lines and writes them to the output handler.
    
    Returns:
        tuple: (logs_in_batch, bytes_in_batch, write_successful)
    """
    log_lines_batch = []
    bytes_generated_in_batch = 0
    logs_generated_in_batch = 0

    for _ in range(current_batch_size):
        log_line = generate_log_line(formatter, log_levels)
        log_lines_batch.append(log_line)
        logs_generated_in_batch += 1
        bytes_generated_in_batch += len(log_line) + 1  # +1 for newline
    
    write_successful = output_handler.write(log_lines_batch)
    if not write_successful:
        logging.error("Failed to write log lines to output handler in _generate_and_write_batch")
    
    return logs_generated_in_batch, bytes_generated_in_batch, write_successful


def _calculate_sleep_and_adjust_batch(
    current_batch_size, time_taken_for_batch, expected_time_per_batch, 
    tokens_per_second, accumulated_sleep_time_for_adjustment
):
    """
    Calculates sleep time and adjusts batch size based on performance.
    
    Returns:
        tuple: (new_batch_size, sleep_duration, updated_accumulated_sleep_time)
    """
    # Constants for batch adjustment logic (could be made configurable)
    SLEEP_ADJUSTMENT_THRESHOLD = 0.05  # seconds
    BATCH_INCREASE_FACTOR_SLOW = 1.2
    BATCH_INCREASE_FACTOR_FAST = 1.5

    sleep_duration = max(0, expected_time_per_batch - time_taken_for_batch)
    new_batch_size = current_batch_size # Default to current if no adjustment needed
    
    if sleep_duration > 0:
        accumulated_sleep_time_for_adjustment += sleep_duration
        if accumulated_sleep_time_for_adjustment > SLEEP_ADJUSTMENT_THRESHOLD:
            new_batch_size = min(current_batch_size * BATCH_INCREASE_FACTOR_SLOW, tokens_per_second if tokens_per_second > 0 else float('inf'))
            accumulated_sleep_time_for_adjustment = 0  # Reset tracker
            logging.debug(f"Increasing batch size to: {int(new_batch_size)}")
    else:
        # If not sleeping, consider increasing batch size more aggressively
        new_batch_size = min(current_batch_size * BATCH_INCREASE_FACTOR_FAST, tokens_per_second if tokens_per_second > 0 else float('inf'))
        logging.debug(f"Rapidly increasing batch size to: {int(new_batch_size)}")
        
    return int(max(1, new_batch_size)), sleep_duration, accumulated_sleep_time_for_adjustment # Ensure batch size is at least 1


def write_logs(rate, duration, output_handler, formatter, log_levels, log_line_size_estimate, metrics=None):
    """
    Write logs at a specified rate for a specified duration.
    
    Args:
        rate (float): Log generation rate in MB/s.
        duration (float): Duration to generate logs for in seconds.
        output_handler: The output handler to use for writing logs.
        formatter: The formatter to use for formatting log lines.
        log_levels (list): List of log levels to choose from.
        log_line_size_estimate (int): Estimated size of a single log line in bytes.
        metrics (Metrics, optional): Metrics collector to update with statistics.
        
    Returns:
        bool: True if the operation was successful, False otherwise.
    """
    logging.info(f"Writing logs at rate: {rate:.4f} MB/s for {duration:.2f} seconds")
    
    # Calculate tokens per second based on rate and log line size
    log_line_size = log_line_size_estimate  # Use configured estimate
    if log_line_size <= 0:
        logging.warning(f"log_line_size_estimate is {log_line_size}, defaulting to 100 bytes.")
        log_line_size = 100 # Default to 100 if invalid to prevent division by zero
    tokens_per_second = rate * 1024 * 1024 / log_line_size
    
    # Initialize token bucket
    token_bucket = TokenBucket(tokens_per_second, tokens_per_second)
    
    # Calculate end time
    end_time = time.time() + duration
    
    # Initialize counters
    total_logs_written = 0
    total_bytes_written = 0
    
    # Initial batch size (could be made configurable)
    current_batch_size = 1024 
    
    # For dynamic batch size adjustment based on sleep
    accumulated_sleep_time_for_adjustment = 0.0
    
    while time.time() < end_time:
        batch_process_start_time = time.time()
        
        # Ensure batch_size is an integer and positive
        current_batch_size = max(1, int(current_batch_size))
        
        # Calculate expected time for the current batch size
        if tokens_per_second <= 0: # Avoid division by zero or issues with zero/negative rate
            expected_time_per_batch = float('inf') 
            if not token_bucket.consume(1): 
                time.sleep(0.1) 
                continue
            current_batch_size = 1
        else:
            expected_time_per_batch = current_batch_size / tokens_per_second

        if token_bucket.consume(current_batch_size):
            logs_in_batch, bytes_in_batch, write_successful = _generate_and_write_batch(
                current_batch_size, formatter, log_levels, output_handler
            )
            
            if not write_successful:
                return False 
            
            total_logs_written += logs_in_batch
            total_bytes_written += bytes_in_batch
            
            time_taken_for_batch = time.time() - batch_process_start_time
            
            current_batch_size, sleep_duration, accumulated_sleep_time_for_adjustment = \
                _calculate_sleep_and_adjust_batch(
                    current_batch_size, time_taken_for_batch, expected_time_per_batch,
                    tokens_per_second, accumulated_sleep_time_for_adjustment
                )
            
            if sleep_duration > 0:
                time.sleep(sleep_duration)
        else:
            time.sleep(0.05) 
    
    if metrics:
        metrics.update(total_logs_written, total_bytes_written)
    
    return True


def _initialize_generator_components(full_config, metrics_instance_param):
    """Initializes and returns core components for the log generator."""
    config_params = full_config.get('CONFIG', {})

    log_levels_cfg = full_config.get('log_levels', ['DEBUG', 'INFO', 'WARNING', 'ERROR'])
    http_status_codes_cfg = full_config.get('http_status_codes', {})
    user_agent_browsers_cfg = full_config.get('user_agent_browsers', [])
    user_agent_systems_cfg = full_config.get('user_agent_systems', [])

    initialize_user_agents(user_agent_browsers_cfg, user_agent_systems_cfg)
    user_agent_pool_size = config_params.get('user_agent_pool_size', 100)
    initialize_user_agent_pool(user_agent_pool_size)

    metrics_obj = metrics_instance_param if metrics_instance_param is not None else Metrics()

    formatter_obj, final_log_levels = initialize_formatters(config_params, http_status_codes_cfg, log_levels_cfg)

    output_handler_obj = None
    try:
        output_handler_obj = create_output_handler(config_params)
    except (IOError, OSError) as e:
        logging.error(f"Error initializing output handler: {e}")
        raise RuntimeError(f"Failed to initialize output handler: {e}") from e
    except (ValueError, KeyError) as e:
        logging.error(f"Invalid output handler configuration: {e}")
        raise RuntimeError(f"Invalid output handler configuration: {e}") from e

    log_line_size_est = config_params.get('log_line_size_estimate', 100)

    return (config_params, formatter_obj, final_log_levels, output_handler_obj, 
            log_line_size_est, metrics_obj)


def _setup_signal_handlers(handler_func):
    """Sets up SIGINT and SIGTERM signal handlers."""
    signal.signal(signal.SIGINT, handler_func)
    signal.signal(signal.SIGTERM, handler_func)


def main(config, metrics_instance=None):
    """
    Main function to initiate log writing based on configuration.
    
    Args:
        config (dict): Configuration dictionary (full YAML content).
        metrics_instance (Metrics, optional): Metrics collector to update with statistics.
        
    Returns:
        int: Exit code (0 for success, non-zero for failure).
    """
    output_handler = None # Ensure output_handler is defined for the finally block
    metrics_collector_ref = None # Ensure defined for finally/interrupt handler
    prometheus_server = None  # Optional metrics endpoint

    try:
        (config_params, formatter, log_levels, output_handler_initialized, 
         log_line_size_estimate, metrics_collector) = _initialize_generator_components(config, metrics_instance)
        output_handler = output_handler_initialized # Assign to outer scope var
        metrics_collector_ref = metrics_collector   # Assign to outer scope var
    except RuntimeError: 
        return 1 

    # Optionally start the Prometheus metrics endpoint.
    prom_cfg = config_params.get('prometheus') or {}
    if prom_cfg.get('enabled'):
        try:
            from random_log_generator.metrics.prometheus import PrometheusMetricsServer
            prometheus_server = PrometheusMetricsServer(
                metrics_collector_ref,
                host=prom_cfg.get('host', '0.0.0.0'),
                port=prom_cfg.get('port', 9090),
                extra_labels=prom_cfg.get('labels'),
            )
            prometheus_server.start()
        except Exception as exc:  # pragma: no cover - defensive
            logging.error("Failed to start Prometheus metrics server: %s", exc)
            prometheus_server = None

    start_time = time.time()
    iteration = 0
    # nonlocal 'interrupted' for the interrupt handler, must be defined in an enclosing scope
    # To achieve this, we can make 'interrupted' a mutable object like a list or dict,
    # or pass it to the handler if the handler is not a closure.
    # For simplicity with a local closure, we'll rely on Python's closure behavior for 'interrupted'.
    # However, to modify it from within the closure, it must be declared nonlocal.
    # Let's make 'interrupted_status' a list to ensure it's mutable and accessible.
    interrupted_status = [False] 

    def handle_interrupt(signal_received, frame):
        # This function will be a closure, accessing 'interrupted_status', 
        # 'metrics_collector_ref', and 'output_handler' from the 'main' scope.
        if not interrupted_status[0]:
            interrupted_status[0] = True
            logging.info("Interrupt received, shutting down...")
            if metrics_collector_ref: 
                logging.info(f"Final metrics: {metrics_collector_ref.get_stats()}")
            if output_handler: 
                output_handler.close()
            if prometheus_server is not None:
                prometheus_server.stop()
            exit(0)

    _setup_signal_handlers(handle_interrupt)
    
    try:
        while config_params['stop_after_seconds'] == -1 or \
              (time.time() - start_time < config_params['stop_after_seconds']):
            
            if interrupted_status[0]: # Check if already interrupted
                logging.info("Exiting main loop due to interrupt.")
                break

            max_segment_duration = config_params.get('max_segment_duration_normal', 5)
            success = write_logs_random_segments(
                config_params['duration_normal'],
                max_segment_duration,
                config_params['rate_normal_min'],
                config_params['rate_normal_max'],
                config_params['base_exit_probability'],
                output_handler,
                formatter,
                log_levels,
                log_line_size_estimate,
                write_logs, # Pass the write_logs function
                metrics_collector
            )
            
            if not success or interrupted_status[0]:
                if not interrupted_status[0]: # Log error only if not interrupted
                    logging.error("Failed during normal log generation period.")
                break
            
            success = write_logs_random_rate(
                config_params['duration_peak'],
                config_params['rate_normal_max'],
                config_params['rate_peak'],
                output_handler,
                formatter,
                log_levels,
                log_line_size_estimate,
                write_logs, # Pass the write_logs function
                metrics_collector
            )
            
            if not success or interrupted_status[0]:
                if not interrupted_status[0]:
                    logging.error("Failed during peak log generation period.")
                break
            
            iteration += 1
            logging.info(f"Iteration {iteration} metrics: {metrics_collector.get_stats()}")

    except Exception as e:
        logging.error(f"An error occurred during log generation: {e}")
        # Consider if specific exceptions should lead to different exit codes
        return 1 # General error
    finally:
        # This block runs regardless of exceptions in 'try', or if 'break' was hit.
        # It also runs if an interrupt occurred and exit(0) was called,
        # though exit() would terminate before this finally could complete fully in that case.
        # The primary purpose here is cleanup if the loop finishes normally or due to non-interrupt error.
        if not interrupted_status[0] and metrics_collector_ref:
            logging.info(f"Final metrics: {metrics_collector_ref.get_stats()}")
        if output_handler: # Ensure output_handler was initialized
            output_handler.close()
        if prometheus_server is not None:
            prometheus_server.stop()
    
    return 0 if not interrupted_status[0] else 0 # Return 0 on normal completion or graceful interrupt
