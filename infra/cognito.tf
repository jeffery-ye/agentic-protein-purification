# --- Sign-in ----------------------------------------------------------------
# The app runs the OIDC flow itself (agent_engine/auth.py) against Cognito's
# managed login pages on the free prefix domain. Accounts are admin-created;
# the runbook covers users and the shared reviewer account.

resource "aws_cognito_user_pool" "users" {
  name                = "ppr-users"
  deletion_protection = "ACTIVE"

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  username_configuration {
    case_sensitive = false
  }

  auto_verified_attributes = ["email"]

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  # Length over composition rules (NIST SP 800-63B).
  password_policy {
    minimum_length                   = 12
    require_lowercase                = false
    require_uppercase                = false
    require_numbers                  = false
    require_symbols                  = false
    temporary_password_validity_days = 7
  }
}

resource "aws_cognito_user_pool_domain" "users" {
  domain                = var.cognito_domain_prefix
  user_pool_id          = aws_cognito_user_pool.users.id
  managed_login_version = 2
}

# A public client: Authlib runs PKCE, so there's no client secret to keep out
# of Terraform state. localhost lets the real flow be tried with APP_ENV=deploy.
resource "aws_cognito_user_pool_client" "app" {
  name         = "ppr-app"
  user_pool_id = aws_cognito_user_pool.users.id

  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  explicit_auth_flows                  = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true

  callback_urls = ["https://${var.domain}/auth/callback", "http://localhost:8000/auth/callback"]
  logout_urls   = ["https://${var.domain}", "http://localhost:8000"]
}

# Managed login v2 serves a client only once it has a style; this uses
# Cognito's default.
resource "aws_cognito_managed_login_branding" "app" {
  user_pool_id                = aws_cognito_user_pool.users.id
  client_id                   = aws_cognito_user_pool_client.app.id
  use_cognito_provided_values = true
}
