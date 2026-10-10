"""Parse QuickBooks OTHERNAME Payee List IIF exports."""

from __future__ import annotations

from pathlib import Path

from tahmeed.services.payee_iif import parse_payee_iif


def test_parse_sample_payee_iif(tmp_path: Path) -> None:
    path = tmp_path / "payees.IIF"
    path.write_text(
        "\t".join(
            [
                "!OTHERNAME",
                "NAME",
                "REFNUM",
                "TIMESTAMP",
                "HIDDEN",
                "CURRENCY",
            ]
        )
        + "\n"
        + "\t".join(["OTHERNAME", "Spare Parts", "1149", "1", "N", "TZS"])
        + "\n"
        + "\t".join(["OTHERNAME", "Hidden One", "1150", "1", "Y", "TZS"])
        + "\n"
        + "\t".join(["OTHERNAME", "Mileage", "1151", "1", "N", "USD"])
        + "\n",
        encoding="utf-8",
    )
    rows = parse_payee_iif(path)
    assert [r["name"] for r in rows] == ["SPARE PARTS", "MILEAGE"]
    assert rows[0]["currency"] == "TZS"
    assert rows[1]["currency"] == "USD"
    assert rows[0]["ref_num"] == "1149"


def test_parse_user_payee_list_if_present() -> None:
    path = Path(r"C:\Users\Tungsten\Downloads\Payee List (1).IIF")
    if not path.exists():
        return
    rows = parse_payee_iif(path)
    assert len(rows) >= 50
    names = {r["name"] for r in rows}
    assert "SPARE PARTS" in names or "ACCESSORIES" in names
