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

TARGET_NETWORKS = ["solana", "base", "bsc"]
BLOCKLIST_PATTERNS = [re.compile(r'\b(pepe|doge|shib|inu|floki|bonk|wojak|pump|moon|safe)\b', re.IGNORECASE)]

# --- HELPER FUNCTIONS ---
def is_blocked(name):
    name_lower = (name or "").lower()
    for pattern in BLOCKLIST_PATTERNS:
        if pattern.search(name_lower):
            return True
    return False

def format_handle(handle):
    """Safely formats a social handle or URL into a clean @username."""
    if not handle:
        return "Not Found"
    handle = str(handle).strip()
    if handle.startswith('http'):
        match = re.search(r'(?:twitter\.com|x\.com|t\.me|telegram\.me)/([a-zA-Z0-9_]{1,32})', handle)
        if match:
            return f"@{match.group(1)}"
        return handle
    return f"@{handle}" if not handle.startswith('@') else handle

def get_contacts_from_website(website_url):
    telegram = "Not Found"
    twitter = "Not Found"
    github = "Not Found"
    
    if website_url and str(website_url).startswith("http"):
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            response = requests.get(website_url, headers=headers, timeout=8)
            html = response.text
            
            tg_match = re.search(r'(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/([a-zA-Z0-9_]{5,32})', html)
            if not tg_match: 
                tg_match = re.search(r'@([a-zA-Z0-9_]{5,32})', html)
            
            tw_match = re.search(r'(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/([a-zA-Z0-9_]{1,15})', html)
            gh_match = re.search(r'(https?://github\.com/[a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+)', html)
            
            if tg_match: telegram = f"@{tg_match.group(1)}"
            if tw_match: twitter = f"@{tw_match.group(1)}"
            if gh_match: github = gh_match.group(1)
        except Exception:
            pass
            
    return telegram, twitter, github

def check_github_activity(github_url):
    if not github_url or github_url == "Not Found":
        return True, "No GitHub URL"
        
    match = re.search(r'github\.com/([a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+)', github_url)
    if not match:
        return True, "Invalid GitHub URL format"
        
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
            if days_since_push > 60:
                return False, f"Low Dev Activity ({days_since_push} days)"
            return True, f"Active ({days_since_push} days)"
        return True, "API Error/Not Found"
    except Exception as e:
        return True, f"Check Failed ({str(e)})"

# --- MODULE 1: DISCOVERY (GeckoTerminal High-Volume Pools) ---
def module1_discovery():
    print("=== MODULE 1: DISCOVERY (GeckoTerminal High-Volume Pools) ===")
    discovered_pools = []
    now = datetime.now(timezone.utc)
    
    for network in TARGET_NETWORKS:
        print(f"  Fetching top pools for {network}...")
        
        # VERIFIED FIX: Exact sort parameter demanded by the API
        url = f"https://api.geckoterminal.com/api/v2/networks/{network}/pools?sort=h24_volume_usd_desc&page=1"
        
        headers = {
            "accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        try:
            time.sleep(1.5) # Rate limiting between network requests
            response = requests.get(url, headers=headers, timeout=15)
            
            if response.status_code != 200:
                print(f"    [ERROR] Failed to fetch {network}: {response.status_code} {response.reason}")
                print(f"    [API RESPONSE] {response.text}")
                continue
                
            data = response.json()
            pools = data.get('data', [])
            
            for pool in pools:
                attributes = pool.get('attributes', {})
                pool_name = attributes.get('name', '')
                
                # 1. Regex Blocklist
                if is_blocked(pool_name):
                    continue
                
                # 2. Extract Volume (Nested dictionary)
                volume_dict = attributes.get('volume_usd', {})
                volume_h24 = float(volume_dict.get('h24', 0) or 0)
                
                # 3. Extract Reserve/Liquidity
                reserve_usd = float(attributes.get('reserve_in_usd', 0) or 0)
                
                # 4. Calculate Age
                created_at_str = attributes.get('pool_created_at')
                age_in_days = 0
                if created_at_str:
                    try:
                        created_dt = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                        age_in_days = (now - created_dt).days
                    except ValueError:
                        continue

                # 5. Apply Hard Filters
                if age_in_days < 14:
                    continue
                if reserve_usd < 50_000:
                    continue
                if volume_h24 < 50_000:
                    continue
                    
                # 6. Extract Token Address for Enrichment
                relationships = pool.get('relationships', {})
                base_token_id = relationships.get('base_token', {}).get('data', {}).get('id', '')
                token_address = base_token_id.split('_')[-1] if '_' in base_token_id else ''

                discovered_pools.append({
                    'name': pool_name,
                    'network': network,
                    'volume_h24': volume_h24,
                    'reserve_usd': reserve_usd,
                    'token_address': token_address
                })
                
            print(f"    Found {len(discovered_pools)} pools passing filters so far.")
            
        except requests.exceptions.RequestException as e:
            print(f"    [ERROR] Request exception for {network}: {e}")
            continue
            
    print(f"Module 1 Complete: {len(discovered_pools)} total pools discovered.")
    return discovered_pools

# --- MODULE 2: ENRICHMENT (Token Endpoint & Scraper) ---
def module2_enrichment(discovered_pools):
    print("\n=== MODULE 2: ENRICHMENT (Token Endpoint & Scraper) ===")
    enriched_leads = []
    seen_addresses = set()
    
    for pool in discovered_pools:
        token_address = pool['token_address']
        network = pool['network']
        
        # In-run Deduplication
        if token_address in seen_addresses:
            print(f"  [DEDUPE] Skipped duplicate address: {pool['name']}")
            continue
            
        print(f"  Enriching: {pool['name']}...")
        
        website_url = ""
        twitter_handle = ""
        
        # Query GeckoTerminal Token Endpoint for website/twitter
        if token_address:
            try:
                time.sleep(1.0) # Rate limiting
                token_url = f"https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{token_address}"
                token_resp = requests.get(token_url, headers={"accept": "application/json"}, timeout=10)
                if token_resp.status_code == 200:
                    token_data = token_resp.json()
                    token_attrs = token_data.get('data', {}).get('attributes', {})
                    website_url = token_attrs.get('website_url', '') or token_attrs.get('website', '')
                    twitter_handle = token_attrs.get('twitter_handle', '') or token_attrs.get('twitter', '')
            except Exception as e:
                print(f"    [WARN] Token endpoint lookup failed: {e}")

        # Utility Proxy Filter: Must have a website or twitter from the token data
        if not website_url and not twitter_handle:
            print(f"    [DROP] No website or twitter found in token data.")
            continue

        # Web Scraper Fallback/Enhancement
        tg_web, tw_web, gh_web = get_contacts_from_website(website_url)
        
        telegram = tg_web if tg_web != "Not Found" else format_handle(twitter_handle)
        twitter = tw_web if tw_web != "Not Found" else format_handle(twitter_handle)
        
        if telegram == "Not Found" and twitter == "Not Found":
            print(f"    [DROP] No contact methods found after scraping.")
            continue
            
        is_active, gh_status = check_github_activity(gh_web)
        if not is_active:
            print(f"    [FLAG] {gh_status}, but proceeding due to strong on-chain metrics.")
        else:
            print(f"    [PASS] GitHub: {gh_status}")
            
        pool['website'] = website_url
        pool['telegram'] = telegram
        pool['twitter'] = twitter
        enriched_leads.append(pool)
        
        seen_addresses.add(token_address)
        print(f"    [PASS] Contacts: TG={telegram}, TW={twitter}")
        
    print(f"Module 2 Complete: {len(enriched_leads)} leads fully enriched.")
    return enriched_leads

# --- MODULE 3: DEDUPLICATION & OUTPUT ---
def load_processed_addresses():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return []

def save_processed_addresses(addresses):
    with open(STATE_FILE, "w") as f:
        json.dump(addresses, f)

def send_telegram_message(lead):
    vol_str = f"${int(lead['volume_h24']):,}"
    res_str = f"${int(lead['reserve_usd']):,}"
    website = lead.get('website', 'N/A')
    
    text = (
        f"🚨 *High-Potential Vibe Trading Lead*\n\n"
        f"📛 *Project*: {lead['name']}\n"
        f"⛓️ *Chain*: {lead['network'].capitalize()}\n"
        f"📊 *24h Volume*: {vol_str}\n"
        f"💧 *Liquidity*: {res_str}\n"
        f"🔗 *Website*: {website}\n\n"
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
    print("=== STARTING MULTI-STAGE QUALIFICATION FUNNEL (VERIFIED API SYNTAX) ===")
    try:
        stage1 = module1_discovery()
        if not stage1:
            print("Exiting: Module 1 returned no results or failed.")
            return
            
        stage2 = module2_enrichment(stage1)
        
        processed_addresses = load_processed_addresses()
        print(f"\n=== MODULE 3: DEDUPLICATION & OUTPUT ===")
        print(f"Current processed addresses in memory: {len(processed_addresses)}")
        
        # Historical Deduplication based on token address
        new_leads = [lead for lead in stage2 if lead['token_address'] not in processed_addresses]
        print(f"New leads to process after historical deduplication: {len(new_leads)}")
        
        if len(new_leads) == 0 and len(stage2) > 0:
            print("⚠️ WARNING: All enriched leads were already processed. No new messages sent.")
        
        for lead in new_leads:
            send_telegram_message(lead)
            processed_addresses.append(lead['token_address'])
            
        save_processed_addresses(processed_addresses)
        print("\n=== PIPELINE FINISHED SUCCESSFULLY ===")
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}")
        raise

if __name__ == "__main__":
    main()
