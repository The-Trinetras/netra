resource "aws_db_subnet_group" "netra" {
  name       = "${local.name}-db"
  subnet_ids = aws_subnet.private_db[*].id
  tags       = local.tags
}
resource "aws_db_instance" "netra" {
  identifier                  = "${local.name}-postgres"
  engine                      = "postgres"
  engine_version              = "17.11"
  instance_class              = var.db_instance_class
  allocated_storage           = 20
  storage_encrypted           = true
  db_name                     = var.db_name
  username                    = var.db_master_username
  manage_master_user_password = true
  db_subnet_group_name        = aws_db_subnet_group.netra.name
  vpc_security_group_ids      = [aws_security_group.rds.id]
  publicly_accessible         = false
  backup_retention_period     = var.db_backup_retention_days
  backup_window               = "20:00-21:00"
  maintenance_window          = "sun:21:30-sun:22:30"
  deletion_protection         = false
  skip_final_snapshot         = false
  final_snapshot_identifier   = "${local.name}-postgres-final"
  copy_tags_to_snapshot       = true
  multi_az                    = false
  tags                        = local.tags
}

# Terraform creates the secret container, while the PgBouncer bootstrap asks
# Secrets Manager to generate and store the password. The generated value is
# therefore absent from Terraform configuration, outputs, plans, and state.
resource "aws_secretsmanager_secret" "database_application" {
  name                    = "${local.name}/database/application"
  recovery_window_in_days = 7
  tags                    = local.tags
}
