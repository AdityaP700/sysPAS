from typing import List, Tuple, Optional


def levenshtein_similarity(s1: str, s2: str) -> float:
    """Computes the normalized Levenshtein similarity between two strings."""
    if not s1 or not s2:
        return 0.0
    if s1 == s2:
        return 1.0

    m, n = len(s1), len(s2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if s1[i - 1] == s2[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + 1)

    distance = dp[m][n]
    return 1.0 - (distance / max(m, n))


class ExactMatcher:
    def score(self, term: str, fields: List[str]) -> List[Tuple[str, float]]:
        candidates = []
        for f in fields:
            if term == f.lower():
                candidates.append((f, 1.0))
        return candidates


class SynonymMatcher:
    def __init__(self):
        self._synonyms = {
            "source_ip": "src_ip",
            "ip": "src_ip",
            "source ip": "src_ip",
            "client_ip": "src_ip",
            "client ip": "src_ip",
            "ip_address": "src_ip",
            "ip address": "src_ip",
            
            "username": "user",
            "user": "user",
            "user_name": "user",
            "user name": "user",
            
            "action": "action",
            "act": "action",
            
            "status": "status",
            "stat": "status",
            
            "threat_score": "threat_score",
            "threat score": "threat_score",
            "score": "threat_score",
            
            "category": "category",
            "cat": "category"
        }

    def score(self, term: str, fields: List[str]) -> List[Tuple[str, float]]:
        candidates = []
        canonical = self._synonyms.get(term)
        if canonical:
            for f in fields:
                if canonical == f.lower():
                    candidates.append((f, 0.9))
        return candidates


class SubstringMatcher:
    def score(self, term: str, fields: List[str]) -> List[Tuple[str, float]]:
        candidates = []
        for f in fields:
            f_lower = f.lower()
            if term in f_lower or f_lower in term:
                candidates.append((f, 0.75))
        return candidates


class LevenshteinMatcher:
    def score(self, term: str, fields: List[str]) -> List[Tuple[str, float]]:
        candidates = []
        for f in fields:
            sim = levenshtein_similarity(term, f.lower())
            if sim >= 0.6:
                # Scale weight to 0.7 - 0.8
                candidates.append((f, round(0.7 + (sim * 0.1), 2)))
        return candidates


class FieldIntelligenceEngine:
    """Resolves runbook terms to schema fields using a ranked pipeline of matcher stages."""

    def __init__(self):
        self._pipeline = [
            ExactMatcher(),
            SynonymMatcher(),
            SubstringMatcher(),
            LevenshteinMatcher()
        ]

    def resolve_field(self, term: str, schema_fields: List[str]) -> Tuple[Optional[str], float]:
        """
        Runs the term through the ranked matcher pipeline.
        Returns the highest-scoring field candidate and its associated confidence score.
        """
        term_clean = term.strip().lower()
        if not schema_fields:
            return None, 0.5

        # Dictionary to hold the highest score for each resolved field candidate
        scores = {}

        for stage in self._pipeline:
            candidates = stage.score(term_clean, schema_fields)
            for field, score in candidates:
                if field not in scores or score > scores[field]:
                    scores[field] = score

        if not scores:
            return None, 0.5

        # Rank candidates by score descending
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        best_field, best_score = ranked[0]

        # Enforce threshold
        if best_score >= 0.6:
            return best_field, best_score

        return None, 0.5
