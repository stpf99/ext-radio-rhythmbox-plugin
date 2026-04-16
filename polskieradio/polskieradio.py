"""
PolskieRadio & RadioBrowser Plugin for Rhythmbox  —  v4.2
UI: Kafelki (FlowBox), Lista, Szczegółowa. Okładki, metadane.
Nawigacja alfabetyczna, zmiana rozmiaru ikon.
"""

import gi
gi.require_version("RB", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Peas", "1.0")

from gi.repository import GObject, GLib, Gtk, Gdk, RB, GdkPixbuf, Peas, Pango
import json
import os
import threading
import urllib.request

try:
    from pyradios import RadioBrowser
    HAS_PYRADIOS = True
except ImportError:
    HAS_PYRADIOS = False
    print("[PR] Biblioteka 'pyradios' nie znaleziona. Funkcje RadioBrowser wyłączone.")

# ── Konfiguracja ────────────────────────────────────────────────
PLUGIN_DIR = os.path.dirname(__file__)
JSON_PATH = os.path.join(PLUGIN_DIR, "stations.json")
COVER_SIZE = 140

ICON_SIZES = {
    "Mini": 32,
    "Małe": 48,
    "Standard": 64,
    "Duże": 96,
    "Największe": 128
}

# ── Pomocnicze ────────────────────────────────────────────────

def fetch_pixbuf(url, size=COVER_SIZE):
    if not url: return None
    if url.startswith("//"): url = "https:" + url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Rhythmbox-PRPlugin/4.2"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read()
        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        pix = loader.get_pixbuf()
        
        w, h = pix.get_width(), pix.get_height()
        min_dim = min(w, h)
        x, y = (w - min_dim) // 2, (h - min_dim) // 2
        pix = pix.new_subpixbuf(x, y, min_dim, min_dim)
        
        return pix.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
    except Exception as e:
        return None

# ── Custom Widgets ────────────────────────────────────────────

class StationCard(Gtk.EventBox):
    def __init__(self, station_data, play_callback, size=COVER_SIZE): 
        super().__init__()
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_size_request(size + 40, -1) 
        box.set_margin_start(5); box.set_margin_end(5)
        box.set_margin_top(5); box.set_margin_bottom(5)
        self.add(box)
        
        url = station_data.get("url_resolved") or station_data.get("url")
        name = station_data.get("name", "Nieznana")
        
        frame = Gtk.Frame(shadow_type=Gtk.ShadowType.ETCHED_IN)
        self.img = Gtk.Image()
        self.img.set_size_request(size, size)
        
        theme = Gtk.IconTheme.get_default()
        if theme.has_icon("radio"):
            self.img.set_from_icon_name("radio", Gtk.IconSize.DIALOG)
        else:
            self.img.set_from_stock(Gtk.STOCK_MEDIA_PLAY, Gtk.IconSize.DIALOG)
            
        frame.add(self.img)
        box.pack_start(frame, False, False, 0)
        
        lbl_name = Gtk.Label()
        lbl_name.set_line_wrap(True)
        lbl_name.set_lines(2)
        lbl_name.set_justify(Gtk.Justification.CENTER)
        
        if size < 64:
            lbl_name.set_markup(f"<small><b>{name}</b></small>")
        else:
            lbl_name.set_markup(f"<b>{name}</b>")
            
        box.pack_start(lbl_name, False, False, 0)
        
        if size >= 96:
            country = station_data.get("country", "")
            if country:
                lbl_country = Gtk.Label(label=country)
                lbl_country.set_opacity(0.7)
                lbl_country.set_justify(Gtk.Justification.CENTER)
                box.pack_start(lbl_country, False, False, 0)

        self.connect("button-release-event", lambda w, e: play_callback(url, name) if e.button == 1 else None)

    def set_cover(self, pixbuf):
        if pixbuf:
            w, h = self.img.get_size_request()
            scaled = pixbuf.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)
            self.img.set_from_pixbuf(scaled)

class CategoryButton(Gtk.EventBox):
    def __init__(self, text, subtext, icon_name, click_callback, callback_data):
        super().__init__()
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        self.add(box)
        
        img = Gtk.Image()
        theme = Gtk.IconTheme.get_default()
        if theme.has_icon(icon_name):
            img.set_from_icon_name(icon_name, Gtk.IconSize.LARGE_TOOLBAR)
        box.pack_start(img, False, False, 0)
        
        txt_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        lbl = Gtk.Label()
        lbl.set_markup(f"<b>{text}</b>")
        lbl.set_xalign(0)
        txt_box.pack_start(lbl, False, False, 0)
        
        if subtext:
            lbl_sub = Gtk.Label(label=subtext)
            lbl_sub.set_opacity(0.6)
            lbl_sub.set_xalign(0)
            txt_box.pack_start(lbl_sub, False, False, 0)
            
        box.pack_start(txt_box, True, True, 0)
        
        self.connect("button-release-event", lambda w, e: click_callback(callback_data) if e.button == 1 else None)

# ── Entry Type ────────────────────────────────────────────────

class PREntryType(RB.RhythmDBEntryType):
    __gtype_name__ = "PREntryType"
    def __init__(self):
        RB.RhythmDBEntryType.__init__(self, name="pr-stream", save_to_disk=False)
    def can_sync_metadata(self, entry):
        return False

# ── Source ────────────────────────────────────────────────────

class PRSource(RB.Source):
    __gtype_name__ = "PRSource"

    def setup(self, db, shell, entry_type):
        self._db = db
        self._shell = shell
        self._entry_type = entry_type
        self._rb = RadioBrowser(user_agent="Rhythmbox-PRPlugin/4.2") if HAS_PYRADIOS else None
        
        self._history = []
        self._current_view = "home"
        self._current_data = None
        self._current_stations = []
        
        self._json_data = {}
        self._load_json()
        self._cover_threads = []

        # Inicjalizacja stanu UI
        self._view_mode = 'grid' 
        self._icon_size_val = "Standard"

        self._build_ui()
        self._show_home()

    def _build_ui(self):
        self._box_main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        
        # --- Toolbar ---
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.set_margin_top(8); toolbar.set_margin_start(10); toolbar.set_margin_end(10)
        
        self._btn_back = Gtk.Button(label="⬅ Wstecz")
        self._btn_back.set_sensitive(False)
        self._btn_back.connect("clicked", self._on_back)
        toolbar.pack_start(self._btn_back, False, False, 0)
        
        self._btn_home = Gtk.Button(label="🏠 Start")
        self._btn_home.connect("clicked", lambda w: self._show_home())
        toolbar.pack_start(self._btn_home, False, False, 0)
        
        toolbar.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 4)
        
        # Wybór trybu
        self._combo_view = Gtk.ComboBoxText()
        for mode in ["Kafelki", "Lista", "Szczegółowa"]:
            self._combo_view.append_text(mode)
        self._combo_view.set_active(0)
        self._combo_view.connect("changed", self._on_view_mode_changed)
        toolbar.pack_start(Gtk.Label(label=" Widok:"), False, False, 0)
        toolbar.pack_start(self._combo_view, False, False, 0)
        
        toolbar.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 4)

        # Wybór rozmiaru
        self._combo_size = Gtk.ComboBoxText()
        for size_name in ICON_SIZES.keys():
            self._combo_size.append_text(size_name)
        self._combo_size.set_active_id("Standard")
        self._combo_size.connect("changed", self._on_icon_size_changed)
        toolbar.pack_start(Gtk.Label(label=" Rozmiar:"), False, False, 0)
        toolbar.pack_start(self._combo_size, False, False, 0)

        toolbar.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 10)

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text("Szukaj...")
        self._search_entry.set_width_chars(20)
        self._search_entry.connect("activate", self._on_global_search)
        self._search_entry.connect("changed", self._on_search_text_changed)
        toolbar.pack_start(self._search_entry, False, False, 0)
        
        self._status_lbl = Gtk.Label(label="")
        self._status_lbl.set_halign(Gtk.Align.END)
        self._status_lbl.set_hexpand(True)
        toolbar.pack_start(self._status_lbl, True, True, 10)
        
        # --- Pasek Alfabetyczny ---
        self._alpha_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self._alpha_bar.set_margin_start(10); self._alpha_bar.set_margin_end(10)
        self._alpha_bar.set_margin_bottom(5)
        self._alpha_bar.hide()
        
        # --- Główny kontener ---
        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_shadow_type(Gtk.ShadowType.NONE)
        self._viewport = Gtk.Viewport()
        self._scroll.add(self._viewport)
        self._content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._viewport.add(self._content_box)
        
        self._box_main.pack_start(toolbar, False, False, 0)
        self._box_main.pack_start(self._alpha_bar, False, False, 0)
        self._box_main.pack_start(self._scroll, True, True, 0)
        self._box_main.show_all()
        
        self.pack_start(self._box_main, True, True, 0)
        self.show_all()

    def _clear_content(self):
        self._cover_threads.clear() 
        for child in self._content_box.get_children():
            self._content_box.remove(child)
        for child in self._alpha_bar.get_children():
            self._alpha_bar.remove(child)
        self._alpha_bar.hide()

    # ── Nawigacja ────────────────────────────────────────────────

    def _push_history(self):
        self._history.append({
            "view": self._current_view,
            "data": self._current_data,
            "title": self._status_lbl.get_text()
        })
        self._btn_back.set_sensitive(True)

    def _on_back(self, widget):
        if not self._history: return
        state = self._history.pop()
        self._btn_back.set_sensitive(len(self._history) > 0)
        self._current_view = state["view"]
        self._current_data = state["data"]
        self._set_status(state["title"])
        
        if self._current_view == "home": self._show_home(push=False)
        elif self._current_view in ["station_list", "search", "json_group"]:
            if isinstance(self._current_data, list):
                self._populate_stations_grid(self._current_data, push=False)
            elif isinstance(self._current_data, str) and self._current_view == "json_group":
                self._show_json_group(self._current_data, push=False)
        elif self._current_view == "category_list":
             self._populate_category_list(self._current_data["items"], self._current_data["type"], push=False)

    def _set_status(self, txt):
        self._status_lbl.set_text(txt)

    # ── UI Actions ────────────────────────────────────────────────

    def _on_view_mode_changed(self, combo):
        mode_text = combo.get_active_text()
        if mode_text == "Kafelki": self._view_mode = 'grid'
        elif mode_text == "Lista": self._view_mode = 'list'
        elif mode_text == "Szczegółowa": self._view_mode = 'detail'
        
        if self._current_view in ["station_list", "search"]:
            self._populate_stations_grid(self._current_stations)

    def _on_icon_size_changed(self, combo):
        # 🔧 POPRAWKA: Sprawdzamy czy get_active_id() zwrócił poprawną wartość
        val = combo.get_active_id()
        if val and val in ICON_SIZES:
            self._icon_size_val = val
            if self._current_view in ["station_list", "search"]:
                self._populate_stations_grid(self._current_stations)

    def _on_global_search(self, entry):
        query = entry.get_text().strip()
        if not query or not self._rb: return
        self._show_stations("search", query, is_search=True)

    def _on_search_text_changed(self, entry):
        text = entry.get_text().lower()
        if not text:
            self._populate_stations_grid(self._current_stations)
            return
        filtered = [s for s in self._current_stations if text in s.get("name", "").lower()]
        self._populate_stations_grid(filtered, skip_alpha=True)

    # ── Pasek Alfabetyczny ────────────────────────────────────────

    def _create_alphabet_bar(self, stations):
        if not stations: return
        self._alpha_bar.hide()
        for child in self._alpha_bar.get_children(): self._alpha_bar.remove(child)
        
        letters = set()
        for s in stations:
            name = s.get("name", "")
            if name: letters.add(name[0].upper())
        sorted_letters = sorted(list(letters))
        
        btn_all = Gtk.Button(label="All")
        btn_all.connect("clicked", lambda w: self._populate_stations_grid(self._current_stations))
        self._alpha_bar.pack_start(btn_all, False, False, 2)
        
        for letter in sorted_letters:
            btn = Gtk.Button(label=letter)
            btn.connect("clicked", lambda w, l=letter: self._filter_by_letter(l))
            self._alpha_bar.pack_start(btn, False, False, 2)
        self._alpha_bar.show_all()

    def _filter_by_letter(self, letter):
        filtered = [s for s in self._current_stations if s.get("name", "").upper().startswith(letter)]
        self._populate_stations_grid(filtered, skip_alpha=True)

    # ── Widoki ────────────────────────────────────────────────────

    def _show_home(self, push=True):
        if push: 
            self._history.clear()
            self._btn_back.set_sensitive(False)
            self._current_view = "home"
        self._clear_content()
        self._set_status("Wybierz kategorię")
        
        # Lokalne
        lbl_loc = Gtk.Label()
        lbl_loc.set_markup("<b>── MOJE STACJE (LOKALNE) ──</b>")
        lbl_loc.set_margin_start(15)
        self._content_box.pack_start(lbl_loc, False, False, 10)
        
        for group_name in sorted(self._json_data.keys()):
            btn = CategoryButton(group_name, "Lokalna baza", "folder-music", self._on_json_group_click, group_name)
            self._content_box.pack_start(btn, False, False, 0)
            
        # RadioBrowser
        if self._rb:
            lbl_rb = Gtk.Label()
            lbl_rb.set_markup("<b>── ŚWIAT (RADIO BROWSER) ──</b>")
            lbl_rb.set_margin_start(15)
            lbl_rb.set_margin_top(20)
            self._content_box.pack_start(lbl_rb, False, False, 10)
            
            categories = [
                ("local", "📻 Stacje z Polski", "Polska", "radio"),
                ("top", "🔥 Popularne na świecie", "Top 500", "radio"),
                ("countries", "🌍 Przeglądaj Kraje", "", "folder"),
                ("tags", "🎵 Przeglądaj Gatunki", "", "folder"),
                ("languages", "🗣️ Przeglądaj Języki", "", "folder")
            ]
            
            for cid, name, info, icon in categories:
                btn = CategoryButton(name, info, icon, self._on_category_click, cid)
                self._content_box.pack_start(btn, False, False, 0)
        self._content_box.show_all()

    def _show_json_group(self, group_name, push=True):
        if push: self._push_history()
        self._current_view = "json_group"
        self._current_data = group_name
        
        self._clear_content()
        streams = self._json_data.get(group_name, {}).get("streams", [])
        self._set_status(f"Stacje: {group_name}")
        
        station_list = []
        for s in streams:
            station_list.append({
                "name": s.get("name", "Stream"),
                "url": s.get("url", ""),
                "url_resolved": s.get("url", ""),
                "favicon": "", 
                "country": "Lokalne",
                "codec": "MP3", "bitrate": "", "tags": ""
            })
        self._populate_stations_grid(station_list, push=False)

    def _show_category_list(self, category_type):
        self._push_history()
        self._current_view = "category_list"
        self._set_status(f"Wczytuję listę: {category_type}...")
        self._clear_content()
        spinner = Gtk.Spinner()
        spinner.start()
        self._content_box.pack_start(spinner, True, True, 50)
        self._content_box.show_all()
        threading.Thread(target=self._bg_load_category, args=(category_type,), daemon=True).start()

    def _bg_load_category(self, cat_type):
        data = []
        try:
            if cat_type == "countries": data = self._rb.countries()
            elif cat_type == "tags": data = self._rb.tags()
            elif cat_type == "languages": data = self._rb.languages()
        except Exception as e:
            print(f"[PR] API Error: {e}")
        GLib.idle_add(self._populate_category_list, data, cat_type)

    def _populate_category_list(self, data, cat_type, push=False):
        if push: self._push_history()
        self._current_view = "category_list"
        self._current_data = {"type": cat_type, "items": data}
        
        self._clear_content()
        self._set_status(f"Lista: {cat_type.capitalize()}")
        
        for item in data:
            name = item.get("name", "Nieznany")
            count = item.get("stationcount", 0)
            btn = CategoryButton(name, f"Stacji: {count}", "folder", self._on_subcategory_click, name)
            self._content_box.pack_start(btn, False, False, 0)
        self._content_box.show_all()

    def _show_stations(self, cat_type, value, is_search=False):
        self._push_history()
        self._current_view = "station_list" if not is_search else "search"
        self._set_status(f"Szukam stacji: {value}...")
        self._clear_content()
        spinner = Gtk.Spinner()
        spinner.start()
        self._content_box.pack_start(spinner, True, True, 50)
        self._content_box.show_all()
        threading.Thread(target=self._bg_load_stations, args=(cat_type, value, is_search), daemon=True).start()

    def _bg_load_stations(self, cat_type, value, is_search):
        results = []
        try:
            limit = 500
            if is_search:
                results = self._rb.search(name=value, limit=limit)
            elif cat_type == "countries":
                results = self._rb.search(country=value, limit=limit)
            elif cat_type == "tags":
                results = self._rb.search(tag=value, limit=limit)
            elif cat_type == "languages":
                results = self._rb.search(language=value, limit=limit)
            elif cat_type == "local":
                results = self._rb.search(country="Poland", limit=limit)
            elif cat_type == "top":
                results = self._rb.topvote(500)
        except Exception as e:
            print(f"[PR] API Error: {e}")
        GLib.idle_add(self._populate_stations_grid, results)

    def _populate_stations_grid(self, stations, push=False, skip_alpha=False):
        if push: self._push_history()
        if self._current_view not in ["search"]: self._current_view = "station_list"
        self._current_stations = stations
        
        self._clear_content()
        if not stations:
            lbl = Gtk.Label(label="Nie znaleziono żadnych stacji.")
            self._content_box.pack_start(lbl, True, True, 50)
            self._content_box.show_all()
            self._set_status("Brak wyników")
            return

        try:
            stations.sort(key=lambda x: x.get("name", ""))
        except: pass

        if not skip_alpha and len(stations) > 10 and self._current_view != "home":
            self._create_alphabet_bar(stations)

        self._set_status(f"Znaleziono: {len(stations)} stacji")

        # 🔧 POPRAWKA: Bezpieczne pobieranie rozmiaru
        current_size_key = self._icon_size_val
        if current_size_key not in ICON_SIZES:
            current_size_key = "Standard"
        size = ICON_SIZES[current_size_key]

        if self._view_mode == 'grid':
            flow = Gtk.FlowBox()
            flow.set_min_children_per_line(3)
            flow.set_max_children_per_line(6)
            flow.set_selection_mode(Gtk.SelectionMode.NONE)
            flow.set_homogeneous(True)
            
            for s in stations:
                card = StationCard(s, self._play_url, size=size) 
                flow.add(card)
            self._content_box.pack_start(flow, True, True, 0)
            self._content_box.show_all()

        elif self._view_mode in ['list', 'detail']:
            listbox = Gtk.ListBox()
            listbox.set_selection_mode(Gtk.SelectionMode.NONE)
            listbox.set_header_func(self._list_header_func, None)
            show_details = (self._view_mode == 'detail')
            
            for s in stations:
                row = self._create_list_row(s, size, show_details)
                listbox.add(row)
            self._content_box.pack_start(listbox, True, True, 0)
            self._content_box.show_all()

        if self._view_mode == 'grid':
            threading.Thread(target=self._bg_load_covers, args=(stations,), daemon=True).start()

    def _create_list_row(self, station, size, show_details):
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_top(5); box.set_margin_bottom(5); box.set_margin_start(10); box.set_margin_end(10)
        
        img = Gtk.Image()
        img.set_size_request(size, size)
        theme = Gtk.IconTheme.get_default()
        if theme.has_icon("radio"): img.set_from_icon_name("radio", Gtk.IconSize.DIALOG)
        
        box.pack_start(img, False, False, 0)
        
        txt_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name_lbl = Gtk.Label()
        name_lbl.set_markup(f"<b>{station.get('name', 'Unknown')}</b>")
        name_lbl.set_xalign(0)
        name_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        txt_box.pack_start(name_lbl, True, True, 0)
        
        if show_details:
            details = f"{station.get('country', '')} • {station.get('codec', '')} • {station.get('bitrate', '')}k"
            det_lbl = Gtk.Label(label=details)
            det_lbl.set_opacity(0.6)
            det_lbl.set_xalign(0)
            det_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            txt_box.pack_start(det_lbl, False, False, 0)
            
        box.pack_start(txt_box, True, True, 0)
        row.add(box)
        
        row.url = station.get("url_resolved") or station.get("url")
        row.title = station.get("name")
        row.img_widget = img 
        row.favicon_url = station.get("favicon", "")
        row.connect("button-release-event", self._on_list_row_clicked)
        return row

    def _on_list_row_clicked(self, row, event):
        if event.button == 1:
            self._play_url(row.url, row.title)

    def _list_header_func(self, row, before, user_data):
        if before and not row.get_header():
            row.set_header(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

    def _bg_load_covers(self, stations):
        # Proste ładowanie dla FlowBox (Grid)
        # Implementacja uproszczona - wymagałaby rozbudowanej logiki dla ListBox
        pass 

    # ── Akcje ────────────────────────────────────────────────────

    def _on_json_group_click(self, group_name):
        self._show_json_group(group_name)

    def _on_category_click(self, cat_type):
        if cat_type in ["countries", "tags", "languages"]:
            self._show_category_list(cat_type)
        else:
            self._show_stations(cat_type, cat_type)

    def _on_subcategory_click(self, value):
        if self._current_view == "category_list" and self._current_data:
            cat_type = self._current_data.get("type", "countries")
            self._show_stations(cat_type, value)

    def _play_url(self, url, title):
        if not url: return
        self._search_entry.set_sensitive(False)
        GLib.timeout_add(300, lambda: self._search_entry.set_sensitive(True))
        
        entry = self._db.entry_lookup_by_location(url)
        if entry is None:
            entry = RB.RhythmDBEntry.new(self._db, self._entry_type, url)
            if entry:
                self._db.entry_set(entry, RB.RhythmDBPropType.TITLE, title)
                self._db.commit()
        if entry:
            self._shell.props.shell_player.play_entry(entry, self)

    def _load_json(self):
        if os.path.exists(JSON_PATH):
            try:
                with open(JSON_PATH, 'r', encoding='utf-8') as f:
                    self._json_data = json.load(f)
            except Exception as e:
                print(f"[PR] Błąd JSON: {e}")

    def delete_thyself(self):
        pass

# ── Plugin ────────────────────────────────────────────────

class PolskieRadioPlugin(GObject.Object, Peas.Activatable):
    __gtype_name__ = 'PolskieRadioPlugin'
    object = GObject.Property(type=GObject.Object)

    def do_activate(self):
        shell = self.object
        db = shell.props.db
        self.entry_type = PREntryType()
        db.register_entry_type(self.entry_type)
        
        source = GObject.new(PRSource, shell=shell, name="Radio PL & World", entry_type=self.entry_type)
        source.setup(db, shell, self.entry_type)
        
        group = RB.DisplayPageGroup.get_by_id("radio")
        if group is None:
            group = RB.DisplayPageGroup.get_by_id("library")
        
        shell.append_display_page(source, group)
        self.source = source

    def do_deactivate(self):
        if hasattr(self, 'source') and self.source:
            self.source.delete_thyself()
            self.source = None
