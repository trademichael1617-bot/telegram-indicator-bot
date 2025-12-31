import os, time, threading, asyncio
from datetime import datetime, timedelta, timezone
import pandas as pd
import pandas_ta as ta
import yfinance as yf
from flask import Flask
from telegram import Bot
import requests

# --- CONFIG ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PAIRS = ["EURUSD=X", "AUDCHF=X", "GBPCHF=X", "EURCAD=X", "AUDCAD=X", "USDCHF=X", "CADCHF=X", "AUDJPY=X", "CADJPY=X", "EURJPY=X", "USDJPY=X", "GBPUSD=X", "EURGBP=X", "GBPJPY=X", "GBPAUD=X"]
START_HOUR, END_HOUR = 9, 21
MIN_ATR = 0.00008  
COOLDOWN_MIN = 5

bot = Bot(token=TELEGRAM_TOKEN)
app = Flask(__name__)

# --- STARTUP NOTIFICATION ---
async def send_startup_alert():
    try:
        await bot.send_message(CHAT_ID, "🚀 *Bot Online:* Signals and Health Monitoring Active.", parse_mode="Markdown")
        print("Startup alert sent to Telegram.")
    except Exception as e:
        print(f"Failed to send startup alert: {e}")

# --- ANALYSIS ENGINE ---
def analyze(df, pair):
    if df is None or len(df) < 30: return None
    df.columns = [str(col).lower() for col in df.columns]
    
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    if df["atr"] is None or df["atr"].iloc[-1] < MIN_ATR: return None

    df["rsi"] = ta.rsi(df["close"], length=7)
    
    macd_df = ta.macd(df["close"], fast=5, slow=13, signal=8)
    if macd_df is not None:
        df["macd_line"] = macd_df.iloc[:, 0]
        df["macd_signal"] = macd_df.iloc[:, 2]
    
    stoch_df = ta.stoch(df["high"], df["low"], df["close"], k=5, d=3, smooth_k=3)
    if stoch_df is not None:
        df["st_k"] = stoch_df.iloc[:, 0]
        df["st_d"] = stoch_df.iloc[:, 1]

    if len(df) < 2: return None
    latest, prev = df.iloc[-1], df.iloc[-2]
    
    buy = (latest["rsi"] > 50 and latest["macd_line"] > latest["macd_signal"] and 
           latest["st_k"] > latest["st_d"] and prev["st_k"] <= prev["st_d"])
    sell = (latest["rsi"] < 50 and latest["macd_line"] < latest["macd_signal"] and 
            latest["st_k"] < latest["st_d"] and prev["st_k"] >= prev["st_d"])

    if buy: return "BUY (CALL) 🟢"
    if sell: return "SELL (PUT) 🔴"
    return None

# --- STRENGTH METER ---
def get_strength():
    try:
        data = yf.download(PAIRS, period="1d", interval="15m", progress=False)['Close']
        if isinstance(data, pd.Series): return []
        strengths = {c: [] for c in ["EUR", "USD", "GBP", "JPY", "AUD", "CAD", "CHF"]}
        for p in PAIRS:
            if p not in data: continue
            rsi_series = ta.rsi(data[p], length=7)
            if rsi_series is None or len(rsi_series) == 0: continue
            rsi = rsi_series.iloc[-1]
            b, q = p[:3], p[3:6]
            if b in strengths: strengths[b].append(rsi)
            if q in strengths: strengths[q].append(100 - rsi)
        return sorted({k: sum(v)/len(v) for k, v in strengths.items() if v}.items(), key=lambda x: x[1], reverse=True)
    except Exception as e:
        print(f"Strength Meter Error: {e}"); return []

# --- MAIN LOOP ---
def run_bot():
    # Send Startup Message
    asyncio.run(send_startup_alert())
    
    last_signal_time = datetime.now(timezone.utc) - timedelta(minutes=COOLDOWN_MIN)
    print("Bot loop started.")
    
    while True:
        # Keep-Alive Ping
        try:
            requests.get(f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}", timeout=5)
        except:
            pass

        now = datetime.now(timezone.utc)
        if START_HOUR <= now.hour < END_HOUR and now > last_signal_time + timedelta(minutes=COOLDOWN_MIN):
            try:
                all_data = yf.download(PAIRS, period="1d", interval="1m", progress=False, group_by='ticker')
                rank = get_strength()
                if not rank: 
                    time.sleep(60); continue
                
                top3, bot3 = [r[0] for r in rank[:3]], [r[0] for r in rank[-3:]]
                for p in PAIRS:
                    df = all_data[p].copy()
                    sig = analyze(df, p)
                    if sig:
                        current_price = round(df["close"].iloc[-1], 5)
                        base, quote = p[:3], p[3:6]
                        if ("BUY" in sig and (base in top3 or quote in bot3)) or \
                           ("SELL" in sig and (base in bot3 or quote in top3)):
                            msg = (f"🎯 *SIGNAL*: {p}\n*Action*: {sig}\n*Price*: {current_price}\n\n"
                                   f"💪 Strong: {', '.join(top3)}\n❄️ Weak: {', '.join(bot3)}")
                            asyncio.run(bot.send_message(CHAT_ID, msg, parse_mode="Markdown"))
                            last_signal_time = now; break 
            except Exception as e:
                print(f"Error: {e}")
        
        time.sleep(60)

@app.route('/')
def home(): return "Bot Active"

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
