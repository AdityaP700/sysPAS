"""
Index Resolver
==============
Responsible for dynamically mapping natural-language data sources or
hallucinated index names into exact Splunk `index` and `sourcetype` pairs
for demo and schema grounding.
"""

def resolve_index(data_source: str) -> str:
    """
    Resolves natural language data sources to actual Splunk index and sourcetype pairs.
    If the data source is empty or unknown, it defaults to 'index=main'.
    """
    if not data_source:
        return "index=main"
        
    ds_lower = data_source.lower()
    
    if "auth" in ds_lower or "login" in ds_lower:
        return "index=main sourcetype=security_logs"
    if "endpoint" in ds_lower or "host" in ds_lower:
        return "index=main sourcetype=endpoint_logs"
    if "network" in ds_lower or "traffic" in ds_lower:
        return "index=main sourcetype=network_traffic"
        
    return "index=main"
