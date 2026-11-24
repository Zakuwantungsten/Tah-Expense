#!/usr/bin/env bash
# Interactive helper: mount a host disk for PDF receipt storage and wire it
# to the Docker API/scheduler bind mount.
#
# Usage (on the Ubuntu host):
#   sudo scripts/attach-receipt-disk.sh
#
# You only pick the disk and keep-or-format. Paths are fixed:
#   format → mount /mnt/tahmeed-receipts  (whole disk = receipts root)
#   keep   → mount /mnt/tahmeed-data + auto find/create tahmeed-receipts/
#
# Remounting the same disk reuses the existing receipts folder (no path prompts).
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DEFAULT_MOUNT_DEDICATED="/mnt/tahmeed-receipts"
DEFAULT_MOUNT_SHARED="/mnt/tahmeed-data"
RECEIPTS_SUBDIR="tahmeed-receipts"
CONTAINER_UID=10001
CONTAINER_GID=10001
FSTAB_COMMENT="# tahmeed-receipts (managed by attach-receipt-disk.sh)"
CANDIDATES=()

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "$*"; }

ask() {
  local prompt="$1" default="${2:-}" reply
  if [[ -n "$default" ]]; then
    read -r -p "$prompt [$default]: " reply || true
    printf '%s' "${reply:-$default}"
  else
    read -r -p "$prompt: " reply || true
    printf '%s' "$reply"
  fi
}

confirm_yes() {
  local reply
  read -r -p "$1 [y/N]: " reply || true
  [[ "${reply,,}" == "y" || "${reply,,}" == "yes" ]]
}

need_root() {
  [[ "$(id -u)" -eq 0 ]] || die "run as root: sudo scripts/attach-receipt-disk.sh"
}

need_tty() {
  [[ -t 0 && -t 1 ]] || die "this script is interactive; run it from a terminal"
}

need_cmds() {
  local c
  for c in lsblk findmnt blkid mount umount awk sed grep; do
    command -v "$c" >/dev/null 2>&1 || die "missing required command: $c"
  done
}

# Extract KEY="value" from lsblk -P lines without eval (eval would clobber PATH).
lsblk_field() {
  local key="$1" line="$2"
  local rest="${line#*"${key}=\""}"
  if [[ "$rest" == "$line" ]]; then
    printf ''
    return 0
  fi
  printf '%s' "${rest%%\"*}"
}

root_disk() {
  local src base
  src="$(findmnt -n -o SOURCE / 2>/dev/null || true)"
  [[ -n "$src" ]] || { printf ''; return 0; }
  base="$(lsblk -no PKNAME "$src" 2>/dev/null | head -n1 | tr -d '[:space:]' || true)"
  if [[ -n "$base" ]]; then
    printf '%s' "$base"
  else
    printf '%s' "$(basename "$src")"
  fi
}

device_fstype() {
  blkid -o value -s TYPE "$1" 2>/dev/null || true
}

device_uuid() {
  blkid -o value -s UUID "$1" 2>/dev/null || true
}

current_receipt_status() {
  info ""
  info "=== Current receipt mount ==="
  local shown=0
  local mp
  for mp in "$DEFAULT_MOUNT_DEDICATED" "$DEFAULT_MOUNT_SHARED"; do
    if findmnt -n "$mp" >/dev/null 2>&1; then
      findmnt -n -o SOURCE,FSTYPE,OPTIONS,TARGET "$mp" || true
      shown=1
    fi
  done
  if [[ "$shown" -eq 0 ]]; then
    info "No standard receipt mounts yet ($DEFAULT_MOUNT_DEDICATED / $DEFAULT_MOUNT_SHARED)."
  fi
  if [[ -f .env ]] && grep -q '^ATTACHMENTS_HOST_PATH=' .env 2>/dev/null; then
    info "Compose .env: $(grep '^ATTACHMENTS_HOST_PATH=' .env)"
  else
    info "Compose ATTACHMENTS_HOST_PATH: (default $DEFAULT_MOUNT_DEDICATED)"
  fi
}

# Fills CANDIDATES with: name|devpath|size|type|fstype|mount|model|tran
list_candidates() {
  CANDIDATES=()
  local root_pk line name size type fstype mount model tran pkname kids
  root_pk="$(root_disk)"

  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    name="$(lsblk_field NAME "$line")"
    size="$(lsblk_field SIZE "$line")"
    type="$(lsblk_field TYPE "$line")"
    fstype="$(lsblk_field FSTYPE "$line")"
    mount="$(lsblk_field MOUNTPOINT "$line")"
    model="$(lsblk_field MODEL "$line")"
    tran="$(lsblk_field TRAN "$line")"
    pkname="$(lsblk_field PKNAME "$line")"

    [[ -n "$name" ]] || continue
    case "$type" in
      disk|part) ;;
      *) continue ;;
    esac
    [[ "$name" == loop* || "$name" == ram* || "$name" == zram* ]] && continue
    # Skip swap / tiny EFI-ish volumes — not usable for receipts
    case "${fstype,,}" in
      swap|linux_raid_member|crypto_LUKS) continue ;;
    esac

    if [[ -n "$root_pk" ]]; then
      [[ "$name" == "$root_pk" ]] && continue
      [[ "$pkname" == "$root_pk" ]] && continue
    fi

    case "$mount" in
      /|/boot|/boot/*|/home|/home/*|/var|/var/*|/usr|/usr/*|/snap|/snap/*) continue ;;
    esac

    if [[ "$type" == "disk" ]]; then
      kids="$(lsblk -nr -o NAME "/dev/$name" 2>/dev/null | wc -l | tr -d ' ')"
      [[ "$kids" -gt 1 ]] && continue
    fi

    CANDIDATES+=("${name}|/dev/${name}|${size}|${type}|${fstype}|${mount}|${model}|${tran}")
  done < <(lsblk -P -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL,TRAN,PKNAME)

  if [[ ${#CANDIDATES[@]} -eq 0 ]]; then
    return 1
  fi

  local i=1 entry
  for entry in "${CANDIDATES[@]}"; do
    IFS='|' read -r name _devpath size type fstype mount model tran <<<"$entry"
    printf '%d) %-14s %-10s %-6s fstype=%-7s mount=%-18s %s %s\n' \
      "$i" "/dev/$name" "$size" "$type" \
      "${fstype:--}" "${mount:-(none)}" \
      "${model:+model=$model}" "${tran:+tran=$tran}"
    i=$((i + 1))
  done
}

# Sets FORMAT_MODE to "keep" or "format"
choose_format_mode() {
  local path="$1" fstype="$2"
  info ""
  info "=== Format choice ==="
  if [[ -n "$fstype" ]]; then
    info "Detected filesystem on $path: $fstype"
    info "  1) Keep existing files — receipts go in fixed folder '$RECEIPTS_SUBDIR/'"
    info "  2) Format / erase — dedicated empty receipts disk"
  else
    info "$path has no filesystem yet."
    info "  1) Keep / do not format  (not possible until a filesystem exists)"
    info "  2) Format as ext4        (erases device)"
  fi
  local choice
  choice="$(ask "Choose 1 or 2" "1")"
  case "$choice" in
    1) FORMAT_MODE="keep" ;;
    2) FORMAT_MODE="format" ;;
    *) die "invalid format choice" ;;
  esac

  if [[ "$FORMAT_MODE" == "keep" && -z "$fstype" ]]; then
    die "cannot keep files: $path has no filesystem. Choose option 2 to format."
  fi
}

do_format() {
  local path="$1" typed
  info ""
  info "FORMAT will erase ALL data on $path."
  typed="$(ask "Type YES to confirm destructive format of $path")"
  [[ "$typed" == "YES" ]] || die "format not confirmed"
  command -v mkfs.ext4 >/dev/null 2>&1 || die "mkfs.ext4 not found (install e2fsprogs)"
  if findmnt -n -S "$path" >/dev/null 2>&1; then
    die "$path is currently mounted — unmount it before formatting"
  fi
  mkfs.ext4 -L tahmeed-receipts -F "$path"
  info "Formatted $path as ext4."
}

uuid_of() {
  local path="$1" uuid
  udevadm settle 2>/dev/null || true
  uuid="$(device_uuid "$path")"
  if [[ -z "$uuid" ]]; then
    blkid -g >/dev/null 2>&1 || true
    uuid="$(device_uuid "$path")"
  fi
  [[ -n "$uuid" ]] || die "could not read UUID for $path"
  printf '%s' "$uuid"
}

fstab_options_for() {
  local fstype="$1"
  case "$fstype" in
    ext2|ext3|ext4|xfs|btrfs)
      printf 'defaults,nofail,x-systemd.device-timeout=10s'
      ;;
    ntfs|ntfs3)
      printf 'defaults,nofail,uid=%s,gid=%s,x-systemd.device-timeout=10s' \
        "$CONTAINER_UID" "$CONTAINER_GID"
      ;;
    vfat|exfat)
      printf 'defaults,nofail,uid=%s,gid=%s,umask=027,x-systemd.device-timeout=10s' \
        "$CONTAINER_UID" "$CONTAINER_GID"
      ;;
    *)
      printf 'defaults,nofail,x-systemd.device-timeout=10s'
      ;;
  esac
}

# Resolve where this device is already mounted (if anywhere).
current_mount_of() {
  findmnt -n -o TARGET -S "$1" 2>/dev/null | head -n1 || true
}

ensure_mount_point_dir() {
  local mp="$1"
  if [[ -e "$mp" && ! -d "$mp" ]]; then
    die "$mp exists and is not a directory"
  fi
  if [[ ! -d "$mp" ]]; then
    install -d -m 0755 "$mp"
  fi
}

# If mp is already a mount of a *different* device, stop. Same device is OK.
assert_mount_point_compatible() {
  local mp="$1" path="$2"
  if ! findmnt -n "$mp" >/dev/null 2>&1; then
    return 0
  fi
  local src
  src="$(findmnt -n -o SOURCE "$mp")"
  if [[ "$src" == "$path" ]]; then
    return 0
  fi
  # Compare UUIDs when SOURCE is like /dev/sdb4 vs UUID=
  local want have
  want="$(device_uuid "$path")"
  have="$(findmnt -n -o UUID "$mp" 2>/dev/null || true)"
  if [[ -n "$want" && -n "$have" && "$want" == "$have" ]]; then
    return 0
  fi
  die "$mp is already mounted from $src — unmount it first to avoid conflict"
}

write_fstab() {
  local uuid="$1" mp="$2" fstype="$3"
  local opts line tmp
  opts="$(fstab_options_for "$fstype")"
  # Prefer ntfs-3g helper name in fstab when available
  if [[ "$fstype" == "ntfs" ]] && command -v mount.ntfs-3g >/dev/null 2>&1; then
    fstype="ntfs-3g"
  fi
  line="UUID=${uuid} ${mp} ${fstype} ${opts} 0 2"
  tmp="$(mktemp)"
  [[ -f /etc/fstab ]] || die "/etc/fstab missing"

  awk -v mp="$mp" -v comment="$FSTAB_COMMENT" '
    $0 == comment { skip=1; next }
    skip == 1 { skip=0; next }
    $1 ~ /^#/ { print; next }
    NF >= 2 && $2 == mp { next }
    { print }
  ' /etc/fstab >"$tmp"

  {
    cat "$tmp"
    printf '%s\n%s\n' "$FSTAB_COMMENT" "$line"
  } >/etc/fstab
  rm -f "$tmp"
  info "Updated /etc/fstab for $mp (UUID=$uuid, type=$fstype, nofail)."
}

ensure_receipts_dir() {
  local dir="$1"
  local existed=0
  if [[ -d "$dir" ]]; then
    existed=1
    info "Found existing receipts folder: $dir (resuming previous storage)."
  else
    info "Creating receipts folder: $dir"
  fi
  install -d -o "$CONTAINER_UID" -g "$CONTAINER_GID" -m 0750 "$dir"
  # Only chown the receipts root itself — never recurse into existing files.
  chown "$CONTAINER_UID:$CONTAINER_GID" "$dir"
  chmod 0750 "$dir"
  if [[ "$existed" -eq 1 ]]; then
    local n
    n="$(find "$dir" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l | tr -d ' ')"
    info "Receipts folder ready (${n} top-level entries kept)."
  else
    info "Receipts folder ready (${CONTAINER_UID}:${CONTAINER_GID} mode 0750)."
  fi
}

write_compose_host_path() {
  local host_path="$1"
  local env_file="$ROOT/.env" tmp

  if [[ "$host_path" == "$DEFAULT_MOUNT_DEDICATED" ]]; then
    if [[ -f "$env_file" ]] && grep -q '^ATTACHMENTS_HOST_PATH=' "$env_file"; then
      tmp="$(mktemp)"
      grep -v '^ATTACHMENTS_HOST_PATH=' "$env_file" >"$tmp" || true
      if [[ -s "$tmp" ]]; then
        mv "$tmp" "$env_file"
        chmod 0600 "$env_file"
      else
        rm -f "$tmp" "$env_file"
      fi
      info "Removed ATTACHMENTS_HOST_PATH override from .env (using default $DEFAULT_MOUNT_DEDICATED)."
    else
      info "Host path is default; no Compose .env override needed."
    fi
    return 0
  fi

  if [[ -f "$env_file" ]]; then
    tmp="$(mktemp)"
    grep -v '^ATTACHMENTS_HOST_PATH=' "$env_file" >"$tmp" || true
    printf 'ATTACHMENTS_HOST_PATH=%s\n' "$host_path" >>"$tmp"
    mv "$tmp" "$env_file"
  else
    printf 'ATTACHMENTS_HOST_PATH=%s\n' "$host_path" >"$env_file"
  fi
  chmod 0600 "$env_file"
  info "Wrote ATTACHMENTS_HOST_PATH=$host_path to $env_file"
  info "Keep ATTACHMENTS_DIRECTORY=/mnt/tahmeed-receipts inside tahmeed-api.env."
}

maybe_restart_compose() {
  if ! command -v docker >/dev/null 2>&1; then
    info "Docker not found; skip container restart."
    return 0
  fi
  if ! docker compose ps --status running 2>/dev/null | grep -qE 'api|scheduler'; then
    info "API/scheduler containers are not running; mount will apply on next deploy/up."
    return 0
  fi
  if confirm_yes "Restart api + scheduler so they remount the receipt path?"; then
    local tag
    tag="$(cat .deployed-tag 2>/dev/null || true)"
    if [[ -n "$tag" ]]; then
      export TAHMEED_IMAGE_TAG="$tag"
    fi
    docker compose up -d --no-build --force-recreate api scheduler
    info "Recreated api and scheduler with the current bind mount."
  else
    info "Skipped restart. Later: sudo scripts/deploy.sh <TAG>"
  fi
}

main() {
  need_root
  need_tty
  need_cmds

  info "Tahmeed receipt disk attach"
  info "Paths are automatic — you only pick the disk and keep/format."
  info "Repo deploy dir: $ROOT"
  current_receipt_status

  info ""
  info "=== Available disks / partitions (OS disk excluded) ==="
  if ! list_candidates; then
    die "no candidate disks found. Plug in / detect the HDD, then re-run."
  fi

  info ""
  local choice idx entry
  choice="$(ask "Select disk number to use for receipts")"
  [[ "$choice" =~ ^[0-9]+$ ]] || die "invalid selection"
  idx=$((choice - 1))
  [[ "$idx" -ge 0 && "$idx" -lt ${#CANDIDATES[@]} ]] || die "selection out of range"

  entry="${CANDIDATES[$idx]}"
  local name path size type fstype mount model tran
  IFS='|' read -r name path size type fstype mount model tran <<<"$entry"
  if [[ -z "$fstype" ]]; then
    fstype="$(device_fstype "$path")"
  fi

  FORMAT_MODE=""
  choose_format_mode "$path" "$fstype"

  # Fixed paths — never prompt for mount point or subdirectory name.
  local mp receipts_host_path already_mounted=0
  local existing_mp
  existing_mp="$(current_mount_of "$path")"

  if [[ "$FORMAT_MODE" == "format" ]]; then
    if [[ -n "$existing_mp" ]]; then
      die "$path is mounted at $existing_mp — unmount it before formatting"
    fi
    mp="$DEFAULT_MOUNT_DEDICATED"
    receipts_host_path="$mp"
  else
    if [[ -n "$existing_mp" ]]; then
      # Remount / reconnect: reuse wherever this disk already is.
      mp="$existing_mp"
      already_mounted=1
      info "Disk already mounted at $mp — will reuse it and ensure '$RECEIPTS_SUBDIR/' exists."
    else
      mp="$DEFAULT_MOUNT_SHARED"
    fi
    receipts_host_path="${mp%/}/${RECEIPTS_SUBDIR}"
  fi

  info ""
  info "Plan (auto paths):"
  info "  device:        $path ($size, $type${fstype:+, $fstype})"
  info "  format:        $FORMAT_MODE"
  if [[ "$already_mounted" -eq 1 ]]; then
    info "  disk mount:    $mp (already mounted — no conflict)"
  else
    info "  disk mount:    $mp"
  fi
  info "  receipts path: $receipts_host_path"
  info "  container:     → /mnt/tahmeed-receipts"
  if [[ "$FORMAT_MODE" == "keep" ]]; then
    info "  note:          keeps other files on the disk; only manages $RECEIPTS_SUBDIR/"
  else
    info "  note:          erases device; mount root becomes the receipts root"
  fi
  if [[ "$receipts_host_path" != "$DEFAULT_MOUNT_DEDICATED" ]]; then
    info "  compose:       ATTACHMENTS_HOST_PATH=$receipts_host_path"
  fi
  confirm_yes "Proceed with this plan?" || die "aborted"

  if [[ "$FORMAT_MODE" == "format" ]]; then
    do_format "$path"
    fstype="ext4"
  fi

  local uuid
  uuid="$(uuid_of "$path")"
  fstype="$(device_fstype "$path")"
  [[ -n "$fstype" ]] || die "no filesystem on $path after prepare step"

  if [[ "$already_mounted" -eq 0 ]]; then
    ensure_mount_point_dir "$mp"
    assert_mount_point_compatible "$mp" "$path"
    if findmnt -n "$mp" >/dev/null 2>&1; then
      info "Mount point $mp already has this disk — skipping remount."
    else
      write_fstab "$uuid" "$mp" "$fstype"
      # ntfs often needs ntfs-3g
      if [[ "${fstype,,}" == "ntfs" ]] && ! mount -t ntfs-3g "$path" "$mp" 2>/dev/null; then
        mount "$mp" || die "mount failed — for NTFS install ntfs-3g, then re-run"
      elif ! mount "$mp" 2>/dev/null; then
        # fstab may use UUID; fall back to explicit device mount
        mount "$path" "$mp" || die "mount failed — check dmesg / journalctl -xe"
      fi
    fi
  else
    # Still record fstab for reboot if this mount isn't already persisted for our path
    if [[ "$mp" == "$DEFAULT_MOUNT_SHARED" || "$mp" == "$DEFAULT_MOUNT_DEDICATED" ]]; then
      write_fstab "$uuid" "$mp" "$fstype"
    else
      info "Reusing existing mount at $mp (left fstab alone for non-standard path)."
    fi
  fi

  ensure_receipts_dir "$receipts_host_path"
  write_compose_host_path "$receipts_host_path"

  info ""
  info "Ready:"
  findmnt -n -o SOURCE,FSTYPE,TARGET "$mp" || true
  df -h "$mp" | awk 'NR==1 || NR==2'
  info "Receipts host path: $receipts_host_path"

  maybe_restart_compose

  info ""
  info "Done. Verify with:"
  info "  findmnt $mp"
  info "  ls -la $receipts_host_path"
  info "  curl -fsS http://127.0.0.1:8000/health/ready"
}

main "$@"
