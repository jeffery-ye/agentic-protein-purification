# --- Data volume (#24) -------------------------------------------------------
# The job database lives here, not on the root volume, so it survives instance
# replacement. deploy/ppr-data.sh mounts it at /data on every boot and formats
# it only if it has no filesystem.

resource "aws_ebs_volume" "data" {
  availability_zone = data.aws_subnet.public.availability_zone
  type              = "gp3"
  size              = 5
  encrypted         = true

  tags = {
    Name   = "ppr-data"
    Backup = "ppr-daily" # the DLM policy below targets this tag
  }

  lifecycle {
    prevent_destroy = true
  }
}

# Replacing the instance replaces this attachment. Stopping the old instance
# first means the volume is never detached under a running database.
resource "aws_volume_attachment" "data" {
  device_name                    = "/dev/sdf"
  volume_id                      = aws_ebs_volume.data.id
  instance_id                    = aws_instance.app.id
  stop_instance_before_detaching = true
}

# --- Daily snapshots ---------------------------------------------------------
# Taken at 08:00 UTC, half an hour after ppr-backup.timer writes a clean copy
# of the database, and kept for 7 days.

data "aws_iam_policy_document" "dlm_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["dlm.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "dlm" {
  name               = "ppr-dlm"
  assume_role_policy = data.aws_iam_policy_document.dlm_assume.json
}

# The volume-snapshot part of AWSDataLifecycleManagerServiceRole, inline so the
# ppr-deploy policy needs no new attachable managed policy.
data "aws_iam_policy_document" "dlm" {
  statement {
    sid = "CreateAndReadSnapshots"
    actions = [
      "ec2:CreateSnapshot",
      "ec2:CreateSnapshots",
      "ec2:DescribeInstances",
      "ec2:DescribeVolumes",
      "ec2:DescribeSnapshots",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "TagAndExpireSnapshots"
    actions   = ["ec2:CreateTags", "ec2:DeleteSnapshot"]
    resources = ["arn:aws:ec2:*::snapshot/*"]
  }
}

resource "aws_iam_role_policy" "dlm" {
  name   = "ppr-dlm-snapshots"
  role   = aws_iam_role.dlm.id
  policy = data.aws_iam_policy_document.dlm.json
}

resource "aws_dlm_lifecycle_policy" "data" {
  description        = "ppr-data daily snapshots kept 7 days"
  execution_role_arn = aws_iam_role.dlm.arn
  state              = "ENABLED"

  policy_details {
    resource_types = ["VOLUME"]
    target_tags = {
      Backup = "ppr-daily"
    }

    schedule {
      name = "daily"

      create_rule {
        interval      = 24
        interval_unit = "HOURS"
        times         = ["08:00"]
      }

      retain_rule {
        count = 7
      }

      # Copies Project=ppr, so ppr-deploy can delete a snapshot by hand.
      copy_tags = true
    }
  }
}
