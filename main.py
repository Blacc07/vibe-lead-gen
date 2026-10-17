import os
import json
import requests
import time
from datetime import datetime, timezone, timedelta

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "processed_leads.json"

# 1. Define strict BD criteria
TARGET_CHAINS_CG = ['solana', 'base', 'binance-smart-chain'] 
BLOCKED_CHAINS_CG = ['ethereum', 'optimistic-ethereum', 'arbitrum-one', 'polygon-pos', 'avalanche']

TARGET_CHAINS_LL = ['Solana', 'Base', 'Binance'] 
BLOCKED_CHAINS_LL = ['Ethereum', 'Arbitrum', 'Optimism', 'Polygon', 'Avalanche']

TARGET_CATEGORIES_LL = [
    'Dexes', 'Yield', 'RWA', 'Gaming', 'Infrastructure', 'Services', 
    'Liquid Staking', 'Derivatives', 'CDP', 'Lending'
]

ZOMBIE_BLOCKLIST = [
    "coinbase", "robinhood", "binance", "kraken", "okx", "bybit", 
    "serum", "belt", "qi", "hakka", "wagmi", "olympus", "wonderland", 
    "iron", "safemoon", "squid", "terra", "tokenized", "wrapped"
]

TARGET_QUOTA = 5

# --- MODULE 1: STRICT DATA INTERSECTION ---
def fetch_and_intersect_datasets():
    print("=== MODULE 1: STRICT DATA INTERSECTION ===")
    
    # STEP A: CoinGecko Master List (Strict Chain Verification)
    print("  [1/2] Fetching CoinGecko Master List...")
    try:
        cg_resp = requests.get("https://api.coingecko.com/api/v3/coins/list?include_platform=true", timeout=30)
        cg_resp.raise_for_status()
        cg_data = cg_resp.json()
        
        valid_cg_ids = set()
        for coin in cg_data:
            platforms = coin.get('platforms', {})
            platform_keys = [k.lower() for k in platforms.keys()]
            
            has_target = any(chain in platform_keys for chain in TARGET_CHAINS_CG)
            has_blocked = any(chain in platform_keys for chain in BLOCKED_CHAINS_CG)
            
            if has_target and not has_blocked:
                valid_cg_ids.add(coin['id'])
                
        print(f"    Found {len(valid_cg_ids)} coins strictly on target chains (Ethereum excluded).")
    except Exception as e:
        print(f"    [ERROR] CoinGecko fetch failed: {e}")
        return []

    # STEP B: DefiLlama Master List (Strict Chain & Metadata Verification)
    print("  [2/2] Fetching DefiLlama Protocol Directory...")
    try:
        ll_resp = requests.get("https://api.llama.fi/protocols", timeout=30)
        ll_resp.raise_for_status()
        ll_data = ll_resp.json()
    except Exception as e:
        print(f"    [ERROR] DefiLlama fetch failed: {e}")
        return []

    # STEP C: Intersect and Filter
    validated_leads = []
    for proto in ll_data:
        gecko_id = proto.get('gecko_id')
        if not gecko_id or gecko_id not in valid_cg_ids:
            continue
            
        name = proto.get('name', '')
        name_lower = name.lower()
        
        if any(blocked in name_lower for blocked in ZOMBIE_BLOCKLIST):
            continue
            
        category = proto.get('category', '')
        if category not in TARGET_CATEGORIES_LL:
            continue
            
        chains = proto.get('chains', [])
        has_target_ll = any(chain in TARGET_CHAINS_LL for chain in chains)
        has_blocked_ll = any(chain in BLOCKED_CHAINS_LL for chain in chains)
        
        if not has_target_ll or has_blocked_ll:
            continue
            
        website = proto.get('url', '')
        twitter = proto.get('twitter', '')
        if not website or not str(website).startswith('http') or not twitter:
            continue
            
        mcap = proto.get('mcap')
        tvl = proto.get('tvl', 0)
        is_valid_size = False
        
        if mcap and 500_000 <= float(mcap) <= 15_000_000:
            is_valid_size = True
        elif tvl and 250_000 <= float(tvl) <= 20_000_000:
            is_valid_size = True
            
        if not is_valid_size:
            continue
            
        validated_leads.append({
            'name': name,
            'category': category,
            'chains': chains, # Added for Telegram visibility
            'website': website,
            'twitter': f"@{twitter}",
            'gecko_id': gecko_id,
            'github_repos': proto.get('github', []) 
        })

    print(f"    Intersection Complete: {len(validated_leads)} strictly filtered baseline leads found.")
    return validated_leads

# --- MODULE 2: SOFT GITHUB VALIDATION ---
def validate_active_builders(leads):
    print("\n=== MODULE 2: GITHUB ACTIVE BUILDER VALIDATION ===")
    active_leads = []
    
    # Calculate the date 60 days ago (Python 3.12+ compatible)
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=60)).strftime('%Y-%m-%d')
    
    for lead in leads:
        if len(active_leads) >= TARGET_QUOTA:
            print(f"  [QUOTA MET] Found {TARGET_QUOTA} verified leads. Stopping GitHub checks.")
            break
            
        print(f"  Checking GitHub activity for {lead['name']}...")
        
        github_repos = lead.get('github_repos', [])
        
        if not github_repos:
            # SOFT FILTER: If no official repo is listed, do NOT drop.
            print(f"    [WARNING] No official GitHub repository listed in DefiLlama. Proceeding with caution.")
            lead['github_repo'] = "Unverified (Not listed in DefiLlama)"
            lead['last_push'] = "N/A"
            active_leads.append(lead)
            continue
            
        # We only need to check the first official repo listed
        official_repo_url = github_repos[0]
        
        # Extract owner and repo name from the URL
        try:
            url_parts = official_repo_url.rstrip('/').split('/')
            owner = url_parts[-2]
            repo = url_parts[-1]
        except IndexError:
            print(f"    [WARNING] Invalid GitHub URL format: {official_repo_url}. Proceeding.")
            lead['github_repo'] = "Unverified (Invalid URL format)"
            lead['last_push'] = "N/A"
            active_leads.append(lead)
            continue
            
        # Query the specific, official repository
        api_url = f"https://api.github.com/repos/{owner}/{repo}"
        
        try:
            time.sleep(6) # Respect GitHub's unauthenticated rate limit (10 req/min)
            
            resp = requests.get(api_url, headers={'Accept': 'application/vnd.github.v3+json', 'User-Agent': 'Vibe-BD-Pipeline'}, timeout=15)
            
            if resp.status_code == 200:
                repo_data = resp.json()
                last_push = repo_data.get('pushed_at', '')
                
                # Check if the last push is newer than our cutoff date
                if last_push and last_push[:10] >= cutoff_date:
                    print(f"    [PASS] Official repo active: {official_repo_url} (Last push: {last_push[:10]})")
                    lead['github_repo'] = official_repo_url
                    lead['last_push'] = last_push[:10]
                    active_leads.append(lead)
                else:
                    print(f"    [DROP] Official repo exists, but inactive since {last_push[:10]} (Zombie project).")
            elif resp.status_code == 404:
                print(f"    [WARNING] Official GitHub repo returned 404. Proceeding with caution.")
                lead['github_repo'] = "Unverified (Repo not found)"
                lead['last_push'] = "N/A"
                active_leads.append(lead)
            else:
                print(f"    [WARNING] GitHub API returned {resp.status_code}. Proceeding with caution.")
                lead['github_repo'] = f"Unverified (API Error: {resp.status_code})"
                lead['last_push'] = "N/A"
                active_leads.append(lead)
                
        except Exception as e:
            print(f"    [WARNING] GitHub check failed for {lead['name']}: {e}. Proceeding with caution.")
            lead['github_repo'] = "Unverified (Check failed)"
            lead['last_push'] = "N/A"
            active_leads.append(lead)

    print(f"\n    Validation Complete: {len(active_leads)} leads verified or flagged for manual review.")
    return active_leads

# --- MODULE 3: DELIVERY & STATE UPDATE ---
def generate_personalized_dm(lead):
    name = lead['name']
    category = lead['category']
    
    template = (
        f"Hey team, huge fan of what you're building with {name} in the {category} space.\n\n"
        f"Saw the recent GitHub activity—looks like you guys are shipping. Quick question: are you currently capturing any derivative volume for your token, or is that liquidity leaking to CEXs?\n\n"
        f"We built Vibe Trading to help protocols like yours capture that value:\n"
        f"• **The Solution:** List a native perp on Vibe for $0 USDC. Deposit idle treasury tokens to our vault to back the Open Interest.\n"
        f"• **The Result:** You earn perpetual trading fees to fund operations without selling a single token on the spot market.\n\n"
        f"Mind if I send over a quick 1-pager on how the vault mechanics work?"
    )
    return template

def deliver_leads(leads):
    print("\n=== MODULE 3: DELIVERY & STATE UPDATE ===")
    
    try:
        with open(STATE_FILE, 'r') as f:
            processed_ids = set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        processed_ids = set()

    newly_processed = []
    
    for lead in leads:
        if lead['gecko_id'] in processed_ids:
            continue
            
        dm_text = generate_personalized_dm(lead)
        chains_str = ", ".join(lead.get('chains', ['Unknown']))
        
        telegram_message = (
            f"🎯 *NEW VERIFIED LEAD*\n\n"
            f"📛 **Project:** {lead['name']}\n"
            f"⛓️ **Chains:** {chains_str}\n"
            f"📂 **Category:** {lead['category']}\n"
            f"🔗 **Website:** {lead['website']}\n"
            f"🐦 **Twitter:** {lead['twitter']}\n"
            f"💻 **GitHub:** {lead['github_repo']}\n"
            f"📅 **Last Commit:** {lead['last_push']}\n\n"
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
            
        newly_processed.append(lead['gecko_id'])

    processed_ids.update(newly_processed)
    with open(STATE_FILE, 'w') as f:
        json.dump(list(processed_ids), f, indent=2)
        
    print(f"\nPipeline Finished. {len(newly_processed)} new leads processed and saved.")

# === MAIN EXECUTION ===
if __name__ == "__main__":
    print("=== STARTING STRICT CHAIN ACTIVE BUILDER PIPELINE (SOFT GITHUB) ===")
    baseline_leads = fetch_and_intersect_datasets()
    if baseline_leads:
        active_leads = validate_active_builders(baseline_leads)
        if active_leads:
            deliver_leads(active_leads)
    print("=== PIPELINE COMPLETE ===")
