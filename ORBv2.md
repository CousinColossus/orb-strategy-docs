# ORBv2 (Opening Range Breakout v2) — Full Documentation

## Overview

ORBv2 refines the original ORB with **Smart Money concepts**: pre-ORB liquidity sweep detection, stricter reversal confirmation, and a family of variants that normalize risk or specialize entry logic. The core mechanism — 3-bar Volume Profile opening range with VAH/VAL/POC — is inherited from ORBv1, but the entry rules, sweep timing, and risk management are substantially upgraded.

**File:** `strategies/orbv2.py` — 1035 lines
**Base class:** `strategies/orb.py` → `ORB(Strategy)`

---

## ORBv2 Base Class

**Class:** `ORBv2(Strategy)` — lines 8–326

### Parameters

| Param | Default | Description |
|-------|---------|-------------|
| `start_hour` | 13 | Session start hour (UTC) |
| `start_minute` | 30 | Session start minute (UTC) |
| `tick_size` | 0.01 | Instrument tick size |
| `rr_ratio` | 2.0 | Fixed risk:reward for breakout entries |
| `max_daily_trades` | 1 | Max one trade per day |
| `vp_levels` | 20 | Number of bins for volume profile |
| `atr_period` | 14 | ATR calculation period |
| `liq_buffer_atr` | 0.15 | ATR multiplier for liquidity sweep threshold |
| `sl_ticks` | 2 | Ticks from POC for breakout SL |
| `pre_orb_lookback` | 20 | Bars to scan pre-ORB for liquidity sweeps |

### Entry Method 1: Reversal (Liquidity Sweep)

Price sweeps below a swing low (long) or above a swing high (short) — defined as the more extreme of the opening range boundary and the prior day's range, plus/minus an ATR-based buffer — then closes back **inside** the Value Area (VAL < close < VAH).

**Key difference from ORBv1:** The sweep detection is stateful — once a sweep is detected (`sweep_active = True`), subsequent bars are checked for a reversal back inside the VA. The reversal bar must also satisfy:

  - **Long:** `close > close[-1]` AND `close > (high + low) / 2` (strong bullish bias)
  - **Short:** `close < close[-1]` AND `close < (high + low) / 2` (strong bearish bias)

This midpoint filter is stricter than v1's simple close-direction check.

**SL:** Minimum low of current + prior bar minus 1 tick (long), or maximum high of current + prior bar plus 1 tick (short).

**TP:** Opposite side of opening range (range_high + tick for longs, range_low − tick for shorts).

**Sweep timeout:** If no reversal entry fires within 6 bars of the sweep, the sweep state resets.

### Entry Method 2: Breakout / Trend Continuation

A bar closes **outside** the Value Area:

  - **Long trigger:** `close > VAH` AND (`pre_orb_swept` OR `close > close[-1]`)
  - **Short trigger:** `close < VAL` AND (`pre_orb_swept` OR `close < close[-1]`)

**SL:** `POC ± sl_ticks × tick_size`

**TP:** Fixed R:R multiple (`rr_ratio × risk`)

### Pre-ORB Liquidity Sweep

Before the opening range is established, the strategy scans `pre_orb_lookback` bars (default 20) looking for a liquidity sweep beyond the prior day's range (+ buffer). If found, `pre_orb_swept = True` — this allows breakout entries even without a same-session confirmation close, on the theory that institutional order flow has already demonstrated intent.

---

## Variant: FixedSL

**Class:** `ORBv2_FixedSL(ORBv2)` — lines 383–601

Replaces the base class's variable SL/TP logic with a flat **0.5 ATR stop** and **fixed 2:1 R:R** for all entries (both reversal and breakout).

### Rationale

The base spec's reversal entries use a local-bar-minimum SL (which varies with volatility) and TP at the opposite side of the opening range (which also varies). The FixedSL variant normalises every trade to a consistent 0.5 ATR risk, making position sizing truly uniform.

### Changes from Base

- Reversal SL: `entry ± 0.5 × ATR` instead of bar-min/max ± 1 tick
- Reversal TP: `entry ∓ rr_ratio × 0.5 × ATR` instead of opposite-side-of-range
- Breakout SL/TP: same flat 0.5 ATR scheme instead of POC ± 2 ticks
- Reversal confirmation relaxed slightly — uses `close > close[-1]` only (removes the midpoint test) since the fixed SL already controls risk

### Registered Variants

| Name | Instrument | Description |
|------|-----------|-------------|
| `spx_orbv2_fixsl` | SPX | SPX M5 fixed ATR SL |
| `nas100_orbv2_fixsl` | NAS100 | NAS100 M5 fixed ATR SL |
| `eurusd_orbv2_fixsl` | EURUSD | EURUSD M5 fixed ATR SL (13:00 UTC, 0.0001 tick) |

---

## Variant: H1 Timeframe

**Class:** `ORBv2_H1(ORBv2)` — lines 657–869

Adapts ORBv2 to H1 bars. The opening range spans the first **3 H1 bars (3 hours)**. All other entry logic — sweep detection, reversal confirmation, breakout rules — is identical to the M5 base class.

### Registered Variants

| Name | Instrument | Description |
|------|-----------|-------------|
| `spx_orbv2_h1` | SPX | SPX H1 3-hour opening range |

---

## Variant: Breakout-Only

**Class:** `ORBv2_BreakoutOnly(ORBv2)` — lines 879–1018

Disables the reversal/liquidity-sweep entry path entirely. Only breakout entries (close outside VA with trend alignment or pre-ORB sweep) are taken. Effectively a pure trend-following ORB.

### Rationale

The reversal entries in testing often produced negative expectancy (especially in strongly trending sessions). Stripping them leaves only the directional breakout trades that align with the dominant intraday flow.

### Registered Variants

| Name | Instrument | Description |
|------|-----------|-------------|
| `spx_orbv2_bo` | SPX | SPX M5 breakout-only |
| `nas100_orbv2_bo` | NAS100 | NAS100 M5 breakout-only |
| `eurusd_orbv2_bo` | EURUSD | EURUSD M5 breakout-only (13:00 UTC, 0.0001 tick) |

---

## Comparison: ORBv1 vs ORBv2

| Feature | ORBv1 | ORBv2 |
|---------|-------|-------|
| Opening range | Configurable N bars | Fixed 3 bars |
| Session start | Configurable | Configurable |
| Sweep liquidity target | Prior-day + range extremes | Prior-day + range extremes (same) |
| Reversal confirmation | `close > close[-1]` only | `close > close[-1]` AND midpoint test |
| Reversal SL | Max/min of last 3 bars | Max/min of last 2 bars |
| Reversal timeout | None | 6-bar sweep timeout |
| Pre-ORB sweep scan | None | 20-bar lookback |
| Breakout trend filter | None | `pre_orb_swept` OR `close > close[-1]` |
| Breakout SL | POC ± 2 ticks | POC ± 2 ticks |
| Fixed SL variant | Not available | ORBv2_FixedSL (0.5 ATR) |
| Breakout-only variant | Not available | ORBv2_BreakoutOnly |
| H1 variant | Separate make_variant | ORBv2_H1 subclass |

---

## Test Results Summary

ORBv2 and its variants were evaluated across SPX, NAS100, and EURUSD on M5 and H1 timeframes. Key findings:

- **SPX M5 ORBv2:** ~31.6% WR, PF ~1.08, −1.02% return over full sample — marginally negative expectancy.
- **NAS100 M5 ORBv2:** ~31.3% WR, PF ~1.20, −6.70% return — wider stops on higher ATR increase average loss.
- **EURUSD M5 ORBv2:** ~32.3% WR, PF ~0.68, −0.001% return — near-zero but still negative.
- **FixedSL variants** performed worse than base, confirming that the variable-bar-based SL preserves favorable R:R even if risk is inconsistent.
- **Breakout-only variants** matched or slightly underperformed base — removing reversals did not help.
- **SPX H1 ORBv2** remained negative (−1.02% return, PF 1.08).
- **SPX ORB H1 14:30 UTC** (v1, not v2) was the only marginally positive variant: PF 1.54, 32.6% WR, −0.003% return — essentially flat.

**Conclusion:** ORBv2 does not solve the fundamental negative expectancy of opening-range strategies on these instruments. The Smart Money additions (pre-ORB sweep, midpoint confirmation) reduce trade count without improving quality. The strategy family remains a negative result — valuable as a demonstration that naive ORB, even with institutional-flow additions, is not viable for NAS100/SPX/EURUSD at M5 or H1.
