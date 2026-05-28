"""Regression tests with fixed expected values from tests/fixtures/."""

import pandas as pd
import pytest

from rebal_core import RebalError, load_portfolio_config, run_rebalance
from tests.conftest import fixture_path


def _run_fixture_rebalance():
    settings = fixture_path('settings.json')
    full_config, portfolio_config = load_portfolio_config(
        settings, cli_portfolio_key='test',
    )
    return run_rebalance(
        export_path=fixture_path('Portfolio_Positions_test.csv'),
        targets_file=fixture_path('alloc.test.csv'),
        pct_of_max_file=fixture_path('pct_of_max_alloc.csv'),
        full_config=full_config,
        portfolio_config=portfolio_config,
    )


def test_fixture_golden_totals():
    result = _run_fixture_rebalance()
    assert result.cash_reserve_usd == 10_000.0
    assert result.total_portfolio_value == 100_000.0
    assert result.bills_per_month_usd == 5_000.0
    assert result.cash_for_bills_months == 2.0


def test_fixture_golden_trade_amounts():
    result = _run_fixture_rebalance()

    def trade_usd(trades: pd.DataFrame, ticker: str) -> float:
        row = trades[trades['Ticker'] == ticker]
        assert len(row) == 1, f"missing trade for {ticker}"
        return float(row['Trade_Amount_USD'].iloc[0])

    assert trade_usd(result.trades_sell, 'AAA') == pytest.approx(-6000.0, abs=0.01)
    assert trade_usd(result.trades_buy, 'BBB') == pytest.approx(6000.0, abs=0.01)


def test_cash_reserve_larger_than_portfolio_rejected():
    settings = fixture_path('settings.json')
    full_config, portfolio_config = load_portfolio_config(
        settings, cli_portfolio_key='test',
    )
    full_config = dict(full_config)
    full_config['portfolios'] = dict(full_config['portfolios'])
    full_config['portfolios']['test'] = dict(full_config['portfolios']['test'])
    full_config['portfolios']['test']['BILLS_PER_MONTH_IN_USD'] = 1_000_000
    full_config['portfolios']['test']['CASH_FOR_BILLS_IN_MONTHS'] = 1

    with pytest.raises(RebalError, match='NOT ENOUGH ASSETS FOR CASH RESERVE'):
        run_rebalance(
            export_path=fixture_path('Portfolio_Positions_test.csv'),
            targets_file=fixture_path('alloc.test.csv'),
            pct_of_max_file=fixture_path('pct_of_max_alloc.csv'),
            full_config=full_config,
            portfolio_config=portfolio_config,
        )


def test_legacy_single_portfolio_settings_still_work(tmp_path):
    legacy = tmp_path / 'settings.json'
    legacy.write_text(
        '{\n'
        '  "BILLS_PER_MONTH_IN_USD": 1000,\n'
        '  "CASH_FOR_BILLS_IN_MONTHS": 1,\n'
        '  "TAX_OWED_IN_USD": 0,\n'
        '  "ACCOUNT_FILTER": "Test Account"\n'
        '}\n',
        encoding='utf-8',
    )
    _, portfolio_config = load_portfolio_config(str(legacy))
    assert portfolio_config['portfolio_key'] == 'fidelity'
    assert portfolio_config['TARGETS_FILE'] == 'alloc.fidelity.csv'
