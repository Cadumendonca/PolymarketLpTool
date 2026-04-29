# 📊 Polymarket Order Monitoring & Liquidity Rewards
### *Monitoramento de Ordens e Recompensas de Liquidez Polymarket*

<div align="center">

![Python](https://img.shields.io/badge/python-3.10+-blue.svg?style=for-the-badge&logo=python&logoColor=white)
![Status](https://img.shields.io/badge/status-active-success.svg?style=for-the-badge)
![Platform](https://img.shields.io/badge/platform-Polymarket-informational.svg?style=for-the-badge)

[🇬🇧 English](#english) | [🇧🇷 Português](#português)

</div>

---

<a name="english"></a>
## 🇬🇧 English Version

> [!IMPORTANT]
> **Donations**: If you'd like to support the project, donations are welcome! 
> **Polygon USDC/MATIC**: `0xf6C7F0C6cDdE13033fF9fC05798B00891f7Ee059`

This Python program provides **Price Monitoring and Automated Adjustment** for your manual orders on Polymarket.
*   **Manual Entry**: You place orders via the [Polymarket UI](https://polymarket.com/).
*   **Automated Management**: The bot monitors open orders and adjusts prices based on the **Orderbook + Incentive Band (δ)**.
*   **Yield Hunter**: A new feature that scans all Polymarket markets to find the ones with the highest liquidity rewards and lowest spreads.
*   **Safety First**: It decides between **Keep / Cancel / Re-post** to maximize liquidity rewards while minimizing risk.

> [!NOTE]
> This is **not** a market-making bot that creates orders from scratch. It only manages orders you have already placed.

---

### 🚀 Quick Links
- **Author's Referral (30% rebate)**: [Register on Polymarket](https://polymarket.com/?r=unflux)
- **Official Documentation**: [Polymarket API](https://docs.polymarket.com/api-reference/introduction)

---

### 🧠 Strategy Summary (Main Loop)

1.  **🔍 Whitelist**: Automatically extracts `token_id` from open orders or uses `PASSIVE_TOKEN_WHITELIST`. Refreshes every 120s by default.
2.  **🛡️ Filtering**: Only manages tokens on the whitelist. **If you have a position** (inventory > 0), the bot **ignores** that token to prevent unintended exposure.
3.  **⚖️ Price Adjustment**: Uses `decide_simple_price` logic (Coarse vs. Fine ticks). Supports **JSON custom rules** set via Telegram or Web Panel.
4.  **⚡ Execution**: Cancels and re-posts orders with optional delays. Handles API errors with a built-in retry mechanism.
5.  **📊 Monitoring**: Real-time Telegram alerts, PnL summaries, and orderbook depth statistics.

---

### 📏 Adjustment Rules (`simple_price_policy`)

#### **Tick Classification**
- **Coarse Tick** (`≈ 0.01`): Used for markets with larger price increments.
- **Fine Tick** (`≈ 0.001`): Used for high-precision markets.

#### **Logic for Coarse Ticks**
| Depth | Action |
| :--- | :--- |
| **≤ 2 Levels** | **Cancel & Abandon** (High risk alert) |
| **3 Levels** | Pick **Intermediate** distance level |
| **4 Levels** | Pick **2nd furthest** level from mid |
| **> 4 Levels** | Pick **2nd furthest** level (Default) |

#### **Logic for Fine Ticks**
Uses `distance_ratio = |price−mid|/δ`.
- **0.4 - 0.6**: **Keep** (Safe zone).
- **Outside**: Move to **0.5 × δ**.

---

### 🤖 Telegram Commands
*Requires `TELEGRAM_ENABLED=true`*

| Command | Description |
| :--- | :--- |
| `/status` | View real-time bot status and account summary |
| `/orders` | List all open orders being managed |
| `/scan` | Scan for markets with the highest rewards 🎯 |
| `/pnl` | View current Profit and Loss (PnL) |
| `/set_rule <id>` | Start interactive setup for a specific order |
| `/get_rule <id>` | Show saved rule for a token+direction |
| `/clear_rule <id>` | Revert to default strategy for that order |
| `/input <val>` | Send input to the interactive setup |

---

### ⚙️ Installation & Setup

1.  **Clone & Environment**:
    ```bash
    git clone https://github.com/Cadumendonca/PolymarketLpTool.git
    cd PolymarketLpTool
    python3 -m venv .venv
    source .venv/bin/activate  # Or .venv\Scripts\activate on Windows
    pip install -r requirements.txt
    ```

2.  **Configure `.env`**:
    ```bash
    cp .env.example .env
    # Edit .env with your PRIVATE_KEY and POLYMARKET_FUNDER
    ```

3.  **Run**:
    ```bash
    python run_passive_bot.py
    ```

#### 🌐 Web Panel (Optional)
Access `http://127.0.0.1:8765` to manage rules visually.
```bash
python run_web_panel.py
```

---

### ⚠️ Disclaimer
This is an unofficial project by `@臭臭Panda`. Use at your own risk. Trading involves significant risk. Always test with small amounts first.

---
---

<a name="português"></a>
## 🇧🇷 Versão em Português

> [!IMPORTANT]
> **Doações**: Se você deseja apoiar o projeto, doações são muito bem-vindas!
> **Polygon USDC/MATIC**: `0xf6C7F0C6cDdE13033fF9fC05798B00891f7Ee059`

Programa em Python para **Monitoramento e Ajuste Automatizado** de suas ordens manuais na Polymarket.
*   **Entrada Manual**: Você coloca as ordens pela [Interface da Polymarket](https://polymarket.com/).
*   **Gestão Automática**: O bot monitora as ordens abertas e ajusta os preços com base no **Orderbook + Banda de Incentivo (δ)**.
*   **Yield Hunter**: Nova funcionalidade que varre todos os mercados da Polymarket para encontrar aqueles com maiores recompensas de liquidez e menores spreads.
*   **Segurança**: Decide entre **Manter / Cancelar / Repostar** para maximizar recompensas de liquidez minimizando riscos.

> [!NOTE]
> Este **não** é um bot de market-making que cria ordens do zero. Ele gerencia apenas ordens que você já abriu.

---

### 🚀 Links Rápidos
- **Referral do Autor (30% rebate)**: [Registrar na Polymarket](https://polymarket.com/?r=unflux)
- **Documentação Oficial**: [API Polymarket](https://docs.polymarket.com/api-reference/introduction)

---

### 🧠 Resumo da Estratégia (Loop Principal)

1.  **🔍 Whitelist**: Extrai automaticamente o `token_id` das ordens abertas ou usa `PASSIVE_TOKEN_WHITELIST`. Atualiza a cada 120s por padrão.
2.  **🛡️ Filtragem**: Gerencia apenas tokens na whitelist. **Se você tiver posição** (estoque > 0), o bot **ignora** esse token para evitar exposição indesejada.
3.  **⚖️ Ajuste de Preço**: Usa a lógica `decide_simple_price` (Ticks Grossos vs. Finos). Suporta **regras JSON customizadas** via Telegram ou Painel Web.
4.  **⚡ Execução**: Cancela e reposta ordens com atrasos opcionais. Trata erros de API com sistema de retentativa integrado.
5.  **📊 Monitoramento**: Alertas em tempo real no Telegram, resumos de PnL e estatísticas de profundidade do livro de ordens.

---

### 📏 Regras de Ajuste (`simple_price_policy`)

#### **Classificação de Tick**
- **Tick Grosso** (`≈ 0.01`): Usado em mercados com incrementos maiores.
- **Tick Fino** (`≈ 0.001`): Usado em mercados de alta precisão.

#### **Lógica para Ticks Grossos**
| Profundidade | Ação |
| :--- | :--- |
| **≤ 2 Níveis** | **Cancelar & Abandonar** (Alerta de alto risco) |
| **3 Níveis** | Escolher nível de distância **Intermediária** |
| **4 Níveis** | Escolher **2º nível mais distante** do mid |
| **> 4 Níveis** | Escolher **2º nível mais distante** (Padrão) |

#### **Lógica para Ticks Finos**
Usa `distance_ratio = |preço−mid|/δ`.
- **0.4 - 0.6**: **Manter** (Zona segura).
- **Fora**: Mover para **0.5 × δ**.

---

### 🤖 Comandos do Telegram
*Requer `TELEGRAM_ENABLED=true`*

| Comando | Descrição |
| :--- | :--- |
| `/status` | Ver status do bot e resumo da conta em tempo real |
| `/orders` | Listar todas as ordens abertas monitoradas |
| `/scan` | Escanear mercados com maiores recompensas 🎯 |
| `/pnl` | Ver Lucros e Perdas (PnL) atual |
| `/set_rule <id>` | Iniciar configuração interativa para uma ordem |
| `/get_rule <id>` | Ver regra salva para um token+direção |
| `/clear_rule <id>` | Voltar para a estratégia padrão para aquela ordem |
| `/input <val>` | Enviar resposta para a configuração interativa |

---

### ⚙️ Instalação e Configuração

1.  **Clone e Ambiente**:
    ```bash
    git clone https://github.com/Cadumendonca/PolymarketLpTool.git
    cd PolymarketLpTool
    python3 -m venv .venv
    source .venv/bin/activate  # No Windows use: .venv\Scripts\activate
    pip install -r requirements.txt
    ```

2.  **Configurar `.env`**:
    ```bash
    cp .env.example .env
    # Edite o .env com sua PRIVATE_KEY e POLYMARKET_FUNDER
    ```

3.  **Executar**:
    ```bash
    python run_passive_bot.py
    ```

#### 🌐 Painel Web (Opcional)
Acesse `http://127.0.0.1:8765` para gerenciar regras visualmente.
```bash
python run_web_panel.py
```

---

### ⚠️ Isenção de Responsabilidade
Este é um projeto não oficial de `@臭臭Panda`. Use por sua conta e risco. O trading envolve riscos significativos. Sempre teste com valores pequenos primeiro.
