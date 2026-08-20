"""Generate story embeddings from the ORIGIN CMS and write data/embeddings.json.

Fetches story text from the public Story API, sends it to OpenAI's
text-embedding-3-small model, and saves the resulting vectors.

Usage:
    OPENAI_API_KEY=sk-... python scripts/generate_embeddings.py
    OPENAI_API_KEY=sk-... python scripts/generate_embeddings.py --output data/embeddings.json
    OPENAI_API_KEY=sk-... python scripts/generate_embeddings.py --dry-run

The output file can be committed to the repo. Re-run it whenever new stories
are added to the catalogue.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

STORY_LIST_URL = "https://origin-api.imagineear.com/api/Story?PageSize=200"
STORY_DETAIL_URL = "https://origin-api.imagineear.com/api/Story/{story_id}"
OPENAI_EMBED_URL = "https://api.openai.com/v1/embeddings"
EMBED_MODEL = "text-embedding-3-small"
DEFAULT_OUTPUT = Path(__file__).parent.parent / "data" / "embeddings.json"


def fetch_json(url: str) -> object:
    with urllib.request.urlopen(url) as r:
        return json.load(r)


def extract_text(story: dict) -> str:
    """Concatenate title, subtitle, and all chapter text into one string."""
    parts = [story.get("title", ""), story.get("subtitle", "")]
    for chapter in story.get("chapters") or []:
        for block in chapter.get("content") or []:
            text = block.get("text", "")
            if text:
                parts.append(text)
    return "\n\n".join(p for p in parts if p).strip()


def embed(texts: list[str], api_key: str) -> list[list[float]]:
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    response = client.embeddings.create(input=texts, model=EMBED_MODEL)
    data = sorted(response.data, key=lambda x: x.index)
    return [item.embedding for item in data]


def main(output: Path = DEFAULT_OUTPUT, dry_run: bool = False) -> None:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key and not dry_run:
        print("Error: OPENAI_API_KEY environment variable not set.", file=sys.stderr)
        sys.exit(1)

    print("Fetching story list...")
    stories_list = fetch_json(STORY_LIST_URL)
    story_ids = [s["id"] for s in stories_list]
    print(f"  {len(story_ids)} stories found")

    print("Fetching story detail (text content)...")
    texts: dict[str, str] = {}
    for i, story_id in enumerate(story_ids, 1):
        url = STORY_DETAIL_URL.format(story_id=story_id)
        detail = fetch_json(url)
        text = extract_text(detail)
        texts[story_id] = text
        if i % 10 == 0:
            print(f"  {i}/{len(story_ids)}")
        time.sleep(0.05)
    print(f"  done — {len(texts)} stories with text")

    if dry_run:
        for sid, text in list(texts.items())[:2]:
            print(f"\n--- {sid} ---\n{text[:300]}\n...")
        print("\nDry run complete. No embeddings generated.")
        return

    print(f"Generating embeddings with {EMBED_MODEL}...")
    BATCH_SIZE = 10
    all_ids = list(texts.keys())
    all_texts = [texts[sid] for sid in all_ids]
    all_vectors: list[list[float]] = []

    for i in range(0, len(all_ids), BATCH_SIZE):
        batch = all_texts[i : i + BATCH_SIZE]
        vectors = embed(batch, api_key)
        all_vectors.extend(vectors)
        print(f"  embedded {min(i + BATCH_SIZE, len(all_ids))}/{len(all_ids)}")
        time.sleep(1)  # stay well under RPM limit

    embeddings = {sid: vec for sid, vec in zip(all_ids, all_vectors)}

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        json.dump(embeddings, f)
    print(f"\nWrote {len(embeddings)} embeddings to {output}")
    dim = len(next(iter(embeddings.values())))
    print(f"Embedding dimension: {dim}")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    output_arg = next(
        (sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--output" and i + 1 < len(sys.argv)),
        None,
    )
    main(output=Path(output_arg) if output_arg else DEFAULT_OUTPUT, dry_run=dry_run)
