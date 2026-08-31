import time
import logging
import datetime
from config import WATCHLIST, MORNING_START, MARKET_CLOSE, STALE_ORDER_MINUTES, ZOMBIE_TRADE_MINUTES, NSE_HOLIDAYS
from fyers_client import FyersClient
from strategy import Strategy
from risk_manager import RiskManager
from state_manager import StateManager

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TradingBot:
    def __init__(self):
        self.state_manager = StateManager()
        self.state_manager.initialize_daily_state()
        self.fyers = FyersClient()
        self.strategy = Strategy()
        self.risk_manager = RiskManager(self.state_manager)

    def run(self):
        logger.info("Initializing Trading Engine...")
        self.fyers.authenticate()
        
        while True:
            self._handle_timing()
            
            # The Engine Tick (every 5 minutes)
            logger.info("Executing 5-minute Engine Tick...")
            try:
                self._sync_state_and_exterminators()
                self._execute_strategy()
            except Exception as e:
                logger.error(f"Error during tick: {e}")
            
            # Sleep until next 5 minute boundary
            now = datetime.datetime.now()
            sleep_seconds = 300 - (now.minute % 5) * 60 - now.second
            logger.info(f"Sleeping for {sleep_seconds} seconds until next candle...")
            time.sleep(max(1, sleep_seconds))

    def _handle_timing(self):
        """Handles the Morning Blindfold and the Railway Sleep Trap."""
        while True:
            now = datetime.datetime.now()
            
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
                self.state_manager.initialize_daily_state()
                self.fyers.authenticate()
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
                self.state_manager.initialize_daily_state()
                self.fyers.authenticate()
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
                self.state_manager.initialize_daily_state()
                self.fyers.authenticate() # Re-auth for the new day
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

    def _sync_state_and_exterminators(self):
        """Polls orderbook and positions to sync state, free slots, set lockouts, and run exterminators."""
        logger.info("Syncing state and running Exterminators...")
        
        # 1. Stale Order Exterminator & Order Sync
        pending_orders = self.state_manager.get_pending_orders()
        orderbook_response = self.fyers.get_order_book()
        
        if orderbook_response and orderbook_response.get("s") == "ok":
            live_orders = {o["id"]: o for o in orderbook_response.get("orderBook", [])}
            
            for p_order in pending_orders:
                order_id = p_order["order_id"]
                live_order = live_orders.get(order_id)
                
                if live_order:
                    status = live_order.get("status")
                    if status == 6:  # Pending
                        order_time = datetime.datetime.fromisoformat(p_order["timestamp"])
                        if (datetime.datetime.now() - order_time).total_seconds() > STALE_ORDER_MINUTES * 60:
                            logger.info(f"Stale Order Exterminator: Canceling {order_id}")
                            if self.fyers.cancel_order(order_id):
                                self.state_manager.free_slot(p_order["slot_used"])
                                self.state_manager.remove_pending_order(order_id)
                    elif status in [2, 3, 4]: # Traded, Cancelled, Rejected
                        if status == 2: # Traded -> Move to open positions
                            self.state_manager.save_open_position({
                                "symbol": p_order["symbol"],
                                "timestamp": datetime.datetime.now().isoformat(),
                                "slot_used": p_order["slot_used"],
                                "entry": p_order["limit_entry"],
                                "position_id": live_order.get("id")
                            })
                        else: # Cancelled/Rejected
                            self.state_manager.free_slot(p_order["slot_used"])
                        self.state_manager.remove_pending_order(order_id)

        # 2. Zombie Trade Exterminator & Position Sync (SL/TP Hit)
        open_positions = self.state_manager.get_open_positions()
        live_positions_resp = self.fyers.get_positions()
        
        if live_positions_resp and live_positions_resp.get("s") == "ok":
            # Map all positions, even closed ones, to find realized_profit for SL/TP hits
            all_positions = {p["symbol"]: p for p in live_positions_resp.get("netPositions", [])}
            
            for o_pos in open_positions:
                symbol = o_pos["symbol"]
                live_pos = all_positions.get(symbol)
                
                if live_pos and live_pos.get("netQty") != 0:
                    # Position is still open
                    pos_time = datetime.datetime.fromisoformat(o_pos["timestamp"])
                    if (datetime.datetime.now() - pos_time).total_seconds() > ZOMBIE_TRADE_MINUTES * 60:
                        logger.info(f"Zombie Trade Exterminator: Exiting {symbol} (open > 90 mins)")
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
        
        for symbol in WATCHLIST:
            if self.state_manager.is_symbol_locked(symbol):
                continue
                
            time.sleep(1)
            df = self.fyers.get_historical_data(symbol)
            if df is None:
                continue
                
            result = self.strategy.evaluate(df)
            if result and result["score"] >= 2:
                result["symbol"] = symbol
                triggered_stocks.append(result)
                
        if not triggered_stocks:
            return
            
        # The Tie-Breaker Logic
        triggered_stocks.sort(key=lambda x: x["rel_vol"], reverse=True)
        logger.info(f"Morning Rush Sort completed. Top candidate: {triggered_stocks[0]['symbol']}")
        
        # Sort slots descending (largest first)
        slots.sort(reverse=True)
        
        # Assign slots
        for stock in triggered_stocks:
            if not slots:
                break # Out of money
                
            score = stock["score"]
            # Score 3 needs 2k, Score 2 needs 1k. We just assign largest available.
            slot_to_use = slots[0] 
            
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
                    slots.pop(0)
                    self.state_manager.allocate_slot(slot_to_use)
                    self.state_manager.save_pending_order({
                        "order_id": order_id,
                        "symbol": stock["symbol"],
                        "timestamp": datetime.datetime.now().isoformat(),
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
