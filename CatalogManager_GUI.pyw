# -*- coding: utf-8 -*-
"""Catalog Manager GUI (Tkinter)

ダブルクリックで起動できる、簡易の管理UIです。

できること
- 現在の作品数 / 画像あり / 動画あり / works.json更新日時 の表示
- 更新モード（維持更新OFF / 追加更新ON）と、最大件数（テスト）設定
- GitHub Actions の自動更新時刻（workflow cron）の表示・変更
- fetch / build / fetch→build / sanitize / 件数削除（今のworks.json）

補足
- 環境変数が無い場合は「APIキー（ローカル）」に保存して使えます
  ※ .catalog_secrets.json は commit しないでください
"""

from __future__ import annotations

import json
import locale
import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List

import tkinter as tk
from tkinter import ttk, messagebox

JST = timezone(timedelta(hours=9))
SECRETS_FILE = ".catalog_secrets.json"  # ローカル専用（commitしない）


# -------------------- file helpers --------------------
def repo_root(start: Optional[Path] = None) -> Path:
    """Locate repo root (folder containing src/)."""
    here = (start or Path.cwd()).resolve()
    for p in [here] + list(here.parents):
        if (p / "src").is_dir():
            return p
        if p.name == "src" and (p.parent / "src").is_dir():
            return p.parent
    # fallback to script location
    p = Path(__file__).resolve().parent
    if (p / "src").is_dir():
        return p
    raise RuntimeError("Could not find repo root (folder containing 'src').")


# src/ を import 可能にして、works_store を利用（chunk分割データ対応）
try:
    _ROOT_FOR_IMPORT = repo_root(Path(__file__).resolve().parent)
    _SRC_FOR_IMPORT = _ROOT_FOR_IMPORT / 'src'
    if _SRC_FOR_IMPORT.is_dir() and str(_SRC_FOR_IMPORT) not in sys.path:
        sys.path.insert(0, str(_SRC_FOR_IMPORT))
    from works_store import load_bundle, save_bundle, paths as works_paths
except Exception:
    load_bundle = None  # type: ignore
    save_bundle = None  # type: ignore
    works_paths = None  # type: ignore


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_default_config(cfg_path: Path, root: Path) -> Dict[str, Any]:
    """catalog_config.json が無ければ作成。workflow_path は自動検出して入れる。"""
    if cfg_path.exists():
        cfg = load_json(cfg_path)
    else:
        cfg = {
            "site_name": "Review Catalog",
            "update": {
                "enabled": True,
                "jst_time": "03:00",
                "workflow_path": ".github/workflows/auto_update.yml",
            },
            "fetch": {
                "add_new_works": False,
                "trim_enable": False,
                "trim_to": 300,
            },
        }

    # workflow 自動検出
    wf_dir = root / ".github" / "workflows"
    candidates = sorted(list(wf_dir.glob("*.yml")) + list(wf_dir.glob("*.yaml")))
    if candidates:
        rels = [str(p.relative_to(root)).replace("\\", "/") for p in candidates]
        wf_rel = str(((cfg.get("update") or {}).get("workflow_path") or "")).strip() or rels[0]
        if wf_rel not in rels:
            pick = None
            for key in ["auto_update", "update", "auto", "pages"]:
                for r in rels:
                    if key in r:
                        pick = r
                        break
                if pick:
                    break
            cfg.setdefault("update", {})["workflow_path"] = pick or rels[0]
        else:
            cfg.setdefault("update", {})["workflow_path"] = wf_rel

    save_json(cfg_path, cfg)
    return cfg


def load_secrets(root: Path) -> Dict[str, str]:
    p = root / SECRETS_FILE
    if not p.exists():
        return {}
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
        out: Dict[str, str] = {}
        for k in ["DMM_API_ID", "DMM_AFFILIATE_ID"]:
            v = j.get(k)
            if isinstance(v, str) and v.strip():
                out[k] = v.strip().strip('"')
        return out
    except Exception:
        return {}


def save_secrets(root: Path, api_id: str, affiliate_id: str) -> None:
    p = root / SECRETS_FILE
    data = {
        "DMM_API_ID": api_id.strip().strip('"'),
        "DMM_AFFILIATE_ID": affiliate_id.strip().strip('"'),
        "_note": "local only. DO NOT COMMIT THIS FILE.",
        "_saved_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S %z"),
    }
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# -------------------- read/update project state --------------------
def read_works_stats(data_dir: Path, legacy_works_path: Path) -> Tuple[int, int, int, str]:
    """Return (count, with_imgs, with_mov, modified_time_str).

    優先: works_manifest.json（chunk分割）
    互換: works.json
    """
    # chunk形式
    if works_paths is not None:
        manifest_path, _, _legacy = works_paths(data_dir)
        if manifest_path.exists():
            try:
                mf = json.loads(manifest_path.read_text(encoding='utf-8'))
                if isinstance(mf, dict):
                    count = int(mf.get('count') or 0)
                    with_imgs = int(mf.get('with_sample_images') or 0)
                    with_mov = int(mf.get('with_sample_movies') or 0)
                    mtime = manifest_path.stat().st_mtime
                    mtime_s = datetime.fromtimestamp(mtime, tz=JST).strftime('%Y-%m-%d %H:%M:%S')
                    return count, with_imgs, with_mov, mtime_s
            except Exception:
                pass

    # legacy works.json
    works_path = legacy_works_path
    if not works_path.exists():
        return 0, 0, 0, '(no works data)'

    j = json.loads(works_path.read_text(encoding='utf-8'))
    works = j.get('works') or []
    if not isinstance(works, list):
        return 0, 0, 0, '(invalid works.json)'

    def has_imgs(w: Dict[str, Any]) -> bool:
        for k in ['sample_images_large', 'sample_images_small', 'sample_images']:
            v = w.get(k)
            if isinstance(v, list) and any(isinstance(x, str) and x.strip() for x in v):
                return True
        return False

    def has_mov(w: Dict[str, Any]) -> bool:
        return bool(w.get('sample_movie'))

    with_imgs = sum(1 for w in works if isinstance(w, dict) and has_imgs(w))
    with_mov = sum(1 for w in works if isinstance(w, dict) and has_mov(w))

    mtime = works_path.stat().st_mtime
    mtime_s = datetime.fromtimestamp(mtime, tz=JST).strftime('%Y-%m-%d %H:%M:%S')
    return len(works), with_imgs, with_mov, mtime_s


def read_fetch_toggles(fetch_path: Path) -> Tuple[Optional[bool], Optional[bool], Optional[int]]:
    if not fetch_path.exists():
        return None, None, None
    text = fetch_path.read_text(encoding="utf-8")

    def mbool(name: str) -> Optional[bool]:
        m = re.search(rf"^\s*{re.escape(name)}\s*=\s*(True|False)\b", text, re.M)
        if not m:
            return None
        return m.group(1) == "True"

    def mint(name: str) -> Optional[int]:
        m = re.search(rf"^\s*{re.escape(name)}\s*=\s*(\d+)\b", text, re.M)
        if not m:
            return None
        return int(m.group(1))

    return mbool("ADD_NEW_WORKS"), mbool("TRIM_ENABLE"), mint("TRIM_TO")


def apply_fetch_toggles(fetch_path: Path, add_new: bool, trim_enable: bool, trim_to: int) -> None:
    if not fetch_path.exists():
        raise FileNotFoundError(f"fetch script not found: {fetch_path}")

    text = fetch_path.read_text(encoding="utf-8")

    def sub_bool(name: str, val: bool) -> None:
        nonlocal text
        new = "True" if val else "False"
        pat = rf"^(\s*{re.escape(name)}\s*=\s*)(True|False)(\b.*)$"
        if re.search(pat, text, re.M):
            text = re.sub(pat, rf"\g<1>{new}\g<3>", text, flags=re.M)
        else:
            text = f"\n{name} = {new}\n" + text

    def sub_int(name: str, val: int) -> None:
        nonlocal text
        pat = rf"^(\s*{re.escape(name)}\s*=\s*)(\d+)(\b.*)$"
        if re.search(pat, text, re.M):
            text = re.sub(pat, rf"\g<1>{int(val)}\g<3>", text, flags=re.M)
        else:
            text = f"\n{name} = {int(val)}\n" + text

    sub_bool("ADD_NEW_WORKS", add_new)
    sub_bool("TRIM_ENABLE", trim_enable)
    sub_int("TRIM_TO", int(trim_to))
    fetch_path.write_text(text, encoding="utf-8")


def parse_cron_from_workflow(yml_text: str) -> Optional[str]:
    m = re.search(r"\bcron\s*:\s*['\"]([^'\"]+)['\"]", yml_text)
    return m.group(1).strip() if m else None


def cron_to_jst_time(cron: str) -> Optional[str]:
    parts = cron.split()
    if len(parts) < 2:
        return None
    try:
        minute = int(parts[0])
        hour_utc = int(parts[1])
    except ValueError:
        return None
    hour_jst = (hour_utc + 9) % 24
    return f"{hour_jst:02d}:{minute:02d}"


def jst_time_to_cron(jst_time: str) -> str:
    m = re.match(r"^(\d{1,2}):(\d{2})$", jst_time.strip())
    if not m:
        raise ValueError("time must be HH:MM")
    hh = int(m.group(1))
    mm = int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("time must be valid HH:MM")
    hour_utc = (hh - 9) % 24
    return f"{mm} {hour_utc} * * *"


def apply_cron_to_workflow(workflow_path: Path, cron: str) -> bool:
    if not workflow_path.exists():
        return False
    text = workflow_path.read_text(encoding="utf-8")
    if re.search(r"\bcron\s*:\s*['\"][^'\"]+['\"]", text):
        text2 = re.sub(r"(\bcron\s*:\s*)['\"][^'\"]+['\"]", rf"\g<1>'{cron}'", text)
        workflow_path.write_text(text2, encoding="utf-8")
        return True
    return False


def trim_works_data(data_dir: Path, legacy_works_path: Path, n: int) -> int:
    """今あるデータを n 件に切り詰めて保存。

    - chunk形式があれば chunk形式で保存
    - 無ければ legacy works.json を切り詰め
    """
    n = max(0, int(n))

    # chunk形式
    if load_bundle is not None and save_bundle is not None:
        meta, works = load_bundle(data_dir)
        if works:
            kept = works[:n]
            chunk_size = int(meta.get('chunk_size') or 500) if isinstance(meta, dict) else 500
            save_bundle(data_dir, meta if isinstance(meta, dict) else {}, kept, chunk_size=chunk_size, cleanup_legacy=True)
            return len(kept)

    # legacy works.json
    works_path = legacy_works_path
    if not works_path.exists():
        return 0
    j = json.loads(works_path.read_text(encoding='utf-8'))
    works = j.get('works') or []
    if not isinstance(works, list):
        return 0

    def key(w: Dict[str, Any]) -> str:
        for k in ['release_date', 'date']:
            v = w.get(k)
            if isinstance(v, str):
                return v
        return ''

    works_sorted = sorted([w for w in works if isinstance(w, dict)], key=key, reverse=True)
    kept = works_sorted[:n]
    j['works'] = kept
    j['_trimmed_at'] = datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S %z')
    save_json(works_path, j)
    return len(kept)


# -------------------- GUI --------------------
class App(ttk.Frame):
    def __init__(self, master: tk.Tk):
        super().__init__(master)
        self.master = master
        self.root = repo_root(Path(__file__).resolve().parent)

        self.cfg_path = self.root / "catalog_config.json"
        self.data_dir = self.root / "src" / "data"
        self.works_manifest_path = self.data_dir / "works_manifest.json"
        self.works_chunks_dir = self.data_dir / "works_chunks"
        self.legacy_works_path = self.data_dir / "works.json"
        self.fetch_path = self.root / "src" / "fetch_to_works_fanza.py"
        self.sanitize_path = self.root / "src" / "sanitize_noimage_samples.py"
        self.build_path = self.root / "src" / "build.py"

        self.cfg = ensure_default_config(self.cfg_path, self.root)
        self.secrets = load_secrets(self.root)

        # ----- vars -----
        # mode: OFF=update(維持) / ON=full(追加)
        self.var_mode = tk.StringVar(value="update")  # update/full
        self.var_trim_enable = tk.BooleanVar(value=False)
        self.var_trim_to = tk.StringVar(value="300")
        self.var_time = tk.StringVar(value="03:00")
        self.var_apply_workflow = tk.BooleanVar(value=True)
        self.var_workflow_path = tk.StringVar(value=self.cfg.get("update", {}).get("workflow_path", ""))

        self.var_api_id = tk.StringVar(value=self.secrets.get("DMM_API_ID", ""))
        self.var_aff_id = tk.StringVar(value=self.secrets.get("DMM_AFFILIATE_ID", ""))
        self.var_show_keys = tk.BooleanVar(value=False)

        # footer
        self.var_auto_apply = tk.BooleanVar(value=True)
        self.status_auto = tk.StringVar(value="")

        # status vars
        self.status_works = tk.StringVar(value="-")
        self.status_imgs = tk.StringVar(value="-")
        self.status_mov = tk.StringVar(value="-")
        self.status_mtime = tk.StringVar(value="-")
        self.status_keys = tk.StringVar(value="-")
        self.status_mode = tk.StringVar(value="-")
        self.status_actions_time = tk.StringVar(value="-")
        self.status_actions_detail = tk.StringVar(value="-")

        # internal
        self._auto_job: Optional[str] = None
        self._is_running = False

        self._build_ui()
        self._bind_auto_apply()
        self.reload_all(show_toast=False)

    # ---------- UI ----------
    def _build_ui(self) -> None:
        self.master.title("Catalog Manager")
        self.master.minsize(720, 460)
        try:
            self.master.geometry("760x520")
        except Exception:
            pass

        self.pack(fill="both", expand=True)

        pad_outer = {"padx": 10, "pady": 8}

        # Header
        hdr = ttk.Frame(self)
        hdr.pack(fill="x", **pad_outer)
        ttk.Label(hdr, text="Catalog Manager", font=("Segoe UI", 13, "bold")).pack(side="left")
        ttk.Label(hdr, text=str(self.root), foreground="#666").pack(side="right")

        # Body
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, **pad_outer)

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        right = ttk.Frame(body)
        right.pack(side="right", fill="both", expand=True, padx=(8, 0))

        # ---- Status ----
        st = ttk.LabelFrame(left, text="現在の状況")
        st.pack(fill="x", pady=(0, 10))

        st_grid = ttk.Frame(st)
        st_grid.pack(fill="x", padx=10, pady=10)
        st_grid.columnconfigure(1, weight=1)

        def add_row(r: int, label: str, var: tk.StringVar, value_font=None):
            ttk.Label(st_grid, text=label).grid(row=r, column=0, sticky="w", pady=2)
            ttk.Label(st_grid, textvariable=var, font=value_font, justify="left").grid(row=r, column=1, sticky="w", pady=2)

        add_row(0, "作品数", self.status_works, value_font=("Segoe UI", 11, "bold"))
        add_row(1, "画像あり", self.status_imgs)
        add_row(2, "動画あり", self.status_mov)
        add_row(3, "データ更新", self.status_mtime)
        add_row(4, "APIキー", self.status_keys)
        add_row(5, "更新モード", self.status_mode)
        add_row(6, "自動更新（Actions）", self.status_actions_time, value_font=("Segoe UI", 11, "bold"))
        add_row(7, "cron / workflow", self.status_actions_detail)

        # ---- Settings ----
        setf = ttk.LabelFrame(left, text="設定")
        setf.pack(fill="both", expand=True)

        # mode
        modef = ttk.Frame(setf)
        modef.pack(fill="x", padx=12, pady=(12, 6))
        ttk.Label(modef, text="更新モード", width=18).pack(side="left")
        ttk.Radiobutton(modef, text="維持更新（OFF）", value="update", variable=self.var_mode).pack(side="left")
        ttk.Radiobutton(modef, text="追加更新（ON）", value="full", variable=self.var_mode).pack(side="left", padx=(10, 0))

        ttk.Label(
            setf,
            text="※ OFF=既存作品だけ更新 / ON=新規追加もする（本番）",
            foreground="#555",
        ).pack(fill="x", padx=12, pady=(0, 8))

        # trim
        trimf = ttk.Frame(setf)
        trimf.pack(fill="x", padx=12, pady=6)
        ttk.Label(trimf, text="最大件数（テスト）", width=18).pack(side="left")
        ttk.Checkbutton(trimf, text="ON", variable=self.var_trim_enable).pack(side="left")
        ttk.Label(trimf, text="件数").pack(side="left", padx=(10, 4))
        self.ent_trim = ttk.Entry(trimf, textvariable=self.var_trim_to, width=8)
        self.ent_trim.pack(side="left")
        ttk.Button(trimf, text="件数削除（今のデータ）", command=self.trim_now).pack(side="right")

        # time + workflow
        timef = ttk.Frame(setf)
        timef.pack(fill="x", padx=12, pady=(10, 6))
        ttk.Label(timef, text="自動更新（Actions）", width=18).pack(side="left")
        self.ent_time = ttk.Entry(timef, textvariable=self.var_time, width=8)
        self.ent_time.pack(side="left")
        ttk.Label(timef, text="(HH:MM)").pack(side="left", padx=(6, 0))
        ttk.Checkbutton(timef, text="workflowへ反映", variable=self.var_apply_workflow).pack(side="right")

        wff = ttk.Frame(setf)
        wff.pack(fill="x", padx=12, pady=(0, 10))
        ttk.Label(wff, text="workflowファイル", width=18).pack(side="left")
        self.cmb_wf = ttk.Combobox(wff, textvariable=self.var_workflow_path, state="readonly")
        self.cmb_wf.pack(side="left", fill="x", expand=True)

        # API keys
        keyf = ttk.LabelFrame(setf, text="APIキー（ローカル）")
        keyf.pack(fill="x", padx=12, pady=(6, 12))

        krow1 = ttk.Frame(keyf)
        krow1.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(krow1, text="DMM_API_ID", width=18).pack(side="left")
        self.ent_api = ttk.Entry(krow1, textvariable=self.var_api_id, show="•")
        self.ent_api.pack(side="left", fill="x", expand=True)

        krow2 = ttk.Frame(keyf)
        krow2.pack(fill="x", padx=10, pady=4)
        ttk.Label(krow2, text="DMM_AFFILIATE_ID", width=18).pack(side="left")
        self.ent_aff = ttk.Entry(krow2, textvariable=self.var_aff_id, show="•")
        self.ent_aff.pack(side="left", fill="x", expand=True)

        krow3 = ttk.Frame(keyf)
        krow3.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Checkbutton(krow3, text="キーを表示", variable=self.var_show_keys, command=self._toggle_show_keys).pack(side="left")
        ttk.Button(krow3, text="保存（.catalog_secrets.json）", command=self.save_keys).pack(side="right")

        ttk.Label(keyf, text="※このファイルはGitHubにcommitしないでください。", foreground="#a33").pack(anchor="w", padx=10, pady=(0, 10))

        # ---- Right: Actions + Log ----
        runbox = ttk.LabelFrame(right, text="操作")
        runbox.pack(fill="x")

        ttk.Button(runbox, text="取得→生成（fetch→build）", command=self.run_fetch_build).pack(fill="x", padx=12, pady=(12, 6))
        ttk.Button(runbox, text="取得のみ（fetch）", command=self.run_fetch).pack(fill="x", padx=12, pady=6)
        ttk.Button(runbox, text="生成のみ（build）", command=self.run_build).pack(fill="x", padx=12, pady=6)
        ttk.Button(runbox, text="No image掃除（sanitize）", command=self.run_sanitize).pack(fill="x", padx=12, pady=(6, 12))

        logbox = ttk.LabelFrame(right, text="ログ")
        logbox.pack(fill="both", expand=True, pady=(10, 0))

        self.txt = tk.Text(logbox, height=16, wrap="word")
        yscroll = ttk.Scrollbar(logbox, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=yscroll.set)
        self.txt.grid(row=0, column=0, sticky="nsew", padx=(12, 0), pady=12)
        yscroll.grid(row=0, column=1, sticky="ns", padx=(0, 12), pady=12)
        logbox.rowconfigure(0, weight=1)
        logbox.columnconfigure(0, weight=1)
        self.txt.configure(state="disabled")

        # Footer (always visible)
        foot = ttk.Frame(self)
        foot.pack(fill="x", **pad_outer)

        ttk.Checkbutton(foot, text="変更したら自動保存/反映", variable=self.var_auto_apply, command=self._on_auto_apply_toggle).pack(side="left")
        ttk.Label(foot, textvariable=self.status_auto, foreground="#555").pack(side="left", padx=(10, 0))

        ttk.Button(foot, text="再読み込み", command=self.reload_all).pack(side="right")
        ttk.Button(foot, text="閉じる", command=self.master.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(foot, text="保存して反映", command=self.apply_all).pack(side="right", padx=(8, 0))

        # ウィンドウが小さすぎてフッターが隠れないよう、必要サイズにフィット
        self._fit_window_to_content()

    def _fit_window_to_content(self) -> None:
        """Ensure the window opens large enough to show the footer/buttons."""
        try:
            self.master.update_idletasks()
            req_w = self.master.winfo_reqwidth()
            req_h = self.master.winfo_reqheight()
            cur_w = self.master.winfo_width()
            cur_h = self.master.winfo_height()
            scr_w = self.master.winfo_screenwidth()
            scr_h = self.master.winfo_screenheight()

            w = min(max(req_w + 20, cur_w), max(320, scr_w - 80))
            h = min(max(req_h + 20, cur_h), max(240, scr_h - 120))
            if w > 100 and h > 100:
                self.master.geometry(f"{w}x{h}")
        except Exception:
            pass

    def _toggle_show_keys(self) -> None:
        show = self.var_show_keys.get()
        self.ent_api.configure(show="" if show else "•")
        self.ent_aff.configure(show="" if show else "•")

    # ---------- auto apply ----------
    def _bind_auto_apply(self) -> None:
        # 変更が頻発するので debounce する
        for v in [self.var_mode, self.var_trim_to, self.var_time, self.var_workflow_path]:
            v.trace_add("write", lambda *_: self._schedule_auto_apply())
        for v in [self.var_trim_enable, self.var_apply_workflow]:
            v.trace_add("write", lambda *_: self._schedule_auto_apply())

        # Entryのフォーカスアウトでも反映（入力途中の事故を減らす）
        self.ent_trim.bind("<FocusOut>", lambda e: self._schedule_auto_apply(force=True))
        self.ent_time.bind("<FocusOut>", lambda e: self._schedule_auto_apply(force=True))

    def _on_auto_apply_toggle(self) -> None:
        if self.var_auto_apply.get():
            self.status_auto.set("自動反映: ON")
            self._schedule_auto_apply(force=True)
        else:
            self.status_auto.set("自動反映: OFF")

    def _schedule_auto_apply(self, force: bool = False) -> None:
        if not self.var_auto_apply.get() and not force:
            return
        if self._is_running:
            # 実行中は少し後に再予約
            self.master.after(1200, lambda: self._schedule_auto_apply(force=force))
            return

        if self._auto_job:
            try:
                self.master.after_cancel(self._auto_job)
            except Exception:
                pass
            self._auto_job = None

        delay = 250 if force else 800
        self._auto_job = self.master.after(delay, lambda: self.apply_all(quiet=True))

    # ---------- helpers ----------
    def log(self, msg: str) -> None:
        self.txt.configure(state="normal")
        self.txt.insert("end", msg + "\n")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _preferred_encoding(self) -> str:
        enc = locale.getpreferredencoding(False) or "utf-8"
        return enc

    def _mask_secrets_line(self, line: str) -> str:
        for k in ["DMM_API_ID", "DMM_AFFILIATE_ID"]:
            if k in line:
                return re.sub(r"([A-Za-z0-9_\-]{6,})", "***", line)
        return line

    def _get_effective_env(self) -> Tuple[Dict[str, str], List[str]]:
        env = os.environ.copy()
        missing: List[str] = []

        api = (env.get("DMM_API_ID") or "").strip().strip('"')
        aff = (env.get("DMM_AFFILIATE_ID") or "").strip().strip('"')

        if not api or not aff:
            sec = load_secrets(self.root)
            if not api:
                api = sec.get("DMM_API_ID", "").strip().strip('"')
            if not aff:
                aff = sec.get("DMM_AFFILIATE_ID", "").strip().strip('"')

        if api:
            env["DMM_API_ID"] = api
        else:
            missing.append("DMM_API_ID")

        if aff:
            env["DMM_AFFILIATE_ID"] = aff
        else:
            missing.append("DMM_AFFILIATE_ID")

        return env, missing

    # ---------- load/save ----------
    def reload_all(self, show_toast: bool = True) -> None:
        try:
            self.cfg = ensure_default_config(self.cfg_path, self.root)
            self.secrets = load_secrets(self.root)

            # workflows list
            wf_dir = self.root / ".github" / "workflows"
            wf_files = sorted(list(wf_dir.glob("*.yml")) + list(wf_dir.glob("*.yaml")))
            wf_rels = [str(p.relative_to(self.root)).replace("\\", "/") for p in wf_files]
            self.cmb_wf["values"] = wf_rels

            # config -> vars
            fetch = self.cfg.get("fetch") or {}
            add_new = bool(fetch.get("add_new_works", False))
            self.var_mode.set("full" if add_new else "update")
            self.var_trim_enable.set(bool(fetch.get("trim_enable", False)))
            self.var_trim_to.set(str(int(fetch.get("trim_to", 300))))

            upd = self.cfg.get("update") or {}
            self.var_time.set(str(upd.get("jst_time", "03:00")))
            wf_rel = str((upd.get("workflow_path") or "")).strip()
            if wf_rels and wf_rel not in wf_rels:
                wf_rel = wf_rels[0]
            self.var_workflow_path.set(wf_rel)

            # status: works
            count, with_imgs, with_mov, mtime_s = read_works_stats(self.data_dir, self.legacy_works_path)
            self.status_works.set(str(count))
            self.status_imgs.set(str(with_imgs))
            self.status_mov.set(str(with_mov))
            self.status_mtime.set(mtime_s)

            # status: keys
            _, missing = self._get_effective_env()
            self.status_keys.set("OK" if not missing else f"未設定: {', '.join(missing)}")

            # status: fetch switches in file
            a, te, tt = read_fetch_toggles(self.fetch_path)

            def onoff(v: Optional[bool]) -> str:
                if v is True:
                    return "ON"
                if v is False:
                    return "OFF"
                return "?"

            # 更新モード（表示）
            mode_text = "維持更新（OFF）" if (self.var_mode.get() == "update") else "追加更新（ON）"
            line1 = f"{mode_text} /ADD_NEW_WORKS={onoff(a)}"
            line2 = f"最大件数 {tt} ({onoff(te)}) / (TRIM)={onoff(te)}"
            self.status_mode.set(line1 + "\n" + line2)

            # status: workflow cron
            wf_rel = self.var_workflow_path.get().strip()
            if wf_rel:
                wf_path = self.root / wf_rel
                if wf_path.exists():
                    yml = wf_path.read_text(encoding="utf-8")
                    cron = parse_cron_from_workflow(yml)
                    if cron:
                        jst = cron_to_jst_time(cron) or "?"
                        self.status_actions_time.set(f"{jst}（JST）")
                        self.status_actions_detail.set(f"{cron} / {wf_rel}")
                    else:
                        self.status_actions_time.set("(cron未設定)")
                        self.status_actions_detail.set(wf_rel)
                else:
                    self.status_actions_time.set("(workflow未検出)")
                    self.status_actions_detail.set(wf_rel)
            else:
                self.status_actions_time.set("(workflow未選択)")
                self.status_actions_detail.set("-")

            # secrets vars
            if self.var_api_id.get().strip() == "":
                self.var_api_id.set(self.secrets.get("DMM_API_ID", ""))
            if self.var_aff_id.get().strip() == "":
                self.var_aff_id.set(self.secrets.get("DMM_AFFILIATE_ID", ""))

            if show_toast:
                self.log("[OK] 読み込みました")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _validate_time(self) -> str:
        t = self.var_time.get().strip()
        _ = jst_time_to_cron(t)
        return t

    def _validate_trim_to(self) -> int:
        s = self.var_trim_to.get().strip()
        n = int(s)
        if n <= 0:
            raise ValueError("件数は 1 以上を指定してください")
        return n

    def save_keys(self) -> None:
        try:
            api = self.var_api_id.get().strip()
            aff = self.var_aff_id.get().strip()
            if not api or not aff:
                raise ValueError("DMM_API_ID と DMM_AFFILIATE_ID を入力してください")
            save_secrets(self.root, api, aff)
            self.log("[OK] .catalog_secrets.json を保存しました（※commitしないでください）")
            self.reload_all(show_toast=False)
        except Exception as e:
            messagebox.showerror("保存エラー", str(e))

    def apply_all(self, quiet: bool = False) -> None:
        """保存（config）+ 反映（fetchトグル / optional workflow）をまとめて実行。"""
        try:
            # validate
            n = self._validate_trim_to()
            t = self._validate_time()

            # save config
            self.cfg.setdefault("fetch", {})["add_new_works"] = (self.var_mode.get() == "full")
            self.cfg["fetch"]["trim_enable"] = bool(self.var_trim_enable.get())
            self.cfg["fetch"]["trim_to"] = int(n)

            self.cfg.setdefault("update", {})["jst_time"] = t
            self.cfg["update"]["workflow_path"] = self.var_workflow_path.get().strip()
            save_json(self.cfg_path, self.cfg)

            # apply to fetch script
            apply_fetch_toggles(
                self.fetch_path,
                add_new=(self.var_mode.get() == "full"),
                trim_enable=bool(self.var_trim_enable.get()),
                trim_to=int(n),
            )

            # apply to workflow
            if self.var_apply_workflow.get():
                wf_rel = self.var_workflow_path.get().strip()
                if wf_rel:
                    wf_path = self.root / wf_rel
                    cron = jst_time_to_cron(t)
                    ok = apply_cron_to_workflow(wf_path, cron)
                    if not ok and not quiet:
                        self.log("[WARN] workflow の cron を更新できませんでした（cron行が見つからない/ファイルなし）")

            if not quiet:
                self.log("[OK] 保存して反映しました")
            self.status_auto.set("自動反映: OK" if self.var_auto_apply.get() else "")
            self.reload_all(show_toast=False)
        except Exception as e:
            if quiet:
                self.status_auto.set(f"自動反映: エラー（{e}）")
                return
            messagebox.showerror("保存/反映エラー", str(e))

    def trim_now(self) -> None:
        try:
            n = self._validate_trim_to()
            kept = trim_works_data(self.data_dir, self.legacy_works_path, n)
            self.log(f"[OK] works.json を {kept} 件にしました")
            self.reload_all(show_toast=False)
        except Exception as e:
            messagebox.showerror("件数削除エラー", str(e))

    # ---------- runners ----------
    def _run_subprocess(self, script_rel: str, title: str) -> None:
        cmd = [sys.executable, script_rel]

        env, missing = self._get_effective_env()
        needs_key = "fetch_to_works_fanza.py" in script_rel
        if needs_key and missing:
            messagebox.showerror(
                "APIキーが未設定",
                "DMM_API_ID / DMM_AFFILIATE_ID が未設定です。\n\n"
                "方法1：Windowsの環境変数に設定する\n"
                "方法2：この画面の『APIキー（ローカル）』に入力→保存\n",
            )
            self.log(f"[ERR] missing env: {', '.join(missing)}")
            return

        enc = self._preferred_encoding()

        def worker():
            self._is_running = True
            self.log(f"[RUN] {title} : {' '.join(cmd)}")
            try:
                p = subprocess.Popen(
                    cmd,
                    cwd=str(self.root),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding=enc,
                    errors="replace",
                    env=env,
                )
                assert p.stdout is not None
                for line in p.stdout:
                    self.log(self._mask_secrets_line(line.rstrip("\n")))
                rc = p.wait()
                self.log(f"[DONE] exit={rc}")
            except Exception as e:
                self.log(f"[ERR] {e}")
            finally:
                self._is_running = False
                self.reload_all(show_toast=False)

        threading.Thread(target=worker, daemon=True).start()

    def run_build(self) -> None:
        self._run_subprocess("src/build.py", "build")

    def run_sanitize(self) -> None:
        if not self.sanitize_path.exists():
            messagebox.showerror("見つかりません", "src/sanitize_noimage_samples.py がありません")
            return
        self._run_subprocess("src/sanitize_noimage_samples.py", "sanitize")

    def run_fetch(self) -> None:
        # 念のため、実行前に保存して反映
        self.apply_all(quiet=True)
        self._run_subprocess("src/fetch_to_works_fanza.py", "fetch")

    def run_fetch_build(self) -> None:
        self.apply_all(quiet=True)

        def worker():
            env, missing = self._get_effective_env()
            if missing:
                messagebox.showerror(
                    "APIキーが未設定",
                    "DMM_API_ID / DMM_AFFILIATE_ID が未設定です。\n\n"
                    "『APIキー（ローカル）』に入力→保存するか、環境変数に設定してください。",
                )
                self.log(f"[ERR] missing env: {', '.join(missing)}")
                return

            enc = self._preferred_encoding()
            self._is_running = True
            try:
                for title, script in [("fetch", "src/fetch_to_works_fanza.py"), ("build", "src/build.py")]:
                    cmd = [sys.executable, script]
                    self.log(f"[RUN] {title} : {' '.join(cmd)}")
                    p = subprocess.Popen(
                        cmd,
                        cwd=str(self.root),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding=enc,
                        errors="replace",
                        env=env,
                    )
                    assert p.stdout is not None
                    for line in p.stdout:
                        self.log(self._mask_secrets_line(line.rstrip("\n")))
                    rc = p.wait()
                    self.log(f"[DONE] {title} exit={rc}")
                    if rc != 0:
                        break
            except Exception as e:
                self.log(f"[ERR] fetch→build {e}")
            finally:
                self._is_running = False
                self.reload_all(show_toast=False)

        threading.Thread(target=worker, daemon=True).start()


def main() -> None:
    root = tk.Tk()

    # Use a nicer default theme if available
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass

    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
