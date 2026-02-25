from __future__ import annotations

"""works_store.py

Worksデータの保存/読み込みを「manifest + chunks」形式で扱うための共通モジュール。

- 旧形式: src/data/works.json  (巨大化してGitHubに上げられない問題が起きやすい)
- 新形式: src/data/works_manifest.json + src/data/works_chunks/works_XXXX.json

使い方:
  from works_store import load_bundle, save_bundle

  meta, works = load_bundle(DATA_DIR)
  save_bundle(DATA_DIR, meta, works, chunk_size=500)
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _read_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def _write_json(p: Path, data: Any) -> None:
    _ensure_dir(p.parent)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def paths(data_dir: Path) -> Tuple[Path, Path, Path]:
    """Return (manifest_path, chunks_dir, legacy_works_json_path)."""
    manifest = data_dir / "works_manifest.json"
    chunks_dir = data_dir / "works_chunks"
    legacy = data_dir / "works.json"
    return manifest, chunks_dir, legacy


def _has_sample_images(w: Dict[str, Any]) -> bool:
    for k in ("sample_images_large", "sample_images_small"):
        v = w.get(k)
        if isinstance(v, list) and any(isinstance(x, str) and x.strip() for x in v):
            return True
    return False


def _has_sample_movie(w: Dict[str, Any]) -> bool:
    if isinstance(w.get("sample_movie"), str) and str(w.get("sample_movie")).strip():
        return True
    urls = w.get("sample_movie_urls")
    if isinstance(urls, dict) and any(isinstance(v, str) and v.strip() for v in urls.values()):
        return True
    return False


def load_bundle(data_dir: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Load works data.

    優先順位:
      1) manifest + chunks
      2) legacy works.json

    Returns:
      meta: dict (site_name/site_url/base_url等を含む)
      works: list[dict]
    """
    manifest_path, chunks_dir, legacy_path = paths(data_dir)

    # 1) chunked
    if manifest_path.exists() and chunks_dir.exists():
        mf = _read_json(manifest_path)
        if not isinstance(mf, dict):
            mf = {}
        works: List[Dict[str, Any]] = []
        chunks = mf.get("chunks") or []
        if isinstance(chunks, list):
            for ch in chunks:
                if not isinstance(ch, dict):
                    continue
                rel = ch.get("file")
                if not isinstance(rel, str) or not rel.strip():
                    continue
                fp = (data_dir / rel).resolve()
                if not fp.exists():
                    # 途中で欠けていても落ちない（できるだけ続行）
                    continue
                part = _read_json(fp)
                if isinstance(part, list):
                    works.extend([x for x in part if isinstance(x, dict)])
        return mf, works

    # 2) legacy
    if legacy_path.exists():
        j = _read_json(legacy_path)
        if isinstance(j, dict):
            works_raw = j.get("works") or []
            works = [x for x in works_raw if isinstance(x, dict)] if isinstance(works_raw, list) else []
            meta = dict(j)
            meta.pop("works", None)
            return meta, works

    return {}, []


def save_bundle(
    data_dir: Path,
    meta: Dict[str, Any],
    works: List[Dict[str, Any]],
    *,
    chunk_size: int = 500,
    cleanup_legacy: bool = False,
) -> Dict[str, Any]:
    """Save works data as manifest + chunks.

    Returns the manifest dict written.

    cleanup_legacy=True の場合は works.json を削除します（git管理から外す前提）。
    """
    manifest_path, chunks_dir, legacy_path = paths(data_dir)
    _ensure_dir(chunks_dir)

    # 古いチャンク掃除
    for p in chunks_dir.glob("works_*.json"):
        try:
            p.unlink()
        except Exception:
            pass

    # stats
    total = len(works)
    with_imgs = sum(1 for w in works if isinstance(w, dict) and _has_sample_images(w))
    with_mov = sum(1 for w in works if isinstance(w, dict) and _has_sample_movie(w))

    # chunks
    chunks: List[Dict[str, Any]] = []
    if chunk_size <= 0:
        chunk_size = 500

    for i in range(0, total, chunk_size):
        part = works[i : i + chunk_size]
        fn = f"works_{i // chunk_size:04d}.json"
        out = chunks_dir / fn
        _write_json(out, part)
        chunks.append({"file": f"works_chunks/{fn}", "count": len(part)})

    mf: Dict[str, Any] = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "count": total,
        "chunk_size": chunk_size,
        "with_sample_images": with_imgs,
        "with_sample_movies": with_mov,
        "chunks": chunks,
    }

    # meta merge（site_name / site_url / base_url など必要なものだけ残す）
    for k in [
        "site_name",
        "site_url",
        "base_url",
        "description",
        "og_image",
    ]:
        v = meta.get(k)
        if isinstance(v, str) and v.strip():
            mf[k] = v.strip()

    _write_json(manifest_path, mf)

    if cleanup_legacy and legacy_path.exists():
        try:
            legacy_path.unlink()
        except Exception:
            pass

    return mf
