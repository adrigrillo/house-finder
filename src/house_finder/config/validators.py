"""Dynaconf validators."""

from dynaconf import Validator


def get_validators() -> list[Validator]:
    """Return the application setting validators."""
    return [
        Validator(
            'logging.level', default='INFO', is_in=['TRACE', 'DEBUG', 'INFO', 'SUCCESS', 'WARNING', 'ERROR', 'CRITICAL']
        ),
        Validator('logging.path', default=None),
        Validator('search.max_price', default=800, is_type_of=int),
        Validator('search.min_rooms', default=2, is_type_of=int),
        Validator('db.path', default='data/house_finder.sqlite'),
        Validator('watch.interval_minutes', default=30, is_type_of=int, gte=5),
        Validator('idealista.firefox_profile', default=None),
        Validator('idealista.interval_hours', default=8, is_type_of=int, gte=1),
        Validator('idealista.api_key', default=None),
        Validator('idealista.api_secret', default=None),
        Validator('email.host', default='imap.gmail.com'),
        Validator('email.user', default=None),
        Validator('email.password', default=None),
        Validator('telegram.token', default=None),
        Validator('telegram.chat_id', default=None),
    ]
