# Plane Radar Pi

A Raspberry Pi 4B proof-of-concept port of an ESP32 live ADS-B plane radar project. This version uses a 1.28 inch round GC9A01 240×240 SPI TFT display connected to a Raspberry Pi and shows nearby aircraft on a circular radar-style screen.

The display is driven through the Raspberry Pi Linux framebuffer using the built-in GC9A01 device tree overlay. Aircraft data is fetched from the public ADS-B API at opendata.adsb.fi.

## Project Overview

This project displays nearby aircraft on a small round TFT screen.

It shows:

* aircraft position relative to a fixed center point
* aircraft heading as a small triangle with a heading line
* aircraft label with three lines:
  * callsign or registration in white
  * aircraft type/designator in orange
  * altitude in feet in blue
* radar range rings
* north/south/east/west markers
* configurable radar range
* configurable radar center latitude and longitude
* configurable display output: SPI framebuffer, HDMI, or preview-only PNG

The current setup was built and tested on:

* Raspberry Pi 4B
* Raspberry Pi OS
* GC9A01 1.28 inch round SPI display
* Python virtual environment
* Linux framebuffer `/dev/fb0`

## Hardware Used

* Raspberry Pi 4B
* 1.28 inch round TFT LCD display

  * 240×240 resolution
  * GC9A01 driver
  * SPI interface
  * 3.3V/5V compatible module
* Jumper wires
* MicroSD card with Raspberry Pi OS
* Internet connection

## Display Wiring

| GC9A01 Display Pin | Raspberry Pi 4B Pin            |
| ------------------ | ------------------------------ |
| VCC                | 3.3V, physical pin 1 or 17     |
| GND                | GND, physical pin 6            |
| SCL / CLK          | GPIO11 / SCLK, physical pin 23 |
| SDA / DIN / MOSI   | GPIO10 / MOSI, physical pin 19 |
| CS                 | GPIO8 / CE0, physical pin 24   |
| DC                 | GPIO25, physical pin 22        |
| RST / RES          | GPIO27, physical pin 13        |
| BL / LED           | 3.3V, physical pin 17          |

Important: power off the Raspberry Pi before wiring the display.

Even if the display module says 3V–5V compatible, use 3.3V with the Raspberry Pi first. Raspberry Pi GPIO pins are not 5V tolerant.

<img width="2064" height="1185" alt="GPIO" src="https://github.com/user-attachments/assets/22c14240-d6d4-4c58-a48c-991a6f620fbb" />

## Enable SPI and GC9A01 Overlay

Edit the Raspberry Pi boot config:

```bash
sudo nano /boot/firmware/config.txt
```

Make sure SPI is enabled:

```ini
dtparam=spi=on
```

Add the GC9A01 overlay:

```ini
dtoverlay=gc9a01,width=240,height=240,rotate=0
```

Reboot:

```bash
sudo reboot
```

After reboot, check for the framebuffer:

```bash
ls -l /dev/fb*
```

On this project, the display appeared as:

```text
/dev/fb0
```

Also check SPI:

```bash
ls -l /dev/spidev*
```

With the overlay using CE0, it is normal to see only:

```text
/dev/spidev0.1
```

## Test the Display

Install test tools:

```bash
sudo apt update
sudo apt install -y fbi imagemagick
```

Create a test image:

```bash
convert -size 240x240 xc:black \
  -fill none -stroke lime -strokewidth 5 -draw "circle 120,120 120,10" \
  -fill white -pointsize 24 -gravity center -annotate 0 "RADAR" \
  /tmp/radar-test.png
```

Show it on the display:

```bash
sudo timeout 5 fbi -T 1 -d /dev/fb0 -noverbose -a /tmp/radar-test.png
```

If the test image appears, the display is working.

## Project Setup

Create the project folder:

```bash
mkdir -p ~/plane-radar-pi
cd ~/plane-radar-pi
```

Create a Python virtual environment:

```bash
python3 -m venv .venv
```

Activate it:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
pip install pillow requests numpy
```

The project intentionally does not use a Python GC9A01 driver. In SPI mode, the display is handled by writing directly to the Raspberry Pi framebuffer. HDMI mode uses `fbi` to show the generated PNG on an HDMI display.

## Usage

The main script is:

```text
~/plane-radar-pi/radar.py
```

Run it manually with the default display mode from `config.ini`:

```bash
cd ~/plane-radar-pi
source .venv/bin/activate
python radar.py
```

Stop it with:

```text
Ctrl+C
```

### Command-Line Options

The script can be run with no arguments:

```bash
python radar.py
```

When run without arguments, the display mode comes from `config.ini`:

```ini
[display]
display_type = spi
```

You can override the display mode for a single run with:

```bash
python radar.py --display spi
python radar.py --display hdmi
python radar.py --display preview
```

Available display modes:

| Option | Description |
| ------ | ----------- |
| `--display spi` | Use the small GC9A01 SPI framebuffer display. |
| `--display hdmi` | Show the radar image on an HDMI display using `fbi`. |
| `--display preview` | Save the radar image as a PNG only. Useful for checking the output remotely. |

### Display Modes

The script supports three display modes:

| Mode      | Description |
| --------- | ----------- |
| `spi`     | Writes directly to the small GC9A01 SPI framebuffer display. |
| `hdmi`    | Saves a PNG and displays it on an HDMI screen using `fbi`. |
| `preview` | Saves a PNG preview only. This is useful when checking the radar from another computer. |

The default mode is set in `config.ini`:

```ini
[display]
display_type = spi
```

Run on the small SPI display:

```bash
python radar.py --display spi
```

Run on an HDMI screen:

```bash
python radar.py --display hdmi
```

Run in preview-only mode:

```bash
python radar.py --display preview
```

### Preview Image

If preview saving is enabled, the script writes a PNG copy of the radar screen to:

```text
/tmp/plane-radar-preview.png
```

Download it from another computer with:

```bash
scp jason@yun:/tmp/plane-radar-preview.png ~/Downloads/
```

On macOS, open it with:

```bash
open ~/Downloads/plane-radar-preview.png
```

### HDMI Output

HDMI mode uses `fbi`, so install it first:

```bash
sudo apt update
sudo apt install -y fbi
```

Then run:

```bash
python radar.py --display hdmi
```

If HDMI output does not appear, check which framebuffer is connected to the HDMI display:

```bash
ls -l /dev/fb*
```

Then update the `device` setting in `config.ini`.

## Configuration

Radar settings are controlled by a local `config.ini` file in the project directory.

Create a file named:

```text
config.ini
```

Example:

```ini
[radar]
center_lat = 30.14705507846894
center_lon = -95.39204791784302
range_mi = 10
refresh_seconds = 15

[display]
display_type = spi
device = /dev/fb0
write_framebuffer = true

save_preview = true
preview_file = /tmp/plane-radar-preview.png

show_heading_lines = true
heading_line_length = 26
heading_line_gap = 7
heading_line_width = 2

hdmi_image_file = /tmp/plane-radar-hdmi.png
hdmi_use_sudo = false
hdmi_tty = 1
```

### Radar Config Options

| Setting           | Description                                   |
| ----------------- | --------------------------------------------- |
| `center_lat`      | Latitude for the center of the radar display  |
| `center_lon`      | Longitude for the center of the radar display |
| `range_mi`        | Radar/API range in statute miles              |
| `refresh_seconds` | Number of seconds between radar refreshes     |

### Display Config Options

| Setting               | Description |
| --------------------- | ----------- |
| `display_type`        | Display mode: `spi`, `hdmi`, or `preview`. |
| `device`              | Framebuffer device, usually `/dev/fb0`. |
| `write_framebuffer`   | Writes raw framebuffer data in `spi` mode when set to `true`. |
| `save_preview`        | Saves a PNG preview image when set to `true`. |
| `preview_file`        | Path where the preview PNG is saved. |
| `show_heading_lines`  | Shows or hides the heading line in front of each aircraft. |
| `heading_line_length` | Length of the aircraft heading line in pixels. |
| `heading_line_gap`    | Gap between the aircraft symbol and the start of the heading line. |
| `heading_line_width`  | Width of the aircraft heading line in pixels. |
| `hdmi_image_file`     | Temporary PNG used for HDMI output. |
| `hdmi_use_sudo`       | Runs `fbi` with `sudo` in HDMI mode when set to `true`. |
| `hdmi_tty`            | TTY number used by `fbi` for HDMI output. |

### Radar Center

The radar center is the fixed latitude/longitude used as the middle of the display.

Example:

```ini
center_lat = 30.14705507846894
center_lon = -95.39204791784302
```

To center the radar somewhere else, update those two values in `config.ini`.

### Radar Range

The radar range controls how far out aircraft are fetched and displayed.

Example:

```ini
range_mi = 10
```

Smaller values show fewer aircraft. Larger values show more aircraft but may make the screen busier.

Common values:

```ini
range_mi = 5
range_mi = 10
range_mi = 25
```

### Refresh Rate

The refresh rate controls how often the radar fetches new aircraft data.

Example:

```ini
refresh_seconds = 15
```

A lower value refreshes more often, but may increase the chance of API rate limiting. A higher value reduces API requests and screen updates.

### Display Output

The display output is controlled by:

```ini
display_type = spi
```

Use `spi` for the GC9A01 round display, `hdmi` for an HDMI screen, or `preview` when you only want to save the PNG preview.

You can also override this for a single run:

```bash
python radar.py --display hdmi
```

### Aircraft Heading Lines

Aircraft heading lines are controlled with:

```ini
show_heading_lines = true
heading_line_length = 26
heading_line_gap = 7
heading_line_width = 2
```

Set `show_heading_lines` to `false` if you only want the aircraft triangles without the line in front.

## Display Position and Size

The green radar circle can be adjusted in `radar.py`:

```python
CENTER_X = WIDTH // 2
CENTER_Y = (HEIGHT // 2) - 4
RADAR_RADIUS = 112
```

Larger `RADAR_RADIUS` uses more of the round display. Smaller `RADAR_RADIUS` prevents clipping around the edges.

## ADS-B API

Aircraft data is fetched from:

```text
https://opendata.adsb.fi/api/v3/lat/{lat}/lon/{lon}/dist/{range}
```

Example test:

```bash
curl "https://opendata.adsb.fi/api/v3/lat/30.14705507846894/lon/-95.39204791784302/dist/10" | head
```

The API returns aircraft in the top-level JSON field:

```json
"ac": []
```

The script parses that field and plots aircraft that include latitude and longitude.

The script uses these ADS-B fields when available:

| Field | Used For |
| ----- | -------- |
| `flight` | Callsign |
| `r` | Registration fallback |
| `hex` | Hex identifier fallback |
| `t` | Aircraft type/designator, such as `B738`, `A21N`, or `P28A` |
| `alt_baro` | Barometric altitude |
| `alt_geom` | Geometric altitude fallback |
| `track` | Aircraft heading/track line and triangle direction |
| `lat` / `lon` | Aircraft position |

## Auto-Start on Boot

A systemd service can be used to start the radar automatically when the Pi boots.

Create a startup script:

```bash
nano ~/plane-radar-pi/start-radar.sh
```

Contents:

```bash
#!/bin/bash

cd /home/jason/plane-radar-pi
source /home/jason/plane-radar-pi/.venv/bin/activate

exec python /home/jason/plane-radar-pi/radar.py
```

Make it executable:

```bash
chmod +x ~/plane-radar-pi/start-radar.sh
```

Create the service:

```bash
sudo nano /etc/systemd/system/plane-radar.service
```

Service file:

```ini
[Unit]
Description=Plane Radar Pi
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/jason/plane-radar-pi
ExecStart=/home/jason/plane-radar-pi/.venv/bin/python /home/jason/plane-radar-pi/radar.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Reload systemd:

```bash
sudo systemctl daemon-reload
```

Enable the service at boot:

```bash
sudo systemctl enable plane-radar.service
```

Start it now:

```bash
sudo systemctl start plane-radar.service
```

Check status:

```bash
sudo systemctl status plane-radar.service --no-pager
```

Verify it is enabled:

```bash
sudo systemctl is-enabled plane-radar.service
```

Expected output:

```text
enabled
```

## Service Commands

Start:

```bash
sudo systemctl start plane-radar.service
```

Stop:

```bash
sudo systemctl stop plane-radar.service
```

Restart:

```bash
sudo systemctl restart plane-radar.service
```

Check status:

```bash
sudo systemctl status plane-radar.service --no-pager
```

View logs:

```bash
journalctl -u plane-radar.service -f
```

Disable auto-start:

```bash
sudo systemctl disable plane-radar.service
```

## Troubleshooting

### Display does not show up

Check framebuffer devices:

```bash
ls -l /dev/fb*
```

Check for the GC9A01 overlay:

```bash
ls /boot/firmware/overlays | grep -i gc9
```

Check boot config:

```bash
grep -E "spi|gc9|dtoverlay" /boot/firmware/config.txt
```

Expected lines include:

```ini
dtparam=spi=on
dtoverlay=gc9a01,width=240,height=240,rotate=0
```

### Display works, but no aircraft appear

Test the API manually:

```bash
curl "https://opendata.adsb.fi/api/v3/lat/30.14705507846894/lon/-95.39204791784302/dist/10" | head
```

If the API returns aircraft but the screen does not show them, check that the script parses:

```python
data.get("ac", [])
```

not:

```python
data.get("aircraft", [])
```

Also check your `config.ini` values:

```bash
cat config.ini
```

Make sure `center_lat`, `center_lon`, and `range_mi` are correct.

### API returns 429 Too Many Requests

If you see an error like this:

```text
429 Client Error: Too Many Requests
```

increase the refresh interval in `config.ini`:

```ini
[radar]
refresh_seconds = 15
```

or:

```ini
refresh_seconds = 30
```

Then restart the script.

### Aircraft type is missing

The aircraft type line uses the ADS-B `t` field when it is available.

You can inspect the API fields with:

```bash
python - <<'PY'
import requests
import configparser

config = configparser.ConfigParser()
config.read("config.ini")

lat = config["radar"].getfloat("center_lat")
lon = config["radar"].getfloat("center_lon")
dist = config["radar"].getfloat("range_mi")

url = f"https://opendata.adsb.fi/api/v3/lat/{lat}/lon/{lon}/dist/{dist}"
data = requests.get(url, timeout=8).json()

for ac in data.get("ac", [])[:5]:
    print("-" * 60)
    print("CALLSIGN:", ac.get("flight") or ac.get("r") or ac.get("hex"))
    print("TYPE:", ac.get("t"))
    print("DESCRIPTION:", ac.get("desc"))
    print("ALL KEYS:")
    print(sorted(ac.keys()))
PY
```

### Preview image does not update

Run in preview mode:

```bash
python radar.py --display preview
```

Then check the preview file:

```bash
ls -lh /tmp/plane-radar-preview.png
```

Copy it to your Mac:

```bash
scp jason@yun:/tmp/plane-radar-preview.png ~/Downloads/
open ~/Downloads/plane-radar-preview.png
```

### HDMI output does not show

Install `fbi`:

```bash
sudo apt update
sudo apt install -y fbi
```

Check available framebuffer devices:

```bash
ls -l /dev/fb*
```

If needed, update the framebuffer device in `config.ini`:

```ini
[display]
device = /dev/fb0
```

Then try:

```bash
python radar.py --display hdmi
```

### Service does not auto-start after reboot

Check status:

```bash
sudo systemctl status plane-radar.service --no-pager
```

Check if enabled:

```bash
sudo systemctl is-enabled plane-radar.service
```

If it says disabled, enable it:

```bash
sudo systemctl enable plane-radar.service
```

Then reboot again:

```bash
sudo reboot
```

### View service logs

```bash
journalctl -u plane-radar.service -n 80 --no-pager
```

## Current Limitations

This is a proof of concept.

Known limitations:

* HDMI mode depends on `fbi`.
* Labels can overlap when several aircraft are close together.
* The display layout is currently designed for a 240×240 round screen.
* HDMI output currently shows the same 240×240 radar image rather than a redesigned full-screen layout.
* It depends on internet access and the public ADS-B API.

## Future Improvements

Possible next steps:

* add physical buttons for range selection
* add a larger HDMI-optimized layout
* improve label collision handling
* add aircraft speed display
* add an airport/runway overlay
* add Wi-Fi status indicator
* add graceful “no aircraft found” screen
* design a 3D printed enclosure for the Pi and round display

## License

This project is licensed under the MIT License. See `LICENSE` for details.

## Credits

Inspired by the ESP32 Plane Radar concept originally designed for an ESP32-C3 and GC9A01 round display.

This Raspberry Pi version was built as a proof-of-concept port using:

* Raspberry Pi 4B
* GC9A01 SPI display
* Python
* Pillow
* fbi for HDMI output
* Raspberry Pi framebuffer overlay
* opendata.adsb.fi ADS-B data
