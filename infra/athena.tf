resource "aws_athena_workgroup" "phase1" {
  name = "${local.name_prefix}-phase1"

  configuration {
    enforce_workgroup_configuration = true
    result_configuration {
      output_location = "s3://${aws_s3_bucket.athena_results.bucket}/results/"
    }
  }
}
