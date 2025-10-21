# 0DTE SPX Options Trading Guide

## Overview

This guide provides comprehensive strategies and workflows for trading 0DTE (zero days to expiration) SPX options using the IBKR MCP Server.

## Table of Contents
1. [Understanding 0DTE Options](#understanding-0dte-options)
2. [Setup and Configuration](#setup-and-configuration)
3. [Trading Strategies](#trading-strategies)
4. [Risk Management](#risk-management)
5. [Automated Workflows](#automated-workflows)
6. [Performance Monitoring](#performance-monitoring)

## Understanding 0DTE Options

### What are 0DTE Options?
0DTE options are options contracts that expire on the same day they are traded. SPX (S&P 500 Index) offers daily expirations Monday through Friday.

### Key Characteristics
- **High Gamma**: Rapid price changes near strikes
- **Accelerated Theta Decay**: Time value erodes quickly
- **Liquidity**: SPX 0DTE options are highly liquid
- **Settlement**: Cash-settled, European-style
- **Multiplier**: $100 per point

### Trading Hours
- **Pre-market**: 4:00 AM - 9:30 AM ET
- **Regular**: 9:30 AM - 4:00 PM ET
- **Settlement**: 4:00 PM ET (closing price)

## Setup and Configuration

### 1. TWS Configuration for 0DTE
```python
# Optimal TWS settings for 0DTE trading
TWS_SETTINGS = {
    "api_settings": {
        "socket_port": 7497,  # Paper trading
        "enable_activex": True,
        "read_only_api": False,
        "bypass_order_precautions": True,  # For faster execution
        "master_client_id": 0
    },
    "market_data": {
        "subscriptions": [
            "US Securities Snapshot and Futures Value Bundle",
            "OPRA (US Options)",
            "US Securities and Commodities Bundle"
        ]
    }
}
```

### 2. Connect to TWS
```python
# Initialize connection
await ibkr_connect(mode="paper", client_id=1)

# Verify connection
status = await ibkr_get_connection_status()
print(f"Connected: {status}")
```

## Trading Strategies

### Strategy 1: ATM Straddle at Market Open

**Concept**: Buy both call and put at-the-money options at market open to profit from volatility.

```python
async def atm_straddle_strategy():
    """Execute ATM straddle strategy at market open."""
    
    # 1. Get current SPX price
    spx_quote = await ibkr_get_stock_quote(symbol="SPX")
    current_price = spx_quote['LAST_PRICE']
    
    # 2. Calculate ATM strike (round to nearest 5)
    atm_strike = round(current_price / 5) * 5
    
    # 3. Get today's expiry (0DTE)
    today = datetime.now().strftime("%Y%m%d")
    
    # 4. Get option chain for ATM strike
    options = await ibkr_get_option_chain(
        underlying="SPX",
        expiry=today,
        strike_range=(atm_strike - 10, atm_strike + 10),
        include_greeks=True
    )
    
    # 5. Find ATM call and put
    atm_call = next((opt for opt in options 
                     if opt['strike'] == atm_strike and opt['right'] == 'C'), None)
    atm_put = next((opt for opt in options 
                    if opt['strike'] == atm_strike and opt['right'] == 'P'), None)
    
    if not atm_call or not atm_put:
        return "ATM options not found"
    
    # 6. Calculate position size based on risk
    account = await ibkr_get_account_summary()
    buying_power = float(account['BuyingPower'])
    max_risk = buying_power * 0.02  # Risk 2% of account
    
    # 7. Place straddle orders
    # Buy Call
    call_order = await ibkr_place_limit_order(
        symbol="SPX",
        sec_type="OPT",
        action="BUY",
        quantity=1,
        limit_price=atm_call['ASK_PRICE'],
        transmit=True
    )
    
    # Buy Put
    put_order = await ibkr_place_limit_order(
        symbol="SPX",
        sec_type="OPT",
        action="BUY",
        quantity=1,
        limit_price=atm_put['ASK_PRICE'],
        transmit=True
    )
    
    return {
        "strategy": "ATM Straddle",
        "strike": atm_strike,
        "call_order": call_order,
        "put_order": put_order,
        "total_cost": (atm_call['ASK_PRICE'] + atm_put['ASK_PRICE']) * 100,
        "breakeven_up": atm_strike + atm_call['ASK_PRICE'] + atm_put['ASK_PRICE'],
        "breakeven_down": atm_strike - (atm_call['ASK_PRICE'] + atm_put['ASK_PRICE'])
    }
```

### Strategy 2: Iron Condor for Range-Bound Markets

**Concept**: Sell OTM call and put spreads to collect premium in low volatility.

```python
async def iron_condor_strategy(width=10, delta_target=0.15):
    """
    Execute Iron Condor strategy.
    
    Args:
        width: Strike width for spreads
        delta_target: Target delta for short strikes (0.15 = 15 delta)
    """
    
    # 1. Get SPX price and options
    spx_quote = await ibkr_get_stock_quote(symbol="SPX")
    current_price = spx_quote['LAST_PRICE']
    
    # 2. Get option chain
    today = datetime.now().strftime("%Y%m%d")
    options = await ibkr_get_option_chain(
        underlying="SPX",
        expiry=today,
        include_greeks=True
    )
    
    # 3. Find strikes based on delta
    # Find ~15 delta call (OTM)
    call_strikes = [opt for opt in options 
                   if opt['right'] == 'C' 
                   and opt['strike'] > current_price
                   and abs(opt.get('delta', 0) - delta_target) < 0.05]
    
    # Find ~15 delta put (OTM)
    put_strikes = [opt for opt in options 
                  if opt['right'] == 'P' 
                  and opt['strike'] < current_price
                  and abs(abs(opt.get('delta', 0)) - delta_target) < 0.05]
    
    if not call_strikes or not put_strikes:
        return "Suitable strikes not found"
    
    # Select strikes
    short_call_strike = call_strikes[0]['strike']
    long_call_strike = short_call_strike + width
    short_put_strike = put_strikes[0]['strike']
    long_put_strike = short_put_strike - width
    
    # 4. Create Iron Condor orders
    orders = []
    
    # Call spread (sell short, buy long)
    orders.append(await ibkr_place_limit_order(
        symbol="SPX", sec_type="OPT", action="SELL",
        quantity=1, limit_price=call_strikes[0]['BID_PRICE'],
        transmit=False
    ))
    
    orders.append(await ibkr_place_limit_order(
        symbol="SPX", sec_type="OPT", action="BUY",
        quantity=1, limit_price=call_strikes[0]['ASK_PRICE'] + width,
        transmit=False
    ))
    
    # Put spread (sell short, buy long)
    orders.append(await ibkr_place_limit_order(
        symbol="SPX", sec_type="OPT", action="SELL",
        quantity=1, limit_price=put_strikes[0]['BID_PRICE'],
        transmit=False
    ))
    
    orders.append(await ibkr_place_limit_order(
        symbol="SPX", sec_type="OPT", action="BUY",
        quantity=1, limit_price=put_strikes[0]['ASK_PRICE'] - width,
        transmit=True  # Transmit all orders
    ))
    
    # Calculate metrics
    credit_received = (call_strikes[0]['BID_PRICE'] + put_strikes[0]['BID_PRICE']) * 100
    max_loss = (width * 100) - credit_received
    
    return {
        "strategy": "Iron Condor",
        "strikes": {
            "long_put": long_put_strike,
            "short_put": short_put_strike,
            "short_call": short_call_strike,
            "long_call": long_call_strike
        },
        "credit": credit_received,
        "max_loss": max_loss,
        "max_profit": credit_received,
        "breakeven_low": short_put_strike - (credit_received / 100),
        "breakeven_high": short_call_strike + (credit_received / 100),
        "orders": orders
    }
```

### Strategy 3: Directional Butterfly

**Concept**: Low-cost directional play with limited risk.

```python
async def butterfly_strategy(direction="bullish", width=5):
    """
    Execute butterfly spread strategy.
    
    Args:
        direction: 'bullish' or 'bearish'
        width: Strike width between legs
    """
    
    # Get current SPX price
    spx_quote = await ibkr_get_stock_quote(symbol="SPX")
    current_price = spx_quote['LAST_PRICE']
    
    # Calculate strikes
    if direction == "bullish":
        # Bullish butterfly: Buy 1 ATM, Sell 2 OTM, Buy 1 further OTM
        lower_strike = round(current_price / 5) * 5
        middle_strike = lower_strike + width
        upper_strike = middle_strike + width
        option_type = "C"  # Use calls for bullish
    else:
        # Bearish butterfly with puts
        upper_strike = round(current_price / 5) * 5
        middle_strike = upper_strike - width
        lower_strike = middle_strike - width
        option_type = "P"  # Use puts for bearish
    
    # Place butterfly orders
    # Buy 1 lower strike
    order1 = await ibkr_place_limit_order(
        symbol="SPX", sec_type="OPT", action="BUY",
        quantity=1, limit_price=0,  # Market order
        transmit=False
    )
    
    # Sell 2 middle strike
    order2 = await ibkr_place_limit_order(
        symbol="SPX", sec_type="OPT", action="SELL",
        quantity=2, limit_price=0,  # Market order
        transmit=False
    )
    
    # Buy 1 upper strike
    order3 = await ibkr_place_limit_order(
        symbol="SPX", sec_type="OPT", action="BUY",
        quantity=1, limit_price=0,  # Market order
        transmit=True
    )
    
    return {
        "strategy": f"{direction.title()} Butterfly",
        "strikes": {
            "lower": lower_strike,
            "middle": middle_strike,
            "upper": upper_strike
        },
        "max_profit": width * 100,  # At middle strike
        "max_loss": "Net debit paid",
        "orders": [order1, order2, order3]
    }
```

## Risk Management

### Position Sizing Rules

```python
def calculate_position_size(account_value, risk_per_trade=0.02, option_price=10):
    """
    Calculate appropriate position size for 0DTE options.
    
    Args:
        account_value: Total account value
        risk_per_trade: Maximum risk per trade (default 2%)
        option_price: Price per option contract
    
    Returns:
        Number of contracts to trade
    """
    max_risk = account_value * risk_per_trade
    contract_value = option_price * 100  # SPX multiplier
    position_size = int(max_risk / contract_value)
    
    # Never risk more than 5 contracts on 0DTE
    return min(position_size, 5)
```

### Stop Loss Implementation

```python
async def set_stop_loss(order_id, stop_percentage=0.30):
    """
    Set stop loss for option position.
    
    Args:
        order_id: Original order ID
        stop_percentage: Stop loss percentage (30% default)
    """
    # Get order details
    order_status = await ibkr_get_order_status(order_id)
    entry_price = order_status['avg_fill_price']
    
    # Calculate stop price
    stop_price = entry_price * (1 - stop_percentage)
    
    # Place stop order
    stop_order = await ibkr_place_stop_order(
        symbol=order_status['symbol'],
        sec_type="OPT",
        action="SELL" if order_status['action'] == "BUY" else "BUY",
        quantity=order_status['filled'],
        stop_price=stop_price,
        transmit=True
    )
    
    return stop_order
```

### Time-Based Exits

```python
async def time_based_exit(position, exit_time="15:30"):
    """
    Exit position at specified time to avoid gamma risk.
    
    Args:
        position: Current position details
        exit_time: Time to exit (default 3:30 PM ET)
    """
    current_time = datetime.now().strftime("%H:%M")
    
    if current_time >= exit_time:
        # Close position at market
        exit_order = await ibkr_place_market_order(
            symbol=position['symbol'],
            sec_type="OPT",
            action="SELL" if position['side'] == "long" else "BUY",
            quantity=position['quantity'],
            transmit=True
        )
        return exit_order
    
    return None
```

## Automated Workflows

### Complete 0DTE Trading System

```python
class ZeroDTETrader:
    """Automated 0DTE SPX options trading system."""
    
    def __init__(self, strategy="straddle", risk_per_trade=0.02):
        self.strategy = strategy
        self.risk_per_trade = risk_per_trade
        self.positions = []
        self.pnl = 0
        
    async def initialize(self):
        """Connect to TWS and verify settings."""
        await ibkr_connect(mode="paper")
        status = await ibkr_get_connection_status()
        if not status['connected']:
            raise Exception("Failed to connect to TWS")
        
        # Get account info
        self.account = await ibkr_get_account_summary()
        self.buying_power = float(self.account['BuyingPower'])
        
    async def scan_opportunities(self):
        """Scan for trading opportunities based on market conditions."""
        # Get SPX quote and calculate metrics
        spx = await ibkr_get_stock_quote(symbol="SPX")
        
        # Get VIX for volatility assessment
        vix = await ibkr_get_stock_quote(symbol="VIX")
        
        # Decision logic
        if float(vix['LAST_PRICE']) > 20:
            return "high_volatility"  # Good for straddles
        elif float(vix['LAST_PRICE']) < 15:
            return "low_volatility"   # Good for iron condors
        else:
            return "neutral"          # Butterflies or directional
    
    async def execute_trade(self):
        """Execute trade based on strategy and market conditions."""
        market_condition = await self.scan_opportunities()
        
        if self.strategy == "adaptive":
            # Choose strategy based on market
            if market_condition == "high_volatility":
                result = await atm_straddle_strategy()
            elif market_condition == "low_volatility":
                result = await iron_condor_strategy()
            else:
                result = await butterfly_strategy()
        else:
            # Execute specified strategy
            if self.strategy == "straddle":
                result = await atm_straddle_strategy()
            elif self.strategy == "condor":
                result = await iron_condor_strategy()
            elif self.strategy == "butterfly":
                result = await butterfly_strategy()
        
        self.positions.append(result)
        return result
    
    async def monitor_positions(self):
        """Monitor and manage open positions."""
        current_positions = await ibkr_get_positions()
        
        for position in current_positions:
            # Check stop loss
            if position['unrealized_pnl'] < -(self.risk_per_trade * self.buying_power):
                # Exit position
                await ibkr_place_market_order(
                    symbol=position['symbol'],
                    action="SELL" if position['position'] > 0 else "BUY",
                    quantity=abs(position['position']),
                    transmit=True
                )
                print(f"Stop loss triggered for {position['symbol']}")
            
            # Check profit target (50% of max profit)
            elif position['unrealized_pnl'] > (position['market_value'] * 0.5):
                # Take profit
                await ibkr_place_market_order(
                    symbol=position['symbol'],
                    action="SELL" if position['position'] > 0 else "BUY",
                    quantity=abs(position['position']),
                    transmit=True
                )
                print(f"Profit target reached for {position['symbol']}")
    
    async def end_of_day_cleanup(self):
        """Close all positions before market close."""
        positions = await ibkr_get_positions()
        
        for position in positions:
            if "SPX" in position['symbol']:
                # Close position
                await ibkr_place_market_order(
                    symbol=position['symbol'],
                    action="SELL" if position['position'] > 0 else "BUY",
                    quantity=abs(position['position']),
                    transmit=True
                )
        
        # Calculate daily P&L
        account_end = await ibkr_get_account_summary()
        daily_pnl = float(account_end['RealizedPnL'])
        print(f"Daily P&L: ${daily_pnl}")
        
        return daily_pnl
    
    async def run(self):
        """Main trading loop."""
        await self.initialize()
        
        # Trading hours: 9:35 AM - 3:45 PM ET
        while True:
            current_time = datetime.now()
            
            # Market open logic (9:35 AM)
            if current_time.hour == 9 and current_time.minute == 35:
                await self.execute_trade()
            
            # Monitor positions every 5 minutes
            if current_time.minute % 5 == 0:
                await self.monitor_positions()
            
            # End of day (3:45 PM)
            if current_time.hour == 15 and current_time.minute == 45:
                await self.end_of_day_cleanup()
                break
            
            # Sleep for 1 minute
            await asyncio.sleep(60)

# Run the automated system
async def main():
    trader = ZeroDTETrader(strategy="adaptive", risk_per_trade=0.02)
    await trader.run()

if __name__ == "__main__":
    asyncio.run(main())
```

## Performance Monitoring

### Daily Performance Tracker

```python
class PerformanceTracker:
    """Track and analyze 0DTE trading performance."""
    
    def __init__(self):
        self.trades = []
        self.daily_results = []
        
    async def log_trade(self, trade_data):
        """Log trade details for analysis."""
        trade_data['timestamp'] = datetime.now()
        self.trades.append(trade_data)
        
    async def calculate_metrics(self):
        """Calculate performance metrics."""
        if not self.trades:
            return None
        
        # Win rate
        winners = [t for t in self.trades if t['pnl'] > 0]
        win_rate = len(winners) / len(self.trades)
        
        # Average win/loss
        avg_win = sum(t['pnl'] for t in winners) / len(winners) if winners else 0
        losers = [t for t in self.trades if t['pnl'] < 0]
        avg_loss = sum(t['pnl'] for t in losers) / len(losers) if losers else 0
        
        # Profit factor
        gross_profit = sum(t['pnl'] for t in winners)
        gross_loss = abs(sum(t['pnl'] for t in losers))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        
        # Sharpe ratio (simplified daily)
        returns = [t['pnl'] for t in self.trades]
        avg_return = sum(returns) / len(returns)
        std_return = (sum((r - avg_return) ** 2 for r in returns) / len(returns)) ** 0.5
        sharpe = (avg_return / std_return) * (252 ** 0.5) if std_return > 0 else 0
        
        return {
            "total_trades": len(self.trades),
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "sharpe_ratio": sharpe,
            "total_pnl": sum(t['pnl'] for t in self.trades)
        }
    
    async def generate_report(self):
        """Generate daily performance report."""
        metrics = await self.calculate_metrics()
        
        report = f"""
        ========== Daily 0DTE Performance Report ==========
        Date: {datetime.now().strftime('%Y-%m-%d')}
        
        Trade Statistics:
        - Total Trades: {metrics['total_trades']}
        - Win Rate: {metrics['win_rate']:.2%}
        - Average Win: ${metrics['avg_win']:.2f}
        - Average Loss: ${metrics['avg_loss']:.2f}
        - Profit Factor: {metrics['profit_factor']:.2f}
        - Sharpe Ratio: {metrics['sharpe_ratio']:.2f}
        
        P&L Summary:
        - Total P&L: ${metrics['total_pnl']:.2f}
        
        Top Performing Strategy: {self.get_best_strategy()}
        ==================================================
        """
        
        return report
    
    def get_best_strategy(self):
        """Identify best performing strategy."""
        strategy_pnl = {}
        for trade in self.trades:
            strategy = trade.get('strategy', 'unknown')
            if strategy not in strategy_pnl:
                strategy_pnl[strategy] = 0
            strategy_pnl[strategy] += trade['pnl']
        
        if strategy_pnl:
            return max(strategy_pnl, key=strategy_pnl.get)
        return "N/A"
```

## Best Practices

### 1. Pre-Market Preparation
- Review overnight futures movement
- Check economic calendar for events
- Identify key support/resistance levels
- Set daily loss limit

### 2. Entry Timing
- **9:35-10:00 AM**: High volatility, good for straddles
- **10:30-11:30 AM**: Settling period, iron condors work well
- **2:00-3:00 PM**: Gamma acceleration, careful with additions
- **3:30-4:00 PM**: Exit period, close positions

### 3. Position Management
- Never average down on 0DTE
- Use bracket orders for defined risk
- Exit losing trades quickly
- Scale out of winners

### 4. Risk Rules
- Maximum 2% risk per trade
- Daily loss limit: 6% of account
- No more than 3 concurrent positions
- Always use stops

### 5. Record Keeping
- Log all trades with rationale
- Track performance by strategy
- Review daily for improvements
- Adjust based on market regime

## Troubleshooting

### Common Issues and Solutions

1. **Orders Not Filling**
   - Use SMART routing
   - Widen bid-ask tolerance
   - Check market hours

2. **High Slippage**
   - Use limit orders
   - Trade liquid strikes only
   - Avoid market orders in fast markets

3. **Connection Drops**
   - Implement reconnection logic
   - Use persistent order types
   - Monitor connection status

4. **Gamma Risk**
   - Exit before 3:30 PM
   - Use spreads vs naked options
   - Monitor delta changes

## Conclusion

Trading 0DTE SPX options requires discipline, proper risk management, and systematic execution. This guide provides the foundation for building a robust trading system using the IBKR MCP Server.

Remember: Start with paper trading, validate strategies thoroughly, and never risk more than you can afford to lose.
