import streamlit as st
import json
from app.service.runbook_service import RunbookService
from app.package.exporter import SkillExporter

# Page Setup
st.set_page_config(
    page_title="RunbookMind - Autonomous SOP Compiler",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.title("🧠 RunbookMind")
st.subheader("Transform operational SOPs and Runbooks into governed autonomous agent skills")

# Initialize Service
if "runbook_service" not in st.session_state:
    st.session_state.runbook_service = RunbookService()

# Predefined Runbook SOP Templates
SOP_TEMPLATES = {
    "Failed Login Investigation": """# Failed Login Investigation
Investigates brute-force authentication attempts.

1. Check auth logs for spikes > 100 failures in 5 min
2. If source_ip == internal, escalate to Tier 2
3. If external, block IP and create JIRA ticket
""",
    "Suspicious PowerShell Activity": """# Suspicious PowerShell Activity
Investigates suspicious PowerShell execution and obfuscated command line activity.

1. Investigate suspicious PowerShell activity
2. If executionpolicy bypass detected, escalate to Tier 2
""",
    "Credential Dumping": """# Credential Dumping
Investigates potential LSASS access and credential dumping attempts.

1. Investigate credential dumping
2. If mimikatz process detected, block IP and escalate to Tier 2
""",
    "Persistence Mechanisms": """# Persistence Mechanisms
Investigates persistence mechanisms such as boot/logon autostart execution.

1. Investigate persistence mechanisms
2. If unauthorized autorun detected, escalate to Tier 2
""",
    "Registry Modifications": """# Registry Modifications
Investigates suspicious modifications to startup registry keys.

1. Investigate registry modifications
2. If suspicious startup key added, escalate to Tier 2
""",
    "Account Creation Activity": """# Account Creation Activity
Investigates suspicious account creation activity.

1. Investigate account creation activity
2. If unauthorized administrator account created, escalate to Tier 2
"""
}

# Sidebar Configuration
st.sidebar.header("📁 Import Runbook")

selected_template = st.sidebar.selectbox(
    "Choose Predefined SOP Template",
    options=list(SOP_TEMPLATES.keys())
)

uploaded_file = st.sidebar.file_uploader(
    "Upload Markdown SOP (.md, .txt)",
    type=["md", "txt"]
)

# Text Area Input
if uploaded_file is not None:
    content = uploaded_file.read().decode("utf-8")
    filename = uploaded_file.name
else:
    content = SOP_TEMPLATES[selected_template]
    filename = f"{selected_template.lower().replace(' ', '_')}.md"

# Workspace/Tenant Selector
st.sidebar.subheader("🏢 Workspace Scoping")
tenants = []
if st.session_state.runbook_service.repo:
    try:
        tenants = st.session_state.runbook_service.repo.list_tenants()
    except Exception as e:
        st.sidebar.error(f"Failed to load tenants: {e}")

tenant_options = {t.tenant_id: f"{t.name} ({t.slug})" for t in tenants}
if not tenant_options:
    tenant_options = {"system": "System Tenant (system)"}

selected_tenant_id = st.sidebar.selectbox(
    "Select Tenant Workspace",
    options=list(tenant_options.keys()),
    format_func=lambda x: tenant_options[x]
)

with st.sidebar.expander("🆕 Register Tenant"):
    new_tenant_name = st.text_input("Tenant Name", placeholder="e.g. SOC Team")
    new_tenant_slug = st.text_input("Tenant Slug", placeholder="e.g. soc-team")
    create_tenant_btn = st.button("Create Tenant")
    if create_tenant_btn:
        if new_tenant_name and new_tenant_slug:
            try:
                from app.auth.models import TenantRecord
                from datetime import datetime, timezone
                import uuid
                now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                new_t = TenantRecord(
                    tenant_id=f"tenant_{uuid.uuid4().hex[:12]}",
                    name=new_tenant_name,
                    slug=new_tenant_slug,
                    created_at=now,
                    enabled=True,
                    deleted_at=None
                )
                st.session_state.runbook_service.repo.save_tenant(new_t)
                st.success(f"Tenant '{new_tenant_name}' created!")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")
        else:
            st.warning("Provide name and slug.")

st.sidebar.subheader("Editor")
editable_content = st.sidebar.text_area(
    "Edit Runbook Raw Source",
    value=content,
    height=300
)

compile_btn = st.sidebar.button("⚙️ Compile to Agent Skill", type="primary")

# Execute compilation
if compile_btn or "compile_response" not in st.session_state:
    with st.spinner("Compiling Runbook into Agent Skill..."):
        response = st.session_state.runbook_service.compile_runbook(
            editable_content, filename, tenant_id=selected_tenant_id
        )
        st.session_state.compile_response = response

response = st.session_state.compile_response
bundle = response.bundle
manifest = bundle.manifest
skill = bundle.agent_skill

# Layout Columns
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Runbook Status", response.status)
with col2:
    st.metric("Steps Count", len(skill.graph.nodes))
with col3:
    st.metric("Compile Confidence", f"{int(manifest.overall_confidence * 100)}%")

# Validation Diagnostics Alerts
if response.errors:
    st.error("### ❌ Compilation Errors")
    for err in response.errors:
        st.write(f"- {err}")

if response.warnings:
    st.warning("### ⚠️ Compilation Warnings")
    for warn in response.warnings:
        st.write(f"- {warn}")

# Display Tabs
tab1, tab2, tab3, tab4 = st.tabs([
    "🔍 Compilation Results",
    "🗺️ Execution Graph Flow",
    "🛡️ Inferred Governance",
    "💾 Export Skill Bundle"
])

with tab1:
    st.write("### Step-by-Step Compiled Queries")
    for step in skill.steps:
        status_icon = "✅" if step.status.value == "SUCCESS" else "❌"
        with st.expander(f"{status_icon} Step {step.step_id}: {step.description} (Confidence: {step.confidence})"):
            st.markdown(f"**Original Text:** {step.description}")
            if step.raw_spl:
                st.markdown("**Generated Raw SPL:**")
                st.code(step.raw_spl, language="spl")
            if step.compiled_spl:
                st.markdown("**Optimized SPL Query:**")
                st.code(step.compiled_spl, language="spl")
            if step.explanation:
                st.markdown(f"**Query Explanation:**  \n{step.explanation}")

with tab2:
    st.write("### Execution Graph Flow")
    
    col_g1, col_g2 = st.columns(2)
    with col_g1:
        st.markdown("**Graph Nodes (Steps)**")
        node_data = []
        for node in skill.graph.nodes:
            node_data.append({
                "Node ID": node.node_id,
                "Step ID": node.step_id,
                "Action Type": node.action_type or "DETECTION",
                "Confidence": node.confidence,
                "Compiled SPL": node.compiled_spl or ""
            })
        st.table(node_data)
        
    with col_g2:
        st.markdown("**Graph Edges (Transitions)**")
        edge_data = []
        for edge in skill.graph.edges:
            edge_data.append({
                "Source Node": edge.source,
                "Target Node": edge.target,
                "Condition Guard": edge.condition or "None"
            })
        if edge_data:
            st.table(edge_data)
        else:
            st.info("No transition edges (single node graph).")

with tab3:
    st.write("### Inferred Governance & Policy Gate")
    gov = skill.governance
    
    st.markdown(f"**Execution Mode:** `{gov.execution_mode.value}`")
    st.markdown(f"**Approval Required:** `{gov.approval_required}`")
    st.markdown(f"**Approval Role:** `{gov.approval_role or 'None'}`")
    st.markdown(f"**Auditing Enabled:** `{gov.audit_enabled}`")
    
    if gov.execution_mode.value == "HUMAN_IN_LOOP":
        st.info("💡 **Reasoning:** Destructive operations like `BLOCK_IP` or escalation triggers mandate human authorization gates.")
    elif gov.execution_mode.value == "MANUAL":
        st.warning("⚠️ **Reasoning:** Step specifies manual operator actions that cannot be fully automated.")
    else:
        st.success("✅ **Reasoning:** All steps resolve to safe automations (ticketing, alerts) and execute without approvals.")

with tab4:
    st.write("### Packed Skill Bundle JSON")
    
    # Export deterministic JSON configuration
    exported_json = SkillExporter.export_json(bundle)
    
    st.code(exported_json, language="json")
    
    st.download_button(
        label="📥 Download Agent Skill Bundle JSON",
        data=exported_json,
        file_name="runbookmind_agent_skill.json",
        mime="application/json"
    )
