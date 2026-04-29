"""
Fluxo interativo /set_rule do Telegram (máquina de estados finitos, uma sessão por chat).
"""

from __future__ import annotations

import enum
import logging
import re
import threading
from dataclasses import dataclass
from typing import Any, Literal, Optional

from passive_liquidity.custom_pricing_rules_store import (
    CustomPricingRulesStore,
    StoredCustomRule,
    stable_rule_key,
)
from passive_liquidity.order_manager import (
    _oid,
    _price,
    _side,
    _token_id,
)
from passive_liquidity.orderbook_fetcher import OrderBookFetcher
from passive_liquidity.simple_price_policy import (
    CustomPricingSettings,
    classify_custom_tick_regime,
)

LOG = logging.getLogger(__name__)

# Dica para usuários em grupos (modo de privacidade do Telegram)
_GROUP_INPUT_HINT = (
    "\n\n———\n"
    "Se o Bot não responder ao seu número ou texto (comum em **grupos**): No modo de privacidade do Telegram, o Bot não recebe mensagens comuns.\n"
    "Escolha uma opção: ① Converse com o Bot no **privado**; ② **Responda** à última mensagem do Bot no grupo; ③ Mencione o Bot (**@nome_do_bot**) na mensagem; "
    "④ Use o comando de input, ex: `/input 2` ou `/input sim` ou `/input confirmar`."
)

_fsm_lock = threading.RLock()
# chat_id str -> session
_sessions: dict[str, "RuleSetupSession"] = {}


class _State(enum.Enum):
    IDLE = 0
    COARSE_OFFSET = 1
    COARSE_TOP = 2
    COARSE_MIN = 3
    COARSE_CONFIRM = 4
    FINE_SAFE_MIN = 5
    FINE_SAFE_MAX = 6
    FINE_TARGET = 7
    FINE_CONFIRM = 8


@dataclass
class _OrderSnap:
    order_id: str
    token_id: str
    side: str
    tick_size: float
    market_title: str
    outcome: str
    price: float


@dataclass
class RuleSetupSession:
    state: _State = _State.IDLE
    snap: Optional[_OrderSnap] = None
    tick_type: Literal["coarse", "fine"] = "fine"
    draft_offset: int = 0
    draft_allow_top: bool = True
    draft_min_cand: int = 1
    draft_safe_min: float = 0.4
    draft_safe_max: float = 0.6
    draft_target_ratio: float = 0.5


def _cancel_session(chat_id: str) -> None:
    with _fsm_lock:
        _sessions.pop(str(chat_id), None)


def _get_session(chat_id: str) -> Optional[RuleSetupSession]:
    with _fsm_lock:
        return _sessions.get(str(chat_id))


def _set_session(chat_id: str, sess: RuleSetupSession) -> None:
    with _fsm_lock:
        _sessions[str(chat_id)] = sess


def cancel_rule_setup_chat(chat_id: str) -> bool:
    """Retorna True se uma sessão estava ativa."""
    with _fsm_lock:
        if str(chat_id) not in _sessions:
            return False
        _sessions.pop(str(chat_id), None)
        return True


def _normalize_step_text(text: str) -> str:
    """Limpa e normaliza dígitos para o formato padrão."""
    s = str(text).strip()
    return s


def _order_meta_title_outcome(order: dict) -> tuple[str, str]:
    title = str(
        order.get("question")
        or order.get("market_question")
        or order.get("title")
        or ""
    ).strip()
    if not title:
        slug = order.get("market_slug") or order.get("slug") or ""
        title = str(slug).strip() if slug else ""
    if not title:
        mid = str(order.get("market") or order.get("condition_id") or "").strip()
        title = (mid[:48] + "…") if len(mid) > 48 else mid if mid else "(Mercado Desconhecido)"
    outcome = str(order.get("outcome") or order.get("outcome_name") or "").strip()
    return title, outcome


def _find_open_order(
    orders: list[dict], order_id: str
) -> Optional[dict]:
    want = str(order_id).strip()
    if not want:
        return None
    for o in orders:
        if not isinstance(o, dict):
            continue
        oid = str(_oid(o) or "").strip()
        if oid == want:
            return o
    return None


def _parse_yes_no(text: str) -> Optional[bool]:
    t = text.strip().lower()
    if t in ("yes", "y", "true", "1", "sim", "s"):
        return True
    if t in ("no", "n", "false", "0", "não", "nao"):
        return False
    return None


def _fmt_snap(s: _OrderSnap) -> str:
    return (
        f"ID da Ordem: {s.order_id[:40]}{'…' if len(s.order_id) > 40 else ''}\n"
        f"Token: {s.token_id}\n"
        f"Lado: {s.side}\n"
        f"Preço Atual: {s.price}\n"
        f"Tick Size: {s.tick_size}\n"
        f"Mercado: {s.market_title}\n"
        f"Outcome: {s.outcome or '—'}\n"
        f"Chave de Regra: {stable_rule_key(s.token_id, s.side)}"
    )


def _summary_coarse(sess: RuleSetupSession) -> str:
    assert sess.snap
    top = "Sim" if sess.draft_allow_top else "Não"
    return (
        f"{_fmt_snap(sess.snap)}\n"
        f"Tipo: Tick Grosso (Coarse)\n"
        f"Posição N na banda (1º = mais perto do mid): N={sess.draft_offset}\n"
        f"Permitir Top of Book: {top}\n"
        f"Níveis mínimos na banda: {sess.draft_min_cand}\n"
        f"\nResponda 'confirm' para salvar, ou 'cancel' para descartar."
    )


def _summary_fine(sess: RuleSetupSession) -> str:
    assert sess.snap
    return (
        f"{_fmt_snap(sess.snap)}\n"
        f"Tipo: Tick Fino (Fine)\n"
        f"Safe Band Min: {sess.draft_safe_min}\n"
        f"Safe Band Max: {sess.draft_safe_max}\n"
        f"Target Ratio: {sess.draft_target_ratio}\n"
        f"\nResponda 'confirm' para salvar, ou 'cancel' para descartar."
    )


def cmd_set_rule(
    chat_id: str,
    order_id_arg: str,
    *,
    client: Any,
    order_manager: Any,
    book_fetcher: OrderBookFetcher,
    default_settings: CustomPricingSettings,
) -> str:
    oid = order_id_arg.strip()
    if not oid:
        return "Uso: /set_rule <order_id>"

    try:
        orders = order_manager.fetch_all_open_orders(client)
    except Exception as e:
        LOG.exception("Erro ao buscar ordens no set_rule")
        return f"Falha ao buscar ordens abertas: {e}"

    o = _find_open_order(orders, oid)
    if o is None:
        return f"Ordem aberta não encontrada para o ID: {oid[:48]}… (Verifique se o ID está completo e se a ordem ainda existe)"

    tid = str(_token_id(o) or "").strip()
    side = str(_side(o) or "").strip().upper()
    if not tid or side not in ("BUY", "SELL"):
        return "A ordem não possui token_id ou lado (BUY/SELL) válido."

    try:
        book = book_fetcher.get_orderbook(tid)
        tick = float(book.tick_size or 0.01)
    except Exception as e:
        LOG.warning("Erro de orderbook no set_rule para %s: %s", tid[:24], e)
        tick = 0.01

    title, outcome = _order_meta_title_outcome(o)
    try:
        price = float(_price(o))
    except (TypeError, ValueError):
        price = 0.0

    tt: Literal["coarse", "fine"] = classify_custom_tick_regime(tick)

    sess = RuleSetupSession()
    sess.snap = _OrderSnap(
        order_id=str(_oid(o)),
        token_id=tid,
        side=side,
        tick_size=tick,
        market_title=title,
        outcome=outcome,
        price=price,
    )
    sess.tick_type = tt
    sess.draft_offset = max(1, int(default_settings.coarse_tick_offset_from_mid))
    sess.draft_allow_top = bool(default_settings.coarse_allow_top_of_book)
    sess.draft_min_cand = max(1, int(default_settings.coarse_min_candidate_levels))
    sess.draft_safe_min = float(default_settings.fine_safe_band_min)
    sess.draft_safe_max = float(default_settings.fine_safe_band_max)
    sess.draft_target_ratio = float(default_settings.fine_target_band_ratio)

    if tt == "coarse":
        sess.state = _State.COARSE_OFFSET
        _set_session(chat_id, sess)
        return (
            f"Configurando ajuste personalizado (Tick Grosso).\n{_fmt_snap(sess.snap)}\n\n"
            "Passo 1/4: Digite a posição N desejada (inteiro ≥1).\n"
            "N é a posição na banda de recompensa (1 = mais perto do preço médio).\n"
            "Ex COMPRA níveis na banda [0.28, 0.27, 0.26]: N=1 → 0.28, N=2 → 0.27, N=3 → 0.26."
            f"{_GROUP_INPUT_HINT}"
        )

    sess.state = _State.FINE_SAFE_MIN
    _set_session(chat_id, sess)
    return (
        f"Configurando ajuste personalizado (Tick Fino).\n{_fmt_snap(sess.snap)}\n\n"
        "Passo 1/4: Digite o safe_band_min (número entre 0 e 1, ex: 0.4)."
        f"{_GROUP_INPUT_HINT}"
    )


def cmd_get_rule(
    order_id_arg: str,
    *,
    client: Any,
    order_manager: Any,
    store: CustomPricingRulesStore,
) -> str:
    oid = order_id_arg.strip()
    if not oid:
        return "Uso: /get_rule <order_id>"

    try:
        orders = order_manager.fetch_all_open_orders(client)
    except Exception as e:
        return f"Falha ao buscar ordens abertas: {e}"

    o = _find_open_order(orders, oid)
    if o is None:
        return f"Ordem aberta não encontrada para o ID: {oid[:48]}…"

    tid = str(_token_id(o) or "").strip()
    side = str(_side(o) or "").strip().upper()
    key = stable_rule_key(tid, side)
    rule = store.get_rule(tid, side)
    if rule is None:
        return f"Não há regra personalizada salva para a chave {key} (usando ajuste padrão)."
    top = "Sim" if rule.coarse_allow_top_of_book else "Não"
    return (
        f"Chave: {key}\n"
        f"Tipo de Tick salvo: {rule.tick_regime}\n"
        f"Grosso: N={rule.coarse_tick_offset_from_mid} "
        f"(N-ésima posição na banda) allow_top={top} níveis_mín={rule.coarse_min_candidate_levels}\n"
        f"Fino: safe=[{rule.fine_safe_band_min}, {rule.fine_safe_band_max}] "
        f"target_ratio={rule.fine_target_band_ratio}"
    )


def cmd_clear_rule(
    order_id_arg: str,
    *,
    client: Any,
    order_manager: Any,
    store: CustomPricingRulesStore,
) -> str:
    oid = order_id_arg.strip()
    if not oid:
        return "Uso: /clear_rule <order_id>"

    try:
        orders = order_manager.fetch_all_open_orders(client)
    except Exception as e:
        return f"Falha ao buscar ordens abertas: {e}"

    o = _find_open_order(orders, oid)
    if o is None:
        return f"Ordem aberta não encontrada para o ID: {oid[:48]}…"

    tid = str(_token_id(o) or "").strip()
    side = str(_side(o) or "").strip().upper()
    key = stable_rule_key(tid, side)
    if store.clear_rule(tid, side):
        return f"Regra personalizada removida para a chave: {key} (o token+direção voltará ao ajuste padrão)."
    return f"A chave {key} não possuía uma regra personalizada."


def _confirm_save(
    chat_id: str,
    sess: RuleSetupSession,
    store: CustomPricingRulesStore,
    defaults: CustomPricingSettings,
) -> str:
    assert sess.snap
    if sess.tick_type == "coarse":
        rule = StoredCustomRule(
            tick_regime="coarse",
            coarse_tick_offset_from_mid=sess.draft_offset,
            coarse_allow_top_of_book=sess.draft_allow_top,
            coarse_min_candidate_levels=sess.draft_min_cand,
            fine_safe_band_min=defaults.fine_safe_band_min,
            fine_safe_band_max=defaults.fine_safe_band_max,
            fine_target_band_ratio=defaults.fine_target_band_ratio,
        )
    else:
        rule = StoredCustomRule(
            tick_regime="fine",
            coarse_tick_offset_from_mid=defaults.coarse_tick_offset_from_mid,
            coarse_allow_top_of_book=defaults.coarse_allow_top_of_book,
            coarse_min_candidate_levels=defaults.coarse_min_candidate_levels,
            fine_safe_band_min=sess.draft_safe_min,
            fine_safe_band_max=sess.draft_safe_max,
            fine_target_band_ratio=sess.draft_target_ratio,
        )
    store.set_rule(sess.snap.token_id, sess.snap.side, rule)
    key = stable_rule_key(sess.snap.token_id, sess.snap.side)
    _cancel_session(chat_id)
    return f"Regra personalizada salva (Chave {key}). Novas ordens para este token+direção usarão esta regra."


def handle_fsm_text(
    chat_id: str,
    text: str,
    *,
    store: CustomPricingRulesStore,
    default_settings: CustomPricingSettings,
) -> Optional[str]:
    """
    Processa mensagens de texto durante a configuração de regras.
    """
    sess = _get_session(chat_id)
    if sess is None or sess.state == _State.IDLE:
        return None

    raw = _normalize_step_text(text)
    low = raw.lower()

    if low == "cancel":
        _cancel_session(chat_id)
        return "Configuração cancelada (nada foi salvo)."

    if sess.state == _State.COARSE_CONFIRM:
        if low == "confirm":
            return _confirm_save(chat_id, sess, store, default_settings)
        return "Responda 'confirm' para salvar ou 'cancel' para descartar."

    if sess.state == _State.FINE_CONFIRM:
        if low == "confirm":
            return _confirm_save(chat_id, sess, store, default_settings)
        return "Responda 'confirm' para salvar ou 'cancel' para descartar."

    if sess.state == _State.COARSE_OFFSET:
        if not re.fullmatch(r"[0-9]+", raw):
            return "Digite um número inteiro positivo (≥1)."
        v = int(raw)
        if v < 1:
            return "O valor de N deve ser >= 1."
        sess.draft_offset = v
        sess.state = _State.COARSE_TOP
        _set_session(chat_id, sess)
        return "Passo 2/4: Permitir que a ordem seja a melhor oferta (Top of Book)? Responda 'sim' ou 'não'."

    if sess.state == _State.COARSE_TOP:
        yn = _parse_yes_no(raw)
        if yn is None:
            return "Responda 'sim' ou 'não'."
        sess.draft_allow_top = yn
        sess.state = _State.COARSE_MIN
        _set_session(chat_id, sess)
        return (
            "Passo 3/4: Digite min_candidate_levels (inteiro positivo).\n"
            "Significado: Quantidade mínima de níveis de preço válidos na banda para aplicar o ajuste. "
            "Se houver menos níveis que este valor, a ordem será mantida."
        )

    if sess.state == _State.COARSE_MIN:
        if not re.fullmatch(r"[0-9]+", raw):
            return "Digite um número inteiro positivo."
        v = int(raw)
        if v < 1:
            return "min_candidate_levels deve ser >= 1."
        sess.draft_min_cand = v
        sess.state = _State.COARSE_CONFIRM
        _set_session(chat_id, sess)
        return "Passo 4/4: Por favor, confirme os detalhes:\n" + _summary_coarse(sess)

    if sess.state == _State.FINE_SAFE_MIN:
        try:
            v = float(raw.replace(",", "."))
        except ValueError:
            return "Digite um número (ex: 0.4)."
        if v < 0 or v > 1:
            return "safe_band_min deve estar entre 0 e 1."
        sess.draft_safe_min = v
        sess.state = _State.FINE_SAFE_MAX
        _set_session(chat_id, sess)
        return "Passo 2/4: Digite o safe_band_max (entre 0 e 1, deve ser maior que o min)."

    if sess.state == _State.FINE_SAFE_MAX:
        try:
            v = float(raw.replace(",", "."))
        except ValueError:
            return "Digite um número (ex: 0.6)."
        if v < 0 or v > 1:
            return "safe_band_max deve estar entre 0 e 1."
        if not (sess.draft_safe_min < v):
            return "O valor máximo deve ser maior que o mínimo."
        sess.draft_safe_max = v
        sess.state = _State.FINE_TARGET
        _set_session(chat_id, sess)
        return "Passo 3/4: Digite o target_band_ratio (entre 0 e 1, ex: 0.5)."

    if sess.state == _State.FINE_TARGET:
        try:
            v = float(raw.replace(",", "."))
        except ValueError:
            return "Digite um número (ex: 0.5)."
        if v < 0 or v > 1:
            return "target_band_ratio deve estar entre 0 e 1."
        sess.draft_target_ratio = v
        sess.state = _State.FINE_CONFIRM
        _set_session(chat_id, sess)
        return "Passo 4/4: Por favor, confirme os detalhes:\n" + _summary_fine(sess)

    return "Entrada não reconhecida. Siga as instruções ou digite 'cancel'."


def dispatch_command(
    chat_id: str,
    command: str,
    arg_line: str,
    *,
    client: Any,
    order_manager: Any,
    book_fetcher: OrderBookFetcher,
    store: CustomPricingRulesStore,
    default_settings: CustomPricingSettings,
) -> Optional[str]:
    """Lida com comandos slash relacionados a regras."""
    cmd = command.lower().strip()
    if cmd == "/set_rule":
        return cmd_set_rule(
            chat_id,
            arg_line,
            client=client,
            order_manager=order_manager,
            book_fetcher=book_fetcher,
            default_settings=default_settings,
        )
    if cmd == "/get_rule":
        return cmd_get_rule(
            arg_line,
            client=client,
            order_manager=order_manager,
            store=store,
        )
    if cmd == "/clear_rule":
        return cmd_clear_rule(
            arg_line,
            client=client,
            order_manager=order_manager,
            store=store,
        )
    if cmd == "/cancel_rule_setup":
        if cancel_rule_setup_chat(chat_id):
            return "Sessão de configuração de regra cancelada."
        return "Não há configuração em andamento."
    return None
