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
def is_blocked(name, symbol):
    name_lower = (name or "").lower()
    symbol_lower = (symbol or "").lower()
    for pattern in BLOCKLIST_PATTERNS:
        if pattern.search(name_lower) or pattern.search(symbol_lower):
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
        url = f"https://api.geckoterminal.com/api/v2/networks/{network}/pools?sort=volume_usd_h24_desc&page=1"
        try:
            time.sleep(1.5) # Rate limiting between network requests
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            for pool in data.get('data', []):
                attrs = pool.get('attributes', {})
                name = attrs.get('name', '')
                symbol = attrs.get('base_token', {}).get('symbol', '')
                website = attrs.get('website_url')
                twitter_handle = attrs.get('twitter_handle')
                
                # Utility Proxy Filter: Must have a website
                if not website or str(website).strip() == "":
                    continue
                
                # Regex Blocklist
                if is_blocked(name, symbol):
                    continue
                
                # Hard Filters: Age >= 14 days
                created_at_str = attrs.get('pool_created_at')
                if created_at_str:
                    try:
                        created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                        age_days = (now - created_at).days
                        if age_days < 14:
                            continue
                    except ValueError:
                        continue # Skip if date parsing fails
                
                # Hard Filters: Reserve >= $50k
                reserve_usd = float(attrs.get('reserve_in_usd', 0) or 0)
                if reserve_usd < 50_000:
                    continue
                
                # Hard Filters: 24h Volume >= $50k
                vol_h24 = float(attrs.get('volume_usd', {}).get('h24', 0) or 0)
                if vol_h24 < 50_000:
                    continue
                
                discovered_pools.append({
                    'pool_name': name,
                    'token_symbol': symbol,
                    'website': website,
                    'twitter_handle': twitter_handle,
                    'volume_usd_h24': vol_h24,
                    'network': network.capitalize()
                })
                
            print(f"    Found {len(discovered_pools)} pools passing filters so far.")
        except Exception as e:
            print(f"    [ERROR] Failed to fetch {network}: {e}")
            continue
            
    print(f"Module 1 Complete: {len(discovered_pools)} total pools discovered.")
    return discovered_pools

# --- MODULE 2: ENRICHMENT (Website Scraper & GitHub) ---
def module2_enrichment(discovered_pools):
    print("\n=== MODULE 2: ENRICHMENT (Scraper & GitHub) ===")
    enriched_leads = []
    seen_websites = set()
    seen_symbols = set()
    
    for pool in discovered_pools:
        website = pool['website']
        symbol = pool['token_symbol']
        
        # In-run Deduplication
        if website in seen_websites or symbol in seen_symbols:
            print(f"  [DEDUPE] Skipped duplicate: {pool['pool_name']}")
            continue
            
        print(f"  Enriching: {pool['pool_name']}...")
        
        tg_web, tw_web, gh_web = get_contacts_from_website(website)
        
        # Fallback to GeckoTerminal data if scraper found nothing
        telegram = tg_web if tg_web != "Not Found" else format_handle(pool.get('twitter_handle'))
        twitter = tw_web if tw_web != "Not Found" else format_handle(pool.get('twitter_handle'))
        
        if telegram == "Not Found" and twitter == "Not Found":
            print(f"    [DROP] No contact methods found.")
            continue
            
        is_active, gh_status = check_github_activity(gh_web)
        if not is_active:
            print(f"    [FLAG] {gh_status}, but proceeding due to strong on-chain metrics.")
        else:
            print(f"    [PASS] GitHub: {gh_status}")
            
        pool['telegram'] = telegram
        pool['twitter'] = twitter
        enriched_leads.append(pool)
        
        seen_websites.add(website)
        seen_symbols.add(symbol)
        print(f"    [PASS] Contacts: TG={telegram}, TW={twitter}")
        
    print(f"Module 2 Complete: {len(enriched_leads)} leads fully enriched.")
    return enriched_leads

# --- MODULE 3: DEDUPLICATION & OUTPUT ---
def load_processed_websites():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return []

def save_processed_websites(websites):
    with open(STATE_FILE, "w") as f:
        json.dump(websites, f)

def send_telegram_message(lead):
    vol_str = f"${int(lead['volume_usd_h24']):,}"
    website = lead.get('website', 'N/A')
    
    text = (
        f"🚨 *High-Potential Vibe Trading Lead*\n\n"
        f"📛 *Project*: {lead['pool_name']} ({lead.get('token_symbol', 'N/A')})\n"
        f"⛓️ *Chain*: {lead['network']}\n"
        f"📊 *24h Volume*: {vol_str}\n"
        f"🔗 *Website*: {website}\n\n"
        f"📞 *Extracted Contacts*:\n"
        f"• Telegram: {lead['telegram']}\n"
        f"• Twitter: {lead['twitter']}\n\n"
        f"💬 *Vibe Pitch*: \"Instead of dumping treasury, deposit tokens to Vibe's vault to list a perp for free, back OI, and earn trading fees forever.\""
    )
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    requests.post(url, json=payload)
    print(f"  [TELEGRAM] ✅ Sent message for {lead['pool_name']}")

def main():
    print("=== STARTING MULTI-STAGE QUALIFICATION FUNNEL (PHASE 11: ON-CHAIN ACTIVITY PIVOT) ===")
    try:
        stage1 = module1_discovery()
        if not stage1:
            print("Exiting: Module 1 returned no results or failed.")
            return
            
        stage2 = module2_enrichment(stage1)
        
        processed_websites = load_processed_websites()
        print(f"\n=== MODULE 3: DEDUPLICATION & OUTPUT ===")
        print(f"Current processed websites in memory: {len(processed_websites)}")
        
        # Historical Deduplication
        new_leads = [lead for lead in stage2 if lead['website'] not in processed_websites]
        print(f"New leads to process after historical deduplication: {len(new_leads)}")
        
        if len(new_leads) == 0 and len(stage2) > 0:
            print("⚠️ WARNING: All enriched leads were already processed. No new messages sent.")
        
        for lead in new_leads:
            send_telegram_message(lead)
            processed_websites.append(lead['website'])
            
        save_processed_websites(processed_websites)
        print("\n=== PIPELINE FINISHED SUCCESSFULLY ===")
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}")
        raise

if __name__ == "__main__":
    main()
