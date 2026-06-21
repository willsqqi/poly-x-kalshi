variable "aws_region" {
  description = "AWS region for Phase 1 resources."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Name prefix for all resources."
  type        = string
  default     = "prediction-market"
}

variable "environment" {
  description = "Deployment environment name."
  type        = string
  default     = "dev"
}

variable "data_bucket_name" {
  description = "Optional explicit S3 data lake bucket name."
  type        = string
  default     = null
}

variable "athena_results_bucket_name" {
  description = "Optional explicit Athena results bucket name."
  type        = string
  default     = null
}

variable "batch_subnet_ids" {
  description = "Subnet IDs for AWS Batch Fargate jobs. Provide private or public subnets with outbound internet/NAT access."
  type        = list(string)
}

variable "batch_security_group_ids" {
  description = "Security group IDs for AWS Batch Fargate jobs."
  type        = list(string)
}

variable "image_tag" {
  description = "ECR image tag used by the Batch job definition."
  type        = string
  default     = "latest"
}

variable "batch_max_vcpus" {
  description = "Maximum vCPUs for the managed AWS Batch Fargate compute environment."
  type        = number
  default     = 16
}

variable "batch_job_vcpu" {
  description = "vCPU assigned to one ETL Batch job."
  type        = string
  default     = "2"
}

variable "batch_job_memory" {
  description = "Memory in MiB assigned to one ETL Batch job."
  type        = string
  default     = "8192"
}

variable "polygon_rpc_secret_value" {
  description = "Optional initial plain Polygon RPC URL. Leave empty and populate the secret later if preferred."
  type        = string
  default     = ""
  sensitive   = true
}
