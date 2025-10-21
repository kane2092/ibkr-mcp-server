"""
Test suite for IBKR MCP Server
================================
Tests all major functionality of the server.
Run with: python -m pytest tests/test_ibkr_server.py
"""

import asyncio
import json
import pytest
from unittest.mock import Mock, patch, AsyncMock
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from ibkr_mcp_server import (
    ConnectionInput, ConnectionMode,
    StockQuoteInput, ResponseFormat,
    MarketOrderInput, OrderAction, SecType,
    LimitOrderInput, BracketOrderInput,
    ibkr_connect, ibkr_disconnect,
    ibkr_get_stock_quote, ibkr_place_market_order,
    ibkr_place_limit_order, ibkr_place_bracket_order,
    ibkr_get_positions, ibkr_get_account_summary
)

# Test fixtures
@pytest.fixture
def mock_bridge():
    """Create a mock bridge for testing."""
    with patch('ibkr_mcp_server.bridge') as mock:
        mock.connected = False
        mock.connect_async = AsyncMock(return_value=True)
        mock.disconnect_async = Mock()
        mock.get_market_data_async = AsyncMock(return_value={
            'symbol': 'SPY',
            'BID_PRICE': 550.50,
            'ASK_PRICE': 550.55,
            'LAST_PRICE': 550.52,
            'VOLUME': 1000000,
            'timestamp': '2024-01-01T10:00:00'
        })
        mock.place_order_async = AsyncMock(return_value=123)
        mock.positions = []
        mock.account_values = {}
        yield mock

@pytest.mark.asyncio
async def test_connect_paper(mock_bridge):
    """Test connecting to paper trading."""
    params = ConnectionInput(mode=ConnectionMode.PAPER)
    result = await ibkr_connect(params)
    
    data = json.loads(result)
    assert data['status'] == 'connected'
    assert data['mode'] == 'paper'
    assert data['port'] == 7497

@pytest.mark.asyncio
async def test_connect_live(mock_bridge):
    """Test connecting to live trading."""
    params = ConnectionInput(mode=ConnectionMode.LIVE, client_id=2)
    result = await ibkr_connect(params)
    
    data = json.loads(result)
    assert data['status'] == 'connected'
    assert data['mode'] == 'live'
    assert data['port'] == 7496
    assert data['client_id'] == 2

@pytest.mark.asyncio
async def test_disconnect(mock_bridge):
    """Test disconnecting from TWS."""
    mock_bridge.connected = True
    result = await ibkr_disconnect()
    
    data = json.loads(result)
    assert data['status'] == 'disconnected'
    mock_bridge.disconnect_async.assert_called_once()

@pytest.mark.asyncio
async def test_get_stock_quote(mock_bridge):
    """Test getting stock quote."""
    mock_bridge.connected = True
    params = StockQuoteInput(symbol="SPY", format=ResponseFormat.JSON)
    result = await ibkr_get_stock_quote(params)
    
    data = json.loads(result)
    assert data['symbol'] == 'SPY'
    assert data['BID_PRICE'] == 550.50
    assert data['ASK_PRICE'] == 550.55
    assert data['spread'] == 0.05

@pytest.mark.asyncio
async def test_place_market_order(mock_bridge):
    """Test placing market order."""
    mock_bridge.connected = True
    params = MarketOrderInput(
        symbol="SPY",
        action=OrderAction.BUY,
        quantity=100,
        transmit=True
    )
    result = await ibkr_place_market_order(params)
    
    data = json.loads(result)
    assert data['order_id'] == 123
    assert data['symbol'] == 'SPY'
    assert data['action'] == 'BUY'
    assert data['quantity'] == 100
    assert data['order_type'] == 'MARKET'

@pytest.mark.asyncio
async def test_place_limit_order(mock_bridge):
    """Test placing limit order."""
    mock_bridge.connected = True
    params = LimitOrderInput(
        symbol="AAPL",
        action=OrderAction.BUY,
        quantity=50,
        limit_price=175.50,
        transmit=False
    )
    result = await ibkr_place_limit_order(params)
    
    data = json.loads(result)
    assert data['order_id'] == 123
    assert data['symbol'] == 'AAPL'
    assert data['limit_price'] == 175.50
    assert data['transmitted'] == False

@pytest.mark.asyncio
async def test_place_bracket_order(mock_bridge):
    """Test placing bracket order."""
    mock_bridge.connected = True
    params = BracketOrderInput(
        symbol="SPX",
        sec_type=SecType.OPTION,
        action=OrderAction.BUY,
        quantity=1,
        entry_price=10.00,
        take_profit_price=15.00,
        stop_loss_price=7.00,
        transmit=True
    )
    result = await ibkr_place_bracket_order(params)
    
    data = json.loads(result)
    assert data['parent_order_id'] == 123
    assert data['symbol'] == 'SPX'
    assert data['entry_price'] == 10.00
    assert data['take_profit_price'] == 15.00
    assert data['stop_loss_price'] == 7.00

@pytest.mark.asyncio
async def test_bracket_order_validation():
    """Test bracket order validation."""
    with pytest.raises(ValueError) as exc_info:
        params = BracketOrderInput(
            symbol="SPX",
            action=OrderAction.BUY,
            quantity=1,
            take_profit_price=5.00,  # Invalid: less than stop loss
            stop_loss_price=10.00
        )
    assert "take_profit must be > stop_loss" in str(exc_info.value)

@pytest.mark.asyncio
async def test_get_positions(mock_bridge):
    """Test getting positions."""
    mock_bridge.connected = True
    mock_bridge.positions = [
        {
            'symbol': 'SPY',
            'position': 100,
            'avg_cost': 545.00,
            'market_value': 55050.00,
            'unrealized_pnl': 550.00
        }
    ]
    
    result = await ibkr_get_positions()
    data = json.loads(result)
    
    assert len(data) == 1
    assert data[0]['symbol'] == 'SPY'
    assert data[0]['position'] == 100

@pytest.mark.asyncio
async def test_get_account_summary(mock_bridge):
    """Test getting account summary."""
    mock_bridge.connected = True
    mock_bridge.account_values = {
        'DU1234567': {
            'NetLiquidation': {'value': '100000.00', 'currency': 'USD'},
            'TotalCashValue': {'value': '50000.00', 'currency': 'USD'},
            'BuyingPower': {'value': '200000.00', 'currency': 'USD'}
        }
    }
    
    result = await ibkr_get_account_summary()
    data = json.loads(result)
    
    assert 'DU1234567' in data
    assert data['DU1234567']['NetLiquidation'] == '100000.00'
    assert data['DU1234567']['BuyingPower'] == '200000.00'

@pytest.mark.asyncio
async def test_not_connected_error(mock_bridge):
    """Test error when not connected."""
    mock_bridge.connected = False
    params = StockQuoteInput(symbol="SPY")
    result = await ibkr_get_stock_quote(params)
    
    data = json.loads(result)
    assert 'error' in data
    assert 'Not connected' in data['error']

# Integration tests (requires TWS running)
@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_full_workflow():
    """
    Integration test for complete workflow.
    Requires TWS to be running with paper trading enabled.
    """
    # Skip if not in integration mode
    if not os.environ.get('RUN_INTEGRATION_TESTS'):
        pytest.skip("Integration tests not enabled")
    
    # 1. Connect
    params = ConnectionInput(mode=ConnectionMode.PAPER)
    result = await ibkr_connect(params)
    assert 'connected' in result
    
    # 2. Get quote
    quote_params = StockQuoteInput(symbol="SPY")
    quote = await ibkr_get_stock_quote(quote_params)
    assert 'BID_PRICE' in quote
    
    # 3. Place order
    order_params = LimitOrderInput(
        symbol="SPY",
        action=OrderAction.BUY,
        quantity=1,
        limit_price=1.00,  # Very low price to avoid fill
        transmit=False
    )
    order = await ibkr_place_limit_order(order_params)
    assert 'order_id' in order
    
    # 4. Get positions
    positions = await ibkr_get_positions()
    assert 'positions' in positions or isinstance(positions, list)
    
    # 5. Disconnect
    result = await ibkr_disconnect()
    assert 'disconnected' in result

if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, '-v'])
