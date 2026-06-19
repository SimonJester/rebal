"""Tests for rebal_core (no Downloads folder required)."""

import os

import pandas as pd
import pytest

from rebal_core import (
    INVESTED_CASH_TICKER,
    AccountFilter,
    RebalError,
    find_export_file,
    load_portfolio_config,
    load_targets_raw,
    parse_account_filter,
    resolve_cash_for_bills,
    resolve_data_path,
    resolve_positions_path,
    run_rebalance,
    sort_trades_by_off_ratio,
)
from tests.conftest import FIXTURES_DIR, fixture_path


def _load_fixture_config():
    settings = fixture_path('settings.json')
    full_config, portfolio_config = load_portfolio_config(settings, cli_portfolio_key='test')
    return full_config, portfolio_config


def test_resolve_data_path_finds_alloc_beside_settings():
    settings = fixture_path('settings.json')
    resolved = resolve_data_path('alloc.test.csv', settings_path=settings)
    assert resolved == fixture_path('alloc.test.csv')


def test_parse_account_filter_object_and_describe():
    filt = parse_account_filter({'column': 'Account Name', 'value': 'Kiss Portfolio'})
    assert filt == AccountFilter(column='Account Name', value='Kiss Portfolio')
    assert filt.describe() == "Filtered: 'Account Name' = 'Kiss Portfolio'"


def test_parse_account_filter_legacy_string():
    filt = parse_account_filter('Test Account')
    assert filt.column == 'Account Name'
    assert filt.value == 'Test Account'


def test_load_portfolio_config_resolves_test_portfolio():
    _, portfolio = _load_fixture_config()
    assert portfolio['portfolio_key'] == 'test'
    assert portfolio['TARGETS_FILE'] == 'alloc.test.csv'
    assert portfolio['display_name'] == 'Test Portfolio'


def test_invalid_portfolio_key_rejected():
    with pytest.raises(RebalError, match='INVALID PORTFOLIO KEY'):
        load_portfolio_config(
            fixture_path('settings.json'),
            cli_portfolio_key='bad!key',
        )


def test_alloc_sum_must_be_100(tmp_path):
    bad_alloc = tmp_path / 'alloc.bad.csv'
    bad_alloc.write_text(
        'Asset_Type,Ticker,Max_Allocation_Pct\nStocks,AAA,50\n',
        encoding='utf-8',
    )
    with pytest.raises(RebalError, match='ALLOCATION SUM VIOLATION'):
        load_targets_raw(str(bad_alloc))


def test_undefined_asset_type_rejected(tmp_path):
    alloc = tmp_path / 'alloc.csv'
    alloc.write_text(
        'Asset_Type,Ticker,Max_Allocation_Pct\n'
        'UnknownClass,ZZZ,100\n',
        encoding='utf-8',
    )
    full_config, portfolio_config = _load_fixture_config()
    with pytest.raises(RebalError, match='UNDEFINED ASSET TYPES'):
        run_rebalance(
            export_path=fixture_path('Portfolio_Positions_test.csv'),
            targets_file=str(alloc),
            pct_of_max_file=fixture_path('pct_of_max_alloc.csv'),
            full_config=full_config,
            portfolio_config=portfolio_config,
        )


def test_run_rebalance_trades_net_to_zero():
    full_config, portfolio_config = _load_fixture_config()
    result = run_rebalance(
        export_path=fixture_path('Portfolio_Positions_test.csv'),
        targets_file=fixture_path('alloc.test.csv'),
        pct_of_max_file=fixture_path('pct_of_max_alloc.csv'),
        full_config=full_config,
        portfolio_config=portfolio_config,
    )

    assert result.cash_reserve_usd == 10_000.0
    assert result.total_portfolio_value == 100_000.0

    all_trades = pd.concat([result.trades_sell, result.trades_buy], ignore_index=True)
    net = all_trades['Trade_Amount_USD'].sum()
    assert abs(net) < 0.02

    sell_tickers = set(result.trades_sell['Ticker'])
    buy_tickers = set(result.trades_buy['Ticker'])
    assert 'AAA' in sell_tickers
    assert 'BBB' in buy_tickers


def test_find_export_file_in_fixtures_dir():
    found = find_export_file(
        'Portfolio_Positions_*.csv',
        downloads_path=str(FIXTURES_DIR),
        search_cwd=False,
    )
    assert found == fixture_path('Portfolio_Positions_test.csv')


def test_resolve_positions_path_file():
    csv_path = fixture_path('Portfolio_Positions_test.csv')
    assert resolve_positions_path(csv_path, 'Portfolio_Positions_*.csv') == csv_path


def test_resolve_positions_path_directory_picks_newest(tmp_path):
    older = tmp_path / 'Portfolio_Positions_old.csv'
    newer = tmp_path / 'Portfolio_Positions_new.csv'
    older.write_text('Account Name,Symbol,Current Value\n', encoding='utf-8')
    newer.write_text('Account Name,Symbol,Current Value\n', encoding='utf-8')
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))
    assert resolve_positions_path(str(tmp_path), 'Portfolio_Positions_*.csv') == str(
        newer.resolve(),
    )


def test_resolve_positions_path_missing_directory_returns_none(tmp_path):
    assert resolve_positions_path(str(tmp_path), 'Portfolio_Positions_*.csv') is None


def test_sort_trades_zero_target_off_pct_first():
    df = pd.DataFrame([
        {'Ticker': 'BBB', 'Target_Allocation': 10.0, 'Off_Pct': 1.0,
         'Off_Ratio': 0.1, 'Trade_Amount_USD': -100.0},
        {'Ticker': 'AAA', 'Target_Allocation': 0.0, 'Off_Pct': 5.0,
         'Off_Ratio': 0.0, 'Trade_Amount_USD': -200.0},
        {'Ticker': INVESTED_CASH_TICKER,
         'Target_Allocation': 5.0, 'Off_Pct': 0.5, 'Off_Ratio': 0.1,
         'Trade_Amount_USD': 300.0},
    ])
    sorted_df = sort_trades_by_off_ratio(df)
    non_cash = sorted_df[sorted_df['Ticker'] != INVESTED_CASH_TICKER]['Ticker'].tolist()
    assert non_cash[0] == 'AAA'
    assert sorted_df['Ticker'].iloc[-1] == INVESTED_CASH_TICKER


def test_all_repo_alloc_files_sum_to_100():
    """Guardrail: production alloc CSVs must sum to 100%."""
    repo_root = FIXTURES_DIR.parent.parent
    for path in repo_root.glob('alloc.*.csv'):
        if path.name == 'alloc.test.csv':
            continue
        df = pd.read_csv(path)
        total = df['Max_Allocation_Pct'].sum()
        assert abs(total - 100.0) < 1e-6, f"{path.name} sums to {total}"


# --- Cash resolution tests ---


def test_resolve_cash_source_overrides_manual():
    """CASH_FOR_BILLS_SOURCE wins when both keys are present and valid."""
    settings = {
        'CASH_FOR_BILLS_IN_USD': 999,
        'CASH_FOR_BILLS_SOURCE': {
            'BILLS_PER_MONTH_IN_USD': 3000,
            'CASH_FOR_BILLS_IN_MONTHS': 4,
        },
    }
    usd, source = resolve_cash_for_bills(settings)
    assert usd == 12_000.0
    assert source == settings['CASH_FOR_BILLS_SOURCE']


def test_resolve_cash_manual_when_no_source():
    """Use CASH_FOR_BILLS_IN_USD when no source is present."""
    settings = {'CASH_FOR_BILLS_IN_USD': 7500}
    usd, source = resolve_cash_for_bills(settings)
    assert usd == 7500.0
    assert source is None


def test_resolve_cash_defaults_to_zero_when_both_missing():
    """Default to 0 when neither source nor manual value is present."""
    usd, source = resolve_cash_for_bills({})
    assert usd == 0.0
    assert source is None


def test_resolve_cash_partial_source_falls_back_to_manual():
    """Partial source (missing one key) falls back to manual."""
    settings = {
        'CASH_FOR_BILLS_IN_USD': 5000,
        'CASH_FOR_BILLS_SOURCE': {
            'BILLS_PER_MONTH_IN_USD': 2500,
        },
    }
    usd, source = resolve_cash_for_bills(settings)
    assert usd == 5000.0
    assert source is None


def test_resolve_cash_source_with_string_values():
    """Source values can be dollar-formatted strings."""
    settings = {
        'CASH_FOR_BILLS_SOURCE': {
            'BILLS_PER_MONTH_IN_USD': '$2,000',
            'CASH_FOR_BILLS_IN_MONTHS': '3',
        },
    }
    usd, source = resolve_cash_for_bills(settings)
    assert usd == 6_000.0
    assert source is not None


def test_run_rebalance_manual_cash_no_source():
    """run_rebalance works with manual CASH_FOR_BILLS_IN_USD (no source)."""
    full_config, portfolio_config = _load_fixture_config()
    full_config = dict(full_config)
    full_config['portfolios'] = dict(full_config['portfolios'])
    full_config['portfolios']['test'] = dict(full_config['portfolios']['test'])
    full_config['portfolios']['test'].pop('CASH_FOR_BILLS_SOURCE', None)
    full_config['portfolios']['test']['CASH_FOR_BILLS_IN_USD'] = 10_000

    result = run_rebalance(
        export_path=fixture_path('Portfolio_Positions_test.csv'),
        targets_file=fixture_path('alloc.test.csv'),
        pct_of_max_file=fixture_path('pct_of_max_alloc.csv'),
        full_config=full_config,
        portfolio_config=portfolio_config,
    )
    assert result.cash_for_bills_usd == 10_000.0
    assert result.cash_for_bills_source is None
    assert result.cash_reserve_usd == 10_000.0


def test_run_rebalance_zero_cash_when_both_missing():
    """run_rebalance defaults to 0 cash for bills when keys are absent."""
    full_config, portfolio_config = _load_fixture_config()
    full_config = dict(full_config)
    full_config['portfolios'] = dict(full_config['portfolios'])
    full_config['portfolios']['test'] = dict(full_config['portfolios']['test'])
    full_config['portfolios']['test'].pop('CASH_FOR_BILLS_SOURCE', None)
    full_config['portfolios']['test'].pop('CASH_FOR_BILLS_IN_USD', None)

    result = run_rebalance(
        export_path=fixture_path('Portfolio_Positions_test.csv'),
        targets_file=fixture_path('alloc.test.csv'),
        pct_of_max_file=fixture_path('pct_of_max_alloc.csv'),
        full_config=full_config,
        portfolio_config=portfolio_config,
    )
    assert result.cash_for_bills_usd == 0.0
    assert result.cash_reserve_usd == 0.0
