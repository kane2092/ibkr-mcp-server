"""
Interactive Brokers MCP Server
===============================
A comprehensive Model Context Protocol (MCP) server for Interactive Brokers TWS API integration.

Features:
- Real-time market data (stocks, options, indices, futures)
- Advanced order management (market, limit, stop, bracket, trailing, OCO)
- Account and portfolio management
- Options chain analysis for 0DTE trading
- Position management and P&L tracking
- Historical data retrieval
- Risk management tools

Author: IBKR MCP Server v2.0
License: MIT
"""

import asyncio
import json
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple, Union
from decimal import Decimal
from enum import Enum
from collections import defaultdict

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, field_validator, ConfigDict
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract, ComboLeg
from ibapi.order import Order
from ibapi.common import TickerId, OrderId, BarData
from ibapi.order_state import OrderState
from ibapi.execution import Execution
from ibapi.commission_report import CommissionReport

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize MCP Server
mcp = FastMCP("ibkr_mcp")

# ==========================================
# Configuration and Constants
# ==========================================

API_VERSION = "2.0.0"
DEFAULT_PORT = 7497  # Paper trading
LIVE_PORT = 7496
DEFAULT_CLIENT_ID = 1
CONNECTION_TIMEOUT = 10  # seconds
CHARACTER_LIMIT = 25000  # Response character limit

# Market data constants
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
    "IMPLIED_VOL": 24,
    "DELTA": 27,
    "GAMMA": 28,
    "VEGA": 29,
    "THETA": 30
}

# ==========================================
# Input Models
# ==========================================

class ConnectionMode(str, Enum):
    """Connection mode for TWS/IB Gateway."""
    PAPER = "paper"
    LIVE = "live"

class OrderAction(str, Enum):
    """Order action types."""
    BUY = "BUY"
    SELL = "SELL"

class OrderType(str, Enum):
    """Order types supported by IB."""
    MARKET = "MKT"
    LIMIT = "LMT"
    STOP = "STP"
    STOP_LIMIT = "STP LMT"
    TRAILING_STOP = "TRAIL"
    BRACKET = "BRACKET"
    ONE_CANCELS_OTHER = "OCO"

class SecType(str, Enum):
    """Security types."""
    STOCK = "STK"
    OPTION = "OPT"
    FUTURE = "FUT"
    INDEX = "IND"
    CASH = "CASH"
    COMBO = "BAG"

class ResponseFormat(str, Enum):
    """Response format options."""
    JSON = "json"
    MARKDOWN = "markdown"
    DETAILED = "detailed"
    CONCISE = "concise"

class ConnectInput(BaseModel):
    """Input model for TWS connection."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    
    mode: ConnectionMode = Field(
        default=ConnectionMode.PAPER,
        description="Connection mode: 'paper' for paper trading (port 7497), 'live' for live trading (port 7496)"
    )
    client_id: int = Field(
        default=DEFAULT_CLIENT_ID,
        description="Client ID for the connection (0-999)",
        ge=0, le=999
    )
    host: str = Field(
        default="127.0.0.1",
        description="TWS/IB Gateway host address"
    )

class StockQuoteInput(BaseModel):
    """Input model for stock quotes."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    
    symbol: str = Field(
        ...,
        description="Stock ticker symbol (e.g., 'AAPL', 'SPY', 'MSFT')",
        min_length=1,
        max_length=10
    )
    exchange: str = Field(
        default="SMART",
        description="Exchange to query. SMART routes to best exchange automatically"
    )
    currency: str = Field(
        default="USD",
        description="Currency for the quote"
    )
    format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Response format: 'json' or 'markdown'"
    )

class OptionChainInput(BaseModel):
    """Input model for option chain queries."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    
    underlying: str = Field(
        ...,
        description="Underlying symbol (e.g., 'SPX' for S&P 500 index options)",
        min_length=1,
        max_length=10
    )
    expiry: Optional[str] = Field(
        default=None,
        description="Expiration date in YYYYMMDD format. None for 0DTE/today's expiry"
    )
    strike_range: Optional[Tuple[float, float]] = Field(
        default=None,
        description="Strike price range (min, max). None for ATM +/- 5%"
    )
    right: Optional[str] = Field(
        default=None,
        description="Option right: 'C' for calls, 'P' for puts, None for both"
    )
    include_greeks: bool = Field(
        default=True,
        description="Include Greeks (delta, gamma, theta, vega) in response"
    )
    format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Response format"
    )

class OrderInput(BaseModel):
    """Base input model for orders."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    
    symbol: str = Field(
        ...,
        description="Security symbol",
        min_length=1,
        max_length=10
    )
    sec_type: SecType = Field(
        default=SecType.STOCK,
        description="Security type"
    )
    action: OrderAction = Field(
        ...,
        description="Order action: BUY or SELL"
    )
    quantity: int = Field(
        ...,
        description="Order quantity",
        gt=0,
        le=1000000
    )
    exchange: str = Field(
        default="SMART",
        description="Exchange for routing"
    )
    currency: str = Field(
        default="USD",
        description="Currency"
    )
    transmit: bool = Field(
        default=False,
        description="If False, order requires manual confirmation in TWS"
    )
    account: Optional[str] = Field(
        default=None,
        description="IB account number. None uses default account"
    )

class MarketOrderInput(OrderInput):
    """Input for market orders."""
    pass

class LimitOrderInput(OrderInput):
    """Input for limit orders."""
    limit_price: float = Field(
        ...,
        description="Limit price for the order",
        gt=0
    )

class StopOrderInput(OrderInput):
    """Input for stop orders."""
    stop_price: float = Field(
        ...,
        description="Stop trigger price",
        gt=0
    )

class BracketOrderInput(OrderInput):
    """Input for bracket orders (entry + take profit + stop loss)."""
    entry_price: Optional[float] = Field(
        default=None,
        description="Entry limit price. None for market entry"
    )
    take_profit_price: float = Field(
        ...,
        description="Take profit limit price",
        gt=0
    )
    stop_loss_price: float = Field(
        ...,
        description="Stop loss trigger price",
        gt=0
    )
    
    @field_validator('take_profit_price')
    @classmethod
    def validate_tp(cls, v: float, values: Dict[str, Any]) -> float:
        """Validate take profit price relative to action."""
        if 'action' in values:
            if values['action'] == OrderAction.BUY and 'stop_loss_price' in values:
                if v <= values['stop_loss_price']:
                    raise ValueError("For BUY orders, take_profit must be > stop_loss")
        return v

class TrailingStopInput(OrderInput):
    """Input for trailing stop orders."""
    trail_amount: Optional[float] = Field(
        default=None,
        description="Trail amount in currency (e.g., $1.00)"
    )
    trail_percent: Optional[float] = Field(
        default=None,
        description="Trail percentage (e.g., 1.5 for 1.5%)",
        ge=0,
        le=100
    )
    
    @field_validator('trail_percent')
    @classmethod
    def validate_trail(cls, v: Optional[float], values: Dict[str, Any]) -> Optional[float]:
        """Validate that either trail_amount or trail_percent is set."""
        if v is None and values.get('trail_amount') is None:
            raise ValueError("Either trail_amount or trail_percent must be specified")
        return v

class ModifyOrderInput(BaseModel):
    """Input for modifying existing orders."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    
    order_id: int = Field(
        ...,
        description="Order ID to modify",
        gt=0
    )
    quantity: Optional[int] = Field(
        default=None,
        description="New quantity",
        gt=0
    )
    limit_price: Optional[float] = Field(
        default=None,
        description="New limit price",
        gt=0
    )
    stop_price: Optional[float] = Field(
        default=None,
        description="New stop price",
        gt=0
    )

class CancelOrderInput(BaseModel):
    """Input for canceling orders."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    
    order_id: int = Field(
        ...,
        description="Order ID to cancel",
        gt=0
    )

class HistoricalDataInput(BaseModel):
    """Input for historical data queries."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    
    symbol: str = Field(
        ...,
        description="Security symbol",
        min_length=1,
        max_length=10
    )
    sec_type: SecType = Field(
        default=SecType.STOCK,
        description="Security type"
    )
    duration: str = Field(
        default="1 D",
        description="Duration string (e.g., '1 D', '1 W', '1 M', '1 Y')"
    )
    bar_size: str = Field(
        default="5 mins",
        description="Bar size (e.g., '1 min', '5 mins', '1 hour', '1 day')"
    )
    what_to_show: str = Field(
        default="TRADES",
        description="Data type: TRADES, MIDPOINT, BID, ASK, etc."
    )
    use_rth: bool = Field(
        default=True,
        description="Use regular trading hours only"
    )
    format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Response format"
    )

# ==========================================
# IB API Bridge
# ==========================================

class IBKRBridge(EWrapper, EClient):
    """
    Enhanced IB API bridge with async/await support.
    Handles all TWS API interactions with thread-safe operations.
    """
    
    def __init__(self):
        EClient.__init__(self, self)
        self.connected = False
        self.next_order_id = 1
        self.next_req_id = 1
        
        # Data storage
        self.market_data: Dict[int, Dict[str, Any]] = {}
        self.orders: Dict[int, Dict[str, Any]] = {}
        self.positions: List[Dict[str, Any]] = []
        self.account_values: Dict[str, Any] = {}
        self.executions: List[Dict[str, Any]] = []
        self.option_chains: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.historical_data: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        
        # Event management
        self.events: Dict[str, asyncio.Event] = {}
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.thread: Optional[threading.Thread] = None
        
        # Request tracking
        self.pending_requests: Dict[int, Dict[str, Any]] = {}
        
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
            
            # Wait for connection
            await asyncio.wait_for(
                self.events['connected'].wait(),
                timeout=CONNECTION_TIMEOUT
            )
            
            return self.connected
            
        except asyncio.TimeoutError:
            logger.error("Connection timeout")
            return False
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return False
    
    def disconnect_async(self):
        """Disconnect from TWS."""
        if self.connected:
            self.disconnect()
            self.connected = False
            if self.thread:
                self.thread.join(timeout=5)
    
    # ==========================================
    # IB API Callbacks
    # ==========================================
    
    def error(self, reqId: TickerId, errorCode: int, errorString: str, advancedOrderRejectJson=""):
        """Handle errors from TWS."""
        logger.error(f"Error {errorCode}: {errorString} (ReqId: {reqId})")
        
        # Store error for request
        if reqId in self.pending_requests:
            self.pending_requests[reqId]['error'] = {
                'code': errorCode,
                'message': errorString
            }
            
            # Set event if waiting
            event_key = f"req_{reqId}"
            if event_key in self.events:
                self.loop.call_soon_threadsafe(self.events[event_key].set)
    
    def connectAck(self):
        """Acknowledge connection."""
        logger.info("Connected to TWS")
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
        
        # Update timestamp
        self.market_data[reqId]['timestamp'] = datetime.now().isoformat()
        
        # Signal data received
        event_key = f"req_{reqId}"
        if event_key in self.events and self.loop:
            self.loop.call_soon_threadsafe(self.events[event_key].set)
    
    def tickSize(self, reqId: TickerId, tickType: int, size: int):
        """Receive tick size data."""
        if reqId not in self.market_data:
            self.market_data[reqId] = {}
        
        tick_name = next((k for k, v in TICK_TYPES.items() if v == tickType), str(tickType))
        self.market_data[reqId][tick_name] = size
    
    def tickGeneric(self, reqId: TickerId, tickType: int, value: float):
        """Receive generic tick data."""
        if reqId not in self.market_data:
            self.market_data[reqId] = {}
        
        tick_name = next((k for k, v in TICK_TYPES.items() if v == tickType), str(tickType))
        self.market_data[reqId][tick_name] = value
    
    def tickOptionComputation(self, reqId: TickerId, tickType: int, tickAttrib: int,
                            impliedVol: float, delta: float, optPrice: float,
                            pvDividend: float, gamma: float, vega: float,
                            theta: float, undPrice: float):
        """Receive option Greeks."""
        if reqId not in self.market_data:
            self.market_data[reqId] = {}
        
        self.market_data[reqId].update({
            'implied_vol': impliedVol if impliedVol != -1 else None,
            'delta': delta if delta != -2 else None,
            'gamma': gamma if gamma != -2 else None,
            'vega': vega if vega != -2 else None,
            'theta': theta if theta != -2 else None,
            'underlying_price': undPrice if undPrice != -1 else None
        })
    
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
            'tif': order.tif,
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
            'market_value': None,  # Will be updated with market data
            'unrealized_pnl': None
        }
        
        # Update or append position
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
        
        self.account_values[account][tag] = {
            'value': value,
            'currency': currency
        }
    
    def execDetails(self, reqId: int, contract: Contract, execution: Execution):
        """Receive execution details."""
        exec_data = {
            'exec_id': execution.execId,
            'order_id': execution.orderId,
            'symbol': contract.symbol,
            'side': execution.side,
            'shares': execution.shares,
            'price': execution.price,
            'time': execution.time,
            'exchange': execution.exchange,
            'cum_qty': execution.cumQty,
            'avg_price': execution.avgPrice
        }
        self.executions.append(exec_data)
    
    def commissionReport(self, commissionReport: CommissionReport):
        """Receive commission report."""
        # Match commission to execution
        for exec in self.executions:
            if exec['exec_id'] == commissionReport.execId:
                exec['commission'] = commissionReport.commission
                exec['currency'] = commissionReport.currency
                break
    
    def historicalData(self, reqId: int, bar: BarData):
        """Receive historical data bar."""
        bar_data = {
            'date': bar.date,
            'open': bar.open,
            'high': bar.high,
            'low': bar.low,
            'close': bar.close,
            'volume': bar.volume,
            'wap': bar.wap,
            'bar_count': bar.barCount
        }
        self.historical_data[reqId].append(bar_data)
    
    def historicalDataEnd(self, reqId: int, start: str, end: str):
        """Signal end of historical data."""
        event_key = f"req_{reqId}"
        if event_key in self.events and self.loop:
            self.loop.call_soon_threadsafe(self.events[event_key].set)
    
    def contractDetails(self, reqId: int, contractDetails):
        """Receive contract details (for option chains)."""
        contract = contractDetails.contract
        
        # Store option contract details
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
            
            key = f"{contract.symbol}_{contract.lastTradeDateOrContractMonth}"
            self.option_chains[key].append(option_data)
    
    def contractDetailsEnd(self, reqId: int):
        """Signal end of contract details."""
        event_key = f"req_{reqId}"
        if event_key in self.events and self.loop:
            self.loop.call_soon_threadsafe(self.events[event_key].set)
    
    # ==========================================
    # High-level async methods
    # ==========================================
    
    async def get_market_data_async(self, contract: Contract, 
                                   timeout: float = 5.0) -> Dict[str, Any]:
        """Get market data for a contract asynchronously."""
        req_id = self._get_next_req_id()
        event_key = f"req_{req_id}"
        self.events[event_key] = asyncio.Event()
        
        try:
            # Request market data
            self.reqMktData(req_id, contract, "", False, False, [])
            
            # Wait for data
            await asyncio.wait_for(
                self.events[event_key].wait(),
                timeout=timeout
            )
            
            # Cancel market data
            self.cancelMktData(req_id)
            
            return self.market_data.get(req_id, {})
            
        except asyncio.TimeoutError:
            self.cancelMktData(req_id)
            return {}
        finally:
            self.events.pop(event_key, None)
    
    async def place_order_async(self, contract: Contract, order: Order) -> int:
        """Place an order asynchronously."""
        order_id = self._get_next_order_id()
        event_key = f"order_{order_id}"
        self.events[event_key] = asyncio.Event()
        
        try:
            # Place order
            self.placeOrder(order_id, contract, order)
            
            # Wait for initial status
            await asyncio.wait_for(
                self.events[event_key].wait(),
                timeout=5.0
            )
            
            return order_id
            
        except asyncio.TimeoutError:
            logger.warning(f"Order {order_id} submission timeout")
            return order_id
        finally:
            self.events.pop(event_key, None)
    
    async def get_option_chain_async(self, underlying: str, 
                                    expiry: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get option chain for underlying."""
        req_id = self._get_next_req_id()
        event_key = f"req_{req_id}"
        self.events[event_key] = asyncio.Event()
        
        try:
            # Create underlying contract
            contract = Contract()
            contract.symbol = underlying
            contract.secType = "OPT"
            contract.exchange = "SMART"
            contract.currency = "USD"
            
            if expiry:
                contract.lastTradeDateOrContractMonth = expiry
            
            # Request contract details
            self.reqContractDetails(req_id, contract)
            
            # Wait for data
            await asyncio.wait_for(
                self.events[event_key].wait(),
                timeout=10.0
            )
            
            key = f"{underlying}_{expiry}" if expiry else underlying
            return self.option_chains.get(key, [])
            
        except asyncio.TimeoutError:
            return []
        finally:
            self.events.pop(event_key, None)
    
    async def get_historical_data_async(self, contract: Contract,
                                       duration: str, bar_size: str,
                                       what_to_show: str, use_rth: bool) -> List[Dict[str, Any]]:
        """Get historical data asynchronously."""
        req_id = self._get_next_req_id()
        event_key = f"req_{req_id}"
        self.events[event_key] = asyncio.Event()
        
        try:
            # Request historical data
            end_datetime = ""  # Use current time
            self.reqHistoricalData(
                req_id, contract, end_datetime, duration,
                bar_size, what_to_show, use_rth, 1, False, []
            )
            
            # Wait for data
            await asyncio.wait_for(
                self.events[event_key].wait(),
                timeout=30.0
            )
            
            return self.historical_data.get(req_id, [])
            
        except asyncio.TimeoutError:
            return []
        finally:
            self.events.pop(event_key, None)
    
    def _get_next_req_id(self) -> int:
        """Get next request ID."""
        req_id = self.next_req_id
        self.next_req_id += 1
        return req_id
    
    def _get_next_order_id(self) -> int:
        """Get next order ID."""
        order_id = self.next_order_id
        self.next_order_id += 1
        return order_id

# ==========================================
# Global Bridge Instance
# ==========================================

bridge = IBKRBridge()

# ==========================================
# Helper Functions
# ==========================================

def create_contract(symbol: str, sec_type: SecType, 
                   exchange: str = "SMART", currency: str = "USD",
                   **kwargs) -> Contract:
    """Create an IB contract object."""
    contract = Contract()
    contract.symbol = symbol
    contract.secType = sec_type.value
    contract.exchange = exchange
    contract.currency = currency
    
    # Add optional fields
    for key, value in kwargs.items():
        if hasattr(contract, key):
            setattr(contract, key, value)
    
    return contract

def format_market_data(data: Dict[str, Any], format: ResponseFormat) -> str:
    """Format market data for response."""
    if format == ResponseFormat.JSON:
        return json.dumps(data, indent=2)
    
    # Markdown format
    lines = ["## Market Data\n"]
    lines.append(f"**Symbol**: {data.get('symbol', 'N/A')}")
    lines.append(f"**Timestamp**: {data.get('timestamp', 'N/A')}\n")
    
    if 'BID_PRICE' in data:
        lines.append(f"**Bid**: ${data['BID_PRICE']:.2f} × {data.get('BID_SIZE', 0)}")
    if 'ASK_PRICE' in data:
        lines.append(f"**Ask**: ${data['ASK_PRICE']:.2f} × {data.get('ASK_SIZE', 0)}")
    if 'LAST_PRICE' in data:
        lines.append(f"**Last**: ${data['LAST_PRICE']:.2f} × {data.get('LAST_SIZE', 0)}")
    
    spread = None
    if 'BID_PRICE' in data and 'ASK_PRICE' in data:
        spread = data['ASK_PRICE'] - data['BID_PRICE']
        lines.append(f"**Spread**: ${spread:.2f}")
    
    if 'VOLUME' in data:
        lines.append(f"**Volume**: {data['VOLUME']:,}")
    
    return "\n".join(lines)

def format_option_chain(options: List[Dict[str, Any]], format: ResponseFormat) -> str:
    """Format option chain data."""
    if format == ResponseFormat.JSON:
        return json.dumps(options, indent=2)
    
    # Markdown format with table
    lines = ["## Option Chain\n"]
    lines.append("| Strike | Right | Expiry | IV | Delta | Gamma | Theta | Vega |")
    lines.append("|--------|-------|--------|-----|-------|-------|-------|------|")
    
    for opt in options[:50]:  # Limit to 50 for readability
        lines.append(
            f"| {opt['strike']} | {opt['right']} | {opt['expiry']} | "
            f"{opt.get('implied_vol', 'N/A')} | {opt.get('delta', 'N/A')} | "
            f"{opt.get('gamma', 'N/A')} | {opt.get('theta', 'N/A')} | "
            f"{opt.get('vega', 'N/A')} |"
        )
    
    return "\n".join(lines)

# ==========================================
# MCP Tool Implementations
# ==========================================

@mcp.tool(
    name="ibkr_connect",
    annotations={
        "title": "Connect to TWS/IB Gateway",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def ibkr_connect(params: ConnectInput) -> str:
    """
    Connect to Interactive Brokers TWS or IB Gateway.
    
    Establishes connection to TWS (Trader Workstation) or IB Gateway.
    Must be called before using any other trading tools.
    
    Prerequisites:
    - TWS or IB Gateway must be running
    - API connections must be enabled in TWS settings
    - Port 7497 for paper trading, 7496 for live trading
    
    Args:
        params: Connection parameters including mode, client_id, and host
    
    Returns:
        JSON with connection status and details
        
    Examples:
        Connect to paper trading:
        ibkr_connect(mode="paper")
        
        Connect to live trading:
        ibkr_connect(mode="live", client_id=2)
    """
    try:
        # Determine port based on mode
        port = LIVE_PORT if params.mode == ConnectionMode.LIVE else DEFAULT_PORT
        
        # Disconnect if already connected
        if bridge.connected:
            bridge.disconnect_async()
            await asyncio.sleep(1)
        
        # Connect to TWS
        success = await bridge.connect_async(params.host, port, params.client_id)
        
        if success:
            result = {
                "status": "connected",
                "mode": params.mode.value,
                "host": params.host,
                "port": port,
                "client_id": params.client_id,
                "next_order_id": bridge.next_order_id,
                "api_version": API_VERSION
            }
        else:
            result = {
                "status": "failed",
                "error": "Failed to connect to TWS. Ensure TWS is running and API is enabled.",
                "troubleshooting": [
                    "1. Check TWS is running",
                    "2. Enable API connections in TWS settings",
                    f"3. Verify port {port} is correct",
                    "4. Check firewall settings"
                ]
            }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        logger.error(f"Connection error: {e}")
        return json.dumps({
            "status": "error",
            "error": str(e)
        }, indent=2)

@mcp.tool(
    name="ibkr_disconnect",
    annotations={
        "title": "Disconnect from TWS",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def ibkr_disconnect() -> str:
    """
    Disconnect from TWS or IB Gateway.
    
    Gracefully closes the connection to TWS/Gateway.
    Active orders remain on the TWS side.
    
    Returns:
        JSON with disconnection status
    """
    try:
        if bridge.connected:
            bridge.disconnect_async()
            return json.dumps({
                "status": "disconnected",
                "message": "Successfully disconnected from TWS"
            }, indent=2)
        else:
            return json.dumps({
                "status": "not_connected",
                "message": "No active connection to disconnect"
            }, indent=2)
    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": str(e)
        }, indent=2)

@mcp.tool(
    name="ibkr_get_stock_quote",
    annotations={
        "title": "Get Stock Quote",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def ibkr_get_stock_quote(params: StockQuoteInput) -> str:
    """
    Get real-time stock quote with bid/ask/last prices.
    
    Retrieves current market data for US stocks including bid/ask prices,
    sizes, last trade, spread, and volume.
    
    Args:
        params: Stock quote parameters including symbol and format
    
    Returns:
        Formatted quote with bid/ask/last/spread
        
    Examples:
        Get SPY quote:
        ibkr_get_stock_quote(symbol="SPY")
        
        Get AAPL quote in markdown:
        ibkr_get_stock_quote(symbol="AAPL", format="markdown")
    """
    try:
        if not bridge.connected:
            return json.dumps({
                "error": "Not connected to TWS. Call ibkr_connect first."
            }, indent=2)
        
        # Create contract
        contract = create_contract(
            params.symbol,
            SecType.STOCK,
            params.exchange,
            params.currency
        )
        
        # Get market data
        data = await bridge.get_market_data_async(contract)
        
        if not data:
            return json.dumps({
                "error": f"No market data available for {params.symbol}"
            }, indent=2)
        
        # Add symbol to data
        data['symbol'] = params.symbol
        
        # Calculate spread if bid/ask available
        if 'BID_PRICE' in data and 'ASK_PRICE' in data:
            data['spread'] = round(data['ASK_PRICE'] - data['BID_PRICE'], 2)
        
        return format_market_data(data, params.format)
        
    except Exception as e:
        logger.error(f"Error getting stock quote: {e}")
        return json.dumps({
            "error": str(e)
        }, indent=2)

@mcp.tool(
    name="ibkr_get_option_chain",
    annotations={
        "title": "Get Option Chain",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def ibkr_get_option_chain(params: OptionChainInput) -> str:
    """
    Get option chain with prices and Greeks for SPX and other underlyings.
    
    Retrieves option chain data including strikes, expiries, and Greeks
    (delta, gamma, theta, vega). Perfect for 0DTE options analysis.
    
    Args:
        params: Option chain parameters including underlying, expiry, and strike range
    
    Returns:
        Option chain data with prices and Greeks
        
    Examples:
        Get 0DTE SPX options:
        ibkr_get_option_chain(underlying="SPX")
        
        Get specific expiry with strike range:
        ibkr_get_option_chain(underlying="SPX", expiry="20241121", strike_range=(5500, 5600))
    """
    try:
        if not bridge.connected:
            return json.dumps({
                "error": "Not connected to TWS. Call ibkr_connect first."
            }, indent=2)
        
        # If no expiry specified, use today (0DTE)
        if not params.expiry:
            params.expiry = datetime.now().strftime("%Y%m%d")
        
        # Get option chain
        options = await bridge.get_option_chain_async(params.underlying, params.expiry)
        
        if not options:
            return json.dumps({
                "error": f"No options found for {params.underlying} expiry {params.expiry}"
            }, indent=2)
        
        # Filter by strike range if specified
        if params.strike_range:
            min_strike, max_strike = params.strike_range
            options = [opt for opt in options 
                      if min_strike <= opt['strike'] <= max_strike]
        
        # Filter by right if specified
        if params.right:
            options = [opt for opt in options if opt['right'] == params.right]
        
        # Get market data and Greeks for each option if requested
        if params.include_greeks:
            for opt in options[:20]:  # Limit to avoid rate limits
                contract = Contract()
                contract.symbol = params.underlying
                contract.secType = "OPT"
                contract.strike = opt['strike']
                contract.right = opt['right']
                contract.lastTradeDateOrContractMonth = opt['expiry']
                contract.exchange = "SMART"
                contract.currency = "USD"
                
                market_data = await bridge.get_market_data_async(contract, timeout=2.0)
                opt.update(market_data)
        
        return format_option_chain(options, params.format)
        
    except Exception as e:
        logger.error(f"Error getting option chain: {e}")
        return json.dumps({
            "error": str(e)
        }, indent=2)

@mcp.tool(
    name="ibkr_place_market_order",
    annotations={
        "title": "Place Market Order",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def ibkr_place_market_order(params: MarketOrderInput) -> str:
    """
    Place a market order for immediate execution.
    
    Submits a market order to buy or sell at the current best available price.
    Use transmit=False to require manual confirmation in TWS.
    
    Args:
        params: Market order parameters
    
    Returns:
        Order confirmation with order ID
        
    Examples:
        Buy 100 shares of SPY at market:
        ibkr_place_market_order(symbol="SPY", action="BUY", quantity=100, transmit=True)
        
        Sell with manual confirmation:
        ibkr_place_market_order(symbol="AAPL", action="SELL", quantity=50, transmit=False)
    """
    try:
        if not bridge.connected:
            return json.dumps({
                "error": "Not connected to TWS. Call ibkr_connect first."
            }, indent=2)
        
        # Create contract and order
        contract = create_contract(
            params.symbol,
            params.sec_type,
            params.exchange,
            params.currency
        )
        
        order = Order()
        order.action = params.action.value
        order.orderType = "MKT"
        order.totalQuantity = params.quantity
        order.transmit = params.transmit
        
        if params.account:
            order.account = params.account
        
        # Place order
        order_id = await bridge.place_order_async(contract, order)
        
        result = {
            "order_id": order_id,
            "symbol": params.symbol,
            "action": params.action.value,
            "quantity": params.quantity,
            "order_type": "MARKET",
            "transmitted": params.transmit,
            "status": "Submitted" if params.transmit else "Created (Requires TWS Confirmation)",
            "timestamp": datetime.now().isoformat()
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        logger.error(f"Error placing market order: {e}")
        return json.dumps({
            "error": str(e)
        }, indent=2)

@mcp.tool(
    name="ibkr_place_limit_order",
    annotations={
        "title": "Place Limit Order",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def ibkr_place_limit_order(params: LimitOrderInput) -> str:
    """
    Place a limit order at a specified price.
    
    Submits a limit order that will only execute at the specified price or better.
    Buy limit orders execute at or below the limit price.
    Sell limit orders execute at or above the limit price.
    
    Args:
        params: Limit order parameters including limit price
    
    Returns:
        Order confirmation with order ID
        
    Examples:
        Buy SPY at $550 or better:
        ibkr_place_limit_order(symbol="SPY", action="BUY", quantity=10, limit_price=550.00)
    """
    try:
        if not bridge.connected:
            return json.dumps({
                "error": "Not connected to TWS. Call ibkr_connect first."
            }, indent=2)
        
        # Create contract and order
        contract = create_contract(
            params.symbol,
            params.sec_type,
            params.exchange,
            params.currency
        )
        
        order = Order()
        order.action = params.action.value
        order.orderType = "LMT"
        order.totalQuantity = params.quantity
        order.lmtPrice = params.limit_price
        order.transmit = params.transmit
        
        if params.account:
            order.account = params.account
        
        # Place order
        order_id = await bridge.place_order_async(contract, order)
        
        result = {
            "order_id": order_id,
            "symbol": params.symbol,
            "action": params.action.value,
            "quantity": params.quantity,
            "order_type": "LIMIT",
            "limit_price": params.limit_price,
            "transmitted": params.transmit,
            "status": "Submitted" if params.transmit else "Created (Requires TWS Confirmation)",
            "timestamp": datetime.now().isoformat()
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        logger.error(f"Error placing limit order: {e}")
        return json.dumps({
            "error": str(e)
        }, indent=2)

@mcp.tool(
    name="ibkr_place_bracket_order",
    annotations={
        "title": "Place Bracket Order",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def ibkr_place_bracket_order(params: BracketOrderInput) -> str:
    """
    Place a bracket order (entry + take profit + stop loss).
    
    Creates a three-legged order:
    1. Parent order (market or limit entry)
    2. Take profit order (limit order, opposite side)
    3. Stop loss order (stop order, opposite side)
    
    Perfect for 0DTE options with defined risk/reward.
    
    Args:
        params: Bracket order parameters
    
    Returns:
        Order IDs for all three legs
        
    Examples:
        Buy with bracket (entry at market, TP at $12, SL at $8):
        ibkr_place_bracket_order(
            symbol="SPY", action="BUY", quantity=100,
            take_profit_price=12.00, stop_loss_price=8.00
        )
        
        Buy with limit entry:
        ibkr_place_bracket_order(
            symbol="SPX", action="BUY", quantity=1,
            entry_price=10.00, take_profit_price=15.00, stop_loss_price=7.00
        )
    """
    try:
        if not bridge.connected:
            return json.dumps({
                "error": "Not connected to TWS. Call ibkr_connect first."
            }, indent=2)
        
        # Create contract
        contract = create_contract(
            params.symbol,
            params.sec_type,
            params.exchange,
            params.currency
        )
        
        # Parent order
        parent = Order()
        parent.action = params.action.value
        parent.orderType = "LMT" if params.entry_price else "MKT"
        parent.totalQuantity = params.quantity
        if params.entry_price:
            parent.lmtPrice = params.entry_price
        parent.transmit = False  # Don't transmit until all orders are created
        
        parent_id = await bridge.place_order_async(contract, parent)
        
        # Take profit order (opposite action)
        take_profit = Order()
        take_profit.action = "SELL" if params.action == OrderAction.BUY else "BUY"
        take_profit.orderType = "LMT"
        take_profit.totalQuantity = params.quantity
        take_profit.lmtPrice = params.take_profit_price
        take_profit.parentId = parent_id
        take_profit.transmit = False
        
        tp_id = await bridge.place_order_async(contract, take_profit)
        
        # Stop loss order (opposite action)
        stop_loss = Order()
        stop_loss.action = "SELL" if params.action == OrderAction.BUY else "BUY"
        stop_loss.orderType = "STP"
        stop_loss.totalQuantity = params.quantity
        stop_loss.auxPrice = params.stop_loss_price
        stop_loss.parentId = parent_id
        stop_loss.transmit = params.transmit  # Transmit all orders if requested
        
        sl_id = await bridge.place_order_async(contract, stop_loss)
        
        result = {
            "parent_order_id": parent_id,
            "take_profit_order_id": tp_id,
            "stop_loss_order_id": sl_id,
            "symbol": params.symbol,
            "action": params.action.value,
            "quantity": params.quantity,
            "entry_type": "LIMIT" if params.entry_price else "MARKET",
            "entry_price": params.entry_price,
            "take_profit_price": params.take_profit_price,
            "stop_loss_price": params.stop_loss_price,
            "transmitted": params.transmit,
            "status": "Submitted" if params.transmit else "Created (Requires TWS Confirmation)",
            "timestamp": datetime.now().isoformat()
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        logger.error(f"Error placing bracket order: {e}")
        return json.dumps({
            "error": str(e)
        }, indent=2)

@mcp.tool(
    name="ibkr_cancel_order",
    annotations={
        "title": "Cancel Order",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def ibkr_cancel_order(params: CancelOrderInput) -> str:
    """
    Cancel a pending order.
    
    Cancels an order that has not been fully filled.
    Partially filled orders will cancel the remaining quantity.
    
    Args:
        params: Cancel order parameters with order ID
    
    Returns:
        Cancellation confirmation
        
    Examples:
        Cancel order #123:
        ibkr_cancel_order(order_id=123)
    """
    try:
        if not bridge.connected:
            return json.dumps({
                "error": "Not connected to TWS. Call ibkr_connect first."
            }, indent=2)
        
        # Cancel the order
        bridge.cancelOrder(params.order_id)
        
        # Wait briefly for status update
        await asyncio.sleep(1)
        
        # Check order status
        order_status = bridge.orders.get(params.order_id, {})
        
        result = {
            "order_id": params.order_id,
            "action": "CANCEL",
            "status": order_status.get('status', 'Cancel Requested'),
            "timestamp": datetime.now().isoformat()
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        logger.error(f"Error canceling order: {e}")
        return json.dumps({
            "error": str(e)
        }, indent=2)

@mcp.tool(
    name="ibkr_get_order_status",
    annotations={
        "title": "Get Order Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def ibkr_get_order_status(order_id: int) -> str:
    """
    Get status of a specific order.
    
    Retrieves detailed status information for an order including
    fill status, remaining quantity, and average fill price.
    
    Args:
        order_id: Order ID to query
    
    Returns:
        Detailed order status
        
    Examples:
        Check status of order #123:
        ibkr_get_order_status(order_id=123)
    """
    try:
        if not bridge.connected:
            return json.dumps({
                "error": "Not connected to TWS. Call ibkr_connect first."
            }, indent=2)
        
        # Get order from cache
        order = bridge.orders.get(order_id)
        
        if not order:
            # Request fresh order status
            bridge.reqOpenOrders()
            await asyncio.sleep(2)
            order = bridge.orders.get(order_id)
        
        if order:
            return json.dumps(order, indent=2)
        else:
            return json.dumps({
                "error": f"Order {order_id} not found"
            }, indent=2)
            
    except Exception as e:
        logger.error(f"Error getting order status: {e}")
        return json.dumps({
            "error": str(e)
        }, indent=2)

@mcp.tool(
    name="ibkr_get_positions",
    annotations={
        "title": "Get Current Positions",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def ibkr_get_positions() -> str:
    """
    Get all current positions in the account.
    
    Retrieves all open positions including stocks, options, and futures
    with current values and unrealized P&L.
    
    Returns:
        List of all positions with details
        
    Examples:
        Get all positions:
        ibkr_get_positions()
    """
    try:
        if not bridge.connected:
            return json.dumps({
                "error": "Not connected to TWS. Call ibkr_connect first."
            }, indent=2)
        
        # Request positions
        bridge.positions.clear()
        bridge.reqPositions()
        
        # Wait for positions
        await asyncio.sleep(3)
        
        # Cancel positions updates
        bridge.cancelPositions()
        
        if bridge.positions:
            return json.dumps(bridge.positions, indent=2)
        else:
            return json.dumps({
                "positions": [],
                "message": "No open positions"
            }, indent=2)
            
    except Exception as e:
        logger.error(f"Error getting positions: {e}")
        return json.dumps({
            "error": str(e)
        }, indent=2)

@mcp.tool(
    name="ibkr_get_account_summary",
    annotations={
        "title": "Get Account Summary",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def ibkr_get_account_summary() -> str:
    """
    Get account summary including net liquidation value, cash, and buying power.
    
    Retrieves key account metrics:
    - Net Liquidation Value
    - Total Cash
    - Buying Power
    - Gross Position Value
    - Unrealized P&L
    - Realized P&L
    
    Returns:
        Account summary with all key metrics
        
    Examples:
        Get account summary:
        ibkr_get_account_summary()
    """
    try:
        if not bridge.connected:
            return json.dumps({
                "error": "Not connected to TWS. Call ibkr_connect first."
            }, indent=2)
        
        # Request account summary
        bridge.account_values.clear()
        bridge.reqAccountSummary(
            1, "All",
            "NetLiquidation,TotalCashValue,BuyingPower,GrossPositionValue,UnrealizedPnL,RealizedPnL"
        )
        
        # Wait for data
        await asyncio.sleep(3)
        
        # Cancel account summary
        bridge.cancelAccountSummary(1)
        
        if bridge.account_values:
            # Format the response
            summary = {}
            for account, values in bridge.account_values.items():
                summary[account] = {
                    tag: info['value'] for tag, info in values.items()
                }
            return json.dumps(summary, indent=2)
        else:
            return json.dumps({
                "error": "No account data available"
            }, indent=2)
            
    except Exception as e:
        logger.error(f"Error getting account summary: {e}")
        return json.dumps({
            "error": str(e)
        }, indent=2)

@mcp.tool(
    name="ibkr_get_historical_data",
    annotations={
        "title": "Get Historical Data",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def ibkr_get_historical_data(params: HistoricalDataInput) -> str:
    """
    Get historical price data for analysis.
    
    Retrieves historical bars for technical analysis and backtesting.
    
    Args:
        params: Historical data parameters
    
    Returns:
        Historical price bars
        
    Examples:
        Get 1 day of 5-minute bars for SPY:
        ibkr_get_historical_data(symbol="SPY", duration="1 D", bar_size="5 mins")
        
        Get 1 week of hourly bars:
        ibkr_get_historical_data(symbol="AAPL", duration="1 W", bar_size="1 hour")
    """
    try:
        if not bridge.connected:
            return json.dumps({
                "error": "Not connected to TWS. Call ibkr_connect first."
            }, indent=2)
        
        # Create contract
        contract = create_contract(
            params.symbol,
            params.sec_type,
            "SMART",
            "USD"
        )
        
        # Get historical data
        bars = await bridge.get_historical_data_async(
            contract,
            params.duration,
            params.bar_size,
            params.what_to_show,
            params.use_rth
        )
        
        if bars:
            result = {
                "symbol": params.symbol,
                "duration": params.duration,
                "bar_size": params.bar_size,
                "bar_count": len(bars),
                "bars": bars
            }
            return json.dumps(result, indent=2)
        else:
            return json.dumps({
                "error": f"No historical data available for {params.symbol}"
            }, indent=2)
            
    except Exception as e:
        logger.error(f"Error getting historical data: {e}")
        return json.dumps({
            "error": str(e)
        }, indent=2)

# ==========================================
# Server Information Tools
# ==========================================

@mcp.tool(
    name="ibkr_get_connection_status",
    annotations={
        "title": "Get Connection Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def ibkr_get_connection_status() -> str:
    """
    Check current TWS connection status.
    
    Returns current connection status including whether the connection
    is active and ready for trading operations.
    
    Returns:
        Detailed connection status
        
    Examples:
        Check if connected:
        ibkr_get_connection_status()
    """
    status = {
        "connected": bridge.connected,
        "next_order_id": bridge.next_order_id if bridge.connected else None,
        "api_version": API_VERSION,
        "timestamp": datetime.now().isoformat()
    }
    
    if bridge.connected:
        status["message"] = "Connected and ready for trading"
    else:
        status["message"] = "Not connected. Call ibkr_connect to establish connection"
    
    return json.dumps(status, indent=2)

@mcp.tool(
    name="ibkr_server_info",
    annotations={
        "title": "Get Server Information",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def ibkr_server_info() -> str:
    """
    Get IBKR MCP Server information and capabilities.
    
    Returns comprehensive information about the server including
    version, supported features, and available tools.
    
    Returns:
        Server information and capabilities
    """
    info = {
        "name": "IBKR MCP Server",
        "version": API_VERSION,
        "description": "Interactive Brokers TWS API integration via Model Context Protocol",
        "features": [
            "Real-time market data",
            "Option chain analysis with Greeks",
            "Advanced order types (market, limit, stop, bracket, trailing)",
            "Account and portfolio management",
            "Position tracking with P&L",
            "Historical data retrieval",
            "0DTE options optimization"
        ],
        "supported_securities": [
            "Stocks (STK)",
            "Options (OPT)",
            "Futures (FUT)",
            "Index (IND)",
            "Forex (CASH)",
            "Combos (BAG)"
        ],
        "connection_status": {
            "connected": bridge.connected,
            "ready": bridge.connected
        },
        "tools_count": 15,
        "author": "IBKR MCP Development",
        "documentation": "https://interactivebrokers.com/api"
    }
    
    return json.dumps(info, indent=2)

# Entry point
if __name__ == "__main__":
    # MCP servers run as long-running processes
    # The FastMCP framework handles the stdio communication
    import sys
    import signal
    
    # Handle graceful shutdown
    def signal_handler(sig, frame):
        if bridge.connected:
            bridge.disconnect_async()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run the server
    mcp.run()
