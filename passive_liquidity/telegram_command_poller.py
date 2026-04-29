"""
Thread para processar comandos do Telegram via polling; lida com /status, /orders, /pnl.

Isolado do loop principal de trading; falhas aqui não afetam a lógica das ordens.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from passive_liquidity.custom_pricing_rules_store import CustomPricingRulesStore
from passive_liquidity.order_manager import OrderManager
from passive_liquidity.orderbook_fetcher import OrderBookFetcher
from passive_liquidity.reward_monitor import RewardMonitor
from passive_liquidity.simple_price_policy import CustomPricingSettings
from passive_liquidity.telegram_live_queries import (
    get_live_account_status,
    get_live_order_summary,
    get_live_pnl,
)
from passive_liquidity.market_display import MarketDisplayResolver
from passive_liquidity.telegram_notifier import TelegramNotifier
from passive_liquidity.telegram_rule_setup import dispatch_command, handle_fsm_text

LOG = logging.getLogger(__name__)


def _commands_enabled_from_env() -> bool:
    v = os.environ.get("TELEGRAM_COMMANDS_ENABLED", "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    return True


def _chat_id_matches(msg_chat_id: Any, configured: str) -> bool:
    if msg_chat_id is None or not configured:
        return False
    return str(msg_chat_id).strip() == str(configured).strip()


def _get_updates(bot_token: str, offset: int, timeout_sec: int) -> list[dict]:
    params: dict[str, Any] = {"timeout": int(timeout_sec)}
    if offset > 0:
        params["offset"] = int(offset)
    q = urllib.parse.urlencode(params)
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates?{q}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout_sec + 5) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw) if raw else {}
    if not data.get("ok"):
        LOG.warning("getUpdates falhou: %s", raw[:500])
        return []
    return list(data.get("result") or [])


def _poll_loop(
    *,
    stop: threading.Event,
    notifier: TelegramNotifier,
    client: Any,
    order_manager: OrderManager,
    funder: str,
    poll_timeout_sec: int,
    rules_store: CustomPricingRulesStore,
    book_fetcher: OrderBookFetcher,
    reward_monitor: RewardMonitor,
    default_custom_settings: CustomPricingSettings,
    market_display: Optional[MarketDisplayResolver],
) -> None:
    token = notifier.bot_token
    expect_chat = notifier.chat_id
    offset = 0
    while not stop.is_set():
        try:
            updates = _get_updates(token, offset, poll_timeout_sec)
        except urllib.error.HTTPError as e:
            LOG.warning("Erro HTTP no getUpdates do Telegram: %s", e)
            time.sleep(3.0)
            continue
        except Exception as e:
            LOG.warning("Falha no getUpdates do Telegram: %s", e)
            time.sleep(3.0)
            continue

        max_uid = 0
        for u in updates:
            try:
                max_uid = max(max_uid, int(u.get("update_id") or 0))
            except (TypeError, ValueError):
                pass

        for u in updates:
            msg = u.get("message") or u.get("edited_message")
            if not isinstance(msg, dict):
                continue
            chat = msg.get("chat") or {}
            if not _chat_id_matches(chat.get("id"), expect_chat):
                continue

            text = msg.get("text")
            if not isinstance(text, str):
                continue

            chat_id = str(chat.get("id"))
            stripped = text.strip()

            def _label(msg_body: str) -> str:
                return f"[{notifier.account_label}]\n{msg_body}"

            rule_slash = (
                "/set_rule",
                "/get_rule",
                "/clear_rule",
                "/cancel_rule_setup",
            )

            if stripped.startswith("/"):
                first_tok = stripped.split(None, 1)[0]
                cmd_base = first_tok.split("@", 1)[0].lower()
                arg_rest = (
                    stripped.split(None, 1)[1].strip()
                    if len(stripped.split(None, 1)) > 1
                    else ""
                )

                if cmd_base in rule_slash:
                    LOG.info("Comando de regra do Telegram: %s", cmd_base)
                    try:
                        reply = dispatch_command(
                            chat_id,
                            cmd_base,
                            arg_rest,
                            client=client,
                            order_manager=order_manager,
                            book_fetcher=book_fetcher,
                            store=rules_store,
                            default_settings=default_custom_settings,
                        )
                    except Exception as e:
                        LOG.exception("Erro no comando de regra do Telegram")
                        reply = f"⚠️ Exceção no processamento do comando: {e}"
                    if reply:
                        notifier.send_command_reply(_label(reply))
                    continue

                if cmd_base in ("/input", "/answer"):
                    if not arg_rest.strip():
                        notifier.send_command_reply(
                            _label(
                                "Uso: /input <resposta>\n"
                                "O mesmo que enviar uma mensagem direta, por exemplo: `/input 2`, `/input sim`, `/input 0.4`, `/input confirmar`.\n"
                                "Use este comando se o Bot não responder a números ou mensagens de texto puras."
                            )
                        )
                        continue
                    fsm_reply = handle_fsm_text(
                        chat_id,
                        arg_rest,
                        store=rules_store,
                        default_settings=default_custom_settings,
                    )
                    if fsm_reply is not None:
                        notifier.send_command_reply(_label(fsm_reply))
                    else:
                        notifier.send_command_reply(
                            _label(
                                "Não há configuração de regra em andamento. Use /set_rule <order_id> primeiro."
                            )
                        )
                    continue

                cmd = cmd_base
                LOG.info("Comando Telegram recebido: %s", cmd)

                try:
                    if cmd == "/status":
                        ok, body = get_live_account_status(
                            client=client,
                            order_manager=order_manager,
                            funder=funder,
                            account_label=notifier.account_label,
                        )
                    elif cmd == "/orders":
                        ok, body = get_live_order_summary(
                            client=client,
                            order_manager=order_manager,
                            market_display=market_display,
                            book_fetcher=book_fetcher,
                            reward_monitor=reward_monitor,
                        )
                    elif cmd == "/cancel":
                        arg = arg_rest.strip()
                        if not arg:
                            ok, body = False, "Uso: /cancel <order_id|all>"
                        elif arg.lower() == "all":
                            try:
                                orders = order_manager.fetch_all_open_orders(client)
                            except Exception as e:
                                ok, body = False, f"Falha ao buscar ordens abertas: {e}"
                            else:
                                total = 0
                                failed = 0
                                for o in orders:
                                    oid = str(o.get("id") or o.get("orderID") or "").strip()
                                    if not oid:
                                        continue
                                    total += 1
                                    try:
                                        client.cancel(oid)
                                    except Exception:
                                        failed += 1
                                if total == 0:
                                    ok, body = True, "Nenhuma ordem aberta para cancelar."
                                elif failed == 0:
                                    ok, body = True, f"Cancelamento de todas as ordens enviado ({total} ordens)."
                                else:
                                    ok, body = False, f"Cancelamento concluído: Sucesso {total - failed}/{total}, Falha {failed}."
                        else:
                            oid = arg
                            try:
                                client.cancel(oid)
                                ok, body = True, f"Cancelamento da ordem enviado: {oid[:48]}…"
                            except Exception as e:
                                ok, body = False, f"Falha ao cancelar: {e}"
                    elif cmd == "/pnl":
                        ok, body = get_live_pnl(
                            client=client,
                            order_manager=order_manager,
                            funder=funder,
                            account_label=notifier.account_label,
                        )
                    elif cmd in ("/start", "/help"):
                        body = (
                            "Comandos disponíveis (consulta em tempo real):\n"
                            "/status — Visão geral da conta e ordens\n"
                            "/orders — Resumo das ordens abertas\n"
                            "/cancel <order_id|all> — Cancelar ordem específica ou todas\n"
                            "/pnl — Lucros e perdas\n"
                            "\nAjuste Personalizado (Regras salvas por token_id + direção):\n"
                            "/set_rule <order_id> — Configuração interativa\n"
                            "/input <resposta> — Enviar resposta para o passo atual\n"
                            "/get_rule <order_id> — Ver regra salva\n"
                            "/clear_rule <order_id> — Remover regra personalizada\n"
                            "/cancel_rule_setup — Cancelar configuração em andamento\n"
                        )
                        ok = True
                    else:
                        continue

                    if not ok:
                        body = f"⚠️ {body}"
                    notifier.send_command_reply(_label(body))
                except Exception as e:
                    LOG.exception("Erro no handler de comando do Telegram: %s", e)
                    notifier.send_command_reply(
                        _label(f"⚠️ Exceção no processamento do comando: {e}")
                    )
                continue

            fsm_reply = handle_fsm_text(
                chat_id,
                text,
                store=rules_store,
                default_settings=default_custom_settings,
            )
            if fsm_reply is not None:
                notifier.send_command_reply(_label(fsm_reply))

        if max_uid > 0:
            offset = max_uid + 1


def start_telegram_command_poller(
    *,
    notifier: TelegramNotifier,
    client: Any,
    order_manager: OrderManager,
    funder: str,
    stop: threading.Event,
    rules_store: CustomPricingRulesStore,
    book_fetcher: OrderBookFetcher,
    reward_monitor: RewardMonitor,
    default_custom_settings: CustomPricingSettings,
    market_display: Optional[MarketDisplayResolver] = None,
) -> Optional[threading.Thread]:
    if not notifier.enabled:
        LOG.info("Polling de comandos do Telegram ignorado (notificações desativadas)")
        return None
    if not _commands_enabled_from_env():
        LOG.info("Polling de comandos do Telegram ignorado (TELEGRAM_COMMANDS_ENABLED=off)")
        return None

    def _timeout() -> int:
        try:
            v = int(os.environ.get("TELEGRAM_COMMAND_POLL_TIMEOUT", "25"))
        except ValueError:
            v = 25
        return max(1, min(50, v))

    poll_timeout = _timeout()

    def _run() -> None:
        LOG.info(
            "Polling de comandos do Telegram iniciado (timeout=%ds, chat_id=%s)",
            poll_timeout,
            notifier.chat_id[:12] + "…" if len(notifier.chat_id) > 12 else notifier.chat_id,
        )
        _poll_loop(
            stop=stop,
            notifier=notifier,
            client=client,
            order_manager=order_manager,
            funder=funder,
            poll_timeout_sec=poll_timeout,
            rules_store=rules_store,
            book_fetcher=book_fetcher,
            reward_monitor=reward_monitor,
            default_custom_settings=default_custom_settings,
            market_display=market_display,
        )
        LOG.info("Polling de comandos do Telegram parado")

    t = threading.Thread(
        target=_run,
        name="telegram-commands",
        daemon=True,
    )
    t.start()
    return t
