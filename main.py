import os
import re
import json
import time
import random
import html
import requests
from pathlib import Path
from datetime import datetime, timezone

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

TARGET_QUOTA = 5
MAX_LIST_AGE_HOURS = 24
MAX_CANDIDATES_TO_PROCESS = 80

# CEX exchange IDs supported by CCXT.
# Binance spot and Binance futures are separate in CCXT.
CEX_EXCHANGE_IDS = [
    "binance",
    "binanceusdm",
    "binancecoinm",
    "okx",
    "bybit",
    "mexc",
    "gate",
]

# CoinGecko chain IDs
TARGET_CHAINS_CG = ["solana", "base", "binance-smart-chain"]

# DexScreener chain IDs
TARGET_CHAINS_DS = ["solana", "base", "bsc"]

CHAIN_DISPLAY = {
    "solana": "Solana",
    "base": "Base",
    "binance-smart-chain": "BSC",
    "bsc": "BSC",
}

# Broad discovery categories.
# The perp-gap filter is the main qualifier, so discovery can be wider.
CATEGORIES = [
    "decentralized-exchange",
    "yield-farming",
    "real-world-assets",
    "gaming",
    "layer-1",
    "layer-2",
    "ai",
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

# Size/volume filters
MCAP_MIN = 300_000
MCAP_MAX = 30_000_000
CG_VOLUME_MIN = 50_000

# DexScreener on-chain life filters
DEX_LIQUIDITY_MIN_USD = 50_000
DEX_VOLUME_24H_MIN_USD = 20_000

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


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
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


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
# BEST-EFFORT DEX PERP EXTRAS
# ============================================================

def fetch_hyperliquid_perps():
    """
    Best-effort Hyperliquid perp symbol fetch.
    If the endpoint changes or is blocked, this safely fails and the pipeline continues.
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
    If the endpoint changes or is blocked, this safely fails and the pipeline continues.
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
    """
    if ccxt is None:
        raise RuntimeError("ccxt is not installed. The GitHub workflow must install it.")

    print("=== BUILDING MASTER CEX SPOT + PERP LISTS ===")

    spot_symbols = set()
    perp_symbols = set()
    stats = {}

    for exchange_id in CEX_EXCHANGE_IDS:
        print(f"  [CEX] {exchange_id}: fetching markets...")

        try:
            exchange_cls = getattr(ccxt, exchange_id)
            exchange = exchange_cls(
                {
                    "enableRateLimit": True,
                    "timeout": 20000,
                    "userAgent": USER_AGENT,
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

                # CCXT normally normalizes active status.
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

    # Best-effort DEX perp coverage.
    print("  [DEX PERPS] Fetching Hyperliquid and dYdX best-effort...")

    hyperliquid_symbols = fetch_hyperliquid_perps()
    dydx_symbols = fetch_dydx_perps()

    perp_symbols.update(hyperliquid_symbols)
    perp_symbols.update(dydx_symbols)

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
    }

    save_json(SPOT_FILE, spot_output)
    save_json(PERP_FILE, perp_output)

    print(f"\n[SUCCESS] Master spot symbols: {len(spot_symbols)}")
    print(f"[SUCCESS] Master perp symbols: {len(perp_symbols)}")

    return spot_symbols, perp_symbols


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
        print(f"=== LOADED FRESH MASTER LISTS ===")
        print(f"  Spot symbols: {len(spot_symbols)}")
        print(f"  Perp symbols: {len(perp_symbols)}")
        return spot_symbols, perp_symbols

    print("=== MASTER LISTS MISSING OR STALE ===")

    try:
        spot_symbols, perp_symbols = build_master_lists()
        if perp_symbols:
            return spot_symbols, perp_symbols
        print("[WARNING] Built perp list is empty.")
    except Exception as e:
        print(f"[ERROR] Failed to build master lists: {e}")

    # Fallback to stale lists if they exist.
    if perp_data and perp_data.get("symbols"):
        print("[WARNING] Falling back to stale master lists.")
        spot_symbols = set(spot_data.get("symbols", [])) if spot_data else set()
        perp_symbols = set(perp_data.get("symbols", []))
        return spot_symbols, perp_symbols

    return set(), set()


# ============================================================
# COINGECKO DISCOVERY
# ============================================================

def fetch_coingecko_candidates():
    """
    Discovery layer:
    Find mid-cap tokens with meaningful spot volume in relevant categories.
    """
    print("\n=== MODULE 1: COINGECKO DISCOVERY ===")

    candidates = []
    seen_ids = set()
    headers = {"User-Agent": USER_AGENT}

    for category in CATEGORIES:
        print(f"  Fetching category: {category}...")

        url = (
            "https://api.coingecko.com/api/v3/coins/markets"
            f"?vs_currency=usd&category={category}"
            "&order=volume_desc&per_page=100&page=1&sparkline=false"
        )

        try:
            resp = requests.get(url, headers=headers, timeout=20)

            if resp.status_code != 200:
                print(f"    [ERROR] CoinGecko returned status {resp.status_code}")
                time.sleep(5)
                continue

            coins = resp.json()

            for coin in coins:
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

            time.sleep(4)

        except Exception as e:
            print(f"    [EXCEPTION] {e}")

    print(f"    Found {len(candidates)} baseline CoinGecko candidates.")
    return candidates


def get_coin_details(coin_id):
    """
    Enrichment layer:
    Fetch website, Twitter, and contract addresses from CoinGecko.
    """
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"
    headers = {"User-Agent": USER_AGENT}

    try:
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 200:
            return resp.json()
        print(f"    [ERROR] CoinGecko detail returned {resp.status_code}")
    except Exception as e:
        print(f"    [ERROR] CoinGecko detail fetch failed: {e}")

    return None


# ============================================================
# DEXSCREENER ON-CHAIN VALIDATION
# ============================================================

def validate_onchain_life(contract_address):
    """
    Validation layer:
    Use DexScreener to verify the token has real liquidity and volume
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

            try:
                liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                volume = float(pair.get("volume", {}).get("h24", 0) or 0)
            except Exception:
                continue

            if liquidity < DEX_LIQUIDITY_MIN_USD or volume < DEX_VOLUME_24H_MIN_USD:
                continue

            # Score primarily by volume, with liquidity as a secondary signal.
            score = volume + (liquidity * 0.25)

            if score > best_score:
                best_score = score
                best_pair = {
                    "chain": chain_id,
                    "chain_display": CHAIN_DISPLAY.get(chain_id, chain_id),
                    "dex": pair.get("dexId", "unknown"),
                    "pair_address": pair.get("pairAddress", ""),
                    "base_token_symbol": pair.get("baseToken", {}).get("symbol", ""),
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

def find_perp_gap_leads(candidates, processed_ids, perp_symbols, spot_symbols):
    """
    Core strategic filter:
    1. Skip already processed tokens.
    2. Drop tokens that already have perp markets.
    3. Require website.
    4. Require target-chain contract.
    5. Require live DEX liquidity/volume.
    6. Prioritize tokens already listed on CEX spot.
    """
    print("\n=== MODULE 2: PERP-GAP SUBTRACTION + ON-CHAIN VALIDATION ===")

    verified_leads = []
    random.shuffle(candidates)

    checked = 0

    for coin in candidates:
        if len(verified_leads) >= TARGET_QUOTA:
            print(f"  [QUOTA MET] Found {TARGET_QUOTA} verified perp-gap leads.")
            break

        if checked >= MAX_CANDIDATES_TO_PROCESS:
            print(f"  [LIMIT MET] Checked {MAX_CANDIDATES_TO_PROCESS} candidates this run.")
            break

        coin_id = coin["id"]
        symbol = coin["symbol"]

        if coin_id in processed_ids:
            continue

        checked += 1

        # Primary strategic filter:
        # Do not pitch tokens that already appear in the perp master list.
        if symbol in perp_symbols:
            print(f"  [DROP] {coin['name']} ({symbol}) | Reason: Existing perp market detected.")
            processed_ids.add(coin_id)
            continue

        print(f"  Processing perp-gap candidate: {coin['name']} ({symbol})...")

        details = get_coin_details(coin_id)
        time.sleep(4)

        if not details:
            print(f"    [TRANSIENT] No CoinGecko details returned. Not marking processed.")
            continue

        links = details.get("links", {})
        platforms = details.get("platforms", {})

        homepages = links.get("homepage", [])
        website = next(
            (url for url in homepages if url and str(url).startswith("http")),
            "",
        )

        if not website:
            print(f"    [DROP] No website found.")
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
            print(f"    [DROP] No target-chain contract address found.")
            processed_ids.add(coin_id)
            continue

        dex_pair = validate_onchain_life(target_contract)
        time.sleep(1)

        if not dex_pair:
            print(f"    [DROP] No qualifying live DEX pair found on target chains.")
            processed_ids.add(coin_id)
            continue

        priority = (
            "CEX_SPOT_NO_PERP"
            if symbol in spot_symbols
            else "DEX_MOMENTUM_NO_PERP"
        )

        verified_leads.append(
            {
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
            }
        )

        print(
            f"    [PASS] Perp gap confirmed. "
            f"Liq=${dex_pair['liquidity_usd']:,.0f}, "
            f"Vol=${dex_pair['volume_h24']:,.0f}, "
            f"Chain={dex_pair['chain_display']}, "
            f"Priority={priority}"
        )

    print(f"\n    Validation Complete: {len(verified_leads)} perp-gap leads verified.")
    return verified_leads


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
        f"• The Solution: List a native perp on Vibe for $0 USDC. Deposit idle treasury tokens to our vault to back the Open Interest.\n"
        f"• The Result: You earn perpetual trading fees to fund operations without selling a single token on the spot market.\n\n"
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

    # First try HTML formatting.
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

    # Fallback to plain text if HTML fails.
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

    print(f"\nDelivery Complete: {delivered}/{len(leads)} leads sent.")
    return delivered


# ============================================================
# MAIN
# ============================================================

def main():
    print("=== STARTING PERP-GAP CEX SPOT PIPELINE ===")

    processed_ids = load_processed_ids()
    print(f"Loaded {len(processed_ids)} previously processed CoinGecko IDs.")

    spot_symbols, perp_symbols = ensure_master_lists()

    if not perp_symbols:
        print("\n[ERROR] Could not build or load a perp master list.")
        print("[ACTION] Aborting to avoid pitching tokens that may already have perps.")
        save_processed_ids(processed_ids)
        return

    candidates = fetch_coingecko_candidates()

    if not candidates:
        print("\nNo baseline candidates found from CoinGecko.")
        save_processed_ids(processed_ids)
        return

    leads = find_perp_gap_leads(
        candidates=candidates,
        processed_ids=processed_ids,
        perp_symbols=perp_symbols,
        spot_symbols=spot_symbols,
    )

    if leads:
        deliver_leads(leads, processed_ids)
    else:
        print("\nNo perp-gap leads passed validation this run.")

    save_processed_ids(processed_ids)

    print("\n=== PIPELINE COMPLETE ===")


if __name__ == "__main__":
    main()
