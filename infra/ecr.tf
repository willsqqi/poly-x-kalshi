resource "aws_ecr_repository" "etl" {
  name                 = "${local.name_prefix}-etl"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}
