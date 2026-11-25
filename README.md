# Samsung Phone Assistant

A system for looking up, asking about and reviewing Samsung phones. It scrapes spec sheets for 15 Samsung phones from GSMArena into PostgreSQL, and builds three things on that data:

- a **RAG chatbot** that answers questions using a local open-source model (Llama 3.2 3B through Ollama)
- a **multi-agent system** (CrewAI) where one agent fetches a phone's specs from the database and another writes a review from them
- a **FastAPI service** that exposes both

Everything runs locally. No paid API is involved.

## Phones covered

Galaxy S21, S21 Ultra, S22, S22 Ultra, S23, S23 Ultra, S23 FE, S24, S24 Ultra, S25, S25 Ultra, Z Fold6, Z Flip6, A55 and A35.

## How it works

```
GSMArena ──scraper──▶ PostgreSQL ──embed──▶ phone_chunks (vectors)
                          │                        │
                          │                        ▼
                          │                 RAG chatbot ──┐
                          ▼                               ├──▶ FastAPI
                   CrewAI agents ─────────────────────────┘
          (Spec Researcher → Tech Reviewer)
```

### 1. Scraping (`scraper/scraper.py`)

GSMArena's search page sits behind a Cloudflare bot check, so the scraper doesn't use it. It walks Samsung's brand listing pages instead and collects the links to the 15 target phones, then opens each phone's spec page. It waits 4 seconds between requests and caches every page in `data/raw/`, so a re-run reads from disk.

Each spec table is read row by row (category, name, value). GSMArena tags the important cells with a `data-spec` attribute, which the scraper uses to pull typed values such as battery capacity, screen size and RAM with regular expressions. Saving uses `INSERT ... ON CONFLICT DO UPDATE`, so running the scraper twice updates rows instead of duplicating them.

### 2. Database (`db/schema.sql`)

| Table | Contents |
|---|---|
| `phones` | One row per phone, with the most-asked specs as typed columns (battery_mah, display_size_in, max_ram_gb, main_camera_mp, ...) |
| `specifications` | Every row of the GSMArena spec sheet: 837 rows in total |
| `prices` | The price field split into one row per currency (USD, EUR, GBP, INR) |
| `phone_chunks` | 212 text chunks with 768-number embeddings, used by the chatbot |
| `reviews` | Finished agent reviews, so each phone is reviewed only once |

### 3. RAG chatbot (`chatbot/`)

`embed.py` groups each phone's specs by category into chunks like "Samsung Galaxy S23 - Battery" and embeds them with `nomic-embed-text`.

`chatbot.py` answers a question in four steps:

1. **Find the phones** named in the question. Longer names are matched first, so "S23 Ultra" isn't mistaken for "S23".
2. **Retrieve** the closest chunks by cosine similarity. If phones are named, only their chunks are searched, with the top 4 per phone, so comparisons stay balanced.
3. **Add a summary table** of all 15 phones for ranking questions ("best", "most", "which"), including GSMArena's own battery test results.
4. **Generate** an answer with Llama 3.2 at temperature 0.2. The system prompt tells it to use only the given facts, quote exact numbers and say so when it doesn't know.

The bot keeps the last few turns, so follow-ups like "and its battery?" work.

### 4. Multi-agent reviews (`agents/review_crew.py`)

Two CrewAI agents run in sequence:

- **Phone Spec Researcher** has one tool, `get_phone_specs`, which reads a phone's full spec sheet from PostgreSQL. The tool is marked `result_as_answer=True`, so the exact database rows are passed on with nothing lost to summarising.
- **Tech Reviewer** has no tools. It receives the researcher's output through `context=[research]` and writes a review with Overview, Display, Performance, Cameras, Battery, Design, Pros, Cons and Verdict. Every pro and con has to contain a fact from the spec sheet.

Finished reviews are stored in the `reviews` table, because generating one takes a few minutes on a CPU-only machine.

### 5. API (`api/main.py`)

| Method | Endpoint | What it does |
|---|---|---|
| GET | `/health` | Checks that the database and Ollama are reachable |
| GET | `/phones` | Lists all phones with their key specs |
| GET | `/phones/{name}` | Full spec sheet of one phone (accepts loose names like `s24 ultra`) |
| POST | `/chat` | Asks the chatbot. Send back the `session_id` for follow-up questions |
| POST | `/review` | Gets a review from the agents. `"refresh": true` writes a new one |

Endpoints are plain `def` functions, so FastAPI runs each request in its own worker thread and a slow review doesn't block chat requests. Each chat session has its own memory, so several users can talk to the bot at once.

## Setup

### Requirements

- Python 3.12
- PostgreSQL (tested on 19)
- [Ollama](https://ollama.com) with two models:
  ```
  ollama pull llama3.2:3b
  ollama pull nomic-embed-text
  ```

### Install

```
git clone <this-repo-url>
cd samsung-phone-assistant
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and put in your PostgreSQL password. Then create the database:

```
psql -U postgres -c "CREATE DATABASE samsung_phones;"
```

### Run, in this order

```
python -m db.database                       # create the tables
python -m scraper.scraper                   # scrape 15 phones (about 1-2 minutes)
python -m chatbot.embed                     # build chunks and embeddings
python -m chatbot.chatbot                   # optional: chat in the terminal
python -m agents.review_crew Galaxy S23     # optional: one review in the terminal
uvicorn api.main:app --reload               # start the API
```

Then open http://127.0.0.1:8000/docs to try every endpoint in the browser.

## Example

```
POST /chat  {"question": "Which Samsung phone has the best battery life?"}
→ "The Samsung Galaxy S25 Ultra has the best battery life, with an active use score of 14:49h."

POST /chat  {"question": "What is the screen size of the Galaxy S22?"}
→ "The screen size of the Galaxy S22 is 6.1 inches."

POST /chat  {"question": "And its battery?", "session_id": "<from the answer above>"}
→ "The battery capacity of the Galaxy S22 5G is 3700 mAh, and its endurance rating is 85h."
```

## Known limitations

- **Prices are current market prices** from GSMArena, not launch prices. The S23, for example, is listed at about $230.
- **Two battery tests.** GSMArena measured older phones with an "Endurance rating" and newer ones with an "Active use score". The scales aren't comparable, so the chatbot is told to only compare phones on the same test.
- **Small-model mistakes.** Llama 3.2 3B occasionally invents a con in a review (for example, a regional limit that isn't in the data). The planned fix is a third fact-checking agent. A larger model in `.env` would also help, with no code changes.
- **Speed.** On a CPU-only laptop a chat answer takes 10-25 seconds and a new review 3-4 minutes. Saved reviews come back instantly.