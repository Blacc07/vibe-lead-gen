import os
import json
import requests
import re
import time
from datetime import datetime, timezone

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "processed_leads.json"

CHAIN_WHITELIST = ["Solana", "Base", "Binance", "BSC", "Binance Smart Chain"]
CATEGORY_WHITELIST = [
    "Dexs", "Yield", "Yield Aggregator", "Lending", "Liquid Staking", 
    "Infrastructure", "Services", "RWA", "Gaming", "Derivatives", "CDP"
]
# Refined blocklist: Only obvious memecoin patterns, no false positives on "safe", "ai", or "moon"
BLOCKLIST_PATTERNS = [re.compile(r'\b(pepe|doge|shib|inu|floki|kishu|bonk|wojak)\b', re.IGNORECASE)]

# --- HELPER FUNCTIONS ---
def is_blocked(name, symbol):
    name_lower = (name or "").lower()
    symbol_lower = (symbol or "").lower()
    for pattern in BLOCKLIST_PATTERNS:
        if pattern.search(name_lower) or pattern.search(symbol_lower):
            return True
    return False

def get_contacts(website_url, dl_twitter, dl_telegram):
    telegram = "Not Found"
    twitter = "Not Found"
    
    # 1. Web Scraper
    if website_url and str(website_url).startswith("http"):
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            response = requests.get(website_url, headers=headers, timeout=8)
            html = response.text
            
            tg_match = re.search(r'(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/([a-zA-Z0-9_]{5,32})', html)
            if not tg_match: 
                tg_match = re.search(r'@([a-zA-Z0-9_]{5,32})', html)
            
            tw_match = re.search(r'(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/([a-zA-Z0-9_]{1,15})', html)
            
            if tg_match: telegram = f"@{tg_match.group(1)}"
            if tw_match: twitter = f"@{tw_match.group(1)}"
        except Exception:
            pass # Fallback to API

    # 2. DefiLlama Fallback
    if telegram == "Not Found" and dl_telegram:
        telegram = f"@{str(dl_telegram).replace('https://t.me/', '').replace('@', '')}"
    if twitter == "Not Found" and dl_twitter:
        twitter = f"@{str(dl_twitter).replace('https://twitter.com/', '').replace('https://x.com/', '').replace('@', '')}"
        
    return telegram, twitter

def check_github_activity(url):
    """Returns (is_active, status_message). Flags if > 60 days but does NOT hard-drop."""
    if not url:
        return True, "No GitHub URL"
        
    match = re.search(r'github\.com/([a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+)', url)
    if not match:
        return True, "Invalid GitHub URL format"
        
    repo_path = match.group(1)
    api_url = f"https://api.github.com/repos/{repo_path}"
    
    try:
        time.sleep(1) # Rate limiting for GitHub API
        headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "Vibe-Lead-Gen"}
        response = requests.get(api_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            pushed_at = datetime.fromisoformat(data['pushed_at'].replace('Z', '+00:00'))
            days_since_push = (datetime.now(timezone.utc) - pushed_at).days
            if days_since_push > 60:
                return False, f"Low Dev Activity ({days_since_push} days)"
            return True, f"Active ({days_since_push} days)"
        return True, "API Error/Not Found"
    except Exception as e:
        return True, f"Check Failed ({str(e)})"

# --- MODULE 1: DISCOVERY (CoinGecko) ---
def module1_discovery():
    print("=== MODULE 1: DISCOVERY (CoinGecko) ===")
    discovered_coins = []
    url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=1&sparkline=false"
    
    try:
        time.sleep(1) # Rate limiting
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        for coin in data:
            name = coin.get('name', '')
            symbol = coin.get('symbol', '')
            market_cap = coin.get('market_cap', 0) or 0
            total_volume = coin.get('total_volume', 0) or 0
            
            # Hard Filters
            if is_blocked(name, symbol):
                continue
                
            if not (100_000 <= market_cap <= 15_000_000):
                continue
                
            if total_volume < 50_000:
                continue
                
            discovered_coins.append({
                'id': coin.get('id'),
                'name': name,
                'symbol': symbol.upper(),
                'market_cap': market_cap,
                'total_volume': total_volume
            })
            
        print(f"Module 1 Complete: {len(discovered_coins)} coins discovered passing initial filters.")
    except Exception as e:
        print(f"  [CRITICAL ERROR] Failed to fetch CoinGecko: {e}")
        return []
        
    return discovered_coins

# --- MODULE 2: VALIDATION (DefiLlama) ---
def module2_validation(discovered_coins):
    print("\n=== MODULE 2: VALIDATION (DefiLlama) ===")
    validated_projects = []
    
    print("  Fetching DefiLlama protocols cache...")
    try:
        dl_response = requests.get("https://api.llama.fi/protocols", timeout=30)
        dl_response.raise_for_status()
        dl_protocols = dl_response.json()
    except Exception as e:
        print(f"  [CRITICAL ERROR] Failed to fetch DefiLlama: {e}")
        return []

    for coin in discovered_coins:
        coin_name_lower = coin['name'].lower()
        coin_symbol_lower = coin['symbol'].lower()
        
        # Search for match in DefiLlama
        matched_proto = None
        for proto in dl_protocols:
            proto_name_lower = proto.get('name', '').lower()
            proto_symbol_lower = proto.get('symbol', '').lower()
            
            # Fuzzy match: coin name contains protocol name, or exact symbol match
            if proto_name_lower in coin_name_lower or coin_name_lower in proto_name_lower or proto_symbol_lower == coin_symbol_lower:
                matched_proto = proto
                break
                
        if not matched_proto:
            continue # Not a recognized protocol
            
        chains = matched_proto.get('chains', [])
        category = matched_proto.get('category', '')
        tvl = float(matched_proto.get('tvl', 0) or 0)
        
        # Chain Whitelist
        if not any(chain in CHAIN_WHITELIST for chain in chains):
            continue
            
        # Category Whitelist
        if not any(allowed.lower() in category.lower() for allowed in CATEGORY_WHITELIST):
            continue
            
        validated_projects.append({
            'name': matched_proto.get('name'),
            'symbol': matched_proto.get('symbol'),
            'category': category,
            'chains': chains,
            'website': matched_proto.get('url'),
            'tvl': tvl,
            'market_cap': coin['market_cap'],
            'volume_24h': coin['total_volume'],
            'dl_twitter': matched_proto.get('twitter'),
            'dl_telegram': matched_proto.get('telegram'),
            'dl_github': matched_proto.get('github')
        })
        
    print(f"Module 2 Complete: {len(validated_projects)} projects validated.")
    return validated_projects

# --- MODULE 3: ENRICHMENT (Scraper & GitHub) ---
def module3_enrichment(validated_projects):
    print("\n=== MODULE 3: ENRICHMENT (Scraper & GitHub) ===")
    enriched_leads = []
    
    for project in validated_projects:
        print(f"  Enriching: {project['name']}...")
        
        # 1. Contact Extraction
        telegram, twitter = get_contacts(project['website'], project['dl_twitter'], project['dl_telegram'])
        
        if telegram == "Not Found" and twitter == "Not Found":
            print(f"    [DROP] No contact methods found.")
            continue
            
        # 2. GitHub Activity Check (Soft drop / flag only)
        github_url = project.get('dl_github')
        if not github_url and project['website']:
            try:
                web_resp = requests.get(project['website'], timeout=8)
                gh_match = re.search(r'(https?://github\.com/[a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+)', web_resp.text)
                if gh_match:
                    github_url = gh_match.group(1)
            except Exception:
                pass

        is_active, gh_status = check_github_activity(github_url)
        if not is_active:
            print(f"    [FLAG] {gh_status}, but proceeding due to strong on-chain metrics.")
        else:
            print(f"    [PASS] GitHub: {gh_status}")
            
        project['telegram'] = telegram
        project['twitter'] = twitter
        enriched_leads.append(project)
        print(f"    [PASS] Contacts: TG={telegram}, TW={twitter}")
        
    print(f"Module 3 Complete: {len(enriched_leads)} leads fully enriched.")
    return enriched_leads

# --- STATE & DELIVERY ---
def load_processed_slugs():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return []

def save_processed_slugs(slugs):
    with open(STATE_FILE, "w") as f:
        json.dump(slugs, f)

def send_telegram_message(lead):
    mcap_str = f"${int(lead['market_cap']):,}" if lead.get('market_cap') else "N/A"
    vol_str = f"${int(lead['volume_24h']):,}" if lead.get('volume_24h') else "N/A"
    tvl_str = f"${int(lead['tvl']):,}" if lead.get('tvl') and lead['tvl'] > 0 else "N/A"
    
    # Filter chains to only show whitelisted ones in the message
    valid_chains = [c for c in lead['chains'] if c in CHAIN_WHITELIST]
    chain_str = ", ".join(valid_chains) if valid_chains else "Multi-Chain"
    
    text = (
        f"🚨 *High-Potential Vibe Trading Lead*\n\n"
        f"📛 *Project*: {lead['name']} ({lead.get('symbol', 'N/A')})\n"
        f"📂 *Category*: {lead['category']}\n"
        f"⛓️ *Chain*: {chain_str}\n"
        f"💰 *Market Cap*: {mcap_str}\n"
        f"📊 *24h Volume*: {vol_str}\n"
        f"🏦 *TVL*: {tvl_str}\n"
        f"🔗 *Website*: {lead.get('website', 'N/A')}\n\n"
        f"📞 *Extracted Contacts*:\n"
        f"• Telegram: {lead['telegram']}\n"
        f"• Twitter: {lead['twitter']}\n\n"
        f"💬 *Vibe Pitch*: \"Instead of dumping treasury, deposit tokens to Vibe's vault to list a perp for free, back OI, and earn trading fees forever.\""
    )
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    requests.post(url, json=payload)
    print(f"  [TELEGRAM] ✅ Sent message for {lead['name']}")

def main():
    print("=== STARTING MULTI-STAGE QUALIFICATION FUNNEL (PHASE 7) ===")
    try:
        # Execute Funnel
        stage1 = module1_discovery()
        if not stage1:
            print("Exiting: Module 1 returned no results or failed.")
            return
            
        stage2 = module2_validation(stage1)
        stage3 = module3_enrichment(stage2)
        
        # Deduplication
        processed_slugs = load_processed_slugs()
        print(f"\n=== DEDUPLICATION ===")
        print(f"Current processed slugs in memory: {len(processed_slugs)}")
        
        # Use name as slug for deduplication
        new_leads = [lead for lead in stage3 if lead['name'] not in processed_slugs]
        print(f"New leads to process after deduplication: {len(new_leads)}")
        
        if len(new_leads) == 0 and len(stage3) > 0:
            print("⚠️ WARNING: All enriched leads were already processed. No new messages sent.")
        
        for lead in new_leads:
            send_telegram_message(lead)
            processed_slugs.append(lead['name'])
            
        save_processed_slugs(processed_slugs)
        print("\n=== PIPELINE FINISHED SUCCESSFULLY ===")
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}")
        raise

if __name__ == "__main__":
    main()
