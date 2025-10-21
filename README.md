# IBKR MCP Server

A comprehensive Model Context Protocol (MCP) server for Interactive Brokers TWS API integration, designed for algorithmic trading with a focus on 0DTE SPX options.

## 🚀 Features

### Market Data
- Real-time stock quotes with bid/ask spreads
- Option chain retrieval with Greeks (Delta, Gamma, Theta, Vega)
- Index quotes (SPX, NDX, VIX)
- Historical price data for analysis

### Order Management
- **Market Orders**: Immediate execution at best price
- **Limit Orders**: Execute at specified price or better
- **Stop Orders**: Trigger orders at stop price
- **Bracket Orders**: Entry + Take Profit + Stop Loss
- **Trailing Stop Orders**: Dynamic stop that follows price
- **Manual/Auto Transmission**: Option for TWS confirmation

### Account Management
- Portfolio positions with P&L tracking
- Account summary (Net Liquidation, Cash, Buying Power)
- Real-time order status tracking
- Execution reports with commissions

### Risk Management
- Position sizing tools
- Risk/reward calculation for brackets
- Greeks analysis for options
- Idempotent order submission

## 📋 Prerequisites

1. **Interactive Brokers Account**
   - Active IB account (Paper or Live)
   - TWS or IB Gateway installed

2. **TWS Configuration**
   - Enable API connections in TWS
   - Configure socket port (7497 for paper, 7496 for live)
   - Set "Read-Only API" to unchecked for trading

3. **Python Environment**
   - Python 3.9 or higher
   - MCP-compatible environment (Claude Desktop)

## 🔧 Installation

### 1. Clone Repository
```bash
git clone https://github.com/yourusername/ibkr-mcp-server.git
cd ibkr-mcp-server
```

### 2. Create Virtual Environment
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Claude Desktop

Add to Claude Desktop configuration (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "ibkr": {
      "command": "python",
      "args": [
        "C:/path/to/ibkr-mcp-server/src/ibkr_mcp_server.py"
      ]
    }
  }
}
```

## 🎯 Usage Examples

### Connect to TWS
```python
# Paper trading
ibkr_connect(mode="paper")

# Live trading
ibkr_connect(mode="live")
```

### Get Market Data
```python
# Stock quote
ibkr_get_stock_quote(symbol="SPY")

# Option chain for 0DTE SPX
ibkr_get_option_chain(underlying="SPX", include_greeks=True)
```

### Place Orders
```python
# Market order
ibkr_place_market_order(
    symbol="SPY",
    action="BUY",
    quantity=100,
    transmit=True
)

# Limit order
ibkr_place_limit_order(
    symbol="AAPL",
    action="BUY",
    quantity=50,
    limit_price=175.50
)

# Bracket order for 0DTE options
ibkr_place_bracket_order(
    symbol="SPX",
    sec_type="OPT",
    action="BUY",
    quantity=1,
    entry_price=10.00,
    take_profit_price=15.00,  # +50% target
    stop_loss_price=7.00       # -30% stop
)
```

### Account Information
```python
# Get positions
ibkr_get_positions()

# Get account summary
ibkr_get_account_summary()

# Check order status
ibkr_get_order_status(order_id=123)
```

## 🛠️ Available Tools

| Tool | Description |
|------|-------------|
| `ibkr_connect` | Connect to TWS/IB Gateway |
| `ibkr_disconnect` | Disconnect from TWS |
| `ibkr_get_connection_status` | Check connection status |
| `ibkr_get_stock_quote` | Get real-time stock quote |
| `ibkr_get_option_chain` | Get option chain with Greeks |
| `ibkr_place_market_order` | Place market order |
| `ibkr_place_limit_order` | Place limit order |
| `ibkr_place_bracket_order` | Place bracket order |
| `ibkr_cancel_order` | Cancel pending order |
| `ibkr_get_order_status` | Get order status |
| `ibkr_get_positions` | Get current positions |
| `ibkr_get_account_summary` | Get account metrics |
| `ibkr_get_historical_data` | Get historical bars |
| `ibkr_server_info` | Get server information |

## 🎮 TWS Setup Guide

### 1. Enable API Access
1. Open TWS
2. Go to **File → Global Configuration**
3. Select **API → Settings**
4. Enable **Enable ActiveX and Socket Clients**
5. Configure port:
   - Paper: 7497
   - Live: 7496
6. Optional: Set **Master API client ID** to "0"

### 2. Security Settings
1. Under **API → Precautions**:
   - Uncheck **Read-Only API** for trading
   - Set **Bypass Order Precautions** based on preference
2. Add trusted IP: 127.0.0.1

### 3. Market Data Subscriptions
Ensure you have appropriate market data subscriptions:
- US Stocks (Network A/B)
- Options (OPRA)
- Index data (for SPX)

## 📊 0DTE Trading Workflow

### Example 0DTE SPX Strategy
```python
# 1. Connect
ibkr_connect(mode="paper")

# 2. Get SPX price
spx_quote = ibkr_get_stock_quote(symbol="SPX")

# 3. Get 0DTE options (today's expiry)
options = ibkr_get_option_chain(
    underlying="SPX",
    strike_range=(5500, 5600),  # Near ATM
    include_greeks=True
)

# 4. Place bracket order on selected option
ibkr_place_bracket_order(
    symbol="SPX",
    sec_type="OPT",
    action="BUY",
    quantity=1,
    entry_price=8.50,
    take_profit_price=12.75,  # +50% target
    stop_loss_price=5.95      # -30% stop
)

# 5. Monitor position
ibkr_get_positions()
```

## 🔒 Security Considerations

1. **API Credentials**: Never hardcode credentials
2. **Paper vs Live**: Always test in paper first
3. **Order Validation**: Use `transmit=False` for manual review
4. **Position Limits**: Implement position sizing rules
5. **Rate Limiting**: Respect IB API rate limits

## 🐛 Troubleshooting

### Connection Issues
```
Error: Failed to connect to TWS
```
**Solution**:
- Verify TWS is running
- Check API settings enabled
- Confirm correct port (7497/7496)
- Check firewall settings

### No Market Data
```
Error: No market data available
```
**Solution**:
- Verify market data subscriptions
- Check if market is open
- Ensure proper contract specification

### Order Rejected
```
Error: Order rejected - insufficient buying power
```
**Solution**:
- Check account balance
- Verify position limits
- Review margin requirements

## 📈 Performance Tips

1. **Connection Management**: Keep persistent connection
2. **Data Caching**: Cache frequently used data
3. **Batch Operations**: Group related requests
4. **Error Handling**: Implement retry logic
5. **Logging**: Enable detailed logging for debugging

## 📝 License

MIT License - See LICENSE file for details

## 🤝 Contributing

Contributions welcome! Please read CONTRIBUTING.md

## 📧 Support

For issues and questions:
- GitHub Issues: [Create Issue](https://github.com/yourusername/ibkr-mcp-server/issues)
- IB API Docs: [Official Documentation](https://interactivebrokers.github.io/tws-api/)

## 🏗️ Roadmap

- [ ] Advanced order types (OCO, Conditional)
- [ ] Options strategies (Spreads, Straddles)
- [ ] Real-time P&L tracking
- [ ] Risk analytics dashboard
- [ ] Backtesting integration
- [ ] Machine learning signals

## ⚠️ Disclaimer

This software is for educational purposes. Trading involves substantial risk of loss. Past performance does not guarantee future results. Always test thoroughly in paper trading before using with real funds.
