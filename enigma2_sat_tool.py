# -*- coding: utf-8 -*-
"""
Enigma2 Satellite Channel Tool
- Charge une liste Enigma2 (dossier, ZIP ou URL de ZIP)
- Détecte les satellites, permet de les cocher
- Filtre par qualité : 4K / HD / SD / Radio
- Exporte lamedb + bouquets (par satellite, par qualité, bouquets d'origine filtrés)
"""
import os, re, zipfile, tempfile, shutil, threading, urllib.request
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from collections import defaultdict

SAT_NAMES = {
    -300: "Hellas Sat/Intelsat 30.0W", -80: "Thor/Intelsat 0.8W",
    -50: "Eutelsat 5W (Atlantic Bird 3)", -70: "Nilesat/Eutelsat 7W",
    -40: "Amos 4.0W", 90: "Eutelsat 9E", 130: "Hotbird 13.0E",
    160: "Eutelsat 16E", 192: "Astra 19.2E", 235: "Astra 23.5E",
    260: "Badr 26.0E", 282: "Astra 28.2E", 305: "Arabsat 30.5E",
    360: "Eutelsat 36E", 420: "Turksat 42E", 530: "Express 53E",
}


def orbital_pos(ns):
    pos = (ns >> 16) & 0xFFFF
    if pos == 0xFFFF:
        return "CABLE"
    if pos == 0xEEEE:
        return "TERR"
    return pos - 3600 if pos > 1800 else pos


def sat_label(pos):
    if pos == "CABLE":
        return "Câble"
    if pos == "TERR":
        return "Terrestre"
    deg = f"{abs(pos) / 10:.1f}°{'E' if pos >= 0 else 'W'}"
    return f"{deg}  {SAT_NAMES.get(pos, '')}".strip()


# ---------------------------------------------------------------- lamedb
class Service:
    __slots__ = ("head", "name", "extra", "sid", "ns", "tsid", "onid",
                 "stype", "pos", "quality")


def classify(name, stype):
    n = name.upper()
    if stype in (2, 10):
        return "Radio"
    if stype == 31 or re.search(r"\b(4K|UHD)\b", n):
        return "4K"
    if stype in (17, 19, 25) or re.search(r"\bHD\b", n):
        return "HD"
    return "SD"


def parse_lamedb(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().replace("\r", "").split("\n")
    m = re.search(r"/(\d)/", lines[0])
    version = int(m.group(1)) if m else 4
    i = 0
    while lines[i].strip() != "transponders":
        i += 1
    i += 1
    transponders = {}
    while lines[i].strip() != "end":
        k = lines[i].strip().split(":")
        key = tuple(int(x, 16) for x in k[:3])
        i += 1
        data = []
        while lines[i].strip() != "/":
            data.append(lines[i])
            i += 1
        i += 1
        transponders[key] = data
    while lines[i].strip() != "services":
        i += 1
    i += 1
    services = []
    while i + 2 < len(lines) and lines[i].strip() != "end":
        head = lines[i].strip()
        f = head.split(":")
        s = Service()
        s.head, s.name, s.extra = head, lines[i + 1], lines[i + 2]
        s.sid, s.ns, s.tsid, s.onid = (int(x, 16) for x in f[:4])
        s.stype = int(f[4])
        s.pos = orbital_pos(s.ns)
        s.quality = classify(s.name, s.stype)
        services.append(s)
        i += 3
    return version, transponders, services


def find_lamedb(root):
    for d, _, files in os.walk(root):
        if "lamedb" in files:
            return d
    return None


# ---------------------------------------------------------------- export
def ref(s):
    return f"1:0:{s.stype:X}:{s.sid:X}:{s.tsid:X}:{s.onid:X}:{s.ns:X}:0:0:0:"


def safe(name):
    return re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_").lower() or "bq"


def export(src_dir, out_dir, version, transponders, services, sats, quals,
           per_sat, per_quality, keep_orig, log):
    chosen = [s for s in services if s.pos in sats and s.quality in quals]
    keep = {(s.sid, s.tsid, s.onid, s.ns) for s in chosen}
    used_tp = {(s.ns, s.tsid, s.onid) for s in chosen}
    os.makedirs(out_dir, exist_ok=True)

    with open(os.path.join(out_dir, "lamedb"), "w", encoding="utf-8", newline="\n") as f:
        f.write(f"eDVB services /{version}/\ntransponders\n")
        for key, data in transponders.items():
            if key in used_tp:
                f.write("%08x:%04x:%04x\n" % key)
                f.write("\n".join(data) + "\n/\n")
        f.write("end\nservices\n")
        for s in chosen:
            f.write(f"{s.head}\n{s.name}\n{s.extra}\n")
        f.write("end\nEditors only!\n")

    bouquets = []  # (filename, title)

    def write_bq(fname, title, refs):
        if not refs:
            return
        with open(os.path.join(out_dir, fname), "w", encoding="utf-8", newline="\n") as f:
            f.write(f"#NAME {title}\n")
            for r in refs:
                f.write(f"#SERVICE {r}\n")
        bouquets.append((fname, title))

    if per_quality:
        for q in ("4K", "HD", "SD"):
            if q in quals:
                write_bq(f"userbouquet.q_{q.lower()}.tv", f"Chaînes {q}",
                         [ref(s) for s in chosen if s.quality == q])
    if per_sat:
        for p in sorted(sats, key=lambda x: (isinstance(x, str), x)):
            for q in ("4K", "HD", "SD"):
                if q not in quals:
                    continue
                lst = [ref(s) for s in chosen if s.pos == p and s.quality == q]
                write_bq(f"userbouquet.sat_{safe(sat_label(p))}_{q.lower()}.tv",
                         f"{sat_label(p)} - {q}", lst)

    if keep_orig:
        for fn in sorted(os.listdir(src_dir)):
            if not (fn.startswith("userbouquet.") and fn.endswith(".tv")):
                continue
            title, refs = fn, []
            with open(os.path.join(src_dir, fn), "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#NAME"):
                        title = line[5:].strip()
                    elif line.startswith("#SERVICE"):
                        r = line[8:].strip()
                        p = r.split(":")
                        try:
                            k = (int(p[3], 16), int(p[4], 16), int(p[5], 16), int(p[6], 16))
                        except (IndexError, ValueError):
                            refs.append(r)  # marqueur / séparateur
                            continue
                        if k in keep:
                            refs.append(r)
            if any(len(r.split(":")) > 6 and r.split(":")[1] == "0" for r in refs):
                write_bq(fn, title, refs)

    with open(os.path.join(out_dir, "bouquets.tv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("#NAME Bouquets (TV)\n")
        for fn, _ in bouquets:
            f.write(f'#SERVICE 1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "{fn}" ORDER BY bouquet\n')
    with open(os.path.join(out_dir, "bouquets.radio"), "w", encoding="utf-8", newline="\n") as f:
        f.write("#NAME Bouquets (Radio)\n")

    log(f"Export terminé : {len(chosen)} chaînes, {len(bouquets)} bouquets -> {out_dir}")


# ---------------------------------------------------------------- GUI
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Enigma2 - Recherche de chaînes par satellite")
        self.geometry("900x640")
        self.tmp = tempfile.mkdtemp(prefix="e2tool_")
        self.src_dir = None
        self.data = None
        self.checked = {}

        top = ttk.LabelFrame(self, text="1. Source (dossier, ZIP ou URL d'un ZIP)")
        top.pack(fill="x", padx=8, pady=6)
        self.src = tk.StringVar()
        ttk.Entry(top, textvariable=self.src).pack(side="left", fill="x", expand=True, padx=4, pady=4)
        ttk.Button(top, text="Dossier…", command=self.pick_dir).pack(side="left", padx=2)
        ttk.Button(top, text="ZIP…", command=self.pick_zip).pack(side="left", padx=2)
        ttk.Button(top, text="Charger / Télécharger", command=self.load).pack(side="left", padx=4)

        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True, padx=8)

        left = ttk.LabelFrame(mid, text="2. Satellites (cliquer pour cocher)")
        left.pack(side="left", fill="both", expand=True)
        self.tree = ttk.Treeview(left, columns=("sat", "n"), show="headings", selectmode="none")
        self.tree.heading("sat", text="Satellite")
        self.tree.heading("n", text="Chaînes")
        self.tree.column("sat", width=320)
        self.tree.column("n", width=70, anchor="e")
        self.tree.pack(fill="both", expand=True, side="left")
        sb = ttk.Scrollbar(left, command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Button-1>", self.toggle)

        right = ttk.LabelFrame(mid, text="3. Catégories & options")
        right.pack(side="left", fill="y", padx=6)
        self.q = {k: tk.BooleanVar(value=(k != "Radio")) for k in ("4K", "HD", "SD", "Radio")}
        for k, v in self.q.items():
            ttk.Checkbutton(right, text=k, variable=v).pack(anchor="w", padx=8, pady=2)
        ttk.Separator(right).pack(fill="x", pady=6)
        self.per_sat = tk.BooleanVar(value=True)
        self.per_q = tk.BooleanVar(value=True)
        self.keep = tk.BooleanVar(value=True)
        ttk.Checkbutton(right, text="Bouquets par satellite", variable=self.per_sat).pack(anchor="w", padx=8)
        ttk.Checkbutton(right, text="Bouquets par qualité", variable=self.per_q).pack(anchor="w", padx=8)
        ttk.Checkbutton(right, text="Garder bouquets d'origine\n(filtrés)", variable=self.keep).pack(anchor="w", padx=8)
        ttk.Separator(right).pack(fill="x", pady=6)
        ttk.Button(right, text="Tout cocher", command=lambda: self.set_all(True)).pack(fill="x", padx=8, pady=2)
        ttk.Button(right, text="Tout décocher", command=lambda: self.set_all(False)).pack(fill="x", padx=8, pady=2)

        bot = ttk.LabelFrame(self, text="4. Export")
        bot.pack(fill="x", padx=8, pady=6)
        self.out = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Desktop", "enigma2_export"))
        ttk.Entry(bot, textvariable=self.out).pack(side="left", fill="x", expand=True, padx=4, pady=4)
        ttk.Button(bot, text="Parcourir…", command=self.pick_out).pack(side="left", padx=2)
        ttk.Button(bot, text="Exporter", command=self.do_export).pack(side="left", padx=4)

        self.logbox = tk.Text(self, height=6)
        self.logbox.pack(fill="x", padx=8, pady=(0, 8))
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def log(self, msg):
        self.logbox.insert("end", msg + "\n")
        self.logbox.see("end")

    def on_close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.destroy()

    def pick_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.src.set(d)

    def pick_zip(self):
        f = filedialog.askopenfilename(filetypes=[("ZIP", "*.zip")])
        if f:
            self.src.set(f)

    def pick_out(self):
        d = filedialog.askdirectory()
        if d:
            self.out.set(d)

    def load(self):
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        try:
            s = self.src.get().strip()
            work = os.path.join(self.tmp, "src")
            shutil.rmtree(work, ignore_errors=True)
            os.makedirs(work)
            if s.lower().startswith(("http://", "https://")):
                self.after(0, self.log, "Téléchargement…")
                zp = os.path.join(self.tmp, "dl.zip")
                req = urllib.request.Request(s, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=60) as r, open(zp, "wb") as f:
                    shutil.copyfileobj(r, f)
                s = zp
            if os.path.isfile(s):
                with zipfile.ZipFile(s) as z:
                    z.extractall(work)
                s = work
            d = find_lamedb(s)
            if not d:
                raise RuntimeError("Fichier 'lamedb' introuvable dans la source.")
            data = parse_lamedb(os.path.join(d, "lamedb"))
            self.src_dir, self.data = d, data
            self.after(0, self.fill)
        except Exception as e:
            self.after(0, messagebox.showerror, "Erreur", str(e))

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        self.checked.clear()
        cnt = defaultdict(int)
        for s in self.data[2]:
            cnt[s.pos] += 1
        for pos in sorted(cnt, key=lambda x: (isinstance(x, str), x)):
            iid = self.tree.insert("", "end", values=("☐  " + sat_label(pos), cnt[pos]))
            self.checked[iid] = (pos, False)
        self.log(f"{len(self.data[2])} chaînes, {len(cnt)} positions détectées.")

    def refresh(self, iid):
        pos, on = self.checked[iid]
        v = self.tree.item(iid, "values")
        self.tree.item(iid, values=(("☑  " if on else "☐  ") + sat_label(pos), v[1]))

    def toggle(self, ev):
        iid = self.tree.identify_row(ev.y)
        if iid in self.checked:
            pos, on = self.checked[iid]
            self.checked[iid] = (pos, not on)
            self.refresh(iid)

    def set_all(self, state):
        for iid in self.checked:
            self.checked[iid] = (self.checked[iid][0], state)
            self.refresh(iid)

    def do_export(self):
        if not self.data:
            return messagebox.showwarning("Info", "Chargez d'abord une liste.")
        sats = {p for p, on in self.checked.values() if on}
        quals = {k for k, v in self.q.items() if v.get()}
        if not sats or not quals:
            return messagebox.showwarning("Info", "Cochez au moins un satellite et une catégorie.")
        version, tps, svcs = self.data
        try:
            export(self.src_dir, self.out.get(), version, tps, svcs, sats, quals,
                   self.per_sat.get(), self.per_q.get(), self.keep.get(), self.log)
            messagebox.showinfo("OK", "Export terminé.\nCopiez le contenu vers /etc/enigma2/ du récepteur.")
        except Exception as e:
            messagebox.showerror("Erreur", str(e))


if __name__ == "__main__":
    App().mainloop()
