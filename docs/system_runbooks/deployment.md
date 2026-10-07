# Runbook: AWS deployment

Optional. Nothing here runs unless you apply it: the app defaults to `dev` mode, CI only formats and validates the Terraform, and the deploy workflow runs only when dispatched by hand.

One EC2 `t4g.small` instance runs the app container and Caddy, which terminates TLS with Let's Encrypt. Terraform in [`infra/`](../../infra) owns every AWS resource, and the host's stack in [`deploy/`](../../deploy) reaches the instance through cloud-init. There is no load balancer or NAT gateway: the backend is one process anyway, and those would have more than doubled the fixed cost. Sign-in is Cognito's managed login, run by the app ([backend.md](backend.md#sign-in)).

**Before deploying your own copy,** set `domain`, `github_repository` and `cognito_domain_prefix` in `infra/terraform.tfvars`, the site address in [`deploy/Caddyfile`](../../deploy/Caddyfile), and `AWS_ROLE_ARN` and `SITE_URL` in [`deploy.yml`](../../.github/workflows/deploy.yml).

Every command below runs from the repository root in Git Bash with the `ppr-deploy` profile, unless it says otherwise. In Git Bash, prefix commands that take an absolute AWS path such as `/ppr/API_KEY` with `MSYS_NO_PATHCONV=1`, or Git Bash rewrites it into a Windows path.

## How the instance runs
- `ppr-data.service` runs [`ppr-data.sh`](../../deploy/ppr-data.sh): it waits for the data volume, formats it only if it has no filesystem, and mounts it at `/data`, owned by the app's UID 10001. The app container bind-mounts `/data` and keeps `jobs.sqlite3` there.
- `ppr.service` (systemd) requires `ppr-data.service`, runs [`ppr-start.sh`](../../deploy/ppr-start.sh), then `docker compose up`. A failed start retries every minute, up to 20 times an hour; after that, `systemctl reset-failed ppr && systemctl start ppr`.
- `docker.service` also waits for `ppr-data.service` (a drop-in), so containers left over from an unclean shutdown never start against an empty `/data`.
- `ppr-start.sh` reads every parameter under `/ppr/` into `app.env` (the app) and `.env` (the image). A value of `unset` means absent. With `IMAGE_TAG=unset`, only Caddy starts. If Parameter Store is unreachable it reuses the last env files, and if ECR is, the image already on the host.
- If `ppr-backup.service` fails, `ppr-alert@.service` emails the alert topic.
- Parameters are read at each start, so a changed parameter takes effect on the next deploy or restart. No Terraform change is needed.
- Changing `deploy/*` or the cloud-init template **replaces the instance**. The new instance mounts the data volume and starts the last deployed image on its own, with the same jobs and the same certificate, which Caddy keeps in `/data/caddy`. Only a new or wiped volume requests a certificate, and Let's Encrypt allows 5 a week per name; uncomment the staging CA in the `Caddyfile` while iterating on that.
- Caddy also sets the browser security headers (HSTS, CSP, `nosniff`, no framing). The CSP allows same-origin scripts only, so a frontend change that loads a script, font or image from another origin needs the `Caddyfile` updated too.
- A new instance gets no CPU launch credits, so BLAST can hit its 30-second timeout for the first few hours.

## First-time setup
1. **IAM bootstrap (owner, admin credentials).** Review [`ppr-deploy-policy-infra.json`](../../infra/bootstrap/ppr-deploy-policy-infra.json), [`ppr-deploy-policy-services.json`](../../infra/bootstrap/ppr-deploy-policy-services.json) and [`ppr-deploy-policy-data-auth.json`](../../infra/bootstrap/ppr-deploy-policy-data-auth.json) (the data volume, its snapshots and Cognito), then run:
   ```bash
   ADMIN_PROFILE=<admin-profile> bash infra/bootstrap/bootstrap.sh
   ```
   From PowerShell, call Git Bash explicitly, because plain `bash` there is WSL's:
   ```powershell
   $env:ADMIN_PROFILE = "<admin-profile>"; & "C:\Program Files\Git\bin\bash.exe" infra/bootstrap/bootstrap.sh
   ```
   `aws login --profile <admin-profile>` gives the admin profile short-lived credentials. Run `aws logout --profile <admin-profile>` afterwards.

   The script creates the `ppr-deploy` user and writes its key into the local `ppr-deploy` profile without printing it. It's safe to re-run: a re-run attaches any policy file added since and keeps the existing key. It never changes a policy that already exists, so a change to one needs a new policy version from the admin profile. `ppr-deploy` can create `ppr-*` roles, so treat that key as admin-equivalent.
2. **State bucket.**
   ```bash
   ACCT=$(aws sts get-caller-identity --query Account --output text --profile ppr-deploy)
   aws s3api create-bucket --bucket ppr-tfstate-$ACCT --profile ppr-deploy
   aws s3api put-bucket-versioning --bucket ppr-tfstate-$ACCT --versioning-configuration Status=Enabled --profile ppr-deploy
   aws s3api put-bucket-encryption --bucket ppr-tfstate-$ACCT --profile ppr-deploy \
     --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
   aws s3api put-public-access-block --bucket ppr-tfstate-$ACCT --profile ppr-deploy \
     --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
   ```
3. **Variables.** Copy `infra/terraform.tfvars.example` to `infra/terraform.tfvars` (gitignored) and fill it in. Pin the AMI with the `describe-images` command in the example file.
4. **Apply.**
   ```bash
   cd infra
   terraform init -backend-config="bucket=ppr-tfstate-$ACCT"
   terraform apply
   terraform plan   # must report no changes
   ```
5. **Manual steps.** Enter the `name_servers` output as NS records for your subdomain at your DNS registrar, and confirm the SNS subscription email. The uptime alarm stays in alarm until the first deploy serves `/health`.

CI runs `terraform fmt -check` and `terraform validate` on every pull request; `plan` and `apply` stay local, since they need `ppr-deploy`.

`terraform output`, run from `infra/`, prints the instance ID, the ECR URL, the deploy role ARN and the name servers used below.

## Deploying and rolling back
Deploys are manual. Merging doesn't deploy, and PR branches can't deploy.
```bash
gh workflow run deploy.yml --ref main                  # the head of main
gh workflow run deploy.yml --ref main -f sha=<commit>  # a specific commit on main
gh run watch
```
The [`deploy`](../../.github/workflows/deploy.yml) workflow assumes `ppr-github-deploy` through OIDC. It builds the image for the commit on an ARM runner, or reuses it if ECR already has that tag, and pushes it as `ppr-app:<sha>`. Then it pulls the image on the instance, writes the SHA to `/ppr/IMAGE_TAG`, restarts `ppr.service`, and polls `/health` for up to 3 minutes. Only one deploy runs at a time.

**Rollback:** dispatch the workflow with the previous good SHA, which is in the `deploy <sha>` comment of the earlier SSM commands or the earlier workflow runs. ECR keeps the last 5 images plus the deployed one, which the workflow tags `live`. An older SHA is rebuilt, which takes longer but works the same way.

## Runtime configuration
Parameters live under `/ppr/`. Terraform sets initial values only and ignores later changes.

| Parameter | Type | Set by |
|---|---|---|
| `ENTREZ_EMAIL` | String | `terraform.tfvars` |
| `LLM_PROVIDER`, `LLM_MODEL` | String | CLI |
| `DAILY_JOB_CAP` | String | CLI. Required: the app won't start without it |
| `MAX_ACTIVE_JOBS` | String | Optional, CLI. Unset means 2 |
| `IMAGE_TAG` | String | The deploy workflow |
| `API_KEY` | SecureString | CLI. Placeholder `unset` |
| `AUTH_ENABLED`, `COGNITO_DOMAIN`, `COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID`, `REVIEWER_USERNAME` | String | Terraform, which keeps them in step with the pool. The app won't start without `AUTH_ENABLED` |
| `SESSION_SECRET_KEY` | SecureString | Terraform generates it once. Bump `value_wo_version` in `ssm.tf` to rotate it, which signs everyone out |

The secrets use write-only attributes, so their values never enter Terraform state. To change a value:
```bash
aws ssm put-parameter --name /ppr/LLM_MODEL --value <model> --overwrite --profile ppr-deploy
# secrets: add --type SecureString; read the value from a file so it stays out of shell history, then delete the file
aws ssm put-parameter --name /ppr/API_KEY --type SecureString --value file://key.txt --overwrite --profile ppr-deploy
```
Then re-run the deploy workflow, or restart the stack:
```bash
aws ssm send-command --instance-ids <instance_id> --document-name AWS-RunShellScript \
  --parameters 'commands=["systemctl restart ppr"]' --profile ppr-deploy
```

### User accounts
Sign-in is Cognito's, with admin-created accounts only. `terraform output cognito_user_pool_id` gives `<pool>`. Passwords need 12 characters or more.

A user, who gets an email with a temporary password and sets their own at first sign-in:
```bash
aws cognito-idp admin-create-user --user-pool-id <pool> --username <name>   --user-attributes Name=email,Value=<email> Name=email_verified,Value=true --profile ppr-deploy
```
A shared account (for example, one handed to a group of evaluators) uses an email the owner controls, so a reset request reaches the owner, and a permanent password, so there's no forced change. Its profile page hides the change-password link (`REVIEWER_USERNAME`).
```bash
aws cognito-idp admin-create-user --user-pool-id <pool> --username reviewer --message-action SUPPRESS   --user-attributes Name=email,Value=<owner-email> Name=email_verified,Value=true --profile ppr-deploy
uv run python -c "import secrets; print(secrets.token_urlsafe(24), end='')" > pw.txt
aws cognito-idp admin-set-user-password --user-pool-id <pool> --username reviewer --password file://pw.txt --permanent --profile ppr-deploy
```
Store the password, then delete `pw.txt`. Rotate it the same way.

Disable a user with `admin-disable-user`. Their session cookie keeps working for up to 7 days; rotating `SESSION_SECRET_KEY` ends it at once.

### LLM route
Switching routes or models takes only these parameters and a redeploy, with no code or Terraform change.
- **Keyed provider.** Set `LLM_PROVIDER` (`gemini`, `openai` or `anthropic`), `LLM_MODEL` (required; there is no default model), and the key in `/ppr/API_KEY`. The key exists only in Parameter Store and in the container's environment. While `API_KEY` is `unset`, jobs fail at synthesis with `API_KEY environment variable not set`.
- **Bedrock** (`LLM_PROVIDER=bedrock`). Signed with the instance role, so there's no key. It needs two changes first: Bedrock permissions on the instance role, and model access enabled in the Bedrock console, without which every Converse call returns `Operation not allowed`.

## Shell access and logs
There is no SSH. Sessions need the [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) locally.
```bash
aws ssm start-session --target <instance_id> --profile ppr-deploy
```
On the instance, the stack is in `/opt/ppr` (`sudo docker compose ps`, `sudo docker compose logs caddy`), and `journalctl -u ppr` shows the start script.

The app container logs to CloudWatch, with 14-day retention:
```bash
aws logs tail /ppr/app --follow --profile ppr-deploy
```

## Restoring the job database
DLM snapshots the data volume daily at 08:00 UTC and keeps 7. `ppr-backup.timer` writes a consistent copy, `jobs.backup.sqlite3`, 30 minutes before, so restore from that file rather than the live one. The restore copies the file back through a temporary volume, so Terraform's volume and state don't change.

1. Find the snapshot, then create a volume from it in the instance's zone:
   ```bash
   aws ec2 describe-snapshots --owner-ids self --filters Name=tag:Name,Values=ppr-data      --query 'sort_by(Snapshots,&StartTime)[].[SnapshotId,StartTime]' --output text --profile ppr-deploy
   aws ec2 create-volume --snapshot-id <snap> --availability-zone us-east-1a --volume-type gp3      --tag-specifications 'ResourceType=volume,Tags=[{Key=Project,Value=ppr},{Key=Name,Value=ppr-restore}]' --profile ppr-deploy
   aws ec2 attach-volume --volume-id <restore-vol> --instance-id <instance_id> --device /dev/sdg --profile ppr-deploy
   ```
2. In an SSM session (`<serial>` is the volume ID without its hyphen):
   ```bash
   sudo mkdir -p /mnt/restore
   # noload: a snapshot of a mounted ext4 volume needs journal recovery, which plain ro refuses
   sudo mount -o ro,noload /dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_<serial> /mnt/restore
   # should be about 07:30 UTC on the snapshot's day; if older, the nightly copy was failing
   ls -l --time-style=full-iso /mnt/restore/jobs.backup.sqlite3
   sudo systemctl stop ppr
   sudo rm -f /data/jobs.sqlite3-wal /data/jobs.sqlite3-shm
   sudo cp /mnt/restore/jobs.backup.sqlite3 /data/jobs.sqlite3 && sudo chown 10001:10001 /data/jobs.sqlite3
   sudo systemctl start ppr
   sudo umount /mnt/restore
   ```
3. Detach and delete the temporary volume:
   ```bash
   aws ec2 detach-volume --volume-id <restore-vol> --profile ppr-deploy
   aws ec2 delete-volume --volume-id <restore-vol> --profile ppr-deploy
   ```

## Deactivating `ppr-deploy`
The `ppr-deploy` key is admin-equivalent, so keep it inactive between infrastructure changes. With admin credentials:
```bash
KEY=$(aws iam list-access-keys --user-name ppr-deploy --query 'AccessKeyMetadata[0].AccessKeyId' --output text --profile <admin-profile>)
aws iam update-access-key --user-name ppr-deploy --access-key-id $KEY --status Active --profile <admin-profile>
aws iam update-access-key --user-name ppr-deploy --access-key-id $KEY --status Inactive --profile <admin-profile>
```

## Teardown
1. `cd infra && terraform destroy` with `ppr-deploy`. This removes everything Terraform owns, including the ECR images, the hosted zone and the parameters. The data volume (`prevent_destroy`) and the user pool (`deletion_protection`) refuse deletion: remove those guards first, deliberately, since they hold users' jobs and accounts.
2. Remove the subdomain's NS records at your DNS registrar.
3. As the owner, with admin credentials: empty and delete the versioned `ppr-tfstate-<account-id>` bucket, delete the `ppr-deploy` access key and user, and delete the three `purification-rescue-deploy-*` policies.

## Cost
About $20 a month before LLM usage. The `ppr-monthly` budget (`monthly_budget_usd`, default $35) emails the alert address at 50%, 80% and 100% of actual spend and at 100% of forecast.
