# AI Proxy Portal

AI Proxy Portal is a FastAPI-based user portal for a LiteLLM proxy. It provides account registration, virtual API key management, usage analytics, package/payment workflows, in-browser chat, model documentation, and optional Anthropic-compatible budget proxying.

This repository is a sanitized open-source package. It intentionally excludes production secrets, SQLite data, private docs, real service IPs/domains, payment credentials, email credentials, QR codes, and internal operations notes.

## Core Features

- User registration and login with email/password.
- API Key login for existing LiteLLM virtual keys.
- LiteLLM virtual key creation, activation, blocking and budget updates.
- Dashboard for current key status, budget, remaining balance, request limits and expiration.
- Usage analytics by current API Key, including daily/weekly/monthly trends, model spend, tokens and recent calls.
- Plan purchase flow with preset bundles and configurable custom bundles.
- Optional ZPay-compatible payment URL generation and callback verification.
- Optional Tencent Cloud SES email notifications.
- Admin catalog editor for models, plans, pricing, limits and LiteLLM team settings.
- In-browser multi-turn chat using the user's own LiteLLM virtual key.
- Chat supports text, pasted/uploaded images, OpenAI-compatible multimodal `content` parts, and common generation parameters.
- Basic markdown rendering for assistant responses.
- Local and server-side chat history.
- Agent task/event dashboard for external clients that report task status.
- Optional resource status panel for upstream account pools or model capacity services.
- OpenAI-compatible API docs for users.
- Anthropic Messages compatibility proxy that checks key budget before forwarding to LiteLLM.
- Manual refund quote workflow that can deduct the order budget and record refund state.

## Architecture

```text
Browser
  |
  | /portal/
  v
FastAPI portal
  |-- SQLite: users, sessions, orders, chat history, events
  |-- LiteLLM admin API: key, model, team, spend logs
  |-- LiteLLM user API: chat completions
  |-- Optional payment gateway callback
  |-- Optional email provider
  |-- Optional resource status API
```

The portal does not replace LiteLLM. LiteLLM remains the model gateway and the source of truth for virtual key budgets, model permissions, spend and rate limits.

## Directory Layout

```text
portal/
  main.py                 FastAPI routes, dashboard aggregation, chat, payments
  db.py                   SQLite schema and data access
  auth.py                 Password hashing and token generation
  litellm_client.py       LiteLLM key/model/team/spend/chat API client
  catalog.py              Model/plan/custom pricing catalog loader and validator
  zpay.py                 Payment URL signing and callback verification
  mailer.py               Optional Tencent Cloud SES sender
  settings.py             Environment and JSON config loading
  static/                 Browser UI, docs, admin page, chat page

config/
  external_services.json  Safe example service URLs
  portal_catalog.json     Safe example models, plans and custom pricing

deploy/
  ai-portal.service.example
  nginx-portal.conf.example
  nginx-litellm-anthropic-budget.conf.example

pngs/
  README.md               Optional public image assets mounted at /assets
```

## Requirements

- Python 3.11+
- A running LiteLLM proxy with database-backed virtual key support.
- LiteLLM master key for admin operations.
- Optional: Nginx for reverse proxying.
- Optional: ZPay-compatible payment gateway.
- Optional: Tencent Cloud SES or another mail provider if you adapt `portal/mailer.py`.

Install Python dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Copy the environment template:

```bash
cp .env.example .env
```

Minimum local configuration:

```text
PORTAL_BASE_URL=http://127.0.0.1:8090
PORTAL_BIND_HOST=127.0.0.1
PORTAL_PORT=8090
PORTAL_DB_PATH=portal/data/portal.db
PORTAL_SITE_NAME=AI Proxy Portal
PORTAL_ADMIN_TOKEN=change-me-admin-token

LITELLM_BASE_URL=http://127.0.0.1:4000
LITELLM_PUBLIC_BASE_URL=http://127.0.0.1:4000
LITELLM_MASTER_KEY=change-me-litellm-master-key
```

The JSON file `config/external_services.json` provides defaults for public URLs and optional endpoints. Environment variables override those values.

The JSON file `config/portal_catalog.json` controls:

- LiteLLM team metadata.
- Public model catalog.
- Preset plans.
- Custom bundle budget range.
- Request-count tiers.
- RPM/TPM tiers.
- Pricing conversion and discounts.

For production, replace the example model ids with model names configured in your LiteLLM proxy. If you want keys to belong to a LiteLLM team, set:

```json
{
  "team": {
    "alias": "your-team-alias",
    "team_id": "your-litellm-team-id",
    "restrict_to_team_models": true
  }
}
```

If `team_id` is empty, the portal will create and update LiteLLM keys without attaching a team.

## Run Locally

Start your LiteLLM proxy first. Then run the portal:

```bash
source .venv/bin/activate
python -m uvicorn portal.main:app --host 127.0.0.1 --port 8090
```

Open:

```text
http://127.0.0.1:8090/
```

Health check:

```bash
curl http://127.0.0.1:8090/health
```

Admin catalog page:

```text
http://127.0.0.1:8090/admin
```

Use `X-Admin-Token: <PORTAL_ADMIN_TOKEN>` when calling admin APIs.

## Basic Smoke Test

1. Start LiteLLM.
2. Configure `.env`.
3. Start the portal.
4. Visit `/`.
5. Register a user.
6. Confirm LiteLLM key creation.
7. Activate a key through your payment flow, or manually update the key in LiteLLM for testing.
8. Visit dashboard and `/api/usage`.
9. Use the Chat page with a model enabled for the key.

For API Key login, paste an existing LiteLLM virtual key into the key-login form. The portal imports it as an external user and reads usage by the current API key hash when LiteLLM spend logs are not tied to the imported email.

## Chat Page

The Chat page proxies requests through the portal using the current user's virtual key. It supports:

- Text-only messages.
- Uploaded or pasted images.
- OpenAI-compatible multimodal content:

```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "What is in this diagram?"},
    {
      "type": "image_url",
      "image_url": {
        "url": "data:image/png;base64,...",
        "detail": "auto"
      }
    }
  ]
}
```

Image limits:

- PNG, JPG, WebP and GIF.
- 5 MB per image.
- 4 images per user message.

Whether the model can answer image questions depends on the selected LiteLLM model and upstream provider.

Supported generation parameters:

- `temperature`
- `top_p`
- `max_tokens`
- `frequency_penalty`
- `presence_penalty`

## Payment Integration

Payment is optional. If `ZPAY_PID` and `ZPAY_KEY` are empty, payment creation is disabled and the rest of the portal can still run.

When configured, the portal:

1. Creates an order from a selected plan or custom bundle.
2. Generates a signed payment URL.
3. Receives asynchronous payment notification.
4. Verifies the callback signature and amount.
5. Activates the LiteLLM virtual key.
6. Updates key models, max budget, duration, RPM and TPM.

Callback URLs are derived from `PORTAL_BASE_URL`:

```text
{PORTAL_BASE_URL}/api/pay/notify
{PORTAL_BASE_URL}/return/{out_trade_no}
```

## Email Integration

Email is optional. If Tencent SES variables are empty, email sending is skipped and recorded as `skipped`.

Used notifications:

- Registration success.
- Password reset.
- Payment success.

If you use another provider, replace `portal/mailer.py` while keeping the public functions:

- `send_registration_email`
- `send_password_reset_email`
- `send_payment_success_email`

## Nginx

Example reverse proxy for serving the portal under `/portal/`:

```nginx
location = /portal {
    return 301 /portal/;
}

location /portal/ {
    proxy_pass http://127.0.0.1:8090/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_read_timeout 120s;
}
```

See `deploy/nginx-portal.conf.example`.

For Anthropic-compatible Claude Code traffic, see `deploy/nginx-litellm-anthropic-budget.conf.example`.

## systemd

Copy and edit the service example:

```bash
sudo cp deploy/ai-portal.service.example /etc/systemd/system/ai-portal.service
sudo sed -i 's#/opt/ai-proxy#/your/deploy/path#g' /etc/systemd/system/ai-portal.service
sudo systemctl daemon-reload
sudo systemctl enable --now ai-portal
sudo systemctl status ai-portal --no-pager
```

## Data Storage

Default SQLite path:

```text
portal/data/portal.db
```

The database stores:

- Users and password hashes.
- LiteLLM virtual key mapping.
- Orders.
- Sessions.
- Password reset tokens.
- Chat history.
- Agent events.
- Page views.

Do not commit SQLite database files.

## Security Notes

- Never commit `.env`.
- Never commit LiteLLM master keys, API keys, payment keys or email provider credentials.
- Set a strong `PORTAL_ADMIN_TOKEN`; do not rely on the LiteLLM master key as an admin token in production.
- Serve production deployments through HTTPS.
- Keep SQLite backups private.
- Review all static docs and images before publishing your fork.
- Replace example domain names and catalog data with your own.
- Use a real license file before publishing as open source.

## What Was Removed From This Sanitized Package

- Production `.env` and `.env.example` values.
- SQLite database files.
- Python bytecode caches.
- Internal deployment notes.
- Real domains, service IPs and QR code assets.
- Payment and email provider credentials.
- Private screenshots and generated document archives.

## License

No license is included in this sanitized export. Choose and add a license, such as MIT or Apache-2.0, before publishing the repository.

