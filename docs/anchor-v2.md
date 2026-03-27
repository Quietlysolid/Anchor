# Anchor v2

Anchor v2 is a reset of the strategy layer around published FX factor research rather than custom intraday sleeves.

This design keeps the system simple:

- 9 non-USD G10 currencies
- 9 USD pairs
- 3 academic strategy sleeves
- monthly rebalance
- equal-volatility sleeve weights by default

## Scope

Anchor v2 replaces the current custom strategy stack as the primary research direction.

It does not assume that legacy London trend, LCR, FIX, or NFP sleeves are invalid. It treats them as separate internal experiments, not as the foundation for the next system.

## Universe

Research universe:

- EUR
- JPY
- GBP
- CHF
- CAD
- AUD
- NZD
- SEK
- NOK

Tradable IBKR implementation:

- EUR.USD
- USD.JPY
- GBP.USD
- USD.CHF
- USD.CAD
- AUD.USD
- NZD.USD
- USD.SEK
- USD.NOK

Why this universe:

- close to the developed-market currency sets used in the carry, momentum, and value literature
- liquid enough for realistic IBKR execution
- wide enough to support cross-sectional ranking
- avoids the data and execution complexity of EMFX in phase 1

## Strategy Sleeves

### 1. Carry

Economic idea:

- currencies with higher short rates tend to outperform currencies with lower short rates on average
- the premium is compensation for crash and global risk exposure

Signal:

- rank currencies by 1-month interest rate or policy-rate proxy relative to USD
- higher yield = stronger carry score

Portfolio construction:

- long top 3 currencies by carry score
- short bottom 3 currencies by carry score

Primary references:

- Burnside, Eichenbaum, Rebelo
- Lustig, Roussanov, Verdelhan

### 2. Momentum

Economic idea:

- currencies that recently outperformed tend to keep outperforming over intermediate horizons

Signal:

- rank currencies by trailing 1-month total return versus USD
- phase 1 keeps the horizon fixed at 1 month to stay close to canonical research implementations

Portfolio construction:

- long top 3 currencies by momentum score
- short bottom 3 currencies by momentum score

Primary references:

- Burnside, Eichenbaum, Rebelo
- Menkhoff, Sarno, Schmeling, Schrimpf

### 3. Value

Economic idea:

- currencies that are cheap relative to long-run fair value tend to mean-revert over time

Signal:

- rank currencies by real exchange rate deviation from PPP-style fair value
- the more undervalued a currency is relative to USD, the stronger the value score

Portfolio construction:

- long top 3 undervalued currencies
- short top 3 overvalued currencies

Primary references:

- Frankel, Rose
- PPP and real exchange rate mean-reversion literature

## Rebalance Rules

Baseline rebalance:

- monthly
- first trading day of each month
- after New York close data has finalized for the prior month

Why monthly:

- closest to standard academic implementations
- reduces turnover and execution noise
- appropriate for carry, value, and medium-horizon momentum

## Weighting

Default portfolio construction:

- equal volatility weight within each sleeve
- equal capital allocation across sleeves

Phase 1 defaults:

- sleeve weights: 1/3 carry, 1/3 momentum, 1/3 value
- positions within sleeve scaled to a common trailing realized-vol target

Rationale:

- equal notional is too sensitive to pair-specific volatility
- equal-risk is simpler and more defensible than fitted weights at launch

## Pair Mapping

The ranking model is currency-centric. Execution is pair-centric.

Rules:

- if the selected currency is quoted as XXX.USD:
  - long currency = buy XXX.USD
  - short currency = sell XXX.USD
- if the selected currency is quoted as USD.XXX:
  - long currency = sell USD.XXX
  - short currency = buy USD.XXX

Examples:

- long JPY => sell USD.JPY
- short CHF => buy USD.CHF
- long EUR => buy EUR.USD
- short AUD => sell AUD.USD

## Data Requirements

Carry:

- policy rate or short-rate proxy by currency
- monthly history

Momentum:

- monthly FX total return versus USD

Value:

- CPI or other inflation index by country
- spot FX history
- real exchange rate / PPP deviation model

Execution and account state:

- IBKR

Historical market data:

- Polygon for spot history where available
- macro series from FRED or other official sources for rates and price indices

## Initial Research Runner

Phase 1 runner:

```bash
make v2-backtest CURRENCIES=EUR,JPY,GBP,CAD,AUD,NZD
```

Full three-sleeve run once carry and value inputs exist:

```bash
make v2-backtest \
  CURRENCIES=EUR,JPY,GBP,CHF,CAD,AUD,NZD,SEK,NOK \
  CARRY_CSV=/app/data/v2_carry.csv \
  VALUE_CSV=/app/data/v2_value.csv
```

Input contract for carry and value CSVs:

- `date`
- `currency`
- `value`

Template files:

- [v2_carry_template.csv](/opt/anchor/data/v2_carry_template.csv)
- [v2_value_template.csv](/opt/anchor/data/v2_value_template.csv)
- [v2_cpi_template.csv](/opt/anchor/data/v2_cpi_template.csv)

Pipeline helpers:

```bash
make v2-carry FRED_API_KEY=... OUTPUT=/app/data/v2_carry.csv
make v2-cpi   FRED_API_KEY=... OUTPUT=/app/data/v2_cpi.csv
make v2-value CPI_CSV=/app/data/v2_cpi.csv OUTPUT=/app/data/v2_value.csv
```

Current status:

- `carry` can be generated automatically from FRED for the covered subset in [v2_carry.py](/opt/anchor/backend/anchor/data/v2_carry.py)
- `cpi` can be generated automatically from FRED search + observation endpoints in [v2_cpi.py](/opt/anchor/backend/anchor/data/v2_cpi.py)
- `value` is generated from spot FX plus CPI data in [v2_value.py](/opt/anchor/backend/anchor/data/v2_value.py)
- `SEK` and `NOK` may require explicit series-map additions for carry if the desired FRED proxies are not yet supplied

## Risk Rules

Phase 1 portfolio rules:

- monthly rebalance only
- max 6 active directional positions per sleeve
- no EMFX
- no leverage beyond configured gross exposure cap
- portfolio vol target applied at sleeve and total-book level

Initial risk controls:

- gross exposure cap: 150% of NAV
- net USD exposure monitored continuously
- per-pair max weight cap: 20% gross
- rebalance turnover cap: configurable, default off for research

## Implementation Plan

### Phase 1: Research engine

- create monthly factor data pipeline
- compute carry, momentum, and value scores
- build monthly basket backtester
- measure return, volatility, max drawdown, turnover, and correlation between sleeves

### Phase 2: Portfolio execution model

- convert currency selections into IBKR tradable pair orders
- generate rebalance order list
- support partial fills and drift-to-target tracking

### Phase 3: Live paper deployment

- run the monthly portfolio on IBKR paper
- compare realized holdings and PnL against research model
- monitor slippage, spread costs, and rebalance drift

## Go/No-Go Criteria

Anchor v2 should not move beyond paper until:

- each sleeve is reproducible from source data end to end
- backtests are cost-adjusted and out-of-sample
- combined portfolio is superior to any single sleeve on risk-adjusted terms
- live paper tracking error versus model is operationally acceptable

## Explicit Non-Goals

Anchor v2 phase 1 does not include:

- intraday signal generation
- event-driven discretionary sleeves
- EMFX
- ML-driven parameter fitting
- optimized dynamic sleeve weights

The goal is a faithful, low-guesswork implementation of well-known FX factor strategies before adding complexity.
