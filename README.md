# SpiteIRL

A self hosted IRL streaming backpack controller built on a Raspberry Pi 4B. Plug in your camera, power on the Pi, and access your personal streaming dashboard from anywhere in the world. no extra software needed, just a browser.

SpiteIRL captures your camera feed, shows you a live preview in a web dashboard, and pushes your stream to Twitch, YouTube, or any custom RTMP destination at the same time.

---

## What It Does

- Captures video and audio from a USB camera (tested with Sony ZV-E10 via USB C)
- Shows a low-res live preview in your browser dashboard from anywhere
- Streams full quality video to Twitch, YouTube, or a custom RTMP URL
- Lets you manage multiple stream destinations per account
- Protects access with a product key and login system
- Runs completely headless on a Raspberry Pi. no monitor, keyboard, or mouse needed after setup

---

## What You Need

- Raspberry Pi 4B (2GB RAM or more recommended)
- MicroSD card (16GB or more)
- USB camera that supports MJPEG output
- Internet connection (WiFi or ethernet or modem)
- A free Cloudflare account (for remote access)
- A Twitch or YouTube account with a stream key (if you want to go live)

---

## Setup Guide

### Step 1 — Flash the Pi

1. Download [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on your laptop
2. Flash **Raspberry Pi OS Lite (64-bit)** to your SD card
3. Before writing, click the settings gear and:
   - Enable SSH
   - Set username to `pi` and a password you'll remember
   - Enter your WiFi name and password
4. Insert the SD card into the Pi and power it on
5. After about 60 seconds, SSH in from your laptop:
   ```
   ssh pi@raspberrypi.local
   ```

---

### Step 2 — Install Dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y ffmpeg python3-venv git mariadb-server v4l-utils
```

Start and enable MariaDB:

```bash
sudo systemctl start mariadb
sudo systemctl enable mariadb
sudo mariadb-secure-installation
```

When it asks questions, answer:
- Switch to unix_socket authentication: **n**
- Change root password: **y** (set something you'll remember)
- Remove anonymous users: **y**
- Disallow root login remotely: **y**
- Remove test database: **y**
- Reload privilege tables: **y**

---

### Step 3 — Set Up the Database

```bash
sudo mysql -u root -p
```

Paste this whole block:

```sql
CREATE DATABASE spite;
USE spite;

CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL
);

CREATE TABLE destinations (
    id VARCHAR(36) PRIMARY KEY,
    platform VARCHAR(20) NOT NULL,
    stream_key VARCHAR(255) NOT NULL,
    label VARCHAR(100) NOT NULL,
    enabled BOOLEAN DEFAULT FALSE,
    username VARCHAR(50) NOT NULL DEFAULT ''
);

CREATE TABLE product_keys (
    key_code VARCHAR(50) PRIMARY KEY,
    used BOOLEAN DEFAULT FALSE,
    used_by VARCHAR(50) DEFAULT NULL
);

INSERT INTO product_keys (key_code) VALUES ('YOURCODEHERE');

EXIT;
```

Replace `YOURCODEHERE` with whatever access code you want to give people. You can add more codes later by running:

```bash
sudo mysql -u root -p -e "INSERT INTO spite.product_keys (key_code) VALUES ('ANOTHERCODE');"
```

---

### Step 4 — Clone the Repo and Install Python Packages

```bash
cd ~
git clone https://github.com/LeviRowh/spiteIRL.git
cd spiteIRL
python3 -m venv venv
source venv/bin/activate
pip install fastapi uvicorn python-multipart mysql-connector-python
```

---

### Step 5 — Configure Your Camera and Database Password

Open `main.py` and update these lines near the top to match your setup:

```python
VIDEO_DEVICE = "/dev/video0"   # your camera device
AUDIO_DEVICE = "hw:3,0"        # your audio device
```

To find your camera device:
```bash
v4l2-ctl --list-devices
```

To find your audio device:
```bash
arecord -l
```

Also update the database password in `main.py` and `destinations.py` wherever you see:
```python
password="1234"
```

Change it to match the password you set in Step 2.

---

### Step 6 — Set Up Autostart

Create a systemd service so the app starts automatically when the Pi boots:

```bash
sudo nano /etc/systemd/system/spiteirl.service
```

Paste this:

```ini
[Unit]
Description=SpiteIRL FastAPI Server
After=network-online.target mariadb.service
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/spiteIRL
ExecStart=/home/pi/spiteIRL/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable spiteirl
sudo systemctl start spiteirl
```

---

### Step 7 — Set Up Cloudflare Tunnel (Remote Access)

This gives you a public URL to access your dashboard from anywhere without port forwarding.

Install cloudflared:

```bash
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb -o cloudflared.deb
sudo dpkg -i cloudflared.deb
```

Create a quick tunnel service:

```bash
sudo nano /etc/systemd/system/cloudflared-quick.service
```

Paste this:

```ini
[Unit]
Description=Cloudflare Quick Tunnel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
ExecStart=/usr/bin/cloudflared tunnel --url http://localhost:8000 --no-autoupdate
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable cloudflared-quick
sudo systemctl start cloudflared-quick
```

To get your public URL after every reboot:

```bash
sudo journalctl -u cloudflared-quick --no-pager | grep -i "trycloudflare.com" | tail -1
```

> **Note:** The free quick tunnel URL changes every time the Pi reboots. For a permanent URL, register a domain through Cloudflare and set up a named tunnel I think it cost somewhere around like $10 for a year maybe

---

## Using the Dashboard

1. Get your current URL using the command above
2. Open it in any browser on any device
3. Enter your product key
4. Login or create an account
5. The live preview will load automatically
6. To stream to Twitch or YouTube, scroll down to **Stream Destinations**, add your stream key, and hit **Go Live**

---

## Adding More Product Keys

```bash
sudo mysql -u root -p -e "INSERT INTO spite.product_keys (key_code) VALUES ('NEWKEY');"
```

---

## Tech Stack

- **Raspberry Pi 4B** — runs everything
- **FFmpeg** — captures camera, encodes video, pushes to RTMP
- **FastAPI** — backend web server
- **MariaDB** — stores users, destinations, and product keys
- **HLS.js** — plays the live preview in the browser
- **Cloudflare Tunnel** — exposes the dashboard to the internet securely
- **Chart.js** — displays stream metrics

---

## Known Limitations

- Only one stream destination can be live at a time (CPU limitation of Pi 4B but could likely multi stream with something like the Orange Pi 5 or Jetson Nano)
- The free Cloudflare tunnel URL changes on every reboot
- The ZV-E10 only outputs MJPEG over USB, which requires software decoding and uses significant CPU, if you have a Elgato Cam Link this would not require you to USB stream and you could send the clean raw ccamera feed over without the decode overhead.

---

## P.S

- happy streaming!!!
