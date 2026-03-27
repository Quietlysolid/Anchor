# Anchor Futures v1

Anchor Futures v1 is a reset of Anchor from a forex-specific system into a structured, fully automated futures portfolio engine.

The first version is intentionally narrow:

- 5 liquid futures markets
- 1 proven strategy family
- daily bars
- fully automated paper trading
- no manual contract selection, no manual rolling, no manual order entry

## Goal

Build a futures-first paper trading system that can run unattended, be evaluated honestly, and only later be considered for real capital.

Anchor Futures v1 is not trying to be clever.
It is trying to be:

- simple
- systematic
- observable
- robust

## Scope

This replaces forex as the primary research direction.

Forex stays frozen as a legacy branch of the system until Futures v1 has:

- a stable backtest
- a stable paper-trading run
- clean automation around contract handling and risk

## First Universe

Use liquid CME contracts with micro sizing where practical.

### Equity Indexes

- `MES` — Micro E-mini S&P 500
- `MNQ` — Micro E-mini Nasdaq-100

Why:

- deep liquidity
- strong trend-following history
- clean first markets for automation

### Rates

- `ZN` — 10-Year U.S. Treasury Note futures

Why:

- gives the system a risk-off / rates market
- diversifies equities and commodities
- widely used in systematic futures portfolios

### Commodities

- `MGC` — Micro Gold
- `MCL` — Micro WTI Crude Oil

Why:

- gold adds defensive / macro behavior
- crude adds cyclical / inflation-sensitive behavior
- both are core managed-futures markets

## First Strategy

### Trend / Time-Series Momentum

Plain-English idea:

- if a market has been trending up over a medium horizon, be long
- if a market has been trending down over a medium horizon, be short
- if trend is weak or unclear, stay flat

This is the most defensible first strategy because it is:

- widely studied
- used in managed futures
- simple to automate
- naturally multi-market

## Baseline Rules

Version 1 should stay simple.

Suggested baseline:

- data frequency: daily
- signal refresh: daily after market close
- rebalance cadence: weekly
- holding logic: maintain long, short, or flat until next rebalance
- position sizing: volatility-scaled
- leverage: conservative by default

Current research baseline:

- markets: `MES`, `MNQ`, `ZN`, `MGC`, `MCL`
- lookback: `63` trading days
- rebalance: `weekly`
- flat zone: `1%`
- transaction cost assumption: `1 bp` per unit of turnover
- roll cost assumption: `2 bps` when an active market rolls

Current stronger candidate:

- `MNQ`, `ZN`, `MGC`, `MCL`
- same settings as the baseline above
- `MES` is currently the weakest market in the first robustness pass

Current paper-trading configuration:

- active markets: `MNQ`, `ZN`, `MGC`, `MCL`
- target gross exposure: `2.50x`
- max estimated margin usage: `35%`
- max contracts per market: `2`

Initial signal definition:

- long if medium-horizon trend is positive
- short if medium-horizon trend is negative
- flat if the signal is too small to justify a position

The exact indicator can be finalized in implementation, but version 1 should use one transparent trend rule rather than a stack of filters.

## Automation Requirements

Paper trading must be fully automated.

That means:

- contract lookup is automatic
- front-month selection is automatic
- roll scheduling is automatic
- position sizing is automatic
- margin checks are automatic
- order submission is automatic
- health monitoring is automatic

Human involvement is limited to:

- reading logs
- reviewing metrics
- changing code or config
- restarting or redeploying when needed

## Contract Handling

Futures v1 must explicitly handle contract mechanics.

### Contract Specs

The system needs to know for each market:

- exchange symbol
- contract multiplier
- tick size
- tick value
- trading hours
- expiry month cycle

Source of truth:

- CME contract specifications
- IBKR contract metadata

### Front-Month Selection

The bot should trade the active front contract, with exclusions for contracts that are too close to expiry.

Simple v1 rule:

- maintain a predefined contract chain per market
- trade the nearest sufficiently liquid contract that is not inside the roll window

### Roll Rule

Version 1 should use a simple deterministic rule.

Example:

- roll `N` business days before first notice or last trade date, depending on the contract

This is better than trying to be clever with volume-switch logic in the first version.

## Risk Framework

Risk should be portfolio-first, not market-by-market guesswork.

### Position Sizing

Use volatility-scaled sizing so one market does not dominate just because it moves more per day.

Plain-English idea:

- calmer markets get larger position sizes
- more volatile markets get smaller position sizes

### Portfolio Limits

Version 1 should include:

- maximum position size per market
- maximum total gross exposure
- maximum portfolio heat
- minimum free margin threshold
- auto-halt on order rejects or repeated contract lookup failures

### Drawdown Rules

Version 1 should also include:

- soft drawdown reduction
- hard drawdown halt
- daily loss circuit breaker

## Data Requirements

Anchor Futures v1 needs:

- daily historical futures prices for research
- live or delayed futures market data for paper execution
- contract metadata
- account and margin state from IBKR

Preferred execution broker:

- IBKR paper account

Expected research CSV contract:

- one file per market in `/opt/anchor/data`
- file name format: `<MARKET>_D.csv`
- required columns:
  - `time`
  - `close`
- optional columns:
  - `open`
  - `high`
  - `low`
  - `volume`

First backtest runner:

```bash
make futures-v1-backtest
```

Current target export helper:

```bash
PYTHONPATH=/opt/anchor/backend /opt/anchor/.venv/bin/python -m anchor.futures.cli_targets --data-dir /opt/anchor/data
```

## Paper-Trading Workflow

The intended loop is:

1. deploy code
2. let the futures system run unattended in paper
3. inspect logs, fills, PnL, exposure, and failures
4. fix code if behavior is wrong
5. repeat
6. only after stable paper performance, consider real capital

No manual trade entry is part of the design.

## What Carries Over From Anchor

These parts remain valuable:

- backend service structure
- scheduler / jobs framework
- DB and event logging
- health and operator-state endpoints
- broker abstraction layer
- UI shell for monitoring

## What Must Change

These parts are forex-specific and should not drive Futures v1:

- spot FX pair assumptions
- OANDA-era data model and naming
- forex session logic
- London/FIX/NFP sleeves
- pair-centric risk sizing

## Proposed Build Order

1. Create a futures universe/config layer
2. Add contract metadata and roll logic
3. Build a daily futures backtester for trend
4. Build a paper execution path for the 5 target markets
5. Add portfolio risk and margin guards
6. Run fully automated paper trading
7. Review results before any live step

## Success Criteria For v1

Futures v1 is ready to graduate from paper only if:

- automation is stable
- rolls happen cleanly
- margin checks behave correctly
- risk controls hold under stress
- paper trading runs without operator intervention
- performance is stable enough to justify deeper validation

Until then, it remains a paper research system.
