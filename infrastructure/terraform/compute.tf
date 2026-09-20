data "aws_iam_policy_document" "pgbouncer_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

data "aws_ami" "pgbouncer" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-arm64-server-*"]
  }

  filter {
    name   = "architecture"
    values = ["arm64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

data "aws_ssm_parameter" "prometheus_ami" {
  count = var.enable_prometheus_gpu && var.prometheus_ami_id == null ? 1 : 0
  name  = "/aws/service/deeplearning/ami/x86_64/base-with-single-cuda-ubuntu-24.04/latest/ami-id"
}

locals {
  prometheus_ami = var.prometheus_ami_id != null ? var.prometheus_ami_id : (
    var.enable_prometheus_gpu ? data.aws_ssm_parameter.prometheus_ami[0].value : null
  )
  prometheus_model_id       = "prometheus-eval/prometheus-7b-v2.0"
  prometheus_model_revision = "66ffb1fc20beebfb60a3964a957d9011723116c5"
  prometheus_vllm_image     = "vllm/vllm-openai:v0.29.0-cu129-ubuntu2404"
}
resource "aws_iam_role" "pgbouncer" {
  name               = "${local.name}-pgbouncer"
  assume_role_policy = data.aws_iam_policy_document.pgbouncer_assume.json
  tags               = local.tags
}
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.pgbouncer.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "database_bootstrap_secrets" {
  statement {
    sid       = "ReadApplicationDatabaseSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.database_application.arn]
  }
}

resource "aws_iam_role_policy" "database_bootstrap_secrets" {
  name   = "${local.name}-database-bootstrap-secrets"
  role   = aws_iam_role.pgbouncer.id
  policy = data.aws_iam_policy_document.database_bootstrap_secrets.json
}

resource "aws_iam_instance_profile" "pgbouncer" {
  name = "${local.name}-pgbouncer"
  role = aws_iam_role.pgbouncer.name
}
resource "aws_instance" "pgbouncer" {
  ami                    = data.aws_ami.pgbouncer.id
  instance_type          = var.pgbouncer_instance_type
  subnet_id              = aws_subnet.public[0].id
  vpc_security_group_ids = [aws_security_group.pgbouncer.id]
  iam_instance_profile   = aws_iam_instance_profile.pgbouncer.name
  metadata_options { http_tokens = "required" }
  user_data = templatefile("${path.module}/scripts/bootstrap_pgbouncer.sh.tftpl", {
    application_secret_arn = aws_secretsmanager_secret.database_application.arn
    aws_region             = var.aws_region
    database_host          = aws_db_instance.netra.address
    database_name          = var.db_name
  })
  user_data_replace_on_change = true
  tags                        = merge(local.tags, { Name = "${local.name}-pgbouncer" })

  # most_recent AMI lookups must not replace a running instance on every
  # plan after Canonical publishes a new image. Replace deliberately instead.
  lifecycle {
    ignore_changes = [ami]
  }
}

resource "aws_iam_role" "prometheus" {
  count              = var.enable_prometheus_gpu ? 1 : 0
  name               = "${local.name}-prometheus"
  assume_role_policy = data.aws_iam_policy_document.pgbouncer_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "prometheus_ssm" {
  count      = var.enable_prometheus_gpu ? 1 : 0
  role       = aws_iam_role.prometheus[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "prometheus" {
  count = var.enable_prometheus_gpu ? 1 : 0
  name  = "${local.name}-prometheus"
  role  = aws_iam_role.prometheus[0].name
}

resource "aws_instance" "prometheus" {
  count                       = var.enable_prometheus_gpu ? 1 : 0
  ami                         = local.prometheus_ami
  instance_type               = var.prometheus_instance_type
  subnet_id                   = aws_subnet.public[1].id
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.evaluator[0].id]
  iam_instance_profile        = aws_iam_instance_profile.prometheus[0].name
  metadata_options { http_tokens = "required" }
  user_data = templatefile("${path.module}/scripts/bootstrap_prometheus.sh.tftpl", {
    model_id       = local.prometheus_model_id
    model_revision = local.prometheus_model_revision
    vllm_image     = local.prometheus_vllm_image
  })
  user_data_replace_on_change = true

  root_block_device {
    encrypted   = true
    volume_size = 80
    volume_type = "gp3"
  }

  tags = merge(local.tags, { Name = "${local.name}-prometheus" })
}
