from datetime import time as dttime

import numpy as np
import pandas as pd
from strategies.base import Strategy


class ORBv2(Strategy):
    """ORBv2: Opening Range Breakout v2 with Volume Profile, Liquidity Sweeps, and Reversals.

    Philosophy: Smart Money traps retail traders using volume-based "secret areas"
    within the opening range. The strategy identifies where institutional volume
    has been executed (VAH, VAL, POC) and trades breakouts/reversals based on
    5-minute candle closes relative to the Value Area at a 70% threshold.

    Opening range = first 3 M5 bars (15 minutes) of each trading session.
    Volume Profile (fixed range) computed from those 3 bars -> VAH, VAL, POC.

    Two entry methods:
      1) Reversal (liquidity sweep): Sweep a swing point outside ORB,
         then close back inside the Value Area.
         SL beyond signal candle, TP at opposite side of opening range.
      2) Breakout (trend continuation): M5 close outside Value Area (above VAH
         or below VAL), with trend alignment or pre-ORB liquidity sweep.
         SL at POC +- 2 ticks, fixed 2:1 R:R.
    """

    name = "orbv2"
    strategy_type = ""
    instrument = "EURUSD"
    timeframe = "M5"
    description = "ORBv2: value-area breakout + liquidity-sweep reversal (Smart Money)"

    def __init__(
        self,
        start_hour: int = 13,
        start_minute: int = 30,
        tick_size: float = 0.01,
        rr_ratio: float = 2.0,
        max_daily_trades: int = 1,
        vp_levels: int = 20,
        atr_period: int = 14,
        liq_buffer_atr: float = 0.15,
        sl_ticks: int = 2,
        pre_orb_lookback: int = 20,
    ):
        self.start_hour = start_hour
        self.start_minute = start_minute
        self.tick_size = tick_size
        self.rr_ratio = rr_ratio
        self.max_daily_trades = max_daily_trades
        self.vp_levels = vp_levels
        self.atr_period = atr_period
        self.liq_buffer_atr = liq_buffer_atr
        self.sl_ticks = sl_ticks
        self.pre_orb_lookback = pre_orb_lookback

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
            poc_idx = np.argmax(vol_dist)
            poc = bin_edges[poc_idx]
            sorted_idx = np.argsort(vol_dist)[::-1]
            cum_vol = 0.0
            va_indices = set()
            for idx in sorted_idx:
                if cum_vol / total_vol >= 0.7:
                    break
                va_indices.add(idx)
                cum_vol += vol_dist[idx]
            va_idx_sorted = sorted(va_indices)
            val = max(lo, bin_edges[min(va_idx_sorted)])
            vah = min(hi, bin_edges[max(va_idx_sorted) + 1])
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
        pre_orb_swept = False
        pre_orb_checked = False

        prev_day_high = np.nan
        prev_day_low = np.nan
        prev_day_close = np.nan
        prev_day_date = None
        cur_day_high = -np.inf
        cur_day_low = np.inf
        cur_day_close = np.nan

        sweep_active = False
        sweep_dir = 0
        sweep_time = None
        sweep_idx = -1

        for i in range(30, n - 1):
            candle_date = df.index[i].date()
            candle_time = df.index[i].time()

            if candle_date != prev_day_date:
                if prev_day_date is not None:
                    prev_day_high = cur_day_high
                    prev_day_low = cur_day_low
                    prev_day_close = cur_day_close
                prev_day_date = candle_date
                cur_day_high = -np.inf
                cur_day_low = np.inf
                cur_day_close = np.nan
            cur_day_high = max(cur_day_high, h_arr[i])
            cur_day_low = min(cur_day_low, l_arr[i])
            cur_day_close = c_arr[i]

            if candle_date != state_date:
                state_date = candle_date
                opening_h, opening_l = [], []
                opening_c, opening_v = [], []
                bars_in_range = 0
                range_closed = False
                val = vah = poc = np.nan
                pre_orb_swept = False
                pre_orb_checked = False
                sweep_active = False
                sweep_dir = 0
                sweep_time = None
                sweep_idx = -1
                if last_trade_date is not None and candle_date > last_trade_date:
                    trade_taken_today = False

            if trade_taken_today:
                continue

            if not range_closed:
                session_start = candle_time >= dttime(self.start_hour, self.start_minute)

                if session_start and bars_in_range == 0 and not pre_orb_checked:
                    pre_orb_checked = True
                    lookback_start = max(0, i - self.pre_orb_lookback)
                    for j in range(lookback_start, i):
                        if not np.isnan(prev_day_high):
                            liq_h = prev_day_high + self.liq_buffer_atr * atr_arr[j]
                            if h_arr[j] > liq_h:
                                pre_orb_swept = True
                                break
                        if not np.isnan(prev_day_low):
                            liq_l = prev_day_low - self.liq_buffer_atr * atr_arr[j]
                            if l_arr[j] < liq_l:
                                pre_orb_swept = True
                                break

                if session_start:
                    if bars_in_range == 0:
                        opening_h = [h_arr[i]]
                        opening_l = [l_arr[i]]
                        opening_c = [c_arr[i]]
                        opening_v = [v_arr[i]]
                        bars_in_range = 1
                    elif bars_in_range < 3:
                        opening_h.append(h_arr[i])
                        opening_l.append(l_arr[i])
                        opening_c.append(c_arr[i])
                        opening_v.append(v_arr[i])
                        bars_in_range += 1

                    if bars_in_range >= 3:
                        range_closed = True
                        val, vah, poc = self._volume_profile(
                            np.array(opening_h), np.array(opening_l),
                            np.array(opening_c), np.array(opening_v),
                            self.vp_levels,
                        )
                        if np.isnan(val) or np.isnan(vah) or np.isnan(poc):
                            val = vah = poc = np.nan
                continue

            if np.isnan(val) or np.isnan(vah) or np.isnan(poc):
                continue

            range_high = max(opening_h)
            range_low = min(opening_l)
            atr_i = atr_arr[i]

            liq_high = range_high + self.liq_buffer_atr * atr_i
            liq_low = range_low - self.liq_buffer_atr * atr_i
            if not np.isnan(prev_day_high):
                liq_high = max(liq_high, prev_day_high + self.liq_buffer_atr * atr_i)
            if not np.isnan(prev_day_low):
                liq_low = min(liq_low, prev_day_low - self.liq_buffer_atr * atr_i)

            if not sweep_active:
                sweep_long = l_arr[i] < liq_low
                sweep_short = h_arr[i] > liq_high
                if sweep_long:
                    sweep_active = True
                    sweep_dir = 1
                    sweep_time = df.index[i]
                    sweep_idx = i
                    continue
                if sweep_short:
                    sweep_active = True
                    sweep_dir = -1
                    sweep_time = df.index[i]
                    sweep_idx = i
                    continue
            else:
                if val < c_arr[i] < vah:
                    entry = c_arr[i]
                    if sweep_dir == 1:
                        conf = c_arr[i] > c_arr[i - 1] and c_arr[i] > (h_arr[i] + l_arr[i]) / 2
                        if not conf:
                            sweep_active = False
                            sweep_dir = 0
                            continue
                        sl = min(l_arr[i], l_arr[i - 1]) - tick
                        tp = range_high + tick
                        if entry > sl:
                            r.at[r.index[i], "entry_signal"] = 1
                            r.at[r.index[i], "custom_sl"] = sl
                            r.at[r.index[i], "custom_tp"] = tp
                            trade_taken_today = True
                            last_trade_date = candle_date
                            sweep_active = False
                            sweep_dir = 0
                            continue
                    else:
                        conf = c_arr[i] < c_arr[i - 1] and c_arr[i] < (h_arr[i] + l_arr[i]) / 2
                        if not conf:
                            sweep_active = False
                            sweep_dir = 0
                            continue
                        sl = max(h_arr[i], h_arr[i - 1]) + tick
                        tp = range_low - tick
                        if sl > entry:
                            r.at[r.index[i], "entry_signal"] = -1
                            r.at[r.index[i], "custom_sl"] = sl
                            r.at[r.index[i], "custom_tp"] = tp
                            trade_taken_today = True
                            last_trade_date = candle_date
                            sweep_active = False
                            sweep_dir = 0
                            continue

                bars_since = i - sweep_idx
                if bars_since > 6:
                    sweep_active = False
                    sweep_dir = 0

            entry = c_arr[i]
            if c_arr[i] > vah:
                if pre_orb_swept or c_arr[i] > c_arr[i - 1]:
                    sl = poc - self.sl_ticks * tick
                    tp = entry + self.rr_ratio * abs(entry - sl)
                    if entry > sl:
                        r.at[r.index[i], "entry_signal"] = 1
                        r.at[r.index[i], "custom_sl"] = sl
                        r.at[r.index[i], "custom_tp"] = tp
                        trade_taken_today = True
                        last_trade_date = candle_date
                        continue

            if c_arr[i] < val:
                if pre_orb_swept or c_arr[i] < c_arr[i - 1]:
                    sl = poc + self.sl_ticks * tick
                    tp = entry - self.rr_ratio * abs(sl - entry)
                    if sl > entry:
                        r.at[r.index[i], "entry_signal"] = -1
                        r.at[r.index[i], "custom_sl"] = sl
                        r.at[r.index[i], "custom_tp"] = tp
                        trade_taken_today = True
                        last_trade_date = candle_date

        return r


def make_variant(name, instrument, description=None, **overrides):
    type_map = {
        "NAS100": "Index", "SPX": "Index", "DJ30": "Index",
        "BTCUSD": "Crypto", "ETHUSD": "Crypto", "LTCUSD": "Crypto", "XRPUSD": "Crypto",
        "XAUUSD": "Commodity", "XAGUSD": "Commodity",
    }
    cls_attrs = {
        "name": name,
        "instrument": instrument,
        "timeframe": "M5",
    }
    if description is not None:
        cls_attrs["description"] = description
    cls_attrs["strategy_type"] = type_map.get(instrument, "Forex")

    ctor_keys = [
        "start_hour", "start_minute", "tick_size", "rr_ratio",
        "max_daily_trades", "vp_levels", "atr_period",
        "liq_buffer_atr", "sl_ticks", "pre_orb_lookback",
    ]
    defaults = dict(
        start_hour=13, start_minute=30, tick_size=0.01, rr_ratio=2.0,
        max_daily_trades=1, vp_levels=20, atr_period=14,
        liq_buffer_atr=0.15, sl_ticks=2, pre_orb_lookback=20,
    )
    defaults.update((k, overrides.pop(k)) for k in list(overrides) if k in ctor_keys)

    def init(self, **kw):
        ORBv2.__init__(self, **defaults)

    cls_attrs["__init__"] = init
    return type(name, (ORBv2,), cls_attrs)


EURUSD_ORBv2 = make_variant(
    "eurusd_orbv2", "EURUSD",
    start_hour=13, start_minute=0,
    tick_size=0.0001,
    description="EURUSD M5 ORBv2: value-area breakout + liquidity-sweep reversal",
)

NAS100_ORBv2 = make_variant(
    "nas100_orbv2", "NAS100",
    description="NAS100 M5 ORBv2: value-area breakout + liquidity-sweep reversal",
)

SPX_ORBv2 = make_variant(
    "spx_orbv2", "SPX",
    description="SPX M5 ORBv2: value-area breakout + liquidity-sweep reversal",
)


# ── Improved Variants ──────────────────────────────────────────────────────

class ORBv2_FixedSL(ORBv2):
    """ORBv2 with fixed ATR-based SL (0.5 ATR) for breakouts + fixed 2:1 RR for reversals.
    
    The base spec (SL at POC +- 2 ticks, TP = opposite side of ORB for reversals)
    creates variable risk. This variant normalises all entries to fixed 0.5 ATR SL
    and 2:1 R:R for consistency.
    """

    name = "orbv2_fixsl"
    timeframe = "M5"
    description = "ORBv2 fixed ATR SL + fixed RR: consistent risk for both entry types"

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
        opening_h, opening_l, opening_c, opening_v = [], [], [], []
        bars_in_range = 0
        range_closed = False
        val = vah = poc = np.nan
        trade_taken_today = False
        last_trade_date = None
        pre_orb_swept = False
        pre_orb_checked = False

        prev_day_high = np.nan
        prev_day_low = np.nan
        prev_day_date = None
        cur_day_high = -np.inf
        cur_day_low = np.inf

        sweep_active = False
        sweep_dir = 0
        sweep_idx = -1

        for i in range(30, n - 1):
            candle_date = df.index[i].date()
            candle_time = df.index[i].time()

            if candle_date != prev_day_date:
                if prev_day_date is not None:
                    prev_day_high = cur_day_high
                    prev_day_low = cur_day_low
                prev_day_date = candle_date
                cur_day_high = -np.inf
                cur_day_low = np.inf
            cur_day_high = max(cur_day_high, h_arr[i])
            cur_day_low = min(cur_day_low, l_arr[i])

            if candle_date != state_date:
                state_date = candle_date
                opening_h, opening_l, opening_c, opening_v = [], [], [], []
                bars_in_range = 0
                range_closed = False
                val = vah = poc = np.nan
                pre_orb_swept = False
                pre_orb_checked = False
                sweep_active = False
                sweep_dir = 0
                sweep_idx = -1
                if last_trade_date is not None and candle_date > last_trade_date:
                    trade_taken_today = False

            if trade_taken_today:
                continue

            if not range_closed:
                session_start = candle_time >= dttime(self.start_hour, self.start_minute)

                if session_start and bars_in_range == 0 and not pre_orb_checked:
                    pre_orb_checked = True
                    lookback_start = max(0, i - self.pre_orb_lookback)
                    for j in range(lookback_start, i):
                        if not np.isnan(prev_day_high):
                            liq_h = prev_day_high + self.liq_buffer_atr * atr_arr[j]
                            if h_arr[j] > liq_h:
                                pre_orb_swept = True
                                break
                        if not np.isnan(prev_day_low):
                            liq_l = prev_day_low - self.liq_buffer_atr * atr_arr[j]
                            if l_arr[j] < liq_l:
                                pre_orb_swept = True
                                break

                if session_start:
                    if bars_in_range == 0:
                        opening_h = [h_arr[i]]
                        opening_l = [l_arr[i]]
                        opening_c = [c_arr[i]]
                        opening_v = [v_arr[i]]
                        bars_in_range = 1
                    elif bars_in_range < 3:
                        opening_h.append(h_arr[i])
                        opening_l.append(l_arr[i])
                        opening_c.append(c_arr[i])
                        opening_v.append(v_arr[i])
                        bars_in_range += 1

                    if bars_in_range >= 3:
                        range_closed = True
                        val, vah, poc = self._volume_profile(
                            np.array(opening_h), np.array(opening_l),
                            np.array(opening_c), np.array(opening_v),
                            self.vp_levels,
                        )
                        if np.isnan(val) or np.isnan(vah) or np.isnan(poc):
                            val = vah = poc = np.nan
                continue

            if np.isnan(val) or np.isnan(vah) or np.isnan(poc):
                continue

            range_high = max(opening_h)
            range_low = min(opening_l)
            atr_i = atr_arr[i]

            liq_high = range_high + self.liq_buffer_atr * atr_i
            liq_low = range_low - self.liq_buffer_atr * atr_i
            if not np.isnan(prev_day_high):
                liq_high = max(liq_high, prev_day_high + self.liq_buffer_atr * atr_i)
            if not np.isnan(prev_day_low):
                liq_low = min(liq_low, prev_day_low - self.liq_buffer_atr * atr_i)

            if not sweep_active:
                sweep_long = l_arr[i] < liq_low
                sweep_short = h_arr[i] > liq_high
                if sweep_long:
                    sweep_active = True
                    sweep_dir = 1
                    sweep_idx = i
                    continue
                if sweep_short:
                    sweep_active = True
                    sweep_dir = -1
                    sweep_idx = i
                    continue
            else:
                if val < c_arr[i] < vah:
                    entry = c_arr[i]
                    conf = None
                    sl_atr = 0.5 * atr_i
                    if sweep_dir == 1:
                        conf = c_arr[i] > c_arr[i - 1]
                        if not conf:
                            sweep_active = False
                            sweep_dir = 0
                            continue
                        sl = entry - sl_atr
                        tp = entry + self.rr_ratio * sl_atr
                        if entry > sl:
                            r.at[r.index[i], "entry_signal"] = 1
                            r.at[r.index[i], "custom_sl"] = sl
                            r.at[r.index[i], "custom_tp"] = tp
                            trade_taken_today = True
                            last_trade_date = candle_date
                            sweep_active = False
                            sweep_dir = 0
                            continue
                    else:
                        conf = c_arr[i] < c_arr[i - 1]
                        if not conf:
                            sweep_active = False
                            sweep_dir = 0
                            continue
                        sl = entry + sl_atr
                        tp = entry - self.rr_ratio * sl_atr
                        if sl > entry:
                            r.at[r.index[i], "entry_signal"] = -1
                            r.at[r.index[i], "custom_sl"] = sl
                            r.at[r.index[i], "custom_tp"] = tp
                            trade_taken_today = True
                            last_trade_date = candle_date
                            sweep_active = False
                            sweep_dir = 0
                            continue

                if i - sweep_idx > 6:
                    sweep_active = False
                    sweep_dir = 0

            entry = c_arr[i]
            sl_atr = 0.5 * atr_i
            if c_arr[i] > vah:
                if pre_orb_swept or c_arr[i] > c_arr[i - 1]:
                    sl = entry - sl_atr
                    tp = entry + self.rr_ratio * sl_atr
                    if entry > sl:
                        r.at[r.index[i], "entry_signal"] = 1
                        r.at[r.index[i], "custom_sl"] = sl
                        r.at[r.index[i], "custom_tp"] = tp
                        trade_taken_today = True
                        last_trade_date = candle_date
                        continue

            if c_arr[i] < val:
                if pre_orb_swept or c_arr[i] < c_arr[i - 1]:
                    sl = entry + sl_atr
                    tp = entry - self.rr_ratio * sl_atr
                    if sl > entry:
                        r.at[r.index[i], "entry_signal"] = -1
                        r.at[r.index[i], "custom_sl"] = sl
                        r.at[r.index[i], "custom_tp"] = tp
                        trade_taken_today = True
                        last_trade_date = candle_date

        return r


def make_variant_ex(cls, name, instrument, description=None, **overrides):
    type_map = {
        "NAS100": "Index", "SPX": "Index", "DJ30": "Index",
        "BTCUSD": "Crypto", "ETHUSD": "Crypto", "LTCUSD": "Crypto", "XRPUSD": "Crypto",
        "XAUUSD": "Commodity", "XAGUSD": "Commodity",
    }
    cls_attrs = {
        "name": name,
        "instrument": instrument,
    }
    if "timeframe" not in cls.__dict__:
        cls_attrs["timeframe"] = "M5"
    if description is not None:
        cls_attrs["description"] = description
    cls_attrs["strategy_type"] = type_map.get(instrument, "Forex")

    ctor_keys = [
        "start_hour", "start_minute", "tick_size", "rr_ratio",
        "max_daily_trades", "vp_levels", "atr_period",
        "liq_buffer_atr", "sl_ticks", "pre_orb_lookback",
    ]
    defaults = dict(
        start_hour=13, start_minute=30, tick_size=0.01, rr_ratio=2.0,
        max_daily_trades=1, vp_levels=20, atr_period=14,
        liq_buffer_atr=0.15, sl_ticks=2, pre_orb_lookback=20,
    )
    defaults.update((k, overrides.pop(k)) for k in list(overrides) if k in ctor_keys)

    def init(self, **kw):
        cls.__init__(self, **defaults)

    cls_attrs["__init__"] = init
    return type(name, (cls,), cls_attrs)


SPX_ORBv2_FIXSL = make_variant_ex(
    ORBv2_FixedSL, "spx_orbv2_fixsl", "SPX",
    description="SPX M5 ORBv2 fixed ATR SL + fixed RR",
)

NAS100_ORBv2_FIXSL = make_variant_ex(
    ORBv2_FixedSL, "nas100_orbv2_fixsl", "NAS100",
    description="NAS100 M5 ORBv2 fixed ATR SL + fixed RR",
)

EURUSD_ORBv2_FIXSL = make_variant_ex(
    ORBv2_FixedSL, "eurusd_orbv2_fixsl", "EURUSD",
    start_hour=13, start_minute=0, tick_size=0.0001,
    description="EURUSD M5 ORBv2 fixed ATR SL + fixed RR",
)

# ── H1 Timeframe Variant ──────────────────────────────────────────────────

class ORBv2_H1(ORBv2):
    """ORBv2 adapted for H1 timeframe.
    
    Opening range = first 3 H1 bars (3 hours).
    All other rules identical.
    """

    name = "orbv2_h1"
    timeframe = "H1"
    description = "ORBv2 H1: 3-hour opening range on H1 chart"

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
        opening_h, opening_l, opening_c, opening_v = [], [], [], []
        bars_in_range = 0
        range_closed = False
        val = vah = poc = np.nan
        trade_taken_today = False
        last_trade_date = None
        pre_orb_swept = False
        pre_orb_checked = False

        prev_day_high = np.nan
        prev_day_low = np.nan
        prev_day_date = None
        cur_day_high = -np.inf
        cur_day_low = np.inf

        sweep_active = False
        sweep_dir = 0
        sweep_idx = -1

        for i in range(30, n - 1):
            candle_date = df.index[i].date()
            candle_time = df.index[i].time()

            if candle_date != prev_day_date:
                if prev_day_date is not None:
                    prev_day_high = cur_day_high
                    prev_day_low = cur_day_low
                prev_day_date = candle_date
                cur_day_high = -np.inf
                cur_day_low = np.inf
            cur_day_high = max(cur_day_high, h_arr[i])
            cur_day_low = min(cur_day_low, l_arr[i])

            if candle_date != state_date:
                state_date = candle_date
                opening_h, opening_l, opening_c, opening_v = [], [], [], []
                bars_in_range = 0
                range_closed = False
                val = vah = poc = np.nan
                pre_orb_swept = False
                pre_orb_checked = False
                sweep_active = False
                sweep_dir = 0
                sweep_idx = -1
                if last_trade_date is not None and candle_date > last_trade_date:
                    trade_taken_today = False

            if trade_taken_today:
                continue

            if not range_closed:
                session_start = candle_time >= dttime(self.start_hour, self.start_minute)

                if session_start and bars_in_range == 0 and not pre_orb_checked:
                    pre_orb_checked = True
                    lookback_start = max(0, i - self.pre_orb_lookback)
                    for j in range(lookback_start, i):
                        if not np.isnan(prev_day_high):
                            liq_h = prev_day_high + self.liq_buffer_atr * atr_arr[j]
                            if h_arr[j] > liq_h:
                                pre_orb_swept = True
                                break
                        if not np.isnan(prev_day_low):
                            liq_l = prev_day_low - self.liq_buffer_atr * atr_arr[j]
                            if l_arr[j] < liq_l:
                                pre_orb_swept = True
                                break

                if session_start:
                    if bars_in_range == 0:
                        opening_h = [h_arr[i]]
                        opening_l = [l_arr[i]]
                        opening_c = [c_arr[i]]
                        opening_v = [v_arr[i]]
                        bars_in_range = 1
                    elif bars_in_range < 3:
                        opening_h.append(h_arr[i])
                        opening_l.append(l_arr[i])
                        opening_c.append(c_arr[i])
                        opening_v.append(v_arr[i])
                        bars_in_range += 1

                    if bars_in_range >= 3:
                        range_closed = True
                        val, vah, poc = self._volume_profile(
                            np.array(opening_h), np.array(opening_l),
                            np.array(opening_c), np.array(opening_v),
                            self.vp_levels,
                        )
                        if np.isnan(val) or np.isnan(vah) or np.isnan(poc):
                            val = vah = poc = np.nan
                continue

            if np.isnan(val) or np.isnan(vah) or np.isnan(poc):
                continue

            range_high = max(opening_h)
            range_low = min(opening_l)
            atr_i = atr_arr[i]

            liq_high = range_high + self.liq_buffer_atr * atr_i
            liq_low = range_low - self.liq_buffer_atr * atr_i
            if not np.isnan(prev_day_high):
                liq_high = max(liq_high, prev_day_high + self.liq_buffer_atr * atr_i)
            if not np.isnan(prev_day_low):
                liq_low = min(liq_low, prev_day_low - self.liq_buffer_atr * atr_i)

            if not sweep_active:
                if l_arr[i] < liq_low:
                    sweep_active = True
                    sweep_dir = 1
                    sweep_idx = i
                    continue
                if h_arr[i] > liq_high:
                    sweep_active = True
                    sweep_dir = -1
                    sweep_idx = i
                    continue
            else:
                if val < c_arr[i] < vah:
                    entry = c_arr[i]
                    if sweep_dir == 1:
                        conf = c_arr[i] > c_arr[i - 1] and c_arr[i] > (h_arr[i] + l_arr[i]) / 2
                        if not conf:
                            sweep_active = False
                            sweep_dir = 0
                            continue
                        sl = min(l_arr[i], l_arr[i - 1]) - tick
                        tp = range_high + tick
                        if entry > sl:
                            r.at[r.index[i], "entry_signal"] = 1
                            r.at[r.index[i], "custom_sl"] = sl
                            r.at[r.index[i], "custom_tp"] = tp
                            trade_taken_today = True
                            last_trade_date = candle_date
                            sweep_active = False
                            sweep_dir = 0
                            continue
                    else:
                        conf = c_arr[i] < c_arr[i - 1] and c_arr[i] < (h_arr[i] + l_arr[i]) / 2
                        if not conf:
                            sweep_active = False
                            sweep_dir = 0
                            continue
                        sl = max(h_arr[i], h_arr[i - 1]) + tick
                        tp = range_low - tick
                        if sl > entry:
                            r.at[r.index[i], "entry_signal"] = -1
                            r.at[r.index[i], "custom_sl"] = sl
                            r.at[r.index[i], "custom_tp"] = tp
                            trade_taken_today = True
                            last_trade_date = candle_date
                            sweep_active = False
                            sweep_dir = 0
                            continue

                if i - sweep_idx > 6:
                    sweep_active = False
                    sweep_dir = 0

            entry = c_arr[i]
            if c_arr[i] > vah:
                if pre_orb_swept or c_arr[i] > c_arr[i - 1]:
                    sl = poc - self.sl_ticks * tick
                    tp = entry + self.rr_ratio * abs(entry - sl)
                    if entry > sl:
                        r.at[r.index[i], "entry_signal"] = 1
                        r.at[r.index[i], "custom_sl"] = sl
                        r.at[r.index[i], "custom_tp"] = tp
                        trade_taken_today = True
                        last_trade_date = candle_date
                        continue

            if c_arr[i] < val:
                if pre_orb_swept or c_arr[i] < c_arr[i - 1]:
                    sl = poc + self.sl_ticks * tick
                    tp = entry - self.rr_ratio * abs(sl - entry)
                    if sl > entry:
                        r.at[r.index[i], "entry_signal"] = -1
                        r.at[r.index[i], "custom_sl"] = sl
                        r.at[r.index[i], "custom_tp"] = tp
                        trade_taken_today = True
                        last_trade_date = candle_date

        return r


SPX_ORBv2_H1 = make_variant_ex(
    ORBv2_H1, "spx_orbv2_h1", "SPX",
    description="SPX H1 ORBv2: 3-hour opening range on H1 chart",
)

# ── Breakout-Only Variant ─────────────────────────────────────────────────

class ORBv2_BreakoutOnly(ORBv2):
    """ORBv2 with reversal entries disabled — breakout entries only."""

    name = "orbv2_bo"
    timeframe = "M5"
    description = "ORBv2 breakout-only: close outside Value Area entries"

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
        opening_h, opening_l, opening_c, opening_v = [], [], [], []
        bars_in_range = 0
        range_closed = False
        val = vah = poc = np.nan
        trade_taken_today = False
        last_trade_date = None
        pre_orb_swept = False
        pre_orb_checked = False

        prev_day_high = np.nan
        prev_day_low = np.nan
        prev_day_date = None
        cur_day_high = -np.inf
        cur_day_low = np.inf

        for i in range(30, n - 1):
            candle_date = df.index[i].date()
            candle_time = df.index[i].time()

            if candle_date != prev_day_date:
                if prev_day_date is not None:
                    prev_day_high = cur_day_high
                    prev_day_low = cur_day_low
                prev_day_date = candle_date
                cur_day_high = -np.inf
                cur_day_low = np.inf
            cur_day_high = max(cur_day_high, h_arr[i])
            cur_day_low = min(cur_day_low, l_arr[i])

            if candle_date != state_date:
                state_date = candle_date
                opening_h, opening_l, opening_c, opening_v = [], [], [], []
                bars_in_range = 0
                range_closed = False
                val = vah = poc = np.nan
                pre_orb_swept = False
                pre_orb_checked = False
                if last_trade_date is not None and candle_date > last_trade_date:
                    trade_taken_today = False

            if trade_taken_today:
                continue

            if not range_closed:
                session_start = candle_time >= dttime(self.start_hour, self.start_minute)

                if session_start and bars_in_range == 0 and not pre_orb_checked:
                    pre_orb_checked = True
                    lookback_start = max(0, i - self.pre_orb_lookback)
                    for j in range(lookback_start, i):
                        if not np.isnan(prev_day_high):
                            liq_h = prev_day_high + self.liq_buffer_atr * atr_arr[j]
                            if h_arr[j] > liq_h:
                                pre_orb_swept = True
                                break
                        if not np.isnan(prev_day_low):
                            liq_l = prev_day_low - self.liq_buffer_atr * atr_arr[j]
                            if l_arr[j] < liq_l:
                                pre_orb_swept = True
                                break

                if session_start:
                    if bars_in_range == 0:
                        opening_h = [h_arr[i]]
                        opening_l = [l_arr[i]]
                        opening_c = [c_arr[i]]
                        opening_v = [v_arr[i]]
                        bars_in_range = 1
                    elif bars_in_range < 3:
                        opening_h.append(h_arr[i])
                        opening_l.append(l_arr[i])
                        opening_c.append(c_arr[i])
                        opening_v.append(v_arr[i])
                        bars_in_range += 1

                    if bars_in_range >= 3:
                        range_closed = True
                        val, vah, poc = self._volume_profile(
                            np.array(opening_h), np.array(opening_l),
                            np.array(opening_c), np.array(opening_v),
                            self.vp_levels,
                        )
                        if np.isnan(val) or np.isnan(vah) or np.isnan(poc):
                            val = vah = poc = np.nan
                continue

            if np.isnan(val) or np.isnan(vah) or np.isnan(poc):
                continue

            atr_i = atr_arr[i]

            entry = c_arr[i]
            if c_arr[i] > vah:
                if pre_orb_swept or c_arr[i] > c_arr[i - 1]:
                    sl = poc - self.sl_ticks * tick
                    tp = entry + self.rr_ratio * abs(entry - sl)
                    if entry > sl:
                        r.at[r.index[i], "entry_signal"] = 1
                        r.at[r.index[i], "custom_sl"] = sl
                        r.at[r.index[i], "custom_tp"] = tp
                        trade_taken_today = True
                        last_trade_date = candle_date
                        continue

            if c_arr[i] < val:
                if pre_orb_swept or c_arr[i] < c_arr[i - 1]:
                    sl = poc + self.sl_ticks * tick
                    tp = entry - self.rr_ratio * abs(sl - entry)
                    if sl > entry:
                        r.at[r.index[i], "entry_signal"] = -1
                        r.at[r.index[i], "custom_sl"] = sl
                        r.at[r.index[i], "custom_tp"] = tp
                        trade_taken_today = True
                        last_trade_date = candle_date

        return r


SPX_ORBv2_BO = make_variant_ex(
    ORBv2_BreakoutOnly, "spx_orbv2_bo", "SPX",
    description="SPX M5 ORBv2 breakout-only",
)

NAS100_ORBv2_BO = make_variant_ex(
    ORBv2_BreakoutOnly, "nas100_orbv2_bo", "NAS100",
    description="NAS100 M5 ORBv2 breakout-only",
)

EURUSD_ORBv2_BO = make_variant_ex(
    ORBv2_BreakoutOnly, "eurusd_orbv2_bo", "EURUSD",
    start_hour=13, start_minute=0, tick_size=0.0001,
    description="EURUSD M5 ORBv2 breakout-only",
)
