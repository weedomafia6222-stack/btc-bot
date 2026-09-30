import os
import time
import threading
import requests
import ccxt
import pandas as pd
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# ----------------- CONFIGURATION -----------------
TELEGRAM_BOT_TOKEN = "8608122374:AAF5OXFFo4pKrhda8RyThOCs9dN0zkd0V14"
TELEGRAM_CHAT_ID   = "1327677831"

PAIRS = {
    "BTC/USD": {
        "name": "BTC/USD (BITCOIN)",
        "min_fvg": 40.0,
        "sl_buf": 35.0,
        "rr": 2.0
    },
    "ETH/USD": {
        "name": "ETH/USD (ETHEREUM)",
        "min_fvg": 4.0,
        "sl_buf": 3.0,
        "rr": 2.0
    },
    "SOL/USD": {
        "name": "SOL/USD (SOLANA)",
        "min_fvg": 0.5,
        "sl_buf": 0.4,
        "rr": 2.0
    },
    "PAXG/USD": {
        "name": "PAXG/USD (GOLD (SPOT))",
        "min_fvg": 1.5,
        "sl_buf": 2.0,
        "rr": 2.0
    }
}

COOLDOWN_MINUTES = 25
last_signal_time = {pair: 0 for pair in PAIRS}
signal_counters  = {pair: 0 for pair in PAIRS}

# ----------------- TELEGRAM NOTIFIER -----------------
def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print(f"Telegram Alert Error: {e}", flush=True)

# ----------------- DATA FETCHER -----------------
exchange = ccxt.coinbase({'enableRateLimit': True})

def fetch_candles(symbol, timeframe='5m', limit=50):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    except Exception as e:
        print(f"Fetch Error ({symbol} {timeframe}): {e}", flush=True)
        return None

# ----------------- STRICT SMC & ICT STRATEGY ENGINE -----------------
def check_smc_setup(df_htf, df_ltf, cfg):
    """
    STRICT INSTITUTIONAL CONFIRMATION:
    1. 15m Liquidity Sweep (BSL / SSL)
    2. MUST have 5m Candle BODY CLOSE (Real CHoCH)
    3. Retracement into Fair Value Gap (FVG Zone)
    """
    if len(df_htf) < 20 or len(df_ltf) < 20:
        return None

    # 15m Higher Timeframe Swing Points (Past 15 candles)
    htf_high = df_htf['high'].iloc[-16:-1].max()
    htf_low  = df_htf['low'].iloc[-16:-1].min()

    # 15m Sweeps (Wick pierced past swing, body stayed back)
    htf_ssl_sweep = (df_htf['low'].iloc[-1] < htf_low) and (df_htf['close'].iloc[-1] > htf_low)
    htf_bsl_sweep = (df_htf['high'].iloc[-1] > htf_high) and (df_htf['close'].iloc[-1] < htf_high)

    # 5m Lower Timeframe Swings for CHoCH
    ltf_swing_high = df_ltf['high'].iloc[-12:-2].max()
    ltf_swing_low  = df_ltf['low'].iloc[-12:-2].min()

    # 5m Candles for FVG Analysis
    c0 = df_ltf.iloc[-3]
    c1 = df_ltf.iloc[-2]
    c2 = df_ltf.iloc[-1]
    curr_price = c2['close']

    # --- 1. BULLISH SETUP ---
    bullish_choch = c1['close'] > ltf_swing_high
    bull_fvg_gap = c2['low'] - c0['high']

    if htf_ssl_sweep and bullish_choch and (bull_fvg_gap >= cfg['min_fvg']):
        fvg_top = c2['low']
        fvg_bottom = c0['high']
        # Price must be actively retesting the FVG zone
        if fvg_bottom <= curr_price <= fvg_top:
            sl = round(ltf_swing_low - cfg['sl_buf'], 2)
            risk = curr_price - sl
            if risk > 0:
                tp = round(curr_price + (risk * cfg['rr']), 2)
                return {
                    "side": "LONG",
                    "entry": curr_price,
                    "sl": sl,
                    "tp": tp,
                    "fvg_size": bull_fvg_gap,
                    "confluence": "15m SSL Sweep + 5m Body CHoCH + Bullish FVG Tap"
                }

    # --- 2. BEARISH SETUP ---
    bearish_choch = c1['close'] < ltf_swing_low
    bear_fvg_gap = c0['low'] - c2['high']

    if htf_bsl_sweep and bearish_choch and (bear_fvg_gap >= cfg['min_fvg']):
        fvg_top = c0['low']
        fvg_bottom = c2['high']
        # Price must be actively retesting the FVG zone
        if fvg_bottom <= curr_price <= fvg_top:
            sl = round(ltf_swing_high + cfg['sl_buf'], 2)
            risk = sl - curr_price
            if risk > 0:
                tp = round(curr_price - (risk * cfg['rr']), 2)
                return {
                    "side": "SHORT",
                    "entry": curr_price,
                    "sl": sl,
                    "tp": tp,
                    "fvg_size": bear_fvg_gap,
                    "confluence": "15m BSL Sweep + 5m Body CHoCH + Bearish FVG Tap"
                }

    return None

# ----------------- SCANNER WORKER -----------------
def scanner_worker():
    print("🚀 Strict SMC & ICT Crypto/Gold Scanner Started...", flush=True)
    send_telegram_alert(
        "🏛️ *SMC & ICT MASTER STRATEGY ACTIVE!*\n\n"
        "• *HTF Structure:* 15m Liquidity Sweeps (BSL/SSL)\n"
        "• *LTF Execution:* 5m Body CHoCH + Fair Value Gap (FVG)\n"
        "• *Risk:Reward:* Minimum 1:2 RR\n"
        "• *Pairs:* BTC, ETH, SOL, GOLD (PAXG)"
    )

    while True:
        try:
            for pair, cfg in PAIRS.items():
                now = time.time()
                if (now - last_signal_time[pair]) < (COOLDOWN_MINUTES * 60):
                    continue

                df_htf = fetch_candles(pair, timeframe='15m', limit=30)
                time.sleep(0.5)
                df_ltf = fetch_candles(pair, timeframe='5m', limit=30)
                time.sleep(0.5)

                if df_htf is None or df_ltf is None:
                    continue

                setup = check_smc_setup(df_htf, df_ltf, cfg)
                if setup:
                    signal_counters[pair] += 1
                    last_signal_time[pair] = now

                    icon = "🟢" if setup['side'] == "LONG" else "🔴"
                    profit_delta = round(abs(setup['tp'] - setup['entry']), 2)
                    loss_delta   = round(abs(setup['entry'] - setup['sl']), 2)

                    msg = (
                        f"{icon} *SMC/ICT INSTITUTIONAL {setup['side']} #{signal_counters[pair]}* {icon}\n\n"
                        f"🔹 *Asset:* {cfg['name']}\n"
                        f"🔹 *Confluence:* {setup['confluence']}\n"
                        f"🔹 *Entry Price:* ${setup['entry']:,.2f}\n"
                        f"🎯 *Take Profit:* ${setup['tp']:,.2f} (+${profit_delta})\n"
                        f"🛑 *Stop Loss:* ${setup['sl']:,.2f} (-${loss_delta})\n"
                        f"⚖️️ *Risk:Reward:* 1:{cfg['rr']}\n\n"
                        f"📌 *Execution Rule:* Trail SL to Entry at 1:1 reward. Hold balance till opposing liquidity."
                    )
                    send_telegram_alert(msg)

            time.sleep(25)

        except Exception as e:
            print(f"Scanner Exception: {e}", flush=True)
            time.sleep(15)

# ----------------- UPTIME HEALTH CHECK SERVER -----------------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"SMC Multi-Timeframe Bot Healthy!")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()

    def log_message(self, format, *args):
        return

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

if __name__ == "__main__":
    t = threading.Thread(target=scanner_worker, daemon=True)
    t.start()
    run_server()
