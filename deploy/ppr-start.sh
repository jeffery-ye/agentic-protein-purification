#!/usr/bin/env bash
# Renders the app's env file from every parameter under /ppr/, then pulls
# the image. Runs before each start of ppr.service, so a parameter change takes
# effect on the next deploy or restart with no Terraform or code change.
#
# A parameter whose value is "unset" is treated as absent, since Parameter
# Store can't hold an empty string. Values are written single-quoted, which
# Compose reads literally, so a "$" in a value isn't interpolated.
#
# A boot shouldn't depend on AWS answering (#100): if Parameter Store is
# unreachable, the env files from the last start are reused, and if ECR is, the
# image already on the host is. The deploy workflow checks that the new image
# was actually picked up.
set -euo pipefail
cd "${PPR_DIR:-/opt/ppr}"

: "${ECR_REPO:?ECR_REPO is set in the systemd unit}"
umask 077

if params=$(aws ssm get-parameters-by-path --region us-east-1 --path /ppr/ --recursive \
     --with-decryption --query 'Parameters[].[Name,Value]' --output text); then
  : > app.env.new
  tag=unset
  while IFS=$'\t' read -r name value; do
    [ -n "$name" ] || continue
    key=${name#/ppr/}
    [ "$value" = unset ] && continue
    case "$value" in *"'"*) echo "Skipping $name: single quotes aren't supported" >&2; continue ;; esac
    case "$key" in
      IMAGE_TAG) tag=$value ;;
      *) printf "%s='%s'\n" "$key" "$value" >> app.env.new ;;
    esac
  done <<< "$params"

  if [ "$tag" = unset ]; then
    echo "APP_IMAGE=unset" > .env.new
  else
    echo "APP_IMAGE=$ECR_REPO:$tag" > .env.new
  fi
  mv app.env.new app.env
  mv .env.new .env
elif [ -f app.env ] && [ -s .env ]; then
  echo "Parameter Store is unreachable; reusing the env files from the last start" >&2
else
  echo "Parameter Store is unreachable and there are no earlier env files" >&2
  exit 1
fi

image=$(sed -n 's/^APP_IMAGE=//p' .env)
if [ "$image" = unset ]; then
  # Nothing deployed yet: run Caddy alone so the certificate is issued.
  echo "No image deployed yet; starting Caddy only" >&2
  exit 0
fi
if ! docker compose pull --quiet app; then
  docker image inspect "$image" >/dev/null 2>&1 ||
    { echo "Pulling $image failed and it isn't on the host" >&2; exit 1; }
  echo "Pulling $image failed; starting the copy already on the host" >&2
fi
