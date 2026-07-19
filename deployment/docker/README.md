# Production Docker deployment (Ubuntu)

This option runs only the FastAPI API and its backup scheduler in containers.
MongoDB Community and Tailscale remain host services and are not changed. Linux
host networking is intentional: both containers reach host MongoDB at
`127.0.0.1:27017`, while Uvicorn listens only on host loopback
`127.0.0.1:8000` for Tailscale Serve.

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
sudo env TAHMEED_IMAGE_TAG=1.0.0 docker compose build --pull
sudo env TAHMEED_IMAGE_TAG=1.0.0 scripts/preflight.sh
# Review the JSON conflict report. Resolve every conflict before continuing.
sudo env TAHMEED_IMAGE_TAG=1.0.0 scripts/preflight.sh --apply-indexes
```

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

## 5. Tailscale ACL, tag, and Serve

Approve the ACL before enabling Serve. Adapt identities to your tailnet:

```json
{
  "tagOwners": {"tag:tahmeed-api": ["autogroup:admin"]},
  "grants": [{
    "src": ["group:tahmeed-users"],
    "dst": ["tag:tahmeed-api"],
    "ip": ["tcp:443"]
  }]
}
```

Then tag the host, verify the tag in the admin console, and publish loopback:

```bash
sudo tailscale set --advertise-tags=tag:tahmeed-api
sudo tailscale serve --bg --https=443 http://127.0.0.1:8000
tailscale serve status
curl --fail https://HOSTNAME.TAILNET.ts.net/health/ready
```

Never enable Funnel. Test an authorized and unauthorized identity.

## 6. Configure and pilot the Windows client

On the trusted Windows build workstation, copy `.env.production.example` to
`.env.production`. Set:

```dotenv
API_BASE_URL=https://HOSTNAME.TAILNET.ts.net
DB_NAME=tahmeed_expense
MONGODB_URI=mongodb://TEMPORARY_APP_USER:PASSWORD@TAILSCALE_MONGO_IP:27017/tahmeed_expense?authSource=tahmeed_expense
UPDATE_MANIFEST_URL=https://YOUR-UPDATE-HOST/tahmeed/version.json
```

Never put the server JWT secret, backup MongoDB credential, or S3 credentials in
the desktop build. This first foundation release is deliberately hybrid:
transactions, imports, reconciliation, and reports still use the existing
desktop `MONGODB_URI`, while login, users, catalog, fleet, and backup status use
the API. Keep the current restricted application MongoDB credential temporarily
so all screens continue working. Remove it and close desktop access to port
27017 only after those remaining domains are migrated to API endpoints.

Build and test with a small named pilot group:

```powershell
.\scripts\build_windows.ps1
.\scripts\publish_release.ps1 -Version 1.0.0
```

Confirm login, roles, representative reads/writes, Tailscale denial for a
non-member, update behavior, and API logs. Expand only after a successful daily
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
