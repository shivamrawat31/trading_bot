import time
import logging
import datetime
import json
import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo
from config import WATCHLIST, MORNING_START, MARKET_CLOSE, GOLDEN_WINDOW_END, STALE_ORDER_MINUTES, ZOMBIE_TRADE_MINUTES, NSE_HOLIDAYS, INITIAL_SLOTS, LEVERAGE, HTF_TIMEFRAME, LTF_TIMEFRAME, MAX_DAILY_TRADES, TRAIL_ACTIVATION_ATR, TRAIL_DISTANCE_ATR, INDEX_SYMBOL, USE_INDEX_FILTER
from fyers_client import FyersClient
from strategy import Strategy
from risk_manager import RiskManager
from state_manager import StateManager
import alerts

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

KOLKATA_TZ = ZoneInfo("Asia/Kolkata")

class TradingBot:
    def __init__(self):
        self.state_manager = StateManager()
        self.broker = FyersClient()
        self.strategy = Strategy()
        self.risk_manager = RiskManager(self.state_manager)

    def _safe_authenticate(self, max_retries=5, retry_delay=15):
        """Attempts to authenticate with Fyers, with retries on failure."""
        for attempt in range(1, max_retries + 1):
            try:
                self.broker.authenticate()
                return
            except Exception as e:
                logger.error(f"Authentication attempt {attempt} failed: {e}")
                if attempt < max_retries:
                    time.sleep(retry_delay * attempt)
                else:
                    alerts.send_alert(f"Critical: All {max_retries} auth attempts failed. Bot halting.", "critical")
                    import sys
                    sys.exit(1)

    def _apply_dynamic_slot_allocation(self):
        """Fetches live broker funds and resizes slots dynamically if balance > 2000."""
        try:
            funds_response = self.broker.fyers.funds()
            if funds_response and funds_response.get("s") == "ok":
                fund_limit = funds_response.get("fund_limit", [])
                available_balance = 0
                for item in fund_limit:
                    if item.get("title") == "Available Balance":
                        available_balance = item.get("equityAmount", 0)
                        break
                        
                if available_balance > 2000:
                    dynamic_slots = [
                        int(available_balance * 0.50),
                        int(available_balance * 0.50)
                    ]
                    self.state_manager.redis_client.set("available_slots", json.dumps(dynamic_slots))
                    self.state_manager.redis_client.set("daily_initial_slots", json.dumps(dynamic_slots))
                    logger.info(f"Dynamically allocated slots based on balance {available_balance}: {dynamic_slots}")
        except Exception as e:
            logger.error(f"Failed to dynamically allocate slots: {e}")

    def run(self):
        logger.info("Initializing Trading Engine...")
        self._safe_authenticate()
        alerts.send_alert("Trading bot started successfully", "info")
        
        try:
            if self.state_manager.should_reset_for_new_day():
                logger.info("New trading day detected — state reset.")
                self.state_manager.initialize_daily_state()
                self._apply_dynamic_slot_allocation()
            else:
                logger.info("Existing trading day state found — reconciling with broker instead of resetting.")
                self.reconcile_state_with_broker()
        except Exception as e:
            logger.critical(f"Startup state reset/reconciliation FAILED: {e}")
            alerts.send_alert(f"Startup state reset/reconciliation FAILED: {e}. Bot cannot safely determine capital/position state and is halting rather than risking trading with unknown state.", "critical")
            raise
        
        while True:
            try:
                self._handle_timing()
            except Exception as e:
                logger.error(f"Error in timing handler: {e}")
                alerts.send_alert(f"Timing handler failed: {e}", "warning")
                time.sleep(10)
                continue
            
            # The Engine Tick (every 5 minutes)
            logger.info("Executing 5-minute Engine Tick...")
            try:
                self._sync_state_and_exterminators()
                self._execute_strategy()
            except Exception as e:
                logger.error(f"Error during tick: {e}")
                alerts.send_alert(f"Error during tick: {e}", "critical")
            
            # Sleep until next 5 minute boundary
            now = datetime.datetime.now(KOLKATA_TZ)
            sleep_seconds = 300 - (now.minute % 5) * 60 - now.second
            logger.info(f"Sleeping for {sleep_seconds} seconds until next candle...")
            time.sleep(max(1, sleep_seconds))

    def _handle_timing(self):
        """Handles the Morning Blindfold and the Railway Sleep Trap."""
        while True:
            now = datetime.datetime.now(KOLKATA_TZ)
            
            # 0. Weekend Sleep Trap
            if now.weekday() >= 5:
                logger.info("Weekend detected. Entering Sleep Trap until Monday morning...")
                days_ahead = 7 - now.weekday()
                monday = now + datetime.timedelta(days=days_ahead)
                next_morning = monday.replace(hour=int(MORNING_START.split(':')[0]), 
                                              minute=int(MORNING_START.split(':')[1]), 
                                              second=0, microsecond=0)
                sleep_secs = (next_morning - now).total_seconds()
                if sleep_secs > 0:
                    time.sleep(sleep_secs)
                
                # Wake up and reset state
                reset_occurred = self.state_manager.should_reset_for_new_day()
                if reset_occurred:
                    try:
                        self.state_manager.initialize_daily_state()
                    except Exception as e:
                        logger.critical(f"Startup state reset/reconciliation FAILED: {e}")
                        alerts.send_alert(f"Startup state reset/reconciliation FAILED: {e}. Bot cannot safely determine capital/position state and is halting rather than risking trading with unknown state.", "critical")
                        import sys
                        sys.exit(1)
                self._safe_authenticate()
                if reset_occurred:
                    self._apply_dynamic_slot_allocation()
                continue
                
            # 0.5 Holiday Sleep Trap
            if now.strftime("%Y-%m-%d") in NSE_HOLIDAYS:
                logger.info("Market Holiday detected. Entering Sleep Trap until tomorrow morning...")
                tomorrow = now + datetime.timedelta(days=1)
                next_morning = tomorrow.replace(hour=int(MORNING_START.split(':')[0]), 
                                                minute=int(MORNING_START.split(':')[1]), 
                                                second=0, microsecond=0)
                sleep_secs = (next_morning - now).total_seconds()
                if sleep_secs > 0:
                    time.sleep(sleep_secs)
                
                # Wake up and reset state
                reset_occurred = self.state_manager.should_reset_for_new_day()
                if reset_occurred:
                    try:
                        self.state_manager.initialize_daily_state()
                    except Exception as e:
                        logger.critical(f"Startup state reset/reconciliation FAILED: {e}")
                        alerts.send_alert(f"Startup state reset/reconciliation FAILED: {e}. Bot cannot safely determine capital/position state and is halting rather than risking trading with unknown state.", "critical")
                        import sys
                        sys.exit(1)
                self._safe_authenticate()
                if reset_occurred:
                    self._apply_dynamic_slot_allocation()
                continue

            current_time_str = now.strftime("%H:%M")
            
            # 1. Market Closed Sleep Trap
            if current_time_str >= MARKET_CLOSE:
                logger.info("Market Closed. Entering Sleep Trap to prevent Railway auto-restarts...")
                days_to_add = 3 if now.weekday() == 4 else 1
                tomorrow = now + datetime.timedelta(days=days_to_add)
                next_morning = tomorrow.replace(hour=int(MORNING_START.split(':')[0]), 
                                                minute=int(MORNING_START.split(':')[1]), 
                                                second=0, microsecond=0)
                sleep_secs = (next_morning - now).total_seconds()
                time.sleep(sleep_secs)
                
                # Wake up and reset state
                reset_occurred = self.state_manager.should_reset_for_new_day()
                if reset_occurred:
                    try:
                        self.state_manager.initialize_daily_state()
                    except Exception as e:
                        logger.critical(f"Startup state reset/reconciliation FAILED: {e}")
                        alerts.send_alert(f"Startup state reset/reconciliation FAILED: {e}. Bot cannot safely determine capital/position state and is halting rather than risking trading with unknown state.", "critical")
                        import sys
                        sys.exit(1)
                self._safe_authenticate() # Re-auth for the new day
                if reset_occurred:
                    self._apply_dynamic_slot_allocation()
                continue
                
            # 2. Morning Blindfold
            if current_time_str < MORNING_START:
                logger.info("Morning Blindfold Active. Waiting for 9:45 AM...")
                today_morning = now.replace(hour=int(MORNING_START.split(':')[0]), 
                                            minute=int(MORNING_START.split(':')[1]), 
                                            second=0, microsecond=0)
                sleep_secs = (today_morning - now).total_seconds()
                if sleep_secs > 0:
                    time.sleep(sleep_secs)
                continue
            
            break

    def reconcile_state_with_broker(self):
        """Reconciles local Redis state with live broker state after a mid-day restart."""
        logger.info("Starting mid-day reconciliation...")
        
        # 1. Fetch live data
        live_positions_resp = self.broker.get_positions()
        orderbook_resp = self.broker.get_order_book()
        
        live_positions = {}
        if live_positions_resp and live_positions_resp.get("s") == "ok":
            live_positions = {p.get("symbol"): p for p in live_positions_resp.get("netPositions", []) if p.get("netQty") != 0}
            
        live_orders = {}
        if orderbook_resp and orderbook_resp.get("s") == "ok":
            live_orders = {o.get("id"): o for o in orderbook_resp.get("orderBook", []) if o.get("status") == 6}
            
        local_positions = self.state_manager.get_open_positions()
        local_orders = self.state_manager.get_pending_orders()
        
        daily_initial_slots_json = self.state_manager.redis_client.get("daily_initial_slots")
        if daily_initial_slots_json:
            daily_initial_slots = json.loads(daily_initial_slots_json)
        else:
            daily_initial_slots = INITIAL_SLOTS.copy()

        def estimate_slot(qty, entry_price):
            approx_cap = (qty * entry_price) / LEVERAGE
            # Find closest match
            closest = min(daily_initial_slots, key=lambda x: abs(x - approx_cap))
            return closest
            
        recomputed_slots = daily_initial_slots.copy()
        
        # 2. Reconcile Positions
        local_pos_symbols = {p.get("symbol") for p in local_positions}
        
        for symbol, live_pos in live_positions.items():
            if symbol not in local_pos_symbols:
                net_qty = live_pos.get("netQty", 0)
                qty = abs(net_qty)
                entry_price = live_pos.get("buyAvg") or live_pos.get("sellAvg") or 0
                logger.critical(f"ORPHANED POSITION DETECTED: {symbol}, qty={qty} — not tracked in local state after restart")
                
                estimated = estimate_slot(qty, entry_price)
                
                side = 1 if net_qty > 0 else -1
                
                atr = 0.0
                try:
                    df_ltf = self.broker.get_history(symbol, resolution=LTF_TIMEFRAME)
                    if df_ltf is not None and len(df_ltf) >= 20:
                        high_low = df_ltf['high'] - df_ltf['low']
                        high_close = np.abs(df_ltf['high'] - df_ltf['close'].shift())
                        low_close = np.abs(df_ltf['low'] - df_ltf['close'].shift())
                        ranges = pd.concat([high_low, high_close, low_close], axis=1)
                        true_range = np.max(ranges, axis=1)
                        atr = float(true_range.ewm(alpha=1/14, min_periods=14, adjust=False).mean().iloc[-1])
                except Exception as e:
                    logger.error(f"Could not calculate ATR for {symbol} during reconciliation: {e}")
                
                # Add back to Redis with current time (zombie timer restarts)
                self.state_manager.save_open_position({
                    "symbol": symbol,
                    "timestamp": datetime.datetime.now(KOLKATA_TZ).isoformat(),
                    "slot_used": estimated,
                    "entry": entry_price,
                    "side": side,
                    "atr": atr,
                    "high_water_mark": entry_price
                })
                local_positions.append({
                    "symbol": symbol,
                    "slot_used": estimated
                })
                
        # Remove stale local positions
        for p in list(local_positions):
            if p.get("symbol") not in live_positions:
                logger.warning(f"STALE LOCAL STATE: {p.get('symbol')} tracked locally but not found on broker — removing from local state")
                self.state_manager.remove_open_position(p.get("symbol"))
                local_positions.remove(p)
                
        # 3. Reconcile Orders
        local_order_ids = {o.get("order_id") for o in local_orders}
        
        for order_id, live_order in live_orders.items():
            if order_id not in local_order_ids:
                symbol = live_order.get("symbol")
                qty = live_order.get("qty", 0)
                limit_price = live_order.get("limitPrice", 0)
                side = live_order.get("side", 1)
                logger.critical(f"ORPHANED PENDING ORDER DETECTED: {order_id} for {symbol}, qty={qty} — not tracked in local state after restart")
                
                estimated = estimate_slot(qty, limit_price)
                
                atr = 0.0
                try:
                    df_ltf = self.broker.get_history(symbol, resolution=LTF_TIMEFRAME)
                    if df_ltf is not None and len(df_ltf) >= 20:
                        high_low = df_ltf['high'] - df_ltf['low']
                        high_close = np.abs(df_ltf['high'] - df_ltf['close'].shift())
                        low_close = np.abs(df_ltf['low'] - df_ltf['close'].shift())
                        ranges = pd.concat([high_low, high_close, low_close], axis=1)
                        true_range = np.max(ranges, axis=1)
                        atr = float(true_range.ewm(alpha=1/14, min_periods=14, adjust=False).mean().iloc[-1])
                except Exception as e:
                    logger.error(f"Could not calculate ATR for {symbol} during reconciliation: {e}")
                
                self.state_manager.save_pending_order({
                    "order_id": order_id,
                    "symbol": symbol,
                    "timestamp": datetime.datetime.now(KOLKATA_TZ).isoformat(),
                    "slot_used": estimated,
                    "limit_entry": limit_price,
                    "side": side,
                    "atr": atr
                })
                local_orders.append({
                    "order_id": order_id,
                    "slot_used": estimated
                })
                
        # Remove stale local orders
        for o in list(local_orders):
            if o.get("order_id") not in live_orders:
                logger.warning(f"STALE LOCAL STATE: Pending order {o.get('order_id')} tracked locally but not found on broker — removing from local state")
                self.state_manager.remove_pending_order(o.get("order_id"))
                local_orders.remove(o)
                
        # 4. Recompute slots from scratch
        all_active_slots_needed = [p.get("slot_used") for p in local_positions if p.get("slot_used")] + [o.get("slot_used") for o in local_orders if o.get("slot_used")]
        
        for needed_slot in all_active_slots_needed:
            if needed_slot in recomputed_slots:
                recomputed_slots.remove(needed_slot)
            else:
                if recomputed_slots:
                    fallback_slot = min(recomputed_slots, key=lambda x: abs(x - needed_slot))
                    recomputed_slots.remove(fallback_slot)
                    logger.warning(f"Slot mismatch: Needed {needed_slot} but not available. Removed closest fallback {fallback_slot}. Manual verification recommended.")
                else:
                    logger.critical(f"CRITICAL: Out of capital slots during reconciliation trying to allocate {needed_slot}.")
        
        self.state_manager.redis_client.set("available_slots", json.dumps(recomputed_slots))
        logger.info(f"Reconciliation complete. Available slots recomputed: {recomputed_slots}")

    def _sync_state_and_exterminators(self):
        """Polls orderbook and positions to sync state, free slots, set lockouts, and run exterminators."""
        logger.info("Syncing state and running Exterminators...")
        
        # 1. Stale Order Exterminator & Order Sync
        pending_orders = self.state_manager.get_pending_orders()
        orderbook_response = self.broker.get_order_book()
        
        if orderbook_response and orderbook_response.get("s") == "ok":
            live_orders = {o.get("id"): o for o in orderbook_response.get("orderBook", []) if o.get("id")}
            
            for p_order in pending_orders:
                order_id = p_order["order_id"]
                live_order = live_orders.get(order_id)
                
                if live_order:
                    status = live_order.get("status")
                    if status == 6:  # Pending
                        order_time = datetime.datetime.fromisoformat(p_order["timestamp"])
                        if (datetime.datetime.now(KOLKATA_TZ) - order_time).total_seconds() > STALE_ORDER_MINUTES * 60:
                            logger.info(f"Stale Order Exterminator: Canceling {order_id}")
                            if self.broker.cancel_order(order_id):
                                self.state_manager.free_slot(p_order["slot_used"])
                                self.state_manager.remove_pending_order(order_id)
                    elif status in [2, 3, 4, 5]: # Traded, Cancelled, Modified, Rejected
                        if status == 5:
                            logger.warning(f"Order {order_id} was rejected by the broker! Freeing slot.")
                        if status == 2: # Traded -> Move to open positions
                            self.state_manager.save_open_position({
                                "symbol": p_order["symbol"],
                                "timestamp": datetime.datetime.now(KOLKATA_TZ).isoformat(),
                                "slot_used": p_order["slot_used"],
                                "entry": p_order["limit_entry"],
                                "side": p_order.get("side"),
                                "atr": p_order.get("atr"),
                                "high_water_mark": p_order["limit_entry"]
                            })
                        else: # Cancelled/Rejected
                            self.state_manager.free_slot(p_order["slot_used"])
                        self.state_manager.remove_pending_order(order_id)

        # 2. Zombie Trade Exterminator & Position Sync (SL/TP Hit)
        open_positions = self.state_manager.get_open_positions()
        live_positions_resp = self.broker.get_positions()
        
        if live_positions_resp and live_positions_resp.get("s") == "ok":
            # Map all positions, even closed ones, to find realized_profit for SL/TP hits
            all_positions = {p.get("symbol"): p for p in live_positions_resp.get("netPositions", []) if p.get("symbol")}
            
            for o_pos in open_positions:
                symbol = o_pos["symbol"]
                live_pos = all_positions.get(symbol)
                
                if live_pos and live_pos.get("netQty") != 0:
                    # Position is still open
                    
                    # 1. Ratchet Trail Logic
                    ltp = live_pos.get("ltp") or live_pos.get("recentPrice")
                    if ltp:
                        side = o_pos.get("side", 1)
                        hwm = o_pos.get("high_water_mark", o_pos["entry"])
                        entry = o_pos["entry"]
                        atr = o_pos.get("atr", 0.0)
                        
                        updated_hwm = False
                        
                        if side == 1 and ltp > hwm:
                            hwm = ltp
                            updated_hwm = True
                        elif side == -1 and ltp < hwm:
                            hwm = ltp
                            updated_hwm = True
                            
                        if updated_hwm:
                            o_pos["high_water_mark"] = hwm
                            self.state_manager.remove_open_position(symbol)
                            self.state_manager.save_open_position(o_pos)
                            
                        abs_profit = abs(hwm - entry)
                        
                        if atr > 0 and abs_profit >= TRAIL_ACTIVATION_ATR * atr:
                            trigger_exit = False
                            if side == 1:
                                dynamic_stop = hwm - (TRAIL_DISTANCE_ATR * atr)
                                if ltp <= dynamic_stop:
                                    trigger_exit = True
                            elif side == -1:
                                dynamic_stop = hwm + (TRAIL_DISTANCE_ATR * atr)
                                if ltp >= dynamic_stop:
                                    trigger_exit = True
                                    
                            if trigger_exit:
                                logger.info(f"Trailing Stop Triggered for {symbol}. Exiting position.")
                                alerts.send_alert(f"Trailing Stop Triggered for {symbol}. Exiting position.", "info")
                                if self.broker.exit_position(live_pos.get("id")):
                                    logger.info(f"Trailing Stop exit order sent for {symbol}. Will sync next tick.")
                                continue # Skip zombie check if we exited
                                
                    # 2. Zombie Exterminator
                    pos_time = datetime.datetime.fromisoformat(o_pos["timestamp"])
                    if (datetime.datetime.now(KOLKATA_TZ) - pos_time).total_seconds() > ZOMBIE_TRADE_MINUTES * 60:
                        logger.info(f"Zombie Trade Exterminator: Exiting {symbol} (open > 90 mins)")
                        alerts.send_alert(f"Zombie Trade Exterminator: Force-exiting {symbol} (open > 90 mins)", "warning")
                        if self.broker.exit_position(live_pos.get("id")):
                            logger.info(f"Exit order sent for {symbol}. PnL and state will sync on the next engine tick.")
                else:
                    # Position is closed (SL or TP hit)
                    logger.info(f"Position for {symbol} closed automatically (SL/TP). Syncing state...")
                    self.state_manager.free_slot(o_pos["slot_used"])
                    self.state_manager.remove_open_position(symbol)
                    self.state_manager.lock_symbol(symbol)
                    
                    if live_pos:
                        realized = live_pos.get("realized_profit", 0)
                        self.state_manager.update_daily_pnl(realized)
                        logger.info(f"Realized PnL of {realized} recorded for {symbol}.")

    def _execute_strategy(self):
        """Fetches data, evaluates signals, applies tie-breaker, and places orders."""
        if self.risk_manager.check_kill_switch():
            return
            
        if self.state_manager.get_daily_trade_count() >= MAX_DAILY_TRADES:
            logger.info("Max daily trades reached. Skipping execution.")
            return

        current_time_str = datetime.datetime.now(KOLKATA_TZ).strftime("%H:%M")
        if current_time_str >= GOLDEN_WINDOW_END:
            logger.info("Golden window has ended. Skipping execution for new setups.")
            return
            
        slots = self.state_manager.get_available_slots()
        if not slots:
            logger.info("No capital slots available. Skipping execution.")
            return
            
        triggered_stocks = []
        
        evaluated_count = 0
        skipped_symbols = []
        
        open_symbols = [p["symbol"] for p in self.state_manager.get_open_positions()]
        
        index_bias = "NEUTRAL"
        if USE_INDEX_FILTER:
            try:
                df_index = self.broker.get_history(INDEX_SYMBOL, resolution=LTF_TIMEFRAME)
                index_bias = self.strategy.evaluate_index_bias(df_index)
                logger.info(f"Broad Market Bias ({INDEX_SYMBOL}): {index_bias}")
            except Exception as e:
                logger.warning(f"Failed to fetch index bias, defaulting to NEUTRAL: {e}")
        
        for symbol in WATCHLIST:
            if self.state_manager.is_symbol_locked(symbol):
                continue
                
            if symbol in open_symbols:
                continue
                
            time.sleep(1)
            df_htf = self.broker.get_history(symbol, resolution=HTF_TIMEFRAME)
            time.sleep(0.3)
            df_ltf = self.broker.get_history(symbol, resolution=LTF_TIMEFRAME)
            
            if df_htf is None or df_ltf is None:
                skipped_symbols.append(symbol)
                continue
                
            try:
                htf_trend = self.strategy.determine_htf_trend(df_htf)
                result = self.strategy.calculate_signal(df_ltf, htf_trend)
                evaluated_count += 1
                if result and result["signal"] in ["BUY", "SELL"] and result["score"] >= 2:
                    if USE_INDEX_FILTER and index_bias != "NEUTRAL":
                        if result["signal"] == "BUY" and index_bias == "BEARISH":
                            logger.info(f"Skipping BUY on {symbol} due to Bearish Nifty bias.")
                            continue
                        if result["signal"] == "SELL" and index_bias == "BULLISH":
                            logger.info(f"Skipping SELL on {symbol} due to Bullish Nifty bias.")
                            continue

                    result["symbol"] = symbol
                    triggered_stocks.append(result)
            except Exception as e:
                logger.error(f"Exception evaluating strategy for {symbol}: {e}")
                skipped_symbols.append(symbol)
                
        total_attempted = evaluated_count + len(skipped_symbols)
        if skipped_symbols:
            logger.warning(f"Scanned {evaluated_count}/{total_attempted} symbols this tick, {len(skipped_symbols)} skipped due to errors: {skipped_symbols}")
        elif total_attempted > 0:
            logger.info(f"Scanned {evaluated_count}/{total_attempted} symbols this tick successfully.")
                
        if not triggered_stocks:
            return
            
        # The Tie-Breaker Logic: Sort by Relative Volume (DESC)
        triggered_stocks.sort(key=lambda x: x["rvol"], reverse=True)
        logger.info(f"Signal Sort completed. Top candidate: {triggered_stocks[0]['symbol']}")
        
        # Sort slots descending (largest first)
        slots.sort(reverse=True)
        
        # Assign slots
        for stock in triggered_stocks:
            if not slots:
                break # Out of money
                
            slot_to_use = slots[0]
            
            if not slot_to_use:
                continue
            
            trade_params = self.risk_manager.calculate_trade_parameters(
                price=stock["price"],
                atr=stock["atr"],
                capital_slot=slot_to_use,
                signal=stock["signal"],
                sl_price=stock["sl_price"],
                target_price=stock["target_price"]
            )
            
            if trade_params:
                order_id = self.broker.place_bracket_order(
                    symbol=stock["symbol"],
                    qty=trade_params["qty"],
                    limit_price=trade_params["limit_entry"],
                    stop_loss_abs=trade_params["stop_loss_abs"],
                    take_profit_abs=trade_params["take_profit_abs"],
                    side=trade_params["side"]
                )
                
                if order_id:
                    slots.remove(slot_to_use)
                    self.state_manager.allocate_slot(slot_to_use)
                    self.state_manager.increment_daily_trade_count()
                    self.state_manager.save_pending_order({
                        "order_id": order_id,
                        "symbol": stock["symbol"],
                        "timestamp": datetime.datetime.now(KOLKATA_TZ).isoformat(),
                        "slot_used": slot_to_use,
                        "limit_entry": trade_params["limit_entry"],
                        "side": trade_params["side"],
                        "atr": stock["atr"]
                    })
                    logger.info(f"Order {order_id} placed for {stock['symbol']} using slot {slot_to_use}")

if __name__ == "__main__":
    bot = TradingBot()
    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot shutting down...")
