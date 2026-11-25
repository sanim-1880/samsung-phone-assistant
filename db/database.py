import os
import psycopg
from dotenv import load_dotenv

load_dotenv()  # reads the .env file in the project folder

def get_connection():
    return psycopg.connect(
        host=os.getenv("PGHOST"),
        port=os.getenv("PGPORT"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
        dbname=os.getenv("PGDATABASE"),
    )

from pathlib import Path

SCHEMA_FILE = Path(__file__).parent / "schema.sql"

def create_tables():
    with get_connection() as conn:
        conn.execute(SCHEMA_FILE.read_text())
    print("Tables created")

if __name__ == "__main__":
    create_tables()