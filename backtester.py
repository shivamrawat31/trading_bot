import time
import math
import pandas as pd
import logging
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from fyers_client import FyersClient
from strategy import Strategy
from config import (WATCHLIST, INDEX_SYMBOL, USE_INDEX_FILTER, LTF_TIMEFRAME, HTF_TIMEFRAME, 
                    MAX_DAILY_TRADES, MAX_RISK_PER_TRADE, LEVERAGE, DAILY_MAX_DRAWDOWN, 
                    DAILY_TARGET_PROFIT, MIN_RR_RATIO, TRAIL_ACTIVATION_ATR, TRAIL_DISTANCE_ATR, 
                    SQUARE_OFF_TIME)

# Suppress debug/info logs for cleaner output
logging.basicConfig(level=logging.ERROR)

def calculate_fyers_charges(buy_value, sell_value):
    brokerage_buy = min(20.0, 0.0003 * buy_value)
    brokerage_sell = min(20.0, 0.0003 * sell_value)
    brokerage = brokerage_buy + brokerage_sell
    
    stt = 0.00025 * sell_value
    exchange = 0.0000297 * (buy_value + sell_value)
    sebi = 0.000001 * (buy_value + sell_value)
    stamp_duty = 0.00003 * buy_value
    gst = 0.18 * (brokerage + exchange + sebi)
    
    return brokerage + stt + exchange + sebi + stamp_duty + gst

def main():
    print("\n" + "="*50)
    print("🕰️  DYNAMIC TIME MACHINE BACKTESTER")
    print("="*50)
    
    # 1. Interactive Inputs
    start_input = input("Enter Start Date (YYYY-MM-DD): ").strip()
    end_input = input("Enter End Date (YYYY-MM-DD): ").strip()
    strat_choice = input("Which strategy to backtest? (1, 2, or both): ").strip().lower()
    
    try:
        capital_input = float(input("Enter starting account balance: "))
    except ValueError:
        print("❌ Invalid capital format. Please enter a number.")
        return
    
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
    
    # 7-day warmup buffer
    fetch_start_dt = start_dt - timedelta(days=7)
    fetch_start_epoch = int(fetch_start_dt.timestamp())
    end_epoch = int(end_dt.timestamp())
    
    if (end_date - start_date).days + 7 > 99:
        print("❌ Fyers API error -50: You cannot request more than 99 days of intraday data at once (including 7-day warmup).")
        return

    print(f"\n📡 Authenticating with Fyers and fetching data (including 7-day warmup)...")
    try:
        broker = FyersClient()
        broker.authenticate()
    except Exception as e:
        print(f"❌ Failed to authenticate: {e}")
        return
        
    strategy = Strategy()
    
    # 2. Fetch and Filter Data
    index_data = broker.get_history(INDEX_SYMBOL, resolution=LTF_TIMEFRAME, start_epoch=fetch_start_epoch, end_epoch=end_epoch)
    if index_data is None or index_data.empty:
        print("❌ Failed to fetch Index data.")
        return
        
    index_data['date'] = index_data['datetime'].dt.date
    
    stock_data_ltf = {}
    stock_data_htf = {}
    for symbol in WATCHLIST:
        time.sleep(0.6)
        # LTF
        df_l = broker.get_history(symbol, resolution=LTF_TIMEFRAME, start_epoch=fetch_start_epoch, end_epoch=end_epoch)
        if df_l is not None and not df_l.empty:
            df_l['date'] = df_l['datetime'].dt.date
            stock_data_ltf[symbol] = df_l
        # HTF
        df_h = broker.get_history(symbol, resolution=HTF_TIMEFRAME, start_epoch=fetch_start_epoch, end_epoch=end_epoch)
        if df_h is not None and not df_h.empty:
            df_h['date'] = df_h['datetime'].dt.date
            stock_data_htf[symbol] = df_h
            
    # Strictly test only within start_date and end_date
    all_dates = index_data['date'].unique()
    unique_dates = sorted([d for d in all_dates if start_date <= d <= end_date])
    
    if len(unique_dates) == 0:
        print(f"⚠️ No market data found between {start_date} and {end_date}. Was it a weekend/holiday?")
        return

    total_days = len(unique_dates)
    days_with_trades = 0
    total_trades = 0
    winning_trades = 0
    losing_trades = 0
    inactive_days = 0
    
    current_capital = capital_input
    total_charges = 0.0
    
    print("\n" + "="*50)
    print(f"🚀 BACKTEST RESULTS ({start_date} to {end_date})")
    print("="*50)
    
    # 3. Execution Loop
    for current_date in unique_dates:
        print(f"\n📅 Date: {current_date}")
        
        available_slots = [current_capital * 0.50, current_capital * 0.50]
        open_positions = []
        daily_pnl = 0.0
        daily_charges = 0.0
        stocks_traded_today = set()
        trades_today = 0
        
        try:
            # Generate ticks from 09:45 to 15:10
            times_to_check = pd.date_range(start=f"{current_date} 09:45:00", end=f"{current_date} 15:10:00", freq="5min", tz="Asia/Kolkata")
        except Exception:
            continue
            
        for check_time in times_to_check:
            time_str = check_time.strftime('%H:%M')
            
            # --- PHASE 1: POSITION MANAGEMENT ---
            # Loop backwards to allow safe removal
            for i in range(len(open_positions) - 1, -1, -1):
                pos = open_positions[i]
                symbol = pos['symbol']
                
                df_ltf = stock_data_ltf.get(symbol)
                if df_ltf is None:
                    continue
                    
                # Find the exact candle for this check_time
                candle_df = df_ltf[df_ltf['datetime'] == check_time]
                if candle_df.empty:
                    continue
                candle = candle_df.iloc[0]
                
                exit_price = None
                exit_reason = ""
                
                open_p = candle['open']
                high = candle['high']
                low = candle['low']
                close = candle['close']
                
                # Check Auto Square-Off
                if time_str >= SQUARE_OFF_TIME:
                    exit_price = close
                    exit_reason = "SQUARE_OFF"
                else:
                    dynamic_stop = pos.get('dynamic_stop')
                    if pos['side'] == "BUY":
                        if dynamic_stop and low <= dynamic_stop:
                            exit_price = min(open_p, dynamic_stop)
                            exit_reason = "TRAIL_STOP"
                        elif low <= pos['sl_price']:
                            exit_price = min(open_p, pos['sl_price'])
                            exit_reason = "STOP_LOSS"
                        elif high >= pos['target_price']:
                            exit_price = max(open_p, pos['target_price'])
                            exit_reason = "TARGET"
                        else:
                            # Update HWM and dynamic stop
                            if high > pos['hwm']:
                                pos['hwm'] = high
                            if pos['hwm'] - pos['entry_price'] >= TRAIL_ACTIVATION_ATR * pos['atr']:
                                pos['dynamic_stop'] = pos['hwm'] - (TRAIL_DISTANCE_ATR * pos['atr'])
                                
                    elif pos['side'] == "SELL":
                        if dynamic_stop and high >= dynamic_stop:
                            exit_price = max(open_p, dynamic_stop)
                            exit_reason = "TRAIL_STOP"
                        elif high >= pos['sl_price']:
                            exit_price = max(open_p, pos['sl_price'])
                            exit_reason = "STOP_LOSS"
                        elif low <= pos['target_price']:
                            exit_price = min(open_p, pos['target_price'])
                            exit_reason = "TARGET"
                        else:
                            # Update HWM and dynamic stop
                            if low < pos['hwm']:
                                pos['hwm'] = low
                            if pos['entry_price'] - pos['hwm'] >= TRAIL_ACTIVATION_ATR * pos['atr']:
                                pos['dynamic_stop'] = pos['hwm'] + (TRAIL_DISTANCE_ATR * pos['atr'])
                            
                # Execute Exit
                if exit_price is not None:
                    buy_value = (pos['qty'] * pos['entry_price']) if pos['side'] == "BUY" else (pos['qty'] * exit_price)
                    sell_value = (pos['qty'] * exit_price) if pos['side'] == "BUY" else (pos['qty'] * pos['entry_price'])
                    
                    gross_pnl = sell_value - buy_value
                    costs = calculate_fyers_charges(buy_value, sell_value)
                    net_pnl = gross_pnl - costs
                    
                    current_capital += net_pnl
                    daily_pnl += net_pnl
                    daily_charges += costs
                    total_charges += costs
                    
                    if net_pnl > 0:
                        winning_trades += 1
                        pnl_tag = "[PROFITABLE TRADE]"
                    else:
                        losing_trades += 1
                        pnl_tag = "[LOSS TRADE]"
                    print(f"  -> [{time_str}] {symbol} Closed via {exit_reason} | Entry: ₹{pos['entry_price']:.2f}, Exit: ₹{exit_price:.2f} | Net: ₹{net_pnl:.2f} {pnl_tag}")
                    
                    # Return slot
                    available_slots.extend(pos['slots_used'])
                    
                    # Remove position
                    open_positions.pop(i)
            
            # --- PHASE 2: CIRCUIT BREAKERS ---
            if time_str >= SQUARE_OFF_TIME:
                continue # Block ghost trades
            if daily_pnl <= DAILY_MAX_DRAWDOWN or daily_pnl >= DAILY_TARGET_PROFIT:
                continue # Skip new entries
            if trades_today >= MAX_DAILY_TRADES:
                continue # Skip new entries
            
            if len(available_slots) == 0:
                continue # No capital to trade
                
            # Determine active tier based on strategy choice and time
            if strat_choice == '1':
                active_tier = 1
                if time_str >= "10:45": continue
            elif strat_choice == '2':
                active_tier = 2
                if time_str < "10:45": continue
            else:
                active_tier = 1 if time_str < "10:45" else 2
                
            sliced_index = index_data[index_data['datetime'] <= check_time].copy()
            bias = "NEUTRAL"
            if USE_INDEX_FILTER and not sliced_index.empty:
                bias = strategy.evaluate_index_bias(sliced_index)
                
            # --- PHASE 3: SCANNING & ENTRY ASSIGNMENT ---
            candidates = []
            
            for symbol in WATCHLIST:
                if symbol in stocks_traded_today:
                    continue
                    
                # Skip if already holding
                if any(p['symbol'] == symbol for p in open_positions):
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
                    target_price = signal_data.get("target_price", 0)
                    
                    if abs(entry_price - sl_price) <= 0:
                        continue
                        
                    rr = abs(target_price - entry_price) / abs(entry_price - sl_price)
                    if rr < MIN_RR_RATIO:
                        continue
                        
                    candidates.append({
                        'symbol': symbol,
                        'signal': signal,
                        'rvol': signal_data.get('rvol', 0),
                        'atr': signal_data.get('atr', 0),
                        'entry_price': entry_price,
                        'sl_price': sl_price,
                        'target_price': target_price,
                        'tier': active_tier
                    })
                    
            # Sort candidates by RVOL descending
            candidates.sort(key=lambda x: x['rvol'], reverse=True)
            
            # Execute entries based on slots
            for cand in candidates:
                if len(available_slots) == 0:
                    break
                if trades_today >= MAX_DAILY_TRADES:
                    break
                    
                if cand['tier'] == 1:
                    # Consumes all remaining slots
                    capital_slot = sum(available_slots)
                    slots_to_consume = available_slots.copy()
                    available_slots.clear()
                    current_max_risk = MAX_RISK_PER_TRADE * 2
                else:
                    # Consumes largest available slot
                    available_slots.sort(reverse=True)
                    capital_slot = available_slots.pop(0)
                    slots_to_consume = [capital_slot]
                    current_max_risk = MAX_RISK_PER_TRADE
                    
                risk_shares = math.floor(current_max_risk / abs(cand['entry_price'] - cand['sl_price']))
                margin_shares = math.floor((capital_slot * LEVERAGE) / cand['entry_price']) if cand['entry_price'] > 0 else 0
                qty = min(risk_shares, margin_shares)
                
                if qty <= 0:
                    # Return slot if trade fails
                    available_slots.extend(slots_to_consume)
                    continue
                    
                open_positions.append({
                    'symbol': cand['symbol'],
                    'side': cand['signal'],
                    'qty': qty,
                    'entry_price': cand['entry_price'],
                    'sl_price': cand['sl_price'],
                    'target_price': cand['target_price'],
                    'atr': cand['atr'],
                    'hwm': cand['entry_price'],
                    'dynamic_stop': None,
                    'slots_used': slots_to_consume
                })
                
                trades_today += 1
                total_trades += 1
                stocks_traded_today.add(cand['symbol'])
                
                print(f"  -> [{time_str}] {cand['symbol']} | {cand['signal']} (Tier {cand['tier']}) Executed | Qty: {qty} | Entry: ₹{cand['entry_price']:.2f}")

        if trades_today > 0:
            days_with_trades += 1
            day_tag = "[PROFITABLE DAY]" if daily_pnl > 0 else "[LOSS DAY]"
            print(f"  💰 Day Result -> Net P&L: ₹{daily_pnl:.2f} | Taxes/Costs: ₹{daily_charges:.2f} | Balance: ₹{current_capital:.2f} {day_tag}")
        else:
            print("  💤 INACTIVE DAY - No setups met strict criteria.")
            inactive_days += 1
            
    print("\n" + "="*50)
    print("📊 BACKTEST SUMMARY")
    print(f"Starting Capital: ₹{capital_input:.2f}")
    print(f"Ending Capital: ₹{current_capital:.2f}")
    
    total_return_pct = ((current_capital - capital_input) / capital_input) * 100 if capital_input > 0 else 0
    print(f"Net Return: {total_return_pct:.2f}%")
    
    print(f"Total Statutory Charges Paid: ₹{total_charges:.2f}")
    print(f"Total Trading Days Analyzed: {total_days}")
    print(f"Days with Trades: {days_with_trades}")
    print(f"Inactive Days (Capital Protected): {inactive_days}")
    print(f"Total High-Probability Trades Fired: {total_trades}")
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    print(f"Win Rate: {win_rate:.2f}%")
    print("="*50)

if __name__ == "__main__":
    main()