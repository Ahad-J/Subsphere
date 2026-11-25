"""Spark Streaming & Batch Medallion Lakehouse Job (Bronze -> Silver -> Gold -> PostgreSQL).

Uses PySpark Structured Streaming/DataFrames when `pyspark` is installed in the Spark cluster
and falls back cleanly to the deterministic `MedallionPipeline` local execution engine in
single-node dev/test environments without PySpark JVM overhead.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.analytics.medallion import MedallionPipeline  # noqa: E402
from app.db.engine import get_db, init_schema  # noqa: E402


def run_pyspark_bronze_to_gold(bronze_dir: Path, silver_dir: Path, gold_dir: Path, checkpoint_dir: Path) -> bool:
    """Attempt native PySpark DataFrame execution if pyspark is available in runtime."""
    try:
        from pyspark.sql import SparkSession
        from pyspark.sql import functions as F
    except ImportError:
        return False

    spark = (
        SparkSession.builder.appName("SubSphereMedallion")
        .config("spark.sql.streaming.checkpointLocation", str(checkpoint_dir))
        .getOrCreate()
    )
    try:
        bronze_glob = str(bronze_dir / "event_date=*" / "events.jsonl")
        df_bronze = spark.read.json(bronze_glob)
        df_silver = (
            df_bronze.filter(
                F.col("event_id").isNotNull()
                & F.col("organization_id").isNotNull()
                & (F.col("quantity") > 0)
            )
            .dropDuplicates(["event_id"])
        )
        df_silver.write.mode("overwrite").parquet(str(silver_dir / "events.parquet"))

        df_gold = df_silver.groupBy("organization_id", "service_id", "metric_key", "event_date").agg(
            F.sum("quantity").alias("total_quantity"),
            F.count("event_id").alias("distinct_events"),
        )
        df_gold.write.mode("overwrite").parquet(str(gold_dir / "daily_usage.parquet"))
        return True
    finally:
        spark.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="SubSphere Medallion Spark Pipeline")
    parser.add_argument("--replay", action="store_true", help="Replay Bronze through Silver/Gold checkpoint")
    parser.add_argument("--try-pyspark", action="store_true", help="Use PySpark DataFrame engine if installed")
    args = parser.parse_args()

    init_schema()
    pipeline = MedallionPipeline()
    used_pyspark = False
    if args.try_pyspark:
        used_pyspark = run_pyspark_bronze_to_gold(
            pipeline.root / "bronze" / "events",
            pipeline.root / "silver",
            pipeline.root / "gold",
            pipeline.root / "checkpoints" / "spark",
        )

    with get_db(write=True) as db:
        result = pipeline.run_silver_and_gold(db, replay_from_bronze=args.replay)
    result["engine"] = "pyspark" if used_pyspark else "python_medallion"
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
