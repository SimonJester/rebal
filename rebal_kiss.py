import pandas as pd
import numpy as np
import pandas.errors
import glob
import os
import json 
import sys
import re

# --- Configuration ---
SETTINGS_FILE = 'kiss_settings.json' 
PCT_OF_MAX_FILE = 'kiss_pct_of_max.csv'   # ← GLOBAL for ALL portfolios

DEFAULT_BILLS_USD = 10000.0
DEFAULT_BILLS_MONTHS = 3.0
DEFAULT_TAX_OWED_USD = 0.0

# Updated to treat "USD***" as cash (same as SPAXX**, SHV, etc.)
FIDELITY_CASH_SYMBOLS = ['SPAXX**', 'SHV', 'USFR', 'BIL', 'SGOV', 'Pending activity', 'USD***'] 
INVESTED_CASH_TICKER = 'INVESTED_CASH'


def load_portfolio_config():
    """Load kiss_settings.json and return the config for the selected portfolio.
    Enforces kiss_alloc.{portfolio_key}.csv naming convention."""
    try:
        with open(SETTINGS_FILE, 'r') as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"\n*** ERROR: SETTINGS FILE NOT FOUND ***")
        print(f"Required file '{SETTINGS_FILE}' not found.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"\n*** ERROR: INVALID JSON FORMAT ***")
        print(f"Error reading '{SETTINGS_FILE}': {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n*** ERROR LOADING SETTINGS ***")
        print(f"An unexpected error occurred while loading '{SETTINGS_FILE}': {e}")
        sys.exit(1)

    # Determine which portfolio to use
    if len(sys.argv) > 1:
        portfolio_key = sys.argv[1].strip()
    else:
        portfolio_key = config.get('default_portfolio', 'fidelity')

    # New multi-portfolio format
    if 'portfolios' in config and isinstance(config['portfolios'], dict):
        portfolios = config['portfolios']
        if portfolio_key not in portfolios:
            print(f"\n*** ERROR: PORTFOLIO NOT FOUND ***")
            print(f"Portfolio key '{portfolio_key}' not found in kiss_settings.json.")
            print("Available portfolios:")
            for k, v in portfolios.items():
                display = v.get('display_name', k)
                print(f"  - {k}  →  {display}")
            print(f"\nUsage: python rebal_kiss.py <portfolio_key>")
            print(f"       (or set \"default_portfolio\" in kiss_settings.json)")
            sys.exit(1)
        portfolio_config = portfolios[portfolio_key]
        portfolio_config['portfolio_key'] = portfolio_key
    else:
        # Backward compatibility: old single-portfolio format
        portfolio_config = config.copy()
        portfolio_config['portfolio_key'] = 'fidelity'
        portfolio_config.setdefault('display_name', 'Fidelity Kiss Portfolio')

    # === Enforce safe portfolio key and auto-generate alloc filename ===
    if not re.match(r'^[a-zA-Z0-9_-]+$', portfolio_key):
        print(f"\n*** ERROR: INVALID PORTFOLIO KEY '{portfolio_key}' ***")
        print("Portfolio keys may only contain letters, numbers, hyphens (-) and underscores (_).")
        print("No spaces, periods, or other special characters are allowed.")
        print("Example valid keys: fidelity, fidelity-crypto, coinbase-btc")
        sys.exit(1)

    TARGETS_FILE = f"kiss_alloc.{portfolio_key}.csv"
    portfolio_config['TARGETS_FILE'] = TARGETS_FILE

    # Ensure other required per-portfolio keys exist
    portfolio_config.setdefault('FILE_PATTERN', 'Portfolio_Positions_*.csv')
    portfolio_config.setdefault('display_name', portfolio_key)

    return portfolio_config


# Load the selected portfolio configuration
portfolio_config = load_portfolio_config()

# Per-portfolio settings
TARGETS_FILE = portfolio_config['TARGETS_FILE']
FILE_PATTERN = portfolio_config.get('FILE_PATTERN', 'Portfolio_Positions_*.csv')
ACCOUNT_FILTER = portfolio_config.get('ACCOUNT_FILTER')
DISPLAY_NAME = portfolio_config.get('display_name', portfolio_config['portfolio_key'])

DOWNLOADS_PATH = os.path.expanduser('~/Downloads/')
FULL_PATTERN = os.path.join(DOWNLOADS_PATH, FILE_PATTERN)

list_of_files = glob.glob(FULL_PATTERN)
if not list_of_files:
    list_of_files = glob.glob(FILE_PATTERN)

if not list_of_files:
    FIDELITY_EXPORT_FILE = None
else:
    FIDELITY_EXPORT_FILE = max(list_of_files, key=os.path.getmtime)


def calculate_rebalance_trades():
    global ACCOUNT_FILTER

    if FIDELITY_EXPORT_FILE is None:
        print("\n*** ERROR: FIDELITY EXPORT FILE NOT FOUND ***")
        print(f"Please ensure a file matching '{FILE_PATTERN}' is in your '{DOWNLOADS_PATH}' folder.")
        print("The script also checked the current directory.")
        return

    # === TARGETS (ALLOC) FILE ===
    try:
        df_targets_raw = pd.read_csv(TARGETS_FILE)
        if df_targets_raw.empty:
            print(f"\n*** ERROR: ALLOC FILE IS EMPTY! ***")
            print(f"'{TARGETS_FILE}' is completely empty.")
            return
        required_alloc_cols = ['Asset_Type', 'Ticker', 'Max_Allocation_Pct']
        missing_alloc_cols = [c for c in required_alloc_cols if c not in df_targets_raw.columns]
        if missing_alloc_cols:
            print(f"\n*** ERROR: {TARGETS_FILE} MISSING COLUMNS ***")
            print(f"Missing: {', '.join(missing_alloc_cols)}")
            print(f"Expected: {', '.join(required_alloc_cols)}")
            return
        df_targets_raw['Ticker'] = df_targets_raw['Ticker'].str.upper()
        df_targets_raw['Asset_Type'] = df_targets_raw['Asset_Type'].str.strip()

        # Strict 100% allocation sum validation
        max_pct_sum = df_targets_raw['Max_Allocation_Pct'].sum()
        if abs(max_pct_sum - 100.0) > 1e-6:
            print(f"\n*** ERROR: ALLOCATION SUM VIOLATION IN {TARGETS_FILE} ***")
            print(f"Sum of Max_Allocation_Pct = {max_pct_sum:.4f}")
            print("The sum must be exactly 100.0 for every portfolio.")
            return

    except FileNotFoundError:
        print(f"\n*** ERROR: ALLOC FILE NOT FOUND ***")
        print(f"Required file '{TARGETS_FILE}' not found.")
        print(f"(Portfolio key: {portfolio_config['portfolio_key']})")
        return
    except Exception as e:
        print(f"\n*** ERROR LOADING ALLOC FILE ***")
        print(f"Error loading '{TARGETS_FILE}': {e}")
        return

    # === GLOBAL PCT_OF_MAX ===
    try:
        df_pct_of_max = pd.read_csv(PCT_OF_MAX_FILE)
        if df_pct_of_max.empty:
            print(f"\n*** ERROR: GLOBAL PCT_OF_MAX FILE IS EMPTY! ***")
            return
        required_pct_cols = ['Asset_Type', 'Pct_of_Max']
        missing_pct_cols = [c for c in required_pct_cols if c not in df_pct_of_max.columns]
        if missing_pct_cols:
            print(f"\n*** ERROR: {PCT_OF_MAX_FILE} MISSING COLUMNS ***")
            return
        df_pct_of_max['Asset_Type'] = df_pct_of_max['Asset_Type'].str.strip()
    except FileNotFoundError:
        print(f"\n*** ERROR: GLOBAL PCT_OF_MAX FILE NOT FOUND ***")
        return
    except Exception as e:
        print(f"\n*** ERROR LOADING GLOBAL PCT_OF_MAX ***")
        print(f"Error loading '{PCT_OF_MAX_FILE}': {e}")
        return

    # Validate Asset_Type consistency
    alloc_asset_types = set(df_targets_raw['Asset_Type'].dropna().unique())
    pct_asset_types = set(df_pct_of_max['Asset_Type'].dropna().unique())
    undefined_types = alloc_asset_types - pct_asset_types
    if undefined_types:
        print(f"\n*** ERROR: UNDEFINED ASSET TYPES IN '{TARGETS_FILE}' ***")
        print(f"The following Asset_Type values have no matching entry in the global '{PCT_OF_MAX_FILE}':")
        for at in sorted(undefined_types):
            print(f"  - {at}")
        return

    df_targets = pd.merge(df_targets_raw, df_pct_of_max[['Asset_Type', 'Pct_of_Max']], on='Asset_Type', how='left')
    df_targets['Value'] = 0.0

    # === Reserve settings ===
    try:
        with open(SETTINGS_FILE, 'r') as f:
            full_config = json.load(f)

        if 'portfolios' in full_config and portfolio_config['portfolio_key'] in full_config['portfolios']:
            reserve_settings = full_config['portfolios'][portfolio_config['portfolio_key']]
        else:
            reserve_settings = full_config

        required_keys = ['BILLS_PER_MONTH_IN_USD', 'CASH_FOR_BILLS_IN_MONTHS', 'TAX_OWED_IN_USD']
        if not all(key in reserve_settings for key in required_keys):
            print(f"\n*** ERROR: MISSING REQUIRED KEYS IN PORTFOLIO '{DISPLAY_NAME}' ***")
            return

        NON_RESERVE_KEYS = {
            'ACCOUNT_FILTER', 'FILE_PATTERN', 'display_name', 'portfolio_key',
            'portfolios', 'default_portfolio'
        }
        reserve_rows = []
        for ticker, value in reserve_settings.items():
            if ticker in NON_RESERVE_KEYS:
                continue
            numeric_value = 0.0
            if isinstance(value, str):
                try:
                    cleaned_value = value.strip().replace('$', '').replace(',', '')
                    numeric_value = float(cleaned_value)
                except ValueError:
                    print(f"\n*** ERROR: Invalid value format for '{ticker}' in portfolio '{DISPLAY_NAME}'. ***")
                    return
            else:
                try:
                    numeric_value = float(value)
                except (ValueError, TypeError):
                    print(f"\n*** ERROR: Invalid value type for '{ticker}' in portfolio '{DISPLAY_NAME}'. ***")
                    return

            reserve_rows.append({
                'Asset_Type': 'RESERVE',
                'Ticker': ticker.upper(),
                'Max_Allocation_Pct': 0.0,
                'Pct_of_Max': 0.0,
                'Value': numeric_value 
            })

        df_reserves = pd.DataFrame(reserve_rows)
        df_targets = pd.concat([df_targets, df_reserves], ignore_index=True)

    except Exception as e:
        print(f"\n*** ERROR LOADING RESERVE SETTINGS ***")
        print(f"Error processing portfolio '{DISPLAY_NAME}': {e}")
        return

    required_cols = ['Max_Allocation_Pct', 'Pct_of_Max', 'Value']
    for col in required_cols:
        if col not in df_targets.columns:
            print(f"\n*** INTERNAL ERROR: MISSING COLUMN AFTER MERGE! ***")
            return

    df_targets['Max_Allocation_Pct'] = pd.to_numeric(df_targets['Max_Allocation_Pct'], errors='coerce').fillna(0.0)
    df_targets['Pct_of_Max'] = pd.to_numeric(df_targets['Pct_of_Max'], errors='coerce').fillna(0.0)
    df_targets['Value'] = pd.to_numeric(df_targets['Value'], errors='coerce').fillna(0.0)

    try:
        df_raw_portfolio = pd.read_csv(
            FIDELITY_EXPORT_FILE, 
            skiprows=0, 
            index_col=False, 
            encoding='utf-8-sig',
            on_bad_lines='skip' 
        )

        df_raw_portfolio.columns = df_raw_portfolio.columns.str.strip()
        
        if 'Account Name' in df_raw_portfolio.columns:
            df_raw_portfolio['Account Name'] = df_raw_portfolio['Account Name'].astype(str).str.strip()

        if ACCOUNT_FILTER:
            df_filtered_portfolio = df_raw_portfolio[
                df_raw_portfolio['Account Name'] == ACCOUNT_FILTER
            ].copy()
        else:
            df_filtered_portfolio = df_raw_portfolio.copy()

        if 'Current Value' in df_filtered_portfolio.columns:
            df_filtered_portfolio['Current Value'] = pd.to_numeric(
                df_filtered_portfolio['Current Value'].astype(str)
                                                    .str.replace('$', '', regex=False)
                                                    .str.replace(',', '', regex=False),
                errors='coerce'
            ).fillna(0.0)
        else:
            print(f"\n*** ERROR: Fidelity export file is missing the required column 'Current Value'. ***")
            return
            
        df_portfolio_cleaned = df_filtered_portfolio[['Symbol', 'Current Value']].copy()
        df_portfolio_cleaned.rename(columns={'Symbol': 'Ticker', 'Current Value': 'Current_Value'}, inplace=True)
        df_portfolio_cleaned['Ticker'] = df_portfolio_cleaned['Ticker'].str.upper()
        
        ignore_tickers = ['LTCG', 'STCG', 'INCOME']
        df_portfolio_filtered = df_portfolio_cleaned[~df_portfolio_cleaned['Ticker'].isin(ignore_tickers)].copy()

    except FileNotFoundError:
        print(f"\n*** ERROR: Fidelity export file not found at the determined path: '{FIDELITY_EXPORT_FILE}' ***")
        return
    except KeyError as e:
        print(f"\n*** ERROR: Fidelity export file is missing the required column {e}. ***")
        return

    cash_max_row = df_targets[df_targets['Ticker'] == 'CASH']
    if not cash_max_row.empty:
        cash_max_pct = cash_max_row['Max_Allocation_Pct'].iloc[0]
        if cash_max_pct != 100.0:
            print("\n*** ERROR: CASH ALLOCATION CONSTRAINT VIOLATION! ***")
            print(f"The 'Max_Allocation_Pct' for CASH must be 100.0.")
            return

    tickers_to_exclude = ['CASH', 'CASH_RESERVE_USD', 'BILLS_PER_MONTH_IN_USD', 'CASH_FOR_BILLS_IN_MONTHS', 'TAX_OWED_IN_USD']
    df_invested_targets = df_targets[~df_targets['Ticker'].isin(tickers_to_exclude)].copy()
    
    max_pct_sum = df_invested_targets['Max_Allocation_Pct'].sum()
    
    if abs(max_pct_sum - 100.0) > 1e-6:
        print("\n*** ERROR: MAX ALLOCATION SUM VIOLATION! ***")
        print(f"The sum of 'Max_Allocation_Pct' for all non-CASH assets must be 100.0.")
        return
    
    df_targets['Target_Allocation'] = df_targets['Max_Allocation_Pct'] * (df_targets['Pct_of_Max'] / 100.0)
    
    df_target_summary = df_targets[['Asset_Type', 'Ticker', 'Max_Allocation_Pct', 'Pct_of_Max', 'Target_Allocation']].copy()
    df_target_summary.rename(columns={'Target_Allocation': 'Target_Percent', 'Ticker': 'Ticker_Target'}, inplace=True)
    df_target_summary['Ticker_Target'] = df_target_summary['Ticker_Target'].replace({'CASH': INVESTED_CASH_TICKER})

    def get_reserve_value(df, ticker, default_value, default_message_format):
        row = df[df['Ticker'] == ticker]
        if row.empty:
            return default_value, f"Note: '{ticker}' not found. Using default value: {default_message_format}"
        value = row['Value'].iloc[0]
        if pd.isna(value):
            return default_value, f"Note: '{ticker}' found but 'Value' is empty/NaN. Using default value: {default_message_format}"
        return value, None

    default_messages = []
    
    BILLS_PER_MONTH_IN_USD, msg_b_usd = get_reserve_value(
        df_targets, 'BILLS_PER_MONTH_IN_USD', DEFAULT_BILLS_USD, f"${DEFAULT_BILLS_USD:,.2f}"
    )
    if msg_b_usd:
        default_messages.append(msg_b_usd)

    CASH_FOR_BILLS_IN_MONTHS, msg_b_months = get_reserve_value(
        df_targets, 'CASH_FOR_BILLS_IN_MONTHS', DEFAULT_BILLS_MONTHS, f"{DEFAULT_BILLS_MONTHS:.1f} months"
    )
    if msg_b_months:
        default_messages.append(msg_b_months)

    TAX_OWED_IN_USD, msg_tax = get_reserve_value(
        df_targets, 'TAX_OWED_IN_USD', DEFAULT_TAX_OWED_USD, f"${DEFAULT_TAX_OWED_USD:,.2f}"
    )
    if msg_tax:
        default_messages.append(msg_tax)

    if default_messages:
        print("\n--- Cash Reserve Defaults Used ---")
        for msg in default_messages:
            print(msg)
        print("----------------------------------\n")

    if BILLS_PER_MONTH_IN_USD < 0 or CASH_FOR_BILLS_IN_MONTHS < 0 or TAX_OWED_IN_USD < 0:
        print("\n*** ERROR: NEGATIVE RESERVE VALUES NOT ALLOWED ***")
        return

    CASH_RESERVE_USD = (BILLS_PER_MONTH_IN_USD * CASH_FOR_BILLS_IN_MONTHS) + TAX_OWED_IN_USD
    
    special_reserve_tickers = ['CASH_RESERVE_USD', 'BILLS_PER_MONTH_IN_USD', 'CASH_FOR_BILLS_IN_MONTHS', 'TAX_OWED_IN_USD']
    non_cash_sum = df_targets[~df_targets['Ticker'].isin(['CASH'] + special_reserve_tickers)]['Target_Allocation'].sum()
    df_targets.loc[df_targets['Ticker'] == 'CASH', 'Target_Allocation'] = 100.0 - non_cash_sum
    
    df_targets_alloc = df_targets[~df_targets['Ticker'].isin(special_reserve_tickers)].copy()
    df_target_summary = df_target_summary[~df_target_summary['Ticker_Target'].isin(special_reserve_tickers)].copy()
    
    current_cash_value = df_portfolio_filtered[
        df_portfolio_filtered['Ticker'].isin([s.upper() for s in FIDELITY_CASH_SYMBOLS])
    ]['Current_Value'].sum()
    
    if current_cash_value < 0:
        print("\n*** ERROR: CONSOLIDATED CASH POOL IS NEGATIVE! ***")
        return

    df_invested_assets = df_portfolio_filtered[
        ~df_portfolio_filtered['Ticker'].isin([s.upper() for s in FIDELITY_CASH_SYMBOLS])
    ].copy()
    
    TOTAL_PORTFOLIO_VALUE = df_invested_assets['Current_Value'].sum() + current_cash_value
    
    if CASH_RESERVE_USD > TOTAL_PORTFOLIO_VALUE:
        print("\n*** ERROR: NOT ENOUGH ASSETS FOR CASH RESERVE! ***")
        return

    TOTAL_INVESTABLE_CAPITAL = TOTAL_PORTFOLIO_VALUE - CASH_RESERVE_USD
    
    cash_row_temp = pd.DataFrame([{'Ticker': INVESTED_CASH_TICKER, 'Current_Value': current_cash_value}])
    df_portfolio_rebal = pd.concat([df_invested_assets, cash_row_temp], ignore_index=True)

    df_targets_alloc['Ticker'] = df_targets_alloc['Ticker'].replace({'CASH': INVESTED_CASH_TICKER})
    
    df_rebalance = pd.merge(
        df_portfolio_rebal, 
        df_targets_alloc[['Ticker', 'Target_Allocation']], 
        on='Ticker', 
        how='outer'
    ).fillna(0.0)
    
    df_rebalance['Target_Allocation_Dec'] = df_rebalance['Target_Allocation'] / 100.0
    df_rebalance['Target_Value'] = df_rebalance['Target_Allocation_Dec'] * TOTAL_INVESTABLE_CAPITAL
    df_rebalance['Current_Pct'] = (df_rebalance['Current_Value'] / TOTAL_INVESTABLE_CAPITAL) * 100.0
    df_rebalance['Trade_Amount_USD'] = df_rebalance['Target_Value'] - df_rebalance['Current_Value']
    
    df_trades = df_rebalance[df_rebalance['Ticker'] != INVESTED_CASH_TICKER].copy()
    df_trades = df_trades[df_trades['Trade_Amount_USD'].abs() > 0.01] 

    net_non_cash_trade = df_trades['Trade_Amount_USD'].sum()
    cash_pool_trade = -net_non_cash_trade
    
    if abs(cash_pool_trade) > 0.01:
        cash_row_data = df_rebalance[df_rebalance['Ticker'] == INVESTED_CASH_TICKER].iloc[0]
        cash_pool_row = pd.DataFrame([{
            'Ticker': INVESTED_CASH_TICKER, 
            'Trade_Amount_USD': cash_pool_trade,
            'Target_Allocation': cash_row_data['Target_Allocation'],
            'Current_Pct': cash_row_data['Current_Pct']
        }])
        df_trades = pd.concat([df_trades, cash_pool_row], ignore_index=True)

    df_trades['Off_Pct'] = abs(df_trades['Target_Allocation'] - df_trades['Current_Pct'])
    
    if INVESTED_CASH_TICKER in df_trades['Ticker'].values:
        df_non_cash = df_trades[df_trades['Ticker'] != INVESTED_CASH_TICKER]
        buys_off_target = df_non_cash.loc[df_non_cash['Trade_Amount_USD'] > 0, 'Off_Pct'].sum()
        sells_off_target = df_non_cash.loc[df_non_cash['Trade_Amount_USD'] < 0, 'Off_Pct'].sum()
        net_off_target = abs(buys_off_target - sells_off_target)
        df_trades.loc[df_trades['Ticker'] == INVESTED_CASH_TICKER, 'Off_Pct'] = net_off_target

    df_trades['Off_Ratio'] = np.where(
        df_trades['Target_Allocation'] > 0,
        df_trades['Off_Pct'] / df_trades['Target_Allocation'],
        0.0
    )

    if not df_trades.empty:
        MAX_TICKER_LEN_DATA = df_trades['Ticker'].apply(len).max()
    else:
        MAX_TICKER_LEN_DATA = 0
        
    MAX_TICKER_LEN_HEADER = max(len('Sell_Ticker'), len('Buy_Ticker'))
    TICKER_W_TRADE = max(MAX_TICKER_LEN_DATA, MAX_TICKER_LEN_HEADER)
    
    trades_sell = df_trades[df_trades['Trade_Amount_USD'] < 0].copy()
    trades_buy = df_trades[df_trades['Trade_Amount_USD'] > 0].copy()
    
    def sort_trades_by_off_ratio(df):
        if df.empty:
            return df.copy()
        df = df.copy()
        df['is_invested_cash'] = (df['Ticker'] == INVESTED_CASH_TICKER)

        cash_rows = df[df['is_invested_cash']].copy()
        non_cash = df[~df['is_invested_cash']].copy()

        tier1 = non_cash[
            (non_cash['Target_Allocation'] == 0) & (non_cash['Off_Pct'] != 0)
        ].sort_values('Off_Pct', ascending=False)

        tier2 = non_cash[
            non_cash['Target_Allocation'] != 0
        ].sort_values('Off_Ratio', ascending=False)

        tier3 = non_cash[
            (non_cash['Target_Allocation'] == 0) & (non_cash['Off_Pct'] == 0)
        ].sort_values('Ticker', ascending=True)

        sorted_non_cash = pd.concat([tier1, tier2, tier3], ignore_index=True)

        if not cash_rows.empty:
            sorted_df = pd.concat([sorted_non_cash, cash_rows], ignore_index=True)
        else:
            sorted_df = sorted_non_cash

        return sorted_df.drop(columns=['is_invested_cash'], errors='ignore')

    trades_sell = sort_trades_by_off_ratio(trades_sell)
    trades_buy = sort_trades_by_off_ratio(trades_buy)

    print(f"\n=== REBALANCE KISS - {DISPLAY_NAME} ===")
    
    def get_max_currency_width(values):
        max_len = 0
        if not isinstance(values, list):
            values = values.tolist() if hasattr(values, 'tolist') else [values]
        for value in values:
            if pd.isna(value): continue
            num_str = f"{value:,.2f}" 
            max_len = max(max_len, len(num_str))
        return max(max_len, 4) 

    df_current_portfolio = df_rebalance[['Ticker', 'Current_Value']].copy()
    df_current_portfolio['is_cash'] = (df_current_portfolio['Ticker'] == INVESTED_CASH_TICKER)
    df_current_portfolio = df_current_portfolio.sort_values(
        by=['is_cash', 'Ticker'], 
        ascending=[True, True]
    ).drop(columns=['is_cash'])
    df_current_portfolio.rename(columns={'Current_Value': 'Current_USD', 'Ticker': 'Ticker_Owned'}, inplace=True)

    if not df_target_summary.empty:
        print() 
        ASSET_W = 15
        TICKER_W = 15
        MAX_W = 10
        PCT_W = 10
        TARGET_W = 10
        header = f"{'Asset_Type':<{ASSET_W}} {'Ticker_Target':<{TICKER_W}} {'Max_Pct':>{MAX_W}} {'Pct_of_Max':>{PCT_W}} {'Target_Pct':>{TARGET_W}}"
        separator = f"{'-' * ASSET_W} {'-' * TICKER_W} {'-' * MAX_W} {'-' * PCT_W} {'-' * TARGET_W}"
        print(header)
        print(separator)
        for index, row in df_target_summary.iterrows():
            asset_type = row['Asset_Type']
            ticker = row['Ticker_Target']
            max_pct = row['Max_Allocation_Pct']
            pct_of_max = row['Pct_of_Max']
            target_pct = row['Target_Percent']
            print(f"{asset_type:<{ASSET_W}} {ticker:<{TICKER_W}} {max_pct:>{MAX_W}.2f} {pct_of_max:>{PCT_W}.2f} {target_pct:>{TARGET_W}.2f}")
        print(separator)
        print()

    cash_management_data = [
        ("Months of Bills to Set Aside as Cash", CASH_FOR_BILLS_IN_MONTHS, False),
        ("Average Bills Per Month", BILLS_PER_MONTH_IN_USD, True),
        ("Estimated Taxes Owed", TAX_OWED_IN_USD, True),
        ("Cash for Bills & Taxes (Not Invested)", CASH_RESERVE_USD, True),
    ]

    currency_values = [d[1] for d in cash_management_data if d[2] is True]
    non_currency_values = [d[1] for d in cash_management_data if d[2] is False]
    NUM_ALIGN_W_DATA = get_max_currency_width(currency_values + non_currency_values) 
    ITEM_W = 40
    HEADER_TEXT = 'Value'
    VALUE_COL_W = max(len(HEADER_TEXT), NUM_ALIGN_W_DATA + 2) 
    NUM_ALIGN_W = VALUE_COL_W - 2 

    header = f"{'Cash_Management_Item':<{ITEM_W}} {'Value':<{VALUE_COL_W}}"
    separator = f"{'-' * ITEM_W} {'-' * VALUE_COL_W}"
    print(header)
    print(separator)

    for item, value, is_currency in cash_management_data:
        num_str = f"{value:>{NUM_ALIGN_W},.2f}"
        if is_currency:
            formatted_value = f"$ {num_str}"
        else:
            formatted_value = f"  {num_str}"
        print(f"{item:<{ITEM_W}} {formatted_value:>{VALUE_COL_W}}")

    print(separator)
    print()

    if not df_current_portfolio.empty:
        print(os.path.basename(FIDELITY_EXPORT_FILE))
        if ACCOUNT_FILTER:
            print(f"Filtering portfolio to account: '{ACCOUNT_FILTER}'")
        else:
            print("No ACCOUNT_FILTER specified → using ALL accounts from export.")
        print()
        
        HEADER_TEXT = 'Current_USD'
        TICKER_W = 15
        current_values = df_current_portfolio['Current_USD'].tolist()
        current_values.append(TOTAL_PORTFOLIO_VALUE)
        NUM_ALIGN_W_DATA = get_max_currency_width(current_values)
        CURRENT_W = max(len(HEADER_TEXT), NUM_ALIGN_W_DATA + 2) 
        NUM_ALIGN_W = CURRENT_W - 2
        
        header_current = f"{'Ticker_Owned':<{TICKER_W}} {'Current_USD':<{CURRENT_W}}"
        separator_current = f"{'-' * TICKER_W} {'-' * CURRENT_W}"
        print(header_current)
        print(separator_current)

        for index, row in df_current_portfolio.iterrows():
            ticker = row['Ticker_Owned']
            current_usd = row['Current_USD'] 
            num_str = f"{current_usd:>{NUM_ALIGN_W},.2f}"
            usd_formatted = f"$ {num_str}"
            print(f"{ticker:<{TICKER_W}} {usd_formatted:>{CURRENT_W}}")

        print(separator_current)
        total_num_str = f"{TOTAL_PORTFOLIO_VALUE:>{NUM_ALIGN_W},.2f}"
        total_usd_formatted = f"$ {total_num_str}"
        print(f"{'TOTAL:':<{TICKER_W}} {total_usd_formatted:>{CURRENT_W}}")
        print()

    HEADER_TEXT = 'Sell_USD'
    sell_values = trades_sell['Trade_Amount_USD'].abs().tolist()
    NUM_ALIGN_W_DATA = get_max_currency_width(sell_values)
    USD_COL_W_SELL = max(len(HEADER_TEXT), NUM_ALIGN_W_DATA + 2)
    NUM_ALIGN_W_SELL = USD_COL_W_SELL - 2
    
    OFF_PCT_W = 11
    OFF_RATIO_W = 10
    header_sell = f"{'Sell_USD':<{USD_COL_W_SELL}} {'Sell_Ticker':<{TICKER_W_TRADE}} {'Off_Pct':>{OFF_PCT_W}} {'Off_Ratio':>{OFF_RATIO_W}}"
    separator_sell = f"{'-' * USD_COL_W_SELL} {'-' * TICKER_W_TRADE} {'-' * OFF_PCT_W} {'-' * OFF_RATIO_W}"
    print(header_sell)
    print(separator_sell)

    if not trades_sell.empty:
        for index, row in trades_sell.iterrows():
            ticker_name = INVESTED_CASH_TICKER if row['Ticker'] == INVESTED_CASH_TICKER else row['Ticker']
            usd_amount = abs(row['Trade_Amount_USD'])
            off_pct = row['Off_Pct']
            off_ratio_str = "N/A" if row['Target_Allocation'] == 0 else f"{row['Off_Ratio']:.4f}"
            num_str = f"{usd_amount:>{NUM_ALIGN_W_SELL},.2f}"
            formatted_usd_with_prefix = f"$ {num_str}" 
            print(f"{formatted_usd_with_prefix:>{USD_COL_W_SELL}} {ticker_name:<{TICKER_W_TRADE}} {off_pct:>{OFF_PCT_W}.4f} {off_ratio_str:>{OFF_RATIO_W}}")
        print(separator_sell)
    else:
        print(f"{' (No sales required) ':{USD_COL_W_SELL + TICKER_W_TRADE + OFF_PCT_W + OFF_RATIO_W + 3}}")
        print(separator_sell)

    HEADER_TEXT = 'Buy_USD'
    buy_values = trades_buy['Trade_Amount_USD'].abs().tolist()
    NUM_ALIGN_W_DATA = get_max_currency_width(buy_values)
    USD_COL_W_BUY = max(len(HEADER_TEXT), NUM_ALIGN_W_DATA + 2) 
    NUM_ALIGN_W_BUY = USD_COL_W_BUY - 2
    
    print()
    header_buy = f"{'Buy_USD':<{USD_COL_W_BUY}} {'Buy_Ticker':<{TICKER_W_TRADE}} {'Off_Pct':>{OFF_PCT_W}} {'Off_Ratio':>{OFF_RATIO_W}}"
    separator_buy = f"{'-' * USD_COL_W_BUY} {'-' * TICKER_W_TRADE} {'-' * OFF_PCT_W} {'-' * OFF_RATIO_W}"
    print(header_buy)
    print(separator_buy)

    if not trades_buy.empty:
        for index, row in trades_buy.iterrows():
            ticker_name = INVESTED_CASH_TICKER if row['Ticker'] == INVESTED_CASH_TICKER else row['Ticker']
            usd_amount = abs(row['Trade_Amount_USD'])
            off_pct = row['Off_Pct']
            off_ratio_str = "N/A" if row['Target_Allocation'] == 0 else f"{row['Off_Ratio']:.4f}"
            num_str = f"{usd_amount:>{NUM_ALIGN_W_BUY},.2f}"
            formatted_usd_with_prefix = f"$ {num_str}" 
            print(f"{formatted_usd_with_prefix:>{USD_COL_W_BUY}} {ticker_name:<{TICKER_W_TRADE}} {off_pct:>{OFF_PCT_W}.4f} {off_ratio_str:>{OFF_RATIO_W}}")
        print(separator_buy)
    else:
        print(f"{' (No purchases required) ':{USD_COL_W_BUY + TICKER_W_TRADE + OFF_PCT_W + OFF_RATIO_W + 3}}")
        print(separator_buy)

    print()


if __name__ == "__main__":
    calculate_rebalance_trades()
