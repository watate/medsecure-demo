output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.app.id
}

output "elastic_ip" {
  description = "Elastic IP address (use this for DNS)"
  value       = aws_eip.app.public_ip
}

output "s3_bucket_name" {
  description = "S3 bucket name for database backups"
  value       = aws_s3_bucket.backups.id
}
