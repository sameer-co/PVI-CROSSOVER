"""
SOL/USDT Futures — Multi-Timeframe PVI Crossover Alert Bot
- Monitors : 5m, 15m, 1h simultaneously
- Signal   : PVI crosses above OR below EMA(13)
- Alerts   : Telegram on every crossover (bullish + bearish)
- No trades, no paper portfolio — pure signal alerting
"""

import subprocess, sys

try:
    import requests
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "--quiet"])
    import requests

import time
import math
from datetime import datetime, timezone, timedelta

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = '8050135427:AAFNQYFpU8lMQ-reJlvLnPYFKc8pyPrHblE'
TELEGRAM_CHAT_ID   = '1950462171'

SYMBOL         = "SOLUSDT"
FUTURES_BASE   = "https://fapi.binance.com"
PVI_EMA_LEN    = 13
CANDLES_NEEDED = 1500          # deep history → EMA warm-up close to TradingView

# Timeframes to monitor: (interval_string, candle_seconds, label)
TIMEFRAMES = [
    ("5m",  300,   "5m"),
    ("15m", 900,   "15m"),
    ("1h",  3600,  "1H"),
]

IST = timezone(timedelta(hours=5, minutes=30))

# ─────────────────────────────────────────────
#  TIME HELPERS
# ─────────────────────────────────────────────

def now_str() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")

def bar_time_str(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc) \
                   .astimezone(IST).strftime("%Y-%m-%d %H:%M IST")

def seconds_until_next_candle(candle_seconds: int, buffer: int = 3) -> float:
    elapsed = time.time() % candle_seconds
    return (candle_seconds - elapsed) + buffer

# ─────────────────────────────────────────────
#  BINANCE FUTURES
# ─────────────────────────────────────────────

def fetch_klines(interval: str, limit: int = CANDLES_NEEDED) -> list[dict]:
    r = requests.get(
        f"{FUTURES_BASE}/fapi/v1/klines",
        params={"symbol": SYMBOL, "interval": interval, "limit": limit},
        timeout=10
    )
    r.raise_for_status()
    candles = []
    for k in r.json():
        candles.append({
            "open_time": int(k[0]),
            "open":      float(k[1]),
            "high":      float(k[2]),
            "low":       float(k[3]),
            "close":     float(k[4]),
            "volume":    float(k[5]),
        })
    return candles[:-1]   # drop unclosed bar

# ─────────────────────────────────────────────
#  INDICATORS
# ─────────────────────────────────────────────

def calc_pvi(candles: list[dict]) -> list[float]:
    n = len(candles)
    pvi = [1000.0] * n          # start at 1000, matching TradingView convention
    for i in range(1, n):
        pc  = candles[i-1]["close"]
        cc  = candles[i]["close"]
        chg = (cc - pc) / pc if pc != 0 else 0.0
        pvi[i] = pvi[i-1] * (1.0 + chg) \
                 if candles[i]["volume"] > candles[i-1]["volume"] \
                 else pvi[i-1]
    # Normalize entire series relative to its own last value.
    # Absolute value will still differ from TV (different history length)
    # but PVI vs EMA relationship — i.e. crossover detection — is preserved.
    last = pvi[-1]
    if last != 0:
        pvi = [v / last * 1000.0 for v in pvi]
    return pvi

def calc_ema(series: list[float], length: int) -> list[float]:
    result = [float("nan")] * len(series)
    if len(series) < length:
        return result
    result[length - 1] = sum(series[:length]) / length
    mult = 2.0 / (length + 1)
    for i in range(length, len(series)):
        result[i] = series[i] * mult + result[i-1] * (1 - mult)
    return result

# ─────────────────────────────────────────────
#  SIGNAL DETECTION
# ─────────────────────────────────────────────

def detect_cross(candles: list[dict]) -> dict:
    """
    Returns cross type for the latest closed candle.
    cross : 'bullish' | 'bearish' | None
    """
    pvi     = calc_pvi(candles)
    pvi_ema = calc_ema(pvi, PVI_EMA_LEN)

    i   = len(candles) - 1
    i_1 = len(candles) - 2

    valid = (
        not math.isnan(pvi[i])   and not math.isnan(pvi_ema[i]) and
        not math.isnan(pvi[i_1]) and not math.isnan(pvi_ema[i_1])
    )
    if not valid:
        return {"cross": None, "pvi": float("nan"), "ema": float("nan"),
                "close": candles[i]["close"], "bar_time": candles[i]["open_time"]}

    bullish = pvi[i] > pvi_ema[i] and pvi[i_1] <= pvi_ema[i_1]

    return {
        "cross":    "bullish" if bullish else None,
        "pvi":      pvi[i],
        "ema":      pvi_ema[i],
        "close":    candles[i]["close"],
        "bar_time": candles[i]["open_time"],
    }

# ─────────────────────────────────────────────
#  TELEGRAM
# ─────────────────────────────────────────────

def send_telegram(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    r   = requests.post(
        url,
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
        timeout=10
    )
    r.raise_for_status()
    print(f"[{now_str()}] ✅ Telegram sent.")

def alert_cross(tf_label: str, sig: dict):
    is_bull  = sig["cross"] == "bullish"
    emoji    = "🟢" if is_bull else "🔴"
    direction = "BULLISH CROSS ▲" if is_bull else "BEARISH CROSS ▼"
    action   = "PVI crossed <b>above</b> EMA(13)" if is_bull \
               else "PVI crossed <b>below</b> EMA(13)"

    send_telegram(
        f"{emoji} <b>[{SYMBOL}] PVI {direction}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ Timeframe : {tf_label}\n"
        f"🕐 Bar Time  : {bar_time_str(sig['bar_time'])}\n"
        f"💵 Price     : ${sig['close']:.4f}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {action}\n"
        f"   PVI  : {sig['pvi']:.4f}\n"
        f"   EMA  : {sig['ema']:.4f}\n"
        f"   Diff : {sig['pvi'] - sig['ema']:+.4f}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔔 Alert @ {now_str()}"
    )

# ─────────────────────────────────────────────
#  PER-TIMEFRAME STATE
# ─────────────────────────────────────────────

class TFWatcher:
    """Watches one timeframe for PVI crossovers."""

    def __init__(self, interval: str, candle_seconds: int, label: str):
        self.interval       = interval
        self.candle_seconds = candle_seconds
        self.label          = label
        self.last_bar_ts    = None       # open_time of last alerted bar
        self.next_wake      = 0.0        # unix timestamp of next check

    def is_due(self) -> bool:
        return time.time() >= self.next_wake

    def schedule_next(self):
        self.next_wake = time.time() + seconds_until_next_candle(self.candle_seconds)

    def check(self):
        try:
            candles = fetch_klines(self.interval)
            if len(candles) < PVI_EMA_LEN + 5:
                print(f"[{now_str()}] [{self.label}] ⚠️  Not enough candles.")
                return

            sig = detect_cross(candles)

            # Status log regardless of signal
            cross_str = "🟢 BULL" if sig["cross"] == "bullish" else "none"
            print(
                f"[{now_str()}] [{self.label}] "
                f"${sig['close']:.4f} | "
                f"PVI={sig['pvi']:.2f} EMA={sig['ema']:.2f} | "
                f"Cross={cross_str}"
            )

            # Only alert once per bar
            if sig["cross"] and sig["bar_time"] != self.last_bar_ts:
                alert_cross(self.label, sig)
                self.last_bar_ts = sig["bar_time"]

        except requests.exceptions.RequestException as e:
            print(f"[{now_str()}] [{self.label}] ❌ Network error: {e}")
        except Exception as e:
            print(f"[{now_str()}] [{self.label}] ❌ Error: {e}")
        finally:
            self.schedule_next()

# ─────────────────────────────────────────────
#  MAIN LOOP
# ─────────────────────────────────────────────

def main():
    start_time = now_str()
    watchers   = [TFWatcher(iv, cs, lb) for iv, cs, lb in TIMEFRAMES]

    tf_str = " | ".join(lb for _, _, lb in TIMEFRAMES)
    print("=" * 55)
    print("  SOL/USDT PVI Crossover — Multi-TF Alert Bot")
    print(f"  Timeframes : {tf_str}")
    print(f"  Signal     : PVI EMA({PVI_EMA_LEN}) crossover")
    print(f"  Started    : {start_time}")
    print("=" * 55)

    send_telegram(
        f"🤖 <b>PVI Alert Bot Started</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 Time       : {start_time}\n"
        f"📊 Symbol     : {SYMBOL} Futures\n"
        f"⏱ Timeframes : {tf_str}\n"
        f"📈 Signal     : PVI EMA({PVI_EMA_LEN}) crossover\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👀 Watching for bullish crosses only..."
    )

    # Stagger first checks so all TFs don't hit API simultaneously
    for i, w in enumerate(watchers):
        w.next_wake = time.time() + i * 2.0

    try:
        while True:
            now = time.time()
            due = [w for w in watchers if w.is_due()]

            for w in due:
                w.check()

            # Sleep until the nearest next wake time
            if watchers:
                sleep_secs = max(0.5, min(w.next_wake for w in watchers) - time.time())
                time.sleep(sleep_secs)

    except (KeyboardInterrupt, SystemExit):
        stop_time = now_str()
        print(f"\n[{stop_time}] 🛑 Stopped.")
        try:
            send_telegram(
                f"🛑 <b>PVI Alert Bot Stopped</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🚀 Started : {start_time}\n"
                f"🛑 Stopped : {stop_time}"
            )
        except Exception as e:
            print(f"Could not send stop message: {e}")
        print(f"[{now_str()}] 👋 Exited cleanly.")


if __name__ == "__main__":
    main()
