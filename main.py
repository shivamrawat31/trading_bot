import time
import logging
import datetime
import json
from zoneinfo import ZoneInfo
from config import WATCHLIST, MORNING_START, MARKET_CLOSE, STALE_ORDER_MINUTES, ZOMBIE_TRADE_MINUTES, NSE_HOLIDAYS, INITIAL_SLOTS, LEVERAGE
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
        self.fyers = FyersClient()
        self.strategy = Strategy()
        self.risk_manager = RiskManager(self.state_manager)

    def _safe_authenticate(self, max_retries=5, retry_delay=15):
        """Attempts to authenticate with Fyers, with retries on failure."""
        for attempt in range(1, max_retries + 1):
            try:
                self.fyers.authenticate()
                return
            except Exception as e:
                logger.error(f"Authentication attempt {attempt} failed: {e}")
                if attempt < max_retries:
                    time.sleep(retry_delay * attempt)
                else:
                    alerts.send_alert(f"Critical: All {max_retries} auth attempts failed. Bot halting.", "critical")
                    raise

    def run(self):
        logger.info("Initializing Trading Engine...")
        self._safe_authenticate()
        alerts.send_alert("Trading bot started successfully", "info")
        
        if self.state_manager.should_reset_for_new_day():
            logger.info("New trading day detected — state reset.")
            self.state_manager.initialize_daily_state()
            
            try:
                funds_response = self.fyers.fyers.funds()
                if funds_response and funds_response.get("s") == "ok":
                    fund_limit = funds_response.get("fund_limit", [])
                    available_balance = 0
                    for item in fund_limit:
                        if item.get("title") == "Available Balance":
                            available_balance = item.get("equityAmount", 0)
                            break
                            
                    if available_balance > 2000:
                        dynamic_slots = [
                            int(available_balance * 0.40),
                            int(available_balance * 0.40),
                            int(available_balance * 0.20)
                        ]
                        import json
                        self.state_manager.redis_client.set("available_slots", json.dumps(dynamic_slots))
                        self.state_manager.redis_client.set("daily_initial_slots", json.dumps(dynamic_slots))
                        logger.info(f"Dynamically allocated slots based on balance {available_balance}: {dynamic_slots}")
            except Exception as e:
                logger.error(f"Failed to dynamically allocate slots: {e}")
        else:
            logger.info("Existing trading day state found — reconciling with broker instead of resetting.")
            self.reconcile_state_with_broker()
        
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
                if self.state_manager.should_reset_for_new_day():
                    self.state_manager.initialize_daily_state()
                self._safe_authenticate()
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
                if self.state_manager.should_reset_for_new_day():
                    self.state_manager.initialize_daily_state()
                self._safe_authenticate()
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
                if self.state_manager.should_reset_for_new_day():
                    self.state_manager.initialize_daily_state()
                self._safe_authenticate() # Re-auth for the new day
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
        live_positions_resp = self.fyers.get_positions()
        orderbook_resp = self.fyers.get_order_book()
        
        live_positions = {}
        if live_positions_resp and live_positions_resp.get("s") == "ok":
            live_positions = {p.get("symbol"): p for p in live_positions_resp.get("netPositions", []) if p.get("netQty") != 0}
            
        live_orders = {}
        if orderbook_resp and orderbook_resp.get("s") == "ok":
            live_orders = {o.get("id"): o for o in orderbook_resp.get("orderBook", []) if o.get("status") == 6}
            
        local_positions = self.state_manager.get_open_positions()
        local_orders = self.state_manager.get_pending_orders()
        
        import json
        daily_initial_slots_json = self.state_manager.redis_client.get("daily_initial_slots")
        if daily_initial_slots_json:
            daily_initial_slots = json.loads(daily_initial_slots_json)
        else:
            from config import INITIAL_SLOTS
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
                qty = abs(live_pos.get("netQty", 0))
                entry_price = live_pos.get("buyAvg", live_pos.get("sellAvg", 0))
                logger.critical(f"ORPHANED POSITION DETECTED: {symbol}, qty={qty} — not tracked in local state after restart")
                
                estimated = estimate_slot(qty, entry_price)
                
                # Add back to Redis with current time (zombie timer restarts)
                self.state_manager.save_open_position({
                    "symbol": symbol,
                    "timestamp": datetime.datetime.now(KOLKATA_TZ).isoformat(),
                    "slot_used": estimated,
                    "entry": entry_price
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
                logger.critical(f"ORPHANED PENDING ORDER DETECTED: {order_id} for {symbol}, qty={qty} — not tracked in local state after restart")
                
                estimated = estimate_slot(qty, limit_price)
                
                self.state_manager.save_pending_order({
                    "order_id": order_id,
                    "symbol": symbol,
                    "timestamp": datetime.datetime.now(KOLKATA_TZ).isoformat(),
                    "slot_used": estimated,
                    "limit_entry": limit_price
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
        orderbook_response = self.fyers.get_order_book()
        
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
                            if self.fyers.cancel_order(order_id):
                                self.state_manager.free_slot(p_order["slot_used"])
                                self.state_manager.remove_pending_order(order_id)
                    elif status in [2, 3, 4]: # Traded, Cancelled, Rejected
                        if status == 2: # Traded -> Move to open positions
                            self.state_manager.save_open_position({
                                "symbol": p_order["symbol"],
                                "timestamp": datetime.datetime.now(KOLKATA_TZ).isoformat(),
                                "slot_used": p_order["slot_used"],
                                "entry": p_order["limit_entry"]
                            })
                        else: # Cancelled/Rejected
                            self.state_manager.free_slot(p_order["slot_used"])
                        self.state_manager.remove_pending_order(order_id)

        # 2. Zombie Trade Exterminator & Position Sync (SL/TP Hit)
        open_positions = self.state_manager.get_open_positions()
        live_positions_resp = self.fyers.get_positions()
        
        if live_positions_resp and live_positions_resp.get("s") == "ok":
            # Map all positions, even closed ones, to find realized_profit for SL/TP hits
            all_positions = {p.get("symbol"): p for p in live_positions_resp.get("netPositions", []) if p.get("symbol")}
            
            for o_pos in open_positions:
                symbol = o_pos["symbol"]
                live_pos = all_positions.get(symbol)
                
                if live_pos and live_pos.get("netQty") != 0:
                    # Position is still open
                    pos_time = datetime.datetime.fromisoformat(o_pos["timestamp"])
                    if (datetime.datetime.now(KOLKATA_TZ) - pos_time).total_seconds() > ZOMBIE_TRADE_MINUTES * 60:
                        logger.info(f"Zombie Trade Exterminator: Exiting {symbol} (open > 90 mins)")
                        alerts.send_alert(f"Zombie Trade Exterminator: Force-exiting {symbol} (open > 90 mins)", "warning")
                        if self.fyers.exit_position(live_pos.get("id")):
                            self.state_manager.free_slot(o_pos["slot_used"])
                            self.state_manager.remove_open_position(symbol)
                            self.state_manager.lock_symbol(symbol)
                            realized = live_pos.get("realized_profit", 0)
                            self.state_manager.update_daily_pnl(realized)
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
            
        slots = self.state_manager.get_available_slots()
        if not slots:
            logger.info("No capital slots available. Skipping execution.")
            return
            
        triggered_stocks = []
        
        evaluated_count = 0
        skipped_symbols = []
        
        open_symbols = [p["symbol"] for p in self.state_manager.get_open_positions()]
        
        for symbol in WATCHLIST:
            if self.state_manager.is_symbol_locked(symbol):
                continue
                
            if symbol in open_symbols:
                continue
                
            time.sleep(1)
            df = self.fyers.get_historical_data(symbol)
            if df is None:
                skipped_symbols.append(symbol)
                continue
                
            try:
                result = self.strategy.evaluate(df)
                evaluated_count += 1
                if result and result["score"] >= 2:
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
            
        # The Tie-Breaker Logic: Sort by Score (DESC) then Relative Volume (DESC)
        triggered_stocks.sort(key=lambda x: (x["score"], x["rel_vol"]), reverse=True)
        logger.info(f"Signal Sort completed. Top candidate: {triggered_stocks[0]['symbol']}")
        
        # Sort slots descending (largest first)
        slots.sort(reverse=True)
        
        # Assign slots
        for stock in triggered_stocks:
            if not slots:
                break # Out of money
                
            score = stock["score"]
            slot_to_use = None
            
            # Score-based smart slot allocation rule
            if score == 3:
                slot_to_use = slots[0]
            elif score == 2:
                slot_to_use = slots[-1]
            
            if not slot_to_use:
                continue
            
            trade_params = self.risk_manager.calculate_trade_parameters(
                price=stock["price"],
                atr=stock["atr"],
                capital_slot=slot_to_use
            )
            
            if trade_params:
                order_id = self.fyers.place_bracket_order(
                    symbol=stock["symbol"],
                    qty=trade_params["qty"],
                    limit_price=trade_params["limit_entry"],
                    stop_loss_abs=trade_params["stop_loss_abs"],
                    take_profit_abs=trade_params["take_profit_abs"]
                )
                
                if order_id:
                    slots.remove(slot_to_use)
                    self.state_manager.allocate_slot(slot_to_use)
                    self.state_manager.save_pending_order({
                        "order_id": order_id,
                        "symbol": stock["symbol"],
                        "timestamp": datetime.datetime.now(KOLKATA_TZ).isoformat(),
                        "slot_used": slot_to_use,
                        "limit_entry": trade_params["limit_entry"]
                    })
                    logger.info(f"Order {order_id} placed for {stock['symbol']} using slot {slot_to_use}")

if __name__ == "__main__":
    bot = TradingBot()
    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot shutting down...")
