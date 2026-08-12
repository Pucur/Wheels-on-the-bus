# 🚀 RPi Bus Controller Dashboard – Wheels on the Bus Edition 🚌🎵

A full-featured Raspberry Pi controller system for a toy bus model featuring music playback, LED effects, live camera streaming, battery monitoring, and a modern web dashboard.

Perfect for DIY IoT projects, smart toys, and interactive kids’ builds.

---

# 🎯 Features

- 🖥️ Dual TM1637 displays and SSD1306 OLED dashboard
- 🎶 MPV-based music player with MP3/WAV playback
- 🌈 WS2812B LED effects
- 📹 MJPEG + RTSP live camera streaming
- 🔋 INA226 battery monitoring
- 🎮 USB gamepad support
- 🚗 PWM motor + steering control
- 📱 Flask + SocketIO web dashboard
- 🔊 PyAudio microphone + MPG123 sound effects
- ⏰ Clock mode and sleep mode

> 🔔 Bus horn automatically plays every hour.

---

# 📸 Project Images

![Bus](https://i.kek.sh/NltX0tUpo2y.jpg)

![Bus](https://i.kek.sh/Fm6l8uEGPOo.jpg)

![Bus](https://i.kek.sh/WRos1KX968G.jpg)

---

# 📸 Wirings

![Bus](https://i.kek.sh/HnJP23avNsx.jpg)

---

# 🛠️ Hardware Setup

## Raspberry Pi (Zero 2 / 4 recommended)

### GPIO Connections

| Function | GPIO |
|---|---|
| Motor ENA | GPIO 5 |
| Motor IN1 | GPIO 26 |
| Motor IN2 | GPIO 12 |
| Motor IN3 | GPIO 16 |
| Motor IN4 | GPIO 20 |
| Status LED | GPIO 25 |
| WS2812B LED Strip | GPIO 13 |
| TM1637 #1 CLK | GPIO 23 |
| TM1637 #1 DIO | GPIO 24 |
| TM1637 #2 CLK | GPIO 27 |
| TM1637 #2 DIO | GPIO 22 |

---

## I2C Devices

| Device | Address |
|---|---|
| SSD1306 OLED (128x32) | `0x3C` |
| INA226 Battery Monitor | `0x40` |

---

## Additional Hardware

- 📷 Raspberry Pi Camera Module
- 🔊 USB Speaker
- 🎤 USB Microphone
- 🎮 USB Gamepad Controller

---

# 📦 Required Libraries

Install dependencies:

```bash
pip install rpi-ws281x evdev luma.oled flask-socketio pyaudio numpy tm1637 pillow smbus2 aiohttp
```

---

# ⚙️ Configuration

Edit the global variables inside `paste.txt`:

```python
MUSICDIR = "/home/$PATH/script/music"
AUDIOBOOKDIR = "/home/$PATH/script/hangoskonyv"

currentvolume = 35
currentspeedpercent = 100
micvolume = 0.4

camerawidth, cameraheight = 854, 480

# SSL Certificates
sslfullchain.pem
sslkey.pem
```

### MPV Socket

```text
/tmp/mpvsocket
```

---

# 🚀 Quick Start

## 1. Install System Dependencies

```bash
sudo apt update
sudo apt install mpg123 python3-pip i2c-tools
```

---

## 2. Install Python Packages

```bash
pip3 install -r requirements.txt
```

---

## 3. Enable Raspberry Pi Interfaces

```bash
sudo raspi-config
```

Enable:
- I2C
- Camera
- SSH

---

## 4. Run

```bash
python3 bus.py
```

---

# 🌐 Web Dashboard

## Main Dashboard

```text
https://<rpi-ip>:443
```

## MJPEG Stream

```text
https://<rpi-ip>:443/stream.mjpg
```

## RTSP Stream

```text
rtsp://<rpi-ip>:8554/
```

## WebSocket Endpoint

```text
/ws
```

---

# 🎮 Controller Mapping

| Button | Action |
|---|---|
| A (South) | LED Toggle / Sleep |
| B (East) | TM1637 Toggle |
| X (West) | OLED On/Off |
| Y (North) | Bus Mode / Shuffle |
| LB / RB | Speed +/- |
| LT / RT | Steering |
| Right Stick | Drive |
| D-Pad Up/Down | Volume +/- |

---

# 🌈 LED Effects

| Effect | Command | Description |
|---|---|---|
| Rainbow | `rainbow` | Cycling rainbow 🌈 |
| Breathing | `breathing` | Pulsing blue 💙 |
| Scanner | `scanner` | LED sweep 🔦 |
| Meteor | `meteor` | Shooting stars ☄️ |
| Sparkle | `sparkle` | Random twinkles ⭐ |
| Police | `police` | Red/blue flashing 🚔 |
| Pulse | `pulse` | Rainbow heartbeat ❤️ |

Example:

```text
ledeffect=police
```

---

# 🔋 Battery Monitoring

INA226 sensor provides:
- Voltage
- Current
- Battery percentage

## API Endpoint

```http
GET /batterystatus
```

Example response:

```json
{
  "voltage": 7.2,
  "percent": 65
}
```

---

# 🎥 Camera Streaming

## Supported Resolutions

- `854x480` (default)
- `1280x720`

## Features

- RTSP server
- MJPEG streaming
- WebSocket control
- Remote start/stop

---

# 🎵 Music & Audio

## Features

- MPV local playback
- MP3/WAV support
- Audiobook mode
- Shuffle playlists
- Karaoke microphone mode

## Sound Effects

- Bus horn
- Engine sound
- Turn indicators
- Sleep sounds

## API Example

```http
/sendcommand?cmd=playmusic&value=track.mp3
```

---

# 🐛 Troubleshooting

| Problem | Solution |
|---|---|
| LEDs not working | `sudo usermod -a -G gpio $USER` then reboot |
| Camera not detected | `vcgencmd get_camera` |
| MPV crashes | `sudo apt install libmpv2` |
| I2C errors | `sudo i2cdetect -y 1` |
| Microphone silent | `arecord -l` |
| HTTPS warning | Accept self-signed certificate |

---

# 📱 Dashboard

![Dashboard](https://i.kek.sh/A8uHMcfELdM.png)

Features:
- Responsive Flask UI
- SocketIO real-time updates
- Drive control
- Music playlists
- LED effects

---

# 🔒 Security

- HTTPS/SSL enabled
- WebSocket CORS support
- Designed for local network usage only

⚠️ No authentication implemented.

---

# 📈 Performance

| Resource | Usage |
|---|---|
| CPU Idle | <20% |
| Camera + Effects | ~50% |
| RAM | ~150 MB |

### Background Threads

- Battery monitor
- Camera service
- MPV listener
- Command queue processor

Tested on:
- Raspberry Pi 4
- Raspberry Pi Zero 2 W

---

# ❤️ About

> “For Leila and Bernat, the meaning of my life.”

Custom Raspberry Pi project by  
**Alex Ladonyi**

---

# ⭐ Support

If your bus goes **"beep beep"** successfully, leave a ⭐ on GitHub! 🚌
