# Claude Sentinel 🛡️

**Claude Sentinel** is an administrative telemetry dashboard and autonomous background daemon engineered to monitor and manage user interactions across both **Claude Chat (Web Interface)** and **Claude Code (CLI/Terminal)**.

---

## Key Features

1. **Strict API Connection Gating & Security Lock**:
   - Until and unless the user links and validates their Anthropic Claude account, **all telemetry graphs, token burn-rate charts, and model tracking are strictly hidden/locked**.
   - The model badge remains in `Model: API Disconnected` state.
   - Any attempt to access limit configurations, reset tallies, or run unverified actions displays a direct warning dialog: *"The task could not be performed until and unless your software is connected with your Anthropic Claude API."*
   - Once authenticated, the dashboard automatically unlocks the full live telemetry cards, model identifiers, and interactive charts.
2. **Dual-Environment Detection**:
   - **Claude Chat**: Real-time browser tab and window title inspection across Chrome, Edge, Firefox, Safari, Brave, Arc, identifying active `claude.ai/chat/*` sessions.
   - **Claude Code**: Live terminal and IDE process scanning (`powershell`, `cmd`, `Terminal`, `iTerm`, `VS Code`, `Cursor`), paired with active JSONL session log tailing in `~/.claude/projects/*/*.jsonl`.
3. **Precision Token Telemetry**:
   - Extracts exact Model IDs (e.g. `claude-3-5-sonnet-20241022`, `claude-opus-5[1m]`, `claude-3-5-haiku`).
   - Detailed metric breakdown: Input Tokens, Output Tokens, Cache Creation Tokens, Cache Retrieval Tokens, Total Tokens, and Estimated Cost in USD.
4. **Hard Limit & Quota Enforcement**:
   - Hourly/Daily token ceilings, daily dollar budgets ($), and hourly call rate limits.
   - **Throttling Actions**:
     - **Claude Code**: Automatically terminates or suspends `claude` CLI sub-processes and writes an overt alert banner to `~/.claude/LIMIT_EXCEEDED_WARNING.txt`.
     - **Claude Chat**: Triggers a persistent floating HUD alert banner (`AlertOverlay`) and optionally blocks HTTP/HTTPS routing at the local proxy gate.
5. **Modern Cyberpunk Administrative Dashboard**:
   - Sleek cyberpunk / dark glassmorphism palette (`#0b0c10`, `#131622`, `#00d2d3`, `#ffa502`, `#10ac84`).
   - Glowing real-time pulse indicator showing active state (Claude Chat vs Claude Code vs Idle vs API Disconnected).
   - Dynamic quota progress bar with automatic green -> amber -> red color grading.
   - Real-time 2-second activity timeline stream.
   - System tray minimization.
6. **Security & Persistence**:
   - SQLite3 with WAL mode and thread-safe pooled connections.
   - Hardware-bound symmetric key encryption (PBKDF2 + AES-GCM) for Anthropic API keys.

---

## How to Test and Verify

### Method 1: Launch the Administrative Dashboard
```powershell
sentinel
```
*(Or `python c:\Users\ADMIN\Desktop\CODE\claude_sentinel\run_sentinel.py`)*

1. Notice that before connecting your API key, the dashboard displays the **"ANTHROPIC API NOT CONNECTED"** lock screen. All graphs and model tags are disabled.
2. Try clicking on **Quota & Hard Limits** or updating limits: the software immediately alerts you that the task cannot be performed until the API is connected.
3. In the **Account & Security** tab, enter your key or demo key `sk-ant-demo` and click **Connect & Unlock Software**.
4. The dashboard unlocks immediately, revealing the live token burn-rate graph, telemetry cards, and active model tracking!

### Method 2: Run the Automated Unit Test Suite (17 Tests)
```powershell
python -m unittest discover -s c:\Users\ADMIN\Desktop\CODE\claude_sentinel\tests
```
*(Covers API gating, database transactions, pricing, JSONL parsing, window classifiers, and quota enforcement).*

### Method 3: Run the Live Verification & Simulation Script
```powershell
python c:\Users\ADMIN\Desktop\CODE\claude_sentinel\verify_simulation.py
```

### Method 4: Run Headless Daemon
```powershell
sentinel --headless
```
