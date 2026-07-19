# Restore verification and disaster recovery

Treat restore as a controlled data operation. Assign an incident commander and
MongoDB operator, preserve evidence, and obtain explicit approval before any
production write. Never test a restore over the existing company database.

## Scheduled restore verification

Run monthly and after backup/tooling changes.

1. Select the newest uploaded daily **manifest** and record its key and version
   ID from `backup_jobs`. If Mongo metadata is unavailable, use S3
   `list-object-versions` and the manifest naming convention; do not guess from
   an archive filename alone.
2. Download into a root-only temporary directory on an isolated staging host.
   Use a restore-only IAM principal. Set `AWS_ENDPOINT_URL` when using a
   compatible non-AWS endpoint:

   ```bash
   install -d -m 0700 /var/tmp/tahmeed-restore
   cd /var/tmp/tahmeed-restore
   export BUCKET='REPLACE_BUCKET'
   export MANIFEST_KEY='REPLACE_PREFIX/selected.archive.gz.manifest.json'
   export MANIFEST_VERSION_ID='REPLACE_VERSION_ID'
   aws s3api get-object --bucket "$BUCKET" --key "$MANIFEST_KEY" \
     --version-id "$MANIFEST_VERSION_ID" selected.manifest.json
   ```

3. Verify the manifest against its S3 SHA-256 metadata, then use the archive key
   and immutable version ID contained in that verified manifest. The following
   deliberately fails closed on a mismatch:

   ```bash
   export MANIFEST_SHA256="$(
     aws s3api head-object --bucket "$BUCKET" --key "$MANIFEST_KEY" \
       --version-id "$MANIFEST_VERSION_ID" --query 'Metadata.sha256' --output text
   )"
   printf '%s  %s\n' "$MANIFEST_SHA256" selected.manifest.json | sha256sum --check -

   export ARCHIVE_KEY="$(
     python3 -c 'import json; print(json.load(open("selected.manifest.json"))["archive_object_key"])'
   )"
   export ARCHIVE_VERSION_ID="$(
     python3 -c 'import json; print(json.load(open("selected.manifest.json"))["archive_version_id"])'
   )"
   export ARCHIVE_SHA256="$(
     python3 -c 'import json; print(json.load(open("selected.manifest.json"))["archive_sha256"])'
   )"
   export ARCHIVE_SIZE="$(
     python3 -c 'import json; print(json.load(open("selected.manifest.json"))["archive_size"])'
   )"
   aws s3api get-object --bucket "$BUCKET" --key "$ARCHIVE_KEY" \
     --version-id "$ARCHIVE_VERSION_ID" selected.archive.gz
   test "$(stat -c '%s' selected.archive.gz)" = "$ARCHIVE_SIZE"
   printf '%s  %s\n' "$ARCHIVE_SHA256" selected.archive.gz | sha256sum --check -
   ```

   If bucket versioning is disabled and the manifest contains `null`, omit both
   `--version-id` arguments and record that weaker recovery guarantee. Also
   compare `head-object` size and `Metadata.sha256` with the manifest. Do not
   place credentials or downloaded files in the source tree.

4. Restore to a disposable database name on a staging MongoDB instance. Replace
   placeholders deliberately:

   ```bash
   mongorestore \
     --uri='mongodb://RESTORE_USER@STAGING_HOST:27017/?authSource=admin' \
     --archive=/var/tmp/tahmeed-restore/selected.archive.gz --gzip \
     --nsFrom='tahmeed_expense.*' --nsTo='tahmeed_restore_verify.*' \
     --drop
   ```

5. Compare collection names and counts with the backup source metadata. Verify
   representative recent expenses, users, categories, trucks, reference links,
   date ranges, and required indexes. Start a staging API pointed only at
   `tahmeed_restore_verify`, then test readiness, login, role checks, and core
   read workflows. Do not send staging writes to production integrations.
6. Record elapsed restore time, checksum, MongoDB/tool versions, validation
   results, reviewer, and cleanup evidence. Drop the disposable database and
   securely delete the downloaded archive according to policy.

A backup is not considered verified merely because upload succeeded. Verification
passes only when checksum, restore, structural checks, and application checks all
pass.

## Production disaster recovery

### Declare and contain

1. Confirm the failure mode: API-only, host loss, MongoDB loss/corruption, S3
   outage, or credential compromise. Capture UTC timestamps and current alerts.
2. Stop client traffic. For data corruption, stop the API before further writes:

   ```bash
   sudo systemctl stop tahmeed-api.service
   ```

3. Preserve the failed host/volume and MongoDB logs when possible. Do not prune
   backups or overwrite the affected database.
4. Choose the recovery point with the business owner. State expected data loss
   (RPO) and estimated restore duration (RTO). Prefer the newest *verified*
   archive before choosing a newer unverified one.

### Recover

1. Provision a clean Ubuntu LTS host and MongoDB target with the same or a
   compatible newer MongoDB Database Tools version. Apply the deployment v1
   host hardening and Tailscale policy.
2. Retrieve the chosen manifest and archive with the explicit, version-aware
   procedure above. Verify the manifest against S3 metadata and the archive
   against the verified manifest. Treat `backup_jobs` as an additional catalog,
   not as the sole source of recovery metadata.
3. Restore first to a new database name and execute the complete scheduled
   verification procedure above. Never use `--drop` against the production
   database until approval is recorded.
4. Choose one cutover method:
   - Prefer configuring the recovered API to the newly verified database.
   - If policy requires restoring the original name, take a final archive of
     any readable current database, stop all writers, obtain approval, and have
     the MongoDB operator perform the rename/restore procedure.
5. Create fresh least-privilege API and backup credentials and a fresh JWT
   secret if compromise is possible. Do not copy secrets from source control or
   incident chat.
6. Install the last known-good immutable API release, point its root-owned
   environment file at the recovered database, and check locally:

   ```bash
   sudo systemctl start tahmeed-api.service
   curl --fail http://127.0.0.1:8000/health/live
   curl --fail http://127.0.0.1:8000/health/ready
   ```

7. Validate counts and representative records again through the API. Re-enable
   Tailscale access for the pilot group, observe errors and write behavior, then
   expand access in stages.
8. Trigger a new local backup and upload retry. Confirm the new object's
   checksum and schedule a follow-up restore verification.

### Back out and close

If validation fails, stop the API, return traffic to the preserved known-good
environment when safe, and do not attempt ad-hoc repair on the only copy. Escalate
to the MongoDB operator with captured logs and checksums.

Close the incident only after user acceptance, backup/upload confirmation,
monitoring recovery, credential rotation, and a written timeline. Record actual
RPO/RTO, root cause, lost or replayed transactions, and corrective actions.
