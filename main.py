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

TARGET_CHAINS = ["solana", "base", "bsc"]

# Blocklists to prevent catching the "quote" token (e.g., catching SOL instead of the utility token)
BASE_TOKEN_BLOCKLIST = ["sol", "eth", "usdc", "usdt", "bnb", "weth", "wsol", "dai"]
MEME_BLOCKLIST_PATTERNS = [
    re.compile(r'\b(pepe|doge|shib|inu|floki|bonk|wojak|pump|moon|safe)\b', re.IGNORECASE)
]

def is_blocked(name, symbol):
    name_lower = (name or "").lower()
    symbol_lower = (symbol or "").lower()
    
    if symbol_lower in BASE_TOKEN_BLOCKLIST:
        return True
        
    for pattern in MEME_BLOCKLIST_PATTERNS:
        if pattern.search(name_lower) or pattern.search(symbol_lower):
            return True
    return False

def check_github_activity(website_url):
    """Checks if the project's GitHub has had recent activity."""
    if not website_url:
        return "Unknown", "No website to scrape"
        
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(website_url, headers=headers, timeout=8)
        gh_match = re.search(r'(https?://github\.com/[a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+)', resp.text)
        if not gh_match:
            return "Unknown", "No GitHub link found on website"
            
        repo_path = gh_match.group(1).split('github.com/')[-1]
        api_url = f"https://api.github.com/repos/{repo_path}"
        
        time.sleep(1.5) # Rate limit
        gh_resp = requests.get(api_url, headers={"Accept": "application/vnd.github.v3+json"}, timeout=10)
        
        if gh_resp.status_code == 200:
            pushed_at = datetime.fromisoformat(gh_resp.json()['pushed_at'].replace('Z', '+00:00'))
            days = (datetime.now(timezone.utc) - pushed_at).days
            if days <= 60:
                return "Active", f"Last commit {days} days ago"
            else:
                return "Stale", f"Last commit {days} days ago"
        return "Unknown", "GitHub API error"
    except Exception as e:
        return "Unknown", f"Check failed: {str(e)[:50]}"

# --- MODULE 1: DISCOVERY & LIQUIDITY FILTER (DexScreener) ---
def module1_discovery():
    print("=== MODULE 1: DISCOVERY & ANTI-MANIPULATION FILTER (DexScreener) ===", flush=True)
    discovered_leads = []
    
    for chain in TARGET_CHAINS:
        print(f"  Fetching top pairs for {chain}...", flush=True)
        url = f"https://api.dexscreener.com/latest/dex/pairs/{chain}"
        
        try:
            time.sleep(2) # Rate limiting
            response = requests.get(url, timeout=15)
            if response.status_code != 200:
                print(f"    [ERROR] {response.status_code} for {chain}", flush=True)
                continue
                
            pairs = response.json().get('pairs', [])
            
            for pair in pairs:
                base_token = pair.get('baseToken', {})
                name = base_token.get('name', '')
                symbol = base_token.get('symbol', '')
                
                if is_blocked(name, symbol):
                    continue
                
                # Extract Metrics
                fdv = pair.get('fdv', 0) or 0  # Using FDV as Market Cap proxy
                liquidity = pair.get('liquidity', {}).get('usd', 0) or 0
                volume_h24 = pair.get('volume', {}).get('h24', 0) or 0
                
                # 1. Strict Market Cap Range (True Low-Cap)
                if not (500_000 <= fdv <= 5_000_000):
                    continue
                    
                # 2. Liquidity to MCAP Ratio (The Manipulation Killer: Must be >= 5%)
                if fdv > 0 and (liquidity / fdv) < 0.05:
                    continue
                    
                # 3. Minimum Real Volume
                if volume_h24 < 30_000:
                    continue
                
                # Extract Socials from DexScreener info
                info = pair.get('info', {})
                websites = info.get('websites', [])
                website = websites[0].get('url', '') if websites else ''
                
                socials = info.get('socials', [])
                twitter = next((s.get('url', '') for s in socials if 'twitter' in s.get('url', '').lower() or 'x.com' in s.get('url', '').lower()), '')
                telegram = next((s.get('url', '') for s in socials if 't.me' in s.get('url', '').lower()), '')
                
                if not website and not twitter:
                    continue # Utility Proxy: Must have a web presence

                discovered_leads.append({
                    'name': name,
                    'symbol': symbol.upper(),
                    'chain': chain.capitalize(),
                    'fdv': float(fdv),
                    'liquidity': float(liquidity),
                    'volume_h24': float(volume_h24),
                    'liq_mcap_ratio': round((liquidity / fdv) * 100, 2),
                    'website': website,
                    'twitter': twitter,
                    'telegram': telegram,
                    'token_address': base_token.get('address', '')
                })
                
            print(f"    Found {len(discovered_leads)} pairs passing anti-manipulation filters so far.", flush=True)
            
        except Exception as e:
            print(f"    [CRITICAL ERROR] {e}", flush=True)
            
    print(f"Module 1 Complete: {len(discovered_leads)} high-potential leads discovered.", flush=True)
    return discovered_leads

# --- MODULE 2: ENRICHMENT (Dev Activity Check) ---
def module2_enrichment(leads):
    print("\n=== MODULE 2: ENRICHMENT (Dev Activity & Deduplication) ===", flush=True)
    enriched_leads = []
    seen_addresses = set()
    
    for lead in leads:
        if lead['token_address'] in seen_addresses:
            continue
            
        print(f"  Checking Dev Activity for: {lead['name']}...", flush=True)
        gh_status, gh_msg = check_github_activity(lead['website'])
        lead['github_status'] = gh_status
        lead['github_msg'] = gh_msg
        
        enriched_leads.append(lead)
        seen_addresses.add(lead['token_address'])
        print(f"    [DEV CHECK] {gh_status}: {gh_msg}", flush=True)
        
    return enriched_leads

# --- MODULE 3: OUTPUT ---
def load_processed():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return []

def save_processed(data):
    with open(STATE_FILE, "w") as f:
        json.dump(data, f)

def send_telegram_message(lead):
    text = (
        f"🚨 *Institutional-Grade Vibe Trading Lead*\n\n"
        f"📛 *Project*: {lead['name']} (${lead['symbol']})\n"
        f"⛓️ *Chain*: {lead['chain']}\n"
        f"💰 *FDV (MCAP)*: ${int(lead['fdv']):,}\n"
        f"💧 *Liquidity*: ${int(lead['liquidity']):,} ({lead['liq_mcap_ratio']}% of FDV)\n"
        f"📊 *24h Volume*: ${int(lead['volume_h24']):,}\n"
        f"💻 *Dev Activity*: {lead['github_status']} ({lead['github_msg']})\n"
        f"🔗 *Website*: {lead['website'] or 'N/A'}\n\n"
        f"📞 *Contacts*:\n"
        f"• Twitter: {lead['twitter'] or 'Not Found'}\n"
        f"• Telegram: {lead['telegram'] or 'Not Found'}\n\n"
        f"⚠️ *MANUAL CHECKS REQUIRED BEFORE OUTREACH*:\n"
        f"1. Verify Holder Concentration (Bubblemaps)\n"
        f"2. Verify Vesting/Unlocks (TokenUnlocks)\n\n"
        f"💬 *Vibe Pitch*: \"Instead of dumping treasury, deposit tokens to Vibe's vault to list a perp for free, back OI, and earn trading fees forever.\""
    )
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    requests.post(url, json=payload, timeout=10)
    print(f"  [TELEGRAM] ✅ Sent message for {lead['name']}", flush=True)

def main():
    print("=== STARTING PHASE 18: ANTI-MANIPULATION & REAL UTILITY FUNNEL ===", flush=True)
    try:
        stage1 = module1_discovery()
        if not stage1:
            print("Exiting: No leads passed the anti-manipulation filters.", flush=True)
            save_processed([]) # Prevent git crash
            return
            
        stage2 = module2_enrichment(stage1)
        
        processed = load_processed()
        new_leads = [lead for lead in stage2 if lead['token_address'] not in processed]
        
        print(f"\n=== MODULE 3: DELIVERY ===", flush=True)
        print(f"New leads to process: {len(new_leads)}", flush=True)
        
        for lead in new_leads:
            send_telegram_message(lead)
            processed.append(lead['token_address'])
            
        save_processed(processed)
        print("\n=== PIPELINE FINISHED SUCCESSFULLY ===", flush=True)
        
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}", flush=True)
        save_processed(load_processed()) # Prevent git crash on failure
        raise

if __name__ == "__main__":
    main()
