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


def recipients(raw: str | list | int) -> list[str]:
    """Read one or many chat ids from a comma or space separated setting."""
    if isinstance(raw, list):
        return [str(chat_id) for chat_id in raw]
    return [chat_id for chat_id in str(raw).replace(',', ' ').split() if chat_id]


def notify_all(token: str, chat_ids: list[str], text: str) -> None:
    failed = 0
    for chat_id in chat_ids:
        try:
            send_telegram(token, chat_id, text)
        except Exception as error:  # noqa: BLE001 - one chat that cannot be reached must not stop the others
            logger.error(f'telegram {chat_id}: {error!r}')
            failed += 1
    if failed == len(chat_ids):
        raise RuntimeError(f'telegram: none of {chat_ids} accepted the message')  # so run_once retries the listing


def _check(dry_run: bool, with_scrapers: bool, with_idealista: bool) -> None:
    chats = recipients(settings.telegram.chat_id or '')
    if dry_run:
        notify = typer.echo
    elif settings.telegram.token and chats:
        notify = partial(notify_all, settings.telegram.token, chats)
    else:
        raise typer.BadParameter('Set telegram.token and telegram.chat_id, or pass --dry-run.')
    portals: dict[str, Portal] = dict(PORTALS) if with_scrapers else {}
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
    if settings.email.user and settings.email.password:  # reads the inbox, never touches idealista, so every tick
        fetcher = partial(fetch_idealista_emails, user=settings.email.user, password=settings.email.password)
        portals['idealista-email'] = (settings.email.host, fetcher, parse_idealista_email)
    db_path = ':memory:' if dry_run else settings.db.path  # a dry run must not hide listings from the real run
    sent = run_once(portals, db_path, settings.search.max_price, settings.search.min_rooms, notify)
    logger.info(f'{sent} new listings')


@app.command()
def check(dry_run: bool = typer.Option(False, help='Print new listings instead of sending them.')) -> None:
    """Check every portal once."""
    _check(dry_run, with_scrapers=True, with_idealista=True)


@app.command()
def watch(dry_run: bool = typer.Option(False, help='Print new listings instead of sending them.')) -> None:
    """Read alert emails every email.interval_minutes, portals every watch.interval_minutes, Idealista every idealista.interval_hours."""
    has_email = bool(settings.email.user and settings.email.password)
    tick = (
        min(settings.email.interval_minutes, settings.watch.interval_minutes)
        if has_email
        else settings.watch.interval_minutes
    )
    last_scrapers = last_idealista = 0.0
    while True:
        # Scrapers are polled gently, and Idealista's anti-bot flags busy clients, so it gets a few visits a day.
        with_scrapers = time.time() - last_scrapers >= settings.watch.interval_minutes * 60
        with_idealista = time.time() - last_idealista >= settings.idealista.interval_hours * 3600
        if with_scrapers:
            last_scrapers = time.time()
        if with_idealista:
            last_idealista = time.time()
        try:
            _check(dry_run, with_scrapers, with_idealista)
        except typer.BadParameter:
            raise
        except Exception as error:  # noqa: BLE001 - keep watching through network blips, a dead portal is logged per run
            logger.exception(error)
        time.sleep(tick * 60)


if __name__ == '__main__':
    app()
