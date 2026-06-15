from app.schema.intelligence import FieldIntelligenceEngine, levenshtein_similarity


def test_levenshtein_similarity():
    """Verify that Levenshtein similarity calculation works correctly."""
    assert levenshtein_similarity("src_ip", "src_ip") == 1.0
    assert levenshtein_similarity("user", "usr") == 0.75  # 1 - (1 / 4)
    assert levenshtein_similarity("user", "different") < 0.5


def test_resolve_exact_match():
    """Verify exact match yields a weight of 1.0."""
    engine = FieldIntelligenceEngine()
    fields = ["src_ip", "user", "action"]
    
    field, weight = engine.resolve_field("user", fields)
    assert field == "user"
    assert weight == 1.0


def test_resolve_synonym_match():
    """Verify standard synonym mapping yields a weight of 0.9."""
    engine = FieldIntelligenceEngine()
    fields = ["src_ip", "user", "action"]
    
    # "source_ip" resolves to canonical "src_ip" in fields
    field, weight = engine.resolve_field("source_ip", fields)
    assert field == "src_ip"
    assert weight == 0.9
    
    # "username" resolves to canonical "user"
    field, weight = engine.resolve_field("username", fields)
    assert field == "user"
    assert weight == 0.9


def test_resolve_heuristic_match():
    """Verify substring heuristics yield a weight of 0.75."""
    engine = FieldIntelligenceEngine()
    fields = ["user_login_attempts", "destination_host"]
    
    # "user" is a substring of "user_login_attempts"
    field, weight = engine.resolve_field("user", fields)
    assert field == "user_login_attempts"
    assert weight == 0.75


def test_resolve_levenshtein_match():
    """Verify Levenshtein distance resolver matches close fields with scaled weights (0.7 - 0.8)."""
    engine = FieldIntelligenceEngine()
    fields = ["src_ip_addr", "destination"]
    
    # "src_ip" has high similarity to "src_ip_addr" (distance 5, max len 11, similarity 0.54? Wait, let's use user -> usr which has 0.75 similarity)
    # Let's say fields is ["user_log"] and term is "usr_log"
    # distance is 1 (u-s-r vs u-s-e), max len is 8, similarity is 1 - 1/8 = 0.875. Score = 0.7 + (0.875 * 0.1) = 0.79
    field, weight = engine.resolve_field("usr_log", ["user_log"])
    assert field == "user_log"
    assert 0.7 <= weight <= 0.8


def test_resolve_fallback_no_match():
    """Verify unresolved fields yield a weight of 0.5."""
    engine = FieldIntelligenceEngine()
    fields = ["src_ip", "user"]
    
    field, weight = engine.resolve_field("unrelated_field", fields)
    assert field is None
    assert weight == 0.5
