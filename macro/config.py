"""macro/config.py — All series and ticker definitions for the macro monitoring system."""

# ---------------------------------------------------------------------------
# FRED Series
# ---------------------------------------------------------------------------
FRED_SERIES = {
    # US GROWTH
    "GDPC1": {"name": "US Real GDP", "country": "US", "category": "growth", "frequency": "quarterly", "unit": "%"},
    "A191RL1Q225SBEA": {"name": "US GDP Growth Rate", "country": "US", "category": "growth", "frequency": "quarterly", "unit": "%"},
    "INDPRO": {"name": "US Industrial Production", "country": "US", "category": "growth", "frequency": "monthly", "unit": "index"},
    "RSAFS": {"name": "US Retail Sales", "country": "US", "category": "growth", "frequency": "monthly", "unit": "$M"},
    "UMCSENT": {"name": "US Consumer Sentiment", "country": "US", "category": "growth", "frequency": "monthly", "unit": "index"},
    "HOUST": {"name": "US Housing Starts", "country": "US", "category": "growth", "frequency": "monthly", "unit": "K"},
    "PERMIT": {"name": "US Building Permits", "country": "US", "category": "growth", "frequency": "monthly", "unit": "K"},

    # US EMPLOYMENT
    "UNRATE": {"name": "US Unemployment Rate", "country": "US", "category": "employment", "frequency": "monthly", "unit": "%"},
    "ICSA": {"name": "US Initial Jobless Claims", "country": "US", "category": "employment", "frequency": "weekly", "unit": "K"},
    "PAYEMS": {"name": "US Nonfarm Payrolls", "country": "US", "category": "employment", "frequency": "monthly", "unit": "K"},
    "JTSJOL": {"name": "US Job Openings", "country": "US", "category": "employment", "frequency": "monthly", "unit": "K"},
    "JTSQUR": {"name": "US Quits Rate", "country": "US", "category": "employment", "frequency": "monthly", "unit": "%"},
    "JTSLDR": {"name": "US Layoffs/Discharges", "country": "US", "category": "employment", "frequency": "monthly", "unit": "K"},

    # US INFLATION
    "CPIAUCSL": {"name": "US CPI", "country": "US", "category": "inflation", "frequency": "monthly", "unit": "index"},
    "CPILFESL": {"name": "US Core CPI", "country": "US", "category": "inflation", "frequency": "monthly", "unit": "index"},
    "PCEPILFE": {"name": "US Core PCE", "country": "US", "category": "inflation", "frequency": "monthly", "unit": "index"},

    # US YIELDS & RATES
    "DGS2": {"name": "US 2Y Yield", "country": "US", "category": "yields", "frequency": "daily", "unit": "%"},
    "DGS5": {"name": "US 5Y Yield", "country": "US", "category": "yields", "frequency": "daily", "unit": "%"},
    "DGS10": {"name": "US 10Y Yield", "country": "US", "category": "yields", "frequency": "daily", "unit": "%"},
    "DGS30": {"name": "US 30Y Yield", "country": "US", "category": "yields", "frequency": "daily", "unit": "%"},
    "T10Y2Y": {"name": "US 2Y/10Y Spread", "country": "US", "category": "yields", "frequency": "daily", "unit": "%"},
    "FEDFUNDS": {"name": "US Fed Funds Rate", "country": "US", "category": "yields", "frequency": "monthly", "unit": "%"},
    "MORTGAGE30US": {"name": "US 30Y Mortgage", "country": "US", "category": "yields", "frequency": "weekly", "unit": "%"},

    # US RISK & COMMODITIES
    "VIXCLS": {"name": "VIX", "country": "US", "category": "risk", "frequency": "daily", "unit": "index"},
    "DCOILWTICO": {"name": "WTI Crude Oil", "country": "US", "category": "commodities", "frequency": "daily", "unit": "$/bbl"},
    "DCOILBRENTEU": {"name": "Brent Crude Oil", "country": "GLOBAL", "category": "commodities", "frequency": "daily", "unit": "$/bbl"},
    "DTWEXBGS": {"name": "Trade-Weighted USD", "country": "US", "category": "currency", "frequency": "daily", "unit": "index"},

    # INTERNATIONAL RATES
    "IRSTCB01JPM156N": {"name": "Japan BOJ Rate", "country": "JP", "category": "rates_intl", "frequency": "monthly", "unit": "%"},
    "BOERUKM": {"name": "UK BOE Rate", "country": "UK", "category": "rates_intl", "frequency": "monthly", "unit": "%"},
    "ECBMLFR": {"name": "ECB Main Rate", "country": "EU", "category": "rates_intl", "frequency": "monthly", "unit": "%"},

    # INTERNATIONAL YIELDS
    "IRLTLT01JPM156N": {"name": "Japan 10Y JGB", "country": "JP", "category": "yields_intl", "frequency": "monthly", "unit": "%"},
    "IRLTLT01GBM156N": {"name": "UK 10Y Gilt", "country": "UK", "category": "yields_intl", "frequency": "monthly", "unit": "%"},
    "IRLTLT01DEM156N": {"name": "Germany 10Y Bund", "country": "EU", "category": "yields_intl", "frequency": "monthly", "unit": "%"},

    # INTERNATIONAL EMPLOYMENT
    "LRHUTTTTJPM156S": {"name": "Japan Unemployment", "country": "JP", "category": "employment_intl", "frequency": "monthly", "unit": "%"},
    "LRHUTTTTGBM156S": {"name": "UK Unemployment", "country": "UK", "category": "employment_intl", "frequency": "monthly", "unit": "%"},
    "LRHUTTTTEZM156S": {"name": "EU Unemployment", "country": "EU", "category": "employment_intl", "frequency": "monthly", "unit": "%"},

    # INTERNATIONAL CPI
    "JPNCPIALLMINMEI": {"name": "Japan CPI", "country": "JP", "category": "inflation_intl", "frequency": "monthly", "unit": "index"},
    "GBRCPIALLMINMEI": {"name": "UK CPI", "country": "UK", "category": "inflation_intl", "frequency": "monthly", "unit": "index"},
    "EA19CPALTT01GYM": {"name": "EU HICP Inflation", "country": "EU", "category": "inflation_intl", "frequency": "monthly", "unit": "%"},
}

# Split by update frequency for scheduling
FRED_DAILY = [s for s, c in FRED_SERIES.items() if c["frequency"] == "daily"]
FRED_WEEKLY = [s for s, c in FRED_SERIES.items() if c["frequency"] == "weekly"]
FRED_MONTHLY = [s for s, c in FRED_SERIES.items() if c["frequency"] in ("monthly", "quarterly")]

# ---------------------------------------------------------------------------
# Yahoo Finance Tickers
# ---------------------------------------------------------------------------
YAHOO_TICKERS = {
    # US INDICES
    "^GSPC": {"name": "S&P 500", "category": "indices_us"},
    "^NDX": {"name": "Nasdaq 100", "category": "indices_us"},
    "^DJI": {"name": "Dow Jones 30", "category": "indices_us"},
    "^RUT": {"name": "Russell 2000", "category": "indices_us"},
    # EU INDICES
    "^FTSE": {"name": "FTSE 100", "category": "indices_eu"},
    "^GDAXI": {"name": "DAX 40", "category": "indices_eu"},
    "^STOXX50E": {"name": "Euro Stoxx 50", "category": "indices_eu"},
    "^FCHI": {"name": "CAC 40", "category": "indices_eu"},
    # ASIA INDICES
    "^N225": {"name": "Nikkei 225", "category": "indices_asia"},
    "^HSI": {"name": "Hang Seng", "category": "indices_asia"},
    "000001.SS": {"name": "Shanghai Composite", "category": "indices_asia"},
    "^KS11": {"name": "KOSPI", "category": "indices_asia"},
    # VOLATILITY
    "^VIX": {"name": "VIX", "category": "volatility"},
    # MAG 7
    "AAPL": {"name": "Apple", "category": "stocks_mag7"},
    "MSFT": {"name": "Microsoft", "category": "stocks_mag7"},
    "NVDA": {"name": "Nvidia", "category": "stocks_mag7"},
    "GOOGL": {"name": "Alphabet", "category": "stocks_mag7"},
    "AMZN": {"name": "Amazon", "category": "stocks_mag7"},
    "META": {"name": "Meta", "category": "stocks_mag7"},
    "TSLA": {"name": "Tesla", "category": "stocks_mag7"},
    # SEMIS
    "TSM": {"name": "TSMC", "category": "stocks_semis"},
    "AMD": {"name": "AMD", "category": "stocks_semis"},
    "AVGO": {"name": "Broadcom", "category": "stocks_semis"},
    "ARM": {"name": "ARM Holdings", "category": "stocks_semis"},
    "MRVL": {"name": "Marvell", "category": "stocks_semis"},
    # AI
    "PLTR": {"name": "Palantir", "category": "stocks_ai"},
    "CRM": {"name": "Salesforce", "category": "stocks_ai"},
    "SNOW": {"name": "Snowflake", "category": "stocks_ai"},
    "ORCL": {"name": "Oracle", "category": "stocks_ai"},
    # CRYPTO PROXIES
    "MSTR": {"name": "MicroStrategy", "category": "stocks_crypto"},
    "COIN": {"name": "Coinbase", "category": "stocks_crypto"},
    "MARA": {"name": "Marathon Digital", "category": "stocks_crypto"},
    # FINANCIALS
    "JPM": {"name": "JP Morgan", "category": "stocks_financials"},
    "GS": {"name": "Goldman Sachs", "category": "stocks_financials"},
    "BAC": {"name": "Bank of America", "category": "stocks_financials"},
    # ENERGY
    "XOM": {"name": "ExxonMobil", "category": "stocks_energy"},
    "CVX": {"name": "Chevron", "category": "stocks_energy"},
    # DEFENCE
    "LMT": {"name": "Lockheed Martin", "category": "stocks_defence"},
    "RTX": {"name": "RTX", "category": "stocks_defence"},
    # AI ENERGY
    "VST": {"name": "Vistra Energy", "category": "stocks_ai_energy"},
    "CEG": {"name": "Constellation Energy", "category": "stocks_ai_energy"},
    # ETFS
    "SPY": {"name": "S&P 500 ETF", "category": "etfs_broad"},
    "QQQ": {"name": "Nasdaq 100 ETF", "category": "etfs_broad"},
    "IWM": {"name": "Russell 2000 ETF", "category": "etfs_broad"},
    "TLT": {"name": "20Y+ Treasury ETF", "category": "etfs_bonds"},
    "HYG": {"name": "High Yield Bond ETF", "category": "etfs_bonds"},
    "LQD": {"name": "IG Bond ETF", "category": "etfs_bonds"},
    "JNK": {"name": "Junk Bond ETF", "category": "etfs_bonds"},
    "GLD": {"name": "Gold ETF", "category": "etfs_commodities"},
    "SLV": {"name": "Silver ETF", "category": "etfs_commodities"},
    "USO": {"name": "Oil ETF", "category": "etfs_commodities"},
    "XLK": {"name": "Tech Sector", "category": "etfs_sectors"},
    "XLF": {"name": "Financial Sector", "category": "etfs_sectors"},
    "XLE": {"name": "Energy Sector", "category": "etfs_sectors"},
    "XLP": {"name": "Consumer Staples", "category": "etfs_sectors"},
    "KRE": {"name": "Regional Banks ETF", "category": "etfs_sectors"},
    "SOXX": {"name": "Semiconductor ETF", "category": "etfs_sectors"},
    "ARKK": {"name": "ARK Innovation", "category": "etfs_thematic"},
    "URA": {"name": "Uranium ETF", "category": "etfs_commodities"},
    # CURRENCIES
    "DX-Y.NYB": {"name": "DXY", "category": "currencies"},
    "EURUSD=X": {"name": "EUR/USD", "category": "currencies"},
    "GBPUSD=X": {"name": "GBP/USD", "category": "currencies"},
    "JPY=X": {"name": "USD/JPY", "category": "currencies"},
    "CNY=X": {"name": "USD/CNY", "category": "currencies"},
    "AUDUSD=X": {"name": "AUD/USD", "category": "currencies"},
    # COMMODITIES
    "GC=F": {"name": "Gold Futures", "category": "commodities"},
    "SI=F": {"name": "Silver Futures", "category": "commodities"},
    "PL=F": {"name": "Platinum Futures", "category": "commodities"},
    "HG=F": {"name": "Copper Futures", "category": "commodities"},
    "CL=F": {"name": "WTI Crude Futures", "category": "commodities"},
    "BZ=F": {"name": "Brent Crude Futures", "category": "commodities"},
    "NG=F": {"name": "Natural Gas", "category": "commodities"},
    "ZW=F": {"name": "Wheat Futures", "category": "commodities_ag"},
    "ZC=F": {"name": "Corn Futures", "category": "commodities_ag"},
}

# ---------------------------------------------------------------------------
# Direction sentiment — is rising good or bad?
# ---------------------------------------------------------------------------
DIRECTION_SENTIMENT = {
    # GOOD WHEN RISING (up)
    "GDPC1": "up", "A191RL1Q225SBEA": "up", "INDPRO": "up", "RSAFS": "up",
    "UMCSENT": "up", "HOUST": "up", "PERMIT": "up", "PAYEMS": "up", "JTSJOL": "up",
    "^GSPC": "up", "^NDX": "up", "^DJI": "up", "^RUT": "up", "SPY": "up", "QQQ": "up",
    "^FTSE": "up", "^GDAXI": "up", "^N225": "up", "^HSI": "up",
    "HG=F": "up",  # Dr Copper
    # BAD WHEN RISING (down)
    "UNRATE": "down", "ICSA": "down", "JTSLDR": "down",
    "CPIAUCSL": "down", "CPILFESL": "down", "PCEPILFE": "down",
    "DGS2": "down", "DGS5": "down", "DGS10": "down", "DGS30": "down",
    "FEDFUNDS": "down", "MORTGAGE30US": "down",
    "VIXCLS": "down", "^VIX": "down",
    "DCOILWTICO": "down", "DCOILBRENTEU": "down", "CL=F": "down", "BZ=F": "down",
    "DX-Y.NYB": "down", "DTWEXBGS": "down",
    "JPNCPIALLMINMEI": "down", "GBRCPIALLMINMEI": "down", "EA19CPALTT01GYM": "down",
}

# ---------------------------------------------------------------------------
# Default thresholds
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLDS = [
    {"name": "Initial Jobless Claims", "series_key": "ICSA",
     "warning_value": 250000, "alert_value": 300000, "direction": "above",
     "source_voice": "Benjamin Cowen", "context": "300K = recession threshold"},
    {"name": "Unemployment Rate", "series_key": "UNRATE",
     "warning_value": 4.5, "alert_value": 5.0, "direction": "above",
     "source_voice": "system", "context": "Rising unemployment → recession"},
    {"name": "VIX Fear Index", "series_key": "VIXCLS",
     "warning_value": 30, "alert_value": 35, "direction": "above",
     "source_voice": "system", "context": "VIX >35 = crisis territory"},
    {"name": "US 10Y Yield", "series_key": "DGS10",
     "warning_value": 4.5, "alert_value": 5.0, "direction": "above",
     "source_voice": "Dan (Coin Bureau)", "context": "Yields breaking out = financial stress"},
    {"name": "Yield Curve 2Y/10Y", "series_key": "T10Y2Y",
     "warning_value": -0.1, "alert_value": -0.5, "direction": "below",
     "source_voice": "system", "context": "Inversion preceded every US recession in 50y"},
    {"name": "30Y Mortgage Rate", "series_key": "MORTGAGE30US",
     "warning_value": 7.5, "alert_value": 8.0, "direction": "above",
     "source_voice": "system", "context": "High mortgages freeze housing market"},
    {"name": "Brent Crude Oil", "series_key": "DCOILBRENTEU",
     "warning_value": 130, "alert_value": 150, "direction": "above",
     "source_voice": "Larry Fink", "context": "Oil >$150 → global recession"},
    {"name": "USD/JPY Carry Trade", "series_key": "JPY=X",
     "warning_value": 145, "alert_value": 140, "direction": "below",
     "source_voice": "system", "context": "USDJPY <140 = carry trade unwind"},
    {"name": "BOJ Rate", "series_key": "IRSTCB01JPM156N",
     "warning_value": 0.75, "alert_value": 1.0, "direction": "above",
     "source_voice": "system", "context": "BOJ hiking narrows carry spread"},
    {"name": "HYG 1M Change", "series_key": "HYG",
     "warning_value": -5, "alert_value": -10, "direction": "below_pct_1m",
     "source_voice": "system", "context": "HYG dropping = credit stress"},
    {"name": "KRE 1M Change", "series_key": "KRE",
     "warning_value": -10, "alert_value": -20, "direction": "below_pct_1m",
     "source_voice": "system", "context": "KRE collapse preceded SVB 2023"},
    {"name": "Copper 3M Change", "series_key": "HG=F",
     "warning_value": -10, "alert_value": -20, "direction": "below_pct_3m",
     "source_voice": "system", "context": "Dr Copper falling = economy contracting"},
]
