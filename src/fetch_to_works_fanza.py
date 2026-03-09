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
import html
import time
from datetime import datetime, timezone, timedelta
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
OFFICIAL_DESC_CACHE_FILE = DATA_DIR / "official_desc_cache.json"

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




# ===== GitHub / GUI 連携（環境変数で上書き可能）=====
# 例: CATALOG_ADD_NEW_WORKS=true など
def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name, "")
    if not v:
        return default
    return v.strip().lower() in ("1","true","yes","on","y")

def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name, "")
    if not v:
        return default
    try:
        return int(v)
    except Exception:
        return default

# constants override (if set)
ADD_NEW_WORKS = _env_bool("CATALOG_ADD_NEW_WORKS", ADD_NEW_WORKS)
TRIM_ENABLE = _env_bool("CATALOG_TRIM_ENABLE", TRIM_ENABLE)
TRIM_TO = _env_int("CATALOG_TRIM_TO", TRIM_TO)

def _now_jst_iso() -> str:
    jst = timezone(timedelta(hours=9))
    return datetime.now(jst).isoformat(timespec="seconds")
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


def _load_json_file(path: Path) -> Dict[str, Any]:
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


OFFICIAL_DESC_CACHE: Dict[str, str] = _load_json_file(OFFICIAL_DESC_CACHE_FILE)
_OFFICIAL_DESC_CACHE_DIRTY = False


def _save_official_desc_cache() -> None:
    global _OFFICIAL_DESC_CACHE_DIRTY
    if not _OFFICIAL_DESC_CACHE_DIRTY:
        return
    try:
        OFFICIAL_DESC_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        OFFICIAL_DESC_CACHE_FILE.write_text(
            json.dumps(OFFICIAL_DESC_CACHE, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _OFFICIAL_DESC_CACHE_DIRTY = False
    except Exception:
        pass


def _ensure_dict(x: Any) -> Dict[str, Any]:
    return x if isinstance(x, dict) else {}


def _ensure_list(x: Any) -> List[Any]:
    return x if isinstance(x, list) else []


def _clean_str(s: Any) -> str:
    return str(s).strip() if s is not None else ""


def _extract_text_any(v: Any) -> str:
    """APIの説明文候補をいろいろな型から取り出す（str / dict / list など）"""
    if v is None:
        return ""
    if isinstance(v, str):
        return _clean_str(v)
    if isinstance(v, dict):
        for k in ("value", "text", "comment", "description", "contents", "body", "summary"):
            if k in v:
                t = _clean_str(v.get(k))
                if t:
                    return t
        return ""
    if isinstance(v, list):
        for it in v:
            t = _extract_text_any(it)
            if t:
                return t
    return _clean_str(v)


def _looks_like_bad_description(t: str, title: str = "") -> bool:
    s = (t or "").strip()
    if not s:
        return True
    title_s = _clean_str(title)
    if title_s:
        norm_s = re.sub(r"[\W_]+", "", s)
        norm_t = re.sub(r"[\W_]+", "", title_s)
        if norm_s == norm_t:
            return True
        if norm_t and norm_s.startswith(norm_t) and len(norm_s) - len(norm_t) < 20:
            return True
    bad_starts = ("価格", "品番", "収録時間", "配信開始日", "レビュー", "お気に入り", "サンプル", "作品番号")
    if any(s.startswith(x) for x in bad_starts):
        return True
    bad_contains = ("円（税込）", "配信開始日", "収録時間", "お気に入り登録数", "レビュー平均", "ジャンル：", "メーカー：", "シリーズ：")
    if sum(1 for x in bad_contains if x in s) >= 2:
        return True
    return False


def _clean_description_text(s: Any, title: str = "") -> str:
    t = html.unescape(str(s or ""))
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"</p>|</div>|</li>|</section>", "\n", t, flags=re.I)
    t = re.sub(r"<li[^>]*>", "・", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"&nbsp;|&#160;", " ", t, flags=re.I)
    t = re.sub(r"[\t\r ]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = t.strip(" 　\n")
    if title:
        title_s = _clean_str(title)
        if title_s and t.startswith(title_s):
            t = t[len(title_s):].lstrip(" 　:-：｜|\n")
    if len(t) < 25:
        return ""
    if _looks_like_bad_description(t, title=title):
        return ""
    return t


def _extract_description_from_html(html_text: str, title: str = "") -> str:
    patterns = [
        r"<meta[^>]+property=[\"']og:description[\"'][^>]+content=[\"'](.*?)[\"']",
        r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"'](.*?)[\"']",
        r'"description"\s*:\s*"(.*?)"',
        r"itemprop=[\"']description[\"'][^>]*>(.*?)</",
    ]
    for pat in patterns:
        for m in re.finditer(pat, html_text, flags=re.I | re.S):
            cand = _clean_description_text(m.group(1), title=title)
            if cand:
                return cand

    heading_words = ("作品紹介", "作品内容", "商品紹介", "内容紹介", "ストーリー", "解説", "紹介")
    for hw in heading_words:
        block_pats = [
            rf'{hw}[^<]{{0,20}}</[^>]+>\s*<div[^>]*>(.*?)</div>',
            rf'{hw}[^<]{{0,20}}</[^>]+>\s*<p[^>]*>(.*?)</p>',
            rf">{hw}<[^>]*>\s*<div[^>]*class=[\"'][^\"']*(?:mg-b20|lh4|txt|text|description|summary)[^\"']*[\"'][^>]*>(.*?)</div>",
            rf'>{hw}<[^>]*>\s*<section[^>]*>(.*?)</section>',
        ]
        for pat in block_pats:
            for m in re.finditer(pat, html_text, flags=re.I | re.S):
                cand = _clean_description_text(m.group(1), title=title)
                if cand:
                    return cand

    generic_pats = [
        r"<div[^>]+class=[\"'][^\"']*(?:description|summary|lead|story|intro|item-detail|mg-b20|lh4)[^\"']*[\"'][^>]*>(.*?)</div>",
        r"<section[^>]+class=[\"'][^\"']*(?:description|summary|story|intro)[^\"']*[\"'][^>]*>(.*?)</section>",
        r"<p[^>]+class=[\"'][^\"']*(?:description|summary|story|intro|lead)[^\"']*[\"'][^>]*>(.*?)</p>",
    ]
    for pat in generic_pats:
        for m in re.finditer(pat, html_text, flags=re.I | re.S):
            cand = _clean_description_text(m.group(1), title=title)
            if cand:
                return cand
    return ""


def _fetch_official_description(official_url: str, title: str = "") -> str:
    global _OFFICIAL_DESC_CACHE_DIRTY
    url = _clean_str(official_url)
    if not url:
        return ""
    if url in OFFICIAL_DESC_CACHE:
        return _clean_description_text(OFFICIAL_DESC_CACHE.get(url, ""), title=title)

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        }
        cookies = {
            "age_check_done": "1",
            "cklg": "ja",
        }
        r = requests.get(url, headers=headers, cookies=cookies, timeout=TIMEOUT, allow_redirects=True)
        r.raise_for_status()
        desc = _extract_description_from_html(r.text, title=title)
    except Exception:
        desc = ""

    OFFICIAL_DESC_CACHE[url] = desc
    _OFFICIAL_DESC_CACHE_DIRTY = True
    return desc


def _extract_description_official(item: Dict[str, Any], title: str = "", official_url: str = "") -> str:
    """公式ページ由来の説明文っぽいものを抽出（見つからなければ空）"""
    for k in ("commentary", "comment", "description", "outline", "catchcopy", "introduction"):
        t = _clean_description_text(_extract_text_any(item.get(k)), title=title)
        if t:
            return t

    iteminfo = _ensure_dict(item.get("iteminfo"))
    for k in ("commentary", "comment", "description", "story", "outline", "summary", "introduction"):
        t = _clean_description_text(_extract_text_any(iteminfo.get(k)), title=title)
        if t:
            return t

    if official_url:
        t = _fetch_official_description(official_url, title=title)
        if t:
            return t

    return ""


def _extract_sale_end(item: Dict[str, Any]) -> str:
    """セール終了日っぽい情報があれば YYYY-MM-DD で返す（無ければ空）"""
    cand: List[str] = []
    for k in ("campaign", "campaigns", "campaign_info", "sale", "sales", "prices", "campaigns_info"):
        if k in item:
            cand.append(json.dumps(item.get(k), ensure_ascii=False))

    dumped = json.dumps(item, ensure_ascii=False)
    m = re.finditer(r'(?:sale|campaign|セール|キャンペーン|期間|終了|まで)[^\d]{0,60}(20\d{2})[\-/年](\d{1,2})[\-/月](\d{1,2})', dumped, flags=re.I)
    for mm in m:
        y, mo, d = int(mm.group(1)), int(mm.group(2)), int(mm.group(3))
        cand.append(f"{y:04d}-{mo:02d}-{d:02d}")

    if not cand:
        s = dumped
        mm = re.search(r'(20\d{2})[\-/](\d{1,2})[\-/](\d{1,2})', s)
        if mm:
            y, mo, d = int(mm.group(1)), int(mm.group(2)), int(mm.group(3))
            return f"{y:04d}-{mo:02d}-{d:02d}"
        return ""

    # 日付文字列候補のうち最も未来寄りを採用
    best = ""
    for s in cand:
        mm = re.search(r'(20\d{2})[\-/](\d{1,2})[\-/](\d{1,2})', s)
        if not mm:
            continue
        y, mo, d = int(mm.group(1)), int(mm.group(2)), int(mm.group(3))
        cur = f"{y:04d}-{mo:02d}-{d:02d}"
        if cur > best:
            best = cur
    return best


def _safe_https(url: str) -> str:
    # Mixed Content 回避（pics.dmm.co.jp 等は https で使えるケースが多い）
    url = _clean_str(url)
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def _looks_like_placeholder_url(url: str) -> bool:
    u = _clean_str(url).lower()
    if not u:
        return True
    hints = (
        "now_print",
        "nowprint",
        "nowprinting",
        "now_printing",
        "noimage",
        "no_img",
        "no-img",
        "nophoto",
        "no-photo",
        "comingsoon",
        "coming_soon",
        "placeholder",
    )
    return any(h in u for h in hints)


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
        # 重複排除 + NOW PRINTING / NO IMAGE などの明確なプレースホルダURLを除外
        out2: List[str] = []
        seen: set[str] = set()
        for u in out:
            uu = _clean_str(u)
            if not uu:
                continue
            if _looks_like_placeholder_url(uu):
                continue
            if uu in seen:
                continue
            seen.add(uu)
            out2.append(uu)
        return out2

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
    for k in ["review_count", "review_average", "price_min", "price_list_min", "price_sale_min", "api_rank"]:
        if new.get(k) is not None:
            merged[k] = new.get(k)

    return merged


def _make_work_from_item(item: Dict[str, Any], *, api_rank: Optional[int] = None) -> Dict[str, Any]:
    content_id = _clean_str(item.get("content_id"))
    title = _clean_str(item.get("title"))
    affiliate_url = _clean_str(item.get("affiliateURL") or item.get("affiliateUrl"))
    official_url = _clean_str(item.get("URL") or item.get("url"))
    url = affiliate_url or official_url

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
    price_min: Optional[int] = None
    price_list_min: Optional[int] = None
    price_sale_min: Optional[int] = None

    prices = _ensure_dict(item.get("prices"))

    def _to_int(v: Any) -> Optional[int]:
        if v is None:
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return int(v)
        s = _clean_str(v)
        if not s:
            return None
        # "1,980" / "1980" / "1980円" などに耐える
        digits = re.findall(r"\d+", s)
        if not digits:
            return None
        try:
            return int("".join(digits))
        except Exception:
            return None

    # DMM API v3 の prices には price / list_price が入る（現在価格/通常価格）
    price_sale_min = _to_int(prices.get("price"))
    price_list_min = _to_int(prices.get("list_price"))

    # deliveries からも最小価格を拾う（配信種別ごとに price がある）
    deliveries = prices.get("deliveries")
    if isinstance(deliveries, dict):
        delivery = deliveries.get("delivery")
        seq = delivery if isinstance(delivery, list) else ([delivery] if isinstance(delivery, dict) else [])
        vals: List[int] = []
        for d in seq:
            if isinstance(d, dict) and d.get("price") is not None:
                pi = _to_int(d.get("price"))
                if pi is not None:
                    vals.append(pi)
        if vals:
            min_delivery = min(vals)
            if (price_sale_min is None) or (min_delivery < price_sale_min):
                price_sale_min = min_delivery

    # 互換: これまで price_min は「表示用の最小価格」として扱っていた
    price_min = price_sale_min

    w: Dict[str, Any] = {
        "id": content_id,
        "title": title,
        "description": "",  # タイトル丸写しを避ける。概要は description_official / build 側で補完
        "description_official": _extract_description_official(item, title=title, official_url=(official_url or url)) or "",
        "sale_end": _extract_sale_end(item) or "",
        "release_date": date,
        "tags": genres,
        "actresses": actresses,
        "official_url": official_url or url,
        "affiliate_url": affiliate_url or None,
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
        "price_list_min": price_list_min,
        "price_sale_min": price_sale_min,
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
    if w["price_list_min"] is None:
        w.pop("price_list_min", None)
    if w["price_sale_min"] is None:
        w.pop("price_sale_min", None)
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
    # 追加時刻が無い旧データにも added_at を補完（新着タブ用）
    for _w in existing_works:
        if not _w.get('added_at'):
            rd = str(_w.get('release_date') or '').replace(' 00:00:00','').strip()
            _w['added_at'] = (rd + 'T00:00:00+09:00') if rd else '1970-01-01T00:00:00+09:00'

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

    # update-only（作品数を増やさない）でも、
    # 「テスト用に件数を固定している（trim / freeze-count）」場合は
    # 作品プールを“入れ替え”できるようにして、rank/review などの情報が
    # データ内に残るようにする。
    # （最終保存時に件数は固定されるので「増えない」は維持される）
    allow_pool_refresh_in_update_only = bool(update_only) and (bool(trim_to) or bool(args.freeze_count))

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
                    # 通常の update-only は「新規は追加しない」
                    # ただし trim / freeze-count で件数固定のテスト運用では
                    # 作品プールを更新して（入れ替え）、rank/review を維持する。
                    if (full_mode and (not update_only)) or allow_pool_refresh_in_update_only:
                        new_w.setdefault('added_at', _now_jst_iso())
                        new_w.setdefault('updated_at', None)
                        by_id[wid] = new_w
                        total_new += 1
                else:
                    if UPDATE_EXISTING:
                        merged = _merge_work(old_w, new_w)
                        # 更新判定（簡易）
                        if merged != old_w:
                            merged.setdefault('added_at', old_w.get('added_at') or _now_jst_iso())
                            merged['updated_at'] = _now_jst_iso()
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
    def _release_sort_key(w: Dict[str, Any]) -> str:
        return _parse_date_for_sort(_clean_str(w.get("release_date")))

    def _review_sort_key(w: Dict[str, Any]) -> tuple:
        # higher avg -> higher count -> newer
        avg = w.get("review_average")
        cnt = w.get("review_count")
        if avg is None:
            return (1, 0.0, 0, _release_sort_key(w))
        try:
            avgf = float(avg)
        except Exception:
            avgf = 0.0
        try:
            cnti = int(cnt) if cnt is not None else 0
        except Exception:
            cnti = 0
        return (0, -avgf, -cnti, _release_sort_key(w))

    def _smart_trim_pool(pool: List[Dict[str, Any]], target: int) -> List[Dict[str, Any]]:
        """テスト用の件数固定でも、rank/review が死なないように混ぜる。"""
        if target <= 0:
            return pool
        latest_sorted = sorted(pool, key=_release_sort_key, reverse=True)
        ranked_sorted = sorted(
            [w for w in pool if w.get("api_rank") is not None],
            key=lambda w: int(w.get("api_rank") or 10**9),
        )
        reviewed_sorted = sorted(
            [w for w in pool if w.get("review_average") is not None],
            key=_review_sort_key,
        )

        # quotas (fill不足は latest で埋める)
        q_latest = int(target * 0.70)
        q_rank = int(target * 0.15)
        q_review = target - q_latest - q_rank

        picked: List[Dict[str, Any]] = []
        seen: set[str] = set()

        def add_many(seq: List[Dict[str, Any]], n: int) -> None:
            nonlocal picked
            for w in seq:
                if len(picked) >= target or n <= 0:
                    return
                wid = _clean_str(w.get("id"))
                if not wid or wid in seen:
                    continue
                seen.add(wid)
                picked.append(w)
                n -= 1

        add_many(latest_sorted, q_latest)
        add_many(ranked_sorted, q_rank)
        add_many(reviewed_sorted, q_review)
        # fill rest by latest
        add_many(latest_sorted, target)
        return picked[:target]

    if update_only and (not allow_pool_refresh_in_update_only):
        # 既存の順序/件数を維持（作品数を増やさない）
        ordered_ids = [str(w.get("id")) for w in existing_works if w.get("id")]
        works = [by_id[i] for i in ordered_ids if i in by_id]
    else:
        # full もしくは「件数固定の入れ替え update-only」
        works = list(by_id.values())
        works.sort(key=_release_sort_key, reverse=True)

        # 件数上限（増えすぎ防止）
        if len(works) > max_total:
            works = works[:max_total]

        # 作品数固定（freeze-count）
        if update_only and args.freeze_count and existing_works:
            target = len(existing_works)
            works = works[:target]

        # 追加の切り詰め（テスト用）
        if trim_to and len(works) > trim_to:
            works = _smart_trim_pool(works, trim_to)

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
