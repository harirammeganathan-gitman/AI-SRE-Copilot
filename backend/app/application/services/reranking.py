import re
from collections.abc import Sequence

from backend.app.application.dto.rag import RankedChunk, RetrievedChunk

_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)


class LexicalSemanticReranker:
    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievedChunk],
        *,
        limit: int,
    ) -> tuple[RankedChunk, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")

        query_terms = set(_TOKEN_PATTERN.findall(query.casefold()))
        ranked: list[tuple[int, RankedChunk]] = []
        for original_rank, candidate in enumerate(candidates):
            candidate_terms = set(_TOKEN_PATTERN.findall(candidate.text.casefold()))
            lexical_score = (
                len(query_terms.intersection(candidate_terms)) / len(query_terms)
                if query_terms
                else 0.0
            )
            score = 0.7 * lexical_score + 0.3 * candidate.similarity_score
            ranked.append((original_rank, RankedChunk(candidate, score)))

        ranked.sort(key=lambda item: (-item[1].score, -item[1].chunk.similarity_score, item[0]))
        return tuple(item[1] for item in ranked[:limit])
