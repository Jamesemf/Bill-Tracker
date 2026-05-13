# Bill Tracker

A self-hosted web app for tracking household bills and subscriptions, with push notifications via [ntfy.sh](https://ntfy.sh).

## Features

- Add, edit, and delete bills with full CRUD
- Assign bills to a family member (James, Chris, Sophia, Daniel, Caroline)
- Track due dates, amounts, and payment frequency (weekly, monthly, quarterly, annual, one-off)
- Monthly and annual cost estimates
- **Overdue detection** — unpaid bills past their due date are flagged automatically
- **Auto-pay / direct debit flag** — payments are logged automatically on the due date each morning
- **Paid-this-month guard** — prevents accidental duplicate payments
- Daily push alerts at 08:00 via [ntfy.sh](https://ntfy.sh) for bills due within a configurable window
- Payment history with undo support
- CSV export of all bills
- Spending chart by family member (monthly)
- Spending trends chart (month-by-month from payment history)
- Search and filter bills by family member
- Sortable columns (name, amount, due date, family member)
- Dark / light mode (persists across sessions)
- Mobile-friendly layout

## Quick Start (local)

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Run the app

```bash
python -m app.main
```

Open [http://localhost:8000](http://localhost:8000).

> **Port in use?** Set a different one: `$env:PORT = "8080"` (PowerShell) or `PORT=8080` (bash)

## Configuration

All settings are controlled via environment variables:

| Variable | Default | Description |
|---|---|---|
| `NTFY_TOPIC` | *(empty)* | Your [ntfy.sh](https://ntfy.sh) topic name. Leave blank to disable push alerts. |
| `ALERT_DAYS_BEFORE` | `3` | Days before a due date to start sending alerts. |
| `HOST` | `0.0.0.0` | Host to bind to. |
| `PORT` | `8000` | Port to listen on. |

### Setting environment variables

**PowerShell (Windows)**
```powershell
$env:NTFY_TOPIC = "your-topic-here"
python -m app.main
```

**Bash (Linux / macOS)**
```bash
NTFY_TOPIC=your-topic-here python -m app.main
```

## Push Notifications

Alerts are sent via [ntfy.sh](https://ntfy.sh). To set up:

1. Install the ntfy app or subscribe at [ntfy.sh](https://ntfy.sh)
2. Pick a unique topic name (e.g. `ford-bills-abc123`)
3. Set `NTFY_TOPIC` to that name when running the app

If `NTFY_TOPIC` is not set, alerts are silently skipped.

## Running as a Service (Linux / Raspberry Pi)

A systemd service file is included. Edit `bills.service` to set your environment variables and user, then:

```bash
sudo cp bills.service /etc/systemd/system/bills.service
sudo systemctl daemon-reload
sudo systemctl enable bills.service
sudo systemctl start bills.service
sudo systemctl status bills.service
```

## Data

Bills and payment history are stored in a local SQLite database (`bills.db`). This file is excluded from version control — back it up separately if needed.

## API

Two JSON endpoints are available for scripting or external integrations:

| Endpoint | Description |
|---|---|
| `GET /api/bills` | All bills as a JSON array |
| `GET /api/summary` | Monthly total, annual total, and active bill count |
| `GET /export/csv` | Download all bills as a CSV file |
