"""
Notificações não-bloqueantes do Telegram para monitoramento da Polymarket (opcional, via env).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

LOG = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _fmt_amt(x: float) -> str:
    """Exibição de valores no Telegram (dinheiro/preço) com 2 decimais."""
    return f"{float(x):.2f}"


def _maybe_log_supergroup_migration(error_body: str) -> None:
    """
    O Telegram retorna 400 com migrate_to_chat_id quando um grupo é promovido a supergrupo.
    O TELEGRAM_CHAT_ID antigo para de funcionar; o usuário deve atualizar o .env.
    """
    try:
        data = json.loads(error_body)
    except json.JSONDecodeError:
        return
    params = data.get("parameters")
    if not isinstance(params, dict):
        return
    new_id = params.get("migrate_to_chat_id")
    if new_id is None:
        return
    LOG.error(
        "Telegram: O TELEGRAM_CHAT_ID atual expirou (o grupo foi atualizado). "
        "Altere o TELEGRAM_CHAT_ID no seu .env para: %s e reinicie o programa. "
        "Comandos e notificações dependem deste ID.",
        new_id,
    )

# Strings de Status (Cópia para Telegram)
SCORING_STATUS_ON = "Ganhando Pontos"
SCORING_STATUS_OFF = "Sem Pontos"
MANUAL_HANDLING_STATUS = "Processamento Manual"
BOT_RESUMED_STATUS = "Retomando Monitoramento"
REPLACE_FAILED_STATUS = "Falha no ajuste, requer intervenção manual"


def scoring_status_text(scoring: bool) -> str:
    return SCORING_STATUS_ON if scoring else SCORING_STATUS_OFF


def scoring_transition_text(was_scoring: bool, now_scoring: bool) -> str:
    return f"{scoring_status_text(was_scoring)} -> {scoring_status_text(now_scoring)}"


# Códigos de razão (reason codes) → Explicação em Português
_PRICING_REASON_PT: dict[str, str] = {
    "coarse_tick_abandon_due_to_too_few_levels": (
        "Tick Grosso: Menos de 2 níveis válidos na banda de recompensa. Risco muito alto, cancelando e não repostando."
    ),
    "coarse_tick_choose_middle_of_3": "Tick Grosso: 3 níveis disponíveis, escolhendo o intermediário.",
    "coarse_tick_choose_third_from_mid_of_4": (
        "Tick Grosso: 4 níveis disponíveis, escolhendo o segundo mais distante (não o último)."
    ),
    "coarse_tick_choose_second_farthest_default": (
        "Tick Grosso: >4 níveis, escolhendo o segundo mais distante por padrão."
    ),
    "coarse_tick_keep_already_at_target": "Tick Grosso: Já está no preço alvo, mudança insuficiente, mantendo.",
    "unsupported_tick_keep": "Tick não suportado (não é 0.01/0.001), mantendo.",
    "fine_tick_keep_in_target_band": "Tick Fino: Ratio |preço−mid|/δ já está entre 0.4～0.6, mantendo.",
    "fine_tick_move_outward_to_half_band": "Tick Fino: Muito perto do mid, movendo para fora (0.5×δ).",
    "fine_tick_move_inward_to_half_band": "Tick Fino: Muito longe do mid, movendo para dentro (0.5×δ).",
    "fine_tick_move_outward_to_half_band_noop_small_delta": (
        "Tick Fino: Deveria mover para fora, mas diferença é menor que o tick mínimo, mantendo."
    ),
    "fine_tick_move_inward_to_half_band_noop_small_delta": (
        "Tick Fino: Deveria mover para dentro, mas diferença é menor que o tick mínimo, mantendo."
    ),
    # modo de ajuste personalizado (PASSIVE_CUSTOM_ORDER_IDS)
    "custom_missing_settings_keep": "Ajuste Custom: Faltam parâmetros, mantendo.",
    "custom_coarse_keep_band_outside_market": (
        "Ajuste Custom Grosso: Banda de incentivo fora do intervalo de preço do mercado, mantendo."
    ),
    "custom_coarse_keep_insufficient_candidates": (
        "Ajuste Custom Grosso: Níveis com profundidade insuficientes na banda (menor que min_candidate_levels), mantendo."
    ),
    "custom_coarse_replace_exact_offset_from_mid": (
        "Ajuste Custom Grosso: Ajustando para o N-ésimo nível da banda (1º é o mais perto do mid)."
    ),
    "custom_coarse_keep_rank_outside_band_levels": (
        "Ajuste Custom Grosso: N escolhido está fora dos níveis disponíveis na banda, mantendo."
    ),
    "custom_coarse_keep_offset_outside_band": (
        "Ajuste Custom Grosso: Preço alvo fora da banda ou inconsistente com os passos configurados, mantendo."
    ),
    "custom_coarse_keep_target_is_top_of_book": (
        "Ajuste Custom Grosso: Preço alvo é o melhor preço (Top of Book) e não é permitido, mantendo."
    ),
    "custom_coarse_keep_offset_invalid_price": (
        "Ajuste Custom Grosso: Preço alvo inválido (fora de 0–1), mantendo."
    ),
    "custom_coarse_keep_already_at_target": (
        "Ajuste Custom Grosso: Já está no preço alvo (mudança insuficiente), mantendo."
    ),
    "custom_fine_keep_in_safe_band": (
        "Ajuste Custom Fino: Ratio |preço−mid|/δ já está na faixa de segurança, mantendo."
    ),
    "custom_fine_move_toward_target_ratio": (
        "Ajuste Custom Fino: Ajustando para o ratio alvo na banda."
    ),
    "custom_fine_keep_small_delta": (
        "Ajuste Custom Fino: Diferença para o preço alvo menor que o tick mínimo, mantendo."
    ),
}


def pricing_adjustment_reason_zh(reason: str) -> str:
    """Converte códigos de razão para Português."""
    if not (reason or "").strip():
        return ""
    if "|" in reason:
        head, tail = reason.split("|", 1)
        head = head.strip()
        tail = tail.strip()
        pt = _PRICING_REASON_PT.get(head, head)
        return f"{pt} | {tail}" if tail else pt
    head = reason.strip()
    return _PRICING_REASON_PT.get(head, head)


@dataclass
class OrderEventFormat:
    """Campos para format_order_event_message."""

    account_label: str
    market_title: str
    outcome: str
    token_id: str
    side: str
    old_price: Optional[float]
    new_price: Optional[float]
    size: Optional[float]
    scoring_status_text: str
    inventory: Optional[float] = None
    reason: str = ""


def stable_fingerprint(*parts: Any) -> str:
    s = "|".join(str(p) for p in parts)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:32]


def polymarket_api_error_zh_hint(error_text: str) -> str:
    """Explicação curta em Português para erros comuns da API Polymarket."""
    e = (error_text or "").lower()
    if "not enough balance" in e or "balance is not enough" in e:
        return (
            "Causa provável: Saldo insuficiente em USDC na Polymarket para cobrir esta ordem, "
            "ou 'allowance' insuficiente. Tente: depositar mais USDC, cancelar outras ordens "
            "abertas ou verificar as permissões da carteira."
        )
    if "allowance" in e:
        return (
            "Causa provável: Permissão (allowance) de USDC insuficiente para o contrato. "
            "Por favor, complete o processo de depósito/aprovação no site da Polymarket."
        )
    if "post only" in e or "post_only" in e or "post-only" in e:
        return "Causa provável: A ordem 'Post-Only' conflita com o preço atual e seria executada imediatamente."
    if "invalid" in e and "price" in e:
        return "Causa provável: O preço ou o tick mínimo não seguem as regras deste mercado."
    if "nonce" in e or "expired" in e:
        return "Causa provável: Assinatura/nonce ou requisição expirada. Tente novamente em instantes ou verifique o relógio do seu sistema."
    return "Verifique o erro original retornado pela API abaixo; se persistir, requer intervenção manual."


class TelegramNotifier:
    """
    send_message é não-bloqueante (thread daemon).
    Falhas são apenas logadas; nunca lançam exceções.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        bot_token: str,
        chat_id: str,
        account_label: str,
        cooldown_sec: float,
    ):
        self._enabled = bool(enabled and bot_token and chat_id)
        self._bot_token = bot_token.strip()
        self._chat_id = chat_id.strip()
        self._account_label = (account_label or "Polymarket").strip() or "Polymarket"
        self._cooldown = max(0.0, float(cooldown_sec))
        self._lock = threading.Lock()
        # event_key -> (last_fingerprint, last_sent_monotonic_ts)
        self._last: dict[str, tuple[str, float]] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def account_label(self) -> str:
        return self._account_label

    @property
    def bot_token(self) -> str:
        return self._bot_token

    @property
    def chat_id(self) -> str:
        return self._chat_id

    def send_command_reply(self, text: str) -> None:
        """Responde a comandos como /status, /orders, etc. Ignora cooldown."""
        if not self._enabled:
            return
        token = self._bot_token
        chat = self._chat_id
        url = TELEGRAM_API.format(token=token)
        body = json.dumps(
            {"chat_id": chat, "text": text, "disable_web_page_preview": True},
            ensure_ascii=False,
        ).encode("utf-8")
        try:
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw else {}
            if not data.get("ok"):
                _maybe_log_supergroup_migration(raw)
                LOG.warning("Falha na resposta de comando do Telegram: %s", raw[:400])
            else:
                LOG.info("Resposta de comando enviada ao Telegram (%d caracteres)", len(text))
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                err_body = str(e)
            _maybe_log_supergroup_migration(err_body)
            LOG.warning(
                "Erro HTTP na resposta do Telegram: %s %s", e.code, err_body[:400]
            )
        except Exception as e:
            LOG.warning("Falha ao enviar resposta de comando: %s", e)

    def should_notify(self, event_key: str, payload_hash: str) -> bool:
        with self._lock:
            return self._should_notify_unlocked(event_key, payload_hash)

    def _should_notify_unlocked(self, event_key: str, payload_hash: str) -> bool:
        rec = self._last.get(event_key)
        now = time.monotonic()
        if rec:
            last_fp, last_ts = rec
            if last_fp == payload_hash:
                LOG.debug("Ignorando notificação duplicada key=%s", event_key)
                return False
            if self._cooldown > 0 and (now - last_ts) < self._cooldown:
                LOG.debug("Ignorando notificação (cooldown) key=%s (%.1fs)", event_key, self._cooldown)
                return False
        return True

    def record_last_notification(self, event_key: str, payload_hash: str) -> None:
        with self._lock:
            self._last[event_key] = (payload_hash, time.monotonic())

    def send_message(self, text: str, *, event_key: str, payload_hash: str) -> None:
        if not self._enabled:
            return
        with self._lock:
            if not self._should_notify_unlocked(event_key, payload_hash):
                return

        LOG.info("Enfileirando envio Telegram key=%s fp=%s…", event_key, payload_hash[:12])

        token = self._bot_token
        chat = self._chat_id
        url = TELEGRAM_API.format(token=token)
        body = json.dumps(
            {"chat_id": chat, "text": text, "disable_web_page_preview": True},
            ensure_ascii=False,
        ).encode("utf-8")

        def _worker() -> None:
            try:
                req = urllib.request.Request(
                    url,
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                data = json.loads(raw) if raw else {}
                if not data.get("ok"):
                    LOG.warning(
                        "API do Telegram retornou erro key=%s response=%s",
                        event_key,
                        raw[:500],
                    )
                    return
                with self._lock:
                    self._last[event_key] = (payload_hash, time.monotonic())
                LOG.info("Mensagem Telegram enviada OK key=%s", event_key)
            except urllib.error.HTTPError as e:
                try:
                    err_body = e.read().decode("utf-8", errors="replace")
                except Exception:
                    err_body = str(e)
                _maybe_log_supergroup_migration(err_body)
                LOG.warning("Erro HTTP Telegram key=%s: %s %s", event_key, e.code, err_body[:300])
            except Exception as e:
                LOG.warning("Falha no envio Telegram key=%s: %s", event_key, e)

        threading.Thread(target=_worker, name="telegram-send", daemon=True).start()

    def format_order_fill_message(
        self,
        *,
        account_label: str,
        market_title: str,
        outcome: str,
        side: str,
        order_price: float,
        filled_size: float,
        remaining_size: float,
        fill_type_zh: str,
        scoring_status_text_s: str,
        fill_price: Optional[float] = None,
        inventory: Optional[float] = None,
        fill_detection_source: Optional[str] = None,
    ) -> str:
        label = (account_label or "").strip() or self._account_label
        lines = [
            f"[{label}]",
            "Evento: Ordem Executada",
            f'Mercado: "{market_title}"',
            f"Direção: {outcome or '—'}",
            f"Tipo da Ordem: {str(side).upper()}",
            f"Preço da Ordem: {_fmt_amt(order_price)}",
            f"Quantidade Executada: {filled_size:g}",
            f"Quantidade Restante: {remaining_size:g}",
            f"Tipo de Execução: {fill_type_zh}",
        ]
        if fill_price is not None:
            lines.append(f"Preço de Execução (aprox): {_fmt_amt(fill_price)}")
        lines.append(f"Status de Pontuação: {scoring_status_text_s}")
        if inventory is not None:
            lines.append(f"Posição Atual: {_fmt_amt(inventory)}")
        if fill_detection_source:
            lines.append(f"Fonte da Detecção: {fill_detection_source}")
        return "\n".join(lines)

    def format_order_event_message(self, ev: OrderEventFormat) -> str:
        label = (ev.account_label or "").strip() or self._account_label
        header = f"[{label}]"
        lines = [
            header,
            f'Mercado: "{ev.market_title}"',
            f"Direção: {ev.outcome or ev.side}",
        ]
        if ev.outcome:
            lines.append(f"Compra/Venda: {ev.side}")
        if ev.token_id:
            lines.append(f"Token: {ev.token_id}")
        if ev.old_price is not None and ev.new_price is not None and ev.old_price != ev.new_price:
            lines.append(f"Preço: {_fmt_amt(ev.old_price)} -> {_fmt_amt(ev.new_price)}")
        elif ev.new_price is not None:
            lines.append(f"Preço: {_fmt_amt(ev.new_price)}")
        elif ev.old_price is not None:
            lines.append(f"Preço: {_fmt_amt(ev.old_price)}")
        if ev.size is not None:
            lines.append(f"Volume: {ev.size:g}")
        if ev.inventory is not None:
            lines.append(f"Posição: {_fmt_amt(ev.inventory)}")
        lines.append(f"Status: {ev.scoring_status_text}")
        if ev.reason:
            lines.append(f"Razão: {pricing_adjustment_reason_zh(ev.reason)}")
        return "\n".join(lines)

    def notify_operational_warning_zh(
        self,
        *,
        title_zh: str,
        lines: list[str],
        event_key: str,
    ) -> None:
        """Alerta genérico para erros de API / operações de ordem (saldo, retentativa, etc)."""
        body = "\n".join([f"[{self._account_label}]", f"⚠️ {title_zh}", ""] + lines)
        fp = stable_fingerprint("op_warn", event_key, body[:3000])
        self.send_message(text=body, event_key=event_key, payload_hash=fp)

    def notify_ws_transport_zh(
        self,
        *,
        title_zh: str,
        lines: list[str],
        event_key: str,
    ) -> None:
        """Status da conexão WebSocket."""
        body = "\n".join([f"[{self._account_label}]", title_zh, ""] + lines)
        fp = stable_fingerprint(
            "ws_transport", event_key, body[:2000], time.time()
        )
        self.send_message(text=body, event_key=event_key, payload_hash=fp)

    def notify_whitelist_init(
        self,
        *,
        source: str,
        token_ids: list[str],
        open_order_count: Optional[int],
    ) -> None:
        parts = ["Iniciando Whitelist", source, str(open_order_count), ",".join(sorted(token_ids))]
        fp = stable_fingerprint(*parts)
        lines = [
            f"[{self._account_label}]",
            "Evento: Whitelist de monitoramento inicializada",
            f"Fonte: {source}",
        ]
        if open_order_count is not None:
            lines.append(f"Ordens abertas na inicialização: {open_order_count}")
        lines.append(f"Quantidade de tokens únicos: {len(token_ids)}")
        for tid in sorted(token_ids)[:40]:
            lines.append(f" · {tid}")
        if len(token_ids) > 40:
            lines.append(f" … total {len(token_ids)} tokens, truncado")
        text = "\n".join(lines)
        self.send_message(text, event_key="startup:whitelist", payload_hash=fp)

    def notify_account_startup(
        self,
        *,
        deposited_reference_usdc: Optional[float],
        total_account_usdc: float,
        available_balance_usdc: float,
        locked_open_buy_usdc: float,
        pnl_usdc: Optional[float],
        extra_note_zh: str = "",
        clob_collateral_usdc: Optional[float] = None,
        positions_market_value_usdc: Optional[float] = None,
        positions_error_zh: str = "",
    ) -> None:
        if deposited_reference_usdc is None:
            ref_line = "Total Depositado (Ref): Não configurado"
            pnl_line = "PnL (Relativo ao depósito): Não calculado"
        else:
            ref_line = f"Total Depositado (Ref): {_fmt_amt(deposited_reference_usdc)} USDC"
            p = float(pnl_usdc or 0.0)
            sign = "+" if p >= 0 else ""
            pnl_line = f"PnL (Relativo ao depósito): {sign}{_fmt_amt(p)} USDC"
        lines = [
            f"[{self._account_label}]",
            "Evento: Programa Iniciado · Snapshot de Capital",
            ref_line,
            f"Total da Conta (Portfolio aprox): {_fmt_amt(total_account_usdc)} USDC",
        ]
        if clob_collateral_usdc is not None:
            lines.append(f"Colateral CLOB: {_fmt_amt(float(clob_collateral_usdc))} USDC")
            if positions_market_value_usdc is not None:
                lines.append(
                    f"Valor de Mercado Posições: {_fmt_amt(float(positions_market_value_usdc))} USDC"
                )
            elif (positions_error_zh or "").strip():
                lines.append(
                    f"Valor de Mercado: (Não incluído: {(positions_error_zh or '').strip()[:120]})"
                )
        lines.extend(
            [
                f"Saldo Disponível (Pode abrir ordens): {_fmt_amt(available_balance_usdc)} USDC",
                f"Bloqueado em ordens de compra: {_fmt_amt(locked_open_buy_usdc)} USDC",
                pnl_line,
            ]
        )
        if (extra_note_zh or "").strip():
            lines.append((extra_note_zh or "").strip())
        text = "\n".join(lines)
        fp = stable_fingerprint("startup_balance", text)
        self.send_message(text, event_key="startup:account_balance", payload_hash=fp)

    def notify_periodic_account_summary(
        self,
        *,
        slot_key: str,
        time_label: str,
        total_account_usdc: float,
        available_balance_usdc: float,
        deposited_reference_usdc: Optional[float],
        pnl_usdc: Optional[float],
        clob_collateral_usdc: Optional[float] = None,
        positions_market_value_usdc: Optional[float] = None,
        positions_error_zh: str = "",
    ) -> None:
        if deposited_reference_usdc is None:
            ref_line = "Ref Depósito: Não configurado"
            pnl_line = "PnL (Relativo ao depósito): Não calculado"
        else:
            ref_line = f"Ref Depósito: {_fmt_amt(deposited_reference_usdc)} USDC"
            p = float(pnl_usdc or 0.0)
            sign = "+" if p >= 0 else ""
            pnl_line = f"PnL (Relativo ao depósito): {sign}{_fmt_amt(p)} USDC"
        lines = [
            f"[{self._account_label}]",
            f"Resumo Periódico ({time_label})",
            f"Total da Conta (Portfolio aprox): {_fmt_amt(total_account_usdc)} USDC",
        ]
        if clob_collateral_usdc is not None:
            lines.append(f"Colateral CLOB: {_fmt_amt(float(clob_collateral_usdc))} USDC")
            if positions_market_value_usdc is not None:
                lines.append(
                    f"Valor de Mercado Posições: {_fmt_amt(float(positions_market_value_usdc))} USDC"
                )
            elif (positions_error_zh or "").strip():
                lines.append(
                    f"Valor de Mercado: (Não incluído: {(positions_error_zh or '').strip()[:100]})"
                )
        lines.extend(
            [
                f"Saldo Disponível (CLOB aprox): {_fmt_amt(available_balance_usdc)} USDC",
                ref_line,
                pnl_line,
            ]
        )
        text = "\n".join(lines)
        fp = stable_fingerprint("periodic", slot_key, text)
        self.send_message(text, event_key=f"periodic:summary:{slot_key}", payload_hash=fp)

    def notify_order_cancelled_chinese(
        self,
        *,
        order_id_short: str,
        market_title: str,
        outcome: str,
        price: float,
        size: float,
        category_zh: str,
        detail_zh: str,
        raw_reason: str,
    ) -> None:
        lines = [
            f"[{self._account_label}]",
            "Evento: Ordem Cancelada",
            f'Mercado: "{market_title}"',
            f"Direção: {outcome or '—'}",
            f"Preço: {_fmt_amt(price)}",
            f"Volume: {size:g}",
            f"Categoria do Cancelamento: {category_zh}",
            f"Detalhe: {detail_zh}",
            f"Código da Estratégia: {raw_reason}",
            f"Ordem: {order_id_short}",
        ]
        text = "\n".join(lines)
        fp = stable_fingerprint(text)
        oid_key = (order_id_short or "unknown").replace(":", "_")[:24]
        self.send_message(
            text,
            event_key=f"cancel:order:{oid_key}:{raw_reason[:40]}",
            payload_hash=fp,
        )

    def notify_order_band_summary(
        self,
        *,
        time_label: str,
        interval_sec: float,
        lines: list[str],
        time_bucket: int,
    ) -> None:
        """Lista periódica de ordens: distância do mid como fração de δ."""
        n = len(lines)
        header = [
            f"[{self._account_label}]",
            f"Ordens em relação ao Mid (em % de δ) · a cada {interval_sec:g}s",
            f"Hora: {time_label}",
            f"Total: {n} itens",
        ]
        body = lines if lines else ["(Sem detalhes)"]
        text = "\n".join(header + [""] + body)
        fp = stable_fingerprint("band_summary", time_bucket, text)
        self.send_message(
            text,
            event_key=f"periodic:band_summary:{time_bucket}",
            payload_hash=fp,
        )

    def notify_coarse_tick_abandon(
        self,
        *,
        market_title: str,
        outcome: str,
        token_id: str,
        n_candidates: int,
        reason_code: str,
        candidate_prices: Optional[list[float]] = None,
        mid: Optional[float] = None,
        coarse_range_lo_hi: Optional[tuple[float, float]] = None,
        tick_size: Optional[float] = None,
        reward_band_delta: Optional[float] = None,
    ) -> None:
        """Risco muito alto no tick grosso — cancelando sem repostar."""
        prices = candidate_prices or []
        prices_fmt = ", ".join(f"{p:.4f}" for p in prices) if prices else ""
        prices_line = (
            f"Níveis válidos encontrados: {prices_fmt}" if prices_fmt else "Níveis válidos: (Nenhum)"
        )
        range_line = ""
        if coarse_range_lo_hi is not None:
            lo, hi = coarse_range_lo_hi
            range_line = f"\nIntervalo estatístico [lo,hi]: [{lo:.4f}, {hi:.4f}]"
        mid_line = f"\nmid: {mid:.4f}" if mid is not None else ""
        tick_line = f"\ntick_size: {tick_size}" if tick_size is not None else ""
        delta_line = (
            f"\nBanda δ: {reward_band_delta:.4f}"
            if reward_band_delta is not None
            else ""
        )
        body = (
            f"O risco neste mercado é muito alto no momento (apenas {n_candidates} níveis válidos), abandonando posição.\n"
            f"Conta: {self._account_label}\n"
            f"Mercado: {market_title}\n"
            f"Direção: {outcome or '—'}\n"
            f"token_id: {token_id}\n"
            f"{prices_line}\n"
            f"Qtd níveis válidos: {n_candidates}"
            f"{mid_line}{range_line}{tick_line}{delta_line}\n"
            f"Razão: {pricing_adjustment_reason_zh(reason_code)}"
        )
        fp = stable_fingerprint(
            "coarse_abandon",
            token_id,
            n_candidates,
            reason_code,
            prices_fmt,
            mid,
            coarse_range_lo_hi,
            tick_size,
            reward_band_delta,
        )
        self.send_message(
            body,
            event_key=f"coarse_abandon:{token_id}:{n_candidates}",
            payload_hash=fp,
        )

    def notify_passive_fill_risk_alert(
        self,
        *,
        market_title: str,
        outcome: str,
        token_id: str,
        side: str,
        fill_rate: float,
        short_trades: int,
        long_trades: int,
        fill_risk_score: float,
        direction_en: str,
        reasons: list[str],
    ) -> None:
        reason_s = ",".join(reasons) if reasons else "—"
        lines = [
            f"[{self._account_label}]",
            "[Alerta de Risco de Execução]",
            f'Mercado: "{market_title}"',
            f"Token: {token_id}",
            f"Direção: {outcome or side}",
            f"Lado da Ordem: {str(side).upper()}",
            f"Taxa de execução: {fill_rate:.4f}",
            f"Trades recentes (curto): {short_trades}",
            f"Trades recentes (longo): {long_trades}",
            f"Score de risco: {fill_risk_score:.4f}",
            f"Direção: {direction_en}",
            f"Gatilhos: {reason_s}",
            'Mensagem: "A atividade aumentou, alto risco de execução imediata."',
        ]
        text = "\n".join(lines)
        fp = stable_fingerprint("passive_fill_risk", text)
        tid = (token_id or "na")[:40].replace(":", "_")
        self.send_message(
            text,
            event_key=f"monitor:fill_risk:{tid}:{fp[:20]}",
            payload_hash=fp,
        )

    def notify_passive_depth_risk_alert(
        self,
        *,
        market_title: str,
        outcome: str,
        token_id: str,
        order_id_short: str,
        band_lo: float,
        band_hi: float,
        total_depth: float,
        closer_depth: float,
        depth_ratio: float,
    ) -> None:
        pct = depth_ratio * 100.0
        lines = [
            f"[{self._account_label}]",
            "[Alerta de Risco de Profundidade]",
            f'Mercado: "{market_title}"',
            f"Token: {token_id}",
            f"Ordem: {order_id_short}",
            f"Outcome: {outcome or '—'}",
            f"Banda: [{band_lo:.4f}, {band_hi:.4f}]",
            f"Profundidade Total: {total_depth:.4f}",
            f"Profundidade na sua frente: {closer_depth:.4f}",
            f"Ratio: {pct:.1f}%",
            'Mensagem: "Muitas ordens à frente da sua na banda, risco de competição alto."',
        ]
        text = "\n".join(lines)
        fp = stable_fingerprint("passive_depth_risk", text)
        oid_key = (order_id_short or "na")[:40].replace(":", "_")
        self.send_message(
            text,
            event_key=f"monitor:depth_risk:{oid_key}:{fp[:20]}",
            payload_hash=fp,
        )


def build_telegram_notifier_from_env() -> TelegramNotifier:
    load = __import__("dotenv", fromlist=["load_dotenv"])
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    load.load_dotenv(root / ".env", override=False)

    def b(name: str, default: bool) -> bool:
        v = os.environ.get(name)
        if v is None or v == "":
            return default
        return v.strip().lower() in ("1", "true", "yes", "on")

    def f(name: str, default: float) -> float:
        v = os.environ.get(name)
        if v is None or v == "":
            return default
        return float(v)

    enabled = b("TELEGRAM_ENABLED", False)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    label = os.environ.get("TELEGRAM_ACCOUNT_LABEL", "Polymarket").strip()
    cooldown = f("TELEGRAM_NOTIFY_COOLDOWN_SEC", 30.0)

    return TelegramNotifier(
        enabled=enabled,
        bot_token=token,
        chat_id=chat,
        account_label=label,
        cooldown_sec=cooldown,
    )
