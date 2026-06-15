#!/usr/bin/env python3

import math
import time
import os
import requests
import configparser
from PIL import Image, ImageDraw, ImageFont


# ============================================================
# Plane Radar Pi - Proof of Concept
# Raspberry Pi 4B + GC9A01 240x240 framebuffer display
# ============================================================


# -----------------------------
# USER CONFIG
# -----------------------------

# Config file path.
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.ini")


def load_config():
    config = configparser.ConfigParser()

    defaults = {
        "center_lat": "30.14705507846894",
        "center_lon": "-95.39204791784302",
        "range_mi": "10",
        "refresh_seconds": "5",
    }

    config["radar"] = defaults
    config.read(CONFIG_PATH)

    radar_config = config["radar"]

    return {
        "center_lat": radar_config.getfloat("center_lat"),
        "center_lon": radar_config.getfloat("center_lon"),
        "range_mi": radar_config.getfloat("range_mi"),
        "refresh_seconds": radar_config.getint("refresh_seconds"),
    }


APP_CONFIG = load_config()

# Your selected radar center.
CENTER_LAT = APP_CONFIG["center_lat"]
CENTER_LON = APP_CONFIG["center_lon"]

# Radar/API range in miles.
# Smaller range = fewer aircraft.
RANGE_MI = APP_CONFIG["range_mi"]

# Refresh rate in seconds.
REFRESH_SECONDS = APP_CONFIG["refresh_seconds"]

# Your GC9A01 display is exposed by the Pi overlay as /dev/fb0.
FRAMEBUFFER = "/dev/fb0"

# Temporary image path used by fbi.
IMAGE_PATH = "/tmp/plane-radar.png"


# -----------------------------
# DISPLAY LAYOUT
# -----------------------------

WIDTH = 240
HEIGHT = 240

# Move radar slightly up to avoid bottom clipping.
CENTER_X = WIDTH // 2
CENTER_Y = (HEIGHT // 2) - 4

# Smaller radar circle to fit safely on round display.
RADAR_RADIUS = 112

# Colors
COLOR_BG = (2, 8, 20)
COLOR_RING_MAJOR = (0, 180, 90)
COLOR_RING_MINOR = (0, 80, 60)
COLOR_TEXT = (220, 220, 220)
COLOR_TEXT_DIM = (160, 210, 170)
COLOR_OWN_SHIP = (255, 255, 255)
COLOR_AIRCRAFT = (255, 70, 70)
COLOR_LABEL = (235, 235, 235)
COLOR_WARN = (255, 190, 80)


# -----------------------------
# FONT HELPERS
# -----------------------------

def load_font(size, bold=False):
    paths = []

    if bold:
        paths.append("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

    paths.extend([
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ])

    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass

    return ImageFont.load_default()


# Reverted to larger/readable font sizes.
FONT_TINY = load_font(10)
FONT_SMALL = load_font(10)
FONT_MED = load_font(12)
FONT_BOLD = load_font(12, bold=True)


# -----------------------------
# GEO MATH
# -----------------------------

def haversine_mi(lat1, lon1, lat2, lon2):
    """
    Distance between two lat/lon points in statute miles.
    """
    earth_radius_mi = 3958.8

    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)

    a = (
        math.sin(dp / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    )

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return earth_radius_mi * c


def bearing_deg(lat1, lon1, lat2, lon2):
    """
    Bearing from point 1 to point 2.
    0 degrees = north, 90 = east.
    """
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dl = math.radians(lon2 - lon1)

    y = math.sin(dl) * math.cos(p2)
    x = (
        math.cos(p1) * math.sin(p2)
        - math.sin(p1) * math.cos(p2) * math.cos(dl)
    )

    bearing = math.degrees(math.atan2(y, x))
    return (bearing + 360) % 360


def polar_to_screen(distance_mi, bearing, max_range_mi):
    """
    Convert distance/bearing into x/y screen position.
    """
    scale = min(distance_mi / max_range_mi, 1.0)
    radius = scale * RADAR_RADIUS

    angle = math.radians(bearing)

    x = CENTER_X + math.sin(angle) * radius
    y = CENTER_Y - math.cos(angle) * radius

    return int(x), int(y)


# -----------------------------
# ADS-B DATA
# -----------------------------

def fetch_aircraft():
    """
    Fetch aircraft near the configured location.

    opendata.adsb.fi returns aircraft in the top-level "ac" array.
    """
    url = (
        f"https://opendata.adsb.fi/api/v3/lat/{CENTER_LAT}/"
        f"lon/{CENTER_LON}/dist/{RANGE_MI}"
    )

    try:
        response = requests.get(url, timeout=8)
        response.raise_for_status()
        data = response.json()

        aircraft = data.get("ac", [])

        print(
            f"API aircraft: {len(aircraft)} | "
            f"API total: {data.get('total', 'n/a')}"
        )

        return aircraft

    except Exception as e:
        print(f"ADS-B fetch failed: {e}")
        return []


# -----------------------------
# AIRCRAFT FIELD HELPERS
# -----------------------------

def format_altitude(ac):
    """
    Format altitude in feet.

    Example:
    2500 ft -> 2500 ft
    """
    alt = ac.get("alt_baro")

    if alt is None or alt == "ground":
        alt = ac.get("alt_geom")

    if alt == "ground":
        return "GND"

    if isinstance(alt, int) or isinstance(alt, float):
        return f"{int(round(alt))} ft"

    return ""

def get_callsign(ac):
    callsign = (
        ac.get("flight")
        or ac.get("r")
        or ac.get("hex")
        or ""
    )

    return str(callsign).strip()


# -----------------------------
# DRAWING HELPERS
# -----------------------------

def draw_centered_text(draw, text, center_x, y, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    draw.text((center_x - text_width // 2, y), text, font=font, fill=fill)


def draw_aircraft_symbol(draw, x, y, track):
    """
    Draw a small aircraft triangle. If no track is available, draw a dot.
    """
    if track is None:
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=COLOR_AIRCRAFT)
        return

    heading = math.radians(track)
    size = 7

    nose = (
        int(x + math.sin(heading) * size),
        int(y - math.cos(heading) * size),
    )

    left = (
        int(x + math.sin(heading + 2.4) * size),
        int(y - math.cos(heading + 2.4) * size),
    )

    right = (
        int(x + math.sin(heading - 2.4) * size),
        int(y - math.cos(heading - 2.4) * size),
    )

    draw.polygon([nose, left, right], fill=COLOR_AIRCRAFT)


def draw_aircraft_label(draw, x, y, callsign, altitude, label_index):
    """
    Draw callsign on first line and altitude on second line.
    """
    if not callsign and not altitude:
        return

    callsign = str(callsign).strip()[:8]
    altitude = str(altitude).strip()

    lines = []

    if callsign:
        lines.append(callsign)

    if altitude:
        lines.append(altitude)

    if not lines:
        return

    line_gap = 1
    line_height = 11

    text_width = 0

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=FONT_TINY)
        text_width = max(text_width, bbox[2] - bbox[0])

    text_height = len(lines) * line_height + (len(lines) - 1) * line_gap

    # Default: place label to right of aircraft.
    tx = x + 7
    ty = y - 8

    # If near right edge, place label to left.
    if tx + text_width > WIDTH - 4:
        tx = x - text_width - 7

    # Keep inside top/bottom safe area.
    if ty < 4:
        ty = y + 7

    if ty + text_height > HEIGHT - 26:
        ty = HEIGHT - 26 - text_height

    # Slight alternating offset to reduce overlap.
    if label_index % 2 == 1:
        ty += 5

    # Draw callsign first, altitude below.
    for i, line in enumerate(lines):
        fill = COLOR_LABEL if i == 0 else COLOR_TEXT_DIM
        draw.text(
            (tx, ty + i * (line_height + line_gap)),
            line,
            fill=fill,
            font=FONT_TINY,
        )


# -----------------------------
# RADAR DRAWING
# -----------------------------

def draw_radar(aircraft):
    img = Image.new("RGB", (WIDTH, HEIGHT), COLOR_BG)
    draw = ImageDraw.Draw(img)

    # Main radar circle.
    draw.ellipse(
        (
            CENTER_X - RADAR_RADIUS,
            CENTER_Y - RADAR_RADIUS,
            CENTER_X + RADAR_RADIUS,
            CENTER_Y + RADAR_RADIUS,
        ),
        outline=COLOR_RING_MAJOR,
        width=2,
    )

    # Range rings.
    ring_fracs = [0.50, 0.75]

    for frac in ring_fracs:
        rr = int(RADAR_RADIUS * frac)
        ring_range = int(RANGE_MI * frac)

        draw.ellipse(
            (CENTER_X - rr, CENTER_Y - rr, CENTER_X + rr, CENTER_Y + rr),
            outline=COLOR_RING_MINOR,
            width=1,
        )

        # Ring label on lower-right part of each ring.
        label = f"{ring_range}"
        label_x = CENTER_X + int(rr * 0.62)
        label_y = CENTER_Y + int(rr * 0.62) - 5

        # Keep ring labels away from the bottom edge.
        if label_y < HEIGHT - 28:
            draw.text(
                (label_x, label_y),
                label,
                fill=COLOR_TEXT_DIM,
                font=FONT_SMALL,
            )

    # Crosshairs.
    draw.line(
        (CENTER_X, CENTER_Y - RADAR_RADIUS, CENTER_X, CENTER_Y + RADAR_RADIUS),
        fill=COLOR_RING_MINOR,
    )
    draw.line(
        (CENTER_X - RADAR_RADIUS, CENTER_Y, CENTER_X + RADAR_RADIUS, CENTER_Y),
        fill=COLOR_RING_MINOR,
    )

    # Cardinal direction labels.
    draw_centered_text(draw, "N", CENTER_X, 4, FONT_BOLD, COLOR_TEXT)
    draw_centered_text(draw, "S", CENTER_X, HEIGHT - 29, FONT_MED, COLOR_TEXT)
    draw.text((WIDTH - 18, CENTER_Y - 7), "E", fill=COLOR_TEXT, font=FONT_MED)
    draw.text((7, CENTER_Y - 7), "W", fill=COLOR_TEXT, font=FONT_MED)

    # Own location / center dot.
    draw.ellipse(
        (CENTER_X - 3, CENTER_Y - 3, CENTER_X + 3, CENTER_Y + 3),
        fill=COLOR_OWN_SHIP,
    )

    plotted = 0
    labeled = 0

    for ac in aircraft:
        lat = ac.get("lat")
        lon = ac.get("lon")

        if lat is None or lon is None:
            continue

        # For testing, do not hide ground aircraft.
        # Uncomment later if you want to hide them.
        # if ac.get("gnd") is True:
        #     continue

        distance = haversine_mi(CENTER_LAT, CENTER_LON, lat, lon)

        if distance > RANGE_MI:
            continue

        bearing = bearing_deg(CENTER_LAT, CENTER_LON, lat, lon)
        x, y = polar_to_screen(distance, bearing, RANGE_MI)

        track = ac.get("track")
        draw_aircraft_symbol(draw, x, y, track)

        callsign = get_callsign(ac)
        altitude = format_altitude(ac)

        # Label only first several targets to avoid clutter.
        if labeled < 8:
            draw_aircraft_label(draw, x, y, callsign, altitude, labeled)
            labeled += 1

        plotted += 1

    # Bottom status bar.
    status_y = HEIGHT - 24
    draw.rectangle((0, status_y - 2, WIDTH, HEIGHT), fill=COLOR_BG)

    status = f"{plotted} targets  {RANGE_MI}mi"
    draw.text((8, status_y), status, fill=COLOR_TEXT_DIM, font=FONT_SMALL)

    draw.text((WIDTH - 42, status_y), "LIVE", fill=COLOR_WARN, font=FONT_SMALL)

    return img, plotted


# -----------------------------
# DISPLAY OUTPUT
# -----------------------------

def image_to_rgb565_bytes(img):
    """
    Convert a Pillow RGB image to RGB565 little-endian bytes.

    Most small SPI framebuffer displays on Raspberry Pi use RGB565.
    """
    img = img.convert("RGB")

    output = bytearray()

    for r, g, b in img.getdata():
        value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)

        # Little-endian RGB565
        output.append(value & 0xFF)
        output.append((value >> 8) & 0xFF)

    return bytes(output)


def show_on_display(img):
    """
    Write the radar image directly to the Linux framebuffer.

    This avoids the flicker caused by repeatedly launching fbi.
    """
    frame = image_to_rgb565_bytes(img)

    with open(FRAMEBUFFER, "wb", buffering=0) as fb:
        fb.write(frame)

# -----------------------------
# MAIN LOOP
# -----------------------------

def main():
    print("Starting Plane Radar Pi...")
    print(f"Center: {CENTER_LAT}, {CENTER_LON}")
    print(f"Range: {RANGE_MI} mi")
    print(f"Display: {FRAMEBUFFER}")
    print("Press Ctrl+C to stop.")

    while True:
        aircraft = fetch_aircraft()
        img, plotted = draw_radar(aircraft)

        print(f"Plotted targets: {plotted}")

        show_on_display(img)

        time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
