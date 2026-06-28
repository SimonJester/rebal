"""Tests for rebal_core (no Downloads folder required)."""

import os

import pandas as pd
import pytest

from rebal_core import (
    CASH_META_TICKER,
    CASH_POOL_SYMBOLS,
    AccountFilter,
    RebalError,
    find_export_file,
    load_portfolio_config,
    load_targets_raw,
    parse_account_filter,
    parse_portfolio_export,
    resolve_cash_for_bills,
    resolve_data_path,
    resolve_positions_path,
    resolve_safe_asset,
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
    assert filt.describe() == 'Filter: "Account Name" = "Kiss Portfolio"'


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
        {'Ticker': CASH_META_TICKER,
         'Target_Allocation': 5.0, 'Off_Pct': 0.5, 'Off_Ratio': 0.1,
         'Trade_Amount_USD': 300.0},
    ])
    sorted_df = sort_trades_by_off_ratio(df)
    non_cash = sorted_df[sorted_df['Ticker'] != CASH_META_TICKER]['Ticker'].tolist()
    assert non_cash[0] == 'AAA'
    assert sorted_df['Ticker'].iloc[-1] == CASH_META_TICKER


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


# --- SAFE_ASSET resolution tests ---


def test_resolve_safe_asset_default_is_cash():
    """SAFE_ASSET defaults to _CASH when not specified."""
    assert resolve_safe_asset({}) == '_CASH'


def test_resolve_safe_asset_explicit_cash():
    assert resolve_safe_asset({'SAFE_ASSET': '_CASH'}) == '_CASH'


def test_resolve_safe_asset_real_ticker():
    assert resolve_safe_asset({'SAFE_ASSET': 'KISS'}) == 'KISS'


def test_resolve_safe_asset_case_insensitive():
    assert resolve_safe_asset({'SAFE_ASSET': 'kiss'}) == 'KISS'


def test_resolve_safe_asset_rejects_cash_symbol():
    """SAFE_ASSET cannot be a CASH_POOL_SYMBOLS entry."""
    for sym in ['SPAXX**', 'SHV', 'USFR']:
        with pytest.raises(RebalError, match='SAFE_ASSET CONFLICT'):
            resolve_safe_asset({'SAFE_ASSET': sym})


def test_resolve_safe_asset_none_treated_as_default():
    assert resolve_safe_asset({'SAFE_ASSET': None}) == '_CASH'


def test_resolve_safe_asset_empty_string_treated_as_default():
    assert resolve_safe_asset({'SAFE_ASSET': ''}) == '_CASH'


# --- SAFE_ASSET integration tests ---


def _make_safe_asset_fixtures(tmp_path, safe_asset='KISS', kiss_alloc_pct=10):
    """Create fixture files for SAFE_ASSET integration tests.

    Portfolio: IBIT $45000, KISS $5000, SPAXX** $50000  (total $100k)
    Alloc: IBIT 90%, KISS <kiss_alloc_pct>%  (must sum to 100)
    Pct_of_max: Bitcoin 50%, Kiss 100%
    Cash for bills: $10,000, tax: $0
    Investable = $100k - $10k = $90k
    """
    ibit_pct = 100 - kiss_alloc_pct
    alloc = tmp_path / 'alloc.test.csv'
    alloc.write_text(
        f'Asset_Type,Ticker,Max_Allocation_Pct\n'
        f'Bitcoin,IBIT,{ibit_pct}\n'
        f'Kiss,KISS,{kiss_alloc_pct}\n',
        encoding='utf-8',
    )
    pct = tmp_path / 'pct_of_max_alloc.csv'
    pct.write_text(
        'Asset_Type,Pct_of_Max\n'
        'Bitcoin,50\n'
        'Kiss,100\n',
        encoding='utf-8',
    )
    positions = tmp_path / 'Portfolio_Positions_test.csv'
    positions.write_text(
        'Account Name,Symbol,Current Value\n'
        'Test,IBIT,"$45,000.00"\n'
        'Test,KISS,"$5,000.00"\n'
        'Test,SPAXX**,"$50,000.00"\n',
        encoding='utf-8',
    )
    settings = tmp_path / 'settings.json'
    import json
    settings.write_text(json.dumps({
        'default_portfolio': 'test',
        'portfolios': {
            'test': {
                'display_name': 'Test',
                'ACCOUNT_FILTER': {'column': 'Account Name', 'value': 'Test'},
                'FILE_PATTERN': 'Portfolio_Positions_test.csv',
                'SAFE_ASSET': safe_asset,
                'CASH_FOR_BILLS_IN_USD': 10_000,
                'TAX_OWED_IN_USD': 0,
            },
        },
    }), encoding='utf-8')
    return settings


def test_safe_asset_kiss_residual_flows_to_kiss(tmp_path):
    """With SAFE_ASSET=KISS: residual goes to KISS, _CASH mirrors all trades."""
    settings = _make_safe_asset_fixtures(tmp_path, safe_asset='KISS', kiss_alloc_pct=10)
    full_config, portfolio_config = load_portfolio_config(
        str(settings), cli_portfolio_key='test',
    )
    result = run_rebalance(
        export_path=str(tmp_path / 'Portfolio_Positions_test.csv'),
        targets_file=str(tmp_path / 'alloc.test.csv'),
        pct_of_max_file=str(tmp_path / 'pct_of_max_alloc.csv'),
        full_config=full_config,
        portfolio_config=portfolio_config,
    )

    assert result.safe_asset_ticker == 'KISS'

    # Investable = 100k - 10k = 90k
    # IBIT: 90% max * 50% pct_of_max = 45% of 90k = $40,500. Has $45k. Sell $4,500.
    # KISS: 10% max * 100% pct_of_max = 10% of 90k = $9,000 (explicit).
    # Residual = 100% - 45% - 10% = 45% of 90k = $40,500.
    # KISS final target = $9,000 + $40,500 = $49,500. Has $5k. Trade = +$44,500.
    # _CASH mirror = -(IBIT trade + KISS trade) = -(-4500 + 44500) = -$40,000.
    # Cash pool after: $50k - $40k = $10k = cash reserve. ✓

    all_trades = pd.concat([result.trades_sell, result.trades_buy], ignore_index=True)
    net = all_trades['Trade_Amount_USD'].sum()
    assert abs(net) < 0.02, f'trades do not net to zero: {net}'

    # KISS should not appear in both buy and sell
    sell_tickers = set(result.trades_sell['Ticker'])
    buy_tickers = set(result.trades_buy['Ticker'])
    assert not (sell_tickers & buy_tickers & {'KISS'}), \
        'KISS should not appear in both buy and sell tables'

    # Verify KISS trade amount
    kiss_trades = all_trades[all_trades['Ticker'] == 'KISS']
    assert len(kiss_trades) == 1
    kiss_trade = float(kiss_trades['Trade_Amount_USD'].iloc[0])
    assert kiss_trade == pytest.approx(44_500.0, abs=1.0)

    # _CASH is the mirror (sell side: gives cash to buy KISS)
    cash_trades = all_trades[all_trades['Ticker'] == '_CASH']
    assert len(cash_trades) == 1
    cash_trade = float(cash_trades['Trade_Amount_USD'].iloc[0])
    assert cash_trade == pytest.approx(-40_000.0, abs=1.0)


def test_safe_asset_default_cash_backward_compatible():
    """With no SAFE_ASSET setting, behavior matches the previous default cash handling."""
    full_config, portfolio_config = _load_fixture_config()
    result = run_rebalance(
        export_path=fixture_path('Portfolio_Positions_test.csv'),
        targets_file=fixture_path('alloc.test.csv'),
        pct_of_max_file=fixture_path('pct_of_max_alloc.csv'),
        full_config=full_config,
        portfolio_config=portfolio_config,
    )
    assert result.safe_asset_ticker == '_CASH'
    # Fixture: AAA 60% + BBB 40% = 100%, no CASH row.
    # AAA sells, BBB buys, they offset, so _CASH trade is ~0.
    all_trades = pd.concat([result.trades_sell, result.trades_buy], ignore_index=True)
    net = all_trades['Trade_Amount_USD'].sum()
    assert abs(net) < 0.02
    # Trade amounts should match the golden expectations
    assert result.cash_reserve_usd == 10_000.0
    assert result.total_portfolio_value == 100_000.0


def test_safe_asset_kiss_trade_netting(tmp_path):
    """KISS gets a single netted trade, not separate alloc + residual."""
    settings = _make_safe_asset_fixtures(tmp_path, safe_asset='KISS', kiss_alloc_pct=10)
    full_config, portfolio_config = load_portfolio_config(
        str(settings), cli_portfolio_key='test',
    )
    result = run_rebalance(
        export_path=str(tmp_path / 'Portfolio_Positions_test.csv'),
        targets_file=str(tmp_path / 'alloc.test.csv'),
        pct_of_max_file=str(tmp_path / 'pct_of_max_alloc.csv'),
        full_config=full_config,
        portfolio_config=portfolio_config,
    )
    all_trades = pd.concat([result.trades_sell, result.trades_buy], ignore_index=True)
    kiss_rows = all_trades[all_trades['Ticker'] == 'KISS']
    assert len(kiss_rows) == 1, 'KISS should appear exactly once (netted)'


def test_safe_asset_appears_last_in_trade_table(tmp_path):
    """Safe asset and _CASH appear last in their respective trade tables."""
    settings = _make_safe_asset_fixtures(tmp_path, safe_asset='KISS', kiss_alloc_pct=10)
    full_config, portfolio_config = load_portfolio_config(
        str(settings), cli_portfolio_key='test',
    )
    result = run_rebalance(
        export_path=str(tmp_path / 'Portfolio_Positions_test.csv'),
        targets_file=str(tmp_path / 'alloc.test.csv'),
        pct_of_max_file=str(tmp_path / 'pct_of_max_alloc.csv'),
        full_config=full_config,
        portfolio_config=portfolio_config,
    )
    # In the sell table, KISS and/or _CASH should be last
    if not result.trades_sell.empty:
        last_sell_ticker = result.trades_sell['Ticker'].iloc[-1]
        assert last_sell_ticker in {'KISS', '_CASH'}
    if not result.trades_buy.empty:
        last_buy_ticker = result.trades_buy['Ticker'].iloc[-1]
        assert last_buy_ticker in {'KISS', '_CASH'}


def test_safe_asset_conflict_with_cash_symbol():
    """SAFE_ASSET that is also a cash symbol should raise an error in run_rebalance."""
    full_config, portfolio_config = _load_fixture_config()
    full_config = dict(full_config)
    full_config['portfolios'] = dict(full_config['portfolios'])
    full_config['portfolios']['test'] = dict(full_config['portfolios']['test'])
    full_config['portfolios']['test']['SAFE_ASSET'] = 'SHV'

    with pytest.raises(RebalError, match='SAFE_ASSET CONFLICT'):
        run_rebalance(
            export_path=fixture_path('Portfolio_Positions_test.csv'),
            targets_file=fixture_path('alloc.test.csv'),
            pct_of_max_file=fixture_path('pct_of_max_alloc.csv'),
            full_config=full_config,
            portfolio_config=portfolio_config,
        )


# --- Tests for SYMBOL_COLUMN / VALUE_COLUMN configuration (new in flexible parsing) ---

def _write_custom_positions_csv(tmp_path, header, rows):
    """Helper to write a temp positions CSV with given header and data rows."""
    csv = tmp_path / 'positions_custom.csv'
    content = header + '\n' + '\n'.join(rows) + '\n'
    csv.write_text(content, encoding='utf-8')
    return csv


def test_column_config_with_both(tmp_path):
    """Test with both SYMBOL_COLUMN and VALUE_COLUMN set to non-default names."""
    header = 'Account Name,MyAsset,MyVal'
    rows = [
        'Test,AAA,"$60,000"',
        'Test,BBB,"$40,000"',
    ]
    pos_csv = _write_custom_positions_csv(tmp_path, header, rows)
    alloc = tmp_path / 'alloc.test.csv'
    alloc.write_text('Asset_Type,Ticker,Max_Allocation_Pct\nStocks,AAA,60\nStocks,BBB,40\n', encoding='utf-8')
    pct = tmp_path / 'pct_of_max_alloc.csv'
    pct.write_text('Asset_Type,Pct_of_Max\nStocks,100\n', encoding='utf-8')

    settings = tmp_path / 'settings.json'
    import json
    settings.write_text(json.dumps({
        'default_portfolio': 'test',
        'portfolios': {
            'test': {
                'display_name': 'Test',
                'ACCOUNT_FILTER': {'column': 'Account Name', 'value': 'Test'},
                'FILE_PATTERN': 'positions_custom.csv',
                'SYMBOL_COLUMN': 'MyAsset',
                'VALUE_COLUMN': 'MyVal',
                'CASH_POOL_TICKERS': [],
                'CASH_FOR_BILLS_IN_USD': 0,
                'TAX_OWED_IN_USD': 0,
            }
        }
    }), encoding='utf-8')

    full_config, portfolio_config = load_portfolio_config(str(settings), cli_portfolio_key='test')
    result = run_rebalance(
        export_path=str(pos_csv),
        targets_file=str(alloc),
        pct_of_max_file=str(pct),
        full_config=full_config,
        portfolio_config=portfolio_config,
    )
    # Should parse correctly: 60k + 40k = 100k total (no cash)
    assert result.total_portfolio_value == pytest.approx(100_000.0)
    assert len(result.df_current_portfolio) == 2


def test_column_config_without_both(tmp_path):
    """Test without SYMBOL_COLUMN or VALUE_COLUMN (rely on defaults)."""
    header = 'Account Name,Symbol,Current Value'
    rows = [
        'Test,AAA,"$60,000"',
        'Test,BBB,"$40,000"',
    ]
    pos_csv = _write_custom_positions_csv(tmp_path, header, rows)
    alloc = tmp_path / 'alloc.test.csv'
    alloc.write_text('Asset_Type,Ticker,Max_Allocation_Pct\nStocks,AAA,60\nStocks,BBB,40\n', encoding='utf-8')
    pct = tmp_path / 'pct_of_max_alloc.csv'
    pct.write_text('Asset_Type,Pct_of_Max\nStocks,100\n', encoding='utf-8')

    settings = tmp_path / 'settings.json'
    import json
    settings.write_text(json.dumps({
        'default_portfolio': 'test',
        'portfolios': {
            'test': {
                'display_name': 'Test',
                'ACCOUNT_FILTER': {'column': 'Account Name', 'value': 'Test'},
                'FILE_PATTERN': 'positions_custom.csv',
                # no SYMBOL_COLUMN or VALUE_COLUMN -> defaults
                'CASH_POOL_TICKERS': [],
                'CASH_FOR_BILLS_IN_USD': 0,
                'TAX_OWED_IN_USD': 0,
            }
        }
    }), encoding='utf-8')

    full_config, portfolio_config = load_portfolio_config(str(settings), cli_portfolio_key='test')
    result = run_rebalance(
        export_path=str(pos_csv),
        targets_file=str(alloc),
        pct_of_max_file=str(pct),
        full_config=full_config,
        portfolio_config=portfolio_config,
    )
    assert result.total_portfolio_value == pytest.approx(100_000.0)


def test_column_config_only_symbol(tmp_path):
    """Test with only SYMBOL_COLUMN set (VALUE_COLUMN uses default)."""
    header = 'Account Name,MyTicker,Current Value'
    rows = [
        'Test,AAA,"$60,000"',
        'Test,BBB,"$40,000"',
    ]
    pos_csv = _write_custom_positions_csv(tmp_path, header, rows)
    alloc = tmp_path / 'alloc.test.csv'
    alloc.write_text('Asset_Type,Ticker,Max_Allocation_Pct\nStocks,AAA,60\nStocks,BBB,40\n', encoding='utf-8')
    pct = tmp_path / 'pct_of_max_alloc.csv'
    pct.write_text('Asset_Type,Pct_of_Max\nStocks,100\n', encoding='utf-8')

    settings = tmp_path / 'settings.json'
    import json
    settings.write_text(json.dumps({
        'default_portfolio': 'test',
        'portfolios': {
            'test': {
                'display_name': 'Test',
                'ACCOUNT_FILTER': {'column': 'Account Name', 'value': 'Test'},
                'FILE_PATTERN': 'positions_custom.csv',
                'SYMBOL_COLUMN': 'MyTicker',
                # VALUE_COLUMN omitted -> default "Current Value"
                'CASH_POOL_TICKERS': [],
                'CASH_FOR_BILLS_IN_USD': 0,
                'TAX_OWED_IN_USD': 0,
            }
        }
    }), encoding='utf-8')

    full_config, portfolio_config = load_portfolio_config(str(settings), cli_portfolio_key='test')
    result = run_rebalance(
        export_path=str(pos_csv),
        targets_file=str(alloc),
        pct_of_max_file=str(pct),
        full_config=full_config,
        portfolio_config=portfolio_config,
    )
    assert result.total_portfolio_value == pytest.approx(100_000.0)


def test_column_config_only_value(tmp_path):
    """Test with only VALUE_COLUMN set (SYMBOL_COLUMN uses default)."""
    header = 'Account Name,Symbol,MyVal'
    rows = [
        'Test,AAA,"$60,000"',
        'Test,BBB,"$40,000"',
    ]
    pos_csv = _write_custom_positions_csv(tmp_path, header, rows)
    alloc = tmp_path / 'alloc.test.csv'
    alloc.write_text('Asset_Type,Ticker,Max_Allocation_Pct\nStocks,AAA,60\nStocks,BBB,40\n', encoding='utf-8')
    pct = tmp_path / 'pct_of_max_alloc.csv'
    pct.write_text('Asset_Type,Pct_of_Max\nStocks,100\n', encoding='utf-8')

    settings = tmp_path / 'settings.json'
    import json
    settings.write_text(json.dumps({
        'default_portfolio': 'test',
        'portfolios': {
            'test': {
                'display_name': 'Test',
                'ACCOUNT_FILTER': {'column': 'Account Name', 'value': 'Test'},
                'FILE_PATTERN': 'positions_custom.csv',
                # SYMBOL_COLUMN omitted -> default "Symbol"
                'VALUE_COLUMN': 'MyVal',
                'CASH_POOL_TICKERS': [],
                'CASH_FOR_BILLS_IN_USD': 0,
                'TAX_OWED_IN_USD': 0,
            }
        }
    }), encoding='utf-8')

    full_config, portfolio_config = load_portfolio_config(str(settings), cli_portfolio_key='test')
    result = run_rebalance(
        export_path=str(pos_csv),
        targets_file=str(alloc),
        pct_of_max_file=str(pct),
        full_config=full_config,
        portfolio_config=portfolio_config,
    )
    assert result.total_portfolio_value == pytest.approx(100_000.0)


# --- Additional explicit tests for edge and corner cases ---

def test_positions_df_with_custom_columns(tmp_path):
    """positions_df path with raw DF using custom column names from config."""
    df = pd.DataFrame({
        'Account Name': ['Test', 'Test'],
        'MyAsset': ['AAA', 'BBB'],
        'MyVal': [60000.0, 40000.0],
    })
    alloc = tmp_path / 'alloc.test.csv'
    alloc.write_text('Asset_Type,Ticker,Max_Allocation_Pct\nStocks,AAA,60\nStocks,BBB,40\n', encoding='utf-8')
    pct = tmp_path / 'pct_of_max_alloc.csv'
    pct.write_text('Asset_Type,Pct_of_Max\nStocks,100\n', encoding='utf-8')

    settings = tmp_path / 'settings.json'
    import json
    settings.write_text(json.dumps({
        'default_portfolio': 'test',
        'portfolios': {
            'test': {
                'display_name': 'Test',
                'ACCOUNT_FILTER': {'column': 'Account Name', 'value': 'Test'},
                'FILE_PATTERN': 'x.csv',
                'SYMBOL_COLUMN': 'MyAsset',
                'VALUE_COLUMN': 'MyVal',
                'CASH_POOL_TICKERS': [],
                'CASH_FOR_BILLS_IN_USD': 0,
                'TAX_OWED_IN_USD': 0,
            }
        }
    }), encoding='utf-8')

    full_config, portfolio_config = load_portfolio_config(str(settings), cli_portfolio_key='test')
    result = run_rebalance(
        positions_df=df,
        targets_file=str(alloc),
        pct_of_max_file=str(pct),
        full_config=full_config,
        portfolio_config=portfolio_config,
    )
    assert result.total_portfolio_value == pytest.approx(100_000.0)
    assert len(result.df_current_portfolio) == 2


def test_positions_df_already_normalized(tmp_path):
    """positions_df already has Ticker/Current_Value (no column remapping needed)."""
    df = pd.DataFrame({
        'Ticker': ['AAA', 'BBB'],
        'Current_Value': [60000.0, 40000.0],
    })
    alloc = tmp_path / 'alloc.test.csv'
    alloc.write_text('Asset_Type,Ticker,Max_Allocation_Pct\nStocks,AAA,60\nStocks,BBB,40\n', encoding='utf-8')
    pct = tmp_path / 'pct_of_max_alloc.csv'
    pct.write_text('Asset_Type,Pct_of_Max\nStocks,100\n', encoding='utf-8')

    settings = tmp_path / 'settings.json'
    import json
    settings.write_text(json.dumps({
        'default_portfolio': 'test',
        'portfolios': {
            'test': {
                'display_name': 'Test',
                'ACCOUNT_FILTER': {'column': 'Account Name', 'value': 'Test'},
                'FILE_PATTERN': 'x.csv',
                'SYMBOL_COLUMN': 'Foo',
                'VALUE_COLUMN': 'Bar',
                'CASH_POOL_TICKERS': [],
                'CASH_FOR_BILLS_IN_USD': 0,
                'TAX_OWED_IN_USD': 0,
            }
        }
    }), encoding='utf-8')

    full_config, portfolio_config = load_portfolio_config(str(settings), cli_portfolio_key='test')
    result = run_rebalance(
        positions_df=df,
        targets_file=str(alloc),
        pct_of_max_file=str(pct),
        full_config=full_config,
        portfolio_config=portfolio_config,
    )
    assert result.total_portfolio_value == pytest.approx(100_000.0)


def test_custom_columns_with_account_filter(tmp_path):
    """Custom columns combined with ACCOUNT_FILTER."""
    header = 'Acct,Asset,Val'
    rows = [
        'Kiss,AAA,"$60,000"',
        'Kiss,BBB,"$40,000"',
        'Other,CCC,"$10,000"',
    ]
    pos_csv = _write_custom_positions_csv(tmp_path, header, rows)
    alloc = tmp_path / 'alloc.test.csv'
    alloc.write_text('Asset_Type,Ticker,Max_Allocation_Pct\nStocks,AAA,60\nStocks,BBB,40\n', encoding='utf-8')
    pct = tmp_path / 'pct_of_max_alloc.csv'
    pct.write_text('Asset_Type,Pct_of_Max\nStocks,100\n', encoding='utf-8')

    settings = tmp_path / 'settings.json'
    import json
    settings.write_text(json.dumps({
        'default_portfolio': 'test',
        'portfolios': {
            'test': {
                'display_name': 'Test',
                'ACCOUNT_FILTER': {'column': 'Acct', 'value': 'Kiss'},
                'FILE_PATTERN': 'positions_custom.csv',
                'SYMBOL_COLUMN': 'Asset',
                'VALUE_COLUMN': 'Val',
                'CASH_POOL_TICKERS': [],
                'CASH_FOR_BILLS_IN_USD': 0,
                'TAX_OWED_IN_USD': 0,
            }
        }
    }), encoding='utf-8')

    full_config, portfolio_config = load_portfolio_config(str(settings), cli_portfolio_key='test')
    result = run_rebalance(
        export_path=str(pos_csv),
        targets_file=str(alloc),
        pct_of_max_file=str(pct),
        full_config=full_config,
        portfolio_config=portfolio_config,
    )
    assert result.total_portfolio_value == pytest.approx(100_000.0)
    assert len(result.df_current_portfolio) == 2


def test_custom_columns_error_missing_column(tmp_path):
    """Error when configured column is missing from the CSV."""
    header = 'Account Name,Symbol,Current Value'  # missing MyVal
    rows = ['Test,AAA,"$60,000"']
    pos_csv = _write_custom_positions_csv(tmp_path, header, rows)
    alloc = tmp_path / 'alloc.test.csv'
    alloc.write_text('Asset_Type,Ticker,Max_Allocation_Pct\nStocks,AAA,100\n', encoding='utf-8')
    pct = tmp_path / 'pct_of_max_alloc.csv'
    pct.write_text('Asset_Type,Pct_of_Max\nStocks,100\n', encoding='utf-8')

    settings = tmp_path / 'settings.json'
    import json
    settings.write_text(json.dumps({
        'default_portfolio': 'test',
        'portfolios': {
            'test': {
                'display_name': 'Test',
                'ACCOUNT_FILTER': {'column': 'Account Name', 'value': 'Test'},
                'FILE_PATTERN': 'positions_custom.csv',
                'SYMBOL_COLUMN': 'Symbol',
                'VALUE_COLUMN': 'MyVal',
                'CASH_POOL_TICKERS': [],
                'CASH_FOR_BILLS_IN_USD': 0,
                'TAX_OWED_IN_USD': 0,
            }
        }
    }), encoding='utf-8')

    full_config, portfolio_config = load_portfolio_config(str(settings), cli_portfolio_key='test')
    with pytest.raises(RebalError, match="missing the required column"):
        run_rebalance(
            export_path=str(pos_csv),
            targets_file=str(alloc),
            pct_of_max_file=str(pct),
            full_config=full_config,
            portfolio_config=portfolio_config,
        )


def test_parse_portfolio_export_direct_custom(tmp_path):
    """Direct test of parse_portfolio_export with custom columns."""
    header = 'Foo,Bar'
    rows = ['AAA,12345.67', 'BBB,9876.54']
    pos_csv = _write_custom_positions_csv(tmp_path, header, rows)

    df = parse_portfolio_export(
        str(pos_csv),
        None,
        symbol_col='Foo',
        value_col='Bar',
    )
    assert list(df.columns) == ['Ticker', 'Current_Value']
    assert list(df['Ticker']) == ['AAA', 'BBB']
    assert df['Current_Value'].iloc[0] == pytest.approx(12345.67)


# --- More explicit happy-path and failure-case tests for column config + positions_df ---

def test_load_portfolio_config_sets_column_defaults_when_absent():
    """load_portfolio_config should set defaults if keys absent in settings."""
    import json, tempfile, os
    with tempfile.TemporaryDirectory() as tmp:
        settings_path = os.path.join(tmp, 's.json')
        with open(settings_path, 'w') as f:
            json.dump({
                'default_portfolio': 'test',
                'portfolios': {
                    'test': {
                        'display_name': 'T',
                        'ACCOUNT_FILTER': {'column': 'A', 'value': 'V'},
                        'FILE_PATTERN': 'x.csv',
                        # deliberately no SYMBOL/VALUE
                        'CASH_POOL_TICKERS': [],
                        'CASH_FOR_BILLS_IN_USD': 0,
                        'TAX_OWED_IN_USD': 0,
                    }
                }
            }, f)
        full, port = load_portfolio_config(settings_path, 'test')
        assert port['SYMBOL_COLUMN'] == 'Symbol'
        assert port['VALUE_COLUMN'] == 'Current Value'


def test_settings_example_has_symbol_value_columns_in_all_portfolios():
    """settings.example.json must declare SYMBOL_COLUMN and VALUE_COLUMN for every portfolio for consistency and to prevent reserve-parsing bugs."""
    import json
    example_path = os.path.join(os.path.dirname(__file__), '..', 'settings.example.json')
    with open(example_path) as f:
        config = json.load(f)
    for pkey, pconf in config['portfolios'].items():
        assert 'SYMBOL_COLUMN' in pconf, f"Missing SYMBOL_COLUMN in portfolio {pkey} in settings.example.json"
        assert 'VALUE_COLUMN' in pconf, f"Missing VALUE_COLUMN in portfolio {pkey} in settings.example.json"


def test_parse_portfolio_export_no_account_filter_custom_cols(tmp_path):
    """parse without ACCOUNT_FILTER, using custom columns, produces correct normalized df."""
    header = 'Sym,Val'
    rows = ['XXX,123.45', 'YYY,678.90']
    csvf = _write_custom_positions_csv(tmp_path, header, rows)
    df = parse_portfolio_export(str(csvf), None, symbol_col='Sym', value_col='Val')
    assert list(df['Ticker']) == ['XXX', 'YYY']
    assert df['Current_Value'].tolist() == pytest.approx([123.45, 678.90])


def test_run_rebalance_export_path_custom_columns_verify_current_holdings(tmp_path):
    """Full run with export_path + custom columns; assert specific holdings (not just total)."""
    header = 'Acct,Tkr,Val'
    rows = ['F,AAA,"60000"', 'F,BBB,"40000"']
    pos = _write_custom_positions_csv(tmp_path, header, rows)
    alloc = tmp_path / 'a.csv'
    alloc.write_text('Asset_Type,Ticker,Max_Allocation_Pct\nS,AAA,60\nS,BBB,40\n', encoding='utf-8')
    pct = tmp_path / 'p.csv'
    pct.write_text('Asset_Type,Pct_of_Max\nS,100\n', encoding='utf-8')
    settings = tmp_path / 's.json'
    import json
    settings.write_text(json.dumps({
        'default_portfolio': 't',
        'portfolios': {'t': {
            'display_name': 'T', 'ACCOUNT_FILTER': {'column': 'Acct', 'value': 'F'},
            'FILE_PATTERN': 'x.csv', 'SYMBOL_COLUMN': 'Tkr', 'VALUE_COLUMN': 'Val',
            'CASH_POOL_TICKERS': [], 'CASH_FOR_BILLS_IN_USD': 0, 'TAX_OWED_IN_USD': 0
        }}
    }), encoding='utf-8')
    fc, pc = load_portfolio_config(str(settings), 't')
    res = run_rebalance(export_path=str(pos), targets_file=str(alloc), pct_of_max_file=str(pct), full_config=fc, portfolio_config=pc)
    cur = res.df_current_portfolio
    assert set(cur['Ticker_Owned']) == {'AAA', 'BBB'}
    vals = dict(zip(cur['Ticker_Owned'], cur['Current_USD']))
    assert vals['AAA'] == pytest.approx(60000)
    assert vals['BBB'] == pytest.approx(40000)


def test_run_rebalance_positions_df_raw_custom_remap_and_holdings(tmp_path):
    """positions_df with raw custom-named columns (config drives rename); verify holdings."""
    df_raw = pd.DataFrame({
        'A': ['F', 'F'],
        'Tk': ['AAA', 'BBB'],
        'Vv': [60000.0, 40000.0]
    })
    alloc = tmp_path / 'a.csv'
    alloc.write_text('Asset_Type,Ticker,Max_Allocation_Pct\nS,AAA,60\nS,BBB,40\n', encoding='utf-8')
    pct = tmp_path / 'p.csv'
    pct.write_text('Asset_Type,Pct_of_Max\nS,100\n', encoding='utf-8')
    settings = tmp_path / 's.json'
    import json
    settings.write_text(json.dumps({
        'default_portfolio': 't',
        'portfolios': {'t': {
            'display_name': 'T', 'ACCOUNT_FILTER': {'column': 'A', 'value': 'F'},
            'FILE_PATTERN': 'x', 'SYMBOL_COLUMN': 'Tk', 'VALUE_COLUMN': 'Vv',
            'CASH_POOL_TICKERS': [], 'CASH_FOR_BILLS_IN_USD': 0, 'TAX_OWED_IN_USD': 0
        }}
    }), encoding='utf-8')
    fc, pc = load_portfolio_config(str(settings), 't')
    res = run_rebalance(positions_df=df_raw, targets_file=str(alloc), pct_of_max_file=str(pct), full_config=fc, portfolio_config=pc)
    cur = res.df_current_portfolio.set_index('Ticker_Owned')['Current_USD']
    assert cur['AAA'] == pytest.approx(60000)
    assert cur['BBB'] == pytest.approx(40000)


def test_run_rebalance_positions_df_pre_normalized_ignores_config_columns(tmp_path):
    """Pre-normalized positions_df should be used as-is even if config columns differ."""
    df_norm = pd.DataFrame({'Ticker': ['AAA'], 'Current_Value': [12345.0]})
    alloc = tmp_path / 'a.csv'
    alloc.write_text('Asset_Type,Ticker,Max_Allocation_Pct\nS,AAA,100\n', encoding='utf-8')
    pct = tmp_path / 'p.csv'
    pct.write_text('Asset_Type,Pct_of_Max\nS,100\n', encoding='utf-8')
    settings = tmp_path / 's.json'
    import json
    settings.write_text(json.dumps({
        'default_portfolio': 't',
        'portfolios': {'t': {
            'display_name': 'T', 'ACCOUNT_FILTER': {'column': 'x', 'value': 'y'},
            'FILE_PATTERN': 'x', 'SYMBOL_COLUMN': 'no', 'VALUE_COLUMN': 'nope',
            'CASH_POOL_TICKERS': [], 'CASH_FOR_BILLS_IN_USD': 0, 'TAX_OWED_IN_USD': 0
        }}
    }), encoding='utf-8')
    fc, pc = load_portfolio_config(str(settings), 't')
    res = run_rebalance(positions_df=df_norm, targets_file=str(alloc), pct_of_max_file=str(pct), full_config=fc, portfolio_config=pc)
    assert res.df_current_portfolio.iloc[0]['Current_USD'] == pytest.approx(12345)


def test_ignore_portfolio_tickers_display_and_trades(tmp_path):
    """Test all combinations of ignore list membership and presence in portfolio input.

    Cases covered (per requirements):
    - ignored but not in portfolio -> not in owned, not in buy/sell
    - ignored and in portfolio -> in owned as 'IGNORED', not in buy/sell
    - not ignored and in portfolio -> in owned with numeric USD, may appear in trades
    - not ignored and not in portfolio -> not in owned, not in buy/sell
    """
    import json

    # Settings: define ignore list, no cash pool or reserves for simple calcs
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({
        'default_portfolio': 't',
        'portfolios': {
            't': {
                'display_name': 'Test',
                'IGNORE_PORTFOLIO_TICKERS': ['LTCG', 'STCG'],
                'CASH_POOL_TICKERS': [],
                'CASH_FOR_BILLS_IN_USD': 0,
                'TAX_OWED_IN_USD': 0,
            }
        }
    }), encoding='utf-8')

    # Alloc: 60/40 split so imbalanced positions will generate trades for non-ignored.
    # We also include ignored tickers with 0% (sum still 100) to ensure the
    # "0 dollar" version from targets does not leak into the owned list.
    alloc = tmp_path / 'alloc.csv'
    alloc.write_text(
        'Asset_Type,Ticker,Max_Allocation_Pct\n'
        'Stocks,AAA,60\nStocks,BBB,40\n'
        'Ignored,LTCG,0\nIgnored,STCG,0\n',
        encoding='utf-8'
    )
    pct = tmp_path / 'pct.csv'
    pct.write_text('Asset_Type,Pct_of_Max\nStocks,100\nIgnored,0\n', encoding='utf-8')

    fc, pc = load_portfolio_config(str(settings), 't')

    # Positions contain:
    # AAA (not ignored, in portfolio) -> numeric in owned, in trades
    # BBB (not ignored, in portfolio) -> numeric in owned, in trades
    # LTCG (ignored, in portfolio) -> 'IGNORED' in owned, not in trades
    # (STCG is ignored but NOT in this portfolio)
    pos_df = pd.DataFrame({
        'Ticker': ['AAA', 'BBB', 'LTCG'],
        'Current_Value': [10000.0, 2000.0, 3000.0],
    })

    res = run_rebalance(
        positions_df=pos_df,
        targets_file=str(alloc),
        pct_of_max_file=str(pct),
        full_config=fc,
        portfolio_config=pc,
    )

    # Owned list checks
    cur = res.df_current_portfolio.set_index('Ticker_Owned')['Current_USD']
    owned_tickers = set(cur.index)

    # No duplicate tickers should ever appear in the owned list
    assert res.df_current_portfolio['Ticker_Owned'].value_counts().max() <= 1

    # Case: not ignored + in portfolio
    assert 'AAA' in owned_tickers
    assert cur['AAA'] == pytest.approx(10000.0)
    assert 'BBB' in owned_tickers
    assert cur['BBB'] == pytest.approx(2000.0)

    # Case: ignored + in portfolio -> only the IGNORED version, never a 0-dollar version
    ltcg_rows = res.df_current_portfolio[res.df_current_portfolio['Ticker_Owned'] == 'LTCG']
    assert len(ltcg_rows) == 1
    assert ltcg_rows.iloc[0]['Current_USD'] == 'IGNORED'
    assert 'LTCG' in owned_tickers
    assert cur['LTCG'] == 'IGNORED'

    # Case: ignored + not in portfolio (STCG) -> must not appear (even though we put 0% in alloc)
    stcg_rows = res.df_current_portfolio[res.df_current_portfolio['Ticker_Owned'] == 'STCG']
    assert len(stcg_rows) == 0
    assert 'STCG' not in owned_tickers

    # Case: not ignored + not in portfolio (arbitrary XXX not present anywhere)
    assert 'XXX' not in owned_tickers

    # Total should exclude ignored value (LTCG 3000)
    assert res.total_portfolio_value == pytest.approx(12000.0)

    # Trades checks
    sell_tickers = set(res.trades_sell['Ticker']) if not res.trades_sell.empty else set()
    buy_tickers = set(res.trades_buy['Ticker']) if not res.trades_buy.empty else set()
    all_trade_tickers = sell_tickers | buy_tickers

    # Ignored ticker in portfolio must NOT generate trades
    assert 'LTCG' not in all_trade_tickers
    assert 'STCG' not in all_trade_tickers

    # Not-ignored in portfolio that are imbalanced DO generate trades
    assert 'AAA' in sell_tickers
    assert 'BBB' in buy_tickers

    # Not ignored not in portfolio does not generate trades
    assert 'XXX' not in all_trade_tickers

    # Net trades still zero
    all_trades = pd.concat([res.trades_sell, res.trades_buy], ignore_index=True) if not (res.trades_sell.empty and res.trades_buy.empty) else pd.DataFrame()
    if not all_trades.empty:
        assert abs(all_trades['Trade_Amount_USD'].sum()) < 0.02

    # Also exercise the CSV/export_path path (parse_portfolio_export + capture)
    csv_path = tmp_path / 'positions.csv'
    csv_path.write_text(
        'Symbol,Current Value\nAAA,"10000"\nBBB,"2000"\nLTCG,"3000"\n',
        encoding='utf-8'
    )
    res_csv = run_rebalance(
        export_path=str(csv_path),
        targets_file=str(alloc),
        pct_of_max_file=str(pct),
        full_config=fc,
        portfolio_config=pc,
    )
    cur_csv = res_csv.df_current_portfolio.set_index('Ticker_Owned')['Current_USD']
    owned_csv = set(cur_csv.index)
    # No duplicates in CSV path either
    assert res_csv.df_current_portfolio['Ticker_Owned'].value_counts().max() <= 1
    assert 'AAA' in owned_csv and cur_csv['AAA'] == pytest.approx(10000)
    ltcg_csv = res_csv.df_current_portfolio[res_csv.df_current_portfolio['Ticker_Owned'] == 'LTCG']
    assert len(ltcg_csv) == 1 and ltcg_csv.iloc[0]['Current_USD'] == 'IGNORED'
    stcg_csv = res_csv.df_current_portfolio[res_csv.df_current_portfolio['Ticker_Owned'] == 'STCG']
    assert len(stcg_csv) == 0
    assert 'STCG' not in owned_csv
    assert 'XXX' not in owned_csv
    sell_csv = set(res_csv.trades_sell['Ticker']) if not res_csv.trades_sell.empty else set()
    buy_csv = set(res_csv.trades_buy['Ticker']) if not res_csv.trades_buy.empty else set()
    assert 'LTCG' not in (sell_csv | buy_csv)
    assert 'AAA' in sell_csv and 'BBB' in buy_csv

    # When an ignored ticker has positive allocation in the alloc file
    # (making non-ignored sum != 100), it must be a halting error with specific message.
    bad_alloc = tmp_path / 'bad_alloc.csv'
    bad_alloc.write_text(
        'Asset_Type,Ticker,Max_Allocation_Pct\nStocks,AAA,55\nStocks,BBB,40\nStocks,LTCG,5\n',
        encoding='utf-8'
    )
    with pytest.raises(RebalError, match='CANNOT IGNORE TICKER REQUIRED FOR ALLOCATION SUM'):
        run_rebalance(
            positions_df=pos_df,
            targets_file=str(bad_alloc),
            pct_of_max_file=str(pct),
            full_config=fc,
            portfolio_config=pc,
        )


def test_run_rebalance_no_positions_source_raises():
    """Calling without export_path and without positions_df raises clear error."""
    full, pc = _load_fixture_config()
    with pytest.raises(RebalError, match="No positions provided"):
        run_rebalance(
            targets_file=fixture_path('alloc.test.csv'),
            pct_of_max_file=fixture_path('pct_of_max_alloc.csv'),
            full_config=full,
            portfolio_config=pc,
        )


def test_parse_missing_configured_value_column_error_mentions_name(tmp_path):
    """Error message for missing value column should mention the configured name."""
    pos = _write_custom_positions_csv(tmp_path, 'Sym,Other', ['AAA,1'])
    with pytest.raises(RebalError, match="MyVal"):
        parse_portfolio_export(str(pos), None, symbol_col='Sym', value_col='MyVal')


def test_positions_df_after_normalize_missing_current_value_path(tmp_path):
    """Exercise the path where positions_df has no usable value column (leads to later failure)."""
    df = pd.DataFrame({'SomeCol': ['AAA']})  # no value info
    alloc = tmp_path / 'a.csv'
    alloc.write_text('Asset_Type,Ticker,Max_Allocation_Pct\nS,AAA,100\n', encoding='utf-8')
    pct = tmp_path / 'p.csv'
    pct.write_text('Asset_Type,Pct_of_Max\nS,100\n', encoding='utf-8')
    settings = tmp_path / 's.json'
    import json
    settings.write_text(json.dumps({
        'default_portfolio': 't',
        'portfolios': {'t': {'display_name': 'T', 'ACCOUNT_FILTER': {'column': 'x', 'value': 'y'},
            'FILE_PATTERN': 'x', 'SYMBOL_COLUMN': 'no', 'VALUE_COLUMN': 'no',
            'CASH_POOL_TICKERS': [], 'CASH_FOR_BILLS_IN_USD': 0, 'TAX_OWED_IN_USD': 0}}
    }), encoding='utf-8')
    fc, pc = load_portfolio_config(str(settings), 't')
    # This will hit normalization that doesn't create Current_Value, then fail in _compute_cash_values
    with pytest.raises(KeyError, match="Ticker"):
        run_rebalance(positions_df=df, targets_file=str(alloc), pct_of_max_file=str(pct), full_config=fc, portfolio_config=pc)


def test_custom_columns_non_empty_cash_pool(tmp_path):
    """Custom columns + non-empty cash pool (exercises cash value calc with custom parse)."""
    header = 'A,S,V'
    rows = ['F,AAA,"50000"', 'F,SPAXX**,"10000"']  # SPAXX will be cash
    pos = _write_custom_positions_csv(tmp_path, header, rows)
    alloc = tmp_path / 'a.csv'
    alloc.write_text('Asset_Type,Ticker,Max_Allocation_Pct\nS,AAA,100\n', encoding='utf-8')
    pct = tmp_path / 'p.csv'
    pct.write_text('Asset_Type,Pct_of_Max\nS,100\n', encoding='utf-8')
    settings = tmp_path / 's.json'
    import json
    settings.write_text(json.dumps({
        'default_portfolio': 't',
        'portfolios': {'t': {
            'display_name': 'T', 'ACCOUNT_FILTER': {'column': 'A', 'value': 'F'},
            'FILE_PATTERN': 'x', 'SYMBOL_COLUMN': 'S', 'VALUE_COLUMN': 'V',
            'CASH_POOL_TICKERS': ['SPAXX**'], 'CASH_FOR_BILLS_IN_USD': 0, 'TAX_OWED_IN_USD': 0
        }}
    }), encoding='utf-8')
    fc, pc = load_portfolio_config(str(settings), 't')
    res = run_rebalance(export_path=str(pos), targets_file=str(alloc), pct_of_max_file=str(pct), full_config=fc, portfolio_config=pc)
    # Invested 50k, cash 10k, total 60k, no reserve
    assert res.total_portfolio_value == pytest.approx(60000.0)
    assert res.cash_reserve_usd == 0


def test_parse_value_without_dollar_sign(tmp_path):
    """Value column with plain numbers (no $ or commas) still parses correctly."""
    header = 'S,V'
    rows = ['AAA,12345.67']
    pos = _write_custom_positions_csv(tmp_path, header, rows)
    df = parse_portfolio_export(str(pos), None, 'S', 'V')
    assert df['Current_Value'].iloc[0] == pytest.approx(12345.67)
