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

PAIRS = {
    "BTC-USD":  {"name": "BTC/USDT",  "atr_mult": 1.2, "zone_buf": 0.0010},
    "ETH-USD":  {"name": "ETH/USDT",  "atr_mult": 1.3, "zone_buf": 0.0012},
    "SOL-USD":  {"name": "SOL/USDT",  "atr_mult": 1.4, "zone_buf": 0.0015},
    "XRP-USD":  {"name": "XRP/USDT",  "atr_mult": 1.4, "zone_buf": 0.0015},
    "DOGE-USD": {"name": "DOGE/USDT", "atr_mult": 1.5, "zone_buf": 0.0020},
    "ADA-USD":  {"name": "ADA/USDT",  "atr_mult": 1.4, "zone_buf": 0.0018},
    "AVAX-USD": {"name": "AVAX/USDT", "atr_mult": 1.4, "zone_buf": 0.0015},
    "LINK-USD": {"name": "LINK/USDT", "atr_mult": 1.3, "zone_buf": 0.0015},
    "NEAR-USD": {"name": "NEAR/USDT", "atr_mult": 1.4, "zone_buf": 0.0018},
    "SUI-USD":  {"name": "SUI/USDT",  "atr_mult": 1.5, "zone_buf": 0.0020},
    "APT-USD":  {"name": "APT/USDT",  "atr_mult": 1.4, "zone_buf": 0.0018},
    "DOT-USD":  {"name": "DOT/USDT",  "atr_mult": 1.3, "zone_buf": 0.0015}
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

def fetch_candles_coinbase(product_id, granularity=300):
    url = f"https://api.exchange.coinbase.com/products/{product_id}/candles?granularity={granularity}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        res = requests.get(url, headers=headers, timeout=7)
        if res.status_code == 200:
            raw = res.json()
            if isinstance(raw, list) and len(raw) >= 20:
                df = pd.DataFrame(raw, columns=['timestamp', 'low', 'high', 'open', 'close', 'volume'])
                df = df.sort_values('timestamp').reset_index(drop=True)
                return df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
        else:
            print(f"API Warning [{product_id}|{granularity}]: Status {res.status_code}", flush=True)
    except Exception as e:
        print(f"Fetch Error ({product_id}): {e}", flush=True)
    return None

def compute_indicators(df):
    df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    
    high = df['high']
    low = df['low']
    close_prev = df['close'].shift(1)
    tr = pd.concat([high - low, (high - close_prev).abs(), (low - close_prev).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()
    return df

def check_unified_setup(df_5m, df_1h, cfg):
    if df_5m is None or len(df_5m) < 25 or df_1h is None or len(df_1h) < 20:
        return None

    # 1. 1-Hour Macro Trend Bias (Coinbase native 3600s bucket)
    df_1h['ema21'] = df_1h['close'].ewm(span=21, adjust=False).mean()
    macro_bull = df_1h['close'].iloc[-1] > df_1h['ema21'].iloc[-1]
    macro_bear = df_1h['close'].iloc[-1] < df_1h['ema21'].iloc[-1]

    # 2. 5M Structure & Pullback Execution
    df_5m = compute_indicators(df_5m)
    p1 = df_5m.iloc[-2]
    curr = df_5m.iloc[-1]

    curr_price = float(curr['close'])
    atr = float(p1['atr'])

    if pd.isna(atr) or atr <= 0:
        return None

    # ---------------- 1. BUY SETUP (LONG) ----------------
    if macro_bull and (curr['ema9'] >= curr['ema21']):
        pullback_tested = (curr['low'] <= curr['ema9'] * 1.0015) or (p1['low'] <= p1['ema9'] * 1.0015)
        candle_bullish = curr['close'] >= curr['open']

        if pullback_tested and candle_bullish:
            sl = round(min(p1['low'], curr['low']) - (atr * cfg['atr_mult']), 4)
            risk = round(curr_price - sl, 4)
            if risk > 0 and (risk / curr_price) <= 0.035:
                zone_top = round(curr_price * (1 + cfg['zone_buf']), 4)
                tp1 = round(curr_price + risk, 4)
                tp2 = round(curr_price + (risk * 1.6), 4)
                return {
                    "side": "BUY (LONG)",
                    "icon": "🟢",
                    "entry_low": curr_price,
                    "entry_high": zone_top,
                    "sl": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "risk": risk
                }

    # ---------------- 2. SELL SETUP (SHORT) ----------------
    if macro_bear and (curr['ema9'] <= curr['ema21']):
        pullback_tested = (curr['high'] >= curr['ema9'] * 0.9985) or (p1['high'] >= p1['ema9'] * 0.9985)
        candle_bearish = curr['close'] <= curr['open']

        if pullback_tested and candle_bearish:
            sl = round(max(p1['high'], curr['high']) + (atr * cfg['atr_mult']), 4)
            risk = round(sl - curr_price, 4)
            if risk > 0 and (risk / curr_price) <= 0.035:
                zone_bottom = round(curr_price * (1 - cfg['zone_buf']), 4)
                tp1 = round(curr_price - risk, 4)
                tp2 = round(curr_price - (risk * 1.6), 4)
                return {
                    "side": "SELL (SHORT)",
                    "icon": "🔴",
                    "entry_low": zone_bottom,
                    "entry_high": curr_price,
                    "sl": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "risk": risk
                }

    return None

def run_crypto_scanner():
    print("🚀 Unified Crypto Master Engine Online (5M + 1H Native)...", flush=True)
    scan_round = 0

    while True:
        try:
            scan_round += 1
            for pair, cfg in PAIRS.items():
                now = time.time()
                if (now - last_signal_time[pair]) < (COOLDOWN_MINUTES * 60):
                    continue

                # 300 = 5m, 3600 = 1h (Native Coinbase API granularity)
                df_5m = fetch_candles_coinbase(pair, granularity=300)
                time.sleep(0.2)
                df_1h = fetch_candles_coinbase(pair, granularity=3600)
                time.sleep(0.2)

                setup = check_unified_setup(df_5m, df_1h, cfg)
                if setup:
                    last_signal_time[pair] = now
                    msg = (
                        f"{setup['icon']} *{setup['side']}: {cfg['name']}* {setup['icon']}\n\n"
                        f"🔹 *Entry Zone:* `${setup['entry_low']:,.4f} – ${setup['entry_high']:,.4f}`\n"
                        f"🛑 *Stop Loss:* `${setup['sl']:,.4f}` (Risk: `${setup['risk']:,.4f}`)\n"
                        f"🎯 *Target 1 (1:1):* `${setup['tp1']:,.4f}` (Book 70%)\n"
                        f"🎯 *Target 2 (Runner):* `${setup['tp2']:,.4f}`\n\n"
                        f"⚠️ *Client Execution Rules:*\n"
                        f"1. Price Entry Zone se cross hone par trade chase na karein.\n"
                        f"2. Target 1 hit hote hi Stop Loss entry par le aayein.\n"
                        f"3. Suggested Leverage: 5x – 10x max."
                    )
                    send_telegram_alert(msg)

            if scan_round % 3 == 0:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Active Scan #{scan_round} OK. All 12 pairs processed without errors.", flush=True)

            time.sleep(15)

        except Exception as e:
            print(f"Scanner Exception: {e}", flush=True)
            time.sleep(10)

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Unified Crypto Master Live!")

    def log_message(self, format, *args):
        return

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

if __name__ == "__main__":
    t = threading.Thread(target=run_crypto_scanner, daemon=True)
    t.start()
    run_server()
