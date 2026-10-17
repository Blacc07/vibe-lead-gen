import os
import json
import random
import requests
import time
from datetime import datetime, timezone

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "processed_leads.json"

TARGET_QUOTA = 5
TARGET_CHAINS_DS = ['solana', 'base', 'bsc']
TARGET_CHAINS_CG = ['solana', 'base', 'binance-smart-chain']

# --- MODULE 1: DEXSCREENER MOMENTUM FETCHER ---
def fetch_dexscreener_momentum():
    print("  [SOURCE 1] Fetching DexScreener Momentum...")
    leads = []
    
    for chain in TARGET_CHAINS_DS:
        try:
            url = f"https://api.dexscreener.com/latest/dex/pairs/{chain}"
            resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}, timeout=15)
            
            if resp.status_code == 200:
                data = resp.json()
                pairs = data.get('pairs', [])
                
                for pair in pairs:
                    # 1. Age Check (pairCreatedAt is in milliseconds)
                    created_at_ms = pair.get('pairCreatedAt', 0)
                    if not created_at_ms:
                        continue
                    age_days = (datetime.now(timezone.utc).timestamp() * 1000 - created_at_ms) / (1000 * 60 * 60 * 24)
                    if not (14 <= age_days <= 180):
                        continue
                        
                    # 2. Liquidity & Volume Checks
                    liquidity_usd = float(pair.get('liquidity', {}).get('usd', 0) or 0)
                    volume_h24 = float(pair.get('volume', {}).get('h24', 0) or 0)
                    
                    if liquidity_usd < 50_000 or volume_h24 < 20_000:
                        continue
                        
                    # 3. Mandatory Website Check (The "Alive" Proxy)
                    info = pair.get('info', {})
                    websites = info.get('websites', [])
                    website = websites[0].get('url', '') if websites else ''
                    
                    socials = info.get('socials', [])
                    twitter = next((s.get('url', '') for s in socials if s.get('type') == 'twitter'), '')
                    
                    if not website or not str(website).startswith('http'):
                        continue
                        
                    leads.append({
                        'name': pair.get('baseToken', {}).get('symbol', 'Unknown'),
                        'website': website,
                        'twitter': twitter,
                        'source': 'DexScreener Momentum',
                        'chain': chain.capitalize(),
                        'liquidity_usd': liquidity_usd,
                        'volume_h24': volume_h24,
                        'unique_id': website.lower() # Dedupe by website
                    })
            time.sleep(2) # Rate limit respect
        except Exception as e:
            print(f"    [ERROR] DexScreener {chain} fetch failed: {e}")
            
    print(f"    Found {len(leads)} momentum leads.")
    return leads

# --- MODULE 2: COINGECKO RECENTLY ADDED (LAUNCHPAD PROXY) ---
def fetch_recently_added():
    print("  [SOURCE 2] Fetching CoinGecko Recently Added (Launchpad Proxy)...")
    leads = []
    
    try:
        # No auth required, strictly free
        url = "https://api.coingecko.com/api/v3/coins/recently_added"
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}, timeout=15)
        
        if resp.status_code == 200:
            data = resp.json()
            
            for coin in data[:100]: # Check top 100 most recent
                platforms = coin.get('platforms', {})
                platform_keys = [k.lower() for k in platforms.keys()]
                
                # Must be on our target chains
                if not any(chain in platform_keys for chain in TARGET_CHAINS_CG):
                    continue
                    
                name = coin.get('name', 'Unknown')
                
                leads.append({
                    'name': name,
                    'website': '', # Will be populated in enrichment
                    'twitter': '',
                    'source': 'CoinGecko Recently Added',
                    'chain': ', '.join([p for p in platform_keys if p in TARGET_CHAINS_CG]).replace('binance-smart-chain', 'BSC').title(),
                    'liquidity_usd': 0,
                    'volume_h24': float(coin.get('total_volume', 0) or 0),
                    'unique_id': coin.get('id'), # Dedupe by CoinGecko ID
                    'gecko_id': coin.get('id')
                })
    except Exception as e:
        print(f"    [ERROR] CoinGecko recently added fetch failed: {e}")
        
    print(f"    Found {len(leads)} recently added leads.")
    return leads

# --- MODULE 3: ENRICHMENT & DELIVERY ---
def enrich_and_deliver_leads(ds_leads, cg_leads, processed_ids):
    print("\n=== MODULE 3: ENRICHMENT & DELIVERY ===")
    
    # Merge and shuffle to ensure random selection
    all_leads = ds_leads + cg_leads
    random.shuffle(all_leads)
    
    active_leads = []
    for lead in all_leads:
        if len(active_leads) >= TARGET_QUOTA:
            print(f"  [QUOTA MET] Found {TARGET_QUOTA} NEW leads for this run.")
            break
            
        unique_id = lead.get('unique_id')
        if unique_id in processed_ids:
            continue # Skip already processed
            
        # Enrichment for CoinGecko leads (fetch website/twitter)
        if lead['source'] == 'CoinGecko Recently Added' and not lead['website']:
            try:
                time.sleep(3) # Respect CoinGecko rate limits
                detail_url = f"https://api.coingecko.com/api/v3/coins/{lead['gecko_id']}"
                detail_resp = requests.get(detail_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
                if detail_resp.status_code == 200:
                    detail_data = detail_resp.json()
                    links = detail_data.get('links', {})
                    homepages = links.get('homepage', [])
                    lead['website'] = next((url for url in homepages if url and str(url).strip()), '')
                    lead['twitter'] = links.get('twitter_screen_name', '')
            except Exception:
                pass
                
        # Final filter: Must have a website to be considered "alive"
        if not lead['website'] or not str(lead['website']).startswith('http'):
            continue
            
        active_leads.append(lead)
        processed_ids.add(unique_id)

    print(f"    Delivery Queue: {len(active_leads)} new leads ready.")
    
    for lead in active_leads:
        # Dynamic greeting
        twitter_handle = lead.get('twitter', '')
        if twitter_handle and twitter_handle != "Not Found":
            # Clean up twitter handle if it's a full URL
            import re
            match = re.search(r'(?:twitter\.com|x\.com)/([a-zA-Z0-9_]{1,15})', twitter_handle)
            clean_tw = f"@{match.group(1)}" if match else (f"@{twitter_handle}" if not twitter_handle.startswith('@') else twitter_handle)
            greeting = f"Hey {clean_tw}, huge fan of the momentum behind {lead['name']} on {lead['chain']}."
        else:
            greeting = f"Hey {lead['name']} team, huge fan of the momentum behind your project on {lead['chain']}."

        dm_text = (
            f"{greeting}\n\n"
            f"Quick question: are you currently capturing any derivative volume for your token, or is that liquidity leaking to external platforms?\n\n"
            f"We built Vibe Trading to help protocols like yours capture that value:\n"
            f"• **The Solution:** List a native perp on Vibe for $0 USDC. Deposit idle treasury tokens to our vault to back the Open Interest.\n"
            f"• **The Result:** You earn perpetual trading fees to fund operations without selling a single token on the spot market.\n\n"
            f"Mind if I send over a quick 1-pager on how the vault mechanics work?"
        )
        
        liq_str = f"${int(lead['liquidity_usd']):,}" if lead['liquidity_usd'] > 0 else "N/A"
        vol_str = f"${int(lead['volume_h24']):,}" if lead['volume_h24'] > 0 else "N/A"
        tw_display = lead['twitter'] if lead['twitter'] else "Not Found"
        if tw_display != "Not Found" and not tw_display.startswith('@'):
            match = re.search(r'(?:twitter\.com|x\.com)/([a-zA-Z0-9_]{1,15})', tw_display)
            if match: tw_display = f"@{match.group(1)}"
        
        telegram_message = (
            f"🎯 *NEW MOMENTUM LEAD ({lead['source']})*\n\n"
            f"📛 **Project:** {lead['name']}\n"
            f"⛓️ **Chain:** {lead['chain']}\n"
            f"💧 **Liquidity:** {liq_str}\n"
            f"📊 **24h Vol:** {vol_str}\n"
            f"🔗 **Website:** {lead['website']}\n"
            f"🐦 **Twitter:** {tw_display}\n\n"
            f"📝 *READY-TO-SEND DM:*\n"
            f"```\n{dm_text}\n```"
        )
        
        tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": telegram_message, "parse_mode": "Markdown"}
        
        try:
            tg_resp = requests.post(tg_url, json=payload, timeout=10)
            if tg_resp.status_code == 200:
                print(f"  [TELEGRAM] ✅ Sent outreach for {lead['name']}")
            else:
                print(f"  [TELEGRAM] ❌ Failed to send for {lead['name']}: {tg_resp.text}")
        except Exception as e:
            print(f"  [TELEGRAM] ❌ Network error sending for {lead['name']}: {e}")

    # Save updated state
    with open(STATE_FILE, 'w') as f:
        json.dump(list(processed_ids), f, indent=2)
        
    print(f"\nPipeline Finished. {len(active_leads)} new leads processed and saved.")

# === MAIN EXECUTION ===
if __name__ == "__main__":
    print("=== STARTING DUAL-SOURCE MOMENTUM PIPELINE ===")
    
    # 1. Load processed IDs
    try:
        with open(STATE_FILE, 'r') as f:
            processed_ids = set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        processed_ids = set()

    # 2. Fetch from both sources
    ds_leads = fetch_dexscreener_momentum()
    cg_leads = fetch_recently_added()
    
    # 3. Process and Deliver
    if ds_leads or cg_leads:
        enrich_and_deliver_leads(ds_leads, cg_leads, processed_ids)
    else:
        print("No leads found from either source.")
        
    print("=== PIPELINE COMPLETE ===")
