# Tahmeed API Ubuntu operations (deployment assets v1)

These assets deploy only `backend/`. They do not migrate, replace, or reconfigure
MongoDB, and they bind the API to `127.0.0.1:8000`. Tailscale Serve is the only
documented ingress path. Test on staging before the pilot.

## 1. Host preparation

Use a supported Ubuntu LTS host, apply security updates, enable time sync, and
install the prerequisites:

```bash
sudo apt update
sudo apt install -y python3 python3-venv rsync curl ca-certificates
# Install MongoDB Database Tools (mongodump/mongorestore) from MongoDB's signed repo.
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh=false
```

Copy a reviewed source tree to the host. From its root:

```bash
sudo bash deployment/v1/scripts/install-or-update.sh . 0.1.0
sudoedit /etc/tahmeed-api/tahmeed-api.env
sudo chown root:tahmeed-api /etc/tahmeed-api/tahmeed-api.env
sudo chmod 0640 /etc/tahmeed-api/tahmeed-api.env
```

The first install deliberately stops before starting when placeholders remain.
After populating the environment, either install the next immutable version or
enable and start the units:

```bash
sudo systemctl enable --now tahmeed-api.service
sudo systemctl enable --now tahmeed-backup-daily.timer
sudo systemctl enable --now tahmeed-backup-weekly.timer
sudo systemctl enable --now tahmeed-upload-retry.timer
curl --fail http://127.0.0.1:8000/health/ready
```

Releases live under `/opt/tahmeed-api/releases/<version>`. `current` and
`previous` are atomic symlinks. Updates build a fresh virtualenv and roll back
automatically if backup preflight or readiness does not pass. Every release
contains the systemd assets that activated it, so rollback restores both code
and units. Builds happen under a temporary release directory and failed builds
are removed. If preflight, activation, or readiness fails after the candidate
is assembled, the previous release and units are restored and the failed
candidate directory is removed. Five releases are retained by default (set
`TAHMEED_KEEP_RELEASES`, minimum 3). Manual rollback:

```bash
sudo bash deployment/v1/scripts/rollback.sh             # previous release
sudo bash deployment/v1/scripts/rollback.sh 0.1.0       # named release
```

Keep at least two known-good releases. Do not run `app.cli.migrate` automatically;
index changes require a separately reviewed maintenance procedure and credential.

For reproducible dependencies, commit a reviewed, hash-locked
`backend/requirements.lock` generated with a resolver such as:

```bash
python -m pip install pip-tools
python -m piptools compile --generate-hashes \
  --output-file backend/requirements.lock backend/pyproject.toml
```

The installer enforces hashes when that file exists and installs the project
without re-resolving dependencies. It always records the actual resolution in
`resolved-requirements.txt`; absence of the lock produces an explicit warning.

## 2. Private HTTPS with Tailscale Serve

Do not enable Serve until the host tag and restrictive tailnet grant below are
approved and active. Never use Funnel, which makes the service public. The API
process must remain bound to `127.0.0.1`, not `0.0.0.0`.

Example tailnet policy fragment (adapt identities and tags in the admin console):

```json
{
  "tagOwners": {"tag:tahmeed-api": ["autogroup:admin"]},
  "grants": [
    {
      "src": ["group:tahmeed-users"],
      "dst": ["tag:tahmeed-api"],
      "ip": ["tcp:443"]
    }
  ]
}
```

Apply the `tag:tahmeed-api` tag to the host, verify it in the admin console,
then publish only the loopback API:

```bash
sudo tailscale set --advertise-tags=tag:tahmeed-api
# Confirm the approved tag is attached in the admin console before continuing.
sudo tailscale serve --bg --https=443 http://127.0.0.1:8000
tailscale serve status
curl --fail https://HOSTNAME.TAILNET-NAME.ts.net/health/ready
```

On newer clients the equivalent shorthand may be `sudo tailscale serve --bg
8000`. Use `tailscale serve --help`. Test with both an authorized and an
unauthorized identity before pilot access. For UFW, retain SSH access before
enabling it:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw deny 8000/tcp
sudo ufw enable
sudo ss -ltnp | grep ':8000'  # must show 127.0.0.1 only
```

Do not open public TCP 443 for Tailscale Serve. If host SSH is not required,
remove the OpenSSH rule only after validating console access.

## 3. MongoDB least privilege

Have the MongoDB administrator create credentials; do not run these examples
blindly against production. The API needs `readWrite` only on
`tahmeed_expense`. It does not need `root`, `dbAdminAnyDatabase`, or cluster
administration:

```javascript
use tahmeed_expense
db.createUser({
  user: "tahmeed_app",
  pwd: passwordPrompt(),
  roles: [{role: "readWrite", db: "tahmeed_expense"}]
})
```

For `BACKUP_MONGODB_URI`, use a separate user with built-in `read` plus a custom
role that can write backup job metadata, the singleton cross-host lease, and
Docker scheduler claims:

```javascript
use tahmeed_expense
db.createRole({
  role: "backupMetadataWriter",
  privileges: [
    {
      resource: {db: "tahmeed_expense", collection: "backup_jobs"},
      actions: ["find", "insert", "update"]
    },
    {
      resource: {db: "tahmeed_expense", collection: "backup_leases"},
      actions: ["find", "insert", "update"]
    },
    {
      resource: {db: "tahmeed_expense", collection: "backup_schedules"},
      actions: ["find", "insert", "update"]
    }
  ],
  roles: []
})
db.createUser({
  user: "tahmeed_backup",
  pwd: passwordPrompt(),
  roles: [
    {role: "read", db: "tahmeed_expense"},
    {role: "backupMetadataWriter", db: "tahmeed_expense"}
  ]
})
```

Validate with a staging dump. Store URIs only in the root-owned environment
file and rotate them through the normal secret-management process.

The default `BACKUP_CONSISTENCY_MODE=standalone` is an off-hours,
best-effort logical dump. It is **not** a point-in-time backup: writes during a
dump can span different logical moments. Schedule it during a no-write window
or stop writers when stronger consistency is required.

`BACKUP_CONSISTENCY_MODE=oplog` is optional and only valid for a replica set.
It invokes whole-instance `mongodump --oplog` because MongoDB does not permit
`--oplog` with `--db`. Its credential needs the MongoDB-documented backup/oplog
privileges; restrict what that credential can read and inspect a staging
archive before production use. This mode is oplog-consistent for the dump, but
it is not continuous PITR.

## 4. Backups and monitoring

Daily and weekly jobs create a local archive and JSON manifest first. The Mongo
URI is passed through a temporary mode-0600 mongodump config file, never process
arguments. S3 failure cannot invalidate the local files; a separate 30-minute
retry uploads pending jobs. Upload success requires `HeadObject` size and
SHA-256 metadata checks for both object and manifest. The manifest records
database, cadence, consistency mode, archive checksum/size, tool version,
completion time, and archive version ID. Mongo metadata additionally records
both object version IDs.

A stale `uploading` job becomes retryable after 60 minutes. A local `flock` plus
a Mongo singleton lease serializes jobs across hosts; only one active backup
host is supported. The lease expires after 90 minutes by default. A 5 GiB
free-space floor prevents a dump from starting on a full volume. The newest
three successfully uploaded local generations per cadence are retained by
default. Uploaded remote backups retain 7 daily, 4 weekly, and 6 monthly time
buckets. Retention deletes the recorded archive and manifest object versions
together while preserving their job metadata in MongoDB. Bucket encryption,
versioning, restrictive IAM, lifecycle rules, and Object Lock where required
remain mandatory infrastructure controls.

Useful checks:

```bash
systemctl list-timers 'tahmeed-*'
systemctl --failed
systemctl status tahmeed-api.service
journalctl -u tahmeed-api.service --since today
journalctl -u tahmeed-backup-daily.service -u tahmeed-upload-retry.service --since '7 days ago'
curl --fail --max-time 5 http://127.0.0.1:8000/health/live
curl --fail --max-time 5 http://127.0.0.1:8000/health/ready
df -h / /var/lib/tahmeed-api/backups
du -sh /var/lib/tahmeed-api/backups
```

Alert when readiness fails for five minutes, a systemd unit fails, no successful
daily backup appears within 30 hours, pending/upload-failed jobs age beyond 2
hours, or either filesystem exceeds 80% usage. Forward journald to the existing
log platform; never log environment-file contents or Mongo/S3 credentials.
Configure journald retention appropriate to host disk capacity.

Manually exercise jobs in staging:

```bash
sudo systemctl start tahmeed-backup-daily.service
sudo systemctl start tahmeed-upload-retry.service
journalctl -u tahmeed-backup-preflight.service -n 100 --no-pager
journalctl -u tahmeed-backup-daily.service -u tahmeed-upload-retry.service -n 100 --no-pager
```

## 5. Staging and pilot rollout

1. Restore a sanitized production-sized backup into staging and use distinct
   Mongo, S3 prefix, JWT secret, Tailscale hostname, and credentials.
2. Install the candidate release; verify live/readiness, authentication, role
   boundaries, key read/write workflows, backup, upload retry, retention, and
   the isolated restore procedure in `RESTORE_AND_DR.md`.
3. Soak for 48 hours. Record latency, API errors, disk growth, backup duration,
   and upload backlog. Rehearse application rollback.
4. Pilot with a small named user group and one support owner. Keep the existing
   desktop/company workflow available; do not repoint all clients at once.
5. Expand only after a successful daily backup and restore verification during
   the pilot. Pause on data mismatch, authorization regression, repeated 5xx,
   stale backup backlog, or disk alerts.
6. Record release version, approver, timestamps, health evidence, backup object,
   and rollback decision. Roll back the API release independently of MongoDB.

The disaster-recovery and restore-verification runbook is
[`RESTORE_AND_DR.md`](RESTORE_AND_DR.md).
