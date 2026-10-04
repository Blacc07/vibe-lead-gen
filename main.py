def normalize_leads(solana_data, evm_data, llama_data):
    """Merges and normalizes data from all sources - QUALITY OVER QUANTITY"""
    leads = []
    
    # 1. Solana (Pump.fun) - These have names & symbols
    for row in solana_data[:10]:
        leads.append({
            "source": "Dune_Solana",
            "slug": row.get("tx_hash"),
            "name": row.get("token_name", "Unknown"),
            "symbol": row.get("token_symbol", "???"),
            "chain": "Solana",
            "website": f"https://pump.fun/{row.get('token_address')}",
            "mcap": None
        })

    # 2. DefiLlama (Established) - These have REAL data
    target_chains = ["Solana", "Base", "Binance"]
    for proto in llama_data:
        chains = proto.get("chains", [])
        has_chain = any(c in target_chains for c in chains)
        mcap = proto.get("mcap")
        
        # Only include if it has: target chain, valid MCAP, AND a URL
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
    
    # 3. EVM - ONLY if we can get useful metadata
    # For now, SKIP raw deployments - they're useless without websites/names
    # Uncomment below if you find a better Dune query with metadata
    """
    for row in evm_data[:5]:
        # Only add if we have a contract address we can investigate
        if row.get("contract_address"):
            leads.append({
                "source": "Dune_EVM",
                "slug": row.get("tx_hash"),
                "name": "New EVM Contract",  # Generic but better than nothing
                "symbol": "N/A",
                "chain": row.get("chain"),
                "contract_address": row.get("contract_address"),
                "website": f"https://{row.get('chain').lower()}.scan/address/{row.get('contract_address')}",  # Link to block explorer
                "mcap": None
            })
    """
    
    # Return ONLY leads with actual names (filter out "Unknown")
    quality_leads = [lead for lead in leads if lead.get("name") != "Unknown"]
    
    # Limit to 15 total, prioritizing DefiLlama (highest quality)
    defi_leads = [l for l in quality_leads if l["source"] == "DefiLlama"][:8]
    solana_leads = [l for l in quality_leads if l["source"] == "Dune_Solana"][:7]
    
    return defi_leads + solana_leads
