"""Check the portals once and notify every listing not seen before."""

import json
import sqlite3
import urllib.request
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from .portals import Listing, Portal


def format_message(listing: Listing, min_rooms: int) -> str:
    star = '⭐ ' if listing.rooms is not None and listing.rooms >= min_rooms else ''
    rooms = f'{listing.rooms} hab' if listing.rooms is not None else '? hab'
    price = f'{listing.price} €' if listing.price else '? €'
    return f'{star}{price} · {rooms} · {listing.portal}\n{listing.title}\n{listing.url}'


def send_telegram(token: str, chat_id: str, text: str) -> None:
    request = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/sendMessage',
        data=json.dumps({'chat_id': chat_id, 'text': text}).encode(),
        headers={'Content-Type': 'application/json'},
    )
    urllib.request.urlopen(request, timeout=30).close()


def run_once(
    portals: dict[str, Portal], db_path: str, max_price: int, min_rooms: int, notify: Callable[[str], None]
) -> int:
    """Notify unseen listings at or under max_price and return how many were sent."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS seen (portal TEXT, id TEXT, PRIMARY KEY (portal, id))')
        db.execute('CREATE TABLE IF NOT EXISTS blocked (portal TEXT PRIMARY KEY)')
        sent = 0
        for name, (url, fetch, parse) in portals.items():
            try:
                listings = parse(fetch(url.format(max_price=max_price)))
            except PermissionError as error:  # blocked or refused: needs a human, so say it once on Telegram
                logger.warning(f'{name}: {error}')
                if db.execute('INSERT OR IGNORE INTO blocked VALUES (?)', (name,)).rowcount:
                    notify(f'⚠️ {name}: {error}')
                    db.commit()
                continue
            except Exception as error:  # noqa: BLE001 - one broken portal must not stop the others
                logger.warning(f'{name}: {error!r}')
                continue
            if db.execute('DELETE FROM blocked WHERE portal = ?', (name,)).rowcount:
                notify(f'✅ {name} works again')
                db.commit()
            logger.info(f'{name}: {len(listings)} listings')
            for listing in listings:
                # Unknown prices pass: email alerts carry none and their saved search already filters by price.
                if listing.price and listing.price > max_price:
                    logger.debug(f'{name}: skipped, over budget: {listing}')
                    continue
                if db.execute(
                    'SELECT 1 FROM seen WHERE portal = ? AND id = ?', (listing.portal, listing.id)
                ).fetchone():
                    continue
                notify(format_message(listing, min_rooms))
                # Marked only after a successful send, so a Telegram outage retries the listing next run.
                db.execute('INSERT INTO seen VALUES (?, ?)', (listing.portal, listing.id))
                db.commit()
                sent += 1
    return sent
