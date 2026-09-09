---
title: USDT Investment Bot
emoji: 💎
colorFrom: green
colorTo: blue
sdk: docker
pinned: false
app_port: 7860
---

# USDT Investment Bot

Telegram bot for managing a USDT digital investment system.

## Environment Variables

Set these in the Space settings (Settings > Variables and secrets):

| Variable | Description |
|----------|-------------|
| `BOT_TOKEN` | Telegram bot token from @BotFather |
| `BSCSCAN_API_KEY` | BscScan API key |
| `TRONGRID_API_KEY` | TronGrid API key |
| `BEP20_ADDRESS` | Deposit address (BEP-20) |
| `TRC20_ADDRESS` | Deposit address (TRC-20) |

## Keeping the bot awake

The free plan puts the Space to sleep after ~48h of no traffic.
Use a free uptime monitor (e.g. UptimeRobot) pinging
`https://<your-space>.hf.space/` every 5 minutes to keep it alive.