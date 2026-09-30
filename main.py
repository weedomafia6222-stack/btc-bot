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

HTF_TIMEFRAME = "15m"   # Institutional Liquidity & Bias
LTF_TIMEFRAME = "5m"    # CHoCH & FVG Entry Execution

COOLDOWN_MINUTES = 12

ASSETS = {
    "BTC/USDT":  {"name": "BITCOIN",    "min_fvg": 15.0,  "sl_buf": 20.0, "rr": 2.0},
    "ETH/USDT":  {"name": "ETHEREUM",   "min_fvg": 1.5,   "sl_buf": 2.0,  "rr": 2.0},
    "SOL/USDT":  {"name": "SOLANA",     "min_fvg": 0.20,  "sl_buf": 0.25, "rr": 2.0},
    "PAXG/USD":  {"name": "GOLD (SPOT)", "min_fvg": 1.5,   "sl_buf": 2.0,  "rr": 2.0}
}

exchange = ccxt.coinbase({'enableRateLimit': True})
last_trade_times = {symbol: None for symbol in ASSETS}
trade_counts = {symbol: 0 for symbol in ASSETS}
last_checked_candles = {symbol: None for symbol in ASSETS}

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

def fetch_data(symbol, timeframe, limit=60):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    except Exception as e:
        print(f"Fetch Error ({symbol} {timeframe}): {e}", flush=True)
        return None

def check_smc_setup(df_htf, df_ltf, cfg):
    """
    SMC/ICT Confluence Logic:
    1. HTF (15m): Swing High / Low Liquidity Sweep
    2. LTF (5m): Change of Character (CHoCH) via Displacement
    3. LTF (5m): Fair Value Gap (FVG) retest entry
    """
    if df_htf is None or df_ltf is None or len(df_htf) < 20 or len(df_ltf) < 20:
        return None

    # --- 1. HTF LIQUIDITY SWEEP CHECK ---
    htf_recent = df_htf.iloc[-10:-2]
    htf_prev_high = htf_recent['high'].max()
    htf_prev_low  = htf_recent['low'].min()
    
    htf_last = df_htf.iloc[-2]  # Last closed 15m candle
    bullish_sweep = (htf_last['low'] < htf_prev_low) and (htf_last['close'] > htf_prev_low)
    bearish_sweep = (htf_last['high'] > htf_prev_high) and (htf_last['close'] < htf_prev_high)

    # --- 2. LTF (5m) CHoCH & FVG CONFIRMATION ---
    c1 = df_ltf.iloc[-4]
    c2 = df_ltf.iloc[-3]  # Displacement candle
    c3 = df_ltf.iloc[-2]  # Re-test / Entry candle
    curr = df_ltf.iloc[-1]

    curr_price = curr['close']

    # --- BULLISH SMC SETUP (BUY) ---
    # FVG condition: c1['high'] < c3['low']
    bull_fvg_gap = c3['low'] - c1['high']
    ltf_swing_high = df_ltf.iloc[-12:-4]['high'].max()
    choch_bullish = c2['close'] > ltf_swing_high  # Displacement break above swing high

    if (bullish_sweep or choch_bullish) and (bull_fvg_gap >= cfg['min_fvg']):
        # Pullback into FVG zone (50% Consequent Encroachment)
        fvg_mid = c1['high'] + (bull_fvg_gap * 0.5)
        if c3['low'] <= c3['high'] and curr_price >= fvg_mid:
            sl_price = round(min(c2['low'], c3['low']) - cfg['sl_buf'], 2)
            risk_dist = curr_price - sl_price
            if risk_dist > 0:
                tp_price = round(curr_price + (risk_dist * cfg['rr']), 2)
                return {
                    "side": "LONG",
                    "entry": round(curr_price, 2),
                    "sl": sl_price,
                    "tp": tp_price,
                    "risk": round(risk_dist, 2),
                    "target_gain": round(risk_dist * cfg['rr'], 2),
                    "confluence": "15m SSL Sweep + 5m CHoCH + Bullish FVG Tap"
                }

    # --- BEARISH SMC SETUP (SELL) ---
    # FVG condition: c1['low'] > c3['high']
    bear_fvg_gap = c1['low'] - c3['high']
    ltf_swing_low = df_ltf.iloc[-12:-4]['low'].min()
    choch_bearish = c2['close'] < ltf_swing_low  # Displacement break below swing low

    if (bearish_sweep or choch_bearish) and (bear_fvg_gap >= cfg['min_fvg']):
        fvg_mid = c3['high'] + (bear_fvg_gap * 0.5)
        if c3['high'] >= c3['low'] and curr_price <= fvg_mid:
            sl_price = round(max(c2['high'], c3['high']) + cfg['sl_buf'], 2)
            risk_dist = sl_price - curr_price
            if risk_dist > 0:
                tp_price = round(curr_price - (risk_dist * cfg['rr']), 2)
                return {
                    "side": "SHORT",
                    "entry": round(curr_price, 2),
                    "sl": sl_price,
                    "tp": tp_price,
                    "risk": round(risk_dist, 2),
                    "target_gain": round(risk_dist * cfg['rr'], 2),
                    "confluence": "15m BSL Sweep + 5m CHoCH + Bearish FVG Tap"
                }

    return None

def smc_scanner_worker():
    print("SMC/ICT Multi-Timeframe Master Engine Live...", flush=True)
    send_telegram_alert(
        "🏛️ *SMC & ICT MASTER STRATEGY ACTIVE!*\n\n"
        "• *HTF Structure:* 15m Liquidity Sweeps (BSL/SSL)\n"
        "• *LTF Execution:* 5m CHoCH + Fair Value Gap (FVG)\n"
        "• *Risk:Reward:* Minimum 1:2 RR\n"
        "• *Pairs:* BTC, ETH, SOL, GOLD (PAXG)"
    )

    while True:
        try:
            ist = pytz.timezone('Asia/Kolkata')
            now_ist = datetime.now(ist)

            for symbol, cfg in ASSETS.items():
                df_ltf = fetch_data(symbol, LTF_TIMEFRAME, 50)
                time.sleep(1)
                df_htf = fetch_data(symbol, HTF_TIMEFRAME, 50)

                if df_ltf is not None and df_htf is not None:
                    last_candle_time = df_ltf.iloc[-1]['timestamp']

                    # Synchronize with fresh candle close
                    if last_checked_candles[symbol] != last_candle_time:
                        cooldown_passed = True
                        if last_trade_times[symbol]:
                            passed = (now_ist - last_trade_times[symbol]).total_seconds() / 60
                            if passed < COOLDOWN_MINUTES:
                                cooldown_passed = False

                        if cooldown_passed:
                            signal = check_smc_setup(df_htf, df_ltf, cfg)
                            if signal:
                                trade_counts[symbol] += 1
                                last_trade_times[symbol] = now_ist
                                last_checked_candles[symbol] = last_candle_time

                                icon = "🟢" if signal['side'] == "LONG" else "🔴"

                                alert_msg = (
                                    f"{icon} *SMC/ICT INSTITUTIONAL {signal['side']} #{trade_counts[symbol]}* {icon}\n\n"
                                    f"🔹 *Asset:* `{symbol}` ({cfg['name']})\n"
                                    f"🔹 *Confluence:* _{signal['confluence']}_\n"
                                    f"🔹 *Entry Price:* ${signal['entry']:,.2f}\n"
                                    f"🎯 *Take Profit:* ${signal['tp']:,.2f} (+${signal['target_gain']})\n"
                                    f"🛑 *Stop Loss:* ${signal['sl']:,.2f} (-${signal['risk']})\n"
                                    f"⚖️ *Risk:Reward:* `1:{cfg['rr']}`\n\n"
                                    f"📌 *Execution Rule:* Trail SL to Entry at 1:1 reward. Hold balance till opposing liquidity."
                                )
                                send_telegram_alert(alert_msg)

                time.sleep(2)

            print(f"[{now_ist.strftime('%H:%M:%S')}] SMC 15m/5m Confluence Scanned | All Active", flush=True)
            time.sleep(20)

        except Exception as e:
            print(f"SMC Loop Error: {e}", flush=True)
            time.sleep(10)

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"SMC Multi-Timeframe Bot Healthy!")

    def log_message(self, format, *args):
        return

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

if __name__ == "__main__":
    t = threading.Thread(target=smc_scanner_worker, daemon=True)
    t.start()
    run_server()
