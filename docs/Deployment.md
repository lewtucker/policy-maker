# Deployment

## Production instance

| | |
|---|---|
| **URL** | https://policy.lewtucker.net |
| **Host** | Hostinger VPS (Ubuntu 22.04) |
| **IP** | 72.62.97.102 |
| **Server path** | `/opt/policy-maker/src/server/` |
| **Service** | `policy-maker` (systemd) |
| **Process** | `uvicorn server:app --host 0.0.0.0 --port 8080` |
| **Database** | `/opt/policy-maker/src/server/policy_maker.db` (SQLite, not deployed by CI) |
| **Environment** | `/opt/policy-maker/src/server/.env` (not deployed by CI) |

## How deployments work

Every push to `main` that touches a file under `src/` or `.github/workflows/` automatically triggers a GitHub Actions workflow (`.github/workflows/deploy.yml`) that:

1. **rsyncs** `src/server/` to the VPS — skipping `__pycache__`, `.env`, and `policy_maker.db` so server state and user data are preserved
2. **restarts** the `policy-maker` systemd service via SSH

Pushes that only touch documentation or other files outside `src/` are skipped automatically — no deploy is triggered.

## What is NOT deployed

These files live on the server only and are never overwritten by CI:

- `.env` — contains `APP_PASSWORD`, `SESSION_SECRET`, `ANTHROPIC_API_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`
- `policy_maker.db` — the SQLite database with all user accounts, rules, and activity

## GitHub Actions secret

The workflow authenticates to the VPS using a deploy key stored as a GitHub repository secret:

| Secret | Value |
|--------|-------|
| `DEPLOY_KEY` | Base64-encoded SSH private key authorised on the VPS |

## Manual operations on the server

SSH in as root:

```bash
ssh root@72.62.97.102
```

Common commands:

```bash
# Check service status
systemctl status policy-maker

# View live logs
journalctl -u policy-maker -f

# Restart manually
systemctl restart policy-maker

# Query the database
sqlite3 /opt/policy-maker/src/server/policy_maker.db
```

## Schema migrations

`database.py` runs `init_db()` at startup via FastAPI's lifespan hook. `init_db()` uses `CREATE TABLE IF NOT EXISTS` and `ALTER TABLE ... ADD COLUMN` migrations, so new columns and tables are applied automatically on the next service restart after a deploy — no manual migration step needed.
