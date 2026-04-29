"""
Mapeia strings de razão (reason) de cancelamento para rótulos em Português para o Telegram.
"""

from __future__ import annotations

from typing import Tuple

# (Título da Categoria, Descrição legível; mantendo a razão original em inglês para depuração)
_REASON_TABLE: dict[str, Tuple[str, str]] = {
    "inventory_at_max_long_no_more_bids": (
        "Cancelamento por Estoque / Risco",
        "Limite máximo de estoque (Long) atingido, cancelando ordens de compra.",
    ),
    "inventory_at_max_short_no_more_asks": (
        "Cancelamento por Estoque / Risco",
        "Limite máximo de estoque (Short) atingido, cancelando ordens de venda.",
    ),
    "buy_above_mid": (
        "Cancelamento por Preço Inválido (fora do Mid)",
        "Preço de compra acima do Mid, fora da estratégia, cancelando.",
    ),
    "sell_below_mid": (
        "Cancelamento por Preço Inválido (fora do Mid)",
        "Preço de venda abaixo do Mid, fora da estratégia, cancelando.",
    ),
    "buy_far_below_reward_band": (
        "Cancelamento por Preço fora da Banda de Incentivo",
        "Preço de compra muito abaixo da banda de recompensa, cancelando.",
    ),
    "sell_far_above_reward_band": (
        "Cancelamento por Preço fora da Banda de Incentivo",
        "Preço de venda muito acima da banda de recompensa, cancelando.",
    ),
}


def cancel_category_zh(decision_reason: str) -> Tuple[str, str, str]:
    """
    Retorna (Categoria, Descrição, Razão Original).
    """
    key = (decision_reason or "").strip()
    if key in _REASON_TABLE:
        cat, desc = _REASON_TABLE[key]
        return (cat, desc, key)
    if key.startswith("widen_") or "fill_pressure" in key or "fill" in key.lower():
        return (
            "Cancelamento Defensivo (Risco de Execução)",
            "Cancelamento relacionado à densidade de execuções ou controle de risco defensivo.",
            key,
        )
    if "manual" in key.lower() or "inventory" in key.lower():
        return (
            "Cancelamento por Estoque / Risco Manual",
            "Cancelamento relacionado ao estoque ou intervenção manual.",
            key,
        )
    if "mid" in key.lower() or "band" in key.lower() or "nudge" in key.lower():
        return (
            "Cancelamento por Ajuste de Preço (Mid/Banda)",
            "Cancelamento relacionado à mudança no preço médio ou banda de recompensa.",
            key,
        )
    return (
        "Cancelamento por Outras Razões do Sistema",
        "Cancelamento de estratégia ou sistema não categorizado.",
        key,
    )
