# =============================================================================
# FILE: scripts/build_index.py
# WHAT: One-time script that builds the FAISS and BM25 search indexes
#       from the runbook .md files in data/runbooks/.
# WHY:  RAGService needs pre-built indexes to search fast at query time.
#       Building the index is slow (embedding takes time) so we do it offline
#       once, save to disk, and RAGService loads from disk at startup.
#       Run this script again whenever you add or update runbooks.
# HOW:  Read .md files → chunk text → embed with sentence-transformers
#       → build FAISS index → build BM25 index → save all to data/
# OUTPUTS:
#   data/runbooks.index  ← FAISS vector index (searched by RAGService)
#   data/chunks.json     ← chunk text + source filename (returned by RAGService)
#   data/bm25.pkl        ← BM25 keyword index (searched by RAGService)
# CONNECTED TO:
#   ← data/runbooks/*.md — source runbook files (you write these)
#   → backend/services/rag_service.py loads the output files at startup
# RUN FROM PROJECT ROOT:
#   python3 scripts/build_index.py
# =============================================================================

from sentence_transformers import SentenceTransformer  # turns text into vectors
import faiss                                           # Facebook's vector similarity search library
import json                                            # for saving chunk metadata
import pickle                                          # for saving the BM25 index
import numpy as np                                     # for array operations on embeddings
from pathlib import Path                               # cross-platform file paths
from rank_bm25 import BM25Okapi                        # keyword search algorithm

# --- Paths and constants ---
RUNBOOKS_DIR = Path("data/runbooks")       # where your .md runbook files live
INDEX_PATH = Path("data/runbooks.index")   # where FAISS index will be saved
CHUNKS_PATH = Path("data/chunks.json")     # where chunk text + metadata will be saved
BM25_PATH = Path("data/bm25.pkl")          # where BM25 index will be saved
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"  # free, 80MB, runs on CPU
CHUNK_SIZE = 512    # words per chunk — big enough for context, small enough to be specific
OVERLAP = 128       # shared words between adjacent chunks — prevents sentences being cut in half


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    # Split text into overlapping chunks.
    # overlap means chunk N and chunk N+1 share 128 words at the boundary.
    # This prevents a sentence being split across two chunks and losing meaning in both.
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        chunk = " ".join(words[start:start + size])
        chunks.append(chunk)
        start += size - overlap     # advance by (size - overlap), not size — creates the overlap
    return chunks


def build_index():
    print("Loading embedding model...")
    model = SentenceTransformer(EMBED_MODEL)    # downloads model on first run (~80MB), cached after

    all_chunks = []     # stores {"text": ..., "source": ...} for each chunk
    all_texts = []      # just the text strings — used for embedding + BM25

    # --- Step 1: Read and chunk all runbook files ---
    md_files = list(RUNBOOKS_DIR.glob("*.md"))
    if not md_files:
        print(f"No .md files found in {RUNBOOKS_DIR}. Add runbooks first.")
        return

    for filepath in md_files:
        text = filepath.read_text(encoding="utf-8")
        chunks = chunk_text(text, CHUNK_SIZE, OVERLAP)
        for chunk in chunks:
            all_chunks.append({"text": chunk, "source": filepath.name})  # track which file it came from
            all_texts.append(chunk)

    print(f"Built {len(all_chunks)} chunks from {len(md_files)} runbook(s).")

    # --- Step 2: Embed all chunks into vectors ---
    print("Embedding chunks...")
    embeddings = model.encode(all_texts, show_progress_bar=True, convert_to_numpy=True)

    # Normalize vectors to unit length — required for cosine similarity with IndexFlatIP.
    # Inner product on normalized vectors = cosine similarity.
    faiss.normalize_L2(embeddings)

    # --- Step 3: Build and save FAISS index ---
    dimension = embeddings.shape[1]                     # 384 for all-MiniLM-L6-v2
    index = faiss.IndexFlatIP(dimension)                # IP = Inner Product (cosine similarity)
    index.add(embeddings)                               # add all vectors to the index
    faiss.write_index(index, str(INDEX_PATH))           # save to disk
    print(f"FAISS index saved to {INDEX_PATH} ({index.ntotal} vectors).")

    # --- Step 4: Save chunk metadata ---
    CHUNKS_PATH.write_text(json.dumps(all_chunks, indent=2))
    print(f"Chunk metadata saved to {CHUNKS_PATH}.")

    # --- Step 5: Build and save BM25 index ---
    # BM25 works on tokenized text — split each chunk into a list of words
    tokenized = [chunk.lower().split() for chunk in all_texts]
    bm25 = BM25Okapi(tokenized)
    BM25_PATH.write_bytes(pickle.dumps(bm25))           # serialize to disk
    print(f"BM25 index saved to {BM25_PATH}.")

    print("Index build complete.")


if __name__ == "__main__":
    build_index()
