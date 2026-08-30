import redis
import json
import logging
from config import REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, INITIAL_SLOTS

logger = logging.getLogger(__name__)

class StateManager:
    def __init__(self):
        try:
            self.redis_client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                password=REDIS_PASSWORD,
                decode_responses=True
            )
            self.redis_client.ping()
            logger.info("Connected to Redis successfully.")
        except redis.ConnectionError as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise

    def initialize_daily_state(self):
        """Initializes or resets the daily state."""
        self.redis_client.set("available_slots", json.dumps(INITIAL_SLOTS))
        self.redis_client.set("daily_pnl", 0.0)
        self.redis_client.set("pending_orders", json.dumps([]))
        self.redis_client.set("open_positions", json.dumps([]))
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
        """Locks a symbol for the rest of the day using 24h expiry."""
        # 24 hours = 86400 seconds
        self.redis_client.setex(f"locked_{symbol}", 86400, "locked")

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

