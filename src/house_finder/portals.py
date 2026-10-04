"""Rental portal scrapers. Each portal is a search URL, a fetcher and a parser that turns its HTML into listings."""

import base64
import configparser
import email
import email.policy
import html
import imaplib
import json
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, unquote

from curl_cffi import requests


@dataclass(frozen=True)
class Listing:
    portal: str
    id: str
    title: str
    price: int | None
    rooms: int | None
    url: str


def fetch(url: str) -> str:
    # Chrome's TLS fingerprint, because anti-bot layers (Milanuncios' Imperva) block Python's own.
    response = requests.get(url, impersonate='chrome', timeout=30, headers={'Accept-Language': 'es-ES,es;q=0.9'})
    if 'Pardon Our Interruption' in response.text:
        raise PermissionError('blocked by Imperva anti-bot')
    response.raise_for_status()
    return response.text


def _script_json(page: str, pattern: str) -> dict:
    """Return the JSON a page embeds for its own JavaScript, either as an object or a JSON.parse("...") string."""
    raw = json.loads(re.search(pattern, page, re.DOTALL).group(1))
    return json.loads(raw) if isinstance(raw, str) else raw


def parse_fotocasa(page: str) -> list[Listing]:
    props = _script_json(page, r'<script[^>]*id="__initial_props__"[^>]*>(.*?)</script>')
    return [
        Listing(
            portal='fotocasa',
            id=str(estate['id']),
            title=f'{estate["buildingSubtype"]} en {", ".join(filter(None, (estate["address"].get("district"), estate["address"]["city"])))}',
            price=estate.get('rawPrice'),
            rooms=next((f['value'] for f in estate['features'] if f['key'] == 'rooms'), None),
            url='https://www.fotocasa.es' + estate['detail']['es-ES'],
        )
        for estate in props['initialSearch']['result']['realEstates']
    ]


def parse_habitaclia(page: str) -> list[Listing]:
    props = _script_json(page, r'window\.__INITIAL_PROPS__\s*=\s*JSON\.parse\((".*?")\);')
    return [
        Listing(
            portal='habitaclia',
            id=item['id'],
            title=f'{item["property"]["propertySubtype"]} en {item["summary"]["location"]["displayZoneLine"]}',
            price=item['transaction']['price']['amount'],
            rooms=item['property']['rooms'],
            url='https://www.habitaclia.com' + item['navigationUrl'],
        )
        for item in props['initialSearchResultsPage']['initialSearchContext']['results']['items']
    ]


def parse_milanuncios(page: str) -> list[Listing]:
    props = _script_json(page, r'window\.__INITIAL_PROPS__\s*=\s*JSON\.parse\((".*?")\);')
    return [
        Listing(
            portal='milanuncios',
            id=str(ad['id']),
            title=ad['title'],
            price=ad['price'].get('cashPrice', {}).get('value'),
            rooms=next((int(t['text']) for t in ad['tags'] if t['type'] == 'dormitorios'), None),
            url='https://www.milanuncios.com' + ad['url'],
        )
        for ad in props['adListPagination']['adList']['ads']
        # 'demand' ads are people looking for a flat. Rentals are split across categories (pisos, estudios,
        # apartamentos), so the search covers every home and this keeps the dwellings for rent, dropping sales and
        # shared rooms, whose price is per room.
        if ad['sellType'] == 'supply' and ad['category']['slug'].startswith('alquiler-de')
    ]


def parse_pisos(page: str) -> list[Listing]:
    listings = []
    # ponytail: regex over server-rendered HTML, switch to an HTML parser if pisos.com reshapes its markup
    for block in page.split('class="ad-preview ')[1:]:
        url = re.search(r'data-lnk-href="([^"]+)"', block).group(1)
        price = re.search(r'ad-preview__price">\s*([\d.]+)', block)
        rooms = re.search(r'ad-preview__char[^>]*>\s*(\d+) hab', block)
        listings.append(
            Listing(
                portal='pisos',
                id=url.rstrip('/').rsplit('-', 1)[-1],
                title=html.unescape(
                    ', '.join(m.strip() for m in re.findall(r'ad-preview__(?:title|subtitle)">([^<]+)', block))
                ),
                price=int(price.group(1).replace('.', '')) if price else None,
                rooms=int(rooms.group(1)) if rooms else None,
                url='https://www.pisos.com' + url,
            )
        )
    return listings


def parse_gallegomartinez(page: str) -> list[Listing]:
    listings = []
    # ponytail: regex over server-rendered HTML, switch to an HTML parser if the agency reshapes its markup
    for block in page.split('class="row lista-v thumbnails thumbnail-style"')[1:]:
        link = re.search(r'href="(ficha\.php\?id=(\d+)[^"]*)"', block)
        text = html.unescape(block)  # &nbsp; between number and unit, &euro; for the sign
        price = re.search(r'<li><strong>([\d.]+)\s*€', text)
        rooms = re.search(r'<li>(\d+)\s*Dormitorio', text)  # one bedroom is "1 Dormitorio", not "Dormitorios"
        title = re.search(r'title-list"><a[^>]*>\s*(.*?)\s*\(Ref', text, re.DOTALL)  # ends in "(Ref.: 3948)"
        listings.append(
            Listing(
                portal='gallegomartinez',
                id=link.group(2),
                title=title.group(1).strip() if title else '',
                price=int(price.group(1).replace('.', '')) if price else None,
                rooms=int(rooms.group(1)) if rooms else None,
                url='https://www.gallegomartinez.com/' + link.group(1),
            )
        )
    return listings


def firefox_cookies(profile: Path, domain: str) -> str:
    """Return a Cookie header with the cookies a Firefox profile holds for a domain."""
    with tempfile.TemporaryDirectory() as tmp:
        # Firefox keeps the database locked and recent writes in the WAL file, so read a copy of both.
        for name in ('cookies.sqlite', 'cookies.sqlite-wal'):
            if (profile / name).exists():
                shutil.copy(profile / name, tmp)
        with closing(sqlite3.connect(Path(tmp) / 'cookies.sqlite')) as db:
            rows = db.execute('SELECT name, value FROM moz_cookies WHERE host LIKE ?', (f'%{domain}',)).fetchall()
    return '; '.join(f'{name}={value}' for name, value in rows)


def fetch_idealista(url: str, profile: str) -> str:
    """Fetch with the DataDome cookie Firefox earned, looking like that same Firefox on the wire."""
    profile_dir = Path(profile).expanduser()
    compatibility = configparser.ConfigParser()
    compatibility.read(profile_dir / 'compatibility.ini')
    version = compatibility['Compatibility']['LastVersion'].split('_')[0]  # e.g. 157.0_20260901000000/...
    response = requests.get(
        url,
        impersonate='firefox',
        timeout=30,
        headers={
            'User-Agent': f'Mozilla/5.0 (X11; Linux x86_64; rv:{version}) Gecko/20100101 Firefox/{version}',
            'Accept-Language': 'es-ES,es;q=0.8,en-US;q=0.5,en;q=0.3',
            'Cookie': firefox_cookies(profile_dir, 'idealista.com'),
        },
    )
    if response.status_code == 403 or 'captcha-delivery.com' in response.text:
        raise PermissionError('blocked by DataDome: open idealista.com in Firefox to refresh its cookie')
    response.raise_for_status()
    return response.text


def parse_idealista(page: str) -> list[Listing]:
    listings = []
    # ponytail: regex over server-rendered HTML, switch to an HTML parser if idealista reshapes its markup
    for block in page.split('<article class="item')[1:]:
        element_id = re.search(r'data-element-id="(\d+)"', block)
        if not element_id:  # advertising and banners share the article markup
            continue
        title = re.search(r'class="item-link[^"]*"[^>]*title="([^"]+)"', block)
        price = re.search(r'item-price[^>]*>\s*([\d.]+)', block)
        rooms = re.search(r'item-detail">\s*(\d+) hab', block)
        listings.append(
            Listing(
                portal='idealista',
                id=element_id.group(1),
                title=html.unescape(title.group(1)) if title else '',
                price=int(price.group(1).replace('.', '')) if price else None,
                rooms=int(rooms.group(1)) if rooms else None,
                url=f'https://www.idealista.com/inmueble/{element_id.group(1)}/',
            )
        )
    return listings


IDEALISTA_API_URL = 'https://api.idealista.com/3.5/es/search'
# ponytail: a circle around Huesca city approximates the Hoya de Huesca comarca, the API has no comarca filter
HOYA_DE_HUESCA = {'center': '42.1362,-0.4087', 'distance': 30000}


def fetch_idealista_api(url: str, key: str, secret: str, max_price: int) -> str:
    """Search through the official API (free tier: about 100 requests a month)."""
    credentials = base64.b64encode(f'{quote(key, safe="")}:{quote(secret, safe="")}'.encode()).decode()
    token = requests.post(
        'https://api.idealista.com/oauth/token',
        headers={'Authorization': f'Basic {credentials}'},
        data={'grant_type': 'client_credentials', 'scope': 'read'},
        timeout=30,
    )
    if token.status_code in (400, 401):
        raise PermissionError(f'API rejected the key and secret ({token.status_code})')
    token.raise_for_status()
    response = requests.post(
        url,
        headers={'Authorization': f'Bearer {token.json()["access_token"]}'},
        data={
            **HOYA_DE_HUESCA,
            'operation': 'rent',
            'propertyType': 'homes',
            'maxPrice': max_price,
            'order': 'publicationDate',
            'sort': 'desc',
            'maxItems': 50,
            'numPage': 1,
            'locale': 'es',
        },
        timeout=30,
    )
    if response.status_code in (401, 403, 429):
        raise PermissionError(f'API refused the search ({response.status_code}), monthly quota used up?')
    response.raise_for_status()
    return response.text


def parse_idealista_api(page: str) -> list[Listing]:
    return [
        Listing(
            portal='idealista',
            id=str(element['propertyCode']),
            title=f'{element.get("propertyType", "")} en {", ".join(filter(None, (element.get("district"), element.get("municipality"))))}',
            price=int(element['price']) if element.get('price') else None,
            rooms=element.get('rooms'),
            url=element['url'],
        )
        for element in json.loads(page)['elementList']
    ]


# uid -> body of the alert emails already downloaded, so a poll only fetches the new ones.
# ponytail: in memory, a restart refetches the last 3 days once; UIDs are only stable while the mailbox's UIDVALIDITY is
_email_bodies: dict[bytes, str] = {}


def fetch_idealista_emails(host: str, user: str, password: str) -> str:
    """Return the bodies of the last few days of Idealista alert emails, without marking anything as read."""
    since = datetime.now(UTC) - timedelta(days=3)
    # IMAP wants English month names, whatever the locale
    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    with imaplib.IMAP4_SSL(host) as imap:
        try:
            imap.login(user, password)
        except imaplib.IMAP4.error as error:
            raise PermissionError(f'email login failed: {error}') from error
        imap.select('INBOX', readonly=True)
        _, found = imap.uid(
            'SEARCH', 'FROM', '"idealista"', 'SINCE', f'{since.day}-{months[since.month - 1]}-{since.year}'
        )
        uids = found[0].split()
        for uid in uids:
            if uid not in _email_bodies:
                _, data = imap.uid('FETCH', uid, '(BODY.PEEK[])')
                body = email.message_from_bytes(data[0][1], policy=email.policy.default).get_body(('html', 'plain'))
                _email_bodies[uid] = body.get_content() if body else ''
    for uid in _email_bodies.keys() - set(uids):  # older than the window
        del _email_bodies[uid]
    return '\n'.join(_email_bodies[uid] for uid in uids)


def parse_idealista_email(text: str) -> list[Listing]:
    # Alert links may be wrapped in click-tracking redirects, which carry the real URL percent-encoded.
    # An alert has one block per flat: its links, then "750 €/mes" and "3 hab.". Each id owns the text after its links,
    # up to the next flat's, so the price, rooms and title come from there; a digest without them leaves them unknown.
    parts = re.split(r'idealista\.com/inmueble/(\d+)', unquote(html.unescape(text)))
    blocks: dict[str, str] = {}
    for id, chunk in zip(parts[1::2], parts[2::2], strict=True):
        blocks[id] = blocks.get(id, '') + chunk
    listings = []
    for id, block in blocks.items():
        price = re.search(r'(\d[\d.]*)\s*€', block)
        rooms = re.search(r'(\d+)\s*hab', block)
        title = re.search(r'title="([^"]+)"', block)
        listings.append(
            Listing(
                'idealista',
                id,
                title[1] if title else 'Nuevo anuncio en tu alerta',
                int(price[1].replace('.', '')) if price else None,
                int(rooms[1]) if rooms else None,
                f'https://www.idealista.com/inmueble/{id}/',
            )
        )
    return listings


# Cheapest first instead of a price filter: page one (30 flats) covers everything under budget in a market this size,
# and the caller drops the rest.
# ponytail: breaks once Hoya de Huesca passes 30 rentals, then switch to a newest-first sort
IDEALISTA_URL = 'https://www.idealista.com/alquiler-viviendas/huesca/hoya-de-huesca/?ordenado-por=precios-asc'

# Search URLs cover the Hoya de Huesca comarca (Huesca city and its surroundings) and are sorted newest first where the
# portal allows it, so page one holds every new listing in a market this size. Milanuncios has no comarca level, only
# city or the whole province, so it stays on Huesca city, and it searches every home because a rental is filed under
# its own category (a piso search misses the studios and apartments); Habitaclia's URL has no price filter, and
# Gallego Martínez's price filter is a POST form capped at 600 €; the caller filters all three.
Portal = tuple[str, Callable[[str], str], Callable[[str], list[Listing]]]

PORTALS: dict[str, Portal] = {
    'fotocasa': (
        'https://www.fotocasa.es/es/alquiler/viviendas/hoya-de-huesca/todas-las-zonas/l?maxPrice={max_price}&sortType=publicationDate',
        fetch,
        parse_fotocasa,
    ),
    'habitaclia': (
        'https://www.habitaclia.com/alquiler/viviendas/huesca-provincia/hoya-de-huesca/s',
        fetch,
        parse_habitaclia,
    ),
    'milanuncios': (
        'https://www.milanuncios.com/viviendas-en-huesca-huesca/?hasta={max_price}&orden=date',
        fetch,
        parse_milanuncios,
    ),
    'pisos': ('https://www.pisos.com/alquiler/pisos-hoya_de_huesca/hasta-{max_price}/', fetch, parse_pisos),
    'gallegomartinez': ('https://www.gallegomartinez.com/alquilar-piso-huesca.php', fetch, parse_gallegomartinez),
}
