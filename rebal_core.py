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

DEFAULT_TAX_OWED_USD = 0.0

FIDELITY_CASH_SYMBOLS = [
    'SPAXX**', 'SHV', 'USFR', 'BIL', 'SGOV', 'Pending activity', 'USD***',
]
CASH_META_TICKER = '_CASH'
DEFAULT_SAFE_ASSET = '_CASH'

NON_RESERVE_KEYS = {
    'ACCOUNT_FILTER', 'FILE_PATTERN', 'display_name', 'portfolio_key',
    'portfolios', 'default_portfolio', 'TARGETS_FILE',
    'CASH_FOR_BILLS_SOURCE', 'SAFE_ASSET',
}

REQUIRED_ALLOC_COLS = ['Asset_Type', 'Ticker', 'Max_Allocation_Pct']
REQUIRED_PCT_COLS = ['Asset_Type', 'Pct_of_Max']

SPECIAL_RESERVE_TICKERS = [
    'CASH_RESERVE_USD', 'CASH_FOR_BILLS_IN_USD', 'TAX_OWED_IN_USD',
]
TICKERS_TO_EXCLUDE_INVESTED = [
    'CASH', 'CASH_RESERVE_USD', 'CASH_FOR_BILLS_IN_USD', 'TAX_OWED_IN_USD',
]

# Legacy alias for external code that imported the old name
INVESTED_CASH_TICKER = CASH_META_TICKER
IGNORE_PORTFOLIO_TICKERS = ['LTCG', 'STCG', 'INCOME']


class RebalError(Exception):
    """Validation or data error; message is suitable for printing to the user."""


@dataclass(frozen=True)
class AccountFilter:
    column: str
    value: str

    def describe(self) -> str:
        return f"Filtered: '{self.column}' = '{self.value}'"


def parse_account_filter(raw: Any) -> AccountFilter | None:
    """Parse ACCOUNT_FILTER from settings (object or legacy string)."""
    if raw is None:
        return None
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return None
        return AccountFilter(column='Account Name', value=stripped)
    if isinstance(raw, dict):
        column = raw.get('column') or raw.get('Column')
        value = raw.get('value') or raw.get('Value')
        if not column or value is None:
            raise RebalError(
                "\n*** ERROR: INVALID ACCOUNT_FILTER ***\n"
                "ACCOUNT_FILTER object must include 'column' and 'value', e.g.\n"
                '  "ACCOUNT_FILTER": {"column": "Account Name", "value": "Kiss Portfolio"}'
            )
        value_str = str(value).strip()
        column_str = str(column).strip()
        if not column_str or not value_str:
            raise RebalError(
                "\n*** ERROR: INVALID ACCOUNT_FILTER ***\n"
                "ACCOUNT_FILTER 'column' and 'value' must be non-empty strings."
            )
        return AccountFilter(column=column_str, value=value_str)
    raise RebalError(
        "\n*** ERROR: INVALID ACCOUNT_FILTER ***\n"
        "ACCOUNT_FILTER must be a string or an object with 'column' and 'value'."
    )


@dataclass
class RebalanceResult:
    display_name: str
    export_path: str
    account_filter: AccountFilter | None
    safe_asset_ticker: str
    df_target_summary: pd.DataFrame
    cash_for_bills_usd: float
    cash_for_bills_source: dict | None
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
                lines.append(f"  - {k}  \u2192  {display}")
            lines.append("\nUsage: python rebal.py <portfolio_key>")
            lines.append('       (or set "default_portfolio" in settings.json)')
            raise RebalError('\n'.join(lines))
        portfolio_config = dict(portfolios[portfolio_key])
        portfolio_config['portfolio_key'] = portfolio_key
    else:
        portfolio_config = dict(config)
        portfolio_config['portfolio_key'] = 'fidelity'
        portfolio_config.setdefault('display_name', 'Fidelity Kiss Portfolio')

    portfolio_config['TARGETS_FILE'] = f"alloc.{portfolio_key}.csv"
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


def _try_parse_float(value: Any) -> float | None:
    """Try to parse a value as float, returning None on failure."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            cleaned = value.strip().replace('$', '').replace(',', '')
            if not cleaned:
                return None
            return float(cleaned)
        except ValueError:
            return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _get_reserve_settings(
    full_config: dict[str, Any],
    portfolio_config: dict[str, Any],
) -> dict[str, Any]:
    """Extract the reserve-settings dict for the active portfolio."""
    pkey = portfolio_config['portfolio_key']
    if 'portfolios' in full_config and pkey in full_config['portfolios']:
        return full_config['portfolios'][pkey]
    return full_config


def resolve_cash_for_bills(
    reserve_settings: dict[str, Any],
) -> tuple[float, dict | None]:
    """Resolve cash-for-bills from source or manual value.

    Returns (cash_for_bills_usd, source_dict_or_None).
    """
    source = reserve_settings.get('CASH_FOR_BILLS_SOURCE')
    if isinstance(source, dict):
        bills_val = _try_parse_float(source.get('BILLS_PER_MONTH_IN_USD'))
        months_val = _try_parse_float(source.get('CASH_FOR_BILLS_IN_MONTHS'))
        if bills_val is not None and months_val is not None:
            return bills_val * months_val, dict(source)

    # Fall back to manual CASH_FOR_BILLS_IN_USD
    raw = reserve_settings.get('CASH_FOR_BILLS_IN_USD', 0)
    parsed = _try_parse_float(raw)
    return (parsed if parsed is not None else 0.0), None


def resolve_safe_asset(
    reserve_settings: dict[str, Any],
) -> str:
    """Return the SAFE_ASSET ticker from settings, defaulting to _CASH."""
    raw = reserve_settings.get('SAFE_ASSET', DEFAULT_SAFE_ASSET)
    if raw is None:
        return DEFAULT_SAFE_ASSET
    ticker = str(raw).strip().upper()
    if not ticker:
        return DEFAULT_SAFE_ASSET
    if ticker != CASH_META_TICKER:
        cash_upper = [s.upper() for s in FIDELITY_CASH_SYMBOLS]
        if ticker in cash_upper:
            raise RebalError(
                f"\n*** ERROR: SAFE_ASSET CONFLICT ***\n"
                f"SAFE_ASSET '{ticker}' is listed in FIDELITY_CASH_SYMBOLS.\n"
                f"A cash-pool symbol cannot also be the safe asset.\n"
                f"Remove it from FIDELITY_CASH_SYMBOLS or choose a different SAFE_ASSET."
            )
    return ticker


def append_reserve_rows(
    df_targets: pd.DataFrame,
    full_config: dict[str, Any],
    portfolio_config: dict[str, Any],
) -> pd.DataFrame:
    display_name = portfolio_config.get(
        'display_name', portfolio_config['portfolio_key'],
    )
    reserve_settings = _get_reserve_settings(full_config, portfolio_config)

    reserve_rows = []
    for ticker, value in reserve_settings.items():
        if ticker in NON_RESERVE_KEYS:
            continue
        if isinstance(value, dict):
            continue
        if ticker.startswith('_'):
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
    account_filter: AccountFilter | None,
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

    if account_filter:
        column = account_filter.column
        if column not in df_raw.columns:
            raise RebalError(
                f"\n*** ERROR: POSITIONS FILE MISSING FILTER COLUMN ***\n"
                f"ACCOUNT_FILTER expects column '{column}', which is not in the export."
            )
        df_raw[column] = df_raw[column].astype(str).str.strip()
        df_filtered = df_raw[df_raw[column] == account_filter.value].copy()
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


def sort_trades_by_off_ratio(
    df: pd.DataFrame,
    last_tickers: set[str] | None = None,
) -> pd.DataFrame:
    """Sort trades: zero-target first, then by Off_Ratio, special tickers last.

    *last_tickers* is a set of tickers that should appear at the end
    (e.g. CASH_META_TICKER and the safe-asset ticker).
    """
    if df.empty:
        return df.copy()
    if last_tickers is None:
        last_tickers = {CASH_META_TICKER}

    out = df.copy()
    out['_is_last'] = out['Ticker'].isin(last_tickers)
    last_rows = out[out['_is_last']].copy()
    regular = out[~out['_is_last']].copy()

    tier1 = regular[
        (regular['Target_Allocation'] == 0) & (regular['Off_Pct'] != 0)
    ].sort_values('Off_Pct', ascending=False)
    tier2 = regular[regular['Target_Allocation'] != 0].sort_values(
        'Off_Ratio', ascending=False,
    )
    tier3 = regular[
        (regular['Target_Allocation'] == 0) & (regular['Off_Pct'] == 0)
    ].sort_values('Ticker', ascending=True)

    sorted_regular = pd.concat([tier1, tier2, tier3], ignore_index=True)
    if not last_rows.empty:
        sorted_df = pd.concat([sorted_regular, last_rows], ignore_index=True)
    else:
        sorted_df = sorted_regular
    return sorted_df.drop(columns=['_is_last'], errors='ignore')


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
    account_filter = parse_account_filter(portfolio_config.get('ACCOUNT_FILTER'))

    df_targets_raw = load_targets_raw(targets_file)
    df_pct_of_max = load_pct_of_max(pct_of_max_file)
    df_targets = merge_targets_with_pct(
        df_targets_raw, df_pct_of_max, targets_file, pct_of_max_file,
    )
    df_targets = append_reserve_rows(df_targets, full_config, portfolio_config)
    df_targets = normalize_targets(df_targets)
    df_portfolio = parse_portfolio_export(export_path, account_filter)

    validate_cash_and_invested_alloc(df_targets)

    # --- Resolve cash reserve directly from settings (not dataframe) ---
    reserve_settings = _get_reserve_settings(full_config, portfolio_config)
    cash_for_bills, cash_for_bills_source = resolve_cash_for_bills(reserve_settings)
    safe_asset_ticker = resolve_safe_asset(reserve_settings)

    tax_raw = reserve_settings.get('TAX_OWED_IN_USD', 0)
    tax = _try_parse_float(tax_raw)
    if tax is None:
        tax = 0.0

    if cash_for_bills < 0 or tax < 0:
        raise RebalError("\n*** ERROR: NEGATIVE RESERVE VALUES NOT ALLOWED ***")

    cash_reserve_usd = cash_for_bills + tax

    # --- Target allocations ---
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
        {'CASH': CASH_META_TICKER},
    )

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

    # Build the rebalancing universe: invested assets + _CASH row
    cash_row_temp = pd.DataFrame([
        {'Ticker': CASH_META_TICKER, 'Current_Value': current_cash_value},
    ])
    df_portfolio_rebal = pd.concat([df_invested_assets, cash_row_temp], ignore_index=True)

    df_targets_alloc = df_targets_alloc.copy()
    df_targets_alloc['Ticker'] = df_targets_alloc['Ticker'].replace(
        {'CASH': CASH_META_TICKER},
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

    # --- Determine which tickers are "special" (last in trade tables) ---
    last_tickers = {CASH_META_TICKER}
    if safe_asset_ticker != CASH_META_TICKER:
        last_tickers.add(safe_asset_ticker)

    # --- Build trades, handling safe-asset residual & cash mirror ---
    if safe_asset_ticker == CASH_META_TICKER:
        # Classic mode: _CASH absorbs residual AND mirrors trades.
        # Exclude _CASH from normal trades; add it as the mirror row.
        df_trades = df_rebalance[df_rebalance['Ticker'] != CASH_META_TICKER].copy()
        df_trades = df_trades[df_trades['Trade_Amount_USD'].abs() > 0.01]

        net_non_cash = df_trades['Trade_Amount_USD'].sum()
        cash_pool_trade = -net_non_cash
        if abs(cash_pool_trade) > 0.01:
            cash_row_data = df_rebalance[
                df_rebalance['Ticker'] == CASH_META_TICKER
            ].iloc[0]
            cash_pool_row = pd.DataFrame([{
                'Ticker': CASH_META_TICKER,
                'Trade_Amount_USD': cash_pool_trade,
                'Target_Allocation': cash_row_data['Target_Allocation'],
                'Current_Pct': cash_row_data['Current_Pct'],
            }])
            df_trades = pd.concat([df_trades, cash_pool_row], ignore_index=True)
    else:
        # SAFE_ASSET is a real ticker (e.g. KISS).
        # That ticker gets its explicit allocation + residual, netted.
        # _CASH is the pure mirror of all non-cash trades.

        # 1) Compute residual %: whatever target allocation isn't
        #    claimed by explicit tickers goes to the safe asset.
        #    This is the _CASH row's Target_Allocation (if it exists),
        #    or equivalently 100% minus the sum of all non-CASH targets.
        explicit_tickers = {CASH_META_TICKER}
        non_cash_target_sum = df_rebalance[
            ~df_rebalance['Ticker'].isin(explicit_tickers)
        ]['Target_Allocation'].sum()
        residual_pct = 100.0 - non_cash_target_sum
        residual_value = (residual_pct / 100.0) * total_investable

        # 2) Compute the safe asset's explicit allocation
        sa_rows = df_rebalance[df_rebalance['Ticker'] == safe_asset_ticker]
        if sa_rows.empty:
            sa_explicit_target_val = 0.0
            sa_current_val = 0.0
            sa_target_alloc = 0.0
            sa_current_pct = 0.0
        else:
            sa_row = sa_rows.iloc[0]
            sa_explicit_target_val = float(sa_row['Target_Value'])
            sa_current_val = float(sa_row['Current_Value'])
            sa_target_alloc = float(sa_row['Target_Allocation'])
            sa_current_pct = float(sa_row['Current_Pct'])

        # 3) Safe asset final target = explicit + residual
        sa_final_target_val = sa_explicit_target_val + residual_value
        sa_final_target_pct = sa_target_alloc + residual_pct
        sa_net_trade = sa_final_target_val - sa_current_val

        # 4) Build trades excluding both _CASH and SAFE_ASSET
        excluded = {CASH_META_TICKER, safe_asset_ticker}
        df_trades = df_rebalance[~df_rebalance['Ticker'].isin(excluded)].copy()
        df_trades = df_trades[df_trades['Trade_Amount_USD'].abs() > 0.01]

        # 5) Add safe asset row (netted) if non-trivial
        if abs(sa_net_trade) > 0.01:
            sa_trade_row = pd.DataFrame([{
                'Ticker': safe_asset_ticker,
                'Trade_Amount_USD': sa_net_trade,
                'Target_Allocation': sa_final_target_pct,
                'Current_Pct': sa_current_pct,
            }])
            df_trades = pd.concat([df_trades, sa_trade_row], ignore_index=True)

        # 6) _CASH mirror: negative sum of all other trades
        net_all_other = df_trades['Trade_Amount_USD'].sum()
        cash_mirror_trade = -net_all_other
        if abs(cash_mirror_trade) > 0.01:
            cash_row_data = df_rebalance[
                df_rebalance['Ticker'] == CASH_META_TICKER
            ]
            c_pct = float(cash_row_data.iloc[0]['Current_Pct']) if not cash_row_data.empty else 0.0
            cash_mirror_row = pd.DataFrame([{
                'Ticker': CASH_META_TICKER,
                'Trade_Amount_USD': cash_mirror_trade,
                'Target_Allocation': 0.0,
                'Current_Pct': c_pct,
            }])
            df_trades = pd.concat([df_trades, cash_mirror_row], ignore_index=True)

    # --- Off-pct / off-ratio ---
    df_trades['Off_Pct'] = abs(
        df_trades['Target_Allocation'] - df_trades['Current_Pct'],
    )
    if CASH_META_TICKER in df_trades['Ticker'].values:
        df_non_special = df_trades[~df_trades['Ticker'].isin(last_tickers)]
        buys_off = df_non_special.loc[
            df_non_special['Trade_Amount_USD'] > 0, 'Off_Pct',
        ].sum()
        sells_off = df_non_special.loc[
            df_non_special['Trade_Amount_USD'] < 0, 'Off_Pct',
        ].sum()
        net_off = abs(buys_off - sells_off)
        df_trades.loc[
            df_trades['Ticker'] == CASH_META_TICKER, 'Off_Pct',
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
        last_tickers=last_tickers,
    )
    trades_buy = sort_trades_by_off_ratio(
        df_trades[df_trades['Trade_Amount_USD'] > 0].copy(),
        last_tickers=last_tickers,
    )

    df_current = df_rebalance[['Ticker', 'Current_Value']].copy()
    df_current['is_cash'] = df_current['Ticker'] == CASH_META_TICKER
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
        safe_asset_ticker=safe_asset_ticker,
        df_target_summary=df_target_summary,
        cash_for_bills_usd=cash_for_bills,
        cash_for_bills_source=cash_for_bills_source,
        tax_owed_usd=tax,
        cash_reserve_usd=cash_reserve_usd,
        total_portfolio_value=total_portfolio_value,
        df_current_portfolio=df_current,
        trades_sell=trades_sell,
        trades_buy=trades_buy,
        ticker_w_trade=ticker_w,
    )
