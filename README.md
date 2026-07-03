# Bill Tracker

A lightweight, self-hosted web app for tracking household bills and subscriptions, with daily push notifications via [ntfy](https://ntfy.sh). Built with FastAPI and SQLite — no accounts, no cloud, one process, one database file.

## Features

- **Full bill management** — add, edit, disable, and delete bills with amounts, due dates, notes, and links to billing portals
- **Configurable household** — define your own family members and bill categories via environment variables
- **Flexible schedules** — weekly, monthly, quarterly, annual, or one-off payment frequencies
- **Monthly & annual estimates** — live totals that update as you filter
- **Overdue detection** — unpaid bills past their due date are flagged automatically
- **Auto-pay support** — mark bills as direct debit and payments are logged automatically on the due date
- **Duplicate-payment guard** — bills already paid this month can't be accidentally paid twice
- **Push alerts** — daily notifications via [ntfy](https://ntfy.sh) for bills due within a configurable window
- **Payment history** — recent payments with one-click undo
- **Charts** — monthly spend broken down by member and by category
- **Search, filter & sort** — by name, member, category, amount, or due date
- **CSV export**, dark/light mode, and a mobile-friendly layout

## Quick start

Requires Python 3.10+.

```bash
git clone https://github.com/Jamesemf/Bill-Tracker.git
cd Bill-Tracker
pip install -r requirements.txt
python -m app.main
```

Open [http://localhost:8000](http://localhost:8000). A SQLite database (`bills.db`) is created automatically on first run.

To customise it for your household, copy the example config and edit it:

```bash
cp .env.example .env
```

```dotenv
APP_TITLE=Smith Family Bills
FAMILY_MEMBERS=anna,ben,charlie,family
CURRENCY_SYMBOL=$
NTFY_TOPIC=your-secret-topic
```

## Configuration

All settings are read from environment variables (or a `.env` file in the project root). Everything is optional.

| Variable | Default | Description |
|---|---|---|
| `APP_TITLE` | `Bill Tracker` | Title shown in the header and browser tab. |
| `FAMILY_MEMBERS` | `alex,sam,family` | Comma-separated list of people (or groups) bills can be assigned to. Include a shared label like `family` if you want one. |
| `BILL_TYPES` | `utilities,streaming,health,wellness,insurance,education,software,finance,other` | Comma-separated list of bill categories. |
| `CURRENCY_SYMBOL` | `£` | Currency symbol used throughout the UI and in alerts. |
| `NTFY_SERVER` | `https://ntfy.sh` | ntfy server URL — point this at your own instance if you self-host ntfy. |
| `NTFY_TOPIC` | *(empty)* | Your ntfy topic name. Leave blank to disable push alerts. |
| `ALERT_DAYS_BEFORE` | `3` | Days before a due date to start sending alerts. |
| `ALERT_HOUR` | `8` | Hour of day (0–23, local time) for the daily alert/auto-pay check. |
| `HOST` | `0.0.0.0` | Host to bind to. |
| `PORT` | `8000` | Port to listen on. |

Member and category colours are assigned automatically from a built-in palette, so any list you configure just works.

> **Note:** renaming a member or category only changes the options offered in the UI — existing bills keep the value they were saved with. Edit those bills to move them to a new name.

## Push notifications

Alerts are sent through [ntfy](https://ntfy.sh), a simple pub-sub notification service with free mobile apps.

1. Install the ntfy app ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) / [iOS](https://apps.apple.com/us/app/ntfy/id1625396347)) or subscribe in a browser at ntfy.sh
2. Pick a topic name and subscribe to it in the app
3. Set `NTFY_TOPIC` to the same name and restart the app

Visit `/test-alert` to send a test notification.

> **Security note:** on the public ntfy.sh server, anyone who guesses your topic name can read your alerts. Use a long, random topic (e.g. `bills-a8f3k2m9x`), or point `NTFY_SERVER` at a self-hosted ntfy instance.

Each day at `ALERT_HOUR`, the app sends a notification for every active bill due within `ALERT_DAYS_BEFORE` days and logs payments for any auto-pay bills due that day.

## Running as a service (Linux / Raspberry Pi)

A systemd unit file is included. Edit `bills.service` to set your user and install path, then:

```bash
sudo cp bills.service /etc/systemd/system/bills.service
sudo systemctl daemon-reload
sudo systemctl enable --now bills.service
```

## API

Simple JSON endpoints for scripting and integrations:

| Endpoint | Description |
|---|---|
| `GET /api/bills` | All bills as a JSON array. |
| `GET /api/summary` | Monthly total, annual total, and active bill count. |
| `GET /export/csv` | Download all bills as CSV. |
| `GET /test-alert` | Send a test push notification. |

The app has no authentication — it is designed for a trusted home network. Do not expose it directly to the internet; if you need remote access, put it behind a VPN (e.g. Tailscale, WireGuard) or an authenticating reverse proxy.

## Data

Everything lives in a single SQLite file, `bills.db`, in the project root. It is excluded from version control — back it up by copying the file.

## Contributing

Issues and pull requests are welcome. To run locally:

```bash
pip install -r requirements.txt
python -m app.main
```

## License

[MIT](LICENSE)
