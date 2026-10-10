import os
import json
import requests
import re
import time
from datetime import datetime, timezone, timedelta

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "processed_leads.json"

TARGET_NETWORKS = ["solana", "base", "bsc"]
ALLOWED_CATEGORIES = ["Yield", "Dexs", "Infrastructure", "Services", "RWA", "Gaming", "Derivatives", "Lending", "Liquid Staking"]
BLOCKLIST_KEYWORDS = ["doge", "shib", "pepe", "safe", "elon", "inu", "floki", "moon", "pump", "rocket", "kishu", "baby"]

# --- HELPER FUNCTIONS ---
def is_blocked(name, symbol):
    name_lower = (name or "").lower()
    symbol_lower = (symbol or "").lower()
    return any(kw in name_lower or kw in symbol_lower for kw in BLOCKLIST_KEYWORDS)

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
            if not tg_match: tg_match = re.search(r'@([a-zA-Z0-9_]{5,32})', html)
            
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
    """Returns True if active (pushed within 45 days), False if dead/invalid."""
    if not url:
        return True # No GitHub URL is not an automatic disqualifier, just skip check
    
    match = re.search(r'github\.com/([a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+)', url)
    if not match:
        return True
        
    repo_path = match.group(1)
    api_url = f"https://api.github.com/repos/{repo_path}"
    
    try:
        time.sleep(1.5) # Rate limiting for GitHub API
        headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "Vibe-Lead-Gen"}
        response = requests.get(api_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            pushed_at = datetime.fromisoformat(data['pushed_at'].replace('Z', '+00:00'))
            days_since_push = (datetime.now(timezone.utc) - pushed_at).days
            return days_since_push <= 45
        return True # If 404 or other error, don't auto-drop, just proceed
    except Exception:
        return True

# --- MODULE 1: TIER 1 DISCOVERY (GeckoTerminal) ---
def module1_discovery():
    print("=== MODULE 1: TIER 1 DISCOVERY (GeckoTerminal) ===")
    discovered_pools = []
    
    for network in TARGET_NETWORKS:
        print(f"  Fetching new pools for {network}...")
        url = f"https://api.geckoterminal.com/api/v2/networks/{network}/new_pools"
        try:
            time.sleep(1.5) # Rate limiting
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            for pool in data.get('data', []):
                attrs = pool.get('attributes', {})
                name = attrs.get('name', '')
                symbol = attrs.get('symbol', '')
                
                # Hard Filters
                if is_blocked(name, symbol):
                    continue
                    
                created_at_str = attrs.get('pool_created_at')
                if created_at_str:
                    created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                    age_days = (datetime.now(timezone.utc) - created_at).days
                    if age_days < 14:
                        continue
                
                vol_h24 = float(attrs.get('volume_usd', {}).get('h24', 0) or 0)
                reserve = float(attrs.get('reserve_in_usd', 0) or 0)
                
                if vol_h24 >= 50000 and reserve >= 20000:
                    discovered_pools.append({
                        'name': name,
                        'symbol': symbol,
                        'network': network,
                        'volume_24h': vol_h24,
                        'reserve': reserve
                    })
            print(f"  Found {len(discovered_pools)} pools passing Module 1 filters so far.")
        except Exception as e:
            print(f"  [ERROR] Failed to fetch {network}: {e}")
            
    print(f"Module 1 Complete: {len(discovered_pools)} pools discovered.")
    return discovered_pools

# --- MODULE 2: TIER 2 VALIDATION (DefiLlama) ---
def module2_validation(discovered_pools):
    print("\n=== MODULE 2: TIER 2 VALIDATION (DefiLlama) ===")
    validated_projects = []
    
    print("  Fetching DefiLlama protocols cache...")
    try:
        dl_response = requests.get("https://api.llama.fi/protocols", timeout=30)
        dl_response.raise_for_status()
        dl_protocols = dl_response.json()
    except Exception as e:
        print(f"  [CRITICAL ERROR] Failed to fetch DefiLlama: {e}")
        return []

    for pool in discovered_pools:
        pool_name_lower = pool['name'].lower()
        pool_symbol_lower = pool['symbol'].lower()
        
        # Search for match in DefiLlama
        matched_proto = None
        for proto in dl_protocols:
            proto_name_lower = proto.get('name', '').lower()
            proto_symbol_lower = proto.get('symbol', '').lower()
            
            # Fuzzy match: pool name contains protocol name, or exact symbol match
            if proto_name_lower in pool_name_lower or pool_name_lower in proto_name_lower or proto_symbol_lower == pool_symbol_lower:
                matched_proto = proto
                break
                
        if not matched_proto:
            continue # Not a recognized protocol, likely just a random token
            
        category = matched_proto.get('category', '')
        tvl = float(matched_proto.get('tvl', 0) or 0)
        mcap = float(matched_proto.get('mcap', 0) or 0)
        
        # Category Whitelist
        if not any(allowed.lower() in category.lower() for allowed in ALLOWED_CATEGORIES):
            continue
            
        # Size Sweet Spot ($100k - $15M)
        size_metric = tvl if tvl > 0 else mcap
        if not (100000 <= size_metric <= 15000000):
            continue
            
        validated_projects.append({
            'name': matched_proto.get('name'),
            'symbol': matched_proto.get('symbol'),
            'category': category,
            'chain': pool['network'].capitalize(),
            'website': matched_proto.get('url'),
            'tvl': tvl,
            'mcap': mcap,
            'dl_twitter': matched_proto.get('twitter'),
            'dl_telegram': matched_proto.get('telegram'),
            'dl_github': matched_proto.get('github')
        })
        
    print(f"Module 2 Complete: {len(validated_projects)} projects validated.")
    return validated_projects

# --- MODULE 3: TIER 3 ENRICHMENT (Scraper & GitHub) ---
def module3_enrichment(validated_projects):
    print("\n=== MODULE 3: TIER 3 ENRICHMENT (Scraper & GitHub) ===")
    enriched_leads = []
    
    for project in validated_projects:
        print(f"  Enriching: {project['name']}...")
        
        # 1. Contact Extraction
        telegram, twitter = get_contacts(project['website'], project['dl_twitter'], project['dl_telegram'])
        
        if telegram == "Not Found" and twitter == "Not Found":
            print(f"    [DROP] No contact methods found.")
            continue
            
        # 2. GitHub Activity Check
        github_url = project.get('dl_github')
        # Attempt to find github in website if not in DL
        if not github_url and project['website']:
            try:
                web_resp = requests.get(project['website'], timeout=8)
                gh_match = re.search(r'(https?://github\.com/[a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+)', web_resp.text)
                if gh_match:
                    github_url = gh_match.group(1)
            except Exception:
                pass

        if github_url:
            is_active = check_github_activity(github_url)
            if not is_active:
                print(f"    [DROP] GitHub repo inactive (>45 days).")
                continue
                
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
    mcap_str = f"${int(lead['mcap']):,}" if lead.get('mcap') and lead['mcap'] > 0 else "N/A"
    tvl_str = f"${int(lead['tvl']):,}" if lead.get('tvl') and lead['tvl'] > 0 else "N/A"
    
    text = (
        f"🚨 *High-Potential Vibe Trading Lead*\n\n"
        f"📛 *Project*: {lead['name']} ({lead.get('symbol', 'N/A')})\n"
        f"📂 *Category*: {lead['category']}\n"
        f"⛓️ *Chain*: {lead['chain']}\n"
        f"💰 *Market Cap*: {mcap_str}\n"
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
    print("=== STARTING MULTI-STAGE QUALIFICATION FUNNEL ===")
    try:
        # Execute Funnel
        stage1 = module1_discovery()
        stage2 = module2_validation(stage1)
        stage3 = module3_enrichment(stage2)
        
        # Deduplication
        processed_slugs = load_processed_slugs()
        print(f"\n=== DEDUPLICATION ===")
        print(f"Current processed slugs in memory: {len(processed_slugs)}")
        
        # Use name+chain as slug for deduplication since GeckoTerminal doesn't have DefiLlama slugs
        new_leads = [lead for lead in stage3 if f"{lead['name']}_{lead['chain']}" not in processed_slugs]
        print(f"New leads to process after deduplication: {len(new_leads)}")
        
        if len(new_leads) == 0 and len(stage3) > 0:
            print("⚠️ WARNING: All enriched leads were already processed. No new messages sent.")
        
        for lead in new_leads:
            send_telegram_message(lead)
            processed_slugs.append(f"{lead['name']}_{lead['chain']}")
            
        save_processed_slugs(processed_slugs)
        print("\n=== PIPELINE FINISHED SUCCESSFULLY ===")
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}")
        raise

if __name__ == "__main__":
    main()
