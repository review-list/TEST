from __future__ import annotations

"""sanitize_noimage_samples.py

作品データ（manifest + chunks）に入ってしまった「NOW PRINTING / NO IMAGE」プレースホルダのサンプル画像を除去します。

特徴:
- 画像を大量ダウンロードしません
- まず HEAD で Content-Length を見て、怪しい場合だけ先頭 8KB を Range 取得して署名照合します
- URL が違っても画像バイト列が同一なら検出できます（あなたのケース）
- 結果は src/data/noimage_cache.json にキャッシュして次回は高速

使い方:
  cd <repo>
  python .\src\sanitize_noimage_samples.py
  python .\src\build.py

オプション:
  --max-check N   : 先頭N件だけチェック（0=全件）
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List

import requests

from works_store import load_bundle, save_bundle

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "src" / "data"
if not DATA_DIR.exists():
    DATA_DIR = Path(__file__).resolve().parent / "data"

MANIFEST_FILE = DATA_DIR / "works_manifest.json"
SIG_FILE = DATA_DIR / "noimage_signatures.json"
CACHE_FILE = DATA_DIR / "noimage_cache.json"


def _clean(s: Any) -> str:
    return str(s).strip() if s is not None else ""


def _https(url: str) -> str:
    url = _clean(url)
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def _load_signatures() -> Dict[str, set]:
    sig = {"content_lengths": set(), "prefix8_sha256": set()}
    try:
        if SIG_FILE.exists():
            j = json.loads(SIG_FILE.read_text(encoding="utf-8"))
            for x in j.get("content_lengths", []) or []:
                try:
                    sig["content_lengths"].add(int(x))
                except Exception:
                    pass
            for h in j.get("prefix8_sha256", []) or []:
                if isinstance(h, str) and h.strip():
                    sig["prefix8_sha256"].add(h.strip().lower())
    except Exception:
        pass

    # fallback built-in
    sig["content_lengths"].add(19378)
    sig["prefix8_sha256"].add("60b0c00c1f599fe3eb1d21c5f5ac1117117aca68ae65ca838ec35a4806601839")
    return sig


def _load_cache() -> Dict[str, Any]:
    try:
        if CACHE_FILE.exists():
            j = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(j, dict):
                return j
    except Exception:
        pass
    return {"url": {}, "sig": {}}


def _save_cache(cache: Dict[str, Any]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


class Detector:
    HINTS = (
        "noimage", "no_img", "no-img", "no-photo", "nophoto", "now_print", "nowprint",
        "nowprinting", "now_printing", "comingsoon", "coming_soon", "placeholder",
    )

    def __init__(self, sess: requests.Session, sig: Dict[str, set], cache: Dict[str, Any]):
        self.sess = sess
        self.sig_lengths = sig["content_lengths"]
        self.sig_prefix8 = sig["prefix8_sha256"]
        self.cache_url = cache.setdefault("url", {})
        self.cache_sig = cache.setdefault("sig", {})

    def _head(self, url: str):
        try:
            r = self.sess.head(url, allow_redirects=True, timeout=15)
            if r.status_code >= 400:
                return None
            return r
        except Exception:
            return None

    def _range_first8(self, url: str) -> bytes:
        try:
            r = self.sess.get(url, headers={"Range": "bytes=0-8191"}, timeout=20)
            if r.status_code >= 400:
                return b""
            return r.content[:8192]
        except Exception:
            return b""

    def is_placeholder(self, url: str) -> bool:
        url = _https(_clean(url))
        if not url:
            return True

        low = url.lower()
        if any(h in low for h in self.HINTS):
            return True

        if url in self.cache_url:
            return bool(self.cache_url[url])

        r = self._head(url)
        if r is None:
            self.cache_url[url] = False
            return False

        etag = (r.headers.get("ETag") or "").strip('"')
        clen = r.headers.get("Content-Length")
        try:
            clen_i = int(clen) if clen is not None else None
        except Exception:
            clen_i = None

        sig_key = None
        if etag and clen_i is not None:
            sig_key = f"{etag}|{clen_i}"
            if sig_key in self.cache_sig:
                val = bool(self.cache_sig[sig_key])
                self.cache_url[url] = val
                return val

        if clen_i is None or clen_i not in self.sig_lengths:
            self.cache_url[url] = False
            if sig_key:
                self.cache_sig[sig_key] = False
            return False

        b = self._range_first8(url)
        if not b:
            self.cache_url[url] = False
            if sig_key:
                self.cache_sig[sig_key] = False
            return False

        h = hashlib.sha256(b).hexdigest().lower()
        is_ph = h in self.sig_prefix8

        self.cache_url[url] = is_ph
        if sig_key:
            self.cache_sig[sig_key] = is_ph
        return is_ph


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-check", type=int, default=0, help="0=all, otherwise check first N works")
    args = ap.parse_args()
    meta, works = load_bundle(DATA_DIR)
    if not works:
        raise SystemExit(f"works data not found: {MANIFEST_FILE} (or legacy works.json)")

    sig = _load_signatures()
    cache = _load_cache()
    sess = requests.Session()
    sess.headers.update({"User-Agent": "catalog-sanitize/1.0 (+requests)"})
    det = Detector(sess, sig, cache)

    changed = 0
    checked = 0

    limit = args.max_check if args.max_check and args.max_check > 0 else len(works)

    for w in works[:limit]:
        checked += 1
        hero = _https(_clean(w.get("hero_image")))
        small = w.get("sample_images_small") or []
        large = w.get("sample_images_large") or []
        if not isinstance(small, list):
            small = []
        if not isinstance(large, list):
            large = []

        # remove obvious hint URLs / hero duplicates
        def filt(urls):
            out = []
            for u in urls:
                uu = _https(_clean(u))
                if not uu:
                    continue
                low = uu.lower()
                if any(h in low for h in det.HINTS):
                    continue
                if hero and uu == hero:
                    continue
                out.append(uu)
            return out

        small2 = filt(small)
        large2 = filt(large)
        cand = (large2[0] if large2 else (small2[0] if small2 else ""))

        if cand and det.is_placeholder(cand):
            if w.get("sample_images_small") or w.get("sample_images_large"):
                w.pop("sample_images_small", None)
                w.pop("sample_images_large", None)
                changed += 1
        else:
            # write filtered lists back
            if small2:
                w["sample_images_small"] = small2
            else:
                w.pop("sample_images_small", None)
            if large2:
                w["sample_images_large"] = large2
            else:
                w.pop("sample_images_large", None)

    if changed:
        # 保存（manifest + chunks に書き戻し）
        chunk_size = int(meta.get("chunk_size") or 500) if isinstance(meta, dict) else 500
        save_bundle(DATA_DIR, meta if isinstance(meta, dict) else {}, works, chunk_size=chunk_size, cleanup_legacy=True)
    _save_cache(cache)

    print(f"OK: checked={checked} changed={changed}")
    print(f"cache: {CACHE_FILE}")


if __name__ == "__main__":
    main()
