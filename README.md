# Anchor

Anchor is a futures trading and review system built around IBKR.

## What it does

- Trades futures on an IBKR paper account
- Currently focuses on `MNQ`, `ZN`, `MGC`, and `MCL`
- Runs signal generation, execution, and trade management
- Includes a simplified UI for monitoring positions, orders, updates, and results
- Includes a manual Robinhood trade journal for learning and review
- Tracks manual trade dates, edits, deletes, and simple daily profit/loss results

## Stack

- Backend: Python, FastAPI, SQLAlchemy, Celery
- Frontend: React, TypeScript, Vite
- Database: PostgreSQL
- Broker: IBKR

## Current Direction

Anchor is designed to be easier to understand than a typical broker platform.

The current version focuses on:

- broker truth from IBKR for live positions, orders, and account state
- a beginner-friendly frontend with simpler language
- a manual trade journal and results view for learning over time

## Notes

- The live trading broker integration is IBKR.
- The manual journal flow is designed to support Robinhood-based manual execution and review.
- `v3.0` is the current stable baseline.
