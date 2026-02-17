from __future__ import annotations

import json
import os
import datetime
from xml.sax.saxutils import escape as xml_escape
import math
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple

from jinja2 import Environment, FileSystemLoader, select_autoescape


# =============================
# Paths
# =============================
ROOT = Path(__file__).resolve().parents[1]      # repo root (catalog-main/)
SRC = ROOT / "src"
TEMPLATES = SRC / "templates"
DATA_DIR = SRC / "data"
OUT = ROOT / "docs"
ASSETS_OUT = OUT / "assets"

WORKS_JSON = DATA_DIR / "works.json"


# =============================
# Template names
# =============================
TPL_INDEX = "index.html"        # works grid page (home / pages / actress detail / genre detail)
TPL_PAGE = "page.html"          # work detail page
TPL_LIST = "list_works.html"    # simple list page (actresses index / genres index)
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
        # user/organization pages の場合（repo名が owner.github.io ）
        if name.lower() == f"{owner.lower()}.github.io":
            return f"https://{owner}.github.io/"
        return f"https://{owner}.github.io/{name}/"

    # ローカル実行では空（sitemap/OGPは相対にならないように後段でガード）
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
    # Sitemap の loc は本来 absolute 推奨なので、base_url が無い場合は生成しない
    if not base_url:
        return
    urlset = []
    for rel in rel_urls:
        loc = abs_url(base_url, rel)
        # ディレクトリURL（末尾/）に統一
        if not loc.endswith("/") and not loc.endswith(".html"):
            loc += "/"
        urlset.append(
            f"  <url><loc>{xml_escape(loc)}</loc><lastmod>{lastmod_date}</lastmod></url>"
        )
    xml = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n" + \
          "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n" + \
          "\n".join(urlset) + "\n</urlset>\n"
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

def safe_template(env: Environment, name: str, fallback: str | None = None):
    try:
        return env.get_template(name)
    except Exception:
        if fallback:
            return env.get_template(fallback)
        raise


def slugify_simple(s: str) -> str:
    s = (s or "").strip()
    for ch in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']:
        s = s.replace(ch, "_")
    s = s.replace(" ", "_")
    return s or "unknown"


def parse_release_date_sort_key(s: str) -> str:
    # 文字列のままでも ISO/同様形式ならソートできる
    # "2026-03-05 00:00:00" / "2012/8/3 10:00" など混在しても、最低限の比較キーとして保持
    return (s or "").replace("/", "-").replace("  ", " ").strip()


def normalize_work(w: Dict[str, Any]) -> Dict[str, Any]:
    # 欠けてても壊れないように最低限補正
    ww = dict(w or {})
    ww["id"] = str(ww.get("id") or "").strip()
    ww["title"] = str(ww.get("title") or "").strip()
    ww["description"] = str(ww.get("description") or ww["title"] or "").strip()
    ww["release_date"] = str(ww.get("release_date") or "").strip()
    ww["hero_image"] = ww.get("hero_image") or None

    # URL系の揺れを吸収（テンプレ側が w.official_url を見てもOK）
    ww["official_url"] = ww.get("official_url") or ww.get("affiliate_url") or ww.get("affiliateURL") or ww.get("URL") or None

    # 配列系
    ww["tags"] = [str(x).strip() for x in (ww.get("tags") or []) if str(x).strip()]
    ww["actresses"] = [str(x).strip() for x in (ww.get("actresses") or []) if str(x).strip()]

    # サンプル（無い場合は空に）
    ww["sample_images"] = [str(x).strip() for x in (ww.get("sample_images") or []) if str(x).strip()]
    ww["sample_movie"] = str(ww.get("sample_movie") or ww.get("sample_movie_url") or "").strip() or None
    # sample_movie_urls は dict のまま保持（テンプレで拾えるように）
    if not isinstance(ww.get("sample_movie_urls"), dict):
        ww["sample_movie_urls"] = {}
    # review / prices も dict で保持
    if not isinstance(ww.get("review"), dict):
        ww["review"] = {}
    if not isinstance(ww.get("prices"), dict):
        ww["prices"] = {}
    return ww


def build_indexes_from_works(
    works: List[Dict[str, Any]]
) -> Tuple[Dict[str, List[Dict[str, Any]]], List[str], Dict[str, List[Dict[str, Any]]], List[str]]:
    actresses: Dict[str, List[Dict[str, Any]]] = {}
    genres: Dict[str, List[Dict[str, Any]]] = {}

    for w in works:
        for a in (w.get("actresses") or []):
            actresses.setdefault(a, []).append(w)

        for g in (w.get("tags") or []):
            genres.setdefault(g, []).append(w)

    actresses_keys = sorted(actresses.keys(), key=lambda s: s.lower())
    genres_keys = sorted(genres.keys(), key=lambda s: s.lower())
    return actresses, actresses_keys, genres, genres_keys


# =============================
# Search index (lightweight)
# =============================
def make_search_index(works_sorted: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    検索ページ用の軽量インデックス（大量でもDOMを増やさない）
    """
    out: List[Dict[str, Any]] = []
    for w in works_sorted:
        wid = w.get("id")
        if not wid:
            continue
        out.append(
            {
                "id": wid,
                "title": w.get("title") or "",
                "release_date": w.get("release_date") or "",
                "hero_image": w.get("hero_image") or None,
                "official_url": w.get("official_url") or None,
                "actresses": w.get("actresses") or [],
                "tags": w.get("tags") or [],
            }
        )
    return out


def count_top_tags_and_actresses(works: List[Dict[str, Any]], top_n: int = 30):
    tag_count: Dict[str, int] = {}
    actress_count: Dict[str, int] = {}

    for w in works:
        for t in (w.get("tags") or []):
            if t:
                tag_count[t] = tag_count.get(t, 0) + 1
        for a in (w.get("actresses") or []):
            if a:
                actress_count[a] = actress_count.get(a, 0) + 1

    top_tags = sorted(tag_count.items(), key=lambda x: (-x[1], x[0].lower()))[:top_n]
    top_actresses = sorted(actress_count.items(), key=lambda x: (-x[1], x[0].lower()))[:top_n]

    return tag_count, actress_count, top_tags, top_actresses


def write_search_index_chunks(
    works_sorted: List[Dict[str, Any]],
    out_assets_dir: Path,
    chunk_size: int = 2000,
) -> None:
    """
    - works_index_manifest.json … 目次（タグ/女優一覧・人気TOP・チャンク一覧）
    - works_index_000.json, works_index_001.json ... … 分割データ
    - works_index.json …（互換用）全件1ファイル
    """
    search_index = make_search_index(works_sorted)

    # 互換用（小～中規模ならこれだけでもOK）
    write_json(out_assets_dir / "works_index.json", search_index)

    # 分割
    chunks = [search_index[i : i + chunk_size] for i in range(0, len(search_index), chunk_size)]
    chunk_files: List[str] = []
    for i, ch in enumerate(chunks):
        name = f"works_index_{i:03d}.json"
        write_json(out_assets_dir / name, ch)
        chunk_files.append(name)

    tag_count, actress_count, top_tags, top_actresses = count_top_tags_and_actresses(works_sorted, top_n=40)

    manifest = {
        "version": 1,
        "total_items": len(search_index),
        "chunk_size": chunk_size,
        "chunk_files": chunk_files,
        "all_tags": sorted(tag_count.keys(), key=lambda s: s.lower()),
        "all_actresses": sorted(actress_count.keys(), key=lambda s: s.lower()),
        "top_tags": [{"name": k, "count": v} for k, v in top_tags],
        "top_actresses": [{"name": k, "count": v} for k, v in top_actresses],
    }
    write_json(out_assets_dir / "works_index_manifest.json", manifest)


# =============================
# Assets
# =============================
def copy_assets() -> None:
    """
    ある場合だけコピー：
    - src/assets/style.css -> docs/assets/style.css
    """
    ensure_dir(ASSETS_OUT)
    src_assets = SRC / "assets"
    css_src = src_assets / "style.css"
    if css_src.exists():
        shutil.copyfile(css_src, ASSETS_OUT / "style.css")


# =============================
# Main build
# =============================
def main() -> None:
    data = load_json(WORKS_JSON)
    site_name = data.get("site_name", "Catalog")
    base_url = guess_site_url(data)
    works_raw: List[Dict[str, Any]] = data.get("works", []) or []

    works = [normalize_work(w) for w in works_raw if isinstance(w, dict)]
    works = [w for w in works if w.get("id") and w.get("title")]

    # 新しい順
    works_sorted = sorted(works, key=lambda x: parse_release_date_sort_key(x.get("release_date", "")), reverse=True)

    # 出力先を用意
    ensure_dir(OUT)
    ensure_dir(OUT / "works")
    ensure_dir(OUT / "actresses")
    ensure_dir(OUT / "genres")
    ensure_dir(OUT / "pages")
    ensure_dir(OUT / "search")
    ensure_dir(ASSETS_OUT)

    # CSSなどコピー
    copy_assets()

    # テンプレ env
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    tpl_index = safe_template(env, TPL_INDEX)
    tpl_page = safe_template(env, TPL_PAGE)
    tpl_list = safe_template(env, TPL_LIST, fallback=TPL_INDEX)

    # search.html は無い場合もあるので、無ければ list/indexで代用しない（検索は必須にする）
    try:
        tpl_search = env.get_template(TPL_SEARCH)
    except Exception:
        tpl_search = None

    # インデックス作成
    actresses, actresses_keys, genres, genres_keys = build_indexes_from_works(works_sorted)

    # =============================
    # CSS relative paths
    # =============================
    CSS_ROOT = "assets/style.css"
    CSS_1DOWN = "../assets/style.css"
    CSS_2DOWN = "../../assets/style.css"
    CSS_3DOWN = "../../../assets/style.css"

    # =============================
    # A) 検索用インデックス（分割 + manifest）
    # =============================
    write_search_index_chunks(
        works_sorted=works_sorted,
        out_assets_dir=ASSETS_OUT,
        chunk_size=2000,  # 重ければ 1000 に
    )

    # =============================
    # 1) トップページ（1ページ目を表示）
    # =============================
    PER_PAGE = 24
    total_pages = max(1, math.ceil(len(works_sorted) / PER_PAGE))
    page1 = works_sorted[:PER_PAGE]

    write_text(
        OUT / "index.html",
        tpl_index.render(
            site_name=site_name,
            works=page1,
            css_path=CSS_ROOT,
            home_href="./",
            pages_href="pages/1/",
            actresses_href="actresses/",
            genres_href="genres/",
            search_href="search/",
            works_prefix="works/",
            page_title=None,
            page_description=None,
            page=1,
            total_pages=total_pages,
            canonical_url=abs_url(base_url, ''),
        ),
    )

    # =============================
    # 2) ページング（/pages/<n>/）
    # =============================
    for p in range(1, total_pages + 1):
        start = (p - 1) * PER_PAGE
        end = start + PER_PAGE
        page_works = works_sorted[start:end]

        write_text(
            OUT / "pages" / str(p) / "index.html",
            tpl_index.render(
                site_name=site_name,
                works=page_works,
                css_path=CSS_2DOWN,
                home_href="../../",
                pages_href="../1/",
                actresses_href="../../actresses/",
                genres_href="../../genres/",
                search_href="../../search/",
                works_prefix="../../works/",
                page_title=f"全作品（{p}/{total_pages}）",
                page_description="全作品のページ一覧です。",
                page=p,
                total_pages=total_pages,
                canonical_url=abs_url(base_url, f"pages/{p}/"),
            ),
        )

    # =============================
    # 3) 作品個別ページ（関連作品）
    # =============================
    works_by_id: Dict[str, Dict[str, Any]] = {w["id"]: w for w in works_sorted if w.get("id")}

    actress_to_ids: Dict[str, List[str]] = {}
    for w in works_sorted:
        wid = w.get("id")
        if not wid:
            continue
        for a in (w.get("actresses") or []):
            actress_to_ids.setdefault(a, []).append(wid)

    def get_related_works(current_work: Dict[str, Any], limit: int = 12) -> List[Dict[str, Any]]:
        cur_id = current_work.get("id")
        cur_actresses = current_work.get("actresses") or []
        if not cur_actresses:
            return []
        related_ids: List[str] = []
        for a in cur_actresses:
            for wid in actress_to_ids.get(a, []):
                if wid == cur_id:
                    continue
                if wid not in related_ids:
                    related_ids.append(wid)

        related = [works_by_id[wid] for wid in related_ids if wid in works_by_id]
        related.sort(key=lambda x: parse_release_date_sort_key(x.get("release_date", "")), reverse=True)
        return related[:limit]

    for w in works_sorted:
        wid = w.get("id")
        if not wid:
            continue

        related_works = get_related_works(w, limit=12)

        write_text(
            OUT / "works" / wid / "index.html",
            tpl_page.render(
                site_name=site_name,
                w=w,
                related_works=related_works,
                css_path=CSS_2DOWN,
                home_href="../../",
                pages_href="../../pages/1/",
                actresses_href="../../actresses/",
                genres_href="../../genres/",
                search_href="../../search/",
                works_prefix="../../works/",
                canonical_url=abs_url(base_url, f"works/{wid}/"),
                w_tag_items=[{"name": t, "href": f"../../genres/{slugify_simple(t)}/"} for t in (w.get("tags") or [])],
                w_actress_items=[{"name": a, "href": f"../../actresses/{slugify_simple(a)}/"} for a in (w.get("actresses") or [])],
            ),
        )

    # =============================
    # 4) 女優一覧ページ（/actresses/）
    # =============================
    write_text(
        OUT / "actresses" / "index.html",
        tpl_list.render(
            site_name=site_name,
            page_title="女優一覧",
            page_description="女優別の一覧ページです。",
            items=[{"name": a, "href": f"./{slugify_simple(a)}/"} for a in actresses_keys],
            css_path=CSS_1DOWN,
            home_href="../",
            pages_href="../pages/1/",
            actresses_href="./",
            genres_href="../genres/",
            search_href="../search/",
            works_prefix="../works/",
            canonical_url=abs_url(base_url, 'actresses/'),
        ),
    )

    # =============================
    # 5) 女優個別ページ（/actresses/<name>/）
    # =============================
    for a in actresses_keys:
        ws = actresses.get(a, [])
        # 女優ページは「最新順」に揃える
        ws_sorted = sorted(ws, key=lambda x: parse_release_date_sort_key(x.get("release_date", "")), reverse=True)

        write_text(
            OUT / "actresses" / slugify_simple(a) / "index.html",
            tpl_index.render(
                site_name=site_name,
                works=ws_sorted[:PER_PAGE],
                css_path=CSS_2DOWN,
                home_href="../../",
                pages_href="../../pages/1/",
                actresses_href="../",
                genres_href="../../genres/",
                search_href="../../search/",
                works_prefix="../../works/",
                page_title=f"女優：{a}",
                page_description=f"{a} の作品一覧です。",
                page=1,
                total_pages=1,
                canonical_url=abs_url(base_url, f"actresses/{slugify_simple(a)}/"),
            ),
        )

    # =============================
    # 6) ジャンル一覧ページ（/genres/）
    # =============================
    write_text(
        OUT / "genres" / "index.html",
        tpl_list.render(
            site_name=site_name,
            page_title="ジャンル一覧",
            page_description="タグ（ジャンル）別の一覧ページです。",
            items=[{"name": g, "href": f"./{slugify_simple(g)}/"} for g in genres_keys],
            css_path=CSS_1DOWN,
            home_href="../",
            pages_href="../pages/1/",
            actresses_href="../actresses/",
            genres_href="./",
            search_href="../search/",
            works_prefix="../works/",
            canonical_url=abs_url(base_url, 'actresses/'),
        ),
    )

    # =============================
    # 7) ジャンル個別ページ（/genres/<tag>/）
    # =============================
    for g in genres_keys:
        ws = genres.get(g, [])
        ws_sorted = sorted(ws, key=lambda x: parse_release_date_sort_key(x.get("release_date", "")), reverse=True)

        write_text(
            OUT / "genres" / slugify_simple(g) / "index.html",
            tpl_index.render(
                site_name=site_name,
                works=ws_sorted[:PER_PAGE],
                css_path=CSS_2DOWN,
                home_href="../../",
                pages_href="../../pages/1/",
                actresses_href="../../actresses/",
                genres_href="../",
                search_href="../../search/",
                works_prefix="../../works/",
                page_title=f"ジャンル：{g}",
                page_description=f"タグ「{g}」の作品一覧です。",
                page=1,
                total_pages=1,
                canonical_url=abs_url(base_url, f"actresses/{slugify_simple(a)}/"),
            ),
        )

    # =============================
    # 8) 検索ページ（/search/）
    # =============================
    if tpl_search is not None:
        write_text(
            OUT / "search" / "index.html",
            tpl_search.render(
                site_name=site_name,
                css_path=CSS_1DOWN,
                home_href="../",
                pages_href="../pages/1/",
                actresses_href="../actresses/",
                genres_href="../genres/",
                search_href="./",
                works_prefix="../works/",
                # 互換用：旧式（単一JSON）でも動く
                data_url="../assets/works_index.json",
                canonical_url=abs_url(base_url, 'search/'),
            ),
        )
    else:
        # テンプレが無い場合の最低限
        write_text(
            OUT / "search" / "index.html",
            f"""<!doctype html><html lang="ja"><meta charset="utf-8"><title>{site_name} - search</title>
<body><p>src/templates/search.html が見つかりません。</p></body></html>""",
        )


    # =============================
    # 9) SEO: robots.txt / sitemap.xml / RSS feed
    # =============================
    today = datetime.date.today().isoformat()
    rel_urls: List[str] = []
    rel_urls.append("")  # home
    for p in range(1, total_pages + 1):
        rel_urls.append(f"pages/{p}/")
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

    write_robots_txt(OUT, base_url)
    write_sitemap_xml(OUT, base_url, rel_urls, today)
    write_rss_feed(OUT, base_url, site_name, works_sorted, max_items=50)

    print("生成完了：docs/ に出力しました")
    print(f" - docs/index.html")
    print(f" - docs/pages/<n>/index.html")
    print(f" - docs/works/<id>/index.html")
    print(f" - docs/actresses/index.html / docs/actresses/<name>/index.html")
    print(f" - docs/genres/index.html / docs/genres/<tag>/index.html")
    print(f" - docs/search/index.html")
    print(f" - docs/assets/works_index_manifest.json (+ chunks)")


if __name__ == "__main__":
    main()
