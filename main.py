import os
import json
import requests
import re
import time
from bs4 import BeautifulSoup

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "processed_leads.json"

TARGET_CHAINS = ['solana', 'base', 'binance', 'bsc', 'bnb']
CEX_BLOCKLIST = ["coinbase.com", "robinhood.com", "binance.com", "kraken.com", "okx.com", "bybit.com", "tokenize", "tokenized"]
MEME_PATTERNS = [re.compile(r'\b(pepe|doge|shib|inu|floki|bonk|wojak|pump|moon|safe)\b', re.IGNORECASE)]

TARGET_QUOTA = 5

def load_processed_websites():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return set(json.load(f))
        except (json.JSONDecodeError, FileNotFoundError):
            return set()
    return set()

def save_processed_websites(websites_set):
    # Always save, even if empty, to prevent git crashes
    with open(STATE_FILE, "w") as f:
        json.dump(list(websites_set), f, indent=2)

def fetch_with_retry(url, max_retries=3):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                time.sleep(60 * (attempt + 1))
            else:
                return None
        except requests.exceptions.RequestException:
            time.sleep(5)
    return None

# --- MODULE 1: Ecosystem Directories ---
def fetch_ecosystem_directories():
    print("  [SOURCE 1] Fetching Ecosystem Directories...", flush=True)
    ecosystem_leads = []
    
    # [NEEDS VERIFICATION]: Engineer must find the actual XHR/JSON endpoint via Chrome DevTools
    sources = [
        {
            'name': 'Base Ecosystem',
            'url': 'https://www.base.org/ecosystem-fund?_rsc=f3Thn-3X2L3XeD6R',
            'chain': 'Base',
            'data_key': 'apps',
            'name_key': 'name',
            'url_key': 'url'
        },
        {
            'name': 'Binance MVB',
            'url': 'https://www.binance.com/bapi/apex/v1/friendly/apex/web/launchpool/holder/project?pageIndex=1&pageSize=20',
            'chain': 'Binance',
            'data_key': 'projects',
            'name_key': 'name',
            'url_key': 'website'
        }
    ]
    
    for src in sources:
        try:
            data = fetch_with_retry(src['url'])
            if data and isinstance(data, dict):
                items = data.get(src['data_key'], [])
                if isinstance(items, list):
                    for proj in items:
                        name = proj.get(src['name_key'], '')
                        website = proj.get(src['url_key'], '')
                        if name and website:
                            ecosystem_leads.append({
                                'name': name.strip(),
                                'website': website.strip(),
                                'source': src['name'],
                                'chain': src['chain']
                            })
        except Exception as e:
            print(f"    [ERROR] {src['name']} fetch failed: {e}", flush=True)
            
    print(f"    Found {len(ecosystem_leads)} projects from Ecosystem Directories.", flush=True)
    return ecosystem_leads

# --- MODULE 2: Mid-Tier Launchpads ---
def fetch_launchpad_graduates():
    print("  [SOURCE 2] Fetching Launchpad Graduates...", flush=True)
    launchpad_leads = []
    
    # [NEEDS VERIFICATION]: Engineer must find the actual XHR/JSON endpoint via Chrome DevTools
    sources = [
        {
            'name': 'Polkastarter',
            'url': 'https://launchpad.polkastarter.com/v3/projects?status=finalized&view=metrics&search=&page=1&sort_direction=desc&sort_by=finalized_at&per_page=15&order=finalized_at&sort=desc',
            'data_key': 'pools',
            'name_key': 'projectName',
            'url_key': 'projectWebsite'
        },
        {
            'name': 'ChainGPT Pad',
            'url': 'https://padapi.chaingpt.org/pools/v3/complete-sale-pools-details',
            'data_key': 'launches',
            'name_key': 'name',
            'url_key': 'websiteUrl'
        },
        {
            'name': 'Legion',
            'url': 'https://api.legion.cc/sales/past?limit=100', 
            'data_key': 'projects',
            'name_key': 'name',
            'url_key': 'website'
        },
        {
            'name': 'Jupiter Studio',
            'url': ' https://datapi.jup.ag/v2/search?query=&tokenExactCaseInsensitive=false&tokenVerified=false&includeStocks=true', 
            'data_key': 'projects',
            'name_key': 'name',
            'url_key': 'website'
        }
    ]
    
    for src in sources:
        try:
            data = fetch_with_retry(src['url'])
            if data and isinstance(data, dict):
                items = data.get(src['data_key'], [])
                if isinstance(items, list):
                    for proj in items:
                        name = proj.get(src['name_key'], '')
                        website = proj.get(src['url_key'], '')
                        if name and website:
                            launchpad_leads.append({
                                'name': name.strip(),
                                'website': website.strip(),
                                'source': src['name'],
                                'chain': 'Multi-chain (Verify in Module 3)'
                            })
            time.sleep(2) # Polite delay
        except Exception as e:
            print(f"    [ERROR] {src['name']} fetch failed: {e}", flush=True)
            
    print(f"    Found {len(launchpad_leads)} projects from Launchpads.", flush=True)
    return launchpad_leads

# --- MODULE 3 HELPERS ---
def verify_chain_via_defillama(project_name):
    """Checks if a project is officially tracked on Solana, Base, or Binance by DefiLlama."""
    try:
        # DefiLlama protocols list is cached by their CDN, very fast
        resp = requests.get("https://api.llama.fi/protocols", timeout=15)
        if resp.status_code == 200:
            protocols = resp.json()
            name_lower = project_name.lower()
            
            for proto in protocols:
                proto_name = proto.get('name', '').lower()
                proto_symbol = proto.get('symbol', '').lower()
                
                # Fuzzy match
                if name_lower in proto_name or proto_name in name_lower or proto_symbol == name_lower:
                    chains = [c.lower() for c in proto.get('chains', [])]
                    if any(target in chains for target in TARGET_CHAINS):
                        return True
            return False
    except Exception:
        return False # Fail open to avoid blocking on temporary DL outage, but log it
    return False

def extract_socials(website_url, project_name):
    """Scrapes website for socials, falls back to CoinGecko search."""
    twitter, telegram = "Not Found", "Not Found"
    
    # 1. Try Website Scraping
    try:
        resp = requests.get(website_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        tw = soup.find('a', href=re.compile(r'(twitter\.com|x\.com)/[a-zA-Z0-9_]{1,15}', re.IGNORECASE))
        if tw:
            match = re.search(r'(?:twitter\.com|x\.com)/([a-zA-Z0-9_]{1,15})', tw['href'], re.IGNORECASE)
            if match: twitter = f"@{match.group(1)}"
            
        tg = soup.find('a', href=re.compile(r'(t\.me|telegram\.me)/[a-zA-Z0-9_]{5,32}', re.IGNORECASE))
        if tg:
            match = re.search(r'(?:t\.me|telegram\.me)/([a-zA-Z0-9_]{5,32})', tg['href'], re.IGNORECASE)
            if match: telegram = f"@{match.group(1)}"
    except Exception:
        pass

    # 2. Fallback to CoinGecko if website scraping failed
    if twitter == "Not Found" or telegram == "Not Found":
        try:
            search_url = f"https://api.coingecko.com/api/v3/search?query={project_name.replace(' ', '%20')}"
            resp = requests.get(search_url, timeout=10)
            if resp.status_code == 200:
                coins = resp.json().get('coins', [])
                for coin in coins[:3]: # Check top 3 matches
                    detail_url = f"https://api.coingecko.com/api/v3/coins/{coin.get('id')}"
                    detail_resp = requests.get(detail_url, timeout=10)
                    if detail_resp.status_code == 200:
                        links = detail_resp.json().get('links', {})
                        if twitter == "Not Found" and links.get('twitter_screen_name'):
                            twitter = f"@{links.get('twitter_screen_name')}"
                        if telegram == "Not Found" and links.get('telegram_channel_identifier'):
                            telegram = f"@{links.get('telegram_channel_identifier')}"
                        if twitter != "Not Found" or telegram != "Not Found":
                            break
        except Exception:
            pass
            
    return twitter, telegram

# --- MODULE 3 MAIN: Unifier & Filter ---
def run_unified_pipeline():
    print("\n=== RUNNING UNIFIED PIPELINE ===", flush=True)
    ecosystems = fetch_ecosystem_directories()
    launchpads = fetch_launchpad_graduates()
    
    all_raw_leads = ecosystems + launchpads
    print(f"  [UNIFIER] Raw pool size: {len(all_raw_leads)} projects.", flush=True)
    
    # Deduplicate by Website URL
    seen_websites = set()
    unique_leads = []
    for lead in all_raw_leads:
        website = lead.get('website', '').strip().lower()
        if website and website not in seen_websites:
            seen_websites.add(website)
            unique_leads.append(lead)
            
    validated_leads = []
    processed_websites = load_processed_websites()
    
    for lead in unique_leads:
        if len(validated_leads) >= TARGET_QUOTA:
            print(f"  [QUOTA MET] Found {TARGET_QUOTA} leads. Stopping.", flush=True)
            break
            
        name_lower = lead['name'].lower()
        website_lower = lead['website'].lower()
        
        # Filter A: CEX/Tokenized domains
        if any(cex in website_lower for cex in CEX_BLOCKLIST):
            continue
            
        # Filter B: Memecoins
        if any(re.search(pattern, name_lower) for pattern in MEME_PATTERNS):
            continue
            
        # Filter C: Chain Verification
        chain_verified = False
        if lead['chain'] != 'Multi-chain (Verify in Module 3)':
            chain_verified = True
        else:
            chain_verified = verify_chain_via_defillama(lead['name'])
            
        if not chain_verified:
            continue
            
        # Filter D: Already processed
        if website_lower in processed_websites:
            continue
            
        # Filter E: Extract Socials
        twitter, telegram = extract_socials(lead['website'], lead['name'])
        if twitter == "Not Found" and telegram == "Not Found":
            continue
            
        lead['twitter'] = twitter
        lead['telegram'] = telegram
        validated_leads.append(lead)
        processed_websites.add(website_lower)
        print(f"  [PASS] {lead['name']} ({lead['source']})", flush=True)

    save_processed_websites(processed_websites)
    print(f"  [FILTER] {len(validated_leads)} leads passed strict ICP filters.", flush=True)
    return validated_leads

# --- OUTPUT: Dynamic Outreach (Launchpad/Grant Focused) ---
def generate_personalized_dm(lead):
    name = lead['name']
    chains = lead['chain'] if lead['chain'] != 'Multi-chain (Verify in Module 3)' else 'multiple chains'
    
    # Using "Engagement Catalyst" / "Idle Asset" angle as DEX volume is not the primary metric here
    template = (
        f"Hey team, huge fan of {name}'s progression since your {lead['source']} milestone.\n\n"
        f"Post-launch, managing treasury exposure and driving sustained trading volume are the biggest hurdles for builders on {chains}. We built Vibe Trading to solve this:\n"
        f"• **The Solution:** List a perp on Vibe for $0 USDC. Deposit idle treasury tokens to our vault to back the Open Interest.\n"
        f"• **The Result:** You capture perpetual trading fees to fund operations and deepen liquidity, without diluting your spot holders or selling on the open market.\n\n"
        f"Are you currently exploring any derivative or liquidity-management integrations for {name}?"
    )
    return template

def send_telegram_message(lead):
    dm_text = generate_personalized_dm(lead)
    twitter_display = lead['twitter'] if lead['twitter'] != "Not Found" else 'N/A'
    telegram_display = lead['telegram'] if lead['telegram'] != "Not Found" else 'N/A'
    
    telegram_message = (
        f"🎯 *NEW CURATED LEAD ({lead['source']})*\n\n"
        f"📛 *Project:* {lead['name']}\n"
        f"⛓️ *Chain:* {lead['chain'].upper() if lead['chain'] != 'Multi-chain (Verify in Module 3)' else 'VERIFIED TARGET CHAIN'}\n"
        f"🔗 *Website:* {lead['website']}\n"
        f"🐦 *Twitter:* {twitter_display}\n"
        f"✈️ *Telegram:* {telegram_display}\n\n"
        f"📝 *READY-TO-SEND DM:*\n"
        f"```\n{dm_text}\n```"
    )
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": telegram_message, "parse_mode": "Markdown"}
    
    try:
        requests.post(url, json=payload, timeout=10)
        print(f"  [TELEGRAM] ✅ Sent sales dossier for {lead['name']}", flush=True)
    except Exception as e:
        print(f"  [TELEGRAM] ❌ Failed to send: {e}", flush=True)

# --- MAIN EXECUTION ---
def main():
    print("=== STARTING 7-SOURCE UNIFIED PIPELINE ===", flush=True)
    try:
        # Ensure state file exists to prevent git crashes on early exit
        save_processed_websites(load_processed_websites())
        
        validated_leads = run_unified_pipeline()
        if not validated_leads:
            print("Exiting: No leads passed the unified filters.", flush=True)
            return
            
        print(f"\n=== DELIVERY ===", flush=True)
        for lead in validated_leads:
            send_telegram_message(lead)
            
        print(f"\n=== PIPELINE FINISHED SUCCESSFULLY ===", flush=True)
        print(f"Sent {len(validated_leads)} curated leads.", flush=True)
        
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}", flush=True)
        raise

if __name__ == "__main__":
    main()
