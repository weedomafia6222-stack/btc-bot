import os
import time
import threading
from datetime import datetime
import pytz
import pandas as pd
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

# ----------------- CONFIGURATION (ALERT BOT) -----------------
TELEGRAM_BOT_TOKEN = "8608122374:AAF5OXFFo4pKrhda8RyThOCs9dN0zkd0V14"
TELEGRAM_CHAT_ID   = "1327677831"

COOLDOWN_MINUTES = 25

CRYPTO_PAIRS = {
    "BTCUSDT": {
        "name": "BITCOIN",
        "symbol": "BTC/USDT",
        "coinbase_id": "BTC-USDT",
        "sl_buf": 60.0,
        "min_risk": 50.0,
        "max_risk": 700.0,
        "rr": 2.0
    },
    "ETHUSDT": {
        "name": "ETHEREUM",
        "symbol": "ETH/USDT",
        "coinbase_id": "ETH-USDT",
        "sl_buf": 4.0,
        "min_risk": 2.0,
        "max_risk": 60.0,
        "rr": 2.0
    },
    "SOLUSDT": {
        "name": "SOLANA",
        "symbol": "SOL/USDT",
        "coinbase_id": "SOL-USDT",
        "sl_buf": 0.15,
        "min_risk": 0.30,   # Fixed: Pehle 1.20 tha jisse trades reject ho rahe the
        "max_risk": 8.0,
        "rr": 2.0
    }
}

last_trade_times = {symbol: None for symbol in CRYPTO_PAIRS}

def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        res = requests.post(url, data=payload, timeout=8)
        if res.status_code != 200:
            # Fallback plain text if HTML tags ever fail
            clean_text = message.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", "")
            payload["text"] = clean_text
            payload.pop("parse_mode", None)
            requests.post(url, data=payload, timeout=8)
    except Exception as e:
        print(f"Telegram Alert Error: {e}", flush=True)

def fetch_klines(symbol, coinbase_id, interval="5m", limit=50):
    granularity = 300 if interval == "5m" else 900
    
    # 1. Primary: Coinbase Public API (Zero US IP Blocks on Render)
    try:
        url = f"https://api.exchange.coinbase.com/products/{coinbase_id}/candles?granularity={granularity}"
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            raw = res.json()
            if isinstance(raw, list) and len(raw) >= 25:
                df = pd.DataFrame(raw, columns=["time", "low", "high", "open", "close", "volume"])
                df = df.sort_values("time").reset_index(drop=True)
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = df[col].astype(float)
                return df.tail(limit)
    except Exception:
        pass

    # 2. Secondary: Binance Futures Feed (Unrestricted Global Feed)
    try:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            raw = res.json()
            if isinstance(raw, list) and len(raw) >= 25:
                df = pd.DataFrame(raw, columns=[
                    "open_time", "open", "high", "low", "close", "volume",
                    "close_time", "qav", "trades", "tb_base", "tb_quote", "ignore"
                ])
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = df[col].astype(float)
                return df
    except Exception as e:
        print(f"Data Fetch Error ({symbol}): {e}", flush=True)

    return None

def check_crypto_setup(df_5m, df_15m, cfg):
    if df_5m is None or len(df_5m) < 25 or df_15m is None or len(df_15m) < 25:
        return None

    # HTF 15M Trend Alignment
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

    # --- LONG SETUP ---
    if htf_bullish and (prev['ema9'] > prev['ema21']):
        pullback = (prev['low'] <= (prev['ema9'] * 1.002)) and (prev['close'] >= prev['ema9'])
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

    # --- SHORT SETUP ---
    if htf_bearish and (prev['ema9'] < prev['ema21']):
        pullback = (prev['high'] >= (prev['ema9'] * 0.998)) and (prev['close'] <= prev['ema9'])
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
    print("🚀 24/7 Crypto Scanner Worker Started (USDT Native Engine)...", flush=True)
    send_telegram_alert(
        "🚀 <b>24/7 MULTI-COIN SCANNER ACTIVATED!</b>\n\n"
        "🔹 <b>Assets:</b> BTC, ETH, SOL (USDT Native)\n"
        "🔹 <b>Engine:</b> 15M Trend + 5M 9-EMA Pullback\n"
        "🔹 <b>Status:</b> Zero Latency Pipeline Connected"
    )

    while True:
        try:
            ist = pytz.timezone('Asia/Kolkata')
            now_ist = datetime.now(ist)

            for symbol, cfg in CRYPTO_PAIRS.items():
                if last_trade_times[symbol]:
                    passed_min = (now_ist - last_trade_times[symbol]).total_seconds() / 60
                    if passed_min < COOLDOWN_MINUTES:
                        continue

                df_5m = fetch_klines(symbol, cfg['coinbase_id'], interval="5m", limit=40)
                df_15m = fetch_klines(symbol, cfg['coinbase_id'], interval="15m", limit=40)

                setup = check_crypto_setup(df_5m, df_15m, cfg)
                if setup:
                    last_trade_times[symbol] = now_ist

                    msg = (
                        f"⚡ <b>24/7 CRYPTO SIGNAL: {cfg['name']} ({setup['side']})</b> ⚡\n\n"
                        f"📌 <b>Pair:</b> <code>{cfg['symbol']}</code>\n"
                        f"🔹 <b>Entry Price:</b> ${setup['entry']:,.2f}\n"
                        f"🎯 <b>Target (1:{cfg['rr']}):</b> ${setup['tp']:,.2f} (+${setup['reward']:,.2f})\n"
                        f"🛑 <b>Stop Loss:</b> ${setup['sl']:,.2f} (-${setup['risk']:,.2f})\n\n"
                        f"📊 <b>Strategy:</b> 15M Trend Alignment + 5M 9 EMA Pullback\n"
                        f"⚡ <b>Rule:</b> Target 1 (1:1) hit hote hi SL Cost-to-Cost karein."
                    )
                    send_telegram_alert(msg)
                    print(f"[{now_ist.strftime('%H:%M:%S')}] Signal sent: {symbol}", flush=True)

            time.sleep(12)

        except Exception as e:
            print(f"Scanner Loop Error: {e}", flush=True)
            time.sleep(8)

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
