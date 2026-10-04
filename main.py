import os
import json
import requests
import re

# --- CONFIGURATION ---
DUNE_API_KEY = os.environ.get("DUNE_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
DUNE_SOLANA_QUERY_ID = os.environ.get("DUNE_SOLANA_QUERY_ID") # Kept for backup, but primary is now DefiLlama
DUNE_EVM_QUERY_ID = os.environ.get("DUNE_EVM_QUERY_ID")

STATE_FILE = "processed_leads.json"

# Blocklist of established giants, CEXs, and infrastructure that don't need this pitch
BLOCKLIST = [
    "pinksale", "pancakeswap", "uniswap", "sushiswap", "raydium", 
    "jupiter", "curve", "aave", "compound", "maker", "lido", 
    "gmx", "dydx", "synthetix", "binance", "coinbase", "okx"
]

def fetch_defillama():
    print("Fetching DefiLlama protocols...")
    url = "https://api.llama.fi/protocols"
    response = requests.get(url)
    response.raise_for_status()
    return response.json()

def scrape_website_for_contacts(url):
    """Attempts to find Telegram or Twitter links in the website HTML."""
    if not url or url == "N/A" or not str(url).startswith("http"):
        return "Not Found", "Not Found"
    
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"}
        response = requests.get(url, headers=headers, timeout=10)
        html = response.text
        
        # Regex for Telegram
        tg_match = re.search(r'(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/([a-zA-Z0-9_]{5,32})', html)
        if not tg_match:
            tg_match = re.search(r'@([a-zA-Z0-9_]{5,32})', html)
            
        # Regex for Twitter/X
        tw_match = re.search(r'(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/([a-zA-Z0-9_]{1,15})', html)
        
        telegram = f"@{tg_match.group(1)}" if tg_match else "Not Found"
        twitter = f"@{tw_match.group(1)}" if tw_match else "Not Found"
        
        return telegram, twitter
    except Exception:
        return "Error/Timeout", "Error/Timeout"

def normalize_leads(llama_data):
    print("Filtering for REAL projects: TVL > $250k AND Market Cap < $10M...")
    leads = []
    target_chains = ["Solana", "Base", "Binance", "Ethereum", "Arbitrum", "Optimism"]
    
    for proto in llama_data:
        chains = proto.get("chains", [])
        has_chain = any(c in target_chains for c in chains)
        
        mcap = proto.get("mcap")
        tvl = proto.get("tvl", 0)
        name_lower = proto.get("name", "").lower()
        url = proto.get("url")
        
        # THE SWEET SPOT FILTER:
        # 1. Must be on a target chain
        # 2. MUST have a valid website URL
        # 3. TVL > $250,000 (Proves real users/liquidity exist)
        # 4. Market Cap < $10,000,000 (Still small enough to be hungry for perp listings)
        # 5. NOT in the blocklist
        
        if (has_chain and url and 
            tvl >= 250000 and 
            mcap and 100000 <= mcap <= 10000000 and 
            not any(blocked in name_lower for blocked in BLOCKLIST)):
            
            leads.append({
                "source": "DefiLlama_SweetSpot",
                "slug": proto.get("slug"),
                "name": proto.get("name"),
                "symbol": proto.get("symbol", "N/A"),
                "chain": next((c for c in chains if c in target_chains), "Multi-Chain"),
                "website": url,
                "mcap": mcap,
                "tvl": tvl
            })
    
    # Sort by TVL descending (prioritize projects with the most actual locked value)
    leads.sort(key=lambda x: x.get("tvl", 0), reverse=True)
    
    # Take the top 15 highest TVL projects that still meet the micro/small cap criteria
    quality_leads = leads[:15]
    print(f"Found {len(quality_leads)} high-quality, undervalued protocols.")
    return quality_leads

def load_processed_slugs():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return []

def save_processed_slugs(slugs):
    with open(STATE_FILE, "w") as f:
        json.dump(slugs, f)

def send_telegram_message(lead):
    mcap_str = f"${int(lead['mcap']):,}"
    tvl_str = f"${int(lead['tvl']):,}"
    website_str = lead.get('website', 'N/A')
    
    print(f"Scraping contacts for: {lead['name']} ({website_str})")
    telegram, twitter = scrape_website_for_contacts(website_str)
    
    text = (
        f"🚨 *High-Potential Vibe Trading Lead*\n\n"
        f"📛 *Project*: {lead['name']} ({lead.get('symbol', 'N/A')})\n"
        f"⛓️ *Chain*: {lead['chain']}\n"
        f"💰 *Market Cap*: {mcap_str}\n"
        f"🏦 *Total Value Locked (TVL)*: {tvl_str}\n"
        f"🔗 *Website*: {website_str}\n\n"
        f"📞 *Auto-Extracted Contacts*:\n"
        f"• Telegram: {telegram}\n"
        f"• Twitter: {twitter}\n\n"
        f"💬 *Vibe Pitch*: \"Instead of dumping treasury, deposit tokens to Vibe's vault to list a perp for free, back OI, and earn trading fees forever.\""
    )
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    requests.post(url, json=payload)
    print(f"✅ Sent Telegram message for {lead['name']}")

def main():
    print("=== STARTING VIBE TRADING LEAD GEN PIPELINE (SWEET SPOT MODE) ===")
    
    try:
        # We are now relying primarily on DefiLlama for quality over raw Dune noise
        llama_data = fetch_defillama()
        
        all_leads = normalize_leads(llama_data)
        print(f"Total quality leads to process: {len(all_leads)}")
        
        processed_slugs = load_processed_slugs()
        new_leads = [lead for lead in all_leads if lead["slug"] not in processed_slugs]
        print(f"New leads after deduplication: {len(new_leads)}")
        
        for lead in new_leads:
            send_telegram_message(lead)
            processed_slugs.append(lead["slug"])
            
        save_processed_slugs(processed_slugs)
        print("=== PIPELINE FINISHED SUCCESSFULLY ===")
        
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}")
        raise

if __name__ == "__main__":
    main()
