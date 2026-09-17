# OSRM routing data — Azure VM deployment guide

This folder holds the **routing map data** and the **Lua profiles** for the two
OSRM engines the backend solver depends on. The generated graph files themselves
are **not** in Git (too large); they are copied to the VM out-of-band.

> **Audience:** whoever deploys the Azure VM. The frontend/backend containers are
> already deployed; this guide only adds/updates the OSRM services.

---

## 1. What runs, and why there are two engines

The routing solver (`backend/app/services/routing/`) needs **two** OSRM servers:

| Compose service | Profile used to build it | Endpoint (internal) | Used for |
|---|---|---|---|
| `osrm` | `car_night.lua` (driving, **left-hand**) | `http://osrm:5000` | car durations, distances, route geometry |
| `osrm-foot` | `foot.lua` (pedestrian) | `http://osrm-foot:5000` | Case A walk times (can a rider walk to a fixed stop?) |

Both are reached over Docker Compose's private network. **Do not publish ports
5000/5001 to the VM host.**

> **The car engine MUST be built with `profiles/car_night.lua`, never OSRM's
> stock `/opt/car.lua`.** `car_night.lua` sets `left_hand_driving = true`
> (Bangladesh) and the Dhaka-night speed calibration. Both are baked into the
> graph at **extract** time, so a wrong profile cannot be fixed by restarting
> `osrm-routed` — the graph has to be rebuilt.

---

## 2. ⚠️ Critical: the OSRM image version must match the graph version

The prebuilt graphs were generated with **OSRM 26.8.0**. An older runtime refuses
to load them, e.g.:

```
[error] File is incompatible with this version of OSRM:
/data/bangladesh-latest.osrm.cells prepared with OSRM 26.8.0 but this is v26.7.3
```

`docker-compose.yml` therefore pins both services to:

```yaml
image: ghcr.io/project-osrm/osrm-backend:26.8.0-debian
```

If you rebuild the graphs (Option B below), use this **same** image for
`osrm-extract` / `osrm-partition` / `osrm-customize` as you use to serve.

---

## 3. Files in this folder

```
osrm-data/
├── README.md            # this file
├── profiles/            # tracked in Git (small) — needed to serve OR rebuild
│   ├── car_night.lua    # car profile: left_hand_driving + Dhaka-night speeds
│   ├── foot.lua         # pedestrian profile
│   └── lib/             # Lua helpers car_night.lua/foot.lua require()
├── car-night/           # NOT in Git — the driving graph (copied to the VM)
└── foot/                # NOT in Git — the pedestrian graph (copied to the VM)
```

`profiles/` **must stay next to the graph folders** only if you rebuild; serving
reads only the graph folders. `car_night.lua` does `require('lib/...')`, so the
`lib/` folder must travel with it whenever you extract.

Expected host layout on the VM:

```
/opt/dders/osrm-data/car-night/bangladesh-latest.osrm*
/opt/dders/osrm-data/foot/bangladesh-latest.osrm*
/opt/dders/osrm-data/profiles/{car_night.lua,foot.lua,lib/}
```

---

## 4. Get the graphs onto the VM

### Option A — copy the prebuilt graphs (recommended)

From the machine that holds them (the repo's `../test-routing/` on the dev
machine; `car-night/` ≈ 811 MB, `foot/` ≈ 849 MB):

```bash
scp -r car-night foot profiles azureuser@<VM_PUBLIC_IP>:/opt/dders/osrm-data/
```

The folders contain `bangladesh-latest.osrm` plus its `.osrm.*` sidecars
(`.cells`, `.mldgr`, `.geometry`, …). The original `.pbf` is **not** needed to
serve. Do **not** copy the graphs through Git — they are `.gitignore`d.

### Option B — rebuild on the VM from the `.pbf`

Only if transferring the graphs is impractical. Copy the PBF
(`bangladesh-latest.osm.pbf`, ≈ 332 MB) into `/opt/dders/osrm-data/`, then build
each graph with the pinned image and the matching profile.

> `osrm-extract` writes its output **next to its input file**, so the `.pbf` has
> to sit inside `car-night/` (and `foot/`) while you build, then be removed.
> Extraction is memory-hungry — build on a machine with enough RAM (a temporary
> larger VM), or prefer Option A.

```bash
cd /opt/dders/osrm-data
IMG=ghcr.io/project-osrm/osrm-backend:26.8.0-debian

# ── car (driving) ─────────────────────────────────────────────
cp bangladesh-latest.osm.pbf car-night/
docker run -t --rm -v "$PWD/car-night:/data" -v "$PWD/profiles:/profiles:ro" \
  $IMG osrm-extract -p /profiles/car_night.lua /data/bangladesh-latest.osm.pbf
docker run -t --rm -v "$PWD/car-night:/data" $IMG osrm-partition /data/bangladesh-latest.osrm
docker run -t --rm -v "$PWD/car-night:/data" $IMG osrm-customize /data/bangladesh-latest.osrm
rm -f car-night/bangladesh-latest.osm.pbf

# ── foot (pedestrian) ────────────────────────────────────────
cp bangladesh-latest.osm.pbf foot/
docker run -t --rm -v "$PWD/foot:/data" -v "$PWD/profiles:/profiles:ro" \
  $IMG osrm-extract -p /profiles/foot.lua /data/bangladesh-latest.osm.pbf
docker run -t --rm -v "$PWD/foot:/data" $IMG osrm-partition /data/bangladesh-latest.osrm
docker run -t --rm -v "$PWD/foot:/data" $IMG osrm-customize /data/bangladesh-latest.osrm
rm -f foot/bangladesh-latest.osm.pbf
```

---

## 5. The Compose services

`docker-compose.yml` defines both engines and points the backend at them:

```yaml
  osrm:
    image: ghcr.io/project-osrm/osrm-backend:26.8.0-debian
    restart: unless-stopped
    volumes:
      - ./osrm-data/car-night:/data:ro
    command: osrm-routed --algorithm mld /data/bangladesh-latest.osrm
    expose:
      - "5000"

  osrm-foot:
    image: ghcr.io/project-osrm/osrm-backend:26.8.0-debian
    restart: unless-stopped
    volumes:
      - ./osrm-data/foot:/data:ro
    command: osrm-routed --algorithm mld /data/bangladesh-latest.osrm
    expose:
      - "5000"
```

and on the `backend` service:

```yaml
    environment:
      ROUTING_ENGINE: osrm
      OSRM_BASE_URL: http://osrm:5000
      OSRM_FOOT_BASE_URL: http://osrm-foot:5000
    depends_on:
      osrm:
        condition: service_started
      osrm-foot:
        condition: service_started
```

> **CI/CD copies `docker-compose.yml` to the VM on every successful push to
> `main`, then pulls images and recreates the stack.** The graph data is the
> exception: it is deliberately excluded from Git and must be copied or built
> on the VM before the deployment that starts these services.

---

## 6. Deploy

```bash
# Push the compose change to main and wait for the deployment workflow to
# copy it to the VM, pull images, and recreate the services.
```

`ROUTING_ENGINE`/`OSRM_*` are set inline in the compose file, so
`/opt/dders/.env.production` needs no change.

---

## 7. Verify

```bash
cd /opt/dders
docker compose ps            # osrm, osrm-foot, backend, frontend all Up

# The ports are internal, so probe from inside the network via the backend image
docker compose exec backend python3 -c "import urllib.request as u; print('car ', u.urlopen('http://osrm:5000/route/v1/driving/90.4085,23.7702;90.4095,23.7702?overview=false', timeout=10).status)"
docker compose exec backend python3 -c "import urllib.request as u; print('foot', u.urlopen('http://osrm-foot:5000/table/v1/foot/90.4085,23.7702;90.4095,23.7702?annotations=duration', timeout=10).status)"

# Backend must NOT report the haversine fallback
docker compose logs backend | grep -i "osrm"
```

Expected in the backend logs:

```
routing: using OSRM at http://osrm:5000
routing: using OSRM foot at http://osrm-foot:5000
```

If you instead see `OSRM unreachable ... falling back to haversine`, the graph
mount or the image version is wrong — check `docker compose logs osrm` and
`docker compose logs osrm-foot`.

Finally, trigger a routing run from the admin UI for a real service date and
confirm routes are produced.

---

## 8. Why this is not in Git (and not Git LFS)

The graph folders are large binary artifacts (≈ 1.66 GB total) and change
whenever the map/profile changes. They are listed in `.gitignore`
(`/osrm-data/*`, with the README and `profiles/` re-included) and provisioned
directly on the host, so no Git LFS is needed. Only the small Lua profiles and
this document are tracked.

---

## 9. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `File is incompatible with this version of OSRM` | Runtime image ≠ graph build version. Use `ghcr.io/project-osrm/osrm-backend:26.8.0-debian` for both building and serving. |
| `osrm` container restarts / exits | Graph missing or wrong mount. `docker compose logs osrm`; confirm `osrm-data/car-night/bangladesh-latest.osrm` exists. |
| Backend logs `falling back to haversine` | OSRM unreachable from `backend`: wrong service name/port or the container is down. |
| Roundabout/turn directions mirrored, odd routes | Car graph built with the stock profile. Rebuild with `car_night.lua`. |
| `osrm-extract` killed (OOM) | Build on a larger machine and copy the graph (Option A). |
