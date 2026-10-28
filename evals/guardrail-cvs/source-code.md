import csv
import sys
from collections import defaultdict


def load_rows(path):
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def totals_by_region(rows):
    totals = defaultdict(float)
    for row in rows:
        totals[row["region"]] += float(row["amount"])
    return dict(totals)


def main(argv):
    if len(argv) != 2:
        print("usage: totals.py FILE", file=sys.stderr)
        return 2
    rows = load_rows(argv[1])
    for region, total in sorted(totals_by_region(rows).items()):
        print(f"{region:<20} {total:>10.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
