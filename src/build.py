from __future__ import annotations

import json
import math
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from jinja2 import Environment, FileSystemLoader, select_autoescape


# =============================
# Paths
# =============================
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TEMPLATES_DIR = SRC / "templates"
DATA_DIR = SRC / "data"
ASSETS_SRC = SRC / "assets"

OUT = ROOT / "docs"
ASSETS_OUT = OUT / "assets"

WORKS_JSON = DATA_DIR / "works.json"


# =============================
# Config
# =============================
PER_PAGE = 60
SEARCH_CHUNK_SIZE = 600
RELATED_LIMIT = 12
POPULAR_TAGS_COUNT = 30
RSS_ITEMS = 50


# =============================
# Helpers
# =============================
def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"JSONが見つかりません: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def slugify(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return "unknown"
    for ch in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']:
        s = s.replace(ch, "_")
    s = s.replace(" ", "_")
    s = re.sub(r"_+", "_", s)
    return s[:120]


def clean_list(xs: Any) -> List[str]:
    if not isinstance(xs, list):
        return []
    out: List[str] = []
    for x in xs:
        t = str(x).strip()
        if t:
            out.append(t)
    return out


def safe_https(url: str | None) -> str | None:
    if not url:
        return None
    url = str(url).strip()
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def parse_dt(s: str) -> Optional[datetime]:
    """
    '2012/8/3 10:00' / '2026-02-13 10:00:00' / '2026-02-13'
    などをできるだけ datetime に変換。
    """
    s = (s or "").strip()
    if not s:
        return None
    s = s.replace("/", "-").replace("  ", " ")

    # YYYY-MM-DD HH:MM(:SS)?
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", s)
    if not m:
        return None
    y = int(m.group(1))
    mo = int(m.group(2))
    d = int(m.group(3))
    hh = int(m.group(4) or 0)
    mm = int(m.group(5) or 0)
    ss = int(m.group(6) or 0)
    try:
        return datetime(y, mo, d, hh, mm, ss)
    except Exception:
        return None


def dt_sort_key(w: Dict[str, Any]) -> Tuple[int, str]:
    dt = parse_dt(str(w.get("release_date") or ""))
    if dt:
        return (1, dt.isoformat())
    return (0, "")


def get_base_url(data: Dict[str, Any]) -> str:
    """
    sitemap/OGP/canonical 用の base_url。
    優先: 環境変数 SITE_URL > works.json の site_url/base_url > GitHub Pages 推測
    """
    base = (os.getenv("SITE_URL") or "").strip()
    if not base:
        base = (data.get("site_url") or data.get("base_url") or "").strip()
    if not base:
        repo = (os.getenv("GITHUB_REPOSITORY") or "").strip()  # owner/repo
        if repo and "/" in repo:
            owner, repo_name = repo.split("/", 1)
            base = f"https://{owner}.github.io/{repo_name}/"
    if base and not base.endswith("/"):
        base += "/"
    return base


def rel(depth: int, path: str) -> str:
    return ("../" * depth) + path


def page_depth(path_from_docs_root: str) -> int:
    """
    docs 直下からの相対パス（例: 'works/xxx/'）を渡すと、階層の深さを返す。
    '' (index) -> 0
    'pages/1/' -> 2? 実際ファイルは pages/1/index.html なので深さ=2
    """
    p = path_from_docs_root.strip("/")
    if not p:
        return 0
    return len(p.split("/"))


def unique_keep_order(items: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def has_sample_images(w: Dict[str, Any]) -> bool:
    return bool(w.get("sample_images_large") or w.get("sample_images_small"))


def sample_images_count(w: Dict[str, Any]) -> int:
    xs = w.get("sample_images_large") or w.get("sample_images_small") or []
    return len(xs) if isinstance(xs, list) else 0


def has_sample_movie(w: Dict[str, Any]) -> bool:
    return bool(w.get("sample_movie"))


def best_sample_images_for_lightbox(w: Dict[str, Any]) -> List[str]:
    xs = w.get("sample_images_large") or w.get("sample_images_small") or []
    return [x for x in xs if isinstance(x, str) and x.strip()]


def best_sample_images_for_grid(w: Dict[str, Any]) -> List[str]:
    xs = w.get("sample_images_small") or w.get("sample_images_large") or []
    return [x for x in xs if isinstance(x, str) and x.strip()]


def video_aspect_ratio(w: Dict[str, Any]) -> str:
    """
    sample_movie_size があればそこから推定。無ければ 16/9。
    """
    size = w.get("sample_movie_size")
    if isinstance(size, dict):
        try:
            ww = int(size.get("w"))
            hh = int(size.get("h"))
            if ww > 0 and hh > 0:
                return f"{ww} / {hh}"
        except Exception:
            pass
    # URLに size=720_480 が入ることが多いので、そこからも推定
    u = str(w.get("sample_movie") or "")
    m = re.search(r"size=(\d+)_(\d+)", u)
    if m:
        return f"{int(m.group(1))} / {int(m.group(2))}"
    return "16 / 9"


# =============================
# Normalize works
# =============================
def normalize_work(w: Dict[str, Any]) -> Dict[str, Any]:
    ww = dict(w or {})
    ww["id"] = str(ww.get("id") or "").strip()
    ww["title"] = str(ww.get("title") or "").strip()
    ww["description"] = str(ww.get("description") or ww["title"] or "").strip()
    ww["release_date"] = str(ww.get("release_date") or "").strip()

    ww["tags"] = clean_list(ww.get("tags"))
    ww["actresses"] = clean_list(ww.get("actresses"))

    ww["hero_image"] = safe_https(ww.get("hero_image"))
    ww["official_url"] = ww.get("official_url") or ww.get("affiliate_url") or ww.get("affiliateURL") or ww.get("URL") or ww.get("url")
    ww["official_url"] = safe_https(ww["official_url"]) if isinstance(ww["official_url"], str) else ww["official_url"]

    ww["maker"] = str(ww.get("maker") or "").strip()
    ww["series"] = str(ww.get("series") or "").strip()
    ww["label"] = str(ww.get("label") or "").strip()

    ww["sample_images_small"] = clean_list(ww.get("sample_images_small"))
    ww["sample_images_large"] = clean_list(ww.get("sample_images_large"))

    ww["sample_movie"] = safe_https(ww.get("sample_movie"))
    if isinstance(ww.get("sample_movie_urls"), dict):
        ww["sample_movie_urls"] = {k: safe_https(v) for k, v in ww["sample_movie_urls"].items() if isinstance(k, str) and isinstance(v, str)}
    else:
        ww["sample_movie_urls"] = {}

    # numbers
    ww["api_rank"] = ww.get("api_rank")
    try:
        ww["api_rank"] = int(ww["api_rank"]) if ww["api_rank"] is not None else None
    except Exception:
        ww["api_rank"] = None

    ww["review_count"] = ww.get("review_count")
    try:
        ww["review_count"] = int(ww["review_count"]) if ww["review_count"] is not None else None
    except Exception:
        ww["review_count"] = None

    ww["review_average"] = ww.get("review_average")
    try:
        ww["review_average"] = float(ww["review_average"]) if ww["review_average"] is not None else None
    except Exception:
        ww["review_average"] = None

    ww["price_min"] = ww.get("price_min")
    try:
        ww["price_min"] = int(ww["price_min"]) if ww["price_min"] is not None else None
    except Exception:
        ww["price_min"] = None

    # computed flags for templates
    ww["_has_img"] = has_sample_images(ww)
    ww["_img_count"] = sample_images_count(ww)
    ww["_has_mov"] = has_sample_movie(ww)
    ww["_dt"] = parse_dt(ww["release_date"])
    return ww


# =============================
# Index builders
# =============================
def index_by_key(works: List[Dict[str, Any]], key: str) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for w in works:
        v = str(w.get(key) or "").strip()
        if v:
            out.setdefault(v, []).append(w)
    return out


def index_by_list_field(works: List[Dict[str, Any]], field: str) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for w in works:
        for v in w.get(field) or []:
            vv = str(v).strip()
            if vv:
                out.setdefault(vv, []).append(w)
    return out


def sort_works_newest(xs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(xs, key=lambda w: (w["_dt"] is not None, w["_dt"] or datetime.min), reverse=True)


def compute_related(works: List[Dict[str, Any]]) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    """
    id -> { actress: [...], genre: [...], maker: [...], series: [...] }
    """
    by_actress = index_by_list_field(works, "actresses")
    by_genre = index_by_list_field(works, "tags")
    by_maker = index_by_key(works, "maker")
    by_series = index_by_key(works, "series")

    relmap: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for w in works:
        wid = w["id"]
        used: Set[str] = {wid}

        def pick(src: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
            out: List[Dict[str, Any]] = []
            for cand in sort_works_newest(src):
                cid = cand["id"]
                if cid in used:
                    continue
                out.append(cand)
                used.add(cid)
                if len(out) >= limit:
                    break
            return out

        # related actress: まず1人目の女優を優先
        actress_list = w.get("actresses") or []
        ra: List[Dict[str, Any]] = []
        if actress_list:
            ra = pick(by_actress.get(actress_list[0], []), RELATED_LIMIT)

        # related series/maker/genre は重複を避けつつ追加
        rm: List[Dict[str, Any]] = []
        if w.get("maker"):
            rm = pick(by_maker.get(w["maker"], []), RELATED_LIMIT)

        rs: List[Dict[str, Any]] = []
        if w.get("series"):
            rs = pick(by_series.get(w["series"], []), RELATED_LIMIT)

        rg: List[Dict[str, Any]] = []
        # 代表ジャンル（先頭）で1ブロック作る
        tags = w.get("tags") or []
        if tags:
            rg = pick(by_genre.get(tags[0], []), RELATED_LIMIT)

        relmap[wid] = {"actress": ra, "maker": rm, "series": rs, "genre": rg}
    return relmap


# =============================
# Search index (chunk + manifest)
# =============================
def build_search_index(works_sorted: List[Dict[str, Any]]) -> None:
    ensure_dir(ASSETS_OUT)

    cards: List[Dict[str, Any]] = []
    tag_counts: Dict[str, int] = {}
    actress_counts: Dict[str, int] = {}
    maker_counts: Dict[str, int] = {}
    series_counts: Dict[str, int] = {}

    for w in works_sorted:
        wid = w.get("id")
        if not wid:
            continue

        tags = w.get("tags") or []
        for t in tags:
            tag_counts[t] = tag_counts.get(t, 0) + 1

        actresses = w.get("actresses") or []
        for a in actresses:
            actress_counts[a] = actress_counts.get(a, 0) + 1

        maker = (w.get("maker") or "").strip()
        if maker:
            maker_counts[maker] = maker_counts.get(maker, 0) + 1

        series = (w.get("series") or "").strip()
        if series:
            series_counts[series] = series_counts.get(series, 0) + 1

        cards.append(
            {
                "id": wid,
                "title": w.get("title") or "",
                "release_date": w.get("release_date") or "",
                "hero_image": w.get("hero_image") or "",
                "path": f"works/{wid}/",
                "tags": tags,
                "actresses": actresses,
                "maker": maker,
                "series": series,
                "has_img": bool(w.get("_has_img")),
                "img_count": int(w.get("_img_count") or 0),
                "has_mov": bool(w.get("_has_mov")),
                "api_rank": w.get("api_rank"),
            }
        )

    # chunking
    chunks: List[Dict[str, Any]] = []
    total = len(cards)
    n_chunks = math.ceil(total / SEARCH_CHUNK_SIZE) if total else 0
    for i in range(n_chunks):
        chunk_cards = cards[i * SEARCH_CHUNK_SIZE : (i + 1) * SEARCH_CHUNK_SIZE]
        fname = f"works_index_{i:03d}.json"
        write_json(ASSETS_OUT / fname, chunk_cards)
        chunks.append({"file": fname, "count": len(chunk_cards)})

    popular_tags = sorted(tag_counts.items(), key=lambda kv: kv[1], reverse=True)[:POPULAR_TAGS_COUNT]
    manifest = {
        "version": 2,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": total,
        "chunk_size": SEARCH_CHUNK_SIZE,
        "chunks": chunks,
        "popular_tags": [{"name": k, "count": v} for k, v in popular_tags],
        "tags": sorted(tag_counts.keys(), key=lambda s: s.lower()),
        "actresses": sorted(actress_counts.keys(), key=lambda s: s.lower()),
        "makers": sorted(maker_counts.keys(), key=lambda s: s.lower()),
        "series": sorted(series_counts.keys(), key=lambda s: s.lower()),
    }
    write_json(ASSETS_OUT / "works_index_manifest.json", manifest)

    # compat: single file (small sites)
    if total <= SEARCH_CHUNK_SIZE:
        write_json(ASSETS_OUT / "works_index.json", cards)


# =============================
# Assets
# =============================
def copy_assets() -> None:
    ensure_dir(ASSETS_OUT)
    if ASSETS_SRC.exists():
        for p in ASSETS_SRC.rglob("*"):
            if p.is_file():
                relp = p.relative_to(ASSETS_SRC)
                dest = ASSETS_OUT / relp
                ensure_dir(dest.parent)
                shutil.copyfile(p, dest)


# =============================
# Sitemap / Robots / RSS
# =============================
def build_sitemap(base_url: str, urls: List[str]) -> None:
    """
    urls: docs ルートからの相対パス（例: 'works/xxx/'）
    """
    if not base_url:
        # base_url が無いと sitemap の loc を作れないので、雛形だけ出す
        write_text(OUT / "sitemap.xml", "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!-- SITE_URL が未設定のため loc を生成できません -->\n")
        return

    lines = [
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
        "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">",
    ]
    for u in urls:
        loc = base_url + u.lstrip("/")
        lines.append("  <url>")
        lines.append(f"    <loc>{loc}</loc>")
        lines.append("  </url>")
    lines.append("</urlset>\n")
    write_text(OUT / "sitemap.xml", "\n".join(lines))


def build_robots(base_url: str) -> None:
    lines = [
        "User-agent: *",
        "Disallow:",
    ]
    if base_url:
        lines.append(f"Sitemap: {base_url}sitemap.xml")
    write_text(OUT / "robots.txt", "\n".join(lines) + "\n")


def build_rss(base_url: str, site_name: str, works_sorted: List[Dict[str, Any]]) -> None:
    if not base_url:
        return
    items = works_sorted[:RSS_ITEMS]
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")

    def esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    parts = [
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
        "<rss version=\"2.0\">",
        "<channel>",
        f"<title>{esc(site_name)}</title>",
        f"<link>{base_url}</link>",
        f"<description>{esc(site_name)} - Latest updates</description>",
        f"<lastBuildDate>{now}</lastBuildDate>",
    ]
    for w in items:
        wid = w["id"]
        link = base_url + f"works/{wid}/"
        title = esc(w.get("title") or "")
        desc = esc((w.get("description") or "")[:180])
        parts += [
            "<item>",
            f"<title>{title}</title>",
            f"<link>{link}</link>",
            f"<guid>{link}</guid>",
            f"<description>{desc}</description>",
            "</item>",
        ]
    parts += ["</channel>", "</rss>\n"]
    write_text(OUT / "feed.xml", "\n".join(parts))


# =============================
# Main build
# =============================
def main() -> None:
    data = load_json(WORKS_JSON)
    site_name = data.get("site_name", "Catalog")

    base_url = get_base_url(data)

    works_raw = data.get("works") or []
    works = [normalize_work(w) for w in works_raw if isinstance(w, dict) and str(w.get("id") or "").strip()]
    works_sorted = sort_works_newest(works)

    # indexes
    by_actress = index_by_list_field(works, "actresses")
    by_genre = index_by_list_field(works, "tags")
    by_maker = index_by_key(works, "maker")
    by_series = index_by_key(works, "series")

    actresses_keys = sorted(by_actress.keys(), key=lambda s: s.lower())
    genres_keys = sorted(by_genre.keys(), key=lambda s: s.lower())
    makers_keys = sorted(by_maker.keys(), key=lambda s: s.lower())
    series_keys = sorted(by_series.keys(), key=lambda s: s.lower())

    # related map
    relmap = compute_related(works)

    # templates
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )

    env.filters['slugify'] = slugify

    tpl_index = env.get_template("index.html")
    tpl_list = env.get_template("list_works.html")
    tpl_page = env.get_template("page.html")
    tpl_search = env.get_template("search.html")
    tpl_featured = env.get_template("featured.html")

    sort_tabs = [
        {"id": "latest", "label": "最新順", "href": ""},
        {"id": "rank", "label": "ランキング順", "href": "rank/"},
        {"id": "reviews", "label": "レビュー順", "href": "reviews/"},
        {"id": "movies", "label": "動画あり", "href": "movies/"},
        {"id": "images", "label": "画像多い", "href": "images/"},
    ]

    # clean docs (keep assets? rebuild all)
    if OUT.exists():
        shutil.rmtree(OUT)
    ensure_dir(OUT)
    ensure_dir(ASSETS_OUT)

    copy_assets()
    build_search_index(works_sorted)

    # collect URLs for sitemap
    sitemap_urls: List[str] = []

    def render_index(
        out_dir: Path,
        *,
        page_title: str,
        heading: str,
        works_list: List[Dict[str, Any]],
        path_from_root: str,
        pager: Optional[Dict[str, Any]] = None,
        sort_id: str = "latest",
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        depth = page_depth(path_from_root)
        root_path = "../" * depth
        css_path = rel(depth, "assets/style.css")
        html = tpl_index.render(
            site_name=site_name,
            base_url=base_url,
            canonical_url=(base_url + path_from_root) if base_url else "",
            page_title=page_title,
            heading=heading,
            works=works_list,
            css_path=css_path,
            root_path=root_path,
            nav_active=(extra or {}).get("nav_active", ""),
            pager=pager,
            sort_tabs=(sort_tabs if (extra or {}).get("show_sort_tabs") else None),
            sort_id=sort_id,
        )
        write_text(out_dir / "index.html", html)
        sitemap_urls.append(path_from_root)

    def render_list(
        out_dir: Path,
        *,
        page_title: str,
        heading: str,
        items: List[Dict[str, str]],
        path_from_root: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        depth = page_depth(path_from_root)
        root_path = "../" * depth
        css_path = rel(depth, "assets/style.css")
        html = tpl_list.render(
            site_name=site_name,
            base_url=base_url,
            canonical_url=(base_url + path_from_root) if base_url else "",
            page_title=page_title,
            heading=heading,
            items=items,
            css_path=css_path,
            root_path=root_path,
            nav_active=(extra or {}).get("nav_active", ""),
        )
        write_text(out_dir / "index.html", html)
        sitemap_urls.append(path_from_root)

    def render_page(
        out_dir: Path,
        *,
        w: Dict[str, Any],
        path_from_root: str,
    ) -> None:
        depth = page_depth(path_from_root)
        root_path = "../" * depth
        css_path = rel(depth, "assets/style.css")

        # lightbox images: hero -> samples(large)
        hero = w.get("hero_image") or ""
        samples = best_sample_images_for_lightbox(w)
        lightbox = unique_keep_order([hero] + samples) if hero else unique_keep_order(samples)
        grid = best_sample_images_for_grid(w)

        rels = relmap.get(w["id"], {})
        html = tpl_page.render(
            site_name=site_name,
            base_url=base_url,
            canonical_url=(base_url + path_from_root) if base_url else "",
            page_title=w.get("title") or "",
            css_path=css_path,
            root_path=root_path,
            nav_active="",
            w=w,
            lightbox_images=lightbox,
            grid_images=grid,
            video_aspect_ratio=video_aspect_ratio(w),
            related_actress=rels.get("actress", []),
            related_genre=rels.get("genre", []),
            related_maker=rels.get("maker", []),
            related_series=rels.get("series", []),
        )
        write_text(out_dir / "index.html", html)
        sitemap_urls.append(path_from_root)

    def render_search() -> None:
        path_from_root = "search/"
        depth = page_depth(path_from_root)
        root_path = "../" * depth
        css_path = rel(depth, "assets/style.css")
        js_path = rel(depth, "assets/search.js")
        manifest_path = rel(depth, "assets/works_index_manifest.json")
        html = tpl_search.render(
            site_name=site_name,
            base_url=base_url,
            canonical_url=(base_url + path_from_root) if base_url else "",
            page_title="検索",
            css_path=css_path,
            root_path=root_path,
            js_path=js_path,
            manifest_path=manifest_path,
            nav_active="search",
        )
        write_text(OUT / "search" / "index.html", html)
        sitemap_urls.append(path_from_root)

    def render_featured_hub() -> None:
        path_from_root = "featured/"
        depth = page_depth(path_from_root)
        root_path = "../" * depth
        css_path = rel(depth, "assets/style.css")
        html = tpl_featured.render(
            site_name=site_name,
            base_url=base_url,
            canonical_url=(base_url + path_from_root) if base_url else "",
            page_title="特集",
            css_path=css_path,
            root_path=root_path,
            nav_active="featured",
            featured_links=[
                {"href": f"{root_path}rank/", "title": "ランキング（API rank）"},
                {"href": f"{root_path}movies/", "title": "サンプル動画あり"},
                {"href": f"{root_path}images/", "title": "サンプル画像あり"},
            ],
        )
        write_text(OUT / "featured" / "index.html", html)
        sitemap_urls.append(path_from_root)

    # ===== Latest listing (Home) =====
    total_pages = max(1, math.ceil(len(works_sorted) / PER_PAGE))

    # page 1 is the top (latest)
    home_works = works_sorted[:PER_PAGE]
    pager_home = {
        "page": 1,
        "total": total_pages,
        "prev": None,
        "next": ("pages/2/" if total_pages > 1 else None),
    }
    render_index(
        OUT,
        page_title="トップ",
        heading="作品一覧",
        works_list=home_works,
        path_from_root="",
        pager=pager_home,
        sort_id="latest",
        extra={"nav_active": "home", "show_sort_tabs": True},
    )

    # legacy: /pages/1/ -> redirect to /
    legacy_dir = OUT / "pages" / "1"
    ensure_dir(legacy_dir)
    write_text(
        legacy_dir / "index.html",
        '<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=../../"><title>Redirect</title>',
    )

    # page 2..N
    for p in range(2, total_pages + 1):
        start = (p - 1) * PER_PAGE
        end = p * PER_PAGE
        page_works = works_sorted[start:end]
        pager = {
            "page": p,
            "total": total_pages,
            "prev": ("" if p == 2 else f"pages/{p-1}/"),
            "next": (f"pages/{p+1}/" if p < total_pages else None),
        }
        out_dir = OUT / "pages" / str(p)
        render_index(
            out_dir,
            page_title=f"作品一覧 {p}/{total_pages}",
            heading="作品一覧",
            works_list=page_works,
            path_from_root=f"pages/{p}/",
            pager=pager,
            sort_id="latest",
            extra={"nav_active": "home", "show_sort_tabs": True},
        )
        sitemap_urls.append(f"pages/{p}/")

    # ===== Sort pages (tabs on top) =====
    def render_sort_pages(*, key: str, heading: str, works_list: List[Dict[str, Any]]) -> None:
        base = key
        total = max(1, math.ceil(len(works_list) / PER_PAGE))
        for p in range(1, total + 1):
            start = (p - 1) * PER_PAGE
            end = p * PER_PAGE
            page_works = works_list[start:end]

            if p == 1:
                out_dir = OUT / base
                path_from_root = f"{base}/"
            else:
                out_dir = OUT / base / "pages" / str(p)
                path_from_root = f"{base}/pages/{p}/"

            pager = {
                "page": p,
                "total": total,
                "prev": (None if p == 1 else (f"{base}/" if p == 2 else f"{base}/pages/{p-1}/")),
                "next": (None if p == total else f"{base}/pages/{p+1}/"),
            }

            render_index(
                out_dir,
                page_title=(heading if p == 1 else f"{heading} {p}/{total}"),
                heading=heading,
                works_list=page_works,
                path_from_root=path_from_root,
                pager=pager,
                sort_id=key,
                extra={"nav_active": "home", "show_sort_tabs": True},
            )
            sitemap_urls.append(path_from_root)

    # ランキング順（API rank）
    ranked = [w for w in works_sorted if w.get("api_rank") is not None]
    if ranked:
        ranked.sort(key=lambda w: w.get("api_rank") or 10**9)
        for i, w in enumerate(ranked, start=1):
            w["_tmp_rank"] = i
    else:
        # fallback: if api_rank is missing, keep latest order as a temporary ranking
        ranked = list(works_sorted)
        for i, w in enumerate(ranked, start=1):
            w["_tmp_rank"] = i

    render_sort_pages(key="rank", heading="ランキング", works_list=ranked)

    # レビュー順（平均点→件数→新しさ）
    def review_sort_key(w: Dict[str, Any]):
        avg = w.get("review_average")
        cnt = w.get("review_count")
        # missing goes last
        if avg is None:
            return (1, 0.0, 0, w.get("_release_ts") or 0)
        return (0, -(avg or 0.0), -(cnt or 0), -(w.get("_release_ts") or 0))

    reviewed = list(works_sorted)
    reviewed.sort(key=review_sort_key)
    render_sort_pages(key="reviews", heading="レビュー順", works_list=reviewed)

    # サンプル動画あり（新しい順）
    w_mov = [w for w in works_sorted if w.get("_has_mov")]
    render_sort_pages(key="movies", heading="サンプル動画あり", works_list=w_mov)

    # 画像多い（枚数→新しい順）
    w_img = [w for w in works_sorted if w.get("_has_img")]
    w_img.sort(key=lambda w: (-(w.get("_img_count") or 0), -(w.get("_release_ts") or 0)))
    render_sort_pages(key="images", heading="サンプル画像（多い順）", works_list=w_img)

    # ===== Works pages =====
    for w in works_sorted:
        wid = w["id"]
        out_dir = OUT / "works" / wid
        render_page(out_dir, w=w, path_from_root=f"works/{wid}/")

    # ===== Actresses =====
    actress_items = [{"name": a, "href": f"actresses/{slugify(a)}/"} for a in actresses_keys]
    render_list(
        OUT / "actresses",
        page_title="女優一覧",
        heading="女優一覧",
        items=actress_items,
        path_from_root="actresses/",
        extra={"nav_active": "actresses"},
    )
    for a in actresses_keys:
        works_a = sort_works_newest(by_actress.get(a, []))
        out_dir = OUT / "actresses" / slugify(a)
        render_index(
            out_dir,
            page_title=f"女優: {a}",
            heading=f"女優: {a}",
            works_list=works_a,
            path_from_root=f"actresses/{slugify(a)}/",
            pager=None,
            extra={"nav_active": "actresses"},
        )

    # ===== Genres =====
    genre_items = [{"name": g, "href": f"genres/{slugify(g)}/"} for g in genres_keys]
    render_list(
        OUT / "genres",
        page_title="ジャンル一覧",
        heading="ジャンル一覧",
        items=genre_items,
        path_from_root="genres/",
        extra={"nav_active": "genres"},
    )
    for g in genres_keys:
        works_g = sort_works_newest(by_genre.get(g, []))
        out_dir = OUT / "genres" / slugify(g)
        render_index(
            out_dir,
            page_title=f"ジャンル: {g}",
            heading=f"ジャンル: {g}",
            works_list=works_g,
            path_from_root=f"genres/{slugify(g)}/",
            pager=None,
            extra={"nav_active": "genres"},
        )

    # ===== Makers =====
    maker_items = [{"name": m, "href": f"makers/{slugify(m)}/"} for m in makers_keys]
    render_list(
        OUT / "makers",
        page_title="メーカー一覧",
        heading="メーカー一覧",
        items=maker_items,
        path_from_root="makers/",
        extra={"nav_active": "makers"},
    )
    for m in makers_keys:
        works_m = sort_works_newest(by_maker.get(m, []))
        out_dir = OUT / "makers" / slugify(m)
        render_index(
            out_dir,
            page_title=f"メーカー: {m}",
            heading=f"メーカー: {m}",
            works_list=works_m,
            path_from_root=f"makers/{slugify(m)}/",
            pager=None,
            extra={"nav_active": "makers"},
        )

    # ===== Series =====
    series_items = [{"name": s, "href": f"series/{slugify(s)}/"} for s in series_keys]
    render_list(
        OUT / "series",
        page_title="シリーズ一覧",
        heading="シリーズ一覧",
        items=series_items,
        path_from_root="series/",
        extra={"nav_active": "series"},
    )
    for s in series_keys:
        works_s = sort_works_newest(by_series.get(s, []))
        out_dir = OUT / "series" / slugify(s)
        render_index(
            out_dir,
            page_title=f"シリーズ: {s}",
            heading=f"シリーズ: {s}",
            works_list=works_s,
            path_from_root=f"series/{slugify(s)}/",
            pager=None,
            extra={"nav_active": "series"},
        )

    # ===== Featured =====
    render_featured_hub()

    # legacy featured subpages -> redirect to new sort pages
    ensure_dir(OUT / "featured" / "rank")
    write_text(OUT / "featured" / "rank" / "index.html",
               '<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=../../rank/"><title>Redirect</title>')
    ensure_dir(OUT / "featured" / "sample-movies")
    write_text(OUT / "featured" / "sample-movies" / "index.html",
               '<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=../../movies/"><title>Redirect</title>')
    ensure_dir(OUT / "featured" / "sample-images")
    write_text(OUT / "featured" / "sample-images" / "index.html",
               '<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=../../images/"><title>Redirect</title>')

    # ===== Search =====
    render_search()

    # ===== SEO =====
    # sitemap: 作品数が多いと大きくなるので、必要なら index 分割するが、まずは単一で
    build_sitemap(base_url, sitemap_urls)
    build_robots(base_url)
    build_rss(base_url, site_name, works_sorted)

    print(f"OK: built docs at {OUT}")


if __name__ == "__main__":
    main()
