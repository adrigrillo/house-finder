"""Application settings and logging."""

import sys

from dynaconf import Dynaconf
from loguru import logger

from .validators import get_validators

settings = Dynaconf(
    envvar_prefix='HOUSE_FINDER',
    settings_files=['config/settings.toml', 'config/settings.local.toml', 'config/.secrets.toml'],
    validators=get_validators(),
    merge_enabled=True,
)


def configure_logs() -> None:
    """Configure stderr and optional file logging."""
    logger.remove()
    logger.add(sys.stderr, level=settings.logging.level, diagnose=False)
    if settings.logging.path:
        logger.add(settings.logging.path, level=settings.logging.level, diagnose=False)
