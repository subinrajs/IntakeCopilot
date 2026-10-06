"""Protocol retrieval: hybrid search in one SQL query (design doc: Protocol retrieval design).

1. Hard filter on modality (an MRI request never sees CT protocols).
2. Cosine similarity between the case query and each protocol's embedding.
3. Keyword boost from Postgres full-text search on the protocol's name and indications.
4. Top k by combined score.
"""

import re
from dataclasses import dataclass
from typing import Any

from psycopg import Connection

from intake.schemas.fields import CaseFields

KEYWORD_WEIGHT = 0.3
TOP_K = 5
_STOPWORDS = frozenset(
    [
        "the",
        "and",
        "for",
        "with",
        "query",
        "from",
        "over",
        "into",
        "this",
        "that",
        "has",
        "have",
        "had",
        "are",
        "was",
        "were",
        "not",
        "but",
        "of",
        "in",
        "on",
        "at",
        "to",
        "a",
        "an",
        "or",
        "by",
        "as",
        "is",
        "it",
        "be",
        "no",
    ]
)


@dataclass(frozen=True)
class Candidate:
    id: str
    name: str
    modality: str
    body_part: str
    contrast: str
    indications: str
    vector_score: float
    keyword_score: float

    @property
    def score(self) -> float:
        return self.vector_score + KEYWORD_WEIGHT * self.keyword_score

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "contrast": self.contrast,
            "score": round(self.score, 4),
            "vector_score": round(self.vector_score, 4),
            "keyword_score": round(self.keyword_score, 4),
        }


def query_text(fields: CaseFields) -> str:
    """Modality + body part + indication + key history. Not the whole requisition (noise)."""
    parts = [
        fields.modality or "",
        fields.body_part or "",
        fields.clinical_indication or "",
        *fields.relevant_history[:3],
    ]
    return ". ".join(p for p in parts if p)


def keyword_query(text: str) -> str:
    words = sorted({w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in _STOPWORDS})
    return " | ".join(words)


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vector) + "]"


def search(
    conn: Connection[Any],
    query_vector: list[float],
    text: str,
    modality: str | None,
    k: int = TOP_K,
) -> list[Candidate]:
    rows = conn.execute(
        """
        WITH q AS (
          SELECT %(vec)s::vector AS v,
                 CASE WHEN %(tsq)s = '' THEN NULL ELSE to_tsquery('english', %(tsq)s) END AS tq
        )
        SELECT p.id, p.name, p.modality, p.body_part, p.contrast, p.indications,
               1 - (p.embedding <=> q.v) AS vector_score,
               coalesce(ts_rank_cd(p.search_tsv, q.tq, 32), 0) AS keyword_score
        FROM protocols p, q
        WHERE p.embedding IS NOT NULL
          AND (%(modality)s::text IS NULL OR p.modality = %(modality)s::text)
        ORDER BY (1 - (p.embedding <=> q.v))
                 + %(weight)s * coalesce(ts_rank_cd(p.search_tsv, q.tq, 32), 0) DESC,
                 p.id
        LIMIT %(k)s
        """,
        {
            "vec": vector_literal(query_vector),
            "tsq": keyword_query(text),
            "modality": modality,
            "weight": KEYWORD_WEIGHT,
            "k": k,
        },
    ).fetchall()
    return [
        Candidate(
            id=r["id"],
            name=r["name"],
            modality=r["modality"],
            body_part=r["body_part"],
            contrast=r["contrast"],
            indications=r["indications"],
            vector_score=float(r["vector_score"]),
            keyword_score=float(r["keyword_score"]),
        )
        for r in rows
    ]
