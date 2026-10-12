import os
import json
import requests
import re
import time

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "processed_leads.json"

TARGET_CHAINS_DL = ["Solana", "Base", "Binance", "BSC", "Binance Smart Chain"]
ALLOWED_CATEGORIES_DL = [
    "Dexs", "Yield", "Yield Aggregator", "Lending", "Liquid Staking", 
    "Infrastructure", "Services", "RWA", "Gaming", "Derivatives", "CDP"
]

GIANT_BLOCKLIST = [
    "uniswap", "pancakeswap", "aave", "curve", "compound", "maker", 
    "lido", "pendle", "gmx", "jupiter", "raydium", "aerodrome", 
    "mavia", "re", "saffron", "balancer", "quickswap", "harvest"
]

MEME_BLOCKLIST_PATTERNS = [
    re.compile(r'\b(pepe|doge|shib|inu|floki|bonk|wojak|pump|moon|safe)\b', re.IGNORECASE)
]

def is_blocked(name, symbol):
    name_lower = (name or "").lower()
    symbol_lower = (symbol or "").lower()
    
    if any(giant in name_lower or giant in symbol_lower for giant in GIANT_BLOCKLIST):
        return True
        
    for pattern in MEME_BLOCKLIST_PATTERNS:
        if pattern.search(name_lower) or pattern.search(symbol_lower):
            return True
            
    return False

def fetch_with_retry(url, max_retries=3):
    """Robust fetch function with exponential backoff for 429 Rate Limits."""
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
            time.sleep(10)
            
    print(f"    [CRITICAL] Failed to fetch {url} after {max_retries} retries.", flush=True)
    return None

# --- SOURCE 1 & 3: CoinGecko Recently Added (Launchpad Graduates / New Pairs) ---
def source_recently_added():
    print("  [SOURCE 1] Fetching CoinGecko Recently Added (Launchpad Graduates)...", flush=True)
    url = "https://api.coingecko.com/api/v3/coins/recently_added"
    data = fetch_with_retry(url)
    
    leads = []
    if data:
        for coin in data:
            name = str(coin.get('name', ''))
            symbol = str(coin.get('symbol', ''))
            mcap = coin.get('market_cap') or 0
            volume = coin.get('total_volume') or 0
            coin_id = coin.get('id')
            
            if is_blocked(name, symbol):
                continue
                
            # Strict filters for new launches
            if not (500_000 <= mcap <= 5_000_000):
                continue
            if volume < 50_000:
                continue
                
            leads.append({
                'source': 'CoinGecko_Recent',
                'unique_id': coin_id,
                'name': name,
                'symbol': symbol.upper(),
                'mcap': float(mcap),
                'volume': float(volume)
            })
    print(f"    Found {len(leads)} leads from Recently Added.", flush=True)
    return leads

# --- SOURCE 2: DefiLlama Momentum (TVL > $250k, MCAP < $5M) ---
def source_defillama_momentum():
    print("  [SOURCE 2] Fetching DefiLlama Protocols (TVL Momentum)...", flush=True)
    url = "https://api.llama.fi/protocols"
    data = fetch_with_retry(url)
    
    leads = []
    if data:
        for proto in data:
            name = str(proto.get('name', ''))
            symbol = str(proto.get('symbol', ''))
            chains = proto.get('chains', [])
            category = proto.get('category', '')
            tvl = float(proto.get('tvl', 0) or 0)
            mcap = float(proto.get('mcap', 0) or 0)
            slug = proto.get('slug', '')
            
            if is_blocked(name, symbol):
                continue
                
            # Chain Filter
            if not any(chain in TARGET_CHAINS_DL for chain in chains):
                continue
                
            # Category Filter
            if not any(allowed.lower() in category.lower() for allowed in ALLOWED_CATEGORIES_DL):
                continue
                
            # Momentum Filters
            if tvl < 250_000:
                continue
            if not (500_000 <= mcap <= 5_000_000):
                continue
                
            leads.append({
                'source': 'DefiLlama_Momentum',
                'unique_id': f"dl_{slug}",
                'name': name,
                'symbol': symbol.upper(),
                'mcap': mcap,
                'volume': 0, # DefiLlama doesn't provide 24h volume in this endpoint
                'website': proto.get('url', ''),
                'twitter': proto.get('twitter', ''),
                'telegram': proto.get('telegram', '')
            })
    print(f"    Found {len(leads)} leads from DefiLlama Momentum.", flush=True)
    return leads

# --- ENRICHMENT (Fetch details for CoinGecko leads) ---
def enrich_leads(leads):
    print("\n=== ENRICHMENT PHASE ===", flush=True)
    enriched = []
    
    for lead in leads:
        if lead['source'] == 'CoinGecko_Recent':
            print(f"  Fetching details for {lead['name']}...", flush=True)
            time.sleep(3.5) # Rate limit
            detail_data = fetch_with_retry(f"https://api.coingecko.com/api/v3/coins/{lead['unique_id']}")
            
            if detail_data:
                links = detail_data.get('links', {})
                homepages = links.get('homepage', [])
                lead['website'] = next((url for url in homepages if url and str(url).strip()), 'Not Found')
                lead['twitter'] = f"@{links.get('twitter_screen_name', '')}" if links.get('twitter_screen_name') else 'Not Found'
                lead['telegram'] = f"@{links.get('telegram_channel_identifier', '')}" if links.get('telegram_channel_identifier') else 'Not Found'
            else:
                continue # Skip if we can't get details
        else:
            # Format DefiLlama contacts
            lead['twitter'] = f"@{lead['twitter']}" if lead['twitter'] and not lead['twitter'].startswith('@') else (lead['twitter'] or 'Not Found')
            lead['telegram'] = f"@{lead['telegram']}" if lead['telegram'] and not lead['telegram'].startswith('@') else (lead['telegram'] or 'Not Found')
            
        # Final Utility Proxy: Must have a website or twitter
        if lead.get('website', 'Not Found') == 'Not Found' and lead.get('twitter', 'Not Found') == 'Not Found':
            continue
            
        enriched.append(lead)
        
    return enriched

# --- STATE & OUTPUT ---
def load_processed_ids():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return []

def save_processed_ids(ids):
    with open(STATE_FILE, "w") as f:
        json.dump(ids, f)

def send_telegram_message(lead):
    mcap_str = f"${int(lead['mcap']):,}"
    vol_str = f"${int(lead['volume']):,}" if lead['volume'] > 0 else "N/A (DefiLlama)"
    
    text = (
        f"🚨 *Dynamic Trigger Lead: {lead['source']}*\n\n"
        f"📛 *Project*: {lead['name']} ({lead.get('symbol', 'N/A')})\n"
        f" *Market Cap*: {mcap_str}\n"
        f"📊 *24h Volume*: {vol_str}\n"
        f"🔗 *Website*: {lead.get('website', 'N/A')}\n\n"
        f"📞 *Contacts*:\n"
        f"• Telegram: {lead.get('telegram', 'Not Found')}\n"
        f"• Twitter: {lead.get('twitter', 'Not Found')}\n\n"
        f"💬 *Vibe Pitch*: \"Instead of dumping treasury, deposit tokens to Vibe's vault to list a perp for free, back OI, and earn trading fees forever.\""
    )
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    
    try:
        requests.post(url, json=payload, timeout=10)
        print(f"  [TELEGRAM] ✅ Sent message for {lead['name']}", flush=True)
    except Exception as e:
        print(f"  [TELEGRAM] ❌ Failed to send: {e}", flush=True)

def main():
    print("=== STARTING DYNAMIC TRIGGER-BASED PIPELINE ===", flush=True)
    try:
        # 1. Fetch from Dynamic Sources
        leads_cg = source_recently_added()
        time.sleep(45) # Mandatory cooldown between major API calls
        leads_dl = source_defillama_momentum()
        
        all_leads = leads_cg + leads_dl
        
        # 2. Enrich (Fetch websites/socials)
        enriched_leads = enrich_leads(all_leads)
        
        # 3. Deduplicate by Immutable ID
        processed_ids = load_processed_ids()
        print(f"\n=== DEDUPLICATION ===", flush=True)
        print(f"Processed IDs in memory: {len(processed_ids)}", flush=True)
        
        new_leads = [lead for lead in enriched_leads if lead['unique_id'] not in processed_ids]
        print(f"New leads to process: {len(new_leads)}", flush=True)
        
        # 4. Deliver
        for lead in new_leads:
            send_telegram_message(lead)
            processed_ids.append(lead['unique_id'])
            
        save_processed_ids(processed_ids)
        print("\n=== PIPELINE FINISHED SUCCESSFULLY ===", flush=True)
        
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}", flush=True)
        save_processed_ids(load_processed_ids())
        raise

if __name__ == "__main__":
    main()
