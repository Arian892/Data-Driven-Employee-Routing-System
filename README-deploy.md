# Deployment Guide — Azure VM with Docker + GitHub Actions CI/CD

## Architecture

```
Internet ──► VM:80 ──► nginx (frontend container)
                           │
                           ├── /*        → React SPA (static files)
                           └── /api/*    → FastAPI (backend container :8000)
                                                        │
                                                        ├── car OSRM (internal :5000)
                                                        └── foot OSRM (internal :5000)
```

Four containers live on the same Azure VM, orchestrated by Docker Compose:
the frontend, backend, car-routing OSRM service, and foot-routing OSRM service.
Images are built by GitHub Actions and stored in GitHub Container Registry (GHCR).
The two OSRM services read prepared Bangladesh road graphs from
`/opt/dders/osrm-data/car-night` and `/opt/dders/osrm-data/foot`; neither graph
is included in an application image.

---

## One-time Azure VM Setup

### 1. Create the VM

In the Azure Portal (or CLI):

```bash
az vm create \
  --resource-group <your-rg> \
  --name dders-vm \
  --image Ubuntu2204 \
  --size Standard_B2s \       # 2 vCPU, 4 GB RAM — baseline for app + two OSRM services
  --admin-username azureuser \
  --generate-ssh-keys
```

Open port 80 (HTTP):

```bash
az vm open-port --resource-group <your-rg> --name dders-vm --port 80
```

> For HTTPS add port 443 and follow the certbot section below.

### 2. Install Docker on the VM

SSH in and run:

```bash
ssh azureuser@<VM_PUBLIC_IP>

# Install Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker azureuser
newgrp docker          # apply group without re-login

# Verify
docker --version
docker compose version
```

### 3. Create the app directory and secrets file

```bash
sudo mkdir -p /opt/dders
sudo chown azureuser:azureuser /opt/dders
```

Create `/opt/dders/.env.production` with your three secrets:

```env
SUPABASE_URL=https://<your-project>.supabase.co
SUPABASE_KEY=<your-supabase-service-role-or-anon-key>
JWT_SECRET_KEY=<a-long-random-string-at-least-32-chars>
```

> ⚠️  Never commit this file — it is already in `.gitignore`.

### 4. Provision the prepared OSRM graphs

Before starting Compose, provision both prepared graph directories on the VM:

```text
/opt/dders/osrm-data/car-night/bangladesh-latest.osrm*
/opt/dders/osrm-data/foot/bangladesh-latest.osrm*
```

They are generated from the same Bangladesh PBF using the repository's
`car_night.lua` and `foot.lua` profiles. The original PBF is not required to
serve routes, but should be retained outside the graph folders as a rebuild
source.

```bash
scp -r ./osrm-data/car-night ./osrm-data/foot ./osrm-data/profiles \
  azureuser@<VM_PUBLIC_IP>:/opt/dders/osrm-data/
```

The graph folders are intentionally ignored by Git because they are large.
The backend reaches car OSRM at `http://osrm:5000` and foot OSRM at
`http://osrm-foot:5000` over Docker's private network; do not expose either
port through the VM firewall. See [the OSRM graph guide](osrm-data/README.md)
for the approved build and verification process.

---

## GitHub Secrets (one-time)

Go to **GitHub → your fork → Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret name       | Value                                      |
|-------------------|--------------------------------------------|
| `AZURE_VM_HOST`   | Public IP of your Azure VM                 |
| `AZURE_VM_USER`   | `azureuser` (or whatever you set)          |
| `AZURE_VM_SSH_KEY`| Contents of `~/.ssh/id_rsa` (private key) |

`GITHUB_TOKEN` is provided automatically by GitHub Actions — no action needed.

---

## CI/CD Pipeline

Defined in `.github/workflows/deploy.yml`.

| Trigger | Jobs that run |
|---------|--------------|
| Any push / PR | **build** — builds both Docker images locally to catch broken builds |
| Push to `main` | **build** → **deploy** — pushes images to GHCR, SSHes into VM, restarts containers |

### What deploy does on the VM

```bash
cd /opt/dders
docker login ghcr.io          # with GITHUB_TOKEN
docker compose pull           # pull ghcr.io/saon110/dders-backend:latest
                              # frontend, and the pinned public OSRM image
docker compose up -d --remove-orphans
docker image prune -f         # clean up old layers
```

---

## First Manual Deploy (bootstrap)

The first time, you need to copy the compose file and start the containers manually
(before CI/CD has run):

```bash
# From your local machine
scp docker-compose.yml azureuser@<VM_IP>:/opt/dders/

# SSH into VM
ssh azureuser@<VM_IP>
cd /opt/dders

# Pull images (log into GHCR with a personal access token if images are private)
docker login ghcr.io -u Saon110 -p <your-github-PAT>

docker compose pull
docker compose up -d

# Check everything is running
docker compose ps
docker compose logs -f
```

Visit `http://<VM_PUBLIC_IP>` — the app should load.

---

## Updating the VM's IP in CORS

The backend accepts requests from any origin when proxied via nginx (same-origin from
the browser's perspective). If you call the backend directly from a different origin,
add it to `CORS_ORIGINS` in `/opt/dders/.env.production`:

```env
CORS_ORIGINS=http://<VM_PUBLIC_IP>,https://<your-domain>
```

Then restart:

```bash
docker compose up -d
```

---

## Optional: HTTPS with Let's Encrypt

```bash
sudo apt install certbot -y
sudo certbot certonly --standalone -d <your-domain.com>
```

Then update `Frontend/nginx.conf` to add an `ssl` server block and rebuild/redeploy.

---

## Troubleshooting

### `tar: docker-compose.yml: Cannot utime / Cannot change mode: Operation not permitted`

Seen in the **Copy compose file to VM** step. Root cause: `/opt/dders/docker-compose.yml`
(or the whole `/opt/dders` directory) is owned by a different user than `AZURE_VM_USER`
— usually `root`, left over from an earlier manual `sudo scp` or `sudo mkdir` bootstrap.

Why `overwrite: true` doesn't fix it: that flag only stops the remote tar from *refusing*
an existing file. Overwriting a file's **contents** only needs directory write
permission, but changing its **mode/mtime** (which tar always does after writing)
requires actually owning the file, or being root — no directory permission grants that.

**Fix already in the pipeline:** the `Ensure remote directory is owned by the deploy user`
step runs `sudo chown -R $(whoami):$(whoami) /opt/dders` before every copy, so this
self-heals on the next run automatically.

**To unblock a run right now**, SSH in once and fix it manually:

```bash
ssh <AZURE_VM_USER>@<AZURE_VM_HOST>
sudo chown -R "$(whoami):$(whoami)" /opt/dders
ls -la /opt/dders   # confirm files are now owned by your user, not root
```

Then re-run the failed workflow from the GitHub Actions tab (or push again).

---

## Useful commands on the VM

```bash
# View live logs
docker compose -f /opt/dders/docker-compose.yml logs -f

# Restart a single service
docker compose -f /opt/dders/docker-compose.yml restart backend

# Tail backend logs only
docker compose -f /opt/dders/docker-compose.yml logs -f backend

# Check container health
docker compose -f /opt/dders/docker-compose.yml ps
```
