# Deployment and Operations Guide

This guide describes the supported production topology and release workflow. Keep credentials in the runtime secret store; never commit `.env` files.

## Production topology

- AWS Application Load Balancer terminates TLS and forwards HTTP to the EC2 host.
- nginx serves the Vite SPA from `/var/www/html/www.vigilath.com/`.
- FastAPI runs as `geo.service` on `127.0.0.1:8070`.
- MoltsPayServer is optional and outside this repository's operational scope.
- `/api/` is proxied to FastAPI; `/pay/` is proxied to the payment service.

The apex domain should redirect directly to the HTTPS `www` hostname. If a registrar forwarding rule emits an HTTP `Location`, update that rule or move the redirect to the ALB.

## Required software

Ubuntu 22.04+, Python 3.12 with `uv`, Node.js 18+, nginx, systemd, and the `sqlite3` CLI.

## Backend service

Run migrations before startup and restart on failure. The service uses `backend/.venv`, loads `backend/.env`, and runs Uvicorn on `127.0.0.1:8070` with four workers. Inspect logs with `sudo journalctl -u geo.service -f`. Store all provider credentials outside Git.

## nginx essentials

Serve the SPA fallback while preserving real discovery files (`robots.txt`, `sitemap.xml`, `llms.txt`, `llms-full.txt`, `humans.txt`, and `/.well-known/`). Hashed `/assets/` files may use immutable caching. Keep API proxy buffering disabled and use a read timeout suitable for long checks.

After changes: `sudo nginx -t && sudo systemctl reload nginx`.

## Release workflow

```bash
cd /home/ubuntu/Dev/Vigilath
git fetch origin
git status
git pull --ff-only origin main
cp backend/data/geo_checker.db backend/data/geo_checker.db.predeploy.$(date +%Y%m%d%H%M)
cd backend && uv pip install -e . && cd ..
sudo systemctl restart geo.service
cd frontend && npm ci --include=dev && npm run build
```

Publish `frontend/dist/` to the configured nginx document root and verify `/`, `/api/health`, and the discovery files.

## Backups, rollback, and monitoring

Back up SQLite before every release and keep a verified off-host copy. Roll back by restoring the previous revision and database backup, then restarting the service. Monitor journald, `X-Process-Time`, restart loops, migration failures, 5xx responses, and requests approaching the client timeout.
