import os

import ollama
from dotenv import load_dotenv

from db.database import get_connection

load_dotenv()
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")


def build_chunks(conn):
    """One chunk per phone per spec category, e.g. 'Samsung Galaxy S23 - Battery'."""
    rows = conn.execute("""
        SELECT p.id, p.name, s.category, s.spec_name, s.spec_value
        FROM specifications s
        JOIN phones p ON p.id = s.phone_id
        ORDER BY p.id, s.id
    """).fetchall()

    groups = {}
    for phone_id, name, category, spec_name, value in rows:
        groups.setdefault((phone_id, name, category), []).append(f"{spec_name}: {value}")

    chunks = []
    for (phone_id, name, category), lines in groups.items():
        text = f"{name} - {category}\n" + "\n".join(lines)
        chunks.append((phone_id, text))
    return chunks


def embed(texts, prefix):
    """Turn texts into vectors. nomic-embed-text wants a prefix saying what the text is for."""
    response = ollama.embed(model=EMBED_MODEL, input=[prefix + t for t in texts])
    return response.embeddings


def main():
    with get_connection() as conn:
        chunks = build_chunks(conn)
        print(f"Built {len(chunks)} chunks. Example:\n{chunks[0][1]}\n")

        vectors = embed([text for _, text in chunks], "search_document: ")
        print(f"Each embedding has {len(vectors[0])} numbers")

        conn.execute("DELETE FROM phone_chunks")   # start fresh each run
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO phone_chunks (phone_id, content, embedding) VALUES (%s, %s, %s)",
                [(phone_id, text, vec) for (phone_id, text), vec in zip(chunks, vectors)],
            )
    print("Saved all chunks with embeddings")


if __name__ == "__main__":
    main()