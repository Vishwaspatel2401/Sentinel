# =============================================================================
# FILE: backend/services/rag_service.py
# WHAT: Searches runbook files for chunks relevant to a query.
#       Uses hybrid search: FAISS (semantic/meaning) + BM25 (keyword).
# WHY:  RAG = Retrieval Augmented Generation. Instead of asking the LLM to
#       answer from memory, we first find the relevant runbook sections and
#       give them as context. The LLM then reasons from actual company knowledge.
#       Hybrid search beats either method alone:
#         FAISS finds conceptually similar content (even with different words)
#         BM25 finds exact technical terms like pool_size, OOMKilled
# FLOW: query → embed query → FAISS search (top 10) + BM25 search (top 10)
#       → merge scores (60% FAISS + 40% BM25) → return top 5 chunks as strings
# CONNECTED TO:
#   ← scripts/build_index.py must be run first to create the index files
#   ← data/runbooks.index, data/chunks.json, data/bm25.pkl (loaded at startup)
#   → services/investigation_service.py calls retrieve() to get runbook context
#     before building the LLM prompt
# =============================================================================

import json                                  # for loading chunk metadata
import logging                              # structured logging
import pickle                               # for loading BM25 index
import numpy as np                          # for vector operations
import faiss                                # for vector similarity search
from pathlib import Path                    # cross-platform file paths
from rank_bm25 import BM25Okapi             # keyword search
from sentence_transformers import SentenceTransformer  # for embedding the query
from config import settings                 # data_dir from .env (or docker-compose env)

logger = logging.getLogger(__name__)

# DATA_DIR is read from config so it works in both environments:
#   Local dev:  Sentinel/data/  (default in config.py — resolved relative to config.py)
#   Docker:     /app/data       (set via DATA_DIR env var in docker-compose.yml)
# This avoids hardcoding __file__-relative paths that break when the container
# filesystem layout differs from the local project layout.
_DATA_DIR   = Path(settings.data_dir)
INDEX_PATH  = _DATA_DIR / "runbooks.index"
CHUNKS_PATH = _DATA_DIR / "chunks.json"
BM25_PATH   = _DATA_DIR / "bm25.pkl"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


# RAGService: searches runbooks for chunks relevant to a query.
# Called by the Investigator Agent (Week 2) via RunbookTool.
# Two search methods combined: FAISS (semantic) + BM25 (keyword) = hybrid search.
class RAGService:

    def __init__(self):
        # Load everything from disk at startup — not per query.
        # Loading a model takes ~2s. Doing it per query would make every investigation slow.
        logger.info("Loading RAG components", extra={"model": EMBED_MODEL})
        self.model = SentenceTransformer(EMBED_MODEL)               # embedding model
        self.index = faiss.read_index(str(INDEX_PATH))              # FAISS vector index
        self.chunks = json.loads(CHUNKS_PATH.read_text())           # list of {"text", "source"} dicts
        self.bm25: BM25Okapi = pickle.loads(BM25_PATH.read_bytes()) # BM25 keyword index

        # Pre-tokenize for BM25 — stored so we don't re-split on every query
        self.tokenized_chunks = [c["text"].lower().split() for c in self.chunks]

    def retrieve(self, query: str, top_k: int = 5) -> list[str]:
        # --- Step 1: Dense search with FAISS (semantic similarity) ---
        query_embedding = self.model.encode([query], convert_to_numpy=True)
        faiss.normalize_L2(query_embedding)                         # normalize for cosine similarity

        # Search for top 10 nearest vectors — returns distances and indices
        distances, indices = self.index.search(query_embedding, 10)
        dense_scores: dict[int, float] = {}
        for idx, score in zip(indices[0], distances[0]):
            if idx != -1:                                           # -1 means no result
                dense_scores[idx] = float(score)

        # --- Step 2: Sparse search with BM25 (keyword matching) ---
        tokenized_query = query.lower().split()
        bm25_raw_scores = self.bm25.get_scores(tokenized_query)     # score for every chunk

        # Get top 10 by BM25 score
        top_bm25_indices = np.argsort(bm25_raw_scores)[::-1][:10]
        max_bm25 = max(bm25_raw_scores) if max(bm25_raw_scores) > 0 else 1.0  # avoid divide by zero
        bm25_scores: dict[int, float] = {
            int(i): bm25_raw_scores[i] / max_bm25                  # normalize to 0-1
            for i in top_bm25_indices
        }

        # Normalize dense scores to 0-1 as well
        max_dense = max(dense_scores.values()) if dense_scores else 1.0
        dense_scores = {i: s / max_dense for i, s in dense_scores.items()}

        # --- Step 3: Merge scores from both methods ---
        # Union of all candidate indices from both searches
        all_indices = set(dense_scores.keys()) | set(bm25_scores.keys())
        combined: dict[int, float] = {}
        for idx in all_indices:
            # 60% weight to semantic, 40% to keyword — semantic is more reliable
            combined[idx] = (
                0.6 * dense_scores.get(idx, 0.0) +
                0.4 * bm25_scores.get(idx, 0.0)
            )

        # --- Step 4: Return top_k chunks sorted by combined score ---
        top_indices = sorted(combined, key=combined.get, reverse=True)[:top_k]
        return [self.chunks[i]["text"] for i in top_indices]
