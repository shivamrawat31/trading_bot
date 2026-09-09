import redis
import json
import logging
import datetime
from zoneinfo import ZoneInfo
from config import REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, INITIAL_SLOTS

logger = logging.getLogger(__name__)

class StateManager:
    def __init__(self):
        try:
            self.redis_client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                password=REDIS_PASSWORD,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5
            )
            self.redis_client.ping()
            logger.info("Connected to Redis successfully.")
        except redis.ConnectionError as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise

    def should_reset_for_new_day(self):
        """Checks if the state needs to be reset for a new trading day."""
        last_reset = self.redis_client.get("last_reset_date")
        today = datetime.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")
        return last_reset != today

    def mark_reset_done(self):
        """Marks the reset as completed for today."""
        today = datetime.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")
        self.redis_client.set("last_reset_date", today)

    def initialize_daily_state(self):
        """Initializes or resets the daily state."""
        self.redis_client.set("available_slots", json.dumps(INITIAL_SLOTS))
        self.redis_client.set("daily_pnl", 0.0)
        self.redis_client.set("pending_orders", json.dumps([]))
        self.redis_client.set("open_positions", json.dumps([]))
        self.redis_client.set("kill_switch_alerted", "false")
        self.redis_client.set("daily_trade_count", 0)
        self.mark_reset_done()
        logger.info("Daily state initialized/reset: available slots, daily pnl, pending orders, and open positions reset.")

    def get_available_slots(self):
        """Retrieves the list of available capital slots."""
        slots_json = self.redis_client.get("available_slots")
        if slots_json:
            return json.loads(slots_json)
        return []

    def allocate_slot(self, amount):
        """Removes a specific slot amount from available slots."""
        slots = self.get_available_slots()
        if amount in slots:
            slots.remove(amount)
            self.redis_client.set("available_slots", json.dumps(slots))
            return True
        return False

    def free_slot(self, amount):
        """Adds a slot amount back to available slots."""
        slots = self.get_available_slots()
        slots.append(amount)
        # Optional: Sort them descending so largest slots are first
        slots.sort(reverse=True)
        self.redis_client.set("available_slots", json.dumps(slots))

    def update_daily_pnl(self, realized_pnl):
        """Adds realized PnL to the daily total."""
        current_pnl = float(self.redis_client.get("daily_pnl") or 0.0)
        new_pnl = current_pnl + realized_pnl
        self.redis_client.set("daily_pnl", new_pnl)
        return new_pnl

    def get_daily_pnl(self):
        """Gets the current daily PnL."""
        return float(self.redis_client.get("daily_pnl") or 0.0)

    def lock_symbol(self, symbol):
        """Locks a symbol until the next trading day's MORNING_START."""
        from config import MORNING_START
        now = datetime.datetime.now(ZoneInfo("Asia/Kolkata"))
        h, m = map(int, MORNING_START.split(':'))
        next_morning = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if now >= next_morning:
            next_morning += datetime.timedelta(days=1)
        ttl = int((next_morning - now).total_seconds())
        self.redis_client.setex(f"locked_{symbol}", max(1, ttl), "locked")

    def is_symbol_locked(self, symbol):
        """Checks if a symbol is locked (one-and-done rule)."""
        return self.redis_client.exists(f"locked_{symbol}")
    
    def get_pending_orders(self):
        """Returns a list of pending orders tracking dicts."""
        data = self.redis_client.get("pending_orders")
        return json.loads(data) if data else []

    def save_pending_order(self, order_data):
        """Saves a pending order to Redis."""
        orders = self.get_pending_orders()
        orders.append(order_data)
        self.redis_client.set("pending_orders", json.dumps(orders))
        
    def remove_pending_order(self, order_id):
        """Removes a pending order from tracking."""
        orders = self.get_pending_orders()
        orders = [o for o in orders if o.get("order_id") != order_id]
        self.redis_client.set("pending_orders", json.dumps(orders))
        
    def get_open_positions(self):
        """Returns a list of open positions tracking dicts."""
        data = self.redis_client.get("open_positions")
        return json.loads(data) if data else []

    def save_open_position(self, pos_data):
        """Saves an open position to Redis."""
        positions = self.get_open_positions()
        positions.append(pos_data)
        self.redis_client.set("open_positions", json.dumps(positions))
        
    def remove_open_position(self, symbol):
        """Removes an open position from tracking."""
        positions = self.get_open_positions()
        positions = [p for p in positions if p.get("symbol") != symbol]
        self.redis_client.set("open_positions", json.dumps(positions))

    def has_alerted_kill_switch(self):
        """Checks if the kill switch alert has already been sent today."""
        return self.redis_client.get("kill_switch_alerted") == "true"

    def mark_kill_switch_alerted(self):
        """Marks that the kill switch alert has been sent."""
        self.redis_client.set("kill_switch_alerted", "true")

    def get_daily_trade_count(self):
        """Gets the daily trade count."""
        return int(self.redis_client.get("daily_trade_count") or 0)

    def increment_daily_trade_count(self):
        """Increments the daily trade count by 1."""
        self.redis_client.incr("daily_trade_count")
