"""
Configuration validators module for Random Log Generator.

This module provides functions for validating configuration values.
"""

import logging


# Required configuration keys
REQUIRED_CONFIG_KEYS = [
    'duration_normal',
    'duration_peak',
    'rate_normal_min',
    'rate_normal_max',
    'rate_peak',
    'log_line_size_estimate', # Renamed from log_line_size
    'user_agent_pool_size', # New
    'max_segment_duration_normal', # New
    'base_exit_probability',
    'rate_change_probability',
    'rate_change_max_percentage',
    'write_to_file',
    'log_file_path',
    'log_rotation_enabled',
    'log_rotation_size',
    'http_format_logs',
    'stop_after_seconds',
    'custom_app_names',
    'custom_log_format',
    'logging_level'
]


def validate_config(config):
    """
    Validate the configuration dictionary.
    
    Args:
        config (dict): Configuration dictionary to validate.
        
    Raises:
        ValueError: If a required configuration key is missing or a value is invalid.
    """
    # The 'config' argument is the full config. We validate the 'CONFIG' sub-dictionary.
    config_params = config.get('CONFIG', None)
    if config_params is None:
        error_msg = "Missing 'CONFIG' block in configuration."
        logging.error(error_msg)
        raise ValueError(error_msg)

    # Check for required keys within the 'CONFIG' block
    for key in REQUIRED_CONFIG_KEYS:
        if key not in config_params:
            error_msg = f"Missing required configuration parameter in 'CONFIG' block: {key}"
            logging.error(error_msg)
            raise ValueError(error_msg)
    
    # Validate numeric values
    validate_numeric(config_params, 'duration_normal', min_value=0)
    validate_numeric(config_params, 'duration_peak', min_value=0)
    validate_numeric(config_params, 'rate_normal_min', min_value=0)
    validate_numeric(config_params, 'rate_normal_max', min_value=0)
    validate_numeric(config_params, 'rate_peak', min_value=0)
    validate_numeric(config_params, 'log_line_size_estimate', min_value=1)
    validate_numeric(config_params, 'user_agent_pool_size', min_value=0) 
    validate_numeric(config_params, 'max_segment_duration_normal', min_value=1)
    validate_numeric(config_params, 'base_exit_probability', min_value=0, max_value=1)
    validate_numeric(config_params, 'rate_change_probability', min_value=0, max_value=1)
    validate_numeric(config_params, 'rate_change_max_percentage', min_value=0)
    validate_numeric(config_params, 'log_rotation_size', min_value=0)
    
    # Validate that rate_normal_min <= rate_normal_max
    if config_params['rate_normal_min'] > config_params['rate_normal_max']:
        error_msg = f"rate_normal_min ({config_params['rate_normal_min']}) must be less than or equal to rate_normal_max ({config_params['rate_normal_max']})"
        logging.error(error_msg)
        raise ValueError(error_msg)
    
    # Validate boolean values
    validate_boolean(config_params, 'write_to_file')
    validate_boolean(config_params, 'log_rotation_enabled')
    validate_boolean(config_params, 'http_format_logs')
    
    # Validate string values
    validate_string(config_params, 'log_file_path')
    validate_string(config_params, 'custom_log_format')
    validate_logging_level(config_params, 'logging_level')
    
    # Validate list values
    validate_list(config_params, 'custom_app_names')

    # Validate optional new feature-flag config blocks.
    _validate_optional_log_format(config_params)
    _validate_optional_sinks(config_params)
    _validate_optional_prometheus(config_params)

    # Validate root-level keys if they exist
    if 'log_levels' in config and config['log_levels'] is not None:
        validate_list(config, 'log_levels')
        if not config['log_levels']:
             error_msg = "Configuration parameter 'log_levels' must not be empty if provided."
             logging.error(error_msg)
             raise ValueError(error_msg)

    if 'http_status_codes' in config and config['http_status_codes'] is not None:
        validate_dict(config, 'http_status_codes')

    if 'user_agent_browsers' in config and config['user_agent_browsers'] is not None:
        validate_list(config, 'user_agent_browsers')

    if 'user_agent_systems' in config and config['user_agent_systems'] is not None:
        validate_list(config, 'user_agent_systems')
    
    logging.info("Configuration validation successful")


def validate_numeric(config, key, min_value=None, max_value=None):
    """
    Validate that a configuration value is numeric and within the specified range.
    
    Args:
        config (dict): Configuration dictionary.
        key (str): Key to validate.
        min_value (float, optional): Minimum allowed value.
        max_value (float, optional): Maximum allowed value.
        
    Raises:
        ValueError: If the value is not numeric or outside the specified range.
    """
    value = config[key]
    if not isinstance(value, (int, float)):
        error_msg = f"Configuration parameter '{key}' must be numeric, got {type(value).__name__}"
        logging.error(error_msg)
        raise ValueError(error_msg)
    
    if min_value is not None and value < min_value:
        error_msg = f"Configuration parameter '{key}' must be at least {min_value}, got {value}"
        logging.error(error_msg)
        raise ValueError(error_msg)
    
    if max_value is not None and value > max_value:
        error_msg = f"Configuration parameter '{key}' must be at most {max_value}, got {value}"
        logging.error(error_msg)
        raise ValueError(error_msg)


def validate_boolean(config, key):
    """
    Validate that a configuration value is a boolean.
    
    Args:
        config (dict): Configuration dictionary.
        key (str): Key to validate.
        
    Raises:
        ValueError: If the value is not a boolean.
    """
    value = config[key]
    if not isinstance(value, bool):
        error_msg = f"Configuration parameter '{key}' must be a boolean, got {type(value).__name__}"
        logging.error(error_msg)
        raise ValueError(error_msg)


def validate_string(config, key):
    """
    Validate that a configuration value is a string.
    
    Args:
        config (dict): Configuration dictionary.
        key (str): Key to validate.
        
    Raises:
        ValueError: If the value is not a string.
    """
    value = config[key]
    if not isinstance(value, str):
        error_msg = f"Configuration parameter '{key}' must be a string, got {type(value).__name__}"
        logging.error(error_msg)
        raise ValueError(error_msg)


def validate_list(config, key):
    """
    Validate that a configuration value is a list.
    
    Args:
        config (dict): Configuration dictionary.
        key (str): Key to validate.
        
    Raises:
        ValueError: If the value is not a list.
    """
    value = config[key]
    if not isinstance(value, list):
        error_msg = f"Configuration parameter '{key}' must be a list, got {type(value).__name__}"
        logging.error(error_msg)
        raise ValueError(error_msg)


def validate_dict(config, key):
    """
    Validate that a configuration value is a dictionary.
    
    Args:
        config (dict): Configuration dictionary.
        key (str): Key to validate.
        
    Raises:
        ValueError: If the value is not a dictionary.
    """
    value = config[key]
    if not isinstance(value, dict):
        error_msg = f"Configuration parameter '{key}' must be a dictionary, got {type(value).__name__}"
        logging.error(error_msg)
        raise ValueError(error_msg)


def validate_logging_level(config, key):
    """
    Validate that a configuration value is a valid logging level.
    
    Args:
        config (dict): Configuration dictionary.
        key (str): Key to validate.
        
    Raises:
        ValueError: If the value is not a valid logging level.
    """
    value = config[key]
    if not isinstance(value, str):
        error_msg = f"Configuration parameter '{key}' must be a string, got {type(value).__name__}"
        logging.error(error_msg)
        raise ValueError(error_msg)
    
    valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
    if value.upper() not in valid_levels:
        error_msg = f"Configuration parameter '{key}' must be one of {valid_levels}, got '{value}'"
        logging.error(error_msg)
        raise ValueError(error_msg)


def _validate_optional_log_format(config_params):
    """Validate the optional ``log_format`` and ``formatter_options`` keys."""
    # Imported lazily to avoid a config<->formatters import cycle at load time.
    from random_log_generator.formatters.factory import SUPPORTED_FORMATS

    log_format = config_params.get('log_format')
    if log_format is not None:
        if not isinstance(log_format, str):
            raise ValueError(
                f"Configuration parameter 'log_format' must be a string, got {type(log_format).__name__}"
            )
        if log_format.lower() not in SUPPORTED_FORMATS:
            raise ValueError(
                f"Configuration parameter 'log_format' must be one of {SUPPORTED_FORMATS}, got '{log_format}'"
            )

    formatter_options = config_params.get('formatter_options')
    if formatter_options is not None and not isinstance(formatter_options, dict):
        raise ValueError(
            f"Configuration parameter 'formatter_options' must be a dict, got {type(formatter_options).__name__}"
        )


def _validate_optional_sinks(config_params):
    """Validate the optional ``sinks`` configuration list."""
    from random_log_generator.output.factory import SUPPORTED_SINK_TYPES

    sinks = config_params.get('sinks')
    if sinks is None:
        return
    if not isinstance(sinks, list) or not sinks:
        raise ValueError("'sinks' must be a non-empty list of sink specifications")

    for index, spec in enumerate(sinks):
        if not isinstance(spec, dict):
            raise ValueError(
                f"sinks[{index}] must be a mapping, got {type(spec).__name__}"
            )
        sink_type = spec.get('type')
        if not isinstance(sink_type, str) or sink_type.lower() not in SUPPORTED_SINK_TYPES:
            raise ValueError(
                f"sinks[{index}].type must be one of {SUPPORTED_SINK_TYPES}, got {sink_type!r}"
            )
        sink_type = sink_type.lower()

        if sink_type == 'http' and not spec.get('url'):
            raise ValueError(f"sinks[{index}] (http) requires a 'url' field")
        if sink_type in ('socket', 'syslog'):
            if not spec.get('host'):
                raise ValueError(f"sinks[{index}] ({sink_type}) requires a 'host' field")
            if sink_type == 'socket' and 'port' not in spec:
                raise ValueError(f"sinks[{index}] (socket) requires a 'port' field")

    require_all = config_params.get('sinks_require_all_success')
    if require_all is not None and not isinstance(require_all, bool):
        raise ValueError(
            "'sinks_require_all_success' must be a boolean if provided"
        )


def _validate_optional_prometheus(config_params):
    """Validate the optional ``prometheus`` configuration block."""
    prom = config_params.get('prometheus')
    if prom is None:
        return
    if not isinstance(prom, dict):
        raise ValueError(
            f"Configuration parameter 'prometheus' must be a dict, got {type(prom).__name__}"
        )
    if 'enabled' in prom and not isinstance(prom['enabled'], bool):
        raise ValueError("'prometheus.enabled' must be a boolean")
    if 'port' in prom:
        port = prom['port']
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("'prometheus.port' must be an int between 1 and 65535")
    if 'host' in prom and not isinstance(prom['host'], str):
        raise ValueError("'prometheus.host' must be a string")
    if 'labels' in prom and prom['labels'] is not None and not isinstance(prom['labels'], dict):
        raise ValueError("'prometheus.labels' must be a dict if provided")
