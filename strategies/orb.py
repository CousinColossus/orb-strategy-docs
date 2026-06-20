from datetime import time as dttime

import numpy as np
import pandas as pd
from strategies.base import Strategy


class ORB(Strategy):
    """Opening Range Breakout with Volume Profile.

    Opening range = first N M5 bars of each session (default 3 bars = 15 min).
    Volume profile (VAH, VAL, POC) computed from those opening bars.

    Two entry methods:
      1) Fakeout/Reversal — price wicks beyond swing point outside opening
         range, reverses, closes back inside the Value Area.
         SL beyond reversal candle, TP at opposite side of opening range.
      2) Breakout/Trend Continuation — M5 close outside Value Area (above
         VAH or below VAL). SL at POC ± 2 ticks. Fixed 2:1 RR.

    Implements the per-signal SL/TP described in the notes.
    """

    name = "orb"
    strategy_type = ""
    instrument = "EURUSD"
    timeframe = "M5"
    description = "ORB + Volume Profile: fakeout & breakout entries (spec SL/TP)"

    def __init__(
        self,
        opening_bars: int = 3,
        start_hour: int = 13,
        start_minute: int = 0,
        tick_size: float = 0.0001,
        rr_ratio: float = 2.0,
        min_atr: float = 0.0,
        max_daily_trades: int = 1,
        enable_fakeout: bool = True,
        enable_breakout: bool = True,
        vp_levels: int = 20,
        atr_period: int = 14,
        liq_buffer_atr: float = 0.15,
        sl_ticks: int = 2,
    ):
        self.opening_bars = opening_bars
        self.start_hour = start_hour
        self.start_minute = start_minute
        self.tick_size = tick_size
        self.rr_ratio = rr_ratio
        self.min_atr = min_atr
        self.max_daily_trades = max_daily_trades
        self.enable_fakeout = enable_fakeout
        self.enable_breakout = enable_breakout
        self.vp_levels = vp_levels
        self.atr_period = atr_period
        self.liq_buffer_atr = liq_buffer_atr
        self.sl_ticks = sl_ticks

    @staticmethod
    def _atr(h, l, c, period=14):
        n = len(h)
        tr = np.maximum(
            h[1:] - l[1:],
            np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])),
        )
        tr = np.concatenate([[tr[0]], tr])
        atr = np.zeros(n)
        atr[: min(period, n)] = np.mean(tr[: min(period, n)])
        for i in range(period, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
        return atr

    @staticmethod
    def _volume_profile(highs, lows, closes, volumes, levels=20):
        lo = min(lows)
        hi = max(highs)
        if hi <= lo:
            return lo, hi, (lo + hi) / 2
        bin_edges = np.linspace(lo, hi, levels + 1)
        vol_dist = np.zeros(levels)
        for bar_h, bar_l, _, bar_v in zip(highs, lows, closes, volumes):
            if bar_h <= bar_l:
                continue
            idx_lo = max(0, int((bar_l - lo) / (hi - lo) * levels))
            idx_hi = min(levels - 1, int((bar_h - lo) / (hi - lo) * levels))
            n_bins = max(idx_hi - idx_lo + 1, 1)
            vol_per_bin = bar_v / n_bins
            for j in range(idx_lo, idx_hi + 1):
                vol_dist[j] += vol_per_bin

        total_vol = vol_dist.sum()
        if total_vol > 0:
            poc = bin_edges[np.argmax(vol_dist)]
            sorted_idx = np.argsort(vol_dist)[::-1]
            cum_vol = 0.0
            va_levels = set()
            for idx in sorted_idx:
                if cum_vol / total_vol >= 0.7:
                    break
                va_levels.add(idx)
                cum_vol += vol_dist[idx]
            va_idx = sorted(va_levels)
            val = max(lo, bin_edges[min(va_idx)])
            vah = min(hi, bin_edges[max(va_idx) + 1])
        else:
            poc = (lo + hi) / 2
            val = lo
            vah = hi
        return val, vah, poc

    def run(self, df: pd.DataFrame) -> pd.DataFrame:
        r = df.copy()
        r["entry_signal"] = 0
        r["custom_sl"] = np.nan
        r["custom_tp"] = np.nan

        h_arr = r["high"].values.astype(float)
        l_arr = r["low"].values.astype(float)
        c_arr = r["close"].values.astype(float)
        has_vol = "volume" in r.columns
        v_arr = r["volume"].values.astype(float) if has_vol else np.ones(len(r))
        n = len(r)

        atr_arr = self._atr(h_arr, l_arr, c_arr, self.atr_period)
        tick = self.tick_size

        state_date = None
        opening_h, opening_l = [], []
        opening_c, opening_v = [], []
        bars_in_range = 0
        range_closed = False
        val = vah = poc = np.nan
        trade_taken_today = False
        last_trade_date = None

        # Prior day range for fakeout liquidity
        prev_day_high = np.nan
        prev_day_low = np.nan
        prev_day_date = None
        cur_day_high = -np.inf
        cur_day_low = np.inf

        for i in range(30, n - 1):
            candle_date = df.index[i].date()
            candle_time = df.index[i].time()

            # Track daily range for prior day reference
            if candle_date != prev_day_date:
                if prev_day_date is not None:
                    prev_day_high = cur_day_high
                    prev_day_low = cur_day_low
                prev_day_date = candle_date
                cur_day_high = -np.inf
                cur_day_low = np.inf
            cur_day_high = max(cur_day_high, h_arr[i])
            cur_day_low = min(cur_day_low, l_arr[i])

            # Reset daily state
            if candle_date != state_date:
                state_date = candle_date
                opening_h, opening_l = [], []
                opening_c, opening_v = [], []
                bars_in_range = 0
                range_closed = False
                val = vah = poc = np.nan
                if last_trade_date is not None and candle_date > last_trade_date:
                    trade_taken_today = False

            if trade_taken_today:
                continue

            # ── Build opening range ──
            if not range_closed:
                if candle_time >= dttime(self.start_hour, self.start_minute):
                    if bars_in_range == 0:
                        opening_h = [h_arr[i]]
                        opening_l = [l_arr[i]]
                        opening_c = [c_arr[i]]
                        opening_v = [v_arr[i]]
                        bars_in_range = 1
                    elif bars_in_range < self.opening_bars:
                        opening_h.append(h_arr[i])
                        opening_l.append(l_arr[i])
                        opening_c.append(c_arr[i])
                        opening_v.append(v_arr[i])
                        bars_in_range += 1

                    if bars_in_range >= self.opening_bars:
                        range_closed = True
                        val, vah, poc = self._volume_profile(
                            np.array(opening_h), np.array(opening_l),
                            np.array(opening_c), np.array(opening_v),
                            self.vp_levels,
                        )
                continue

            if np.isnan(val) or np.isnan(vah) or np.isnan(poc):
                continue

            range_high = max(opening_h)
            range_low = min(opening_l)

            atr_i = atr_arr[i]

            # ── Fakeout / Reversal ──
            if self.enable_fakeout and not np.isnan(prev_day_high):
                liq_high = max(range_high, prev_day_high) + self.liq_buffer_atr * atr_i
                liq_low = min(range_low, prev_day_low) - self.liq_buffer_atr * atr_i

                if i >= 3:
                    wick_above = max(h_arr[i - 2], h_arr[i - 1]) > liq_high
                    wick_below = min(l_arr[i - 2], l_arr[i - 1]) < liq_low
                    inside_va = val < c_arr[i] < vah

                    if wick_above and inside_va and c_arr[i] < c_arr[i - 1]:
                        entry = c_arr[i]
                        sl = max(h_arr[i], h_arr[i - 1], h_arr[i - 2]) + tick
                        tp = range_low - tick
                        if sl > entry:
                            r.at[r.index[i], "entry_signal"] = -1
                            r.at[r.index[i], "custom_sl"] = sl
                            r.at[r.index[i], "custom_tp"] = tp
                            trade_taken_today = True
                            last_trade_date = candle_date
                            continue

                    if wick_below and inside_va and c_arr[i] > c_arr[i - 1]:
                        entry = c_arr[i]
                        sl = min(l_arr[i], l_arr[i - 1], l_arr[i - 2]) - tick
                        tp = range_high + tick
                        if entry > sl:
                            r.at[r.index[i], "entry_signal"] = 1
                            r.at[r.index[i], "custom_sl"] = sl
                            r.at[r.index[i], "custom_tp"] = tp
                            trade_taken_today = True
                            last_trade_date = candle_date
                            continue

            # ── Breakout / Trend Continuation ──
            if self.enable_breakout:
                entry = c_arr[i]
                if c_arr[i] > vah and c_arr[i] > c_arr[i - 1]:
                    sl = poc - self.sl_ticks * tick
                    tp = entry + self.rr_ratio * abs(entry - sl)
                    if entry > sl:
                        r.at[r.index[i], "entry_signal"] = 1
                        r.at[r.index[i], "custom_sl"] = sl
                        r.at[r.index[i], "custom_tp"] = tp
                        trade_taken_today = True
                        last_trade_date = candle_date
                        continue

                if c_arr[i] < val and c_arr[i] < c_arr[i - 1]:
                    sl = poc + self.sl_ticks * tick
                    tp = entry - self.rr_ratio * abs(sl - entry)
                    if sl > entry:
                        r.at[r.index[i], "entry_signal"] = -1
                        r.at[r.index[i], "custom_sl"] = sl
                        r.at[r.index[i], "custom_tp"] = tp
                        trade_taken_today = True
                        last_trade_date = candle_date

        return r


def make_variant(name, instrument, timeframe="M5", description=None, **overrides):
    type_map = {
        "NAS100": "Index", "SPX": "Index", "DJ30": "Index",
        "BTCUSD": "Crypto", "ETHUSD": "Crypto", "LTCUSD": "Crypto", "XRPUSD": "Crypto",
        "XAUUSD": "Commodity", "XAGUSD": "Commodity",
    }
    cls_attrs = {
        "name": name,
        "instrument": instrument,
        "timeframe": timeframe,
    }
    if description is not None:
        cls_attrs["description"] = description
    cls_attrs["strategy_type"] = type_map.get(instrument, "Forex")

    ctor_keys = [
        "opening_bars", "start_hour", "start_minute",
        "tick_size", "rr_ratio", "min_atr", "max_daily_trades",
        "enable_fakeout", "enable_breakout", "vp_levels",
        "atr_period", "liq_buffer_atr", "sl_ticks",
    ]
    defaults = dict(
        opening_bars=3,
        start_hour=13,
        start_minute=0,
        tick_size=0.0001,
        rr_ratio=2.0,
        min_atr=0.0,
        max_daily_trades=1,
        enable_fakeout=True,
        enable_breakout=True,
        vp_levels=20,
        atr_period=14,
        liq_buffer_atr=0.15,
        sl_ticks=2,
    )
    defaults.update((k, overrides.pop(k)) for k in list(overrides) if k in ctor_keys)

    def init(self, **kw):
        ORB.__init__(self, **defaults)

    cls_attrs["__init__"] = init
    return type(name, (ORB,), cls_attrs)


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
    "spx_orb_m15", "SPX",
    timeframe="M15",
    start_hour=13, start_minute=30,
    tick_size=0.01,
    description="SPX M15 ORB + volume profile, fakeout + breakout",
)

SPX_ORB_H1 = make_variant(
    "spx_orb_h1", "SPX",
    timeframe="H1",
    start_hour=13, start_minute=30,
    tick_size=0.01,
    description="SPX H1 ORB + volume profile, fakeout + breakout",
)

SPX_ORB_H1_1430 = make_variant(
    "spx_orb_h1_1430", "SPX",
    timeframe="H1",
    start_hour=14, start_minute=30,
    tick_size=0.01,
    description="SPX H1 ORB, 14:30 UTC opening (9:30 AM ET EST)",
)

SPX_ORB_H1_1BAR = make_variant(
    "spx_orb_h1_1bar", "SPX",
    timeframe="H1",
    opening_bars=1,
    start_hour=13, start_minute=30,
    tick_size=0.01,
    description="SPX H1 ORB, 1-bar opening range",
)

SPX_ORB_H1_2BAR = make_variant(
    "spx_orb_h1_2bar", "SPX",
    timeframe="H1",
    opening_bars=2,
    start_hour=13, start_minute=30,
    tick_size=0.01,
    description="SPX H1 ORB, 2-bar opening range",
)

SPX_ORB_M5_1430 = make_variant(
    "spx_orb_m5_1430", "SPX",
    start_hour=14, start_minute=30,
    tick_size=0.01,
    description="SPX M5 ORB, 14:30 UTC opening",
)

SPX_ORB_M5_6BAR = make_variant(
    "spx_orb_m5_6bar", "SPX",
    opening_bars=6,
    start_hour=13, start_minute=30,
    tick_size=0.01,
    description="SPX M5 ORB, 6-bar (30 min) opening range",
)
