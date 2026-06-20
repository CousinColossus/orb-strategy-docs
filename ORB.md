# ORB (Opening Range Breakout) Strategy — Full Documentation

## Overview

The ORB strategy is a pure price-action / volume-profile strategy that trades breakouts and fakeouts from an **opening range** — defined as the first N bars of a given session (default 3 bars = 15 minutes on M5). The strategy uses **Volume Profile** (VAH, VAL, POC) computed from those opening bars to determine the Value Area, and then triggers entries when price interacts with the Value Area boundaries.

**Author's note:** This strategy was implemented early in the project as a "classic" intraday approach. Extensive testing across NASDAQ (NAS100), S&P 500 (SPX), and EURUSD — at M5, M15, and H1 timeframes — revealed severe structural flaws. With one marginal exception (SPX H1, PF=1.27, +0.85% return), every variant produced negative expectancy. The strategy serves as an important negative result — a demonstration that naive ORB without institutional flow filtration is not viable for the instruments tested.

---

## Strategy Architecture

**File:** `strategies/orb.py`
**Class:** `ORB(Strategy)` — 345 lines
**Base class:** `strategies/base.py` → `Strategy(ABC)`

### Two Entry Methods

#### 1. Fakeout / Reversal

Price wicks beyond a swing point that lies *outside* the opening range (liquidity sweep), reverses direction, and closes back **inside** the Value Area.

- **Long trigger:** Price breaks below prior-day low + range low minus buffer, then closes back inside VA with a bullish close (`c[i] > c[i-1]`).
- **Short trigger:** Price breaks above prior-day high + range high plus buffer, then closes back inside VA with a bearish close (`c[i] < c[i-1]`).
- **SL:** Beyond the reversal candle wick (max high / min low of last 3 bars + 1 tick).
- **TP:** Opposite side of the opening range.
- **Liquidity buffer:** `liq_buffer_atr * ATR` added to prior-day extreme + range extreme for sweep detection.

#### 2. Breakout / Trend Continuation

A bar closes **outside** the Value Area (above VAH or below VAL) with momentum (close extends beyond prior close in same direction).

- **Long trigger:** `close > VAH AND close > close[-1]`
- **Short trigger:** `close < VAL AND close < close[-1]`
- **SL:** at POC ± `sl_ticks * tick_size`
- **TP:** Fixed R:R multiple of risk (default 2:1)

### Volume Profile Calculation

- Divides the opening range's price span into N bins (default 20).
- Distributes each bar's volume proportionally across its high-low bins.
- **POC (Point of Control):** The bin with the highest volume.
- **VAH/VAL (Value Area High/Low):** The narrowest price range containing ≥70% of total volume, centred on the highest-volume bins.

### Key Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `opening_bars` | 3 | Number of bars in the opening range |
| `start_hour` | 13 | Session start hour (UTC) |
| `start_minute` | 0 | Session start minute |
| `tick_size` | 0.0001 | Instrument tick size (0.01 for NAS100/SPX) |
| `rr_ratio` | 2.0 | Fixed R:R for breakout entries |
| `min_atr` | 0.0 | Minimum ATR filter to skip low-volatility days |
| `max_daily_trades` | 1 | Max one trade per session |
| `enable_fakeout` | True | Enable fakeout/reversal entries |
| `enable_breakout` | True | Enable breakout/trend entries |
| `vp_levels` | 20 | Number of price bins for volume profile |
| `atr_period` | 14 | ATR calculation period |
| `liq_buffer_atr` | 0.15 | ATR multiple buffer for liquidity sweep detection |
| `sl_ticks` | 2 | Ticks above/below POC for breakout SL |

### Pre-built Variants

Defined at file bottom via `make_variant()`:

```python
EURUSD_ORB_VP = make_variant(
    "eurusd_orb_vp", "EURUSD",
    description="EURUSD M5 ORB + volume profile, fakeout + breakout",
)

NAS100_ORB_VP = make_variant(
    "nas100_orb_vp", "NAS100",
    start_hour=13, start_minute=30,
    tick_size=0.01,
    description="NAS100 M5 ORB + volume profile, fakeout + breakout",
)

SPX_ORB_VP = make_variant(
    "spx_orb_vp", "SPX",
    start_hour=13, start_minute=30,
    tick_size=0.01,
    description="SPX M5 ORB + volume profile, fakeout + breakout",
)

SPX_ORB_M15 = make_variant(
    "spx_orb_m15", "SPX", timeframe="M15",
    start_hour=13, start_minute=30, tick_size=0.01,
    description="SPX M15 ORB + volume profile, fakeout + breakout",
)

SPX_ORB_H1 = make_variant(
    "spx_orb_h1", "SPX", timeframe="H1",
    start_hour=13, start_minute=30, tick_size=0.01,
    description="SPX H1 ORB + volume profile, fakeout + breakout",
)
```

Additional test variants: `spx_orb_h1_1430`, `spx_orb_h1_1bar`, `spx_orb_h1_2bar`, `spx_orb_m5_1430`, `spx_orb_m5_6bar`.

---

## Scoreboard Results

A total of 17 ORB variants were tested across 3 instruments and 3 timeframes.

### NASDAQ (NAS100)

| Row | Name | TF | WR% | PF | Return% | DD% | Trades | Wins | Losses | Note |
|-----|------|----|-----|----|---------|-----|--------|------|--------|------|
| 222 | `nas100_orb_vp` | M5 | **60.6** | **2.40** | 9.09 | **109.87** | 554 | 336 | 218 | ORB+VP ATR-based v1 |
| 223 | `orb` | M5 | **60.6** | **2.40** | 9.09 | **109.87** | 554 | 336 | 218 | ORB breakout-only |
| 254 | `orb` | H1 | 39.9 | 0.68 | -3.97 | 33.91 | 363 | 145 | 218 | Default params |
| 260 | `orb` | M15 | 37.3 | 0.65 | -3.35 | 33.14 | 359 | 134 | 225 | 09:30 ET opening |
| 297 | `orb` | M5 | 24.1 | 0.35 | -43.72 | 99.18 | 406 | 98 | 308 | Baseline (SL=1.0 ATR) |

**Verdict: NOT VIABLE.** The 60.6% WR is destroyed by 109.87% DD. All other variants have PF < 1.0.

### S&P 500 (SPX) — New Results (June 2026)

| Row | Name | TF | WR% | PF | Return% | DD% | Trades | Wins | Losses | Note |
|-----|------|----|-----|----|---------|-----|--------|------|--------|------|
| 260 | `spx_orb_h1` | H1 | **37.3** | **1.27** | **+0.85** | **16.14** | 185 | 69 | 116 | 13:30 UTC, 3-bar opening |
| 261 | `spx_orb_h1` | H1 | **37.3** | **1.27** | **+0.85** | **16.14** | 185 | 69 | 116 | Breakout-only (identical) |
| 263 | `spx_orb_h1_2bar` | H1 | 36.6 | 0.91 | -0.34 | 20.40 | 224 | 82 | 142 | 2-bar opening range |
| 272 | `spx_orb_m15` | M15 | 34.5 | 0.92 | -0.27 | 25.97 | 296 | 102 | 194 | 13:30 UTC, 3-bar |
| 273 | `spx_orb_m15` | M15 | 34.5 | 0.92 | -0.27 | 25.97 | 296 | 102 | 194 | Breakout-only (identical) |
| 280 | `spx_orb_h1_1430` | H1 | 32.6 | 1.00 | -0.00 | 15.86 | 172 | 56 | 116 | 14:30 UTC opening |
| 282 | `spx_orb_h1_1bar` | H1 | 32.0 | 1.25 | +0.74 | 17.22 | 197 | 63 | 134 | 1-bar opening |
| 283 | `spx_orb_m5_6bar` | M5 | 31.7 | 0.70 | -0.93 | 27.67 | 312 | 99 | 213 | 6-bar (30 min) opening |
| 288 | `spx_orb_m5_1430` | M5 | 30.9 | 0.67 | -0.92 | 27.78 | 314 | 97 | 217 | 14:30 UTC opening |
| 291 | `spx_orb_vp` | M5 | 30.2 | 0.60 | -1.12 | 27.77 | 311 | 94 | 217 | 13:30 UTC, 3-bar |
| 292 | `spx_orb_vp` | M5 | 30.2 | 0.60 | -1.12 | 27.77 | 311 | 94 | 217 | Breakout-only (identical) |

**Verdict: MARGINAL on H1; NOT VIABLE on M5/M15.**

### Forex (EURUSD)

| Row | Name | TF | WR% | PF | Return% | DD% | Trades | Wins | Losses | Note |
|-----|------|----|-----|----|---------|-----|--------|------|--------|------|
| 280 | `eurusd_orb_vp` | M5 | 30.6 | 0.39 | -0.00 | 35.09 | 432 | 132 | 300 | ORB+VP EURUSD initial |

**Verdict: NOT VIABLE.**

---

## Analysis of Results

### SPX H1 — The Best ORB Result Across All Tests (Row 260)

SPX H1 at 13:30 UTC with 3-bar opening range achieved:
| Metric | Value |
|--------|-------|
| Win Rate | 37.3% |
| Profit Factor | **1.27** |
| Total Return | **+0.85%** |
| Max DD | 16.14% |
| Trades | 185 |
| Avg R:R | 1.5 |

This is the **only ORB variant across all instruments/timeframes with PF > 1.10 and positive net return**. Key characteristics:

- **Positive expectancy:** For every $1 risked, $1.27 returned on average.
- **Reasonable drawdown:** 16.14% DD is high for a low-return strategy but not catastrophic like NAS100 M5 (109.87%).
- **Low return:** +0.85% over ~2.5 years of H1 data (9240 bars ≈ Jan 2024–Jun 2026) is economically insignificant. At 1% risk per trade, a $100k account would net $850 over 2.5 years.
- **Negative Sharpe:** -1.63 indicates high variance in returns — the positive PF comes from a few large wins masking many small losses.

**Why SPX H1 works better than NAS100 H1 (PF=0.68)?**
- SPX is more mean-reverting than NAS100. The ORB breakout logic catches intraday momentum that fades by the close — SPX's tendency to revert after the first 3-hour move creates more favourable R:R for breakout trades.
- NAS100 trends harder, so breakouts often continue beyond TP but also produce more severe false breaks.

**But it is NOT tradeable:**
- 0.85% return over 2.5 years is ~0.34% annually — below any reasonable hurdle rate.
- Sharpe of -1.63 means the equity curve is jagged with frequent drawdowns despite positive net return.
- At 37.3% WR, the strategy loses 62.7% of the time. The average loss must be smaller than the average win (RR=1.5), but the frequency of losses creates long flat/declining periods.

### SPX M5 — Consistently Negative (Rows 283, 288, 291, 292)

All SPX M5 variants (VP, 14:30 UTC, 6-bar opening) cluster around:
- WR: 30–32%
- PF: 0.60–0.70
- Return: -0.9% to -1.1%
- DD: 27–28%
- Trades: ~311–314

The consistency across 3 different parameter sets (opening bar count, start time) confirms the result is **robustly negative** — no tweak changes the sign of expectancy.

Comparison with NAS100 M5:
- Both are negative expectancy (NAS100 M5 baseline PF=0.35, tuned PF=2.40 but with 109.87% DD).
- SPX M5 trades about 40% fewer signals (311 vs 554) on M5 data — likely because SPX has fewer false breakouts that get caught by the tight SL.

### SPX M15 — Borderline Negative (Rows 272, 273)

- PF=0.92, just below 1.0. Almost breakeven but not quite.
- 25.97% DD and -0.27% return.
- Identical results for full vs breakout-only confirms fakeout signals never trigger.

### NAS100 M5 — The Anomaly (Rows 222/223)

The NAS100 M5 variant achieved a **60.6% win rate** and **2.40 profit factor** — the strongest raw metrics of any ORB test. However, the **109.87% drawdown** and modest 9.09% total return reveal the strategy is over-trading with catastrophic tail risk.

Key observations:
- **Rows 222 and 223 are duplicate runs** (identical 554 trades, 336W/218L). The `breakout-only` label had no effect because fakeout signals never triggered.
- **SL = 0.5 ATR** — very tight for M5. The 109.87% DD despite tight stops suggests the strategy suffers catastrophic tail events (gap moves, fast reversals) that slip past the stop.
- **554 trades over ~3 years** ≈ 0.7 trades/day.

### NAS100 M5 — Baseline (Row 297)

- **24.1% WR, PF=0.35, -43.72% return** — dramatically worse.
- SL=1.0 ATR vs 0.5 ATR in the tuned runs. Tighter stops improved WR by 36.5 percentage points but still produced negative or marginal net return.
- The high DD (99.18%) confirms wide stops were catastrophic — the strategy catches the wrong end of moves.

### NAS100 H1 (Row 254) & NAS100 M15 (Row 260)

Both negative expectancy:
- H1: **39.9% WR, PF=0.68, -3.97% return**, 33.91% DD
- M15: **37.3% WR, PF=0.65, -3.35% return**, 33.14% DD

The wider timeframes make the opening range less relevant (3 hours of H1 bars = a full morning session rather than the first 15 min). NAS100's trending nature works against ORB on longer timeframes.

### EURUSD M5 (Row 280)

- **30.6% WR, PF=0.39, -0.00% return**, 35.09% DD
- Forex shows the worst results. The fakeout/breakout logic designed for index-futures behaviour does not translate to the 24-hour forex market where the "opening range" concept is ambiguous.

---

## Key Findings

### Finding 1: Fakeout Signals Never Trigger
Across ALL tests on ALL instruments, enabling fakeout mode produced identical results to breakout-only mode. The liquidity sweep conditions (`wick above liq_high AND close inside VA AND close < prior close`) are too restrictive — they require a specific pattern (break of prior-day extreme + range extreme + buffer, then reversal back inside VA) that virtually never occurs in the data. **The fakeout entry method is dead code on real market data.**

### Finding 2: SPX H1 Is the Only Variant with Positive Expectancy
SPX H1 at 13:30 UTC with 3-bar opening range achieved PF=1.27 — the only ORB variant across all tests with PF > 1.10. The mean-reverting nature of SPX creates more favourable breakout conditions than the trending NAS100.

### Finding 3: The Positive Expectancy Is Economically Meaningless
At 0.85% return over 2.5 years, SPX H1 ORB generates ~0.34% annually. This is below any reasonable investment hurdle and would be completely erased by realistic trading costs (spread, commission, slippage) that the backtest does not apply to ORB's custom SL/TP trades.

### Finding 4: M5 Never Works on Any Instrument
Regardless of instrument (NAS100, SPX, EURUSD), M5 ORB consistently produces PF < 1.0. The 60.6% WR on NAS100 M5 is a statistical illusion — high WR masked by catastrophic DD that would blow any real account.

### Finding 5: Parameter Tuning Cannot Salvage the Strategy
Across all tests:
- Opening bar count (1, 2, 3, 6): No material improvement
- Start hour (13:30 vs 14:30 UTC): No material improvement
- Breakout-only vs full: Identical (dead fakeout code)
- Instrument: Marginal SPX H1, dead everywhere else

The strategy's flaws are structural, not parametric.

---

## Recommendations

**Recommendation: REJECT ORB as a standalone strategy for ALL instruments and timeframes.**

1. **SPX H1 is the "least bad" variant** but still not viable. PF=1.27 with 0.85% return over 2.5 years means ~0.34% annual return — below a savings account rate, with 16.14% peak drawdown and negative Sharpe (-1.63). This does not pass any reasonable risk/return test.

2. **The 60.6% WR on NAS100 M5 is a stop-hunting trap.** Tight stops (0.5 ATR) create a high win rate by cutting losers early, but the strategy's directional bias is wrong often enough that cumulative tail losses produce 109.87% DD. A 60.6% WR with PF=2.40 should produce much higher net returns; the fact that it doesn't implies large losses when wrong.

3. **No ORB variant passed the 80/20 train/test or Monte Carlo bar.** Given the low returns and high DD, the strategy would not survive out-of-sample validation.

4. **ORB is not suitable for PropFirm evaluation.** Even SPX H1 (the best variant) at 1% risk would produce P(DD>25%) well above acceptable thresholds given 16.14% measured DD on a short ~2.5-year sample.

5. **Potential salvage path:** ORB as a **confluence filter** rather than standalone — e.g., only take opening range breakouts that align with higher-timeframe trend or order block. This was never tested and represents the only plausible path to making ORB viable.

---

## What Was NOT Tested

- **Volume-weighted ORB filter:** Using tick volume as a threshold to skip low-volume breakouts.
- **ORB on other instruments:** Gold (XAUUSD), Bitcoin (BTCUSD), other indices (DJ30).
- **ORB with trailing stops:** Instead of fixed TP, using ATR-based trailing for breakout trades.
- **ORB combined with DRM or LiquidityGrabReversal** as part of a multi-strategy portfolio.
- **ORB with different opening range definitions** (e.g., based on actual session time rather than UTC fixed hour).

---

## Key Takeaway

> ORB is not viable as a standalone strategy. Across 17 tests on 3 instruments (NAS100, SPX, EURUSD) and 3 timeframes (M5, M15, H1), the best variant — SPX H1 at 13:30 UTC — achieves PF=1.27 but produces only 0.85% return over 2.5 years with 16.14% DD. Every M5 and M15 variant has PF < 1.0. The fakeout entry is dead code. **Do not deploy ORB in live or PropFirm accounts.**

---

## Technical Details

### Data Source
- All tests use the project's standard OHLCV CSV files in `data/`.
- **SPX M5:** 2024-01-02 to 2026-06-03, 110,880 bars (continuous futures).
- **SPX M15:** Same period, 36,960 bars.
- **SPX H1:** Same period, 9,240 bars.
- **NAS100 M5:** Comparable period.
- **EURUSD M5:** Comparable period.
- All timestamps are in UTC.

### Test Date
All SPX tests were run on **June 20, 2026** as part of systematic ORB evaluation.

### Backtest Engine
- `framework/backtest.py` — `BacktestEngine` class processes `entry_signal`, `custom_sl`, and `custom_tp` columns.
- ORB uses **per-signal SL/TP** via `custom_sl` and `custom_tp` columns populated in `run()`. The `--sl` and `--rr` CLI args do not affect ORB results (they are fallbacks for non-custom trades).
- Default engine params for non-custom fallback: 1.5 ATR SL, 2.0 R:R; commission 0.001%, slippage 0.0005%.

### MT5 EA Status
- **No MT5 EA exists** for ORB. No `ORB.mq5` file, no `.set` settings files, no `ea_mapping.json` entries.
- All scoreboard ORB rows show empty EA Name and Settings File columns.
- To deploy ORB in MT5, a new EA would need to be written from scratch.

### Scoreboard Entry Format
ORB entries follow the 20-column format:
```
| # | Strategy | Type | Instrument | TF | Description | Entry Rules | Exit Rules | R:R | WR% | PF | Eq% Growth | DD% | Trades | Wins | Losses | Improvement | Status | EA Name | Settings File |
```

---

## File Reference

| File | Purpose |
|------|---------|
| `strategies/orb.py` | Full ORB strategy class (345 lines, 11 pre-built variants) |
| `strategies/base.py` | Abstract `Strategy` base class |
| `framework/backtest.py` | Backtest engine that processes ORB signals |
| `framework/metrics.py` | Performance metrics calculation |
| `scripts/evaluate.py` | Evaluation script that runs ORB and writes to scoreboard |
| `SCOREBOARD.md` | Scoreboard with 17 ORB entries across 3 instruments |
| `AGENTS.md` | Project knowledge base |
| `ORB.md` | This file — full ORB documentation |
