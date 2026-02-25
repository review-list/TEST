# -*- coding: utf-8 -*-
from __future__ import annotations

"""sanitize_noimage_samples.py

作品データ（manifest + chunks / legacy works.json）に入ってしまった
「NOW PRINTING / NO IMAGE」プレースホルダ画像を、サンプル画像リストから除外します。

ポイント
- **大量ダウンロードしません**：各URLについて必要なら Range(先頭8KB) だけ取得します
- URLが普通でも（now_printing を含まない）画像の中身が同じなら **署名（8KBハッシュ）で判定**できます
- 判定結果を src/data/noimage_cache.json にキャッシュし、次回以降は高速です

使い方（推奨）
  cd <repo>
  python ./src/sanitize_noimage_samples.py --learn
  python ./src/build.py

  # 以後は learn なしでOK
  python ./src/sanitize_noimage_samples.py

オプション
  --max-check N : 先頭N件だけチェック（0=全件）
  --learn       : 既知の NOW PRINTING URL から署名を学習（DMM側が画像を変えても追従しやすい）
"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from works_store import load_bundle, save_bundle


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "src" / "data"
if not DATA_DIR.exists():
    # 直実行される想定だが、念のため
    DATA_DIR = Path(__file__).resolve().parent / "data"

MANIFEST_FILE = DATA_DIR / "works_manifest.json"
SIG_FILE = DATA_DIR / "noimage_signatures.json"
CACHE_FILE = DATA_DIR / "noimage_cache.json"

# 署名学習に使う既知URL（ここが取れれば URLが変わっても中身で判定できる）
KNOWN_PLACEHOLDER_URLS: List[str] = [
    "https://imgsrc.dmm.com/pics/mono/movie/n/now_printing/now_printing.jpg",
    # query 付き（ユーザー提示）
    "https://imgsrc.dmm.com/pics/mono/movie/n/now_printing/now_printing.jpg?w=800&f=.jpg&h=800&q=88",
    # 別ドメインの例
    "https://pics.dmm.co.jp/mono/movie/n/now_printing/now_printing.jpg",
]


def _clean(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def _https(url: str) -> str:
    url = _clean(url)
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest().lower()


def _looks_like_placeholder_url(url: str) -> bool:
    # URLヒント（通信なし最速）
    low = url.lower()
    hints = (
        "now_print", "nowprint", "nowprinting", "now_printing",
        "noimage", "no_img", "no-img", "nophoto", "no-photo",
        "comingsoon", "coming_soon", "placeholder",
    )
    return any(h in low for h in hints)


def _load_signatures() -> Dict[str, set]:
    sig = {"content_lengths": set(), "prefix8_sha256": set()}
    try:
        if SIG_FILE.exists():
            j = json.loads(SIG_FILE.read_text(encoding="utf-8"))
            if isinstance(j, dict):
                for x in (j.get("content_lengths") or []):
                    try:
                        sig["content_lengths"].add(int(x))
                    except Exception:
                        pass
                for h in (j.get("prefix8_sha256") or []):
                    if isinstance(h, str) and h.strip():
                        sig["prefix8_sha256"].add(h.strip().lower())
    except Exception:
        pass

    # 互換: 旧版で配布した固定署名（残しておく）
    sig["content_lengths"].add(19378)
    sig["prefix8_sha256"].add(
        "60b0c00c1f599fe3eb1d21c5f5ac1117117aca68ae65ca838ec35a4806601839"
    )
    return sig


def _save_signatures(sig: Dict[str, set]) -> None:
    SIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 1,
        "content_lengths": sorted(int(x) for x in sig.get("content_lengths", set()) if isinstance(x, int) or str(x).isdigit()),
        "prefix8_sha256": sorted(str(x) for x in sig.get("prefix8_sha256", set()) if isinstance(x, str)),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "note": "prefix8_sha256 = SHA-256 of first 8KB (Range) for placeholder images",
    }
    SIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_cache() -> Dict[str, Any]:
    try:
        if CACHE_FILE.exists():
            j = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(j, dict):
                # 旧形式を吸収
                if "url" in j and isinstance(j.get("url"), dict):
                    return j
                # {url: bool} だけの形式
                return {"version": 1, "url": {str(k): bool(v) for k, v in j.items()}, "sig": {}}
    except Exception:
        pass
    return {"version": 1, "url": {}, "sig": {}}


def _save_cache(cache: Dict[str, Any]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    cache["version"] = 1
    cache["updated_at"] = datetime.now(timezone.utc).isoformat()
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class Detector:
    def __init__(self, sess: requests.Session, sig: Dict[str, set], cache: Dict[str, Any]):
        self.sess = sess
        self.sig_lengths = sig["content_lengths"]
        self.sig_prefix8 = sig["prefix8_sha256"]
        self.cache_url: Dict[str, bool] = cache.setdefault("url", {})
        self.cache_sig: Dict[str, bool] = cache.setdefault("sig", {})

    def _head(self, url: str) -> Tuple[Optional[str], Optional[int]]:
        try:
            r = self.sess.head(url, allow_redirects=True, timeout=15)
            if r.status_code >= 400:
                return None, None
            etag = (r.headers.get("ETag") or "").strip().strip('"')
            clen = r.headers.get("Content-Length")
            clen_i: Optional[int]
            try:
                clen_i = int(clen) if clen is not None else None
            except Exception:
                clen_i = None
            return etag or None, clen_i
        except Exception:
            return None, None

    def _range_first8(self, url: str) -> bytes:
        try:
            r = self.sess.get(url, headers={"Range": "bytes=0-8191"}, timeout=20)
            if r.status_code >= 400:
                return b""
            return (r.content or b"")[:8192]
        except Exception:
            return b""

    def _sig8(self, url: str) -> Optional[str]:
        b = self._range_first8(url)
        if not b:
            return None
        return _sha256_hex(b)

    def learn_from_known_urls(self, urls: List[str]) -> int:
        """既知URLから署名を学習して、sig_prefix8 / sig_lengths を増やす。"""
        learned = 0
        for u in urls:
            url = _https(_clean(u))
            if not url:
                continue
            h = self._sig8(url)
            if not h:
                continue
            if h not in self.sig_prefix8:
                self.sig_prefix8.add(h)
                learned += 1
            etag, clen = self._head(url)
            if isinstance(clen, int) and clen > 0:
                self.sig_lengths.add(clen)
            # 既知URLはプレースホルダ扱い
            self.cache_url[url] = True
        return learned

    def is_placeholder(self, url: str, learn: bool = False) -> bool:
        url = _https(_clean(url))
        if not url:
            return True

        # URLヒント（最速）
        if _looks_like_placeholder_url(url):
            if learn:
                h = self._sig8(url)
                if h:
                    self.sig_prefix8.add(h)
                etag, clen = self._head(url)
                if isinstance(clen, int) and clen > 0:
                    self.sig_lengths.add(clen)
            self.cache_url[url] = True
            return True

        # URL単位のキャッシュ
        if url in self.cache_url and not learn:
            return bool(self.cache_url[url])

        # ETag+Content-Length のキャッシュ（URLが違っても同一判定に寄せる）
        etag, clen = self._head(url)
        sig_key = None
        if etag and clen is not None:
            sig_key = f"{etag}|{clen}"
            if sig_key in self.cache_sig and not learn:
                val = bool(self.cache_sig[sig_key])
                self.cache_url[url] = val
                return val

        # 署名が無い状態では中身判定ができない
        if not self.sig_prefix8:
            self.cache_url[url] = False
            if sig_key:
                self.cache_sig[sig_key] = False
            return False

        h = self._sig8(url)
        if not h:
            self.cache_url[url] = False
            if sig_key:
                self.cache_sig[sig_key] = False
            return False

        is_ph = h in self.sig_prefix8
        self.cache_url[url] = bool(is_ph)
        if sig_key:
            self.cache_sig[sig_key] = bool(is_ph)
        return bool(is_ph)


def _as_url_list(v: Any) -> List[str]:
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str)]
    return []


def _dedup_keep_order(urls: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for u in urls:
        uu = _https(_clean(u))
        if not uu:
            continue
        if uu in seen:
            continue
        seen.add(uu)
        out.append(uu)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-check", type=int, default=0, help="0=all, otherwise check first N works")
    ap.add_argument("--learn", action="store_true", help="learn placeholder signatures from known URLs")
    args = ap.parse_args()

    meta, works = load_bundle(DATA_DIR)
    if not works:
        raise SystemExit(f"works data not found: {MANIFEST_FILE} (or legacy works.json)")

    sig = _load_signatures()
    cache = _load_cache()

    sess = requests.Session()
    sess.headers.update({"User-Agent": "catalog-sanitize/2.0 (+requests)"})

    det = Detector(sess, sig, cache)

    learned = 0
    if args.learn:
        learned = det.learn_from_known_urls(KNOWN_PLACEHOLDER_URLS)

    limit = args.max_check if args.max_check and args.max_check > 0 else len(works)

    checked = 0
    changed_works = 0
    removed_imgs = 0

    for w in works[:limit]:
        checked += 1

        hero = _https(_clean(w.get("hero_image")))
        small = _dedup_keep_order(_as_url_list(w.get("sample_images_small")))
        large = _dedup_keep_order(_as_url_list(w.get("sample_images_large")))

        def keep_real(urls: List[str]) -> Tuple[List[str], int]:
            out: List[str] = []
            removed = 0
            for u in urls:
                if hero and u == hero:
                    continue
                if det.is_placeholder(u, learn=bool(args.learn)):
                    removed += 1
                    continue
                out.append(u)
            return out, removed

        small2, r1 = keep_real(small)
        large2, r2 = keep_real(large)
        removed_imgs += (r1 + r2)

        # 変更検知
        before_small = _as_url_list(w.get("sample_images_small"))
        before_large = _as_url_list(w.get("sample_images_large"))

        def _norm_for_compare(xs: List[str]) -> List[str]:
            return _dedup_keep_order(xs)

        changed = (_norm_for_compare(before_small) != small2) or (_norm_for_compare(before_large) != large2)

        if changed:
            if small2:
                w["sample_images_small"] = small2
            else:
                w.pop("sample_images_small", None)
            if large2:
                w["sample_images_large"] = large2
            else:
                w.pop("sample_images_large", None)
            changed_works += 1

    if changed_works:
        chunk_size = int(meta.get("chunk_size") or 500) if isinstance(meta, dict) else 500
        save_bundle(DATA_DIR, meta if isinstance(meta, dict) else {}, works, chunk_size=chunk_size, cleanup_legacy=True)

    _save_cache(cache)
    _save_signatures(sig)

    print(f"OK: checked={checked} changed={changed_works} removed_imgs={removed_imgs}")
    if args.learn:
        print(f"learned_signatures: {learned} (total={len(sig['prefix8_sha256'])})")
    print(f"cache: {CACHE_FILE}")
    print(f"signatures: {SIG_FILE}")


if __name__ == "__main__":
    main()
