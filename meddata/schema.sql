CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    short_name TEXT,               -- ≤3 word label for bubble display
    organization TEXT NOT NULL,
    url TEXT,
    record_count INTEGER,          -- approximate number of records/data points
    record_count_label TEXT,       -- human-readable (e.g. "~2.3 billion")
    description TEXT NOT NULL,     -- rich paragraph: what, where, years, reliability
    category TEXT NOT NULL,        -- primary category
    subcategory TEXT,
    source_type TEXT NOT NULL,     -- government, academic, international, industry, nonprofit
    country TEXT DEFAULT 'US',
    year_start INTEGER,
    year_end INTEGER,              -- NULL = ongoing
    update_frequency TEXT,         -- daily, weekly, monthly, quarterly, annual, static
    access_level TEXT,             -- open, registration, application, restricted
    reliability_tier INTEGER,     -- 1=gold-standard, 2=high, 3=moderate, 4=variable
    tags TEXT,                     -- comma-separated tags for filtering
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS embeddings (
    source_id INTEGER PRIMARY KEY,
    embedding BLOB NOT NULL,       -- numpy array stored as bytes
    model TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS projections (
    source_id INTEGER PRIMARY KEY,
    x REAL NOT NULL,
    y REAL NOT NULL,
    cluster_id INTEGER,
    method TEXT NOT NULL,           -- umap, tsne
    FOREIGN KEY (source_id) REFERENCES sources(id)
);
