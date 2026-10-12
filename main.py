import os
import json
import requests
import re
import time

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "processed_leads.json"

# Phase 19: Zombie & Legacy Blocklist
ZOMBIE_BLOCKLIST = ["serum", "belt", "qi", "hakka", "wagmi", "olympus", "wonderland", "iron finance", "luna", "terra", "celsius", "voyager"]
GIANT_BLOCKLIST = ["uniswap", "pancakeswap", "aave", "curve", "compound", "maker", "lido", "pendle", "gmx", "jupiter", "raydium", "aerodrome", "mavia", "re", "saffron", "balancer", "quickswap", "harvest"]
MEME_BLOCKLIST_PATTERNS = [
    re.compile(r'\b(pepe|doge|shib|inu|floki|bonk|wojak|pump|moon|safe)\b', re.IGNORECASE)
]

def is_blocked(name, symbol):
    name_lower = (name or "").lower()
    symbol_lower = (symbol or "").lower()
    
    # Zombie & Giant Blocklist
    for zombie in ZOMBIE_BLOCKLIST + GIANT_BLOCKLIST:
        if zombie in name_lower or zombie in symbol_lower:
            return True
            
    # Memecoin Regex
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

# --- SOURCE: CoinGecko Recently Added (Real-time Activity) ---
def source_recently_added():
    print("  [SOURCE] Fetching CoinGecko Recently Added (Real-time Activity)...", flush=True)
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

# --- ENRICHMENT (Fetch details for CoinGecko leads) ---
def enrich_leads(leads):
    print("\n=== ENRICHMENT PHASE ===", flush=True)
    enriched = []
    
    for lead in leads:
        print(f"  Fetching details for {lead['name']}...", flush=True)
        time.sleep(3.5) # Rate limit
        detail_data = fetch_with_retry(f"https://api.coingecko.com/api/v3/coins/{lead['unique_id']}")
        
        if detail_data:
            links = detail_data.get('links', {})
            homepages = links.get('homepage', [])
            lead['website'] = next((url for url in homepages if url and str(url).strip()), 'Not Found')
            lead['twitter'] = f"@{links.get('twitter_screen_name', '')}" if links.get('twitter_screen_name') else 'Not Found'
            lead['telegram'] = f"@{links.get('telegram_channel_identifier', '')}" if links.get('telegram_channel_identifier') else 'Not Found'
            
            # Utility Proxy: Must have a website or twitter
            if lead['website'] == 'Not Found' and lead['twitter'] == 'Not Found':
                continue
                
            enriched.append(lead)
        else:
            print(f"    [DROP] Failed to fetch details for {lead['name']}.", flush=True)
            
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
    vol_str = f"${int(lead['volume']):,}"
    
    text = (
        f" *Dynamic Trigger Lead: {lead['source']}*\n\n"
        f"📛 *Project*: {lead['name']} ({lead.get('symbol', 'N/A')})\n"
        f"💰 *Market Cap*: {mcap_str}\n"
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
    print("=== STARTING DYNAMIC TRIGGER-BASED PIPELINE (ANTI-ZOMBIE) ===", flush=True)
    try:
        # 1. Fetch from Dynamic Source
        leads_cg = source_recently_added()
        
        # 2. Enrich (Fetch websites/socials)
        enriched_leads = enrich_leads(leads_cg)
        
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
