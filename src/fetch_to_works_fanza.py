from __future__ import annotations

from pathlib import Path
import os
import json
import time
import requests
from typing import Any, Dict, List, Optional, Tuple


BASE = Path(__file__).resolve().parent
OUT_FILE = BASE / "data" / "works.json"

API_ID = os.getenv("DMM_API_ID")
AFFILIATE_ID = os.getenv("DMM_AFFILIATE_ID")


# =========================
# 取得設定（ここだけ触ればOK）
# =========================
SITE_NAME = "Review Catalog"

# FANZA動画（ビデオ）
SITE = "FANZA"
SERVICE = "digital"
FLOOR = "videoa"
SORT = "date"

HITS = 100               # 1回で取る件数（最大100）
PAGES = 5                # 何ページ分取るか（100×5=最大500件）
SLEEP_SEC = 0.8          # API負荷回避（少し待つ）

MAX_TOTAL_WORKS = 5000   # works.json の最大保存数（増えすぎ防止）

# 既存作品も更新する（サンプル画像/動画の追加など）
UPDATE_EXISTING = True


def load_existing() -> Dict[str, Any]:
    """既存works.jsonを読む。なければ空で作る。"""
    if OUT_FILE.exists():
        try:
            data = json.loads(OUT_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {"site_name": SITE_NAME, "works": []}
            if "works" not in data or not isinstance(data["works"], list):
                data["works"] = []
            if "site_name" not in data:
                data["site_name"] = SITE_NAME
            return data
        except Exception:
            return {"site_name": SITE_NAME, "works": []}
    return {"site_name": SITE_NAME, "works": []}


def fetch_items(hits: int = 100, offset: int = 1, sort: str = "date", keyword: str | None = None) -> List[dict]:
    if not API_ID or not AFFILIATE_ID:
        raise SystemExit("環境変数 DMM_API_ID と DMM_AFFILIATE_ID を設定してください。")

    url = "https://api.dmm.com/affiliate/v3/ItemList"
    params = {
        "api_id": API_ID,
        "affiliate_id": AFFILIATE_ID,
        "site": SITE,
        "service": SERVICE,
        "floor": FLOOR,
        "hits": str(hits),
        "offset": str(offset),
        "sort": sort,
        "output": "json",
    }
    if keyword:
        params["keyword"] = keyword

    for attempt in range(3):
        r = requests.get(url, params=params, timeout=30)
        print(f"status: {r.status_code} (offset={offset}, hits={hits})")
        if r.status_code == 200:
            data = r.json()
            return data.get("result", {}).get("items", []) or []

        print(r.text[:1000])
        if attempt < 2:
            time.sleep(1.5 * (attempt + 1))
            continue
        r.raise_for_status()

    return []


def pick_best_image(item: dict) -> Optional[str]:
    img = item.get("imageURL") or {}
    if isinstance(img, dict):
        return img.get("large") or img.get("list") or img.get("small")
    return None


def extract_genres(item: dict) -> List[str]:
    iteminfo = item.get("iteminfo") or {}
    genres: List[str] = []
    for g in (iteminfo.get("genre") or []):
        name = (g or {}).get("name")
        if name:
            genres.append(name)
    # 重複除去（順序維持）
    seen = set()
    out = []
    for x in genres:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def extract_actresses(item: dict) -> List[str]:
    iteminfo = item.get("iteminfo") or {}
    actresses: List[str] = []
    for a in (iteminfo.get("actress") or []):
        name = (a or {}).get("name")
        if name:
            actresses.append(name)
    # 重複除去（順序維持）
    seen = set()
    out = []
    for x in actresses:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out



def extract_first_iteminfo_name(item: dict, key: str) -> Optional[str]:
    """iteminfo の maker/series/label などから最初の name を取り出す。

    公式レスポンス例: iteminfo.maker = [{name: '...', id: '...'}, ...]
    """
    iteminfo = item.get('iteminfo') or {}
    if not isinstance(iteminfo, dict):
        return None
    v = iteminfo.get(key) or []
    if isinstance(v, dict):
        v = [v]
    if not isinstance(v, list):
        return None
    for it in v:
        if isinstance(it, dict):
            name = it.get('name')
            if name:
                return str(name).strip() or None
    return None

def extract_sample_images(item: dict) -> Tuple[List[str], List[str]]:
    """
    公式レスポンス例:
      sampleImageURL:
        sample_s: [{image: ...}, ...]
        sample_l: [{image: ...}, ...]
    戻り値: (large_list, small_list)
    """
    s = item.get("sampleImageURL") or {}
    if not isinstance(s, dict):
        return [], []

    def to_list(v) -> List[str]:
        """sample_* の形揺れを吸収して URL配列にする。

        パターン例（JSON）
        - sample_l: [ {"image": "..."}, ... ]
        - sample_l: {"image": ["...", "...", ...]}
        - sample_l: {"image": "..."}
        """
        out: List[str] = []
        if isinstance(v, list):
            for it in v:
                if isinstance(it, dict):
                    img = it.get("image")
                    if isinstance(img, list):
                        out += [str(x).strip() for x in img if isinstance(x, str) and x.strip()]
                    elif isinstance(img, str) and img.strip():
                        out.append(img.strip())
                elif isinstance(it, str) and it.strip():
                    out.append(it.strip())
        elif isinstance(v, dict):
            img = v.get("image")
            if isinstance(img, list):
                out += [str(x).strip() for x in img if isinstance(x, str) and x.strip()]
            elif isinstance(img, str) and img.strip():
                out.append(img.strip())
        elif isinstance(v, str) and v.strip():
            out.append(v.strip())

        # http -> https 正規化（Mixed Content 回避）
        out2: List[str] = []
        for u in out:
            if u.startswith("http://"):
                u = "https://" + u[len("http://"):]
            out2.append(u)
        return out2

    small = to_list(s.get("sample_s"))
    large = to_list(s.get("sample_l"))

    # unique (keep order)
    def uniq(xs: List[str]) -> List[str]:
        seen = set()
        out = []
        for u in xs:
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        return out

    return uniq(large), uniq(small)


def extract_sample_movie_urls(item: dict) -> Tuple[Optional[str], Dict[str, str]]:
    """
    公式レスポンス例:
      sampleMovieURL:
        size_720_480: ...
        size_644_414: ...
        size_560_360: ...
        size_476_306: ...
        pc_flag: 1
        sp_flag: 1

    戻り値: (best_url, urls_dict)
    """
    mv = item.get("sampleMovieURL") or {}
    if not isinstance(mv, dict):
        return None, {}

    urls: Dict[str, str] = {}
    for k, v in mv.items():
        if not isinstance(v, str):
            continue
        if k.startswith("size_") and v:
            urls[k] = v

    prefer = ["size_720_480", "size_644_414", "size_560_360", "size_476_306"]
    best = None
    for k in prefer:
        if k in urls:
            best = urls[k]
            break
    if not best and urls:
        # 何か1つ
        best = next(iter(urls.values()))

    return best, urls


def extract_review(item: dict) -> Dict[str, Any]:
    rv = item.get("review") or {}
    if not isinstance(rv, dict):
        return {}
    out: Dict[str, Any] = {}
    if rv.get("count") is not None:
        out["count"] = rv.get("count")
    if rv.get("average") is not None:
        out["average"] = rv.get("average")
    return out


def extract_prices(item: dict) -> Dict[str, Any]:
    pr = item.get("prices") or {}
    if not isinstance(pr, dict):
        return {}
    out: Dict[str, Any] = {}
    # 公式例: price / list_price / deliveries
    if pr.get("price") is not None:
        out["price"] = pr.get("price")
    if pr.get("list_price") is not None:
        out["list_price"] = pr.get("list_price")
    if isinstance(pr.get("deliveries"), dict):
        out["deliveries"] = pr.get("deliveries")
    return out


def normalize_item(item: dict) -> dict:
    content_id = item.get("content_id") or ""
    product_id = item.get("product_id") or ""
    work_id = content_id or product_id

    title = item.get("title") or ""
    # 公式例だと説明が無い場合があるので、commentなどがあれば優先
    description = item.get("comment") or item.get("description") or title

    release_date = item.get("date") or ""
    official_url = item.get("affiliateURL") or item.get("URL") or ""
    hero_image = pick_best_image(item)

    tags = extract_genres(item)
    actresses = extract_actresses(item)

    maker = extract_first_iteminfo_name(item, 'maker')
    series = extract_first_iteminfo_name(item, 'series')
    label = extract_first_iteminfo_name(item, 'label')

    sample_large, sample_small = extract_sample_images(item)
    sample_movie_best, sample_movie_urls = extract_sample_movie_urls(item)

    return {
        "id": work_id,
        "content_id": content_id,
        "product_id": product_id,
        "title": title,
        "description": description,
        "release_date": release_date,
        "tags": tags,
        "actresses": actresses,
        "maker": maker,
        "series": series,
        "label": label,

        "official_url": official_url,
        "hero_image": hero_image,

        # 公式APIから取得できるサンプル
        "sample_images": sample_large or sample_small,  # 表示は基本こっち
        "sample_images_large": sample_large,
        "sample_images_small": sample_small,
        "sample_movie": sample_movie_best,
        "sample_movie_urls": sample_movie_urls,

        # 任意（将来の厚み付け用）
        "review": extract_review(item),
        "prices": extract_prices(item),
        "volume": item.get("volume"),
    }


def needs_update(existing: Dict[str, Any], incoming: Dict[str, Any]) -> bool:
    """既存にサンプル等が無ければ更新対象にする。"""
    def has_valid_urls_list(k: str) -> bool:
        v = existing.get(k)
        if not (isinstance(v, list) and len(v) > 0):
            return False
        # 以前の誤実装で "['https://...','https://...']" のような1文字列が入ることがある
        for s in v:
            if not isinstance(s, str):
                continue
            ss = s.strip()
            if ss.startswith("http") and "[" not in ss and "]" not in ss and "'" not in ss and '"' not in ss:
                return True
        return False

    if incoming.get("sample_movie") and not existing.get("sample_movie"):
        return True
    if incoming.get("sample_images"):
        if not has_valid_urls_list("sample_images"):
            return True
        # incoming の方が画像枚数が多いなら更新
        try:
            if len(incoming.get("sample_images") or []) > len(existing.get("sample_images") or []):
                return True
        except Exception:
            return True
    if incoming.get("review") and not existing.get("review"):
        return True
    if incoming.get("prices") and not existing.get("prices"):
        return True
    if incoming.get("maker") and not existing.get("maker"):
        return True
    if incoming.get("series") and not existing.get("series"):
        return True
    if incoming.get("label") and not existing.get("label"):
        return True
    return False


def merge_works(existing: List[Dict[str, Any]], incoming: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int, int]:
    """
    idで重複排除して追記/更新。
    並びは「新しいものが先」になるように release_date でソート。
    """
    by_id: Dict[str, Dict[str, Any]] = {}
    for w in existing:
        wid = (w or {}).get("id")
        if wid:
            by_id[wid] = w

    added = 0
    updated = 0
    for w in incoming:
        wid = (w or {}).get("id")
        if not wid:
            continue
        if wid not in by_id:
            by_id[wid] = w
            added += 1
            continue

        if UPDATE_EXISTING and needs_update(by_id[wid], w):
            # 既存を残しつつ、incomingの値で上書き（空は上書きしない）
            base = dict(by_id[wid])
            for k, v in w.items():
                if v in (None, "", [], {}):
                    continue
                base[k] = v
            by_id[wid] = base
            updated += 1

    works_all = list(by_id.values())

    def sort_key(x: Dict[str, Any]) -> str:
        s = (x.get("release_date") or "")
        return s.replace("/", "-")

    works_all.sort(key=sort_key, reverse=True)

    if MAX_TOTAL_WORKS and len(works_all) > MAX_TOTAL_WORKS:
        works_all = works_all[:MAX_TOTAL_WORKS]

    return works_all, added, updated


def main():
    data = load_existing()
    existing_works: List[Dict[str, Any]] = data.get("works", []) or []
    existing_by_id = {w.get("id"): w for w in existing_works if isinstance(w, dict) and w.get("id")}

    print(f"existing works: {len(existing_works)}")

    incoming_all: List[Dict[str, Any]] = []

    for page in range(PAGES):
        offset = 1 + page * HITS
        items = fetch_items(hits=HITS, offset=offset, sort=SORT)
        if not items:
            print("no items. stop.")
            break

        normalized = [normalize_item(it) for it in items if it]
        # 新規 or 既存更新対象だけを入れる
        for w in normalized:
            wid = w.get("id")
            if not wid:
                continue
            if wid not in existing_by_id:
                incoming_all.append(w)
            elif UPDATE_EXISTING and needs_update(existing_by_id[wid], w):
                incoming_all.append(w)

        print(f"page {page+1}/{PAGES}: fetched={len(normalized)} candidates={len(incoming_all)}")
        time.sleep(SLEEP_SEC)

    merged, added, updated = merge_works(existing_works, incoming_all)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    out = dict(data)
    out["site_name"] = data.get("site_name") or SITE_NAME
    out["works"] = merged
    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"saved: {OUT_FILE}")
    print(f"added: {added}")
    print(f"updated: {updated}")
    print(f"total works: {len(merged)}")

    # 先頭3件の確認
    for w in merged[:3]:
        print("----")
        print("id:", w.get("id"))
        print("title:", (w.get("title") or "")[:60])
        print("actresses:", w.get("actresses") or [])
        print("sample_images:", len(w.get("sample_images") or []))
        print("sample_movie:", bool(w.get("sample_movie")))


if __name__ == "__main__":
    main()
