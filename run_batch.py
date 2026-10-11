"""Generate the competition CSVs.  python run_batch.py --bills data/test/bills --csv data/test/csv --out output"""
import argparse, logging
from pathlib import Path
from billdecoder.pipeline import run_batch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
ap = argparse.ArgumentParser()
ap.add_argument("--bills", required=True, type=Path, help="folder of bill images")
ap.add_argument("--csv", required=True, type=Path, help="folder containing level1.csv and level2.csv templates")
ap.add_argument("--out", default=Path("output"), type=Path)
a = ap.parse_args()
run_batch(a.bills, a.csv, a.out)
