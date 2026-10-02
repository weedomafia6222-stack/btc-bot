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

# 12 Top High-Volume Coins for Steady Signals
PAIRS = {
    "BTC-USD":   {"name": "BTC/USD",   "atr_mult": 1.3},
    "ETH-USD":   {"name": "ETH/USD",   "atr_mult": 1.4},
    "SOL-USD":   {"name": "SOL/USD",   "atr_mult": 1.5},
    "XRP-USD":   {"name": "XRP/USD",   "atr_mult": 1.5},
    "DOGE-USD":  {"name": "DOGE/USD",  "atr_mult": 1.6},
    "ADA-USD":   {"name": "ADA/USD",   "atr_mult": 1.5},
    "AVAX-USD":  {"name": "AVAX/USD",  "atr_mult": 1.5},
    "LINK-USD":  {"name": "LINK/USD",  "atr_mult": 1.4},
    "NEAR-USD":  {"name": "NEAR/USD",  "atr_mult": 1.5},
    "SUI-USD":   {"name": "SUI/USD",   "atr_mult": 1.5},
    "APT-USD":   {"name": "APT/USD",   "atr_mult": 1.5},
    "DOT-USD":   {"name": "DOT/USD",   "atr_mult": 1.4}
}

COOLDOWN_MINUTES = 20
last_signal_time = {pair: 0 for pair in PAIRS}

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

def fetch_candles_coinbase(product_id, granularity=300, limit=60):
    url = f"https://api.exchange.coinbase.com/products/{product_id}/candles?granularity={granularity}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=6)
        if res.status_code == 200:
            raw = res.json()
            df = pd.DataFrame(raw, columns=['timestamp', 'low', 'high', 'open', 'close', 'volume'])
            df = df.sort_values('timestamp').reset_index(drop=True)
            return df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
    except Exception as e:
        print(f"Fetch Error ({product_id}): {e}", flush=True)
    return None

def compute_indicators(df):
    df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    
    # ATR (14) for Dynamic Structural Stop Loss
    high = df['high']
    low = df['low']
    close_prev = df['close'].shift(1)
    tr = pd.concat([high - low, (high - close_prev).abs(), (low - close_prev).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()
    return df

def check_ema_pullback_setup(df, cfg):
    if df is None or len(df) < 35:
        return None

    df = compute_indicators(df)

    p2 = df.iloc[-3]  # Retest/Touch candle
    p1 = df.iloc[-2]  # Confirmed Confirmation candle
    curr = df.iloc[-1]  # Active breakout candle

    curr_price = float(curr['close'])
    atr = float(p1['atr'])

    if pd.isna(atr) or atr <= 0:
        return None

    # --- 1. BULLISH SCALP (9 EMA > 21 EMA Trend Continuation) ---
    is_bull_trend = p1['ema9'] > p1['ema21']
    # Price pulled back to touch the 9-21 EMA value zone
    touched_zone_bull = (p2['low'] <= p2['ema9']) or (p1['low'] <= p1['ema9'])
    # Confirmation: Strong Green Candle closing above EMA 9
    bull_confirmation = (p1['close'] > p1['open']) and (p1['close'] > p1['ema9'])
    # Active breakout above the confirmation candle high
    if is_bull_trend and touched_zone_bull and bull_confirmation:
        if curr_price > p1['high']:
            sl = round(min(p1['low'], p2['low']) - (atr * cfg['atr_mult']), 4)
            risk = round(curr_price - sl, 4)
            if risk > 0:
                tp1 = round(curr_price + risk, 4)          # 1:1 Quick Target
                tp2 = round(curr_price + (risk * 1.5), 4)   # 1:1.5 Runner
                return {
                    "side": "LONG",
                    "entry": curr_price,
                    "sl": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "risk": risk,
                    "reward1": risk,
                    "reward2": round(risk * 1.5, 4)
                }

    # --- 2. BEARISH SCALP (9 EMA < 21 EMA Trend Continuation) ---
    is_bear_trend = p1['ema9'] < p1['ema21']
    # Price pulled back to touch the 9-21 EMA value zone
    touched_zone_bear = (p2['high'] >= p2['ema9']) or (p1['high'] >= p1['ema9'])
    # Confirmation: Strong Red Candle closing below EMA 9
    bear_confirmation = (p1['close'] < p1['open']) and (p1['close'] < p1['ema9'])
    # Active breakout below the confirmation candle low
    if is_bear_trend and touched_zone_bear and bear_confirmation:
        if curr_price < p1['low']:
            sl = round(max(p1['high'], p2['high']) + (atr * cfg['atr_mult']), 4)
            risk = round(sl - curr_price, 4)
            if risk > 0:
                tp1 = round(curr_price - risk, 4)          # 1:1 Quick Target
                tp2 = round(curr_price - (risk * 1.5), 4)   # 1:1.5 Runner
                return {
                    "side": "SHORT",
                    "entry": curr_price,
                    "sl": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "risk": risk,
                    "reward1": risk,
                    "reward2": round(risk * 1.5, 4)
                }

    return None

def run_scanner():
    print("🚀 Active Trend-Pullback Scalper Live...", flush=True)
    send_telegram_alert(
        "⚡ *ACTIVE TREND-SCALPER ACTIVATED!*\n\n"
        "• *Universe:* 12 High-Volume Liquid Coins\n"
        "• *Strategy:* EMA 9/21 Dynamic Value Zone Pullback\n"
        "• *Execution:* Breakout with Confirmed Closed Retest\n"
        "• *Target Plan:* 1:1 (Quick Lock) & 1:1.5 (Runner)\n"
        "• *Expected Frequency:* 6–10 High-Quality Signals/Day"
    )

    while True:
        try:
            for pair, cfg in PAIRS.items():
                now = time.time()
                if (now - last_signal_time[pair]) < (COOLDOWN_MINUTES * 60):
                    continue

                df = fetch_candles_coinbase(pair, granularity=300, limit=45)
                time.sleep(0.3)

                setup = check_ema_pullback_setup(df, cfg)
                if setup:
                    last_signal_time[pair] = now

                    icon = "🟢" if setup['side'] == "LONG" else "🔴"
                    msg = (
                        f"🚨 *[VIP SCALP] {cfg['name']} {setup['side']}* {icon}\n\n"
                        f"⏱ *Timeframe:* 5-Minute Trend Retest\n"
                        f"🔹 *Entry Price:* `${setup['entry']:,.4f}`\n"
                        f"🛑 *Stop Loss:* `${setup['sl']:,.4f}` (Risk: `${setup['risk']:,.4f}`)\n"
                        f"🎯 *Target 1 (1:1):* `${setup['tp1']:,.4f}` (+`${setup['reward1']:,.4f}`)\n"
                        f"🎯 *Target 2 (1:1.5):* `${setup['tp2']:,.4f}` (+`${setup['reward2']:,.4f}`)\n\n"
                        f"💡 *Trading Rules:*\n"
                        f"1. Book 60%–70% quantity at Target 1.\n"
                        f"2. Move Stop Loss to Entry (Cost) immediately.\n"
                        f"3. Suggested Leverage: 5x – 10x max."
                    )
                    send_telegram_alert(msg)

            time.sleep(20)

        except Exception as e:
            print(f"Scanner Exception: {e}", flush=True)
            time.sleep(10)

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Dynamic Trend Scalper Live!")

    def log_message(self, format, *args):
        return

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

if __name__ == "__main__":
    t = threading.Thread(target=run_scanner, daemon=True)
    t.start()
    run_server()
