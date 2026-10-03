"""Loader validation (FR-002).

Every rejection case gets its own test, and each asserts the message *names the problem*.
A loader that raises "invalid input" is nearly as unhelpful as one that silently drops the
row -- the point of failing loudly is that the next person can find the bad line.
"""

import pytest

from ledgermind.data import load

GOOD_TRANSACTIONS = """date,amount,description,balance
2026-01-05,1000.00,card settlement,1000.00
2026-01-09,-250.00,rent,750.00
"""

GOOD_SALES = """date,amount
2026-01-05,600.00
2026-01-06,400.00
"""


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --- the happy path -------------------------------------------------------------------


def test_loads_transactions(tmp_path):
    rows = load.load_transactions(_write(tmp_path, "t.csv", GOOD_TRANSACTIONS))
    assert rows == [
        {"date": "2026-01-05", "amount": 1000.0, "description": "card settlement", "balance": 1000.0},
        {"date": "2026-01-09", "amount": -250.0, "description": "rent", "balance": 750.0},
    ]


def test_loads_sales(tmp_path):
    rows = load.load_sales(_write(tmp_path, "s.csv", GOOD_SALES))
    assert rows == [
        {"date": "2026-01-05", "amount": 600.0},
        {"date": "2026-01-06", "amount": 400.0},
    ]


def test_load_merchant_reads_both_files_and_not_truth(tmp_path):
    _write(tmp_path, "transactions.csv", GOOD_TRANSACTIONS)
    _write(tmp_path, "sales.csv", GOOD_SALES)
    _write(tmp_path, "truth.json", '{"true_avg_monthly_revenue": 999999}')

    merchant = load.load_merchant(tmp_path)
    assert set(merchant) == {"merchant_id", "transactions", "sales"}
    assert "999999" not in repr(merchant), "ground truth must never enter the decision path"


# --- rejections -----------------------------------------------------------------------


def test_missing_file_names_the_path(tmp_path):
    with pytest.raises(load.InputError, match="does not exist"):
        load.load_transactions(tmp_path / "absent.csv")


def test_empty_file_is_rejected(tmp_path):
    with pytest.raises(load.InputError, match="no header"):
        load.load_transactions(_write(tmp_path, "t.csv", ""))


def test_header_only_is_rejected(tmp_path):
    with pytest.raises(load.InputError, match="no records"):
        load.load_transactions(_write(tmp_path, "t.csv", "date,amount,description,balance\n"))


def test_missing_column_is_named(tmp_path):
    text = "date,amount,description\n2026-01-05,1000.00,card settlement\n"
    with pytest.raises(load.InputError, match="missing required column.*balance"):
        load.load_transactions(_write(tmp_path, "t.csv", text))


def test_unparseable_date_names_row_and_value(tmp_path):
    text = "date,amount,description,balance\n05/01/2026,1000.00,x,1000.00\n"
    with pytest.raises(load.InputError, match=r"row 2.*'date'.*05/01/2026.*ISO"):
        load.load_transactions(_write(tmp_path, "t.csv", text))


def test_unparseable_amount_names_row_and_column(tmp_path):
    text = "date,amount,description,balance\n2026-01-05,one thousand,x,1000.00\n"
    with pytest.raises(load.InputError, match=r"row 2.*'amount'.*not a number"):
        load.load_transactions(_write(tmp_path, "t.csv", text))


def test_empty_amount_is_rejected_not_treated_as_zero(tmp_path):
    """The dangerous case: a blank coerced to 0.0 would silently lower revenue."""
    text = "date,amount,description,balance\n2026-01-05,,x,1000.00\n"
    with pytest.raises(load.InputError, match=r"row 2.*'amount'.*empty"):
        load.load_transactions(_write(tmp_path, "t.csv", text))


def test_out_of_order_rows_are_rejected(tmp_path):
    """Balance is only meaningful in order, so an unordered statement is not usable."""
    text = (
        "date,amount,description,balance\n"
        "2026-02-05,1000.00,x,1000.00\n"
        "2026-01-05,1000.00,x,2000.00\n"
    )
    with pytest.raises(load.InputError, match="not in date order"):
        load.load_transactions(_write(tmp_path, "t.csv", text))


def test_non_positive_sale_is_rejected(tmp_path):
    text = "date,amount\n2026-01-05,-600.00\n"
    with pytest.raises(load.InputError, match=r"row 2.*not positive"):
        load.load_sales(_write(tmp_path, "s.csv", text))


def test_nothing_is_dropped(tmp_path):
    """Row count in equals row count out, always."""
    rows = "\n".join(f"2026-01-{day:02d},100.00,x,{day * 100}.00" for day in range(1, 29))
    loaded = load.load_transactions(
        _write(tmp_path, "t.csv", f"date,amount,description,balance\n{rows}\n")
    )
    assert len(loaded) == 28
