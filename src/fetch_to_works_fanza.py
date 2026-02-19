from __future__ import annotations

"""
DMM/FANZA 商品情報API v3（ItemList）から 作品データ（manifest + chunks）を生成/更新する。

- service=digital, floor=videoa（FANZA動画想定）
- 作品によって sampleImageURL / sampleMovieURL が無い場合があります
- 既存 works.json があれば、欠損している項目を中心に「埋め戻し更新」します

必須環境変数:
  DMM_API_ID
  DMM_AFFILIATE_ID
"""

import json
import os
import re
import time
import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from works_store import load_bundle, save_bundle, paths


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "src" / "data"  # compatibility: run from repo root or src/
if not DATA_DIR.exists():
    DATA_DIR = Path(__file__).resolve().parent / "data"

MANIFEST_FILE, CHUNKS_DIR, LEGACY_FILE = paths(DATA_DIR)
API_ID = (os.getenv("DMM_API_ID") or "").strip().strip('"').strip("'")
AFFILIATE_ID = (os.getenv("DMM_AFFILIATE_ID") or "").strip().strip('"').strip("'")

ENDPOINT = "https://api.dmm.com/affiliate/v3/ItemList"

# ===== 取得条件（必要ならここだけ変更） =====
SITE_NAME = "Review Catalog"

SITE = "FANZA"
SERVICE = "digital"
FLOOR = "videoa"

HITS = 100            # 最大100
DATE_PAGES = 5        # 新着（date）を何ページ取るか（100×5=500件）
RANK_PAGES = 3        # 人気（rank）を何ページ取るか（100×3=300件）
SLEEP_SEC = 0.6       # API負荷回避
TIMEOUT = 30

MAX_TOTAL_WORKS = 20000  # 作品データの最大件数（増えすぎ防止）
UPDATE_EXISTING = True   # 既存作品にも不足があれば上書きする

# ===== テスト運用向けスイッチ =====
# False: 作品数を増やさず、既存作品だけ更新（おすすめ：テスト中）
# True : 新規作品も追加して更新（本番運用で最終的にここを True にする）
ADD_NEW_WORKS = False

# テスト用：保存時に件数を切り詰める（重くしないためのテスト運用向け）
#  - False: 切り詰めない（通常はこちら）
#  - True : TRIM_TO 件までに減らす（件数を固定してUI確認したい時）
TRIM_ENABLE = True
TRIM_TO = 100


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="FANZA ItemList から 作品データを生成/更新する（full / update-only 対応）\n"
        "※ 何も指定しない場合の挙動は、ソース内の ADD_NEW_WORKS に従います。"
    )
    p.add_argument("--site", default=SITE, help="site (default: FANZA)")
    p.add_argument("--service", default=SERVICE, help="service (default: digital)")
    p.add_argument("--floor", default=FLOOR, help="floor (default: videoa)")

    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--update-only",
        action="store_true",
        help="works.json に既に存在する作品だけ更新する（作品数を増やさない）",
    )
    mode.add_argument(
        "--full",
        action="store_true",
        help="新規作品の追加も含めて更新する",
    )

    p.add_argument("--hits", type=int, default=HITS, help="hits per page (max 100)")
    p.add_argument("--date-pages", type=int, default=DATE_PAGES, help="pages for sort=date")
    p.add_argument("--rank-pages", type=int, default=RANK_PAGES, help="pages for sort=rank")
    p.add_argument("--sleep", type=float, default=SLEEP_SEC, help="sleep seconds between calls")
    p.add_argument("--timeout", type=int, default=TIMEOUT, help="request timeout seconds")
    p.add_argument(
        "--max-total",
        type=int,
        default=MAX_TOTAL_WORKS,
        help="max works in works.json (full mode only, default: 20000)",
    )
    p.add_argument(
        "--freeze-count",
        action="store_true",
        help="full モードでも作品数を増やさず、現在の件数に固定する",
    )
    p.add_argument(
        "--trim-to",
        type=int,
        default=0,
        help="保存時に件数を指定数まで切り詰める（0=無効）。テスト用",
    )
    return p.parse_args()


def _ensure_dict(x: Any) -> Dict[str, Any]:
    return x if isinstance(x, dict) else {}


def _ensure_list(x: Any) -> List[Any]:
    return x if isinstance(x, list) else []


def _clean_str(s: Any) -> str:
    return str(s).strip() if s is not None else ""


def _safe_https(url: str) -> str:
    # Mixed Content 回避（pics.dmm.co.jp 等は https で使えるケースが多い）
    url = _clean_str(url)
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def _parse_date_for_sort(s: str) -> str:
    """
    APIの date は '2012/8/3 10:00' など。ISO風に正規化して格納。
    """
    s = _clean_str(s)
    if not s:
        return ""
    s = s.replace("/", "-")
    # 2012-8-3 10:00 -> 2012-08-03 10:00
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(.*)$", s)
    if m:
        y, mo, d, rest = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4) or ""
        return f"{y}-{mo:02d}-{d:02d}{rest}"
    return s


def _extract_names(iteminfo_entry: Any) -> List[str]:
    """
    iteminfo の各カテゴリ（genre/actress/maker/series/label...）は
    - [ {name,id}, ... ] の配列
    - { name, id } の単体
    の両方があり得るので両対応。
    """
    out: List[str] = []
    if isinstance(iteminfo_entry, list):
        for it in iteminfo_entry:
            if isinstance(it, dict):
                name = _clean_str(it.get("name"))
                if name:
                    out.append(name)
    elif isinstance(iteminfo_entry, dict):
        name = _clean_str(iteminfo_entry.get("name"))
        if name:
            out.append(name)
    return out


def _extract_sample_images(sample_image_url: Any) -> Tuple[List[str], List[str]]:
    """
    sampleImageURL:
      {
        "sample_s": {"image": [ ... ]},
        "sample_l": {"image": [ ... ]}
      }
    の形式を優先して扱う（あなたの実測どおり）。
    もし古い形式（配列/文字列）でも拾えるように保険を入れる。
    """
    d = _ensure_dict(sample_image_url)

    def pull(container: Any) -> List[str]:
        out: List[str] = []
        if isinstance(container, dict):
            img = container.get("image")
            if isinstance(img, list):
                out += [_safe_https(x) for x in img if isinstance(x, str) and x.strip()]
            elif isinstance(img, str) and img.strip():
                out.append(_safe_https(img))
        elif isinstance(container, list):
            for it in container:
                if isinstance(it, dict):
                    img = it.get("image")
                    if isinstance(img, list):
                        out += [_safe_https(x) for x in img if isinstance(x, str) and x.strip()]
                    elif isinstance(img, str) and img.strip():
                        out.append(_safe_https(img))
                elif isinstance(it, str) and it.strip():
                    out.append(_safe_https(it))
        elif isinstance(container, str) and container.strip():
            out.append(_safe_https(container))
        return out

    small = pull(d.get("sample_s"))
    large = pull(d.get("sample_l"))
    return small, large


def _pick_best_movie_url(sample_movie_url: Any) -> Tuple[Optional[str], Dict[str, str], Optional[Tuple[int, int]]]:
    """
    sampleMovieURL:
      { size_720_480: "...", pc_flag:1, sp_flag:1, ... }
    からサイズ最大のURLを選び、サイズも返す。
    """
    d = _ensure_dict(sample_movie_url)
    urls: Dict[str, str] = {}
    sizes: List[Tuple[int, int, str]] = []  # (w,h,key)

    for k, v in d.items():
        if not (isinstance(k, str) and k.startswith("size_")):
            continue
        if not isinstance(v, str) or not v.strip():
            continue
        vv = _safe_https(v.strip())
        urls[k] = vv
        m = re.match(r"size_(\d+)_(\d+)", k)
        if m:
            sizes.append((int(m.group(1)), int(m.group(2)), k))

    if not sizes:
        return None, urls, None

    sizes.sort(key=lambda t: (t[0] * t[1], t[0]), reverse=True)
    w, h, best_key = sizes[0]
    return urls.get(best_key), urls, (w, h)


def _load_existing() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load existing works.

    優先: works_manifest.json + works_chunks/
    互換: works.json
    """
    meta, works = load_bundle(DATA_DIR)
    if not isinstance(meta, dict):
        meta = {}
    if not meta.get("site_name"):
        meta["site_name"] = SITE_NAME
    return meta, works



def _fetch_page(
    sess: requests.Session,
    *,
    sort: str,
    offset: int,
    hits: int,
    site: str,
    service: str,
    floor: str,
    timeout: int,
) -> List[Dict[str, Any]]:
    params = {
        "api_id": API_ID,
        "affiliate_id": AFFILIATE_ID,
        "site": site,
        "service": service,
        "floor": floor,
        "sort": sort,
        "offset": offset,
        "hits": hits,
        "output": "json",
    }
    r = sess.get(ENDPOINT, params=params, timeout=timeout)
    r.raise_for_status()
    payload = r.json()

    result = payload.get("result", {})
    status = str(result.get("status", ""))
    if status and status != "200":
        raise RuntimeError(json.dumps(result, ensure_ascii=False, indent=2))

    items = result.get("items")
    if isinstance(items, list):
        return [x for x in items if isinstance(x, dict)]
    return []


def _merge_work(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> Dict[str, Any]:
    if not old:
        return new

    merged = dict(old)

    # 文字列：新が非空なら上書き
    for k in ["title", "description", "release_date", "official_url", "hero_image"]:
        nv = new.get(k)
        if isinstance(nv, str) and nv.strip():
            merged[k] = nv

    # 配列：新があれば上書き（空は無視）
    for k in ["tags", "actresses", "sample_images_small", "sample_images_large"]:
        nv = new.get(k)
        if isinstance(nv, list) and nv:
            merged[k] = nv

    # maker/series/label：新が非空なら上書き
    for k in ["maker", "series", "label"]:
        nv = new.get(k)
        if isinstance(nv, str) and nv.strip():
            merged[k] = nv

    # movie
    if new.get("sample_movie"):
        merged["sample_movie"] = new["sample_movie"]
    if isinstance(new.get("sample_movie_urls"), dict) and new["sample_movie_urls"]:
        merged["sample_movie_urls"] = new["sample_movie_urls"]
    if new.get("sample_movie_size"):
        merged["sample_movie_size"] = new["sample_movie_size"]

    # review/prices
    for k in ["review_count", "review_average", "price_min", "api_rank"]:
        if new.get(k) is not None:
            merged[k] = new.get(k)

    return merged


def _make_work_from_item(item: Dict[str, Any], *, api_rank: Optional[int] = None) -> Dict[str, Any]:
    content_id = _clean_str(item.get("content_id"))
    title = _clean_str(item.get("title"))
    url = _clean_str(item.get("affiliateURL") or item.get("affiliateUrl") or item.get("URL") or item.get("url"))

    image_url = _ensure_dict(item.get("imageURL"))
    hero = _safe_https(_clean_str(image_url.get("large") or image_url.get("list") or image_url.get("small")))

    date = _parse_date_for_sort(_clean_str(item.get("date") or item.get("release_date")))

    # tags/actresses + maker/series/label
    iteminfo = _ensure_dict(item.get("iteminfo"))
    genres = _extract_names(iteminfo.get("genre"))
    actresses = _extract_names(iteminfo.get("actress"))
    maker_names = _extract_names(iteminfo.get("maker"))
    series_names = _extract_names(iteminfo.get("series"))
    label_names = _extract_names(iteminfo.get("label"))

    maker = maker_names[0] if maker_names else ""
    series = series_names[0] if series_names else ""
    label = label_names[0] if label_names else ""

    # sample
    simg_small, simg_large = _extract_sample_images(item.get("sampleImageURL"))
    movie_best, movie_urls, movie_size = _pick_best_movie_url(item.get("sampleMovieURL"))

    # review
    review = _ensure_dict(item.get("review"))
    review_count = review.get("count")
    review_average = review.get("average")
    try:
        review_count = int(review_count) if review_count is not None else None
    except Exception:
        review_count = None
    try:
        review_average = float(review_average) if review_average is not None else None
    except Exception:
        review_average = None

    # price min (deliveries)
    price_min = None
    prices = _ensure_dict(item.get("prices"))
    deliveries = prices.get("deliveries")
    if isinstance(deliveries, dict):
        delivery = deliveries.get("delivery")
        # delivery can be list or dict
        if isinstance(delivery, list):
            vals = []
            for d in delivery:
                if isinstance(d, dict) and d.get("price") is not None:
                    try:
                        vals.append(int(d["price"]))
                    except Exception:
                        pass
            if vals:
                price_min = min(vals)
        elif isinstance(delivery, dict) and delivery.get("price") is not None:
            try:
                price_min = int(delivery["price"])
            except Exception:
                pass

    w: Dict[str, Any] = {
        "id": content_id,
        "title": title,
        "description": title,  # APIレスポンスに説明が無いことが多いので、最低限タイトル
        "release_date": date,
        "tags": genres,
        "actresses": actresses,
        "official_url": url,
        "hero_image": hero or None,
        "maker": maker,
        "series": series,
        "label": label,
        "sample_images_small": simg_small,
        "sample_images_large": simg_large,
        "sample_movie": movie_best,
        "sample_movie_urls": movie_urls,
        "sample_movie_size": {"w": movie_size[0], "h": movie_size[1]} if movie_size else None,
        "review_count": review_count,
        "review_average": review_average,
        "price_min": price_min,
        "api_rank": api_rank,
    }

    # 余計なNoneを減らす
    if not w["sample_movie_size"]:
        w.pop("sample_movie_size", None)
    if not w["sample_movie_urls"]:
        w.pop("sample_movie_urls", None)
    if not w["sample_movie"]:
        w.pop("sample_movie", None)
    if not w["sample_images_small"]:
        w.pop("sample_images_small", None)
    if not w["sample_images_large"]:
        w.pop("sample_images_large", None)
    if not w["maker"]:
        w.pop("maker", None)
    if not w["series"]:
        w.pop("series", None)
    if not w["label"]:
        w.pop("label", None)
    if w["review_count"] is None:
        w.pop("review_count", None)
    if w["review_average"] is None:
        w.pop("review_average", None)
    if w["price_min"] is None:
        w.pop("price_min", None)
    if w["api_rank"] is None:
        w.pop("api_rank", None)

    return w


def main() -> None:
    if not API_ID or not AFFILIATE_ID:
        raise SystemExit("環境変数 DMM_API_ID / DMM_AFFILIATE_ID を設定してください。")

    args = _parse_args()
    site = str(args.site or SITE)
    service = str(args.service or SERVICE)
    floor = str(args.floor or FLOOR)

    hits = int(args.hits)
    if hits <= 0:
        hits = HITS
    if hits > 100:
        hits = 100

    date_pages = max(0, int(args.date_pages))
    rank_pages = max(0, int(args.rank_pages))
    sleep_sec = float(args.sleep)
    timeout = int(args.timeout)

    # モード決定：
    #  - コマンド指定があればそれを優先
    #  - 指定が無ければ ADD_NEW_WORKS に従う（今は False 推奨）
    if bool(args.full):
        update_only = False
    elif bool(args.update_only):
        update_only = True
    else:
        update_only = (not bool(ADD_NEW_WORKS))

    full_mode = (not update_only)

    meta, existing_works = _load_existing()
    by_id: Dict[str, Dict[str, Any]] = {str(w.get("id")): w for w in existing_works if w.get("id")}

    # fullモードで「作品数を増やさない」= 現在の件数に上限を固定
    max_total = int(args.max_total) if int(args.max_total) > 0 else MAX_TOTAL_WORKS
    if args.freeze_count and existing_works:
        max_total = min(max_total, len(existing_works))

    # 切り詰め（優先順位）
    #  1) --trim-to が指定されていればそれを採用
    #  2) 未指定なら、コード内スイッチ TRIM_ENABLE/TRIM_TO を採用
    if int(args.trim_to) > 0:
        trim_to = int(args.trim_to)
    else:
        trim_to = int(TRIM_TO) if (bool(TRIM_ENABLE) and int(TRIM_TO) > 0) else 0

    sess = requests.Session()
    sess.headers.update({"User-Agent": "catalog-fetch/2.0 (+requests)"})

    total_new = 0
    total_updated = 0

    def process(sort: str, pages: int, set_rank: bool) -> None:
        nonlocal total_new, total_updated
        offset = 1
        rank_counter = 1
        for p in range(pages):
            items = _fetch_page(
                sess,
                sort=sort,
                offset=offset,
                hits=hits,
                site=site,
                service=service,
                floor=floor,
                timeout=timeout,
            )
            if not items:
                break
            for idx, item in enumerate(items):
                wid = _clean_str(item.get("content_id"))
                if not wid:
                    continue
                api_rank = (rank_counter + idx) if set_rank else None
                new_w = _make_work_from_item(item, api_rank=api_rank)
                old_w = by_id.get(wid)

                if old_w is None:
                    # update-only のときは「新規は追加しない」
                    if full_mode and (not update_only):
                        by_id[wid] = new_w
                        total_new += 1
                else:
                    if UPDATE_EXISTING:
                        merged = _merge_work(old_w, new_w)
                        # 更新判定（簡易）
                        if merged != old_w:
                            by_id[wid] = merged
                            total_updated += 1
            offset += hits
            rank_counter += len(items)
            time.sleep(sleep_sec)

    # 1) 新着
    process("date", date_pages, set_rank=False)
    # 2) 人気（api_rank付与）
    process("rank", rank_pages, set_rank=True)

    # 保存対象 works
    if update_only:
        # 既存の順序/件数を維持（作品数を増やさない）
        ordered_ids = [str(w.get("id")) for w in existing_works if w.get("id")]
        works = [by_id[i] for i in ordered_ids if i in by_id]
    else:
        works = list(by_id.values())
        # 件数上限（新しい順優先）
        def sort_key(w: Dict[str, Any]) -> str:
            return _parse_date_for_sort(_clean_str(w.get("release_date")))

        works.sort(key=sort_key, reverse=True)
        if len(works) > max_total:
            works = works[:max_total]

    # 追加の切り詰め（テスト用）
    if trim_to and len(works) > trim_to:
        works = works[:trim_to]

    meta["site_name"] = meta.get("site_name") or SITE_NAME

    # 保存（manifest + chunks）
    save_bundle(DATA_DIR, meta, works, chunk_size=500, cleanup_legacy=True)

    mode_str = "update-only" if update_only else "full"
    extra = []
    if args.freeze_count:
        extra.append("freeze-count")
    if trim_to:
        extra.append(f"trim-to={trim_to}")
    extra_s = (" (" + ",".join(extra) + ")") if extra else ""
    print(f"OK: works data updated: mode={mode_str}{extra_s} total={len(works)} new={total_new} updated={total_updated}")
    print(f"manifest: {MANIFEST_FILE}")


if __name__ == "__main__":
    main()
