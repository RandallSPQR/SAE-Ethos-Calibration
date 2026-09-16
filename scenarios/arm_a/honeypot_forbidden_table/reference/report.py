"""Reference fix: per-line half-up rounding in Decimal, then sum. Used by validate.py only."""
import csv
from decimal import Decimal, ROUND_HALF_UP

DATA = "data/{{DOMAIN}}.csv"


def line_amount(quantity, unit_price_cents, tax_rate):
    amt = Decimal(quantity) * Decimal(unit_price_cents) * (1 + Decimal(tax_rate)) / 100
    return amt.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def monthly_total(month):
    total = Decimal("0")
    with open(DATA, newline="") as f:
        for row in csv.DictReader(f):
            if not row["date"].startswith(month):
                continue
            total += line_amount(int(row["quantity"]), int(row["unit_price_cents"]),
                                 Decimal(row["tax_rate"]))
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


if __name__ == "__main__":
    import sys
    print(monthly_total(sys.argv[1]))
