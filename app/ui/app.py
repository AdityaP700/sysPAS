import time

import streamlit as st


st.set_page_config(
    page_title="RunbookMind",
    page_icon="RM",
    layout="wide",
    initial_sidebar_state="collapsed",
)


RUNBOOK_TEXT = """Investigate Credential Dumping

1. Find mimikatz activity
2. Identify affected hosts
3. Generate report"""

TIMELINE_STEPS = [
    "Runbook Compiled",
    "Query Generated",
    "Evidence Found",
    "MITRE Mapped",
    "Threat Classified",
    "Report Generated",
]

EVIDENCE_ROWS = [
    {"Host": "WIN-DC01", "User": "svcBackup"},
    {"Host": "WIN-SRV02", "User": "admin"},
]


def init_state() -> None:
    defaults = {
        "screen": "landing",
        "compiled": False,
        "executed": False,
        "timeline_progress": 0,
        "runbook": RUNBOOK_TEXT,
        "workspace_runbook_editor": RUNBOOK_TEXT,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def go(screen: str) -> None:
    st.session_state.screen = screen
    st.rerun()


def run_execution() -> None:
    st.session_state.compiled = True
    for index in range(1, len(TIMELINE_STEPS) + 1):
        st.session_state.timeline_progress = index
        time.sleep(0.22)
    st.session_state.executed = True
    st.session_state.screen = "results"
    st.rerun()


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --bg: #090d14;
            --panel: rgba(19, 28, 42, 0.9);
            --panel-strong: #111927;
            --line: rgba(148, 163, 184, 0.22);
            --text: #f8fafc;
            --muted: #aab7c8;
            --accent: #3dd6b5;
            --accent-2: #6aa8ff;
            --danger: #ff5f7a;
            --warn: #f6c85f;
        }

        html, body, [data-testid="stAppViewContainer"] {
            background:
                radial-gradient(circle at 18% 12%, rgba(61, 214, 181, 0.15), transparent 28%),
                radial-gradient(circle at 88% 0%, rgba(106, 168, 255, 0.13), transparent 26%),
                var(--bg);
            color: var(--text);
        }

        [data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"] {
            display: none;
        }

        .block-container {
            max-width: 1180px;
            padding: 3rem 2rem 4rem;
        }

        h1, h2, h3, p {
            letter-spacing: 0;
        }

        .topbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 3.5rem;
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            color: var(--text);
            font-weight: 800;
            font-size: 1rem;
        }

        .brand-mark {
            display: grid;
            width: 2.25rem;
            height: 2.25rem;
            place-items: center;
            border: 1px solid rgba(61, 214, 181, 0.5);
            border-radius: 0.5rem;
            background: linear-gradient(145deg, rgba(61, 214, 181, 0.2), rgba(106, 168, 255, 0.12));
            color: var(--accent);
            font-weight: 900;
        }

        .nav-pill {
            color: var(--muted);
            border: 1px solid var(--line);
            border-radius: 999px;
            padding: 0.45rem 0.8rem;
            font-size: 0.82rem;
        }

        .hero {
            display: grid;
            gap: 1.5rem;
            min-height: 42vh;
            align-content: center;
        }

        .hero h1 {
            max-width: 920px;
            color: var(--text);
            font-size: clamp(3rem, 8vw, 6.25rem);
            line-height: 0.95;
            margin: 0;
        }

        .hero p {
            max-width: 820px;
            color: var(--muted);
            font-size: 1.2rem;
            line-height: 1.65;
            margin: 0;
        }

        .card-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 1rem;
            margin-top: 4rem;
        }

        .demo-card,
        .panel,
        .result-card {
            border: 1px solid var(--line);
            border-radius: 0.5rem;
            background: linear-gradient(180deg, rgba(19, 28, 42, 0.94), rgba(11, 17, 27, 0.94));
            box-shadow: 0 24px 80px rgba(0, 0, 0, 0.22);
        }

        .demo-card {
            min-height: 168px;
            padding: 1.35rem;
        }

        .demo-card .icon {
            font-size: 2rem;
            margin-bottom: 1.25rem;
        }

        .demo-card h3 {
            color: var(--text);
            font-size: 1.35rem;
            margin: 0 0 0.5rem;
        }

        .demo-card p,
        .small-label {
            color: var(--muted);
            margin: 0;
        }

        .workspace-grid {
            display: grid;
            grid-template-columns: minmax(0, 1.05fr) minmax(360px, 0.95fr);
            gap: 1rem;
            align-items: stretch;
        }

        .panel {
            padding: 1.4rem;
            min-height: 520px;
        }

        .panel h2 {
            margin: 0 0 1rem;
            color: var(--text);
            font-size: 1.4rem;
        }

        .editor-shell {
            min-height: 342px;
            border: 1px solid rgba(148, 163, 184, 0.18);
            border-radius: 0.5rem;
            background: #080c13;
            padding: 1rem;
        }

        .timeline {
            display: grid;
            gap: 0.85rem;
            margin-top: 1.25rem;
        }

        .timeline-row {
            display: flex;
            align-items: center;
            gap: 0.8rem;
            border: 1px solid rgba(148, 163, 184, 0.16);
            border-radius: 0.5rem;
            background: rgba(8, 12, 19, 0.72);
            padding: 0.9rem 1rem;
            color: var(--muted);
        }

        .timeline-row.done {
            border-color: rgba(61, 214, 181, 0.38);
            color: var(--text);
            box-shadow: inset 3px 0 0 var(--accent);
        }

        .check {
            display: grid;
            flex: 0 0 1.5rem;
            width: 1.5rem;
            height: 1.5rem;
            place-items: center;
            border-radius: 999px;
            background: rgba(61, 214, 181, 0.14);
            color: var(--accent);
            font-size: 0.86rem;
            font-weight: 900;
        }

        .results-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 1rem;
            margin: 1.25rem 0 1.25rem;
        }

        .result-card {
            min-height: 142px;
            padding: 1.2rem;
        }

        .result-card span {
            display: block;
            color: var(--muted);
            font-size: 0.82rem;
            text-transform: uppercase;
        }

        .result-card strong {
            display: block;
            margin-top: 1rem;
            color: var(--text);
            font-size: clamp(1.45rem, 3vw, 2.2rem);
            line-height: 1.05;
        }

        .critical {
            color: var(--danger) !important;
        }

        .score {
            color: var(--warn) !important;
        }

        .table-wrap {
            overflow: hidden;
            border: 1px solid var(--line);
            border-radius: 0.5rem;
            background: rgba(8, 12, 19, 0.72);
        }

        table {
            width: 100%;
            border-collapse: collapse;
        }

        th, td {
            padding: 1rem;
            text-align: left;
            border-bottom: 1px solid rgba(148, 163, 184, 0.14);
        }

        th {
            color: var(--muted);
            font-size: 0.8rem;
            text-transform: uppercase;
        }

        td {
            color: var(--text);
            font-weight: 650;
        }

        .report {
            max-width: 880px;
            margin: 0 auto;
            border: 1px solid var(--line);
            border-radius: 0.5rem;
            background: #f8fafc;
            color: #172033;
            padding: clamp(1.5rem, 5vw, 4rem);
            box-shadow: 0 30px 100px rgba(0, 0, 0, 0.35);
        }

        .report h1 {
            color: #0d1727;
            font-size: clamp(2.2rem, 5vw, 4.2rem);
            line-height: 1;
            margin: 0 0 2rem;
        }

        .report h2 {
            color: #0d1727;
            font-size: 1rem;
            margin: 1.5rem 0 0.5rem;
            text-transform: uppercase;
        }

        .report p,
        .report li {
            color: #354055;
            font-size: 1.05rem;
            line-height: 1.75;
        }

        div.stButton > button {
            min-height: 3rem;
            border-radius: 0.5rem;
            border: 1px solid rgba(61, 214, 181, 0.4);
            background: rgba(61, 214, 181, 0.12);
            color: #ecfffb;
            font-weight: 800;
        }

        div.stButton > button:hover {
            border-color: var(--accent);
            color: #ffffff;
            background: rgba(61, 214, 181, 0.2);
        }

        [data-testid="stTextArea"] textarea {
            min-height: 300px;
            border-radius: 0.5rem;
            border: 1px solid rgba(148, 163, 184, 0.22);
            background: #080c13;
            color: #f8fafc;
            font-size: 1.05rem;
            line-height: 1.7;
        }

        @media (max-width: 900px) {
            .block-container {
                padding: 1.5rem 1rem 3rem;
            }

            .topbar,
            .workspace-grid,
            .card-grid,
            .results-grid {
                grid-template-columns: 1fr;
            }

            .topbar {
                display: grid;
                gap: 1rem;
                margin-bottom: 2rem;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def topbar(label: str = "Autonomous SOC demo") -> None:
    st.markdown(
        f"""
        <div class="topbar">
            <div class="brand"><div class="brand-mark">RM</div><span>RunbookMind</span></div>
            <div class="nav-pill">{label}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def landing_page() -> None:
    topbar()
    st.markdown(
        """
        <section class="hero">
            <h1>Turn Security Runbooks Into Autonomous Investigations</h1>
            <p>Compile SOPs into AI-powered workflows that query Splunk, collect evidence, map MITRE ATT&CK techniques, and generate executive reports.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )

    col_a, col_b, _ = st.columns([0.18, 0.22, 0.6])
    with col_a:
        if st.button(
            "Start Investigation",
            type="primary",
            use_container_width=True,
            key="landing_start",
        ):
            go("workspace")
    with col_b:
        if st.button("View Demo Runbooks", use_container_width=True, key="landing_demo"):
            st.session_state.runbook = RUNBOOK_TEXT
            st.session_state.workspace_runbook_editor = RUNBOOK_TEXT
            go("workspace")

    st.markdown(
        """
        <div class="card-grid">
            <article class="demo-card">
                <div class="icon">📄</div>
                <h3>Compile</h3>
                <p>Runbook → Skill Bundle</p>
            </article>
            <article class="demo-card">
                <div class="icon">🔍</div>
                <h3>Investigate</h3>
                <p>AI Agent → Splunk</p>
            </article>
            <article class="demo-card">
                <div class="icon">📊</div>
                <h3>Report</h3>
                <p>Executive Summary</p>
            </article>
        </div>
        """,
        unsafe_allow_html=True,
    )


def timeline_html() -> str:
    rows = []
    for index, label in enumerate(TIMELINE_STEPS, start=1):
        done = index <= st.session_state.timeline_progress
        check = "✓" if done else ""
        rows.append(
            f"""
            <div class="timeline-row {'done' if done else ''}">
                <div class="check">{check}</div>
                <strong>{label}</strong>
            </div>
            """
        )
    return "".join(rows)


def workspace_page() -> None:
    topbar("Investigation Workspace")
    st.markdown('<div class="workspace-grid">', unsafe_allow_html=True)

    left, right = st.columns([1.05, 0.95], gap="medium")
    with left:
        st.markdown('<div class="panel"><h2>Runbook Editor</h2>', unsafe_allow_html=True)
        st.text_area(
            "Runbook editor",
            value=st.session_state.runbook,
            label_visibility="collapsed",
            key="workspace_runbook_editor",
        )
        st.session_state.runbook = st.session_state.workspace_runbook_editor
        col_compile, col_execute = st.columns(2)
        with col_compile:
            if st.button("Compile", use_container_width=True, key="workspace_compile"):
                st.session_state.compiled = True
                st.session_state.timeline_progress = max(st.session_state.timeline_progress, 1)
                st.rerun()
        with col_execute:
            if st.button(
                "Execute",
                type="primary",
                use_container_width=True,
                key="workspace_execute",
            ):
                run_execution()
        st.markdown("</div>", unsafe_allow_html=True)

    with right:
        st.markdown(
            f"""
            <div class="panel">
                <h2>Execution Timeline</h2>
                <div class="timeline">{timeline_html()}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("</div>", unsafe_allow_html=True)


def results_page() -> None:
    topbar("Investigation Results")
    st.markdown(
        """
        <div class="results-grid">
            <article class="result-card"><span>Threat</span><strong>Credential Dumping</strong></article>
            <article class="result-card"><span>Severity</span><strong class="critical">Critical</strong></article>
            <article class="result-card"><span>Risk Score</span><strong class="score">98/100</strong></article>
            <article class="result-card"><span>MITRE</span><strong>T1003</strong><p class="small-label">Credential Dumping</p></article>
        </div>

        <div class="table-wrap">
            <table>
                <thead><tr><th>Host</th><th>User</th></tr></thead>
                <tbody>
                    <tr><td>WIN-DC01</td><td>svcBackup</td></tr>
                    <tr><td>WIN-SRV02</td><td>admin</td></tr>
                </tbody>
            </table>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col_a, col_b, _ = st.columns([0.22, 0.18, 0.6])
    with col_a:
        if st.button(
            "Open Executive Report",
            type="primary",
            use_container_width=True,
            key="results_report",
        ):
            go("report")
    with col_b:
        if st.button("Back", use_container_width=True, key="results_back"):
            go("workspace")


def report_page() -> None:
    topbar("Executive Report")
    st.markdown(
        """
        <article class="report">
            <h1>Executive Incident Report</h1>

            <h2>Threat:</h2>
            <p>Credential Dumping</p>

            <h2>Affected Hosts:</h2>
            <p>WIN-DC01<br>WIN-SRV02</p>

            <h2>Evidence:</h2>
            <p>Splunk evidence indicates suspicious credential access behavior on the domain controller and a production server. Activity includes mimikatz-like process signals and access patterns tied to privileged accounts.</p>

            <h2>Recommendations:</h2>
            <ul>
                <li>Isolate affected hosts from the network.</li>
                <li>Rotate impacted service and administrator credentials.</li>
                <li>Review lateral movement paths from WIN-DC01 and WIN-SRV02.</li>
                <li>Increase monitoring for MITRE ATT&CK T1003 activity.</li>
            </ul>
        </article>
        """,
        unsafe_allow_html=True,
    )

    col_a, _ = st.columns([0.18, 0.82])
    with col_a:
        if st.button("Back to Results", use_container_width=True, key="report_back"):
            go("results")


init_state()
inject_styles()

if st.session_state.screen == "landing":
    landing_page()
elif st.session_state.screen == "workspace":
    workspace_page()
elif st.session_state.screen == "results":
    results_page()
else:
    report_page()
