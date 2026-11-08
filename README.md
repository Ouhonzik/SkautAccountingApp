# Skaut Accounting App

A web application that helps volunteers account for scout events: from receipt
scans to a cash book, forms and spreadsheets ready for the treasurer.

> **Status:** early stage, currently in test use at one scout unit.
> The user interface is in Czech.

## The problem

Scout events are organised by volunteers. Afterwards someone has to collect the
receipts, type amounts into spreadsheets, fill in cash vouchers and travel
orders, and hand everything over to the treasurer. This app turns that into a
shorter, less error-prone workflow.

## How it works

- **Receipt scans:** upload scans or photos (PDF, JPG, HEIC) in bulk and sort
  them into folders.
- **AI reading:** Google Gemini reads the merchant, amount and date from a
  receipt, but only when you press a button; nothing is read in the background.
- **Manual correction:** every value read by the AI can be reviewed and edited by hand.
- **Cash book** for each event.
- **Forms:** cash receipt and expense vouchers (PPD/VPD).
- **Travel orders** with a map and distance calculation (optional, via Mapy.com).
- **Participants:** import a participant list from a spreadsheet (processed
  locally), with attendance and a list of gift recipients.
- **Exports:** Excel for the treasurer, a ZIP with the scans, and a backup of a
  single event with a restore function.
- **Storage:** SQLite; schema changes go through Alembic migrations.

## Users and roles

Accounts are stored in the database. Users are invited by e-mail, passwords can
be reset by e-mail, and an activity log is kept. Roles: administrator, user, and
reviewer (read-only).

## Tech stack

Python 3.14 · FastAPI · Jinja2 · SQLite (SQLAlchemy, Alembic) · Pico CSS and
plain JavaScript · Leaflet · Google Gemini API · [uv](https://docs.astral.sh/uv/)

## Running locally

You need [uv](https://docs.astral.sh/uv/); it downloads the right Python itself.

```bash
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run spravce      # creates the first administrator account
uv run app          # http://localhost:8000
```

Nothing in `.env` is required locally. To enable receipt reading, set
`GOOGLE_API_KEY` (a key from [Google AI Studio](https://aistudio.google.com/apikey)).
Every variable is explained in `.env.example`. Put your own organisation's
details (name, address, ID numbers) into the `STREDISKO_*` variables; the
defaults are placeholders.

The fonts of the original design are licensed and not included; the app falls
back to default fonts. The logo files in `app/static/` belong to the unit that
built the app; replace them with your own.

## Data protection

Only receipt photos are sent to Google Gemini. The app itself stores personal
data of event participants (including children), so **whoever operates an
instance is responsible for GDPR compliance** (legal basis, access control,
retention, backups). Never commit your `.env` file or the `data/` folder. The
free Gemini tier is meant for development only; `.env.example` explains that a
production server should use a paid key.

## Contributing

Issues and pull requests are welcome. Please do not include real personal data
or receipts in issues.

## License

[MIT](LICENSE)
