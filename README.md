# Rebalance KISS

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
cp kiss_settings.example.json kiss_settings.json   # first time only; then edit locally
```

Your real `kiss_settings.json` stays on your machine only (see [Privacy](#privacy)).

## Usage

```bash
python rebal_kiss.py                  # default portfolio from settings
python rebal_kiss.py fidelity         # specific portfolio key
python rebal_kiss.py fidelity-crypto
```

The script looks for a position export matching each portfolio’s `FILE_PATTERN`:

1. `~/Downloads/` (most recent file wins)
2. Current working directory (fallback)

Default pattern for Fidelity-style exports: `Portfolio_Positions_*.csv`.

## Project files

| File | Purpose |
|------|---------|
| `rebal_kiss.py` | Main script |
| `kiss_settings.json` | **Local only** — reserves, account filter, portfolio registry |
| `kiss_settings.example.json` | Safe template to copy; safe to commit/share |
| `kiss_pct_of_max.csv` | Global “aggressiveness” per asset class (`Pct_of_Max`) |
| `kiss_alloc.{portfolio_key}.csv` | Target max weights per ticker for that portfolio |

Portfolio keys must match `^[a-zA-Z0-9_-]+$` and map to `kiss_alloc.{key}.csv`.

### Example alloc file (`kiss_alloc.fidelity.csv`)

```csv
Asset_Type,Ticker,Max_Allocation_Pct
Stocks,VTI,26
...
```

- `Max_Allocation_Pct` must sum to **100.0** (non-CASH rows; CASH row rules are enforced in code).
- Every `Asset_Type` in an alloc file must appear in `kiss_pct_of_max.csv`.

### Global pct-of-max (`kiss_pct_of_max.csv`)

```csv
Asset_Type,Pct_of_Max
Stocks,100
Gold,0
...
```

Target weight for a line = `Max_Allocation_Pct × (Pct_of_Max / 100)`.

## Settings (`kiss_settings.json`)

Copy from `kiss_settings.example.json` and edit. The live file is listed in `.gitignore`
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
      "BILLS_PER_MONTH_IN_USD": "$21,303",
      "CASH_FOR_BILLS_IN_MONTHS": "0.6529",
      "TAX_OWED_IN_USD": "$0"
    },
    "fidelity-crypto": {
      "display_name": "Fidelity Crypto",
      "FILE_PATTERN": "Portfolio_Positions_*.csv",
      "BILLS_PER_MONTH_IN_USD": 0,
      "CASH_FOR_BILLS_IN_MONTHS": 0,
      "TAX_OWED_IN_USD": 0
    }
  }
}
```

### Legacy single-portfolio

Top-level keys (no `portfolios` object) still work; the script treats the portfolio key as `fidelity` and uses `kiss_alloc.fidelity.csv`.

Required per portfolio: `BILLS_PER_MONTH_IN_USD`, `CASH_FOR_BILLS_IN_MONTHS`, `TAX_OWED_IN_USD`. Dollar amounts may include `$` and commas.

Optional: `ACCOUNT_FILTER` (`column` + `value` matched in the positions CSV), `FILE_PATTERN`,
`display_name`. Legacy string values still work (treated as `Account Name` = that string).

## Portfolios in this repo

| Key | Alloc file |
|-----|------------|
| `fidelity` | `kiss_alloc.fidelity.csv` |
| `fidelity-crypto` | `kiss_alloc.fidelity-crypto.csv` |
| `coinbase-btc` | `kiss_alloc.coinbase-btc.csv` |
| `coinbase-eth` | `kiss_alloc.coinbase-eth.csv` |

## Development

Logic lives in `rebal_core.py` (testable, no printing). `rebal_kiss.py` is the CLI.

```bash
./scripts/check.sh
# or: pytest -q
```

Optional: run tests before every commit on **your machine only**:

```bash
pip install pre-commit
pre-commit install
```

This does not upload data anywhere. Skip GitHub Actions / remote CI if you prefer everything local.

## Privacy

**Safe to keep in git (no personal holdings):** `rebal_kiss.py`, `rebal_core.py`, `kiss_alloc.*.csv`
(target weights only), `kiss_pct_of_max.csv`, `kiss_settings.example.json`, `tests/fixtures/`.

**Keep local — never commit:**

| File | Why |
|------|-----|
| `kiss_settings.json` | Bills, taxes, account names |
| `Portfolio_Positions_*.csv` | Real positions and balances |
| Anything under `~/Downloads/` used as `--positions` | Same as above |

If `kiss_settings.json` was committed earlier, stop tracking it without deleting your copy:

```bash
git rm --cached kiss_settings.json
```

Use git **locally only** (no `git remote`) if you want zero cloud copies. No GitHub Actions
workflow is included in this project.

Optional CLI flags:

```bash
# Specific positions CSV
python rebal_kiss.py fidelity --positions ~/Downloads/Portfolio_Positions_May.csv

# Folder with one or more position CSVs (uses newest matching FILE_PATTERN)
python rebal_kiss.py fidelity --positions ~/Downloads/

python rebal_kiss.py --settings kiss_settings.json
```

Tests use synthetic data under `tests/fixtures/` only (no `~/Downloads/` required).
Alloc and pct-of-max files are looked up in the current directory first, then beside
`--settings` if not found in cwd.
