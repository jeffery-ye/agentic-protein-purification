#!/usr/bin/env bash
# Mounts the data volume (#24) at /data before the stack starts. Terraform
# attaches the volume only after the instance is running, so this waits for the
# device. It formats the device only when it holds no filesystem at all, so a
# replacement instance picks up the existing database.
set -euo pipefail

: "${DATA_DEVICE:?DATA_DEVICE is set in the systemd unit}"
APP_UID=10001 # the app user in the Dockerfile

for _ in $(seq 1 180); do
  [ -b "$DATA_DEVICE" ] && break
  sleep 5
done
[ -b "$DATA_DEVICE" ] || { echo "$DATA_DEVICE never appeared" >&2; exit 1; }

# blkid -p exits 2 only when the probe finds nothing. Any other failure stops
# here rather than risk formatting a volume that holds data.
rc=0
blkid -p "$DATA_DEVICE" >/dev/null || rc=$?
if [ "$rc" = 2 ]; then
  echo "Formatting the empty data volume $DATA_DEVICE"
  mkfs.ext4 -q -L ppr-data "$DATA_DEVICE"
elif [ "$rc" != 0 ]; then
  echo "blkid failed on $DATA_DEVICE (exit $rc); not mounting" >&2
  exit 1
fi

mkdir -p /data
mountpoint -q /data || mount -o noatime "$DATA_DEVICE" /data
chown "$APP_UID:$APP_UID" /data
# Caddy's certificates and keys, so a replacement instance reuses them instead
# of spending one of Let's Encrypt's 5 a week. Root-only: the app sees /data too.
install -d -m 0700 -o root -g root /data/caddy
