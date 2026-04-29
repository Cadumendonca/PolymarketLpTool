import logging
import sys
import os

# Adiciona o diretório atual ao path para importar os módulos locais
sys.path.append(os.getcwd())

from passive_liquidity.yield_hunter import get_top_reward_opportunities, format_yield_scan_msg
from passive_liquidity.market_display import MarketDisplayResolver

# Configuração de log básica
logging.basicConfig(level=logging.INFO)

def main():
    print("Starting Yield Hunter Opportunity Scanner Test...")
    
    clob_host = "https://clob.polymarket.com"
    gamma_host = "https://gamma-api.polymarket.com"
    
    print(f"Querying {clob_host}...")
    
    try:
        resolver = MarketDisplayResolver(gamma_host)
        
        # Busca as top oportunidades (limite de 5 para o teste)
        opps = get_top_reward_opportunities(clob_host, limit=5)
        
        if not opps:
            print("No opportunities found or error in query.")
            return
            
        print(f"Found {len(opps)} opportunities!\n")
        
        # Formata a mensagem com o resolver para pegar os nomes reais
        msg = format_yield_scan_msg(opps, resolver)
        
        print("--- SCAN RESULT ---")
        print(msg)
        print("-------------------------")
        
    except Exception as e:
        print(f"Fatal error during test: {e}")

if __name__ == "__main__":
    main()
