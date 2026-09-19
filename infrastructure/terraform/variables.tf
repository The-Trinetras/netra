variable "aws_region" {
  type = string
}
variable "project" {
  type    = string
  default = "netra"
}
variable "environment" {
  type    = string
  default = "dev"
}
variable "vpc_cidr" {
  type    = string
  default = "10.42.0.0/16"
}
variable "availability_zones" {
  type = list(string)
}
variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}
variable "db_name" {
  type    = string
  default = "netra"
}
variable "db_master_username" {
  type    = string
  default = "netra_admin"
}
variable "db_application_username" {
  type    = string
  default = "netra_app"

  validation {
    condition     = can(regex("^[a-z_][a-z0-9_]{0,62}$", var.db_application_username))
    error_message = "db_application_username must be a valid lowercase PostgreSQL role name."
  }
}
variable "pgbouncer_instance_type" {
  type    = string
  default = "t4g.micro"
}
variable "budget_amount_usd" {
  type    = number
  default = null
}
variable "budget_email" {
  type    = string
  default = null
}
variable "enable_prometheus_gpu" {
  type    = bool
  default = false
}
variable "prometheus_instance_type" {
  type    = string
  default = "g5.xlarge"
}
variable "prometheus_ami_id" {
  type    = string
  default = null

  validation {
    condition     = var.prometheus_ami_id == null || can(regex("^ami-[0-9a-f]+$", var.prometheus_ami_id))
    error_message = "prometheus_ami_id must be null or a valid explicit AMI ID."
  }
}

variable "db_backup_retention_days" {
  description = "RDS automated backup retention (D-BACKUP). 0 disables backups."
  type        = number
  default     = 7

  validation {
    condition     = var.db_backup_retention_days >= 1 && var.db_backup_retention_days <= 35
    error_message = "db_backup_retention_days must be between 1 and 35 so student data is backed up."
  }
}

variable "app_instance_type" {
  description = "Application host size for the API, worker and nginx (D-HOST). No default: choosing it is a cost decision."
  type        = string
}

variable "app_root_volume_gb" {
  description = "Encrypted root volume for images, containers and logs on the application host."
  type        = number
  default     = 30
}

variable "app_ingress_cidrs" {
  description = "Client networks allowed to reach nginx on 80/443."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}
