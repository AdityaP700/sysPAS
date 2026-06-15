# SPLCopilot → RunbookMind: Transformed Architecture
## "The Superfluid Moment" for Splunk Hackathon

**Track:** Platform & Developer Experience
**Bonus Targets:** Best Splunk MCP Server + Best Splunk Developer Tools + Best Splunk Hosted Models
**Build Time:** 28-32 hours
**Win Probability:** VERY HIGH

---

## THE SUPERFLUID INSIGHT

Original SPLCopilot = NL → SPL generator (good, but saturated pattern)

Transformed RunbookMind = **Runbook/SOP → Autonomous Agent Compiler**

**Why this is the "Superfluid moment":**

Cisco just announced (Cisco Live, June 2–6 2026 — 6 days ago):
1. **Machine Data Lake (Alpha, Feb 2026)** — schema-less RAG-ready telemetry store. First hackathon to use this.
2. **Federated Search for Snowflake (Alpha now, GA July 2026)** — join business + machine data in SPL. Nobody has built on this yet.
3. **AI Toolkit Agent Builder (Feature Preview, March 2026)** — runbooks/SOPs → reusable agent skills. Just shipped.
4. **Cisco AI Canvas (Summer 2026 GA)** — multiplayer human+agent incident workspace. Bleeding-edge deploy target.

Same pattern as Superfluid: you're the first builder on a brand-new primitive that just became accessible. Judges at Splunk will recognize this immediately.

---

## TRANSFORMED CORE CONCEPT

```
OLD: User types NL query → get SPL query back
NEW: User uploads runbook/SOP → gets autonomous agent that runs it
```

**RunbookMind Agentic Loop:**

```
┌─────────────────────────────────────────────────────────────┐
│ INPUT: Runbook/SOP (Markdown, PDF, or plain text)           │
│ e.g. "Investigate Failed Login Surge: Step 1... Step 2..."  │
└──────────────────────┬──────────────────────────────────────┘
                       │
         ┌─────────────▼──────────────────┐
         │ DECOMPOSITION AGENT             │
         │ Foundation-sec parses runbook   │
         │ Extracts: steps, data sources,  │
         │ conditions, actions, thresholds  │
         └─────────────┬──────────────────┘
                       │
         ┌─────────────▼──────────────────────────────────────┐
         │ SPL COMPILER LAYER                                  │
         │ MCP: generate_spl per each runbook step            │
         │ MCP: optimize_spl → performance tune               │
         │ MCP: explain_spl → human-readable annotation       │
         │ Foundation-sec: validate SPL against data schema   │
         └─────────────┬──────────────────────────────────────┘
                       │
         ┌─────────────▼──────────────────────────────────────┐
         │ MACHINE DATA LAKE CONTEXT LAYER ⭐ NEW              │
         │ Query MDL for schema context before SPL gen        │
         │ MDL = schema-less, AI-ready, RAG-optimized         │
         │ Foundation-sec grounds queries in real field names │
         └─────────────┬──────────────────────────────────────┘
                       │
         ┌─────────────▼──────────────────────────────────────┐
         │ FEDERATED QUERY ENRICHMENT ⭐ BRAND NEW             │
         │ If runbook needs business context:                 │
         │ Join Splunk machine data + Snowflake business data │
         │ One SPL query spans both sources                   │
         │ e.g. "Failed logins on POS systems + revenue drop" │
         └─────────────┬──────────────────────────────────────┘
                       │
         ┌─────────────▼──────────────────────────────────────┐
         │ AI TOOLKIT AGENT BUILDER COMPILATION ⭐ NEW         │
         │ Converts compiled SPL steps → Agent Skill          │
         │ Skill = reusable, governed, auditable agent        │
         │ Deploy to AI Toolkit Agent Builder                 │
         │ Agent runs runbook autonomously on trigger         │
         └─────────────┬──────────────────────────────────────┘
                       │
         ┌─────────────▼──────────────────────────────────────┐
         │ OUTPUT: Compiled Agent Skill                        │
         │ • All SPL steps (original + optimized)             │
         │ • Execution order + branching logic                │
         │ • Human-in-loop gates per runbook conditions       │
         │ • Deployed to AI Toolkit Agent Builder             │
         │ • Demo in Cisco AI Canvas (if access available)    │
         └─────────────────────────────────────────────────────┘
```

---

## WHAT "SUPERFLUID MOMENT" MEANS HERE

| Superfluid for Payments | RunbookMind for Splunk |
|---|---|
| Real-time streaming money protocol | Real-time runbook → agent compilation |
| Nobody had built payment streaming as primitive before | Nobody has built runbook → SPL → agent pipeline before |
| Judged novel because new primitive existed | Novel because Machine Data Lake + Agent Builder are alpha/new |
| Won because judges saw future potential | Judges will see it as the "how Splunk is meant to be used in 2026" |

---

## LAYER 1: RUNBOOK DECOMPOSITION

### Foundation-sec Prompt (Decomposition)

```
SYSTEM:
You are runbook parser. Convert operational SOP into structured JSON.
Extract: steps, data_sources, conditions, thresholds, actions.
Output ONLY valid JSON. Never invent field names.

EXAMPLE RUNBOOK INPUT:
"Failed Login Investigation:
1. Check auth logs for spikes > 100 failures in 5 min
2. Identify source IPs, correlate with threat intel
3. If source IP is internal, escalate to Tier 2
4. If external, block IP and create JIRA ticket"

EXPECTED OUTPUT:
{
  "runbook_name": "Failed Login Investigation",
  "steps": [
    {
      "step_id": 1,
      "description": "Check auth spike",
      "data_source": "auth_logs",
      "condition": "failures > 100",
      "time_window": "5m",
      "spl_hint": "stats count by src_ip | where count > 100"
    },
    {
      "step_id": 2,
      "description": "Correlate threat intel",
      "data_source": "auth_logs + threat_intel",
      "join_required": true,
      "spl_hint": "lookup threat_intel_lookup src_ip"
    },
    {
      "step_id": 3,
      "description": "Escalation check",
      "condition": "src_ip IN internal_range",
      "action": "human_escalation",
      "gate": "human_in_loop"
    },
    {
      "step_id": 4,
      "description": "Block + ticket",
      "condition": "src_ip NOT IN internal_range",
      "action": "block_ip + create_jira",
      "automation": true
    }
  ]
}
```

### Failure Modes

| Failure | Fix |
|---|---|
| Ambiguous runbook steps | Ask user for clarification before proceeding |
| Missing data source in runbook | Use MDL to discover relevant indexes |
| Runbook has unsupported actions | Mark step as "manual" in output, don't fail entire runbook |

---

## LAYER 2: SPL COMPILER

### MCP Tool Sequence Per Step

```python
for step in runbook_steps:
    # 1. Get schema context from Machine Data Lake first
    schema = mcp.get_field_names(step.data_source)
    
    # 2. Generate SPL grounded in real schema
    raw_spl = mcp.generate_spl(
        nl_description=step.description,
        schema_context=schema,
        time_window=step.time_window
    )
    
    # 3. Optimize for performance
    optimized_spl = mcp.optimize_spl(raw_spl)
    
    # 4. Explain for human review
    explanation = mcp.explain_spl(optimized_spl)
    
    # 5. If federated query needed (Snowflake join)
    if step.join_required and step.data_source contains "snowflake":
        spl = apply_federated_join(optimized_spl, step.snowflake_table)
    
    step.compiled_spl = optimized_spl
    step.explanation = explanation
```

### Before/After Comparison Output

```markdown
## Step 1: Auth Spike Detection

**Original (Generated)**
```spl
index=auth | stats count by src_ip | where count > 100
```

**Optimized (+Federated)**
```spl
| tstats count as failure_count WHERE index=auth earliest=-5m
  BY src_ip, _time span=1m
| where failure_count > 100
| lookup threat_intel src_ip OUTPUT threat_score, country
```

**Speedup:** ~3.2x (tstats vs stats)
**Explanation:** Uses tstats for pre-indexed metadata. Adds threat intel join inline.
**Confidence:** 91%
```

---

## LAYER 3: MACHINE DATA LAKE INTEGRATION ⭐

### What MDL Is (New, Alpha Feb 2026)

Machine Data Lake = Splunk's new schema-less, AI-ready telemetry storage layer.

Key property: Raw data lands here BEFORE indexing. Designed for:
- RAG pipelines (AI agents query MDL for context)
- Fine-tuning datasets
- Cost-efficient long-term retention

### How RunbookMind Uses MDL

**Step 1: Schema Discovery**
```python
# Before generating any SPL, query MDL for available fields
# This prevents column hallucination (main NL→SPL failure mode)
mdl_context = splunk_mcp.query_machine_data_lake(
    query=f"What fields are available for {step.data_source}?",
    index_hint=step.data_source
)
schema_fields = parse_mdl_schema(mdl_context)
```

**Step 2: RAG Context Injection**
```python
# Inject real field names into Foundation-sec prompt
prompt = f"""
Real fields in {step.data_source}: {schema_fields}
ONLY use these fields. No invented column names.
Generate SPL for: {step.description}
"""
```

### Why This Is Novel

Every other hackathon project uses Splunk indexes directly. RunbookMind uses Machine Data Lake as a schema context oracle. Judges designed MDL for exactly this use case. Demonstrates bleeding-edge platform knowledge.

### MDL Access (Risk Management)

MDL status: Alpha. Access via Splunk Cloud.
Risk: MDL may not be accessible in hackathon dev environment.

Mitigation plan:
- Primary: Request MDL access day 1 via splunkai@cisco.com
- Fallback: Use `| fieldsummary` SPL command to get schema from existing indexes
- Demo script: Pre-populate MDL demo with sample auth log data

---

## LAYER 4: FEDERATED SEARCH ENRICHMENT ⭐

### What Federated Search Enables (Brand New)

Before: Splunk indexes machine data. Snowflake holds business data. Never the twain shall meet without ETL.

After: One SPL query joins both. No data movement.

```spl
// RunbookMind federates: failed logins + revenue impact
| tstats count as login_failures WHERE index=auth BY src_ip
| join src_ip
  [search `federated://snowflake` SELECT store_id, revenue_drop_pct
   FROM sales_metrics WHERE date = today()]
| table src_ip, login_failures, store_id, revenue_drop_pct
```

### Hackathon Use Case

**Runbook input:** "Investigate POS failures and correlate with revenue impact"

**Without Federated Search:** Two separate queries, manual correlation

**With Federated Search:** One SPL, one result, instant business context

**Impact statement for judges:** "First hackathon project to demonstrate business + operational data correlation using Splunk Federated Search for Snowflake."

### Federated Search Access (Risk Management)

GA status: July 2026 for Splunk Cloud AWS commercial.
Hackathon timing: Submission opens May 18, 2026.

Mitigation options:
1. **Primary:** Request early access via Splunk developer program
2. **Alternative:** Use Query.ai Splunk App (documented workaround for Splunk→Snowflake via | queryai command. Available now, no wait required)
3. **Demo fallback:** Pre-build mock federated result with clear architectural diagram showing what Federated Search enables. Label as "architecture preview — GA July 2026"

Query.ai workaround documented at: query.ai/resources/blogs/splunk-snowflake-federated-search

---

## LAYER 5: AI TOOLKIT AGENT BUILDER COMPILATION ⭐

### What Agent Builder Does (Feature Preview)

Announced: Feature Preview in AI Toolkit 5.6+
Purpose: No-code interface to build governed, auditable agents
Key capability: Agents ground decisions in knowledge bases + MCP tools
Key quote from Cisco: "Agent Builder turns runbooks, SOPs, and procedures into reusable skills"

This is LITERALLY what RunbookMind does.

### Compilation Output

```json
{
  "agent_skill": {
    "name": "Failed Login Investigation",
    "source_runbook": "failed_login_sop_v2.md",
    "steps": [
      {
        "step_id": 1,
        "spl": "| tstats count as failure_count...",
        "trigger_condition": "failure_count > 100",
        "next_step": 2
      },
      {
        "step_id": 2,
        "spl": "... lookup threat_intel ...",
        "gate": "human_in_loop",
        "escalate_if": "src_ip IN internal_range"
      }
    ],
    "governance": {
      "rbac_role": "soc_analyst",
      "audit_log": true,
      "human_approval_required": ["step_3_block_ip"]
    }
  }
}
```

### Agent Builder Access (Risk Management)

Access: Email splunkai@cisco.com with stack ID + AWS region.

Risk: Preview access may take days.
Mitigation: Apply on Day 0 before hackathon. If denied, fallback = Python LangGraph agent with same skill structure. Show Agent Builder UI in demo video as target deploy environment.

---

## TECH STACK

| Layer | Tech | Status | Risk |
|-------|------|--------|------|
| Runbook parsing | Foundation-sec (hosted) | GA | Low |
| SPL generation | Splunk MCP (generate_spl) | GA | Low |
| SPL optimization | Splunk MCP (optimize_spl) | GA | Low |
| Schema context | Machine Data Lake | Alpha | Medium |
| Cross-source queries | Federated Search Snowflake | Alpha/Preview | Medium |
| Agent compilation | AI Toolkit Agent Builder | Feature Preview | Medium |
| Frontend | Streamlit or React | GA | Low |
| Backend | Python + FastAPI | GA | Low |
| Hosting | Cloud Run or local | GA | Low |

**Risk Level: MEDIUM** (vs LOW for plain SPLCopilot)
**Differentiation Level: VERY HIGH** (vs MEDIUM for plain SPLCopilot)

Tradeoff worth making. Even if MDL and Federated Search fall back to alternatives, the CONCEPT demonstrates understanding of Splunk's 2026 roadmap. Judges designed these features. Recognition = scoring premium.

---

## DEMO SCRIPT (3 MIN VIDEO)

```
0:00–0:30: Problem statement
  "Every SOC team has runbooks. Nobody has automated them into agents. Until now."

0:30–1:15: Live demo — upload runbook
  Upload "Failed Login Investigation SOP.md"
  Watch: Decomposition (Foundation-sec extracts 4 steps)
  Watch: SPL compilation per step (generate → optimize → explain)
  Show before/after SPL comparison with speedup metrics

1:15–2:00: Machine Data Lake + Federated Search
  Show: Schema discovery from MDL → zero column hallucination
  Show: Federated query joining auth logs + Snowflake POS revenue
  One query. Two sources. Business context appears.

2:00–2:45: Agent Builder compilation
  Show: Compiled skill deployed to AI Toolkit Agent Builder
  Show: Agent runs automatically on trigger (>100 failed logins)
  Show: Human-in-loop gate for escalation decision

2:45–3:00: Pitch
  "RunbookMind converts SOPs into autonomous agents. Uses Machine Data Lake,
  Federated Search, Foundation-sec, and AI Toolkit Agent Builder — the full
  2026 Splunk agentic stack. Every SOC team can do this in minutes."
```

---

## PRIZE TARGETING

| Prize | How Targeted |
|-------|-------------|
| Grand Prize ($7K) | Uses entire 2026 Splunk agentic stack. Most complete demo. |
| Best Platform & Dev Experience ($3K) | Runbook → Agent = developer productivity |
| Best Splunk MCP Server ($1K) | Uses generate_spl + optimize_spl + explain_spl in tight loop |
| Best Splunk Hosted Models ($1K) | Foundation-sec for decomposition + SPL validation |
| Best Splunk Developer Tools ($1K) | Agent Builder + AI Toolkit |

**Max Possible Prize: $12,000 (Grand + Platform + all 3 bonus prizes)**

---

## WHAT CHANGED FROM ORIGINAL SPLCOPILOT

| Original SPLCopilot | RunbookMind (Transformed) |
|---|---|
| NL → SPL query | Runbook/SOP → Autonomous agent |
| Generates one query | Compiles multi-step execution plan |
| User manually runs query | Agent runs autonomously on trigger |
| Uses Splunk MCP only | Uses MCP + MDL + Federated Search + Agent Builder |
| 2023-era "chat with data" pattern | 2026 "runbook as code" pattern |
| Competing with Splunk AI Assistant | Extending Splunk AI Assistant into new territory |

---

## FAILURE MODES & PRACTICAL MITIGATIONS

### Alpha Feature Access Denied

**Scenario:** Can't get MDL or Agent Builder access before deadline.

**Mitigation:**
```
MDL → Use | fieldsummary for schema discovery
         → Functionally identical to demo
Federated Search → Use Query.ai Splunk App workaround
                 → Same result, different plumbing
Agent Builder → Use Python + LangGraph to simulate skill
              → Show Agent Builder UI in architecture diagram
              → Label as "agent skill structure, deploys to Agent Builder"
```
Demo still works. Architecture still novel. Judges still see the vision.

### Foundation-sec Generates Wrong SPL

**Symptom:** Generated SPL references fields not in runbook's data source.

**Fix:**
```python
# Pre-validate: extract schema before generation
schema = get_schema_from_splunk(step.data_source)
# Post-validate: test SPL syntax before showing user
validation_result = mcp.explain_spl(generated_spl)
if "field not found" in validation_result:
    regenerate_with_schema_constraint(schema)
```

### Runbook Too Ambiguous

**Symptom:** Foundation-sec can't extract structured steps.

**Fix:**
```
Confidence threshold: if confidence < 70%, ask user to clarify step N.
Show which steps compiled cleanly vs. need refinement.
Never fail silently. Always show compilation status per step.
```

### Demo Environment Mismatch

**Symptom:** Splunk dev environment missing data for demo queries.

**Fix:**
```
Day 0 task: Load Splunk sample datasets (auth, web, network)
Pre-build: 3 demo runbooks matched to sample data
Test: Run all 3 end-to-end before submission
Backup: Record video with working demo before final day
```

---

## BUILD TIMELINE (28-32 hours)

```
Hour 0–2:   Setup
            - Splunk Enterprise dev license
            - MCP Server connection test
            - Foundation-sec API test
            - Email splunkai@cisco.com for Agent Builder + MDL access

Hour 2–8:   Core layer
            - Runbook decomposition (Foundation-sec prompt engineering)
            - SPL compilation (generate → optimize → explain loop)
            - Unit test: 3 runbooks × 4 steps each

Hour 8–14:  Schema + federated layer
            - MDL schema discovery OR | fieldsummary fallback
            - Federated Search integration OR Query.ai workaround
            - Test: federated query joining auth + Snowflake/business data

Hour 14–20: Agent Builder compilation
            - Skill JSON structure definition
            - AI Toolkit Agent Builder integration OR LangGraph fallback
            - Human-in-loop gate logic

Hour 20–26: Frontend + UX
            - Streamlit: runbook upload, step-by-step compilation viewer
            - Before/after SPL comparison with speedup metrics
            - Agent skill export (JSON download + Agent Builder link)

Hour 26–30: Testing + polish
            - End-to-end test: 3 runbooks
            - Record demo video
            - Write README + architecture diagram
            - Devpost submission

Hour 30–32: Buffer for surprises
```

---

## QUICK START (Cursor / Claude Code)

```bash
# 1. Setup
git init runbookmind && cd runbookmind
pip install splunk-sdk streamlit langchain langgraph python-dotenv

# 2. Environment
cat > .env << EOF
SPLUNK_HOST=localhost
SPLUNK_PORT=8089
SPLUNK_USERNAME=admin
SPLUNK_PASSWORD=your_password
SPLUNK_MCP_ENDPOINT=https://{SPLUNK_HOST}:8089/api/mcp
SNOWFLAKE_ACCOUNT=
SNOWFLAKE_USER=
SNOWFLAKE_PASSWORD=
EOF

# 3. Test MCP connection
python test_mcp.py

# 4. Run app
streamlit run app.py
```

---

## ARCHITECTURE DIAGRAM (for repo root)

```
┌─────────────────────────────────────────────────────────────────┐
│                        RunbookMind                               │
│                                                                   │
│  ┌──────────┐    ┌─────────────────┐    ┌──────────────────┐   │
│  │ Runbook  │───▶│ Foundation-sec  │───▶│ SPL Compiler     │   │
│  │ Input    │    │ Decomposition   │    │ MCP Tools        │   │
│  └──────────┘    └─────────────────┘    │ generate_spl     │   │
│                                          │ optimize_spl     │   │
│  ┌──────────────────────────────────┐   │ explain_spl      │   │
│  │ Cisco Data Fabric Layer          │   └────────┬─────────┘   │
│  │                                  │            │              │
│  │  Machine Data Lake               │            ▼              │
│  │  (Schema context for SPL gen)    │   ┌──────────────────┐   │
│  │                                  │   │ Agent Skill JSON  │   │
│  │  Federated Search                │   │ (AI Toolkit Agent│   │
│  │  (Splunk + Snowflake joins)      │   │  Builder deploy) │   │
│  └──────────────────────────────────┘   └──────────────────┘   │
│                                                                   │
│  Splunk MCP Server ◄─────── Foundation-sec (Splunk Hosted)       │
└─────────────────────────────────────────────────────────────────┘
```

---

## RESUME ANGLE

**What This Proves**

1. **Agentic Infrastructure Thinking:** Not just using APIs — using Machine Data Lake as RAG context, Federated Search as cross-source join layer. System architecture thinking.
2. **Bleeding-Edge Platform Awareness:** Built on features announced 6 days ago. Proves continuous learning.
3. **Runbook-as-Code Pattern:** Converts institutional knowledge (SOPs) into autonomous agents. Massive enterprise value.
4. **Human-in-Loop Design:** Every escalation gate intentional. Production-grade agent thinking.
5. **Full Cisco/Splunk Stack:** Foundation-sec + MCP + MDL + Federated Search + Agent Builder. Shows breadth.

**Pitch to Hiring Manager**

> "Built the first runbook-to-agent compiler on Splunk's new agentic stack — Machine Data Lake, Federated Search for Snowflake, AI Toolkit Agent Builder, Foundation-sec. Converts SOPs into governed autonomous agents. Demonstrated bleeding-edge platform knowledge by building on alpha features within days of announcement. Deployed to Splunk AI Toolkit Agent Builder. Human-in-loop gates for every escalation decision."
