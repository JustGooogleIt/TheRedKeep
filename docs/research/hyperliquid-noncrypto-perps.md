# Hyperliquid non-crypto perps (research note, 8 Nov 2026)

Why it matters for The Red Keep: 24/7 perps can measure an event's jump in windows when the cash market is closed (weekends, holidays, after hours). This note says which factors that works for.

## Summary
- Almost all non-crypto perps on Hyperliquid are HIP-3 markets from one deployer, **trade[XYZ]** (dex `xyz`), built by Unit Labs (Hyperunit) and run by XYZ Ltd (BVI). ICE took a minority stake on 29 Oct 2026. About 97-98% of HIP-3 open interest and volume [3][4].
- Spec index lists 118 markets [1]: 5 indices (SP500, XYZ100, JP225, KR200, NIFTY), 17 ETFs (incl. SMH, TLT, SOXL, XLE), 3 FX pairs (EUR, JPY, GBP), 9 commodities (WTI, Brent, natural gas, gold, silver, platinum, palladium, copper, diesel), 84 single stocks.
- Minor deployers: Markets by Kinetiq (`km`, Jan 2026, Kaiko oracle; 23 small markets incl. US500, USOIL, USBOND, EUR, GOLD) [12]; EntropyIO (pre-IPO); Paragon (crypto indices).
- Shut down or delisted: Felix (Jun 2026), Ventuals (Jun 2026), HyENA (Sep 2026) [10]; xyz:DXY, xyz:VIX, xyz:VOL delisted [1][11].
- **No products** for yields/rates, credit, TIPS/breakevens or equity VIX.
- The perp only discovers its own price while the reference market is closed; otherwise its oracle is the external price.
- Weekend evidence is good on average: a Blockworks study of 614 market-weekends (Mar-Jul 2026) found the last weekend price cut Monday-gap error by 53%, slope 0.83 [5]. Oil strongest (80-88% of Monday gap variance hedged), gold weak (17%) [7].

## Coverage by Red Keep factor
| Factor | Best market (launch, max leverage) | Liquidity | Usable 24/7? | Notes |
|---|---|---|---|---|
| US stocks | xyz:SP500 (18 Mar 2026, 50x); xyz:XYZ100 (13 Oct 2025, 30x) | Sep-26 volume $5.47B / $5.66B (~$185M/day each); SP500 OI $421M on 28 Oct | Yes | Weeknights track futures-implied price; own discovery weekends, holidays, 5-6pm ET. Weekend moves capped near +/-4% |
| Oil | xyz:CL (WTIOIL, early 2026, 20x); xyz:BRENTOIL | WTI Sep $7.13B (~$240M/day), OI $265M; Brent $4.02B | Yes (best) | Caps +15.8% / -14.3%; hit the cap on the 28 Feb 2026 Iran-strike weekend |
| Chips and AI | xyz:NVDA (by Jan 2026, 20x); xyz:SMH (20x) | NVDA < $1.76B in Sep; SMH ~$3.5M/day (undated) | Yes for NVDA; SMH thin | Weeknights anchored to Blue Ocean ATS prices |
| Gold | xyz:GOLD (by Jan 2026, 25x) | Sep $1.76B; OI $322M | Partly | Weak weekend signal |
| US dollar | xyz:EUR, xyz:JPY (~May 2026, 50x) | All FX ~0.5% of xyz volume (~$10M/day) | Marginal | DXY delisted; build a synthetic |
| 10-year yield | xyz:TLT (20x); km:USBOND (10x) | ~$2M/day and ~$0.5M/day (undated) | Marginal | TLT is 20+ yr duration |
| China | xyz:BABA, xyz:KSTR (10x) | ~$8M/day and < $1M/day (undated) | Weak | No FXI or Hang Seng |
| VIX | none | - | No | Only Bitcoin implied vol (BVIV) [13] |
| Credit | none | - | No | No HYG, LQD, IEF |
| Inflation expectations | none | - | No | No TIP or IEF |

## How pricing works [1]
- Deployer pushes oracle, mark and external prices about every 3 s, each update clamped to +/-50bp.
- Mark = median of (oracle; oracle + 150 s average of (mid - oracle); median of bid, ask, last).
- Market open: oracle = external price (cash index or futures-implied; metals spot; energy designated futures; stocks 24/5 incl. Blue Ocean ATS).
- Market closed: oracle starts at the last external price and drifts toward the book with a 30-minute time constant (8h, then 1h from 21 Nov 2025, 30 min from 30 Apr 2026). 30 minutes after a jump the oracle shows ~63% of it: **measure windows on trades or mids, not the oracle.**
- Discovery bounds keep the mark within +/-1/max-leverage of a reference, re-anchor a set number of times, then hard-cap until reopen. No other halts.
- Funding hourly, at 0.5x Hyperliquid's formula since 19 Dec 2025.

## Data access [2][16]
- `POST api.hyperliquid.xyz/info` with `candleSnapshot`; HIP-3 coins need the dex prefix (`xyz:SP500`). Intervals 1m to 1M.
- Only the latest 5,000 candles are kept (~3.5 days at 1m): record via websocket or buy history.
- Rate limit 1,200 weight/min/IP. `fundingHistory` covers HIP-3. Raw fills on requester-pays S3 [6].
- Vendors: Tardis (HIP-3 from 2 Dec 2025), 0xArchive (HIP-3 trades from 13 Oct 2025), Dwellir (1s and 1m candles).
- Not checked live (API blocked from the research sandbox).

## Caveats for the engine
- Oracle risk: one bad pre-market print sent xyz:SKHYNIX down 17.9% on 28 Jul 2026 ($57M liquidated) [9]. Filter outlier prints.
- Thin books: oil depth ~1/125 of CME; weekend depth 31-50% of weekday, median order $106 [5][6].
- Censoring: on the Iran-strike weekend the bound cut oil's move to ~45% of Monday's gap [6]. Flag windows where the mark sits at its bound.
- Short, changing history: most markets are under a year old; rules changed repeatedly in 2026.
- Concentration: one deployer holds nearly everything; shutdowns end series abruptly.
- Access: trade.xyz blocks US, Canadian and UK persons [15]; the S&P licence covers eligible non-US users [8]; ICE plans a CFTC-regulated US venue [14]. The Blockworks study was funded by the Hyperliquid Policy Center.
- Weeknights, CME futures (ES, ZN, CL, GC, DX, VX) are deeper; Hyperliquid's unique value is weekends and holidays.
- Aside: trade.xyz has run HIP-4 event (prediction) contracts since 10 Sep 2026 on the same API: odds and prices on one clock [15].

## Sources
1. trade.xyz docs, accessed 8 Nov 2026: [spec index](https://docs.trade.xyz/perpetuals/specifications-and-schedules/specification-index.md), [oracle](https://docs.trade.xyz/perpetuals/mechanics/oracle-price.md), [bounds](https://docs.trade.xyz/perpetuals/mechanics/discovery-bounds.md), [oracle time-constant changelog](https://docs.trade.xyz/perpetuals/changelog/oracle-time-constant-updates.md)
2. Hyperliquid docs: [info endpoint](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint.md), [rate limits](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits), [historical data](https://hyperliquid.gitbook.io/hyperliquid-docs/historical-data), [HIP-3](https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-3-builder-deployed-perpetuals)
3. [0xArchive: what is trade.xyz](https://0xarchive.io/resources/market-data-guides/what-is-tradexyz), 4 Oct 2026
4. [TokenPost](https://www.tokenpost.com/news/investing/42970), 28 Oct 2026
5. [Blockworks Research: price discovery in Hyperliquid's weekend markets](https://0xarchive.io/resources/research/price-discovery-in-hyperliquids-weekend-markets), 3 Aug 2026
6. [Castle Labs: 432 hours of Hyperliquid oil market](https://research.castlelabs.io/p/432-hours-of-hyperliquid-oil-market), Feb-Mar 2026 data
7. [AK20061/Hyperliquid-weekend-hedging](https://github.com/AK20061/Hyperliquid-weekend-hedging), undated
8. [S&P DJI licenses S&P 500 to Trade XYZ](https://press.spglobal.com/2026-03-18-S-P-Dow-Jones-Indices-Licenses-S-P-500-R-to-Trade-XYZ-for-Perpetual-Contracts-on-Hyperliquid), 18 Mar 2026
9. [Nexus Mutual: xyz:SKHYNIX flash crash incident report](https://nexusmutual.io/blog/xyz-skhynix-flash-crash-on-hyperliquid-incident-report), Jul 2026
10. [Felix shutdown](https://www.kucoin.com/news/flash/felix-first-hip-3-platform-on-hyperliquid-announces-shutdown), Jun 2026; [Ventuals winds down](https://thedefiant.io/news/defi/hyperliquid-based-ventuals-winds-down-on-chain-pre-ipo-markets), 15 Jun 2026; [HyENA shuts down](https://crypto.news/hyena-shuts-down-after-processing-4b-dollars-in-trades/), 28 Aug 2026
11. oakresearch: [DXY](https://oakresearch.io/en/hyperliquid/hip-3/markets/xyz:DXY), [VIX](https://oakresearch.io/fr/hyperliquid/hip-3/markets/xyz:VIX), [VOL](https://oakresearch.io/en/hyperliquid/hip-3/markets/xyz:VOL)
12. [DefiLlama: Markets by Kinetiq](https://defillama.com/rwa/perps/venue/markets-by-kinetiq); [Kaiko on Kinetiq's oracle](https://www.kaiko.com/news/how-kaikos-oracle-and-institutional-rates-power-kinetiqs-24-7-global-on-chain-perpetual-markets)
13. [Buildix: VIX perps and BVIV](https://www.buildix.trade/blog/vix-perpetual-futures-cboe-bviv-hyperliquid-funding-2026), 3 Oct 2026
14. [Bankless: NYSE owner backs trade.xyz](https://www.bankless.com/read/news/nyse-owner-druckenmiller-back-trade-xyz-hyperliquid-perps-us.md), 29 Oct 2026
15. [Datawallet: trade.xyz explained](https://www.datawallet.com/crypto/tradexyz-explained), updated 28 Sep 2026
16. [Tardis](https://docs.tardis.dev/historical-data-details/hyperliquid); [0xArchive API](https://docs.0xarchive.io/hyperliquid-hip3-data-api); [Dwellir](https://www.dwellir.com/docs/hyperliquid/historical-data)
17. [hyperliquidguide: FX perps](https://hyperliquidguide.com/ecosystem/hyperliquid-fx-perpetuals), 29 Oct 2026
18. [Blockworks 0xResearch: silver broke first](https://blockworks-research.beehiiv.com/p/silver-broke-first), 2 Feb 2026

Could not confirm: exact launch dates for CL/WTIOIL, GOLD, SILVER, NVDA, TLT, SMH; whether the API coin is "CL" or "WTIOIL" (check `meta` with `dex: "xyz"`); live liquidity.
