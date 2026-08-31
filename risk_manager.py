import math
import logging
from config import MAX_RISK_PER_TRADE, LEVERAGE, BUFFER_PERCENT, DAILY_MAX_DRAWDOWN, SL_ATR_MULTIPLIER, TP_ATR_MULTIPLIER
import alerts

logger = logging.getLogger(__name__)

class RiskManager:
    def __init__(self, state_manager):
        self.state_manager = state_manager

    def check_kill_switch(self):
        """Checks if the daily max drawdown has been breached."""
        daily_pnl = self.state_manager.get_daily_pnl()
        if daily_pnl <= DAILY_MAX_DRAWDOWN:
            logger.warning(f"KILL SWITCH ENGAGED! Daily PnL: {daily_pnl} <= {DAILY_MAX_DRAWDOWN}")
            if not self.state_manager.has_alerted_kill_switch():
                alerts.send_alert(f"KILL SWITCH ENGAGED! Daily PnL: {daily_pnl} <= {DAILY_MAX_DRAWDOWN}", "critical")
                self.state_manager.mark_kill_switch_alerted()
            return True
        return False

    def calculate_trade_parameters(self, price, atr, capital_slot):
        """
        LONG-ONLY: this function assumes a long position. If short-side trading is ever added
        to strategy.py, this function needs new SL-above-entry / TP-below-entry math — do not
        just flip a flag without rewriting the price logic.
        
        Calculates Limit Entry, absolute SL distance, absolute TP distance, and downsizes shares.
        Ensures strict max ₹50 risk.
        Returns a dictionary or None if rejected.
        """
        if self.check_kill_switch():
            return None

        # 1. Calculate Prices
        # Entry with 0.1% buffer
        raw_limit_price = price * (1 + BUFFER_PERCENT)
        limit_entry = round(raw_limit_price, 1)
        
        # Absolute SL and TP distances
        raw_sl_distance = atr * SL_ATR_MULTIPLIER
        raw_tp_distance = atr * TP_ATR_MULTIPLIER
        
        abs_sl = round(raw_sl_distance, 1)
        abs_tp = round(raw_tp_distance, 1)
        
        # Risk per share is exactly the SL distance
        risk_per_share = abs_sl
        
        if risk_per_share <= 0:
            logger.error(f"Invalid risk per share calculated: {risk_per_share}")
            return None

        # 2. Downsize Logic
        max_shares_by_risk = math.floor(MAX_RISK_PER_TRADE / risk_per_share)
        
        max_exposure = capital_slot * LEVERAGE
        max_shares_by_margin = math.floor(max_exposure / limit_entry)
        
        # We take the minimum of the two constraints
        final_shares = min(max_shares_by_risk, max_shares_by_margin)
        
        if final_shares <= 0:
            logger.info("Trade rejected: Calculated shares dropped to 0 due to risk/margin limits.")
            return None
            
        return {
            "limit_entry": limit_entry,
            "stop_loss_abs": abs_sl,
            "take_profit_abs": abs_tp,
            "qty": final_shares
        }
