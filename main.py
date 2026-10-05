import os
import time
import threading
import requests
import pandas as pd
import numpy as np
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# ----------------- CONFIGURATION -----------------
TELEGRAM_BOT_TOKEN = "8608122374:AAF5OXFFo4pKrhda8RyThOCs9dN0zkd0V14"
TELEGRAM_CHAT_ID   = "1327677831"

# Pairs to scan for SMC Liquidity Sweeps
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
COOLDOWN_MINUTES = 45
last_signal_tracker = {s: 0 for s in SYMBOLS}

def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, data=payload, timeout=8)
    except Exception as e:
        print(f"Telegram Alert Error: {e}", flush=True)

def fetch_candles(symbol, interval="15m", limit=40):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=7)
        if res.status_code == 200:
            raw = res.json()
            if isinstance(raw, list) and len(raw) >= 25:
                df = pd.DataFrame(raw, columns=[
                    'timestamp', 'open', 'high', 'low', 'close', 'volume',
                    'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'
                ])
                df = df.iloc[:-1]  # Exclude live candle
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    df[col] = df[col].astype(float)
                return df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
    except Exception as e:
        print(f"Fetch Error ({symbol}): {e}", flush=True)
    return None

def check_liquidity_sweep(df, symbol):
    if df is None or len(df) < 25:
        return None

    # Reference range (prior 20 candles excluding recent 2)
    lookback = df.iloc[-22:-2]
    key_high = lookback['high'].max()
    key_low = lookback['low'].min()

    # The sweep candle (previous candle)
    sweep_candle = df.iloc[-2]
    # The confirmation candle (last closed candle)
    confirm_candle = df.iloc[-1]

    # --- BEARISH SWEEP (Top Liquidity Hunt / Supply Rejection) ---
    # 1. Sweep candle ne High ko break kiya, par close key_high ke niche hua (wick sweep)
    # 2. Confirm candle bearish hai (close < open) aur price rejection validate karti hai
    if (sweep_candle['high'] > key_high and sweep_candle['close'] < key_high and
        confirm_candle['close'] < confirm_candle['open']):
        
        entry = float(confirm_candle['close'])
        sl = float(round(sweep_candle['high'] * 1.0015, 2))  # Thoda sa buffer wick ke upar
        risk = round(sl - entry, 2)
        
        if risk > 0:
            tp1 = round(entry - (risk * 1.5), 2)  # 1:1.5 RR (Partial)
            tp2 = round(entry - (risk * 3.0), 2)  # 1:3.0 RR (Target)
            
            return {
                "symbol": symbol,
                "side": "SELL (SHORT)",
                "icon": "🔴",
                "entry": entry,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "risk": risk,
                "rr": "1:3.0",
                "swept_level": round(key_high, 2)
            }

    # --- BULLISH SWEEP (Bottom Liquidity Hunt / Demand Rejection) ---
    # 1. Sweep candle ne Low ko sweep kiya, par close key_low ke upar hua
    # 2. Confirm candle bullish hai (close > open)
    if (sweep_candle['low'] < key_low and sweep_candle['close'] > key_low and
        confirm_candle['close'] > confirm_candle['open']):
        
        entry = float(confirm_candle['close'])
        sl = float(round(sweep_candle['low'] * 0.9985, 2))  # Buffer below wick
        risk = round(entry - sl, 2)
        
        if risk > 0:
            tp1 = round(entry + (risk * 1.5), 2)
            tp2 = round(entry + (risk * 3.0), 2)
            
            return {
                "symbol": symbol,
                "side": "BUY (LONG)",
                "icon": "🟢",
                "entry": entry,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "risk": risk,
                "rr": "1:3.0",
                "swept_level": round(key_low, 2)
            }

    return None

def run_smc_scanner():
    global last_signal_tracker
    print("🚀 SMC Liquidity Sweep & Reversal Bot Active...", flush=True)

    while True:
        try:
            now = time.time()
            for sym in SYMBOLS:
                if (now - last_signal_tracker[sym]) < (COOLDOWN_MINUTES * 60):
                    continue

                df = fetch_candles(sym, interval="15m", limit=30)
                setup = check_liquidity_sweep(df, sym)

                if setup:
                    last_signal_tracker[sym] = now
                    msg = (
                        f"{setup['icon']} *SMC SNIPER SETUP: {setup['symbol']}* {setup['icon']}\n\n"
                        f"Action: *{setup['side']}*\n"
                        f"🔹 *Entry Price:* `${setup['entry']:,.2f}`\n"
                        f"🛑 *Stop Loss:* `${setup['sl']:,.2f}` (Risk: `${setup['risk']:,.2f}`)\n"
                        f"🎯 *Target 1 (1:1.5):* `${setup['tp1']:,.2f}` (Safe 60% Book)\n"
                        f"🎯 *Target 2 (Runner):* `${setup['tp2']:,.2f}` (RR: {setup['rr']})\n\n"
                        f"🧠 *Smart Money Footprint:*\n"
                        f"• Swept Liquidity Pivot: `${setup['swept_level']:,.2f}`\n"
                        f"• Pattern: Trap & Reversal (Wick Hunt)\n\n"
                        f"⚡ *Rule:* Target 1 aate hi Stop Loss direct entry (Cost) par lock karein."
                    )
                    send_telegram_alert(msg)
                    time.sleep(1)

                time.sleep(0.5)

            time.sleep(20)

        except Exception as e:
            print(f"SMC Scanner Loop Error: {e}", flush=True)
            time.sleep(10)

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"SMC Liquidity Bot Live!")

    def log_message(self, format, *args):
        return

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

if __name__ == "__main__":
    t = threading.Thread(target=run_smc_scanner, daemon=True)
    t.start()
    run_server()
