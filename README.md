# Job Card Automation System

Initial local-first job card automation system using FastAPI, Streamlit, and PostgreSQL.

## What is included

- FastAPI backend with async PostgreSQL access.
- Streamlit frontend with Dashboard, Job Cards, Inventory, Invoices, and Settings navigation.
- First-login bootstrap: when the users table is empty, the first login creates the first admin.
- SMTP email support for account, worker profile, and customer welcome emails.
- Search and pagination on setup lists, with one setup section loaded at a time for faster saves.
- Deactivation-first record handling for master data.
- Customer vehicles are stored separately so one customer can have multiple cars.
- Inventory receiving from suppliers, stock movements, job cards, invoice records, and supplier invoice attachments stored locally.
- Itemized invoice lines can be reviewed before saving and sharing, including discount and optional tax lines.
- Invoice payment status and amount paid can be tracked for receivables.
- Job cards have a pending summary and a view/update screen for status tracking.
- Marking a job card as complete sends the customer a ready-for-pickup email when the customer has an email address.
- Role handling:
  - `admin`: can create, read, update, and delete setup data.
  - `user`: can read setup data only.
- Base tables for suppliers, users, workers, customers, customer vehicles, work types, garage costs, and garage items.

## Local setup

1. Create a PostgreSQL database named `job_card_db`.
2. Copy `.env.example` to `.env`.
3. Confirm the local database URL:

```text
LOCAL_DATABASE_URL=postgresql+asyncpg://postgres:your-local-password@localhost:5432/job_card_db
```

4. Install dependencies with UV:

```powershell
uv sync
```

If you prefer a direct install from the requirements file:

```powershell
uv pip install -r requirements.txt
```

5. Configure email in `.env`.

For Gmail, create a Gmail App Password and set:

```text
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-gmail-address@gmail.com
SMTP_PASSWORD=your-gmail-app-password
SMTP_FROM_EMAIL=your-gmail-address@gmail.com
SMTP_FROM_NAME=Job Card Automation System
SMTP_USE_TLS=true
SMTP_ENABLED=true
```

Keep `SMTP_ENABLED=false` while testing if you do not want emails to send yet.

6. Start the API:

```powershell
uv run uvicorn app.main:app --reload
```

7. Start the Streamlit app in another terminal:

```powershell
uv run streamlit run streamlit_app.py
```

8. Open Streamlit and log in. If no users exist, the first login automatically becomes the admin.

## Email behavior

- The first bootstrap admin receives a welcome email if SMTP is enabled.
- Users created by an admin receive a welcome email.
- Workers receive a worker-profile confirmation email when an email address is provided.
- Customers receive a welcome email when an email address is provided.
- Email sending is best-effort: records still save if SMTP is disabled or temporarily unavailable.

## Login refresh behavior

After login, Streamlit keeps the current login token in the page URL so refreshing the page can restore the session. Use Sign out on shared computers. For hosted production use, replace this local convenience with secure HTTP-only cookies or a proper session store.

## Password resets

The login screen includes a Forgot password form. A user enters their email address, and if the account exists, the system emails a one-time reset link. The link expires after `PASSWORD_RESET_TTL_MINUTES` and opens Streamlit with a reset token so the user can set a new password. Set `FRONTEND_URL` to the Streamlit URL that users open, for example `http://localhost:8501`.

## Customer vehicles

Customers hold contact and location details. Vehicles are stored separately under `customer_vehicles`, so the same customer can have multiple cars. The setup form still captures the first vehicle when creating a customer, then additional vehicles can be added from the Customers tab.

Vehicle make is intentionally broad, for example Toyota, Mazda, Isuzu, or Other. The specific type/model, such as Axela or Bighorn, is captured in the free-text car type field.

The Customers settings screen opens a customer file where all cars for that customer are shown together. Cars can be edited, added, or transferred to another customer without deleting or deactivating the vehicle. Transfers are recorded in `vehicle_ownership_history`.

## Deactivation

Delete actions in the UI now deactivate records that have an `is_active` field. This keeps historical references safe as job cards, assignments, supplier purchases, and invoices are added later.

## Inventory And Job Cards

Inventory starts from garage items and supplier receipts. Receiving stock records the supplier, quantity, unit cost, sale price, reference, optional supplier invoice attachment path, and remaining stock. Job cards consume inventory from the oldest available receipt first, creating stock-out movements so supplier cost history remains available.

The Inventory page includes an item cost history section. Select an item to compare supplier pricing, see the cheapest supplier, latest cost, average cost, and receipt history with references and attached invoice paths.

The Job Cards page follows the intake flow: customer/car, pickup or in-shop arrival, car condition and items present, work assignment, inventory used, itemized line review, invoice totals, then sharing. Discounts show as negative line items. Optional tax can be applied by percentage and the invoice breaks down net amount, tax, and gross total. Saved invoices can be downloaded as PDF, emailed with the PDF attached, or opened as a WhatsApp message link.

The Job Cards page also includes a View & Update tab. Pending job cards are summarized with customer, vehicle, status, invoice, and total information. Admins can update the job-card status there; when the status becomes Complete, the system queues a customer email saying the vehicle is ready.

A job card cannot be marked Closed until its latest invoice balance is zero. If there is an outstanding balance, the status update is blocked so receivables stay clean.

## Dashboard And Receivables

The Dashboard shows core counts, invoice totals, received amounts, receivables, stock value, low-stock attention items, and simple invoice/job-card status charts. The Invoices page lets an admin mark invoices as not paid, partially paid, or paid while tracking amount received and remaining balance.

## Notes

This first shell creates tables automatically at API startup to make local development easy. When the application grows, add Alembic migrations before production hosting.

## Moving To Alembic

Use the current automatic schema creation only during local prototyping. To move safely to Alembic:

1. Add Alembic to the project: `uv add alembic`.
2. Initialize async migrations: `uv run alembic init -t async alembic`.
3. In `alembic/env.py`, import `Base` from `app.db.session`, import `app.models`, set `target_metadata = Base.metadata`, and read the database URL from `get_settings().local_database_url`. That keeps Alembic pointed at the same `.env` value as FastAPI.
4. Create a baseline migration from the current models: `uv run alembic revision --autogenerate -m "baseline schema"`.
5. If your local database already has the current tables, mark it as already migrated with `uv run alembic stamp head`. For a new empty database, use `uv run alembic upgrade head`.
6. After confirming migrations work, remove `Base.metadata.create_all` and `ensure_local_schema()` from API startup so Alembic becomes the only schema owner.

Keep `.env` private. The repository should only include `.env.example` with placeholder values; put real database passwords, SMTP app passwords, and secret keys in your local `.env` or your hosting provider's secret settings.
