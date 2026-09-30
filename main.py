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
TELEGRAM_BOT_TOKEN = "8608122374:AAF5OXFFo4pKrhda8RyThOCs9dN0zkd0V14"
TELEGRAM_CHAT_ID   = "1327677831"

SYMBOL = "BTC/USDT"
TIMEFRAME = "5m"
EMA_PERIOD = 20           # Dynamic intraday pullback EMA
RR_RATIO = 1.3
COOLDOWN_MINUTES = 10

exchange = ccxt.coinbase({'enableRateLimit': True})

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

def fetch_btc_data():
    try:
        ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=100)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms').dt.tz_localize('UTC').dt.tz_convert('Asia/Kolkata')
        df['ema'] = df['close'].ewm(span=EMA_PERIOD, adjust=False).mean()
        return df
    except Exception as e:
        print(f"Fetch Error: {e}", flush=True)
        return None

def btc_scalper_worker():
    print("🚀 BTC Pullback Scalper Engine Started...", flush=True)
    send_telegram_alert("🚀 *BTC/USDT PULLBACK BOT ENGAGED!*\nTracking 5m 20-EMA dynamic pullbacks.")

    last_trade_time = None
    trades_count = 0

    while True:
        try:
            ist = pytz.timezone('Asia/Kolkata')
            now_ist = datetime.now(ist)

            df = fetch_btc_data()

            if df is not None and len(df) > EMA_PERIOD:
                curr_price = df.iloc[-1]['close']
                last = df.iloc[-2]
                ema_val = last['ema']
                is_green = last['close'] > last['open']
                is_red   = last['close'] < last['open']

                print(f"[{now_ist.strftime('%H:%M:%S')}] BTC: ${curr_price:,.2f} | 20-EMA: ${ema_val:,.2f} | Scanning...", flush=True)

                cooldown_passed = True
                if last_trade_time:
                    passed = (now_ist - last_trade_time).total_seconds() / 60
                    if passed < COOLDOWN_MINUTES:
                        cooldown_passed = False

                if cooldown_passed:
                    # 1. Bullish Pullback (Dip to 20-EMA + Green Bounce)
                    if last['close'] > ema_val and last['low'] <= (ema_val + 35) and is_green:
                        sl_pts = round(curr_price - last['low'] + 30, 2)
                        tp_pts = round(sl_pts * RR_RATIO, 2)
                        target_price = round(curr_price + tp_pts, 2)
                        stop_price = round(curr_price - sl_pts, 2)

                        trades_count += 1
                        last_trade_time = now_ist

                        msg = (
                            f"🟢 *BTC/USDT LONG PULLBACK #{trades_count}* 🟢\n\n"
                            f"🔹 *Entry Price:* ${curr_price:,.2f}\n"
                            f"🎯 *Take Profit:* ${target_price:,.2f} (+${tp_pts})\n"
                            f"🛑 *Stop Loss:* ${stop_price:,.2f} (-${sl_pts})\n"
                            f"📈 *Setup:* 20-EMA Dynamic Support Bounce\n\n"
                            f"⚡ *Tip:* Book 50% profit at 1:1 and trail SL to entry."
                        )
                        send_telegram_alert(msg)

                    # 2. Bearish Pullback (Rally to 20-EMA + Red Rejection)
                    elif last['close'] < ema_val and last['high'] >= (ema_val - 35) and is_red:
                        sl_pts = round(last['high'] - curr_price + 30, 2)
                        tp_pts = round(sl_pts * RR_RATIO, 2)
                        target_price = round(curr_price - tp_pts, 2)
                        stop_price = round(curr_price + sl_pts, 2)

                        trades_count += 1
                        last_trade_time = now_ist

                        msg = (
                            f"🔴 *BTC/USDT SHORT PULLBACK #{trades_count}* 🔴\n\n"
                            f"🔹 *Entry Price:* ${curr_price:,.2f}\n"
                            f"🎯 *Take Profit:* ${target_price:,.2f} (-${tp_pts})\n"
                            f"🛑 *Stop Loss:* ${stop_price:,.2f} (+${sl_pts})\n"
                            f"📉 *Setup:* 20-EMA Dynamic Resistance Rejection\n\n"
                            f"⚡ *Tip:* Book 50% profit at 1:1 and trail SL to entry."
                        )
                        send_telegram_alert(msg)

            time.sleep(20)

        except Exception as e:
            print(f"BTC Loop Notice: {e}", flush=True)
            time.sleep(15)

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"BTC Pullback Bot Healthy!")

    def log_message(self, format, *args):
        return

def run_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

if __name__ == "__main__":
    t = threading.Thread(target=btc_scalper_worker, daemon=True)
    t.start()
    run_server()
