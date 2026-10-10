import os
import json
import requests
import re
import time

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "processed_leads.json"

# Phase 13: Native CoinGecko Approach Parameters
CATEGORIES = [
    'decentralized-exchange', 
    'yield-farming', 
    'real-world-assets-rwa', 
    'gaming'
]

GIANT_BLOCKLIST = [
    "uniswap", "pancakeswap", "aave", "curve", "compound", "maker", 
    "lido", "pendle", "gmx", "jupiter", "raydium", "aerodrome", 
    "mavia", "re", "saffron", "balancer", "quickswap", "harvest"
]

# Regex patterns for memecoin blocklist (case-insensitive, word boundaries)
MEME_BLOCKLIST_PATTERNS = [
    re.compile(r'\b(pepe|doge|shib|inu|floki|bonk|wojak|pump|moon|safe)\b', re.IGNORECASE)
]

def is_blocked(name, symbol):
    """Checks if project name or symbol matches giant or memecoin blocklists."""
    name_lower = (name or "").lower()
    symbol_lower = (symbol or "").lower()
    
    # 1. Giant Blocklist Check (substring match)
    if any(giant in name_lower or giant in symbol_lower for giant in GIANT_BLOCKLIST):
        return True
        
    # 2. Memecoin Regex Blocklist Check
    for pattern in MEME_BLOCKLIST_PATTERNS:
        if pattern.search(name_lower) or pattern.search(symbol_lower):
            return True
            
    return False

def fetch_and_filter_coingecko():
    """Queries CoinGecko categories and applies all filters in a single pass."""
    print("=== MODULE 1: DISCOVERY & ENRICHMENT (Native CoinGecko) ===")
    validated_leads = []
    
    for category in CATEGORIES:
        print(f"  Fetching category: {category}...")
        url = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&category={category}&order=market_cap_desc&per_page=100&page=1&sparkline=false"
        
        try:
            time.sleep(2.5)  # Respect CoinGecko free tier rate limits (10-30 calls/min)
            response = requests.get(url, timeout=15)
            
            if response.status_code != 200:
                print(f"    [ERROR] {response.status_code} {response.reason} for {category}")
                continue
                
            coins = response.json()
            
            for coin in coins:
                name = str(coin.get('name', ''))
                symbol = str(coin.get('symbol', ''))
                mcap = coin.get('market_cap') or 0
                volume = coin.get('total_volume') or 0
                links = coin.get('links', {})
                
                # 1. Blocklist Checks
                if is_blocked(name, symbol):
                    continue
                    
                # 2. Strict Market Cap Filter ($500k - $3M)
                if not (500_000 <= mcap <= 3_000_000):
                    continue
                    
                # 3. Minimum Volume Filter ($50k+)
                if volume < 50_000:
                    continue
                    
                # 4. Utility Proxy: Must have a valid homepage URL
                homepages = links.get('homepage', [])
                valid_homepage = next((url for url in homepages if url and str(url).strip()), None)
                if not valid_homepage:
                    continue
                    
                # 5. Extract Contacts
                twitter_handle = links.get('twitter_screen_name', '')
                telegram_handle = links.get('telegram_channel_identifier', '')
                
                # If it passes all filters, it's a high-quality lead
                validated_leads.append({
                    'name': name,
                    'symbol': symbol.upper(),
                    'market_cap': float(mcap),
                    'volume_24h': float(volume),
                    'website': valid_homepage,
                    'twitter': f"@{twitter_handle}" if twitter_handle else "Not Found",
                    'telegram': f"@{telegram_handle}" if telegram_handle else "Not Found"
                })
                print(f"    [PASS] {name} (MCAP: ${mcap:,.0f}, Vol: ${volume:,.0f})")
                
        except requests.exceptions.RequestException as e:
            print(f"    [ERROR] Request exception for {category}: {e}")
        except Exception as e:
            print(f"    [ERROR] Unexpected exception for {category}: {e}")

    print(f"Module Complete: {len(validated_leads)} high-quality leads discovered.")
    return validated_leads

def load_processed_names():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return []

def save_processed_names(names):
    with open(STATE_FILE, "w") as f:
        json.dump(names, f)

def send_telegram_message(lead):
    mcap_str = f"${int(lead['market_cap']):,}"
    vol_str = f"${int(lead['volume_24h']):,}"
    
    text = (
        f"🚨 *High-Potential Vibe Trading Lead*\n\n"
        f"📛 *Project*: {lead['name']} ({lead.get('symbol', 'N/A')})\n"
        f"💰 *Market Cap*: {mcap_str}\n"
        f"📊 *24h Volume*: {vol_str}\n"
        f"🔗 *Website*: {lead.get('website', 'N/A')}\n\n"
        f"📞 *Extracted Contacts*:\n"
        f"• Telegram: {lead['telegram']}\n"
        f"• Twitter: {lead['twitter']}\n\n"
        f"💬 *Vibe Pitch*: \"Instead of dumping treasury, deposit tokens to Vibe's vault to list a perp for free, back OI, and earn trading fees forever.\""
    )
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    
    try:
        requests.post(url, json=payload, timeout=10)
        print(f"  [TELEGRAM] ✅ Sent message for {lead['name']}")
    except Exception as e:
        print(f"  [TELEGRAM] ❌ Failed to send message for {lead['name']}: {e}")

def main():
    print("=== STARTING MULTI-STAGE QUALIFICATION FUNNEL (PHASE 13: NATIVE COINGECKO) ===")
    try:
        # 1. Discovery & Enrichment
        validated_leads = fetch_and_filter_coingecko()
        if not validated_leads:
            print("Exiting: No leads passed the filtering criteria.")
            return
            
        # 2. Deduplication
        processed_names = load_processed_names()
        print(f"\n=== MODULE 2: DEDUPLICATION & OUTPUT ===")
        print(f"Current processed names in memory: {len(processed_names)}")
        
        # Deduplicate by project name to prevent sending the same project twice
        new_leads = [lead for lead in validated_leads if lead['name'] not in processed_names]
        print(f"New leads to process after deduplication: {len(new_leads)}")
        
        if len(new_leads) == 0 and len(validated_leads) > 0:
            print("⚠️ WARNING: All enriched leads were already processed. No new messages sent.")
        
        # 3. Delivery
        for lead in new_leads:
            send_telegram_message(lead)
            processed_names.append(lead['name'])
            
        save_processed_names(processed_names)
        print("\n=== PIPELINE FINISHED SUCCESSFULLY ===")
        
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}")
        raise

if __name__ == "__main__":
    main()
