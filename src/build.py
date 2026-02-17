from __future__ import annotations

import json
import os
import datetime
import math
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional
from xml.sax.saxutils import escape as xml_escape

from jinja2 import Environment, FileSystemLoader, select_autoescape


# =============================
# Paths
# =============================
ROOT = Path(__file__).resolve().parents[1]      # repo root
SRC = ROOT / "src"
TEMPLATES = SRC / "templates"
DATA_DIR = SRC / "data"
OUT = ROOT / "docs"
ASSETS_OUT = OUT / "assets"

WORKS_JSON = DATA_DIR / "works.json"


# =============================
# Template names
# =============================
TPL_INDEX = "index.html"        # works grid page (home / pages / facet detail)
TPL_PAGE = "page.html"          # work detail page
TPL_LIST = "list_works.html"    # simple list page (facet index)
TPL_SEARCH = "search.html"      # search page


# =============================
# Helpers
# =============================

def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"JSONが見つかりません: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_trailing_slash(u: str) -> str:
    u = (u or "").strip()
    if not u:
        return ""
    return u if u.endswith("/") else (u + "/")


def guess_site_url(data: Dict[str, Any]) -> str:
    # works.json の site_url を優先（カスタムドメイン対応）
    site_url = str(data.get("site_url") or "").strip()
    if site_url:
        return ensure_trailing_slash(site_url)

    # GitHub Pages 推測（Actions上で有効）
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()  # "owner/repo"
    if repo and "/" in repo:
        owner, name = repo.split("/", 1)
        # user/organization pages（repo名が owner.github.io）
        if name.lower() == f"{owner.lower()}.github.io":
            return f"https://{owner}.github.io/"
        return f"https://{owner}.github.io/{name}/"

    # ローカル実行では空（sitemap/RSSは生成しない）
    return ""


def abs_url(base: str, rel: str) -> str:
    base = ensure_trailing_slash(base)
    rel = (rel or "").lstrip("/")
    return base + rel


def write_robots_txt(out_dir: Path, base_url: str) -> None:
    lines = [
        "User-agent: *",
        "Allow: /",
    ]
    if base_url:
        lines.append(f"Sitemap: {abs_url(base_url, 'sitemap.xml')}")
    write_text(out_dir / "robots.txt", "\n".join(lines) + "\n")


def write_sitemap_xml(out_dir: Path, base_url: str, rel_urls: List[str], lastmod_date: str) -> None:
    # base_url が無い場合は absolute URL を作れないので生成しない
    if not base_url:
        return
    urlset = []
    for rel in rel_urls:
        loc = abs_url(base_url, rel)
        if not loc.endswith("/") and not loc.endswith(".html"):
            loc += "/"
        urlset.append(
            f"  <url><loc>{xml_escape(loc)}</loc><lastmod>{lastmod_date}</lastmod></url>"
        )
    xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n"
        + "\n".join(urlset)
        + "\n</urlset>\n"
    )
    write_text(out_dir / "sitemap.xml", xml)


def write_rss_feed(out_dir: Path, base_url: str, site_name: str, works_sorted: List[Dict[str, Any]], max_items: int = 50) -> None:
    if not base_url:
        return

    items_xml = []
    for w in works_sorted[:max_items]:
        wid = w.get("id")
        if not wid:
            continue
        link = abs_url(base_url, f"works/{wid}/")
        title = xml_escape(w.get("title") or "")
        desc = xml_escape((w.get("description") or w.get("title") or "")[:500])
        pub = xml_escape(w.get("release_date") or "")
        items_xml.append(
            f"<item><title>{title}</title><link>{xml_escape(link)}</link><guid>{xml_escape(link)}</guid>"
            f"<description>{desc}</description><pubDate>{pub}</pubDate></item>"
        )

    channel = (
        f"<channel><title>{xml_escape(site_name)}</title>"
        f"<link>{xml_escape(base_url)}</link>"
        f"<description>{xml_escape(site_name)} の新着作品</description>"
        + "".join(items_xml)
        + "</channel>"
    )
    rss = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n" + f"<rss version=\"2.0\">{channel}</rss>\n"
    write_text(out_dir / "feed.xml", rss)


def slugify_simple(s: str) -> str:
    s = (s or "").strip()
    for ch in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']:
        s = s.replace(ch, "_")
    s = s.replace(" ", "_")
    return s or "unknown"


def parse_release_date_sort_key(s: str) -> str:
    return (s or "").replace("/", "-").replace("  ", " ").strip()


def parse_release_date_iso(s: str) -> Optional[str]:
    """release_date を可能な範囲で ISO 8601 (YYYY-MM-DD) に寄せる。"""
    s = (s or "").strip()
    if not s:
        return None

    # よくある形式: 2012/8/3 10:00
    for fmt in ("%Y/%m/%d %H:%M", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.datetime.strptime(s, fmt)
            # datePublished は日付だけでもOK
            return dt.date().isoformat()
        except Exception:
            pass

    # 先頭10文字が YYYY-MM-DD なら採用
    if len(s) >= 10 and s[4] in "-/" and s[7] in "-/":
        x = s[:10].replace("/", "-")
        # 簡易チェック
        try:
            datetime.date.fromisoformat(x)
            return x
        except Exception:
            return None

    return None


def pick_name(v: Any) -> Optional[str]:
    """maker/series/label などの形揺れを吸収して name を返す。"""
    if v is None:
        return None
    if isinstance(v, str):
        vv = v.strip()
        return vv or None
    if isinstance(v, dict):
        name = v.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    if isinstance(v, list):
        for it in v:
            n = pick_name(it)
            if n:
                return n
    return None


def normalize_work(w: Dict[str, Any]) -> Dict[str, Any]:
    ww = dict(w or {})
    ww["id"] = str(ww.get("id") or "").strip()
    ww["title"] = str(ww.get("title") or "").strip()
    ww["description"] = str(ww.get("description") or ww["title"] or "").strip()
    ww["release_date"] = str(ww.get("release_date") or "").strip()

    ww["hero_image"] = ww.get("hero_image") or None
    ww["official_url"] = ww.get("official_url") or ww.get("affiliate_url") or ww.get("affiliateURL") or ww.get("URL") or None

    ww["tags"] = [str(x).strip() for x in (ww.get("tags") or []) if str(x).strip()]
    ww["actresses"] = [str(x).strip() for x in (ww.get("actresses") or []) if str(x).strip()]

    ww["sample_images"] = [str(x).strip() for x in (ww.get("sample_images") or []) if str(x).strip()]
    ww["sample_movie"] = str(ww.get("sample_movie") or ww.get("sample_movie_url") or "").strip() or None
    if not isinstance(ww.get("sample_movie_urls"), dict):
        ww["sample_movie_urls"] = {}

    if not isinstance(ww.get("review"), dict):
        ww["review"] = {}
    if not isinstance(ww.get("prices"), dict):
        ww["prices"] = {}

    ww["volume"] = ww.get("volume")

    # 追加のファセット（無い場合は None）
    ww["maker"] = pick_name(ww.get("maker"))
    ww["series"] = pick_name(ww.get("series"))
    ww["label"] = pick_name(ww.get("label"))

    return ww


def build_facets(works: List[Dict[str, Any]]):
    actresses: Dict[str, List[Dict[str, Any]]] = {}
    genres: Dict[str, List[Dict[str, Any]]] = {}
    makers: Dict[str, List[Dict[str, Any]]] = {}
    series: Dict[str, List[Dict[str, Any]]] = {}

    for w in works:
        for a in (w.get("actresses") or []):
            actresses.setdefault(a, []).append(w)
        for g in (w.get("tags") or []):
            genres.setdefault(g, []).append(w)
        mk = w.get("maker")
        if mk:
            makers.setdefault(mk, []).append(w)
        se = w.get("series")
        if se:
            series.setdefault(se, []).append(w)

    actresses_keys = sorted(actresses.keys(), key=lambda s: s.lower())
    genres_keys = sorted(genres.keys(), key=lambda s: s.lower())
    makers_keys = sorted(makers.keys(), key=lambda s: s.lower())
    series_keys = sorted(series.keys(), key=lambda s: s.lower())

    return actresses, actresses_keys, genres, genres_keys, makers, makers_keys, series, series_keys


# =============================
# Search index (lightweight)
# =============================

def make_search_index(works_sorted: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for w in works_sorted:
        wid = w.get("id")
        if not wid:
            continue

        # サンプル有無（検索/一覧でバッジ表示用）
        si = len(w.get("sample_images") or [])
        sm = bool(w.get("sample_movie"))
        if (not sm) and isinstance(w.get("sample_movie_urls"), dict):
            d = w.get("sample_movie_urls") or {}
            sm = bool(d.get("size_720_480") or d.get("size_644_414") or d.get("size_560_360") or d.get("size_476_306"))

        out.append(
            {
                "id": wid,
                "title": w.get("title") or "",
                "release_date": w.get("release_date") or "",
                "hero_image": w.get("hero_image") or None,
                "official_url": w.get("official_url") or None,
                "actresses": w.get("actresses") or [],
                "tags": w.get("tags") or [],
                # 追加（今後の絞り込み用）
                "maker": w.get("maker") or None,
                "series": w.get("series") or None,
                # フラグ
                "sample_image_count": si,
                "has_sample_movie": sm,
            }
        )
    return out


def count_top(items: List[str], top_n: int = 30):
    d: Dict[str, int] = {}
    for x in items:
        if not x:
            continue
        d[x] = d.get(x, 0) + 1
    top = sorted(d.items(), key=lambda t: (-t[1], t[0].lower()))[:top_n]
    return d, top


def write_search_index_chunks(
    works_sorted: List[Dict[str, Any]],
    out_assets_dir: Path,
    actresses_keys: List[str],
    genres_keys: List[str],
    makers_keys: List[str],
    series_keys: List[str],
    chunk_size: int = 2000,
) -> None:
    """検索用 index を chunk+manifest で出力。"""
    search_index = make_search_index(works_sorted)

    # 互換用（小～中規模向け）
    write_json(out_assets_dir / "works_index.json", search_index)

    chunks = [search_index[i : i + chunk_size] for i in range(0, len(search_index), chunk_size)]

    chunk_files = []
    for i, ch in enumerate(chunks):
        fname = f"works_index_{i:03d}.json"
        write_json(out_assets_dir / fname, ch)
        chunk_files.append(fname)

    # 人気TOP
    all_tags: List[str] = []
    all_actresses: List[str] = []
    all_makers: List[str] = []
    all_series: List[str] = []
    for w in works_sorted:
        all_tags += (w.get("tags") or [])
        all_actresses += (w.get("actresses") or [])
        if w.get("maker"):
            all_makers.append(w.get("maker"))
        if w.get("series"):
            all_series.append(w.get("series"))

    tag_count, top_tags = count_top(all_tags, top_n=30)
    actress_count, top_actresses = count_top(all_actresses, top_n=30)
    maker_count, top_makers = count_top(all_makers, top_n=30)
    series_count, top_series = count_top(all_series, top_n=30)

    manifest = {
        "total": len(search_index),
        "chunk_size": chunk_size,
        "chunks": chunk_files,
        "all_tags": genres_keys,
        "all_actresses": actresses_keys,
        "top_tags": [{"name": k, "count": v} for k, v in top_tags],
        "top_actresses": [{"name": k, "count": v} for k, v in top_actresses],
        # 追加（将来拡張用）
        "all_makers": makers_keys,
        "all_series": series_keys,
        "top_makers": [{"name": k, "count": v} for k, v in top_makers],
        "top_series": [{"name": k, "count": v} for k, v in top_series],
    }
    write_json(out_assets_dir / "works_index_manifest.json", manifest)


# =============================
# Structured data (JSON-LD)
# =============================

def build_jsonld_for_work(base_url: str, w: Dict[str, Any]) -> Optional[str]:
    if not base_url:
        return None

    wid = w.get("id")
    if not wid:
        return None

    url = abs_url(base_url, f"works/{wid}/")
    title = w.get("title") or ""
    desc = (w.get("description") or title or "")[:1200]

    date_iso = parse_release_date_iso(w.get("release_date") or "")

    graph: List[Dict[str, Any]] = []

    # Breadcrumb
    graph.append(
        {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "ホーム", "item": abs_url(base_url, "")},
                {"@type": "ListItem", "position": 2, "name": "作品一覧", "item": abs_url(base_url, "pages/1/")},
                {"@type": "ListItem", "position": 3, "name": title, "item": url},
            ],
        }
    )

    # Work object（サンプル動画があれば VideoObject に寄せる）
    has_movie = bool(w.get("sample_movie"))
    if (not has_movie) and isinstance(w.get("sample_movie_urls"), dict):
        d = w.get("sample_movie_urls") or {}
        has_movie = bool(d.get("size_720_480") or d.get("size_644_414") or d.get("size_560_360") or d.get("size_476_306"))

    if has_movie:
        obj: Dict[str, Any] = {
            "@type": "VideoObject",
            "name": title,
            "description": desc,
            "url": url,
        }
        if date_iso:
            obj["datePublished"] = date_iso
        if w.get("hero_image"):
            obj["thumbnailUrl"] = w.get("hero_image")

        # duration: volume が int なら minutes とみなす
        vol = w.get("volume")
        if isinstance(vol, int) and vol > 0:
            obj["duration"] = f"PT{vol}M"

        # actor
        acts = w.get("actresses") or []
        if acts:
            obj["actor"] = [{"@type": "Person", "name": a} for a in acts[:20]]

        # genre
        tags = w.get("tags") or []
        if tags:
            obj["genre"] = tags[:30]

        graph.append(obj)
    else:
        obj2: Dict[str, Any] = {
            "@type": "CreativeWork",
            "name": title,
            "description": desc,
            "url": url,
        }
        if date_iso:
            obj2["datePublished"] = date_iso
        if w.get("hero_image"):
            obj2["image"] = w.get("hero_image")
        tags = w.get("tags") or []
        if tags:
            obj2["keywords"] = tags[:30]
        graph.append(obj2)

    root = {"@context": "https://schema.org", "@graph": graph}
    return json.dumps(root, ensure_ascii=False)


# =============================
# Main
# =============================

def main() -> None:
    data = load_json(WORKS_JSON)
    site_name = str(data.get("site_name") or "Review Catalog")
    base_url = guess_site_url(data)

    # reset output
    if OUT.exists():
        shutil.rmtree(OUT)
    ensure_dir(OUT)
    ensure_dir(ASSETS_OUT)

    # copy css (src/assets/style.css -> docs/assets/style.css)
    src_css = SRC / "assets" / "style.css"
    if src_css.exists():
        shutil.copy2(src_css, ASSETS_OUT / "style.css")

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    tpl_index = env.get_template(TPL_INDEX)
    tpl_page = env.get_template(TPL_PAGE)
    tpl_list = env.get_template(TPL_LIST)
    tpl_search = None
    try:
        tpl_search = env.get_template(TPL_SEARCH)
    except Exception:
        tpl_search = None

    works_raw = data.get("works") or []
    works: List[Dict[str, Any]] = [normalize_work(w) for w in works_raw if isinstance(w, dict)]
    works = [w for w in works if w.get("id") and w.get("title")]

    works_sorted = sorted(works, key=lambda x: parse_release_date_sort_key(x.get("release_date", "")), reverse=True)

    actresses, actresses_keys, genres, genres_keys, makers, makers_keys, series, series_keys = build_facets(works_sorted)

    # Search index assets
    write_search_index_chunks(
        works_sorted,
        ASSETS_OUT,
        actresses_keys=actresses_keys,
        genres_keys=genres_keys,
        makers_keys=makers_keys,
        series_keys=series_keys,
        chunk_size=2000,
    )

    # css paths per depth
    CSS_ROOT = "./assets/style.css"
    CSS_1DOWN = "../assets/style.css"
    CSS_2DOWN = "../../assets/style.css"

    # common hrefs (relative)
    def nav_for(depth: int):
        if depth == 0:
            return {
                "home_href": "./",
                "pages_href": "./pages/1/",
                "actresses_href": "./actresses/",
                "genres_href": "./genres/",
                "search_href": "./search/",
                "works_prefix": "./works/",
            }
        if depth == 1:
            return {
                "home_href": "../",
                "pages_href": "../pages/1/",
                "actresses_href": "../actresses/",
                "genres_href": "../genres/",
                "search_href": "../search/",
                "works_prefix": "../works/",
            }
        if depth == 2:
            return {
                "home_href": "../../",
                "pages_href": "../../pages/1/",
                "actresses_href": "../../actresses/",
                "genres_href": "../../genres/",
                "search_href": "../../search/",
                "works_prefix": "../../works/",
            }
        raise ValueError("depth must be 0..2")

    # =============================
    # 1) Home
    # =============================
    PER_PAGE = 24
    total_pages = max(1, math.ceil(len(works_sorted) / PER_PAGE))

    write_text(
        OUT / "index.html",
        tpl_index.render(
            site_name=site_name,
            works=works_sorted[:PER_PAGE],
            css_path=CSS_ROOT,
            page_title=site_name,
            page_description=f"{site_name} の作品一覧です。",
            page=1,
            total_pages=total_pages,
            canonical_url=abs_url(base_url, "") if base_url else "",
            **nav_for(0),
        ),
    )

    # =============================
    # 2) Paging (/pages/<n>/)
    # =============================
    for pnum in range(1, total_pages + 1):
        start = (pnum - 1) * PER_PAGE
        end = start + PER_PAGE
        write_text(
            OUT / "pages" / str(pnum) / "index.html",
            tpl_index.render(
                site_name=site_name,
                works=works_sorted[start:end],
                css_path=CSS_2DOWN,
                page_title=f"作品一覧（{pnum}ページ目）",
                page_description=f"作品一覧の {pnum} ページ目です。",
                page=pnum,
                total_pages=total_pages,
                canonical_url=abs_url(base_url, f"pages/{pnum}/") if base_url else "",
                **nav_for(2),
            ),
        )

    # =============================
    # 3) Work pages (/works/<id>/)
    # =============================
    by_id = {w["id"]: w for w in works_sorted if w.get("id")}

    # related helpers
    def related_by_actress(w: Dict[str, Any], limit: int = 12) -> List[Dict[str, Any]]:
        acts = w.get("actresses") or []
        if not acts:
            return []
        # 1人目を優先（重すぎない）
        a0 = acts[0]
        pool = actresses.get(a0, [])
        out = [x for x in pool if x.get("id") != w.get("id")]
        out = sorted(out, key=lambda x: parse_release_date_sort_key(x.get("release_date", "")), reverse=True)
        return out[:limit]

    def related_by_genre(w: Dict[str, Any], limit: int = 12) -> List[Dict[str, Any]]:
        tags = w.get("tags") or []
        if not tags:
            return []
        # 人気タグは件数が多いので、上位2タグまで
        picked = tags[:2]
        seen = set([w.get("id")])
        out: List[Dict[str, Any]] = []
        for g in picked:
            for x in genres.get(g, []):
                xid = x.get("id")
                if not xid or xid in seen:
                    continue
                seen.add(xid)
                out.append(x)
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
        out = sorted(out, key=lambda x: parse_release_date_sort_key(x.get("release_date", "")), reverse=True)
        return out[:limit]

    for w in works_sorted:
        wid = w.get("id")
        if not wid:
            continue

        # facet links
        w_actress_items = [{"name": a, "href": f"{nav_for(1)['actresses_href']}{slugify_simple(a)}/"} for a in (w.get("actresses") or [])]
        w_tag_items = [{"name": g, "href": f"{nav_for(1)['genres_href']}{slugify_simple(g)}/"} for g in (w.get("tags") or [])]
        w_maker_item = None
        if w.get("maker"):
            w_maker_item = {"name": w.get("maker"), "href": f"{nav_for(1)['home_href']}makers/{slugify_simple(w.get('maker'))}/"}
        w_series_item = None
        if w.get("series"):
            w_series_item = {"name": w.get("series"), "href": f"{nav_for(1)['home_href']}series/{slugify_simple(w.get('series'))}/"}

        schema_jsonld = build_jsonld_for_work(base_url, w)

        write_text(
            OUT / "works" / wid / "index.html",
            tpl_page.render(
                site_name=site_name,
                w=w,
                css_path=CSS_2DOWN,
                canonical_url=abs_url(base_url, f"works/{wid}/") if base_url else "",
                related_works=related_by_actress(w, limit=12),
                related_works_genre=related_by_genre(w, limit=12),
                w_actress_items=w_actress_items,
                w_tag_items=w_tag_items,
                w_maker_item=w_maker_item,
                w_series_item=w_series_item,
                schema_jsonld=schema_jsonld,
                home_href=nav_for(2)["home_href"],
                actresses_href=nav_for(2)["actresses_href"],
                genres_href=nav_for(2)["genres_href"],
                search_href=nav_for(2)["search_href"],
                works_prefix=nav_for(2)["works_prefix"],
            ),
        )

    # =============================
    # 4) Actresses index/detail
    # =============================
    write_text(
        OUT / "actresses" / "index.html",
        tpl_list.render(
            site_name=site_name,
            page_title="女優一覧",
            page_description="女優別の一覧ページです。",
            items=[{"name": a, "href": f"./{slugify_simple(a)}/"} for a in actresses_keys],
            css_path=CSS_1DOWN,
            canonical_url=abs_url(base_url, "actresses/") if base_url else "",
            **nav_for(1),
        ),
    )

    for a in actresses_keys:
        ws_sorted = sorted(actresses.get(a, []), key=lambda x: parse_release_date_sort_key(x.get("release_date", "")), reverse=True)
        write_text(
            OUT / "actresses" / slugify_simple(a) / "index.html",
            tpl_index.render(
                site_name=site_name,
                works=ws_sorted[:PER_PAGE],
                css_path=CSS_2DOWN,
                page_title=f"女優：{a}",
                page_description=f"{a} の作品一覧です。",
                page=1,
                total_pages=1,
                canonical_url=abs_url(base_url, f"actresses/{slugify_simple(a)}/") if base_url else "",
                **nav_for(2),
            ),
        )

    # =============================
    # 5) Genres index/detail
    # =============================
    write_text(
        OUT / "genres" / "index.html",
        tpl_list.render(
            site_name=site_name,
            page_title="ジャンル一覧",
            page_description="タグ（ジャンル）別の一覧ページです。",
            items=[{"name": g, "href": f"./{slugify_simple(g)}/"} for g in genres_keys],
            css_path=CSS_1DOWN,
            canonical_url=abs_url(base_url, "genres/") if base_url else "",
            **nav_for(1),
        ),
    )

    for g in genres_keys:
        ws_sorted = sorted(genres.get(g, []), key=lambda x: parse_release_date_sort_key(x.get("release_date", "")), reverse=True)
        write_text(
            OUT / "genres" / slugify_simple(g) / "index.html",
            tpl_index.render(
                site_name=site_name,
                works=ws_sorted[:PER_PAGE],
                css_path=CSS_2DOWN,
                page_title=f"ジャンル：{g}",
                page_description=f"タグ「{g}」の作品一覧です。",
                page=1,
                total_pages=1,
                canonical_url=abs_url(base_url, f"genres/{slugify_simple(g)}/") if base_url else "",
                **nav_for(2),
            ),
        )

    # =============================
    # 6) Makers index/detail (NEW)
    # =============================
    if makers_keys:
        write_text(
            OUT / "makers" / "index.html",
            tpl_list.render(
                site_name=site_name,
                page_title="メーカー一覧",
                page_description="メーカー別の一覧ページです。",
                items=[{"name": m, "href": f"./{slugify_simple(m)}/"} for m in makers_keys],
                css_path=CSS_1DOWN,
                canonical_url=abs_url(base_url, "makers/") if base_url else "",
                **nav_for(1),
            ),
        )

        for m in makers_keys:
            ws_sorted = sorted(makers.get(m, []), key=lambda x: parse_release_date_sort_key(x.get("release_date", "")), reverse=True)
            write_text(
                OUT / "makers" / slugify_simple(m) / "index.html",
                tpl_index.render(
                    site_name=site_name,
                    works=ws_sorted[:PER_PAGE],
                    css_path=CSS_2DOWN,
                    page_title=f"メーカー：{m}",
                    page_description=f"メーカー「{m}」の作品一覧です。",
                    page=1,
                    total_pages=1,
                    canonical_url=abs_url(base_url, f"makers/{slugify_simple(m)}/") if base_url else "",
                    **nav_for(2),
                ),
            )

    # =============================
    # 7) Series index/detail (NEW)
    # =============================
    if series_keys:
        write_text(
            OUT / "series" / "index.html",
            tpl_list.render(
                site_name=site_name,
                page_title="シリーズ一覧",
                page_description="シリーズ別の一覧ページです。",
                items=[{"name": s, "href": f"./{slugify_simple(s)}/"} for s in series_keys],
                css_path=CSS_1DOWN,
                canonical_url=abs_url(base_url, "series/") if base_url else "",
                **nav_for(1),
            ),
        )

        for s in series_keys:
            ws_sorted = sorted(series.get(s, []), key=lambda x: parse_release_date_sort_key(x.get("release_date", "")), reverse=True)
            write_text(
                OUT / "series" / slugify_simple(s) / "index.html",
                tpl_index.render(
                    site_name=site_name,
                    works=ws_sorted[:PER_PAGE],
                    css_path=CSS_2DOWN,
                    page_title=f"シリーズ：{s}",
                    page_description=f"シリーズ「{s}」の作品一覧です。",
                    page=1,
                    total_pages=1,
                    canonical_url=abs_url(base_url, f"series/{slugify_simple(s)}/") if base_url else "",
                    **nav_for(2),
                ),
            )

    # =============================
    # 8) Search page
    # =============================
    if tpl_search is not None:
        write_text(
            OUT / "search" / "index.html",
            tpl_search.render(
                site_name=site_name,
                css_path=CSS_1DOWN,
                canonical_url=abs_url(base_url, "search/") if base_url else "",
                **nav_for(1),
            ),
        )

    # =============================
    # 9) SEO files
    # =============================
    today = datetime.date.today().isoformat()

    rel_urls: List[str] = [""]
    for pnum in range(1, total_pages + 1):
        rel_urls.append(f"pages/{pnum}/")
    for w in works_sorted:
        wid = w.get("id")
        if wid:
            rel_urls.append(f"works/{wid}/")
    rel_urls.append("actresses/")
    for a in actresses_keys:
        rel_urls.append(f"actresses/{slugify_simple(a)}/")
    rel_urls.append("genres/")
    for g in genres_keys:
        rel_urls.append(f"genres/{slugify_simple(g)}/")
    if makers_keys:
        rel_urls.append("makers/")
        for m in makers_keys:
            rel_urls.append(f"makers/{slugify_simple(m)}/")
    if series_keys:
        rel_urls.append("series/")
        for s in series_keys:
            rel_urls.append(f"series/{slugify_simple(s)}/")
    rel_urls.append("search/")

    write_robots_txt(OUT, base_url)
    write_sitemap_xml(OUT, base_url, rel_urls, today)
    write_rss_feed(OUT, base_url, site_name, works_sorted, max_items=50)

    print("生成完了：docs/ に出力しました")


if __name__ == "__main__":
    main()
