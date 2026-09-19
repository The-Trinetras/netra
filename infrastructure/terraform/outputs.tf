output "document_bucket_name" { value = aws_s3_bucket.documents.bucket }
output "rds_endpoint" {
  value     = aws_db_instance.netra.address
  sensitive = true
}
output "pgbouncer_instance_id" { value = aws_instance.pgbouncer.id }
output "database_application_secret_arn" {
  description = "Secret identifier only; retrieve its value through an authorized AWS session."
  value       = aws_secretsmanager_secret.database_application.arn
}
output "database_master_secret_arn" {
  description = "RDS-managed master secret identifier for the one-time operator bootstrap."
  value       = aws_db_instance.netra.master_user_secret[0].secret_arn
}
output "prometheus_instance_id" { value = try(aws_instance.prometheus[0].id, null) }
output "prometheus_runtime" {
  description = "Non-secret evaluator runtime identity; null while GPU evaluation is disabled."
  value = var.enable_prometheus_gpu ? {
    instance_type = var.prometheus_instance_type
    model         = local.prometheus_model_id
    revision      = local.prometheus_model_revision
    image         = local.prometheus_vllm_image
    port          = 8000
  } : null
}
output "app_instance_id" { value = aws_instance.app.id }
output "app_public_ip" {
  description = "Stable address for the TLS domain's DNS A record."
  value       = aws_eip.app.public_ip
}
output "app_environment_secret_arn" {
  description = "Secret identifier only; an operator fills it with NETRA_* provider settings."
  value       = aws_secretsmanager_secret.app_environment.arn
}
output "app_parameter_prefix" { value = local.app_parameter_prefix }
