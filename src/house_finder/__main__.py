"""Command-line entry point."""

import time
from functools import partial

import typer
from loguru import logger

from .config import configure_logs, settings
from .finder import run_once, send_telegram
from .portals import (
    IDEALISTA_API_URL,
    IDEALISTA_URL,
    PORTALS,
    Portal,
    fetch_idealista,
    fetch_idealista_api,
    fetch_idealista_emails,
    parse_idealista,
    parse_idealista_api,
    parse_idealista_email,
)

app = typer.Typer(help='Watch Spanish rental portals and send new listings to Telegram.', no_args_is_help=True)


@app.callback()
def main() -> None:
    """Configure the application before running a command."""
    configure_logs()


def _check(dry_run: bool, with_idealista: bool) -> None:
    if dry_run:
        notify = typer.echo
    elif settings.telegram.token and settings.telegram.chat_id:
        notify = partial(send_telegram, settings.telegram.token, str(settings.telegram.chat_id))
    else:
        raise typer.BadParameter('Set telegram.token and telegram.chat_id, or pass --dry-run.')
    portals: dict[str, Portal] = dict(PORTALS)
    idealista = settings.idealista
    if with_idealista and idealista.api_key and idealista.api_secret:  # the official API wins over the cookie
        fetcher = partial(
            fetch_idealista_api, key=idealista.api_key, secret=idealista.api_secret, max_price=settings.search.max_price
        )
        portals['idealista'] = (IDEALISTA_API_URL, fetcher, parse_idealista_api)
    elif with_idealista and idealista.firefox_profile:
        portals['idealista'] = (
            IDEALISTA_URL,
            partial(fetch_idealista, profile=idealista.firefox_profile),
            parse_idealista,
        )
    if settings.email.user and settings.email.password:  # reads the inbox, never touches idealista, so every round
        fetcher = partial(fetch_idealista_emails, user=settings.email.user, password=settings.email.password)
        portals['idealista-email'] = (settings.email.host, fetcher, parse_idealista_email)
    db_path = ':memory:' if dry_run else settings.db.path  # a dry run must not hide listings from the real run
    sent = run_once(portals, db_path, settings.search.max_price, settings.search.min_rooms, notify)
    logger.info(f'{sent} new listings')


@app.command()
def check(dry_run: bool = typer.Option(False, help='Print new listings instead of sending them.')) -> None:
    """Check every portal once."""
    _check(dry_run, with_idealista=True)


@app.command()
def watch(dry_run: bool = typer.Option(False, help='Print new listings instead of sending them.')) -> None:
    """Check every portal forever, every watch.interval_minutes, and Idealista every idealista.interval_hours."""
    last_idealista = 0.0
    while True:
        # Idealista's anti-bot flags busy clients, so it gets a few visits a day instead of one per round.
        with_idealista = time.time() - last_idealista >= settings.idealista.interval_hours * 3600
        if with_idealista:
            last_idealista = time.time()
        try:
            _check(dry_run, with_idealista)
        except typer.BadParameter:
            raise
        except Exception as error:  # noqa: BLE001 - keep watching through network blips, a dead portal is logged per run
            logger.exception(error)
        time.sleep(settings.watch.interval_minutes * 60)


if __name__ == '__main__':
    app()
