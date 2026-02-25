# -*- coding: utf-8 -*-
"""
Catalog Manager GUI (Tkinter) - v3

改善点（ユーザー要望）
- 「操作」ボタン群を画面下に固定して、ウィンドウを広げなくても必ず見えるように
- ログを見やすく：開始/完了/失敗が一目で分かる形式（✅/❌/⏳）＋実行時間＋状態表示
- DMM API / AFFILIATE ID を2段入力（.catalog_secrets.json に保存、commitしない）
- 取得→生成 ボタン追加（ローカル: fetch→build / GitHub実行時: fullへフォールバック）
- GitHub本番の「更新モード/最大件数/自動更新/自動更新時間」を表示
  - 自動更新/時間は Variables: CATALOG_AUTO_UPDATE_ENABLED / CATALOG_AUTO_UPDATE_TIME_JST を優先
  - 無い場合のみ workflow の cron を参照（表示だけ。書換えはしない＝403回避）

注意
- このGUIは cron を workflow ファイルへPUT更新しません（Contents Write不要）。
"""

from __future__ import annotations

import base64
import json
import os
import re
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tkinter import Tk, StringVar, BooleanVar, IntVar, messagebox
from tkinter import ttk
import tkinter as tk

APP_TITLE = "Catalog Manager"
CONFIG_FILE = "catalog_config.json"
SECRETS_FILE = ".catalog_secrets.json"

JST = timezone(timedelta(hours=9))


# ---------------- util ----------------

def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))

def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def safe_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on", "y")
    return False

def now_jst_str() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

def ensure_config(cfg_path: Path, root: Path) -> dict:
    cfg = load_json(cfg_path) if cfg_path.exists() else {}

    cfg.setdefault("update", {})
    cfg["update"].setdefault("enabled", True)      # 表示用
    cfg["update"].setdefault("jst_time", "03:00")  # 表示用
    cfg["update"].setdefault("workflow_path", ".github/workflows/update.yml")

    cfg.setdefault("fetch", {})
    cfg["fetch"].setdefault("add_new_works", False)
    cfg["fetch"].setdefault("trim_enable", False)
    cfg["fetch"].setdefault("trim_to", 300)

    cfg.setdefault("github", {})
    cfg["github"].setdefault("enabled", False)
    cfg["github"].setdefault("repo", "")          # OWNER/REPO
    cfg["github"].setdefault("branch", "main")
    cfg["github"].setdefault("run_buttons_on_github", True)
    cfg["github"].setdefault("push_on_save", True)
    cfg["github"].setdefault("sync_vars_on_save", True)  # 互換

    # workflow 自動検出（ローカル）
    wf_dir = root / ".github" / "workflows"
    if wf_dir.exists():
        candidates = sorted(list(wf_dir.glob("*.yml")) + list(wf_dir.glob("*.yaml")))
        if candidates:
            rels = [str(p.relative_to(root)).replace("\\", "/") for p in candidates]
            cur = str(cfg["update"].get("workflow_path") or "").strip()
            if not cur or cur not in rels:
                pick = None
                for key in ["update", "auto_update", "auto", "pages", "build_only"]:
                    for r in rels:
                        if key in r:
                            pick = r
                            break
                    if pick:
                        break
                cfg["update"]["workflow_path"] = pick or rels[0]

    save_json(cfg_path, cfg)
    return cfg

def load_secrets(root: Path) -> dict:
    p = root / SECRETS_FILE
    if not p.exists():
        return {}
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
        out = {}
        for k in ["DMM_API_ID", "DMM_AFFILIATE_ID", "GITHUB_PAT"]:
            v = j.get(k)
            if isinstance(v, str) and v.strip():
                out[k] = v.strip().strip('"')
        return out
    except Exception:
        return {}

def save_secret_fields(root: Path, **kwargs: str) -> None:
    p = root / SECRETS_FILE
    data = load_json(p) if p.exists() else {}
    for k, v in kwargs.items():
        if v is not None:
            data[k] = v
    data.setdefault("_note", "local only. DO NOT COMMIT THIS FILE.")
    data["_saved_at"] = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S %z")
    save_json(p, data)

def parse_cron_from_workflow(yml_text: str) -> str | None:
    m = re.search(r"(?m)^\s*-\s*cron:\s*['\"]([^'\"]+)['\"]\s*$", yml_text)
    return m.group(1).strip() if m else None

def cron_to_jst_time(cron: str) -> str | None:
    parts = cron.strip().split()
    if len(parts) != 5:
        return None
    minute, hour, dom, mon, dow = parts
    if dom != "*" or mon != "*" or dow != "*":
        return None
    if not minute.isdigit() or not hour.isdigit():
        return None
    dt = datetime(2000, 1, 1, int(hour), int(minute), tzinfo=timezone.utc).astimezone(JST)
    return dt.strftime("%H:%M")


# ---------------- GitHub API ----------------

@dataclass
class GH:
    owner: str
    repo: str
    branch: str
    token: str
    workflow_path: str

    @property
    def api_base(self) -> str:
        return f"https://api.github.com/repos/{self.owner}/{self.repo}"

    def _req(self, method: str, url: str, payload: dict | None = None):
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except Exception as e:
            return 0, str(e).encode("utf-8")

    def list_variables(self) -> dict:
        url = f"{self.api_base}/actions/variables?per_page=100"
        st, body = self._req("GET", url)
        if st != 200:
            raise RuntimeError(f"GitHub variables GET failed (status={st}) {body!r}")
        j = json.loads(body.decode("utf-8"))
        out = {}
        for it in j.get("variables", []) or []:
            name = (it.get("name") or "").strip()
            val = it.get("value")
            if name:
                out[name] = "" if val is None else str(val)
        return out

    def upsert_variable(self, name: str, value: str) -> None:
        url_u = f"{self.api_base}/actions/variables/{name}"
        st, body = self._req("PATCH", url_u, {"value": value})
        if st == 204:
            return
        if st == 404:
            url_c = f"{self.api_base}/actions/variables"
            st2, body2 = self._req("POST", url_c, {"name": name, "value": value})
            if st2 in (201, 204):
                return
            raise RuntimeError(f"GitHub variables POST failed: {name} (status={st2}) {body2!r}")
        raise RuntimeError(f"GitHub variables PATCH failed: {name} (status={st}) {body!r}")

    def get_workflow_text(self) -> str:
        wf = self.workflow_path.strip().lstrip("/")
        url = f"{self.api_base}/contents/{wf}?ref={self.branch}"
        st, body = self._req("GET", url)
        if st != 200:
            raise RuntimeError(f"GitHub contents GET failed: {wf} (status={st}) {body!r}")
        j = json.loads(body.decode("utf-8"))
        b64 = j.get("content") or ""
        raw = base64.b64decode(b64)
        return raw.decode("utf-8", errors="replace")

    def dispatch(self, task: str) -> None:
        wf_id = Path(self.workflow_path).name
        url = f"{self.api_base}/actions/workflows/{wf_id}/dispatches"
        payload = {"ref": self.branch, "inputs": {"task": task}}
        st, body = self._req("POST", url, payload)
        if st in (201, 204):
            return
        payload2 = {"ref": self.branch}
        st2, body2 = self._req("POST", url, payload2)
        if st2 in (201, 204):
            return
        raise RuntimeError(f"GitHub dispatch failed (status={st}) {body!r} / retry(status={st2}) {body2!r}")


# ---------------- GUI ----------------

class App:
    def __init__(self, master: Tk):
        self.master = master
        self.root = Path(__file__).resolve().parent
        self.cfg_path = self.root / CONFIG_FILE
        self.cfg = ensure_config(self.cfg_path, self.root)
        self.secrets = load_secrets(self.root)

        self.master.title(APP_TITLE)
        # 横を少し広め：左(設定) + 右(ログ)
        self.master.geometry("980x580")
        self.master.minsize(860, 520)

        # status line (top)
        self.var_run_state = StringVar(value="待機中")
        topbar = ttk.Frame(master)
        topbar.pack(fill="x", padx=10, pady=(8, 2))
        ttk.Label(topbar, text="状態:").pack(side="left")
        ttk.Label(topbar, textvariable=self.var_run_state).pack(side="left", padx=(6, 0))
        ttk.Button(topbar, text="GitHub設定 取得 (get)", command=self.refresh_github_status_async).pack(side="right")

        # main panes
        paned = ttk.PanedWindow(master, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=8)

        # LEFT container: (scrollable settings/status) + (fixed actions)
        left = ttk.Frame(paned)
        paned.add(left, weight=3)

        # Scroll area for status/settings
        scroll_area = ttk.Frame(left)
        scroll_area.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(scroll_area, highlightthickness=0)
        vsb = ttk.Scrollbar(scroll_area, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.inner = ttk.Frame(self.canvas)
        self._inner_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # Fixed actions area (always visible)
        self.fixed_actions = ttk.LabelFrame(left, text="操作（常に表示）")
        self.fixed_actions.pack(fill="x", pady=(8, 0))

        # RIGHT: log
        right = ttk.Frame(paned)
        paned.add(right, weight=2)
        lf_log = ttk.LabelFrame(right, text="ログ（開始/完了が分かる）")
        lf_log.pack(fill="both", expand=True)
        self.txt_log = tk.Text(lf_log, wrap="word")
        self.txt_log.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        sb_log = ttk.Scrollbar(lf_log, orient="vertical", command=self.txt_log.yview)
        sb_log.pack(side="right", fill="y", padx=(6, 8), pady=8)
        self.txt_log.configure(yscrollcommand=sb_log.set)
        self.txt_log.configure(state="disabled")

        # Log tags
        self.txt_log.tag_configure("INFO", spacing1=2, spacing3=2)
        self.txt_log.tag_configure("OK", spacing1=2, spacing3=2)
        self.txt_log.tag_configure("ERR", spacing1=2, spacing3=2)
        self.txt_log.tag_configure("RAW", spacing1=0, spacing3=0)

        # vars (local)
        self.var_add_new = BooleanVar(value=safe_bool(self.cfg["fetch"].get("add_new_works")))
        self.var_trim_en = BooleanVar(value=safe_bool(self.cfg["fetch"].get("trim_enable")))
        self.var_trim_to = IntVar(value=int(self.cfg["fetch"].get("trim_to") or 300))
        self.var_auto_en = BooleanVar(value=safe_bool(self.cfg["update"].get("enabled")))
        self.var_auto_time = StringVar(value=str(self.cfg["update"].get("jst_time") or "03:00"))
        self.var_workflow_path = StringVar(value=str(self.cfg["update"].get("workflow_path") or ".github/workflows/update.yml"))

        # vars (github)
        gh_cfg = self.cfg.get("github") or {}
        self.var_gh_enabled = BooleanVar(value=safe_bool(gh_cfg.get("enabled")))
        self.var_gh_repo = StringVar(value=str(gh_cfg.get("repo") or ""))
        self.var_gh_branch = StringVar(value=str(gh_cfg.get("branch") or "main"))
        self.var_gh_run_on_github = BooleanVar(value=safe_bool(gh_cfg.get("run_buttons_on_github", True)))
        self.var_push_github_on_save = BooleanVar(value=safe_bool(gh_cfg.get("push_on_save", True)))

        # status (local)
        self.local_status_mode = StringVar(value="-")
        self.local_status_trim = StringVar(value="-")
        self.local_status_auto = StringVar(value="-")
        self.local_status_time = StringVar(value="-")

        # status (github)
        self.gh_status_mode = StringVar(value="-")
        self.gh_status_trim = StringVar(value="-")
        self.gh_status_auto = StringVar(value="-")
        self.gh_status_time = StringVar(value="-")
        self.gh_status_updated = StringVar(value="-")

        self._build_scroll_ui()
        self._build_fixed_actions()
        self.refresh_local_status()

        self.master.after(200, self.refresh_github_status_async)

    # ---------- canvas handlers ----------
    def _on_inner_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self._inner_id, width=event.width)

    # ---------- logging ----------
    def _append_log(self, msg: str, tag: str = "INFO"):
        ts = datetime.now(JST).strftime("%H:%M:%S")
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", f"[{ts}] {msg}\n", tag)
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def log_info(self, msg: str):
        self._append_log(f"⏳ {msg}", "INFO")

    def log_ok(self, msg: str):
        self._append_log(f"✅ {msg}", "OK")

    def log_err(self, msg: str):
        self._append_log(f"❌ {msg}", "ERR")

    def log_raw(self, msg: str):
        # subprocess raw line (indent)
        self._append_log(f"   {msg}", "RAW")

    def set_state(self, s: str):
        self.var_run_state.set(s)

    # ---------- UI builders ----------
    def _build_scroll_ui(self):
        pad = {"padx": 8, "pady": 8}

        def row(parent, label, var):
            r = ttk.Frame(parent)
            r.pack(fill="x", padx=8, pady=2)
            ttk.Label(r, text=label, width=16).pack(side="left")
            ttk.Label(r, textvariable=var).pack(side="left", fill="x", expand=True)

        # Local status
        lf = ttk.LabelFrame(self.inner, text="ローカル（テスト）: 現在の状況")
        lf.pack(fill="x", **pad)
        row(lf, "更新モード", self.local_status_mode)
        row(lf, "最大件数", self.local_status_trim)
        row(lf, "自動更新", self.local_status_auto)
        row(lf, "自動更新時間", self.local_status_time)

        # GitHub status
        gf = ttk.LabelFrame(self.inner, text="GitHub（本番）: 現在の状況")
        gf.pack(fill="x", **pad)
        row(gf, "更新モード", self.gh_status_mode)
        row(gf, "最大件数", self.gh_status_trim)
        row(gf, "自動更新", self.gh_status_auto)
        row(gf, "自動更新時間", self.gh_status_time)
        row(gf, "最終取得", self.gh_status_updated)

        ttk.Label(gf, text="※自動更新/時間は Variables(CATALOG_AUTO_UPDATE_*) を優先。無い場合のみ workflow cron を参照（表示のみ）。").pack(anchor="w", padx=8, pady=(2, 6))

        # Local settings
        sf = ttk.LabelFrame(self.inner, text="設定（ローカル / GitHub反映元）")
        sf.pack(fill="x", **pad)

        r1 = ttk.Frame(sf); r1.pack(fill="x", padx=8, pady=3)
        ttk.Checkbutton(
            r1,
            text="新規作品も追加する（ON=新しい作品を追加 / OFF=既存作品だけ更新）",
            variable=self.var_add_new
        ).pack(side="left")

        r2 = ttk.Frame(sf); r2.pack(fill="x", padx=8, pady=3)
        ttk.Checkbutton(r2, text="最大件数（テスト）ON", variable=self.var_trim_en).pack(side="left")
        ttk.Label(r2, text="件数").pack(side="left", padx=(10, 0))
        ttk.Entry(r2, textvariable=self.var_trim_to, width=8).pack(side="left", padx=(6, 0))

        r3 = ttk.Frame(sf); r3.pack(fill="x", padx=8, pady=3)
        ttk.Checkbutton(r3, text="自動更新（本番の設定として保存）", variable=self.var_auto_en).pack(side="left")
        ttk.Label(r3, text="時間(JST)").pack(side="left", padx=(10, 0))
        ttk.Entry(r3, textvariable=self.var_auto_time, width=7).pack(side="left", padx=(6, 0))
        ttk.Label(r3, text="workflow").pack(side="left", padx=(10, 0))
        ttk.Entry(r3, textvariable=self.var_workflow_path, width=32).pack(side="left", padx=(6, 0))

        # GitHub settings
        gsf = ttk.LabelFrame(self.inner, text="GitHub 本番連携（表示/実行）")
        gsf.pack(fill="x", **pad)

        r0 = ttk.Frame(gsf); r0.pack(fill="x", padx=8, pady=3)
        ttk.Checkbutton(r0, text="GitHub連携を有効化", variable=self.var_gh_enabled).pack(side="left")
        ttk.Checkbutton(r0, text="操作ボタンはGitHubで実行", variable=self.var_gh_run_on_github).pack(side="left", padx=(12, 0))

        rA = ttk.Frame(gsf); rA.pack(fill="x", padx=8, pady=3)
        ttk.Label(rA, text="Repo").pack(side="left")
        ttk.Entry(rA, textvariable=self.var_gh_repo, width=28).pack(side="left", padx=(6, 0))
        ttk.Label(rA, text="Branch").pack(side="left", padx=(10, 0))
        ttk.Entry(rA, textvariable=self.var_gh_branch, width=12).pack(side="left", padx=(6, 0))

        rC = ttk.Frame(gsf); rC.pack(fill="x", padx=8, pady=3)
        ttk.Label(rC, text="PAT").pack(side="left")
        self.ent_pat = ttk.Entry(rC, show="*", width=40)
        self.ent_pat.pack(side="left", padx=(6, 0))
        self.ent_pat.insert(0, self.secrets.get("GITHUB_PAT", ""))
        ttk.Button(rC, text="PAT保存 (save)", command=self.save_pat).pack(side="left", padx=(8, 0))
        ttk.Label(rC, text="※workflow cron表示にはContents Readが必要").pack(side="left", padx=(10, 0))

        # DMM API (two rows)
        rD1 = ttk.Frame(gsf); rD1.pack(fill="x", padx=8, pady=3)
        ttk.Label(rD1, text="DMM API").pack(side="left")
        self.ent_dmm_api = ttk.Entry(rD1, show="*", width=30)
        self.ent_dmm_api.pack(side="left", padx=(6, 0))
        self.ent_dmm_api.insert(0, self.secrets.get("DMM_API_ID", ""))

        rD2 = ttk.Frame(gsf); rD2.pack(fill="x", padx=8, pady=3)
        ttk.Label(rD2, text="AFFILIATE ID").pack(side="left")
        self.ent_dmm_aff = ttk.Entry(rD2, show="*", width=30)
        self.ent_dmm_aff.pack(side="left", padx=(6, 0))
        self.ent_dmm_aff.insert(0, self.secrets.get("DMM_AFFILIATE_ID", ""))

        ttk.Button(rD2, text="API保存 (save)", command=self.save_dmm_keys).pack(side="left", padx=(8, 0))
        ttk.Label(rD2, text="※ローカルfetch用（.catalog_secrets.json / GitHubには送られません）").pack(side="left", padx=(10, 0))

    def _build_fixed_actions(self):
        r = ttk.Frame(self.fixed_actions)
        r.pack(fill="x", padx=8, pady=8)

        ttk.Button(r, text="保存して反映 (save)", command=self.save_and_apply).pack(side="left")
        ttk.Checkbutton(r, text="GitHubにも反映（Variables更新）", variable=self.var_push_github_on_save).pack(side="left", padx=(10, 0))
        ttk.Button(r, text="再読込 (reload)", command=self.reload).pack(side="left", padx=(12, 0))

        r2 = ttk.Frame(self.fixed_actions)
        r2.pack(fill="x", padx=8, pady=(0, 8))

        # 1段目：単体
        ttk.Button(r2, text="取得のみ (fetch)", command=lambda: self.run_task("fetch")).pack(side="left")
        ttk.Button(r2, text="生成のみ (build)", command=lambda: self.run_task("build")).pack(side="left", padx=(8, 0))
        ttk.Button(r2, text="No image掃除 (sanitize)", command=lambda: self.run_task("sanitize")).pack(side="left", padx=(8, 0))

        # 2段目：組み合わせ（必ず見える）
        r3 = ttk.Frame(self.fixed_actions)
        r3.pack(fill="x", padx=8, pady=(0, 10))
        ttk.Button(r3, text="取得→生成 (fetch+build)", command=lambda: self.run_task("fetch_build")).pack(side="left")
        ttk.Button(r3, text="取得→掃除→生成 (full)", command=lambda: self.run_task("full")).pack(side="left", padx=(8, 0))

    # ---------- local status ----------
    def refresh_local_status(self):
        mode = "新規も追加（新しい作品を追加）" if self.var_add_new.get() else "既存のみ更新（新規は追加しない）"
        trim = f"ON（{self.var_trim_to.get()}件）" if self.var_trim_en.get() else "OFF"
        auto = "ON" if self.var_auto_en.get() else "OFF"
        t = self.var_auto_time.get().strip() if self.var_auto_en.get() else "-"
        self.local_status_mode.set(mode)
        self.local_status_trim.set(trim)
        self.local_status_auto.set(auto)
        self.local_status_time.set(t)

    # ---------- GitHub helpers ----------
    def _get_gh(self) -> GH:
        if not self.var_gh_enabled.get():
            raise RuntimeError("GitHub連携がOFFです")
        repo = self.var_gh_repo.get().strip()
        if "/" not in repo:
            raise RuntimeError("Repo は OWNER/REPO 形式で入力してください")
        owner, name = repo.split("/", 1)
        token = self.ent_pat.get().strip() or self.secrets.get("GITHUB_PAT", "")
        if not token:
            raise RuntimeError("PAT が未設定です（PAT保存を押してください）")
        return GH(
            owner=owner.strip(),
            repo=name.strip(),
            branch=self.var_gh_branch.get().strip() or "main",
            token=token,
            workflow_path=self.var_workflow_path.get().strip() or ".github/workflows/update.yml",
        )

    def refresh_github_status_async(self):
        def job():
            try:
                gh = self._get_gh()
                vars_ = gh.list_variables()

                add_new = safe_bool(vars_.get("CATALOG_ADD_NEW_WORKS", "false"))
                trim_en = safe_bool(vars_.get("CATALOG_TRIM_ENABLE", "false"))
                trim_to = (vars_.get("CATALOG_TRIM_TO", "") or "").strip()
                mode = "新規も追加（新しい作品を追加）" if add_new else "既存のみ更新（新規は追加しない）"
                trim = f"ON（{trim_to}件）" if trim_en and trim_to else "OFF"

                auto_en_v = vars_.get("CATALOG_AUTO_UPDATE_ENABLED", None)
                auto_time_v = vars_.get("CATALOG_AUTO_UPDATE_TIME_JST", None)

                if auto_en_v is not None or auto_time_v is not None:
                    auto = "ON" if safe_bool(auto_en_v or "false") else "OFF"
                    auto_time = (auto_time_v or "").strip() if auto == "ON" else "-"
                    if auto == "ON" and not auto_time:
                        auto_time = "（未設定）"
                else:
                    # fallback to cron (read-only)
                    auto, auto_time = "（取得中）", "（取得中）"
                    try:
                        wf_text = gh.get_workflow_text()
                        cron = parse_cron_from_workflow(wf_text)
                        if cron:
                            auto = "ON"
                            auto_time = cron_to_jst_time(cron) or f"(cron) {cron}"
                        else:
                            auto = "OFF"
                            auto_time = "-"
                    except Exception as e:
                        auto = "（取得不可）"
                        auto_time = "PATにContents Read"
                        self.master.after(0, lambda: self.log_info(f"GitHub: workflow cron参照失敗: {e}"))

                def apply_ui():
                    self.gh_status_mode.set(mode)
                    self.gh_status_trim.set(trim)
                    self.gh_status_auto.set(auto)
                    self.gh_status_time.set(auto_time)
                    self.gh_status_updated.set(now_jst_str())
                    self.log_ok("GitHub設定 取得OK")

                self.master.after(0, apply_ui)
            except Exception as e:
                self.master.after(0, lambda: self.log_err(f"GitHub設定取得エラー: {e}"))

        threading.Thread(target=job, daemon=True).start()

    def push_github_variables_async(self):
        def job():
            try:
                gh = self._get_gh()
                gh.upsert_variable("CATALOG_ADD_NEW_WORKS", "true" if self.var_add_new.get() else "false")
                gh.upsert_variable("CATALOG_TRIM_ENABLE", "true" if self.var_trim_en.get() else "false")
                gh.upsert_variable("CATALOG_TRIM_TO", str(int(self.var_trim_to.get())))
                gh.upsert_variable("CATALOG_SANITIZE_LEARN", "false")

                gh.upsert_variable("CATALOG_AUTO_UPDATE_ENABLED", "true" if self.var_auto_en.get() else "false")
                gh.upsert_variable("CATALOG_AUTO_UPDATE_TIME_JST", self.var_auto_time.get().strip())

                self.master.after(0, lambda: self.log_ok("GitHub variables 更新OK"))
                self.master.after(0, self.refresh_github_status_async)
            except Exception as e:
                self.master.after(0, lambda: self.log_err(f"GitHub反映エラー（variables）: {e}"))

        threading.Thread(target=job, daemon=True).start()

    def save_pat(self):
        tok = self.ent_pat.get().strip()
        if not tok:
            messagebox.showerror("PAT", "PATが空です")
            return
        save_secret_fields(self.root, GITHUB_PAT=tok)
        self.secrets["GITHUB_PAT"] = tok
        self.log_ok("PAT保存OK（.catalog_secrets.json）")

    def save_dmm_keys(self):
        api = self.ent_dmm_api.get().strip()
        aff = self.ent_dmm_aff.get().strip()
        if not api or not aff:
            messagebox.showerror("DMM API", "DMM API / AFFILIATE ID の両方を入力してください")
            return
        save_secret_fields(self.root, DMM_API_ID=api, DMM_AFFILIATE_ID=aff)
        self.secrets["DMM_API_ID"] = api
        self.secrets["DMM_AFFILIATE_ID"] = aff
        self.log_ok("API保存OK（.catalog_secrets.json）")

    # ---------- actions ----------
    def reload(self):
        self.cfg = ensure_config(self.cfg_path, self.root)
        self.secrets = load_secrets(self.root)

        self.var_add_new.set(safe_bool(self.cfg["fetch"].get("add_new_works")))
        self.var_trim_en.set(safe_bool(self.cfg["fetch"].get("trim_enable")))
        self.var_trim_to.set(int(self.cfg["fetch"].get("trim_to") or 300))
        self.var_auto_en.set(safe_bool(self.cfg["update"].get("enabled")))
        self.var_auto_time.set(str(self.cfg["update"].get("jst_time") or "03:00"))
        self.var_workflow_path.set(str(self.cfg["update"].get("workflow_path") or ".github/workflows/update.yml"))

        gh = self.cfg.get("github") or {}
        self.var_gh_enabled.set(safe_bool(gh.get("enabled")))
        self.var_gh_repo.set(str(gh.get("repo") or ""))
        self.var_gh_branch.set(str(gh.get("branch") or "main"))
        self.var_gh_run_on_github.set(safe_bool(gh.get("run_buttons_on_github", True)))
        self.var_push_github_on_save.set(safe_bool(gh.get("push_on_save", True)))

        # reflect secrets in entries
        self.ent_pat.delete(0, "end")
        self.ent_pat.insert(0, self.secrets.get("GITHUB_PAT", ""))
        self.ent_dmm_api.delete(0, "end")
        self.ent_dmm_api.insert(0, self.secrets.get("DMM_API_ID", ""))
        self.ent_dmm_aff.delete(0, "end")
        self.ent_dmm_aff.insert(0, self.secrets.get("DMM_AFFILIATE_ID", ""))

        self.refresh_local_status()
        self.log_ok("再読込OK")
        self.refresh_github_status_async()

    def save_and_apply(self):
        t = self.var_auto_time.get().strip()
        if t and not re.match(r"^\d{2}:\d{2}$", t):
            messagebox.showerror("入力エラー", "自動更新時間は HH:MM 形式で入力してください")
            return

        self.cfg.setdefault("fetch", {})
        self.cfg["fetch"]["add_new_works"] = bool(self.var_add_new.get())
        self.cfg["fetch"]["trim_enable"] = bool(self.var_trim_en.get())
        self.cfg["fetch"]["trim_to"] = int(self.var_trim_to.get())

        self.cfg.setdefault("update", {})
        self.cfg["update"]["enabled"] = bool(self.var_auto_en.get())
        self.cfg["update"]["jst_time"] = t
        self.cfg["update"]["workflow_path"] = self.var_workflow_path.get().strip()

        self.cfg.setdefault("github", {})
        self.cfg["github"]["enabled"] = bool(self.var_gh_enabled.get())
        self.cfg["github"]["repo"] = self.var_gh_repo.get().strip()
        self.cfg["github"]["branch"] = self.var_gh_branch.get().strip() or "main"
        self.cfg["github"]["run_buttons_on_github"] = bool(self.var_gh_run_on_github.get())
        self.cfg["github"]["push_on_save"] = bool(self.var_push_github_on_save.get())
        self.cfg["github"]["sync_vars_on_save"] = bool(self.var_push_github_on_save.get())  # 互換

        save_json(self.cfg_path, self.cfg)
        self.refresh_local_status()
        self.log_ok("保存OK（catalog_config.json）")

        if self.var_gh_enabled.get() and self.var_push_github_on_save.get():
            self.push_github_variables_async()

    def run_task(self, task: str):
        # GitHub実行
        if self.var_gh_enabled.get() and self.var_gh_run_on_github.get():
            if task == "fetch_build":
                self.log_info("GitHub実行では「取得→生成」は full（取得→掃除→生成）で実行します")
                self.dispatch_async("full")
                return
            self.dispatch_async(task)
            return
        # ローカル実行
        self.run_local(task)

    def dispatch_async(self, task: str):
        def job():
            self.master.after(0, lambda: self.set_state("GitHub実行中..."))
            t0 = time.time()
            try:
                gh = self._get_gh()
                self.master.after(0, lambda: self.log_info(f"GitHub実行 発火: task={task}"))
                gh.dispatch(task)
                dt = time.time() - t0
                self.master.after(0, lambda: self.log_ok(f"GitHub実行 発火OK (task={task}, {dt:.1f}s)"))
                self.master.after(0, lambda: self.set_state("待機中"))
            except Exception as e:
                self.master.after(0, lambda: self.log_err(f"GitHub実行エラー: {e}"))
                self.master.after(0, lambda: self.set_state("エラー"))

        threading.Thread(target=job, daemon=True).start()

    def run_local(self, task: str):
        root = self.root
        py = sys.executable
        cmds = {
            "fetch": [py, "src/fetch_to_works_fanza.py"],
            "sanitize": [py, "src/sanitize_noimage_samples.py", "--learn"],
            "build": [py, "src/build.py"],
        }
        if task not in ("fetch", "sanitize", "build", "full", "fetch_build"):
            self.log_err(f"unknown task: {task}")
            return

        def job():
            self.master.after(0, lambda: self.set_state("ローカル実行中..."))
            t0 = time.time()
            fetch_env = {
                "CATALOG_ADD_NEW_WORKS": "true" if self.var_add_new.get() else "false",
                "CATALOG_TRIM_ENABLE": "true" if self.var_trim_en.get() else "false",
                "CATALOG_TRIM_TO": str(int(self.var_trim_to.get())),
            }
            try:
                if task == "full":
                    self.master.after(0, lambda: self.log_info("ローカル: 取得→掃除→生成 開始"))
                    self._run_subprocess(cmds["fetch"], cwd=root, extra_env=fetch_env)
                    self._run_subprocess(cmds["sanitize"], cwd=root)
                    self._run_subprocess(cmds["build"], cwd=root)
                elif task == "fetch_build":
                    self.master.after(0, lambda: self.log_info("ローカル: 取得→生成 開始"))
                    self._run_subprocess(cmds["fetch"], cwd=root, extra_env=fetch_env)
                    self._run_subprocess(cmds["build"], cwd=root)
                else:
                    self.master.after(0, lambda: self.log_info(f"ローカル: {task} 開始"))
                    if task == "fetch":
                        self._run_subprocess(cmds[task], cwd=root, extra_env=fetch_env)
                    else:
                        self._run_subprocess(cmds[task], cwd=root)

                dt = time.time() - t0
                self.master.after(0, lambda: self.log_ok(f"ローカル: {task} 完了 ({dt:.1f}s)"))
                self.master.after(0, lambda: self.set_state("待機中"))
            except Exception as e:
                self.master.after(0, lambda: self.log_err(f"ローカル: {task} 失敗: {e}"))
                self.master.after(0, lambda: self.set_state("エラー"))

        threading.Thread(target=job, daemon=True).start()

    def _run_subprocess(self, cmd, cwd: Path, extra_env=None):
        # 文字化け対策：子プロセスの標準出力をUTF-8へ
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")

        # DMMキー：.catalog_secrets.json から env に流し込み（未設定時のみ）
        if not env.get("DMM_API_ID") and self.secrets.get("DMM_API_ID"):
            env["DMM_API_ID"] = self.secrets["DMM_API_ID"]
        if not env.get("DMM_AFFILIATE_ID") and self.secrets.get("DMM_AFFILIATE_ID"):
            env["DMM_AFFILIATE_ID"] = self.secrets["DMM_AFFILIATE_ID"]

        # GUIから渡された追加env（設定反映用）
        if extra_env:
            env.update(extra_env)

        self.master.after(0, lambda: self.log_raw("[RUN] " + " ".join(map(str, cmd))))

        p = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert p.stdout is not None
        for line in p.stdout:
            s = line.rstrip("\n")
            if not s:
                continue
            # raw lines: keep but indent
            self.master.after(0, lambda ss=s: self.log_raw(ss))
        rc = p.wait()
        if rc != 0:
            raise RuntimeError(f"command failed rc={rc}")


def main():
    root = Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
