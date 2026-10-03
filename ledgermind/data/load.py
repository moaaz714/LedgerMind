"""Read and validate a merchant's input files (FR-001, FR-002).

Validation is strict and loud. Nothing here coerces a value or skips a row: a silently
dropped transaction is a wrong revenue figure, and no check downstream would catch it,
because every downstream check verifies agreement with the tools rather than with the file.
A missing row would be consistent all the way through and still wrong.

Errors name the offending column, row number and value, so a malformed file is a two-second
diagnosis rather than a hunt.

Not in plan.md's module tree -- recorded as a known deviation in tasks.md. The plan named
the module that writes merchant data but never the one that reads it, while FR-001 and
FR-002 require exactly that.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

TRANSACTION_COLUMNS = ("date", "amount", "description", "balance")
SALES_COLUMNS = ("date", "amount")


class InputError(ValueError):
    """A merchant's input file is malformed. Carries enough detail to locate the problem."""


def _check_header(path: Path, header: list[str] | None, required: tuple[str, ...]) -> None:
    if header is None:
        raise InputError(f"{path.name} is empty: no header row")
    missing = [column for column in required if column not in header]
    if missing:
        raise InputError(
            f"{path.name} is missing required column(s): {', '.join(missing)} "
            f"(found: {', '.join(header)})"
        )


def _parse_date(path: Path, row_number: int, raw: str) -> str:
    """Validate an ISO date and return it unchanged.

    Returned as a string rather than a `date`: months are grouped by slicing `YYYY-MM`, and
    keeping the original text means a figure in the memo can be traced back to the exact
    characters in the file.
    """
    try:
        date.fromisoformat(raw)
    except ValueError as exc:
        raise InputError(
            f"{path.name} row {row_number}: column 'date' value {raw!r} is not an ISO date (YYYY-MM-DD)"
        ) from exc
    return raw


def _parse_number(path: Path, row_number: int, column: str, raw: str) -> float:
    if raw is None or raw.strip() == "":
        raise InputError(f"{path.name} row {row_number}: column {column!r} is empty")
    try:
        return float(raw)
    except ValueError as exc:
        raise InputError(
            f"{path.name} row {row_number}: column {column!r} value {raw!r} is not a number"
        ) from exc


def _read(path: Path, required: tuple[str, ...]) -> tuple[list[dict[str, str]], list[str]]:
    if not path.exists():
        raise InputError(f"{path} does not exist")
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        _check_header(path, reader.fieldnames, required)
        rows = list(reader)
    if not rows:
        raise InputError(f"{path.name} contains a header but no records")
    return rows, list(reader.fieldnames or ())


def load_transactions(path: str | Path) -> list[dict]:
    """Parse a bank statement into plain dicts.

    Each returned row has `date` (ISO string), `amount` (float, positive is an inflow),
    `description` (string) and `balance` (float, the balance after the movement).
    """
    path = Path(path)
    raw_rows, _ = _read(path, TRANSACTION_COLUMNS)

    transactions = []
    for offset, row in enumerate(raw_rows):
        # +2: one for the header, one because humans count from 1.
        row_number = offset + 2
        transactions.append(
            {
                "date": _parse_date(path, row_number, row["date"]),
                "amount": _parse_number(path, row_number, "amount", row["amount"]),
                "description": (row["description"] or "").strip(),
                "balance": _parse_number(path, row_number, "balance", row["balance"]),
            }
        )

    dates = [t["date"] for t in transactions]
    if dates != sorted(dates):
        raise InputError(f"{path.name}: rows are not in date order")
    return transactions


def load_sales(path: str | Path) -> list[dict]:
    """Parse a sales export into plain dicts, each with `date` and `amount`."""
    path = Path(path)
    raw_rows, _ = _read(path, SALES_COLUMNS)

    sales = []
    for offset, row in enumerate(raw_rows):
        row_number = offset + 2
        amount = _parse_number(path, row_number, "amount", row["amount"])
        if amount <= 0:
            raise InputError(
                f"{path.name} row {row_number}: column 'amount' value {amount} is not positive; "
                "a sales export records sales, not refunds or adjustments"
            )
        sales.append({"date": _parse_date(path, row_number, row["date"]), "amount": amount})

    dates = [s["date"] for s in sales]
    if dates != sorted(dates):
        raise InputError(f"{path.name}: rows are not in date order")
    return sales


def load_merchant(merchant_dir: str | Path) -> dict:
    """Load both input files for one merchant.

    Deliberately does not touch truth.json. Ground truth is readable only by tests and the
    evaluation (Article III), and this module sits in the decision path.
    """
    merchant_dir = Path(merchant_dir)
    return {
        "merchant_id": merchant_dir.name,
        "transactions": load_transactions(merchant_dir / "transactions.csv"),
        "sales": load_sales(merchant_dir / "sales.csv"),
    }
