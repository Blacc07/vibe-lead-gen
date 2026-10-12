import os
import json
import requests
import time
from datetime import datetime, timezone

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "processed_leads.json"

NETWORKS = ['solana', 'base', 'bsc']
ZOMBIE_BLOCKLIST = ["serum", "belt", "qi", "hakka", "wagmi", "olympus", "wonderland", "iron", "safemoon", "squid", "terra", "luna", "celsius"]

def fetch_with_retry(url, max_retries=3):
    """Robust fetch function with exponential backoff for 429 Rate Limits."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }
    
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
            time.sleep(10)
            
    print(f"    [CRITICAL] Failed to fetch {url} after {max_retries} retries.", flush=True)
    return None

# --- SOURCE 1: GeckoTerminal New Pools (Pure On-Chain Data) ---
def source_new_pools():
    print("  [SOURCE] Fetching GeckoTerminal New Pools (On-Chain Metrics)...", flush=True)
    validated_leads = []
    
    for network in NETWORKS:
        print(f"  Fetching new pools for {network}...", flush=True)
        url = f"https://api.geckoterminal.com/api/v2/networks/{network}/new_pools"
        
        try:
            response = requests.get(url, timeout=15)
            
            # Handle Rate Limits Gracefully
            if response.status_code == 429:
                print("    [RATE LIMIT] Pausing 60s before retry...", flush=True)
                time.sleep(60)
                response = requests.get(url, timeout=15)
                
            if response.status_code != 200:
                print(f"    [ERROR] Unexpected status {response.status_code}", flush=True)
                continue
                
            data = response.json()
            pools = data.get('data', [])
            
            for pool in pools:
                attrs = pool.get('attributes', {})
                name = attrs.get('name', '').lower()
                
                # 1. Zombie/Legacy Blocklist Check
                if any(zombie in name for zombie in ZOMBIE_BLOCKLIST):
                    continue
                    
                # 2. Age Check (Must be 14 to 90 days old)
                created_at_str = attrs.get('pool_created_at')
                if not created_at_str:
                    continue
                created_dt = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                age_days = (datetime.now(timezone.utc) - created_dt).days
                if not (14 <= age_days <= 90):
                    continue
                    
                # 3. Liquidity Check (>= $50,000 USD)
                liquidity_usd = float(attrs.get('reserve_in_usd', 0) or 0)
                if liquidity_usd < 50000:
                    continue
                    
                # 4. Volume Check (>= $50,000 USD in 24h)
                volume_dict = attrs.get('volume_usd', {})
                volume_h24 = float(volume_dict.get('h24', 0) or 0)
                if volume_h24 < 50000:
                    continue
                    
                # 5. Validity Check (Must have a base token relationship)
                relationships = pool.get('relationships', {})
                base_token_id = relationships.get('base_token', {}).get('data', {}).get('id', '')
                if not base_token_id:
                    continue
                    
                # If it passes all filters, it's a high-signal lead
                validated_leads.append({
                    'name': attrs.get('name'),
                    'network': network,
                    'age_days': age_days,
                    'liquidity_usd': liquidity_usd,
                    'volume_h24': volume_h24,
                    'token_id': base_token_id # Used for deduplication and enrichment
                })
                print(f"    [PASS] {attrs.get('name')} | Age: {age_days}d | Liq: ${liquidity_usd:,.0f} | Vol: ${volume_h24:,.0f}", flush=True)
                
            time.sleep(3) # Respect API rate limits between networks
            
        except Exception as e:
            print(f"    [ERROR] Exception for {network}: {e}", flush=True)

    print(f"Module Complete: {len(validated_leads)} high-quality, non-zombie leads discovered.", flush=True)
    return validated_leads

# --- ENRICHMENT: Fetch Token Details for Contacts ---
def enrich_leads(leads):
    print("\n=== ENRICHMENT PHASE (Fetching Contacts) ===", flush=True)
    enriched = []
    
    for lead in leads:
        token_address = lead['token_id'].split('_')[-1]
        detail_url = f"https://api.geckoterminal.com/api/v2/networks/{lead['network']}/tokens/{token_address}"
        
        print(f"  Enriching {lead['name']} ({token_address[:8]}...)...", flush=True)
        time.sleep(2) # Rate limit for token endpoint
        
        detail_response = requests.get(detail_url, timeout=10)
        if detail_response.status_code == 200:
            detail_data = detail_response.json()
            token_attrs = detail_data.get('data', {}).get('attributes', {})
            
            website = token_attrs.get('website', '') or token_attrs.get('homepage', '')
            twitter = token_attrs.get('twitter', '') or token_attrs.get('twitter_handle', '')
            telegram = token_attrs.get('telegram', '') or token_attrs.get('telegram_handle', '')
            
            # Format handles
            twitter = f"@{twitter}" if twitter and not twitter.startswith('@') else (twitter or 'Not Found')
            telegram = f"@{telegram}" if telegram and not telegram.startswith('@') else (telegram or 'Not Found')
            
            # Utility Proxy: Must have at least a website or twitter
            if website or (twitter != 'Not Found'):
                lead['website'] = website or 'Not Found'
                lead['twitter'] = twitter
                lead['telegram'] = telegram
                enriched.append(lead)
                print(f"    [ENRICHED] {lead['name']} | Web: {lead['website']}", flush=True)
            else:
                print(f"    [DROP] {lead['name']} (No website or twitter found)", flush=True)
        else:
            print(f"    [DROP] Failed to fetch details for {lead['name']}", flush=True)
            
    return enriched

# --- STATE & OUTPUT ---
def load_processed_ids():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return []

def save_processed_ids(ids):
    with open(STATE_FILE, "w") as f:
        json.dump(ids, f)

def send_telegram_message(lead):
    liq_str = f"${int(lead['liquidity_usd']):,}"
    vol_str = f"${int(lead['volume_h24']):,}"
    
    text = (
        f"🚨 *On-Chain Trigger Lead (Anti-Zombie)*\n\n"
        f"📛 *Project*: {lead['name']}\n"
        f"⛓️ *Network*: {lead['network'].capitalize()}\n"
        f"⏳ *Age*: {lead['age_days']} days\n"
        f"💧 *Liquidity*: {liq_str}\n"
        f"📊 *24h Volume*: {vol_str}\n"
        f"🔗 *Website*: {lead.get('website', 'N/A')}\n\n"
        f"📞 *Contacts*:\n"
        f"• Telegram: {lead.get('telegram', 'Not Found')}\n"
        f"• Twitter: {lead.get('twitter', 'Not Found')}\n\n"
        f"💬 *Vibe Pitch*: \"Instead of dumping treasury, deposit tokens to Vibe's vault to list a perp for free, back OI, and earn trading fees forever.\""
    )
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    
    try:
        requests.post(url, json=payload, timeout=10)
        print(f"  [TELEGRAM] ✅ Sent message for {lead['name']}", flush=True)
    except Exception as e:
        print(f"  [TELEGRAM] ❌ Failed to send: {e}", flush=True)

def main():
    print("=== STARTING DEFINITIVE ON-CHAIN PIPELINE ===", flush=True)
    try:
        # 1. Source pure on-chain data
        leads = source_new_pools()
        if not leads:
            print("Exiting: No leads passed the on-chain filters.", flush=True)
            save_processed_ids(load_processed_ids()) # Prevent git crash
            return
            
        # 2. Enrich with contact data
        enriched_leads = enrich_leads(leads)
        
        # 3. Deduplicate by Immutable Token ID
        processed_ids = load_processed_ids()
        print(f"\n=== DEDUPLICATION ===", flush=True)
        print(f"Processed Token IDs in memory: {len(processed_ids)}", flush=True)
        
        new_leads = [lead for lead in enriched_leads if lead['token_id'] not in processed_ids]
        print(f"New leads to process: {len(new_leads)}", flush=True)
        
        # 4. Deliver
        for lead in new_leads:
            send_telegram_message(lead)
            processed_ids.append(lead['token_id'])
            
        save_processed_ids(processed_ids)
        print("\n=== PIPELINE FINISHED SUCCESSFULLY ===", flush=True)
        
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}", flush=True)
        save_processed_ids(load_processed_ids()) # Prevent git crash on failure
        raise

if __name__ == "__main__":
    main()
