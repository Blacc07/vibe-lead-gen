import os
import json
import requests
import re
import time

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "processed_leads.json"

# ICP PARAMETERS
TARGET_CHAINS = ["Solana", "Base", "Binance"] # DefiLlama uses "Binance" for BSC
ALLOWED_CATEGORIES = ["Dexes", "Yield", "Launchpad", "Services", "Infrastructure", "Gaming"]
BLOCKLIST_KEYWORDS = ["doge", "shib", "pepe", "safe", "elon", "inu", "floki", "moon", "pump", "rocket", "kishu", "baby"]

def fetch_defillama(max_retries=3):
    """Fetches protocols with built-in retry and rate-limit protection."""
    print("Fetching DefiLlama protocols...")
    url = "https://api.llama.fi/protocols"
    
    for attempt in range(max_retries):
        try:
            time.sleep(2)  # Polite delay to avoid triggering WAF
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
    """Checks if project name or symbol contains blocklist keywords."""
    name_lower = (name or "").lower()
    symbol_lower = (symbol or "").lower()
    for keyword in BLOCKLIST_KEYWORDS:
        if keyword in name_lower or keyword in symbol_lower:
            return True
    return False

def get_contacts(lead):
    """Extracts contacts from website HTML, falling back to DefiLlama API fields."""
    telegram = "Not Found"
    twitter = "Not Found"
    
    # 1. Try scraping the website first
    url = lead.get('website')
    if url and str(url).startswith("http"):
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            response = requests.get(url, headers=headers, timeout=8)
            html = response.text
            
            # Telegram Regex
            tg_match = re.search(r'(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/([a-zA-Z0-9_]{5,32})', html)
            if not tg_match: 
                tg_match = re.search(r'@([a-zA-Z0-9_]{5,32})', html)
            
            # Twitter/X Regex
            tw_match = re.search(r'(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/([a-zA-Z0-9_]{1,15})', html)
            
            if tg_match: telegram = f"@{tg_match.group(1)}"
            if tw_match: twitter = f"@{tw_match.group(1)}"
        except Exception:
            pass  # Fallback to API fields below

    # 2. Fallback to DefiLlama API fields if website scrape failed
    if telegram == "Not Found" and lead.get('api_telegram'):
        tg_val = str(lead['api_telegram']).replace("https://t.me/", "").replace("@", "")
        telegram = f"@{tg_val}"
        
    if twitter == "Not Found" and lead.get('api_twitter'):
        tw_val = str(lead['api_twitter']).replace("https://twitter.com/", "").replace("https://x.com/", "").replace("@", "")
        twitter = f"@{tw_val}"
        
    return telegram, twitter

def normalize_leads(llama_data):
    print("Filtering based on ICP parameters (Pre-Scrape)...")
    leads = []
    
    for proto in llama_data:
        name = proto.get("name")
        symbol = proto.get("symbol")
        category = proto.get("category", "")
        chains = proto.get("chains", [])
        tvl = proto.get("tvl", 0)
        mcap = proto.get("mcap")
        url = proto.get("url")
        
        # 1. Category Filter
        if not any(allowed.lower() in category.lower() for allowed in ALLOWED_CATEGORIES):
            continue
            
        # 2. Chain Filter
        if not any(chain in TARGET_CHAINS for chain in chains):
            continue
            
        # 3. Metric Filter (TVL >= 250k, MCAP 1M - 10M)
        if tvl < 250000 or not mcap or not (1000000 <= mcap <= 10000000):
            continue
            
        # 4. Blocklist Filter
        if is_blocked(name, symbol):
            continue
            
        # 5. Basic Presence Filter (Must have a Website OR Twitter to even attempt scraping)
        if not url and not proto.get("twitter"):
            continue 

        leads.append({
            "source": "DefiLlama_ICP",
            "slug": proto.get("slug"),
            "name": name,
            "symbol": symbol,
            "category": category,
            "chain": next((c for c in chains if c in TARGET_CHAINS), "Multi-Chain"),
            "website": url,
            "mcap": mcap,
            "tvl": tvl,
            "api_twitter": proto.get("twitter"),
            "api_telegram": proto.get("telegram")
        })
    
    # Sort by TVL descending (highest traction first)
    leads.sort(key=lambda x: x.get("tvl", 0), reverse=True)
    
    print(f"Found {len(leads)} projects passing initial ICP metrics. Sending top 5 to scraper...")
    return leads[:5]  # Strictly limit to top 5 highest TVL leads per run

def load_processed_slugs():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return []

def save_processed_slugs(slugs):
    with open(STATE_FILE, "w") as f:
        json.dump(slugs, f)

def send_telegram_message(lead):
    mcap_str = f"${int(lead['mcap']):,}"
    tvl_str = f"${int(lead['tvl']):,}"
    
    print(f"Scraping contacts for: {lead['name']}")
    telegram, twitter = get_contacts(lead)
    
    # FINAL QUALITY GATE: Drop if completely uncontactable after all efforts
    if telegram == "Not Found" and twitter == "Not Found":
        print(f"⚠️ Dropped {lead['name']}: No contact methods found after scraping.")
        return

    text = (
        f"🚨 *High-Potential Vibe Trading Lead*\n\n"
        f" *Project*: {lead['name']} ({lead.get('symbol', 'N/A')})\n"
        f"📂 *Category*: {lead['category']}\n"
        f"️ *Chain*: {lead['chain']}\n"
        f"💰 *Market Cap*: {mcap_str}\n"
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
    print(f"✅ Sent message for {lead['name']}")

def main():
    print("=== STARTING ICP-TARGETED LEAD GEN PIPELINE ===")
    try:
        llama_data = fetch_defillama()
        all_leads = normalize_leads(llama_data)
        
        processed_slugs = load_processed_slugs()
        new_leads = [lead for lead in all_leads if lead["slug"] not in processed_slugs]
        print(f"New leads to process: {len(new_leads)}")
        
        for lead in new_leads:
            send_telegram_message(lead)
            processed_slugs.append(lead["slug"])
            
        save_processed_slugs(processed_slugs)
        print("=== PIPELINE FINISHED SUCCESSFULLY ===")
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}")
        raise

if __name__ == "__main__":
    main()
