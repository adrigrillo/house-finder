# House Finder

Watches Spanish rental portals for flats in Hoya de Huesca (Huesca city and its surroundings) at or under a price
limit and sends every new listing to Telegram. Listings with enough rooms get a ⭐.

| Portal | How | Notes |
| --- | --- | --- |
| Fotocasa | JSON embedded in the search page | |
| Habitaclia | JSON embedded in the search page | URL ignores price, filtered in code |
| Milanuncios | JSON embedded in the search page | Huesca city only (no comarca search); "busco piso" ads are skipped |
| Pisos.com | Server-rendered HTML | |
| Idealista | Official API, else your Firefox's DataDome cookie; plus alert emails | A few checks a day, see below |
| Yaencontre | Not supported | DataDome blocks plain HTTP and automated browsers |

Search URLs and parsers live in `src/house_finder/portals.py`. Already-notified listings are kept in SQLite, so each
listing is sent once. The first run sends everything currently listed. When a portal gets blocked or refuses access
(DataDome, Imperva, API quota, wrong email password) you get one ⚠️ message on Telegram, and a ✅ once it works again.

## Idealista

Idealista sits behind DataDome, which blocks scripts and automated browsers. There are three ways in, and they can be
combined; the same flat is only notified once whichever way finds it.

**Official API (preferred).** Put the key and secret from developers.idealista.com in `config/.secrets.toml`
(`[idealista] api_key`, `api_secret`) or `.env`. It searches 30 km around Huesca, newest first. The free tier allows
about 100 requests a month, which `idealista.interval_hours = 8` keeps under. When set, it replaces the cookie scraper.

**Firefox cookie (fallback).** DataDome trusts the Firefox you browse with. The app reads only the `idealista.com`
cookies from a copy of your Firefox cookie database and sends requests that look like that Firefox (same version, TLS
fingerprint and language).

1. Open idealista.com in Firefox once, so the cookie exists.
2. Find the profile folder in Firefox's `about:profiles` ("Root Directory").
3. Set it as `idealista.firefox_profile` in `config/settings.local.toml`, or mount it in `compose.yaml` for Docker.

When Telegram says `blocked by DataDome`, open idealista.com in Firefox to refresh the cookie.

**Alert emails.** Save the search in Idealista with email alerts on, then give the app read-only IMAP access to that
inbox in `config/.secrets.toml` (`[email] user`, `password`) or `.env`. For Gmail, `password` must be an
[app password](https://myaccount.google.com/apppasswords), and other providers need `email.host` in `settings.toml`.
Every round it reads the last 3 days of emails from Idealista, never marking them as read, and sends the flats it has
not seen. The price and rooms come from the email, so `search.max_price` filters alerts too and `search.min_rooms` stars
them; the saved search can be wider than your budget.

## Telegram

1. Talk to [@BotFather](https://t.me/BotFather), run `/newbot` and keep the token.
2. Send your new bot any message, open `https://api.telegram.org/bot<token>/getUpdates` and copy
   `result[0].message.chat.id`.
3. To send the listings to more people, have each of them message the bot and put every chat id in `telegram.chat_id`
   separated by commas. A chat that cannot be reached is logged and skipped; the listing counts as sent once any chat got it.

## Run with Docker

```bash
cp .env.example .env   # fill in the token and chat id
docker compose up -d --build
docker compose logs -f
```

The container checks every 30 minutes and restarts with the Docker daemon. State is kept in `./data`.

## Run with uv

```bash
uv sync
cp config/.secrets.toml.example config/.secrets.toml   # fill in the token and chat id
uv run house-finder check --dry-run   # print new listings instead of sending them
uv run house-finder watch             # check forever
```

Settings live in `config/settings.toml` (`search.max_price`, `search.min_rooms`, `watch.interval_minutes`) and can be
overridden with `HOUSE_FINDER_` environment variables, for example `HOUSE_FINDER_SEARCH__MAX_PRICE=900`.

## Development

```bash
make test     # Run tests
make lint     # Run every pre-commit check
```
