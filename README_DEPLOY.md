# Scenario Debugger — Deployment Guide

Step-by-step instructions to deploy on a Linux server (RHEL/CentOS/Ubuntu).
Every command is copy-paste ready.

---

## Prerequisites

- Linux server (RHEL 8+, CentOS 8+, or Ubuntu 20.04+)
- Python 3.12+ installed
- Node.js 18+ installed
- Nginx installed (`sudo dnf install nginx` or `sudo apt install nginx`)
- `uv` installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Network access to Oracle DB from this server

---

## Step 1 — Create a dedicated service user

```bash
# Create a low-privilege user that runs the app
sudo useradd -r -s /bin/false scenario-debugger

# Verify
id scenario-debugger
```

---

## Step 2 — Copy application files

```bash
# Create app directory
sudo mkdir -p /opt/scenario-debugger
sudo chown scenario-debugger:scenario-debugger /opt/scenario-debugger

# Copy your project files (from your dev machine or git)
# Option A: from git
cd /opt/scenario-debugger
sudo -u scenario-debugger git clone <your-repo-url> .

# Option B: copy manually via scp
scp -r ./scenario-debugger user@yourserver:/opt/
```

---

## Step 3 — Configure environment

```bash
cd /opt/scenario-debugger/backend

# Copy the example env file
sudo -u scenario-debugger cp .env.example .env

# Edit with your actual values
sudo nano .env
```

**Minimum required changes in `.env`:**
```
APP_BASE_PATH=/opt/scenario-debugger/data
SECRET_KEY=<generate with: python3 -c "import secrets; print(secrets.token_hex(32))">
DB_USERNAME=your_oracle_username
DB_PASSWORD=your_oracle_password
DB_DSN=your_oracle_dsn
SERVER_HOST=your_ofsaa_server_ip
SERVER_USERNAME=your_ssh_username
SERVER_PASSWORD=your_ssh_password
MANTAS_BATCH_PATH=/path/to/mantas/batch
OUTPUT_BASE_PATH=/opt/scenario-debugger/data/outputs
MAX_SQL_FILE_BYTES=10485760
API_HOST=127.0.0.1
API_PORT=8000
CORS_ORIGINS=https://scenario-debugger.yourbank.com
```

```bash
# Lock down the .env file — only the service user can read it
sudo chown scenario-debugger:scenario-debugger .env
sudo chmod 600 .env
```

---

## Step 4 — Install Python dependencies

```bash
cd /opt/scenario-debugger/backend
sudo -u scenario-debugger uv sync

# Or if no pyproject.toml yet:
sudo -u scenario-debugger uv add fastapi uvicorn python-dotenv PyJWT \
    bcrypt paramiko python-multipart pydantic oracledb
```

---

## Step 5 — Build the React frontend

```bash
cd /opt/scenario-debugger/frontend

# Set production API URL
echo "VITE_API_URL=https://scenario-debugger.yourbank.com" > .env

# Install dependencies and build
npm install
npm run build
# This creates frontend/dist/ — the static files Nginx will serve
```

---

## Step 6 — SSL Certificate

### Option A — Bank CA certificate (recommended)
```bash
# Copy your bank-issued certificate files
sudo cp your-cert.crt /etc/ssl/certs/scenario-debugger.crt
sudo cp your-cert.key /etc/ssl/private/scenario-debugger.key
sudo chmod 600 /etc/ssl/private/scenario-debugger.key
```

### Option B — Self-signed (testing/internal only)
```bash
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout /etc/ssl/private/scenario-debugger.key \
    -out /etc/ssl/certs/scenario-debugger.crt \
    -subj "/C=IN/ST=Maharashtra/O=YourBank/CN=scenario-debugger.yourbank.com"
sudo chmod 600 /etc/ssl/private/scenario-debugger.key
```

---

## Step 7 — Configure Nginx

```bash
# Copy nginx config
sudo cp /opt/scenario-debugger/nginx/scenario-debugger.conf \
        /etc/nginx/sites-available/

# Edit — replace server_name and paths with yours
sudo nano /etc/nginx/sites-available/scenario-debugger.conf

# Enable the site
sudo ln -s /etc/nginx/sites-available/scenario-debugger.conf \
           /etc/nginx/sites-enabled/

# Remove default site if present
sudo rm -f /etc/nginx/sites-enabled/default

# Test the config — MUST say "test is successful"
sudo nginx -t

# Reload nginx
sudo systemctl reload nginx
sudo systemctl enable nginx
```

---

## Step 8 — Configure Systemd service

```bash
# Copy service file
sudo cp /opt/scenario-debugger/systemd/scenario-debugger.service \
        /etc/systemd/system/

# Find where uv is installed and update ExecStart if needed
which uv   # e.g. /home/youruser/.local/bin/uv
# Update the path in the service file if different from /usr/local/bin/uv
sudo nano /etc/systemd/system/scenario-debugger.service

# Reload systemd so it sees the new service
sudo systemctl daemon-reload

# Enable — starts automatically on server reboot
sudo systemctl enable scenario-debugger

# Start now
sudo systemctl start scenario-debugger

# Check it's running
sudo systemctl status scenario-debugger
```

Expected output:
```
● scenario-debugger.service - OFSAA Scenario Debugger — FastAPI Backend
     Loaded: loaded (/etc/systemd/system/scenario-debugger.service; enabled)
     Active: active (running) since ...
```

---

## Step 9 — Verify everything works

```bash
# 1. Check FastAPI is running internally
curl http://127.0.0.1:8000/api/health
# Expected: {"status":"healthy","version":"1.0.0"}

# 2. Check Nginx is serving the app
curl -k https://localhost/api/health
# Expected: {"status":"healthy","version":"1.0.0"}

# 3. Open in browser
# https://scenario-debugger.yourbank.com
# Login with: admin / changeme123
# CHANGE THIS PASSWORD IMMEDIATELY
```

---

## Step 10 — Change default admin password

1. Open the app in browser
2. Login with `admin / changeme123`
3. Go to Admin panel → Users → change password

Or via the API:
```bash
# Login to get token
TOKEN=$(curl -s -X POST https://scenario-debugger.yourbank.com/api/auth/login \
  -d "username=admin&password=changeme123" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Create a proper admin account
curl -X POST https://scenario-debugger.yourbank.com/api/admin/users \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username":"yourname","password":"StrongPassword123!","full_name":"Your Name","email":"you@bank.com","role":"admin"}'
```

---

## Updating the application

```bash
# Pull latest code
cd /opt/scenario-debugger
sudo -u scenario-debugger git pull

# Rebuild frontend
cd frontend && npm run build

# Restart backend
sudo systemctl restart scenario-debugger

# Check logs
sudo journalctl -u scenario-debugger -n 50
```

---

## Useful commands

```bash
# Live logs
sudo journalctl -u scenario-debugger -f

# Last 100 log lines
sudo journalctl -u scenario-debugger -n 100

# Nginx logs
sudo tail -f /var/log/nginx/scenario-debugger-access.log
sudo tail -f /var/log/nginx/scenario-debugger-error.log

# Restart app
sudo systemctl restart scenario-debugger

# Stop app
sudo systemctl stop scenario-debugger

# Check app status
sudo systemctl status scenario-debugger

# Check port is listening
sudo ss -tlnp | grep 8000
```

---

## Troubleshooting

| Problem | Check |
|---|---|
| App not starting | `sudo journalctl -u scenario-debugger -n 50` |
| Nginx 502 Bad Gateway | FastAPI not running — check systemd status |
| WebSocket not working | Check nginx has `Upgrade` and `Connection` headers |
| Oracle connection fails | Check DB_USERNAME, DB_PASSWORD, DB_DSN in .env |
| Login page loads but login fails | Check SECRET_KEY is set in .env |
| File upload fails | Check MAX_UPLOAD_MB in .env and `client_max_body_size` in nginx.conf |
| Permission denied errors | Check `/opt/scenario-debugger` owned by `scenario-debugger` user |
