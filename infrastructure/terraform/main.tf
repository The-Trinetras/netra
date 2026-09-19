locals {
  name = "${var.project}-${var.environment}"
  tags = { Project = var.project, Environment = var.environment, ManagedBy = "terraform" }
}

resource "aws_vpc" "netra" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = merge(local.tags, { Name = local.name })
}
resource "aws_internet_gateway" "netra" {
  vpc_id = aws_vpc.netra.id
  tags   = local.tags
}
resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.netra.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = true
  tags                    = merge(local.tags, { Name = "${local.name}-public-${count.index}" })
}
resource "aws_subnet" "private_db" {
  count             = 2
  vpc_id            = aws_vpc.netra.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + 4)
  availability_zone = var.availability_zones[count.index]
  tags              = merge(local.tags, { Name = "${local.name}-db-${count.index}" })
}
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.netra.id
  tags   = local.tags
}
resource "aws_route" "internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.netra.id
}
resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "pgbouncer" {
  name        = "${local.name}-pgbouncer"
  vpc_id      = aws_vpc.netra.id
  description = "PgBouncer has no public ingress; SSM only"
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = local.tags
}
resource "aws_security_group" "rds" {
  name        = "${local.name}-rds"
  vpc_id      = aws_vpc.netra.id
  description = "PostgreSQL from PgBouncer only"
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.pgbouncer.id]
  }
  tags = local.tags
}
resource "aws_security_group" "evaluator" {
  count  = var.enable_prometheus_gpu ? 1 : 0
  name   = "${local.name}-evaluator"
  vpc_id = aws_vpc.netra.id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = local.tags
}
