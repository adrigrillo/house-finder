import json
import sqlite3
from contextlib import closing
from typing import ClassVar, Self

import pytest

from house_finder import finder, portals
from house_finder.portals import Listing


def _initial_props(props: dict) -> str:
    return f'<script>window.__INITIAL_PROPS__ = JSON.parse({json.dumps(json.dumps(props))});</script>'


def test_parse_fotocasa() -> None:
    estate = {
        'id': 1,
        'buildingSubtype': 'Apartment',
        'address': {'district': 'Casco Antiguo', 'city': 'Huesca'},
        'rawPrice': 685,
        'features': [{'key': 'surface', 'value': 60}, {'key': 'rooms', 'value': 2}],
        'detail': {'es-ES': '/es/alquiler/vivienda/huesca/1/d'},
    }
    page = f'<script id="__initial_props__" type="application/json">{json.dumps({"initialSearch": {"result": {"realEstates": [estate]}}})}</script>'

    assert portals.parse_fotocasa(page) == [
        Listing(
            'fotocasa',
            '1',
            'Apartment en Casco Antiguo, Huesca',
            685,
            2,
            'https://www.fotocasa.es/es/alquiler/vivienda/huesca/1/d',
        )
    ]


def test_parse_milanuncios_keeps_only_rentals() -> None:
    ad = {
        'id': 7,
        'title': 'Huesca Capital',
        'description': 'Ref: 000873. Avenida Danzantes\nPara estudiantes, cerca de la zona universitaria.',
        'price': {'cashPrice': {'value': 700}},
        'url': '/a-7.htm',
        'sellType': 'supply',
        'category': {'slug': 'alquiler-de-pisos'},
        'tags': [{'type': 'dormitorios', 'text': '3'}],
    }
    ads = [
        ad,
        {**ad, 'id': 8, 'description': 'x ' * 80, 'category': {'slug': 'alquiler-de-estudios'}, 'tags': []},
        {**ad, 'id': 9, 'description': '', 'sellType': 'demand', 'tags': []},  # "busco piso"
        {**ad, 'id': 10, 'category': {'slug': 'venta-de-pisos'}},  # a flat for sale
        {**ad, 'id': 11, 'category': {'slug': 'pisos-compartidos'}},  # a room in a shared flat
    ]
    page = _initial_props({'adListPagination': {'adList': {'ads': ads}}})

    assert portals.parse_milanuncios(page) == [
        Listing(
            'milanuncios',
            '7',
            'Avenida Danzantes Para estudiantes, cerca de la zona universitaria.',
            700,
            3,
            'https://www.milanuncios.com/a-7.htm',
        ),
        # cut on length, never mid-word
        Listing('milanuncios', '8', 'x ' * 49 + 'x', 700, None, 'https://www.milanuncios.com/a-7.htm'),
    ]


def test_parse_milanuncios_falls_back_to_the_title_without_a_body() -> None:
    ad = {
        'id': 9,
        'title': 'Centro - C. Coso Alto',
        'description': None,
        'sellType': 'supply',
        'category': {'slug': 'alquiler-de-apartamentos'},
        'price': {},
        'tags': [],
        'url': '/a-9.htm',
    }

    assert portals.parse_milanuncios(_initial_props({'adListPagination': {'adList': {'ads': [ad]}}})) == [
        Listing('milanuncios', '9', 'Centro - C. Coso Alto', None, None, 'https://www.milanuncios.com/a-9.htm')
    ]


def test_parse_pisos() -> None:
    page = """
    <div id="1" class="ad-preview  ad-preview--has-desc" data-lnk-href="/alquilar/piso-huesca-123_106100/">
      <span class="ad-preview__price"> 1.200 €<span>/mes</span></span>
      <a href="/alquilar/piso-huesca-123_106100/" class="ad-preview__title">Piso en Calle Mayor &#xF1;</a>
      <p class="p-sm ad-preview__subtitle">Huesca Capital</p>
      <p class="ad-preview__char p-sm">2 hab.</p>
    </div>
    """

    assert portals.parse_pisos(page) == [
        Listing(
            'pisos',
            '123_106100',
            'Piso en Calle Mayor ñ, Huesca Capital',
            1200,
            2,
            'https://www.pisos.com/alquilar/piso-huesca-123_106100/',
        )
    ]


def test_parse_gallegomartinez() -> None:
    page = """
    <div class="row lista-v thumbnails thumbnail-style">
      <h3 class="title-list"><a href="ficha.php?id=3475&t=apartamento-en-el-centro">
        Apartamento en el centro.   (Ref.: <strong>3948</strong>)</a></h3>
      <ul class="resumen list"><li><strong>575&nbsp;&euro;/mes</strong></li><li>1&nbsp;Dormitorio</li></ul>
    </div>
    <div class="row lista-v thumbnails thumbnail-style">
      <h3 class="title-list"><a href="ficha.php?id=13919&t=atico-en-alquiler">
        Atico en alquiler   (Ref.: <strong>13919</strong>)</a></h3>
      <ul class="resumen list"><li><strong>1.000&nbsp;&euro;/mes</strong></li><li>3&nbsp;Dormitorios</li></ul>
    </div>
    """

    assert portals.parse_gallegomartinez(page) == [
        Listing(
            'gallegomartinez',
            '3475',
            'Apartamento en el centro.',
            575,
            1,
            'https://www.gallegomartinez.com/ficha.php?id=3475&t=apartamento-en-el-centro',
        ),
        Listing(
            'gallegomartinez',
            '13919',
            'Atico en alquiler',
            1000,
            3,
            'https://www.gallegomartinez.com/ficha.php?id=13919&t=atico-en-alquiler',
        ),
    ]


def test_run_once_filters_dedupes_and_retries_failed_sends(tmp_path, monkeypatch) -> None:
    listings = [
        Listing('p', '1', 'cheap 2 rooms', 700, 2, 'u1'),
        Listing('p', '2', 'too expensive', 900, 2, 'u2'),
        Listing('p', '3', 'no price', None, 1, 'u3'),
        Listing('p', '4', 'cheap 1 room', 500, 1, 'u4'),
    ]
    portals = {'p': ('url', lambda url: '', lambda _: listings), 'broken': ('url', lambda url: 1 / 0, lambda _: [])}
    db = str(tmp_path / 'seen.sqlite')

    def failing_notify(text: str) -> None:
        raise OSError('telegram down')

    with pytest.raises(OSError):
        finder.run_once(portals, db, 800, 2, failing_notify)

    sent: list[str] = []
    assert finder.run_once(portals, db, 800, 2, sent.append) == 3
    assert sent[0].startswith('⭐ 700 €') and sent[1].startswith('? €') and sent[2].startswith('500 €')
    assert finder.run_once(portals, db, 800, 2, sent.append) == 0


def test_run_once_alerts_a_block_once_and_its_recovery(tmp_path) -> None:
    blocked = True

    def fetch(url: str) -> str:
        if blocked:
            raise PermissionError('blocked by DataDome')
        return ''

    portals = {'idealista': ('url', fetch, lambda _: [])}
    db = str(tmp_path / 'seen.sqlite')
    sent: list[str] = []

    finder.run_once(portals, db, 800, 2, sent.append)
    finder.run_once(portals, db, 800, 2, sent.append)
    blocked = False
    finder.run_once(portals, db, 800, 2, sent.append)
    finder.run_once(portals, db, 800, 2, sent.append)

    assert sent == ['⚠️ idealista: blocked by DataDome', '✅ idealista works again']


def test_parse_idealista_skips_ads() -> None:
    page = """
    <article class="item extended-item item-multimedia-container" data-element-id="111">
      <a href="/inmueble/111/" role="heading" class="item-link " title="Piso en Calle Coso Alto, Huesca">x</a>
      <span class="item-price h2-simulated">650<span class="txt-big">€/mes</span></span>
      <span class="item-detail">2 hab.</span><span class="item-detail">70 m²</span>
    </article>
    <article class="item adv"><div>publicidad</div></article>
    """

    assert portals.parse_idealista(page) == [
        Listing(
            'idealista', '111', 'Piso en Calle Coso Alto, Huesca', 650, 2, 'https://www.idealista.com/inmueble/111/'
        )
    ]


def test_firefox_cookies_reads_only_the_domain(tmp_path) -> None:
    with closing(sqlite3.connect(tmp_path / 'cookies.sqlite')) as db:
        db.execute('CREATE TABLE moz_cookies (name TEXT, value TEXT, host TEXT)')
        db.executemany(
            'INSERT INTO moz_cookies VALUES (?, ?, ?)',
            [
                ('datadome', 'abc', '.idealista.com'),
                ('session', 'x', 'www.idealista.com'),
                ('other', 'no', '.bank.com'),
            ],
        )
        db.commit()

    assert portals.firefox_cookies(tmp_path, 'idealista.com') == 'datadome=abc; session=x'


def test_parse_idealista_api() -> None:
    page = json.dumps(
        {
            'elementList': [
                {
                    'propertyCode': '987',
                    'price': 750.0,
                    'rooms': 2,
                    'propertyType': 'flat',
                    'district': 'Santiago',
                    'municipality': 'Huesca',
                    'url': 'https://www.idealista.com/inmueble/987/',
                }
            ]
        }
    )

    assert portals.parse_idealista_api(page) == [
        Listing('idealista', '987', 'flat en Santiago, Huesca', 750, 2, 'https://www.idealista.com/inmueble/987/')
    ]


def test_parse_idealista_email_reads_plain_and_tracked_links_once() -> None:
    text = """
    <a href="https://www.idealista.com/inmueble/111/?xtor=EPR-1">Piso</a>
    <a href="https://click.idealista.com/c?u=https%3A%2F%2Fwww.idealista.com%2Finmueble%2F222%2F">Otro</a>
    <a href="https://www.idealista.com/inmueble/111/">Ver fotos</a>
    """

    assert [listing.id for listing in portals.parse_idealista_email(text)] == ['111', '222']


def test_parse_idealista_email_reads_price_rooms_and_title_per_flat() -> None:
    text = """
    <a href="https://www.idealista.com/inmueble/111/?utm=1"><img title="Piso en Calle Mayor, Huesca"></a>
    <a href="https://www.idealista.com/inmueble/111/?utm=2">Ver fotos</a>
    <div>1.050 &euro;/mes</div><div>3 hab.   77 m&sup2;</div>
    <a href="https://www.idealista.com/inmueble/111/?utm=3">Contactar</a>
    <a href="https://click.idealista.com/c?u=https%3A%2F%2Fwww.idealista.com%2Finmueble%2F222%2F"><img title="Estudio"></a>
    <div>600 &euro;/mes</div>
    """

    assert portals.parse_idealista_email(text) == [
        Listing('idealista', '111', 'Piso en Calle Mayor, Huesca', 1050, 3, 'https://www.idealista.com/inmueble/111/'),
        Listing('idealista', '222', 'Estudio', 600, None, 'https://www.idealista.com/inmueble/222/'),
    ]


def test_parse_idealista_email_ignores_the_preview_line_before_the_first_flat() -> None:
    # Real alerts open with a hidden preview of the flat ("title 750 €/mes") before any link, and end with a footer.
    text = """
    <div style="display:none">Piso en Plaza Cervantes, 8 750 &euro;/mes . Piso en alquiler</div>
    <a href="https://www.idealista.com/inmueble/333/?utm_link=propertyNewPhoto" target="_blank">Ver 6 fotos</a>
    <p>Piso en Plaza Cervantes, 8, Santiago, Huesca</p><p>750 &euro;/mes</p><p>3 hab. 77 m&sup2;</p>
    <a href="https://www.idealista.com/inmueble/333/?utm_link=propertyNewContact">Contactar</a>
    <p>Ver todos los anuncios de Cerca de Huesca</p>
    """

    assert portals.parse_idealista_email(text) == [
        Listing('idealista', '333', 'Nuevo anuncio en tu alerta', 750, 3, 'https://www.idealista.com/inmueble/333/')
    ]


class FakeImap:
    """Serves two emails by UID and counts the downloads."""

    fetched: ClassVar[list[bytes]] = []

    def __init__(self, host: str) -> None: ...
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None: ...
    def login(self, user: str, password: str) -> None: ...
    def select(self, mailbox: str, readonly: bool) -> None: ...

    def uid(self, command: str, *args: str) -> tuple[str, list]:
        if command == 'SEARCH':
            return 'OK', [b'7 9']
        FakeImap.fetched.append(args[0])
        return 'OK', [(b'', b'Content-Type: text/plain\n\nbody ' + args[0])]


def test_fetch_idealista_emails_downloads_each_email_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(portals.imaplib, 'IMAP4_SSL', FakeImap)
    monkeypatch.setattr(portals, '_email_bodies', {})
    FakeImap.fetched.clear()

    first = portals.fetch_idealista_emails('h', 'u', 'p')
    second = portals.fetch_idealista_emails('h', 'u', 'p')

    assert first == second
    assert 'body 7' in first and 'body 9' in first
    assert FakeImap.fetched == [b'7', b'9']
