import os
import pandas as pd
import logging
from datetime import datetime

from fyers_client import FyersClient
from strategy import Strategy
from config import WATCHLIST, INDEX_SYMBOL, USE_INDEX_FILTER

# Suppress debug/info logs for cleaner output
logging.basicConfig(level=logging.WARNING)

def main():
    print("Starting 30-day Historical Backtester...")
    
    try:
        broker = FyersClient()
        broker.authenticate()
    except Exception as e:
        print(f"Failed to authenticate with Fyers: {e}")
        return
        
    strategy = Strategy()
    
    print("Fetching historical data for 30 days...")
    
    # Fetch Index data
    index_data = broker.get_history(INDEX_SYMBOL, resolution="5", days=30)
    if index_data is None or index_data.empty:
        print("Failed to fetch index data.")
        return
        
    # Fetch Stock data
    stock_data = {}
    for symbol in WATCHLIST:
        df = broker.get_history(symbol, resolution="5", days=30)
        if df is not None and not df.empty:
            stock_data[symbol] = df
        else:
            print(f"Warning: Failed to fetch data for {symbol}")
            
    # Get unique dates from index data
    unique_dates = index_data['datetime'].dt.date.unique()
    
    total_days = len(unique_dates)
    days_with_trades = 0
    total_trades = 0
    inactive_days = 0
    
    print("\n--- Starting Backtest Engine ---")
    
    for date in unique_dates:
        print(f"\n[Date: {date}]")
        
        # Simulate time loop: 09:45 to 11:30 in 5 min intervals
        try:
            times_to_check = pd.date_range(start=f"{date} 09:45:00", end=f"{date} 11:30:00", freq="5min", tz="Asia/Kolkata")
        except Exception as e:
            print(f"Error creating time range for {date}: {e}")
            continue
            
        trades_today = 0
        stocks_traded_today = set()
        
        for check_time in times_to_check:
            # Slice index data
            sliced_index = index_data[index_data['datetime'] <= check_time].copy()
            if sliced_index.empty:
                continue
                
            # Get Index Bias
            bias = "NEUTRAL"
            if USE_INDEX_FILTER:
                bias = strategy.evaluate_index_bias(sliced_index)
                
            # Check stocks
            for symbol, df in stock_data.items():
                if symbol in stocks_traded_today:
                    continue
                    
                sliced_stock = df[df['datetime'] <= check_time].copy()
                if sliced_stock.empty:
                    continue
                    
                # 1. Determine HTF Trend (Opening Range)
                or_data = strategy.determine_htf_trend(sliced_stock)
                
                # 2. Calculate Signal
                signal_data = strategy.calculate_signal(sliced_stock, or_data)
                signal = signal_data.get("signal", "NONE")
                
                if signal in ["BUY", "SELL"]:
                    # 3. Apply Index Bias Filter
                    if USE_INDEX_FILTER:
                        if (signal == "BUY" and bias == "BEARISH") or \
                           (signal == "SELL" and bias == "BULLISH"):
                            continue # Rejected by filter
                            
                    # Valid Opening Range Liquidity Sweep
                    entry_price = signal_data.get("price", 0)
                    time_str = check_time.strftime('%H:%M')
                    print(f"  -> [{time_str}] {symbol} | Signal: {signal} | Nifty Bias: {bias} | Entry: ₹{entry_price:.2f}")
                    
                    trades_today += 1
                    total_trades += 1
                    stocks_traded_today.add(symbol)
                    
        if trades_today > 0:
            days_with_trades += 1
        else:
            inactive_days += 1
            
    print("\n=================================")
    print("BACKTEST SUMMARY")
    print("=================================")
    print(f"Total days analyzed: {total_days}")
    print(f"Days with trades: {days_with_trades}")
    print(f"Inactive (capital protected) days: {inactive_days}")
    print(f"Total trades fired: {total_trades}")
    print("=================================")

if __name__ == "__main__":
    main()
