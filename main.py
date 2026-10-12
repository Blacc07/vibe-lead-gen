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

# Quota System: Stop searching once we find this many fresh, high-quality leads
TARGET_NEW_LEADS_PER_RUN = 4

def load_processed_ids():
    """Loads processed IDs as a set for O(1) deduplication lookups."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return set(json.load(f))
        except (json.JSONDecodeError, FileNotFoundError):
            return set()
    return set()

def save_processed_ids(ids_set):
    """Saves the set of IDs back to JSON as a list."""
    with open(STATE_FILE, "w") as f:
        json.dump(list(ids_set), f, indent=2)

def fetch_with_retry(url, max_retries=3):
    """Robust fetch function with exponential backoff for 429 Rate Limits and Timeouts."""
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
            # Catches ReadTimeout, ConnectionError, etc.
            print(f"    [NETWORK ERROR] {e}. Retrying...", flush=True)
            time.sleep(5)
            
    print(f"    [CRITICAL] Failed to fetch {url} after {max_retries} retries.", flush=True)
    return None

# --- SOURCE: Paginated Discovery with Early Exit ---
def source_active_pools(processed_ids):
    print("  [SOURCE] Fetching GeckoTerminal Pools (Paginated Quota System)...", flush=True)
    validated_leads = []
    
    for network in NETWORKS:
        if len(validated_leads) >= TARGET_NEW_LEADS_PER_RUN:
            print(f"    [QUOTA MET] Found {TARGET_NEW_LEADS_PER_RUN} new leads. Stopping discovery.", flush=True)
            break
            
        print(f"  Fetching pools for {network}...", flush=True)
        
        for page in range(1, 6):
            if len(validated_leads) >= TARGET_NEW_LEADS_PER_RUN:
                break
                
            url = f"https://api.geckoterminal.com/api/v2/networks/{network}/pools?sort=h24_volume_usd_desc&page={page}"
            
            try:
                response = requests.get(url, timeout=15)
                if response.status_code == 429:
                    print("    [RATE LIMIT] Pausing 60s before retry...", flush=True)
                    time.sleep(60)
                    response = requests.get(url, timeout=15)
                    
                if response.status_code != 200:
                    print(f"    [ERROR] Unexpected status {response.status_code}. Moving to next network.", flush=True)
                    break
                    
                data = response.json()
                pools = data.get('data', [])
                
                if not pools:
                    print(f"    [INFO] No more pools on page {page} for {network}.", flush=True)
                    break
                    
                for pool in pools:
                    if len(validated_leads) >= TARGET_NEW_LEADS_PER_RUN:
                        break
                        
                    attrs = pool.get('attributes', {})
                    name = attrs.get('name', '').lower()
                    
                    if any(zombie in name for zombie in ZOMBIE_BLOCKLIST):
                        continue
                        
                    created_at_str = attrs.get('pool_created_at')
                    if not created_at_str:
                        continue
                    created_dt = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                    age_days = (datetime.now(timezone.utc) - created_dt).days
                    if not (14 <= age_days <= 90):
                        continue
                        
                    liquidity_usd = float(attrs.get('reserve_in_usd', 0) or 0)
                    volume_data = attrs.get('volume_usd', {})
                    volume_h24 = float(volume_data.get('h24', 0) or 0) if isinstance(volume_data, dict) else float(volume_data or 0)
                    
                    if liquidity_usd < 50_000 or volume_h24 < 50_000:
                        continue
                        
                    relationships = pool.get('relationships', {})
                    base_token_id = relationships.get('base_token', {}).get('data', {}).get('id', '')
                    if not base_token_id or base_token_id in processed_ids:
                        continue
                        
                    validated_leads.append({
                        'name': attrs.get('name'),
                        'network': network,
                        'age_days': age_days,
                        'liquidity_usd': liquidity_usd,
                        'volume_h24': volume_h24,
                        'token_id': base_token_id
                    })
                    print(f"    [PASS] {attrs.get('name')} | Age: {age_days}d | Liq: ${liquidity_usd:,.0f} | Vol: ${volume_h24:,.0f}", flush=True)
                    
                time.sleep(2)
                
            except Exception as e:
                print(f"    [ERROR] Exception for {network} page {page}: {e}", flush=True)
                continue

    print(f"Discovery Complete: {len(validated_leads)} new, high-quality leads found.", flush=True)
    return validated_leads

# --- ENRICHMENT: Fallback Strategy (GeckoTerminal -> CoinGecko) ---
def enrich_leads(leads):
    print("\n=== ENRICHMENT PHASE (Fallback Strategy) ===", flush=True)
    enriched = []
    
    for lead in leads:
        token_address = lead['token_id'].split('_')[-1]
        token_name = lead['name'].split(' / ')[0].strip()
        network = lead['network']
        
        print(f"  Enriching {lead['name']} ({token_address[:8]}...)...", flush=True)
        
        website = ''
        twitter = ''
        telegram = ''
        
        # STEP 1: Try GeckoTerminal Token Endpoint (Using robust fetch_with_retry)
        gt_url = f"https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{token_address}"
        gt_data = fetch_with_retry(gt_url)
        
        if gt_data:
            token_attrs = gt_data.get('data', {}).get('attributes', {})
            website = token_attrs.get('website', '') or token_attrs.get('homepage', '')
            twitter = token_attrs.get('twitter', '') or token_attrs.get('twitter_handle', '')
            telegram = token_attrs.get('telegram', '') or token_attrs.get('telegram_handle', '')
            
        # STEP 2: Fallback to CoinGecko Search (Using robust fetch_with_retry)
        if not website and not twitter:
            print(f"    [INFO] GeckoTerminal missing links. Trying CoinGecko search for '{token_name}'...", flush=True)
            cg_url = f"https://api.coingecko.com/api/v3/search?query={token_name}"
            cg_data = fetch_with_retry(cg_url)
            
            if cg_data:
                coins = cg_data.get('coins', [])
                
                for coin in coins:
                    if coin.get('name', '').lower() == token_name.lower() or coin.get('symbol', '').lower() == token_name.lower():
                        detail_url = f"https://api.coingecko.com/api/v3/coins/{coin.get('id')}"
                        detail_data = fetch_with_retry(detail_url)
                        
                        if detail_data:
                            links = detail_data.get('links', {})
                            homepages = links.get('homepage', [])
                            website = next((url for url in homepages if url and str(url).strip()), '')
                            twitter = links.get('twitter_screen_name', '')
                            telegram = links.get('telegram_channel_identifier', '')
                            
                            if website or twitter:
                                print(f"    [SUCCESS] Found links via CoinGecko: Web={website}, TW={twitter}", flush=True)
                                break
                        time.sleep(2) # Respect CoinGecko rate limits
                        
        # STEP 3: Final Validation
        if website or twitter:
            lead['website'] = website or 'Not Found'
            lead['twitter'] = f"@{twitter}" if twitter and not twitter.startswith('@') else (twitter or 'Not Found')
            lead['telegram'] = f"@{telegram}" if telegram and not telegram.startswith('@') else (telegram or 'Not Found')
            enriched.append(lead)
            print(f"    [ENRICHED] {lead['name']}", flush=True)
        else:
            print(f"    [DROP] {lead['name']} (No verifiable website or twitter found in any source)", flush=True)
            
        time.sleep(1) # Polite delay between enrichments

    print(f"Enrichment Complete: {len(enriched)} leads ready for outreach.", flush=True)
    return enriched

# --- OUTPUT ---
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
    print("=== STARTING ENDLESS FLOW ON-CHAIN PIPELINE ===", flush=True)
    try:
        processed_ids = load_processed_ids()
        print(f"Loaded {len(processed_ids)} previously processed token IDs.", flush=True)
        
        leads = source_active_pools(processed_ids)
        if not leads:
            print("Exiting: No new leads passed the on-chain filters.", flush=True)
            return
            
        enriched_leads = enrich_leads(leads)
        
        print(f"\n=== DELIVERY & STATE UPDATE ===", flush=True)
        newly_processed_ids = []
        
        for lead in enriched_leads:
            send_telegram_message(lead)
            newly_processed_ids.append(lead['token_id'])
            
        processed_ids.update(newly_processed_ids)
        save_processed_ids(processed_ids)
        
        print(f"\n=== PIPELINE FINISHED SUCCESSFULLY ===", flush=True)
        print(f"Sent {len(enriched_leads)} leads. Database updated with {len(newly_processed_ids)} new IDs.", flush=True)
        
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}", flush=True)
        raise

if __name__ == "__main__":
    main()
