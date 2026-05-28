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
```

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
| `kiss_settings.json` | Reserves, account filter, portfolio registry |
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

### Multi-portfolio (recommended)

```json
{
  "default_portfolio": "fidelity",
  "portfolios": {
    "fidelity": {
      "display_name": "Fidelity Kiss Portfolio",
      "ACCOUNT_FILTER": "Kiss Portfolio",
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

Optional: `ACCOUNT_FILTER` (exact `Account Name` in export), `FILE_PATTERN`, `display_name`.

## Portfolios in this repo

| Key | Alloc file |
|-----|------------|
| `fidelity` | `kiss_alloc.fidelity.csv` |
| `fidelity-crypto` | `kiss_alloc.fidelity-crypto.csv` |
| `coinbase-btc` | `kiss_alloc.coinbase-btc.csv` |
| `coinbase-eth` | `kiss_alloc.coinbase-eth.csv` |

## Development

Automated tests and a split between core logic and CLI are planned in follow-up work. After those land:

```bash
pytest -q
```
