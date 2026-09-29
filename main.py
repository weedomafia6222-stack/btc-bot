import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import ccxt
import pandas as pd
import requests
from datetime import datetime
import pytz

# ----------------- CONFIGURATION -----------------
TELEGRAM_BOT_TOKEN = "8608122374:AAF5OXFFo4pKrhda8RyThOCs9dN0zkd0V14"
TELEGRAM_CHAT_ID   = "1327677831"

SYMBOL = "BTC/USDT"
RR_RATIO = 1.2          # 1:1.2 High Win-Rate Scalp Target
EMA_PERIOD = 200        # Major Trend Filter
SWING_LOOKBACK = 15     # Recent Liquidity Swings (~1 Hour)
COOLDOWN_MINUTES = 20   # Avoid double entries

exchange = ccxt.binance({'enableRateLimit': True})

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
        print(f"Telegram Error: {e}")

def get_5m_candles(limit=250):
    ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe='5m', limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms').dt.tz_localize('UTC').dt.tz_convert('Asia/Kolkata')
    df['ema200'] = df['close'].ewm(span=EMA_PERIOD, adjust=False).mean()
    return df

def trading_bot_loop():
    print("🚀 BTC 75%+ Win-Rate Pro Scalper Active...")
    send_telegram_alert("🎯 *Gujju 75%+ Scalper Mode Activated!* \nTracking Trend Sweeps with 200 EMA Filter on BTC/USDT (5m).")
    
    last_trade_time = None
    trades_count = 0
    current_day = None

    while True:
        try:
            df = get_5m_candles(limit=250)
            now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))

            if current_day != now_ist.day:
                current_day = now_ist.day
                trades_count = 0

            recent_high = df['high'].iloc[-(SWING_LOOKBACK + 2):-2].max()
            recent_low  = df['low'].iloc[-(SWING_LOOKBACK + 2):-2].min()

            last_closed = df.iloc[-2]
            curr_price  = df.iloc[-1]['close']
            ema_val     = last_closed['ema200']

            cooldown_passed = True
            if last_trade_time:
                diff = (now_ist - last_trade_time).total_seconds() / 60
                if diff < COOLDOWN_MINUTES:
                    cooldown_passed = False

            if cooldown_passed:
                # SHORT Setup
                if (last_closed['close'] < ema_val and 
                    last_closed['high'] > recent_high and 
                    last_closed['close'] < recent_high and 
                    last_closed['close'] < last_closed['open']):
                    
                    entry = curr_price
                    sl = last_closed['high'] + 40.0
                    risk = sl - entry
                    tp = entry - (risk * RR_RATIO)
                    
                    trades_count += 1
                    last_trade_time = now_ist

                    alert_msg = (
                        f"🎯 *BTC HIGH-ACCURACY SHORT #{trades_count}* 🎯\n\n"
                        f"🔹 *Pair:* {SYMBOL}\n"
                        f"🔹 *Entry Price:* ${entry:,.2f}\n"
                        f"🛑 *Stop Loss:* ${sl:,.2f}\n"
                        f"🎯 *Target (1:{RR_RATIO}):* ${tp:,.2f}\n"
                        f"📉 *Trend:* Bearish (< 200 EMA @ ${ema_val:,.1f})\n"
                        f"📊 *High Swept:* ${recent_high:,.2f}\n\n"
                        f"⚡ *Quick Scalp:* Book 70% at 1:1, trail rest."
                    )
                    send_telegram_alert(alert_msg)

                # LONG Setup
                elif (last_closed['close'] > ema_val and 
                      last_closed['low'] < recent_low and 
                      last_closed['close'] > recent_low and 
                      last_closed['close'] > last_closed['open']):
                    
                    entry = curr_price
                    sl = last_closed['low'] - 40.0
                    risk = entry - sl
                    tp = entry + (risk * RR_RATIO)
                    
                    trades_count += 1
                    last_trade_time = now_ist

                    alert_msg = (
                        f"🎯 *BTC HIGH-ACCURACY LONG #{trades_count}* 🎯\n\n"
                        f"🔹 *Pair:* {SYMBOL}\n"
                        f"🔹 *Entry Price:* ${entry:,.2f}\n"
                        f"🛑 *Stop Loss:* ${sl:,.2f}\n"
                        f"🎯 *Target (1:{RR_RATIO}):* ${tp:,.2f}\n"
                        f"📈 *Trend:* Bullish (> 200 EMA @ ${ema_val:,.1f})\n"
                        f"📊 *Low Swept:* ${recent_low:,.2f}\n\n"
                        f"⚡ *Quick Scalp:* Book 70% at 1:1, trail rest."
                    )
                    send_telegram_alert(alert_msg)

            time.sleep(15)

        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(10)

# Dummy Server to keep Render Free Web Service happy
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is healthy and running!")

    def log_message(self, format, *args):
        return  # Mute server logs

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

if __name__ == "__main__":
    # Start bot loop in background
    bot_thread = threading.Thread(target=trading_bot_loop, daemon=True)
    bot_thread.start()
    
    # Start port server for Render
    run_dummy_server()
