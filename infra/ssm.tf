# Runtime configuration under /ppr/. deploy/ppr-start.sh renders every
# parameter into the stack's env files each time ppr.service starts. The value
# "unset" means absent, because Parameter Store can't hold an empty string.
#
# Terraform only sets the initial values. Later changes go through the CLI (see
# docs/system_runbooks/deployment.md), so plain values ignore drift.

locals {
  plain_parameters = {
    ENTREZ_EMAIL  = var.entrez_email
    LLM_PROVIDER  = "unset" # the app's default provider (#33 sets the deploy route)
    LLM_MODEL     = "unset" # required by the app: set it with the CLI
    DAILY_JOB_CAP = "20"    # read by #26; retuned at launch (#41)
  }
}

resource "aws_ssm_parameter" "plain" {
  for_each = local.plain_parameters

  name  = "/ppr/${each.key}"
  type  = "String"
  value = each.value

  lifecycle {
    ignore_changes = [value]
  }
}

# Written by the deploy workflow (#23). "unset" starts Caddy alone.
resource "aws_ssm_parameter" "image_tag" {
  name  = "/ppr/IMAGE_TAG"
  type  = "String"
  value = "unset"

  lifecycle {
    ignore_changes = [value]
  }
}

# --- Secrets -----------------------------------------------------------------
# Write-only values never enter Terraform state, and a refresh doesn't read the
# real secret back (#25, #33). Terraform writes the placeholder once, on create
# or when the version is bumped; the owner sets the real value from the CLI.

# The provider key (#33). "unset" leaves API_KEY out of the app's environment.
resource "aws_ssm_parameter" "api_key" {
  name             = "/ppr/API_KEY"
  type             = "SecureString"
  value_wo         = "unset"
  value_wo_version = 1
}

# The key that signs the session cookie. Generated here and never
# stored anywhere else; bumping the version rotates it and signs everyone out.
ephemeral "random_password" "session_secret" {
  length  = 64
  special = false
}

resource "aws_ssm_parameter" "session_secret_key" {
  name             = "/ppr/SESSION_SECRET_KEY"
  type             = "SecureString"
  value_wo         = ephemeral.random_password.session_secret.result
  value_wo_version = 1
}

# --- Sign-in settings (#25) --------------------------------------------------
# Unlike the values above, these follow Terraform, so a replaced pool or
# client reaches the app on the next restart.

locals {
  auth_parameters = {
    AUTH_ENABLED         = "true"
    COGNITO_DOMAIN       = "${aws_cognito_user_pool_domain.users.domain}.auth.${var.region}.amazoncognito.com"
    COGNITO_USER_POOL_ID = aws_cognito_user_pool.users.id
    COGNITO_CLIENT_ID    = aws_cognito_user_pool_client.app.id
    REVIEWER_USERNAME    = var.reviewer_username
  }
}

resource "aws_ssm_parameter" "auth" {
  for_each = local.auth_parameters

  name  = "/ppr/${each.key}"
  type  = "String"
  value = each.value
}
