# Phillips Project Tracker

React + FastAPI replacement for the Excel tracker tabs:

- Ad Hoc
- Buys
- Completed
- Login and role-based access
- Admin user management

## Stack

- Frontend: React, TypeScript, Vite
- Backend: FastAPI
- Database: SQLite locally, PostgreSQL in production when `DATABASE_URL` is set

## Dev Login

For local development, the backend creates this admin automatically unless env vars override it:

- Email: `admin@example.com`
- Password: `Admin123`

For Render/production, set:

- `ADMIN_EMAIL`
- `ADMIN_PASSWORD`
- `ADMIN_NAME`
- `DATABASE_URL` from the Render PostgreSQL database
- `CLOUDINARY_CLOUD_NAME`
- `CLOUDINARY_API_KEY`
- `CLOUDINARY_API_SECRET`

## Import the Workbook

```powershell
python backend\scripts\import_excel.py "C:\Users\FSIJonathanCastro-Ra\Downloads\Phillips Project Tracker - Copy for FSI Dev.xlsx"
```

## Run the App

Install frontend packages once:

```powershell
cd frontend
npm install
```

Install backend packages once:

```powershell
python -m pip install -r backend\requirements.txt
```

Start the backend API from the project root:

```powershell
python backend\app.py
```

FastAPI docs are available at `http://localhost:8000/docs`.

In another terminal, start React:

```powershell
cd frontend
npm run dev
```

Open the Vite URL shown in the terminal, usually `http://localhost:5173`.

## Production-Style Local Run

Build React and let FastAPI serve the built frontend:

```powershell
cd frontend
npm run build
cd ..
python backend\app.py
```

Then open `http://localhost:8000`.

## Render Deployment

This repo includes `render.yaml` for a Render Blueprint.

Render will:

- create a FastAPI web service
- create a PostgreSQL database
- provide `DATABASE_URL` to the app
- install backend dependencies
- build the React frontend
- start the backend with `python backend/app.py`
- load `backend/data/seed_items.json` into an empty tracker database

Before deploying publicly, set `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `ADMIN_NAME`, and the
`CLOUDINARY_*` values in Render.

### NetSuite catalog sync

The catalog reconciles against the NetSuite item master once a night (`render.yaml`
cron `smartbuy-catalog-sync`, 07:00 UTC) and on demand from **Survey / Buy Book →
Buy Book Layout → Sync from NetSuite**. Read-only towards NetSuite: SuiteQL
queries only, nothing is ever written back.

Set these on the web service **and** the cron job:

- `NETSUITE_CONSUMER_KEY`, `NETSUITE_CONSUMER_SECRET`, `NETSUITE_TOKEN_ID`,
  `NETSUITE_TOKEN_SECRET` — a token-based-auth (TBA) integration token whose role
  has SuiteQL access. Read-only role; the app never posts a record.
- `NETSUITE_REALM` — the account id (e.g. `1234567`), upper-cased here.
- `NETSUITE_BASE_URL` — the REST base, e.g. `https://1234567.suitetalk.api.netsuite.com`.
  Quotes and a pasted `htttps://` are repaired on read.
- `NETSUITE_SYNC_COMPANY_SLUG` — the one company a run writes to (default: the
  app's company).

Run it by hand before trusting the cron:

```
python backend/scripts/sync_catalog.py                    # dry run, prints the report
python backend/scripts/sync_catalog.py --apply            # writes
python backend/scripts/sync_catalog.py --limit 200 --report /tmp/run.json
```

A dry run is the same code path inside a transaction that is rolled back, so it
is an honest preview. `python backend/scripts/netsuite_sync.py` is the offline
acceptance check (add `--live` for one real read when the credentials are set).
