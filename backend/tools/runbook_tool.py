# =============================================================================
# FILE: backend/tools/runbook_tool.py
# WHAT: Tool that searches the runbook knowledge base for relevant context.
#       Wraps RAGService so InvestigatorAgent can call it as a "tool action".
# WHY:  RunbookTool lets the agent actively DECIDE when to search for runbooks
#       rather than always searching (like InvestigationService did).
#       The agent might first fetch logs, see "connection refused", THEN decide
#       to search runbooks for "DB connection pool" — more targeted retrieval.
# OOP:  Composition — RunbookTool HAS-A RAGService (injected, not created here).
#       Inheritance — implements the BaseTool interface.
# CONNECTED TO:
#   ← tools/base_tool.py        — inherits BaseTool interface
#   ← services/rag_service.py   — delegates vector + keyword search here
#   → agents/investigator_agent.py — added to the agent's tools list
# =============================================================================

from tools.base_tool import BaseTool         # abstract interface all tools implement
from services.rag_service import RAGService  # does the FAISS + BM25 hybrid search


class RunbookTool(BaseTool):

    def __init__(self, rag_svc: RAGService):
        # rag_svc is the shared instance loaded once at worker startup.
        # Loading the embedding model takes ~2s — we reuse the same instance.
        self.rag_svc = rag_svc

    @property
    def name(self) -> str:
        return "search_runbooks"

    @property
    def description(self) -> str:
        return (
            "Searches the internal runbook knowledge base for troubleshooting guidance. "
            "Input: {\"query\": \"<description of the problem>\"}. "
            "Returns the most relevant runbook sections (up to 3 chunks)."
        )

    async def run(self, input_data: dict) -> str:
        query = input_data.get("query", "")

        if not query:
            return "Error: query is required. Provide {\"query\": \"<problem description>\"}."

        # RAGService.retrieve() is synchronous (FAISS is CPU-bound, not I/O-bound).
        # Returning top_k=3 chunks — fewer than the default 5 to keep context tight.
        chunks = self.rag_svc.retrieve(query, top_k=3)

        if not chunks:
            return f"[search_runbooks result for '{query}']\nNo relevant runbook content found."

        # Join chunks with a separator so the LLM can distinguish between sections
        joined = "\n---\n".join(chunks)
        return f"[search_runbooks result for '{query}']\n{joined}"
