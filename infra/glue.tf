locals {
  dim_market_columns = [
    { name = "market_id", type = "string" },
    { name = "question", type = "string" },
    { name = "slug", type = "string" },
    { name = "category", type = "string" },
    { name = "status", type = "string" },
    { name = "open_time", type = "string" },
    { name = "close_time", type = "string" },
    { name = "resolution_time", type = "string" },
    { name = "condition_id", type = "string" },
    { name = "volume", type = "double" },
    { name = "liquidity", type = "double" },
    { name = "raw_payload_path", type = "string" },
    { name = "ingested_at", type = "string" }
  ]

  dim_outcome_columns = [
    { name = "outcome_id", type = "string" },
    { name = "market_id", type = "string" },
    { name = "outcome_name", type = "string" },
    { name = "outcome_index", type = "int" },
    { name = "token_id", type = "string" },
    { name = "side", type = "string" },
    { name = "ingested_at", type = "string" }
  ]

  fact_trades_columns = [
    { name = "trade_id", type = "string" },
    { name = "market_id", type = "string" },
    { name = "outcome_id", type = "string" },
    { name = "timestamp", type = "bigint" },
    { name = "hour", type = "int" },
    { name = "maker", type = "string" },
    { name = "taker", type = "string" },
    { name = "price", type = "double" },
    { name = "token_amount", type = "double" },
    { name = "usd_amount", type = "double" },
    { name = "maker_direction", type = "string" },
    { name = "taker_direction", type = "string" },
    { name = "transaction_hash", type = "string" },
    { name = "block_number", type = "bigint" },
    { name = "order_hash", type = "string" },
    { name = "fee_amount", type = "double" },
    { name = "source_type", type = "string" },
    { name = "ingested_at", type = "string" },
    { name = "raw_payload_path", type = "string" }
  ]

  fact_market_daily_columns = [
    { name = "market_id", type = "string" },
    { name = "daily_volume", type = "double" },
    { name = "daily_trade_count", type = "bigint" },
    { name = "unique_makers", type = "bigint" },
    { name = "unique_takers", type = "bigint" },
    { name = "avg_price", type = "double" },
    { name = "min_price", type = "double" },
    { name = "max_price", type = "double" },
    { name = "close_price", type = "double" }
  ]
}

resource "aws_glue_catalog_database" "phase1" {
  name        = local.glue_database_name
  description = "Phase 1 historical Polymarket ETL tables."
}

resource "aws_glue_catalog_table" "dim_market" {
  name          = "dim_market"
  database_name = aws_glue_catalog_database.phase1.name
  table_type    = "EXTERNAL_TABLE"
  parameters = {
    EXTERNAL       = "TRUE"
    classification = "parquet"
  }

  storage_descriptor {
    location      = "${local.polygon_gold_location}/dim_market/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    dynamic "columns" {
      for_each = local.dim_market_columns
      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }
}

resource "aws_glue_catalog_table" "dim_outcome" {
  name          = "dim_outcome"
  database_name = aws_glue_catalog_database.phase1.name
  table_type    = "EXTERNAL_TABLE"
  parameters = {
    EXTERNAL       = "TRUE"
    classification = "parquet"
  }

  storage_descriptor {
    location      = "${local.polygon_gold_location}/dim_outcome/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    dynamic "columns" {
      for_each = local.dim_outcome_columns
      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }
}

resource "aws_glue_catalog_table" "fact_trades" {
  name          = "fact_trades"
  database_name = aws_glue_catalog_database.phase1.name
  table_type    = "EXTERNAL_TABLE"
  parameters = {
    EXTERNAL                    = "TRUE"
    classification              = "parquet"
    "projection.enabled"        = "true"
    "projection.date.type"      = "date"
    "projection.date.range"     = "2020-01-01,NOW"
    "projection.date.format"    = "yyyy-MM-dd"
    "storage.location.template" = "${local.polygon_gold_location}/fact_trades/date=$${date}/"
  }

  partition_keys {
    name = "date"
    type = "string"
  }

  storage_descriptor {
    location      = "${local.polygon_gold_location}/fact_trades/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    dynamic "columns" {
      for_each = local.fact_trades_columns
      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }
}

resource "aws_glue_catalog_table" "fact_market_daily" {
  name          = "fact_market_daily"
  database_name = aws_glue_catalog_database.phase1.name
  table_type    = "EXTERNAL_TABLE"
  parameters = {
    EXTERNAL                    = "TRUE"
    classification              = "parquet"
    "projection.enabled"        = "true"
    "projection.date.type"      = "date"
    "projection.date.range"     = "2020-01-01,NOW"
    "projection.date.format"    = "yyyy-MM-dd"
    "storage.location.template" = "${local.polygon_gold_location}/fact_market_daily/date=$${date}/"
  }

  partition_keys {
    name = "date"
    type = "string"
  }

  storage_descriptor {
    location      = "${local.polygon_gold_location}/fact_market_daily/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    dynamic "columns" {
      for_each = local.fact_market_daily_columns
      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }
}
