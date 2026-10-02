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
    "BTC-USD":  {"name": "BTC/USDT",  "atr_mult": 1.3, "zone_buf": 0.0010},
    "ETH-USD":  {"name": "ETH/USDT",  "atr_mult": 1.4, "zone_buf": 0.0012},
    "SOL-USD":  {"name": "SOL/USDT",  "atr_mult": 1.5, "zone_buf": 0.0015},
    "XRP-USD":  {"name": "XRP/USDT",  "atr_mult": 1.5, "zone_buf": 0.0015},
    "DOGE-USD": {"name": "DOGE/USDT", "atr_mult": 1.6, "zone_buf": 0.0020},
    "ADA-USD":  {"name": "ADA/USDT",  "atr_mult": 1.5, "zone_buf": 0.0018},
    "AVAX-USD": {"name": "AVAX/USDT", "atr_mult": 1.5, "zone_buf": 0.0015},
    "LINK-USD": {"name": "LINK/USDT", "atr_mult": 1.4, "zone_buf": 0.0015},
    "NEAR-USD": {"name": "NEAR/USDT", "atr_mult": 1.5, "zone_buf": 0.0018},
    "SUI-USD":  {"name": "SUI/USDT",  "atr_mult": 1.5, "zone_buf": 0.0020},
    "APT-USD":  {"name": "APT/USDT",  "atr_mult": 1.5, "zone_buf": 0.0018},
    "DOT-USD":  {"name": "DOT/USDT",  "atr_mult": 1.4, "zone_buf": 0.0015}
}

COOLDOWN_MINUTES = 25
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

def fetch_candles_coinbase(product_id, granularity=300, limit=50):
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
    
    high = df['high']
    low = df['low']
    close_prev = df['close'].shift(1)
    tr = pd.concat([high - low, (high - close_prev).abs(), (low - close_prev).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()
    return df

def check_unified_setup(df_5m, df_30m, cfg):
    if df_5m is None or len(df_5m) < 35 or df_30m is None or len(df_30m) < 25:
        return None

    # 1. 30M Macro Trend Confirmation
    df_30m['ema21'] = df_30m['close'].ewm(span=21, adjust=False).mean()
    macro_bull = df_30m['close'].iloc[-2] > df_30m['ema21'].iloc[-2]
    macro_bear = df_30m['close'].iloc[-2] < df_30m['ema21'].iloc[-2]

    # 2. 5M Structure & Triggers
    df_5m = compute_indicators(df_5m)
    p2 = df_5m.iloc[-3]
    p1 = df_5m.iloc[-2]
    curr = df_5m.iloc[-1]

    curr_price = float(curr['close'])
    atr = float(p1['atr'])

    if pd.isna(atr) or atr <= 0:
        return None

    # Anti-Chop Check
    if abs(p1['ema9'] - p1['ema21']) < (atr * 0.10):
        return None

    # --- 1. LONG SETUP ---
    is_5m_bull = p1['ema9'] > p1['ema21']
    touched_ema_bull = (p2['low'] <= p2['ema9']) or (p1['low'] <= p1['ema9'])
    bull_reject_wick = (p1['close'] > p1['open']) and (p1['close'] > p1['ema9'])

    if macro_bull and is_5m_bull and touched_ema_bull and bull_reject_wick:
        if curr_price > p1['high']:
            sl = round(min(p1['low'], p2['low']) - (atr * cfg['atr_mult']), 4)
            risk = round(curr_price - sl, 4)
            if risk > 0:
                zone_top = round(curr_price * (1 + cfg['zone_buf']), 4)
                tp1 = round(curr_price + risk, 4)
                tp2 = round(curr_price + (risk * 1.5), 4)
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

    # --- 2. SHORT SETUP ---
    is_5m_bear = p1['ema9'] < p1['ema21']
    touched_ema_bear = (p2['high'] >= p2['ema9']) or (p1['high'] >= p1['ema9'])
    bear_reject_wick = (p1['close'] < p1['open']) and (p1['close'] < p1['ema9'])

    if macro_bear and is_5m_bear and touched_ema_bear and bear_reject_wick:
        if curr_price < p1['low']:
            sl = round(max(p1['high'], p2['high']) + (atr * cfg['atr_mult']), 4)
            risk = round(sl - curr_price, 4)
            if risk > 0:
                zone_bottom = round(curr_price * (1 - cfg['zone_buf']), 4)
                tp1 = round(curr_price - risk, 4)
                tp2 = round(curr_price - (risk * 1.5), 4)
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
    print("🚀 Unified Crypto Master Engine Online...", flush=True)
    send_telegram_alert(
        "💎 *VIP CRYPTO SIGNALS ACTIVE!*\n\n"
        "• *Coverage:* Top 12 High-Volume Coins\n"
        "• *System:* 30M Trend Bias + 5M Pullback\n"
        "• *Execution:* Zone-Based Client Entries\n"
        "• *Risk Control:* Max 1:1 Fast Scalp Focus"
    )

    while True:
        try:
            for pair, cfg in PAIRS.items():
                now = time.time()
                if (now - last_signal_time[pair]) < (COOLDOWN_MINUTES * 60):
                    continue

                df_5m = fetch_candles_coinbase(pair, granularity=300, limit=45)
                time.sleep(0.25)
                df_30m = fetch_candles_coinbase(pair, granularity=1800, limit=35)
                time.sleep(0.25)

                setup = check_unified_setup(df_5m, df_30m, cfg)
                if setup:
                    last_signal_time[pair] = now
                    msg = (
                        f"{setup['icon']} *{setup['side']}: {cfg['name']}* {setup['icon']}\n\n"
                        f"🔹 *Entry Zone:* `${setup['entry_low']:,.4f} – ${setup['entry_high']:,.4f}`\n"
                        f"🛑 *Stop Loss:* `${setup['sl']:,.4f}` (Risk: `${setup['risk']:,.4f}`)\n"
                        f"🎯 *Target 1 (1:1):* `${setup['tp1']:,.4f}` (Book 70%)\n"
                        f"🎯 *Target 2 (Runner):* `${setup['tp2']:,.4f}`\n\n"
                        f"⚠️ *Client Execution Rules:*\n"
                        f"1. Agar price Entry Zone se upar nikal chuki ho toh chase mat karein.\n"
                        f"2. Target 1 aate hi Stop Loss seedha Entry par shift karein.\n"
                        f"3. Suggested Leverage: 5x – 10x max."
                    )
                    send_telegram_alert(msg)

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
