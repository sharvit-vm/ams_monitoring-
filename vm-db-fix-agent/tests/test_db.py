import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from clients.postgres_client import PostgreSQLClient

db = PostgreSQLClient()

result = db.fetch_one(
    "SELECT NOW();"
)

print(result)