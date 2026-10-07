data "aws_caller_identity" "current" {}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnet" "public" {
  vpc_id            = data.aws_vpc.default.id
  availability_zone = "${var.region}a"
  default_for_az    = true
}

locals {
  compose_version = "v5.5.1"
  compose_sha256  = "732e3a84c1a0f67256ce80bc2598a24546b10ca05f9faa97efceb1171ece2ef7"

  user_data = templatefile("${path.module}/templates/user-data.yaml.tftpl", {
    compose         = filebase64("${path.module}/../deploy/compose.yaml")
    caddyfile       = filebase64("${path.module}/../deploy/Caddyfile")
    start_script    = filebase64("${path.module}/../deploy/ppr-start.sh")
    systemd_unit    = base64encode(templatefile("${path.module}/../deploy/ppr.service", { ecr_repo = aws_ecr_repository.app.repository_url }))
    data_script     = filebase64("${path.module}/../deploy/ppr-data.sh")
    data_unit       = base64encode(templatefile("${path.module}/../deploy/ppr-data.service", { volume_serial = replace(aws_ebs_volume.data.id, "-", "") }))
    backup_script   = filebase64("${path.module}/../deploy/ppr-backup.sh")
    backup_unit     = filebase64("${path.module}/../deploy/ppr-backup.service")
    backup_timer    = filebase64("${path.module}/../deploy/ppr-backup.timer")
    alert_unit      = base64encode(templatefile("${path.module}/../deploy/ppr-alert@.service", { alert_topic_arn = aws_sns_topic.alerts.arn }))
    docker_dropin   = filebase64("${path.module}/../deploy/docker-wait-for-data.conf")
    compose_version = local.compose_version
    compose_sha256  = local.compose_sha256
  })
}

# --- Compute -----------------------------------------------------------------

resource "aws_security_group" "web" {
  name        = "ppr-web"
  description = "HTTP and HTTPS only. Shell access goes through SSM."
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "HTTP (ACME challenge and redirect)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Outbound for ECR, SSM, NCBI, RCSB, UniProt and the LLM provider"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "app" {
  ami                    = var.ami_id
  instance_type          = "t4g.small"
  subnet_id              = data.aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.web.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name
  monitoring             = false

  user_data                   = local.user_data
  user_data_replace_on_change = true

  # t4g defaults to unlimited mode, which bills for surplus CPU.
  credit_specification {
    cpu_credits = "standard"
  }

  # A hop limit of 2 lets containers on Docker's bridge network reach the role.
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  maintenance_options {
    auto_recovery = "default"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 12
    encrypted             = true
    delete_on_termination = true
  }

  # Tags the root volume at launch. The provider then reads volume_tags from
  # every attached volume, data volume included, so a later update would strip
  # the data volume's Backup tag and end the daily snapshots (#100).
  volume_tags = {
    Project = "ppr"
    Name    = "ppr-app"
  }

  tags = {
    Name = "ppr-app"
  }

  lifecycle {
    ignore_changes = [volume_tags]
  }
}

resource "aws_eip" "app" {
  domain   = "vpc"
  instance = aws_instance.app.id

  tags = {
    Name = "ppr-app"
  }
}

# --- Container registry and logs ---------------------------------------------

resource "aws_ecr_repository" "app" {
  name                 = "ppr-app"
  image_tag_mutability = "IMMUTABLE_WITH_EXCLUSION"
  force_delete         = true

  # SHA tags stay immutable. The deploy workflow moves "live" to the deployed
  # image, which the lifecycle policy below never expires (#100).
  image_tag_mutability_exclusion_filter {
    filter      = "live"
    filter_type = "WILDCARD"
  }

  # Basic scanning: free, and findings show in the ECR console per image.
  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    # An image matched by a rule is never expired by a lower-priority one, so
    # the deployed image survives rollbacks and undeployed pushes.
    rules = [
      {
        rulePriority = 1
        description  = "Keep the deployed image"
        selection = {
          tagStatus      = "tagged"
          tagPatternList = ["live"]
          countType      = "imageCountMoreThan"
          countNumber    = 1
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep the last 5 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 5
        }
        action = { type = "expire" }
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ppr/app"
  retention_in_days = 14
}
