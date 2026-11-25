import os
import sys

from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")   # stops the "share trace?" prompt
os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "true")

from crewai import LLM, Agent, Crew, Process, Task
from crewai.tools import tool

from chatbot.chatbot import phones_mentioned
from db.database import get_connection

llm = LLM(
    model=f"ollama/{os.getenv('OLLAMA_MODEL', 'llama3.2:3b')}",
    base_url="http://localhost:11434",
    temperature=0.3,
)


# ---------- The tool the spec agent uses ----------

@tool("get_phone_specs", result_as_answer=True)
def get_phone_specs(phone_name: str) -> str:
    """Get the full spec sheet of one Samsung phone from the database.
    Input: the phone name, for example 'Galaxy S23' or 'S24 Ultra'."""
    found = phones_mentioned(phone_name)
    if not found:
        return f"No phone called '{phone_name}' in the database."
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT s.category, s.spec_name, s.spec_value
            FROM specifications s JOIN phones p ON p.id = s.phone_id
            WHERE p.name = %s
              -- keep "Network - Technology" (2G/3G/4G/5G) but skip the long lists of radio bands
              AND NOT (s.category = 'Network' AND s.spec_name <> 'Technology')
            ORDER BY s.id
            """,
            (found[0],),
        ).fetchall()
    lines = [found[0]] + [f"{cat} - {name}: {value}" for cat, name, value in rows]
    return "\n".join(lines)


# ---------- The two agents ----------

spec_agent = Agent(
    role="Phone Spec Researcher",
    goal="Fetch accurate, complete specifications for a Samsung phone from the database",
    backstory="You are meticulous about data. You never guess a number; you always look it up "
              "with the get_phone_specs tool and report it exactly.",
    tools=[get_phone_specs],
    llm=llm,
    max_iter=3,          # one tool call is enough; stops it from looping
    verbose=False,
)

review_agent = Agent(
    role="Tech Reviewer",
    goal="Write an honest, detailed product review based only on the specs you are given",
    backstory="You are an experienced smartphone reviewer. You explain what specs mean for "
              "real users, and you never invent numbers that are not in the spec sheet.",
    llm=llm,
    max_iter=2,
    verbose=False,
)


# ---------- Saved reviews ----------

def load_saved_review(phone):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT r.review FROM reviews r JOIN phones p ON p.id = r.phone_id WHERE p.name = %s",
            (phone,),
        ).fetchone()
    return row[0] if row else None


def save_review(phone, text):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO reviews (phone_id, review)
            SELECT id, %s FROM phones WHERE name = %s
            ON CONFLICT (phone_id) DO UPDATE SET review = EXCLUDED.review, created_at = now()
            """,
            (text, phone),
        )


# ---------- Running the crew ----------

def generate_review(phone_name, refresh=False):
    found = phones_mentioned(phone_name)
    if not found:
        return f"Sorry, '{phone_name}' is not one of the phones in the database."
    phone = found[0]   # full official name, e.g. "Samsung Galaxy S23"

    if not refresh:
        saved = load_saved_review(phone)
        if saved:
            return saved

    research = Task(
               description=f"Use the get_phone_specs tool to fetch the full spec sheet of the {phone}.",
        expected_output="The complete spec sheet exactly as the tool returns it.",
        agent=spec_agent,
    )

    review = Task(
                description=(
            f"Write a product review of the {phone} using ONLY the spec sheet you receive. "
            "Use these sections: Overview, Display, Performance, Cameras, Battery, Design, "
            "Pros, Cons, Verdict. Quote the real numbers. Call the price the 'current market "
            "price', never the launch or starting price.\n"
            "Rules for Pros and Cons:\n"
            "- Every bullet must contain a specific number or fact copied from the spec sheet.\n"
            "- Good cons are things like: charging speed in watts, battery capacity in mAh, "
            "RAM in GB, weight in grams, screen size.\n"
            "- Never say a feature is missing (5G, wireless charging, etc.) unless the sheet "
            "explicitly says it is missing.\n"
            "- Never mention previous or other models."
        ),
        expected_output="A detailed review in markdown, around 300-400 words.",
        agent=review_agent,
        context=[research],   # the reviewer receives the researcher's output
    )

    crew = Crew(
        agents=[spec_agent, review_agent],
        tasks=[research, review],
        process=Process.sequential,   # researcher first, then reviewer
        verbose=False,
    )
    text = str(crew.kickoff())
    save_review(phone, text)
    return text


if __name__ == "__main__":
    args = sys.argv[1:]
    refresh = "--refresh" in args
    phone = " ".join(a for a in args if a != "--refresh") or "Galaxy S23"
    print("\n" + "=" * 60 + "\n" + generate_review(phone, refresh=refresh))