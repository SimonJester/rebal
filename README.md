# Rebalance

Portfolio rebalancing helper: reads a broker position export, applies target allocations and cash reserves, and prints suggested buy/sell trades.

## Prerequisites

- Python 3.10+ recommended
- Dependencies in `requirements.txt` (`pandas`, `numpy`)

## Setup

```bash
cd /path/to/rebal
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp settings.example.json settings.json   # first time only; then edit locally
```

Your real `settings.json` stays on your machine only (see [Privacy](#privacy)).

## Usage

```bash
python rebal.py                  # default portfolio from settings
python rebal.py fidelity         # specific portfolio key
python rebal.py fidelity-crypto
```

The script looks for a position export matching each portfolio’s `FILE_PATTERN`:

1. `~/Downloads/` (most recent file wins)
2. Current working directory (fallback)

Default pattern for Fidelity-style exports: `Portfolio_Positions_*.csv`.

## Project files

| File | Purpose |
|------|---------|
| `rebal.py` | Main script |
| `settings.json` | **Local only** — reserves, account filter, portfolio registry |
| `settings.example.json` | Safe template to copy; safe to commit/share |
| `pct_of_max_alloc.csv` | Global “aggressiveness” per asset class (`Pct_of_Max`) |
| `alloc.{portfolio_key}.csv` | Target max weights per ticker for that portfolio |

Portfolio keys must match `^[a-zA-Z0-9_-]+$` and map to `alloc.{key}.csv`.

### Example alloc file (`alloc.fidelity.csv`)

```csv
Asset_Type,Ticker,Max_Allocation_Pct
Stocks,VTI,26
...
```

- `Max_Allocation_Pct` must sum to **100.0** (non-CASH rows; CASH row rules are enforced in code).
- Every `Asset_Type` in an alloc file must appear in `pct_of_max_alloc.csv`.

### Global pct-of-max (`pct_of_max_alloc.csv`)

```csv
Asset_Type,Pct_of_Max
Stocks,100
Gold,0
...
```

Target weight for a line = `Max_Allocation_Pct × (Pct_of_Max / 100)`.

## Settings (`settings.json`)

Copy from `settings.example.json` and edit. The live file is listed in `.gitignore`
so dollar amounts and account names are not committed by mistake.

### Multi-portfolio (recommended)

```json
{
  "default_portfolio": "fidelity",
  "portfolios": {
    "fidelity": {
      "display_name": "Fidelity Kiss Portfolio",
      "ACCOUNT_FILTER": {
        "column": "Account Name",
        "value": "Kiss Portfolio"
      },
      "FILE_PATTERN": "Portfolio_Positions_*.csv",
      "CASH_POOL_TICKERS": ["SPAXX**", "SHV", "USFR", "BIL", "SGOV", "Pending activity", "USD***"],
      "SYMBOL_COLUMN": "Symbol",
      "VALUE_COLUMN": "Current Value",
      "SAFE_ASSET": "_CASH",
      "BILLS_PER_MONTH_IN_USD": "$21,303",
      "CASH_FOR_BILLS_IN_MONTHS": "0.6529",
      "TAX_OWED_IN_USD": "$0"
    },
    "fidelity-crypto": {
      "display_name": "Fidelity Crypto",
      "FILE_PATTERN": "Portfolio_Positions_*.csv",
      "CASH_POOL_TICKERS": [],
      "SYMBOL_COLUMN": "Symbol",
      "VALUE_COLUMN": "Current Value",
      "BILLS_PER_MONTH_IN_USD": 0,
      "CASH_FOR_BILLS_IN_MONTHS": 0,
      "TAX_OWED_IN_USD": 0
    }
  }
}
```

### Legacy single-portfolio

Top-level keys (no `portfolios` object) still work; the script treats the portfolio key as `fidelity` and uses `alloc.fidelity.csv`.

Required per portfolio: `BILLS_PER_MONTH_IN_USD`, `CASH_FOR_BILLS_IN_MONTHS`, `TAX_OWED_IN_USD`. Dollar amounts may include `$` and commas.

Optional: `ACCOUNT_FILTER` (`column` + `value` matched in the positions CSV), `FILE_PATTERN`,
`display_name`, `CASH_POOL_TICKERS`, `SYMBOL_COLUMN`, `VALUE_COLUMN` (for non-Fidelity position exports or future API sources),
`SAFE_ASSET`.
Legacy string values still work (treated as `Account Name` = that string).

`SYMBOL_COLUMN` and `VALUE_COLUMN` let you adapt to other brokers' CSV exports. For non-CSV sources (e.g. future Coinbase API), load your data into a pandas DataFrame (with columns `Ticker` and `Current_Value`, or raw columns matching your settings) and pass it via the core API (see below).

## Portfolios in this repo

| Key | Alloc file |
|-----|------------|
| `fidelity` | `alloc.fidelity.csv` |
| `fidelity-crypto` | `alloc.fidelity-crypto.csv` |
| `coinbase-btc` | `alloc.coinbase-btc.csv` |
| `coinbase-eth` | `alloc.coinbase-eth.csv` |

## Development

Logic lives in `rebal_core.py` (testable, no printing). `rebal.py` is the CLI.

```bash
./scripts/check.sh
# or: pytest -q
```

See `AGENTS.md` for the strict TDD rules followed by AI agents working on this project (all changes must be driven by tests covering happy paths, failure modes, and edges).

Optional: run tests before every commit on **your machine only**:

```bash
pip install pre-commit
pre-commit install
```

This does not upload data anywhere. Skip GitHub Actions / remote CI if you prefer everything local.

## Privacy

**Safe to keep in git (no personal holdings):** `rebal.py`, `rebal_core.py`, `alloc.*.csv`
(target weights only), `pct_of_max_alloc.csv`, `settings.example.json`, `tests/fixtures/`.

**Cursor AI:** This repo includes `.cursorignore` so Tab/Agent/`@` mentions should not pull in
`settings.json`, position exports, or credential patterns. That is best-effort—not absolute:
enable **Privacy Mode** in Cursor settings, and add a **global ignore** for
`~/.config/rebal/` (Settings → General → Global Cursor Ignore List) before storing Coinbase API
keys there. Do not paste secrets into chat.

**Keep local — never commit:**

| File | Why |
|------|-----|
| `settings.json` | Bills, taxes, account names |
| `Portfolio_Positions_*.csv` | Real positions and balances |
| `~/.config/rebal/*.credentials.json` | Coinbase API keys (when added) |
| Anything under `~/Downloads/` used as `--positions` | Same as above |

If `settings.json` was committed earlier, stop tracking it without deleting your copy:

```bash
git rm --cached settings.json
```

Use git **locally only** (no `git remote`) if you want zero cloud copies. No GitHub Actions
workflow is included in this project.

Optional CLI flags:

```bash
# Specific positions CSV
python rebal.py fidelity --positions ~/Downloads/Portfolio_Positions_May.csv

# Folder with one or more position CSVs (uses newest matching FILE_PATTERN)
python rebal.py fidelity --positions ~/Downloads/

python rebal.py --settings settings.json
```

### Advanced usage (e.g. future non-CSV / API sources)

The CLI always loads from CSV files. For other data sources (such as a future Coinbase view-only API), load your positions into a DataFrame and call the core directly:

```python
import pandas as pd
from rebal_core import run_rebalance, load_portfolio_config

full_config, portfolio_config = load_portfolio_config('settings.json', 'fidelity')

# Example: positions_df from API or other loader.
# The DF can use your configured SYMBOL_COLUMN / VALUE_COLUMN,
# or already be normalized to 'Ticker' / 'Current_Value'.
positions_df = pd.DataFrame({...})   # your data here

result = run_rebalance(
    positions_df=positions_df,   # or omit for CSV
    # export_path=... is not needed when positions_df is supplied
    targets_file='alloc.fidelity.csv',
    pct_of_max_file='pct_of_max_alloc.csv',
    full_config=full_config,
    portfolio_config=portfolio_config,
)
print_rebalance_report(result)
```

Tests use synthetic data under `tests/fixtures/` only (no `~/Downloads/` required).
Alloc and pct-of-max files are looked up in the current directory first, then beside
`--settings` if not found in cwd.
