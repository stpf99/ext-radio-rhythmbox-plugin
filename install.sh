#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
#  Polskie Radio — Plugin do Rhythmbox — Instalator
# ─────────────────────────────────────────────────────────────────────────────
set -e

PLUGIN_SRC="$(cd "$(dirname "$0")/polskieradio" && pwd)"

# ── Wykryj typ instalacji Rhythmbox ──────────────────────────────────────────
detect_rhythmbox() {
    if flatpak list 2>/dev/null | grep -qi "rhythmbox"; then
        INSTALL_TYPE="flatpak"
        FLATPAK_ID=$(flatpak list 2>/dev/null | grep -i rhythmbox | awk '{print $2}' | head -1)
        PLUGIN_DIR="$HOME/.var/app/${FLATPAK_ID}/data/rhythmbox/plugins/polskieradio"
        return
    fi
    if snap list 2>/dev/null | grep -qi "rhythmbox"; then
        INSTALL_TYPE="snap"
        PLUGIN_DIR="$HOME/snap/rhythmbox/current/.local/share/rhythmbox/plugins/polskieradio"
        return
    fi
    INSTALL_TYPE="native"
    PLUGIN_DIR="$HOME/.local/share/rhythmbox/plugins/polskieradio"
}

detect_rhythmbox

echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║        Polskie Radio Plugin — Instalator                     ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Typ instalacji Rhythmbox : $INSTALL_TYPE"
echo "  Katalog docelowy         : $PLUGIN_DIR"
echo ""

# ── Sprawdź zależności Python ──────────────────────────────────────────────
echo "  Sprawdzam zależności Python…"

PYTHON_CMD="python3"
[ "$INSTALL_TYPE" = "flatpak" ] && PYTHON_CMD="flatpak run --command=python3 $FLATPAK_ID"

PYTHON_CHECK=$($PYTHON_CMD -c \
    "import gi; gi.require_version('RB','3.0'); from gi.repository import RB; print('OK')" 2>&1)

if echo "$PYTHON_CHECK" | grep -q "OK"; then
    echo "  ✅ Zależności GObject/RB: OK"
else
    echo "  ⚠  Brak gi.repository.RB — zainstaluj:"
    if command -v apt &>/dev/null; then
        echo "     sudo apt install gir1.2-rb-3.0 python3-gi"
    elif command -v dnf &>/dev/null; then
        echo "     sudo dnf install rhythmbox-devel python3-gobject"
    elif command -v pacman &>/dev/null; then
        echo "     sudo pacman -S python-gobject"
    fi
fi

# ── Sprawdź GStreamer HLS ──────────────────────────────────────────────────
echo "  Sprawdzam GStreamer HLS (m3u8)…"
if gst-inspect-1.0 hlsdemux &>/dev/null 2>&1; then
    echo "  ✅ GStreamer HLS (hlsdemux): OK"
else
    echo "  ⚠  Brak hlsdemux — strumienie HLS mogą nie działać!"
    echo "     Zainstaluj: sudo apt install gstreamer1.0-plugins-bad gstreamer1.0-libav"
fi

echo ""

# ── Instalacja ────────────────────────────────────────────────────────────
echo "  Instaluję plugin…"
mkdir -p "$PLUGIN_DIR"
cp "$PLUGIN_SRC/polskieradio.plugin" "$PLUGIN_DIR/"
cp "$PLUGIN_SRC/polskieradio.py"     "$PLUGIN_DIR/"
cp "$PLUGIN_SRC/stations.json"     "$PLUGIN_DIR/"
echo ""
echo "  ✅  Gotowe!"
echo ""
echo "  Następne kroki:"
if [ "$INSTALL_TYPE" = "flatpak" ]; then
    echo "    1. Uruchom:  flatpak run $FLATPAK_ID"
else
    echo "    1. Uruchom Rhythmbox"
fi
echo "    2. Idź do:   Edit → Plugins"
echo "    3. Zaznacz:  Polskie Radio"
echo "    4. W lewym panelu pojawi się źródło 'Polskie Radio'"
echo ""

if pgrep -x rhythmbox &>/dev/null; then
    echo "  💡 Rhythmbox jest uruchomiony."
    echo "     Wyłącz i włącz plugin w Edit → Plugins aby załadować nową wersję."
    echo ""
fi
