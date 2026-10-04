import time
from pathlib import Path

import pytest

from house_finder import __main__ as main
from house_finder.__main__ import notify_all, recipients, retry_missed, save_missed


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('3843410', ['3843410']),
        ('3843410,6213661840', ['3843410', '6213661840']),
        ('3843410 6213661840', ['3843410', '6213661840']),
        ('3843410, 6213661840', ['3843410', '6213661840']),
        (['3843410', '6213661840'], ['3843410', '6213661840']),  # dynaconf can split a dotenv value into a list
        (3843410, ['3843410']),
    ],
)
def test_recipients(raw: str | list | int, expected: list[str]) -> None:
    assert recipients(raw) == expected


def test_notify_all_fans_out(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(main, 'send_telegram', lambda token, chat_id, text: sent.append((chat_id, text)))

    notify_all('t', ['3843410', '6213661840'], 'flat', [])

    assert sent == [('3843410', 'flat'), ('6213661840', 'flat')]


def test_notify_all_survives_one_bad_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []

    def fake(token: str, chat_id: str, text: str) -> None:
        if chat_id == '3843410':
            raise RuntimeError('403 bot cannot initiate conversation')
        sent.append(chat_id)

    monkeypatch.setattr(main, 'send_telegram', fake)

    missed: list[tuple[str, str]] = []
    notify_all('t', ['3843410', '6213661840'], 'flat', missed)  # the reachable chat still gets it

    assert sent == ['6213661840']
    assert missed == [('3843410', 'flat')]


def test_notify_all_raises_when_nobody_got_it(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(token: str, chat_id: str, text: str) -> None:
        raise RuntimeError('boom')

    monkeypatch.setattr(main, 'send_telegram', fake)

    with pytest.raises(RuntimeError):  # run_once marks a listing seen only after a send, so this retries
        notify_all('t', ['3843410', '6213661840'], 'flat', [])


def test_recipients_of_nothing_is_empty() -> None:
    assert recipients('') == recipients(', ') == []


def test_missed_messages_are_resent_until_they_arrive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = str(tmp_path / 'db.sqlite')
    sent: list[tuple[str, str]] = []
    reachable = False

    def fake(token: str, chat_id: str, text: str) -> None:
        if not reachable:
            raise RuntimeError('403')
        sent.append((chat_id, text))

    monkeypatch.setattr(main, 'send_telegram', fake)
    save_missed(db_path, [('3843410', 'flat')])

    retry_missed('t', db_path)  # still unreachable: kept
    assert sent == []

    reachable = True
    retry_missed('t', db_path)
    retry_missed('t', db_path)  # delivered once, then forgotten

    assert sent == [('3843410', 'flat')]


def test_missed_messages_expire(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = str(tmp_path / 'db.sqlite')
    sent: list[str] = []
    monkeypatch.setattr(main, 'send_telegram', lambda token, chat_id, text: sent.append(chat_id))
    save_missed(db_path, [('3843410', 'flat')])
    later = time.time() + main.OUTBOX_TTL_SECONDS + 1
    monkeypatch.setattr(main.time, 'time', lambda: later)

    retry_missed('t', db_path)

    assert sent == []
