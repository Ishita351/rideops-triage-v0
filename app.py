import streamlit as st
import pandas as pd
import numpy as np
import re
from datetime import datetime

st.set_page_config(
    page_title="RideOps L1 Triage & Escalation Gate", layout="wide")

AVOIDABLE = [
    "Insufficient info - returned to L1",
    "Config issue - returned to L1",
    "Duplicate of known issue",
    "Not a bug - how-to",
    "Not reproducible",
]

PLAYBOOKS = {
    "Roster / booking not syncing": {
        "config_checks": [
            "Verified client's latest booking cutoff time matches shift template in Admin Panel",
            "Confirmed ad-hoc booking / cancellation sync cron ran (triggered manual re-sync if <15 mins old)",
            "Checked Excel roster upload for blank employee IDs, duplicate rows, or night-shift date rollover",
        ],
        "required_fields": ["Client ID & Shift Window", "Affected Employee ID(s) / Upload Batch ID", "Exact Timestamp (IST) & Sync Log Status"],
        "known_workaround": "Re-save cutoff rule under Client Config -> Shift Rules and click 'Force Roster Push' (resolves ~37% of roster escalations without Engineering).",
    },
    "Live tracking / GPS incorrect": {
        "config_checks": [
            "Checked Driver App GPS permission & last ping timestamp in Fleet Console",
            "Verified geofence pickup radius & security-team tracking link expiry/domain whitelist",
            "Confirmed cab is not in a low-network basement/parking zone (>5 mins stale ping)",
        ],
        "required_fields": ["Trip ID & Vehicle/Driver ID", "Last GPS Ping Timestamp vs Expected Location", "Screenshot of Employee App vs Fleet Map"],
        "known_workaround": "Trigger 'Refresh Trip Telemetry' in Ops Console and regenerate tracking URL for security desk (resolves ~35% of GPS escalations at L1).",
    },
    "Report export failing": {
        "config_checks": [
            "Checked if selected date range exceeds 15 days (known timeout limit -> split into 2 batches)",
            "Verified client billing email whitelist & scheduled report cron status",
            "Checked Known Issues Registry below before raising a duplicate Engineering ticket",
        ],
        "required_fields": ["Report Type & Exact Date Range", "Client ID & Admin Email", "Browser Console / Export Job ID"],
        "known_workaround": "KNOWN ISSUE (30 duplicate tickets): Exports >15 days or peak-hour monthly reports time out. Split into 10-day chunks or trigger async CSV export from Admin -> Reports -> Legacy Queue.",
    },
    "DEFAULT": {
        "config_checks": [
            "Verified client account permissions, active shift config, and user role settings",
            "Checked whether issue is a standard How-To covered in the L1 Knowledge Base",
        ],
        "required_fields": ["Client ID & Affected User/Trip ID", "Exact Timestamp (IST)", "Repro Steps & Screenshot/Error Message"],
        "known_workaround": "Validate client configuration and reproduce on staging/impersonation view before escalating.",
    },
}


@st.cache_data
def load_data(file_or_path):
    df = pd.read_csv(file_or_path)
    for col in ["created_at", "first_response_at", "first_closed_at", "final_closed_at"]:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    ref = df["created_at"].max().normalize() + \
        pd.Timedelta(hours=23, minutes=59, seconds=59)
    df["age_or_close_hrs"] = np.where(
        df["final_closed_at"].notna(),
        (df["final_closed_at"] - df["created_at"]).dt.total_seconds() / 3600,
        (ref - df["created_at"]).dt.total_seconds() / 3600,
    )
    df["first_close_hrs"] = (df["first_closed_at"] -
                             df["created_at"]).dt.total_seconds() / 3600
    df["is_avoidable"] = df["eng_outcome"].isin(AVOIDABLE)
    return df, ref


def token_similarity(a, b):
    ta = set(re.findall(r"[a-z0-9]+", str(a).lower()))
    tb = set(re.findall(r"[a-z0-9]+", str(b).lower()))
    stop = {"the", "for", "not", "and", "in", "on",
            "to", "is", "of", "at", "by", "from", "with"}
    ta, tb = ta - stop, tb - stop
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


st.sidebar.title("RideOps Support v0")
uploaded = st.sidebar.file_uploader(
    "Upload updated ticket CSV (optional)", type=["csv"])
df, ref_date = load_data(
    uploaded if uploaded else "rideops_support_tickets.csv")

if "session_actions" not in st.session_state:
    st.session_state.session_actions = {}

st.title("RideOps L1 Triage & Escalation Gate (v0)")
st.caption(
    f"Live data: {len(df):,} tickets | Snapshot date: {ref_date.strftime('%d %b %Y')} | Goal: Cut open backlog from 48 -> <=30 without new hires")

tab1, tab2, tab3 = st.tabs([
    "1. Live Open Queue (Triage)",
    "2. Smart Escalation Gate (L1 Action)",
    "3. Weekly Ops Pulse & Simulator",
])

# --- TAB 1: LIVE OPEN QUEUE ---
with tab1:
    open_df = df[df["status"] != "Closed"].copy()
    open_df["session_status"] = open_df["ticket_id"].map(
        st.session_state.session_actions).fillna(open_df["status"])
    open_df["risk_flag"] = np.where(
        (open_df["eng_handoffs"] >= 2) & (
            open_df["is_avoidable"]), "PING-PONG BOUNCE",
        np.where(open_df["age_or_close_hrs"] >= 72, "AGING >72h",
                 np.where(open_df["reopen_count"] > 0, "REOPENED", "STANDARD"))
    )

    c1, c2, c3, c4 = st.columns(4)
    active_open = open_df[~open_df["session_status"].str.startswith(
        "Resolved")]
    c1.metric("Current Open Backlog", len(active_open),
              f"{len(active_open) - len(open_df)} this session")
    c2.metric("Escalated in Backlog", int((active_open["escalated_to_eng"] == "Yes").sum(
    )), f"{(active_open['escalated_to_eng'] == 'Yes').mean()*100:.0f}% of open")
    c3.metric("Ping-Pong / Aging >72h",
              int((active_open["age_or_close_hrs"] >= 72).sum()))
    c4.metric("Deflected / Resolved in v0", sum(v.startswith("Resolved")
              for v in st.session_state.session_actions.values()))

    f1, f2, f3 = st.columns(3)
    cat_f = f1.multiselect("Category", sorted(open_df["category"].unique()))
    agent_f = f2.multiselect("Agent", sorted(
        open_df["assigned_agent"].unique()))
    risk_f = f3.multiselect(
        "Risk Flag", ["PING-PONG BOUNCE", "AGING >72h", "REOPENED", "STANDARD"])

    view = open_df.copy()
    if cat_f:
        view = view[view["category"].isin(cat_f)]
    if agent_f:
        view = view[view["assigned_agent"].isin(agent_f)]
    if risk_f:
        view = view[view["risk_flag"].isin(risk_f)]

    view = view.sort_values(["age_or_close_hrs"], ascending=False)
    st.dataframe(
        view[["ticket_id", "risk_flag", "priority", "category", "client_name", "assigned_agent",
              "eng_handoffs", "eng_outcome", "age_or_close_hrs", "session_status"]]
        .rename(columns={"age_or_close_hrs": "age_hrs"})
        .style.format({"age_hrs": "{:.1f}"}),
        use_container_width=True,
        hide_index=True,
    )
    st.info("Tip for demo: Open `SUP-11258` (Rahul T's 223-hour P1 GPS ticket bounced 3x for Config Issue) or `SUP-11379` (Roster Duplicate) in Tab 2.")

# --- TAB 2: SMART ESCALATION GATE ---
with tab2:
    mode = st.radio("Work on:", [
                    "Select Real Open Ticket", "Simulate New Incoming Ticket"], horizontal=True)
    open_ids = df[df["status"] != "Closed"].sort_values(
        "age_or_close_hrs", ascending=False)["ticket_id"].tolist()

    if mode == "Select Real Open Ticket":
        tid = st.selectbox("Pick Open Ticket", open_ids)
        row = df[df["ticket_id"] == tid].iloc[0]
        category = row["category"]
        desc = row["description"]
        client_label = f"{row['client_name']} ({row['client_id']} | {row['client_tier']})"
        priority = row["priority"]
        agent = row["assigned_agent"]
        st.markdown(
            f"**Ticket:** `{tid}` | **Priority:** `{priority}` | **Client:** {client_label} | **Owner:** {agent} | **Handoffs so far:** `{row['eng_handoffs']}`")
        st.warning(f"**Issue Summary:** {desc}")
    else:
        col_a, col_b = st.columns(2)
        category = col_a.selectbox("Category", sorted(df["category"].unique()))
        priority = col_b.selectbox(
            "Priority", ["P1", "P2", "P3", "P4"], index=1)
        desc = st.text_input("One-line issue description",
                             "Monthly trip report download stuck at loading")
        tid, client_label, agent = "SUP-NEW", "New Client Ticket", "L1 Agent"

    # 1. LIVE KNOWN-ISSUE & DUPLICATE DETECTOR
    st.subheader(
        "Step 1: Live Duplicate & Historical Outcome Check (Runs on 1,444 Tickets)")
    cat_hist = df[df["category"] == category].copy()
    cat_hist["sim"] = cat_hist["description"].apply(
        lambda x: token_similarity(desc, x))
    matches = cat_hist[cat_hist["sim"] >=
                       0.45].sort_values("sim", ascending=False)

    dup_matches = matches[matches["eng_outcome"] == "Duplicate of known issue"]
    config_matches = matches[matches["eng_outcome"]
                             == "Config issue - returned to L1"]
    insuff_matches = matches[matches["eng_outcome"]
                             == "Insufficient info - returned to L1"]
    l1_resolved = matches[matches["escalated_to_eng"] == "No"]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Similar Past Tickets", len(matches))
    m2.metric("Resolved at L1 (No Eng)", len(l1_resolved))
    m3.metric("Known-Issue Duplicates", len(dup_matches))
    m4.metric("Bounced (Config / Info)",
              len(config_matches) + len(insuff_matches))

    pb = PLAYBOOKS.get(category, PLAYBOOKS["DEFAULT"])
    if len(dup_matches) >= 2:
        st.error(
            f"STOP — LIKELY KNOWN ISSUE DUPLICATE ({len(dup_matches)} past duplicates found)! Do NOT create a new Engineering ticket. Apply workaround: {pb['known_workaround']}")
    elif len(config_matches) >= 3:
        st.warning(
            f"HIGH CONFIG-BOUNCE RISK ({len(config_matches)} similar tickets returned to L1 for Config issues, avg 140 hrs lost). Complete config checks below first.")
    else:
        st.success(f"Recommended L1 Check: {pb['known_workaround']}")

    # 2. CONFIG SELF-FIX CHECKLIST
    st.subheader("Step 2: Mandatory Config & Self-Fix Checklist")
    checks_done = [st.checkbox(chk, key=f"{tid}_chk_{i}")
                   for i, chk in enumerate(pb["config_checks"])]

    col_left, col_right = st.columns(2)
    with col_left:
        st.subheader(
            "Option A: Resolve / Deflect at L1 (with Anti-Reopen Guardrail)")
        g1 = st.checkbox(
            "Confirmed fix works on live client view / test trip", key=f"{tid}_g1")
        g2 = st.checkbox(
            "Shared clear resolution update with client transport admin", key=f"{tid}_g2")
        if st.button("Mark Resolved at L1 (Deflect Escalation)", disabled=not (g1 and g2)):
            st.session_state.session_actions[tid] = "Resolved at L1 (Deflected via Gate)"
            st.success(
                f"{tid} marked Resolved at L1! Estimated savings: ~94.2 open ticket-hours.")

    with col_right:
        st.subheader(
            "Option B: Escalate to Engineering (Gated Handoff Packet)")
        inputs = {}
        for idx, field in enumerate(pb["required_fields"]):
            inputs[field] = st.text_input(
                f"Required: {field}", key=f"{tid}_req_{idx}")
        all_checks = all(checks_done)
        all_fields = all(v.strip() != "" for v in inputs.values())
        if not (all_checks and all_fields):
            st.caption(
                "Escalation blocked until all Config Checks are ticked and all Required Diagnostic Fields are filled.")
        if st.button("Generate Clean Engineering Handoff Packet", disabled=not (all_checks and all_fields)):
            packet = f"""[RIDEOPS CLEAN ESCALATION PACKET]
Support Ticket: {tid} | Priority: {priority} | Category: {category}
Client: {client_label} | L1 Owner: {agent}
Summary: {desc}
Config Checks Verified: YES ({len(checks_done)}/{len(checks_done)})
Diagnostics:
""" + "\n".join([f"- {k}: {v}" for k, v in inputs.items()])
            st.code(packet, language="markdown")
            st.session_state.session_actions[tid] = "Escalated Cleanly (Gate Passed)"
            st.success(
                "Clean Engineering packet ready to copy into Jira/Slack!")

# --- TAB 3: WEEKLY OPS PULSE & SIMULATOR ---
with tab3:
    st.subheader("Weekly North Star, Guardrails & Agent Coaching Flags")
    sep = df[df["created_at"].dt.strftime("%Y-%m") == "2026-09"]
    esc_all = df[df["escalated_to_eng"] == "Yes"]

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Sep 30 Open Backlog", int(
        (df["status"] != "Closed").sum()), "+60% vs Jul 31 (30)", delta_color="inverse")
    k2.metric("Avoidable Esc. Rate (All)",
              f"{esc_all['is_avoidable'].mean()*100:.1f}%", "Target <=25%", delta_color="inverse")
    k3.metric("Team Reopen Rate (Guardrail)",
              f"{(df['reopen_count'] > 0).mean()*100:.1f}%", "Target <=7%", delta_color="inverse")
    k4.metric("Overall CSAT (Guardrail)",
              f"{df['csat_score'].mean():.2f} / 5", "Target >=4.00")

    st.markdown("#### Agent Coaching Table (Auto-Flagged Outliers)")
    ag = df.groupby("assigned_agent").agg(
        Tickets=("ticket_id", "count"),
        Esc_Rate_Pct=("escalated_to_eng", lambda x: (x == "Yes").mean() * 100),
        Avoidable_Esc=("is_avoidable", "sum"),
        Reopen_Rate_Pct=("reopen_count", lambda x: (x > 0).mean() * 100),
        Med_First_Close_Hrs=("first_close_hrs", "median"),
        Avg_CSAT=("csat_score", "mean"),
    ).reset_index()
    ag["Coaching_Flag"] = np.where(
        ag["Reopen_Rate_Pct"] > 12, "PREMATURE CLOSURES (Audit closures)",
        np.where(ag["Esc_Rate_Pct"] > 45,
                 "OVER-ESCALATING (Enforce Gate)", "Healthy Core")
    )
    st.dataframe(ag.round(2), use_container_width=True, hide_index=True)

    st.markdown("#### Little's Law Backlog Impact Simulator (Zero New Hires)")
    s1, s2 = st.columns(2)
    deflect_pct = s1.slider(
        "% of Avoidable Escalations Deflected at L1", 0, 90, 60, 5)
    reopen_cut_pct = s2.slider(
        "% of Premature Reopens Prevented", 0, 80, 50, 5)

    total_hrs = df["age_or_close_hrs"].sum()
    av_df = df[df["is_avoidable"]]
    l1_avg = df[df["escalated_to_eng"] == "No"]["age_or_close_hrs"].mean()
    saved_esc_hrs = len(av_df) * (deflect_pct / 100) * \
        (av_df["age_or_close_hrs"].mean() - l1_avg)
    reopen_hrs = (df["final_closed_at"] - df["first_closed_at"]
                  ).dt.total_seconds().fillna(0).sum() / 3600
    saved_reopen_hrs = reopen_hrs * (reopen_cut_pct / 100)
    # Secondary benefit: faster genuine bug resolution as Eng queue shrinks
    eng_queue_relief = 0.25 * \
        (deflect_pct / 100) * df[df["eng_outcome"]
                                 == "Fixed"]["age_or_close_hrs"].sum()

    pct_reduction = (saved_esc_hrs + saved_reopen_hrs +
                     eng_queue_relief) / total_hrs
    proj_backlog = max(12, round(48 * (1 - pct_reduction)))
    st.success(
        f"**Projected End-of-Month Open Backlog:** `48` → **`~{proj_backlog} tickets`** "
        f"(**-{pct_reduction*100:.1f}% WIP reduction** with ₹0 hiring cost | "
        f"{saved_esc_hrs + saved_reopen_hrs:,.0f} direct ticket-hours saved)."
    )
