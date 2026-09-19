"""Fail-closed configuration for an explicitly authorized project database."""
from dataclasses import dataclass, field
from pathlib import Path
import os
import re
from dotenv import dotenv_values

SCHEMA = 'sales_assignment'
SCHEMA_MARKER = 'sales-assignment-owned-schema:v1'


class SafetyError(ValueError):
    pass


@dataclass(frozen=True)
class DatabaseConfig:
    environment: str
    host: str
    port: int
    name: str
    user: str
    password: str = field(repr=False)

    def target(self):
        return {'host': self.host, 'port': self.port, 'database': self.name, 'schema': SCHEMA}

    def connect_args(self):
        return dict(host=self.host, port=self.port, dbname=self.name,
                    user=self.user, password=self.password, connect_timeout=3,
                    application_name='sales-assignment-reset')


def validate(config, confirm_reset, allow_compose=False):
    if not confirm_reset:
        raise SafetyError('Explicit --confirm-reset is required.')
    if config.environment not in {'local', 'test'}:
        raise SafetyError('APP_ENV must be exactly local or test.')
    compose_target = allow_compose and config.host == 'postgres' and config.port == 5432 and Path('/.dockerenv').exists()
    if config.host not in {'localhost', '127.0.0.1', '::1'} and not compose_target:
        raise SafetyError('Only explicit loopback PostgreSQL hosts are authorized; remote/shared targets are rejected.')
    if not isinstance(config.port, int) or isinstance(config.port, bool) or not 1 <= config.port <= 65535:
        raise SafetyError('DATABASE_PORT must be between 1 and 65535.')
    expected = r'sales_assignment_local' if config.environment == 'local' else r'sales_assignment_test(?:_[a-z0-9]+)?'
    if not re.fullmatch(expected, config.name or ''):
        raise SafetyError('DATABASE_NAME must identify this project: sales_assignment_local or sales_assignment_test[_suffix], matching APP_ENV.')
    if not re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', config.user or ''):
        raise SafetyError('DATABASE_USER must be an explicit valid PostgreSQL role name.')
    if not config.password or re.search(r'\$\{|\$[A-Za-z_]', config.password):
        raise SafetyError('DATABASE_PASSWORD must be configured, not an unresolved variable.')
    return config


def from_environment(env_file=None, environ=None):
    values = dict(dotenv_values(env_file, interpolate=False)) if env_file and Path(env_file).is_file() else {}
    values.update(os.environ if environ is None else environ)
    if values.get('DATABASE_URL'):
        raise SafetyError('Use separate DATABASE_* variables; DATABASE_URL is not accepted by this reset command.')
    required = ('APP_ENV', 'DATABASE_HOST', 'DATABASE_PORT', 'DATABASE_NAME', 'DATABASE_USER', 'DATABASE_PASSWORD')
    if any(not values.get(key) for key in required):
        raise SafetyError('Missing explicit database configuration: ' + ', '.join(key for key in required if not values.get(key)))
    try:
        port = int(values['DATABASE_PORT'])
    except (TypeError, ValueError):
        raise SafetyError('DATABASE_PORT must be an integer.') from None
    return DatabaseConfig(values['APP_ENV'], values['DATABASE_HOST'], port,
                          values['DATABASE_NAME'], values['DATABASE_USER'], values['DATABASE_PASSWORD'])
