import os
import re

import numpy as np
import ollama
from dotenv import load_dotenv

from chatbot.embed import embed
from db.database import get_connection

load_dotenv()
CHAT_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
CHUNKS_PER_PHONE = 4   # when specific phones are named
CHUNKS_GENERAL = 8     # when no phone is named

RANKING_WORDS = ("best", "most", "biggest", "largest", "highest", "lowest", "cheapest",
                 "longest", "smallest", "lightest", "heaviest", "fastest", "which", "top", "rank")

SYSTEM_PROMPT = """You are a Samsung phone expert. Answer using ONLY the facts in the context.
- If the context doesn't contain the answer, say you don't have that information.
- Quote exact numbers from the context (mAh, inches, MP, GB, chipset names).
- If several phones tie, say they tie instead of picking one.
- Prices are current market prices from GSMArena, not launch prices.
- For battery life, use the battery tests, not just mAh. "Active use score" (newer test) and "Endurance rating" (older test) are different scales: only compare phones on the same test.
- Keep answers short and clear."""


# ---------- Load all chunks once ----------

def load_chunks():
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT p.name, c.content, c.embedding
            FROM phone_chunks c JOIN phones p ON p.id = c.phone_id
        """).fetchall()
    names = [r[0] for r in rows]
    texts = [r[1] for r in rows]
    matrix = np.array([r[2] for r in rows], dtype=np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)   # normalise for cosine similarity
    return names, texts, matrix

NAMES, TEXTS, MATRIX = load_chunks()
ALL_PHONES = sorted(set(NAMES))


# ---------- 1. Which phones does the question mention? ----------

def short_name(full_name):
    """'Samsung Galaxy S22 Ultra 5G' -> 's22 ultra'"""
    return full_name.replace("Samsung Galaxy ", "").replace(" 5G", "").lower()

def phones_mentioned(question):
    q = question.lower()
    found = []
    # Longest names first, so "s23 ultra" is matched before "s23".
    for full in sorted(ALL_PHONES, key=lambda n: -len(short_name(n))):
        pattern = r"\b" + re.escape(short_name(full)) + r"\b"
        if re.search(pattern, q):
            found.append(full)
            q = re.sub(pattern, " ", q)   # remove it so "s23" doesn't match again
    return found


# ---------- 2. Find the most relevant chunks ----------

def retrieve(question, phones):
    q = np.array(embed([question], "search_query: ")[0], dtype=np.float32)
    q /= np.linalg.norm(q)
    scores = MATRIX @ q   # cosine similarity with every chunk

    if not phones:
        best = np.argsort(-scores)[:CHUNKS_GENERAL]
        return [TEXTS[i] for i in best]

    chunks = []
    for phone in phones:
        idx = [i for i, n in enumerate(NAMES) if n == phone]
        idx.sort(key=lambda i: -scores[i])
        chunks += [TEXTS[i] for i in idx[:CHUNKS_PER_PHONE]]
    return chunks


# ---------- 3. Summary table for ranking questions ----------

def overview_table():
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT p.name, p.release_year, p.chipset, p.display_size_in, p.refresh_rate_hz,
                   p.max_ram_gb, p.main_camera_mp, p.battery_mah,
                   (SELECT string_agg(s.spec_value, '; ')
                      FROM specifications s
                     WHERE s.phone_id = p.id
                       AND s.category = 'Our Tests'
                       AND s.spec_name LIKE 'Battery%') AS battery_tests,
                   p.weight_g, p.price_text
            FROM phones p ORDER BY p.name
        """).fetchall()
    lines = ["All phones (name | year | chipset | screen in | Hz | max RAM GB | main cam MP "
             "| battery mAh | battery tests | weight g | price):"]
    for r in rows:
        lines.append(" | ".join(str(x) for x in r))
    return "\n".join(lines)


# ---------- 4. Ask the model ----------

class Chatbot:
    def __init__(self):
        self.history = []        # past messages, so follow-up questions work
        self.last_phones = []    # phones from the previous question

    def ask(self, question):
        phones = phones_mentioned(question) or self.last_phones
        self.last_phones = phones

        context_parts = retrieve(question, phones)
        if not phones or any(w in question.lower() for w in RANKING_WORDS):
            context_parts.insert(0, overview_table())
        context = "\n\n".join(context_parts)

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages += self.history[-6:]   # last 3 exchanges
        messages.append({"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"})

        response = ollama.chat(model=CHAT_MODEL, messages=messages, options={"temperature": 0.2})
        answer = response.message.content

        self.history += [{"role": "user", "content": question},
                         {"role": "assistant", "content": answer}]
        return {"answer": answer, "phones": phones, "sources": len(context_parts)}


# ---------- Try it in the terminal ----------

if __name__ == "__main__":
    bot = Chatbot()
    print("Ask about Samsung phones (type 'quit' to exit)\n")
    while True:
        question = input("You: ").strip()
        if question.lower() in ("quit", "exit"):
            break
        if question:
            result = bot.ask(question)
            print(f"\nBot: {result['answer']}\n   [phones: {result['phones'] or 'all'}]\n")