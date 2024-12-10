from pyspark.sql import SparkSession, functions as F
from pyspark.sql.types import (
    StructType, StructField, StringType, FloatType, LongType, IntegerType, BooleanType
)
import sys

if __name__ == "__main__":
    # Read input path from args
    # Example usage: spark-submit historical_arbitrage_calc.py --input /path/to/historicalOpportunity.txt
    input_path = "historicalData.txt"
    for i, arg in enumerate(sys.argv):
        if arg == "--input" and i + 1 < len(sys.argv):
            input_path = sys.argv[i+1]

    if input_path is None:
        print("Please provide --input path to the historical data JSON file.")
        sys.exit(1)

    spark = SparkSession.builder \
        .appName("HistoricalArbitrageOpportunityCalculation") \
        .getOrCreate()

    # Define schema matching the sample records
    schema = StructType([
        StructField("ev", StringType(), True),
        StructField("pair", StringType(), True),
        StructField("lp", FloatType(), True),
        StructField("ls", FloatType(), True),
        StructField("bp", FloatType(), True),
        StructField("bs", FloatType(), True),
        StructField("ap", FloatType(), True),
        StructField("as", FloatType(), True),
        StructField("t", LongType(), True),
        StructField("x", IntegerType(), True),
        StructField("r", LongType(), True)
    ])

    # Load the data from the JSON file
    df = spark.read.schema(schema).json(input_path)

    # Filter pairs where both sides are 3-letter currencies
    def valid_pair(pair_str):
        if pair_str is None:
            return False
        parts = pair_str.split("-")
        if len(parts) == 2 and len(parts[0]) == 3 and len(parts[1]) == 3:
            return True
        return False

    # Register the UDF with BooleanType return
    spark.udf.register("valid_pair_udf", valid_pair, BooleanType())
    df = df.filter(F.expr("valid_pair_udf(pair)"))

    # Create a 5ms bucket
    df = df.withColumn("bucket", (F.col("t") / F.lit(5)).cast("long"))

    # Group by (bucket, pair) and collect all quotes
    grouped = df.groupBy("bucket", "pair").agg(
        F.collect_list(F.struct("x", "bp", "ap")).alias("quotes")
    )

    # UDF to determine if arbitrage exists in a given set of quotes
    def find_arbitrage_opportunities(quotes):
        # quotes: list of {x, bp, ap}
        # 1. Find best quote (highest bp) per exchange
        best_per_exchange = {}
        for q in quotes:
            ex_id = q['x']
            bp = q['bp']
            ap = q['ap']
            if ex_id not in best_per_exchange or bp > best_per_exchange[ex_id]['bp']:
                best_per_exchange[ex_id] = {'bp': bp, 'ap': ap}

        # If less than 2 exchanges, no arbitrage
        if len(best_per_exchange) < 2:
            return 0

        # Check pairwise for arbitrage
        exchanges = list(best_per_exchange.keys())
        for i in range(len(exchanges)):
            for j in range(i+1, len(exchanges)):
                ex1 = best_per_exchange[exchanges[i]]
                ex2 = best_per_exchange[exchanges[j]]
                # Check if price difference > 0.01
                if (ex2['bp'] - ex1['ap'] > 0.01) or (ex1['bp'] - ex2['ap'] > 0.01):
                    return 1  # Arbitrage found
        return 0

    spark.udf.register("find_arbitrage_udf", find_arbitrage_opportunities, IntegerType())

    arbitrage_df = grouped.withColumn("opportunity", F.expr("find_arbitrage_udf(quotes)"))

    # Sum opportunities per pair
    result = arbitrage_df.groupBy("pair").agg(F.sum("opportunity").alias("total_opportunities"))

    # Show results
    result.show(truncate=False)

    spark.stop()