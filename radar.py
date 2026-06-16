#!/usr/bin/env python3

import argparse
import math
import time
import os
import shutil
import subprocess
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

    radar_defaults = {
        "center_lat": "30.14705507846894",
        "center_lon": "-95.39204791784302",
        "range_mi": "10",
        "refresh_seconds": "5",
    }

    display_defaults = {
        # display_type options:
        #   spi     = write raw RGB565 bytes to the small SPI framebuffer
        #   hdmi    = show the PNG on an HDMI framebuffer using fbi
        #   preview = do not write to a display; only save the preview PNG
        "display_type": "spi",

        # Framebuffer device. For the SPI screen this is usually /dev/fb0.
        # For HDMI it may also be /dev/fb0, depending on your Pi setup.
        "device": "/dev/fb0",
        "write_framebuffer": "true",

        # HDMI/fbi display settings. Used only when display_type = hdmi.
        "hdmi_image_file": "/tmp/plane-radar-hdmi.png",
        "hdmi_use_sudo": "false",
        "hdmi_tty": "1",

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

        "display_type": display_config.get("display_type").strip().lower(),
        "display_device": display_config.get("device"),
        "write_framebuffer": display_config.getboolean("write_framebuffer"),

        "hdmi_image_file": display_config.get("hdmi_image_file"),
        "hdmi_use_sudo": display_config.getboolean("hdmi_use_sudo"),
        "hdmi_tty": display_config.get("hdmi_tty"),

        "save_preview": display_config.getboolean("save_preview"),
        "preview_file": display_config.get("preview_file"),

        "show_heading_lines": display_config.getboolean("show_heading_lines"),
        "heading_line_length": display_config.getint("heading_line_length"),
        "heading_line_gap": display_config.getint("heading_line_gap"),
        "heading_line_width": display_config.getint("heading_line_width"),
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Plane Radar Pi")

    parser.add_argument(
        "--display",
        choices=("spi", "hdmi", "preview"),
        help="Override config.ini display.display_type for this run.",
    )

    return parser.parse_args()


def apply_cli_overrides(config):
    args = parse_args()

    if args.display:
        config["display_type"] = args.display

        # HDMI and preview modes should not write raw RGB565 bytes to the SPI screen.
        if args.display in ("hdmi", "preview"):
            config["write_framebuffer"] = False

        # Preview mode should always save a PNG.
        if args.display == "preview":
            config["save_preview"] = True

    return config


APP_CONFIG = apply_cli_overrides(load_config())

# Your selected radar center.
CENTER_LAT = APP_CONFIG["center_lat"]
CENTER_LON = APP_CONFIG["center_lon"]

# Radar/API range in miles.
# Smaller range = fewer aircraft.
RANGE_MI = APP_CONFIG["range_mi"]

# Refresh rate in seconds.
REFRESH_SECONDS = APP_CONFIG["refresh_seconds"]

# Display mode and device.
DISPLAY_TYPE = APP_CONFIG["display_type"]
FRAMEBUFFER = APP_CONFIG["display_device"]
WRITE_FRAMEBUFFER = APP_CONFIG["write_framebuffer"]

# HDMI/fbi output settings.
HDMI_IMAGE_FILE = APP_CONFIG["hdmi_image_file"]
HDMI_USE_SUDO = APP_CONFIG["hdmi_use_sudo"]
HDMI_TTY = APP_CONFIG["hdmi_tty"]

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

        track = get_aircraft_heading(ac)
        draw_aircraft_symbol(draw, x, y, track)

        callsign = get_callsign(ac)
        altitude = format_altitude(ac)

        # Label only first several targets to avoid clutter.
        if labeled < 8:
            draw_aircraft_label(draw, x, y, callsign, altitude, labeled)
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

    output = bytearray()

    for r, g, b in img.getdata():
        value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)

        # Little-endian RGB565
        output.append(value & 0xFF)
        output.append((value >> 8) & 0xFF)

    return bytes(output)


def save_png(img, path):
    """
    Save the radar image as a PNG.
    """
    output_dir = os.path.dirname(path)

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    img.save(path)


def save_preview_image(img):
    """
    Save a PNG copy of the radar image so it can be copied to another computer.
    """
    if SAVE_PREVIEW:
        save_png(img, PREVIEW_FILE)


def show_on_spi_framebuffer(img):
    """
    Write the 240x240 radar image directly to the small SPI Linux framebuffer.

    This is intended for the GC9A01 SPI display using RGB565.
    """
    if not WRITE_FRAMEBUFFER:
        return

    frame = image_to_rgb565_bytes(img)

    with open(FRAMEBUFFER, "wb", buffering=0) as fb:
        fb.write(frame)


def show_on_hdmi(img):
    """
    Display the radar image on an HDMI framebuffer using fbi.

    Requires fbi:
        sudo apt install fbi
    """
    save_png(img, HDMI_IMAGE_FILE)

    fbi_path = shutil.which("fbi")

    if not fbi_path:
        print("HDMI display requested, but fbi is not installed.")
        print("Install it with: sudo apt install fbi")
        return

    cmd = [
        fbi_path,
        "-T", str(HDMI_TTY),
        "-d", FRAMEBUFFER,
        "-a",
        "-noverbose",
        HDMI_IMAGE_FILE,
    ]

    if HDMI_USE_SUDO and os.geteuid() != 0:
        cmd.insert(0, "sudo")

    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print("HDMI display command timed out. The preview PNG was still saved.")


def show_on_display(img):
    """
    Route the radar image to the selected display output.
    """
    if DISPLAY_TYPE == "spi":
        show_on_spi_framebuffer(img)
    elif DISPLAY_TYPE == "hdmi":
        show_on_hdmi(img)
    elif DISPLAY_TYPE == "preview":
        return
    else:
        print(f"Unknown display_type '{DISPLAY_TYPE}'. Use spi, hdmi, or preview.")


# -----------------------------
# MAIN LOOP
# -----------------------------

def main():
    print("Starting Plane Radar Pi...")
    print(f"Center: {CENTER_LAT}, {CENTER_LON}")
    print(f"Range: {RANGE_MI} mi")
    print(f"Display type: {DISPLAY_TYPE}")
    print(f"Framebuffer device: {FRAMEBUFFER}")
    print(f"Write raw framebuffer: {WRITE_FRAMEBUFFER}")
    print(f"Save preview: {SAVE_PREVIEW}")
    if SAVE_PREVIEW:
        print(f"Preview file: {PREVIEW_FILE}")
    if DISPLAY_TYPE == "hdmi":
        print(f"HDMI image file: {HDMI_IMAGE_FILE}")
        print(f"HDMI tty: {HDMI_TTY}")
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
