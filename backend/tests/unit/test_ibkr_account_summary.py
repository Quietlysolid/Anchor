from anchor.execution.ibkr_client import IBKRBrokerClient


def test_normalize_account_summary_rows_prefers_base_currency_for_nav():
    rows = [
        {"account": "DU123", "tag": "NetLiquidation", "value": "99999.99", "currency": "USD"},
        {"account": "DU123", "tag": "NetLiquidation", "value": "100192.52", "currency": "BASE"},
        {"account": "DU123", "tag": "TotalCashValue", "value": "100019.82", "currency": "BASE"},
        {"account": "DU123", "tag": "Currency", "value": "USD", "currency": "BASE"},
    ]

    normalized = IBKRBrokerClient._normalize_account_summary_rows(rows, "DU123")

    assert normalized["NAV"] == 100192.52
    assert normalized["balance"] == 100019.82
    assert normalized["currency"] == "USD"


def test_normalize_account_summary_rows_filters_to_requested_account():
    rows = [
        {"account": "OTHER", "tag": "NetLiquidation", "value": "250000.00", "currency": "BASE"},
        {"account": "DU123", "tag": "NetLiquidation", "value": "100192.52", "currency": "BASE"},
        {"account": "OTHER", "tag": "TotalCashValue", "value": "249000.00", "currency": "BASE"},
        {"account": "DU123", "tag": "TotalCashValue", "value": "100019.82", "currency": "BASE"},
    ]

    normalized = IBKRBrokerClient._normalize_account_summary_rows(rows, "DU123")

    assert normalized["NAV"] == 100192.52
    assert normalized["balance"] == 100019.82


def test_normalize_account_summary_rows_falls_back_to_cash_balance():
    rows = [
        {"account": "DU123", "tag": "NetLiquidation", "value": "100192.52", "currency": "BASE"},
        {"account": "DU123", "tag": "CashBalance", "value": "99950.00", "currency": "BASE"},
    ]

    normalized = IBKRBrokerClient._normalize_account_summary_rows(rows, "DU123")

    assert normalized["NAV"] == 100192.52
    assert normalized["balance"] == 99950.00
