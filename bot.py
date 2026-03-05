"""
SOL/USDT Futures - PVI Crossover Alert Bot
Matches TradingView calculations exactly using Binance Futures public API.
Sends Telegram alerts on PVI crossover above EMA(13).
"""

import time
import math
import requests
from datetime import datetime, timezone

# ─────────────────────────────────────────────
#  ✏️  CONFIGURE THESE BEFORE RUNNING
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = '8050135427:AAFNQYFpU8lMQ-reJlvLnPYFKc8pyPrHblE'       # e.g. "7123456789:AAF..."
TELEGRAM_CHAT_ID   = '1950462171'          # e.g. "-1001234567890"

# ─────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────
SYMBOL          = "SOLUSDT"
INTERVAL        = "5m"
FUTURES_BASE    = "https://fapi.binance.com"
PVI_EMA_LEN     = 13
NVI_EMA_LEN     = 255
ATR_LEN         = 14
ADX_LEN         = 14
ADX_SMOOTH      = 14
CANDLES_NEEDED  = 600     # enough warm-up bars for EMA(255) to stabilise
POLL_SECONDS    = 15      # how often to check if a new 5m candle closed

# ─────────────────────────────────────────────
#  Binance Futures – fetch klines
# ─────────────────────────────────────────────
def fetch_klines(symbol: str, interval: str, limit: int = 600) -> list[dict]:
    """
    Returns list of closed candles as dicts with keys:
    open_time, open, high, low, close, volume
    """
    url = f"{FUTURES_BASE}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    raw = r.json()
    candles = []
    for k in raw:
        candles.append({
            "open_time": int(k[0]),
            "open":   float(k[1]),
            "high":   float(k[2]),
            "low":    float(k[3]),
            "close":  float(k[4]),
            "volume": float(k[5]),
        })
    # Drop the last candle – it is the currently forming (unclosed) bar
    return candles[:-1]


def fetch_current_price(symbol: str) -> float:
    url = f"{FUTURES_BASE}/fapi/v1/ticker/price"
    r = requests.get(url, params={"symbol": symbol}, timeout=10)
    r.raise_for_status()
    return float(r.json()["price"])


# ─────────────────────────────────────────────
#  Indicators  (mirror TradingView Pine v6)
# ─────────────────────────────────────────────

def calc_ema(series: list[float], length: int) -> list[float]:
    """
    Wilder/standard EMA matching Pine's ta.ema().
    First valid value = SMA of first `length` bars, then EMA from there.
    """
    result = [float("nan")] * len(series)
    if len(series) < length:
        return result
    # Seed with SMA
    sma_seed = sum(series[:length]) / length
    result[length - 1] = sma_seed
    mult = 2.0 / (length + 1)
    for i in range(length, len(series)):
        result[i] = series[i] * mult + result[i - 1] * (1 - mult)
    return result


def calc_pvi_nvi(candles: list[dict]) -> tuple[list[float], list[float]]:
    """
    Replicates Pine's ta.pvi / ta.nvi exactly.

    PVI starts at 1.0; each bar:
      if volume > prev_volume → pvi *= (1 + price_change_pct)
      else                    → pvi unchanged

    NVI starts at 1.0; each bar:
      if volume < prev_volume → nvi *= (1 + price_change_pct)
      else                    → nvi unchanged

    Pine multiplies the result by 1000 in the script – we do the same.
    """
    n = len(candles)
    pvi_raw = [1.0] * n
    nvi_raw = [1.0] * n

    for i in range(1, n):
        prev_close  = candles[i - 1]["close"]
        curr_close  = candles[i]["close"]
        prev_vol    = candles[i - 1]["volume"]
        curr_vol    = candles[i]["volume"]
        pct_change  = (curr_close - prev_close) / prev_close if prev_close != 0 else 0.0

        pvi_raw[i] = pvi_raw[i - 1] * (1 + pct_change) if curr_vol > prev_vol else pvi_raw[i - 1]
        nvi_raw[i] = nvi_raw[i - 1] * (1 + pct_change) if curr_vol < prev_vol else nvi_raw[i - 1]

    # Scale ×1000 like the Pine script
    pvi = [v * 1000.0 for v in pvi_raw]
    nvi = [v * 1000.0 for v in nvi_raw]
    return pvi, nvi


def calc_atr(candles: list[dict], length: int) -> list[float]:
    """
    Replicates Pine's ta.atr() which uses RMA (Wilder smoothing).
    RMA(x, n) = (x + (n-1) * prev_rma) / n
    """
    n = len(candles)
    tr_list = [float("nan")] * n

    for i in range(1, n):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i - 1]["close"]
        tr_list[i] = max(h - l, abs(h - pc), abs(l - pc))

    atr = [float("nan")] * n
    # Seed: SMA of first `length` TR values starting at index 1
    valid_start = 1  # first TR is at index 1
    if n < valid_start + length:
        return atr

    seed_vals = tr_list[valid_start: valid_start + length]
    atr[valid_start + length - 1] = sum(seed_vals) / length
    alpha = 1.0 / length
    for i in range(valid_start + length, n):
        atr[i] = tr_list[i] * alpha + atr[i - 1] * (1 - alpha)
    return atr


def calc_dmi_adx(candles: list[dict], di_len: int, adx_smooth: int) -> tuple[list[float], list[float], list[float]]:
    """
    Replicates Pine's ta.dmi(diLength, adxSmoothing).
    Uses Wilder's RMA for smoothing.
    """
    n = len(candles)
    plus_dm  = [0.0] * n
    minus_dm = [0.0] * n
    tr_list  = [0.0] * n

    for i in range(1, n):
        h, l, ph, pl = candles[i]["high"], candles[i]["low"], candles[i-1]["high"], candles[i-1]["low"]
        pc = candles[i-1]["close"]
        up   = h - ph
        down = pl - l
        plus_dm[i]  = up   if (up > down and up > 0)   else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0
        tr_list[i]  = max(h - l, abs(h - pc), abs(l - pc))

    def rma(series, length, start=1):
        out = [float("nan")] * n
        if n < start + length:
            return out
        out[start + length - 1] = sum(series[start: start + length]) / length
        alpha = 1.0 / length
        for i in range(start + length, n):
            out[i] = series[i] * alpha + out[i-1] * (1 - alpha)
        return out

    smoothed_tr      = rma(tr_list,  di_len)
    smoothed_plus    = rma(plus_dm,  di_len)
    smoothed_minus   = rma(minus_dm, di_len)

    di_plus  = [float("nan")] * n
    di_minus = [float("nan")] * n
    dx_list  = [float("nan")] * n

    for i in range(n):
        if not math.isnan(smoothed_tr[i]) and smoothed_tr[i] != 0:
            dp = 100.0 * smoothed_plus[i]  / smoothed_tr[i]
            dm = 100.0 * smoothed_minus[i] / smoothed_tr[i]
            di_plus[i]  = dp
            di_minus[i] = dm
            dsum = dp + dm
            dx_list[i] = 100.0 * abs(dp - dm) / dsum if dsum != 0 else 0.0

    # ADX = RMA of DX
    # Find first valid DX index
    first_dx = next((i for i in range(n) if not math.isnan(dx_list[i])), None)
    adx_out = [float("nan")] * n
    if first_dx is not None and n >= first_dx + adx_smooth:
        seed = sum(dx_list[first_dx: first_dx + adx_smooth]) / adx_smooth
        adx_out[first_dx + adx_smooth - 1] = seed
        alpha = 1.0 / adx_smooth
        for i in range(first_dx + adx_smooth, n):
            adx_out[i] = dx_list[i] * alpha + adx_out[i-1] * (1 - alpha)

    return di_plus, di_minus, adx_out


# ─────────────────────────────────────────────
#  Signal Labels
# ─────────────────────────────────────────────

def adx_label(adx: float) -> str:
    if math.isnan(adx):  return "N/A"
    if adx >= 50: return "Extremely Strong"
    if adx >= 25: return "Strong Trend"
    if adx >= 20: return "Weak Trend"
    return "No Trend"


def atr_label(atr_now: float, atr_10ago: float) -> str:
    if math.isnan(atr_now) or math.isnan(atr_10ago) or atr_10ago == 0:
        return "N/A"
    if atr_now > atr_10ago * 1.5:  return "🔥 High Volatility"
    if atr_now > atr_10ago:         return "📈 Rising Volatility"
    if atr_now < atr_10ago * 0.75: return "😴 Low Volatility"
    return "Normal Volatility"


def dmi_label(di_plus: float, di_minus: float) -> str:
    if math.isnan(di_plus) or math.isnan(di_minus): return "N/A"
    return "▲ Bullish" if di_plus > di_minus else "▼ Bearish"


# ─────────────────────────────────────────────
#  Telegram
# ─────────────────────────────────────────────

def send_telegram(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg,
        "parse_mode": "HTML",
    }
    r = requests.post(url, json=payload, timeout=10)
    r.raise_for_status()
    print(f"[{now_str()}] ✅ Telegram alert sent.")


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def safe(val: float, fmt: str = ".4f") -> str:
    return f"{val:{fmt}}" if not math.isnan(val) else "N/A"


# ─────────────────────────────────────────────
#  Core: compute signals from candles
# ─────────────────────────────────────────────

def compute_signals(candles: list[dict]) -> dict:
    pvi, nvi          = calc_pvi_nvi(candles)
    pvi_ema           = calc_ema(pvi, PVI_EMA_LEN)
    nvi_ema           = calc_ema(nvi, NVI_EMA_LEN)
    atr               = calc_atr(candles, ATR_LEN)
    di_plus, di_minus, adx = calc_dmi_adx(candles, ADX_LEN, ADX_SMOOTH)

    i   = len(candles) - 1   # last closed bar
    i_1 = i - 1              # previous bar

    pvi_cross = (
        not math.isnan(pvi[i])     and not math.isnan(pvi_ema[i]) and
        not math.isnan(pvi[i_1])   and not math.isnan(pvi_ema[i_1]) and
        pvi[i]   > pvi_ema[i] and     # current bar: PVI above EMA
        pvi[i_1] <= pvi_ema[i_1]       # prev bar:    PVI at or below EMA
    )

    # ATR comparison: current vs 10 bars ago
    atr_10ago = atr[i - 10] if i >= 10 else float("nan")

    return {
        "pvi_cross":  pvi_cross,
        "pvi_val":    pvi[i],
        "pvi_ema":    pvi_ema[i],
        "pvi_bull":   pvi[i] > pvi_ema[i] if not math.isnan(pvi[i]) else False,
        "nvi_val":    nvi[i],
        "nvi_ema":    nvi_ema[i],
        "nvi_bull":   nvi[i] > nvi_ema[i] if not math.isnan(nvi[i]) else False,
        "atr_val":    atr[i],
        "atr_10ago":  atr_10ago,
        "adx_val":    adx[i],
        "di_plus":    di_plus[i],
        "di_minus":   di_minus[i],
        "close":      candles[i]["close"],
        "bar_time":   candles[i]["open_time"],
    }


# ─────────────────────────────────────────────
#  Build alert message
# ─────────────────────────────────────────────

def build_message(sig: dict, live_price: float) -> str:
    ts = datetime.fromtimestamp(sig["bar_time"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pvi_signal = "▲ Bullish" if sig["pvi_bull"] else "▼ Bearish"
    adx_str    = adx_label(sig["adx_val"])
    atr_str    = atr_label(sig["atr_val"], sig["atr_10ago"])
    dmi_str    = dmi_label(sig["di_plus"], sig["di_minus"])

    msg = (
        f"🚨 <b>SOL/USDT — PVI Crossover Alert</b> 🚨\n"
        f"⏱ Timeframe: 5m  |  Bar: {ts}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>SOL Price:</b>  ${live_price:.4f}\n"
        f"📊 <b>PVI Signal:</b> {pvi_signal}\n"
        f"   PVI: {safe(sig['pvi_val'], '.4f')}  |  EMA({PVI_EMA_LEN}): {safe(sig['pvi_ema'], '.4f')}\n"
        f"📈 <b>ADX ({ADX_LEN}):</b>  {safe(sig['adx_val'], '.2f')}  — {adx_str}\n"
        f"🌊 <b>ATR ({ATR_LEN}):</b>  {safe(sig['atr_val'], '.4f')}  — {atr_str}\n"
        f"🔁 <b>DMI Signal:</b> {dmi_str}\n"
        f"   DI+: {safe(sig['di_plus'], '.2f')}  |  DI−: {safe(sig['di_minus'], '.2f')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ PVI crossed above EMA({PVI_EMA_LEN}) — Potential bullish momentum!"
    )
    return msg


# ─────────────────────────────────────────────
#  Main loop
# ─────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  SOL/USDT Futures PVI Alert Bot")
    print(f"  Symbol   : {SYMBOL}")
    print(f"  Interval : {INTERVAL}")
    print(f"  PVI EMA  : {PVI_EMA_LEN}")
    print(f"  Started  : {now_str()}")
    print("=" * 55)

    last_alert_bar: int | None = None   # track which bar triggered last alert

    while True:
        try:
            candles = fetch_klines(SYMBOL, INTERVAL, limit=CANDLES_NEEDED)
            if len(candles) < 50:
                print(f"[{now_str()}] ⚠️  Not enough candles ({len(candles)}), retrying...")
                time.sleep(POLL_SECONDS)
                continue

            sig = compute_signals(candles)
            current_bar_ts = sig["bar_time"]

            status = (
                f"[{now_str()}] "
                f"Price=${sig['close']:.4f} | "
                f"PVI={safe(sig['pvi_val'],'.4f')} EMA={safe(sig['pvi_ema'],'.4f')} | "
                f"Cross={'YES ✅' if sig['pvi_cross'] else 'no'}"
            )
            print(status)

            if sig["pvi_cross"] and current_bar_ts != last_alert_bar:
                live_price = fetch_current_price(SYMBOL)
                msg = build_message(sig, live_price)
                send_telegram(msg)
                last_alert_bar = current_bar_ts
                print(f"[{now_str()}] 🔔 Alert fired for bar {current_bar_ts}")
            else:
                if sig["pvi_cross"] and current_bar_ts == last_alert_bar:
                    print(f"[{now_str()}] ⏭  Crossover already alerted for this bar, skipping.")

        except requests.exceptions.RequestException as e:
            print(f"[{now_str()}] ❌ Network error: {e}")
        except Exception as e:
            print(f"[{now_str()}] ❌ Unexpected error: {e}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
