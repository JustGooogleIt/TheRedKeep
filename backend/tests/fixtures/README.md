# Test fixtures

The JSON files here copy the **shape** of live API responses read on 2026-11-08
(Polymarket Gamma `/events` and `/public-search`, CLOB `/prices-history`, Kalshi
`/events` and candlesticks), trimmed to the fields the code reads. The **values are
made up** for the tests. Nothing in this folder is market data and nothing here
should be quoted as odds.

`resolved.json` does the same for the calibration study: closed Polymarket events
(`/events?closed=true`) and a daily price history, and Kalshi's series list, settled
markets from the live list and the archive (`/historical/markets`), and daily candles
from both, whose price fields are named differently. The markets, prices and
outcomes in it are invented.
