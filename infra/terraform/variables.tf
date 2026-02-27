variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "ap-southeast-1"
}

variable "aws_profile" {
  description = "AWS CLI profile name"
  type        = string
  default     = "watate"
}

variable "project" {
  description = "Project name used for resource naming"
  type        = string
  default     = "medsecure"
}

variable "environment" {
  description = "Environment (e.g. prod, staging)"
  type        = string
  default     = "prod"
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.small"
}

variable "ssh_public_key_path" {
  description = "Path to the SSH public key file for EC2 access"
  type        = string
}
