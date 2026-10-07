#!/usr/bin/env bash
# One-time IAM bootstrap for the AWS deployment (#13). The repo owner runs this
# once, with admin credentials, from the repository root in Git Bash:
#
#   ADMIN_PROFILE=<your-admin-profile> bash infra/bootstrap/bootstrap.sh
#
# From PowerShell, call Git Bash explicitly (plain `bash` there is WSL's):
#
#   $env:ADMIN_PROFILE = "<your-admin-profile>"
#   & "C:\Program Files\Git\bin\bash.exe" infra/bootstrap/bootstrap.sh
#
# It creates the ppr-deploy IAM user, attaches the three policies in this folder,
# and writes an access key straight into a local `ppr-deploy` CLI profile, so
# the secret is never printed. Everything after this runs as ppr-deploy.
#
# The policies keep Terraform on ppr-* resources, but ppr-deploy can create
# ppr-* roles and attach ppr-* policies, so it can make itself an admin role.
# Treat its access key as admin-equivalent, and deactivate it when infrastructure
# work pauses (docs/system_runbooks/deployment.md covers reactivating it).
set -euo pipefail

: "${ADMIN_PROFILE:?Set ADMIN_PROFILE to an AWS CLI profile with admin rights}"
USER_NAME=ppr-deploy
# `pwd -W` gives a C:/... path in Git Bash, which the Windows AWS CLI can read.
DIR="$(cd "$(dirname "$0")" && { pwd -W 2>/dev/null || pwd; })"
# The Windows AWS CLI ends lines with CRLF; strip the CR so captured ARNs and
# the key aren't corrupted.
admin() { aws --profile "$ADMIN_PROFILE" "$@" | tr -d '\r'; }

ACCOUNT_ID=$(admin sts get-caller-identity --query Account --output text)
echo "Account: $ACCOUNT_ID"

# Each step skips what already exists, so the script can be re-run after a failure.
if admin iam get-user --user-name "$USER_NAME" >/dev/null 2>&1; then
  echo "User $USER_NAME already exists"
else
  admin iam create-user --user-name "$USER_NAME" --tags Key=Project,Value=ppr >/dev/null
  echo "Created user $USER_NAME"
fi

# The policy names deliberately don't start with ppr-, so ppr-deploy can't edit them.
for part in infra services data-auth; do
  name="purification-rescue-deploy-$part"
  arn="arn:aws:iam::$ACCOUNT_ID:policy/$name"
  if admin iam get-policy --policy-arn "$arn" >/dev/null 2>&1; then
    echo "Policy $name already exists; not changing it"
  else
    admin iam create-policy --policy-name "$name" \
      --policy-document "file://$DIR/ppr-deploy-policy-$part.json" \
      --tags Key=Project,Value=ppr >/dev/null
  fi
  admin iam attach-user-policy --user-name "$USER_NAME" --policy-arn "$arn"
  echo "Attached $name"
done

keys=$(admin iam list-access-keys --user-name "$USER_NAME" --query 'length(AccessKeyMetadata)' --output text)
if [ "$keys" != 0 ]; then
  # A re-run to attach a new policy: nothing more to do if the local profile works.
  if aws --profile ppr-deploy sts get-caller-identity --query Arn --output text 2>/dev/null | grep -q "user/$USER_NAME"; then
    echo "The local ppr-deploy profile already holds a working key; done"
    exit 0
  fi
  echo "$USER_NAME already has an access key; not creating another." >&2
  echo "If the local ppr-deploy profile doesn't hold it, delete that key and re-run." >&2
  exit 1
fi
read -r KEY_ID SECRET < <(admin iam create-access-key --user-name "$USER_NAME" \
  --query 'AccessKey.[AccessKeyId,SecretAccessKey]' --output text)
aws configure set aws_access_key_id "$KEY_ID" --profile ppr-deploy
aws configure set aws_secret_access_key "$SECRET" --profile ppr-deploy
aws configure set region us-east-1 --profile ppr-deploy
unset SECRET
echo "Wrote the access key to the local ppr-deploy profile"

# New keys can take a few seconds to propagate.
sleep 10
aws --profile ppr-deploy sts get-caller-identity --query Arn --output text
