data "aws_iam_policy_document" "ec2_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ec2" {
  name               = "${var.project}-${var.environment}-ec2-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json

  tags = {
    Name = "${var.project}-${var.environment}-ec2-role"
  }
}

# S3 access for database backups
data "aws_iam_policy_document" "s3_access" {
  statement {
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket",
    ]

    resources = [
      aws_s3_bucket.backups.arn,
      "${aws_s3_bucket.backups.arn}/*",
    ]
  }
}

resource "aws_iam_role_policy" "s3_access" {
  name   = "${var.project}-${var.environment}-s3-access"
  role   = aws_iam_role.ec2.id
  policy = data.aws_iam_policy_document.s3_access.json
}

# SSM Parameter Store read access for secrets
data "aws_iam_policy_document" "ssm_access" {
  statement {
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:GetParametersByPath",
    ]

    resources = [
      "arn:aws:ssm:${var.aws_region}:*:parameter/${var.project}/${var.environment}/*",
    ]
  }

  statement {
    actions = [
      "ssm:DescribeParameters",
    ]

    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "ssm_access" {
  name   = "${var.project}-${var.environment}-ssm-access"
  role   = aws_iam_role.ec2.id
  policy = data.aws_iam_policy_document.ssm_access.json
}

# CloudWatch Logs access for Docker awslogs driver
data "aws_iam_policy_document" "cloudwatch_logs" {
  statement {
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = [
      "arn:aws:logs:${var.aws_region}:*:log-group:/${var.project}/${var.environment}/*",
      "arn:aws:logs:${var.aws_region}:*:log-group:/${var.project}/${var.environment}/*:log-stream:*",
    ]
  }
}

resource "aws_iam_role_policy" "cloudwatch_logs" {
  name   = "${var.project}-${var.environment}-cloudwatch-logs"
  role   = aws_iam_role.ec2.id
  policy = data.aws_iam_policy_document.cloudwatch_logs.json
}

resource "aws_iam_instance_profile" "ec2" {
  name = "${var.project}-${var.environment}-ec2-profile"
  role = aws_iam_role.ec2.name
}
