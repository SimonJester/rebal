#!/usr/bin/env python3
"""CLI for portfolio rebalancing — loads files, runs core logic, prints reports."""

import argparse
import os
import sys

import pandas as pd

from rebal_core import (
    RebalError,
    RebalanceResult,
    find_export_file,
    load_portfolio_config,
    resolve_data_path,
    resolve_positions_path,
    run_rebalance,
)

DEFAULT_SETTINGS = 'settings.json'
DEFAULT_PCT_OF_MAX = 'pct_of_max_alloc.csv'


def get_max_currency_width(values):
    max_len = 0
    if not isinstance(values, list):
        values = values.tolist() if hasattr(values, 'tolist') else [values]
    for value in values:
        if pd.isna(value):
            continue
        num_str = f"{value:,.2f}"
        max_len = max(max_len, len(num_str))
    return max(max_len, 4)


def _format_source_float(raw: object) -> float:
    """Parse a raw source value to float for display."""
    if isinstance(raw, str):
        return float(raw.strip().replace('$', '').replace(',', ''))
    return float(raw)


def print_rebalance_report(result: RebalanceResult) -> None:
    print(f"\n=== REBALANCE - {result.display_name} ===")

    if not result.df_target_summary.empty:
        print()
        asset_w = 15
        ticker_w = 15
        max_w = 10
        pct_w = 10
        target_w = 10
        header = (
            f"{'Asset_Type':<{asset_w}} {'Ticker_Target':<{ticker_w}} "
            f"{'Max_Pct':>{max_w}} {'Pct_of_Max':>{pct_w}} {'Target_Pct':>{target_w}}"
        )
        separator = (
            f"{'-' * asset_w} {'-' * ticker_w} {'-' * max_w} "
            f"{'-' * pct_w} {'-' * target_w}"
        )
        print(header)
        print(separator)
        for _, row in result.df_target_summary.iterrows():
            print(
                f"{row['Asset_Type']:<{asset_w}} {row['Ticker_Target']:<{ticker_w}} "
                f"{row['Max_Allocation_Pct']:>{max_w}.2f} {row['Pct_of_Max']:>{pct_w}.2f} "
                f"{row['Target_Percent']:>{target_w}.2f}"
            )
        print(separator)
        print()

    # --- Cash management table ---
    currency_values = [
        result.cash_for_bills_usd,
        result.tax_owed_usd,
        result.cash_reserve_usd,
    ]
    num_align_w_data = get_max_currency_width(currency_values)
    item_w = 40
    header_text = 'Value'
    value_col_w = max(len(header_text), num_align_w_data + 2)
    num_align_w = value_col_w - 2

    header = f"{'Cash_Management_Item':<{item_w}} {'Value':<{value_col_w}}"
    separator = f"{'-' * item_w} {'-' * value_col_w}"
    print(header)
    print(separator)

    # Cash for Bills line (with optional source annotation)
    bills_num = f"{result.cash_for_bills_usd:>{num_align_w},.2f}"
    print(f"{'Cash for Bills (Not Invested)':<{item_w}} {f'$ {bills_num}':>{value_col_w}}")
    if result.cash_for_bills_source:
        bills_v = _format_source_float(
            result.cash_for_bills_source.get('BILLS_PER_MONTH_IN_USD', 0),
        )
        months_v = _format_source_float(
            result.cash_for_bills_source.get('CASH_FOR_BILLS_IN_MONTHS', 0),
        )
        print(f"  (${bills_v:,.2f}/mo \u00d7 {months_v:g} mo)")

    # Estimated Taxes Owed
    tax_num = f"{result.tax_owed_usd:>{num_align_w},.2f}"
    print(f"{'Estimated Taxes Owed':<{item_w}} {f'$ {tax_num}':>{value_col_w}}")

    # Cash for Bills & Taxes total
    total_num = f"{result.cash_reserve_usd:>{num_align_w},.2f}"
    print(
        f"{'Cash for Bills & Taxes (Not Invested)':<{item_w}} "
        f"{f'$ {total_num}':>{value_col_w}}"
    )
    print(separator)
    print()

    if not result.df_current_portfolio.empty:
        portfolio_file = os.path.basename(result.export_path) if result.export_path else "<provided via DataFrame>"
        print(f"Portfolio File: {portfolio_file}")
        if result.account_filter:
            print(result.account_filter.describe())
        else:
            print('Filter: (none) \u2192 using ALL rows from export.')
        print(f'Symbol Column: "{result.symbol_column}"')
        print(f'Value Column: "{result.value_column}"')
        cash_list = ', '.join(f'"{t}"' for t in result.cash_pool_tickers)
        print(f'_CASH = [{cash_list}]')
        print()

        header_text = 'Current_USD'
        ticker_w = 15
        current_values = result.df_current_portfolio['Current_USD'].tolist()
        current_values.append(result.total_portfolio_value)
        num_align_w_data = get_max_currency_width(current_values)
        current_w = max(len(header_text), num_align_w_data + 2)
        num_align_w = current_w - 2

        header_current = f"{'Ticker_Owned':<{ticker_w}} {'Current_USD':<{current_w}}"
        separator_current = f"{'-' * ticker_w} {'-' * current_w}"
        print(header_current)
        print(separator_current)
        for _, row in result.df_current_portfolio.iterrows():
            num_str = f"{row['Current_USD']:>{num_align_w},.2f}"
            print(f"{row['Ticker_Owned']:<{ticker_w}} {f'$ {num_str}':>{current_w}}")
        print(separator_current)
        total_num_str = f"{result.total_portfolio_value:>{num_align_w},.2f}"
        print(f"{'TOTAL:':<{ticker_w}} {f'$ {total_num_str}':>{current_w}}")
        print()

    _print_trade_table(
        result.trades_sell, 'Sell', result.ticker_w_trade, is_sell=True,
    )
    print()
    _print_trade_table(
        result.trades_buy, 'Buy', result.ticker_w_trade, is_sell=False,
    )
    print()


def _print_trade_table(trades, side: str, ticker_w_trade: int, *, is_sell: bool):
    usd_header = f'{side}_USD'
    ticker_header = f'{side}_Ticker'
    values = trades['Trade_Amount_USD'].abs().tolist() if not trades.empty else []
    num_align_w_data = get_max_currency_width(values)
    usd_col_w = max(len(usd_header), num_align_w_data + 2)
    num_align_w = usd_col_w - 2
    off_pct_w = 11
    off_ratio_w = 10

    header = (
        f"{usd_header:<{usd_col_w}} {ticker_header:<{ticker_w_trade}} "
        f"{'Off_Pct':>{off_pct_w}} {'Off_Ratio':>{off_ratio_w}}"
    )
    separator = (
        f"{'-' * usd_col_w} {'-' * ticker_w_trade} "
        f"{'-' * off_pct_w} {'-' * off_ratio_w}"
    )
    print(header)
    print(separator)

    if not trades.empty:
        for _, row in trades.iterrows():
            ticker_name = row['Ticker']
            usd_amount = abs(row['Trade_Amount_USD'])
            off_ratio_str = (
                "N/A" if row['Target_Allocation'] == 0
                else f"{row['Off_Ratio']:.4f}"
            )
            num_str = f"{usd_amount:>{num_align_w},.2f}"
            print(
                f"{f'$ {num_str}':>{usd_col_w}} {ticker_name:<{ticker_w_trade}} "
                f"{row['Off_Pct']:>{off_pct_w}.4f} {off_ratio_str:>{off_ratio_w}}"
            )
        print(separator)
    else:
        label = ' (No sales required) ' if is_sell else ' (No purchases required) '
        print(f"{label:{usd_col_w + ticker_w_trade + off_pct_w + off_ratio_w + 3}}")
        print(separator)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Portfolio rebalancing helper')
    parser.add_argument(
        'portfolio_key',
        nargs='?',
        help='Portfolio key (default from settings.json)',
    )
    parser.add_argument(
        '--settings',
        default=DEFAULT_SETTINGS,
        help=f'Settings JSON path (default: {DEFAULT_SETTINGS})',
    )
    parser.add_argument(
        '--pct-of-max',
        default=DEFAULT_PCT_OF_MAX,
        help=f'Global pct-of-max CSV (default: {DEFAULT_PCT_OF_MAX})',
    )
    parser.add_argument(
        '--positions',
        help=(
            'Positions CSV file, or folder containing one or more position CSVs '
            '(newest matching FILE_PATTERN is used; default: ~/Downloads)'
        ),
    )
    parser.add_argument(
        '--downloads',
        help='Downloads folder for export glob (default: ~/Downloads)',
    )
    args = parser.parse_args(argv)

    try:
        full_config, portfolio_config = load_portfolio_config(
            args.settings,
            cli_portfolio_key=args.portfolio_key,
        )
    except RebalError as e:
        print(e)
        return 1

    file_pattern = portfolio_config.get('FILE_PATTERN', 'Portfolio_Positions_*.csv')
    if args.positions:
        positions_path = resolve_positions_path(args.positions, file_pattern)
        if not positions_path:
            expanded = os.path.expanduser(args.positions)
            if os.path.isdir(expanded):
                print("\n*** ERROR: POSITIONS FILE NOT FOUND ***")
                print(
                    f"No file matching '{file_pattern}' in folder '{expanded}'."
                )
            else:
                print("\n*** ERROR: POSITIONS PATH NOT FOUND ***")
                print(f"Path does not exist: '{expanded}'")
            return 1
    else:
        positions_path = find_export_file(
            file_pattern,
            downloads_path=args.downloads,
        )
        if not positions_path:
            downloads = args.downloads or os.path.expanduser('~/Downloads/')
            print("\n*** ERROR: POSITIONS FILE NOT FOUND ***")
            print(
                f"Please ensure a file matching '{file_pattern}' is in '{downloads}' "
                "or the current directory, or pass --positions."
            )
            return 1

    targets_file = resolve_data_path(
        portfolio_config['TARGETS_FILE'],
        settings_path=args.settings,
    )
    pct_file = resolve_data_path(args.pct_of_max, settings_path=args.settings)

    try:
        result = run_rebalance(
            export_path=positions_path,
            targets_file=targets_file,
            pct_of_max_file=pct_file,
            full_config=full_config,
            portfolio_config=portfolio_config,
        )
    except RebalError as e:
        print(e)
        return 1

    print_rebalance_report(result)
    return 0


if __name__ == '__main__':
    sys.exit(main())
