#!/usr/bin/env python3
"""
IBKR MCP Server - Enhanced Version 3.0
=======================================
Complete implementation with Bracket Orders, Greeks, and all advanced features.
Compatible with current MCP SDK structure.
"""

import asyncio
import json
import logging
import threading
from typing import Dict, Any, Optional, List, Tuple, Union
from datetime import datetime, timedelta
from decimal import Decimal
from dataclasses import dataclass, field
from enum import Enum
import time

# MCP imports - corrected structure
try:
    from mcp.server import Server, NotificationOptions
    from mcp.server.models import InitializationOptions
    import mcp.server.stdio
    import mcp.types as types
except ImportError:
    # Fallback for different MCP versions
    from mcp import Server
    import mcp.types as types

# IBKR imports
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.common import TickerId, OrderId, BarData
from ibapi.order_state import OrderState
from ibapi.execution import Execution
from ibapi.commission_report import CommissionReport

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ibkr-mcp")

# ==========================================
# Constants and Configuration
# ==========================================

API_VERSION = "3.0.0"
DEFAULT_PORT = 7497  # Paper trading
LIVE_PORT = 7496
DEFAULT_CLIENT_ID = 1
CONNECTION_TIMEOUT = 10
MARKET_DATA_TIMEOUT = 5

# Tick type mappings
TICK_TYPES = {
    "BID_SIZE": 0,
    "BID_PRICE": 1,
    "ASK_PRICE": 2,
    "ASK_SIZE": 3,
    "LAST_PRICE": 4,
    "LAST_SIZE": 5,
    "HIGH": 6,
    "LOW": 7,
    "VOLUME": 8,
    "CLOSE": 9,
    "OPEN": 14,
    "MARK_PRICE": 37,
    "MODEL_OPTION_COMPUTATION": 13,
    "LAST_OPTION_COMPUTATION": 53
}

# Greeks tick types
GREEKS_TICK_TYPES = {
    "BID_OPTION_COMPUTATION": 10,
    "ASK_OPTION_COMPUTATION": 11,
    "LAST_OPTION_COMPUTATION": 12,
    "MODEL_OPTION_COMPUTATION": 13
}

# ==========================================
# Enhanced IB API Bridge
# ==========================================

class IBKRBridge(EWrapper, EClient):
    """
    Enhanced IB API Bridge with full async support,
    bracket orders, and Greeks computation.
    """
    
    def __init__(self):
        EClient.__init__(self, self)
        self.connected = False
        self.next_order_id = 1
        self.next_req_id = 1000
        
        # Data storage
        self.market_data: Dict[int, Dict[str, Any]] = {}
        self.orders: Dict[int, Dict[str, Any]] = {}
        self.positions: List[Dict[str, Any]] = []
        self.account_values: Dict[str, Any] = {}
        self.executions: List[Dict[str, Any]] = []
        self.option_chains: Dict[str, List[Dict[str, Any]]] = {}
        self.historical_data: Dict[int, List[Dict[str, Any]]] = {}
        self.pending_orders: Dict[int, Dict[str, Any]] = {}

        # New data storage for enhanced features
        self.realtime_bars: Dict[int, List[Dict[str, Any]]] = {}
        self.pnl_data: Dict[int, Dict[str, Any]] = {}
        self.pnl_single_data: Dict[int, Dict[str, Any]] = {}
        self.execution_details: Dict[int, List[Dict[str, Any]]] = {}
        self.commission_reports: Dict[str, Dict[str, Any]] = {}
        self.open_orders: List[Dict[str, Any]] = []
        self.option_params: Dict[int, List[Dict[str, Any]]] = {}
        self.contract_details_data: Dict[int, List[Dict[str, Any]]] = {}
        
        # Event management
        self.events: Dict[str, asyncio.Event] = {}
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.thread: Optional[threading.Thread] = None
        
        # Request tracking
        self.req_id_map: Dict[int, str] = {}
        
    def start_api(self):
        """Start the API thread."""
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        
    async def connect_async(self, host: str, port: int, client_id: int) -> bool:
        """Connect to TWS/IB Gateway asynchronously."""
        try:
            self.loop = asyncio.get_event_loop()
            self.events['connected'] = asyncio.Event()
            
            # Connect to TWS
            self.connect(host, port, client_id)
            self.start_api()
            
            # Wait for connection with timeout
            try:
                await asyncio.wait_for(
                    self.events['connected'].wait(),
                    timeout=CONNECTION_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.error("Connection timeout")
                return False
            
            logger.info(f"Connected successfully. Next order ID: {self.next_order_id}")
            return self.connected
            
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return False
    
    def disconnect_async(self):
        """Disconnect from TWS."""
        if self.connected:
            self.disconnect()
            self.connected = False
            if self.thread:
                try:
                    self.thread.join(timeout=2)
                except:
                    pass
            logger.info("Disconnected from TWS")
    
    # ==========================================
    # IB API Callbacks
    # ==========================================
    
    def error(self, reqId: TickerId, errorCode: int, errorString: str, advancedOrderRejectJson=""):
        """Handle errors from TWS."""
        if errorCode == 200:  # No security definition
            logger.warning(f"No security definition for request {reqId}")
        elif errorCode == 2104:  # Market data farm connection OK
            logger.info(errorString)
        elif errorCode == 2106:  # HMDS data farm connection OK
            logger.info(errorString)
        else:
            logger.error(f"Error {errorCode}: {errorString} (ReqId: {reqId})")
    
    def connectAck(self):
        """Acknowledge connection."""
        logger.info("Connection acknowledged")
        self.connected = True
    
    def nextValidId(self, orderId: int):
        """Receive next valid order ID."""
        self.next_order_id = orderId
        logger.info(f"Next valid order ID: {orderId}")
        
        # Signal connection complete
        if 'connected' in self.events and self.loop:
            self.loop.call_soon_threadsafe(self.events['connected'].set)
    
    def tickPrice(self, reqId: TickerId, tickType: int, price: float, attrib):
        """Receive tick price data."""
        if reqId not in self.market_data:
            self.market_data[reqId] = {}
        
        # Map tick type to field name
        tick_name = next((k for k, v in TICK_TYPES.items() if v == tickType), str(tickType))
        self.market_data[reqId][tick_name] = price
        self.market_data[reqId]['timestamp'] = datetime.now().isoformat()
        
        # Signal data update
        event_key = f"market_data_{reqId}"
        if event_key in self.events and self.loop:
            self.loop.call_soon_threadsafe(self.events[event_key].set)
    
    def tickSize(self, reqId: TickerId, tickType: int, size: int):
        """Receive tick size data."""
        if reqId not in self.market_data:
            self.market_data[reqId] = {}
        
        tick_name = next((k for k, v in TICK_TYPES.items() if v == tickType), str(tickType))
        self.market_data[reqId][tick_name] = size
    
    def tickOptionComputation(self, reqId: TickerId, tickType: int, tickAttrib: int,
                            impliedVol: float, delta: float, optPrice: float,
                            pvDividend: float, gamma: float, vega: float,
                            theta: float, undPrice: float):
        """Receive option Greeks."""
        if reqId not in self.market_data:
            self.market_data[reqId] = {}
        
        # Store Greeks if valid
        if tickType in [10, 11, 12, 13, 53]:  # Various option computation types
            greeks_data = {
                'implied_volatility': impliedVol if impliedVol is not None and impliedVol != -1 and impliedVol < 10 else None,
                'delta': delta if delta is not None and abs(delta) <= 1 else None,
                'gamma': gamma if gamma is not None and gamma != -2 else None,
                'vega': vega if vega is not None and vega != -2 else None,
                'theta': theta if theta is not None and theta != -2 else None,
                'option_price': optPrice if optPrice is not None and optPrice >= 0 else None,
                'underlying_price': undPrice if undPrice is not None and undPrice > 0 else None
            }
            
            # Update with valid values only
            self.market_data[reqId].update({k: v for k, v in greeks_data.items() if v is not None})
            
            # Signal Greeks received
            event_key = f"greeks_{reqId}"
            if event_key in self.events and self.loop:
                self.loop.call_soon_threadsafe(self.events[event_key].set)
    
    def orderStatus(self, orderId: OrderId, status: str, filled: float,
                   remaining: float, avgFillPrice: float, permId: int,
                   parentId: int, lastFillPrice: float, clientId: int,
                   whyHeld: str, mktCapPrice: float):
        """Receive order status updates."""
        self.orders[orderId] = {
            'order_id': orderId,
            'status': status,
            'filled': filled,
            'remaining': remaining,
            'avg_fill_price': avgFillPrice,
            'parent_id': parentId if parentId != 0 else None,
            'why_held': whyHeld if whyHeld else None,
            'timestamp': datetime.now().isoformat()
        }
        
        logger.info(f"Order {orderId} status: {status}, filled: {filled}")
        
        # Signal order update
        event_key = f"order_{orderId}"
        if event_key in self.events and self.loop:
            self.loop.call_soon_threadsafe(self.events[event_key].set)
    
    def openOrder(self, orderId: OrderId, contract: Contract, order: Order,
                 orderState: OrderState):
        """Receive open order details."""
        if orderId not in self.orders:
            self.orders[orderId] = {}
        
        self.orders[orderId].update({
            'order_id': orderId,
            'symbol': contract.symbol,
            'sec_type': contract.secType,
            'action': order.action,
            'order_type': order.orderType,
            'total_quantity': order.totalQuantity,
            'limit_price': order.lmtPrice if order.lmtPrice != 0 else None,
            'stop_price': order.auxPrice if order.auxPrice != 0 else None,
            'parent_id': order.parentId if order.parentId != 0 else None,
            'tif': order.tif,
            'transmit': order.transmit,
            'status': orderState.status
        })
    
    def position(self, account: str, contract: Contract, position: float,
                avgCost: float):
        """Receive position data."""
        pos_data = {
            'account': account,
            'symbol': contract.symbol,
            'sec_type': contract.secType,
            'currency': contract.currency,
            'position': position,
            'avg_cost': avgCost,
            'market_value': position * avgCost if position != 0 else 0
        }
        
        # Update or add position
        existing = next((p for p in self.positions 
                        if p['symbol'] == contract.symbol 
                        and p['account'] == account), None)
        if existing:
            existing.update(pos_data)
        else:
            self.positions.append(pos_data)
    
    def accountSummary(self, reqId: int, account: str, tag: str,
                      value: str, currency: str):
        """Receive account summary data."""
        if account not in self.account_values:
            self.account_values[account] = {}
        
        self.account_values[account][tag] = value
    
    def contractDetails(self, reqId: int, contractDetails):
        """Receive contract details (for option chains)."""
        contract = contractDetails.contract
        
        if contract.secType == "OPT":
            option_data = {
                'symbol': contract.symbol,
                'strike': contract.strike,
                'right': contract.right,
                'expiry': contract.lastTradeDateOrContractMonth,
                'multiplier': contract.multiplier,
                'exchange': contract.exchange,
                'con_id': contract.conId
            }
            
            key = f"chain_{reqId}"
            if key not in self.option_chains:
                self.option_chains[key] = []
            self.option_chains[key].append(option_data)
    
    def contractDetailsEnd(self, reqId: int):
        """Signal end of contract details."""
        event_key = f"contract_details_{reqId}"
        if event_key in self.events and self.loop:
            self.loop.call_soon_threadsafe(self.events[event_key].set)

    def historicalData(self, reqId: int, bar: BarData):
        """Receive historical bar data."""
        if reqId not in self.historical_data:
            self.historical_data[reqId] = []

        bar_data = {
            'date': bar.date,
            'open': bar.open,
            'high': bar.high,
            'low': bar.low,
            'close': bar.close,
            'volume': bar.volume,
            'wap': getattr(bar, 'wap', 0),  # Weighted average price (may not always be available)
            'bar_count': getattr(bar, 'barCount', 0)
        }
        self.historical_data[reqId].append(bar_data)

    def historicalDataEnd(self, reqId: int, start: str, end: str):
        """Signal end of historical data."""
        logger.info(f"Historical data complete for request {reqId}: {start} to {end}")
        event_key = f"historical_data_{reqId}"
        if event_key in self.events and self.loop:
            self.loop.call_soon_threadsafe(self.events[event_key].set)

    def realtimeBar(self, reqId: int, time: int, open_: float, high: float,
                    low: float, close: float, volume: int, wap: float, count: int):
        """Receive real-time 5-second bar."""
        if reqId not in self.realtime_bars:
            self.realtime_bars[reqId] = []

        bar_data = {
            'time': datetime.fromtimestamp(time).isoformat(),
            'open': open_,
            'high': high,
            'low': low,
            'close': close,
            'volume': volume,
            'wap': wap,
            'count': count
        }
        self.realtime_bars[reqId].append(bar_data)

        # Keep only last 100 bars to avoid memory issues
        if len(self.realtime_bars[reqId]) > 100:
            self.realtime_bars[reqId] = self.realtime_bars[reqId][-100:]

        # Signal new bar received
        event_key = f"realtime_bar_{reqId}"
        if event_key in self.events and self.loop:
            self.loop.call_soon_threadsafe(self.events[event_key].set)

    def pnl(self, reqId: int, dailyPnL: float, unrealizedPnL: float, realizedPnL: float):
        """Receive real-time P&L for account."""
        self.pnl_data[reqId] = {
            'daily_pnl': dailyPnL,
            'unrealized_pnl': unrealizedPnL,
            'realized_pnl': realizedPnL,
            'timestamp': datetime.now().isoformat()
        }

        # Signal P&L update
        event_key = f"pnl_{reqId}"
        if event_key in self.events and self.loop:
            self.loop.call_soon_threadsafe(self.events[event_key].set)

    def pnlSingle(self, reqId: int, pos: int, dailyPnL: float,
                  unrealizedPnL: float, realizedPnL: float, value: float):
        """Receive real-time P&L for single position."""
        self.pnl_single_data[reqId] = {
            'position': pos,
            'daily_pnl': dailyPnL,
            'unrealized_pnl': unrealizedPnL,
            'realized_pnl': realizedPnL,
            'value': value,
            'timestamp': datetime.now().isoformat()
        }

        # Signal P&L update
        event_key = f"pnl_single_{reqId}"
        if event_key in self.events and self.loop:
            self.loop.call_soon_threadsafe(self.events[event_key].set)

    def execDetails(self, reqId: int, contract: Contract, execution):
        """Receive execution details."""
        if reqId not in self.execution_details:
            self.execution_details[reqId] = []

        exec_data = {
            'exec_id': execution.execId,
            'order_id': execution.orderId,
            'symbol': contract.symbol,
            'sec_type': contract.secType,
            'time': execution.time,
            'account': execution.acctNumber,
            'exchange': execution.exchange,
            'side': execution.side,
            'shares': execution.shares,
            'price': execution.price,
            'perm_id': execution.permId,
            'client_id': execution.clientId,
            'liquidation': execution.liquidation,
            'cum_qty': execution.cumQty,
            'avg_price': execution.avgPrice
        }
        self.execution_details[reqId].append(exec_data)
        logger.info(f"Execution: {execution.side} {execution.shares} {contract.symbol} @ {execution.price}")

    def commissionReport(self, commissionReport):
        """Receive commission report for execution."""
        report_data = {
            'exec_id': commissionReport.execId,
            'commission': commissionReport.commission,
            'currency': commissionReport.currency,
            'realized_pnl': commissionReport.realizedPNL,
            'yield_': commissionReport.yield_,
            'yield_redemption_date': commissionReport.yieldRedemptionDate
        }
        self.commission_reports[commissionReport.execId] = report_data
        logger.info(f"Commission: {commissionReport.commission} {commissionReport.currency} for {commissionReport.execId}")

    def execDetailsEnd(self, reqId: int):
        """Signal end of execution details."""
        logger.info(f"Execution details complete for request {reqId}")
        event_key = f"exec_details_{reqId}"
        if event_key in self.events and self.loop:
            self.loop.call_soon_threadsafe(self.events[event_key].set)

    def securityDefinitionOptionParameter(self, reqId: int, exchange: str,
                                         underlyingConId: int, tradingClass: str,
                                         multiplier: str, expirations: set,
                                         strikes: set):
        """Receive option parameter definition."""
        if reqId not in self.option_params:
            self.option_params[reqId] = []

        param_data = {
            'exchange': exchange,
            'underlying_con_id': underlyingConId,
            'trading_class': tradingClass,
            'multiplier': multiplier,
            'expirations': sorted(list(expirations)),
            'strikes': sorted(list(strikes))
        }
        self.option_params[reqId].append(param_data)
        logger.info(f"Option params: {exchange} - {len(expirations)} expirations, {len(strikes)} strikes")

    def securityDefinitionOptionParameterEnd(self, reqId: int):
        """Signal end of option parameters."""
        logger.info(f"Option parameters complete for request {reqId}")
        event_key = f"option_params_{reqId}"
        if event_key in self.events and self.loop:
            self.loop.call_soon_threadsafe(self.events[event_key].set)

    def contractDetails(self, reqId: int, contractDetails):
        """Receive contract details (enhanced for both option chains and general contract details)."""
        contract = contractDetails.contract

        # Store detailed contract information
        detail_data = {
            'con_id': contract.conId,
            'symbol': contract.symbol,
            'sec_type': contract.secType,
            'exchange': contract.exchange,
            'primary_exchange': contract.primaryExchange,
            'currency': contract.currency,
            'local_symbol': contract.localSymbol,
            'trading_class': contract.tradingClass,
            'multiplier': contract.multiplier if contract.multiplier else "1",
            'min_tick': contractDetails.minTick,
            'order_types': contractDetails.orderTypes,
            'valid_exchanges': contractDetails.validExchanges,
            'contract_month': contract.lastTradeDateOrContractMonth,
            'trading_hours': contractDetails.tradingHours,
            'liquid_hours': contractDetails.liquidHours,
            'time_zone_id': contractDetails.timeZoneId,
            'long_name': contractDetails.longName,
            'industry': getattr(contractDetails, 'industry', ''),
            'category': getattr(contractDetails, 'category', ''),
            'subcategory': getattr(contractDetails, 'subcategory', '')
        }

        # For options, add option-specific fields
        if contract.secType == "OPT":
            detail_data.update({
                'strike': contract.strike,
                'right': contract.right,
                'expiry': contract.lastTradeDateOrContractMonth
            })

            # Also store in option_chains for backward compatibility
            option_data = {
                'symbol': contract.symbol,
                'strike': contract.strike,
                'right': contract.right,
                'expiry': contract.lastTradeDateOrContractMonth,
                'multiplier': contract.multiplier,
                'exchange': contract.exchange,
                'con_id': contract.conId
            }

            key = f"chain_{reqId}"
            if key not in self.option_chains:
                self.option_chains[key] = []
            self.option_chains[key].append(option_data)

        # Store in contract_details_data
        if reqId not in self.contract_details_data:
            self.contract_details_data[reqId] = []
        self.contract_details_data[reqId].append(detail_data)

    # contractDetailsEnd already exists, no need to duplicate

    # ==========================================
    # High-level async methods
    # ==========================================
    
    def get_next_order_id(self) -> int:
        """Get and increment next order ID."""
        order_id = self.next_order_id
        self.next_order_id += 1
        return order_id
    
    def get_next_req_id(self) -> int:
        """Get and increment next request ID."""
        req_id = self.next_req_id
        self.next_req_id += 1
        return req_id

    def fix_order_attributes(self, order: Order) -> Order:
        """
        Fix order attributes to prevent Error 10268, 10269, 10270.

        These deprecated attributes must be explicitly set to False
        for TWS versions 983+ to avoid errors:
        - Error 10268: The 'EtradeOnly' order attribute is not supported
        - Error 10269: The 'FirmQuoteOnly' order attribute is not supported
        - Error 10270: The 'NbboPriceCap' order attribute is not supported

        Args:
            order: Order object to fix

        Returns:
            Fixed order object
        """
        # Set deprecated attributes to False
        order.eTradeOnly = False
        order.firmQuoteOnly = False

        # Additional safe defaults
        if not hasattr(order, 'outsideRth'):
            order.outsideRth = False

        return order

    
    async def get_market_data_async(self, contract: Contract, 
                                   include_greeks: bool = False,
                                   timeout: float = MARKET_DATA_TIMEOUT) -> Dict[str, Any]:
        """Get market data for a contract asynchronously."""
        req_id = self.get_next_req_id()
        event_key = f"market_data_{req_id}"
        self.events[event_key] = asyncio.Event()
        
        # Store request type
        self.req_id_map[req_id] = contract.symbol
        
        try:
            # Request market data
            if include_greeks and contract.secType == "OPT":
                # Request option market data with Greeks computation
                self.reqMktData(req_id, contract, "100,101,104,106", False, False, [])
            else:
                # Regular market data
                self.reqMktData(req_id, contract, "", False, False, [])
            
            # Wait for data
            await asyncio.wait_for(
                self.events[event_key].wait(),
                timeout=timeout
            )
            
            # If Greeks requested, wait a bit more for computation
            if include_greeks:
                greeks_event = f"greeks_{req_id}"
                if greeks_event not in self.events:
                    self.events[greeks_event] = asyncio.Event()
                try:
                    await asyncio.wait_for(
                        self.events[greeks_event].wait(),
                        timeout=2.0
                    )
                except asyncio.TimeoutError:
                    pass  # Greeks might not be available
            
            # Cancel market data subscription
            self.cancelMktData(req_id)
            
            # Return data
            data = self.market_data.get(req_id, {})
            data['symbol'] = contract.symbol
            if contract.secType == "OPT":
                data['strike'] = contract.strike
                data['right'] = contract.right
                data['expiry'] = contract.lastTradeDateOrContractMonth
            
            return data
            
        except asyncio.TimeoutError:
            self.cancelMktData(req_id)
            logger.warning(f"Timeout getting market data for {contract.symbol}")
            return {}
        finally:
            self.events.pop(event_key, None)
            self.req_id_map.pop(req_id, None)
    
    async def place_order_async(self, contract: Contract, order: Order) -> int:
        """Place an order asynchronously."""
        order_id = order.orderId if order.orderId else self.get_next_order_id()
        order.orderId = order_id
        
        event_key = f"order_{order_id}"
        self.events[event_key] = asyncio.Event()
        
        # Store pending order
        self.pending_orders[order_id] = {
            'contract': contract,
            'order': order,
            'timestamp': datetime.now().isoformat()
        }
        
        try:
            # Fix deprecated order attributes
            order = self.fix_order_attributes(order)

            # Place order
            self.placeOrder(order_id, contract, order)
            
            # Wait for initial acknowledgment
            await asyncio.wait_for(
                self.events[event_key].wait(),
                timeout=5.0
            )
            
            logger.info(f"Order {order_id} placed successfully")
            return order_id
            
        except asyncio.TimeoutError:
            logger.warning(f"Order {order_id} submission timeout - may still be processing")
            return order_id
        finally:
            self.events.pop(event_key, None)
    
    async def place_bracket_order_async(self, contract: Contract, 
                                      parent_action: str, quantity: int,
                                      limit_price: Optional[float],
                                      take_profit_price: float,
                                      stop_loss_price: float,
                                      transmit: bool = True) -> Dict[str, int]:
        """
        Place a bracket order (parent + take profit + stop loss).
        Returns dict with all three order IDs.
        """
        # Get order IDs
        parent_id = self.get_next_order_id()
        tp_id = self.get_next_order_id()
        sl_id = self.get_next_order_id()
        
        # Parent order (entry)
        parent = Order()
        parent.orderId = parent_id
        parent.action = parent_action
        parent.orderType = "LMT" if limit_price else "MKT"
        parent.totalQuantity = quantity
        if limit_price:
            parent.lmtPrice = limit_price
        parent.transmit = False  # Don't transmit until all orders are created

        # Fix deprecated attributes for parent
        parent = self.fix_order_attributes(parent)
        
        # Take profit order (opposite action)
        take_profit = Order()
        take_profit.orderId = tp_id
        take_profit.action = "SELL" if parent_action == "BUY" else "BUY"
        take_profit.orderType = "LMT"
        take_profit.totalQuantity = quantity
        take_profit.lmtPrice = take_profit_price
        take_profit.parentId = parent_id
        take_profit.transmit = False

        # Fix deprecated attributes for take profit
        take_profit = self.fix_order_attributes(take_profit)
        
        # Stop loss order (opposite action)
        stop_loss = Order()
        stop_loss.orderId = sl_id
        stop_loss.action = "SELL" if parent_action == "BUY" else "BUY"
        stop_loss.orderType = "STP"
        stop_loss.totalQuantity = quantity
        stop_loss.auxPrice = stop_loss_price
        stop_loss.parentId = parent_id
        stop_loss.triggerMethod = 2  # 2 = last price
        stop_loss.transmit = transmit  # This transmits all three orders

        # Fix deprecated attributes for stop loss
        stop_loss = self.fix_order_attributes(stop_loss)
        
        # Place all three orders
        await self.place_order_async(contract, parent)
        await self.place_order_async(contract, take_profit)
        await self.place_order_async(contract, stop_loss)
        
        logger.info(f"Bracket order placed: Parent={parent_id}, TP={tp_id}, SL={sl_id}")
        
        return {
            'parent_id': parent_id,
            'take_profit_id': tp_id,
            'stop_loss_id': sl_id
        }

    async def place_vertical_spread_async(self,
                                         symbol: str,
                                         expiry: str,
                                         long_strike: float,
                                         short_strike: float,
                                         right: str,
                                         quantity: int,
                                         net_price: float,
                                         order_type: str = "LMT",
                                         exchange: str = "SMART",
                                         currency: str = "USD",
                                         trading_class: str = "",
                                         multiplier: str = "100",
                                         transmit: bool = True) -> Dict[str, Any]:
        """
        Place a vertical spread order using BAG contract (combo order).

        A vertical spread involves buying one option and selling another option
        of the same type (both calls or both puts) with the same expiration
        but different strikes.

        Args:
            symbol: Underlying symbol (e.g., "AAPL", "DAX")
            expiry: Option expiry date (YYYYMMDD format)
            long_strike: Strike price to BUY (lower for call spread, higher for put spread)
            short_strike: Strike price to SELL (higher for call spread, lower for put spread)
            right: "C" for call spread, "P" for put spread
            quantity: Number of spreads (each spread = 1 long + 1 short)
            net_price: Net debit/credit for the spread (positive = debit, negative = credit)
            order_type: "LMT" for limit order, "MKT" for market order
            exchange: Exchange (default "SMART")
            currency: Currency (default "USD", use "EUR" for DAX)
            trading_class: Trading class (e.g., "ODAX", "AAPL")
            multiplier: Option multiplier (default "100", use "5" for ODAX)
            transmit: If True, submit order immediately; if False, create preview order (default True)

        Returns:
            Dict with order_id and spread details

        Example - Bull Call Spread:
            long_strike=260, short_strike=265, right="C"
            Buy 260 Call, Sell 265 Call (bullish, max profit if above 265)

        Example - Bear Put Spread:
            long_strike=265, short_strike=260, right="P"
            Buy 265 Put, Sell 260 Put (bearish, max profit if below 260)
        """
        from ibapi.contract import Contract, ComboLeg
        from ibapi.order import Order

        logger.info(f"Placing vertical {right} spread: {symbol} {expiry} {long_strike}/{short_strike}")

        # Step 1: Get contract IDs for both legs
        # Long leg
        long_details = await self.get_contract_details_async(
            symbol=symbol,
            sec_type="OPT",
            exchange=exchange,
            currency=currency,
            strike=long_strike,
            right=right,
            expiry=expiry,
            timeout=5.0
        )

        if not long_details:
            raise ValueError(f"Could not find long leg: {symbol} {expiry} {long_strike} {right}")

        long_con_id = long_details[0]['con_id']
        logger.info(f"Long leg contract ID: {long_con_id}")

        # Short leg
        short_details = await self.get_contract_details_async(
            symbol=symbol,
            sec_type="OPT",
            exchange=exchange,
            currency=currency,
            strike=short_strike,
            right=right,
            expiry=expiry,
            timeout=5.0
        )

        if not short_details:
            raise ValueError(f"Could not find short leg: {symbol} {expiry} {short_strike} {right}")

        short_con_id = short_details[0]['con_id']
        logger.info(f"Short leg contract ID: {short_con_id}")

        # Step 2: Create BAG contract for the spread
        spread = Contract()
        spread.symbol = symbol
        spread.secType = "BAG"
        spread.currency = currency
        spread.exchange = exchange

        if trading_class:
            spread.tradingClass = trading_class

        # Create combo legs
        # Leg 1: Buy (long position)
        leg1 = ComboLeg()
        leg1.conId = long_con_id
        leg1.ratio = 1
        leg1.action = "BUY"
        leg1.exchange = exchange

        # Leg 2: Sell (short position)
        leg2 = ComboLeg()
        leg2.conId = short_con_id
        leg2.ratio = 1
        leg2.action = "SELL"
        leg2.exchange = exchange

        spread.comboLegs = [leg1, leg2]

        # Step 3: Create order
        order_id = self.get_next_order_id()

        order = Order()
        order.orderId = order_id
        order.action = "BUY"  # We're buying the spread (net debit for most spreads)
        order.orderType = order_type
        order.totalQuantity = quantity

        if order_type == "LMT":
            order.lmtPrice = abs(net_price)  # Net price for the spread

        order.transmit = transmit

        # Fix deprecated attributes
        order = self.fix_order_attributes(order)

        # Step 4: Place the combo order
        await self.place_order_async(spread, order)

        logger.info(f"Vertical spread order placed: Order ID {order_id}")

        return {
            'order_id': order_id,
            'spread_type': f"{right} Spread",
            'symbol': symbol,
            'expiry': expiry,
            'long_strike': long_strike,
            'short_strike': short_strike,
            'quantity': quantity,
            'net_price': net_price,
            'long_con_id': long_con_id,
            'short_con_id': short_con_id
        }

    async def get_option_chain_async(self, underlying: str, 
                                    expiry: Optional[str] = None,
                                    strike_range: Optional[Tuple[float, float]] = None,
                                    right: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get option chain for underlying."""
        req_id = self.get_next_req_id()
        event_key = f"contract_details_{req_id}"
        self.events[event_key] = asyncio.Event()
        
        # Create option contract for search
        contract = Contract()
        contract.symbol = underlying
        contract.secType = "OPT"
        contract.exchange = "SMART"
        contract.currency = "USD"
        
        if expiry:
            contract.lastTradeDateOrContractMonth = expiry
        
        if right:
            contract.right = right
        
        if strike_range:
            contract.strike = 0  # Will filter results later
        
        try:
            # Request contract details
            chain_key = f"chain_{req_id}"
            self.option_chains[chain_key] = []
            
            self.reqContractDetails(req_id, contract)
            
            # Wait for all contracts
            await asyncio.wait_for(
                self.events[event_key].wait(),
                timeout=10.0
            )
            
            # Get results
            options = self.option_chains.get(chain_key, [])
            
            # Filter by strike range if specified
            if strike_range and options:
                min_strike, max_strike = strike_range
                options = [opt for opt in options 
                          if min_strike <= opt['strike'] <= max_strike]
            
            # Filter by right if specified
            if right and options:
                options = [opt for opt in options if opt['right'] == right]
            
            return options
            
        except asyncio.TimeoutError:
            logger.warning(f"Timeout getting option chain for {underlying}")
            return []
        finally:
            self.events.pop(event_key, None)
            self.option_chains.pop(chain_key, None)

    async def get_historical_data_async(self, contract: Contract,
                                       end_datetime: str = "",
                                       duration_str: str = "1 D",
                                       bar_size: str = "1 hour",
                                       what_to_show: str = "TRADES",
                                       use_rth: int = 1,
                                       timeout: float = 30.0) -> List[Dict[str, Any]]:
        """
        Get historical bar data asynchronously.

        Args:
            contract: Contract to fetch data for
            end_datetime: End date/time (empty string = current moment)
            duration_str: Time period (e.g., "1 D", "1 W", "1 M")
            bar_size: Bar size (e.g., "1 min", "5 mins", "1 hour", "1 day")
            what_to_show: Data type (TRADES, MIDPOINT, BID, ASK, etc.)
            use_rth: 1=Regular Trading Hours only, 0=All hours
            timeout: Timeout in seconds

        Returns:
            List of bar data dictionaries
        """
        req_id = self.get_next_req_id()
        event_key = f"historical_data_{req_id}"
        self.events[event_key] = asyncio.Event()

        # Clear previous data
        self.historical_data[req_id] = []

        try:
            # Request historical data
            self.reqHistoricalData(
                reqId=req_id,
                contract=contract,
                endDateTime=end_datetime,
                durationStr=duration_str,
                barSizeSetting=bar_size,
                whatToShow=what_to_show,
                useRTH=use_rth,
                formatDate=1,
                keepUpToDate=False,
                chartOptions=[]
            )

            # Wait for data
            await asyncio.wait_for(
                self.events[event_key].wait(),
                timeout=timeout
            )

            # Return collected bars
            bars = self.historical_data.get(req_id, [])
            logger.info(f"Received {len(bars)} historical bars for {contract.symbol}")
            return bars

        except asyncio.TimeoutError:
            logger.warning(f"Timeout getting historical data for {contract.symbol}")
            return []
        finally:
            self.events.pop(event_key, None)
            self.historical_data.pop(req_id, None)

    async def subscribe_realtime_bars_async(self, contract: Contract,
                                           what_to_show: str = "TRADES",
                                           use_rth: bool = False) -> int:
        """
        Subscribe to real-time 5-second bars.

        Args:
            contract: Contract to subscribe to
            what_to_show: Data type (TRADES or MIDPOINT)
            use_rth: Regular trading hours only

        Returns:
            Request ID for this subscription
        """
        req_id = self.get_next_req_id()
        event_key = f"realtime_bar_{req_id}"
        self.events[event_key] = asyncio.Event()

        # Initialize storage
        self.realtime_bars[req_id] = []

        try:
            # Request real-time bars (5 seconds only)
            self.reqRealTimeBars(
                reqId=req_id,
                contract=contract,
                barSize=5,  # Fixed at 5 seconds
                whatToShow=what_to_show,
                useRTH=use_rth,
                realTimeBarsOptions=[]
            )

            # Wait for first bar
            await asyncio.wait_for(
                self.events[event_key].wait(),
                timeout=10.0
            )

            logger.info(f"Real-time bars subscription started for {contract.symbol} (req_id: {req_id})")
            return req_id

        except asyncio.TimeoutError:
            logger.warning(f"Timeout starting real-time bars for {contract.symbol}")
            return req_id

    def cancel_realtime_bars(self, req_id: int):
        """Cancel real-time bars subscription."""
        self.cancelRealTimeBars(req_id)
        self.realtime_bars.pop(req_id, None)
        self.events.pop(f"realtime_bar_{req_id}", None)
        logger.info(f"Real-time bars subscription cancelled (req_id: {req_id})")

    async def subscribe_pnl_async(self, account: str, model_code: str = "") -> int:
        """
        Subscribe to real-time P&L updates for account.

        Args:
            account: Account code
            model_code: Portfolio model code (empty if not applicable)

        Returns:
            Request ID for this subscription
        """
        req_id = self.get_next_req_id()
        event_key = f"pnl_{req_id}"
        self.events[event_key] = asyncio.Event()

        try:
            # Subscribe to P&L
            self.reqPnL(req_id, account, model_code)

            # Wait for first update
            await asyncio.wait_for(
                self.events[event_key].wait(),
                timeout=5.0
            )

            logger.info(f"P&L subscription started for account {account} (req_id: {req_id})")
            return req_id

        except asyncio.TimeoutError:
            logger.warning(f"Timeout starting P&L subscription for {account}")
            return req_id

    def cancel_pnl(self, req_id: int):
        """Cancel P&L subscription."""
        self.cancelPnL(req_id)
        self.pnl_data.pop(req_id, None)
        self.events.pop(f"pnl_{req_id}", None)
        logger.info(f"P&L subscription cancelled (req_id: {req_id})")

    async def subscribe_pnl_single_async(self, account: str, con_id: int,
                                        model_code: str = "") -> int:
        """
        Subscribe to real-time P&L for single position.

        Args:
            account: Account code
            con_id: Contract ID for the position
            model_code: Portfolio model code (empty if not applicable)

        Returns:
            Request ID for this subscription
        """
        req_id = self.get_next_req_id()
        event_key = f"pnl_single_{req_id}"
        self.events[event_key] = asyncio.Event()

        try:
            # Subscribe to position P&L
            self.reqPnLSingle(req_id, account, model_code, con_id)

            # Wait for first update
            await asyncio.wait_for(
                self.events[event_key].wait(),
                timeout=5.0
            )

            logger.info(f"Position P&L subscription started for conId {con_id} (req_id: {req_id})")
            return req_id

        except asyncio.TimeoutError:
            logger.warning(f"Timeout starting position P&L subscription")
            return req_id

    def cancel_pnl_single(self, req_id: int):
        """Cancel position P&L subscription."""
        self.cancelPnLSingle(req_id)
        self.pnl_single_data.pop(req_id, None)
        self.events.pop(f"pnl_single_{req_id}", None)
        logger.info(f"Position P&L subscription cancelled (req_id: {req_id})")

    async def get_executions_async(self, client_id: int = -1, acct_code: str = "",
                                  time_filter: str = "", symbol: str = "",
                                  sec_type: str = "", exchange: str = "",
                                  side: str = "", timeout: float = 10.0) -> Dict[str, Any]:
        """
        Get execution details with optional filters.

        Args:
            client_id: Filter by client ID (-1 = all)
            acct_code: Filter by account code
            time_filter: Filter by time (format: yyyymmdd-hh:mm:ss)
            symbol: Filter by symbol
            sec_type: Filter by security type
            exchange: Filter by exchange
            side: Filter by side (BOT/SLD)
            timeout: Timeout in seconds

        Returns:
            Dict with executions and commission reports
        """
        req_id = self.get_next_req_id()
        event_key = f"exec_details_{req_id}"
        self.events[event_key] = asyncio.Event()

        # Clear previous data
        self.execution_details[req_id] = []

        # Import ExecutionFilter
        from ibapi.execution import ExecutionFilter

        try:
            # Create filter
            exec_filter = ExecutionFilter()
            exec_filter.clientId = client_id
            exec_filter.acctCode = acct_code
            exec_filter.time = time_filter
            exec_filter.symbol = symbol
            exec_filter.secType = sec_type
            exec_filter.exchange = exchange
            exec_filter.side = side

            # Request executions
            self.reqExecutions(req_id, exec_filter)

            # Wait for all executions
            await asyncio.wait_for(
                self.events[event_key].wait(),
                timeout=timeout
            )

            # Get results with commissions
            executions = self.execution_details.get(req_id, [])

            # Add commission data to executions
            for exec_data in executions:
                exec_id = exec_data['exec_id']
                if exec_id in self.commission_reports:
                    exec_data['commission'] = self.commission_reports[exec_id]

            logger.info(f"Received {len(executions)} execution details")
            return {
                'executions': executions,
                'count': len(executions)
            }

        except asyncio.TimeoutError:
            logger.warning("Timeout getting execution details")
            return {'executions': [], 'count': 0}
        finally:
            self.events.pop(event_key, None)
            self.execution_details.pop(req_id, None)

    async def get_all_open_orders_async(self, timeout: float = 10.0) -> List[Dict[str, Any]]:
        """
        Get all open orders from all clients.

        Returns:
            List of all open orders
        """
        # Clear existing open orders
        self.open_orders = []

        # Request all open orders
        self.reqAllOpenOrders()

        # Wait a bit for orders to arrive
        await asyncio.sleep(2.0)

        logger.info(f"Retrieved {len(self.orders)} open orders")
        return list(self.orders.values())

    def cancel_all_orders_async(self):
        """Cancel all open orders (global cancel)."""
        self.reqGlobalCancel()
        logger.info("Global cancel requested - all orders will be cancelled")

    async def get_option_parameters_async(self, symbol: str, sec_type: str = "STK",
                                         exchange: str = "SMART",
                                         currency: str = "USD",
                                         timeout: float = 10.0) -> List[Dict[str, Any]]:
        """
        Get option parameters (available strikes and expirations) for an underlying.

        Args:
            symbol: Underlying symbol (e.g., "SPY", "AAPL", "DAX")
            sec_type: Security type (default: "STK")
            exchange: Exchange (default: "SMART")
            currency: Currency (default: "USD", use "EUR" for DAX, etc.)
            timeout: Timeout in seconds

        Returns:
            List of option parameter sets with expirations and strikes
        """
        # First, get the contract ID for the underlying
        logger.info(f"Getting contract ID for {symbol}...")
        details = await self.get_contract_details_async(
            symbol=symbol,
            sec_type=sec_type,
            exchange=exchange,
            currency=currency,
            timeout=5.0
        )

        if not details:
            logger.error(f"Could not find contract details for {symbol}")
            return []

        underlying_con_id = details[0]['con_id']
        logger.info(f"Found contract ID {underlying_con_id} for {symbol}")

        req_id = self.get_next_req_id()
        event_key = f"option_params_{req_id}"
        self.events[event_key] = asyncio.Event()

        # Clear previous data
        self.option_params[req_id] = []

        try:
            # Request option parameters with the valid contract ID
            self.reqSecDefOptParams(
                reqId=req_id,
                underlyingSymbol=symbol,
                futFopExchange="",
                underlyingSecType=sec_type,
                underlyingConId=underlying_con_id
            )

            # Wait for parameters
            await asyncio.wait_for(
                self.events[event_key].wait(),
                timeout=timeout
            )

            # Return collected parameters
            params = self.option_params.get(req_id, [])
            logger.info(f"Received option parameters for {symbol}: {len(params)} exchange(s)")
            return params

        except asyncio.TimeoutError:
            logger.warning(f"Timeout getting option parameters for {symbol}")
            return []
        finally:
            self.events.pop(event_key, None)
            self.option_params.pop(req_id, None)

    async def get_contract_details_async(self, symbol: str, sec_type: str = "STK",
                                        exchange: str = "SMART", currency: str = "USD",
                                        strike: float = 0.0, right: str = "",
                                        expiry: str = "",
                                        timeout: float = 10.0) -> List[Dict[str, Any]]:
        """
        Get detailed contract information.

        Args:
            symbol: Symbol to get details for
            sec_type: Security type (STK, OPT, FUT, etc.)
            exchange: Exchange
            currency: Currency
            strike: Strike price (for options)
            right: Option right (C/P for options)
            expiry: Expiration date (for options/futures, format: YYYYMMDD)
            timeout: Timeout in seconds

        Returns:
            List of contract details matching criteria
        """
        req_id = self.get_next_req_id()
        event_key = f"contract_details_{req_id}"
        self.events[event_key] = asyncio.Event()

        # Clear previous data
        self.contract_details_data[req_id] = []

        try:
            # Create contract for search
            contract = Contract()
            contract.symbol = symbol
            contract.secType = sec_type
            contract.exchange = exchange
            contract.currency = currency

            # For options
            if sec_type == "OPT":
                if strike > 0:
                    contract.strike = strike
                if right:
                    contract.right = right
                if expiry:
                    contract.lastTradeDateOrContractMonth = expiry

            # For futures
            if sec_type == "FUT" and expiry:
                contract.lastTradeDateOrContractMonth = expiry

            # Request contract details
            self.reqContractDetails(req_id, contract)

            # Wait for details
            await asyncio.wait_for(
                self.events[event_key].wait(),
                timeout=timeout
            )

            # Return collected details
            details = self.contract_details_data.get(req_id, [])
            logger.info(f"Received {len(details)} contract detail(s) for {symbol}")
            return details

        except asyncio.TimeoutError:
            logger.warning(f"Timeout getting contract details for {symbol}")
            return []
        finally:
            self.events.pop(event_key, None)
            self.contract_details_data.pop(req_id, None)

# ==========================================
# Global Bridge Instance
# ==========================================

bridge = IBKRBridge()

# ==========================================
# MCP Server Implementation
# ==========================================

# Create server instance
server = Server("ibkr-mcp")

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """List all available tools."""
    return [
        types.Tool(
            name="ibkr_connect",
            description="Connect to TWS/IB Gateway. Must be called before using other tools.",
            inputSchema={
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["paper", "live"],
                        "default": "paper",
                        "description": "Connection mode: paper (7497) or live (7496)"
                    },
                    "client_id": {
                        "type": "integer",
                        "default": 1,
                        "minimum": 0,
                        "maximum": 999,
                        "description": "Client ID for connection"
                    },
                    "host": {
                        "type": "string",
                        "default": "127.0.0.1",
                        "description": "TWS host address"
                    }
                }
            }
        ),
        types.Tool(
            name="ibkr_disconnect",
            description="Disconnect from TWS/IB Gateway",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        types.Tool(
            name="ibkr_connection_status",
            description="Check TWS connection status",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        types.Tool(
            name="ibkr_get_quote",
            description="Get real-time quote for stock/index/option with optional Greeks",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Symbol to quote (e.g., SPY, AAPL, DAX, SPX)"
                    },
                    "sec_type": {
                        "type": "string",
                        "enum": ["STK", "IND", "OPT"],
                        "default": "STK",
                        "description": "Security type"
                    },
                    "exchange": {
                        "type": "string",
                        "default": "SMART",
                        "description": "Exchange (SMART for US, EUREX for DAX, CBOE for SPX)"
                    },
                    "currency": {
                        "type": "string",
                        "default": "USD",
                        "description": "Currency (USD for US stocks/SPX, EUR for DAX)"
                    },
                    "include_greeks": {
                        "type": "boolean",
                        "default": False,
                        "description": "Include Greeks for options"
                    }
                },
                "required": ["symbol"]
            }
        ),
        types.Tool(
            name="ibkr_get_option_chain",
            description="Get option chain with strikes, expiries, and optional Greeks",
            inputSchema={
                "type": "object",
                "properties": {
                    "underlying": {
                        "type": "string",
                        "description": "Underlying symbol (e.g., SPY, SPX, DAX)"
                    },
                    "expiry": {
                        "type": "string",
                        "description": "Expiry date YYYYMMDD (null for 0DTE)"
                    },
                    "min_strike": {
                        "type": "number",
                        "description": "Minimum strike price"
                    },
                    "max_strike": {
                        "type": "number",
                        "description": "Maximum strike price"
                    },
                    "right": {
                        "type": "string",
                        "enum": ["C", "P"],
                        "description": "Option right (C=Call, P=Put, null=both)"
                    },
                    "exchange": {
                        "type": "string",
                        "default": "SMART",
                        "description": "Exchange (SMART for US options, EUREX for DAX options)"
                    },
                    "currency": {
                        "type": "string",
                        "default": "USD",
                        "description": "Currency (USD for US options, EUR for DAX options)"
                    },
                    "trading_class": {
                        "type": "string",
                        "description": "Trading class (e.g., ODAX for DAX options, SPXW for SPX weeklies)"
                    },
                    "include_greeks": {
                        "type": "boolean",
                        "default": False,
                        "description": "Fetch Greeks for each option"
                    }
                },
                "required": ["underlying"]
            }
        ),
        types.Tool(
            name="ibkr_place_order",
            description="Place a market or limit order",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Symbol to trade"
                    },
                    "action": {
                        "type": "string",
                        "enum": ["BUY", "SELL"],
                        "description": "Order action"
                    },
                    "quantity": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Order quantity"
                    },
                    "order_type": {
                        "type": "string",
                        "enum": ["MKT", "LMT", "STP"],
                        "default": "MKT",
                        "description": "Order type"
                    },
                    "limit_price": {
                        "type": "number",
                        "description": "Limit price (required for LMT orders)"
                    },
                    "stop_price": {
                        "type": "number",
                        "description": "Stop price (required for STP orders)"
                    },
                    "sec_type": {
                        "type": "string",
                        "enum": ["STK", "OPT", "FUT"],
                        "default": "STK",
                        "description": "Security type"
                    },
                    "transmit": {
                        "type": "boolean",
                        "default": False,
                        "description": "Auto-transmit order (false=manual confirmation in TWS)"
                    }
                },
                "required": ["symbol", "action", "quantity"]
            }
        ),
        types.Tool(
            name="ibkr_place_bracket_order",
            description="Place bracket order with entry + take profit + stop loss",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Symbol to trade"
                    },
                    "action": {
                        "type": "string",
                        "enum": ["BUY", "SELL"],
                        "description": "Initial order action"
                    },
                    "quantity": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Order quantity"
                    },
                    "entry_price": {
                        "type": "number",
                        "description": "Entry limit price (null for market entry)"
                    },
                    "take_profit_price": {
                        "type": "number",
                        "description": "Take profit limit price"
                    },
                    "stop_loss_price": {
                        "type": "number",
                        "description": "Stop loss trigger price"
                    },
                    "sec_type": {
                        "type": "string",
                        "enum": ["STK", "OPT", "FUT"],
                        "default": "STK",
                        "description": "Security type"
                    },
                    "transmit": {
                        "type": "boolean",
                        "default": False,
                        "description": "Auto-transmit all orders"
                    }
                },
                "required": ["symbol", "action", "quantity", "take_profit_price", "stop_loss_price"]
            }
        ),
        types.Tool(
            name="ibkr_place_vertical_spread",
            description="Place a vertical spread order (buy one strike, sell another) as a single combo order",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Underlying symbol (e.g., AAPL, DAX, SPX)"
                    },
                    "expiry": {
                        "type": "string",
                        "description": "Option expiry date in YYYYMMDD format (e.g., 20251024)"
                    },
                    "long_strike": {
                        "type": "number",
                        "description": "Strike price to BUY (lower for call spread, higher for put spread)"
                    },
                    "short_strike": {
                        "type": "number",
                        "description": "Strike price to SELL (higher for call spread, lower for put spread)"
                    },
                    "right": {
                        "type": "string",
                        "enum": ["C", "P"],
                        "description": "C for call spread (bullish), P for put spread (bearish)"
                    },
                    "quantity": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Number of spreads to trade"
                    },
                    "net_price": {
                        "type": "number",
                        "description": "Net debit/credit for the spread (e.g., 2.50 for $2.50 debit)"
                    },
                    "order_type": {
                        "type": "string",
                        "enum": ["LMT", "MKT"],
                        "default": "LMT",
                        "description": "Order type (LMT for limit, MKT for market)"
                    },
                    "exchange": {
                        "type": "string",
                        "default": "SMART",
                        "description": "Exchange (SMART for US, EUREX for DAX)"
                    },
                    "currency": {
                        "type": "string",
                        "default": "USD",
                        "description": "Currency (USD for US, EUR for DAX)"
                    },
                    "trading_class": {
                        "type": "string",
                        "description": "Trading class (e.g., ODAX for DAX, leave empty for US options)"
                    },
                    "multiplier": {
                        "type": "string",
                        "default": "100",
                        "description": "Option multiplier (100 for US, 5 for ODAX)"
                    },
                    "transmit": {
                        "type": "boolean",
                        "default": True,
                        "description": "If true, submit order immediately; if false, create preview order (allows review before submission)"
                    }
                },
                "required": ["symbol", "expiry", "long_strike", "short_strike", "right", "quantity", "net_price"]
            }
        ),
        types.Tool(
            name="ibkr_get_order_status",
            description="Get status of a specific order",
            inputSchema={
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "integer",
                        "description": "Order ID to check"
                    }
                },
                "required": ["order_id"]
            }
        ),
        types.Tool(
            name="ibkr_cancel_order",
            description="Cancel a pending order",
            inputSchema={
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "integer",
                        "description": "Order ID to cancel"
                    }
                },
                "required": ["order_id"]
            }
        ),
        types.Tool(
            name="ibkr_get_positions",
            description="Get all current positions",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        types.Tool(
            name="ibkr_get_account_summary",
            description="Get account summary with buying power and balances",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        types.Tool(
            name="ibkr_get_historical_data",
            description="Get historical OHLCV candlestick data for charting and backtesting",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Symbol to get historical data for"
                    },
                    "sec_type": {
                        "type": "string",
                        "enum": ["STK", "IND", "OPT", "FUT"],
                        "default": "STK",
                        "description": "Security type"
                    },
                    "bar_size": {
                        "type": "string",
                        "enum": ["1 min", "5 mins", "15 mins", "30 mins", "1 hour", "2 hours", "4 hours", "1 day", "1 week"],
                        "default": "1 hour",
                        "description": "Bar size/granularity"
                    },
                    "duration": {
                        "type": "string",
                        "enum": ["1 D", "2 D", "1 W", "1 M", "3 M", "6 M", "1 Y"],
                        "default": "1 D",
                        "description": "Time period (D=Day, W=Week, M=Month, Y=Year)"
                    },
                    "what_to_show": {
                        "type": "string",
                        "enum": ["TRADES", "MIDPOINT", "BID", "ASK", "BID_ASK"],
                        "default": "TRADES",
                        "description": "Data type to fetch"
                    },
                    "use_rth": {
                        "type": "boolean",
                        "default": True,
                        "description": "Regular trading hours only (true) or all hours (false)"
                    }
                },
                "required": ["symbol"]
            }
        ),
        types.Tool(
            name="ibkr_subscribe_realtime_bars",
            description="Subscribe to real-time 5-second OHLCV bars (live charting)",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Symbol to subscribe to"
                    },
                    "sec_type": {
                        "type": "string",
                        "enum": ["STK", "IND", "OPT", "FUT"],
                        "default": "STK",
                        "description": "Security type"
                    },
                    "what_to_show": {
                        "type": "string",
                        "enum": ["TRADES", "MIDPOINT"],
                        "default": "TRADES",
                        "description": "Data type (TRADES required for volume)"
                    },
                    "use_rth": {
                        "type": "boolean",
                        "default": False,
                        "description": "Regular trading hours only"
                    }
                },
                "required": ["symbol"]
            }
        ),
        types.Tool(
            name="ibkr_cancel_realtime_bars",
            description="Cancel real-time bars subscription",
            inputSchema={
                "type": "object",
                "properties": {
                    "req_id": {
                        "type": "integer",
                        "description": "Request ID from subscribe_realtime_bars"
                    }
                },
                "required": ["req_id"]
            }
        ),
        types.Tool(
            name="ibkr_subscribe_pnl",
            description="Subscribe to real-time P&L updates for entire account",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {
                        "type": "string",
                        "description": "Account code (optional, will use default)"
                    }
                }
            }
        ),
        types.Tool(
            name="ibkr_subscribe_pnl_single",
            description="Subscribe to real-time P&L for specific position",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {
                        "type": "string",
                        "description": "Account code"
                    },
                    "con_id": {
                        "type": "integer",
                        "description": "Contract ID of the position"
                    }
                },
                "required": ["con_id"]
            }
        ),
        types.Tool(
            name="ibkr_get_executions",
            description="Get today's execution details with commissions and P&L",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Filter by symbol (optional)"
                    },
                    "side": {
                        "type": "string",
                        "enum": ["BOT", "SLD"],
                        "description": "Filter by side: BOT=Buy, SLD=Sell (optional)"
                    },
                    "client_id": {
                        "type": "integer",
                        "description": "Filter by client ID (optional)"
                    }
                }
            }
        ),
        types.Tool(
            name="ibkr_get_all_open_orders",
            description="Get all open orders from all clients (better overview than single order status)",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        types.Tool(
            name="ibkr_cancel_all_orders",
            description="EMERGENCY: Cancel ALL open orders globally (from API and TWS)",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        types.Tool(
            name="ibkr_get_option_params",
            description="Get available option strikes and expirations for an underlying (option chain discovery)",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Underlying symbol (e.g., SPY, AAPL, SPX, DAX)"
                    },
                    "sec_type": {
                        "type": "string",
                        "enum": ["STK", "IND"],
                        "default": "STK",
                        "description": "Underlying security type"
                    },
                    "exchange": {
                        "type": "string",
                        "default": "SMART",
                        "description": "Exchange (SMART for US, EUREX for DAX, CBOE for SPX)"
                    },
                    "currency": {
                        "type": "string",
                        "default": "USD",
                        "description": "Currency (USD for US, EUR for DAX)"
                    }
                },
                "required": ["symbol"]
            }
        ),
        types.Tool(
            name="ibkr_get_contract_details",
            description="Get detailed contract information (conId, multiplier, trading hours, etc.)",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Symbol to get details for"
                    },
                    "sec_type": {
                        "type": "string",
                        "enum": ["STK", "OPT", "FUT", "IND", "CASH"],
                        "default": "STK",
                        "description": "Security type"
                    },
                    "exchange": {
                        "type": "string",
                        "default": "SMART",
                        "description": "Exchange"
                    },
                    "currency": {
                        "type": "string",
                        "default": "USD",
                        "description": "Currency"
                    },
                    "strike": {
                        "type": "number",
                        "description": "Strike price (for options only)"
                    },
                    "right": {
                        "type": "string",
                        "enum": ["C", "P"],
                        "description": "Option right: C=Call, P=Put (for options only)"
                    },
                    "expiry": {
                        "type": "string",
                        "description": "Expiration date YYYYMMDD (for options/futures)"
                    }
                },
                "required": ["symbol"]
            }
        )
    ]

@server.call_tool()
async def handle_call_tool(
    name: str,
    arguments: dict | None
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    """Handle tool calls."""
    
    try:
        # ==========================================
        # Connection Tools
        # ==========================================
        
        if name == "ibkr_connect":
            mode = arguments.get("mode", "paper")
            client_id = arguments.get("client_id", 1)
            host = arguments.get("host", "127.0.0.1")
            port = DEFAULT_PORT if mode == "paper" else LIVE_PORT
            
            success = await bridge.connect_async(host, port, client_id)
            
            if success:
                result = {
                    "status": "connected",
                    "mode": mode,
                    "host": host,
                    "port": port,
                    "client_id": client_id,
                    "next_order_id": bridge.next_order_id,
                    "api_version": API_VERSION
                }
            else:
                result = {
                    "status": "failed",
                    "error": "Failed to connect to TWS. Ensure TWS is running and API is enabled."
                }
            
            return [types.TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]
        
        elif name == "ibkr_disconnect":
            bridge.disconnect_async()
            return [types.TextContent(
                type="text",
                text=json.dumps({
                    "status": "disconnected",
                    "message": "Successfully disconnected from TWS"
                }, indent=2)
            )]
        
        elif name == "ibkr_connection_status":
            status = {
                "connected": bridge.connected,
                "next_order_id": bridge.next_order_id if bridge.connected else None,
                "api_version": API_VERSION,
                "timestamp": datetime.now().isoformat()
            }
            return [types.TextContent(
                type="text",
                text=json.dumps(status, indent=2)
            )]
        
        # ==========================================
        # Market Data Tools
        # ==========================================
        
        elif name == "ibkr_get_quote":
            if not bridge.connected:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": "Not connected to TWS"}, indent=2)
                )]

            symbol = arguments.get("symbol")
            sec_type = arguments.get("sec_type", "STK")
            exchange = arguments.get("exchange", "SMART")
            currency = arguments.get("currency", "USD")
            include_greeks = arguments.get("include_greeks", False)

            # Create contract
            contract = Contract()
            contract.symbol = symbol
            contract.secType = sec_type
            contract.exchange = exchange
            contract.currency = currency
            
            # Get market data
            data = await bridge.get_market_data_async(contract, include_greeks=include_greeks)
            
            if not data:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": f"No market data available for {symbol}"}, indent=2)
                )]
            
            # Calculate spread if bid/ask available
            if 'BID_PRICE' in data and 'ASK_PRICE' in data:
                data['spread'] = round(data['ASK_PRICE'] - data['BID_PRICE'], 4)
            
            return [types.TextContent(
                type="text",
                text=json.dumps(data, indent=2, default=str)
            )]
        
        elif name == "ibkr_get_option_chain":
            if not bridge.connected:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": "Not connected to TWS"}, indent=2)
                )]

            underlying = arguments.get("underlying")
            expiry = arguments.get("expiry")
            min_strike = arguments.get("min_strike")
            max_strike = arguments.get("max_strike")
            right = arguments.get("right")
            exchange = arguments.get("exchange", "SMART")
            currency = arguments.get("currency", "USD")
            trading_class = arguments.get("trading_class")
            include_greeks = arguments.get("include_greeks", False)

            # If no expiry specified, use today (0DTE)
            if not expiry:
                expiry = datetime.now().strftime("%Y%m%d")

            # Get option chain
            strike_range = (min_strike, max_strike) if min_strike and max_strike else None
            options = await bridge.get_option_chain_async(underlying, expiry, strike_range, right)

            if not options:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({
                        "error": f"No options found for {underlying} expiry {expiry}",
                        "suggestions": "Check expiry format (YYYYMMDD) and strike range"
                    }, indent=2)
                )]

            # Get market data and Greeks for each option if requested
            if include_greeks:
                for i, opt in enumerate(options[:20]):  # Limit to 20 to avoid rate limits
                    contract = Contract()
                    contract.symbol = underlying
                    contract.secType = "OPT"
                    contract.strike = opt['strike']
                    contract.right = opt['right']
                    contract.lastTradeDateOrContractMonth = opt['expiry']
                    contract.exchange = exchange
                    contract.currency = currency
                    contract.multiplier = opt.get('multiplier', '100')

                    # Set trading class if specified (required for DAX options)
                    if trading_class:
                        contract.tradingClass = trading_class

                    # Get market data with Greeks
                    market_data = await bridge.get_market_data_async(contract, include_greeks=True)

                    # Merge market data into option
                    options[i].update(market_data)

                    # Small delay to avoid rate limits
                    await asyncio.sleep(0.1)
            
            result = {
                "underlying": underlying,
                "expiry": expiry,
                "option_count": len(options),
                "options": options
            }
            
            return [types.TextContent(
                type="text",
                text=json.dumps(result, indent=2, default=str)
            )]
        
        # ==========================================
        # Order Management Tools
        # ==========================================
        
        elif name == "ibkr_place_order":
            if not bridge.connected:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": "Not connected to TWS"}, indent=2)
                )]
            
            # Create contract
            contract = Contract()
            contract.symbol = arguments.get("symbol")
            contract.secType = arguments.get("sec_type", "STK")
            contract.exchange = "SMART"
            contract.currency = "USD"
            
            # Create order
            order = Order()
            order.action = arguments.get("action")
            order.totalQuantity = arguments.get("quantity")
            order.orderType = arguments.get("order_type", "MKT")
            
            if order.orderType == "LMT":
                order.lmtPrice = arguments.get("limit_price", 0)
            elif order.orderType == "STP":
                order.auxPrice = arguments.get("stop_price", 0)
            
            order.transmit = arguments.get("transmit", False)
            
            # Place order
            order_id = await bridge.place_order_async(contract, order)
            
            result = {
                "order_id": order_id,
                "symbol": contract.symbol,
                "action": order.action,
                "quantity": order.totalQuantity,
                "order_type": order.orderType,
                "transmitted": order.transmit,
                "status": "Submitted" if order.transmit else "Created (Requires TWS Confirmation)",
                "timestamp": datetime.now().isoformat()
            }
            
            return [types.TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]
        
        elif name == "ibkr_place_bracket_order":
            if not bridge.connected:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": "Not connected to TWS"}, indent=2)
                )]
            
            # Create contract
            contract = Contract()
            contract.symbol = arguments.get("symbol")
            contract.secType = arguments.get("sec_type", "STK")
            contract.exchange = "SMART"
            contract.currency = "USD"
            
            # Place bracket order
            order_ids = await bridge.place_bracket_order_async(
                contract=contract,
                parent_action=arguments.get("action"),
                quantity=arguments.get("quantity"),
                limit_price=arguments.get("entry_price"),
                take_profit_price=arguments.get("take_profit_price"),
                stop_loss_price=arguments.get("stop_loss_price"),
                transmit=arguments.get("transmit", False)
            )
            
            result = {
                "parent_order_id": order_ids['parent_id'],
                "take_profit_order_id": order_ids['take_profit_id'],
                "stop_loss_order_id": order_ids['stop_loss_id'],
                "symbol": contract.symbol,
                "action": arguments.get("action"),
                "quantity": arguments.get("quantity"),
                "entry_price": arguments.get("entry_price", "MARKET"),
                "take_profit_price": arguments.get("take_profit_price"),
                "stop_loss_price": arguments.get("stop_loss_price"),
                "transmitted": arguments.get("transmit", False),
                "status": "Submitted" if arguments.get("transmit", False) else "Created (Requires TWS Confirmation)",
                "timestamp": datetime.now().isoformat()
            }
            
            return [types.TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]

        elif name == "ibkr_place_vertical_spread":
            if not bridge.connected:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": "Not connected to TWS"}, indent=2)
                )]

            symbol = arguments.get("symbol")
            expiry = arguments.get("expiry")
            long_strike = arguments.get("long_strike")
            short_strike = arguments.get("short_strike")
            right = arguments.get("right")
            quantity = arguments.get("quantity")
            net_price = arguments.get("net_price")
            order_type = arguments.get("order_type", "LMT")
            exchange = arguments.get("exchange", "SMART")
            currency = arguments.get("currency", "USD")
            trading_class = arguments.get("trading_class", "")
            multiplier = arguments.get("multiplier", "100")
            transmit = arguments.get("transmit", True)

            try:
                # Place vertical spread
                spread_result = await bridge.place_vertical_spread_async(
                    symbol=symbol,
                    expiry=expiry,
                    long_strike=long_strike,
                    short_strike=short_strike,
                    right=right,
                    quantity=quantity,
                    net_price=net_price,
                    order_type=order_type,
                    exchange=exchange,
                    currency=currency,
                    trading_class=trading_class,
                    multiplier=multiplier,
                    transmit=transmit
                )

                # Determine spread type name
                if right == "C":
                    if long_strike < short_strike:
                        spread_name = "Bull Call Spread"
                    else:
                        spread_name = "Bear Call Spread"
                else:  # Put
                    if long_strike > short_strike:
                        spread_name = "Bear Put Spread"
                    else:
                        spread_name = "Bull Put Spread"

                result = {
                    "order_id": spread_result['order_id'],
                    "spread_type": spread_name,
                    "symbol": symbol,
                    "expiry": expiry,
                    "long_leg": f"{right} {long_strike} (BUY)",
                    "short_leg": f"{right} {short_strike} (SELL)",
                    "quantity": quantity,
                    "net_price": net_price,
                    "order_type": order_type,
                    "currency": currency,
                    "max_profit": abs(short_strike - long_strike) - abs(net_price) if right == "C" else abs(net_price),
                    "max_loss": abs(net_price),
                    "status": "Submitted" if transmit else "Preview (Not Submitted)",
                    "transmit": transmit,
                    "timestamp": datetime.now().isoformat()
                }

                return [types.TextContent(
                    type="text",
                    text=json.dumps(result, indent=2)
                )]

            except Exception as e:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({
                        "error": str(e),
                        "symbol": symbol,
                        "expiry": expiry,
                        "strikes": f"{long_strike}/{short_strike}"
                    }, indent=2)
                )]

        elif name == "ibkr_get_order_status":
            order_id = arguments.get("order_id")
            order_data = bridge.orders.get(order_id)
            
            if not order_data:
                # Try to refresh orders
                bridge.reqOpenOrders()
                await asyncio.sleep(1)
                order_data = bridge.orders.get(order_id)
            
            if order_data:
                return [types.TextContent(
                    type="text",
                    text=json.dumps(order_data, indent=2)
                )]
            else:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": f"Order {order_id} not found"}, indent=2)
                )]
        
        elif name == "ibkr_cancel_order":
            order_id = arguments.get("order_id")
            bridge.cancelOrder(order_id)
            
            # Wait a moment for status update
            await asyncio.sleep(1)
            
            return [types.TextContent(
                type="text",
                text=json.dumps({
                    "order_id": order_id,
                    "action": "CANCEL",
                    "status": "Cancel requested",
                    "timestamp": datetime.now().isoformat()
                }, indent=2)
            )]
        
        # ==========================================
        # Account Management Tools
        # ==========================================
        
        elif name == "ibkr_get_positions":
            if not bridge.connected:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": "Not connected to TWS"}, indent=2)
                )]
            
            # Clear and request positions
            bridge.positions.clear()
            bridge.reqPositions()
            
            # Wait for positions
            await asyncio.sleep(2)
            
            # Cancel position updates
            bridge.cancelPositions()
            
            if bridge.positions:
                return [types.TextContent(
                    type="text",
                    text=json.dumps(bridge.positions, indent=2)
                )]
            else:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({
                        "positions": [],
                        "message": "No open positions"
                    }, indent=2)
                )]
        
        elif name == "ibkr_get_account_summary":
            if not bridge.connected:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": "Not connected to TWS"}, indent=2)
                )]
            
            # Request account summary
            bridge.account_values.clear()
            req_id = bridge.get_next_req_id()
            bridge.reqAccountSummary(
                req_id, "All",
                "NetLiquidation,TotalCashValue,BuyingPower,GrossPositionValue,UnrealizedPnL,RealizedPnL"
            )
            
            # Wait for data
            await asyncio.sleep(2)
            
            # Cancel account summary
            bridge.cancelAccountSummary(req_id)
            
            if bridge.account_values:
                return [types.TextContent(
                    type="text",
                    text=json.dumps(bridge.account_values, indent=2)
                )]
            else:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": "No account data available"}, indent=2)
                )]

        elif name == "ibkr_get_historical_data":
            if not bridge.connected:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": "Not connected to TWS"}, indent=2)
                )]

            symbol = arguments.get("symbol")
            sec_type = arguments.get("sec_type", "STK")
            bar_size = arguments.get("bar_size", "1 hour")
            duration = arguments.get("duration", "1 D")
            what_to_show = arguments.get("what_to_show", "TRADES")
            use_rth = 1 if arguments.get("use_rth", True) else 0

            # Create contract
            contract = Contract()
            contract.symbol = symbol
            contract.secType = sec_type
            contract.exchange = "SMART"
            contract.currency = "USD"

            # Get historical data
            bars = await bridge.get_historical_data_async(
                contract=contract,
                end_datetime="",
                duration_str=duration,
                bar_size=bar_size,
                what_to_show=what_to_show,
                use_rth=use_rth
            )

            result = {
                "symbol": symbol,
                "bar_size": bar_size,
                "duration": duration,
                "bar_count": len(bars),
                "bars": bars
            }

            return [types.TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]

        elif name == "ibkr_subscribe_realtime_bars":
            if not bridge.connected:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": "Not connected to TWS"}, indent=2)
                )]

            symbol = arguments.get("symbol")
            sec_type = arguments.get("sec_type", "STK")
            what_to_show = arguments.get("what_to_show", "TRADES")
            use_rth = arguments.get("use_rth", False)

            # Create contract
            contract = Contract()
            contract.symbol = symbol
            contract.secType = sec_type
            contract.exchange = "SMART"
            contract.currency = "USD"

            # Subscribe to real-time bars
            req_id = await bridge.subscribe_realtime_bars_async(
                contract=contract,
                what_to_show=what_to_show,
                use_rth=use_rth
            )

            # Get first few bars
            await asyncio.sleep(10)  # Wait for a few bars to accumulate
            bars = bridge.realtime_bars.get(req_id, [])

            result = {
                "status": "subscribed",
                "symbol": symbol,
                "req_id": req_id,
                "bar_count": len(bars),
                "latest_bars": bars[-5:] if len(bars) > 5 else bars,
                "note": "Subscription is active. Use ibkr_cancel_realtime_bars to stop."
            }

            return [types.TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]

        elif name == "ibkr_cancel_realtime_bars":
            if not bridge.connected:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": "Not connected to TWS"}, indent=2)
                )]

            req_id = arguments.get("req_id")
            bridge.cancel_realtime_bars(req_id)

            result = {
                "status": "cancelled",
                "req_id": req_id
            }

            return [types.TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]

        elif name == "ibkr_subscribe_pnl":
            if not bridge.connected:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": "Not connected to TWS"}, indent=2)
                )]

            account = arguments.get("account", "")

            # Subscribe to P&L
            req_id = await bridge.subscribe_pnl_async(account=account)

            # Wait for a few updates
            await asyncio.sleep(2)

            # Get current P&L data
            pnl_data = bridge.pnl_data.get(req_id, {})

            result = {
                "status": "subscribed",
                "req_id": req_id,
                "account": account if account else "default",
                "pnl": pnl_data,
                "note": "P&L subscription is active. Updates arrive in real-time."
            }

            return [types.TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]

        elif name == "ibkr_subscribe_pnl_single":
            if not bridge.connected:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": "Not connected to TWS"}, indent=2)
                )]

            account = arguments.get("account", "")
            con_id = arguments.get("con_id")

            if not con_id:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": "con_id is required"}, indent=2)
                )]

            # Subscribe to position P&L
            req_id = await bridge.subscribe_pnl_single_async(
                account=account,
                con_id=con_id
            )

            # Wait for updates
            await asyncio.sleep(2)

            # Get current P&L data
            pnl_data = bridge.pnl_single_data.get(req_id, {})

            result = {
                "status": "subscribed",
                "req_id": req_id,
                "con_id": con_id,
                "account": account if account else "default",
                "pnl": pnl_data,
                "note": "Position P&L subscription is active (~1 update/second)."
            }

            return [types.TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]

        elif name == "ibkr_get_executions":
            if not bridge.connected:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": "Not connected to TWS"}, indent=2)
                )]

            symbol = arguments.get("symbol", "")
            side = arguments.get("side", "")
            client_id = arguments.get("client_id", -1)

            # Get executions
            exec_data = await bridge.get_executions_async(
                client_id=client_id,
                symbol=symbol,
                side=side
            )

            result = {
                "status": "success",
                "filters": {
                    "symbol": symbol if symbol else "all",
                    "side": side if side else "all",
                    "client_id": client_id if client_id != -1 else "all"
                },
                "execution_count": exec_data['count'],
                "executions": exec_data['executions']
            }

            return [types.TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]

        elif name == "ibkr_get_all_open_orders":
            if not bridge.connected:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": "Not connected to TWS"}, indent=2)
                )]

            # Get all open orders
            orders = await bridge.get_all_open_orders_async()

            result = {
                "status": "success",
                "order_count": len(orders),
                "orders": orders
            }

            return [types.TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]

        elif name == "ibkr_cancel_all_orders":
            if not bridge.connected:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": "Not connected to TWS"}, indent=2)
                )]

            # Cancel all orders
            bridge.cancel_all_orders_async()

            # Wait a moment
            await asyncio.sleep(1)

            result = {
                "status": "success",
                "message": "Global cancel requested. All open orders are being cancelled.",
                "warning": "This cancels ALL orders from ALL clients and TWS!"
            }

            return [types.TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]

        elif name == "ibkr_get_option_params":
            if not bridge.connected:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": "Not connected to TWS"}, indent=2)
                )]

            symbol = arguments.get("symbol")
            sec_type = arguments.get("sec_type", "STK")
            exchange = arguments.get("exchange", "SMART")
            currency = arguments.get("currency", "USD")

            # Get option parameters
            params = await bridge.get_option_parameters_async(
                symbol=symbol,
                sec_type=sec_type,
                exchange=exchange,
                currency=currency
            )

            result = {
                "status": "success",
                "symbol": symbol,
                "underlying_type": sec_type,
                "param_sets": len(params),
                "parameters": params
            }

            # Add summary statistics if params available
            if params:
                all_expirations = set()
                all_strikes = set()
                for param_set in params:
                    all_expirations.update(param_set.get('expirations', []))
                    all_strikes.update(param_set.get('strikes', []))

                result['summary'] = {
                    'total_expirations': len(all_expirations),
                    'total_strikes': len(all_strikes),
                    'exchanges': [p['exchange'] for p in params]
                }

            return [types.TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]

        elif name == "ibkr_get_contract_details":
            if not bridge.connected:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": "Not connected to TWS"}, indent=2)
                )]

            symbol = arguments.get("symbol")
            sec_type = arguments.get("sec_type", "STK")
            exchange = arguments.get("exchange", "SMART")
            currency = arguments.get("currency", "USD")
            strike = arguments.get("strike", 0.0)
            right = arguments.get("right", "")
            expiry = arguments.get("expiry", "")

            # Get contract details
            details = await bridge.get_contract_details_async(
                symbol=symbol,
                sec_type=sec_type,
                exchange=exchange,
                currency=currency,
                strike=strike,
                right=right,
                expiry=expiry
            )

            result = {
                "status": "success",
                "symbol": symbol,
                "sec_type": sec_type,
                "contract_count": len(details),
                "contracts": details
            }

            return [types.TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]

        else:
            return [types.TextContent(
                type="text",
                text=f"Unknown tool: {name}"
            )]
    
    except Exception as e:
        logger.error(f"Error in tool {name}: {e}", exc_info=True)
        return [types.TextContent(
            type="text",
            text=json.dumps({
                "error": f"Tool execution failed: {str(e)}",
                "tool": name
            }, indent=2)
        )]

# ==========================================
# Main Entry Point
# ==========================================

async def main():
    """Run the MCP server."""
    logger.info(f"Starting IBKR MCP Server v{API_VERSION}")
    
    try:
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            logger.info("MCP stdio server started")
            
            await server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="ibkr-mcp",
                    server_version=API_VERSION,
                    capabilities=server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    )
                )
            )
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)