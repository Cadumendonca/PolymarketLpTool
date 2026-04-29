# Polymarket Order Monitoring (Liquidity Rewards) / Monitoramento de Ordens Polymarket

[English](#english) | [Português](#português)

---

<a name="english"></a>
# English

# If you want to help by donating Polygon USDC or Polygon MATIC: 0xf6C7F0C6cDdE13033fF9fC05798B00891f7Ee059

Python program for **Price Monitoring and Adjustment**: You place **orders manually** on the [Polymarket](https://docs.polymarket.com/api-reference/introduction) front-end, and this program **will not create new orders**. It only queries the **open orders** under your API key and follows a **simplified rule** based on the **Orderbook + Incentive Band δ** to decide between **Keep / Cancel / Re-post with the same size and adjusted price**.

This is not an automatic market making bot that creates orders from scratch.

Feel free to use the author's referral link on PolyMarket (30% rebate): https://polymarket.com/?r=unflux

## Current Strategy Summary (Main Loop)

1.  **Whitelist**: If `PASSIVE_TOKEN_WHITELIST` is configured, only tokens on this list will be monitored. Otherwise, the bot extracts `token_id` from current open orders and updates the list every **120 seconds** (`PASSIVE_WHITELIST_REFRESH_SEC`) by default. If set to `0`, the list is loaded only at startup.
2.  **Filtering**: The bot only manages tokens on the whitelist. If you **already have a position** (inventory) in the `token_id` (`abs(inventory) > 1e-8`), the bot will **completely ignore this token** (no cancels, no adjustments, no execution alerts or periodic summaries).
3.  **Price Adjustment**: Uses only the `decide_simple_price` logic in `passive_liquidity/simple_price_policy.py` (coarse tick / fine tick). If there is a **JSON custom rule** via Telegram or Web Panel, or if the order ID is in **`PASSIVE_CUSTOM_ORDER_IDS`**, or if **`PASSIVE_DEFAULT_CUSTOM_PRICING`** is enabled, the bot enters **custom** mode (using `PASSIVE_CUSTOM_*` settings from `.env`). Old logic (like `AdjustmentEngine`, structural risk, etc.) is no longer used by the main loop.
4.  **Execution**: `OrderManager.apply_decision` (cancels, waits for an optional delay, and re-posts the order. Failures in re-posting can be retried infinitely or limited per configuration).
5.  **Optional**: Execution alerts via Telegram, financial summaries every half-hour, and periodic summaries of **band + orderbook depth**.

## Price Adjustment Rules (`simple_price_policy`)

**Tick Classification**

*   **Coarse Tick**: `tick ≈ 0.01` or `≈ 1.0` (depending on API representation).
*   **Fine Tick**: `tick ≈ 0.001` or `≈ 0.1`.
*   **Others**: **Keeps** without adjusting price.

**Coarse Tick**

*   For **BUY looks at bids / SELL looks at asks**, price level statistics with **positive depth** inside the incentive band.
*   **Range**: `band = floor(δ/tick)×tick`; BUY **`[mid−band, mid]`**, SELL **`[mid, mid+band]`** (δ comes from CLOB rewards).
*   **Price Levels ≤ 2**: Cancels the order and does not re-post (sends "very high risk, abandoning position" alert).
*   **3 Levels**: Chooses the level with the **intermediate distance** from the mid price.
*   **4 Levels**: Chooses the **second furthest level** from the mid.
*   **>4 Levels**: Default is the **second furthest level**.
*   If the difference to the target price is smaller than the **minimum replacement tick**: Keeps.

**Fine Tick**

*   `distance_ratio = |price−mid|/δ`.
*   **[0.4, 0.6]**: Keeps.
*   **< 0.4**: Moves outward to **0.5 × δ**.
*   **> 0.6**: Moves inward to **0.5 × δ**.
*   If the change is smaller than the minimum tick: Keeps (with reason code `_noop_small_delta`).

Reason codes in order events and Telegram messages are displayed with **Portuguese descriptions** (after mapping file translation).

## Custom Adjustment (Telegram / Web / Env)

In addition to the default rules, you can pin a logic for a **specific token + direction**. The **Telegram `/set_rule` command and the Web Panel orders page** write to the same `custom_pricing_rules.json` file (path configured in **`PASSIVE_CUSTOM_RULES_PATH`**).

**Execution Priority (for the same order)**

1.  Saved rule for **`token_id` + `BUY`/`SELL`** (via Telegram or Web) → Uses the persistent JSON rule.
2.  Otherwise, if **`PASSIVE_DEFAULT_CUSTOM_PRICING=true`** → Uses **`PASSIVE_CUSTOM_*` parameters from `.env`** as the global default.
3.  Otherwise, if the order ID is in **`PASSIVE_CUSTOM_ORDER_IDS`** → Uses the same **`PASSIVE_CUSTOM_*`** parameters.
4.  Otherwise → Uses the **native** coarse/fine tick strategy.

**Telegram Interaction** (Requires `TELEGRAM_ENABLED=true`)

| Command | Action |
| --- | --- |
| **`/set_rule <order_id>`** | Starts configuration for an open order. |
| **`/get_rule <order_id>`** | Displays the saved rule summary for that order. |
| **`/clear_rule <order_id>`** | Deletes the custom rule and reverts to default adjustment. |
| **`/cancel_rule_setup`** | Cancels an ongoing configuration session. |
| **`/input <response>`** | Sends a response for the current configuration step. |

## Architecture (Modules)

| Module | File | Responsibility |
| --- | --- | --- |
| **MainLoop** | `passive_liquidity/main_loop.py` | Main loop; whitelist, position filter, price adjustment, Telegram triggers |
| **SimplePricePolicy** | `passive_liquidity/simple_price_policy.py` | Price decision; band depth statistics |
| **OrderManager** | `passive_liquidity/order_manager.py` | Fetches orders, applies decisions, and handles error retries |
| **RewardMonitor** | `passive_liquidity/reward_monitor.py` | Monitors δ (band width) and scoring status |
| **OrderBookFetcher** | `passive_liquidity/orderbook_fetcher.py` | Fetches orderbook and mid price |
| **TelegramNotifier** | `passive_liquidity/telegram_notifier.py` | Notifications, operational alerts, and reason mapping |
| **Web Panel** | `passive_liquidity/web_panel/` | Optional Flask interface for overview and rule editing |

## Installation

```bash
cd polymarket_lp_tool
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

To run:
```bash
./.venv/bin/python run_passive_bot.py
```

## Environment Variables (`.env`)

Create the `.env` file from the example:
```bash
cp .env.example .env
```

Fill in at least **`PRIVATE_KEY`** and **`POLYMARKET_FUNDER`**.

### Main Variables (`PASSIVE_*`)

| Variable | Meaning |
| --- | --- |
| **`PASSIVE_LOOP_INTERVAL`** | Interval between main loop cycles (seconds) |
| **`PASSIVE_TOKEN_WHITELIST`** | Comma-separated token IDs (empty = automatic) |
| **`PASSIVE_MAX_API_ERRORS`** | Consecutive API errors before `cancel_all` (0 = disabled) |

### Telegram

| Variable | Meaning |
| --- | --- |
| **`TELEGRAM_BOT_TOKEN`** | Token obtained from @BotFather |
| **`TELEGRAM_CHAT_ID`** | Your ID or group ID (use @userinfobot to find out) |
| **`PASSIVE_TELEGRAM_NOTIFY_FILL`** | Enables fill notification alerts (partial or full) |

## Execution

1. Place your limit orders manually on Polymarket.
2. Start the program; if there are no open orders, it will wait (idle).

```bash
python run_passive_bot.py
```

### Web Panel (Optional)

1. Set **`WEB_PANEL_TOKEN`** in `.env` (your login password).
2. Start:
```bash
python run_web_panel.py
```
Access `http://127.0.0.1:8765` in your browser.

## Disclaimer

This is an unofficial project by `@臭臭Panda`. Use at your own risk. No guarantees of profit or correct scoring. Test with small amounts before using at scale.

---

<a name="português"></a>
# Português

# Se quiser ajudar doando Polygon USDC ou Polygon MATIC: 0xf6C7F0C6cDdE13033fF9fC05798B00891f7Ee059

Programa em Python para **Monitoramento e Ajuste de Preços**: Você coloca as **ordens manualmente** no front-end da [Polymarket](https://docs.polymarket.com/api-reference/introduction), e este programa **não criará novas ordens**. Ele apenas consulta as **ordens abertas** sob a sua chave de API e segue uma **regra simplificada** baseada no **Orderbook + Banda de Incentivo δ** para decidir entre **Manter / Cancelar / Re-alocar com o mesmo volume e preço ajustado**.

Este não é um bot de market making automático que cria ordens do zero.

Sinta-se à vontade para usar o link de registro do autor na PolyMarket (30% de rebate): https://polymarket.com/?r=unflux

## Resumo da Estratégia Atual (Loop Principal)

1.  **Whitelist (Lista Branca)**: Se `PASSIVE_TOKEN_WHITELIST` estiver configurado, apenas os tokens nessa lista serão monitorados. Caso contrário, o bot extrai os `token_id` das ordens abertas atuais e atualiza a lista a cada **120 segundos** (`PASSIVE_WHITELIST_REFRESH_SEC`) por padrão. Se definido como `0`, a lista é carregada apenas na inicialização.
2.  **Filtragem**: O bot gerencia apenas ordens da whitelist. Se você **já possuir uma posição** (estoque) no `token_id` (`abs(inventory) > 1e-8`), o bot **ignorará este token completamente** (não cancela, não ajusta, não envia alertas de execução ou resumos periódicos).
3.  **Ajuste de Preço**: Utiliza apenas a lógica `decide_simple_price` em `passive_liquidity/simple_price_policy.py` (tick grosso / tick fino). Se houver uma **regra personalizada JSON** via Telegram ou Painel Web, ou se o ID da ordem estiver em **`PASSIVE_CUSTOM_ORDER_IDS`**, ou se **`PASSIVE_DEFAULT_CUSTOM_PRICING`** estiver ativado, o bot entra no modo **custom** (usando as configurações `PASSIVE_CUSTOM_*` do `.env`). Lógicas antigas (como `AdjustmentEngine`, risco estrutural, etc.) não são mais usadas pelo loop principal.
4.  **Execução**: `OrderManager.apply_decision` (cancela, aguarda um atraso opcional e reposta a ordem. Falhas na repostagem podem ser tentadas infinitamente ou limitadas conforme a configuração).
5.  **Opcionais**: Alertas de execução via Telegram, resumos financeiros a cada meia hora e resumos periódicos de **banda + profundidade do orderbook**.

## Regras de Ajuste de Preço (`simple_price_policy`)

**Classificação de Tick**

*   **Tick Grosso (Coarse)**: `tick ≈ 0.01` ou `≈ 1.0` (dependendo da representação da API).
*   **Tick Fino (Fine)**: `tick ≈ 0.001` ou `≈ 0.1`.
*   **Outros**: **Mantém** sem ajustar o preço.

**Tick Grosso**

*   Para **BUY (Compra) olha os bids / SELL (Venda) olha os asks**, estatística de níveis de preço com **profundidade positiva** dentro da banda de incentivo.
*   **Intervalo**: `band = floor(δ/tick)×tick`; COMPRA **`[mid−band, mid]`**, VENDA **`[mid, mid+band]`** (δ vem das recompensas do CLOB).
*   **Níveis de Preço ≤ 2**: Cancela a ordem e não reposta (envia alerta de "risco muito alto, abandonando posição").
*   **3 Níveis**: Escolhe o nível com a **distância intermediária** em relação ao preço médio (mid).
*   **4 Níveis**: Escolhe o **segundo nível mais distante** do mid.
*   **>4 Níveis**: Padrão é o **segundo nível mais distante**.
*   Se a diferença para o preço alvo for menor que o **tick mínimo de substituição**: Mantém.

**Tick Fino**

*   `distance_ratio = |preço−mid|/δ`.
*   **[0.4, 0.6]**: Mantém.
*   **< 0.4**: Move para fora até **0.5 × δ**.
*   **> 0.6**: Move para dentro até **0.5 × δ**.
*   Se a mudança for menor que o tick mínimo: Mantém (com código de razão `_noop_small_delta`).

Os códigos de razão nos eventos de ordem e mensagens do Telegram são exibidos com **descrições em português** (após a tradução do arquivo de mapeamento).

## Ajuste Personalizado (Telegram / Web / Env)

Além das regras padrão, você pode fixar uma lógica para um **token + direção específico**. O **comando `/set_rule` do Telegram e a página de ordens do Painel Web** gravam no mesmo arquivo `custom_pricing_rules.json` (caminho configurado em **`PASSIVE_CUSTOM_RULES_PATH`**).

**Prioridade de Execução (para a mesma ordem)**

1.  Regra salva para o **`token_id` + `BUY`/`SELL`** (via Telegram ou Web) → Usa a regra JSON persistente.
2.  Caso contrário, se **`PASSIVE_DEFAULT_CUSTOM_PRICING=true`** → Usa os parâmetros **`PASSIVE_CUSTOM_*` do `.env`** como padrão global.
3.  Caso contrário, se o ID da ordem estiver em **`PASSIVE_CUSTOM_ORDER_IDS`** → Usa os mesmos parâmetros **`PASSIVE_CUSTOM_*`**.
4.  Caso contrário → Usa a estratégia **nativa** de tick grosso/fino.

**Interação via Telegram** (Requer `TELEGRAM_ENABLED=true`)

| Comando | Ação |
| --- | --- |
| **`/set_rule <order_id>`** | Inicia a configuração para uma ordem aberta. |
| **`/get_rule <order_id>`** | Exibe o resumo da regra salva para aquela ordem. |
| **`/clear_rule <order_id>`** | Exclui a regra personalizada e volta ao ajuste padrão. |
| **`/cancel_rule_setup`** | Cancela uma sessão de configuração em andamento. |
| **`/input <resposta>`** | Envia uma resposta para o passo atual da configuração. |

## Arquitetura (Módulos)

| Módulo | Arquivo | Responsabilidade |
| --- | --- | --- |
| **MainLoop** | `passive_liquidity/main_loop.py` | Loop principal; whitelist, filtro de posição, ajuste de preço, gatilhos de Telegram |
| **SimplePricePolicy** | `passive_liquidity/simple_price_policy.py` | Decisão de preço; estatísticas de profundidade da banda |
| **OrderManager** | `passive_liquidity/order_manager.py` | Busca ordens, aplica decisões e trata retentativas de erro |
| **RewardMonitor** | `passive_liquidity/reward_monitor.py` | Monitora δ (largura da banda) e status de pontuação |
| **OrderBookFetcher** | `passive_liquidity/orderbook_fetcher.py` | Busca orderbook e preço médio (mid) |
| **TelegramNotifier** | `passive_liquidity/telegram_notifier.py` | Notificações, alertas operacionais e mapeamento de razões |
| **Web Panel** | `passive_liquidity/web_panel/` | Interface Flask opcional para visão geral e edição de regras |

## Instalação

```bash
cd polymarket_lp_tool
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Para rodar:
```bash
./.venv/bin/python run_passive_bot.py
```

## Variáveis de Ambiente (`.env`)

Crie o arquivo `.env` a partir do exemplo:
```bash
cp .env.example .env
```

Preencha ao menos **`PRIVATE_KEY`** e **`POLYMARKET_FUNDER`**.

### Principais Variáveis (`PASSIVE_*`)

| Variável | Significado |
| --- | --- |
| **`PASSIVE_LOOP_INTERVAL`** | Intervalo entre ciclos do loop principal (segundos) |
| **`PASSIVE_TOKEN_WHITELIST`** | IDs de token separados por vírgula (vazio = automático) |
| **`PASSIVE_MAX_API_ERRORS`** | Quantos erros de API seguidos antes de dar `cancel_all` (0 = desativado) |

### Telegram

| Variável | Significado |
| --- | --- |
| **`TELEGRAM_BOT_TOKEN`** | Token obtido no @BotFather |
| **`TELEGRAM_CHAT_ID`** | Seu ID ou ID do grupo (use @userinfobot para descobrir) |
| **`PASSIVE_TELEGRAM_NOTIFY_FILL`** | Ativa notificações de ordem executada (parcial ou total) |

## Execução

1. Coloque suas ordens limitadas manualmente na Polymarket.
2. Inicie o programa; se não houver ordens abertas, ele ficará em espera (idle).

```bash
python run_passive_bot.py
```

### Painel Web (Opcional)

1. Defina **`WEB_PANEL_TOKEN`** no `.env` (sua senha de login).
2. Inicie:
```bash
python run_web_panel.py
```
Acesse `http://127.0.0.1:8765` no navegador.

## Isenção de Responsabilidade

Este é um projeto não oficial de `@臭臭Panda`. Use por sua conta e risco. Não há garantias de lucro ou de pontuação correta. Teste com valores pequenos antes de usar em escala real.
