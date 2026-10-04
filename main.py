import os
import json
import requests
import re

# --- CONFIGURATION ---
DUNE_API_KEY = os.environ.get("DUNE_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
DUNE_SOLANA_QUERY_ID = os.environ.get("DUNE_SOLANA_QUERY_ID")
DUNE_EVM_QUERY_ID = os.environ.get("DUNE_EVM_QUERY_ID")

STATE_FILE = "processed_leads.json"

def fetch_dune_results(query_id):
    print(f"Fetching Dune query: {query_id}")
    url = f"https://api.dune.com/api/v1/query/{query_id}/results"
    headers = {"x-dune-api-key": DUNE_API_KEY}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json().get("result", {}).get("rows", [])

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
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
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

def normalize_leads(solana_data, evm_data, llama_data):
    print("Normalizing and filtering leads...")
    leads = []
    
    # 1. Solana (Pump.fun)
    for row in solana_data[:10]:
        name = row.get("token_name", "Unknown")
        if name and name != "Unknown":
            leads.append({
                "source": "Dune_Solana",
                "slug": row.get("tx_hash"),
                "name": name,
                "symbol": row.get("token_symbol", "???"),
                "chain": "Solana",
                "website": f"https://pump.fun/{row.get('token_address')}",
                "mcap": None
            })

    # 2. DefiLlama (Established Projects)
    target_chains = ["Solana", "Base", "Binance"]
    for proto in llama_data:
        chains = proto.get("chains", [])
        has_chain = any(c in target_chains for c in chains)
        mcap = proto.get("mcap")
        
        # Strict filter: Must have target chain, valid MCAP ($100k - $20M), AND a real URL
        if has_chain and mcap and 100000 <= mcap <= 20000000 and proto.get("url"):
            leads.append({
                "source": "DefiLlama",
                "slug": proto.get("slug"),
                "name": proto.get("name"),
                "symbol": proto.get("symbol", "N/A"),
                "chain": next((c for c in chains if c in target_chains), "Unknown"),
                "website": proto.get("url"),
                "mcap": mcap
            })
    
    # Final cleanup: Ensure no "Unknown" names slipped through
    quality_leads = [lead for lead in leads if lead.get("name") and lead.get("name") != "Unknown"]
    
    # Balance: Max 8 DefiLlama, Max 7 Solana (Total 15 high-quality leads)
    defi_leads = [l for l in quality_leads if l["source"] == "DefiLlama"][:8]
    solana_leads = [l for l in quality_leads if l["source"] == "Dune_Solana"][:7]
    
    print(f"Selected {len(defi_leads)} DefiLlama leads and {len(solana_leads)} Solana leads.")
    return defi_leads + solana_leads

def load_processed_slugs():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return []

def save_processed_slugs(slugs):
    with open(STATE_FILE, "w") as f:
        json.dump(slugs, f)

def send_telegram_message(lead):
    mcap_str = f"${int(lead['mcap']):,}" if lead.get('mcap') else "New/Unknown"
    website_str = lead.get('website', 'N/A')
    
    print(f"Scraping contacts for: {lead['name']} ({website_str})")
    telegram, twitter = scrape_website_for_contacts(website_str)
    
    text = (
        f"🚨 *New Vibe Trading Lead*\n\n"
        f"📛 *Project*: {lead['name']} ({lead.get('symbol', 'N/A')})\n"
        f"⛓️ *Chain*: {lead['chain']}\n"
        f"💰 *Market Cap*: {mcap_str}\n"
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
    print("=== STARTING VIBE TRADING LEAD GEN PIPELINE ===")
    
    try:
        solana_data = fetch_dune_results(DUNE_SOLANA_QUERY_ID)
        evm_data = fetch_dune_results(DUNE_EVM_QUERY_ID) 
        llama_data = fetch_defillama()
        
        all_leads = normalize_leads(solana_data, evm_data, llama_data)
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
