#!/usr/bin/env python3
"""
Build the medical data landscape SQLite database.
Populates sources, computes embeddings, runs UMAP projection.
"""

import sqlite3
import json
import numpy as np
from pathlib import Path

DB_PATH = Path(__file__).parent / "meddata.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"
CATALOG_PATH = Path(__file__).parent / "catalog.json"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_PATH.read_text())
    return conn


def load_catalog():
    with open(CATALOG_PATH) as f:
        return json.load(f)


def populate_sources(conn, catalog):
    cur = conn.cursor()
    for src in catalog:
        cur.execute("""
            INSERT OR IGNORE INTO sources
            (name, short_name, organization, url, record_count, record_count_label,
             description, category, subcategory, source_type, country,
             year_start, year_end, update_frequency, access_level,
             reliability_tier, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            src["name"], src.get("short_name", ""), src["organization"], src.get("url"),
            src.get("record_count"), src.get("record_count_label"),
            src["description"], src["category"], src.get("subcategory"),
            src["source_type"], src.get("country", "US"),
            src.get("year_start"), src.get("year_end"),
            src.get("update_frequency"), src.get("access_level", "open"),
            src.get("reliability_tier", 3), src.get("tags")
        ))
    conn.commit()
    print(f"Loaded {len(catalog)} sources into DB")


def compute_embeddings(conn):
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("all-MiniLM-L6-v2")
    cur = conn.cursor()

    rows = cur.execute(
        "SELECT id, name, organization, description, category, tags FROM sources"
    ).fetchall()

    # Build rich text for embedding: concat name + org + description + category + tags
    texts = []
    ids = []
    for row in rows:
        sid, name, org, desc, cat, tags = row
        text = f"{name}. {org}. {cat}. {desc}"
        if tags:
            text += f" Tags: {tags}"
        texts.append(text)
        ids.append(sid)

    print(f"Computing embeddings for {len(texts)} sources...")
    embeddings = model.encode(texts, show_progress_bar=True, batch_size=64)

    for sid, emb in zip(ids, embeddings):
        cur.execute(
            "INSERT OR REPLACE INTO embeddings (source_id, embedding, model) VALUES (?, ?, ?)",
            (sid, emb.tobytes(), "all-MiniLM-L6-v2")
        )
    conn.commit()
    print("Embeddings stored")
    return ids, embeddings


def compute_projections(conn, ids, embeddings):
    import umap

    print("Running UMAP projection...")
    reducer = umap.UMAP(
        n_neighbors=15,
        min_dist=0.1,
        n_components=2,
        metric="cosine",
        random_state=42
    )
    coords = reducer.fit_transform(embeddings)

    # Optional: cluster with HDBSCAN or KMeans
    from sklearn.cluster import KMeans
    n_clusters = min(20, len(ids) // 5)
    if n_clusters < 2:
        n_clusters = 2
    km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = km.fit_predict(embeddings)

    cur = conn.cursor()
    for sid, (x, y), cl in zip(ids, coords, labels):
        cur.execute(
            "INSERT OR REPLACE INTO projections (source_id, x, y, cluster_id, method) VALUES (?, ?, ?, ?, ?)",
            (sid, float(x), float(y), int(cl), "umap")
        )
    conn.commit()
    print(f"Projections stored ({n_clusters} clusters)")


def main():
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = init_db()
    catalog = load_catalog()
    populate_sources(conn, catalog)
    ids, embeddings = compute_embeddings(conn)
    compute_projections(conn, ids, embeddings)
    conn.close()
    print(f"Done. DB at {DB_PATH}")


if __name__ == "__main__":
    main()
