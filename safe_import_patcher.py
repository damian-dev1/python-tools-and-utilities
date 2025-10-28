from __future__ import annotations
import ast
import os
import sys
import threading
import importlib.util
import csv
from typing import List, Tuple, Set, Optional, Dict, Any
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
LOG_BUFFER: List[str] = []
LOG_SINK_WIDGET: tk.Text | None = None
def set_log_sink(widget: tk.Text | None) -> None:
    global LOG_SINK_WIDGET
    LOG_SINK_WIDGET = widget
def g_log(line: str) -> None:
    """Append to global buffer and live Text sink (if present)."""
    LOG_BUFFER.append(line)
    if LOG_SINK_WIDGET is not None:
        try:
            LOG_SINK_WIDGET.insert("end", line + "\n")
            LOG_SINK_WIDGET.see("end")
            LOG_SINK_WIDGET.update_idletasks()
        except Exception:
            pass
def g_log_block(block: str) -> None:
    for ln in block.splitlines():
        g_log(ln)
MARKER = "# OPTIONAL-IMPORT-PATCH"
HELPER_BLOCK = f"""{MARKER}-HELPER
class _OptionalImportsHelper:
    def __init__(self, _globals):
        self._g = _globals
    def is_available(self, name: str) -> bool:
        return self._g.get(name) is not None
optional_imports = _OptionalImportsHelper(globals())
{MARKER}-HELPER-END
"""
def _read_lines(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as f:
        return f.readlines()
def _write_lines(path: str, lines: List[str]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)
def _parse_tree(lines: List[str], filename: str) -> ast.AST:
    return ast.parse("".join(lines), filename=filename)
def _is_stdlib(mod: str) -> bool:
    names = getattr(sys, "stdlib_module_names", None)
    if names is None:
        likely_std = {
            "sys","os","re","json","pathlib","typing","tkinter","threading","queue",
            "time","datetime","math","itertools","functools","subprocess","argparse",
            "logging","base64","hashlib","collections","random","statistics","shutil",
            "tempfile","traceback","inspect","types","enum","uuid","platform","ctypes",
            "gzip","bz2","lzma","csv","configparser"
        }
        return mod in likely_std
    return mod in names
def _find_missing(mods: Set[str]) -> Set[str]:
    missing = set()
    for m in mods:
        try:
            if importlib.util.find_spec(m) is None:
                missing.add(m)
        except (ImportError, ValueError):
            missing.add(m)
    return missing
def _collect_imports(tree: ast.AST) -> List[Tuple[str, str, Optional[str], Optional[str], int, int]]:
    """
    Returns list of (kind, module, feature, alias, lineno, level)
    """
    results = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                results.append(("import", top, None, alias.asname, node.lineno, 0))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module
            level = node.level or 0
            base = "" if mod is None else mod.split(".")[0]
            for alias in node.names:
                results.append(("from", base, alias.name, alias.asname, node.lineno, level))
    return sorted(results, key=lambda t: t[4])
def _gen_wrapper(kind: str, module: str, feature: Optional[str], asname: Optional[str]) -> List[str]:
    indent = " " * 4
    alias = f" as {asname}" if asname else ""
    if kind == "import":
        target = asname or module
        return [
            f"{MARKER}\n",
            "try:\n",
            f"{indent}import {module}{alias}\n",
            "except ImportError:\n",
            f"{indent}{target} = None  # module optional\n",
        ]
    else:
        target = asname or feature
        return [
            f"{MARKER}\n",
            "try:\n",
            f"{indent}from {module} import {feature}{alias}\n",
            "except (ImportError, AttributeError):\n",
            f"{indent}{target} = None  # feature optional\n",
        ]
def _insert_top_helper(lines: List[str]) -> List[str]:
    text = "".join(lines)
    if f"{MARKER}-HELPER" in text:
        return lines
    insert_at = 0
    if lines and lines[0].startswith("#!"):
        insert_at = 1
    if insert_at < len(lines) and "coding" in lines[insert_at]:
        insert_at += 1
    try:
        tree = ast.parse("".join(lines))
        if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(getattr(tree.body[0], "value", None), ast.Str):
            doc_node = tree.body[0]
            insert_at = getattr(doc_node, "lineno", 1)
    except Exception:
        pass
    new_lines = lines[:insert_at] + [HELPER_BLOCK] + lines[insert_at:]
    return new_lines
def _patch_inline(
    lines: List[str],
    imports: List[Tuple[str,str,Optional[str],Optional[str],int,int]],
    missing: Set[str],
    wrap_all: bool,
    include_stdlib: bool,
    skip: Set[str],
    log: List[str],
) -> List[str]:
    patched = lines[:]
    offset = 0
    seen_linenos: Set[int] = set()
    for kind, mod, feat, asname, lineno, level in imports:
        if level and level > 0:
            msg = f"skip: relative import at line {lineno}"
            log.append(msg); g_log(msg)
            continue
        if not mod:
            msg = f"skip: empty module at line {lineno}"
            log.append(msg); g_log(msg)
            continue
        if mod in skip:
            msg = f"skip: {mod} (user skip list) at line {lineno}"
            log.append(msg); g_log(msg)
            continue
        should_wrap = wrap_all or (mod in missing)
        if not should_wrap:
            if not include_stdlib and _is_stdlib(mod):
                msg = f"keep: stdlib {mod} at line {lineno}"
                log.append(msg); g_log(msg)
                continue
            else:
                msg = f"keep: present {mod} at line {lineno}"
                log.append(msg); g_log(msg)
                continue
        if lineno in seen_linenos:
            msg = f"skip: already patched line {lineno}"
            log.append(msg); g_log(msg)
            continue
        seen_linenos.add(lineno)
        wrapper = _gen_wrapper(kind, mod, feat, asname)
        idx0 = lineno - 1 + offset
        patched[idx0:idx0+1] = wrapper
        offset += len(wrapper) - 1
        msg = f"wrap: {('import' if kind=='import' else f'from {mod} import {feat}')}, line {lineno}"
        log.append(msg); g_log(msg)
    return patched
def patch_file(
    path: str,
    write_mode: str,              # "create" or "rewrite"
    output: Optional[str],
    dry_run: bool,
    wrap_all: bool,
    include_stdlib: bool,
    top_helper: bool,
    skip_csv: str = "",
) -> Tuple[int, str, str]:
    """
    Returns (exit_code, log_text, preview_text)
    """
    logs: List[str] = []
    preview = ""
    if not os.path.isfile(path):
        msg = f"[error] File not found: {path}"
        g_log(msg)
        return 2, msg, ""
    try:
        lines = _read_lines(path)
        tree = _parse_tree(lines, path)
    except Exception as e:
        msg = f"[error] Failed to read/parse file: {e}"
        g_log(msg)
        return 2, msg, ""
    imps = _collect_imports(tree)
    modules = {m for _, m, _, _, _, lvl in imps if m and lvl == 0}
    missing = _find_missing(modules)
    msg1 = f"[info] Found {len(imps)} import entries. Unique modules: {len(modules)}"
    msg2 = f"[info] Missing modules: {', '.join(sorted(missing)) if missing else 'None'}"
    logs.extend([msg1, msg2]); g_log(msg1); g_log(msg2)
    patched = lines[:]
    if top_helper:
        patched = _insert_top_helper(patched)
        m = "[info] Inserted optional_imports helper at top"
        logs.append(m); g_log(m)
    skip = {s.strip() for s in (skip_csv or "").split(",") if s.strip()}
    patched = _patch_inline(
        patched, imps, missing=missing,
        wrap_all=wrap_all, include_stdlib=include_stdlib,
        skip=skip, log=logs
    )
    if dry_run:
        preview = "".join(patched)
        m = "[dry-run] preview generated (not written)."
        logs.append(m); g_log(m)
        return 0, "\n".join(logs), preview
    if write_mode == "rewrite":
        dest = path
    else:
        dest = output or path.replace(".py", "_patched.py")
    try:
        _write_lines(dest, patched)
    except Exception as e:
        msg = f"[error] Failed to write destination: {e}"
        g_log(msg)
        return 2, msg, ""
    m = f"[ok] Patched file written: {dest}"
    logs.append(m); g_log(m)
    return 0, "\n".join(logs), ""
def scan_imports(path: str) -> Dict[str, Any]:
    lines = _read_lines(path)
    tree = _parse_tree(lines, path)
    imps = _collect_imports(tree)
    per_mod: Dict[str, Dict[str, Any]] = {}
    for kind, mod, feat, asname, lineno, level in imps:
        if level and level > 0:
            continue
        if not mod:
            continue
        d = per_mod.setdefault(mod, {
            "module": mod,
            "kinds": set(),
            "occurrences": 0,
            "lines": [],
            "stdlib": _is_stdlib(mod),
            "present": None,
        })
        d["kinds"].add(kind)
        d["occurrences"] += 1
        d["lines"].append(lineno)
    modules = set(per_mod.keys())
    missing = _find_missing(modules)
    for m in per_mod.values():
        m["present"] = (m["module"] not in missing)
        m["kinds"] = ",".join(sorted(m["kinds"]))
        m["lines"] = ",".join(str(x) for x in sorted(m["lines"]))
    total_entries = len([x for x in imps if x[5] == 0 and x[1]])
    unique_modules = len(modules)
    stdlib = sum(1 for v in per_mod.values() if v["stdlib"])
    third_party = unique_modules - stdlib
    present = sum(1 for v in per_mod.values() if v["present"])
    missing_cnt = unique_modules - present
    stats = {
        "total_import_entries": total_entries,
        "unique_modules": unique_modules,
        "stdlib_modules": stdlib,
        "third_party_modules": third_party,
        "present_modules": present,
        "missing_modules": missing_cnt,
        "missing_names": sorted(missing),
    }
    g_log(f"[scan] total_entries={total_entries}, unique={unique_modules}, "
          f"stdlib={stdlib}, third_party={third_party}, present={present}, missing={missing_cnt}")
    rows = sorted(per_mod.values(), key=lambda r: (not r["present"], r["module"].lower()))
    return {"stats": stats, "rows": rows}
class PatcherGUI(ttk.Frame):
    def __init__(self, master: tk.Tk):
        super().__init__(master)
        self.master = master
        self._build_vars()
        self._build_ui()
        self._layout()
        self._wire()
    def _build_vars(self):
        self.file_var = tk.StringVar()
        self.dry_run_var = tk.BooleanVar(value=True)
        self.wrap_all_var = tk.BooleanVar(value=False)
        self.include_stdlib_var = tk.BooleanVar(value=False)
        self.top_helper_var = tk.BooleanVar(value=True)
        self.skip_var = tk.StringVar(value="")
        self.write_mode_var = tk.StringVar(value="create")
        self.output_var = tk.StringVar()
        self._worker: Optional[threading.Thread] = None
        self.scan_rows: List[Dict[str, Any]] = []
        self.scan_stats: Dict[str, Any] = {}
    def _build_ui(self):
        self.grid(row=0, column=0, sticky="nsew")
        self.master.rowconfigure(0, weight=1)
        self.master.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.nb = ttk.Notebook(self)
        self.nb.grid(row=0, column=0, sticky="nsew", padx=6, pady=(6,0))
        self.rowconfigure(0, weight=1)
        self.tab_src = ttk.Frame(self.nb)
        self.tab_scan = ttk.Frame(self.nb)
        self.tab_log = ttk.Frame(self.nb)
        self.tab_prev = ttk.Frame(self.nb)
        self.nb.add(self.tab_src, text="Source & Options")
        self.nb.add(self.tab_scan, text="Scan")
        self.nb.add(self.tab_log, text="Log")
        self.nb.add(self.tab_prev, text="Preview")
        self.footer = ttk.Frame(self)
        self.footer.grid(row=1, column=0, sticky="ew", padx=6, pady=6)
        self.columnconfigure(0, weight=1)
        self.btn_scan = ttk.Button(self.footer, text="Scan Only", command=self._on_scan)
        self.btn_run = ttk.Button(self.footer, text="Run Patch", command=self._on_run)
        self.btn_stop = ttk.Button(self.footer, text="Stop", command=self._on_stop, state="disabled")
        self.btn_clear = ttk.Button(self.footer, text="Clear", command=self._on_clear)
        self.footer.columnconfigure(0, weight=1)
        ttk.Label(self.footer, text="").grid(row=0, column=0, sticky="ew")  # spacer
        self.btn_scan.grid(row=0, column=1, padx=4)
        self.btn_run.grid(row=0, column=2, padx=4)
        self.btn_stop.grid(row=0, column=3, padx=4)
        self.btn_clear.grid(row=0, column=4, padx=4)
        for w in (self.tab_src, self.tab_scan, self.tab_log, self.tab_prev):
            w.rowconfigure(0, weight=0)
            w.rowconfigure(1, weight=1)
            w.columnconfigure(0, weight=1)
        self.src_file_row = ttk.Frame(self.tab_src)
        self.src_file_row.grid(row=0, column=0, sticky="ew", padx=10, pady=(10,6))
        self.src_file_row.columnconfigure(1, weight=1)
        ttk.Label(self.src_file_row, text="Python file:").grid(row=0, column=0, sticky="w")
        self.file_entry = ttk.Entry(self.src_file_row, textvariable=self.file_var)
        self.file_entry.grid(row=0, column=1, sticky="ew", padx=(6,0))
        self.file_btn = ttk.Button(self.src_file_row, text="Browse…", command=self._choose_file)
        self.file_btn.grid(row=0, column=2, padx=(6,0))
        self.opts = ttk.LabelFrame(self.tab_src, text="Options")
        self.opts.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0,10))
        for i in range(3):
            self.opts.columnconfigure(i, weight=1)
        self.dry_chk = ttk.Checkbutton(self.opts, text="Dry-run (show preview only)", variable=self.dry_run_var, command=self._on_toggle_dry)
        self.wrap_all_chk = ttk.Checkbutton(self.opts, text="Wrap all imports (not just missing)", variable=self.wrap_all_var)
        self.include_stdlib_chk = ttk.Checkbutton(self.opts, text="Allow stdlib wrapping", variable=self.include_stdlib_var)
        self.top_helper_chk = ttk.Checkbutton(self.opts, text="Insert optional_imports helper at top", variable=self.top_helper_var)
        self.dry_chk.grid(row=0, column=0, sticky="w", padx=8, pady=4)
        self.wrap_all_chk.grid(row=0, column=1, sticky="w", padx=8, pady=4)
        self.include_stdlib_chk.grid(row=0, column=2, sticky="w", padx=8, pady=4)
        self.top_helper_chk.grid(row=1, column=0, sticky="w", padx=8, pady=4)
        self.skip_row = ttk.Frame(self.opts)
        self.skip_row.grid(row=2, column=0, columnspan=3, sticky="ew", padx=8, pady=(6,4))
        self.skip_row.columnconfigure(1, weight=1)
        ttk.Label(self.skip_row, text="Skip modules (comma-separated):").grid(row=0, column=0, sticky="w")
        self.skip_entry = ttk.Entry(self.skip_row, textvariable=self.skip_var)
        self.skip_entry.grid(row=0, column=1, sticky="ew", padx=(6,0))
        self.write_group = ttk.LabelFrame(self.tab_src, text="Write Mode")
        self.write_group.grid(row=2, column=0, sticky="ew", padx=10, pady=(0,10))
        self.write_group.columnconfigure(1, weight=1)
        self.write_create = ttk.Radiobutton(self.write_group, text="Create patch file", value="create", variable=self.write_mode_var, command=self._on_write_mode)
        self.write_rewrite = ttk.Radiobutton(self.write_group, text="Rewrite existing (in-place)", value="rewrite", variable=self.write_mode_var, command=self._on_write_mode)
        self.write_create.grid(row=0, column=0, sticky="w", padx=8, pady=4)
        self.write_rewrite.grid(row=0, column=1, sticky="w", padx=8, pady=4)
        self.out_row = ttk.Frame(self.write_group)
        self.out_row.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(4,8))
        self.out_row.columnconfigure(1, weight=1)
        ttk.Label(self.out_row, text="Output path (optional):").grid(row=0, column=0, sticky="w")
        self.out_entry = ttk.Entry(self.out_row, textvariable=self.output_var)
        self.out_entry.grid(row=0, column=1, sticky="ew", padx=(6,0))
        self.out_btn = ttk.Button(self.out_row, text="Select…", command=self._choose_output)
        self.out_btn.grid(row=0, column=2, padx=(6,0))
        self.scan_summary = ttk.LabelFrame(self.tab_scan, text="Summary")
        self.scan_summary.grid(row=0, column=0, sticky="ew", padx=10, pady=(10,6))
        for i in range(6):
            self.scan_summary.columnconfigure(i, weight=1)
        self.sum_total = ttk.Label(self.scan_summary, text="Import entries: 0")
        self.sum_unique = ttk.Label(self.scan_summary, text="Unique modules: 0")
        self.sum_stdlib = ttk.Label(self.scan_summary, text="Stdlib: 0")
        self.sum_3p = ttk.Label(self.scan_summary, text="Third-party: 0")
        self.sum_present = ttk.Label(self.scan_summary, text="Present: 0")
        self.sum_missing = ttk.Label(self.scan_summary, text="Missing: 0")
        for idx, w in enumerate((self.sum_total, self.sum_unique, self.sum_stdlib, self.sum_3p, self.sum_present, self.sum_missing)):
            w.grid(row=0, column=idx, sticky="w", padx=8, pady=4)
        self.scan_table = ttk.Treeview(self.tab_scan, columns=("module","kinds","occ","stdlib","present","lines"), show="headings")
        self.scan_table.heading("module", text="Module")
        self.scan_table.heading("kinds", text="Kinds")
        self.scan_table.heading("occ", text="Occur.")
        self.scan_table.heading("stdlib", text="Stdlib")
        self.scan_table.heading("present", text="Present")
        self.scan_table.heading("lines", text="Lines")
        self.scan_table.column("module", width=180, anchor="w")
        self.scan_table.column("kinds", width=100, anchor="center")
        self.scan_table.column("occ", width=70, anchor="e")
        self.scan_table.column("stdlib", width=70, anchor="center")
        self.scan_table.column("present", width=70, anchor="center")
        self.scan_table.column("lines", width=200, anchor="w")
        self.scan_y = ttk.Scrollbar(self.tab_scan, orient="vertical", command=self.scan_table.yview)
        self.scan_table.configure(yscrollcommand=self.scan_y.set)
        self.tab_scan.columnconfigure(0, weight=1)
        self.tab_scan.columnconfigure(1, weight=0)
        self.tab_scan.rowconfigure(1, weight=1)
        self.scan_table.grid(row=1, column=0, sticky="nsew", padx=(10,0), pady=(0,10))
        self.scan_y.grid(row=1, column=1, sticky="ns", padx=(0,10), pady=(0,10))
        self.scan_actions = ttk.Frame(self.tab_scan)
        self.scan_actions.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(0,10))
        self.scan_actions.columnconfigure(0, weight=1)
        self.copy_report_btn = ttk.Button(self.scan_actions, text="Copy Report", command=self._copy_scan_report)
        self.export_csv_btn = ttk.Button(self.scan_actions, text="Export CSV…", command=self._export_scan_csv)
        ttk.Label(self.scan_actions, text="").grid(row=0, column=0, sticky="ew")  # spacer
        self.copy_report_btn.grid(row=0, column=1, padx=4, sticky="e")
        self.export_csv_btn.grid(row=0, column=2, padx=4, sticky="e")
        self.log_text = tk.Text(self.tab_log, wrap="word")
        self.log_scroll = ttk.Scrollbar(self.tab_log, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=self.log_scroll.set)
        self.tab_log.columnconfigure(0, weight=1)
        self.tab_log.columnconfigure(1, weight=0)
        self.tab_log.rowconfigure(1, weight=1)
        ttk.Label(self.tab_log, text="Global Log (persists across scans/runs)").grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(10,4))
        self.log_text.grid(row=1, column=0, sticky="nsew", padx=(10,0), pady=(0,10))
        self.log_scroll.grid(row=1, column=1, sticky="ns", padx=(0,10), pady=(0,10))
        set_log_sink(self.log_text)
        self.prev_text = tk.Text(self.tab_prev, wrap="none")
        self.prev_y = ttk.Scrollbar(self.tab_prev, orient="vertical", command=self.prev_text.yview)
        self.prev_x = ttk.Scrollbar(self.tab_prev, orient="horizontal", command=self.prev_text.xview)
        self.prev_text.configure(yscrollcommand=self.prev_y.set, xscrollcommand=self.prev_x.set)
        self.tab_prev.columnconfigure(0, weight=1)
        self.tab_prev.columnconfigure(1, weight=0)
        self.tab_prev.rowconfigure(0, weight=1)
        self.prev_text.grid(row=0, column=0, sticky="nsew", padx=(10,0), pady=(10,0))
        self.prev_y.grid(row=0, column=1, sticky="ns", padx=(0,10), pady=(10,0))
        self.prev_x.grid(row=1, column=0, sticky="ew", padx=10, pady=(0,10))
        self.status = ttk.Label(self, text="Ready.", anchor="w")
        self.status.grid(row=2, column=0, sticky="ew", padx=6, pady=(0,6))
        self._on_toggle_dry()
    def _layout(self):
        pass  # layout is fully grid-based above (sticky sidebars retained on resize)
    def _wire(self):
        self.master.bind("<Escape>", lambda e: self.master.quit())
    def _choose_file(self):
        path = filedialog.askopenfilename(
            title="Select Python file",
            filetypes=[("Python files", "*.py"), ("All files", "*.*")]
        )
        if path:
            self.file_var.set(path)
    def _choose_output(self):
        path = filedialog.asksaveasfilename(
            title="Choose output file",
            defaultextension=".py",
            filetypes=[("Python files", "*.py"), ("All files", "*.*")]
        )
        if path:
            self.output_var.set(path)
    def _set_status(self, text: str):
        self.status.config(text=text)
        self.status.update_idletasks()
    def _append_log(self, text: str):
        g_log_block(text)
    def _on_clear(self):
        self.prev_text.delete("1.0", "end")
        for i in self.scan_table.get_children():
            self.scan_table.delete(i)
        self._update_scan_summary({})
        LOG_BUFFER.clear()
        if LOG_SINK_WIDGET is not None:
            LOG_SINK_WIDGET.delete("1.0", "end")
        self._set_status("Cleared.")
    def _on_stop(self):
        self.btn_stop.config(state="disabled")
        self._set_status("Stop requested (will apply after current action).")
        g_log("[ui] stop requested")
    def _toggle_running(self, running: bool):
        state_on = "normal" if not running else "disabled"
        self.btn_run.config(state=("disabled" if running else "normal"))
        self.btn_scan.config(state=("disabled" if running else "normal"))
        self.btn_stop.config(state=("normal" if running else "disabled"))
        self.file_btn.config(state=state_on)
    def _on_toggle_dry(self):
        is_dry = self.dry_run_var.get()
        for child in self.write_group.winfo_children():
            try:
                child.configure(state=("disabled" if is_dry else "normal"))
            except Exception:
                pass
        self._on_write_mode()
    def _on_write_mode(self):
        is_dry = self.dry_run_var.get()
        create_selected = (self.write_mode_var.get() == "create")
        target_state = ("normal" if (create_selected and not is_dry) else "disabled")
        for w in (self.out_entry, self.out_btn):
            try:
                w.configure(state=target_state)
            except Exception:
                pass
    def _on_scan(self):
        file_path = self.file_var.get().strip()
        if not file_path:
            messagebox.showwarning("Missing file", "Select a Python file to scan.")
            return
        self._set_status("Scanning…")
        self._toggle_running(True)
        self.nb.select(self.tab_scan)
        def work():
            try:
                data = scan_imports(file_path)
                self.scan_rows = data["rows"]
                self.scan_stats = data["stats"]
                self.master.after(0, self._populate_scan_tab)
                self.master.after(0, self._set_status, "Scan complete.")
            except Exception as e:
                err = str(e)
                self.master.after(0, self._set_status, "Scan failed.")
                self.master.after(0, lambda msg=err: messagebox.showerror("Scan error", msg))
            finally:
                self.master.after(0, self._toggle_running, False)
        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()
    def _populate_scan_tab(self):
        for i in self.scan_table.get_children():
            self.scan_table.delete(i)
        for row in self.scan_rows:
            self.scan_table.insert("", "end", values=(
                row["module"],
                row["kinds"],
                row["occurrences"],
                "Yes" if row["stdlib"] else "No",
                "Yes" if row["present"] else "No",
                row["lines"],
            ))
        self._update_scan_summary(self.scan_stats)
    def _update_scan_summary(self, stats: Dict[str, Any]):
        s = stats or {}
        self.sum_total.config(text=f"Import entries: {s.get('total_import_entries', 0)}")
        self.sum_unique.config(text=f"Unique modules: {s.get('unique_modules', 0)}")
        self.sum_stdlib.config(text=f"Stdlib: {s.get('stdlib_modules', 0)}")
        self.sum_3p.config(text=f"Third-party: {s.get('third_party_modules', 0)}")
        self.sum_present.config(text=f"Present: {s.get('present_modules', 0)}")
        self.sum_missing.config(text=f"Missing: {s.get('missing_modules', 0)}")
    def _copy_scan_report(self):
        if not self.scan_stats:
            messagebox.showinfo("No data", "Run Scan first.")
            return
        s = self.scan_stats
        missing = ", ".join(s.get("missing_names", [])) or "None"
        buf = [
            "=== Import Scan Report ===",
            f"Import entries: {s['total_import_entries']}",
            f"Unique modules: {s['unique_modules']}",
            f"Stdlib: {s['stdlib_modules']}    Third-party: {s['third_party_modules']}",
            f"Present: {s['present_modules']}   Missing: {s['missing_modules']}",
            f"Missing modules: {missing}",
            "",
            "Module, Kinds, Occurrences, Stdlib, Present, Lines",
        ]
        for r in self.scan_rows:
            buf.append(f"{r['module']}, {r['kinds']}, {r['occurrences']}, "
                       f"{'Yes' if r['stdlib'] else 'No'}, "
                       f"{'Yes' if r['present'] else 'No'}, {r['lines']}")
        try:
            self.master.clipboard_clear()
            self.master.clipboard_append("\n".join(buf))
            self.master.update()
            messagebox.showinfo("Copied", "Scan report copied to clipboard.")
        except Exception as e:
            messagebox.showerror("Clipboard error", str(e))
    def _export_scan_csv(self):
        if not self.scan_rows:
            messagebox.showinfo("No data", "Run Scan first.")
            return
        path = filedialog.asksaveasfilename(
            title="Export Scan CSV",
            defaultextension=".csv",
            filetypes=[("CSV files","*.csv"), ("All files","*.*")]
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["module","kinds","occurrences","stdlib","present","lines"])
                for r in self.scan_rows:
                    w.writerow([r["module"], r["kinds"], r["occurrences"],
                                "yes" if r["stdlib"] else "no",
                                "yes" if r["present"] else "no",
                                r["lines"]])
            messagebox.showinfo("Exported", f"CSV written to:\n{path}")
        except Exception as e:
            messagebox.showerror("Export error", str(e))
    def _on_run(self):
        file_path = self.file_var.get().strip()
        if not file_path:
            messagebox.showwarning("Missing file", "Select a Python file to patch.")
            return
        dry_run = self.dry_run_var.get()
        wrap_all = self.wrap_all_var.get()
        include_stdlib = self.include_stdlib_var.get()
        top_helper = self.top_helper_var.get()
        skip_csv = self.skip_var.get().strip()
        write_mode = self.write_mode_var.get()
        output = self.output_var.get().strip() or None
        self.prev_text.delete("1.0", "end")
        self.nb.select(self.tab_log)
        self._set_status("Running…")
        self._toggle_running(True)
        def work():
            try:
                code, log_text, preview = patch_file(
                    path=file_path,
                    write_mode=write_mode,
                    output=output,
                    dry_run=dry_run,
                    wrap_all=wrap_all,
                    include_stdlib=include_stdlib,
                    top_helper=top_helper,
                    skip_csv=skip_csv,
                )
                self.master.after(0, self._append_log, log_text)
                if dry_run and preview:
                    self.master.after(0, self.prev_text.insert, "end", preview)
                    self.master.after(0, self.nb.select, self.tab_prev)
                try:
                    data = scan_imports(file_path)
                    self.scan_rows = data["rows"]
                    self.scan_stats = data["stats"]
                    self.master.after(0, self._populate_scan_tab)
                except Exception:
                    pass
                if code == 0:
                    self.master.after(0, self._set_status, "Done.")
                    if not dry_run:
                        self.master.after(0, lambda: messagebox.showinfo("Success", "Patching complete."))
                else:
                    self.master.after(0, self._set_status, "Failed.")
                    self.master.after(0, lambda: messagebox.showerror("Error", log_text.splitlines()[-1] if log_text else "Unknown error"))
            finally:
                self.master.after(0, self._toggle_running, False)
        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()
def main():
    root = tk.Tk()
    root.title("Safe Import Patcher — GUI (Notebook)")
    root.geometry("666x400")
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", padding=3)
        style.configure("TLabel", padding=2)
        style.configure("TButton", padding=4)
        style.configure("TCheckbutton", padding=2)
        style.configure("TRadiobutton", padding=2)
        style.configure("TLabelframe", padding=6)
    except Exception:
        pass
    root.rowconfigure(0, weight=1)
    root.columnconfigure(0, weight=1)
    app = PatcherGUI(root)
    root.mainloop()
if __name__ == "__main__":
    main()
