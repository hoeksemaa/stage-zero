#!/usr/bin/env python3
"""
Generate interactive 3D bubble chart from the medical data landscape DB.
Outputs a self-contained HTML file.

Features:
- 3D scatter with click-drag rotation and zoom
- Short (≤3 word) labels on each bubble
- Click a bubble to see record count + full description in a side panel
- Bubble size = log(record count), color = source type
"""

import sqlite3
import math
import json
from pathlib import Path

import plotly.graph_objects as go
import pandas as pd
import numpy as np

DB_PATH = Path(__file__).parent / "meddata.db"
OUTPUT_PATH = Path(__file__).parent / "medical_data_landscape.html"

TYPE_COLORS = {
    "government": "#2171b5",
    "academic": "#6a51a3",
    "international": "#238b45",
    "industry": "#d94801",
    "nonprofit": "#cb181d",
    "consortium": "#41ab5d",
}


def load_data():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT s.*, p.x, p.y, p.cluster_id
        FROM sources s
        JOIN projections p ON s.id = p.source_id
    """, conn)
    # Ensure short_name column exists
    if "short_name" not in df.columns:
        df["short_name"] = df["name"].str[:20]
    conn.close()
    return df


def bubble_size(record_count):
    if record_count is None or record_count <= 0:
        return 8
    return max(8, min(60, 5 + 8 * math.log10(record_count)))


def build_figure(df):
    fig = go.Figure()

    # We need a 3rd dimension — run UMAP with 3 components
    # But since we stored 2D projections, we'll compute z from cluster + jitter
    # Actually, let's recompute 3D projections inline
    from sentence_transformers import SentenceTransformer
    import umap

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT source_id, embedding FROM embeddings ORDER BY source_id"
    ).fetchall()
    conn.close()

    if not rows:
        raise RuntimeError("No embeddings found in DB. Run build_db.py first.")

    emb_dim = len(np.frombuffer(rows[0][1], dtype=np.float32))
    embeddings = np.array([np.frombuffer(r[1], dtype=np.float32) for r in rows])
    emb_ids = [r[0] for r in rows]

    reducer = umap.UMAP(
        n_neighbors=15, min_dist=0.15, n_components=3,
        metric="cosine", random_state=42
    )
    coords_3d = reducer.fit_transform(embeddings)

    # Map source_id -> 3d coords
    id_to_xyz = {sid: (coords_3d[i, 0], coords_3d[i, 1], coords_3d[i, 2])
                 for i, sid in enumerate(emb_ids)}

    df["x3"] = df["id"].map(lambda sid: id_to_xyz.get(sid, (0, 0, 0))[0])
    df["y3"] = df["id"].map(lambda sid: id_to_xyz.get(sid, (0, 0, 0))[1])
    df["z3"] = df["id"].map(lambda sid: id_to_xyz.get(sid, (0, 0, 0))[2])

    # Build traces by source_type
    for stype in sorted(df["source_type"].unique()):
        subset = df[df["source_type"] == stype].copy()
        color = TYPE_COLORS.get(stype, "#888888")
        sizes = [bubble_size(rc) for rc in subset["record_count"]]

        # Short hover labels (just name)
        hover_labels = subset["name"].tolist()

        # Custom data for click handler: [name, org, record_count_label, description, category, url, year_start, year_end, access_level]
        customdata = []
        for _, row in subset.iterrows():
            rc_label = row["record_count_label"] or "Unknown"
            years = ""
            if pd.notna(row["year_start"]):
                years = f"{int(row['year_start'])}–"
                years += str(int(row["year_end"])) if pd.notna(row["year_end"]) else "present"
            customdata.append([
                row["name"],
                row["organization"],
                rc_label,
                row["description"],
                row["category"],
                row.get("url") or "",
                years,
                row.get("access_level") or "unknown",
                row.get("tags") or "",
            ])

        fig.add_trace(go.Scatter3d(
            x=subset["x3"],
            y=subset["y3"],
            z=subset["z3"],
            mode="markers+text",
            name=stype.replace("_", " ").title(),
            marker=dict(
                size=[s * 0.6 for s in sizes],  # scale down slightly for 3D
                color=color,
                opacity=0.8,
                line=dict(width=0.5, color="white"),
            ),
            text=subset["short_name"].fillna(subset["name"].str[:20]),
            textposition="top center",
            textfont=dict(size=7, color="#444"),
            customdata=customdata,
            hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}<br>Records: %{customdata[2]}<extra></extra>",
        ))

    # Small 3-axis orientation indicator at data centroid
    cx, cy, cz = df["x3"].median(), df["y3"].median(), df["z3"].median()
    span = max(df["x3"].max() - df["x3"].min(),
               df["y3"].max() - df["y3"].min(),
               df["z3"].max() - df["z3"].min()) * 0.08
    for axis_vec, color, label in [
        ((1, 0, 0), "#e41a1c", "X"),
        ((0, 1, 0), "#4daf4a", "Y"),
        ((0, 0, 1), "#377eb8", "Z"),
    ]:
        fig.add_trace(go.Scatter3d(
            x=[cx, cx + span * axis_vec[0]],
            y=[cy, cy + span * axis_vec[1]],
            z=[cz, cz + span * axis_vec[2]],
            mode="lines+text",
            line=dict(color=color, width=4),
            text=["", label],
            textposition="top center",
            textfont=dict(size=9, color=color),
            hoverinfo="skip",
            showlegend=False,
        ))

    fig.update_layout(
        title=dict(
            text="Public Medical Data Landscape",
            font=dict(size=22),
        ),
        scene=dict(
            xaxis=dict(showgrid=False, showticklabels=False, title="", showbackground=False),
            yaxis=dict(showgrid=False, showticklabels=False, title="", showbackground=False),
            zaxis=dict(showgrid=False, showticklabels=False, title="", showbackground=False),
            bgcolor="rgba(248,248,248,1)",
        ),
        paper_bgcolor="white",
        legend=dict(
            title="Source Type",
            font=dict(size=11),
            borderwidth=1,
            yanchor="top", y=0.95,
            xanchor="left", x=0.01,
        ),
        width=1600,
        height=950,
        margin=dict(l=0, r=350, t=60, b=30),
    )

    return fig


def build_html(fig, df):
    """Wrap plotly figure in HTML with a click-to-detail side panel."""

    plotly_html = fig.to_html(
        include_plotlyjs=True,
        full_html=False,
        div_id="plotDiv",
        config={"scrollZoom": True, "displayModeBar": True},
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Public Medical Data Landscape</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #fafafa; }}
  #container {{ display: flex; height: 100vh; }}
  #chart {{ flex: 1; min-width: 0; }}
  #detail {{
    width: 380px; min-width: 380px;
    background: white;
    border-left: 1px solid #ddd;
    padding: 24px 20px;
    overflow-y: auto;
    transition: opacity 0.2s;
  }}
  #detail.empty {{ opacity: 0.4; }}
  #detail h2 {{ font-size: 18px; margin-bottom: 8px; color: #1a1a1a; }}
  #detail .org {{ font-size: 13px; color: #666; margin-bottom: 12px; }}
  #detail .meta {{ font-size: 12px; color: #888; margin-bottom: 4px; }}
  #detail .meta b {{ color: #444; }}
  #detail .divider {{ height: 1px; background: #eee; margin: 14px 0; }}
  #detail .desc {{ font-size: 13px; line-height: 1.55; color: #333; margin-top: 10px; }}
  #detail .tags {{ margin-top: 12px; }}
  #detail .tag {{
    display: inline-block; background: #f0f0f0; color: #555;
    padding: 2px 8px; border-radius: 10px; font-size: 11px; margin: 2px 4px 2px 0;
  }}
  #detail .url {{ font-size: 12px; margin-top: 12px; }}
  #detail .url a {{ color: #2171b5; text-decoration: none; }}
  #detail .url a:hover {{ text-decoration: underline; }}
  #detail .placeholder {{ color: #aaa; font-size: 14px; margin-top: 40px; text-align: center; }}
  #detail .record-count {{
    font-size: 28px; font-weight: 700; color: #2171b5; margin: 8px 0;
  }}
  .legend-note {{
    position: fixed; bottom: 12px; left: 12px;
    font-size: 11px; color: #999;
  }}
</style>
</head>
<body>
<div id="container">
  <div id="chart">
    {plotly_html}
  </div>
  <div id="detail" class="empty">
    <div class="placeholder">Click a bubble to see details</div>
  </div>
</div>
<div class="legend-note">
  Bubble size = log(record count) &middot; Position = semantic similarity (UMAP 3D) &middot; Color = source type
</div>
<script>
var plotDiv = document.getElementById('plotDiv');
var detailDiv = document.getElementById('detail');

plotDiv.on('plotly_click', function(data) {{
  var pt = data.points[0];
  var cd = pt.customdata;
  if (!cd) return;

  var name = cd[0], org = cd[1], rc = cd[2], desc = cd[3],
      cat = cd[4], url = cd[5], years = cd[6], access = cd[7], tags = cd[8];

  var tagsHtml = '';
  if (tags) {{
    var tagList = tags.split(',');
    tagsHtml = '<div class="tags">' + tagList.map(function(t) {{
      return '<span class="tag">' + t.trim() + '</span>';
    }}).join('') + '</div>';
  }}

  var urlHtml = url ? '<div class="url"><a href="' + url + '" target="_blank">' + url + '</a></div>' : '';

  detailDiv.className = '';
  detailDiv.innerHTML =
    '<h2>' + name + '</h2>' +
    '<div class="org">' + org + '</div>' +
    '<div class="record-count">' + rc + ' records</div>' +
    '<div class="meta"><b>Category:</b> ' + cat + '</div>' +
    (years ? '<div class="meta"><b>Coverage:</b> ' + years + '</div>' : '') +
    '<div class="meta"><b>Access:</b> ' + access + '</div>' +
    '<div class="divider"></div>' +
    '<div class="desc">' + desc + '</div>' +
    tagsHtml +
    urlHtml;
}});
</script>
</body>
</html>"""
    return html


def main():
    df = load_data()
    print(f"Loaded {len(df)} sources with projections")

    fig = build_figure(df)
    html = build_html(fig, df)

    with open(OUTPUT_PATH, "w") as f:
        f.write(html)
    print(f"Visualization saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
