"""Core rebalancing logic (no console output)."""

from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

DEFAULT_BILLS_USD = 10000.0
DEFAULT_BILLS_MONTHS = 3.0
DEFAULT_TAX_OWED_USD = 0.0

FIDELITY_CASH_SYMBOLS = [
    'SPAXX**', 'SHV', 'USFR', 'BIL', 'SGOV', 'Pending activity', 'USD***',
]
INVESTED_CASH_TICKER = 'INVESTED_CASH'

NON_RESERVE_KEYS = {
    'ACCOUNT_FILTER', 'FILE_PATTERN', 'display_name', 'portfolio_key',
    'portfolios', 'default_portfolio', 'TARGETS_FILE',
}

REQUIRED_ALLOC_COLS = ['Asset_Type', 'Ticker', 'Max_Allocation_Pct']
REQUIRED_PCT_COLS = ['Asset_Type', 'Pct_of_Max']
REQUIRED_RESERVE_KEYS = [
    'BILLS_PER_MONTH_IN_USD', 'CASH_FOR_BILLS_IN_MONTHS', 'TAX_OWED_IN_USD',
]

SPECIAL_RESERVE_TICKERS = [
    'CASH_RESERVE_USD', 'BILLS_PER_MONTH_IN_USD',
    'CASH_FOR_BILLS_IN_MONTHS', 'TAX_OWED_IN_USD',
]
TICKERS_TO_EXCLUDE_INVESTED = [
    'CASH', 'CASH_RESERVE_USD', 'BILLS_PER_MONTH_IN_USD',
    'CASH_FOR_BILLS_IN_MONTHS', 'TAX_OWED_IN_USD',
]
IGNORE_PORTFOLIO_TICKERS = ['LTCG', 'STCG', 'INCOME']


class RebalError(Exception):
    """Validation or data error; message is suitable for printing to the user."""


@dataclass
class RebalanceResult:
    display_name: str
    export_path: str
    account_filter: str | None
    default_messages: list[str]
    df_target_summary: pd.DataFrame
    bills_per_month_usd: float
    cash_for_bills_months: float
    tax_owed_usd: float
    cash_reserve_usd: float
    total_portfolio_value: float
    df_current_portfolio: pd.DataFrame
    trades_sell: pd.DataFrame
    trades_buy: pd.DataFrame
    ticker_w_trade: int


def load_settings_file(settings_path: str) -> dict[str, Any]:
    try:
        with open(settings_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        raise RebalError(
            f"\n*** ERROR: SETTINGS FILE NOT FOUND ***\n"
            f"Required file '{settings_path}' not found."
        ) from None
    except json.JSONDecodeError as e:
        raise RebalError(
            f"\n*** ERROR: INVALID JSON FORMAT ***\n"
            f"Error reading '{settings_path}': {e}"
        ) from None
    except OSError as e:
        raise RebalError(
            f"\n*** ERROR LOADING SETTINGS ***\n"
            f"An unexpected error occurred while loading '{settings_path}': {e}"
        ) from None


def resolve_portfolio_key(
    config: dict[str, Any],
    cli_portfolio_key: str | None = None,
) -> str:
    if cli_portfolio_key is not None:
        return cli_portfolio_key.strip()
    return config.get('default_portfolio', 'fidelity')


def load_portfolio_config(
    settings_path: str,
    cli_portfolio_key: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (full_config, portfolio_config) for the selected portfolio."""
    config = load_settings_file(settings_path)
    portfolio_key = resolve_portfolio_key(config, cli_portfolio_key)

    if not re.match(r'^[a-zA-Z0-9_-]+$', portfolio_key):
        raise RebalError(
            f"\n*** ERROR: INVALID PORTFOLIO KEY '{portfolio_key}' ***\n"
            "Portfolio keys may only contain letters, numbers, hyphens (-) and underscores (_).\n"
            "No spaces, periods, or other special characters are allowed.\n"
            "Example valid keys: fidelity, fidelity-crypto, coinbase-btc"
        )

    if 'portfolios' in config and isinstance(config['portfolios'], dict):
        portfolios = config['portfolios']
        if portfolio_key not in portfolios:
            lines = [
                f"\n*** ERROR: PORTFOLIO NOT FOUND ***",
                f"Portfolio key '{portfolio_key}' not found in {os.path.basename(settings_path)}.",
                "Available portfolios:",
            ]
            for k, v in portfolios.items():
                display = v.get('display_name', k)
                lines.append(f"  - {k}  →  {display}")
            lines.append("\nUsage: python rebal_kiss.py <portfolio_key>")
            lines.append('       (or set "default_portfolio" in kiss_settings.json)')
            raise RebalError('\n'.join(lines))
        portfolio_config = dict(portfolios[portfolio_key])
        portfolio_config['portfolio_key'] = portfolio_key
    else:
        portfolio_config = dict(config)
        portfolio_config['portfolio_key'] = 'fidelity'
        portfolio_config.setdefault('display_name', 'Fidelity Kiss Portfolio')

    portfolio_config['TARGETS_FILE'] = f"kiss_alloc.{portfolio_key}.csv"
    portfolio_config.setdefault('FILE_PATTERN', 'Portfolio_Positions_*.csv')
    portfolio_config.setdefault('display_name', portfolio_key)
    return config, portfolio_config


def resolve_data_path(
    filename: str,
    *,
    settings_path: str | None = None,
) -> str:
    """Resolve a config CSV: cwd first, then directory containing settings."""
    if os.path.isabs(filename):
        return filename
    if os.path.isfile(filename):
        return os.path.abspath(filename)
    if settings_path:
        settings_dir = os.path.dirname(os.path.abspath(settings_path))
        candidate = os.path.join(settings_dir, os.path.basename(filename))
        if os.path.isfile(candidate):
            return candidate
    return filename


def _newest_file_matching(directory: str, file_pattern: str) -> str | None:
    candidates = glob.glob(os.path.join(directory, file_pattern))
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def resolve_positions_path(positions: str, file_pattern: str) -> str | None:
    """Resolve --positions: a CSV file, or newest match in a directory."""
    path = os.path.expanduser(positions)
    if os.path.isfile(path):
        return os.path.abspath(path)
    if os.path.isdir(path):
        chosen = _newest_file_matching(path, file_pattern)
        return os.path.abspath(chosen) if chosen else None
    return None


def find_export_file(
    file_pattern: str,
    downloads_path: str | None = None,
    search_cwd: bool = True,
) -> str | None:
    downloads_path = downloads_path or os.path.expanduser('~/Downloads/')
    chosen = _newest_file_matching(downloads_path, file_pattern)
    if chosen:
        return chosen
    if search_cwd:
        return _newest_file_matching('.', file_pattern)
    return None


def load_targets_raw(targets_file: str) -> pd.DataFrame:
    try:
        df = pd.read_csv(targets_file)
    except FileNotFoundError:
        raise RebalError(
            f"\n*** ERROR: ALLOC FILE NOT FOUND ***\n"
            f"Required file '{targets_file}' not found."
        ) from None
    except OSError as e:
        raise RebalError(
            f"\n*** ERROR LOADING ALLOC FILE ***\n"
            f"Error loading '{targets_file}': {e}"
        ) from None

    if df.empty:
        raise RebalError(
            f"\n*** ERROR: ALLOC FILE IS EMPTY! ***\n"
            f"'{targets_file}' is completely empty."
        )
    missing = [c for c in REQUIRED_ALLOC_COLS if c not in df.columns]
    if missing:
        raise RebalError(
            f"\n*** ERROR: {targets_file} MISSING COLUMNS ***\n"
            f"Missing: {', '.join(missing)}\n"
            f"Expected: {', '.join(REQUIRED_ALLOC_COLS)}"
        )

    df = df.copy()
    df['Ticker'] = df['Ticker'].str.upper()
    df['Asset_Type'] = df['Asset_Type'].str.strip()

    max_pct_sum = df['Max_Allocation_Pct'].sum()
    if abs(max_pct_sum - 100.0) > 1e-6:
        raise RebalError(
            f"\n*** ERROR: ALLOCATION SUM VIOLATION IN {targets_file} ***\n"
            f"Sum of Max_Allocation_Pct = {max_pct_sum:.4f}\n"
            "The sum must be exactly 100.0 for every portfolio."
        )
    return df


def load_pct_of_max(pct_of_max_file: str) -> pd.DataFrame:
    try:
        df = pd.read_csv(pct_of_max_file)
    except FileNotFoundError:
        raise RebalError(
            f"\n*** ERROR: GLOBAL PCT_OF_MAX FILE NOT FOUND ***"
        ) from None
    except OSError as e:
        raise RebalError(
            f"\n*** ERROR LOADING GLOBAL PCT_OF_MAX ***\n"
            f"Error loading '{pct_of_max_file}': {e}"
        ) from None

    if df.empty:
        raise RebalError("\n*** ERROR: GLOBAL PCT_OF_MAX FILE IS EMPTY! ***")
    missing = [c for c in REQUIRED_PCT_COLS if c not in df.columns]
    if missing:
        raise RebalError(f"\n*** ERROR: {pct_of_max_file} MISSING COLUMNS ***")

    df = df.copy()
    df['Asset_Type'] = df['Asset_Type'].str.strip()
    return df


def merge_targets_with_pct(
    df_targets_raw: pd.DataFrame,
    df_pct_of_max: pd.DataFrame,
    targets_file: str,
    pct_of_max_file: str,
) -> pd.DataFrame:
    alloc_types = set(df_targets_raw['Asset_Type'].dropna().unique())
    pct_types = set(df_pct_of_max['Asset_Type'].dropna().unique())
    undefined = alloc_types - pct_types
    if undefined:
        lines = [
            f"\n*** ERROR: UNDEFINED ASSET TYPES IN '{targets_file}' ***",
            f"The following Asset_Type values have no matching entry "
            f"in the global '{pct_of_max_file}':",
        ]
        for at in sorted(undefined):
            lines.append(f"  - {at}")
        raise RebalError('\n'.join(lines))

    df = pd.merge(
        df_targets_raw,
        df_pct_of_max[['Asset_Type', 'Pct_of_Max']],
        on='Asset_Type',
        how='left',
    )
    df['Value'] = 0.0
    return df


def _parse_reserve_value(value: Any, ticker: str, display_name: str) -> float:
    if isinstance(value, str):
        try:
            cleaned = value.strip().replace('$', '').replace(',', '')
            return float(cleaned)
        except ValueError:
            raise RebalError(
                f"\n*** ERROR: Invalid value format for '{ticker}' "
                f"in portfolio '{display_name}'. ***"
            ) from None
    try:
        return float(value)
    except (ValueError, TypeError):
        raise RebalError(
            f"\n*** ERROR: Invalid value type for '{ticker}' "
            f"in portfolio '{display_name}'. ***"
        ) from None


def append_reserve_rows(
    df_targets: pd.DataFrame,
    full_config: dict[str, Any],
    portfolio_config: dict[str, Any],
) -> pd.DataFrame:
    display_name = portfolio_config.get(
        'display_name', portfolio_config['portfolio_key'],
    )
    pkey = portfolio_config['portfolio_key']
    if 'portfolios' in full_config and pkey in full_config['portfolios']:
        reserve_settings = full_config['portfolios'][pkey]
    else:
        reserve_settings = full_config

    if not all(k in reserve_settings for k in REQUIRED_RESERVE_KEYS):
        raise RebalError(
            f"\n*** ERROR: MISSING REQUIRED KEYS IN PORTFOLIO '{display_name}' ***"
        )

    reserve_rows = []
    for ticker, value in reserve_settings.items():
        if ticker in NON_RESERVE_KEYS:
            continue
        reserve_rows.append({
            'Asset_Type': 'RESERVE',
            'Ticker': ticker.upper(),
            'Max_Allocation_Pct': 0.0,
            'Pct_of_Max': 0.0,
            'Value': _parse_reserve_value(value, ticker, display_name),
        })

    df_reserves = pd.DataFrame(reserve_rows)
    return pd.concat([df_targets, df_reserves], ignore_index=True)


def normalize_targets(df_targets: pd.DataFrame) -> pd.DataFrame:
    df = df_targets.copy()
    for col in ['Max_Allocation_Pct', 'Pct_of_Max', 'Value']:
        if col not in df.columns:
            raise RebalError("\n*** INTERNAL ERROR: MISSING COLUMN AFTER MERGE! ***")
    df['Max_Allocation_Pct'] = pd.to_numeric(
        df['Max_Allocation_Pct'], errors='coerce',
    ).fillna(0.0)
    df['Pct_of_Max'] = pd.to_numeric(df['Pct_of_Max'], errors='coerce').fillna(0.0)
    df['Value'] = pd.to_numeric(df['Value'], errors='coerce').fillna(0.0)
    return df


def parse_portfolio_export(
    export_path: str,
    account_filter: str | None,
) -> pd.DataFrame:
    try:
        df_raw = pd.read_csv(
            export_path,
            skiprows=0,
            index_col=False,
            encoding='utf-8-sig',
            on_bad_lines='skip',
        )
    except FileNotFoundError:
        raise RebalError(
            f"\n*** ERROR: Fidelity export file not found at the determined path: "
            f"'{export_path}' ***"
        ) from None

    df_raw.columns = df_raw.columns.str.strip()
    if 'Account Name' in df_raw.columns:
        df_raw['Account Name'] = df_raw['Account Name'].astype(str).str.strip()

    if account_filter:
        df_filtered = df_raw[df_raw['Account Name'] == account_filter].copy()
    else:
        df_filtered = df_raw.copy()

    if 'Current Value' not in df_filtered.columns:
        raise RebalError(
            "\n*** ERROR: Fidelity export file is missing the required column "
            "'Current Value'. ***"
        )

    df_filtered['Current Value'] = pd.to_numeric(
        df_filtered['Current Value'].astype(str)
        .str.replace('$', '', regex=False)
        .str.replace(',', '', regex=False),
        errors='coerce',
    ).fillna(0.0)

    try:
        df_clean = df_filtered[['Symbol', 'Current Value']].copy()
    except KeyError as e:
        raise RebalError(
            f"\n*** ERROR: Fidelity export file is missing the required column {e}. ***"
        ) from None

    df_clean.rename(
        columns={'Symbol': 'Ticker', 'Current Value': 'Current_Value'},
        inplace=True,
    )
    df_clean['Ticker'] = df_clean['Ticker'].str.upper()
    return df_clean[~df_clean['Ticker'].isin(IGNORE_PORTFOLIO_TICKERS)].copy()


def get_reserve_value(
    df: pd.DataFrame,
    ticker: str,
    default_value: float,
    default_message_format: str,
) -> tuple[float, str | None]:
    row = df[df['Ticker'] == ticker]
    if row.empty:
        return default_value, (
            f"Note: '{ticker}' not found. Using default value: {default_message_format}"
        )
    value = row['Value'].iloc[0]
    if pd.isna(value):
        return default_value, (
            f"Note: '{ticker}' found but 'Value' is empty/NaN. "
            f"Using default value: {default_message_format}"
        )
    return float(value), None


def validate_cash_and_invested_alloc(df_targets: pd.DataFrame) -> None:
    cash_max_row = df_targets[df_targets['Ticker'] == 'CASH']
    if not cash_max_row.empty:
        cash_max_pct = cash_max_row['Max_Allocation_Pct'].iloc[0]
        if cash_max_pct != 100.0:
            raise RebalError(
                "\n*** ERROR: CASH ALLOCATION CONSTRAINT VIOLATION! ***\n"
                "The 'Max_Allocation_Pct' for CASH must be 100.0."
            )

    df_invested = df_targets[~df_targets['Ticker'].isin(TICKERS_TO_EXCLUDE_INVESTED)].copy()
    max_pct_sum = df_invested['Max_Allocation_Pct'].sum()
    if abs(max_pct_sum - 100.0) > 1e-6:
        raise RebalError(
            "\n*** ERROR: MAX ALLOCATION SUM VIOLATION! ***\n"
            "The sum of 'Max_Allocation_Pct' for all non-CASH assets must be 100.0."
        )


def sort_trades_by_off_ratio(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = df.copy()
    out['is_invested_cash'] = out['Ticker'] == INVESTED_CASH_TICKER
    cash_rows = out[out['is_invested_cash']].copy()
    non_cash = out[~out['is_invested_cash']].copy()

    tier1 = non_cash[
        (non_cash['Target_Allocation'] == 0) & (non_cash['Off_Pct'] != 0)
    ].sort_values('Off_Pct', ascending=False)
    tier2 = non_cash[non_cash['Target_Allocation'] != 0].sort_values(
        'Off_Ratio', ascending=False,
    )
    tier3 = non_cash[
        (non_cash['Target_Allocation'] == 0) & (non_cash['Off_Pct'] == 0)
    ].sort_values('Ticker', ascending=True)

    sorted_non_cash = pd.concat([tier1, tier2, tier3], ignore_index=True)
    if not cash_rows.empty:
        sorted_df = pd.concat([sorted_non_cash, cash_rows], ignore_index=True)
    else:
        sorted_df = sorted_non_cash
    return sorted_df.drop(columns=['is_invested_cash'], errors='ignore')


def run_rebalance(
    *,
    export_path: str,
    targets_file: str,
    pct_of_max_file: str,
    full_config: dict[str, Any],
    portfolio_config: dict[str, Any],
) -> RebalanceResult:
    """Compute rebalance trades from config files and a position export."""
    display_name = portfolio_config.get(
        'display_name', portfolio_config['portfolio_key'],
    )
    account_filter = portfolio_config.get('ACCOUNT_FILTER')

    df_targets_raw = load_targets_raw(targets_file)
    df_pct_of_max = load_pct_of_max(pct_of_max_file)
    df_targets = merge_targets_with_pct(
        df_targets_raw, df_pct_of_max, targets_file, pct_of_max_file,
    )
    df_targets = append_reserve_rows(df_targets, full_config, portfolio_config)
    df_targets = normalize_targets(df_targets)
    df_portfolio = parse_portfolio_export(export_path, account_filter)

    validate_cash_and_invested_alloc(df_targets)

    df_targets['Target_Allocation'] = (
        df_targets['Max_Allocation_Pct'] * (df_targets['Pct_of_Max'] / 100.0)
    )
    df_target_summary = df_targets[
        ['Asset_Type', 'Ticker', 'Max_Allocation_Pct', 'Pct_of_Max', 'Target_Allocation']
    ].copy()
    df_target_summary.rename(
        columns={'Target_Allocation': 'Target_Percent', 'Ticker': 'Ticker_Target'},
        inplace=True,
    )
    df_target_summary['Ticker_Target'] = df_target_summary['Ticker_Target'].replace(
        {'CASH': INVESTED_CASH_TICKER},
    )

    default_messages: list[str] = []
    bills, msg = get_reserve_value(
        df_targets, 'BILLS_PER_MONTH_IN_USD', DEFAULT_BILLS_USD,
        f"${DEFAULT_BILLS_USD:,.2f}",
    )
    if msg:
        default_messages.append(msg)
    months, msg = get_reserve_value(
        df_targets, 'CASH_FOR_BILLS_IN_MONTHS', DEFAULT_BILLS_MONTHS,
        f"{DEFAULT_BILLS_MONTHS:.1f} months",
    )
    if msg:
        default_messages.append(msg)
    tax, msg = get_reserve_value(
        df_targets, 'TAX_OWED_IN_USD', DEFAULT_TAX_OWED_USD,
        f"${DEFAULT_TAX_OWED_USD:,.2f}",
    )
    if msg:
        default_messages.append(msg)

    if bills < 0 or months < 0 or tax < 0:
        raise RebalError("\n*** ERROR: NEGATIVE RESERVE VALUES NOT ALLOWED ***")

    cash_reserve_usd = (bills * months) + tax

    non_cash_sum = df_targets[
        ~df_targets['Ticker'].isin(['CASH'] + SPECIAL_RESERVE_TICKERS)
    ]['Target_Allocation'].sum()
    df_targets.loc[df_targets['Ticker'] == 'CASH', 'Target_Allocation'] = 100.0 - non_cash_sum

    df_targets_alloc = df_targets[
        ~df_targets['Ticker'].isin(SPECIAL_RESERVE_TICKERS)
    ].copy()
    df_target_summary = df_target_summary[
        ~df_target_summary['Ticker_Target'].isin(SPECIAL_RESERVE_TICKERS)
    ].copy()

    cash_symbols_upper = [s.upper() for s in FIDELITY_CASH_SYMBOLS]
    current_cash_value = df_portfolio[
        df_portfolio['Ticker'].isin(cash_symbols_upper)
    ]['Current_Value'].sum()

    if current_cash_value < 0:
        raise RebalError("\n*** ERROR: CONSOLIDATED CASH POOL IS NEGATIVE! ***")

    df_invested_assets = df_portfolio[
        ~df_portfolio['Ticker'].isin(cash_symbols_upper)
    ].copy()

    total_portfolio_value = (
        df_invested_assets['Current_Value'].sum() + current_cash_value
    )
    if cash_reserve_usd > total_portfolio_value:
        raise RebalError("\n*** ERROR: NOT ENOUGH ASSETS FOR CASH RESERVE! ***")

    total_investable = total_portfolio_value - cash_reserve_usd

    cash_row_temp = pd.DataFrame([
        {'Ticker': INVESTED_CASH_TICKER, 'Current_Value': current_cash_value},
    ])
    df_portfolio_rebal = pd.concat([df_invested_assets, cash_row_temp], ignore_index=True)

    df_targets_alloc = df_targets_alloc.copy()
    df_targets_alloc['Ticker'] = df_targets_alloc['Ticker'].replace(
        {'CASH': INVESTED_CASH_TICKER},
    )

    df_rebalance = pd.merge(
        df_portfolio_rebal,
        df_targets_alloc[['Ticker', 'Target_Allocation']],
        on='Ticker',
        how='outer',
    ).fillna(0.0)

    df_rebalance['Target_Allocation_Dec'] = df_rebalance['Target_Allocation'] / 100.0
    df_rebalance['Target_Value'] = (
        df_rebalance['Target_Allocation_Dec'] * total_investable
    )
    df_rebalance['Current_Pct'] = (
        df_rebalance['Current_Value'] / total_investable
    ) * 100.0
    df_rebalance['Trade_Amount_USD'] = (
        df_rebalance['Target_Value'] - df_rebalance['Current_Value']
    )

    df_trades = df_rebalance[df_rebalance['Ticker'] != INVESTED_CASH_TICKER].copy()
    df_trades = df_trades[df_trades['Trade_Amount_USD'].abs() > 0.01]

    net_non_cash = df_trades['Trade_Amount_USD'].sum()
    cash_pool_trade = -net_non_cash
    if abs(cash_pool_trade) > 0.01:
        cash_row_data = df_rebalance[
            df_rebalance['Ticker'] == INVESTED_CASH_TICKER
        ].iloc[0]
        cash_pool_row = pd.DataFrame([{
            'Ticker': INVESTED_CASH_TICKER,
            'Trade_Amount_USD': cash_pool_trade,
            'Target_Allocation': cash_row_data['Target_Allocation'],
            'Current_Pct': cash_row_data['Current_Pct'],
        }])
        df_trades = pd.concat([df_trades, cash_pool_row], ignore_index=True)

    df_trades['Off_Pct'] = abs(
        df_trades['Target_Allocation'] - df_trades['Current_Pct'],
    )
    if INVESTED_CASH_TICKER in df_trades['Ticker'].values:
        df_non_cash = df_trades[df_trades['Ticker'] != INVESTED_CASH_TICKER]
        buys_off = df_non_cash.loc[
            df_non_cash['Trade_Amount_USD'] > 0, 'Off_Pct',
        ].sum()
        sells_off = df_non_cash.loc[
            df_non_cash['Trade_Amount_USD'] < 0, 'Off_Pct',
        ].sum()
        net_off = abs(buys_off - sells_off)
        df_trades.loc[
            df_trades['Ticker'] == INVESTED_CASH_TICKER, 'Off_Pct',
        ] = net_off

    df_trades['Off_Ratio'] = np.where(
        df_trades['Target_Allocation'] > 0,
        df_trades['Off_Pct'] / df_trades['Target_Allocation'],
        0.0,
    )

    if not df_trades.empty:
        ticker_w = max(
            df_trades['Ticker'].apply(len).max(),
            len('Sell_Ticker'),
            len('Buy_Ticker'),
        )
    else:
        ticker_w = max(len('Sell_Ticker'), len('Buy_Ticker'))

    trades_sell = sort_trades_by_off_ratio(
        df_trades[df_trades['Trade_Amount_USD'] < 0].copy(),
    )
    trades_buy = sort_trades_by_off_ratio(
        df_trades[df_trades['Trade_Amount_USD'] > 0].copy(),
    )

    df_current = df_rebalance[['Ticker', 'Current_Value']].copy()
    df_current['is_cash'] = df_current['Ticker'] == INVESTED_CASH_TICKER
    df_current = df_current.sort_values(
        by=['is_cash', 'Ticker'], ascending=[True, True],
    ).drop(columns=['is_cash'])
    df_current.rename(
        columns={'Current_Value': 'Current_USD', 'Ticker': 'Ticker_Owned'},
        inplace=True,
    )

    return RebalanceResult(
        display_name=display_name,
        export_path=export_path,
        account_filter=account_filter,
        default_messages=default_messages,
        df_target_summary=df_target_summary,
        bills_per_month_usd=bills,
        cash_for_bills_months=months,
        tax_owed_usd=tax,
        cash_reserve_usd=cash_reserve_usd,
        total_portfolio_value=total_portfolio_value,
        df_current_portfolio=df_current,
        trades_sell=trades_sell,
        trades_buy=trades_buy,
        ticker_w_trade=ticker_w,
    )
