import os
import json
import requests
import re
import time

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "processed_leads.json"

# ICP PARAMETERS (Phase 4: Sweet Spot Balance)
TARGET_CHAINS = ["Solana", "Base", "Binance", "BSC", "Sol"]
ALLOWED_CATEGORIES = [
    "Infrastructure", "Services", "RWA", "Gaming", "Indexes", "Derivatives",
    "Yield", "Dexs", "Lending", "Liquid Staking"
]
BLOCKLIST_KEYWORDS = ["doge", "shib", "pepe", "safe", "elon", "inu", "floki", "moon", "pump", "rocket", "kishu", "baby"]

def fetch_defillama(max_retries=3):
    print("Fetching DefiLlama protocols...")
    url = "https://api.llama.fi/protocols"
    
    for attempt in range(max_retries):
        try:
            time.sleep(2)
            response = requests.get(url, timeout=30)
            
            if response.status_code == 429:
                print(f"Rate limited. Waiting 60 seconds before retry {attempt + 1}...")
                time.sleep(60)
                continue
                
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(10)
            else:
                raise
    return []

def is_blocked(name, symbol):
    name_lower = (name or "").lower()
    symbol_lower = (symbol or "").lower()
    for keyword in BLOCKLIST_KEYWORDS:
        if keyword in name_lower or keyword in symbol_lower:
            return True
    return False

def get_contacts(lead):
    telegram = "Not Found"
    twitter = "Not Found"
    
    url = lead.get('website')
    if url and str(url).startswith("http"):
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            response = requests.get(url, headers=headers, timeout=8)
            html = response.text
            
            tg_match = re.search(r'(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/([a-zA-Z0-9_]{5,32})', html)
            if not tg_match: 
                tg_match = re.search(r'@([a-zA-Z0-9_]{5,32})', html)
            
            tw_match = re.search(r'(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/([a-zA-Z0-9_]{1,15})', html)
            
            if tg_match: telegram = f"@{tg_match.group(1)}"
            if tw_match: twitter = f"@{tw_match.group(1)}"
        except Exception:
            pass

    if telegram == "Not Found" and lead.get('api_telegram'):
        tg_val = str(lead['api_telegram']).replace("https://t.me/", "").replace("@", "")
        telegram = f"@{tg_val}"
        
    if twitter == "Not Found" and lead.get('api_twitter'):
        tw_val = str(lead['api_twitter']).replace("https://twitter.com/", "").replace("https://x.com/", "").replace("@", "")
        twitter = f"@{tw_val}"
        
    return telegram, twitter

def normalize_leads(llama_data):
    print("=== PHASE 1: PRE-SCRAPER FILTERING ===")
    leads = []
    debug_count = 0
    max_debug = 30  # Increased cap slightly to see more volume rejections
    
    for proto in llama_data:
        name = proto.get("name")
        symbol = proto.get("symbol")
        category = proto.get("category", "")
        chains = proto.get("chains", [])
        tvl = proto.get("tvl", 0)
        url = proto.get("url")
        
        # 1. Category Filter
        if not any(allowed.lower() in category.lower() for allowed in ALLOWED_CATEGORIES):
            if debug_count < max_debug:
                print(f"  [PRE-SCRAPER] Skipped '{name}': Category '{category}' not allowed.")
                debug_count += 1
            continue
            
        # 2. Chain Filter
        if not any(chain in TARGET_CHAINS for chain in chains):
            if debug_count < max_debug:
                print(f"  [PRE-SCRAPER] Skipped '{name}': Chain not in target list.")
                debug_count += 1
            continue
            
        # 3. TVL Filter (Kept as a baseline sanity check)
        if tvl < 100000: # Lowered slightly to allow early-stage projects
            if debug_count < max_debug:
                print(f"  [PRE-SCRAPER] Skipped '{name}': TVL too low (${tvl:,}).")
                debug_count += 1
            continue

        # 4. Blocklist Filter
        if is_blocked(name, symbol):
            if debug_count < max_debug:
                print(f"  [PRE-SCRAPER] Skipped '{name}': Blocklist match.")
                debug_count += 1
            continue
            
        # 5. Basic Presence Filter
        if not url and not proto.get("twitter"):
            if debug_count < max_debug:
                print(f"  [PRE-SCRAPER] Skipped '{name}': No website or Twitter.")
                debug_count += 1
            continue 

        # 6. PHASE 4: STRICT SWEET SPOT FILTERS (MCAP & Volume)
        mcap = proto.get("mcap")
        # Fallback to volume1d if volume_24h is missing in API response
        volume_24h = proto.get("volume_24h", proto.get("volume1d", 0)) 
        
        # Handle missing/invalid MCAP
        if mcap is None or str(mcap).upper() == 'N/A':
            if debug_count < max_debug:
                print(f"  [PRE-SCRAPER] Skipped '{name}': MCAP is N/A or missing.")
                debug_count += 1
            continue

        try:
            mcap_value = float(mcap)
            vol_value = float(volume_24h) if volume_24h else 0.0
        except (ValueError, TypeError):
            if debug_count < max_debug:
                print(f"  [PRE-SCRAPER] Skipped '{name}': Could not parse MCAP/Volume values.")
                debug_count += 1
            continue

        # MCAP: $500k to $15M
        if not (500_000 <= mcap_value <= 15_000_000):
            if debug_count < max_debug:
                print(f"  [PRE-SCRAPER] Skipped '{name}': MCAP ${mcap_value:,.0f} outside $500k-$15M range.")
                debug_count += 1
            continue

        # VOLUME: Minimum $100,000 in 24h
        if vol_value < 100_000:
            if debug_count < max_debug:
                # Special log to see if volume data is just missing (0) or actually low
                vol_status = "Missing/0" if vol_value == 0 else f"${vol_value:,.0f}"
                print(f"  [PRE-SCRAPER] Skipped '{name}': 24h Vol {vol_status} is below $100k minimum.")
                debug_count += 1
            continue

        # If it passes all filters, log it and append
        print(f"  [PRE-SCRAPER] ✅ PASSED: '{name}' | MCAP: ${mcap_value:,.0f} | 24h Vol: ${vol_value:,.0f}")
        
        leads.append({
            "source": "DefiLlama_ICP",
            "slug": proto.get("slug"),
            "name": name,
            "symbol": symbol,
            "category": category,
            "chain": next((c for c in chains if c in TARGET_CHAINS), "Multi-Chain"),
            "website": url,
            "mcap": mcap_value,
            "tvl": tvl,
            "volume_24h": vol_value,
            "api_twitter": proto.get("twitter"),
            "api_telegram": proto.get("telegram")
        })
    
    leads.sort(key=lambda x: x.get("volume_24h", 0), reverse=True) # Sort by Volume now, not TVL
    print(f"\nFound {len(leads)} projects passing Phase 1. Sending top 5 to Phase 2 (Scraper)...")
    return leads[:5]

def load_processed_slugs():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return []

def save_processed_slugs(slugs):
    with open(STATE_FILE, "w") as f:
        json.dump(slugs, f)

def send_telegram_message(lead):
    print(f"\n=== PHASE 2: POST-SCRAPER EVALUATION FOR '{lead['name']}' ===")
    
    mcap_str = f"${int(lead['mcap']):,}" if lead.get('mcap') else "N/A"
    vol_str = f"${int(lead.get('volume_24h', 0)):,}"
    tvl_str = f"${int(lead['tvl']):,}" if lead.get('tvl') else "N/A"
    
    print(f"  [SCRAPER] Attempting to extract contacts from {lead.get('website', 'API Fallback')}...")
    telegram, twitter = get_contacts(lead)
    
    if telegram == "Not Found" and twitter == "Not Found":
        print(f"  [POST-SCRAPER REJECTION] ❌ Dropped '{lead['name']}' -> No Twitter or Telegram link found after scraping.")
        return

    print(f"  [POST-SCRAPER SUCCESS] ✅ Contacts found: TG={telegram}, TW={twitter}")

    text = (
        f"🚨 *High-Potential Vibe Trading Lead*\n\n"
        f"📛 *Project*: {lead['name']} ({lead.get('symbol', 'N/A')})\n"
        f" *Category*: {lead['category']}\n"
        f"⛓️ *Chain*: {lead['chain']}\n"
        f" *Market Cap*: {mcap_str}\n"
        f"📊 *24h Volume*: {vol_str}\n"
        f" *TVL*: {tvl_str}\n"
        f"🔗 *Website*: {lead.get('website', 'N/A')}\n\n"
        f"📞 *Extracted Contacts*:\n"
        f"• Telegram: {telegram}\n"
        f"• Twitter: {twitter}\n\n"
        f"💬 *Vibe Pitch*: \"Instead of dumping treasury, deposit tokens to Vibe's vault to list a perp for free, back OI, and earn trading fees forever.\""
    )
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    requests.post(url, json=payload)
    print(f"  [TELEGRAM] ✅ Message sent to channel for {lead['name']}")

def main():
    print("=== STARTING ICP-TARGETED LEAD GEN PIPELINE ===")
    try:
        llama_data = fetch_defillama()
        all_leads = normalize_leads(llama_data)
        
        processed_slugs = load_processed_slugs()
        print(f"\n=== PHASE 3: DEDUPLICATION ===")
        print(f"Current processed slugs in memory: {len(processed_slugs)}")
        
        new_leads = [lead for lead in all_leads if lead["slug"] not in processed_slugs]
        print(f"New leads to process after deduplication: {len(new_leads)}")
        
        if len(new_leads) == 0 and len(all_leads) > 0:
            print("⚠️ WARNING: All top 5 leads were already in processed_leads.json. No new messages sent.")
        
        for lead in new_leads:
            send_telegram_message(lead)
            processed_slugs.append(lead["slug"])
            
        save_processed_slugs(processed_slugs)
        print("\n=== PIPELINE FINISHED SUCCESSFULLY ===")
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}")
        raise

if __name__ == "__main__":
    main()
