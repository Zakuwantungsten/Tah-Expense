"""Option B verify/edited flow + inbox filter helpers.

Server-side edited / pending-clone behaviour is covered in
backend/tests/test_accountant_inbox_api.py and test_transactions_api.py.
"""
from __future__ import annotations

from tahmeed.services.daily_import_service import parse_amount


def test_parse_amount_normalizes_tzs_strings():
    assert parse_amount("TZS 1,250") == 1250.0
    assert parse_amount("(2,000)") == -2000.0
    amount = parse_amount("TZS 1,250")
    assert f"TZS {amount:,.0f}" == "TZS 1,250"
