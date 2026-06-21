resource "aws_secretsmanager_secret" "polygon_rpc" {
  name        = "${local.name_prefix}/polygon-rpc-url"
  description = "Plain Polygon RPC URL used by historical Polymarket ETL jobs."
}

resource "aws_secretsmanager_secret_version" "polygon_rpc" {
  count         = var.polygon_rpc_secret_value == "" ? 0 : 1
  secret_id     = aws_secretsmanager_secret.polygon_rpc.id
  secret_string = var.polygon_rpc_secret_value
}
