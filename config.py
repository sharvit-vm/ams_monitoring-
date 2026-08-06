import os
from dotenv import load_dotenv
from neo4j import GraphDatabase
from pinecone import Pinecone

load_dotenv(override=True)

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")
EMBEDDING_MODEL      = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "1536"))
MAX_CHUNK_TOKENS = int(os.getenv("MAX_CHUNK_TOKENS", "800"))
MAX_HIERARCHY_LEVELS = int(os.getenv("MAX_HIERARCHY_LEVELS", "8"))
CACHE_DIR = os.getenv("CACHE_DIR", "cache")

_neo4j_driver = None
_pinecone_index = None
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    api_key=OPENAI_API_KEY,
)

agent_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    api_key=OPENAI_API_KEY,
)

def get_neo4j_driver():
    """Returns a singleton Neo4j driver. Call driver.close() when done."""
    global _neo4j_driver
    if _neo4j_driver is None:
        if not NEO4J_URI or not NEO4J_PASSWORD:
            raise ValueError("NEO4J_URI and NEO4J_PASSWORD must be set in .env")
        _neo4j_driver = GraphDatabase.driver(
            NEO4J_URI,
            auth=(NEO4J_USERNAME, NEO4J_PASSWORD)
        )
    return _neo4j_driver

def get_pinecone_index():
    """Returns a singleton Pinecone index client."""
    global _pinecone_index
    if _pinecone_index is None:
        if not PINECONE_API_KEY or not PINECONE_INDEX_NAME:
            raise ValueError("PINECONE_API_KEY and PINECONE_INDEX_NAME must be set in .env")
        pc = Pinecone(api_key=PINECONE_API_KEY)
        _pinecone_index = pc.Index(PINECONE_INDEX_NAME)
    return _pinecone_index

def verify_connections():
    """
    Call this once at startup to confirm both DBs are reachable.
    Raises an exception immediately if either connection fails.
    """
    print("Verifying Neo4j connection...")
    driver = get_neo4j_driver()
    with driver.session() as session:
        result = session.run("RETURN 'Neo4j connected' AS status")
        print(" ", result.single()["status"])

    print("Verifying Pinecone connection...")
    index = get_pinecone_index()
    stats = index.describe_index_stats()
    print(f"  Pinecone connected - total vectors: {stats['total_vector_count']}")

    print("All connections verified.\n")

if __name__ == "__main__":
    verify_connections()


