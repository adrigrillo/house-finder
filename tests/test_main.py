import pytest

from house_finder import __main__ as main
from house_finder.__main__ import notify_all, recipients


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

    notify_all('t', ['3843410', '6213661840'], 'flat')

    assert sent == [('3843410', 'flat'), ('6213661840', 'flat')]


def test_notify_all_survives_one_bad_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []

    def fake(token: str, chat_id: str, text: str) -> None:
        if chat_id == '3843410':
            raise RuntimeError('403 bot cannot initiate conversation')
        sent.append(chat_id)

    monkeypatch.setattr(main, 'send_telegram', fake)

    notify_all('t', ['3843410', '6213661840'], 'flat')  # the reachable chat still gets it

    assert sent == ['6213661840']


def test_notify_all_raises_when_nobody_got_it(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(token: str, chat_id: str, text: str) -> None:
        raise RuntimeError('boom')

    monkeypatch.setattr(main, 'send_telegram', fake)

    with pytest.raises(RuntimeError):  # run_once marks a listing seen only after a send, so this retries
        notify_all('t', ['3843410', '6213661840'], 'flat')


def test_recipients_of_nothing_is_empty() -> None:
    assert recipients('') == recipients(', ') == []
