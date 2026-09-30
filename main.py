import os
import time
import threading
from datetime import datetime
import pytz
import ccxt
import pandas as pd
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

# ----------------- CONFIGURATION -----------------
TELEGRAM_BOT_TOKEN = "8608122374:AAF5OXFFo4pKrhda8RyThOCs9dN0zkd0V14"    # Token dalein
TELEGRAM_CHAT_ID   = "1327677831"      # Chat ID dalein

TIMEFRAME = "5m"
EMA_PERIOD = 20
COOLDOWN_MINUTES = 10
# Multi-Asset Configuration
ASSETS = {
    "BTC/USDT":  {"name": "BITCOIN",    "zone": 35.0, "sl_buf": 30.0, "rr": 1.3},
    "ETH/USDT":  {"name": "ETHEREUM",   "zone": 4.0,  "sl_buf": 3.0,  "rr": 1.4},
    "SOL/USDT":  {"name": "SOLANA",     "zone": 0.4,  "sl_buf": 0.35, "rr": 1.4},
    "PAXG/USD":  {"name": "GOLD (SPOT)", "zone": 3.0,  "sl_buf": 2.5,  "rr": 1.4}
}
exchange = ccxt.coinbase({'enableRateLimit': True})
last_trade_times = {symbol: None for symbol in ASSETS}
trade_counts = {symbol: 0 for symbol in ASSETS}

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
        print(f"Telegram Error: {e}", flush=True)

def fetch_ohlcv_data(symbol):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=60)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['ema'] = df['close'].ewm(span=EMA_PERIOD, adjust=False).mean()
        return df
    except Exception as e:
        print(f"Fetch Error ({symbol}): {e}", flush=True)
        return None

def crypto_gold_scanner_worker():
    print("🚀 Crypto & Gold Multi-Scanner Engine Live...", flush=True)
    send_telegram_alert("🌍 *CRYPTO & GOLD MULTI-ASSET BOT ACTIVE!*\nTracking 5m 20-EMA Pullbacks on:\n• BTC/USDT\n• ETH/USDT\n• SOL/USDT\n• GOLD (PAXG/USDT)")

    while True:
        try:
            ist = pytz.timezone('Asia/Kolkata')
            now_ist = datetime.now(ist)

            for symbol, cfg in ASSETS.items():
                df = fetch_ohlcv_data(symbol)
                if df is not None and len(df) > EMA_PERIOD:
                    curr_price = df.iloc[-1]['close']
                    last = df.iloc[-2]
                    ema_val = last['ema']
                    is_green = last['close'] > last['open']
                    is_red   = last['close'] < last['open']

                    # Check Cooldown per symbol
                    cooldown_passed = True
                    if last_trade_times[symbol]:
                        passed = (now_ist - last_trade_times[symbol]).total_seconds() / 60
                        if passed < COOLDOWN_MINUTES:
                            cooldown_passed = False

                    if cooldown_passed:
                        # 1. LONG Setup (Bounce at EMA)
                        if last['close'] > ema_val and last['low'] <= (ema_val + cfg['zone']) and is_green:
                            sl_pts = round(curr_price - last['low'] + cfg['sl_buf'], 2)
                            tp_pts = round(sl_pts * cfg['rr'], 2)
                            target_price = round(curr_price + tp_pts, 2)
                            stop_price = round(curr_price - sl_pts, 2)

                            trade_counts[symbol] += 1
                            last_trade_times[symbol] = now_ist

                            msg = (
                                f"🟢 *{cfg['name']} LONG SCALP #{trade_counts[symbol]}* 🟢\n\n"
                                f"🔹 *Pair:* `{symbol}`\n"
                                f"🔹 *Entry Price:* ${curr_price:,.2f}\n"
                                f"🎯 *Take Profit:* ${target_price:,.2f} (+${tp_pts})\n"
                                f"🛑 *Stop Loss:* ${stop_price:,.2f} (-${sl_pts})\n"
                                f"📈 *Pattern:* 20-EMA Dynamic Support Rebound\n\n"
                                f"⚡ *Rule:* Book 50% at 1:1 and trail SL to entry."
                            )
                            send_telegram_alert(msg)

                        # 2. SHORT Setup (Rejection at EMA)
                        elif last['close'] < ema_val and last['high'] >= (ema_val - cfg['zone']) and is_red:
                            sl_pts = round(last['high'] - curr_price + cfg['sl_buf'], 2)
                            tp_pts = round(sl_pts * cfg['rr'], 2)
                            target_price = round(curr_price - tp_pts, 2)
                            stop_price = round(curr_price + sl_pts, 2)

                            trade_counts[symbol] += 1
                            last_trade_times[symbol] = now_ist

                            msg = (
                                f"🔴 *{cfg['name']} SHORT SCALP #{trade_counts[symbol]}* 🔴\n\n"
                                f"🔹 *Pair:* `{symbol}`\n"
                                f"🔹 *Entry Price:* ${curr_price:,.2f}\n"
                                f"🎯 *Take Profit:* ${target_price:,.2f} (-${tp_pts})\n"
                                f"🛑 *Stop Loss:* ${stop_price:,.2f} (+${sl_pts})\n"
                                f"📉 *Pattern:* 20-EMA Dynamic Resistance Rejection\n\n"
                                f"⚡ *Rule:* Book 50% at 1:1 and trail SL to entry."
                            )
                            send_telegram_alert(msg)

                time.sleep(2)  # Short pause between pairs

            print(f"[{now_ist.strftime('%H:%M:%S')}] Scanned BTC, ETH, SOL, GOLD | All Active", flush=True)
            time.sleep(15)

        except Exception as e:
            print(f"Crypto Scanner Loop Exception: {e}", flush=True)
            time.sleep(10)

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Crypto & Gold Scanner Live!")

    def log_message(self, format, *args):
        return

def run_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

if __name__ == "__main__":
    t = threading.Thread(target=crypto_gold_scanner_worker, daemon=True)
    t.start()
    run_server()
