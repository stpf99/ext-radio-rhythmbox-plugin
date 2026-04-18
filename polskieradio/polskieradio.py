"""
PolskieRadio & RadioBrowser Plugin for Rhythmbox — v5.0
Nowości:
  • Covers stacji we wszystkich widokach (Kafelki / Lista / Szczegółowa)
  • Zaawansowane wyszukiwanie (nazwa, kraj, gatunek, język, bitrate, kodek, limit, kolejność)
  • Ulubione – dodaj/usuń gwiazdką, widok z sortowaniem (A-Z, Z-A, ocena, data)
  • Kategorie lokalne wg geolokalizacji IP (automatyczne wykrywanie kraju)
  • Podzielony widok – prawa kolumna z cover, nazwą, kodekiem, bitrate,
    krajem, tagami, głosami, językiem, oceną gwiazdkową i przyciskiem ulubionych
  • Historia ostatnio odtwarzanych (MAX_HISTORY wpisów, z czasem i cover)
  • Oceny gwiazdkowe 1–5 (lokalny JSON), edytowalne w panelu stacji
  • Sortowanie ulubionych: Nazwa A→Z / Z→A / Ocena ↓↑ / Ostatnio dodane
"""

import gi
gi.require_version("RB",        "3.0")
gi.require_version("Gtk",       "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Peas",      "1.0")

from gi.repository import GObject, GLib, Gtk, Gdk, RB, GdkPixbuf, Peas, Pango
import json
import os
import threading
import urllib.request
import time
import datetime

try:
    from pyradios import RadioBrowser
    HAS_PYRADIOS = True
except ImportError:
    HAS_PYRADIOS = False
    print("[PR] Biblioteka 'pyradios' nie znaleziona. RadioBrowser wyłączony.")

# ── Ścieżki ─────────────────────────────────────────────────────────────────
PLUGIN_DIR = os.path.dirname(__file__)
JSON_PATH  = os.path.join(PLUGIN_DIR, "stations.json")
FAV_PATH   = os.path.join(PLUGIN_DIR, "favorites.json")
HIST_PATH  = os.path.join(PLUGIN_DIR, "history.json")
RATE_PATH  = os.path.join(PLUGIN_DIR, "ratings.json")

# ── Stałe ────────────────────────────────────────────────────────────────────
COVER_SIZE  = 200
PANEL_W     = 280
MAX_HISTORY = 50
ICON_SIZES  = {"Mini": 32, "Małe": 48, "Standard": 64, "Duże": 96, "Największe": 128}
CODECS      = ["Wszystkie", "MP3", "AAC", "AAC+", "OGG", "FLAC", "WMA", "HLS"]
SORT_FAVS   = ["Nazwa A→Z", "Nazwa Z→A", "Ocena ↓", "Ocena ↑", "Ostatnio dodane"]


# ══════════════════════════════════════════════════════════════════════════════
#  Persistence Manager
# ══════════════════════════════════════════════════════════════════════════════
class PersistenceManager:
    """Zarządza ulubionymi, historią i ocenami (zapis/odczyt JSON)."""

    def __init__(self):
        self._favorites = self._load(FAV_PATH, [])   # [station_dict, ...]
        self._history   = self._load(HIST_PATH, [])  # [{station, ts}, ...]
        self._ratings   = self._load(RATE_PATH, {})  # {url: 1-5}
        self._fav_urls  = {self._key(s) for s in self._favorites}

    # ── internal ──────────────────────────────────────────────────────────
    @staticmethod
    def _key(station):
        return station.get("url_resolved") or station.get("url", "")

    def _load(self, path, default):
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return default

    def _save(self, path, data):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[PR] Zapis błąd {path}: {e}")

    # ── Ulubione ──────────────────────────────────────────────────────────
    def is_favorite(self, url):
        return url in self._fav_urls

    def toggle_favorite(self, station):
        url = self._key(station)
        if url in self._fav_urls:
            self._favorites = [s for s in self._favorites if self._key(s) != url]
            self._fav_urls.discard(url)
        else:
            s = dict(station)
            s["_added_at"] = time.time()
            self._favorites.append(s)
            self._fav_urls.add(url)
        self._save(FAV_PATH, self._favorites)
        return url in self._fav_urls

    def get_favorites(self, sort_key="Nazwa A→Z"):
        favs = list(self._favorites)
        for f in favs:
            f["_rating"] = self._ratings.get(self._key(f), 0)
        if sort_key == "Nazwa A→Z":
            favs.sort(key=lambda x: x.get("name", "").lower())
        elif sort_key == "Nazwa Z→A":
            favs.sort(key=lambda x: x.get("name", "").lower(), reverse=True)
        elif sort_key == "Ocena ↓":
            favs.sort(key=lambda x: -x.get("_rating", 0))
        elif sort_key == "Ocena ↑":
            favs.sort(key=lambda x: x.get("_rating", 0))
        elif sort_key == "Ostatnio dodane":
            favs.sort(key=lambda x: -x.get("_added_at", 0))
        return favs

    # ── Historia ──────────────────────────────────────────────────────────
    def add_history(self, station):
        url = self._key(station)
        self._history = [h for h in self._history if self._key(h["station"]) != url]
        self._history.insert(0, {"station": dict(station), "ts": time.time()})
        self._history = self._history[:MAX_HISTORY]
        self._save(HIST_PATH, self._history)

    def get_history(self):
        return self._history

    # ── Oceny ─────────────────────────────────────────────────────────────
    def set_rating(self, url, rating):
        if rating == 0:
            self._ratings.pop(url, None)
        else:
            self._ratings[url] = rating
        self._save(RATE_PATH, self._ratings)

    def get_rating(self, url):
        return self._ratings.get(url, 0)


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════════
def fetch_pixbuf(url, size=COVER_SIZE):
    if not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Rhythmbox-PRPlugin/5.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = resp.read()
        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        pix = loader.get_pixbuf()
        w, h = pix.get_width(), pix.get_height()
        m = min(w, h)
        pix = pix.new_subpixbuf((w - m) // 2, (h - m) // 2, m, m)
        return pix.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
    except Exception:
        return None


def detect_country():
    """Wykrywa kraj użytkownika przez API geolokalizacji IP."""
    try:
        req = urllib.request.Request(
            "https://ipapi.co/json/",
            headers={"User-Agent": "Rhythmbox-PRPlugin/5.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            d = json.loads(r.read())
        return d.get("country_name", ""), d.get("country_code", "")
    except Exception:
        return "", ""


def _safe_icon(widget, name, fallback_size=Gtk.IconSize.DIALOG):
    theme = Gtk.IconTheme.get_default()
    if theme.has_icon(name):
        widget.set_from_icon_name(name, fallback_size)
    else:
        widget.set_from_icon_name("media-playback-start", fallback_size)


# ══════════════════════════════════════════════════════════════════════════════
#  Star Rating Widget
# ══════════════════════════════════════════════════════════════════════════════
class StarRating(Gtk.Box):
    """Klikalny lub tylko do odczytu widget gwiazdkowy (0–5)."""

    __gsignals__ = {
        "rating-changed": (GObject.SignalFlags.RUN_FIRST, None, (int,))
    }

    def __init__(self, rating=0, editable=True, large=False):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._rating = rating
        self._editable = editable
        self._large = large
        self._btns = []
        for i in range(1, 6):
            btn = Gtk.Button()
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.set_can_focus(False)
            btn.set_margin_start(0)
            btn.set_margin_end(0)
            self._set_star(btn, i <= rating, large)
            if editable:
                btn.connect("clicked", self._on_click, i)
                btn.connect("enter-notify-event", self._on_hover, i)
                btn.connect("leave-notify-event", self._on_leave)
            self._btns.append(btn)
            self.pack_start(btn, False, False, 0)

    def _set_star(self, btn, filled, large):
        sym = "★" if filled else "☆"
        if large:
            btn.set_label(sym)
        else:
            lbl = btn.get_child()
            if isinstance(lbl, Gtk.Label):
                lbl.set_markup(f"<small>{sym}</small>")
            else:
                lbl2 = Gtk.Label()
                lbl2.set_markup(f"<small>{sym}</small>")
                btn.add(lbl2)

    def set_rating(self, r):
        self._rating = r
        for i, btn in enumerate(self._btns, 1):
            self._set_star(btn, i <= r, self._large)

    def get_rating(self):
        return self._rating

    def _on_click(self, _btn, val):
        new = val if val != self._rating else 0
        self.set_rating(new)
        self.emit("rating-changed", new)

    def _on_hover(self, _btn, _ev, val):
        for i, b in enumerate(self._btns, 1):
            self._set_star(b, i <= val, self._large)

    def _on_leave(self, _btn, _ev):
        self.set_rating(self._rating)


# ══════════════════════════════════════════════════════════════════════════════
#  Current Station Info Panel
# ══════════════════════════════════════════════════════════════════════════════
class StationInfoPanel(Gtk.Box):
    """Prawa kolumna – cover + szczegóły aktualnie odtwarzanej stacji."""

    def __init__(self, persistence, fav_callback, rate_callback):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._pm = persistence
        self._fav_cb = fav_callback
        self._rate_cb = rate_callback
        self._station = None

        self.set_margin_start(6)
        self.set_margin_end(8)
        self.set_margin_top(8)
        self.set_margin_bottom(8)

        # ── Cover ──
        cover_frame = Gtk.Frame(shadow_type=Gtk.ShadowType.ETCHED_IN)
        self._img = Gtk.Image()
        self._img.set_size_request(COVER_SIZE, COVER_SIZE)
        _safe_icon(self._img, "radio")
        cover_frame.add(self._img)
        self.pack_start(cover_frame, False, False, 0)

        # ── Nazwa ──
        self._lbl_name = Gtk.Label()
        self._lbl_name.set_markup("<b><big>Nic nie gra</big></b>")
        self._lbl_name.set_line_wrap(True)
        self._lbl_name.set_lines(3)
        self._lbl_name.set_justify(Gtk.Justification.CENTER)
        self._lbl_name.set_max_width_chars(22)
        self.pack_start(self._lbl_name, False, False, 0)

        self.pack_start(Gtk.Separator(), False, False, 2)

        # ── Info grid ──
        grid = Gtk.Grid()
        grid.set_column_spacing(8)
        grid.set_row_spacing(3)
        grid.set_margin_start(4)
        grid.set_margin_end(4)

        rows = [
            ("Kraj",      "_lbl_country"),
            ("Kodek",     "_lbl_codec"),
            ("Bitrate",   "_lbl_bitrate"),
            ("Kategoria", "_lbl_tags"),
            ("Język",     "_lbl_lang"),
            ("Głosy",     "_lbl_votes"),
            ("Pochodzi",  "_lbl_source"),
        ]
        for i, (label, attr) in enumerate(rows):
            key = Gtk.Label(label=f"{label}:")
            key.set_xalign(1.0)
            key.set_opacity(0.55)
            key.set_markup(f"<small>{label}:</small>")
            grid.attach(key, 0, i, 1, 1)

            val = Gtk.Label(label="—")
            val.set_xalign(0.0)
            val.set_hexpand(True)
            val.set_ellipsize(Pango.EllipsizeMode.END)
            setattr(self, attr, val)
            grid.attach(val, 1, i, 1, 1)

        self.pack_start(grid, False, False, 0)
        self.pack_start(Gtk.Separator(), False, False, 2)

        # ── Ocena ──
        rbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        rl = Gtk.Label()
        rl.set_markup("<small>Ocena:</small>")
        rl.set_opacity(0.6)
        rbox.pack_start(rl, False, False, 0)
        self._stars = StarRating(0, editable=True, large=True)
        self._stars.connect("rating-changed", self._on_rate)
        rbox.pack_start(self._stars, False, False, 0)
        self.pack_start(rbox, False, False, 0)

        # ── Ulubione ──
        self._btn_fav = Gtk.Button(label="☆  Dodaj do ulubionych")
        self._btn_fav.set_sensitive(False)
        self._btn_fav.connect("clicked", self._on_fav)
        self.pack_start(self._btn_fav, False, False, 0)

        # ── Placeholder ──
        self._lbl_idle = Gtk.Label(label="Wybierz stację,\naby zobaczyć szczegóły.")
        self._lbl_idle.set_opacity(0.45)
        self._lbl_idle.set_justify(Gtk.Justification.CENTER)
        self.pack_end(self._lbl_idle, True, True, 0)

    # ── public API ────────────────────────────────────────────────────────
    def update_station(self, station):
        self._station = station
        if station is None:
            self._reset()
            return

        self._lbl_idle.hide()
        self._btn_fav.set_sensitive(True)

        name = station.get("name", "Nieznana")
        self._lbl_name.set_markup(f"<b>{GLib.markup_escape_text(name)}</b>")

        def t(v):
            return str(v) if v else "—"

        br = station.get("bitrate", 0)
        self._lbl_country.set_text(t(station.get("country")))
        self._lbl_codec.set_text(t(station.get("codec")))
        self._lbl_bitrate.set_text(f"{br} kbps" if br else "—")
        tags = (station.get("tags") or "").strip()
        self._lbl_tags.set_text(tags[:35] if tags else "—")
        self._lbl_lang.set_text(t(station.get("language")))
        self._lbl_votes.set_text(t(station.get("votes")))
        # źródło (lokalne vs RB)
        source = station.get("_source", station.get("country", "—"))
        self._lbl_source.set_text(source[:30] if source else "—")

        url = station.get("url_resolved") or station.get("url", "")
        self._stars.set_rating(self._pm.get_rating(url))

        is_fav = self._pm.is_favorite(url)
        self._btn_fav.set_label("★  Usuń z ulubionych" if is_fav else "☆  Dodaj do ulubionych")

        favicon = station.get("favicon", "")
        if favicon:
            threading.Thread(target=self._load_cover, args=(favicon,), daemon=True).start()
        else:
            _safe_icon(self._img, "radio")

    def refresh_fav_button(self):
        if self._station:
            url = self._station.get("url_resolved") or self._station.get("url", "")
            is_fav = self._pm.is_favorite(url)
            self._btn_fav.set_label("★  Usuń z ulubionych" if is_fav else "☆  Dodaj do ulubionych")

    # ── private ───────────────────────────────────────────────────────────
    def _reset(self):
        self._lbl_name.set_markup("<b><big>Nic nie gra</big></b>")
        _safe_icon(self._img, "radio")
        for attr in ("_lbl_country", "_lbl_codec", "_lbl_bitrate",
                     "_lbl_tags", "_lbl_lang", "_lbl_votes", "_lbl_source"):
            getattr(self, attr).set_text("—")
        self._stars.set_rating(0)
        self._btn_fav.set_label("☆  Dodaj do ulubionych")
        self._btn_fav.set_sensitive(False)
        self._lbl_idle.show()

    def _load_cover(self, url):
        pix = fetch_pixbuf(url, COVER_SIZE)
        GLib.idle_add(self._set_cover, pix)

    def _set_cover(self, pix):
        if pix:
            self._img.set_from_pixbuf(pix)
        else:
            _safe_icon(self._img, "radio")

    def _on_fav(self, _btn):
        if self._station:
            self._fav_cb(self._station)
            self.refresh_fav_button()

    def _on_rate(self, _w, rating):
        if self._station:
            url = self._station.get("url_resolved") or self._station.get("url", "")
            self._rate_cb(url, rating)


# ══════════════════════════════════════════════════════════════════════════════
#  Advanced Search Dialog
# ══════════════════════════════════════════════════════════════════════════════
class AdvancedSearchDialog(Gtk.Dialog):
    def __init__(self, parent=None):
        super().__init__(
            title="Zaawansowane wyszukiwanie stacji",
            transient_for=parent,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT
        )
        self.add_button("🔍 Szukaj", Gtk.ResponseType.OK)
        self.add_button("Anuluj",   Gtk.ResponseType.CANCEL)
        self.set_default_response(Gtk.ResponseType.OK)
        self.set_default_size(420, 380)

        grid = Gtk.Grid()
        grid.set_column_spacing(14)
        grid.set_row_spacing(10)
        grid.set_margin_start(18)
        grid.set_margin_end(18)
        grid.set_margin_top(16)
        grid.set_margin_bottom(16)

        def row(g, r, label, widget):
            lbl = Gtk.Label(label=label)
            lbl.set_xalign(1.0)
            lbl.set_opacity(0.75)
            g.attach(lbl, 0, r, 1, 1)
            widget.set_hexpand(True)
            g.attach(widget, 1, r, 1, 1)
            return widget

        self._e_name    = row(grid, 0, "Nazwa stacji:", Gtk.Entry())
        self._e_country = row(grid, 1, "Kraj:", Gtk.Entry())
        self._e_tag     = row(grid, 2, "Gatunek / Tag:", Gtk.Entry())
        self._e_lang    = row(grid, 3, "Język:", Gtk.Entry())

        self._spin_bitrate = Gtk.SpinButton.new_with_range(0, 320, 16)
        self._spin_bitrate.set_value(0)
        row(grid, 4, "Min. bitrate (kbps):", self._spin_bitrate)

        self._combo_codec = Gtk.ComboBoxText()
        for c in CODECS:
            self._combo_codec.append_text(c)
        self._combo_codec.set_active(0)
        row(grid, 5, "Kodek:", self._combo_codec)

        self._chk_favicon = Gtk.CheckButton(label="Tylko stacje z okładką (favicon)")
        grid.attach(self._chk_favicon, 0, 6, 2, 1)

        self._combo_order = Gtk.ComboBoxText()
        for o in ["name", "votes", "clickcount", "bitrate", "random"]:
            self._combo_order.append_text(o)
        self._combo_order.set_active(0)
        row(grid, 7, "Sortuj wg:", self._combo_order)

        self._spin_limit = Gtk.SpinButton.new_with_range(10, 1000, 10)
        self._spin_limit.set_value(200)
        row(grid, 8, "Limit wyników:", self._spin_limit)

        self.get_content_area().add(grid)
        self.show_all()

    def get_params(self):
        codec = self._combo_codec.get_active_text()
        return {
            "name":       self._e_name.get_text().strip(),
            "country":    self._e_country.get_text().strip(),
            "tag":        self._e_tag.get_text().strip(),
            "language":   self._e_lang.get_text().strip(),
            "bitrate":    int(self._spin_bitrate.get_value()),
            "codec":      codec if codec != "Wszystkie" else "",
            "has_favicon": self._chk_favicon.get_active(),
            "order":      self._combo_order.get_active_text() or "name",
            "limit":      int(self._spin_limit.get_value()),
        }


# ══════════════════════════════════════════════════════════════════════════════
#  Station Card (widok kafelkowy)
# ══════════════════════════════════════════════════════════════════════════════
class StationCard(Gtk.EventBox):
    def __init__(self, station, play_cb, fav_cb, pm, size=64):
        super().__init__()
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK)
        self._station = station
        self._fav_cb  = fav_cb
        self._pm      = pm
        self._size    = size

        url  = station.get("url_resolved") or station.get("url", "")
        name = station.get("name", "Nieznana")

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.set_size_request(size + 40, -1)
        outer.set_margin_start(5); outer.set_margin_end(5)
        outer.set_margin_top(5);   outer.set_margin_bottom(5)
        self.add(outer)

        # Cover
        frame = Gtk.Frame(shadow_type=Gtk.ShadowType.ETCHED_IN)
        self.img = Gtk.Image()
        self.img.set_size_request(size, size)
        _safe_icon(self.img, "radio")
        frame.add(self.img)
        outer.pack_start(frame, False, False, 0)

        # Nazwa
        lbl = Gtk.Label()
        lbl.set_line_wrap(True); lbl.set_lines(2)
        lbl.set_justify(Gtk.Justification.CENTER)
        esc = GLib.markup_escape_text(name)
        markup = f"<b>{esc}</b>" if size >= 64 else f"<small><b>{esc}</b></small>"
        lbl.set_markup(markup)
        outer.pack_start(lbl, False, False, 0)

        if size >= 96:
            country = station.get("country", "")
            if country:
                lc = Gtk.Label(label=country)
                lc.set_opacity(0.65)
                lc.set_justify(Gtk.Justification.CENTER)
                outer.pack_start(lc, False, False, 0)

        # Pasek dołu: ocena + fav
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self._stars = StarRating(pm.get_rating(url), editable=False)
        bar.pack_start(self._stars, True, True, 0)

        self._btn_fav = Gtk.Button()
        self._btn_fav.set_relief(Gtk.ReliefStyle.NONE)
        self._btn_fav.set_can_focus(False)
        self._btn_fav.set_label("★" if pm.is_favorite(url) else "☆")
        self._btn_fav.connect("clicked", self._on_fav_click)
        bar.pack_start(self._btn_fav, False, False, 0)
        outer.pack_start(bar, False, False, 0)

        self.connect("button-release-event",
                     lambda w, e: play_cb(url, name) if e.button == 1 else None)

    def set_cover(self, pixbuf):
        if pixbuf:
            s = self._size
            self.img.set_from_pixbuf(
                pixbuf.scale_simple(s, s, GdkPixbuf.InterpType.BILINEAR))

    def refresh_fav(self):
        url = self._station.get("url_resolved") or self._station.get("url", "")
        self._btn_fav.set_label("★" if self._pm.is_favorite(url) else "☆")

    def _on_fav_click(self, _btn):
        self._fav_cb(self._station)
        self.refresh_fav()


# ══════════════════════════════════════════════════════════════════════════════
#  Category Button
# ══════════════════════════════════════════════════════════════════════════════
class CategoryButton(Gtk.EventBox):
    def __init__(self, text, subtext, icon_name, callback, data):
        super().__init__()
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_start(15); box.set_margin_end(15)
        box.set_margin_top(8);   box.set_margin_bottom(8)
        self.add(box)

        img = Gtk.Image()
        if Gtk.IconTheme.get_default().has_icon(icon_name):
            img.set_from_icon_name(icon_name, Gtk.IconSize.LARGE_TOOLBAR)
        box.pack_start(img, False, False, 0)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        lbl = Gtk.Label()
        lbl.set_markup(f"<b>{GLib.markup_escape_text(text)}</b>")
        lbl.set_xalign(0)
        vbox.pack_start(lbl, False, False, 0)
        if subtext:
            sub = Gtk.Label(label=subtext)
            sub.set_opacity(0.6); sub.set_xalign(0)
            vbox.pack_start(sub, False, False, 0)
        box.pack_start(vbox, True, True, 0)

        self.connect("button-release-event",
                     lambda w, e: callback(data) if e.button == 1 else None)


# ══════════════════════════════════════════════════════════════════════════════
#  Entry Type
# ══════════════════════════════════════════════════════════════════════════════
class PREntryType(RB.RhythmDBEntryType):
    __gtype_name__ = "PREntryType"

    def __init__(self):
        RB.RhythmDBEntryType.__init__(self, name="pr-stream", save_to_disk=False)

    def can_sync_metadata(self, entry):
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  Source – główny widok
# ══════════════════════════════════════════════════════════════════════════════
class PRSource(RB.Source):
    __gtype_name__ = "PRSource"

    def setup(self, db, shell, entry_type):
        self._db          = db
        self._shell       = shell
        self._entry_type  = entry_type
        self._rb          = RadioBrowser(user_agent="Rhythmbox-PRPlugin/5.0") if HAS_PYRADIOS else None
        self._pm          = PersistenceManager()

        # Stan nawigacji
        self._nav_history    = []
        self._current_view   = "home"
        self._current_data   = None
        self._current_stations = []

        # Stan UI
        self._view_mode     = "grid"
        self._icon_size_val = "Standard"
        self._fav_sort      = "Nazwa A→Z"

        # Geolokalizacja
        self._geo_country = ""
        self._geo_code    = ""

        self._json_data = {}
        self._load_json()

        # Wykryj kraj w tle
        threading.Thread(target=self._detect_geo, daemon=True).start()

        self._build_ui()
        self._show_home()

        # Sygnały gracza
        self._shell.props.shell_player.connect("playing-song-changed", self._on_song_changed)

    # ── Geolokalizacja ────────────────────────────────────────────────────
    def _detect_geo(self):
        name, code = detect_country()
        GLib.idle_add(self._on_geo, name, code)

    def _on_geo(self, name, code):
        self._geo_country = name
        self._geo_code    = code
        if self._current_view == "home":
            self._show_home()

    # ── Sygnał gracza ─────────────────────────────────────────────────────
    def _on_song_changed(self, player, entry):
        if entry is None:
            self._info_panel.update_station(None)
            return
        if entry.get_entry_type() != self._entry_type:
            return
        url   = entry.get_string(RB.RhythmDBPropType.LOCATION)
        title = entry.get_string(RB.RhythmDBPropType.TITLE)

        station = next(
            (s for s in self._current_stations
             if (s.get("url_resolved") or s.get("url", "")) == url),
            None
        )
        if station is None:
            station = {"name": title, "url": url, "url_resolved": url}

        GLib.idle_add(self._info_panel.update_station, station)
        self._pm.add_history(station)

    # ══════════════════════════════════════════════════════════════════════
    #  Budowa UI
    # ══════════════════════════════════════════════════════════════════════
    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # ── Toolbar ──────────────────────────────────────────────────────
        tb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        tb.set_margin_top(6); tb.set_margin_bottom(4)
        tb.set_margin_start(8); tb.set_margin_end(8)

        self._btn_back = Gtk.Button(label="⬅ Wstecz")
        self._btn_back.set_sensitive(False)
        self._btn_back.connect("clicked", self._on_back)
        tb.pack_start(self._btn_back, False, False, 0)

        btn_home = Gtk.Button(label="🏠 Start")
        btn_home.connect("clicked", lambda _: self._show_home())
        tb.pack_start(btn_home, False, False, 0)

        tb.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 3)

        # Wybór widoku
        tb.pack_start(Gtk.Label(label="Widok:"), False, False, 0)
        self._combo_view = Gtk.ComboBoxText()
        for m in ["Kafelki", "Lista", "Szczegółowa"]:
            self._combo_view.append_text(m)
        self._combo_view.set_active(0)
        self._combo_view.connect("changed", self._on_view_changed)
        tb.pack_start(self._combo_view, False, False, 0)

        tb.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 3)

        # Rozmiar ikon
        tb.pack_start(Gtk.Label(label="Ikony:"), False, False, 0)
        self._combo_size = Gtk.ComboBoxText()
        for k in ICON_SIZES.keys():
            self._combo_size.append_text(k)
        self._combo_size.set_active_id("Standard")
        self._combo_size.connect("changed", self._on_size_changed)
        tb.pack_start(self._combo_size, False, False, 0)

        tb.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 3)

        # Szybkie szukanie
        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text("Szukaj…")
        self._search_entry.set_width_chars(18)
        self._search_entry.connect("activate", self._on_quick_search)
        self._search_entry.connect("changed",  self._on_search_changed)
        tb.pack_start(self._search_entry, False, False, 0)

        # Zaawansowane szukanie
        btn_adv = Gtk.Button(label="🔍 Zaawansowane")
        btn_adv.connect("clicked", self._on_adv_search)
        tb.pack_start(btn_adv, False, False, 0)

        self._status_lbl = Gtk.Label(label="")
        self._status_lbl.set_halign(Gtk.Align.END)
        self._status_lbl.set_hexpand(True)
        tb.pack_start(self._status_lbl, True, True, 0)

        root.pack_start(tb, False, False, 0)

        # ── Pasek alfabetyczny ────────────────────────────────────────────
        self._alpha_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self._alpha_bar.set_margin_start(8); self._alpha_bar.set_margin_end(8)
        self._alpha_bar.set_margin_bottom(4)
        self._alpha_bar.hide()
        root.pack_start(self._alpha_bar, False, False, 0)

        # ── Paned: lewa (treść) | prawa (panel stacji) ────────────────────
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)

        scroll = Gtk.ScrolledWindow()
        scroll.set_shadow_type(Gtk.ShadowType.NONE)
        vp = Gtk.Viewport()
        self._content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        vp.add(self._content_box)
        scroll.add(vp)
        paned.pack1(scroll, True, True)

        right_scroll = Gtk.ScrolledWindow()
        right_scroll.set_shadow_type(Gtk.ShadowType.IN)
        right_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        right_scroll.set_size_request(PANEL_W + 20, -1)
        self._info_panel = StationInfoPanel(
            self._pm,
            self._toggle_favorite,
            self._pm.set_rating
        )
        right_scroll.add(self._info_panel)
        paned.pack2(right_scroll, False, False)

        root.pack_start(paned, True, True, 0)
        root.show_all()
        self.pack_start(root, True, True, 0)
        self.show_all()

    # ── Pomocnicze UI ─────────────────────────────────────────────────────
    def _clear_content(self):
        for ch in self._content_box.get_children():
            self._content_box.remove(ch)
        for ch in self._alpha_bar.get_children():
            self._alpha_bar.remove(ch)
        self._alpha_bar.hide()

    def _set_status(self, txt):
        self._status_lbl.set_text(txt)

    def _push_nav(self):
        self._nav_history.append({
            "view":  self._current_view,
            "data":  self._current_data,
            "title": self._status_lbl.get_text()
        })
        self._btn_back.set_sensitive(True)

    def _on_back(self, _w):
        if not self._nav_history:
            return
        s = self._nav_history.pop()
        self._btn_back.set_sensitive(bool(self._nav_history))
        self._current_view = s["view"]
        self._current_data = s["data"]
        self._set_status(s["title"])

        v = s["view"]
        if   v == "home":          self._show_home(push=False)
        elif v == "favorites":     self._show_favorites(push=False)
        elif v == "history":       self._show_history(push=False)
        elif v == "category_list": self._populate_category_list(s["data"]["items"], s["data"]["type"], push=False)
        elif v in ("station_list", "search", "json_group"):
            if isinstance(s["data"], list):
                self._populate_stations_grid(s["data"], push=False)
            elif isinstance(s["data"], str) and v == "json_group":
                self._show_json_group(s["data"], push=False)

    # ── Toolbar akcje ─────────────────────────────────────────────────────
    def _on_view_changed(self, combo):
        t = combo.get_active_text()
        self._view_mode = {"Kafelki": "grid", "Lista": "list", "Szczegółowa": "detail"}.get(t, "grid")
        if self._current_view in ("station_list", "search", "favorites", "json_group"):
            self._populate_stations_grid(self._current_stations)

    def _on_size_changed(self, combo):
        v = combo.get_active_id()
        if v and v in ICON_SIZES:
            self._icon_size_val = v
            if self._current_view in ("station_list", "search", "favorites", "json_group"):
                self._populate_stations_grid(self._current_stations)

    def _on_quick_search(self, entry):
        q = entry.get_text().strip()
        if q and self._rb:
            self._show_stations("search", q, is_search=True)

    def _on_search_changed(self, entry):
        text = entry.get_text().lower()
        if not text:
            self._populate_stations_grid(self._current_stations)
            return
        filtered = [s for s in self._current_stations
                    if text in s.get("name", "").lower()]
        self._populate_stations_grid(filtered, skip_alpha=True)

    def _on_adv_search(self, _w):
        dlg = AdvancedSearchDialog(parent=None)
        resp = dlg.run()
        if resp == Gtk.ResponseType.OK:
            params = dlg.get_params()
            self._show_advanced_search(params)
        dlg.destroy()

    # ── Ulubione ──────────────────────────────────────────────────────────
    def _toggle_favorite(self, station):
        self._pm.toggle_favorite(station)
        self._info_panel.refresh_fav_button()
        if self._current_view == "favorites":
            self._show_favorites(push=False)

    # ══════════════════════════════════════════════════════════════════════
    #  Widoki główne
    # ══════════════════════════════════════════════════════════════════════

    # ── Home ──────────────────────────────────────────────────────────────
    def _show_home(self, push=True):
        if push:
            self._nav_history.clear()
            self._btn_back.set_sensitive(False)
        self._current_view = "home"
        self._clear_content()
        self._set_status("Wybierz kategorię")

        def section(title):
            lbl = Gtk.Label()
            lbl.set_markup(f"<b>── {GLib.markup_escape_text(title)} ──</b>")
            lbl.set_xalign(0)
            lbl.set_margin_start(15)
            lbl.set_margin_top(10)
            self._content_box.pack_start(lbl, False, False, 4)

        # Ulubione
        fav_n = len(self._pm.get_favorites())
        section("ULUBIONE")
        self._content_box.pack_start(
            CategoryButton("★ Moje ulubione stacje", f"{fav_n} stacji",
                           "emblem-favorite", lambda _: self._show_favorites(), None),
            False, False, 0)

        # Historia
        hist_n = len(self._pm.get_history())
        section("HISTORIA ODTWARZANIA")
        self._content_box.pack_start(
            CategoryButton("🕐 Ostatnio odtwarzane", f"{hist_n} stacji",
                           "document-open-recent", lambda _: self._show_history(), None),
            False, False, 0)

        # Lokalne (JSON)
        if self._json_data:
            section("MOJE STACJE (LOKALNE)")
            for grp in sorted(self._json_data.keys()):
                self._content_box.pack_start(
                    CategoryButton(grp, "Lokalna baza", "folder-music",
                                   self._on_json_group_click, grp),
                    False, False, 0)

        # RadioBrowser
        if self._rb:
            section("ŚWIAT (RADIO BROWSER)")
            geo_label = (f"📍 Stacje z {self._geo_country}"
                         if self._geo_country else "📍 Stacje z Polski")
            categories = [
                ("local",     geo_label,                self._geo_country or "Poland", "radio"),
                ("top",       "🔥 Popularne na świecie", "Top 500",                    "radio"),
                ("countries", "🌍 Przeglądaj Kraje",     "",                           "folder"),
                ("tags",      "🎵 Przeglądaj Gatunki",   "",                           "folder"),
                ("languages", "🗣️ Przeglądaj Języki",    "",                           "folder"),
            ]
            for cid, name, info, icon in categories:
                self._content_box.pack_start(
                    CategoryButton(name, info, icon, self._on_category_click, cid),
                    False, False, 0)

        self._content_box.show_all()

    # ── Ulubione (widok) ──────────────────────────────────────────────────
    def _show_favorites(self, push=True):
        if push:
            self._push_nav()
        self._current_view = "favorites"
        self._clear_content()

        # Pasek sortowania
        sort_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        sort_bar.set_margin_start(10); sort_bar.set_margin_end(10)
        sort_bar.set_margin_top(6);   sort_bar.set_margin_bottom(6)
        sort_bar.pack_start(Gtk.Label(label="Sortuj:"), False, False, 0)

        combo = Gtk.ComboBoxText()
        for s in SORT_FAVS:
            combo.append_text(s)
        try:
            idx = SORT_FAVS.index(self._fav_sort)
        except ValueError:
            idx = 0
        combo.set_active(idx)
        combo.connect("changed", self._on_fav_sort_changed)
        sort_bar.pack_start(combo, False, False, 0)
        self._content_box.pack_start(sort_bar, False, False, 0)

        favs = self._pm.get_favorites(self._fav_sort)
        self._current_stations = favs
        self._set_status(f"Ulubione: {len(favs)} stacji")

        if not favs:
            lbl = Gtk.Label(label="Brak ulubionych stacji.\nKliknij ☆ przy stacji, aby dodać.")
            lbl.set_opacity(0.55)
            lbl.set_justify(Gtk.Justification.CENTER)
            self._content_box.pack_start(lbl, True, True, 50)
            self._content_box.show_all()
        else:
            self._populate_stations_widget(favs)

    def _on_fav_sort_changed(self, combo):
        new = combo.get_active_text()
        if new and new != self._fav_sort:
            self._fav_sort = new
            self._show_favorites(push=False)

    # ── Historia (widok) ──────────────────────────────────────────────────
    def _show_history(self, push=True):
        if push:
            self._push_nav()
        self._current_view = "history"
        self._clear_content()

        history = self._pm.get_history()
        stations = [h["station"] for h in history]
        self._current_stations = stations
        self._set_status(f"Historia: {len(stations)} stacji")

        if not stations:
            lbl = Gtk.Label(label="Brak historii odtwarzania.")
            lbl.set_opacity(0.55)
            self._content_box.pack_start(lbl, True, True, 50)
            self._content_box.show_all()
            return

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        listbox.set_header_func(
            lambda r, b, _: r.set_header(Gtk.Separator()) if b and not r.get_header() else None,
            None
        )

        img_refs = []
        for h in history:
            s  = h["station"]
            ts = h.get("ts", 0)
            dt_str = (datetime.datetime.fromtimestamp(ts).strftime("%d.%m  %H:%M")
                      if ts else "")

            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            box.set_margin_start(10); box.set_margin_end(10)
            box.set_margin_top(5);   box.set_margin_bottom(5)

            img = Gtk.Image()
            img.set_size_request(40, 40)
            _safe_icon(img, "radio", Gtk.IconSize.LARGE_TOOLBAR)
            box.pack_start(img, False, False, 0)

            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            n = Gtk.Label()
            n.set_markup(f"<b>{GLib.markup_escape_text(s.get('name','?'))}</b>")
            n.set_xalign(0)
            vbox.pack_start(n, False, False, 0)

            detail_parts = []
            if s.get("country"): detail_parts.append(s["country"])
            if dt_str:           detail_parts.append(dt_str)
            codec = s.get("codec", "")
            br    = s.get("bitrate", 0)
            if codec: detail_parts.append(codec)
            if br:    detail_parts.append(f"{br} kbps")
            d = Gtk.Label(label="  •  ".join(detail_parts))
            d.set_opacity(0.6); d.set_xalign(0)
            d.set_ellipsize(Pango.EllipsizeMode.END)
            vbox.pack_start(d, False, False, 0)
            box.pack_start(vbox, True, True, 0)

            # Przycisk zagraj
            url   = s.get("url_resolved") or s.get("url", "")
            title = s.get("name", "")
            btn_p = Gtk.Button(label="▶ Zagraj")
            btn_p.connect("clicked", lambda _w, u=url, t=title: self._play_url(u, t))
            box.pack_start(btn_p, False, False, 0)

            # Przycisk ulubione
            btn_f = Gtk.Button()
            btn_f.set_relief(Gtk.ReliefStyle.NONE)
            btn_f.set_label("★" if self._pm.is_favorite(url) else "☆")
            btn_f.connect("clicked", lambda _w, st=s, b=btn_f: self._hist_fav_toggle(_w, st, b))
            box.pack_start(btn_f, False, False, 0)

            row.add(box)
            listbox.add(row)
            img_refs.append((img, s.get("favicon", ""), 40))

        self._content_box.pack_start(listbox, True, True, 0)
        self._content_box.show_all()

        threading.Thread(
            target=self._bg_covers_list, args=(img_refs,), daemon=True
        ).start()

    def _hist_fav_toggle(self, _btn, station, fav_btn):
        self._pm.toggle_favorite(station)
        url = station.get("url_resolved") or station.get("url", "")
        fav_btn.set_label("★" if self._pm.is_favorite(url) else "☆")
        self._info_panel.refresh_fav_button()

    # ── Kategorie (JSON) ──────────────────────────────────────────────────
    def _on_json_group_click(self, group_name):
        self._show_json_group(group_name)

    def _show_json_group(self, group_name, push=True):
        if push:
            self._push_nav()
        self._current_view = "json_group"
        self._current_data = group_name
        self._clear_content()
        streams = self._json_data.get(group_name, {}).get("streams", [])
        self._set_status(f"Stacje: {group_name}")
        slist = [{
            "name": s.get("name", "Stream"),
            "url":  s.get("url", ""),
            "url_resolved": s.get("url", ""),
            "favicon": "", "country": "Lokalne",
            "codec": "MP3", "bitrate": "", "tags": "",
            "_source": "lokalna baza"
        } for s in streams]
        self._populate_stations_grid(slist, push=False)

    # ── Kategorie (RB) ────────────────────────────────────────────────────
    def _on_category_click(self, cat_type):
        if cat_type in ("countries", "tags", "languages"):
            self._show_category_list(cat_type)
        else:
            self._show_stations(cat_type, cat_type)

    def _on_subcategory_click(self, value):
        if self._current_view == "category_list" and self._current_data:
            cat_type = self._current_data.get("type", "countries")
            self._show_stations(cat_type, value)

    def _show_category_list(self, cat_type):
        self._push_nav()
        self._current_view = "category_list"
        self._set_status(f"Wczytuję: {cat_type}…")
        self._clear_content()
        sp = Gtk.Spinner(); sp.start()
        self._content_box.pack_start(sp, True, True, 50)
        self._content_box.show_all()
        threading.Thread(target=self._bg_cat, args=(cat_type,), daemon=True).start()

    def _bg_cat(self, cat_type):
        data = []
        try:
            if cat_type == "countries": data = self._rb.countries()
            elif cat_type == "tags":    data = self._rb.tags()
            elif cat_type == "languages": data = self._rb.languages()
        except Exception as e:
            print(f"[PR] API: {e}")
        GLib.idle_add(self._populate_category_list, data, cat_type)

    def _populate_category_list(self, data, cat_type, push=False):
        if push:
            self._push_nav()
        self._current_view = "category_list"
        self._current_data = {"type": cat_type, "items": data}
        self._clear_content()
        self._set_status(f"Lista: {cat_type.capitalize()} ({len(data)})")
        for item in data:
            name  = item.get("name", "?")
            count = item.get("stationcount", 0)
            self._content_box.pack_start(
                CategoryButton(name, f"Stacji: {count}", "folder",
                               self._on_subcategory_click, name),
                False, False, 0)
        self._content_box.show_all()

    # ── Ładowanie stacji ──────────────────────────────────────────────────
    def _show_stations(self, cat_type, value, is_search=False):
        self._push_nav()
        self._current_view = "search" if is_search else "station_list"
        self._set_status(f"Szukam: {value}…")
        self._clear_content()
        sp = Gtk.Spinner(); sp.start()
        self._content_box.pack_start(sp, True, True, 50)
        self._content_box.show_all()
        threading.Thread(
            target=self._bg_stations,
            args=(cat_type, value, is_search, None),
            daemon=True
        ).start()

    def _show_advanced_search(self, params):
        self._push_nav()
        self._current_view = "search"
        self._set_status("Zaawansowane wyszukiwanie…")
        self._clear_content()
        sp = Gtk.Spinner(); sp.start()
        self._content_box.pack_start(sp, True, True, 50)
        self._content_box.show_all()
        threading.Thread(
            target=self._bg_stations,
            args=("advanced", "", True, params),
            daemon=True
        ).start()

    def _bg_stations(self, cat_type, value, is_search, adv_params):
        results = []
        try:
            if adv_params:
                p = {}
                for k in ("name", "country", "tag", "language", "codec"):
                    if adv_params.get(k):
                        p[k] = adv_params[k]
                if adv_params.get("bitrate", 0) > 0:
                    p["bitrateMin"] = adv_params["bitrate"]
                if adv_params.get("has_favicon"):
                    p["has_favicon"] = "true"
                p["order"] = adv_params.get("order", "name")
                p["limit"] = adv_params.get("limit", 200)
                results = self._rb.search(**p)
            elif is_search:
                results = self._rb.search(name=value, limit=500)
            elif cat_type == "countries":
                results = self._rb.search(country=value, limit=500)
            elif cat_type == "tags":
                results = self._rb.search(tag=value, limit=500)
            elif cat_type == "languages":
                results = self._rb.search(language=value, limit=500)
            elif cat_type == "local":
                country = self._geo_country or "Poland"
                results = self._rb.search(country=country, limit=500)
            elif cat_type == "top":
                results = self._rb.topvote(500)
        except Exception as e:
            print(f"[PR] API Error: {e}")
        GLib.idle_add(self._populate_stations_grid, results)

    # ══════════════════════════════════════════════════════════════════════
    #  Renderowanie listy / kafelków
    # ══════════════════════════════════════════════════════════════════════
    def _populate_stations_grid(self, stations, push=False, skip_alpha=False):
        if push:
            self._push_nav()
        if self._current_view not in ("search", "favorites", "history", "json_group"):
            self._current_view = "station_list"
        self._current_stations = stations
        self._clear_content()

        if not stations:
            lbl = Gtk.Label(label="Nie znaleziono żadnych stacji.")
            lbl.set_opacity(0.55)
            self._content_box.pack_start(lbl, True, True, 50)
            self._content_box.show_all()
            self._set_status("Brak wyników")
            return

        try:
            stations.sort(key=lambda x: x.get("name", "").lower())
        except Exception:
            pass

        if not skip_alpha and len(stations) > 10 and self._current_view != "home":
            self._create_alphabet_bar(stations)

        self._set_status(f"Znaleziono: {len(stations)} stacji")
        self._populate_stations_widget(stations)

    def _populate_stations_widget(self, stations):
        size = ICON_SIZES.get(self._icon_size_val, 64)

        if self._view_mode == "grid":
            flow = Gtk.FlowBox()
            flow.set_min_children_per_line(3)
            flow.set_max_children_per_line(8)
            flow.set_selection_mode(Gtk.SelectionMode.NONE)
            flow.set_homogeneous(True)
            cards = []
            for s in stations:
                card = StationCard(s, self._play_station, self._toggle_favorite, self._pm, size)
                flow.add(card)
                cards.append((card, s.get("favicon", "")))
            self._content_box.pack_start(flow, True, True, 0)
            self._content_box.show_all()
            threading.Thread(target=self._bg_covers_grid, args=(cards, size), daemon=True).start()

        elif self._view_mode in ("list", "detail"):
            show_details = (self._view_mode == "detail")
            listbox = Gtk.ListBox()
            listbox.set_selection_mode(Gtk.SelectionMode.NONE)
            listbox.set_header_func(
                lambda r, b, _: r.set_header(Gtk.Separator()) if b and not r.get_header() else None,
                None
            )
            img_refs = []
            for s in stations:
                row, img = self._make_list_row(s, size, show_details)
                listbox.add(row)
                img_refs.append((img, s.get("favicon", ""), size))
            self._content_box.pack_start(listbox, True, True, 0)
            self._content_box.show_all()
            threading.Thread(target=self._bg_covers_list, args=(img_refs,), daemon=True).start()

    def _make_list_row(self, station, size, show_details):
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_start(8); box.set_margin_end(8)
        box.set_margin_top(4);   box.set_margin_bottom(4)

        # Cover
        img = Gtk.Image()
        img.set_size_request(size, size)
        _safe_icon(img, "radio")
        box.pack_start(img, False, False, 0)

        # Tekst
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        n = Gtk.Label()
        n.set_markup(f"<b>{GLib.markup_escape_text(station.get('name', '?'))}</b>")
        n.set_xalign(0)
        n.set_ellipsize(Pango.EllipsizeMode.END)
        vbox.pack_start(n, True, True, 0)

        if show_details:
            parts = []
            if station.get("country"):  parts.append(station["country"])
            codec   = station.get("codec", "")
            bitrate = station.get("bitrate", 0)
            tags    = (station.get("tags") or "").strip()
            lang    = (station.get("language") or "").strip()
            votes   = station.get("votes", 0)
            if codec:   parts.append(codec)
            if bitrate: parts.append(f"{bitrate} kbps")
            if tags:    parts.append(tags[:25])
            if lang:    parts.append(f"🗣 {lang}")
            if votes:   parts.append(f"👍 {votes}")
            d = Gtk.Label(label="  •  ".join(parts))
            d.set_opacity(0.6); d.set_xalign(0)
            d.set_ellipsize(Pango.EllipsizeMode.END)
            vbox.pack_start(d, False, False, 0)

        box.pack_start(vbox, True, True, 0)

        # Prawa strona: gwiazdki + ulubione
        url = station.get("url_resolved") or station.get("url", "")
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        right.set_valign(Gtk.Align.CENTER)

        stars = StarRating(self._pm.get_rating(url), editable=False)
        right.pack_start(stars, False, False, 0)

        btn_f = Gtk.Button()
        btn_f.set_relief(Gtk.ReliefStyle.NONE)
        btn_f.set_can_focus(False)
        btn_f.set_label("★" if self._pm.is_favorite(url) else "☆")
        btn_f.connect("clicked", lambda _w, s=station, b=btn_f: self._row_fav_toggle(s, b))
        right.pack_start(btn_f, False, False, 0)

        box.pack_start(right, False, False, 0)
        row.add(box)

        row.url          = url
        row.title        = station.get("name", "")
        row.station_data = station
        row.connect("button-release-event", self._on_row_click)
        return row, img

    def _on_row_click(self, row, event):
        if event.button == 1:
            self._play_station(row.url, row.title, row.station_data)

    def _row_fav_toggle(self, station, btn):
        self._pm.toggle_favorite(station)
        url = station.get("url_resolved") or station.get("url", "")
        btn.set_label("★" if self._pm.is_favorite(url) else "☆")
        self._info_panel.refresh_fav_button()

    # ── Ładowanie covers ──────────────────────────────────────────────────
    def _bg_covers_grid(self, cards, size):
        for card, favicon in cards:
            if not favicon:
                continue
            pix = fetch_pixbuf(favicon, size)
            if pix:
                GLib.idle_add(card.set_cover, pix)

    def _bg_covers_list(self, img_refs):
        for img, favicon, size in img_refs:
            if not favicon:
                continue
            pix = fetch_pixbuf(favicon, size)
            if pix:
                GLib.idle_add(self._set_img, img, pix, size)

    @staticmethod
    def _set_img(img, pix, size):
        img.set_from_pixbuf(pix.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR))

    # ── Pasek alfabetyczny ────────────────────────────────────────────────
    def _create_alphabet_bar(self, stations):
        for ch in self._alpha_bar.get_children():
            self._alpha_bar.remove(ch)
        letters = sorted({s.get("name", " ")[0].upper() for s in stations
                          if s.get("name", "")})
        btn_all = Gtk.Button(label="Wszystkie")
        btn_all.connect("clicked", lambda _: self._populate_stations_grid(self._current_stations))
        self._alpha_bar.pack_start(btn_all, False, False, 2)
        for letter in letters:
            btn = Gtk.Button(label=letter)
            btn.connect("clicked", lambda _w, l=letter: self._filter_alpha(l))
            self._alpha_bar.pack_start(btn, False, False, 2)
        self._alpha_bar.show_all()

    def _filter_alpha(self, letter):
        filtered = [s for s in self._current_stations
                    if s.get("name", "").upper().startswith(letter)]
        self._populate_stations_grid(filtered, skip_alpha=True)

    # ── Odtwarzanie ───────────────────────────────────────────────────────
    def _play_station(self, url, title, station_data=None):
        self._play_url(url, title)
        if station_data:
            self._info_panel.update_station(station_data)
            self._pm.add_history(station_data)

    def _play_url(self, url, title):
        if not url:
            return
        entry = self._db.entry_lookup_by_location(url)
        if entry is None:
            entry = RB.RhythmDBEntry.new(self._db, self._entry_type, url)
            if entry:
                self._db.entry_set(entry, RB.RhythmDBPropType.TITLE, title)
                self._db.commit()
        if entry:
            self._shell.props.shell_player.play_entry(entry, self)

    # ── JSON ──────────────────────────────────────────────────────────────
    def _load_json(self):
        if os.path.exists(JSON_PATH):
            try:
                with open(JSON_PATH, "r", encoding="utf-8") as f:
                    self._json_data = json.load(f)
            except Exception as e:
                print(f"[PR] Błąd JSON: {e}")

    def delete_thyself(self):
        pass


# ══════════════════════════════════════════════════════════════════════════════
#  Plugin
# ══════════════════════════════════════════════════════════════════════════════
class PolskieRadioPlugin(GObject.Object, Peas.Activatable):
    __gtype_name__ = "PolskieRadioPlugin"
    object = GObject.Property(type=GObject.Object)

    def do_activate(self):
        shell = self.object
        db = shell.props.db
        self.entry_type = PREntryType()
        db.register_entry_type(self.entry_type)
        source = GObject.new(
            PRSource, shell=shell,
            name="Radio PL & World",
            entry_type=self.entry_type
        )
        source.setup(db, shell, self.entry_type)
        group = (RB.DisplayPageGroup.get_by_id("radio")
                 or RB.DisplayPageGroup.get_by_id("library"))
        shell.append_display_page(source, group)
        self.source = source

    def do_deactivate(self):
        if hasattr(self, "source") and self.source:
            self.source.delete_thyself()
            self.source = None
