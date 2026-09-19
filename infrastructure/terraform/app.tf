# D-HOST: one application host in the existing VPC running Compose (API,
# worker, nginx). Administration is through SSM only (no SSH, no key pair).
#
# Non-secret configuration the host needs at deploy time lives in SSM
# Parameter Store under /<project>-<environment>/app/, so replacing PgBouncer
# (which changes its private address) never replaces this host. Secrets stay
# in Secrets Manager: the database application secret, and one "environment"
# secret that Terraform creates EMPTY and an operator fills with the provider
# keys (see infrastructure/aws/RUNBOOK.md). No secret value is ever a
# Terraform input, output or state value.

locals {
  app_parameter_prefix = "/${local.name}/app"
}

resource "aws_secretsmanager_secret" "app_environment" {
  name                    = "${local.name}/app/environment"
  description             = "JSON object of NETRA_* provider settings for the application host; filled by an operator."
  recovery_window_in_days = 7
  tags                    = local.tags
}

resource "aws_ssm_parameter" "app_config" {
  for_each = {
    pgbouncer_host = aws_instance.pgbouncer.private_ip
    database_name  = var.db_name
    s3_bucket      = aws_s3_bucket.documents.bucket
    aws_region     = var.aws_region
  }
  name  = "${local.app_parameter_prefix}/${each.key}"
  type  = "String"
  value = each.value
  tags  = local.tags
}

data "aws_iam_policy_document" "app" {
  statement {
    sid       = "SourceDocuments"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.documents.arn}/*"]
  }
  statement {
    sid     = "ApplicationSecrets"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.database_application.arn,
      aws_secretsmanager_secret.app_environment.arn,
    ]
  }
  statement {
    sid       = "ApplicationConfiguration"
    actions   = ["ssm:GetParameter", "ssm:GetParametersByPath"]
    resources = ["arn:aws:ssm:${var.aws_region}:*:parameter${local.app_parameter_prefix}/*"]
  }
}

resource "aws_iam_role" "app" {
  name               = "${local.name}-app"
  assume_role_policy = data.aws_iam_policy_document.pgbouncer_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "app_ssm" {
  role       = aws_iam_role.app.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "app" {
  name   = "${local.name}-app"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.app.json
}

resource "aws_iam_instance_profile" "app" {
  name = "${local.name}-app"
  role = aws_iam_role.app.name
}

resource "aws_instance" "app" {
  ami                    = data.aws_ami.pgbouncer.id
  instance_type          = var.app_instance_type
  subnet_id              = aws_subnet.public[0].id
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile   = aws_iam_instance_profile.app.name
  metadata_options { http_tokens = "required" }
  user_data = templatefile("${path.module}/scripts/bootstrap_app.sh.tftpl", {
    aws_region             = var.aws_region
    parameter_prefix       = local.app_parameter_prefix
    database_secret_arn    = aws_secretsmanager_secret.database_application.arn
    environment_secret_arn = aws_secretsmanager_secret.app_environment.arn
  })
  user_data_replace_on_change = true

  root_block_device {
    encrypted   = true
    volume_size = var.app_root_volume_gb
    volume_type = "gp3"
  }

  lifecycle {
    ignore_changes = [ami]
  }

  tags = merge(local.tags, { Name = "${local.name}-app" })
}

# A stable public address for the TLS domain's DNS record.
resource "aws_eip" "app" {
  domain   = "vpc"
  instance = aws_instance.app.id
  tags     = merge(local.tags, { Name = "${local.name}-app" })
}
