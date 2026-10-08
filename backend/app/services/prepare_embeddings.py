"""Download a local embedding model once; no paid account or API key required."""
from fastembed import TextEmbedding
from app.core.config import settings
from app.services.knowledge_service import data_root

if __name__ == "__main__":
    TextEmbedding(model_name=settings.EMBEDDING_MODEL, cache_dir=str(data_root() / "models"), threads=2)
    print("Embedding model cache is ready. API indexing runs offline.")
