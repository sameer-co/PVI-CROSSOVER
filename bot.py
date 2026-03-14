
   
   """
SOL/USDT Futures - PVI Crossover Paper Trading Simulator
- Entry  : PVI crosses above EMA(13)
- SL     : Below crossover candle low
- TP     : 3x SL distance (1:3 Risk/Reward)
- Tracks : Win rate, PnL, accuracy in real time
- Alerts : Telegram on every entry/exit + daily summary
"""

import subprocess
import sys

try:
    import requests
except ImportError:
    print("Installing requests...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "--quiet"])
    import requests

import time
import math
from datetime import datetime, timezone, timedelta

# ─────────────────────────────────────────────
#  ✏️  CONFIGURE THESE
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = '8050135427:AAFNQYFpU8lMQ-reJlvLnPYFKc8pyPrHblE'
TELEGRAM_CHAT_ID   = '1950462171'
# ─────────────────────────────────────────────
#  Paper Trading Config
# ─────────────────────────────────────────────
STARTING_CAPITAL   = 1000.0    # virtual USDT
TRADE_SIZE_PCT     = 0.95      # use 95% of capital per trade
RR_RATIO           = 3.0       # TP = 3x SL distance

# ─────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────
SYMBOL          = "SOLUSDT"
INTERVAL        = "5m"
FUTURES_BASE    = "https://fapi.binance.com"
PVI_EMA_LEN     = 13
ATR_LEN         = 14
ADX_LEN         = 14
ADX_SMOOTH      = 14
CANDLES_NEEDED  = 600
CANDLE_SECONDS  = 300
BUFFER_SECONDS  = 3

IST = timezone(timedelta(hours=5, minutes=30))

# ─────────────────────────────────────────────
#  Time Helpers
# ─────────────────────────────────────────────

def now_str() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")

def bar_ist_str(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)\
                   .astimezone(IST).strftime("%Y-%m-%d %H:%M IST")

def seconds_until_next_candle() -> float:
    elapsed = time.time() % CANDLE_SECONDS
    return (CANDLE_SECONDS - elapsed) + BUFFER_SECONDS

# ─────────────────────────────────────────────
#  Binance Futures
# ─────────────────────────────────────────────

def fetch_klines(limit=600) -> list[dict]:
    r = requests.get(
        f"{FUTURES_BASE}/fapi/v1/klines",
        params={"symbol": SYMBOL, "interval": INTERVAL, "limit": limit},
        timeout=10
    )
    r.raise_for_status()
    candles = []
    for k in r.json():
        candles.append({
            "open_time": int(k[0]),
            "high":      float(k[2]),
            "low":       float(k[3]),
            "close":     float(k[4]),
            "volume":    float(k[5]),
        })
    return candles[:-1]   # drop unclosed bar

def fetch_price() -> float:
    r = requests.get(
        f"{FUTURES_BASE}/fapi/v1/ticker/price",
        params={"symbol": SYMBOL}, timeout=10
    )
    r.raise_for_status()
    return float(r.json()["price"])

# ─────────────────────────────────────────────
#  Indicators
# ─────────────────────────────────────────────

def calc_ema(series: list[float], length: int) -> list[float]:
    result = [float("nan")] * len(series)
    if len(series) < length:
        return result
    result[length - 1] = sum(series[:length]) / length
    mult = 2.0 / (length + 1)
    for i in range(length, len(series)):
        result[i] = series[i] * mult + result[i - 1] * (1 - mult)
    return result

def calc_pvi(candles: list[dict]) -> list[float]:
    n       = len(candles)
    pvi_raw = [1.0] * n
    for i in range(1, n):
        pc  = candles[i-1]["close"]
        cc  = candles[i]["close"]
        chg = (cc - pc) / pc if pc != 0 else 0.0
        pvi_raw[i] = pvi_raw[i-1] * (1 + chg) \
                     if candles[i]["volume"] > candles[i-1]["volume"] \
                     else pvi_raw[i-1]
    return [v * 1000.0 for v in pvi_raw]

def calc_atr(candles: list[dict], length: int) -> list[float]:
    n       = len(candles)
    tr_list = [float("nan")] * n
    for i in range(1, n):
        h, l, pc   = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
        tr_list[i] = max(h - l, abs(h - pc), abs(l - pc))
    atr = [float("nan")] * n
    if n < 1 + length:
        return atr
    atr[length] = sum(tr_list[1: length + 1]) / length
    alpha = 1.0 / length
    for i in range(length + 1, n):
        atr[i] = tr_list[i] * alpha + atr[i-1] * (1 - alpha)
    return atr

def calc_dmi_adx(candles, di_len, adx_smooth):
    n        = len(candles)
    plus_dm  = [0.0] * n
    minus_dm = [0.0] * n
    tr_list  = [0.0] * n
    for i in range(1, n):
        h, l   = candles[i]["high"],   candles[i]["low"]
        ph, pl = candles[i-1]["high"], candles[i-1]["low"]
        pc     = candles[i-1]["close"]
        up, dn      = h - ph, pl - l
        plus_dm[i]  = up if (up > dn and up > 0) else 0.0
        minus_dm[i] = dn if (dn > up and dn > 0) else 0.0
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

    s_tr  = rma(tr_list,  di_len)
    s_pdm = rma(plus_dm,  di_len)
    s_mdm = rma(minus_dm, di_len)
    di_p  = [float("nan")] * n
    di_m  = [float("nan")] * n
    dx    = [float("nan")] * n
    for i in range(n):
        if not math.isnan(s_tr[i]) and s_tr[i] != 0:
            dp, dm  = 100.0 * s_pdm[i] / s_tr[i], 100.0 * s_mdm[i] / s_tr[i]
            di_p[i] = dp
            di_m[i] = dm
            dsum    = dp + dm
            dx[i]   = 100.0 * abs(dp - dm) / dsum if dsum != 0 else 0.0
    first_dx = next((i for i in range(n) if not math.isnan(dx[i])), None)
    adx_out  = [float("nan")] * n
    if first_dx is not None and n >= first_dx + adx_smooth:
        adx_out[first_dx + adx_smooth - 1] = \
            sum(dx[first_dx: first_dx + adx_smooth]) / adx_smooth
        alpha = 1.0 / adx_smooth
        for i in range(first_dx + adx_smooth, n):
            adx_out[i] = dx[i] * alpha + adx_out[i-1] * (1 - alpha)
    return di_p, di_m, adx_out

# ─────────────────────────────────────────────
#  Signal Labels
# ─────────────────────────────────────────────

def adx_label(v):
    if math.isnan(v): return "N/A"
    if v >= 50: return "Extremely Strong"
    if v >= 25: return "Strong Trend"
    if v >= 20: return "Weak Trend"
    return "No Trend"

def atr_label(now, ago):
    if math.isnan(now) or math.isnan(ago) or ago == 0: return "N/A"
    if now > ago * 1.5:  return "🔥 High"
    if now > ago:         return "📈 Rising"
    if now < ago * 0.75: return "😴 Low"
    return "Normal"

def dmi_label(dp, dm):
    if math.isnan(dp) or math.isnan(dm): return "N/A"
    return "▲ Bullish" if dp > dm else "▼ Bearish"

def safe(val, fmt=".4f"):
    return f"{val:{fmt}}" if not math.isnan(val) else "N/A"

# ─────────────────────────────────────────────
#  Compute Signals
# ─────────────────────────────────────────────

def compute_signals(candles):
    pvi                    = calc_pvi(candles)
    pvi_ema                = calc_ema(pvi, PVI_EMA_LEN)
    atr                    = calc_atr(candles, ATR_LEN)
    di_plus, di_minus, adx = calc_dmi_adx(candles, ADX_LEN, ADX_SMOOTH)

    i, i_1 = len(candles) - 1, len(candles) - 2

    pvi_cross = (
        not math.isnan(pvi[i])   and not math.isnan(pvi_ema[i])   and
        not math.isnan(pvi[i_1]) and not math.isnan(pvi_ema[i_1]) and
        pvi[i] > pvi_ema[i] and pvi[i_1] <= pvi_ema[i_1]
    )

    atr_10ago = atr[i - 10] if i >= 10 else float("nan")

    return {
        "pvi_cross":    pvi_cross,
        "pvi_val":      pvi[i],
        "pvi_ema":      pvi_ema[i],
        "atr_val":      atr[i],
        "atr_10ago":    atr_10ago,
        "adx_val":      adx[i],
        "di_plus":      di_plus[i],
        "di_minus":     di_minus[i],
        "close":        candles[i]["close"],
        "candle_low":   candles[i]["low"],     # SL reference
        "candle_high":  candles[i]["high"],
        "bar_time":     candles[i]["open_time"],
    }

# ─────────────────────────────────────────────
#  Telegram
# ─────────────────────────────────────────────

def send_telegram(msg: str):
    url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}
    r       = requests.post(url, json=payload, timeout=10)
    r.raise_for_status()
    print(f"[{now_str()}] ✅ Telegram sent.")

# ─────────────────────────────────────────────
#  Trade State
# ─────────────────────────────────────────────

class PaperPortfolio:
    def __init__(self, capital: float):
        self.capital       = capital        # current virtual USDT
        self.start_capital = capital
        self.position      = None           # active trade dict or None

        # Accuracy tracking
        self.total_trades  = 0
        self.wins          = 0
        self.losses        = 0
        self.total_pnl     = 0.0
        self.trade_log     = []             # list of closed trade dicts

    def open_trade(self, entry_price: float, candle_low: float, bar_time: int, sig: dict):
        sl_distance        = entry_price - candle_low          # distance to SL
        sl_price           = candle_low                        # SL = crossover candle low
        tp_price           = entry_price + (sl_distance * RR_RATIO)  # TP = 3x SL
        trade_usdt         = self.capital * TRADE_SIZE_PCT
        qty                = trade_usdt / entry_price

        self.position = {
            "entry_price":  entry_price,
            "sl_price":     sl_price,
            "tp_price":     tp_price,
            "sl_distance":  sl_distance,
            "qty":          qty,
            "trade_usdt":   trade_usdt,
            "bar_time":     bar_time,
            "entry_time":   now_str(),
            "candles_held": 0,
            "sig":          sig,
        }

        sl_pct = (sl_distance / entry_price) * 100
        tp_pct = sl_pct * RR_RATIO

        send_telegram(
            f"📥 <b>PAPER TRADE OPENED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 Time    : {now_str()}\n"
            f"💰 Entry   : ${entry_price:.4f}\n"
            f"🛑 SL      : ${sl_price:.4f}  (-{sl_pct:.2f}%)\n"
            f"🎯 TP      : ${tp_price:.4f}  (+{tp_pct:.2f}%)\n"
            f"📐 R:R     : 1 : {RR_RATIO}\n"
            f"💵 Size    : ${trade_usdt:.2f} USDT\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 PVI     : {safe(sig['pvi_val'],'.4f')}  EMA: {safe(sig['pvi_ema'],'.4f')}\n"
            f"📈 ADX     : {safe(sig['adx_val'],'.2f')}  — {adx_label(sig['adx_val'])}\n"
            f"🌊 ATR     : {safe(sig['atr_val'],'.4f')}  — {atr_label(sig['atr_val'], sig['atr_10ago'])}\n"
            f"🔁 DMI     : {dmi_label(sig['di_plus'], sig['di_minus'])}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏦 Capital : ${self.capital:.2f} USDT"
        )
        print(f"[{now_str()}] 📥 Trade opened | Entry=${entry_price:.4f} SL=${sl_price:.4f} TP=${tp_price:.4f}")

    def check_exit(self, current_high: float, current_low: float, current_close: float) -> str | None:
        """
        Check if current candle hits TP or SL.
        Returns 'TP', 'SL', or None.
        Priority: if candle hits both (gap), SL takes priority (conservative).
        """
        if self.position is None:
            return None

        self.position["candles_held"] += 1
        hit_sl = current_low  <= self.position["sl_price"]
        hit_tp = current_high >= self.position["tp_price"]

        if hit_sl:
            return "SL"
        if hit_tp:
            return "TP"
        return None

    def close_trade(self, exit_reason: str, exit_price: float):
        p         = self.position
        pnl_usdt  = (exit_price - p["entry_price"]) * p["qty"]
        pnl_pct   = ((exit_price - p["entry_price"]) / p["entry_price"]) * 100
        self.capital += pnl_usdt
        self.total_pnl += pnl_usdt
        self.total_trades += 1

        result = "WIN ✅" if exit_reason == "TP" else "LOSS ❌"
        if exit_reason == "TP":
            self.wins += 1
        else:
            self.losses += 1

        win_rate = (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0
        total_return = ((self.capital - self.start_capital) / self.start_capital) * 100

        trade_record = {
            "trade_no":    self.total_trades,
            "entry":       p["entry_price"],
            "exit":        exit_price,
            "sl":          p["sl_price"],
            "tp":          p["tp_price"],
            "result":      exit_reason,
            "pnl_usdt":    pnl_usdt,
            "pnl_pct":     pnl_pct,
            "candles_held": p["candles_held"],
            "entry_time":  p["entry_time"],
            "exit_time":   now_str(),
        }
        self.trade_log.append(trade_record)

        send_telegram(
            f"{'🎯' if exit_reason == 'TP' else '🛑'} <b>PAPER TRADE CLOSED — {result}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 Exit Time  : {now_str()}\n"
            f"📥 Entry      : ${p['entry_price']:.4f}\n"
            f"📤 Exit       : ${exit_price:.4f}\n"
            f"📊 PnL        : {'+'if pnl_usdt>=0 else ''}{pnl_usdt:.2f} USDT  "
            f"({'+'if pnl_pct>=0 else ''}{pnl_pct:.2f}%)\n"
            f"⏱ Held        : {p['candles_held']} candles "
            f"({p['candles_held']*5} min)\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 <b>Accuracy Scorecard</b>\n"
            f"   Total Trades : {self.total_trades}\n"
            f"   Wins ✅      : {self.wins}\n"
            f"   Losses ❌    : {self.losses}\n"
            f"   Win Rate     : {win_rate:.1f}%\n"
            f"   Total PnL    : {'+'if self.total_pnl>=0 else ''}{self.total_pnl:.2f} USDT\n"
            f"   Capital      : ${self.capital:.2f} USDT\n"
            f"   Return       : {'+'if total_return>=0 else ''}{total_return:.2f}%\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏦 Starting Capital : ${self.start_capital:.2f} USDT"
        )
        print(
            f"[{now_str()}] {'✅' if exit_reason=='TP' else '❌'} Trade closed | "
            f"{exit_reason} @ ${exit_price:.4f} | PnL={pnl_usdt:+.2f} USDT | "
            f"WinRate={win_rate:.1f}%"
        )
        self.position = None

    def daily_summary(self):
        if self.total_trades == 0:
            send_telegram(
                f"📋 <b>Daily Summary</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🕐 {now_str()}\n"
                f"No trades taken today."
            )
            return

        win_rate     = self.wins / self.total_trades * 100
        total_return = (self.capital - self.start_capital) / self.start_capital * 100
        avg_win      = sum(t["pnl_usdt"] for t in self.trade_log if t["result"] == "TP") / max(self.wins, 1)
        avg_loss     = sum(t["pnl_usdt"] for t in self.trade_log if t["result"] == "SL") / max(self.losses, 1)
        expectancy   = (win_rate/100 * avg_win) + ((1 - win_rate/100) * avg_loss)

        send_telegram(
            f"📋 <b>Daily Paper Trading Summary</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 {now_str()}\n"
            f"📊 Symbol     : {SYMBOL} Futures 5m\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔢 Total Trades : {self.total_trades}\n"
            f"✅ Wins         : {self.wins}\n"
            f"❌ Losses       : {self.losses}\n"
            f"🎯 Win Rate     : {win_rate:.1f}%\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Total PnL    : {self.total_pnl:+.2f} USDT\n"
            f"📈 Avg Win      : {avg_win:+.2f} USDT\n"
            f"📉 Avg Loss     : {avg_loss:+.2f} USDT\n"
            f"🧮 Expectancy   : {expectancy:+.2f} USDT/trade\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏦 Start Capital : ${self.start_capital:.2f}\n"
            f"💵 Now Capital   : ${self.capital:.2f}\n"
            f"📊 Total Return  : {total_return:+.2f}%\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📐 Strategy: PVI EMA({PVI_EMA_LEN}) | R:R = 1:{RR_RATIO}"
        )

# ─────────────────────────────────────────────
#  Main Loop
# ─────────────────────────────────────────────

def main():
    start_time = now_str()
    portfolio  = PaperPortfolio(STARTING_CAPITAL)

    print("=" * 55)
    print("  SOL/USDT PVI Crossover — Paper Trader")
    print(f"  Capital  : ${STARTING_CAPITAL}")
    print(f"  R:R      : 1:{RR_RATIO}")
    print(f"  SL       : Crossover candle low")
    print(f"  TP       : {RR_RATIO}x SL distance")
    print(f"  Started  : {start_time}")
    print("=" * 55)

    send_telegram(
        f"🤖 <b>Paper Trader Started</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 Time      : {start_time}\n"
        f"📊 Symbol    : {SYMBOL} Futures 5m\n"
        f"💵 Capital   : ${STARTING_CAPITAL} USDT (virtual)\n"
        f"📐 R:R Ratio : 1 : {RR_RATIO}\n"
        f"🛑 SL        : Crossover candle LOW\n"
        f"🎯 TP        : {RR_RATIO}x SL distance\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👀 Watching PVI({PVI_EMA_LEN}) crossovers..."
    )

    last_signal_bar: int | None = None
    first_run   = True
    last_day    = now_str()[:10]   # track date for daily summary

    try:
        while True:
            if not first_run:
                sleep_secs = seconds_until_next_candle()
                next_wake  = datetime.fromtimestamp(
                    time.time() + sleep_secs, tz=timezone.utc
                ).astimezone(IST).strftime("%H:%M:%S IST")
                print(f"[{now_str()}] 💤 Sleeping {sleep_secs:.1f}s → next at {next_wake}")
                time.sleep(sleep_secs)
            first_run = False

            # ── Daily summary at midnight IST ──
            today = now_str()[:10]
            if today != last_day:
                portfolio.daily_summary()
                last_day = today

            try:
                candles = fetch_klines(CANDLES_NEEDED)
                if len(candles) < 50:
                    print(f"[{now_str()}] ⚠️ Not enough candles, skipping.")
                    continue

                sig            = compute_signals(candles)
                current_bar_ts = sig["bar_time"]
                current_price  = sig["close"]

                # ── Check exit on active position ──
                if portfolio.position is not None:
                    exit_reason = portfolio.check_exit(
                        sig["candle_high"],
                        sig["candle_low"],
                        current_price
                    )
                    if exit_reason == "TP":
                        portfolio.close_trade("TP", portfolio.position["tp_price"])
                    elif exit_reason == "SL":
                        portfolio.close_trade("SL", portfolio.position["sl_price"])

                # ── Check entry signal ──
                if (sig["pvi_cross"]
                        and current_bar_ts != last_signal_bar
                        and portfolio.position is None):

                    entry_price  = fetch_price()
                    candle_low   = sig["candle_low"]
                    sl_distance  = entry_price - candle_low

                    # Skip if SL is too tight (< 0.1%) or too wide (> 5%)
                    sl_pct = (sl_distance / entry_price) * 100
                    if sl_pct < 0.1:
                        print(f"[{now_str()}] ⚠️ SL too tight ({sl_pct:.2f}%), skipping trade.")
                    elif sl_pct > 5.0:
                        print(f"[{now_str()}] ⚠️ SL too wide ({sl_pct:.2f}%), skipping trade.")
                    else:
                        portfolio.open_trade(entry_price, candle_low, current_bar_ts, sig)
                        last_signal_bar = current_bar_ts

                elif sig["pvi_cross"] and portfolio.position is not None:
                    print(f"[{now_str()}] ⏭ Signal found but position already open, skipping.")

                # ── Console status ──
                pos_str = (
                    f"IN TRADE | Entry=${portfolio.position['entry_price']:.4f} "
                    f"SL=${portfolio.position['sl_price']:.4f} "
                    f"TP=${portfolio.position['tp_price']:.4f} "
                    f"Candles={portfolio.position['candles_held']}"
                ) if portfolio.position else "No position"

                print(
                    f"[{now_str()}] "
                    f"${current_price:.4f} | "
                    f"PVI={safe(sig['pvi_val'],'.2f')} EMA={safe(sig['pvi_ema'],'.2f')} | "
                    f"Cross={'YES ✅' if sig['pvi_cross'] else 'no'} | "
                    f"{pos_str}"
                )

            except requests.exceptions.RequestException as e:
                print(f"[{now_str()}] ❌ Network error: {e}")
            except Exception as e:
                print(f"[{now_str()}] ❌ Error: {e}")

    except (KeyboardInterrupt, SystemExit):
        stop_time = now_str()
        print(f"\n[{stop_time}] 🛑 Stopping...")
        portfolio.daily_summary()
        try:
            send_telegram(
                f"🛑 <b>Paper Trader Stopped</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🕐 Stopped : {stop_time}\n"
                f"🚀 Started : {start_time}\n"
                f"🔢 Trades  : {portfolio.total_trades}\n"
                f"💰 Final Capital : ${portfolio.capital:.2f} USDT\n"
                f"📊 Return  : {((portfolio.capital-portfolio.start_capital)/portfolio.start_capital*100):+.2f}%"
            )
        except Exception as e:
            print(f"Could not send stop message: {e}")
        print(f"[{now_str()}] 👋 Exited cleanly.")


if __name__ == "__main__":
    main()
