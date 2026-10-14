# Production Docker deployment (Ubuntu)

This option runs only the FastAPI API and its backup scheduler in containers.
MongoDB Community remains a host service. Linux host networking is intentional:
containers reach host MongoDB at `127.0.0.1:27017`, and Uvicorn listens only on
host loopback `127.0.0.1:8000`. Public HTTPS is
[Cloudflare Tunnel](CLOUDFLARE.md) to `https://api.meedexpe.com`. Tailscale may
stay on the host for admin access; Tailscale Serve is not the production
ingress path.

The existing `deployment/v1` systemd deployment remains supported. Do not run
its API/timers and this Compose deployment at the same time.

## 1. Install Docker Engine on Ubuntu

Use Docker's signed apt repository (not the convenience script):

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker run --rm hello-world
```

### Reliable DNS for image builds (recommended on VPS)

If `docker compose build` fails with `could not resolve` or hangs for minutes on
`apt-get` / MongoDB repo / `pip install`, Docker’s container DNS is often the
cause. Point the daemon at public resolvers, then restart Docker:

```bash
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "dns": ["1.1.1.1", "8.8.8.8"]
}
EOF
sudo systemctl restart docker
```

Compose builds in this folder already use `network: host` (Linux only) so the
build shares the host’s DNS and routing. That does **not** change how the API
runs — containers still use `network_mode: host` at runtime as below.

Keep production Docker administration restricted to root. Membership in the
`docker` group is effectively root access.

## 2. Copy and configure

Copy a reviewed release of the repository to `/opt/tahmeed-expense`; do not copy
developer `.env`, cache, or database files. From the release root:

```bash
sudo install -d -m 0755 /opt/tahmeed-expense
sudo rsync -a --delete --exclude='.git' --exclude='.env*' \
  --exclude='__pycache__' --exclude='.pytest_cache' \
  ./ /opt/tahmeed-expense/
cd /opt/tahmeed-expense/deployment/docker
sudo cp tahmeed-api.env.example tahmeed-api.env
sudoedit tahmeed-api.env
sudo chown root:root tahmeed-api.env
sudo chmod 0600 tahmeed-api.env
sudo chmod 0755 scripts/*.sh
```

Replace every `REPLACE_...` value. URI passwords must be URL-encoded. Use
separate least-privilege API and backup MongoDB users as documented in
`../v1/README.md`; the backup role there covers `backup_jobs`, `backup_leases`,
and `backup_schedules`. Keep MongoDB bound according to its existing host
policy. The backup scheduler does not need MongoDB exposed on a container
bridge.

The image installs `mongodb-database-tools` from MongoDB's official HTTPS apt
repository using a dedicated `signed-by` keyring. It does not install or start
MongoDB server. Review the selected `MONGODB_REPO_SERIES` build argument when
upgrading tool major versions.

## 3. Duplicate/index and backup preflight

Build a versioned image, check duplicates, then apply indexes in an approved
maintenance window:

```bash
cd /opt/tahmeed-expense/deployment/docker
# Build uses host networking (see compose.yaml) so apt/PyPI/Mongo mirrors resolve.
sudo env TAHMEED_IMAGE_TAG=1.0.0 docker compose build --pull
sudo env TAHMEED_IMAGE_TAG=1.0.0 scripts/preflight.sh
# Review the JSON conflict report. Resolve every conflict before continuing.
sudo env TAHMEED_IMAGE_TAG=1.0.0 scripts/preflight.sh --apply-indexes
```

If a build still cannot resolve hostnames after the DNS step in §1, confirm
outbound HTTPS from the host (`curl -I https://pypi.org`) and retry. Manual
equivalent: `docker build --network=host -f ../../backend/Dockerfile …`.

The migration adds the unique `backup_jobs.schedule_id` index used to prevent a
second dump for one schedule slot. The scheduler stores daily, weekly, and
maintenance claims in `backup_schedules`, reclaims expired claims after
`BACKUP_SCHEDULE_STALE_MINUTES`, and reconciles an existing backup job instead
of dumping again. Existing local `flock` and renewable MongoDB backup leases
still serialize actual backup/upload/prune activity. On the configured weekly
day, the weekly backup replaces the daily backup so two large dumps are not
created. After a long restart, backup slots older than
`BACKUP_SCHEDULE_CATCHUP_MINUTES` are skipped; upload retry and pruning still run
immediately.

## 4. Start and verify

```bash
sudo scripts/deploy.sh 1.0.0
sudo env TAHMEED_IMAGE_TAG=1.0.0 docker compose ps
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
sudo ss -ltnp | grep ':8000'
```

The listener must show only `127.0.0.1:8000`. Compose uses host networking,
non-root UID/GID 10001, a read-only root filesystem, `no-new-privileges`, all
capabilities dropped, bounded PID/tmp/log usage, health checks, and
`unless-stopped` restart policies. `tahmeed-api-backups` is the named volume for
archives, manifests, and the local lock.

## 5. Public HTTPS (Cloudflare Tunnel)

Do **not** stop the API containers to add ingress. Follow
[CLOUDFLARE.md](CLOUDFLARE.md):

1. Cloudflare zone for `meedexpe.com`.
2. Tunnel hostname `api.meedexpe.com` → `http://127.0.0.1:8000`.
3. Host `cloudflared` systemd connector; leave Docker running.
4. Verify from a non-Tailscale network:
   `curl -fsS https://api.meedexpe.com/health/ready`.
5. Only then cut Tailscale Serve (`sudo tailscale serve reset`). Keep the
   Tailscale node if you still want admin SSH over the tailnet. Never enable
   Funnel.

Optional temporary overlap: Tailscale Serve may remain up until step 4 passes
so existing pilots are not interrupted.

Firewall: deny public `8000` and `27017`; API and Mongo stay on loopback.

## 6. Configure and pilot the Windows client

On the trusted Windows build workstation, copy `.env.production.example` to
`.env.production`. Set:

```dotenv
API_BASE_URL=https://api.meedexpe.com
UPDATE_MANIFEST_URL=https://updates.example.com/tahmeed/version.json
```

Do **not** bake `MONGODB_URI` or `DB_NAME` into the desktop installer — the app
is API-only (Phase 11). Never put the server JWT secret, backup MongoDB
credential, or S3 credentials in the desktop build.

Build and test with a small named pilot group:

```powershell
.\scripts\build_windows.ps1
.\scripts\publish_release.ps1 -Sequence 1 -KeyId production-2026 `
  -PrivateKeyPath E:\release-secrets\tahmeed-update.pem
```

Use an R2 custom domain for `UPDATE_MANIFEST_URL`; see `releases/README.md` for
key custody, ordered publishing, rollback, and the Authenticode/SmartScreen
limitation.

Confirm login, roles, representative reads/writes (no Tailscale required on
user PCs), update behavior, and API logs. Expand only after a successful daily
backup and restore verification.

## 7. Backup and restore smoke tests

Run a manual local backup and upload cycle:

```bash
cd /opt/tahmeed-expense/deployment/docker
sudo docker compose run --rm scheduler tahmeed-backup run --cadence daily
sudo docker compose run --rm scheduler tahmeed-backup retry
sudo docker compose run --rm scheduler tahmeed-backup prune
sudo docker volume inspect tahmeed-api-backups
```

For a restore smoke test, use a disposable database on an isolated staging
MongoDB host—never production. Download the selected archive and manifest,
verify both SHA-256 values as described in `../v1/RESTORE_AND_DR.md`, then:

```bash
sudo install -d -m 0700 /var/tmp/tahmeed-restore
# Place the verified archive at /var/tmp/tahmeed-restore/selected.archive.gz.
mongorestore \
  --uri='mongodb://RESTORE_USER@STAGING_HOST:27017/?authSource=admin' \
  --archive=/var/tmp/tahmeed-restore/selected.archive.gz --gzip \
  --nsFrom='tahmeed_expense.*' --nsTo='tahmeed_restore_smoke.*' --drop
```

Compare collections/counts and start a staging API against only
`tahmeed_restore_smoke`. Record evidence, then drop that disposable database
and securely remove the archive.

## 8. Logs, updates, rollback, and troubleshooting

```bash
sudo docker compose ps
sudo docker compose logs --since=24h api
sudo docker compose logs --since=7d scheduler
sudo docker inspect --format '{{json .State.Health}}' tahmeed-api-api-1

# After copying a reviewed newer source release:
sudo scripts/preflight.sh --apply-indexes
sudo scripts/deploy.sh 1.0.1

# Roll back code only; never roll back MongoDB data automatically:
sudo scripts/rollback.sh              # recorded previous image
sudo scripts/rollback.sh 1.0.0        # explicit retained image
```

Troubleshooting:

- Build `could not resolve` / hung on apt or pip: apply the Docker `dns`
  block in §1, `sudo systemctl restart docker`, then rebuild. Compose already
  sets `build.network: host`. Check host egress with
  `curl -fsS -o /dev/null -w '%{http_code}\n' https://pypi.org` and
  `https://repo.mongodb.org`.
- `connection refused 127.0.0.1:27017`: verify host MongoDB is running and
  listening on loopback. Host networking is Linux-only; Docker Desktop on
  Windows cannot validate this production topology.
- API unhealthy: run the readiness curl, inspect API logs, and verify the API
  MongoDB credential and database name.
- Scheduler permission error: verify the named volume ownership and paths, plus
  backup user access to `backup_jobs`, `backup_leases`, and `backup_schedules`.
- Repeated schedule failure: inspect the matching `backup_schedules` and
  `backup_jobs.schedule_id`. A stale/failed existing job intentionally blocks a
  duplicate dump; investigate and document before changing records.
- `mongodump` failure: run `tahmeed-backup preflight`, check consistency mode,
  free space, MongoDB role, and Database Tools compatibility.
- Compose reports a port conflict: stop the systemd API deployment or other
  loopback listener. Do not change the container to `0.0.0.0`.
- S3 backlog: verify endpoint/IAM/bucket encryption and inspect `upload_failed`
  errors. Local archives remain in the named volume until successful upload and
  retention rules permit pruning.

Back up the named volume before destructive Docker maintenance. `docker compose
down` keeps it; `docker compose down --volumes` deletes local backups and must
not be used in production.
