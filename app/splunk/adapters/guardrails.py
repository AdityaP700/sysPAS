"""
SPL Guardrails & Validation
============================
Provides schema-grounding validation for generated SPL queries to detect and
prevent field hallucinations before they reach the execution engine.
"""

from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)

# Standard Splunk keywords, functions, and commands that are NOT fields
_SPL_KEYWORDS = {
    # Search command & operators
    "index", "source", "sourcetype", "host", "linecount", "splunk_server",
    "and", "or", "not", "in", "true", "false", "null", "like", "match",
    
    # Common commands
    "search", "where", "eval", "stats", "eventstats", "streamstats",
    "table", "fields", "dedup", "sort", "head", "tail", "rename",
    "join", "union", "append", "appendcols", "map", "multikv", "rex",
    "replace", "lookup", "fillnull", "addtotals", "top", "rare", "chart",
    "timechart", "bucket", "bin", "span", "earliest", "latest", "as", "by",
    
    # Stats aggregation functions
    "count", "sum", "avg", "mean", "median", "max", "min", "dc", "distinct_count",
    "values", "list", "first", "last", "range", "stdev", "var", "sumsq", "mode",
    
    # Eval functions (math, string, time, comparison)
    "coalesce", "if", "isnull", "isnotnull", "isnum", "isstr", "typeof", "len",
    "lower", "upper", "trim", "ltrim", "rtrim", "substr", "split", "mvjoin",
    "mvindex", "mvcount", "mvfilter", "mvfind", "mvappend", "mvsort", "now",
    "relative_time", "strptime", "strftime", "tonumber", "tostring", "urldecode",
    "urlencode", "abs", "ceil", "floor", "round", "pow", "sqrt", "exp", "log", "ln",
    "sigfig", "random", "md5", "sha1", "sha256", "sha512", "match", "cidrmatch",
}

# Standard metadata fields present in virtually all Splunk indexes
_STANDARD_FIELDS = {
    "_time", "_raw", "_indextime", "_cd", "_bkt", "_serial", "host", "source", "sourcetype"
}


def _extract_derived_fields(spl: str) -> set[str]:
    """
    Extract fields that are dynamically created inside the SPL query via eval,
    stats/eventstats/streamstats aliases, rename, or rex extraction.
    These are not hallucinations; they are query-defined computed aliases.
    """
    spl_lower = spl.lower()
    derived: set[str] = set()

    # 1. eval fieldname = ...
    for m in re.finditer(r'\beval\s+([a-z_][a-z0-9_]*)\s*=', spl_lower):
        derived.add(m.group(1))

    # 2. count/sum/avg/... as alias
    for m in re.finditer(
        r'\b(?:count|sum|avg|mean|median|max|min|dc|distinct_count|values|list|first|last|range|stdev|var|sumsq)'
        r'(?:\([^)]*\))?\s+as\s+([a-z_][a-z0-9_]*)',
        spl_lower
    ):
        derived.add(m.group(1))

    # 3. stats/eventstats/streamstats ... as alias
    for m in re.finditer(
        r'\b(?:eventstats|streamstats|stats|chart|timechart)\s+.*?\bas\s+([a-z_][a-z0-9_]*)',
        spl_lower
    ):
        derived.add(m.group(1))

    # 4. rename old_field as new_field
    for m in re.finditer(r'\brename\s+.*?\bas\s+([a-z_][a-z0-9_]*)', spl_lower):
        derived.add(m.group(1))

    # 5. rex field=... "(?<new_field>...)"
    for m in re.finditer(r'\brex\s+.*?\(\?<([a-z0-9_]+)>', spl_lower):
        derived.add(m.group(1))

    return derived


def validate_spl_fields(spl: str, allowed_fields: list[str]) -> set[str]:
    """
    Extracts all fields referenced in the SPL query and compares them against
    the list of allowed schema fields. Returns a set of unknown/hallucinated fields.
    """
    if not allowed_fields:
        return set()  # No schema constraints provided

    spl_lower = spl.lower()
    allowed_lower = {f.lower() for f in allowed_fields}

    candidates: set[str] = set()

    # 1. Left side of comparisons: field = value, field != value, etc.
    candidates.update(re.findall(r'\b([a-z_][a-z0-9_]*)\s*(?:=|!=|<=|>=|<|>)', spl_lower))

    # 2. Preceded by by or as: by field, as field
    candidates.update(re.findall(r'\b(?:by|as)\s+([a-z_][a-z0-9_]*)', spl_lower))

    # 3. Inside table/fields commands: table field1 field2, fields field1 field2
    for cmd in ["table", "fields"]:
        for match in re.finditer(rf'\b{cmd}\s+([^|]+)', spl_lower):
            args_str = match.group(1)
            for word in re.findall(r'\b([a-z_][a-z0-9_]*)\b', args_str):
                candidates.add(word)

    # 4. Inside function calls: count(src_ip) or coalesce(field1, field2)
    for match in re.finditer(r'\b[a-z_][a-z0-9_]*\s*\(([^)]*)\)', spl_lower):
        args_str = match.group(1)
        for word in re.findall(r'\b([a-z_][a-z0-9_]*)\b', args_str):
            candidates.add(word)

    # Filter out keywords, standard fields, and digits/short noise
    unknown = {
        c for c in candidates
        if c not in _SPL_KEYWORDS
        and c not in _STANDARD_FIELDS
        and c not in allowed_lower
        and not c.isdigit()
        and len(c) > 1
    }

    if not unknown:
        return set()

    # Filter out fields derived dynamically inside the query itself
    derived = _extract_derived_fields(spl)
    hallucinations = unknown - derived

    return hallucinations


def validate_schema_preferences(spl: str, schema_fields: list[str]) -> set[str]:
    """
    Validates schema preference rules:
    - If status field exists in schema, check if the query uses semantic keywords like 'failed', 'failure', or 'error'.
      If so, it must use 'status=failed' instead of raw keywords 'failed', 'failure', 'error'.
    Returns a set of violation description strings.
    """
    violations = set()
    if not schema_fields:
        return violations

    schema_lower = {f.lower() for f in schema_fields}
    spl_lower = spl.lower()

    if "status" in schema_lower:
        # Check if the query contains semantic keywords failed, failure, or error as separate words,
        # but not as part of the correct status=failed syntax.
        # Allow status=failed or status = failed or status="failed" or status='failed'.
        # Replace status=failed with empty string and see if failed/failure/error still exists as words.
        temp_spl = re.sub(r'\bstatus\s*=\s*["\']?failed["\']?', '', spl_lower)
        
        # Check for any usage of the keywords as separate words
        for kw in ["failed", "failure", "error"]:
            if re.search(rf'\b{kw}\b', temp_spl):
                violations.add(f"status_preference_violation_{kw}")

    return violations

