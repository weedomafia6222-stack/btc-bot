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
    "BTC-USD":  {"name": "BTC/USDT",  "pip_dec": 2, "max_sl_pct": 0.025},
    "ETH-USD":  {"name": "ETH/USDT",  "pip_dec": 2, "max_sl_pct": 0.028},
    "SOL-USD":  {"name": "SOL/USDT",  "pip_dec": 3, "max_sl_pct": 0.035},
    "XRP-USD":  {"name": "XRP/USDT",  "pip_dec": 4, "max_sl_pct": 0.035},
    "DOGE-USD": {"name": "DOGE/USDT", "pip_dec": 4, "max_sl_pct": 0.040},
    "NEAR-USD": {"name": "NEAR/USDT", "pip_dec": 4, "max_sl_pct": 0.038},
    "SUI-USD":  {"name": "SUI/USDT",  "pip_dec": 4, "max_sl_pct": 0.040},
    "LINK-USD": {"name": "LINK/USDT", "pip_dec": 3, "max_sl_pct": 0.035}
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

def fetch_candles(product_id, granularity=300):
    url = f"https://api.exchange.coinbase.com/products/{product_id}/candles?granularity={granularity}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=7)
        if res.status_code == 200:
            raw = res.json()
            if isinstance(raw, list) and len(raw) >= 30:
                df = pd.DataFrame(raw, columns=['timestamp', 'low', 'high', 'open', 'close', 'volume'])
                df = df.sort_values('timestamp').reset_index(drop=True)
                return df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
    except Exception as e:
        print(f"Fetch Error ({product_id}): {e}", flush=True)
    return None

def get_market_structure(df_15m):
    """
    15M Structural Bias: Detects Higher-Highs (Bullish) or Lower-Lows (Bearish)
    without lagging indicators.
    """
    recent = df_15m.iloc[-14:-1] # Last 13 closed 15m candles (~3.2 hours)
    half = len(recent) // 2
    
    first_half_high = recent['high'].iloc[:half].max()
    second_half_high = recent['high'].iloc[half:].max()
    first_half_low = recent['low'].iloc[:half].min()
    second_half_low = recent['low'].iloc[half:].min()

    # Higher Highs & Higher Lows = Pure Bullish
    if second_half_high > first_half_high and second_half_low >= first_half_low:
        return "BULLISH"
    # Lower Lows & Lower Highs = Pure Bearish
    elif second_half_low < first_half_low and second_half_high <= first_half_high:
        return "BEARISH"
    
    # Neutral filter
    return "BULLISH" if df_15m['close'].iloc[-2] > df_15m['open'].iloc[-6] else "BEARISH"

def check_price_action_setup(df_exec, macro_bias, cfg):
    """
    Volume-Confirmed 5M Break of Structure (BOS)
    - Closed candle verification (Zero Repainting)
    - 15-period structural swing pivots
    - Volume surge verification
    """
    if df_exec is None or len(df_exec) < 30:
        return None

    lookback = 15
    # Window looking strictly before the last closed candle
    window = df_exec.iloc[-(lookback + 2):-2]
    
    swing_high = float(window['high'].max())
    swing_low = float(window['low'].min())

    # Strictly evaluate the LAST CLOSED CANDLE (iloc[-2])
    confirmed = df_exec.iloc[-2]
    close_price = float(confirmed['close'])
    open_price = float(confirmed['open'])
    vol = float(confirmed['volume'])
    
    # Volume check: Breakout candle volume must exceed 20-period average volume by 25%
    avg_vol = float(df_exec['volume'].iloc[-18:-2].mean())
    volume_surge = vol > (avg_vol * 1.25)

    dec = cfg['pip_dec']

    # ---------------- 1. CONFIRMED BUY SETUP ----------------
    if macro_bias == "BULLISH" and volume_surge:
        # Solid green candle closing clean above swing high
        if close_price > swing_high and close_price > open_price:
            # 0.35% protective buffer below swing low
            sl = round(swing_low * 0.9965, dec)
            risk = round(close_price - sl, dec)
            risk_pct = risk / close_price

            if 0.0035 <= risk_pct <= cfg['max_sl_pct']:
                tp1 = round(close_price + (risk * 1.2), dec)
                tp2 = round(close_price + (risk * 2.0), dec)
                return {
                    "side": "BUY (LONG)",
                    "icon": "🟢",
                    "entry": close_price,
                    "sl": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "risk": risk,
                    "structure_break": swing_high,
                    "bias": macro_bias
                }

    # ---------------- 2. CONFIRMED SELL SETUP ----------------
    if macro_bias == "BEARISH" and volume_surge:
        # Solid red candle closing clean below swing low
        if close_price < swing_low and close_price < open_price:
            # 0.35% protective buffer above swing high
            sl = round(swing_high * 1.0035, dec)
            risk = round(sl - close_price, dec)
            risk_pct = risk / close_price

            if 0.0035 <= risk_pct <= cfg['max_sl_pct']:
                tp1 = round(close_price - (risk * 1.2), dec)
                tp2 = round(close_price - (risk * 2.0), dec)
                return {
                    "side": "SELL (SHORT)",
                    "icon": "🔴",
                    "entry": close_price,
                    "sl": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "risk": risk,
                    "structure_break": swing_low,
                    "bias": macro_bias
                }

    return None

def run_price_action_engine():
    print("⚡ Institutional Volume & Structure Engine Online...", flush=True)
    send_telegram_alert(
        "🏛 *UPGRADED INSTITUTIONAL ENGINE ACTIVATED!*\n\n"
        "• *HTF Filter:* 15M Macro Structure (HH / LL)\n"
        "• *Execution:* 5M Confirmed Candle Close (No Wicks/Repaint)\n"
        "• *Volume Gate:* > 1.25x Volume Surge Required\n"
        "• *Risk Model:* 1:1.2 Fast Scalp & 1:2.0 Expansion Target\n"
        "• *Volatility Armor:* Wider SL Buffer to prevent spread hunt."
    )

    cycle = 0
    while True:
        try:
            cycle += 1
            for pair, cfg in PAIRS.items():
                now = time.time()
                if (now - last_signal_time[pair]) < (COOLDOWN_MINUTES * 60):
                    continue

                # 300 = 5M candles, 900 = 15M candles
                df_exec = fetch_candles(pair, granularity=300)
                time.sleep(0.2)
                df_macro = fetch_candles(pair, granularity=900)
                time.sleep(0.2)

                if df_exec is not None and df_macro is not None:
                    macro_bias = get_market_structure(df_macro)
                    setup = check_price_action_setup(df_exec, macro_bias, cfg)

                    if setup:
                        last_signal_time[pair] = now
                        msg = (
                            f"{setup['icon']} *CONFIRMED SETUP: {cfg['name']}* {setup['icon']}\n\n"
                            f"Action: *{setup['side']}*\n"
                            f"🔹 *Entry (Closed):* `${setup['entry']:,.4f}`\n"
                            f"🛑 *Stop Loss:* `${setup['sl']:,.4f}`\n"
                            f"🎯 *Target 1 (1:1.2):* `${setup['tp1']:,.4f}` (Secure 70%)\n"
                            f"🎯 *Target 2 (1:2.0):* `${setup['tp2']:,.4f}` (Runner)\n\n"
                            f"🏛 *Logic Breakdown:*\n"
                            f"• Macro 15M Bias: *{setup['bias']}*\n"
                            f"• Pivot Breached: `${setup['structure_break']:,.4f}`\n"
                            f"• Confirmation: *Confirmed Close + Volume Surge*\n\n"
                            f"⚡ *Rule:* Target 1 aate hi Stop Loss ko entry price par move karein."
                        )
                        send_telegram_alert(msg)

            if cycle % 3 == 0:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Volume & Structure Scan Complete. System Healthy.", flush=True)

            time.sleep(15)

        except Exception as e:
            print(f"Scanner Exception: {e}", flush=True)
            time.sleep(10)

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Institutional Engine Active & Protected!")

    def log_message(self, format, *args):
        return

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

if __name__ == "__main__":
    t = threading.Thread(target=run_price_action_engine, daemon=True)
    t.start()
    run_server()
