# Signed Windows desktop releases

The only distributable artifact is the per-user Inno installer
`installer_output/TahmeedExpenseSetup-X.Y.Z.exe`. Do not publish a ZIP. The
desktop version has one source: `tahmeed/version.py`.

## One-time signing setup

Set a strong temporary `UPDATE_KEY_PASSWORD`, then create an encrypted private
key outside this repository:

```powershell
$env:UPDATE_KEY_PASSWORD = Read-Host -AsSecureString | ConvertFrom-SecureString -AsPlainText
python .\scripts\generate_update_key.py --key-id production-2026 --private-key E:\release-secrets\tahmeed-update.pem
Remove-Item Env:\UPDATE_KEY_PASSWORD
```

Review and commit only `tahmeed/assets/update_public_keys.json`, then rebuild all
clients that should trust the key. The bootstrap public key has no retained
private key and must be replaced before the first production release. Keep the
encrypted private key offline with restricted ACLs and a separate backed-up
password. Never place either in `.env`, source control, CI logs, or R2.

## Build and publish

Fill `.env.production` with the final Tailscale API URL, temporary restricted
MongoDB URI, and an HTTPS Cloudflare R2 custom-domain manifest URL. The R2
`r2.dev` development URL and S3 API endpoint are not client update URLs.

```powershell
.\scripts\build_windows.ps1
$env:UPDATE_KEY_PASSWORD = Read-Host -AsSecureString | ConvertFrom-SecureString -AsPlainText
.\scripts\publish_release.ps1 -Sequence 42 -KeyId production-2026 `
  -PrivateKeyPath E:\release-secrets\tahmeed-update.pem `
  -MinimumSupportedVersion 1.0.0
Remove-Item Env:\UPDATE_KEY_PASSWORD
```

Review the installer hash, `releases/version.json`, and
`releases/version.json.sig`. To upload with AWS CLI credentials supplied only by
the workstation environment:

```powershell
.\scripts\publish_release.ps1 -Sequence 42 -KeyId production-2026 `
  -PrivateKeyPath E:\release-secrets\tahmeed-update.pem -Upload `
  -R2Bucket tahmeed-updates `
  -R2EndpointUrl https://ACCOUNT_ID.r2.cloudflarestorage.com
```

The script uploads the installer first, detached manifest signature second, and
manifest last. Clients accept only strict schema version 1, a nondecreasing
sequence, stable SemVer, the configured custom-domain host, and the signed exact
installer size and SHA-256. Increment `sequence` for every publication,
including a rollback.

## Security and operations

This pipeline provides Ed25519 update authenticity but intentionally does not
provide Authenticode. Windows SmartScreen may warn users because the installer
and application are unsigned; do not tell users that the updater signature
removes that warning. Add a separately managed Authenticode certificate later
if reputation and warning suppression are required.

For manual rollback, check out/build the approved older application version,
publish its installer with a sequence higher than every previously published
manifest, and explain the rollback in `releases/notes.md`. Never reuse or lower
a sequence and never overwrite only the installer behind an existing manifest.
Retain prior manifests, signatures, installers, hashes, and release approvals
in an access-controlled archive.

Rotate keys by committing both old and new public keys, shipping that trust set,
then signing later releases with the new key. Remove an old public key only
after all supported clients have received the overlap release.
