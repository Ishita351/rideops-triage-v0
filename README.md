# RideOps L1 Triage & Escalation Gate (v0)

Working internal tool built for RideOps L1 Support Agents to stop avoidable Engineering escalations (56.6% of all escalations), prevent ping-pong handoffs, and cut open backlog from 48 to <=30 without new hires.

## What It Does
1. **Live Open Queue Triage:** Flags aging (>72h), reopened, and ping-pong bounced tickets from the live dataset.
2. **Smart Escalation Gate:** Matches incoming/open tickets against 1,444 historical records to catch Known-Issue Duplicates, enforces Top-3 category config self-checks, and blocks incomplete escalations until diagnostic fields are filled.
3. **2-Step Anti-Reopen Closure Guardrail:** Prevents premature closures before client verification.
4. **Weekly Ops Pulse & Backlog Simulator:** Tracks North Star + Guardrail KPIs, flags coaching outliers, and simulates Little's Law WIP reduction.

## Run Locally
```bash
pip install -r requirements.txt
streamlit run app.py