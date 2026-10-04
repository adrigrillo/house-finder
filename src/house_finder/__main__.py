"""Command-line entry point."""

import sqlite3
import time
from contextlib import closing
from functools import partial
from pathlib import Path

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

OUTBOX_TTL_SECONDS = 24 * 3600

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


def notify_all(token: str, chat_ids: list[str], text: str, missed: list[tuple[str, str]]) -> None:
    """Send to every chat; the chats that failed while others got it are added to missed for save_missed."""
    failed = []
    for chat_id in chat_ids:
        try:
            send_telegram(token, chat_id, text)
        except Exception as error:  # noqa: BLE001 - one chat that cannot be reached must not stop the others
            logger.error(f'telegram {chat_id}: {error!r}')
            failed.append(chat_id)
    if len(failed) == len(chat_ids):
        raise RuntimeError(f'telegram: none of {chat_ids} accepted the message')  # so run_once retries the listing
    missed.extend((chat_id, text) for chat_id in failed)


def _outbox(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path)
    db.execute('CREATE TABLE IF NOT EXISTS outbox (chat_id TEXT, text TEXT, created REAL)')
    return db


def save_missed(db_path: str, missed: list[tuple[str, str]]) -> None:
    """Keep what a chat missed. Not written inside notify_all: run_once may hold a write transaction on the same file."""
    with closing(_outbox(db_path)) as db, db:
        db.executemany(
            'INSERT INTO outbox VALUES (?, ?, ?)', [(chat_id, text, time.time()) for chat_id, text in missed]
        )


def retry_missed(token: str, db_path: str) -> None:
    """Resend what a chat missed. Gives up after a day, e.g. on a chat that blocked the bot, so it cannot retry forever."""
    with closing(_outbox(db_path)) as db, db:
        db.execute('DELETE FROM outbox WHERE created < ?', (time.time() - OUTBOX_TTL_SECONDS,))
        for rowid, chat_id, text in db.execute('SELECT rowid, chat_id, text FROM outbox ORDER BY rowid').fetchall():
            try:
                send_telegram(token, chat_id, text)
            except Exception as error:  # noqa: BLE001 - still unreachable, try again next tick
                logger.warning(f'telegram {chat_id}, retry: {error!r}')
                continue
            db.execute('DELETE FROM outbox WHERE rowid = ?', (rowid,))


def _check(dry_run: bool, with_scrapers: bool, with_idealista: bool) -> None:
    chats = recipients(settings.telegram.chat_id or '')
    missed: list[tuple[str, str]] = []
    if dry_run:
        notify = typer.echo
    elif settings.telegram.token and chats:
        notify = partial(notify_all, settings.telegram.token, chats, missed=missed)
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
    if not dry_run:
        retry_missed(settings.telegram.token, db_path)
    try:
        sent = run_once(portals, db_path, settings.search.max_price, settings.search.min_rooms, notify)
    finally:
        if missed:
            save_missed(db_path, missed)
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
