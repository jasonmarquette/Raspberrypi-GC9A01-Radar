#!/usr/bin/env python3

import argparse
import configparser
import math
import os
import socket
import time

import requests
from PIL import Image, ImageDraw, ImageFont


# ============================================================
# Plane Radar Pi - HDMI Edition
# Raspberry Pi HDMI fullscreen radar with right-side status panel
# ============================================================

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.ini")


def parse_args():
    parser = argparse.ArgumentParser(description="Plane Radar Pi HDMI display")
    parser.add_argument(
        "--display",
        choices=["hdmi"],
        default="hdmi",
        help="Display output mode. This HDMI-optimized version only supports hdmi.",
    )
    parser.add_argument(
        "--windowed",
        action="store_true",
        help="Use a normal resizable window instead of fullscreen.",
    )
    return parser.parse_args()


def load_config():
    config = configparser.ConfigParser()

    config["radar"] = {
        "center_lat": "30.14705507846894",
        "center_lon": "-95.39204791784302",
        "range_mi": "10",
        "refresh_seconds": "1",
        "api_poll_seconds": "15",
    }

    config["display"] = {
        "save_preview": "false",
        "preview_file": "/tmp/plane-radar-preview.png",
        "show_heading_lines": "true",
        "heading_line_length": "26",
        "heading_line_gap": "7",
        "heading_line_width": "2",
    }

    config.read(CONFIG_PATH)

    radar_config = config["radar"]
    display_config = config["display"]

    return {
        "center_lat": radar_config.getfloat("center_lat"),
        "center_lon": radar_config.getfloat("center_lon"),
        "range_mi": radar_config.getfloat("range_mi"),
        # refresh_seconds is now the screen redraw interval.
        # api_poll_seconds controls how often the ADS-B API is called.
        "refresh_seconds": max(1, radar_config.getint("refresh_seconds", fallback=1)),
        "api_poll_seconds": max(15, radar_config.getint("api_poll_seconds", fallback=radar_config.getint("refresh_seconds", fallback=15))),
        "save_preview": display_config.getboolean("save_preview"),
        "preview_file": display_config.get("preview_file"),
        "show_heading_lines": display_config.getboolean("show_heading_lines"),
        "heading_line_length": display_config.getint("heading_line_length"),
        "heading_line_gap": display_config.getint("heading_line_gap"),
        "heading_line_width": display_config.getint("heading_line_width"),
    }


ARGS = parse_args()
APP_CONFIG = load_config()

CENTER_LAT = APP_CONFIG["center_lat"]
CENTER_LON = APP_CONFIG["center_lon"]
RANGE_MI = APP_CONFIG["range_mi"]
REFRESH_SECONDS = APP_CONFIG["refresh_seconds"]
API_POLL_SECONDS = APP_CONFIG["api_poll_seconds"]
SAVE_PREVIEW = APP_CONFIG["save_preview"]
PREVIEW_FILE = APP_CONFIG["preview_file"]
SHOW_HEADING_LINES = APP_CONFIG["show_heading_lines"]
HEADING_LINE_LENGTH = APP_CONFIG["heading_line_length"]
HEADING_LINE_GAP = APP_CONFIG["heading_line_gap"]
HEADING_LINE_WIDTH = APP_CONFIG["heading_line_width"]


# -----------------------------
# DISPLAY LAYOUT
# -----------------------------

WIDTH = 1920
HEIGHT = 1080
DRAW_SCALE = 1.0

MARGIN = 28
RADAR_LEFT = MARGIN
RADAR_TOP = MARGIN
RADAR_SIZE = 1024
CENTER_X = RADAR_LEFT + RADAR_SIZE // 2
CENTER_Y = RADAR_TOP + RADAR_SIZE // 2
RADAR_RADIUS = RADAR_SIZE // 2 - 34
SIDEBAR_X = RADAR_LEFT + RADAR_SIZE + MARGIN
SIDEBAR_W = WIDTH - SIDEBAR_X - MARGIN

COLOR_BG = (2, 8, 20)
COLOR_PANEL_BG = (6, 17, 34)
COLOR_PANEL_LINE = (0, 115, 95)
COLOR_RING_MAJOR = (0, 210, 105)
COLOR_RING_MINOR = (0, 105, 75)
COLOR_TEXT = (225, 235, 235)
COLOR_TEXT_DIM = (155, 210, 175)
COLOR_OWN_SHIP = (255, 255, 255)
COLOR_AIRCRAFT = (255, 70, 70)
COLOR_HEADING_LINE = (180, 80, 255)
COLOR_LABEL = (245, 245, 245)
COLOR_TYPE = (255, 190, 80)
COLOR_ALTITUDE = (80, 205, 255)
COLOR_WARN = (255, 190, 80)
COLOR_OK = (85, 235, 145)
COLOR_CACHED = (255, 190, 80)


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


FONT_TINY = load_font(22)
FONT_SMALL = load_font(26)
FONT_MED = load_font(34)
FONT_BOLD = load_font(34, bold=True)
FONT_TITLE = load_font(54)
FONT_VALUE = load_font(52, bold=True)
FONT_LABEL_PANEL = load_font(24, bold=True)
FONT_PANEL = load_font(28)


def scaled(value, minimum=1):
    return max(minimum, int(round(value * DRAW_SCALE)))


def configure_layout(width, height):
    global WIDTH, HEIGHT, DRAW_SCALE
    global MARGIN, RADAR_LEFT, RADAR_TOP, RADAR_SIZE, CENTER_X, CENTER_Y, RADAR_RADIUS
    global SIDEBAR_X, SIDEBAR_W
    global FONT_TINY, FONT_SMALL, FONT_MED, FONT_BOLD, FONT_TITLE, FONT_VALUE, FONT_LABEL_PANEL, FONT_PANEL

    WIDTH = int(width)
    HEIGHT = int(height)
    DRAW_SCALE = min(WIDTH, HEIGHT) / 1080.0
    MARGIN = scaled(28)

    # Keep the radar large and crisp, but reserve a dedicated right-hand panel.
    preferred_sidebar = max(scaled(380), int(WIDTH * 0.30))
    RADAR_SIZE = min(HEIGHT - (MARGIN * 2), WIDTH - preferred_sidebar - (MARGIN * 3))

    # Fallback for smaller displays/windowed testing.
    if RADAR_SIZE < scaled(420):
        RADAR_SIZE = min(HEIGHT - (MARGIN * 2), WIDTH - (MARGIN * 2))
        preferred_sidebar = 0

    RADAR_LEFT = MARGIN
    RADAR_TOP = (HEIGHT - RADAR_SIZE) // 2
    CENTER_X = RADAR_LEFT + RADAR_SIZE // 2
    CENTER_Y = RADAR_TOP + RADAR_SIZE // 2
    RADAR_RADIUS = RADAR_SIZE // 2 - scaled(38)

    SIDEBAR_X = RADAR_LEFT + RADAR_SIZE + MARGIN
    SIDEBAR_W = max(0, WIDTH - SIDEBAR_X - MARGIN)

    # Font sizes are based on screen height so 720p/1080p/4K all stay proportional.
    FONT_TINY = load_font(scaled(18, 12))
    FONT_SMALL = load_font(scaled(22, 14))
    FONT_MED = load_font(scaled(28, 18))
    FONT_BOLD = load_font(scaled(30, 18), bold=True)
    FONT_TITLE = load_font(scaled(54, 28))
    FONT_VALUE = load_font(scaled(52, 26), bold=True)
    FONT_LABEL_PANEL = load_font(scaled(23, 14), bold=True)
    FONT_PANEL = load_font(scaled(27, 16))


# -----------------------------
# GEO MATH
# -----------------------------

def haversine_mi(lat1, lon1, lat2, lon2):
    earth_radius_mi = 3958.8
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return earth_radius_mi * c


def bearing_deg(lat1, lon1, lat2, lon2):
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def polar_to_screen(distance_mi, bearing, max_range_mi):
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
    Fetch aircraft once.

    Returns:
        {
            "ok": bool,
            "aircraft": list | None,
            "retry_after": int | None,
            "message": str,
        }

    Important: this function is only called by the scheduler in main(),
    not every screen redraw. That prevents accidental API hammering.
    """
    url = f"https://opendata.adsb.fi/api/v3/lat/{CENTER_LAT}/lon/{CENTER_LON}/dist/{RANGE_MI}"

    try:
        response = requests.get(url, timeout=8)

        if response.status_code == 429:
            retry_after = None
            retry_after_header = response.headers.get("Retry-After")
            if retry_after_header:
                try:
                    retry_after = int(float(retry_after_header))
                except ValueError:
                    retry_after = None

            message = "429 Too Many Requests"
            if retry_after:
                message += f" | Retry-After: {retry_after}s"

            print(f"ADS-B fetch rate limited: {message} | keeping last good data")
            return {"ok": False, "aircraft": None, "retry_after": retry_after, "message": message}

        response.raise_for_status()
        data = response.json()
        aircraft = data.get("ac", [])
        print(f"API aircraft: {len(aircraft)} | API total: {data.get('total', 'n/a')}")
        return {"ok": True, "aircraft": aircraft, "retry_after": None, "message": "OK"}

    except Exception as e:
        message = str(e)
        print(f"ADS-B fetch failed: {message} | keeping last good data")
        return {"ok": False, "aircraft": None, "retry_after": None, "message": message}


# -----------------------------
# AIRCRAFT FIELD HELPERS
# -----------------------------

def format_altitude(ac):
    alt = ac.get("alt_baro")
    if alt is None or alt == "ground":
        alt = ac.get("alt_geom")
    if alt == "ground":
        return "GND"
    if isinstance(alt, (int, float)):
        return f"{int(round(alt))} ft"
    return ""


def get_callsign(ac):
    return str(ac.get("flight") or ac.get("r") or ac.get("hex") or "").strip()


def get_aircraft_type(ac):
    return str(ac.get("t") or ac.get("type") or ac.get("desc") or "").strip()


def get_aircraft_heading(ac):
    for key in ("track", "true_track", "heading", "mag_heading", "nav_heading"):
        value = ac.get(key)
        if value is None:
            continue
        try:
            return float(value) % 360
        except (TypeError, ValueError):
            continue
    return None


_LOCAL_IP_CACHE = {"value": None, "time": 0}


def get_local_ip():
    # Do not do a network socket check every frame. Cache it for 60 seconds.
    now = time.time()
    if _LOCAL_IP_CACHE["value"] is not None and now - _LOCAL_IP_CACHE["time"] < 60:
        return _LOCAL_IP_CACHE["value"]

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            value = s.getsockname()[0]
    except Exception:
        value = "unknown"

    _LOCAL_IP_CACHE["value"] = value
    _LOCAL_IP_CACHE["time"] = now
    return value


# -----------------------------
# DRAWING HELPERS
# -----------------------------

def draw_centered_text(draw, text, center_x, y, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    draw.text((center_x - text_width // 2, y), text, font=font, fill=fill)


def draw_heading_line(draw, x, y, heading_deg):
    if not SHOW_HEADING_LINES or heading_deg is None:
        return
    try:
        heading_deg = float(heading_deg)
    except (TypeError, ValueError):
        return

    radians = math.radians(heading_deg)
    dx = math.sin(radians)
    dy = -math.cos(radians)
    gap = scaled(HEADING_LINE_GAP)
    length = scaled(HEADING_LINE_LENGTH)

    draw.line(
        [(x + dx * gap, y + dy * gap), (x + dx * length, y + dy * length)],
        fill=COLOR_HEADING_LINE,
        width=scaled(HEADING_LINE_WIDTH),
    )


def draw_aircraft_symbol(draw, x, y, track):
    dot_radius = scaled(4)

    if track is None:
        draw.ellipse((x - dot_radius, y - dot_radius, x + dot_radius, y + dot_radius), fill=COLOR_AIRCRAFT)
        return

    try:
        track = float(track)
    except (TypeError, ValueError):
        draw.ellipse((x - dot_radius, y - dot_radius, x + dot_radius, y + dot_radius), fill=COLOR_AIRCRAFT)
        return

    draw_heading_line(draw, x, y, track)

    heading = math.radians(track)
    size = scaled(10)
    nose = (int(x + math.sin(heading) * size), int(y - math.cos(heading) * size))
    left = (int(x + math.sin(heading + 2.4) * size), int(y - math.cos(heading + 2.4) * size))
    right = (int(x + math.sin(heading - 2.4) * size), int(y - math.cos(heading - 2.4) * size))
    draw.polygon([nose, left, right], fill=COLOR_AIRCRAFT)


def draw_aircraft_label(draw, x, y, callsign, aircraft_type, altitude, label_index):
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

    line_gap = scaled(2)
    line_height = scaled(22)
    text_width = 0
    for line_text, _ in lines:
        bbox = draw.textbbox((0, 0), line_text, font=FONT_TINY)
        text_width = max(text_width, bbox[2] - bbox[0])

    text_height = len(lines) * line_height + (len(lines) - 1) * line_gap
    tx = x + scaled(12)
    ty = y - scaled(18)

    radar_right = RADAR_LEFT + RADAR_SIZE
    radar_bottom = RADAR_TOP + RADAR_SIZE

    if tx + text_width > radar_right - scaled(8):
        tx = x - text_width - scaled(12)
    if ty < RADAR_TOP + scaled(8):
        ty = y + scaled(12)
    if ty + text_height > radar_bottom - scaled(8):
        ty = radar_bottom - scaled(8) - text_height
    if label_index % 2 == 1:
        ty += scaled(9)

    for i, (line_text, line_color) in enumerate(lines):
        draw.text((tx, ty + i * (line_height + line_gap)), line_text, fill=line_color, font=FONT_TINY)


def panel_text(draw, x, y, label, value, value_color=COLOR_TEXT, small_value=False):
    draw.text((x, y), label.upper(), font=FONT_LABEL_PANEL, fill=COLOR_TEXT_DIM)
    value_font = FONT_PANEL if small_value else FONT_VALUE
    draw.text((x, y + scaled(28)), str(value), font=value_font, fill=value_color)
    return y + (scaled(88) if small_value else scaled(110))


def draw_sidebar(draw, plotted, api_count, api_status, cache_age_seconds, last_update_time):
    if SIDEBAR_W <= scaled(260):
        return

    panel_left = SIDEBAR_X
    panel_right = WIDTH - MARGIN
    panel_top = RADAR_TOP
    panel_bottom = RADAR_TOP + RADAR_SIZE

    draw.rectangle((panel_left, panel_top, panel_right, panel_bottom), fill=COLOR_PANEL_BG)
    draw.line((panel_left, panel_top, panel_left, panel_bottom), fill=COLOR_PANEL_LINE, width=scaled(2))

    x = panel_left + scaled(34)
    y = panel_top + scaled(36)

    draw.text((x, y), "Plane", font=FONT_TITLE, fill=COLOR_TEXT)
    draw.text((x, y + scaled(60)), "Radar", font=FONT_TITLE, fill=COLOR_TEXT)
    y += scaled(140)
    draw.line((x, y, panel_right - scaled(28), y), fill=COLOR_PANEL_LINE, width=scaled(1))
    y += scaled(24)

    y = panel_text(draw, x, y, "Aircraft", plotted)

    range_km = RANGE_MI * 1.60934
    range_text = f"{RANGE_MI:g} mi / {range_km:.0f} km"
    y = panel_text(draw, x, y, "Range", range_text, small_value=True)

    ip_addr = get_local_ip()
    wifi_value = "CONNECTED" if ip_addr != "unknown" else "UNKNOWN"
    y = panel_text(draw, x, y, "Network", wifi_value, COLOR_OK if ip_addr != "unknown" else COLOR_WARN, small_value=True)
    draw.text((x, y - scaled(36)), ip_addr, font=FONT_PANEL, fill=COLOR_TEXT)
    y += scaled(14)

    y = panel_text(draw, x, y, "Lat", f"{CENTER_LAT:.4f}", small_value=True)
    y = panel_text(draw, x, y, "Lon", f"{CENTER_LON:.4f}", small_value=True)

    # Keep status near the bottom of the panel.
    status_y = max(y, panel_bottom - scaled(180))
    draw.line((x, status_y - scaled(18), panel_right - scaled(28), status_y - scaled(18)), fill=COLOR_PANEL_LINE, width=scaled(1))

    if api_status == "live":
        status_text = "LIVE"
        status_color = COLOR_OK
    elif api_status == "cached":
        status_text = f"CACHED {cache_age_seconds}s" if cache_age_seconds is not None else "CACHED"
        status_color = COLOR_CACHED
    else:
        status_text = "WAITING"
        status_color = COLOR_WARN

    draw.text((x, status_y), "API", font=FONT_LABEL_PANEL, fill=COLOR_TEXT_DIM)
    draw.text((x, status_y + scaled(30)), status_text, font=FONT_PANEL, fill=status_color)

    if last_update_time is not None:
        updated = time.strftime("%H:%M:%S", time.localtime(last_update_time))
        draw.text((x, status_y + scaled(70)), f"Updated {updated}", font=FONT_PANEL, fill=COLOR_TEXT)

    draw.text((x, panel_bottom - scaled(40)), f"API poll {API_POLL_SECONDS}s | API {api_count}", font=FONT_SMALL, fill=COLOR_TEXT_DIM)


# -----------------------------
# RADAR DRAWING
# -----------------------------

def draw_radar(aircraft, api_status="waiting", cache_age_seconds=None, last_update_time=None):
    img = Image.new("RGB", (WIDTH, HEIGHT), COLOR_BG)
    draw = ImageDraw.Draw(img)

    # Radar screen background.
    draw.rectangle((RADAR_LEFT, RADAR_TOP, RADAR_LEFT + RADAR_SIZE, RADAR_TOP + RADAR_SIZE), fill=COLOR_BG)

    # Main radar circle.
    draw.ellipse(
        (CENTER_X - RADAR_RADIUS, CENTER_Y - RADAR_RADIUS, CENTER_X + RADAR_RADIUS, CENTER_Y + RADAR_RADIUS),
        outline=COLOR_RING_MAJOR,
        width=scaled(2),
    )

    # Range rings.
    for frac in (0.25, 0.50, 0.75):
        rr = int(RADAR_RADIUS * frac)
        draw.ellipse((CENTER_X - rr, CENTER_Y - rr, CENTER_X + rr, CENTER_Y + rr), outline=COLOR_RING_MINOR, width=scaled(1))

    # Crosshairs.
    draw.line((CENTER_X, CENTER_Y - RADAR_RADIUS, CENTER_X, CENTER_Y + RADAR_RADIUS), fill=COLOR_RING_MINOR, width=scaled(1))
    draw.line((CENTER_X - RADAR_RADIUS, CENTER_Y, CENTER_X + RADAR_RADIUS, CENTER_Y), fill=COLOR_RING_MINOR, width=scaled(1))

    # Cardinal labels.
    draw_centered_text(draw, "N", CENTER_X, CENTER_Y - RADAR_RADIUS - scaled(34), FONT_BOLD, COLOR_TEXT)
    draw_centered_text(draw, "S", CENTER_X, CENTER_Y + RADAR_RADIUS + scaled(8), FONT_BOLD, COLOR_TEXT)
    draw.text((CENTER_X + RADAR_RADIUS + scaled(10), CENTER_Y - scaled(16)), "E", fill=COLOR_TEXT, font=FONT_BOLD)
    draw.text((CENTER_X - RADAR_RADIUS - scaled(32), CENTER_Y - scaled(16)), "W", fill=COLOR_TEXT, font=FONT_BOLD)

    # Range label on edge of the outer circle.
    draw.text((CENTER_X + RADAR_RADIUS - scaled(60), CENTER_Y - scaled(16)), f"{RANGE_MI:g}mi", fill=COLOR_TEXT_DIM, font=FONT_SMALL)

    # Own location / center dot.
    draw.ellipse((CENTER_X - scaled(4), CENTER_Y - scaled(4), CENTER_X + scaled(4), CENTER_Y + scaled(4)), fill=COLOR_OWN_SHIP)

    plotted = 0
    labeled = 0

    for ac in aircraft:
        lat = ac.get("lat")
        lon = ac.get("lon")
        if lat is None or lon is None:
            continue

        distance = haversine_mi(CENTER_LAT, CENTER_LON, lat, lon)
        if distance > RANGE_MI:
            continue

        bearing = bearing_deg(CENTER_LAT, CENTER_LON, lat, lon)
        x, y = polar_to_screen(distance, bearing, RANGE_MI)
        track = get_aircraft_heading(ac)

        draw_aircraft_symbol(draw, x, y, track)

        if labeled < 10:
            draw_aircraft_label(
                draw,
                x,
                y,
                get_callsign(ac),
                get_aircraft_type(ac),
                format_altitude(ac),
                labeled,
            )
            labeled += 1

        plotted += 1

    draw_sidebar(draw, plotted, len(aircraft), api_status, cache_age_seconds, last_update_time)

    return img, plotted


# -----------------------------
# HDMI OUTPUT
# -----------------------------

def init_hdmi_display():
    # Let `python3 radar.py --display hdmi` work from SSH without prefixing DISPLAY=:0.
    os.environ.setdefault("DISPLAY", ":0")

    if "XAUTHORITY" not in os.environ:
        xauthority = os.path.join(os.path.expanduser("~"), ".Xauthority")
        if os.path.exists(xauthority):
            os.environ["XAUTHORITY"] = xauthority

    try:
        import pygame
    except ImportError as exc:
        raise SystemExit("pygame is required. Install it with: sudo apt install python3-pygame") from exc

    pygame.init()
    pygame.display.set_caption("Plane Radar Pi")
    pygame.mouse.set_visible(False)

    if ARGS.windowed:
        screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
    else:
        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)

    return pygame, screen


def show_on_hdmi(img, hdmi):
    pygame, screen = hdmi

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            raise KeyboardInterrupt
        if event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_q):
            raise KeyboardInterrupt

    screen_width, screen_height = screen.get_size()

    # If a window was resized, reconfigure and caller will draw correct size on next loop.
    if img.size != (screen_width, screen_height):
        surface = pygame.image.fromstring(img.convert("RGB").tobytes(), img.size, "RGB")
        surface = pygame.transform.smoothscale(surface, (screen_width, screen_height))
    else:
        surface = pygame.image.fromstring(img.convert("RGB").tobytes(), img.size, "RGB")

    screen.blit(surface, (0, 0))
    pygame.display.flip()


def save_preview_image(img):
    if not SAVE_PREVIEW:
        return

    preview_dir = os.path.dirname(PREVIEW_FILE)
    if preview_dir:
        os.makedirs(preview_dir, exist_ok=True)

    img.save(PREVIEW_FILE)


# -----------------------------
# MAIN LOOP
# -----------------------------

def main():
    print("Starting Plane Radar Pi HDMI Edition...")
    print(f"Center: {CENTER_LAT}, {CENTER_LON}")
    print(f"Range: {RANGE_MI} mi")
    print(f"Screen refresh: {REFRESH_SECONDS}s")
    print(f"API poll interval: {API_POLL_SECONDS}s minimum")
    print(f"HDMI DISPLAY: {os.environ.get('DISPLAY', ':0')}")
    print("Press Ctrl+C to stop. Press Esc or q to quit HDMI mode.")

    hdmi = init_hdmi_display()
    _, screen = hdmi
    screen_width, screen_height = screen.get_size()
    configure_layout(screen_width, screen_height)
    print(f"HDMI screen: {screen_width}x{screen_height}")
    print(f"Radar area: {RADAR_SIZE}x{RADAR_SIZE} | Sidebar: {SIDEBAR_W}px wide")

    last_good_aircraft = []
    last_good_fetch_time = None
    next_api_fetch_time = 0
    api_status = "waiting"
    api_count = 0

    while True:
        loop_start = time.time()

        # Support window resizing in --windowed mode.
        current_width, current_height = screen.get_size()
        if current_width != WIDTH or current_height != HEIGHT:
            configure_layout(current_width, current_height)

        # Only call the ADS-B API when the scheduler says it is time.
        # Screen redraws can happen more often without creating API traffic.
        if loop_start >= next_api_fetch_time:
            result = fetch_aircraft()

            if result["ok"]:
                last_good_aircraft = result["aircraft"]
                last_good_fetch_time = loop_start
                api_status = "live"
                api_count = len(last_good_aircraft)
                next_api_fetch_time = loop_start + API_POLL_SECONDS
            else:
                if last_good_fetch_time is None:
                    api_status = "waiting"
                else:
                    api_status = "cached"

                # Back off harder after a rate limit. Honor Retry-After if present.
                retry_after = result.get("retry_after")
                if retry_after is not None:
                    wait_seconds = max(API_POLL_SECONDS, retry_after)
                elif "429" in result.get("message", ""):
                    wait_seconds = max(API_POLL_SECONDS * 2, 30)
                else:
                    wait_seconds = API_POLL_SECONDS

                next_api_fetch_time = loop_start + wait_seconds
                print(f"Next API fetch in {int(wait_seconds)}s")
        else:
            if last_good_fetch_time is not None and api_status != "live":
                api_status = "cached"

        aircraft = last_good_aircraft
        cache_age = None if last_good_fetch_time is None else int(time.time() - last_good_fetch_time)

        # Show LIVE only briefly after a successful fetch; otherwise show cached age.
        visible_status = api_status
        if last_good_fetch_time is not None and time.time() - last_good_fetch_time > 3:
            visible_status = "cached"

        img, plotted = draw_radar(
            aircraft,
            api_status=visible_status,
            cache_age_seconds=cache_age,
            last_update_time=last_good_fetch_time,
        )

        save_preview_image(img)
        show_on_hdmi(img, hdmi)

        seconds_until_api = max(0, int(next_api_fetch_time - time.time()))
        if visible_status == "cached":
            print(f"Plotted targets: {plotted} using cached data: {cache_age}s old | next API fetch in {seconds_until_api}s")
        elif visible_status == "waiting":
            print(f"Plotted targets: {plotted} waiting for first successful API fetch | next API fetch in {seconds_until_api}s")
        else:
            print(f"Plotted targets: {plotted} | next API fetch in {seconds_until_api}s")

        elapsed = time.time() - loop_start
        time.sleep(max(0.1, REFRESH_SECONDS - elapsed))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
