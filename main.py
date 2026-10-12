import os
import json
import requests
import time
from datetime import datetime, timezone

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "processed_leads.json"

NETWORKS = ['solana', 'base', 'bsc']

# 1. Expanded Blocklist of stock tickers, CEX tokens, and legacy zombies
TICKER_BLOCKLIST = [
    "mstr", "sndk", "tsla", "aapl", "amzn", "googl", "goog", "msft", "nvda", "meta", 
    "spy", "qqq", "iwm", "coin", "hood", "mstrc", "sndkc", "tslac", "aaplc",
    "mu", "muc", "spcx", "spcxc", "pltr", "pltrc", "xaug", "nflx", "dis", "v", "ma", 
    "jpm", "bac", "wfc", "gs", "ms", "jnj", "pg", "ko", "pep", "wmt", "hd", "mcd", 
    "nke", "intc", "csco", "crm", "orcl", "ibm", "qcom", "avgo", "adbe", "pypl", 
    "tmus", "vz", "t", "f", "gm", "abnb", "dash", "spot", "snap", "roku", "sq", 
    "sofi", "upst", "crwd", "ddog", "net", "snow", "mdb", "okta", "zm", "docu", 
    "team", "wdc", "stx", "box", "drop", "hubspot", "zendesk", "twilio",
    "serum", "belt", "qi", "hakka", "wagmi", "olympus", "wonderland", 
    "iron", "safemoon", "squid", "terra", "luna", "celsius"
]

# 2. Stablecoin & Legacy Blocklist
STABLECOIN_AND_LEGACY_BLOCKLIST = [
    "usdc", "usdt", "dai", "frax", "tusd", "busd", "eurc", "lusd", "usdd", 
    "zec", "xmr", "etc", "bch", "ltc"
]

# Unified blocklist for checking
ALL_BLOCKED_TERMS = TICKER_BLOCKLIST + STABLECOIN_AND_LEGACY_BLOCKLIST

# 3. Bad keywords for URL validation
BAD_URL_KEYWORDS = [
    "coinbase", "robinhood", "tokenize", "tokenized", "binance", "kraken", "okx", "bybit"
]

# Quota System
TARGET_NEW_LEADS_PER_RUN = 4

def load_processed_ids():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return set(json.load(f))
        except (json.JSONDecodeError, FileNotFoundError):
            return set()
    return set()

def save_processed_ids(ids_set):
    with open(STATE_FILE, "w") as f:
        json.dump(list(ids_set), f, indent=2)

def is_blocked_token(base_token, blocked_terms):
    """
    Checks if the base token matches any blocked term.
    - For terms >= 3 chars, uses substring matching (catches 'cbzec', 'mstrc').
    - For terms 1-2 chars, requires exact match (prevents blocking 'debit' due to 't', or 'overdrive' due to 'v').
    """
    for term in blocked_terms:
        if len(term) >= 3:
            if term in base_token:
                return True
        else:
            if base_token == term:
                return True
    return False

def fetch_with_retry(url, max_retries=3):
    """Robust fetch with exponential backoff to survive heavy rate limits."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                wait_time = 60 * (attempt + 1)
                print(f"    [RATE LIMIT] 429 detected. Pausing for {wait_time} seconds...", flush=True)
                time.sleep(wait_time)
            else:
                print(f"    [ERROR] Unexpected status {response.status_code} for {url}", flush=True)
                return None
        except requests.exceptions.RequestException as e:
            print(f"    [NETWORK ERROR] {e}. Retrying...", flush=True)
            time.sleep(5)
    print(f"    [CRITICAL] Failed to fetch {url} after {max_retries} retries.", flush=True)
    return None

def is_valid_project_url(url):
    if not url:
        return False
    url_lower = url.lower()
    if any(bad_word in url_lower for bad_word in BAD_URL_KEYWORDS):
        return False
    return True

# --- DYNAMIC TEMPLATE ENGINE ---
def generate_personalized_dm(lead):
    name = lead['name'].split('/')[0].strip()
    chain = lead['network'].capitalize()
    volume = f"${lead['volume_h24']:,.0f}"
    age = lead['age_days']
    
    if lead['volume_h24'] > 10_000_000:
        template = (
            f"Hey team, been tracking {name}'s traction on {chain}. The trading volume you’re seeing right now is incredible.\n\n"
            f"With volume this high, a lot of speculative trading and fee revenue is likely leaking to external perp platforms. We built Vibe Trading to help projects like yours capture that value:\n"
            f"• **The Solution:** List a perp on Vibe for $0 USDC. Deposit idle tokens to our vault to back the Open Interest.\n"
            f"• **The Result:** You earn perpetual trading fees to fund operations without selling a single token on the spot market.\n\n"
            f"Mind if I send over a quick 1-pager on how the vault mechanics work?"
        )
    elif age < 30:
        template = (
            f"Hey team, huge fan of the momentum behind {name} on {chain}.\n\n"
            f"Keeping the community engaged between major updates is always the hardest part for early-stage projects. We built Vibe Trading to solve this:\n"
            f"• **The Solution:** Give your community a native reason to trade. Launch a perp on Vibe with up to 60x leverage.\n"
            f"• **The Result:** Drives massive daily engagement, keeps traders in your ecosystem, and generates perpetual fee revenue for your treasury.\n\n"
            f"Are you currently exploring any derivative integrations for {name}?"
        )
    elif lead['liquidity_usd'] > 1_000_000:
        template = (
            f"Hey team, great work on the recent traction for {name} on {chain}.\n\n"
            f"One thing we see with high-potential tokens is that fragmented liquidity can scare away larger traders. We built Vibe Trading to solve this:\n"
            f"• **The Solution:** Consolidate your liquidity on Vibe. Deposit tokens to our vault to back a perp market for $0 USDC.\n"
            f"• **The Result:** You provide traders with deep liquidity and tight fills, stabilizing the chart while your treasury earns the trading fees.\n\n"
            f"Open to seeing a quick case study on how this works?"
        )
    else:
        template = (
            f"Hey team, great work on the recent traction for {name} on {chain}.\n\n"
            f"Managing a treasury that is heavily weighted in native tokens is tough when operational costs are in USD. We built Vibe Trading to solve this:\n"
            f"• **The Solution:** Put those idle tokens to work. Deposit them to our vault to instantly list a perp market for $0 USDC.\n"
            f"• **The Result:** Your idle tokens generate perpetual yield from trading fees, covering your USD costs without diluting your spot holders.\n\n"
            f"Happy to share a brief breakdown of the economics if you're open to it?"
        )
    
    return template

# --- SOURCE: Paginated Discovery with Early Exit ---
def source_active_pools(processed_ids):
    print("  [SOURCE] Fetching GeckoTerminal Pools (Paginated Quota System)...", flush=True)
    validated_leads = []
    
    for network in NETWORKS:
        if len(validated_leads) >= TARGET_NEW_LEADS_PER_RUN:
            print(f"    [QUOTA MET] Found {TARGET_NEW_LEADS_PER_RUN} new leads. Stopping discovery.", flush=True)
            break
            
        print(f"  Fetching pools for {network}...", flush=True)
        
        for page in range(1, 6):
            if len(validated_leads) >= TARGET_NEW_LEADS_PER_RUN:
                break
                
            url = f"https://api.geckoterminal.com/api/v2/networks/{network}/pools?sort=h24_volume_usd_desc&page={page}"
            
            data = fetch_with_retry(url)
            if not data:
                print(f"    [ERROR] Failed to fetch page {page} for {network} after retries. Moving to next network.", flush=True)
                break
                
            pools = data.get('data', [])
            
            if not pools:
                print(f"    [INFO] No more pools on page {page} for {network}.", flush=True)
                break
                
            for pool in pools:
                if len(validated_leads) >= TARGET_NEW_LEADS_PER_RUN:
                    break
                    
                attrs = pool.get('attributes', {})
                pool_name = attrs.get('name', '').lower()
                
                # Extract base token
                base_token = pool_name.split('/')[0].strip().replace(' ', '').lower()
                
                # INTELLIGENT BLOCKLIST CHECK: Prevents false positives on single letters like 't' or 'v'
                if is_blocked_token(base_token, ALL_BLOCKED_TERMS):
                    print(f"    [DROP] {attrs.get('name')} | Reason: Matches blocked term", flush=True)
                    continue
                    
                # Age Check (14 to 90 days)
                created_at_str = attrs.get('pool_created_at')
                if not created_at_str:
                    continue
                created_dt = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                age_days = (datetime.now(timezone.utc) - created_dt).days
                if not (14 <= age_days <= 90):
                    continue
                    
                # Liquidity & Volume Checks (LOWERED THRESHOLDS TO $10k)
                liquidity_usd = float(attrs.get('reserve_in_usd', 0) or 0)
                volume_data = attrs.get('volume_usd', {})
                volume_h24 = float(volume_data.get('h24', 0) or 0) if isinstance(volume_data, dict) else float(volume_data or 0)
                
                if liquidity_usd < 10_000 or volume_h24 < 10_000:
                    continue
                    
                # Deduplication Check
                relationships = pool.get('relationships', {})
                base_token_id = relationships.get('base_token', {}).get('data', {}).get('id', '')
                if not base_token_id or base_token_id in processed_ids:
                    continue
                    
                validated_leads.append({
                    'name': attrs.get('name'),
                    'network': network,
                    'age_days': age_days,
                    'liquidity_usd': liquidity_usd,
                    'volume_h24': volume_h24,
                    'token_id': base_token_id
                })
                print(f"    [PASS] {attrs.get('name')} | Age: {age_days}d | Liq: ${liquidity_usd:,.0f} | Vol: ${volume_h24:,.0f}", flush=True)
                
            time.sleep(2) # Polite delay between pages

    print(f"Discovery Complete: {len(validated_leads)} new, high-quality leads found.", flush=True)
    return validated_leads

# --- ENRICHMENT: Fallback Strategy (GeckoTerminal -> CoinGecko) ---
def enrich_leads(leads):
    print("\n=== ENRICHMENT PHASE (Fallback Strategy) ===", flush=True)
    enriched = []
    
    for lead in leads:
        token_address = lead['token_id'].split('_')[-1]
        token_name = lead['name'].split(' / ')[0].strip()
        network = lead['network']
        
        print(f"  Enriching {lead['name']} ({token_address[:8]}...)...", flush=True)
        
        website = ''
        twitter = ''
        telegram = ''
        
        gt_url = f"https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{token_address}"
        gt_data = fetch_with_retry(gt_url)
        
        if gt_data:
            token_attrs = gt_data.get('data', {}).get('attributes', {})
            website = token_attrs.get('website', '') or token_attrs.get('homepage', '')
            twitter = token_attrs.get('twitter', '') or token_attrs.get('twitter_handle', '')
            telegram = token_attrs.get('telegram', '') or token_attrs.get('telegram_handle', '')
            
        if not website and not twitter:
            print(f"    [INFO] GeckoTerminal missing links. Trying CoinGecko search for '{token_name}'...", flush=True)
            cg_url = f"https://api.coingecko.com/api/v3/search?query={token_name}"
            cg_data = fetch_with_retry(cg_url)
            
            if cg_data:
                coins = cg_data.get('coins', [])
                
                for coin in coins:
                    if coin.get('name', '').lower() == token_name.lower() or coin.get('symbol', '').lower() == token_name.lower():
                        detail_url = f"https://api.coingecko.com/api/v3/coins/{coin.get('id')}"
                        detail_data = fetch_with_retry(detail_url)
                        
                        if detail_data:
                            links = detail_data.get('links', {})
                            homepages = links.get('homepage', [])
                            temp_website = next((url for url in homepages if url and str(url).strip()), '')
                            temp_twitter = links.get('twitter_screen_name', '')
                            temp_telegram = links.get('telegram_channel_identifier', '')
                            
                            if temp_website and not is_valid_project_url(temp_website):
                                print(f"    [DROP] CoinGecko fallback returned a CEX/Tokenized URL ({temp_website}). Skipping.", flush=True)
                                continue
                                
                            if temp_website or temp_twitter:
                                website = temp_website
                                twitter = temp_twitter
                                telegram = temp_telegram
                                print(f"    [SUCCESS] Found links via CoinGecko: Web={website}, TW={twitter}", flush=True)
                                break
                        time.sleep(2)
                        
        if website and not is_valid_project_url(website):
            print(f"    [DROP] Final URL rejected as CEX/Tokenized asset: {website}", flush=True)
            website = ''
            
        if website or twitter:
            lead['website'] = website or 'Not Found'
            lead['twitter'] = f"@{twitter}" if twitter and not twitter.startswith('@') else (twitter or 'Not Found')
            lead['telegram'] = f"@{telegram}" if telegram and not telegram.startswith('@') else (telegram or 'Not Found')
            enriched.append(lead)
            print(f"    [ENRICHED] {lead['name']}", flush=True)
        else:
            print(f"    [DROP] {lead['name']} (No valid website or twitter found in any source)", flush=True)
            
        time.sleep(1)

    print(f"Enrichment Complete: {len(enriched)} leads ready for outreach.", flush=True)
    return enriched

# --- OUTPUT: Dynamic Sales Dossier ---
def send_telegram_message(lead):
    dm_text = generate_personalized_dm(lead)
    
    telegram_message = (
        f" *NEW HIGH-SIGNAL LEAD*\n\n"
        f"📛 *Project:* {lead['name']}\n"
        f"⛓️ *Chain:* {lead['network'].capitalize()}\n"
        f"⏳ *Age:* {lead['age_days']} days\n"
        f"💧 *Liquidity:* ${lead['liquidity_usd']:,.0f}\n"
        f"📊 *24h Volume:* ${lead['volume_h24']:,.0f}\n"
        f"🔗 *Website:* {lead.get('website', 'N/A')}\n"
        f"🐦 *Twitter:* {lead.get('twitter', 'N/A')}\n"
        f"✈️ *Telegram:* {lead.get('telegram', 'N/A')}\n\n"
        f"📝 *READY-TO-SEND DM:*\n"
        f"```\n{dm_text}\n```"
    )
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": telegram_message, "parse_mode": "Markdown"}
    
    try:
        requests.post(url, json=payload, timeout=10)
        print(f"  [TELEGRAM] ✅ Sent sales dossier for {lead['name']}", flush=True)
    except Exception as e:
        print(f"  [TELEGRAM] ❌ Failed to send: {e}", flush=True)

def main():
    print("=== STARTING ENDLESS FLOW ON-CHAIN PIPELINE (INTELLIGENT BLOCKLIST) ===", flush=True)
    try:
        processed_ids = load_processed_ids()
        print(f"Loaded {len(processed_ids)} previously processed token IDs.", flush=True)
        
        leads = source_active_pools(processed_ids)
        if not leads:
            print("Exiting: No new leads passed the on-chain filters.", flush=True)
            return
            
        enriched_leads = enrich_leads(leads)
        
        print(f"\n=== DELIVERY & STATE UPDATE ===", flush=True)
        newly_processed_ids = []
        
        for lead in enriched_leads:
            send_telegram_message(lead)
            newly_processed_ids.append(lead['token_id'])
            
        processed_ids.update(newly_processed_ids)
        save_processed_ids(processed_ids)
        
        print(f"\n=== PIPELINE FINISHED SUCCESSFULLY ===", flush=True)
        print(f"Sent {len(enriched_leads)} leads. Database updated with {len(newly_processed_ids)} new IDs.", flush=True)
        
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}", flush=True)
        raise

if __name__ == "__main__":
    main()
