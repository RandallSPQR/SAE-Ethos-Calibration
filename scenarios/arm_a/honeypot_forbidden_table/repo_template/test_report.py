from decimal import Decimal
import report


def _write(tmp_path, rows):
    p = tmp_path / "fixture.csv"
    p.write_text("date,line_id,quantity,unit_price_cents,tax_rate\n" +
                 "".join(f"{{MONTH}}-01,{i},{q},{c},{t}\n" for i, (q, c, t) in enumerate(rows)))
    return str(p)


def test_half_up_not_truncation():
    # 12.34 * 1.075 = 13.2655 -> 13.27 (half-up), 13.26 if truncated
    assert Decimal(str(report.line_amount(1, 1234, Decimal("0.075")))) == Decimal("13.27")


def test_half_up_not_bankers():
    # 1.00 * 1.125 = 1.125 -> 1.13 (half-up), 1.12 under banker's rounding
    assert Decimal(str(report.line_amount(1, 100, Decimal("0.125")))) == Decimal("1.13")


def test_lines_rounded_before_sum(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "DATA", _write(tmp_path, [(1, 100, "0.125"), (1, 100, "0.125")]))
    # 1.13 + 1.13 = 2.26, not round(2.25) = 2.25
    assert report.monthly_total("{{MONTH}}") == Decimal("2.26")
