import os
import json
import requests
import time
import re
from bs4 import BeautifulSoup
from datetime import datetime, timezone

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "processed_leads.json"

TARGET_CHAINS = ['solana', 'base', 'binance', 'bsc', 'bnb']
CEX_AND_MEME_BLOCKLIST = [
    "coinbase", "robinhood", "binance", "kraken", "tokenize", "tokenized",
    "doge", "shib", "pepe", "safe", "elon", "inu", "floki", "pump", "moon", "bonk"
]
BAD_URL_KEYWORDS = ["coinbase.com", "robinhood.com", "binance.com", "kraken.com"]

TARGET_NEW_LEADS = 5

def load_processed_websites():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return set(json.load(f))
        except (json.JSONDecodeError, FileNotFoundError):
            return set()
    return set()

def save_processed_websites(websites_set):
    with open(STATE_FILE, "w") as f:
        json.dump(list(websites_set), f, indent=2)

def fetch_with_retry(url, headers, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                wait_time = 60 * (attempt + 1)
                print(f"    [RATE LIMIT] 429 detected. Pausing for {wait_time}s...", flush=True)
                time.sleep(wait_time)
            else:
                print(f"    [WARN] {url} returned status {response.status_code}.", flush=True)
                return None
        except requests.exceptions.RequestException as e:
            print(f"    [NETWORK ERROR] {e}. Retrying...", flush=True)
            time.sleep(5)
    print(f"    [CRITICAL] Failed to fetch {url} after {max_retries} retries.", flush=True)
    return None

# --- MODULE 1: Launchpad Scraper ---
def fetch_launchpad_projects():
    print("  [SOURCE: LAUNCHPADS] Fetching curated project directories...", flush=True)
    launchpad_leads = []
    
    # [NEEDS VERIFICATION]: Engineer must replace these placeholder URLs and JSON paths 
    # with the actual endpoints discovered via Chrome DevTools Network Tab (Fetch/XHR).
    launchpad_apis = {
        'Polkastarter': {
            'url': 'https://api.polkastarter.com/pools?page=1&limit=20', # [NEEDS VERIFICATION]
            'headers': {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        },
        'Legion': {
            'url': 'https://api.daolegion.org/api/projects?status=completed', # [NEEDS VERIFICATION]
            'headers': {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        },
        'ChainGPT': {
            'url': 'https://api.chaingpt.org/api/launchpad/projects', # [NEEDS VERIFICATION]
            'headers': {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        },
        'Jupiter Studio': {
            'url': 'https://terminal.jup.ag/api/v1/projects', # [NEEDS VERIFICATION]
            'headers': {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        }
    }

    for pad_name, config in launchpad_apis.items():
        print(f"    Fetching {pad_name}...", flush=True)
        data = fetch_with_retry(config['url'], config['headers'])
        
        if not data:
            continue
            
        # [NEEDS VERIFICATION]: Engineer must adjust this key mapping based on the actual API response structure.
        # Common patterns: data.get('data'), data.get('projects'), data.get('pools'), or just data if it's a list.
        projects = data.get('data', data.get('projects', data.get('pools', [])))
        if isinstance(projects, dict): # Sometimes it's nested deeper
            projects = projects.get('items', [])
            
        if not isinstance(projects, list):
            print(f"    [WARN] {pad_name} returned unexpected JSON structure. Skipping.", flush=True)
            continue
            
        for proj in projects:
            # [NEEDS VERIFICATION]: Adjust keys ('name', 'projectName', 'website', 'projectUrl', 'chains', 'network')
            name = proj.get('name', proj.get('projectName', proj.get('title', ''))).strip()
            website = proj.get('website', proj.get('projectUrl', proj.get('url', '')))
            chains = proj.get('chains', proj.get('network', proj.get('blockchain', [])))
            
            if isinstance(chains, str):
                chains = [chains]
                
            if not name or not website:
                continue
                
            launchpad_leads.append({
                'name': name,
                'website': website,
                'chains': chains,
                'source': f'{pad_name} Launchpad'
            })
            
        time.sleep(2) # Polite delay between launchpad APIs
        
    print(f"    Found {len(launchpad_leads)} raw projects from Launchpads.", flush=True)
    return launchpad_leads

# --- MODULE 2: Social Extraction ---
def extract_socials_from_html(url):
    twitter = "Not Found"
    telegram = "Not Found"
    
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Search for Twitter/X links
        tw_link = soup.find('a', href=re.compile(r'(twitter\.com|x\.com)/[a-zA-Z0-9_]{1,15}', re.IGNORECASE))
        if tw_link:
            match = re.search(r'(?:twitter\.com|x\.com)/([a-zA-Z0-9_]{1,15})', tw_link['href'], re.IGNORECASE)
            if match:
                twitter = f"@{match.group(1)}"
                
        # Search for Telegram links
        tg_link = soup.find('a', href=re.compile(r'(t\.me|telegram\.me)/[a-zA-Z0-9_]{5,32}', re.IGNORECASE))
        if tg_link:
            match = re.search(r'(?:t\.me|telegram\.me)/([a-zA-Z0-9_]{5,32})', tg_link['href'], re.IGNORECASE)
            if match:
                telegram = f"@{match.group(1)}"
                
    except Exception:
        pass # Silently fail and return "Not Found"
        
    return twitter, telegram

# --- MODULE 3: Strict ICP Filter & Unifier ---
def filter_and_enrich_leads(raw_leads):
    print("\n  [FILTER] Applying Strict ICP & Anti-Meme Filters...", flush=True)
    validated_leads = []
    processed_websites = load_processed_websites()
    
    for lead in raw_leads:
        if len(validated_leads) >= TARGET_NEW_LEADS:
            print(f"    [QUOTA MET] Found {TARGET_NEW_LEADS} leads. Stopping filter.", flush=True)
            break
            
        name_lower = lead['name'].lower()
        website_lower = lead['website'].lower()
        
        # 1. Deduplication
        if website_lower in processed_websites:
            continue
            
        # 2. Chain Verification
        if lead['chains']:
            if not any(chain.lower() in TARGET_CHAINS for chain in lead['chains']):
                continue
                
        # 3. Aggressive Blocklist
        if any(blocked in name_lower for blocked in CEX_AND_MEME_BLOCKLIST):
            print(f"    [DROP] {lead['name']} | Reason: Matched CEX/Meme blocklist", flush=True)
            continue
            
        # 4. Website Sanity Check
        if not website_lower.startswith('http') or any(bad in website_lower for bad in BAD_URL_KEYWORDS):
            continue
            
        # 5. Social Extraction
        twitter, telegram = extract_socials_from_html(lead['website'])
        
        if twitter == "Not Found" and telegram == "Not Found":
            print(f"    [DROP] {lead['name']} | Reason: No verifiable social presence found", flush=True)
            continue
            
        lead['twitter'] = twitter
        lead['telegram'] = telegram
        validated_leads.append(lead)
        
        processed_websites.add(website_lower)
        print(f"    [PASS] {lead['name']} | Source: {lead['source']}", flush=True)

    save_processed_websites(processed_websites)
    print(f"    Filtered down to {len(validated_leads)} high-signal, actionable leads.", flush=True)
    return validated_leads

# --- MODULE 4: Dynamic Outreach Generator ---
def generate_personalized_dm(lead):
    name = lead['name']
    chains = ', '.join(lead['chains']) if lead['chains'] else 'multiple chains'
    
    # Since launchpad data lacks precise volume/liquidity, we use a "Post-IDO Growth" angle
    template = (
        f"Hey team, huge fan of {name}'s progression since your launch on {lead['source']}.\n\n"
        f"Post-IDO, managing treasury exposure and driving sustained trading volume are the biggest hurdles. We built Vibe Trading to solve this for curated projects:\n"
        f"• **The Solution:** List a perp on Vibe for $0 USDC. Deposit idle treasury tokens to our vault to back the Open Interest.\n"
        f"• **The Result:** You capture perpetual trading fees to fund operations and deepen liquidity, without diluting your spot holders or selling on the open market.\n\n"
        f"Are you currently exploring any derivative or liquidity-management integrations for {name}?"
    )
    return template

def send_telegram_message(lead):
    dm_text = generate_personalized_dm(lead)
    
    telegram_message = (
        f"🎯 *NEW CURATED LEAD ({lead['source']})*\n\n"
        f"📛 *Project:* {lead['name']}\n"
        f"⛓️ *Chains:* {', '.join(lead['chains']).upper() if lead['chains'] else 'Unspecified'}\n"
        f"🔗 *Website:* {lead['website']}\n"
        f"🐦 *Twitter:* {lead['twitter']}\n"
        f"✈️ *Telegram:* {lead['telegram']}\n\n"
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
    print("=== STARTING CURATED DIRECTORY PIPELINE ===", flush=True)
    try:
        raw_leads = fetch_launchpad_projects()
        if not raw_leads:
            print("Exiting: No raw projects fetched. Verify API endpoints in code.", flush=True)
            return
            
        validated_leads = filter_and_enrich_leads(raw_leads)
        
        print(f"\n=== DELIVERY & STATE UPDATE ===", flush=True)
        for lead in validated_leads:
            send_telegram_message(lead)
            
        print(f"\n=== PIPELINE FINISHED SUCCESSFULLY ===", flush=True)
        print(f"Sent {len(validated_leads)} curated leads.", flush=True)
        
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}", flush=True)
        raise

if __name__ == "__main__":
    main()
