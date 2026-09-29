import time
import ccxt
import pandas as pd
import requests
from datetime import datetime
import pytz

# ----------------- CONFIGURATION -----------------
# BotFather se mila token yahan dalein
TELEGRAM_BOT_TOKEN = "8608122374:AAF5OXFFo4pKrhda8RyThOCs9dN0zkd0V14"

# Userinfobot se mili Numeric ID yahan dalein
TELEGRAM_CHAT_ID = "1327677831"

SYMBOL = "BTC/USDT"
RR_RATIO = 2.0  # 1:2 Risk to Reward Target

# Binance public data client (Free, zero API key needed)
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
        print(f"Telegram Send Error: {e}")

def get_5m_candles(limit=120):
    ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe='5m', limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms').dt.tz_localize('UTC').dt.tz_convert('Asia/Kolkata')
    return df

def run_bot():
    print("Gujju BTC SMC Bot Active! Tracking Asian Session...")
    send_telegram_alert("🟢 *Gujju BTC Bot Activated!* \nTracking Asian Session Sweep on BTC/USDT (5m).")
    
    current_day = None
    trade_taken_today = False

    while True:
        try:
            df = get_5m_candles(limit=120)
            now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))

            # Daily reset at midnight IST
            if current_day != now_ist.day:
                current_day = now_ist.day
                trade_taken_today = False

            # Asian Session (05:30 AM to 11:30 AM IST)
            today_asian = df[(df['datetime'].dt.day == now_ist.day) & 
                             (df['datetime'].dt.hour >= 5) & 
                             ((df['datetime'].dt.hour < 11) | ((df['datetime'].dt.hour == 11) & (df['datetime'].dt.minute <= 30)))]

            if today_asian.empty:
                time.sleep(20)
                continue

            asian_high = today_asian['high'].max()
            asian_low = today_asian['low'].min()

            last_closed = df.iloc[-2] # Last completed 5m candle
            curr_price = df.iloc[-1]['close']

            print(f"[{now_ist.strftime('%H:%M:%S')}] BTC: ${curr_price:.2f} | Asian High: ${asian_high:.2f} | Asian Low: ${asian_low:.2f}", end='\r')

            # Check after Asian Session ends (Post 11:30 AM IST)
            is_post_asian = (now_ist.hour > 11) or (now_ist.hour == 11 and now_ist.minute > 30)

            if is_post_asian and not trade_taken_today:
                # 1. Bearish Liquidity Sweep of Asian High
                if last_closed['high'] > asian_high and last_closed['close'] < asian_high and last_closed['close'] < last_closed['open']:
                    entry = curr_price
                    sl = last_closed['high'] + 60.0
                    risk = sl - entry
                    tp = entry - (risk * RR_RATIO)

                    alert_msg = (
                        "🚨 *GUJJU SETUP: BEARISH SWEEP (SHORT BTC)* 🚨\n\n"
                        f"🔹 *Asset:* {SYMBOL}\n"
                        f"🔹 *Entry Price:* ${entry:,.2f}\n"
                        f"🛑 *Stop Loss:* ${sl:,.2f}\n"
                        f"🎯 *Target (1:{RR_RATIO}):* ${tp:,.2f}\n"
                        f"📊 *Asian High Swept:* ${asian_high:,.2f}\n\n"
                        "💡 *Tip:* Move SL to Cost once 1:1 is hit."
                    )
                    send_telegram_alert(alert_msg)
                    trade_taken_today = True

                # 2. Bullish Liquidity Sweep of Asian Low
                elif last_closed['low'] < asian_low and last_closed['close'] > asian_low and last_closed['close'] > last_closed['open']:
                    entry = curr_price
                    sl = last_closed['low'] - 60.0
                    risk = entry - sl
                    tp = entry + (risk * RR_RATIO)

                    alert_msg = (
                        "🚀 *GUJJU SETUP: BULLISH SWEEP (LONG BTC)* 🚀\n\n"
                        f"🔹 *Asset:* {SYMBOL}\n"
                        f"🔹 *Entry Price:* ${entry:,.2f}\n"
                        f"🛑 *Stop Loss:* ${sl:,.2f}\n"
                        f"🎯 *Target (1:{RR_RATIO}):* ${tp:,.2f}\n"
                        f"📊 *Asian Low Swept:* ${asian_low:,.2f}\n\n"
                        "💡 *Tip:* Move SL to Cost once 1:1 is hit."
                    )
                    send_telegram_alert(alert_msg)
                    trade_taken_today = True

            time.sleep(15)

        except Exception as e:
            print(f"\nLoop Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run_bot()
