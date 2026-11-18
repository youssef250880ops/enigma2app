# -*- coding: utf-8 -*-
"""
Enigma2 Portable - télécharge les listes depuis des sites, filtre par satellite
et qualité (4K/HD/SD), exporte lamedb + bouquets.

Placer à côté : enigma2_sat_tool.py (moteur). Les sources sont stockées dans
sources.json à côté de l'exécutable (application portable, aucune installation).
"""
import os, re, sys, json, threading, urllib.request
from urllib.parse import urljoin
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from enigma2_sat_tool import App as BaseApp

APP_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
SRC_FILE = os.path.join(APP_DIR, "sources.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# Exemples vides : ajoutez les adresses des sites que vous utilisez.
DEFAULT_SOURCES = [
    {"name": "Exemple - lien ZIP direct", "type": "zip", "url": "https://exemple.com/settings.zip"},
    {"name": "Exemple - page de téléchargement", "type": "page", "url": "https://exemple.com/settings/"},
]


def load_sources():
    try:
        with open(SRC_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        save_sources(DEFAULT_SOURCES)
        return list(DEFAULT_SOURCES)


def save_sources(lst):
    with open(SRC_FILE, "w", encoding="utf-8") as f:
        json.dump(lst, f, ensure_ascii=False, indent=2)


def http_get(url, timeout=60):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout)


def scan_zip_links(page_url):
    with http_get(page_url) as r:
        html = r.read().decode("utf-8", errors="replace")
    links = re.findall(r'href=["\']([^"\']+?\.zip[^"\']*)["\']', html, flags=re.I)
    seen, out = set(), []
    for l in links:
        full = urljoin(page_url, l)
        if full not in seen:
            seen.add(full)
            out.append(full)
    return out


class Portable(BaseApp):
    def __init__(self):
        super().__init__()
        self.title("Enigma2 Portable - Téléchargement des listes")
        self.sources = load_sources()

        fr = ttk.LabelFrame(self, text="0. Sources en ligne (sites qui génèrent les listes)")
        fr.pack(fill="x", padx=8, pady=6, before=self.winfo_children()[0])
        self.cb = ttk.Combobox(fr, state="readonly", width=50)
        self.cb.pack(side="left", padx=4, pady=4)
        ttk.Button(fr, text="Télécharger", command=self.fetch).pack(side="left", padx=2)
        ttk.Button(fr, text="Ajouter", command=self.add_source).pack(side="left", padx=2)
        ttk.Button(fr, text="Supprimer", command=self.del_source).pack(side="left", padx=2)
        self.pb = ttk.Progressbar(fr, length=150)
        self.pb.pack(side="left", padx=6)
        self.refresh_sources()

    # ---- sources
    def refresh_sources(self):
        self.cb["values"] = [s["name"] for s in self.sources]
        if self.sources:
            self.cb.current(0)

    def add_source(self):
        name = simpledialog.askstring("Source", "Nom de la source :", parent=self)
        if not name:
            return
        url = simpledialog.askstring("Source", "Adresse (URL) :", parent=self)
        if not url:
            return
        typ = "zip" if url.lower().split("?")[0].endswith(".zip") else "page"
        self.sources.append({"name": name, "type": typ, "url": url.strip()})
        save_sources(self.sources)
        self.refresh_sources()
        self.cb.current(len(self.sources) - 1)

    def del_source(self):
        i = self.cb.current()
        if i >= 0 and messagebox.askyesno("Supprimer", f"Supprimer « {self.sources[i]['name']} » ?"):
            del self.sources[i]
            save_sources(self.sources)
            self.refresh_sources()

    # ---- téléchargement
    def fetch(self):
        i = self.cb.current()
        if i < 0:
            return messagebox.showinfo("Info", "Ajoutez d'abord une source.")
        s = self.sources[i]
        threading.Thread(target=self._fetch, args=(s,), daemon=True).start()

    def _fetch(self, s):
        try:
            url = s["url"]
            if s["type"] == "page":
                self.after(0, self.log, f"Analyse de la page : {url}")
                links = scan_zip_links(url)
                if not links:
                    raise RuntimeError("Aucun fichier ZIP trouvé sur cette page.")
                url = self.ask_link(links)
                if not url:
                    return
            self.after(0, self.log, f"Téléchargement : {url}")
            dest = os.path.join(self.tmp, "dl.zip")
            with http_get(url) as r, open(dest, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        self.after(0, self.pb.configure, {"value": done * 100 / total})
            self.after(0, self.pb.configure, {"value": 100})
            self.after(0, self.src.set, dest)
            self._load()  # extrait + détecte les satellites
        except Exception as e:
            self.after(0, messagebox.showerror, "Erreur", f"{e}\n\nVérifiez l'URL et votre connexion.")

    def ask_link(self, links):
        res, ev = {}, threading.Event()

        def dlg():
            w = tk.Toplevel(self)
            w.title("Choisir le fichier à télécharger")
            lb = tk.Listbox(w, width=100, height=15)
            for l in links:
                lb.insert("end", l)
            lb.pack(padx=8, pady=8)
            lb.selection_set(0)

            def ok():
                if lb.curselection():
                    res["u"] = links[lb.curselection()[0]]
                w.destroy()
                ev.set()

            ttk.Button(w, text="Télécharger", command=ok).pack(pady=6)
            w.protocol("WM_DELETE_WINDOW", lambda: (w.destroy(), ev.set()))

        self.after(0, dlg)
        ev.wait()
        return res.get("u")


if __name__ == "__main__":
    Portable().mainloop()
