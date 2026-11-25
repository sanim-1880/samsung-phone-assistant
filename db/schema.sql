
CREATE TABLE IF NOT EXISTS phones (
    id               SERIAL PRIMARY KEY,
    name             TEXT UNIQUE NOT NULL,
    url              TEXT NOT NULL,
    release_year     INT,
    chipset          TEXT,
    display_size_in  NUMERIC(4, 2),
    refresh_rate_hz  INT,
    max_ram_gb       INT,
    main_camera_mp   NUMERIC(6, 1),
    battery_mah      INT,
    weight_g         NUMERIC(6, 1),
    price_text       TEXT,
    scraped_at       TIMESTAMPTZ DEFAULT now()
);

-- Every line of the GSMArena spec sheet, so nothing is lost.
CREATE TABLE IF NOT EXISTS specifications (
    id         SERIAL PRIMARY KEY,
    phone_id   INT NOT NULL REFERENCES phones(id) ON DELETE CASCADE,
    category   TEXT NOT NULL,   -- e.g. "Display", "Battery"
    spec_name  TEXT NOT NULL,   -- e.g. "Size", "Type"
    spec_value TEXT NOT NULL    -- e.g. "6.1 inches"
);

-- The price split into one row per currency.
CREATE TABLE IF NOT EXISTS prices (
    id       SERIAL PRIMARY KEY,
    phone_id INT NOT NULL REFERENCES phones(id) ON DELETE CASCADE,
    currency TEXT NOT NULL,     -- USD, EUR, GBP, INR
    amount   NUMERIC(12, 2) NOT NULL
);

-- Text chunks + their embeddings for the RAG chatbot (filled in Step 2).
CREATE TABLE IF NOT EXISTS phone_chunks (
    id        SERIAL PRIMARY KEY,
    phone_id  INT NOT NULL REFERENCES phones(id) ON DELETE CASCADE,
    content   TEXT NOT NULL,
    embedding REAL[]
);

-- Finished reviews, so each phone's review is generated only once.
CREATE TABLE IF NOT EXISTS reviews (
    phone_id   INT PRIMARY KEY REFERENCES phones(id) ON DELETE CASCADE,
    review     TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);