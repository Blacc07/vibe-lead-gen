import os
import json
import requests
import time
from datetime import datetime, timezone

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "processed_leads.json"

# DexScreener Chain IDs
CHAIN_IDS = ['solana', 'base', 'bsc']

# Block CEX/Tokenized domains in the website URL
CEX_DOMAINS = ["coinbase.com", "robinhood.com", "tokenize", "tokenized", "binance.com", "kraken.com", "okx.com", "bybit.com"]

# Quota System
TARGET_NEW_LEADS = 4

def load_processed_ids():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return set(json.load(f))
        except (json.JSONDecodeError, FileNotFoundError):
            return set()
    return set()

def save_processed_ids(ids_set):
    with open(STATE_FILE, "w") as f:
        json.dump(list(ids_set), f, indent=2)

def fetch_with_retry(url, max_retries=3):
    """Robust fetch with exponential backoff to survive rate limits."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"}
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                wait_time = 60 * (attempt + 1)
                print(f"    [RATE LIMIT] 429 detected. Pausing for {wait_time} seconds...", flush=True)
                time.sleep(wait_time)
            else:
                print(f"    [ERROR] Unexpected status {response.status_code} for {url}", flush=True)
                return None
        except requests.exceptions.RequestException as e:
            print(f"    [NETWORK ERROR] {e}. Retrying...", flush=True)
            time.sleep(5)
    print(f"    [CRITICAL] Failed to fetch {url} after {max_retries} retries.", flush=True)
    return None

# --- DYNAMIC TEMPLATE ENGINE ---
def generate_personalized_dm(lead):
    name = lead['name'].split('/')[0].strip()
    chain = lead['network'].capitalize()
    volume = f"${lead['volume_h24']:,.0f}"
    age = lead['age_days']
    
    if lead['volume_h24'] > 10_000_000:
        template = (
            f"Hey team, been tracking {name}'s traction on {chain}. The trading volume you’re seeing right now is incredible.\n\n"
            f"With volume this high, a lot of speculative trading and fee revenue is likely leaking to external perp platforms. We built Vibe Trading to help projects like yours capture that value:\n"
            f"• **The Solution:** List a perp on Vibe for $0 USDC. Deposit idle tokens to our vault to back the Open Interest.\n"
            f"• **The Result:** You earn perpetual trading fees to fund operations without selling a single token on the spot market.\n\n"
            f"Mind if I send over a quick 1-pager on how the vault mechanics work?"
        )
    elif age < 30:
        template = (
            f"Hey team, huge fan of the momentum behind {name} on {chain}.\n\n"
            f"Keeping the community engaged between major updates is always the hardest part for early-stage projects. We built Vibe Trading to solve this:\n"
            f"• **The Solution:** Give your community a native reason to trade. Launch a perp on Vibe with up to 60x leverage.\n"
            f"• **The Result:** Drives massive daily engagement, keeps traders in your ecosystem, and generates perpetual fee revenue for your treasury.\n\n"
            f"Are you currently exploring any derivative integrations for {name}?"
        )
    elif lead['liquidity_usd'] > 1_000_000:
        template = (
            f"Hey team, great work on the recent traction for {name} on {chain}.\n\n"
            f"One thing we see with high-potential tokens is that fragmented liquidity can scare away larger traders. We built Vibe Trading to solve this:\n"
            f"• **The Solution:** Consolidate your liquidity on Vibe. Deposit tokens to our vault to back a perp market for $0 USDC.\n"
            f"• **The Result:** You provide traders with deep liquidity and tight fills, stabilizing the chart while your treasury earns the trading fees.\n\n"
            f"Open to seeing a quick case study on how this works?"
        )
    else:
        template = (
            f"Hey team, great work on the recent traction for {name} on {chain}.\n\n"
            f"Managing a treasury that is heavily weighted in native tokens is tough when operational costs are in USD. We built Vibe Trading to solve this:\n"
            f"• **The Solution:** Put those idle tokens to work. Deposit them to our vault to instantly list a perp market for $0 USDC.\n"
            f"• **The Result:** Your idle tokens generate perpetual yield from trading fees, covering your USD costs without diluting your spot holders.\n\n"
            f"Happy to share a brief breakdown of the economics if you're open to it?"
        )
    
    return template

# --- SOURCE: DexScreener New Pairs (Website-First Filter) ---
def source_new_pairs(processed_ids):
    print("  [SOURCE] Fetching DexScreener New Pairs (Website-First Filter)...", flush=True)
    validated_leads = []
    
    for chain in CHAIN_IDS:
        if len(validated_leads) >= TARGET_NEW_LEADS:
            print(f"    [QUOTA MET] Found {TARGET_NEW_LEADS} new leads. Stopping discovery.", flush=True)
            break
            
        print(f"  Fetching new pairs for {chain}...", flush=True)
        url = f"https://api.dexscreener.com/latest/dex/pairs/{chain}"
        
        data = fetch_with_retry(url)
        if not data:
            print(f"    [ERROR] Failed to fetch {chain}. Moving to next chain.", flush=True)
            continue
            
        pairs = data.get('pairs', [])
        
        for pair in pairs:
            if len(validated_leads) >= TARGET_NEW_LEADS:
                break
                
            # 1. Extract Base Token Info
            base_token = pair.get('baseToken', {})
            token_symbol = base_token.get('symbol', '')
            token_address = base_token.get('address', '')
            
            # Skip if already processed
            if token_address in processed_ids:
                continue
                
            # 2. THE ULTIMATE FILTER: Must have a valid website in metadata
            # NOTE: DexScreener API schema uses 'websites' (list of dicts) and 'socials' (list of dicts)
            info = pair.get('info', {})
            websites = info.get('websites', [])
            website = websites[0].get('url', '') if websites else ''
            
            socials = info.get('socials', [])
            twitter = next((s.get('url', '') for s in socials if s.get('type') == 'twitter'), '')
            
            if not website:
                continue # Instantly skip projects without a declared website
                
            # 3. Block CEX/Tokenized domains in the website URL
            if any(bad in website.lower() for bad in CEX_DOMAINS):
                continue
                
            # 4. Basic Volume/Liquidity Sanity Check
            liquidity_usd = float(pair.get('liquidity', {}).get('usd', 0) or 0)
            volume_h24 = float(pair.get('volume', {}).get('h24', 0) or 0)
            
            if liquidity_usd < 10_000 or volume_h24 < 10_000:
                continue
                
            # 5. Age Check (DexScreener provides pairCreatedAt in ms)
            pair_created_at_ms = pair.get('pairCreatedAt', 0)
            if not pair_created_at_ms:
                continue
                
            age_days = (datetime.now(timezone.utc).timestamp() * 1000 - pair_created_at_ms) / (1000 * 60 * 60 * 24)
            if not (14 <= age_days <= 90):
                continue
                
            # If it passes all checks, it's a golden lead
            validated_leads.append({
                'name': f"{token_symbol} / {pair.get('quoteToken', {}).get('symbol', 'UNKNOWN')}",
                'network': chain,
                'age_days': int(age_days),
                'liquidity_usd': liquidity_usd,
                'volume_h24': volume_h24,
                'token_id': token_address,
                'website': website,
                'twitter': twitter
            })
            print(f"    [PASS] {token_symbol} | Age: {int(age_days)}d | Liq: ${liquidity_usd:,.0f} | Web: {website}", flush=True)
            
        time.sleep(3) # Polite delay between chains

    print(f"Discovery Complete: {len(validated_leads)} new, high-quality leads found.", flush=True)
    return validated_leads

# --- OUTPUT: Dynamic Sales Dossier ---
def send_telegram_message(lead):
    dm_text = generate_personalized_dm(lead)
    
    twitter_display = lead['twitter'] if lead['twitter'] else 'N/A'
    
    telegram_message = (
        f"🎯 *NEW HIGH-SIGNAL LEAD*\n\n"
        f"📛 *Project:* {lead['name']}\n"
        f"⛓️ *Chain:* {lead['network'].capitalize()}\n"
        f"⏳ *Age:* {lead['age_days']} days\n"
        f"💧 *Liquidity:* ${lead['liquidity_usd']:,.0f}\n"
        f"📊 *24h Volume:* ${lead['volume_h24']:,.0f}\n"
        f"🔗 *Website:* {lead['website']}\n"
        f"🐦 *Twitter:* {twitter_display}\n\n"
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

def main():
    print("=== STARTING ENDLESS FLOW PIPELINE (WEBSITE-FIRST DEXSCREENER) ===", flush=True)
    try:
        processed_ids = load_processed_ids()
        print(f"Loaded {len(processed_ids)} previously processed token addresses.", flush=True)
        
        leads = source_new_pairs(processed_ids)
        if not leads:
            print("Exiting: No new leads passed the website-first filters.", flush=True)
            return
            
        print(f"\n=== DELIVERY & STATE UPDATE ===", flush=True)
        newly_processed_ids = []
        
        for lead in leads:
            send_telegram_message(lead)
            newly_processed_ids.append(lead['token_id'])
            
        processed_ids.update(newly_processed_ids)
        save_processed_ids(processed_ids)
        
        print(f"\n=== PIPELINE FINISHED SUCCESSFULLY ===", flush=True)
        print(f"Sent {len(leads)} leads. Database updated with {len(newly_processed_ids)} new IDs.", flush=True)
        
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}", flush=True)
        raise

if __name__ == "__main__":
    main()
