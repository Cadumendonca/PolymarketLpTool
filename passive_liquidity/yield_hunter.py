from __future__ import annotations

import logging
from typing import Any, Optional

from passive_liquidity.http_utils import http_json
from passive_liquidity.market_display import MarketDisplayResolver

LOG = logging.getLogger(__name__)

def get_top_reward_opportunities(clob_host: str, limit: int = 10) -> list[dict[str, Any]]:
    """
    Busca mercados com as maiores recompensas diárias.
    """
    clob_host = clob_host.rstrip("/")
    # Endpoint para mercados com recompensas atuais
    url = f"{clob_host}/rewards/markets/current"
    
    try:
        data = http_json("GET", url)
        if not isinstance(data, dict) or "data" not in data:
            LOG.warning("Formato de resposta de recompensas inesperado: %s", data)
            return []
        
        markets = data["data"]
        if not isinstance(markets, list):
            return []

        results = []
        for m in markets:
            condition_id = m.get("condition_id")
            max_spread = float(m.get("rewards_max_spread") or 0)
            min_size = float(m.get("rewards_min_size") or 0)
            
            # Calcular taxa diária total (soma de USDC, etc)
            configs = m.get("rewards_config") or []
            daily_rate = 0.0
            for cfg in configs:
                daily_rate += float(cfg.get("rate_per_day") or 0)
            
            if daily_rate <= 0:
                continue

            results.append({
                "condition_id": condition_id,
                "daily_rate": daily_rate,
                "max_spread": max_spread,
                "min_size": min_size,
            })

        # Ordenar por maior recompensa diária
        results.sort(key=lambda x: x["daily_rate"], reverse=True)
        return results[:limit]

    except Exception as e:
        LOG.error("Falha ao buscar oportunidades de rendimento: %s", e)
        return []

def format_yield_scan_msg(opps: list[dict[str, Any]], resolver: Optional[MarketDisplayResolver] = None) -> str:
    """
    Formata a lista de oportunidades para uma mensagem do Telegram.
    """
    if not opps:
        return "Nenhuma oportunidade de recompensa encontrada no momento."

    lines = ["🎯 **Melhores Oportunidades de Recompensa**", ""]
    
    for i, op in enumerate(opps, 1):
        cid = op["condition_id"]
        rate = op["daily_rate"]
        spread = op["max_spread"]
        
        title = ""
        if resolver:
            title, _ = resolver.lookup(cid, "")
        
        market_name = title if title else f"ID: {cid[:8]}..."
        
        lines.append(f"{i}. **{market_name}**")
        lines.append(f"   💰 Recompensa: ${rate:,.2f}/dia")
        lines.append(f"   📏 Spread Máx: {spread}%")
        # Link para o mercado (opcional, facilitando a vida do usuário)
        # Note: condition_id não é o slug, mas ajuda a identificar.
        lines.append("")

    lines.append("💡 *Dica: Use /set_rule <order_id> após abrir uma ordem nestes mercados.*")
    return "\n".join(lines)
