# from __future__ import annotations

import argparse
import glob
import json
import os
import re
from dataclasses import dataclass
from typing import Iterable, List, Dict, Any, Optional, Tuple

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm
from fastembed import TextEmbedding
import uuid

from .ingest_utils import read_file, normalize_whitespace

# Optional GCS support (kept optional to avoid hard dependency if you don't need it)
try:
    from google.cloud import storage  # type: ignore
except Exception:  # pragma: no cover
    storage = None


@dataclass
class Chunk:
    id: str
    text: str
    metadata: Dict[str, Any]


def iter_local_files(path: str, patterns: List[str]) -> Iterable[str]:
    for pat in patterns:
        yield from sorted(glob.glob(os.path.join(path, pat)))



def chunk_text(text: str, chunk_size: int = 900, overlap: int = 150) -> List[str]:
    """
    Simple character-based chunker with overlap.
    - chunk_size: target size in characters
    - overlap: overlap between consecutive chunks
    """
    text = normalize_whitespace(text)
    if not text:
        return []
    if overlap >= chunk_size:
        overlap = max(0, chunk_size // 4)

    chunks: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + chunk_size)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start = max(0, end - overlap)
    return chunks


def ensure_collection(client: QdrantClient, collection: str, vector_size: int):
    existing = {c.name for c in client.get_collections().collections}
    if collection in existing:
        return
    client.create_collection(
        collection_name=collection,
        vectors_config=qm.VectorParams(size=vector_size, distance=qm.Distance.COSINE),
    )


def upsert_chunks(
    client: QdrantClient,
    collection: str,
    embedder: TextEmbedding,
    chunks: List[Chunk],
    batch_size: int = 64,
):
    # embed + upsert in batches
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        texts = [c.text for c in batch]
        vectors = [v.tolist() for v in embedder.embed(texts)]
        points = []
        for ch, vec in zip(batch, vectors):
            points.append(
                qm.PointStruct(
                    id=ch.id,
                    vector=vec,
                    payload={
                        "text": ch.text,
                        "metadata": ch.metadata,
                    },
                )
            )
        client.upsert(collection_name=collection, points=points)


def ingest_local_path(
    input_path: str,
    collection: str,
    source_name: str,
    patterns: List[str],
    chunk_size: int,
    overlap: int,
) -> List[Chunk]:
    files = list(iter_local_files(input_path, patterns))
    if not files:
        raise SystemExit(f"No matching files in {input_path} for patterns {patterns}")

    chunks: List[Chunk] = []
    for fp in files:
        raw = read_file(fp)
        txt = normalize_whitespace(raw)
        rel = os.path.relpath(fp, input_path)
        doc_id = rel.replace(os.sep, "/")
        for idx, ch in enumerate(chunk_text(txt, chunk_size=chunk_size, overlap=overlap)):
            # cid = f"{source_name}:{doc_id}#{idx}"
            cid = str(uuid.uuid4())
            chunks.append(
                Chunk(
                    id=cid,
                    text=ch,
                    metadata={
                        "source": source_name,
                        "document": doc_id,
                        "chunk_index": idx,
                    },
                )
            )
    return chunks

def list_gcs_blobs(gcs_uri: str):
    if storage is None:
        raise SystemExit(
            "google-cloud-storage is not installed. "
            "Add it to requirements.txt to use --gcs-uri."
        )

    if not gcs_uri.startswith("gs://"):
        raise SystemExit("--gcs-uri must start with gs://")

    _, _, rest = gcs_uri.partition("gs://")
    bucket_name, _, prefix = rest.partition("/")
    prefix = prefix.strip("/")

    client = storage.Client()
    bucket = client.bucket(bucket_name)

    blobs = list(client.list_blobs(bucket, prefix=prefix))
    if not blobs:
        raise SystemExit(f"No blobs found under {gcs_uri}")

    return bucket_name, blobs


def resolve_allowed_suffixes(patterns: List[str]) -> List[str]:
    allowed_suffixes = []
    for p in patterns:
        if p.startswith("*."):
            allowed_suffixes.append(p[1:])  # ".txt", ".md", ".jsonl"

    if not allowed_suffixes:
        allowed_suffixes = [".txt", ".md", ".jsonl"]

    return allowed_suffixes

def blob_to_chunks(
    blob,
    bucket_name: str,
    source_name: str,
    chunk_size: int,
    overlap: int,
) -> List[Chunk]:
    raw = blob.download_as_text(encoding="utf-8", errors="ignore")
    txt = normalize_whitespace(raw)
    doc_id = blob.name

    chunks: List[Chunk] = []
    for idx, ch in enumerate(chunk_text(txt, chunk_size=chunk_size, overlap=overlap)):
        cid = f"{source_name}:{doc_id}#{idx}"
        chunks.append(
            Chunk(
                id=cid,
                text=ch,
                metadata={
                    "source": source_name,
                    "document": doc_id,
                    "chunk_index": idx,
                    "gcs_uri": f"gs://{bucket_name}/{blob.name}",
                },
            )
        )
    return chunks

def ingest_gcs_prefix(
    gcs_uri: str,
    collection: str,
    source_name: str,
    patterns: List[str],
    chunk_size: int,
    overlap: int,
) -> List[Chunk]:

    bucket_name, blobs = list_gcs_blobs(gcs_uri)
    allowed_suffixes = resolve_allowed_suffixes(patterns)

    chunks: List[Chunk] = []

    for blob in blobs:
        if not any(blob.name.endswith(suf) for suf in allowed_suffixes):
            continue
        chunks.extend(
            blob_to_chunks(
                blob,
                bucket_name=bucket_name,
                source_name=source_name,
                chunk_size=chunk_size,
                overlap=overlap,
            )
        )

    if not chunks:
        raise SystemExit(f"No matching .txt/.md/.jsonl blobs under {gcs_uri}")

    return chunks


def main():
    ap = argparse.ArgumentParser(description="Ingest documents into Qdrant for RAG.")
    ap.add_argument("--qdrant-url", required=True, help="e.g. http://qdrant:6333")
    ap.add_argument("--collection", default=os.getenv("QDRANT_COLLECTION", "medical_docs"))
    ap.add_argument("--embedding-model", default=os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"))
    ap.add_argument("--top-level-path", default="/data", help="Local mount path for docs (used with --input-path)")
    ap.add_argument("--input-path", default=".", help="Relative to --top-level-path when running in cluster")
    ap.add_argument("--gcs-uri", default="", help="gs://bucket/prefix (optional alternative to local input)")
    ap.add_argument("--source-name", default="medical_corpus")
    ap.add_argument("--patterns", default="*.txt,*.md,*.jsonl", help="Comma-separated glob patterns")
    ap.add_argument("--chunk-size", type=int, default=900)
    ap.add_argument("--overlap", type=int, default=150)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    patterns = [p.strip() for p in args.patterns.split(",") if p.strip()]

    qclient = QdrantClient(url=args.qdrant_url)
    embedder = TextEmbedding(model_name=args.embedding_model)

    # discover embedding vector size
    vec_size = len(next(embedder.embed(["vector size probe"])).tolist())
    ensure_collection(qclient, args.collection, vec_size)

    if args.gcs_uri.strip():
        chunks = ingest_gcs_prefix(
            gcs_uri=args.gcs_uri.strip(),
            collection=args.collection,
            source_name=args.source_name,
            patterns=patterns,
            chunk_size=args.chunk_size,
            overlap=args.overlap,
        )
    else:
        base = os.path.join(args.top_level_path, args.input_path)
        chunks = ingest_local_path(
            input_path=base,
            collection=args.collection,
            source_name=args.source_name,
            patterns=patterns,
            chunk_size=args.chunk_size,
            overlap=args.overlap,
        )

    print(json.dumps({"collection": args.collection, "chunks": len(chunks)}, indent=2))

    if args.dry_run:
        return

    upsert_chunks(
        client=qclient,
        collection=args.collection,
        embedder=embedder,
        chunks=chunks,
        batch_size=args.batch_size,
    )
    print(f" Upserted {len(chunks)} chunks into '{args.collection}' at {args.qdrant_url}.")


if __name__ == "__main__":
    main()
