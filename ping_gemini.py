"""
ping_gemini.py
--------------
Tests Gemini Flash with a realistic runbook-style prompt:
  - Schema-grounded (real field names passed in)
  - Multiple runbook steps at different complexity levels
  - Measures latency per call
  - Checks the SPL output uses only the fields provided (hallucination guard)
  - Shows token usage

Run:
    python ping_gemini.py

Do NOT execute via the IDE runner — run from terminal directly.
"""

import os
import sys
import time
import re

sys.path.insert(0, ".")

# Suppress gRPC ALTS noise
os.environ["GRPC_VERBOSITY"] = "ERROR"
os.environ["GRPC_TRACE"] = ""

from dotenv import load_dotenv
load_dotenv()

from app.config.settings import settings

if not settings.gemini_api_key:
    print("ERROR: RUNBOOKMIND_GEMINI_API_KEY is not set in .env")
    sys.exit(1)

import google.generativeai as genai

genai.configure(api_key=settings.gemini_api_key)

# ---------------------------------------------------------------------------
# Guardrail: two-level field validator
# ---------------------------------------------------------------------------

# SPL aggregation keywords that are outputs, not inputs
_SPL_KEYWORDS = {
    "index", "earliest", "latest", "count", "sum", "avg", "max", "min",
    "by", "as", "where", "eval", "stats", "table", "fields", "head", "tail",
    "sort", "dedup", "rex", "search", "true", "false", "null", "values",
    "list", "dc", "first", "last", "limit", "span", "bin", "bucket",
    "range", "stdev", "var", "perc", "distinct_count", "rate", "sumsq",
    "eventstats", "streamstats", "now", "and", "or", "not", "in",
}


def _extract_derived_fields(spl: str) -> set[str]:
    """
    Level 2: Detect field aliases that the SPL query CREATES internally via:
      eval <alias>=...
      stats/eventstats/streamstats ... as <alias>
    These are NOT hallucinations — they are computed fields.
    """
    spl_lower = spl.lower()
    derived: set[str] = set()

    # Pattern: | eval fieldname = ...
    for m in re.finditer(r'\beval\s+([a-z_][a-z0-9_]*)\s*=', spl_lower):
        derived.add(m.group(1))

    # Pattern: count/sum/avg/... as alias  (from stats, eventstats, streamstats)
    for m in re.finditer(
        r'\b(?:count|sum|avg|max|min|dc|values|list|first|last|range|stdev|var|sumsq)'
        r'(?:\([^)]*\))?\s+as\s+([a-z_][a-z0-9_]*)',
        spl_lower
    ):
        derived.add(m.group(1))

    # Pattern: eventstats count as alias / streamstats count as alias
    for m in re.finditer(
        r'\b(?:eventstats|streamstats|stats)\s+.*?\bas\s+([a-z_][a-z0-9_]*)',
        spl_lower
    ):
        derived.add(m.group(1))

    return derived


def validate_spl_fields(
    spl: str,
    allowed_fields: list[str]
) -> dict:
    """
    Two-level field validation.

    Returns:
        {
            "hallucinated": [...],   # Level 1: real hallucinations
            "derived":      [...],   # Level 2: SPL-computed aliases (OK)
            "status": "PASS" | "DERIVED" | "HALLUCINATION"
        }
    """
    spl_lower = spl.lower()
    allowed_lower = {f.lower() for f in allowed_fields}

    # Extract all field-like references
    candidates: set[str] = set()
    candidates.update(re.findall(r'\b([a-z_][a-z0-9_]*)\s*(?:=|!=|<|>|<=|>=)', spl_lower))
    candidates.update(re.findall(r'\b(?:by|as)\s+([a-z_][a-z0-9_]+)', spl_lower))

    # Remove keywords, schema fields, short tokens
    unknown = {
        c for c in candidates
        if c not in _SPL_KEYWORDS
        and c not in allowed_lower
        and not c.isdigit()
        and len(c) > 1
    }

    if not unknown:
        return {"hallucinated": [], "derived": [], "status": "PASS"}

    # Level 2: check which unknowns are derived inside the query
    derived_fields = _extract_derived_fields(spl)
    derived     = [u for u in unknown if u in derived_fields]
    hallucinated = [u for u in unknown if u not in derived_fields]

    status = "PASS"
    if hallucinated:
        status = "HALLUCINATION"
    elif derived:
        status = "DERIVED"

    return {"hallucinated": hallucinated, "derived": derived, "status": status}


# ---------------------------------------------------------------------------
# Model selector — tries in order, stops at first success
# ---------------------------------------------------------------------------

CANDIDATES = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

SYSTEM_PROMPT = (
    "You are a Splunk SPL expert. "
    "Generate a precise, executable SPL query for the given runbook step. "
    "Rules:\n"
    "  1. Use ONLY the fields listed under 'Available Fields'.\n"
    "  2. Always scope with earliest/latest time bounds.\n"
    "  3. Reply with ONLY the raw SPL query — no markdown, no explanation, no fences.\n"
    "  4. Do not invent field names not in the list."
)

def pick_model() -> tuple[genai.GenerativeModel, str]:
    for name in CANDIDATES:
        try:
            m = genai.GenerativeModel(model_name=name, system_instruction=SYSTEM_PROMPT)
            # Quick probe — list_models doesn't cost quota
            return m, name
        except Exception:
            continue
    print("ERROR: Could not instantiate any candidate model.")
    sys.exit(1)

model, model_name = pick_model()

# ---------------------------------------------------------------------------
# Test cases — each is a realistic runbook step
# ---------------------------------------------------------------------------

TEST_CASES = [
    {
        "label": "Step 1 — Failed SSH logins (simple)",
        "description": "Investigate failed logins from unusual IP addresses",
        "index": "auth_logs",
        "fields": ["timestamp", "user", "src_ip", "status", "country", "action"],
        "time_window": "15m",
    },
    {
        "label": "Step 2 — Brute-force detection (threshold)",
        "description": (
            "Detect brute force attempts: more than 10 failed login attempts "
            "from the same src_ip within 5 minutes"
        ),
        "index": "auth_logs",
        "fields": ["timestamp", "user", "src_ip", "status", "action", "bytes_in"],
        "time_window": "5m",
    },
    {
        "label": "Step 3 — Privilege escalation (multi-field)",
        "description": (
            "Find events where a user performed a privilege escalation action "
            "('sudo' or 'su') and the resulting status is not 'success'"
        ),
        "index": "endpoint_logs",
        "fields": ["timestamp", "user", "process", "parent_process", "status", "host", "action"],
        "time_window": "1h",
    },
]

def build_prompt(case: dict) -> str:
    fields_str = "\n".join(f"  - {f}" for f in case["fields"])
    return (
        f"Step:\n{case['description']}\n\n"
        f"Index: {case['index']}\n\n"
        f"Available Fields:\n{fields_str}\n\n"
        f"Time window: last {case['time_window']}\n\n"
        "Generate SPL only."
    )

# ---------------------------------------------------------------------------
# Run tests
# ---------------------------------------------------------------------------

print("=" * 65)
print(f"Model   : {model_name}")
print(f"RPM cap : {settings.gemini_rpm_cap}  |  Cache TTL: {settings.gemini_cache_ttl}s")
print("=" * 65)

total_prompt_tokens = 0
total_response_tokens = 0
results = []

for i, case in enumerate(TEST_CASES):
    print(f"\n[{i+1}/{len(TEST_CASES)}] {case['label']}")
    print(f"  Index : {case['index']}")
    print(f"  Fields: {', '.join(case['fields'])}")
    print(f"  Window: {case['time_window']}")
    print()

    prompt = build_prompt(case)

    t0 = time.perf_counter()
    try:
        response = model.generate_content(prompt)
        t1 = time.perf_counter()
    except Exception as e:
        print(f"  ERROR: {e}")
        results.append({"label": case["label"], "error": str(e)})
        continue

    elapsed = t1 - t0

    # Strip any accidental markdown fences
    spl = response.text.strip()
    spl = re.sub(r'^```[a-z]*\n?', '', spl, flags=re.IGNORECASE)
    spl = re.sub(r'\n?```$', '', spl).strip()

    # Guardrail: two-level validation
    validation = validate_spl_fields(spl, case["fields"])

    # Token accounting
    try:
        usage = response.usage_metadata
        pt = usage.prompt_token_count
        rt = usage.candidates_token_count
        total_prompt_tokens += pt
        total_response_tokens += rt
        token_str = f"prompt={pt}, response={rt}"
    except Exception:
        token_str = "n/a"

    print(f"  SPL ({elapsed:.2f}s):")
    for line in spl.split("\n"):
        print(f"    {line}")
    print(f"  Tokens : {token_str}")

    status = validation["status"]
    if status == "PASS":
        print("  Guardrail [L1+L2]: PASS -- all fields in schema")
    elif status == "DERIVED":
        print(f"  Guardrail [L1]: PASS -- schema fields OK")
        print(f"  Guardrail [L2]: DERIVED fields (SPL-computed, not hallucinations): {validation['derived']}")
    else:
        print(f"  Guardrail [L1]: PASS")
        print(f"  Guardrail [L2]: DERIVED OK: {validation['derived']}")
        print(f"  Guardrail [!!]: HALLUCINATION -- invented fields: {validation['hallucinated']}")

    results.append({
        "label": case["label"],
        "elapsed": elapsed,
        "spl": spl,
        "validation": validation,
        "tokens": token_str,
    })

    # Respect rate limit between calls
    if i < len(TEST_CASES) - 1:
        wait = 60 / settings.gemini_rpm_cap
        print(f"\n  [rate limiter] waiting {wait:.1f}s before next call...")
        time.sleep(wait)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print("\n" + "=" * 65)
print("SUMMARY")
print("=" * 65)
for r in results:
    if "error" in r:
        status_str = f"FAILED  -- {r['error'][:55]}"
    else:
        v = r["validation"]
        if v["status"] == "PASS":
            status_str = f"PASS       {r['elapsed']:.2f}s"
        elif v["status"] == "DERIVED":
            status_str = f"PASS       {r['elapsed']:.2f}s  (derived aliases: {v['derived']})"
        else:
            status_str = f"HALLUCINATION  {r['elapsed']:.2f}s  invented: {v['hallucinated']}"
    print(f"  {r['label']}: {status_str}")

print(f"\n  Total prompt tokens  : {total_prompt_tokens}")
print(f"  Total response tokens: {total_response_tokens}")
print(f"  Grand total tokens   : {total_prompt_tokens + total_response_tokens}")

# RPD budget warning
rpd_used  = 17   # update from your dashboard before running
rpd_limit = 500  # free tier daily limit
rpd_left  = rpd_limit - rpd_used
print(f"\n  RPD budget  : {rpd_used} used / {rpd_limit} limit  ({rpd_left} remaining)")
print(f"  This run    : {len(results)} requests")
print(f"  After run   : ~{rpd_used + len(results)} / {rpd_limit}")

est_runs = rpd_left // max(len(results), 1)
print(f"  Demo runs left (at {len(results)} req/run): ~{est_runs}")
print()
