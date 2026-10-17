import os
import requests
import json
import time
import random

# 1. STRICT BD CRITERIA & BLOCKLISTS
TARGET_CHAINS_CG = ['solana', 'base', 'binance-smart-chain']
TARGET_CHAINS_DS = ['solana', 'base', 'bsc'] # DexScreener uses 'bsc'

CATEGORIES = ['decentralized-exchange', 'yield-farming', 'real-world-assets', 'gaming']

ZOMBIE_BLOCKLIST = [
    "coinbase", "robinhood", "binance", "kraken", "okx", "bybit", 
    "serum", "belt", "qi", "hakka", "wagmi", "olympus", "wonderland", 
    "iron", "safemoon", "squid", "terra", "tokenized", "wrapped"
]

def fetch_coingecko_candidates():
    print("=== MODULE 1: COINGECKO DISCOVERY ===")
    candidates = []
    
    for category in CATEGORIES:
        print(f"  Fetching category: {category}...")
        url = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&category={category}&order=market_cap_desc&per_page=100&page=1&sparkline=false"
        
        try:
            resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
            if resp.status_code != 200:
                print(f"    [ERROR] Status {resp.status_code}")
                time.sleep(5)
                continue
                
            coins = resp.json()
            for coin in coins:
                name = coin.get('name', '')
                if any(z in name.lower() for z in ZOMBIE_BLOCKLIST):
                    continue
                    
                mcap = coin.get('market_cap', 0) or 0
                volume = coin.get('total_volume', 0) or 0
                
                # Filter: $500k - $15M MCAP, >$20k Volume
                if 500_000 <= mcap <= 15_000_000 and volume >= 20_000:
                    candidates.append({
                        'id': coin['id'],
                        'name': name,
                        'symbol': coin.get('symbol', '').upper()
                    })
                    
            time.sleep(4) # Respect CoinGecko rate limits
        except Exception as e:
            print(f"    [EXCEPTION] {e}")
            
    print(f"    Found {len(candidates)} baseline candidates.")
    return candidates

def enrich_and_validate(candidates, processed_ids):
    print("\n=== MODULE 2: ENRICHMENT & DEXSCREENER VALIDATION ===")
    verified_leads = []
    
    random.shuffle(candidates)
    
    for coin in candidates:
        if len(verified_leads) >= 5:
            print("  [QUOTA MET] Found 5 fully verified leads.")
            break
            
        if coin['id'] in processed_ids:
            continue
            
        print(f"  Processing {coin['name']}...")
        
        # STEP A: Fetch detailed metadata from CoinGecko
        try:
            detail_url = f"https://api.coingecko.com/api/v3/coins/{coin['id']}"
            resp = requests.get(detail_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
            time.sleep(4) # Rate limit respect
            
            if resp.status_code != 200:
                continue
                
            data = resp.json()
            links = data.get('links', {})
            platforms = data.get('platforms', {})
            
            # 1. Extract Website
            homepages = links.get('homepage', [])
            website = next((url for url in homepages if url and str(url).startswith('http')), '')
            if not website:
                print(f"    [DROP] No website found.")
                continue
                
            # 2. Extract Twitter
            twitter = links.get('twitter_screen_name', '')
            
            # 3. Find Contract Address for our target chains
            target_contract = None
            for chain in TARGET_CHAINS_CG:
                if chain in platforms and platforms[chain]:
                    target_contract = platforms[chain]
                    break
                    
            if not target_contract:
                print(f"    [DROP] No contract address found for target chains.")
                continue
                
        except Exception as e:
            print(f"    [ERROR] CoinGecko detail fetch failed: {e}")
            continue

        # STEP B: Validate On-Chain Life via DexScreener
        try:
            ds_url = f"https://api.dexscreener.com/latest/dex/tokens/{target_contract}"
            ds_resp = requests.get(ds_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
            time.sleep(1) # Polite delay
            
            if ds_resp.status_code == 200:
                ds_data = ds_resp.json()
                pairs = ds_data.get('pairs', [])
                
                is_alive = False
                for pair in pairs:
                    chain_id = pair.get('chainId', '')
                    if chain_id not in TARGET_CHAINS_DS:
                        continue
                        
                    liq = float(pair.get('liquidity', {}).get('usd', 0) or 0)
                    vol = float(pair.get('volume', {}).get('h24', 0) or 0)
                    
                    # The Ultimate "Alive" Check
                    if liq >= 50_000 and vol >= 20_000:
                        is_alive = True
                        break
                        
                if is_alive:
                    print(f"    [PASS] Verified alive on DexScreener (Liq > $50k, Vol > $20k).")
                    verified_leads.append({
                        'name': coin['name'],
                        'symbol': coin['symbol'],
                        'website': website,
                        'twitter': f"@{twitter}" if twitter else "Not Found",
                        'contract': target_contract,
                        'gecko_id': coin['id']
                    })
                else:
                    print(f"    [DROP] DexScreener shows dead/low liquidity on target chains.")
            else:
                print(f"    [DROP] DexScreener returned {ds_resp.status_code}.")
                
        except Exception as e:
            print(f"    [ERROR] DexScreener validation failed: {e}")

    print(f"    Validation Complete: {len(verified_leads)} leads verified.")
    return verified_leads

def generate_personalized_dm(lead):
    name = lead['name']
    twitter = lead.get('twitter', '')
    
    if twitter and twitter != "Not Found":
        greeting = f"Hey {twitter}, been tracking the momentum behind {name}."
    else:
        greeting = f"Hey {name} team, been tracking the momentum behind the project."

    template = (
        f"{greeting}\n\n"
        f"You guys are seeing real on-chain traction right now. Quick question: are you currently capturing any derivative volume for your token, or is that liquidity leaking to CEXs?\n\n"
        f"We built Vibe Trading to help protocols like yours capture that value:\n"
        f"• **The Solution:** List a native perp on Vibe for $0 USDC. Deposit idle treasury tokens to our vault to back the Open Interest.\n"
        f"• **The Result:** You earn perpetual trading fees to fund operations without selling a single token on the spot market.\n\n"
        f"Mind if I send over a quick 1-pager on how the vault mechanics work?"
    )
    return template

def deliver_leads(leads, processed_ids):
    print("\n=== MODULE 3: DELIVERY & STATE UPDATE ===")
    
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID")
    
    for lead in leads:
        dm_text = generate_personalized_dm(lead)
        
        telegram_message = (
            f"🎯 *NEW VERIFIED MOMENTUM LEAD*\n\n"
            f"📛 **Project:** {lead['name']} ({lead['symbol']})\n"
            f"🔗 **Website:** {lead['website']}\n"
            f"🐦 **Twitter:** {lead['twitter']}\n"
            f"📜 **Contract:** `{lead['contract']}`\n\n"
            f"📝 *READY-TO-SEND DM:*\n"
            f"```\n{dm_text}\n```"
        )
        
        if tg_token and tg_chat:
            tg_url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
            payload = {"chat_id": tg_chat, "text": telegram_message, "parse_mode": "Markdown"}
            try:
                resp = requests.post(tg_url, json=payload, timeout=10)
                if resp.status_code == 200:
                    print(f"✅ Sent outreach for {lead['name']} to Telegram.")
                else:
                    print(f"❌ Failed to send for {lead['name']}: {resp.text}")
            except Exception as e:
                print(f"❌ Network error sending for {lead['name']}: {e}")
        else:
            print(f"⚠️ Telegram credentials missing. Console output for {lead['name']}:\n{telegram_message}")
        
        processed_ids.add(lead['gecko_id'])

    with open('processed_leads.json', 'w') as f:
        json.dump(list(processed_ids), f, indent=2)
        
    print(f"\nPipeline Finished. {len(leads)} new leads processed and saved.")

# === MAIN EXECUTION ===
if __name__ == "__main__":
    print("=== STARTING ACTIVE MOMENTUM PIPELINE ===")
    
    # FIXED: Catch both FileNotFoundError AND json.JSONDecodeError (for empty files)
    try:
        with open('processed_leads.json', 'r') as f:
            processed_ids = set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        processed_ids = set()

    candidates = fetch_coingecko_candidates()
    verified_leads = enrich_and_validate(candidates, processed_ids)
    deliver_leads(verified_leads, processed_ids)
    
    print("=== PIPELINE COMPLETE ===")
