from prediction_market.aws_etl.cli import main
import sys


if __name__ == "__main__":
    raise SystemExit(main(["--job", "fetch_markets", *sys.argv[1:]]))
