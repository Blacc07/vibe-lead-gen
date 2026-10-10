import os
import json
import requests
import re
import time
import sys

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "processed_leads.json"

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

def fetch_and_filter_coingecko():
    print("=== MODULE 1: DISCOVERY & ENRICHMENT (Native CoinGecko) ===", flush=True)
    validated_leads = []
    
    for category in CATEGORIES:
        print(f"  Fetching category: {category}...", flush=True)
        url = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&category={category}&order=market_cap_desc&per_page=100&page=1&sparkline=false"
        
        try:
            time.sleep(3.5)  # Increased delay to respect rate limits
            
            # Use a standard browser User-Agent to reduce the chance of immediate WAF blocking
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
                "Accept": "application/json"
            }
            
            response = requests.get(url, headers=headers, timeout=15)
            
            # BULLETPROOF LOGGING: Print exactly what the API returns
            print(f"    [API RESPONSE] Status: {response.status_code} {response.reason}", flush=True)
            
            if response.status_code != 200:
                # Print the first 300 characters of the error body to see if it's a Cloudflare block, rate limit, etc.
                print(f"    [API RESPONSE BODY] {response.text[:300]}", flush=True)
                continue
                
            coins = response.json()
            print(f"    [SUCCESS] Received {len(coins)} coins for {category}", flush=True)
            
            for coin in coins:
                name = str(coin.get('name', ''))
                symbol = str(coin.get('symbol', ''))
                mcap = coin.get('market_cap') or 0
                volume = coin.get('total_volume') or 0
                links = coin.get('links', {})
                
                if is_blocked(name, symbol):
                    continue
                    
                if not (500_000 <= mcap <= 3_000_000):
                    continue
                    
                if volume < 50_000:
                    continue
                    
                homepages = links.get('homepage', [])
                valid_homepage = next((url for url in homepages if url and str(url).strip()), None)
                if not valid_homepage:
                    continue
                    
                twitter_handle = links.get('twitter_screen_name', '')
                telegram_handle = links.get('telegram_channel_identifier', '')
                
                validated_leads.append({
                    'name': name,
                    'symbol': symbol.upper(),
                    'market_cap': float(mcap),
                    'volume_24h': float(volume),
                    'website': valid_homepage,
                    'twitter': f"@{twitter_handle}" if twitter_handle else "Not Found",
                    'telegram': f"@{telegram_handle}" if telegram_handle else "Not Found"
                })
                print(f"    [PASS] {name} (MCAP: ${mcap:,.0f}, Vol: ${volume:,.0f})", flush=True)
                
        except requests.exceptions.RequestException as e:
            print(f"    [CRITICAL NETWORK ERROR] {e}", flush=True)
        except Exception as e:
            print(f"    [CRITICAL UNEXPECTED ERROR] {e}", flush=True)

    print(f"Module Complete: {len(validated_leads)} high-quality leads discovered.", flush=True)
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
        print(f"  [TELEGRAM] ✅ Sent message for {lead['name']}", flush=True)
    except Exception as e:
        print(f"  [TELEGRAM] ❌ Failed to send message for {lead['name']}: {e}", flush=True)

def main():
    print("=== STARTING MULTI-STAGE QUALIFICATION FUNNEL (PHASE 14: BULLETPROOF LOGGING) ===", flush=True)
    try:
        validated_leads = fetch_and_filter_coingecko()
        if not validated_leads:
            print("Exiting: No leads passed the filtering criteria or API failed.", flush=True)
            return
            
        processed_names = load_processed_names()
        print(f"\n=== MODULE 2: DEDUPLICATION & OUTPUT ===", flush=True)
        print(f"Current processed names in memory: {len(processed_names)}", flush=True)
        
        new_leads = [lead for lead in validated_leads if lead['name'] not in processed_names]
        print(f"New leads to process after deduplication: {len(new_leads)}", flush=True)
        
        if len(new_leads) == 0 and len(validated_leads) > 0:
            print("⚠️ WARNING: All enriched leads were already processed. No new messages sent.", flush=True)
        
        for lead in new_leads:
            send_telegram_message(lead)
            processed_names.append(lead['name'])
            
        save_processed_names(processed_names)
        print("\n=== PIPELINE FINISHED SUCCESSFULLY ===", flush=True)
        
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}", flush=True)
        raise

if __name__ == "__main__":
    main()
