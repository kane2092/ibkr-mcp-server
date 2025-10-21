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
                'implied_volatility': impliedVol if impliedVol != -1 and impliedVol < 10 else None,
                'delta': delta if abs(delta) <= 1 else None,
                'gamma': gamma if gamma != -2 else None,
                'vega': vega if vega != -2 else None,
                'theta': theta if theta != -2 else None,
                'option_price': optPrice if optPrice >= 0 else None,
                'underlying_price': undPrice if undPrice > 0 else None
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
        
        # Take profit order (opposite action)
        take_profit = Order()
        take_profit.orderId = tp_id
        take_profit.action = "SELL" if parent_action == "BUY" else "BUY"
        take_profit.orderType = "LMT"
        take_profit.totalQuantity = quantity
        take_profit.lmtPrice = take_profit_price
        take_profit.parentId = parent_id
        take_profit.transmit = False
        
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
                        "description": "Symbol to quote (e.g., SPY, AAPL)"
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
                        "description": "Exchange (SMART for auto-routing)"
                    },
                    "include_greeks": {
                        "type": "boolean",
                        "default": false,
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
                        "description": "Underlying symbol (e.g., SPY, SPX)"
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
                    "include_greeks": {
                        "type": "boolean",
                        "default": false,
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
                        "default": false,
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
                        "default": false,
                        "description": "Auto-transmit all orders"
                    }
                },
                "required": ["symbol", "action", "quantity", "take_profit_price", "stop_loss_price"]
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
            include_greeks = arguments.get("include_greeks", False)
            
            # Create contract
            contract = Contract()
            contract.symbol = symbol
            contract.secType = sec_type
            contract.exchange = exchange if sec_type == "STK" else "CBOE" if sec_type == "IND" else exchange
            contract.currency = "USD"
            
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
                    contract.exchange = "SMART"
                    contract.currency = "USD"
                    contract.multiplier = opt.get('multiplier', '100')
                    
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