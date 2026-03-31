# Anchor

Anchor is my live futures trading system.

It is built for IBKR and focused on the setup I use now.

## What Anchor does

- Trades futures through IBKR
- Focuses on MES and MNQ
- Runs a live trading workflow
- Keeps risk rules and system checks in one place

## What this repo is for

This repo is for the live system I actually use.

It is not meant to be a mix of old forex code, research projects, and retired tools.

## Main focus

- Broker: IBKR
- Markets: futures
- Symbols: MES, MNQ
- Goal: simple, steady live trading

## Main parts

- Backend API and runtime
- Scheduler and live jobs
- Execution and trade management
- Frontend for monitoring and control

## Start

1. Copy `.env.example` to `.env`
2. Fill in your live settings
3. Start the stack
4. Run migrations if needed

## Direction

The direction of this project is simple:

- Futures only
- IBKR only
- Live trading first
- Remove old paths that are no longer used
