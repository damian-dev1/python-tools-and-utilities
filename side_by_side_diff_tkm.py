import os
import re
import tkinter as tk
from tkinter import filedialog, ttk
import difflib
MIDNIGHT_THEME = {
    "bg_main": "#0f0f10",
    "bg_entry": "#1b1c20",
    "bg_output": "#1a1b1f",
    "fg_text": "#f8f8f2",
    "fg_entry": "#8be9fd",
    "fg_label": "#bd93f9",
    "btn_browse_bg": "#282a36",
    "btn_browse_fg": "#ff79c6",
    "btn_refresh_bg": "#44475a",
    "btn_refresh_fg": "#50fa7b",
    "btn_copy_bg": "#6272a4",
    "btn_copy_fg": "#f8f8f2",
    "diff_bg": "#D44545",
}
RE_PREFIX = re.compile(r'^\s*\d+\s*:\s')
LIGHT = dict(bg="SystemWindow", fg="SystemWindowText", insert="black")
DARK = dict(bg=MIDNIGHT_THEME["bg_output"], fg=MIDNIGHT_THEME["fg_text"], insert=MIDNIGHT_THEME["fg_text"])

class SideBySideDiffApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.theme = MIDNIGHT_THEME
        root.title("Side-by-Side Diff Viewer")
        root.configure(bg=self.theme["bg_main"])
        self.style = ttk.Style(root)
        self.root.geometry("600x600")
        self.root.minsize(540, 240)
        self._req_thread = None
        self.style.theme_use('clam')
        self.style.configure('TButton', background=self.theme["btn_copy_bg"], foreground=self.theme["btn_copy_fg"])
        self.style.map('TButton', background=[('active', self.theme["btn_copy_bg"])], foreground=[('active', self.theme["btn_copy_fg"])])
        top = tk.Frame(root, bg=self.theme["bg_main"])
        top.pack(fill="x", padx=6, pady=6)
        tk.Button(top, text="Load Left File", command=self.load_left,
                  bg=self.theme["btn_refresh_bg"], fg=self.theme["btn_refresh_fg"],
                  activebackground=self.theme["btn_refresh_bg"], activeforeground=self.theme["btn_refresh_fg"],
                  relief=tk.FLAT, bd=1).pack(side="left")
        tk.Button(top, text="Copy Left", command=self.copy_left,
                  bg=self.theme["btn_refresh_bg"], fg=self.theme["btn_refresh_fg"],
                  activebackground=self.theme["btn_refresh_bg"], activeforeground=self.theme["btn_refresh_fg"],
                  relief=tk.FLAT, bd=1).pack(side="left", padx=(6, 0))
        tk.Button(top, text="Compare", command=self.compare,
                  bg=self.theme["btn_copy_bg"], fg=self.theme["btn_copy_fg"],
                  activebackground=self.theme["btn_copy_bg"], activeforeground=self.theme["btn_copy_fg"],
                  relief=tk.FLAT, bd=1).pack(side="left", padx=(12, 0))
        tk.Button(top, text="Reset", command=self.reset,
                  bg=self.theme["btn_copy_bg"], fg=self.theme["btn_copy_fg"],
                  activebackground=self.theme["btn_copy_bg"], activeforeground=self.theme["btn_copy_fg"],
                  relief=tk.FLAT, bd=1).pack(side="left", padx=(6, 0))
        self.dark_enabled = True
        tk.Button(top, text="Light Mode", command=self.toggle_theme,
                  bg=self.theme["btn_browse_bg"], fg=self.theme["btn_browse_fg"],
                  activebackground=self.theme["btn_browse_bg"], activeforeground=self.theme["btn_browse_fg"],
                  relief=tk.FLAT, bd=1).pack(side="right", padx=(12, 0))
        tk.Button(top, text="Load Right File", command=self.load_right,
                  bg=self.theme["btn_browse_bg"], fg=self.theme["btn_browse_fg"],
                  activebackground=self.theme["btn_browse_bg"], activeforeground=self.theme["btn_browse_fg"],
                  relief=tk.FLAT, bd=1).pack(side="right")
        tk.Button(top, text="Copy Right", command=self.copy_right,
                  bg=self.theme["btn_browse_bg"], fg=self.theme["btn_browse_fg"],
                  activebackground=self.theme["btn_browse_bg"], activeforeground=self.theme["btn_browse_fg"],
                  relief=tk.FLAT, bd=1).pack(side="right", padx=(0, 6))
        panel = tk.Frame(root, bg=self.theme["bg_main"])
        panel.pack(fill="both", expand=True)
        self.left_text = tk.Text(panel, wrap="none", width=60, undo=False,
                                 bg=DARK["bg"], fg=DARK["fg"], insertbackground=DARK["insert"],
                                 selectbackground=self.theme["btn_copy_bg"])
        self.right_text = tk.Text(panel, wrap="none", width=60, undo=False,
                                  bg=DARK["bg"], fg=DARK["fg"], insertbackground=DARK["insert"],
                                  selectbackground=self.theme["btn_copy_bg"])
        self.left_scroll = ttk.Scrollbar(panel, orient="vertical")
        self.right_scroll = ttk.Scrollbar(panel, orient="vertical")
        self.style.configure('Vertical.TScrollbar', background=self.theme["bg_entry"], troughcolor=self.theme["bg_output"], arrowcolor=self.theme["fg_text"])
        self.left_text.grid(row=0, column=0, sticky="nsew")
        self.left_scroll.grid(row=0, column=1, sticky="ns")
        self.right_text.grid(row=0, column=2, sticky="nsew")
        self.right_scroll.grid(row=0, column=3, sticky="ns")
        panel.columnconfigure(0, weight=1)
        panel.columnconfigure(2, weight=1)
        panel.rowconfigure(0, weight=1)
        self.left_scroll.config(command=self._scroll_both)
        self.right_scroll.config(command=self._scroll_both)
        self.left_text.config(yscrollcommand=self._sync_scrollbars_left)
        self.right_text.config(yscrollcommand=self._sync_scrollbars_right)
        for w in (self.left_text, self.right_text):
            w.tag_configure("diff", background=self.theme["diff_bg"])
        bottom = tk.Frame(root, bg=self.theme["bg_main"])
        bottom.pack(fill="x", padx=6, pady=6)
        self.left_stats = tk.Label(bottom, text="Left: 0 lines", fg=self.theme["fg_text"], bg=self.theme["bg_main"])
        self.left_stats.pack(side="left", padx=(0, 12))
        self.right_stats = tk.Label(bottom, text="Right: 0 lines", fg=self.theme["fg_text"], bg=self.theme["bg_main"])
        self.right_stats.pack(side="left", padx=(0, 12))
        self.diff_stats = tk.Label(bottom, text="Diff: 0", fg=self.theme["fg_text"], bg=self.theme["bg_main"])
        self.diff_stats.pack(side="left")
        self.left_path = None
        self.right_path = None
        self.left_raw = ""
        self.right_raw = ""
        self.apply_theme(DARK)
    def apply_theme(self, theme: dict):
        for w in (self.left_text, self.right_text):
            w.configure(background=theme["bg"], foreground=theme["fg"], insertbackground=theme["insert"])
    def toggle_theme(self):
        self.dark_enabled = not self.dark_enabled
        new_theme = DARK if self.dark_enabled else LIGHT
        self.apply_theme(new_theme)
        if self.dark_enabled:
            self.root.children['!frame'].children['!button5'].config(text="Light Mode")
        else:
            self.root.children['!frame'].children['!button5'].config(text="Dark Mode")
    def _scroll_both(self, *args):
        self.left_text.yview(*args)
        self.right_text.yview(*args)
    def _sync_scrollbars_left(self, first, last):
        self.left_scroll.set(first, last)
        self.right_scroll.set(first, last)
    def _sync_scrollbars_right(self, first, last):
        self.left_scroll.set(first, last)
        self.right_scroll.set(first, last)
    @staticmethod
    def _strip_prefixes(lines):
        return [RE_PREFIX.sub("", ln) for ln in lines]
    def _clear_tags_and_text(self):
        for w in (self.left_text, self.right_text):
            w.delete("1.0", "end")
            w.tag_remove("diff", "1.0", "end")
    def load_left(self):
        path = filedialog.askopenfilename()
        if not path:
            return
        self.left_path = path
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            self.left_raw = f.read()
        self.left_text.delete("1.0", "end")
        self.left_text.insert("1.0", self.left_raw)
        self.left_stats.config(text=f"Left: {self.left_raw.count(chr(10)) + 1} lines")
    def load_right(self):
        path = filedialog.askopenfilename()
        if not path:
            return
        self.right_path = path
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            self.right_raw = f.read()
        self.right_text.delete("1.0", "end")
        self.right_text.insert("1.0", self.right_raw)
        self.right_stats.config(text=f"Right: {self.right_raw.count(chr(10)) + 1} lines")
    def compare(self):
        left_src = self.left_raw if self.left_raw else "\n".join(
            self._strip_prefixes(self.left_text.get("1.0", "end-1c").splitlines()))
        right_src = self.right_raw if self.right_raw else "\n".join(
            self._strip_prefixes(self.right_text.get("1.0", "end-1c").splitlines()))
        left_lines = left_src.splitlines()
        right_lines = right_src.splitlines()
        sm = difflib.SequenceMatcher(None, left_lines, right_lines)
        self._clear_tags_and_text()
        diffs = 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    ln = i1 + k + 1
                    rn = j1 + k + 1
                    self._insert_line(self.left_text, ln, left_lines[i1 + k], highlight=False)
                    self._insert_line(self.right_text, rn, right_lines[j1 + k], highlight=False)
            else:
                max_len = max(i2 - i1, j2 - j1)
                for k in range(max_len):
                    l_has = k < (i2 - i1)
                    r_has = k < (j2 - j1)
                    if l_has:
                        ln = i1 + k + 1
                        self._insert_line(self.left_text, ln, left_lines[i1 + k], highlight=True)
                        diffs += 1
                    else:
                        self._insert_line(self.left_text, "", "", highlight=True)
                    if r_has:
                        rn = j1 + k + 1
                        self._insert_line(self.right_text, rn, right_lines[j1 + k], highlight=True)
                    else:
                        self._insert_line(self.right_text, "", "", highlight=True)
        self.left_stats.config(text=f"Left: {len(left_lines)} lines")
        self.right_stats.config(text=f"Right: {len(right_lines)} lines")
        self.diff_stats.config(text=f"Diff: {diffs}")
    def _insert_line(self, widget: tk.Text, line_num, line_text, highlight=False):
        prefix = f"{line_num:4d}: " if line_num else "    : "
        start = widget.index("end-1c")
        widget.insert("end", prefix + line_text + "\n")
        end = widget.index("end-1c")
        if highlight:
            widget.tag_add("diff", start, end)
    def reset(self):
        self._clear_tags_and_text()
        self.left_stats.config(text="Left: 0 lines")
        self.right_stats.config(text="Right: 0 lines")
        self.diff_stats.config(text="Diff: 0")
        self.left_path = None
        self.right_path = None
        self.left_raw = ""
        self.right_raw = ""
    def copy_left(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.left_text.get("1.0", "end-1c"))
        self.root.update()
    def copy_right(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.right_text.get("1.0", "end-1c"))
        self.root.update()
if __name__ == "__main__":
    root = tk.Tk()
    app = SideBySideDiffApp(root)
    root.mainloop()
