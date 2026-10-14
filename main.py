import os
import time
import threading
from datetime import datetime
import pytz
import pandas as pd
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

# ----------------- CONFIGURATION -----------------
TELEGRAM_BOT_TOKEN = "8608122374:AAF5OXFFo4pKrhda8RyThOCs9dN0zkd0V14"
TELEGRAM_CHAT_ID   = "1327677831"

COOLDOWN_MINUTES = 25  # Ek coin par trade aane ke baad cooldown

CRYPTO_PAIRS = {
    "BTCUSDT": {
        "name": "BITCOIN",
        "symbol": "BTC/USDT",
        "sl_buf": 60.0,       # $60 buffer below/above wick
        "min_risk": 150.0,    # Minimum $150 stop loss
        "max_risk": 550.0,    # Maximum $550 stop loss (wide stop avoid)
        "rr": 2.0
    },
    "ETHUSDT": {
        "name": "ETHEREUM",
        "symbol": "ETH/USDT",
        "sl_buf": 4.0,
        "min_risk": 10.0,
        "max_risk": 45.0,
        "rr": 2.0
    },
    "SOLUSDT": {
        "name": "SOLANA",
        "symbol": "SOL/USDT",
        "sl_buf": 0.40,
        "min_risk": 1.20,
        "max_risk": 5.0,
        "rr": 2.0
    }
}

last_trade_times = {symbol: None for symbol in CRYPTO_PAIRS}

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
        print(f"Telegram Error: {e}", flush=True)

def fetch_klines(symbol, interval="5m", limit=60):
    """Real-time live candle fetch from Binance Public API (Zero Delay)"""
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        res = requests.get(url, timeout=8)
        if res.status_code == 200:
            raw = res.json()
            df = pd.DataFrame(raw, columns=[
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "qav", "num_trades", "taker_base_vol", "taker_quote_vol", "ignore"
            ])
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)
            return df
    except Exception as e:
        print(f"Fetch Error ({symbol} {interval}): {e}", flush=True)
    return None

def check_crypto_setup(df_5m, df_15m, cfg):
    if df_5m is None or len(df_5m) < 30 or df_15m is None or len(df_15m) < 30:
        return None

    # HTF 15M Trend Confirmation
    df_15m['ema9'] = df_15m['close'].ewm(span=9, adjust=False).mean()
    df_15m['ema21'] = df_15m['close'].ewm(span=21, adjust=False).mean()
    htf_candle = df_15m.iloc[-2]

    htf_bullish = htf_candle['ema9'] > htf_candle['ema21']
    htf_bearish = htf_candle['ema9'] < htf_candle['ema21']

    # LTF 5M Pullback Confirmation
    df_5m['ema9'] = df_5m['close'].ewm(span=9, adjust=False).mean()
    df_5m['ema21'] = df_5m['close'].ewm(span=21, adjust=False).mean()

    prev = df_5m.iloc[-2]  # Confirmed closed candle
    curr = df_5m.iloc[-1]  # Active breakout candle

    # --- 1. LONG / BUY SETUP ---
    if htf_bullish and (prev['ema9'] > prev['ema21']):
        # Pullback test near EMA 9
        pullback = (prev['low'] <= (prev['ema9'] * 1.002)) and (prev['close'] >= prev['ema9'])
        # Breakout trigger
        if pullback and (curr['close'] > prev['high']):
            sl = round(prev['low'] - cfg['sl_buf'], 2)
            risk = round(curr['close'] - sl, 2)
            if cfg['min_risk'] <= risk <= cfg['max_risk']:
                tp = round(curr['close'] + (risk * cfg['rr']), 2)
                return {
                    "side": "LONG 🟢",
                    "entry": curr['close'],
                    "sl": sl,
                    "tp": tp,
                    "risk": risk,
                    "reward": round(risk * cfg['rr'], 2)
                }

    # --- 2. SHORT / SELL SETUP ---
    if htf_bearish and (prev['ema9'] < prev['ema21']):
        # Pullback test near EMA 9
        pullback = (prev['high'] >= (prev['ema9'] * 0.998)) and (prev['close'] <= prev['ema9'])
        # Breakdown trigger
        if pullback and (curr['close'] < prev['low']):
            sl = round(prev['high'] + cfg['sl_buf'], 2)
            risk = round(sl - curr['close'], 2)
            if cfg['min_risk'] <= risk <= cfg['max_risk']:
                tp = round(curr['close'] - (risk * cfg['rr']), 2)
                return {
                    "side": "SHORT 🔴",
                    "entry": curr['close'],
                    "sl": sl,
                    "tp": tp,
                    "risk": risk,
                    "reward": round(risk * cfg['rr'], 2)
                }

    return None

def crypto_scanner_worker():
    print("🚀 24/7 Crypto Multi-Coin Scanner Live (BTC, ETH, SOL)...", flush=True)

    while True:
        try:
            ist = pytz.timezone('Asia/Kolkata')
            now_ist = datetime.now(ist)

            for symbol, cfg in CRYPTO_PAIRS.items():
                # Cooldown check per coin
                if last_trade_times[symbol]:
                    passed_min = (now_ist - last_trade_times[symbol]).total_seconds() / 60
                    if passed_min < COOLDOWN_MINUTES:
                        continue

                df_5m = fetch_klines(symbol, interval="5m", limit=40)
                time.sleep(0.3)
                df_15m = fetch_klines(symbol, interval="15m", limit=40)
                time.sleep(0.3)

                setup = check_crypto_setup(df_5m, df_15m, cfg)
                if setup:
                    last_trade_times[symbol] = now_ist

                    msg = (
                        f"⚡ *24/7 CRYPTO SIGNAL: {cfg['name']} ({setup['side']})* ⚡\n\n"
                        f"📌 *Pair:* `{cfg['symbol']}`\n"
                        f"🔹 *Entry Price:* ${setup['entry']:,.2f}\n"
                        f"🎯 *Target (1:{cfg['rr']}):* ${setup['tp']:,.2f} (+${setup['reward']:,.2f})\n"
                        f"🛑 *Stop Loss:* ${setup['sl']:,.2f} (-${setup['risk']:,.2f})\n\n"
                        f"📊 *Strategy:* 15M Trend Alignment + 5M 9 EMA Pullback\n"
                        f"⚡ *Trade Rule:* Target 1 (1:1 R:R) hit hote hi Stop Loss ko entry/cost par shift karein."
                    )
                    send_telegram_alert(msg)
                    print(f"[{now_ist.strftime('%H:%M:%S')}] Alert sent for {symbol}", flush=True)

            time.sleep(15)  # Har 15 second mein teeno coins scan honge

        except Exception as e:
            print(f"Scanner Loop Error: {e}", flush=True)
            time.sleep(10)

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"24/7 Multi-Coin Scanner Alive!")

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
