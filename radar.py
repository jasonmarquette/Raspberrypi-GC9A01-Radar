#!/usr/bin/env python3

import math
import time
import os
import requests
import configparser
from PIL import Image, ImageDraw, ImageFont


# ============================================================
# Plane Radar Pi - Proof of Concept
# Raspberry Pi 4B + GC9A01 240x240 + HDMI framebuffer display
# ============================================================


# -----------------------------
# USER CONFIG
# -----------------------------

# Config file path.
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.ini")


def load_config():
    config = configparser.ConfigParser()

    radar_defaults = {
        "center_lat": "30.14705507846894",
        "center_lon": "-95.39204791784302",
        "range_mi": "10",
        "refresh_seconds": "5",
    }

    display_defaults = {
        # Framebuffer display settings.
        "device": "/dev/fb0",
        "write_framebuffer": "true",

        # Remote preview image settings.
        "save_preview": "false",
        "preview_file": "/tmp/plane-radar-preview.png",

        # Aircraft heading line settings.
        "show_heading_lines": "true",
        "heading_line_length": "26",
        "heading_line_gap": "7",
        "heading_line_width": "2",
    }

    config["radar"] = radar_defaults
    config["display"] = display_defaults

    config.read(CONFIG_PATH)

    radar_config = config["radar"]
    display_config = config["display"]

    return {
        "center_lat": radar_config.getfloat("center_lat"),
        "center_lon": radar_config.getfloat("center_lon"),
        "range_mi": radar_config.getfloat("range_mi"),
        "refresh_seconds": radar_config.getint("refresh_seconds"),

        "display_device": display_config.get("device"),
        "write_framebuffer": display_config.getboolean("write_framebuffer"),

        "save_preview": display_config.getboolean("save_preview"),
        "preview_file": display_config.get("preview_file"),

        "show_heading_lines": display_config.getboolean("show_heading_lines"),
        "heading_line_length": display_config.getint("heading_line_length"),
        "heading_line_gap": display_config.getint("heading_line_gap"),
        "heading_line_width": display_config.getint("heading_line_width"),
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
FRAMEBUFFER = APP_CONFIG["display_device"]
WRITE_FRAMEBUFFER = APP_CONFIG["write_framebuffer"]

# Optional preview PNG for checking the radar image remotely.
SAVE_PREVIEW = APP_CONFIG["save_preview"]
PREVIEW_FILE = APP_CONFIG["preview_file"]

# Aircraft heading line options.
SHOW_HEADING_LINES = APP_CONFIG["show_heading_lines"]
HEADING_LINE_LENGTH = APP_CONFIG["heading_line_length"]
HEADING_LINE_GAP = APP_CONFIG["heading_line_gap"]
HEADING_LINE_WIDTH = APP_CONFIG["heading_line_width"]

# Temporary image path used by older display methods.
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
COLOR_HEADING_LINE = (180, 80, 255)
COLOR_LABEL = (235, 235, 235)
COLOR_TYPE = (255, 190, 80)
COLOR_ALTITUDE = (80, 200, 255)
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


def get_aircraft_type(ac):
    """
    Return aircraft type/designator if available.
    Examples: A319, B738, BCS3, P28A
    """
    aircraft_type = (
        ac.get("t")
        or ac.get("type")
        or ac.get("desc")
        or ""
    )

    return str(aircraft_type).strip()


def get_aircraft_heading(ac):
    """
    Try common ADS-B heading/track fields.
    Returns heading in degrees, or None if unavailable.

    For this display, track is usually the best field because it shows
    where the aircraft is moving over the ground.
    """
    for key in ("track", "true_track", "heading", "mag_heading", "nav_heading"):
        value = ac.get(key)

        if value is None:
            continue

        try:
            return float(value) % 360
        except (TypeError, ValueError):
            continue

    return None


# -----------------------------
# DRAWING HELPERS
# -----------------------------

def draw_centered_text(draw, text, center_x, y, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    draw.text((center_x - text_width // 2, y), text, font=font, fill=fill)


def draw_heading_line(draw, x, y, heading_deg):
    """
    Draw a heading line in front of the aircraft symbol.

    heading_deg is degrees clockwise from north:
    0 = north/up, 90 = east/right, 180 = south/down, 270 = west/left.
    """
    if not SHOW_HEADING_LINES:
        return

    if heading_deg is None:
        return

    try:
        heading_deg = float(heading_deg)
    except (TypeError, ValueError):
        return

    radians = math.radians(heading_deg)

    dx = math.sin(radians)
    dy = -math.cos(radians)

    start_x = x + dx * HEADING_LINE_GAP
    start_y = y + dy * HEADING_LINE_GAP

    end_x = x + dx * HEADING_LINE_LENGTH
    end_y = y + dy * HEADING_LINE_LENGTH

    draw.line(
        [(start_x, start_y), (end_x, end_y)],
        fill=COLOR_HEADING_LINE,
        width=HEADING_LINE_WIDTH,
    )


def draw_aircraft_symbol(draw, x, y, track):
    """
    Draw a small aircraft triangle. If no track is available, draw a dot.
    """
    if track is None:
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=COLOR_AIRCRAFT)
        return

    try:
        track = float(track)
    except (TypeError, ValueError):
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=COLOR_AIRCRAFT)
        return

    # Draw the heading line first so the aircraft triangle appears on top of it.
    draw_heading_line(draw, x, y, track)

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


def draw_aircraft_label(draw, x, y, callsign, aircraft_type, altitude, label_index):
    """
    Draw:
    line 1 = callsign in white
    line 2 = aircraft type in orange
    line 3 = altitude in blue
    """
    callsign = str(callsign).strip()[:8] if callsign else ""
    aircraft_type = str(aircraft_type).strip()[:8] if aircraft_type else ""
    altitude = str(altitude).strip() if altitude else ""

    lines = []

    if callsign:
        lines.append((callsign, COLOR_LABEL))

    if aircraft_type:
        lines.append((aircraft_type, COLOR_TYPE))

    if altitude:
        lines.append((altitude, COLOR_ALTITUDE))

    if not lines:
        return

    line_gap = 1
    line_height = 11

    text_width = 0

    for line_text, _ in lines:
        bbox = draw.textbbox((0, 0), line_text, font=FONT_TINY)
        text_width = max(text_width, bbox[2] - bbox[0])

    text_height = len(lines) * line_height + (len(lines) - 1) * line_gap

    # Default: place label to right of aircraft.
    tx = x + 7
    ty = y - 10

    # If near right edge, place label to left.
    if tx + text_width > WIDTH - 4:
        tx = x - text_width - 7

    # Keep inside top/bottom safe area.
    if ty < 4:
        ty = y + 7

    if ty + text_height > HEIGHT - 6:
        ty = HEIGHT - 6 - text_height

    # Slight alternating offset to reduce overlap.
    if label_index % 2 == 1:
        ty += 5

    for i, (line_text, line_color) in enumerate(lines):
        draw.text(
            (tx, ty + i * (line_height + line_gap)),
            line_text,
            fill=line_color,
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

    # Radar range label.
    draw.text(
        (8, HEIGHT - 17),
        f"Range: {RANGE_MI:g} mi",
        fill=COLOR_TEXT_DIM,
        font=FONT_SMALL,
    )
    
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

        track = get_aircraft_heading(ac)
        draw_aircraft_symbol(draw, x, y, track)

        callsign = get_callsign(ac)
        aircraft_type = get_aircraft_type(ac)
        altitude = format_altitude(ac)

        # Label only first several targets to avoid clutter.
        if labeled < 8:
            draw_aircraft_label(draw, x, y, callsign, aircraft_type, altitude, labeled)
            labeled += 1

        plotted += 1

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
    rgb = img.tobytes()

    output = bytearray()

    for i in range(0, len(rgb), 3):
        r = rgb[i]
        g = rgb[i + 1]
        b = rgb[i + 2]

        value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)

        # Little-endian RGB565
        output.append(value & 0xFF)
        output.append((value >> 8) & 0xFF)

    return bytes(output)

def save_preview_image(img):
    """
    Save a PNG copy of the radar image so it can be copied to another computer.
    """
    if not SAVE_PREVIEW:
        return

    preview_dir = os.path.dirname(PREVIEW_FILE)

    if preview_dir:
        os.makedirs(preview_dir, exist_ok=True)

    img.save(PREVIEW_FILE)


def show_on_display(img):
    """
    Write the radar image directly to the Linux framebuffer.

    This avoids the flicker caused by repeatedly launching fbi.
    """
    if not WRITE_FRAMEBUFFER:
        return

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
    print(f"Write framebuffer: {WRITE_FRAMEBUFFER}")
    print(f"Save preview: {SAVE_PREVIEW}")
    if SAVE_PREVIEW:
        print(f"Preview file: {PREVIEW_FILE}")
    print(f"Heading lines: {SHOW_HEADING_LINES}")
    print("Press Ctrl+C to stop.")

    while True:
        aircraft = fetch_aircraft()
        img, plotted = draw_radar(aircraft)

        save_preview_image(img)
        show_on_display(img)

        print(f"Plotted targets: {plotted}")

        time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
