import os
import re
import json
import time
import random
import html
import requests
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter

try:
    import ccxt
except Exception:
    ccxt = None


# ============================================================
# CONFIGURATION
# ============================================================

STATE_FILE = Path("processed_leads.json")
SPOT_FILE = Path("master_spot_list.json")
PERP_FILE = Path("master_perp_list.json")
REVIEW_FILE = Path("review_leads.json")
CATEGORY_CURSOR_FILE = Path("category_cursor.json")

# Optional future contract-level perp list.
# Expected format:
# {
#   "generated_at": "...",
#   "contracts": ["0x...", "SolanaMint..."]
# }
PERP_CONTRACTS_FILE = Path("master_perp_contracts.json")

# Lead generation quota.
TARGET_QUOTA = 3

# Maximum number of candidates that will consume a CoinGecko detail API call.
# Symbol-based perp drops are cheap and do not count against this limit.
MAX_CANDIDATE_DETAIL_CHECKS = 15

# Safety cap to avoid scanning an enormous candidate list in one run.
MAX_CANDIDATES_TO_SCAN = 120

# Master list freshness window.
MAX_LIST_AGE_HOURS = 24

# Rotate CoinGecko categories to reduce API pressure.
CATEGORIES_PER_RUN = 4

# Critical executive fix:
# If a token symbol already appears in the perp master list, drop it before
# spending a CoinGecko detail API call.
#
# Set to False only if you want to preserve possible symbol-collision review leads.
PRE_FILTER_SYMBOL_HARD_DROP = True

# If pre-filter is disabled, this controls how aggressively symbol-only perp matches
# are hard-dropped versus routed to manual review.
PERP_SYMBOL_MIN_SOURCES_FOR_AUTO_DROP = 3

# CoinGecko public/demo API settings.
COINGECKO_BASE_URL = os.environ.get(
    "COINGECKO_BASE_URL",
    "https://api.coingecko.com/api/v3",
).rstrip("/")

COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "").strip()
COINGECKO_API_HEADER = os.environ.get("COINGECKO_API_HEADER", "x-cg-demo-api-key").strip()

# Strict CoinGecko rate limiting.
COINGECKO_MIN_INTERVAL_SECONDS = 3.0
COINGECKO_MAX_RETRIES = 3
COINGECKO_429_BASE_SLEEP_SECONDS = 60
COINGECKO_MAX_SLEEP_SECONDS = 180

_coingecko_last_call = 0.0

# CCXT exchange IDs.
CEX_EXCHANGE_IDS = [
    "binance",
    "binanceusdm",
    "binancecoinm",
    "okx",
    "bybit",
    "mexc",
    "gate",
]

# CoinGecko chain IDs.
TARGET_CHAINS_CG = [
    "solana",
    "base",
    "binance-smart-chain",
]

# DexScreener chain IDs.
TARGET_CHAINS_DS = [
    "solana",
    "base",
    "bsc",
]

CHAIN_DISPLAY = {
    "solana": "Solana",
    "base": "Base",
    "binance-smart-chain": "BSC",
    "bsc": "BSC",
}

# CoinGecko category slugs.
# Updated per executive instruction.
CATEGORIES = [
    "decentralized-exchange",
    "yield-farming",
    "real-world-assets-rwa",
    "gaming",
    "layer-1",
    "layer-2",
    "artificial-intelligence",
    "prediction-markets",
    "launchpad",
    "derivatives",
    "infrastructure",
    "services",
]

ZOMBIE_BLOCKLIST = [
    "coinbase",
    "robinhood",
    "binance",
    "kraken",
    "okx",
    "bybit",
    "serum",
    "belt",
    "qi",
    "hakka",
    "wagmi",
    "olympus",
    "wonderland",
    "iron",
    "safemoon",
    "squid",
    "terra",
    "tokenized",
    "wrapped",
]

# Stablecoins/fiat-like assets. We do not pitch these.
NON_TARGET_BASES = {
    "USDT",
    "USDC",
    "USD",
    "DAI",
    "TUSD",
    "FDUSD",
    "USDE",
    "SUSDE",
    "USDS",
    "PYUSD",
    "BUSD",
    "EURC",
    "EUR",
    "GBP",
    "JPY",
    "TRY",
}

# Major/ambiguous symbols that are poor outreach targets or prone to symbol collisions.
MAJOR_AMBIGUOUS_SYMBOLS = {
    "BTC",
    "ETH",
    "SOL",
    "BNB",
    "XRP",
    "ADA",
    "DOGE",
    "DOT",
    "LINK",
    "AVAX",
    "TRX",
    "TON",
    "MATIC",
    "POL",
    "SHIB",
    "LTC",
    "BCH",
    "NEAR",
    "UNI",
    "AAVE",
    "MKR",
    "SNX",
    "CRV",
    "LDO",
    "ARB",
    "OP",
    "IMX",
    "SEI",
    "TIA",
    "JTO",
    "JUP",
    "WIF",
    "BONK",
    "POPCAT",
    "MEW",
    "PNUT",
    "FARTCOIN",
    "TRUMP",
    "HYPE",
    "ENA",
    "SUI",
    "APT",
    "INJ",
    "FET",
    "RNDR",
    "RENDER",
    "TAO",
    "GRT",
    "FIL",
    "ATOM",
    "ALGO",
    "VET",
    "ICP",
    "ETC",
    "XLM",
    "HBAR",
    "QNT",
    "CHZ",
    "MANA",
    "SAND",
    "GALA",
    "APE",
    "FLOW",
    "ENJ",
    "SXP",
    "CELO",
    "ROSE",
    "KAVA",
    "ZIL",
    "STX",
    "MINA",
    "KSM",
    "EOS",
    "XTZ",
    "RVN",
    "DASH",
    "ZEC",
    "XMR",
    "BSV",
}

# Size/volume filters.
MCAP_MIN = 300_000
MCAP_MAX = 30_000_000
CG_VOLUME_MIN = 50_000

# DexScreener on-chain life filters.
DEX_LIQUIDITY_MIN_USD = 50_000
DEX_VOLUME_24H_MIN_USD = 20_000

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ============================================================
# UTILITIES
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def norm(value):
    return str(value or "").upper().strip()


def parse_iso(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def save_json(path, data):
    Path(path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_processed_ids():
    data = load_json(STATE_FILE)
    if isinstance(data, list):
        return set(data)
    return set()


def save_processed_ids(processed_ids):
    save_json(STATE_FILE, sorted(list(processed_ids)))


def is_fresh(data):
    if not data:
        return False

    generated_at = parse_iso(data.get("generated_at", ""))
    if not generated_at:
        return False

    age_seconds = (now_utc() - generated_at).total_seconds()
    return age_seconds < MAX_LIST_AGE_HOURS * 3600


def escape_html(value):
    return html.escape(str(value), quote=False)


def strip_html_tags(value):
    return re.sub(r"<[^>]+>", "", value)


# ============================================================
# COINGECKO RATE LIMITER
# ============================================================

def coingecko_headers():
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }

    if COINGECKO_API_KEY and COINGECKO_API_HEADER:
        headers[COINGECKO_API_HEADER] = COINGECKO_API_KEY

    return headers


def coingecko_get(path, params=None):
    """
    Conservative CoinGecko GET wrapper.

    Enforces:
    - minimum 3-second delay before every CoinGecko request
    - 429 retry with 60s / 120s / 180s backoff
    - graceful 404 handling for invalid category slugs
    """
    global _coingecko_last_call

    url = path if str(path).startswith("http") else f"{COINGECKO_BASE_URL}{path}"
    last_status = None

    for attempt in range(COINGECKO_MAX_RETRIES):
        # Mandatory delay before every CoinGecko request.
        time.sleep(COINGECKO_MIN_INTERVAL_SECONDS)

        try:
            resp = requests.get(
                url,
                headers=coingecko_headers(),
                params=params,
                timeout=20,
            )
        except Exception as e:
            last_status = "exception"
            print(f"    [COINGECKO EXCEPTION] {e}")

            if attempt < COINGECKO_MAX_RETRIES - 1:
                time.sleep(5 * (attempt + 1))
                continue

            return None, last_status

        _coingecko_last_call = time.monotonic()
        last_status = resp.status_code

        if resp.status_code == 200:
            try:
                return resp.json(), 200
            except Exception as e:
                print(f"    [COINGECKO JSON PARSE ERROR] {e}")
                return None, 200

        if resp.status_code == 429:
            wait_time = min(
                COINGECKO_429_BASE_SLEEP_SECONDS * (attempt + 1),
                COINGECKO_MAX_SLEEP_SECONDS,
            )
            print(f"    [COINGECKO 429] Rate limited. Sleeping {wait_time}s before retry.")

            if attempt < COINGECKO_MAX_RETRIES - 1:
                time.sleep(wait_time)
                continue

            return None, 429

        if resp.status_code == 404:
            return None, 404

        # Other HTTP errors.
        if attempt < COINGECKO_MAX_RETRIES - 1:
            print(f"    [COINGECKO {resp.status_code}] Retrying after transient error.")
            time.sleep(5 * (attempt + 1))
            continue

        return None, resp.status_code

    return None, last_status


# ============================================================
# CATEGORY ROTATION
# ============================================================

def load_category_cursor():
    data = load_json(CATEGORY_CURSOR_FILE)

    if isinstance(data, dict):
        try:
            cursor = int(data.get("cursor", 0))
            if cursor < 0:
                return 0
            return cursor
        except Exception:
            return 0

    return 0


def save_category_cursor(cursor):
    save_json(
        CATEGORY_CURSOR_FILE,
        {
            "generated_at": now_utc().isoformat(),
            "cursor": int(cursor),
            "categories": CATEGORIES,
        },
    )


def select_categories():
    """
    Select a rotating subset of CoinGecko categories to reduce API pressure.
    """
    if not CATEGORIES:
        return [], 0

    cursor = load_category_cursor() % len(CATEGORIES)
    count = min(CATEGORIES_PER_RUN, len(CATEGORIES))

    selected = [
        CATEGORIES[(cursor + i) % len(CATEGORIES)]
        for i in range(count)
    ]

    next_cursor = (cursor + count) % len(CATEGORIES)
    return selected, next_cursor


# ============================================================
# BEST-EFFORT DEX PERP EXTRAS
# ============================================================

def fetch_hyperliquid_perps():
    """
    Best-effort Hyperliquid perp symbol fetch.

    Expected endpoint:
      POST https://api.hyperliquid.xyz/info
      {"type": "metaAndAssetCtxs"}

    If the endpoint or response shape changes, this safely fails.
    """
    symbols = set()

    try:
        resp = requests.post(
            "https://api.hyperliquid.xyz/info",
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
            },
            json={"type": "metaAndAssetCtxs"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()

        if isinstance(data, list) and data:
            meta = data[0]
            if isinstance(meta, dict):
                universe = meta.get("universe", [])
                if isinstance(universe, list):
                    for item in universe:
                        if not isinstance(item, dict):
                            continue

                        name = norm(item.get("name"))
                        if name and name not in NON_TARGET_BASES:
                            symbols.add(name)

    except Exception as e:
        print(f"    [HYPERLIQUID] Best-effort fetch failed: {e}")

    return symbols


def fetch_dydx_perps():
    """
    Best-effort dYdX v4 perp symbol fetch.

    Expected endpoint:
      GET https://indexer.dydx.trade/v4/perpetualMarkets

    If the endpoint or response shape changes, this safely fails.
    """
    symbols = set()

    try:
        resp = requests.get(
            "https://indexer.dydx.trade/v4/perpetualMarkets",
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()

        markets = data.get("markets", {})
        if isinstance(markets, dict):
            for market_key in markets.keys():
                base = norm(str(market_key).split("-")[0])
                if base and base not in NON_TARGET_BASES:
                    symbols.add(base)

    except Exception as e:
        print(f"    [DYDX] Best-effort fetch failed: {e}")

    return symbols


# ============================================================
# MASTER LIST BUILDER
# ============================================================

def build_master_lists():
    """
    Builds:
    - master_spot_list.json: base symbols listed on CEX spot markets
    - master_perp_list.json: base symbols with existing perp/derivative markets

    Also tracks how many independent venues list each perp symbol.
    """
    if ccxt is None:
        raise RuntimeError("ccxt is not installed. The GitHub workflow must install it.")

    print("=== BUILDING MASTER CEX SPOT + PERP LISTS ===")

    spot_symbols = set()
    perp_symbols = set()
    perp_symbol_counts = Counter()
    stats = {}

    for exchange_id in CEX_EXCHANGE_IDS:
        print(f"  [CEX] {exchange_id}: fetching markets...")

        try:
            exchange_cls = getattr(ccxt, exchange_id)
            exchange = exchange_cls(
                {
                    "enableRateLimit": True,
                    "timeout": 20000,
                }
            )

            markets = exchange.fetch_markets()

            if isinstance(markets, dict):
                market_list = list(markets.values())
            else:
                market_list = markets

            spot_count = 0
            perp_count = 0

            for market in market_list:
                if not isinstance(market, dict):
                    continue

                if market.get("active") is False:
                    continue

                base = norm(market.get("base"))
                quote = norm(market.get("quote"))

                if not base or not quote:
                    continue

                market_type = norm(market.get("type"))

                is_spot = market.get("spot") is True or market_type == "SPOT"
                is_perp = market.get("swap") is True or market_type == "SWAP"

                if is_spot:
                    spot_count += 1
                    if base not in NON_TARGET_BASES:
                        spot_symbols.add(base)

                if is_perp:
                    perp_count += 1
                    if base not in NON_TARGET_BASES:
                        perp_symbols.add(base)
                        perp_symbol_counts[base] += 1

            stats[exchange_id] = {
                "spot_markets": spot_count,
                "perp_markets": perp_count,
                "total_markets": len(market_list),
            }

            print(f"  [CEX] {exchange_id}: spot={spot_count}, perp={perp_count}")

        except Exception as e:
            stats[exchange_id] = {"error": str(e)}
            print(f"  [CEX] {exchange_id}: ERROR: {e}")

        time.sleep(1)

    print("  [DEX PERPS] Fetching Hyperliquid and dYdX best-effort...")

    hyperliquid_symbols = fetch_hyperliquid_perps()
    dydx_symbols = fetch_dydx_perps()

    perp_symbols.update(hyperliquid_symbols)
    perp_symbols.update(dydx_symbols)

    for symbol in hyperliquid_symbols:
        perp_symbol_counts[symbol] += 1

    for symbol in dydx_symbols:
        perp_symbol_counts[symbol] += 1

    stats["hyperliquid"] = {"perp_symbols": len(hyperliquid_symbols)}
    stats["dydx"] = {"perp_symbols": len(dydx_symbols)}

    spot_output = {
        "generated_at": now_utc().isoformat(),
        "source": "ccxt_cex_spot",
        "stats": stats,
        "total_unique_spot_base_symbols": len(spot_symbols),
        "symbols": sorted(list(spot_symbols)),
    }

    perp_output = {
        "generated_at": now_utc().isoformat(),
        "source": "ccxt_cex_perp_plus_best_effort_dex_perp",
        "stats": stats,
        "total_unique_perp_base_symbols": len(perp_symbols),
        "symbols": sorted(list(perp_symbols)),
        "symbol_source_counts": dict(perp_symbol_counts),
    }

    save_json(SPOT_FILE, spot_output)
    save_json(PERP_FILE, perp_output)

    print(f"\n[SUCCESS] Master spot symbols: {len(spot_symbols)}")
    print(f"[SUCCESS] Master perp symbols: {len(perp_symbols)}")
    print(
        "[SUCCESS] Perp symbols with 2+ sources: "
        f"{sum(1 for v in perp_symbol_counts.values() if v >= 2)}"
    )

    return spot_symbols, perp_symbols, perp_symbol_counts


def load_perp_symbol_counts(perp_data):
    if not perp_data:
        return {}

    counts = perp_data.get("symbol_source_counts", {})

    if isinstance(counts, dict) and counts:
        return {norm(k): int(v) for k, v in counts.items() if norm(k)}

    symbols = perp_data.get("symbols", [])
    return {norm(s): 1 for s in symbols if norm(s)}


def ensure_master_lists():
    """
    Loads fresh master lists if available.
    Otherwise rebuilds them.
    Falls back to stale lists if rebuild fails.
    """
    spot_data = load_json(SPOT_FILE)
    perp_data = load_json(PERP_FILE)

    if is_fresh(spot_data) and is_fresh(perp_data):
        spot_symbols = set(spot_data.get("symbols", []))
        perp_symbols = set(perp_data.get("symbols", []))
        perp_symbol_counts = load_perp_symbol_counts(perp_data)

        print("=== LOADED FRESH MASTER LISTS ===")
        print(f"  Spot symbols: {len(spot_symbols)}")
        print(f"  Perp symbols: {len(perp_symbols)}")
        print(f"  Perp symbol count entries: {len(perp_symbol_counts)}")

        return spot_symbols, perp_symbols, perp_symbol_counts

    print("=== MASTER LISTS MISSING OR STALE ===")

    try:
        spot_symbols, perp_symbols, perp_symbol_counts = build_master_lists()
        if perp_symbols:
            return spot_symbols, perp_symbols, perp_symbol_counts
        print("[WARNING] Built perp list is empty.")
    except Exception as e:
        print(f"[ERROR] Failed to build master lists: {e}")

    if perp_data and perp_data.get("symbols"):
        print("[WARNING] Falling back to stale master lists.")
        spot_symbols = set(spot_data.get("symbols", [])) if spot_data else set()
        perp_symbols = set(perp_data.get("symbols", []))
        perp_symbol_counts = load_perp_symbol_counts(perp_data)
        return spot_symbols, perp_symbols, perp_symbol_counts

    return set(), set(), {}


def load_perp_contracts():
    """
    Optional future-proofing.

    If we later add on-chain perp venues that expose underlying token contracts,
    store them in master_perp_contracts.json as:

    {
      "generated_at": "...",
      "contracts": ["0x...", "SolanaMint..."]
    }
    """
    data = load_json(PERP_CONTRACTS_FILE)
    if not data:
        return set()

    contracts = data.get("contracts", [])
    if isinstance(contracts, list):
        return {norm(c) for c in contracts if norm(c)}

    return set()


def classify_perp_risk(symbol, contract, perp_symbols, perp_symbol_counts, perp_contracts=None):
    """
    Returns:
      risk: "auto_send" | "manual_review" | "hard_drop"
      reason: human-readable explanation
    """
    perp_contracts = perp_contracts or set()

    if contract and norm(contract) in perp_contracts:
        return "hard_drop", "Contract-level perp market exists."

    if symbol in perp_symbols:
        source_count = perp_symbol_counts.get(symbol, 1)

        if source_count >= PERP_SYMBOL_MIN_SOURCES_FOR_AUTO_DROP:
            return (
                "hard_drop",
                f"Perp symbol appears on {source_count} independent venues. "
                "High-confidence existing perp.",
            )

        return (
            "manual_review",
            f"Perp symbol appears on only {source_count} venue. "
            "Possible symbol collision. Manual review required.",
        )

    return "auto_send", "No perp market detected."


# ============================================================
# COINGECKO DISCOVERY
# ============================================================

def fetch_coingecko_candidates():
    """
    Discovery layer:
    Find mid-cap tokens with meaningful spot volume in rotating categories.

    Returns:
        (candidates, rate_limited)
    """
    print("\n=== MODULE 1: COINGECKO DISCOVERY ===")

    selected_categories, next_cursor = select_categories()
    save_category_cursor(next_cursor)

    print(f"  Using category batch: {selected_categories}")
    print(f"  Next category cursor: {next_cursor}")

    candidates = []
    seen_ids = set()
    rate_limited = False

    for category in selected_categories:
        print(f"  Fetching category: {category}...")

        data, status = coingecko_get(
            "/coins/markets",
            params={
                "vs_currency": "usd",
                "category": category,
                "order": "volume_desc",
                "per_page": "100",
                "page": "1",
                "sparkline": "false",
            },
        )

        if status == 429:
            print("    [COINGECKO RATE LIMITED] Stopping category discovery for this run.")
            rate_limited = True
            break

        if status == 404:
            print(f"    [INVALID CATEGORY] {category} returned 404. Skipping.")
            continue

        if status != 200 or not data:
            print(f"    [SKIP] Category returned status {status}.")
            continue

        if not isinstance(data, list):
            continue

        for coin in data:
            if not isinstance(coin, dict):
                continue

            coin_id = coin.get("id")
            if not coin_id or coin_id in seen_ids:
                continue

            name = coin.get("name", "")
            symbol = norm(coin.get("symbol"))

            if not name or not symbol:
                continue

            if symbol in NON_TARGET_BASES:
                continue

            if symbol in MAJOR_AMBIGUOUS_SYMBOLS:
                continue

            if any(blocked in name.lower() for blocked in ZOMBIE_BLOCKLIST):
                continue

            try:
                mcap = float(coin.get("market_cap", 0) or 0)
                volume = float(coin.get("total_volume", 0) or 0)
            except Exception:
                continue

            if MCAP_MIN <= mcap <= MCAP_MAX and volume >= CG_VOLUME_MIN:
                seen_ids.add(coin_id)
                candidates.append(
                    {
                        "id": coin_id,
                        "name": name,
                        "symbol": symbol,
                        "market_cap": mcap,
                        "cg_volume_24h": volume,
                    }
                )

    print(f"    Found {len(candidates)} baseline CoinGecko candidates.")
    return candidates, rate_limited


def get_coin_details(coin_id):
    """
    Enrichment layer:
    Fetch website, Twitter, and contract addresses from CoinGecko.

    Returns:
        (details_or_none, status)
    """
    return coingecko_get(f"/coins/{coin_id}")


# ============================================================
# DEXSCREENER ON-CHAIN VALIDATION
# ============================================================

def fetch_dexscreener_best_pair(contract_address):
    """
    Validation layer:
    Use DexScreener to verify the token contract has real liquidity and volume
    on Solana, Base, or BSC.
    """
    url = f"https://api.dexscreener.com/latest/dex/tokens/{contract_address}"
    headers = {"User-Agent": USER_AGENT}

    try:
        resp = requests.get(url, headers=headers, timeout=20)

        if resp.status_code != 200:
            print(f"    [ERROR] DexScreener returned {resp.status_code}")
            return None

        data = resp.json()
        pairs = data.get("pairs", [])

        best_pair = None
        best_score = 0

        for pair in pairs:
            if not isinstance(pair, dict):
                continue

            chain_id = pair.get("chainId", "")
            if chain_id not in TARGET_CHAINS_DS:
                continue

            base_token = pair.get("baseToken", {})
            if not isinstance(base_token, dict):
                continue

            base_address = base_token.get("address", "")
            if base_address and norm(base_address) != norm(contract_address):
                continue

            try:
                liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                volume = float(pair.get("volume", {}).get("h24", 0) or 0)
            except Exception:
                continue

            if liquidity < DEX_LIQUIDITY_MIN_USD or volume < DEX_VOLUME_24H_MIN_USD:
                continue

            score = volume + (liquidity * 0.25)

            if score > best_score:
                best_score = score
                best_pair = {
                    "chain": chain_id,
                    "chain_display": CHAIN_DISPLAY.get(chain_id, chain_id),
                    "dex": pair.get("dexId", "unknown"),
                    "pair_address": pair.get("pairAddress", ""),
                    "base_token_symbol": base_token.get("symbol", ""),
                    "base_token_address": base_address,
                    "quote_token_symbol": pair.get("quoteToken", {}).get("symbol", ""),
                    "liquidity_usd": liquidity,
                    "volume_h24": volume,
                    "price_change_h24": pair.get("priceChange", {}).get("h24", None),
                    "url": pair.get("url", ""),
                }

        return best_pair

    except Exception as e:
        print(f"    [ERROR] DexScreener validation failed: {e}")
        return None


# ============================================================
# PERP-GAP FILTERING
# ============================================================

def find_perp_gap_leads(
    candidates,
    processed_ids,
    perp_symbols,
    perp_symbol_counts,
    spot_symbols,
    perp_contracts=None,
):
    """
    Core strategic filter:
    1. Skip already processed tokens.
    2. Pre-drop tokens whose symbol already exists in the perp master list.
    3. Only then spend a CoinGecko detail API call.
    4. Resolve candidate by contract address.
    5. Validate live DEX liquidity/volume by contract.
    6. Classify remaining perp risk.
    7. Prioritize tokens already listed on CEX spot.

    Returns:
        (auto_leads, review_leads, rate_limited)
    """
    print("\n=== MODULE 2: RATE-LIMITED PERP-GAP FILTERING ===")

    auto_leads = []
    review_leads = []
    rate_limited = False

    perp_contracts = perp_contracts or set()

    random.shuffle(candidates)

    scanned = 0
    detail_checks = 0

    for coin in candidates:
        if len(auto_leads) >= TARGET_QUOTA and len(review_leads) >= TARGET_QUOTA:
            print("  [QUOTA MET] Found enough auto and review leads.")
            break

        if scanned >= MAX_CANDIDATES_TO_SCAN:
            print(f"  [SCAN LIMIT MET] Scanned {scanned} candidates this run.")
            break

        coin_id = coin["id"]
        symbol = coin["symbol"]

        if coin_id in processed_ids:
            continue

        scanned += 1

        # Critical API-saving fix:
        # Drop immediately if symbol already has a perp market.
        if PRE_FILTER_SYMBOL_HARD_DROP and symbol in perp_symbols:
            print(f"  [DROP] {coin['name']} ({symbol}) | Reason: Already has perp market.")
            processed_ids.add(coin_id)
            continue

        # If pre-filter is disabled, still avoid wasting detail calls on
        # high-confidence multi-venue perp symbols.
        if not PRE_FILTER_SYMBOL_HARD_DROP:
            early_source_count = perp_symbol_counts.get(symbol, 0)
            if symbol in perp_symbols and early_source_count >= PERP_SYMBOL_MIN_SOURCES_FOR_AUTO_DROP:
                print(
                    f"  [DROP] {coin['name']} ({symbol}) | "
                    f"Reason: Perp symbol appears on {early_source_count} venues."
                )
                processed_ids.add(coin_id)
                continue

        if detail_checks >= MAX_CANDIDATE_DETAIL_CHECKS:
            print(f"  [DETAIL LIMIT MET] Used {detail_checks} CoinGecko detail calls.")
            break

        detail_checks += 1
        print(f"  Processing candidate: {coin['name']} ({symbol})...")

        details, status = get_coin_details(coin_id)

        if status == 429:
            print("    [COINGECKO RATE LIMITED] Stopping candidate enrichment for this run.")
            rate_limited = True
            break

        if status != 200 or not details:
            print(f"    [TRANSIENT/SKIP] CoinGecko detail status={status}. Not marking processed.")
            continue

        links = details.get("links", {})
        platforms = details.get("platforms", {})

        homepages = links.get("homepage", [])
        website = next(
            (url for url in homepages if url and str(url).startswith("http")),
            "",
        )

        if not website:
            print("    [DROP] No website found.")
            processed_ids.add(coin_id)
            continue

        twitter = links.get("twitter_screen_name", "")

        target_contract = None
        target_chain = None

        for chain in TARGET_CHAINS_CG:
            if chain in platforms and platforms[chain]:
                target_contract = platforms[chain]
                target_chain = chain
                break

        if not target_contract:
            print("    [DROP] No target-chain contract address found.")
            processed_ids.add(coin_id)
            continue

        dex_pair = fetch_dexscreener_best_pair(target_contract)
        time.sleep(1)

        if not dex_pair:
            print("    [DROP] No qualifying live DEX pair found on target chains.")
            processed_ids.add(coin_id)
            continue

        risk, reason = classify_perp_risk(
            symbol=symbol,
            contract=target_contract,
            perp_symbols=perp_symbols,
            perp_symbol_counts=perp_symbol_counts,
            perp_contracts=perp_contracts,
        )

        priority = (
            "CEX_SPOT_NO_PERP"
            if symbol in spot_symbols
            else "DEX_MOMENTUM_NO_PERP"
        )

        lead = {
            "gecko_id": coin_id,
            "name": coin["name"],
            "symbol": symbol,
            "market_cap": coin["market_cap"],
            "cg_volume_24h": coin["cg_volume_24h"],
            "website": website,
            "twitter": f"@{twitter}" if twitter else "Not Found",
            "contract": target_contract,
            "chain": target_chain,
            "chain_display": CHAIN_DISPLAY.get(target_chain, target_chain),
            "dex": dex_pair,
            "priority": priority,
            "perp_risk": risk,
            "perp_reason": reason,
            "perp_symbol_source_count": perp_symbol_counts.get(symbol, 0),
        }

        if risk == "hard_drop":
            print(f"    [DROP] {reason}")
            processed_ids.add(coin_id)
            continue

        if risk == "manual_review":
            print(f"    [REVIEW] {reason}")
            review_leads.append(lead)
            processed_ids.add(coin_id)
            continue

        print(
            f"    [AUTO PASS] {reason} | "
            f"Liq=${dex_pair['liquidity_usd']:,.0f}, "
            f"Vol=${dex_pair['volume_h24']:,.0f}, "
            f"Chain={dex_pair['chain_display']}, "
            f"Priority={priority}"
        )

        auto_leads.append(lead)

    print(
        f"\n    Validation Complete: "
        f"{len(auto_leads)} auto-send leads, "
        f"{len(review_leads)} review leads."
    )

    return auto_leads, review_leads, rate_limited


def save_review_leads(review_leads):
    if not review_leads:
        return

    existing = load_json(REVIEW_FILE)

    if not isinstance(existing, list):
        existing = []

    by_id = {}

    for item in existing:
        if isinstance(item, dict) and item.get("gecko_id"):
            by_id[item["gecko_id"]] = item

    for lead in review_leads:
        by_id[lead["gecko_id"]] = lead

    save_json(REVIEW_FILE, list(by_id.values()))

    print(f"\n[REVIEW] Saved {len(review_leads)} symbol-conflict leads to {REVIEW_FILE}")


# ============================================================
# OUTREACH GENERATION
# ============================================================

def generate_personalized_dm(lead):
    name = lead["name"]
    symbol = lead["symbol"]
    twitter = lead.get("twitter", "")
    chain = lead["chain_display"]
    dex = lead["dex"]

    volume = dex["volume_h24"]
    liquidity = dex["liquidity_usd"]

    if twitter and twitter != "Not Found":
        greeting = f"Hey {twitter}, been tracking {name} ({symbol}) on {chain}."
    else:
        greeting = f"Hey {name} team, been tracking {symbol} on {chain}."

    if lead["priority"] == "CEX_SPOT_NO_PERP":
        insight = (
            f"You’re already listed on CEX spot markets and doing ${volume:,.0f} in 24h DEX volume "
            f"with ${liquidity:,.0f} liquidity, but I don’t see an active perp market for {symbol} "
            f"on the major venues I track."
        )
    else:
        insight = (
            f"You’re doing ${volume:,.0f} in 24h DEX volume with ${liquidity:,.0f} liquidity, "
            f"but I don’t see an active perp market for {symbol} on the major venues I track."
        )

    template = (
        f"{greeting}\n\n"
        f"{insight}\n\n"
        f"That usually means derivative demand is either leaking to CEXs or being suppressed entirely.\n\n"
        f"We built Vibe Trading to help protocols capture that value:\n"
        f"- The Solution: List a native perp on Vibe for $0 USDC. Deposit idle treasury tokens to our vault to back the Open Interest.\n"
        f"- The Result: You earn perpetual trading fees to fund operations without selling a single token on the spot market.\n\n"
        f"Mind if I send over a quick 1-pager on how the vault mechanics work?"
    )

    return template


def build_telegram_html_message(lead, dm_text):
    dex = lead["dex"]

    if lead["priority"] == "CEX_SPOT_NO_PERP":
        signal_label = "CEX SPOT LISTED, NO PERP DETECTED"
    else:
        signal_label = "DEX MOMENTUM, NO PERP DETECTED"

    price_change = dex.get("price_change_h24")
    price_change_str = "N/A"

    if price_change is not None:
        try:
            price_change_str = f"{float(price_change):+.2f}%"
        except Exception:
            price_change_str = str(price_change)

    message = (
        "🎯 <b>NEW PERP-GAP LEAD</b>\n\n"
        f"📛 <b>Project:</b> {escape_html(lead['name'])} ({escape_html(lead['symbol'])})\n"
        f"⛓️ <b>Chain:</b> {escape_html(lead['chain_display'])}\n"
        f"🧠 <b>Signal:</b> {escape_html(signal_label)}\n\n"
        f"📂 <b>Market Cap:</b> ${lead['market_cap']:,.0f}\n"
        f"📊 <b>CoinGecko 24h Volume:</b> ${lead['cg_volume_24h']:,.0f}\n"
        f"💧 <b>DEX Liquidity:</b> ${dex['liquidity_usd']:,.0f}\n"
        f"🔥 <b>DEX 24h Volume:</b> ${dex['volume_h24']:,.0f}\n"
        f"📈 <b>24h Price Change:</b> {escape_html(price_change_str)}\n"
        f"🏦 <b>DEX:</b> {escape_html(dex['dex'])}\n\n"
        f"🔗 <b>Website:</b> {escape_html(lead['website'])}\n"
        f"🐦 <b>Twitter:</b> {escape_html(lead['twitter'])}\n"
        f"📜 <b>Contract:</b> <code>{escape_html(lead['contract'])}</code>\n\n"
        f"📝 <b>READY-TO-SEND DM:</b>\n"
        f"<pre>{escape_html(dm_text)}</pre>"
    )

    return message


# ============================================================
# TELEGRAM DELIVERY
# ============================================================

def send_telegram_message(html_text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("    [TELEGRAM WARNING] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": html_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            return True

        print(f"    [TELEGRAM HTML ERROR] {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"    [TELEGRAM EXCEPTION] {e}")

    plain_text = strip_html_tags(html_text)
    fallback_payload = {
        "chat_id": chat_id,
        "text": plain_text,
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=fallback_payload, timeout=15)
        if resp.status_code == 200:
            print("    [TELEGRAM] Sent using plain-text fallback.")
            return True

        print(f"    [TELEGRAM PLAIN ERROR] {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"    [TELEGRAM PLAIN EXCEPTION] {e}")

    return False


def deliver_leads(leads, processed_ids):
    print("\n=== MODULE 3: DELIVERY ===")

    delivered = 0

    for lead in leads:
        dm_text = generate_personalized_dm(lead)
        message = build_telegram_html_message(lead, dm_text)

        success = send_telegram_message(message)

        if success:
            print(f"  [TELEGRAM] ✅ Sent perp-gap lead for {lead['name']}")
            processed_ids.add(lead["gecko_id"])
            delivered += 1
        else:
            print(f"  [TELEGRAM] ❌ Failed to send lead for {lead['name']}")

        time.sleep(2)

    print(f"\nDelivery Complete: {delivered}/{len(leads)} leads sent.")
    return delivered


# ============================================================
# MAIN
# ============================================================

def main():
    print("=== STARTING RATE-LIMITED PERP-GAP PIPELINE ===")

    processed_ids = load_processed_ids()
    save_processed_ids(processed_ids)

    print(f"Loaded {len(processed_ids)} previously processed CoinGecko IDs.")

    spot_symbols, perp_symbols, perp_symbol_counts = ensure_master_lists()
    perp_contracts = load_perp_contracts()

    if not perp_symbols:
        print("\n[ERROR] Could not build or load a perp master list.")
        print("[ACTION] Aborting to avoid pitching tokens that may already have perps.")
        save_processed_ids(processed_ids)
        return

    candidates, discovery_rate_limited = fetch_coingecko_candidates()

    if not candidates:
        print("\nNo baseline candidates found from CoinGecko.")

        if discovery_rate_limited:
            print("[WARNING] CoinGecko was rate-limited during discovery.")
            print("[ACTION] Consider adding COINGECKO_API_KEY or reducing CATEGORIES_PER_RUN.")

        save_processed_ids(processed_ids)
        return

    auto_leads, review_leads, validation_rate_limited = find_perp_gap_leads(
        candidates=candidates,
        processed_ids=processed_ids,
        perp_symbols=perp_symbols,
        perp_symbol_counts=perp_symbol_counts,
        spot_symbols=spot_symbols,
        perp_contracts=perp_contracts,
    )

    if review_leads:
        save_review_leads(review_leads)

    if auto_leads:
        deliver_leads(auto_leads, processed_ids)
    else:
        print("\nNo auto-send perp-gap leads passed validation this run.")

    save_processed_ids(processed_ids)

    if discovery_rate_limited or validation_rate_limited:
        print("\n[WARNING] CoinGecko rate limiting affected this run.")
        print("[ACTION] If this repeats, add a CoinGecko API key or reduce run frequency.")

    print("\n=== PIPELINE COMPLETE ===")


if __name__ == "__main__":
    main()
