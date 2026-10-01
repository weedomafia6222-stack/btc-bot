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
    "BTC/USDT": {
        "name": "BTC/USDT (BITCOIN)",
        "min_fvg": 25.0,
        "sl_buf": 30.0,
        "rr": 2.0
    },
    "ETH/USDT": {
        "name": "ETH/USDT (ETHEREUM)",
        "min_fvg": 2.5,
        "sl_buf": 2.5,
        "rr": 2.0
    },
    "SOL/USDT": {
        "name": "SOL/USDT (SOLANA)",
        "min_fvg": 0.3,
        "sl_buf": 0.35,
        "rr": 2.0
    }
}

COOLDOWN_MINUTES = 25
last_signal_time = {pair: 0 for pair in PAIRS}
signal_counters  = {pair: 0 for pair in PAIRS}

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

exchange = ccxt.binance({'enableRateLimit': True})  # Binance reliable liquid feeds

def fetch_candles(symbol, timeframe='5m', limit=40):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    except Exception as e:
        print(f"Fetch Error ({symbol} {timeframe}): {e}", flush=True)
        return None

# ----------------- ROBUST SMC & ICT ENGINE -----------------
def check_smc_setup(df_htf, df_ltf, cfg):
    if df_htf is None or df_ltf is None or len(df_htf) < 20 or len(df_ltf) < 20:
        return None

    # 1. 15m HTF Liquidity Sweep Check (Last 3 candles mein kisi ne sweep kiya ho)
    htf_high = df_htf['high'].iloc[-18:-3].max()
    htf_low  = df_htf['low'].iloc[-18:-3].min()

    # Check sweep in recent 15m candles
    recent_htf = df_htf.iloc[-3:]
    htf_ssl_sweep = any((recent_htf['low'] < htf_low) & (recent_htf['close'] > htf_low))
    htf_bsl_sweep = any((recent_htf['high'] > htf_high) & (recent_htf['close'] < htf_high))

    # 2. 5m LTF Structure Break (CHoCH)
    ltf_swing_high = df_ltf['high'].iloc[-15:-3].max()
    ltf_swing_low  = df_ltf['low'].iloc[-15:-3].min()

    curr_candle = df_ltf.iloc[-1]
    curr_price  = curr_candle['close']

    # FVG Defined on closed candles [c_prev2, c_prev1, c_prev0]
    c0 = df_ltf.iloc[-4]
    c1 = df_ltf.iloc[-3]
    c2 = df_ltf.iloc[-2]  # Completed FVG candle

    # --- 1. BULLISH SMC SETUP ---
    bull_choch = (df_ltf['close'].iloc[-3] > ltf_swing_high) or (df_ltf['close'].iloc[-2] > ltf_swing_high)
    bull_fvg_gap = c2['low'] - c0['high']

    if htf_ssl_sweep and bull_choch and (bull_fvg_gap >= cfg['min_fvg']):
        fvg_top = c2['low']
        fvg_bottom = c0['high']
        # Active candle is tapping/retesting inside FVG zone
        if curr_candle['low'] <= fvg_top and curr_price >= fvg_bottom:
            sl = round(ltf_swing_low - cfg['sl_buf'], 2)
            risk = round(curr_price - sl, 2)
            if risk > 0:
                tp = round(curr_price + (risk * cfg['rr']), 2)
                return {
                    "side": "LONG",
                    "entry": curr_price,
                    "sl": sl,
                    "tp": tp,
                    "confluence": "15m SSL Sweep + 5m CHoCH + FVG Retest"
                }

    # --- 2. BEARISH SMC SETUP ---
    bear_choch = (df_ltf['close'].iloc[-3] < ltf_swing_low) or (df_ltf['close'].iloc[-2] < ltf_swing_low)
    bear_fvg_gap = c0['low'] - c2['high']

    if htf_bsl_sweep and bear_choch and (bear_fvg_gap >= cfg['min_fvg']):
        fvg_top = c0['low']
        fvg_bottom = c2['high']
        # Active candle is tapping/retesting inside FVG zone
        if curr_candle['high'] >= fvg_bottom and curr_price <= fvg_top:
            sl = round(ltf_swing_high + cfg['sl_buf'], 2)
            risk = round(sl - curr_price, 2)
            if risk > 0:
                tp = round(curr_price - (risk * cfg['rr']), 2)
                return {
                    "side": "SHORT",
                    "entry": curr_price,
                    "sl": sl,
                    "tp": tp,
                    "confluence": "15m BSL Sweep + 5m CHoCH + FVG Retest"
                }

    return None

# ----------------- SCANNER WORKER -----------------
def scanner_worker():
    print("🚀 Corrected SMC & ICT Crypto Scanner Started...", flush=True)
    send_telegram_alert(
        "🏛️ *SMC & ICT STRATEGY BOT ONLINE!*\n\n"
        "• *HTF:* 15m Liquidity Sweeps\n"
        "• *LTF:* 5m CHoCH + Fair Value Gap (FVG)\n"
        "• *Pairs:* BTC, ETH, SOL\n"
        "• *Target:* 1:2 Risk to Reward"
    )

    while True:
        try:
            for pair, cfg in PAIRS.items():
                now = time.time()
                if (now - last_signal_time[pair]) < (COOLDOWN_MINUTES * 60):
                    continue

                df_htf = fetch_candles(pair, timeframe='15m', limit=35)
                time.sleep(0.4)
                df_ltf = fetch_candles(pair, timeframe='5m', limit=35)
                time.sleep(0.4)

                setup = check_smc_setup(df_htf, df_ltf, cfg)
                if setup:
                    signal_counters[pair] += 1
                    last_signal_time[pair] = now

                    icon = "🟢" if setup['side'] == "LONG" else "🔴"
                    profit_delta = round(abs(setup['tp'] - setup['entry']), 2)
                    loss_delta   = round(abs(setup['entry'] - setup['sl']), 2)

                    msg = (
                        f"{icon} *SMC/ICT INSTITUTIONAL {setup['side']}* {icon}\n\n"
                        f"🔹 *Asset:* {cfg['name']}\n"
                        f"🔹 *Confluence:* {setup['confluence']}\n"
                        f"🔹 *Entry Price:* ${setup['entry']:,.2f}\n"
                        f"🎯 *Take Profit (1:{cfg['rr']}):* ${setup['tp']:,.2f} (+${profit_delta})\n"
                        f"🛑 *Stop Loss:* ${setup['sl']:,.2f} (-${loss_delta})\n\n"
                        f"⚡ *Rule:* 1:1 risk cover hote hi SL Entry par karein."
                    )
                    send_telegram_alert(msg)

            time.sleep(25)

        except Exception as e:
            print(f"Scanner Exception: {e}", flush=True)
            time.sleep(15)

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"SMC Bot Live & Scanning!")

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
