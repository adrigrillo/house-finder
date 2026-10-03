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
        if ad['sellType'] == 'supply'  # 'demand' ads are people looking for a flat
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
        _, found = imap.search(
            None, 'FROM', '"idealista"', 'SINCE', f'{since.day}-{months[since.month - 1]}-{since.year}'
        )
        bodies = []
        for number in found[0].split():
            _, data = imap.fetch(number, '(BODY.PEEK[])')
            body = email.message_from_bytes(data[0][1], policy=email.policy.default).get_body(('html', 'plain'))
            if body:
                bodies.append(body.get_content())
    return '\n'.join(bodies)


def parse_idealista_email(text: str) -> list[Listing]:
    # Alert links may be wrapped in click-tracking redirects, which carry the real URL percent-encoded.
    # ponytail: ids only, the alert's saved search already filters price and area; parse price and rooms once a real
    # alert email shows their markup
    ids = dict.fromkeys(re.findall(r'idealista\.com/inmueble/(\d+)', unquote(html.unescape(text))))
    return [
        Listing('idealista', id, 'Nuevo anuncio en tu alerta', None, None, f'https://www.idealista.com/inmueble/{id}/')
        for id in ids
    ]


# Cheapest first instead of a price filter: page one (30 flats) covers everything under budget in a market this size,
# and the caller drops the rest.
# ponytail: breaks once Hoya de Huesca passes 30 rentals, then switch to a newest-first sort
IDEALISTA_URL = 'https://www.idealista.com/alquiler-viviendas/huesca/hoya-de-huesca/?ordenado-por=precios-asc'

# Search URLs cover the Hoya de Huesca comarca (Huesca city and its surroundings) and are sorted newest first where the
# portal allows it, so page one holds every new listing in a market this size. Milanuncios has no comarca level, only
# city or the whole province, so it stays on Huesca city. Habitaclia's URL has no price filter; the caller filters.
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
        'https://www.milanuncios.com/alquiler-de-pisos-en-huesca-huesca/?hasta={max_price}&orden=date',
        fetch,
        parse_milanuncios,
    ),
    'pisos': ('https://www.pisos.com/alquiler/pisos-hoya_de_huesca/hasta-{max_price}/', fetch, parse_pisos),
}
