import math
import logging
from config import MAX_RISK_PER_TRADE, LEVERAGE, BUFFER_PERCENT, DAILY_MAX_DRAWDOWN, MIN_RR_RATIO, DAILY_TARGET_PROFIT
import alerts

logger = logging.getLogger(__name__)

class RiskManager:
    def __init__(self, state_manager):
        self.state_manager = state_manager

    def check_kill_switch(self):
        """Checks if the daily max drawdown has been breached."""
        daily_pnl = self.state_manager.get_daily_pnl()
        
        if daily_pnl >= DAILY_TARGET_PROFIT:
            logger.info(f"DAILY TARGET REACHED! Daily PnL: {daily_pnl} >= {DAILY_TARGET_PROFIT}")
            alerts.send_alert(f"DAILY TARGET REACHED! Daily PnL: {daily_pnl} >= {DAILY_TARGET_PROFIT}", "info")
            return True
            
        if daily_pnl <= DAILY_MAX_DRAWDOWN:
            logger.warning(f"KILL SWITCH ENGAGED! Daily PnL: {daily_pnl} <= {DAILY_MAX_DRAWDOWN}")
            if not self.state_manager.has_alerted_kill_switch():
                alerts.send_alert(f"KILL SWITCH ENGAGED! Daily PnL: {daily_pnl} <= {DAILY_MAX_DRAWDOWN}", "critical")
                self.state_manager.mark_kill_switch_alerted()
            return True
        return False

    def calculate_trade_parameters(self, price, atr, capital_slot, signal, sl_price, target_price):
        """
        Calculates Limit Entry, absolute SL distance, absolute TP distance, and downsizes shares.
        Ensures strict max MAX_RISK_PER_TRADE risk.
        Returns a dictionary or None if rejected.
        """
        if self.check_kill_switch():
            return None

        # 1. Calculate Prices based on signal
        if signal == "BUY":
            raw_limit_price = price * (1 + BUFFER_PERCENT)
            side = 1
        elif signal == "SELL":
            raw_limit_price = price * (1 - BUFFER_PERCENT)
            side = -1
        else:
            return None
            
        limit_entry = round(raw_limit_price, 1)
        
        abs_sl = round(abs(limit_entry - sl_price), 1)
        abs_tp = round(abs(target_price - limit_entry), 1)
        
        if abs_sl <= 0 or abs_tp <= 0:
            logger.error(f"Invalid structural levels: abs_sl={abs_sl}, abs_tp={abs_tp}")
            return None
            
        rr_ratio = abs_tp / abs_sl
        if rr_ratio < MIN_RR_RATIO:
            logger.info(f"Trade rejected: Poor R:R ratio {rr_ratio:.2f} < {MIN_RR_RATIO}")
            return None
            
        risk_per_share = abs_sl

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
            "qty": final_shares,
            "side": side
        }
