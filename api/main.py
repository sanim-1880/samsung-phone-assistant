"""Step 4: a FastAPI service for the chatbot and the review agents.

Run:   uvicorn api.main:app --reload
Docs:  http://127.0.0.1:8000/docs
"""
import uuid

import ollama
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agents.review_crew import generate_review
from chatbot.chatbot import Chatbot, phones_mentioned
from db.database import get_connection

app = FastAPI(
    title="Samsung Phone Assistant",
    description="Ask questions about 15 Samsung phones and get AI-written reviews. "
                "Data scraped from GSMArena, answers from a local Llama 3.2 model.",
    version="1.0",
)

sessions: dict[str, Chatbot] = {}   # one Chatbot (with its own memory) per conversation


# ---------- Request / response shapes ----------

class ChatRequest(BaseModel):
    question: str = Field(..., min_length=2, examples=["What are the camera specs of the Galaxy S23?"])
    session_id: str | None = Field(None, description="Send back the session_id from a previous "
                                                     "answer to ask a follow-up question.")

class ChatResponse(BaseModel):
    answer: str
    phones: list[str]
    session_id: str

class ReviewRequest(BaseModel):
    phone: str = Field(..., examples=["Galaxy S24 Ultra"])
    refresh: bool = Field(False, description="Write a new review even if one is saved.")

class ReviewResponse(BaseModel):
    phone: str
    review: str


# ---------- Helpers ----------

def resolve_phone(name: str) -> str:
    found = phones_mentioned(name)
    if not found:
        raise HTTPException(status_code=404, detail=f"No phone matching '{name}'. See GET /phones.")
    return found[0]


# ---------- Endpoints ----------
# They're plain `def` (not async), so FastAPI runs each request in its own worker thread.
# A slow review doesn't block other people's chat questions.

@app.get("/health")
def health():
    """Check that the database and Ollama are reachable."""
    with get_connection() as conn:
        phones = conn.execute("SELECT COUNT(*) FROM phones").fetchone()[0]
    models = [m.model for m in ollama.list().models]
    return {"status": "ok", "phones_in_db": phones, "ollama_models": models}


@app.get("/phones")
def list_phones():
    """All phones with their key specs."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT name, release_year, chipset, display_size_in, refresh_rate_hz, max_ram_gb,
                   main_camera_mp, battery_mah, weight_g, price_text
            FROM phones ORDER BY release_year, name
        """).fetchall()
    keys = ["name", "release_year", "chipset", "display_size_in", "refresh_rate_hz", "max_ram_gb",
            "main_camera_mp", "battery_mah", "weight_g", "price"]
    return [dict(zip(keys, r)) for r in rows]


@app.get("/phones/{name}")
def phone_specs(name: str):
    """The full GSMArena spec sheet of one phone, grouped by category."""
    phone = resolve_phone(name)
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT s.category, s.spec_name, s.spec_value
            FROM specifications s JOIN phones p ON p.id = s.phone_id
            WHERE p.name = %s ORDER BY s.id
        """, (phone,)).fetchall()
    specs: dict[str, dict[str, str]] = {}
    for category, spec_name, value in rows:
        specs.setdefault(category, {})[spec_name] = value
    return {"phone": phone, "specs": specs}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    """Ask the RAG chatbot a question. Reuse session_id for follow-up questions."""
    session_id = req.session_id if req.session_id in sessions else str(uuid.uuid4())
    bot = sessions.setdefault(session_id, Chatbot())
    result = bot.ask(req.question)
    return ChatResponse(answer=result["answer"], phones=result["phones"], session_id=session_id)


@app.post("/review", response_model=ReviewResponse)
def review(req: ReviewRequest):
    """Run the two CrewAI agents to write a review. Saved reviews come back instantly;
    a new one takes a few minutes on a CPU-only machine."""
    phone = resolve_phone(req.phone)
    return ReviewResponse(phone=phone, review=generate_review(phone, refresh=req.refresh))