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

# Realistic Virtual Capital & Risk Settings
STARTING_BALANCE = 1000.0    # $1,000 Starting Balance
RISK_PER_TRADE_USD = 15.0    # 1.5% ($15) risk per trade

ASSETS = {
    "BTC/USDT":  {"name": "BITCOIN",    "zone": 35.0, "sl_buf": 30.0, "rr": 1.3},
    "ETH/USDT":  {"name": "ETHEREUM",   "zone": 4.0,  "sl_buf": 3.0,  "rr": 1.4},
    "SOL/USDT":  {"name": "SOLANA",     "zone": 0.4,  "sl_buf": 0.35, "rr": 1.4},
    "PAXG/USD":  {"name": "GOLD (SPOT)", "zone": 3.0,  "sl_buf": 2.5,  "rr": 1.4}
}

public_exchange = ccxt.coinbase({'enableRateLimit': True})
last_trade_times = {symbol: None for symbol in ASSETS}
trade_counts = {symbol: 0 for symbol in ASSETS}

# Performance & Live PnL State Tracker
pnl_tracker = {
    "current_balance": STARTING_BALANCE,
    "total_trades": 0,
    "wins": 0,
    "losses": 0,
    "active_positions": {}
}

def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print(f"Telegram Error: {e}", flush=True)

def fetch_ohlcv_data(symbol):
    try:
        ohlcv = public_exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=60)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['ema'] = df['close'].ewm(span=EMA_PERIOD, adjust=False).mean()
        return df
    except Exception as e:
        print(f"Fetch Error ({symbol}): {e}", flush=True)
        return None

def manage_positions_and_pnl(symbol, curr_price):
    if symbol not in pnl_tracker["active_positions"]:
        return

    pos = pnl_tracker["active_positions"][symbol]
    closed = False
    pnl = 0.0
    status_reason = ""

    # LONG Position Handling
    if pos["type"] == "LONG":
        if not pos["trailed"] and curr_price >= (pos["entry"] + pos["sl_distance"]):
            pos["stop_loss"] = pos["entry"]
            pos["trailed"] = True
            send_telegram_alert(
                f"🛡️ *TRAIL TO COST ({pos['name']})*\n"
                f"1:1 achieved. Stop Loss moved to Entry: ${pos['entry']:,.2f}"
            )

        if curr_price >= pos["target"]:
            pnl = pos["qty"] * (pos["target"] - pos["entry"])
            status_reason = "🎯 TARGET HIT"
            closed = True
            pnl_tracker["wins"] += 1
        elif curr_price <= pos["stop_loss"]:
            pnl = pos["qty"] * (pos["stop_loss"] - pos["entry"])
            status_reason = "🛑 STOP LOSS HIT"
            closed = True
            pnl_tracker["losses"] += 1

    # SHORT Position Handling
    elif pos["type"] == "SHORT":
        if not pos["trailed"] and curr_price <= (pos["entry"] - pos["sl_distance"]):
            pos["stop_loss"] = pos["entry"]
            pos["trailed"] = True
            send_telegram_alert(
                f"🛡️ *TRAIL TO COST ({pos['name']})*\n"
                f"1:1 achieved. Stop Loss moved to Entry: ${pos['entry']:,.2f}"
            )

        if curr_price <= pos["target"]:
            pnl = pos["qty"] * (pos["entry"] - pos["target"])
            status_reason = "🎯 TARGET HIT"
            closed = True
            pnl_tracker["wins"] += 1
        elif curr_price >= pos["stop_loss"]:
            pnl = pos["qty"] * (pos["entry"] - pos["stop_loss"])
            status_reason = "🛑 STOP LOSS HIT"
            closed = True
            pnl_tracker["losses"] += 1

    if closed:
        pnl_tracker["current_balance"] += pnl
        pnl_tracker["total_trades"] += 1

        win_rate = (pnl_tracker["wins"] / pnl_tracker["total_trades"]) * 100
        net_profit_pct = ((pnl_tracker["current_balance"] - STARTING_BALANCE) / STARTING_BALANCE) * 100
        net_profit_usd = pnl_tracker["current_balance"] - STARTING_BALANCE

        res_icon = "🟢" if pnl >= 0 else "🔴"
        pnl_sign = "+" if pnl >= 0 else ""

        report_msg = (
            f"{res_icon} *TRADE CLOSED: {status_reason}*\n\n"
            f"🔹 *Asset:* `{symbol}` ({pos['type']})\n"
            f"🔹 *Entry:* ${pos['entry']:,.2f} \vert{} *Exit:*${curr_price:,.2f}\n"
            f"💵 *Trade PnL:* {pnl_sign}${pnl:,.2f}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 *PERFORMANCE SCORECARD:*\n"
            f"• *Accuracy (Win-Rate):* `{win_rate:.1f}%` ({pnl_tracker['wins']}W / {pnl_tracker['losses']}L)\n"
            f"• *Total Completed Trades:* `{pnl_tracker['total_trades']}`\n"
            f"• *Total Net PnL:* `{'+' if net_profit_usd >= 0 else ''}${net_profit_usd:,.2f} ({'+' if net_profit_pct >= 0 else ''}{net_profit_pct:.2f}%)`\n"
            f"• *Current Portfolio Balance:* `${pnl_tracker['current_balance']:,.2f}`"
        )
        send_telegram_alert(report_msg)
        del pnl_tracker["active_positions"][symbol]

def execute_auto_trade(symbol, side, entry, sl, tp, name):
    sl_dist = abs(entry - sl)
    if sl_dist == 0:
        return
    qty = round(RISK_PER_TRADE_USD / sl_dist, 4)

    pnl_tracker["active_positions"][symbol] = {
        "name": name,
        "type": side,
        "entry": entry,
        "stop_loss": sl,
        "target": tp,
        "sl_distance": sl_dist,
        "qty": qty,
        "trailed": False
    }

    fill_msg = (
        f"🤖 *AUTO PAPER ORDER FILLED ({side})*\n"
        f"• *Asset:* `{symbol}`\n"
        f"• *Position Size (Qty):* `{qty}`\n"
        f"• *Max Risk Allocated:* ${RISK_PER_TRADE_USD:.2f} (1.5% of $1,000)"
    )
    send_telegram_alert(fill_msg)

def crypto_gold_scanner_worker():
    print("🚀 Scanner & Performance Engine Live with $1,000 Capital...", flush=True)
    send_telegram_alert(
        f"🌍 *CRYPTO & GOLD AUTO-PAPER TRADER ACTIVE!*\n\n"
        f"💼 *Starting Balance:* ${STARTING_BALANCE:,.2f}\n"
        f"🎯 *Risk Per Trade:* ${RISK_PER_TRADE_USD:,.2f} (1.5%)\n"
        f"Tracking Live Accuracy (Win-Rate %) & Net PnL %"
    )

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

                    manage_positions_and_pnl(symbol, curr_price)

                    cooldown_passed = True
                    if last_trade_times[symbol]:
                        passed = (now_ist - last_trade_times[symbol]).total_seconds() / 60
                        if passed < COOLDOWN_MINUTES:
                            cooldown_passed = False

                    if cooldown_passed and (symbol not in pnl_tracker["active_positions"]):
                        # LONG Signal
                        if last['close'] > ema_val and last['low'] <= (ema_val + cfg['zone']) and is_green:
                            sl_pts = round(curr_price - last['low'] + cfg['sl_buf'], 2)
                            tp_pts = round(sl_pts * cfg['rr'], 2)
                            target_price = round(curr_price + tp_pts, 2)
                            stop_price = round(curr_price - sl_pts, 2)

                            trade_counts[symbol] += 1
                            last_trade_times[symbol] = now_ist

                            signal_msg = (
                                f"🟢 *{cfg['name']} LONG SCALP #{trade_counts[symbol]}* 🟢\n\n"
                                f"🔹 *Pair:* `{symbol}`\n"
                                f"🔹 *Entry Price:* ${curr_price:,.2f}\n"
                                f"🎯 *Take Profit:* ${target_price:,.2f} (+${tp_pts})\n"
                                f"🛑 *Stop Loss:* ${stop_price:,.2f} (-${sl_pts})\n"
                                f"📈 *Pattern:* 20-EMA Dynamic Support Rebound\n\n"
                                f"⚡ *Rule:* Trail SL to entry at 1:1 reward."
                            )
                            send_telegram_alert(signal_msg)
                            execute_auto_trade(symbol, "LONG", curr_price, stop_price, target_price, cfg['name'])

                        # SHORT Signal
                        elif last['close'] < ema_val and last['high'] >= (ema_val - cfg['zone']) and is_red:
                            sl_pts = round(last['high'] - curr_price + cfg['sl_buf'], 2)
                            tp_pts = round(sl_pts * cfg['rr'], 2)
                            target_price = round(curr_price - tp_pts, 2)
                            stop_price = round(curr_price + sl_pts, 2)

                            trade_counts[symbol] += 1
                            last_trade_times[symbol] = now_ist

                            signal_msg = (
                                f"🔴 *{cfg['name']} SHORT SCALP #{trade_counts[symbol]}* 🔴\n\n"
                                f"🔹 *Pair:* `{symbol}`\n"
                                f"🔹 *Entry Price:* ${curr_price:,.2f}\n"
                                f"🎯 *Take Profit:* ${target_price:,.2f} (-${tp_pts})\n"
                                f"🛑 *Stop Loss:* ${stop_price:,.2f} (+${sl_pts})\n"
                                f"📉 *Pattern:* 20-EMA Dynamic Resistance Rejection\n\n"
                                f"⚡ *Rule:* Trail SL to entry at 1:1 reward."
                            )
                            send_telegram_alert(signal_msg)
                            execute_auto_trade(symbol, "SHORT", curr_price, stop_price, target_price, cfg['name'])

                time.sleep(2)

            time.sleep(12)

        except Exception as e:
            print(f"Scanner Loop Error: {e}", flush=True)
            time.sleep(10)

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot & Engine Running Healthy!")

    def log_message(self, format, *args):
        return

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

if __name__ == "__main__":
    t = threading.Thread(target=crypto_gold_scanner_worker, daemon=True)
    t.start()
    run_server()
