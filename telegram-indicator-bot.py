import os, time, threading, asyncio
from datetime import datetime, timedelta, timezone
import pandas as pd
import pandas_ta as ta
import yfinance as yf
from flask import Flask
from telegram import Bot

# --- CONFIG ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PAIRS = ["EURUSD=X", "AUDCHF=X", "GBPCHF=X", "EURCAD=X", "AUDCAD=X", "USDCHF=X", "CADCHF=X", "AUDJPY=X", "CADJPY=X", "EURJPY=X", "USDJPY=X", "GBPUSD=X", "EURGBP=X", "GBPJPY=X", "GBPAUD=X"]
START_HOUR, END_HOUR = 9, 21
MIN_ATR = 0.00008  # Volatility Floor
COOLDOWN_MIN = 5

bot = Bot(token=TELEGRAM_TOKEN)
app = Flask(__name__)

# --- ANALYSIS ENGINE ---
def analyze(df, pair):
    if df is None or len(df) < 30: return None
    
    # 1. Standardize column names to lowercase
    df.columns = [str(col).lower() for col in df.columns]
    
    # 2. Calculate Indicators
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    if df["atr"] is None or df["atr"].iloc[-1] < MIN_ATR: return None

    df["rsi"] = ta.rsi(df["close"], length=7)
    
    # MACD Extraction (Safely take by position)
    macd_df = ta.macd(df["close"], fast=5, slow=13, signal=8)
    if macd_df is not None:
        df["macd_line"] = macd_df.iloc[:, 0]   # First column (MACD)
        df["macd_signal"] = macd_df.iloc[:, 2] # Third column (Signal)
    
    # Stochastic Extraction (Safely take by position)
    stoch_df = ta.stoch(df["high"], df["low"], df["close"], k=5, d=3, smooth_k=3)
    if stoch_df is not None:
        df["st_k"] = stoch_df.iloc[:, 0] # First column (K)
        df["st_d"] = stoch_df.iloc[:, 1] # Second column (D)

    # 3. Final Signal Logic
    if len(df) < 2: return None
    latest, prev = df.iloc[-1], df.iloc[-2]
    
    buy = (latest["rsi"] > 50 and 
           latest["macd_line"] > latest["macd_signal"] and 
           latest["st_k"] > latest["st_d"] and prev["st_k"] <= prev["st_d"])
           
    sell = (latest["rsi"] < 50 and 
            latest["macd_line"] < latest["macd_signal"] and 
            latest["st_k"] < latest["st_d"] and prev["st_k"] >= prev["st_d"])

    if buy: return "BUY (CALL) 🟢"
    if sell: return "SELL (PUT) 🔴"
    return None
# --- STRENGTH METER ---
def get_strength():
    data = yf.download(PAIRS, period="1d", interval="15m", progress=False)['Close']
    strengths = {c: [] for c in ["EUR", "USD", "GBP", "JPY", "AUD", "CAD", "CHF"]}
    for p in PAIRS:
        rsi = ta.rsi(data[p], length=7).iloc[-1]
        b, q = p[:3], p[3:6]
        if b in strengths: strengths[b].append(rsi)
        if q in strengths: strengths[q].append(100 - rsi)
    return sorted({k: sum(v)/len(v) for k, v in strengths.items() if v}.items(), key=lambda x: x[1], reverse=True)

# --- MAIN LOOP ---
def run_bot():
    last_signal_time = datetime.now(timezone.utc) - timedelta(minutes=COOLDOWN_MIN)
    while True:
        now = datetime.now(timezone.utc)
        # Check time window and cooldown
        if START_HOUR <= now.hour < END_HOUR and now > last_signal_time + timedelta(minutes=COOLDOWN_MIN):
            try:
                # Batch fetch all pairs
                all_data = yf.download(PAIRS, period="1d", interval="1m", progress=False, group_by='ticker')
                
                rank = get_strength()
                top3, bot3 = [r[0] for r in rank[:3]], [r[0] for r in rank[-3:]]
                
                for p in PAIRS:
                    df = all_data[p].copy()
                    sig = analyze(df, p) # This makes columns lowercase
                    
                    if sig:
                        # Safety: Check if 'close' exists after analyze() conversion
                        current_price = round(df["close"].iloc[-1], 5)
                        
                        base, quote = p[:3], p[3:6]
                        # Strength Filter
                        if ("BUY" in sig and (base in top3 or quote in bot3)) or \
                           ("SELL" in sig and (base in bot3 or quote in top3)):
                            
                            msg = (f"🎯 *SIGNAL*: {p}\n"
                                   f"*Action*: {sig}\n"
                                   f"*Price*: {current_price}\n\n"
                                   f"💪 Strong: {', '.join(top3)}\n"
                                   f"❄️ Weak: {', '.join(bot3)}")
                            
                            asyncio.run(bot.send_message(CHAT_ID, msg, parse_mode="Markdown"))
                            last_signal_time = now
                            break # Cooldown after one successful signal
            except Exception as e:
                print(f"Error in loop: {e}")
        
        time.sleep(60)

@app.route('/')
def home(): return "Bot Active"

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
