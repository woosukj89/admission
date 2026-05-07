# MVP Beta Deployment Plan (Free Options)

## Context

Deploying the admission AI chat app for 1–2 beta testers. Key constraints:
- `data/admission.db` is **4.1 GB** — rules out most container-based free tiers (image size limits, ephemeral disks)
- Needs persistent filesystem across restarts
- Needs always-on (no cold-start spin-down for real users)
- Google OAuth redirect URI must match the live domain

---

## Pre-Deployment Checklist (required for all options)

### 1. Generate JWT_SECRET ✅ (already done)
```bash
.venv/Scripts/python.exe -c "import secrets; print(secrets.token_hex(32))"
```
Already filled in `.env` as `JWT_SECRET=<value>`.

### 2. Create `.gitignore` ✅ (already done)
File created at project root. Excludes `.env`, `data/*.db`, `.venv/`, etc.
The DB will be uploaded separately via SCP — do NOT commit it.

### 3. Update Google OAuth Authorized Redirect URIs
- Go to Google Cloud Console → APIs & Services → Credentials → your OAuth2 Web Client
- Add the production redirect URI: `https://<your-domain>/auth/callback`
- Keep `http://localhost:8000/auth/callback` for local dev

---

## Option A — Oracle Cloud Always Free (Recommended)

**Why:** Best free resources available anywhere — ARM A1 VM gives 4 OCPUs + 24 GB RAM + 200 GB storage for free, forever.

**Resources:** Always Free (Ampere A1 instance)
- 4 OCPUs, 24 GB RAM — plenty for 1–2 beta users
- 200 GB boot volume — easily fits 4.1 GB DB + room to grow
- Public IP + free HTTPS via Caddy + Let's Encrypt

### Setup Steps

**1. Create Oracle Cloud account**
- signup.cloud.oracle.com (free, requires credit card verification — will not be charged for Always Free resources)
- Choose home region: ap-seoul-1 or ap-tokyo-1

**2. Provision VM**
- Compute → Instances → Create Instance
- Shape: VM.Standard.A1.Flex (Ampere ARM), 2 OCPU, 4 GB RAM
- Image: Ubuntu 22.04 (ARM)
- Boot volume: 50 GB
- Add your SSH public key

**3. Open ports in Security List**
- Networking → VCN → Security Lists → Add Ingress Rules:
  - TCP port 22 (SSH)
  - TCP port 80 (HTTP)
  - TCP port 443 (HTTPS)

**4. Connect and set up server**
```bash
ssh ubuntu@<public-ip>

sudo apt update && sudo apt install -y python3.11 python3.11-venv python3-pip git caddy

sudo mkdir -p /opt/admission
sudo chown ubuntu:ubuntu /opt/admission
```

**5. Upload project files from your Windows machine**
```bash
scp -r src/ frontend/ pyproject.toml requirements.txt ubuntu@<ip>:/opt/admission/
scp .env ubuntu@<ip>:/opt/admission/
# Upload the 4.1 GB DB (takes ~5–15 min on home connection):
scp data/admission.db ubuntu@<ip>:/opt/admission/data/
scp data/university_meta.json data/suneung_grade_table.json ubuntu@<ip>:/opt/admission/data/
```

**6. Install dependencies**
```bash
ssh ubuntu@<ip>
cd /opt/admission
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e .

# Edit .env: set APP_BASE_URL=https://<your-domain>
nano .env
```

**7. Set up Caddy for HTTPS (automatic Let's Encrypt)**

Create `/etc/caddy/Caddyfile`:
```
your-domain.com {
    reverse_proxy localhost:8000
}
```
```bash
sudo systemctl reload caddy
```
*(If using IP only without a domain, skip Caddy and use HTTP for now)*

**8. systemd service for auto-start**

Create `/etc/systemd/system/admission.service`:
```ini
[Unit]
Description=Admission AI Chat
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/admission
EnvironmentFile=/opt/admission/.env
ExecStart=/opt/admission/.venv/bin/uvicorn src.api:app --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable admission
sudo systemctl start admission
```

---

## Option B — Google Cloud e2-micro (Always Free)

**Why:** You already have a GCP account (Google OAuth is configured there). e2-micro in us-central1 is always free. 30 GB standard persistent disk fits the 4.1 GB DB.

**Trade-off:** Only 1 GB RAM (vs 24 GB on Oracle). Fine for 1–2 beta users.

```bash
# Create VM:
gcloud compute instances create admission-app \
  --zone=us-central1-a \
  --machine-type=e2-micro \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=30GB \
  --tags=http-server,https-server

# Firewall rules:
gcloud compute firewall-rules create allow-http --allow tcp:80,tcp:443 --target-tags http-server,https-server
```

Then follow the same SCP upload, pip install, systemd setup as Option A.

---

## Option C — Cloudflare Tunnel (Instant, No Server Needed)

**Why:** Fastest path to a working URL — takes 5 minutes. No server, no upload. The DB stays on your local machine.

**Trade-off:** Your computer must stay on while beta users test.

```bash
# Install cloudflared (Windows):
winget install Cloudflare.cloudflared

# Quick temporary tunnel (URL changes on restart):
cloudflared tunnel --url http://localhost:8000
# → gives you a URL like https://abc-def.trycloudflare.com

# For a stable URL (requires a Cloudflare-managed domain):
cloudflared tunnel create admission
cloudflared tunnel route dns admission your-domain.com
cloudflared tunnel run admission
```

- Update `APP_BASE_URL` in `.env` to the tunnel URL
- Update Google OAuth redirect URI to `https://<tunnel-url>/auth/callback`
- Start the server locally: `uvicorn src.api:app --port 8000`

---

## Recommended Order

| Priority | Option | Setup Time | Stability |
|----------|--------|-----------|-----------|
| 1 (immediate) | Cloudflare Tunnel | 5 min | Requires local machine on |
| 2 (proper) | Oracle Cloud A1 | 1–2 hours | Always-on, best resources |
| 3 (if GCP preferred) | GCP e2-micro | 1 hour | Always-on, limited RAM |

**Suggested approach:**
1. Start with Cloudflare Tunnel today — send URL to beta testers immediately
2. Set up Oracle Cloud VM in parallel — switch beta testers to the proper URL once ready

---

## Verification

1. Visit `https://<your-url>` → login screen appears
2. Click "Google로 로그인" → Google consent → redirected back to chat screen
3. Send "내신 2.8등급 서울 수시 컴퓨터 관련 학과 추천해줘" → response streams with "조회 중: ..." status then markdown answer
4. Check usage counter increments in sidebar
5. Logout → redirected to login screen
