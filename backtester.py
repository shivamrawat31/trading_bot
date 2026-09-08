import pandas as pd
import logging
from datetime import datetime, date
from zoneinfo import ZoneInfo
from fyers_client import FyersClient
from strategy import Strategy
from config import WATCHLIST, INDEX_SYMBOL, USE_INDEX_FILTER, LTF_TIMEFRAME, HTF_TIMEFRAME

# Suppress debug/info logs for cleaner output
logging.basicConfig(level=logging.ERROR)

def main():
    print("\n" + "="*50)
    print("🕰️  DYNAMIC TIME MACHINE BACKTESTER")
    print("="*50)
    
    # 1. Interactive Inputs
    start_input = input("Enter Start Date (YYYY-MM-DD): ").strip()
    end_input = input("Enter End Date (YYYY-MM-DD): ").strip()
    strat_choice = input("Which strategy to backtest? (1, 2, or both): ").strip().lower()
    
    try:
        start_date = datetime.strptime(start_input, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_input, "%Y-%m-%d").date()
    except ValueError:
        print("❌ Invalid date format. Please use YYYY-MM-DD.")
        return

    if start_date > end_date:
        print("❌ Start date cannot be after end date.")
        return

    tz = ZoneInfo("Asia/Kolkata")
    
    start_dt = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=tz)
    end_dt = datetime.combine(end_date, datetime.max.time()).replace(tzinfo=tz)
    
    start_epoch = int(start_dt.timestamp())
    end_epoch = int(end_dt.timestamp())
    
    if (end_date - start_date).days > 99:
        print("❌ Fyers API error -50: You cannot request more than 99 days of intraday data at once.")
        return

    print(f"\n📡 Authenticating with Fyers and fetching data...")
    try:
        broker = FyersClient()
        broker.authenticate()
    except Exception as e:
        print(f"❌ Failed to authenticate: {e}")
        return
        
    strategy = Strategy()
    
    # 2. Fetch and Filter Data
    index_data = broker.get_history(INDEX_SYMBOL, resolution=LTF_TIMEFRAME, start_epoch=start_epoch, end_epoch=end_epoch)
    if index_data is None or index_data.empty:
        print("❌ Failed to fetch Index data.")
        return
        
    # Filter strictly to user's date range
    index_data['date'] = index_data['datetime'].dt.date
    index_data = index_data[(index_data['date'] >= start_date) & (index_data['date'] <= end_date)]
    
    stock_data_ltf = {}
    stock_data_htf = {}
    for symbol in WATCHLIST:
        # LTF
        df_l = broker.get_history(symbol, resolution=LTF_TIMEFRAME, start_epoch=start_epoch, end_epoch=end_epoch)
        if df_l is not None and not df_l.empty:
            df_l['date'] = df_l['datetime'].dt.date
            stock_data_ltf[symbol] = df_l[(df_l['date'] >= start_date) & (df_l['date'] <= end_date)]
        # HTF
        df_h = broker.get_history(symbol, resolution=HTF_TIMEFRAME, start_epoch=start_epoch, end_epoch=end_epoch)
        if df_h is not None and not df_h.empty:
            df_h['date'] = df_h['datetime'].dt.date
            stock_data_htf[symbol] = df_h[(df_h['date'] >= start_date) & (df_h['date'] <= end_date)]
            
    unique_dates = index_data['date'].unique()
    
    if len(unique_dates) == 0:
        print(f"⚠️ No market data found between {start_date} and {end_date}. Was it a weekend/holiday?")
        return

    total_days = len(unique_dates)
    days_with_trades = 0
    total_trades = 0
    inactive_days = 0
    
    print("\n" + "="*50)
    print(f"🚀 BACKTEST RESULTS ({start_date} to {end_date})")
    print("="*50)
    
    # 3. Execution Loop
    for current_date in unique_dates:
        print(f"\n📅 Date: {current_date}")
        
        try:
            if strat_choice == '1':
                t_start, t_end = "09:45:00", "10:45:00"
            elif strat_choice == '2':
                t_start, t_end = "10:45:00", "15:10:00"
            else:
                t_start, t_end = "09:45:00", "15:10:00"
            times_to_check = pd.date_range(start=f"{current_date} {t_start}", end=f"{current_date} {t_end}", freq="5min", tz="Asia/Kolkata")
        except Exception:
            continue
            
        trades_today = 0
        stocks_traded_today = set()
        
        for check_time in times_to_check:
            time_str = check_time.strftime('%H:%M')
            if strat_choice == '1':
                active_tier = 1
            elif strat_choice == '2':
                active_tier = 2
            else:
                active_tier = 1 if time_str < "10:45" else 2
                
            sliced_index = index_data[index_data['datetime'] <= check_time].copy()
            
            bias = "NEUTRAL"
            if USE_INDEX_FILTER and not sliced_index.empty:
                bias = strategy.evaluate_index_bias(sliced_index)
                
            for symbol in WATCHLIST:
                if symbol in stocks_traded_today:
                    continue
                    
                df_ltf = stock_data_ltf.get(symbol)
                df_htf = stock_data_htf.get(symbol)
                
                if df_ltf is None or df_htf is None:
                    continue
                    
                sliced_ltf = df_ltf[df_ltf['datetime'] <= check_time].copy()
                sliced_htf = df_htf[df_htf['datetime'] <= check_time].copy()
                
                if sliced_ltf.empty or sliced_htf.empty:
                    continue
                    
                if active_tier == 1:
                    or_data = strategy.determine_htf_trend(sliced_htf)
                    signal_data = strategy.calculate_signal(sliced_ltf, or_data)
                else:
                    signal_data = strategy.calculate_strategy_2(sliced_ltf)
                signal = signal_data.get("signal", "NONE")
                
                if signal in ["BUY", "SELL"] and signal_data.get("score", 0) >= 2:
                    if USE_INDEX_FILTER:
                        if (signal == "BUY" and bias == "BEARISH") or (signal == "SELL" and bias == "BULLISH"):
                            continue 
                            
                    entry_price = signal_data.get("price", 0)
                    sl_price = signal_data.get("sl_price", 0)
                    tp_price = signal_data.get("target_price", 0)
                    
                    print(f"  -> [{time_str}] {symbol} | {signal} (Tier {active_tier}) | Nifty: {bias}")
                    print(f"      Entry: ₹{entry_price:.2f} | SL: ₹{sl_price:.2f} | TP: ₹{tp_price:.2f}")
                    
                    trades_today += 1
                    total_trades += 1
                    stocks_traded_today.add(symbol)
                    
        if trades_today > 0:
            days_with_trades += 1
        else:
            print("  💤 INACTIVE DAY - No setups met strict criteria.")
            inactive_days += 1
            
    print("\n" + "="*50)
    print("📊 BACKTEST SUMMARY")
    print(f"Total Trading Days Analyzed: {total_days}")
    print(f"Days with Trades: {days_with_trades}")
    print(f"Inactive Days (Capital Protected): {inactive_days}")
    print(f"Total High-Probability Trades Fired: {total_trades}")
    print("="*50)

if __name__ == "__main__":
    main()