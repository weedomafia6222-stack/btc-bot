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
        "min_move": 70.0,    # 3 candles mein kam se kam $70 ka impulse move
        "sl_buf": 25.0,
        "rr": 1.8
    },
    "ETH/USDT": {
        "name": "ETH/USDT (ETHEREUM)",
        "min_move": 5.0,     # Kam se kam $5 ka move
        "sl_buf": 2.5,
        "rr": 1.8
    },
    "SOL/USDT": {
        "name": "SOL/USDT (SOLANA)",
        "min_move": 0.6,     # Kam se kam $0.6 ka move
        "sl_buf": 0.35,
        "rr": 1.8
    },
    "PAXG/USDT": {
        "name": "GOLD (PAXG/USDT)",
        "min_move": 3.0,     # Kam se kam $3 ka impulse move
        "sl_buf": 2.0,
        "rr": 1.8
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
        requests.post(url, data=payload, timeout=8)
    except Exception as e:
        print(f"Telegram Alert Error: {e}", flush=True)

exchange = ccxt.binance({'enableRateLimit': True})

def fetch_candles(symbol, timeframe='5m', limit=30):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    except Exception as e:
        print(f"Fetch Error ({symbol}): {e}", flush=True)
        return None

def is_strong_body(candle):
    candle_range = candle['high'] - candle['low']
    if candle_range == 0:
        return False
    body_size = abs(candle['close'] - candle['open'])
    return (body_size / candle_range) >= 0.50  # Kam se kam 50% solid body honi chahiye

def check_filtered_momentum_setup(df, cfg):
    if df is None or len(df) < 6:
        return None

    c1 = df.iloc[-5]
    c2 = df.iloc[-4]
    c3 = df.iloc[-3]
    c4 = df.iloc[-2]  # Pullback Candle
    c5 = df.iloc[-1]  # Active Trigger Candle

    curr_price = c5['close']

    # --- 1. BULLISH SETUP (CALL / LONG) ---
    is_3_green = (
        (c1['close'] > c1['open']) and 
        (c2['close'] > c2['open']) and 
        (c3['close'] > c3['open']) and
        (c3['high'] > c2['high'] > c1['high'])
    )
    strong_green_bodies = is_strong_body(c1) and is_strong_body(c2) and is_strong_body(c3)
    bullish_impulse = (c3['close'] - c1['open']) >= cfg['min_move']
    is_bull_pullback = (c4['close'] <= c4['open']) or (c4['high'] < c3['high'])
    swing_high = max(c1['high'], c2['high'], c3['high'])

    if is_3_green and strong_green_bodies and bullish_impulse and is_bull_pullback:
        if curr_price > swing_high:
            sl = round(c4['low'] - cfg['sl_buf'], 2)
            risk = round(curr_price - sl, 2)
            if risk > 0:
                tp = round(curr_price + (risk * cfg['rr']), 2)
                return {
                    "side": "LONG",
                    "entry": curr_price,
                    "sl": sl,
                    "tp": tp,
                    "risk": risk,
                    "reward": round(risk * cfg['rr'], 2)
                }

    # --- 2. BEARISH SETUP (PUT / SHORT) ---
    is_3_red = (
        (c1['close'] < c1['open']) and 
        (c2['close'] < c2['open']) and 
        (c3['close'] < c3['open']) and
        (c3['low'] < c2['low'] < c1['low'])
    )
    strong_red_bodies = is_strong_body(c1) and is_strong_body(c2) and is_strong_body(c3)
    bearish_impulse = (c1['open'] - c3['close']) >= cfg['min_move']
    is_bear_pullback = (c4['close'] >= c4['open']) or (c4['low'] > c3['low'])
    swing_low = min(c1['low'], c2['low'], c3['low'])

    if is_3_red and strong_red_bodies and bearish_impulse and is_bear_pullback:
        if curr_price < swing_low:
            sl = round(c4['high'] + cfg['sl_buf'], 2)
            risk = round(sl - curr_price, 2)
            if risk > 0:
                tp = round(curr_price - (risk * cfg['rr']), 2)
                return {
                    "side": "SHORT",
                    "entry": curr_price,
                    "sl": sl,
                    "tp": tp,
                    "risk": risk,
                    "reward": round(risk * cfg['rr'], 2)
                }

    return None

def crypto_scanner_worker():
    print("🚀 Filtered 3-Candle Momentum Scanner Live...", flush=True)
    send_telegram_alert(
        "⚡ *MOMENTUM PULLBACK BOT RE-LOADED!*\n\n"
        "• *Upgrades:* Added 50% Body Filter + Min Impulse Move\n"
        "• *Risk:Reward:* 1:1.8\n"
        "• *Pairs:* BTC, ETH, SOL, GOLD (PAXG)\n"
        "• *Target:* Eliminates Chop & Small Doji Traps."
    )

    while True:
        try:
            for pair, cfg in PAIRS.items():
                now = time.time()
                if (now - last_signal_time[pair]) < (COOLDOWN_MINUTES * 60):
                    continue

                df = fetch_candles(pair, timeframe='5m', limit=25)
                time.sleep(0.4)

                setup = check_filtered_momentum_setup(df, cfg)
                if setup:
                    signal_counters[pair] += 1
                    last_signal_time[pair] = now

                    icon = "🟢" if setup['side'] == "LONG" else "🔴"
                    msg = (
                        f"{icon} *SOLID MOMENTUM {setup['side']} #{signal_counters[pair]}* {icon}\n\n"
                        f"🔹 *Asset:* {cfg['name']}\n"
                        f"🔹 *Entry Price:* ${setup['entry']:,.2f}\n"
                        f"🎯 *Target (TP 1:{cfg['rr']}):* ${setup['tp']:,.2f} (+${setup['reward']})\n"
                        f"🛑 *Stop Loss (SL):* ${setup['sl']:,.2f} (-${setup['risk']})\n"
                        f"⚖️ *Risk:Reward:* 1:{cfg['rr']}\n\n"
                        f"⚡ *Rule:* 1:1 reward aate hi SL Cost-to-Cost karein."
                    )
                    send_telegram_alert(msg)

            time.sleep(20)

        except Exception as e:
            print(f"Scanner Loop Error: {e}", flush=True)
            time.sleep(10)

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Filtered Momentum Bot Running!")

    def log_message(self, format, *args):
        return

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

if __name__ == "__main__":
    t = threading.Thread(target=crypto_scanner_worker, daemon=True)
    t.start()
    run_server()
