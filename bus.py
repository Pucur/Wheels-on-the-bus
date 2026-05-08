import RPi.GPIO as GPIO
import time, signal, sys, os, subprocess, threading, queue, random, socket, json, io, datetime, smbus2, asyncio
from evdev import InputDevice, list_devices, ecodes as e
from luma.oled.device import ssd1306 as luma_ssd1306_device
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from PIL import ImageFont, Image
from rpi_ws281x import PixelStrip, Color
import pyaudio, numpy as np, base64, logging, subprocess
import tm1637
from flask import Flask, render_template, request, jsonify, Response
from flask_socketio import SocketIO
from aiohttp import web, WSMsgType
import ssl
import asyncio
import json
import subprocess

app = Flask(__name__)
app.config['SECRET_KEY'] = 'hifi-zero-perfect'
socketio = SocketIO(app, cors_allowed_origins="*", logger=False, engineio_logger=False)

# --- GLOBAL VARIABLES ---

led_state = False
display_on = False
oled_on = False
oled_current_text = "BUDAPEST"
oled_backup_text = oled_current_text
oled_timer_id = 0
tm_current_val = 505
current_speed_percent = 100
current_volume = 35
last_button_state = {}
cmd_queue = queue.Queue()
REPEAT_INTERVAL = 0.15
SPEED_STEP = 10
manual_track_playing = None
audiobook_playing = False
current_music_index = 0
bus_sounds_on = False
bus_engine_process = None
bus_engine_process2 = None
bus_engine_boot = False
shuffle_mode = False
tm_clock_enabled = False
led_effect_thread = None
led_effect_stop = threading.Event()
current_led_effect = None
last_bushorn_hour = -1


ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ssl_context.load_cert_chain('ssl/fullchain.pem', 'ssl/key.pem')
camera_process = None
camera_active = False
camera_width = 854
camera_height = 480


# --- INA226 CONFIG ---

battery_voltage = None
battery_current = None
battery_percent = None

I2C_BUS = 1
INA226_ADDR = 0x40

REG_CONFIG = 0x00
REG_SHUNT_VOLTAGE = 0x01
REG_BUS_VOLTAGE = 0x02
REG_CURRENT = 0x04
REG_CALIBRATION = 0x05

SHUNT_RESISTOR = 0.1
MAX_CURRENT = 3.0
CALIBRATION = int(0.00512 / (SHUNT_RESISTOR * MAX_CURRENT))

# --- MIC ---

mic_p = None
mic_stream = None
mic_audio_queue = []
mic_volume = 0.4
mic_active = False
MIC_FORMAT = pyaudio.paInt16
MIC_CHANNELS = 2
MIC_RATE = 22050
MIC_CHUNK = 2048

# --- GPIO ---

ENA, IN1, IN2, IN3, IN4 = 5, 26, 12, 16, 20
LED_PIN = 25
TM_CLK, TM_DIO = 23, 24
TM2_CLK, TM2_DIO = 27, 22
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup([ENA, IN1, IN2, IN3, IN4, LED_PIN], GPIO.OUT, initial=GPIO.LOW)
pwm_a = GPIO.PWM(ENA, 100)
pwm_a.start(0)

# --- TM1637 & OLED ---

time.sleep(1)
tm1637_display = tm1637.TM1637(clk=TM_CLK, dio=TM_DIO)
tm1637_display2 = tm1637.TM1637(clk=TM2_CLK, dio=TM2_DIO)
tm1637_display.brightness = 7
tm1637_display.show("    ")
tm1637_display2.brightness = 7
tm1637_display2.show("    ")
tm_lock = threading.Lock()

OLED_WIDTH, OLED_HEIGHT = 128, 32
serial_interface = i2c(port=1, address=0x3C)
oled_device = luma_ssd1306_device(serial_interface, width=OLED_WIDTH, height=OLED_HEIGHT)

# --- LED CONFIG ---

LED_COUNT = 5
LEDS_PIN = 13
LED_FREQ_HZ = 800000
LED_DMA = 10
LED_BRIGHTNESS = 255
LED_INVERT = False
LED_CHANNEL = 1

strip = PixelStrip(
    LED_COUNT,
    LEDS_PIN,
    LED_FREQ_HZ,
    LED_DMA,
    LED_INVERT,
    LED_BRIGHTNESS,
    LED_CHANNEL
)
strip.begin()

def wheel(pos):
    pos &= 255
    if pos < 85:
        return Color(pos * 3, 255 - pos * 3, 0)
    elif pos < 170:
        pos -= 85
        return Color(255 - pos * 3, 0, pos * 3)
    else:
        pos -= 170
        return Color(0, pos * 3, 255 - pos * 3)


# --- INA CURRENT SENSOR ---

def ina_write_register(bus, reg, value):
    bus.write_word_data(
        INA226_ADDR,
        reg,
        ((value << 8) & 0xFF00) | (value >> 8)
    )

def ina_read_register(bus, reg):
    raw = bus.read_word_data(INA226_ADDR, reg)
    return ((raw & 0xFF) << 8) | (raw >> 8)

def voltage_to_percent(v):
    MIN_V = 6.6
    MAX_V = 8.4

    if v is None or v < 1.0:
        return None
    if v <= MIN_V:
        return 0
    if v >= MAX_V:
        return 100

    return int((v - MIN_V) / (MAX_V - MIN_V) * 100)

def battery_monitor_thread():
    global battery_voltage, battery_current, battery_percent

    bus = smbus2.SMBus(I2C_BUS)

    ina_write_register(bus, REG_CALIBRATION, CALIBRATION)
    ina_write_register(bus, REG_CONFIG, 0x4527)

    while True:
        try:
            bus_v_raw = ina_read_register(bus, REG_BUS_VOLTAGE)
            shunt_v_raw = ina_read_register(bus, REG_SHUNT_VOLTAGE)
            cur_raw = ina_read_register(bus, REG_CURRENT)

            battery_voltage = bus_v_raw * 1.25 / 1000.0
            shunt_voltage = shunt_v_raw * 2.5e-6
            battery_current = cur_raw * (MAX_CURRENT / 32768.0)
            battery_percent = voltage_to_percent(battery_voltage)

        except Exception as e:
            battery_voltage = None
            battery_current = None
            battery_percent = None

        time.sleep(5)

# --- LED EFFECT CONTROL ---

led_effect_thread = None
led_effect_stop = threading.Event()
current_led_effect = None


def clear_strip():
    for i in range(LED_COUNT):
        strip.setPixelColor(i, 0)
    strip.show()

# --- LED EFFECTS ---

def rainbow_fade(wait=0.01):
    while not led_effect_stop.is_set():
        for j in range(256):
            if led_effect_stop.is_set():
                return
            for i in range(LED_COUNT):
                strip.setPixelColor(i, wheel((i * 256 // LED_COUNT + j) & 255))
            strip.show()
            time.sleep(wait)


def breathing(color=Color(0, 0, 255), wait=0.02):
    r = (color >> 16) & 255
    g = (color >> 8) & 255
    b0 = color & 255

    while not led_effect_stop.is_set():
        for k in range(0, 256, 5):
            if led_effect_stop.is_set():
                return
            for i in range(LED_COUNT):
                strip.setPixelColor(i, Color(r * k // 255, g * k // 255, b0 * k // 255))
            strip.show()
            time.sleep(wait)

        for k in range(255, -1, -5):
            if led_effect_stop.is_set():
                return
            for i in range(LED_COUNT):
                strip.setPixelColor(i, Color(r * k // 255, g * k // 255, b0 * k // 255))
            strip.show()
            time.sleep(wait)


def scanner(color=Color(255, 0, 0), wait=0.05):
    while not led_effect_stop.is_set():
        for i in range(LED_COUNT):
            if led_effect_stop.is_set():
                return
            clear_strip()
            strip.setPixelColor(i, color)
            strip.show()
            time.sleep(wait)

        for i in range(LED_COUNT - 2, 0, -1):
            if led_effect_stop.is_set():
                return
            clear_strip()
            strip.setPixelColor(i, color)
            strip.show()
            time.sleep(wait)


def meteor(color=Color(255, 255, 255), decay=64, wait=0.05):
    r = (color >> 16) & 255
    g = (color >> 8) & 255
    b0 = color & 255

    trail = [0] * LED_COUNT

    while not led_effect_stop.is_set():
        for i in range(LED_COUNT * 2):
            if led_effect_stop.is_set():
                return

            for j in range(LED_COUNT):
                trail[j] = max(0, trail[j] - decay)

            if i < LED_COUNT:
                trail[i] = 255

            for j in range(LED_COUNT):
                v = trail[j]
                strip.setPixelColor(j, Color(r * v // 255, g * v // 255, b0 * v // 255))

            strip.show()
            time.sleep(wait)


def rainbow_pulse(wait=0.02):
    while not led_effect_stop.is_set():
        for j in range(256):
            if led_effect_stop.is_set():
                return
            for i in range(LED_COUNT):
                strip.setPixelColor(i, wheel((j + i * 10) & 255))
            strip.show()
            time.sleep(wait)


def sparkle(color=Color(255, 255, 255), wait=0.05):
    while not led_effect_stop.is_set():
        i = random.randint(0, LED_COUNT - 1)
        strip.setPixelColor(i, color)
        strip.show()
        time.sleep(wait)
        strip.setPixelColor(i, 0)
        strip.show()


def police(wait=0.15):
    left0 = 0
    left1 = min(1, LED_COUNT - 1)
    right0 = LED_COUNT - 1
    right1 = max(0, LED_COUNT - 2)

    while not led_effect_stop.is_set():
        for _ in range(3):
            if led_effect_stop.is_set():
                return
            clear_strip()
            strip.setPixelColor(left0, Color(255, 0, 0))
            strip.setPixelColor(left1, Color(255, 0, 0))
            strip.show()
            time.sleep(wait)

            if led_effect_stop.is_set():
                return
            clear_strip()
            strip.setPixelColor(right0, Color(0, 0, 255))
            strip.setPixelColor(right1, Color(0, 0, 255))
            strip.show()
            time.sleep(wait)

# --- START/STOP LED ---

def start_led_effect(effect_func):
    global led_effect_thread, current_led_effect

    led_effect_stop.set()

    if led_effect_thread and led_effect_thread.is_alive():
        led_effect_thread.join(timeout=1.0)

    clear_strip()

    led_effect_stop.clear()
    current_led_effect = effect_func
    led_effect_thread = threading.Thread(target=effect_func, daemon=True)
    led_effect_thread.start()

def stop_led_effect():
    led_effect_stop.set()
    if led_effect_thread and led_effect_thread.is_alive():
        led_effect_thread.join(timeout=1.0)
    clear_strip()

# --- MICROPHONE ---

def init_mic_audio():
    global mic_p, mic_stream
    with mic_stream_lock:
        if mic_stream is not None:
            return True
        try:
            mic_p = pyaudio.PyAudio()
            mic_stream = mic_p.open(
                format=MIC_FORMAT,
                channels=MIC_CHANNELS,
                rate=MIC_RATE,
                output=True,
                output_device_index=0
            )
            return True
        except Exception as e:
            return False

def mic_audio_thread():
    global mic_stream, mic_audio_queue

    while True:
        while mic_audio_queue:
            try:
                data = mic_audio_queue.pop(0)
                if mic_stream:
                    mic_stream.write(data)
            except:
                pass
        time.sleep(0.001)

def cleanup_mic():
    global mic_stream, mic_p
    if mic_stream:
        mic_stream.stop_stream()
        mic_stream.close()
        mic_stream = None
    if mic_p:
        mic_p.terminate()
        mic_p = None

# --- Music / Sounds ---

MUSIC_DIR = "/home/$PATH/script/music"
AUDIOBOOK_DIR = "/home/$PATH/script/hangoskonyv"
SOUNDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")
music_files = [f for f in os.listdir(MUSIC_DIR) if f.lower().endswith(('.mp3', '.wav'))]
audiobook_files = sorted([f for f in os.listdir(AUDIOBOOK_DIR) if f.lower().endswith((".mp3", ".wav"))])
mpv_process = None
MPV_SOCKET = "/tmp/mpvsocket"

# --- MPV helper definitions ---

def start_mpv_daemon():
    global mpv_process
    if os.path.exists(MPV_SOCKET):
        os.remove(MPV_SOCKET)
    mpv_process = subprocess.Popen([
        "mpv", "--no-video", f"--input-ipc-server={MPV_SOCKET}",
        "--idle=yes", "--volume="+str(current_volume)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    timeout = 0
    while not os.path.exists(MPV_SOCKET) and timeout < 5:
        time.sleep(0.1)
        timeout += 0.1

def mpv_command(cmd_list):
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(MPV_SOCKET)
        cmd = json.dumps({"command": cmd_list}) + "\n"
        sock.send(cmd.encode())
        sock.close()
    except:
        pass

def mpv_loadfile(file_path):
    if os.path.exists(file_path):
        mpv_command(["loadfile", file_path, "replace"])

def set_mpv_volume(vol_percent):
    global current_volume
    vol_percent = max(0, min(100, int(vol_percent)))
    current_volume = vol_percent
    mpv_command(["set_property", "volume", current_volume])

# --- OLED display ---

def update_oled_display(text="", duration=None):
    global oled_current_text, oled_backup_text, oled_on, oled_timer_id
    was_off = not oled_on
    my_id = oled_timer_id
    if duration is not None:
        oled_current_text = text
        with canvas(oled_device) as draw:
            draw_text(draw, text)

        def restore_later(timer_id):
            global oled_current_text, oled_on
            time.sleep(duration)
            if timer_id != oled_timer_id:
                return
            if was_off:
                oled_device.clear()
                oled_on = False
                oled_current_text = ""
            else:
                with canvas(oled_device) as draw:
                    draw_text(draw, oled_backup_text)
                oled_current_text = oled_backup_text

        threading.Thread(target=restore_later, args=(my_id,), daemon=True).start()
    else:
        oled_current_text = text
        if not duration:
            oled_backup_text = text
        if oled_on:
            with canvas(oled_device) as draw:
                draw_text(draw, text)

def draw_text(draw, text):
    max_font_size = 26
    min_font_size = 8
    text_len = len(text)
    try:
        if text_len < 17:
            font_size = min(max_font_size, max(min_font_size, int(max_font_size * 8 / max(1, text_len))))
            font = ImageFont.truetype("DejaVuSansMono.ttf", font_size)
            bbox = draw.textbbox((0,0), text, font=font)
            w,h = bbox[2]-bbox[0], bbox[3]-bbox[1]
            draw.text(((OLED_WIDTH-w)/2, (OLED_HEIGHT-h)/2), text, font=font, fill="white")
        else:
            line1 = text[:16]
            line2 = text[16:]
            available_height = OLED_HEIGHT - 10
            font_size = max(min_font_size, available_height // 2 - 2)
            font = ImageFont.truetype("DejaVuSansMono.ttf", font_size)
            bbox1 = draw.textbbox((0,0), line1, font=font)
            w1,h1 = bbox1[2]-bbox1[0], bbox1[3]-bbox1[1]
            draw.text(((OLED_WIDTH-w1)/2, 5), line1, font=font, fill="white")
            bbox2 = draw.textbbox((0,0), line2, font=font)
            w2,h2 = bbox2[2]-bbox2[0], bbox2[3]-bbox2[1]
            draw.text(((OLED_WIDTH-w2)/2, OLED_HEIGHT//2 + 5), line2, font=font, fill="white")
    except:
        font = ImageFont.load_default()
        draw.text((0,0), text, font=font, fill="white")

# --- TM1637 ---

def tm1637_display_number_right_aligned(val):
    with tm_lock:
        val_str = str(val)[-4:].rjust(4)
        tm1637_display.show(val_str)
        tm1637_display2.show(val_str)

def tm_clock():
    global tm1637_display, tm1637_display2, tm_clock_enabled, last_bushorn_hour, audiobook_playing

    colon = True

    while True:
        try:
            if tm_clock_enabled:
                now = datetime.datetime.now()
                h = now.hour
                m = now.minute
                s = now.second

                with tm_lock:
                    tm1637_display.show(f"{h:02d}{m:02d}", sep=colon)
                    tm1637_display2.show(f"{h:02d}{m:02d}", sep=colon)

                colon = (s % 2 == 0)

                if audiobook_playing == False:
                    if m == 0 and s == 0 and last_bushorn_hour != h:
                        last_bushorn_hour = h
                        threading.Thread(target=play_bushorn, daemon=True).start()

        except Exception as e:
            print("[TM_CLOCK ERROR]", e)

        time.sleep(1)

# --- Drive ---

def set_drive_speed(speed_percent_input):
    global bus_engine_process, bus_sounds_on

    actual_speed = (abs(speed_percent_input) / 100.0) * current_speed_percent
    GPIO.output(IN1, GPIO.HIGH if speed_percent_input >= 0 else GPIO.LOW)
    GPIO.output(IN2, GPIO.LOW if speed_percent_input >= 0 else GPIO.HIGH)
    pwm_a.ChangeDutyCycle(actual_speed)

    if bus_sounds_on and speed_percent_input != 0:
        if bus_engine_process is None or bus_engine_process.poll() is not None:
            bus_engine_process = subprocess.Popen(['mpg123', '-q', '-o', 'alsa:dmix','-f','1284', os.path.join(SOUNDS_DIR, "busriding.mp3")])

def stop_drive():
    GPIO.output([IN1, IN2], GPIO.LOW)
    pwm_a.ChangeDutyCycle(0)

def stop_steer():
    GPIO.output([IN3, IN4], GPIO.LOW)

def execute_steer_direct_dc(direction):
    global bus_engine_process2, bus_sounds_on
    GPIO.output(IN4, GPIO.HIGH if direction=='left' else GPIO.LOW)
    GPIO.output(IN3, GPIO.LOW if direction=='left' else GPIO.HIGH)
    if bus_sounds_on:
        if bus_engine_process2 is None or bus_engine_process2.poll() is not None:
            bus_engine_process2 = subprocess.Popen(['mpg123', '-q', '-o', 'alsa:dmix', os.path.join(SOUNDS_DIR, "indicator.mp3")])

# --- Cleanup ---

def cleanup_all():
    stop_drive()
    stop_steer()
    GPIO.output(LED_PIN, GPIO.LOW)
    tm1637_display.show("    ")
    tm1637_display2.show("    ")
    oled_device.clear()
    GPIO.cleanup()
    sys.exit()
signal.signal(signal.SIGINT, lambda s,f: cleanup_all())


# --- CAMERA THREAD ---

async def websocket_handler(request):
    global camera_process, camera_active
    global camera_width, camera_height

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    async for msg in ws:
        if msg.type != WSMsgType.TEXT:
            continue

        try:
            data = json.loads(msg.data)
            action = data.get("action")

            if action == "toggle_camera":

                if camera_active:
                    if camera_process:
                        camera_process.terminate()
                        camera_process = None

                    camera_active = False
                    await ws.send_json({"status": "camera_off"})
                else:
                    camera_process = subprocess.Popen([
                        "rpicam-vid",
                        "--width", str(camera_width),
                        "--height", str(camera_height),
                        "--framerate", "30",
                        "--codec", "h264",
                        "--inline",
                        "-t", "0",
                        "--listen",
                        "-o", "tcp://0.0.0.0:8554"
                    ])

                    camera_active = True
                    await asyncio.sleep(1.5)

                    await ws.send_json({
                        "status": "camera_on",
                        "width": camera_width,
                        "height": camera_height
                    })

            elif action == "set_resolution":

                res = data.get("resolution")

                if res == "1280x720":
                    camera_width, camera_height = 1280, 720
                elif res == "854×480":
                    camera_width, camera_height = 854, 480
                else:
                    await ws.send_json({"error": "invalid_resolution"})
                    continue

                if camera_active and camera_process:
                    camera_process.terminate()
                    await asyncio.sleep(1)

                    camera_process = subprocess.Popen([
                        "rpicam-vid",
                        "--width", str(camera_width),
                        "--height", str(camera_height),
                        "--framerate", "30",
                        "--codec", "h264",
                        "--inline",
                        "-t", "0",
                        "--listen",
                        "-o", "tcp://0.0.0.0:8554"
                    ])

                    await asyncio.sleep(1)

                await ws.send_json({
                    "status": "resolution_changed",
                    "width": camera_width,
                    "height": camera_height
                })

            else:
                await ws.send_json({"error": "unknown_action"})

        except Exception as e:
            try:
                await ws.send_json({"error": "internal_error"})
            except:
                pass

    return ws


async def mjpeg_stream(request):
    if not camera_active:
        raise web.HTTPNotFound()

    cmd = [
        "rpicam-vid",
        "--width", str(camera_width),
        "--height", str(camera_height),
        "--framerate", "30",
        "--codec", "mjpeg",
        "--quality", "85",
        "-t", "0",
        "--flush",
        "-o", "-"
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    response = web.StreamResponse()
    response.content_type = 'multipart/x-mixed-replace; boundary=--frame'
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['Connection'] = 'keep-alive'
    response.headers['Access-Control-Allow-Origin'] = '*'

    await response.prepare(request)

    buffer = b''

    try:
        while True:
            chunk = await proc.stdout.read(512*1024)
            if not chunk:
                break

            buffer += chunk

            while True:
                start = buffer.find(b'\xff\xd8')
                end = buffer.find(b'\xff\xd9', start + 2)

                if start == -1 or end == -1:
                    break

                frame = buffer[start:end + 2]
                buffer = buffer[end + 2:]

                await response.write(b'--frame\r\n')
                await response.write(b'Content-Type: image/jpeg\r\n\r\n')
                await response.write(frame)
                await response.write(b'\r\n')

    except Exception as e:
        pass
    finally:
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except:
            pass

    return response

def start_camera_server():
    async def _run():
        app = web.Application()
        app.router.add_get('/ws', websocket_handler)
        app.router.add_get('/stream.mjpg', mjpeg_stream)

        runner = web.AppRunner(app)
        await runner.setup()

        site = web.TCPSite(
            runner,
            host='0.0.0.0',
            port=8443,
            ssl_context=ssl_context
        )

        await site.start()

        while True:
            await asyncio.sleep(3600)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_run())

# --- FLASK ---

@app.route('/')
def index():
    return render_template('index.html',
        music_files=music_files,
        audiobook_files=audiobook_files,
        current_volume=current_volume,
        oled_current_text=oled_current_text,
        tm_current_val=tm_current_val
    )

@app.route('/send_command', methods=['POST'])
def send_command():
    global oled_current_text, oled_backup_text, oled_on, tm_current_val, tm_clock_enabled
    cmd = request.form.get('cmd')
    val = request.form.get('value', '')
    if cmd=='oled_set':
        oled_backup_text = val if val else "BUDAPEST"
        oled_current_text = oled_backup_text
        oled_on = True
        update_oled_display(oled_current_text)
    elif cmd=='tm1637_set':
        tm_clock_enabled = False
        try:
            tm_current_val = int(val)
        except:
            tm_current_val = 505
        tm1637_display_number_right_aligned(tm_current_val)
    else:
        cmd_queue.put((cmd,val))
    return jsonify({"status":"ok","cmd":cmd,"value":val})

@app.route("/camera_stream")
def camera_stream():
    if not camera_running:
        return "Camera off", 404
    return Response(
        generate_mjpeg(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )

@app.route('/camera_toggle', methods=['POST'])
def camera_toggle():
    if camera_running:
        stop_camera()
        return "off"
    else:
        start_camera()
        return "on"

@app.route('/battery_status')
def battery_status():
    return jsonify({
        "voltage": round(battery_voltage, 2) if battery_voltage is not None else None,
        "percent": battery_percent
    })

# --- SOCKETIO MICROPHONE HANDLERS ---

@socketio.on('audio')
def mic_audio_data(data):
    global mic_audio_queue, mic_p, mic_stream, mic_active
    try:
        if mic_p is None or mic_stream is None or not mic_stream.is_active():
            try:
                if mic_stream:
                    mic_stream.stop_stream()
                    mic_stream.close()
                    mic_stream = None
                if mic_p:
                    mic_p.terminate()
                    mic_p = None
            except:
                pass

            mpv_command(["quit"])
            subprocess.run(["pkill", "-f", "mpg123"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            mic_p = pyaudio.PyAudio()
            mic_stream = mic_p.open(
                format=MIC_FORMAT,
                channels=MIC_CHANNELS,
                rate=MIC_RATE,
                output=True,
                output_device_index=0
            )

            if not hasattr(mic_audio_thread, "_running"):
                threading.Thread(target=mic_audio_thread, daemon=True).start()
                mic_audio_thread._running = True

        decoded = base64.b64decode(data)
        audio = np.frombuffer(decoded, dtype=np.int16)
        audio = (audio * mic_volume).astype(np.int16)
        mic_audio_queue.append(audio.tobytes())
        if len(mic_audio_queue) > 10:
            mic_audio_queue.pop(0)

        mic_active = True

    except Exception as e:
        pass

@socketio.on('mic_volume')
def mic_volume_control(data):
    global mic_volume
    vol = float(data) / 100
    mic_volume = max(0.1, min(1.0, vol))

@socketio.on('mic_stop')
def handle_mic_stop():
    global mic_active, mic_p, mic_stream

    if mic_stream is not None:
        mic_stream.stop_stream()
        mic_stream.close()
        mic_stream = None

    if mic_p is not None:
        mic_p.terminate()
        mic_p = None

    mic_active = False

    threading.Thread(target=mpv_event_listener, daemon=True).start()
    start_mpv_daemon()

# --- BUS MODE ---

def bus_mode():
    global bus_sounds_on, shuffle_mode, bus_engine_boot
    mpv_command(["stop"])
    update_oled_display(f"BUS MODE {'ON' if bus_sounds_on else 'OFF'}", duration=3)
    shuffle_mode = False
    if bus_sounds_on == True and bus_engine_boot == False:
        #bus_engine_boot = True
        subprocess.Popen(['mpg123','-q','-o','alsa:dmix','-f','1284', os.path.join(SOUNDS_DIR, "busengine.mp3")])

# --- CONTROLLER ---

def find_gamepad():
    devices = [InputDevice(path) for path in list_devices()]
    for dev in devices:
        if e.EV_KEY in dev.capabilities() or e.EV_ABS in dev.capabilities():
            return dev
    return None

def process_controller(dev):
    global current_speed_percent, current_volume, oled_on, oled_backup_text
    global bus_sounds_on, shuffle_mode, manual_track_playing

    pressed_since = {}
    last_repeat = {}
    LONG_PRESS_TIME = 0.35
    NORTH_LONG_TIME = 1.0

    while True:
        try:
            for event in dev.read_loop():
                now = time.time()

                # =====================================================
                # BUTTON EVENTS
                # =====================================================
                if event.type == e.EV_KEY:
                    key, val = event.code, event.value

                    if key not in last_button_state:
                        last_button_state[key] = 0

                    # ---------------- BUTTON PRESSED ------------------

                    if val == 1 and last_button_state[key] == 0:
                        pressed_since[key] = now
                        last_repeat[key] = None

                        if key == e.BTN_WEST:
                            oled_on = not oled_on
                            if oled_on:
                                update_oled_display(oled_backup_text)
                            else:
                                oled_device.clear()

                        elif key == e.BTN_EAST:
                            tm_clock_enabled = False
                            cmd_queue.put(('tm_toggle', ''))

                        elif key == 319:
                            threading.Thread(target=play_bushorn, daemon=True).start()

                    # ---------------- BUTTON RELEASED -----------------

                    elif val == 0:
                        held_time = now - pressed_since.get(key, now)

                        if key == e.BTN_SOUTH and held_time < LONG_PRESS_TIME:
                            cmd_queue.put(('led_toggle', ''))

                        elif key == e.BTN_NORTH and held_time < NORTH_LONG_TIME:
                            bus_sounds_on = not bus_sounds_on
                            bus_mode()

                        pressed_since.pop(key, None)
                        last_repeat.pop(key, None)

                    last_button_state[key] = val

                # =====================================================
                # ANALOG EVENTS
                # =====================================================
                elif event.type == e.EV_ABS:

                    if event.code == 16 and music_files and event.value != 0:
                        if not bus_sounds_on:
                            manual_track_playing = True
                            mpv_command(["set_property", "pause", False])
                            play_random_music_track()

                    elif event.code == 17 and not bus_sounds_on:
                        if event.value == -1:
                            current_volume = min(100, current_volume + 5)
                        elif event.value == 1:
                            current_volume = max(0, current_volume - 5)
                        else:
                            continue

                        set_mpv_volume(current_volume)
                        update_oled_display(f"VOL {current_volume}%", duration=3)

                    elif event.code == e.ABS_Y:
                        speed_input = -((event.value - 127.5) / 127.5) * 100
                        if abs(speed_input) > 10:
                            set_drive_speed(speed_input)
                        else:
                            stop_drive()

                    elif event.code == e.ABS_RX:
                        steer_val = (event.value - 127.5) / 127.5
                        if steer_val < -0.5:
                            execute_steer_direct_dc('left')
                        elif steer_val > 0.5:
                            execute_steer_direct_dc('right')
                        else:
                            stop_steer()

                # =====================================================
                # LONG PRESS HANDLING (ALL BUTTONS)
                # =====================================================
                for key in list(pressed_since.keys()):
                    held_time = now - pressed_since[key]

                    if key in (e.BTN_TL, e.BTN_TR) and held_time > LONG_PRESS_TIME:
                        if last_repeat.get(key) is None or now - last_repeat[key] > REPEAT_INTERVAL:
                            if key == e.BTN_TR:
                                current_speed_percent = min(100, current_speed_percent + SPEED_STEP)
                            else:
                                current_speed_percent = max(0, current_speed_percent - SPEED_STEP)

                            update_oled_display(f"SPEED {current_speed_percent}%", duration=2)
                            last_repeat[key] = now

                    elif key == e.BTN_NORTH and held_time > NORTH_LONG_TIME:
                        if last_repeat.get(key) != "handled":
                            update_oled_display("SHUFFLE MUSIC", duration=3)
                            bus_sounds_on = False
                            audiobook_playing = False
                            shuffle_mode = True
                            mpv_command(["set_property", "pause", False])
                            play_random_music_track()
                            last_repeat[key] = "handled"

                    elif key == e.BTN_SOUTH and held_time > LONG_PRESS_TIME:
                        if last_repeat.get(key) != "handled":
                            if current_led_effect == rainbow_fade:
                                stop_led_effect()
                                update_oled_display("LED OFF", duration=2)
                            else:
                                start_led_effect(rainbow_fade)
                                update_oled_display("LED RAINBOW", duration=2)

                            last_repeat[key] = "handled"

        except Exception as err:
            time.sleep(1)
            dev = find_gamepad()
            if not dev:
                time.sleep(2)

# --- BUSHORN ---

def play_bushorn():
    global bus_sounds_on
    if bus_sounds_on == False:
        subprocess.run(["pkill", "-f", "mpg123"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    mpv_command(["stop"])
    time.sleep(0.1)
    subprocess.run(['mpg123','-q','-o','alsa:dmix','-f','2184', os.path.join(SOUNDS_DIR, "bushorn.mp3")])
    if manual_track_playing is not None or shuffle_mode is True:
        play_random_music_track()

# --- SLEEP ---

def play_sleep():
    global shuffle_mode, tm_clock_enabled, display_on, tm1637_display, tm1637_display2
    subprocess.run(["pkill", "-f", "mpg123"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shuffle_mode = False
    audiobook_playing = False
    mpv_command(["stop"])
    GPIO.output(LED_PIN, False)
    display_on = False
    stop_led_effect()
    oled_on = False
    oled_device.clear()
    tm_clock_enabled = False
    with tm_lock:
        tm1637_display.show("    ")
        tm1637_display2.show("    ")
    time.sleep(0.1)
    subprocess.Popen(['mpg123','-q','-o','alsa:dmix','-f','1484', os.path.join(SOUNDS_DIR, "sleep.mp3")])

# --- MPV PLAYER ---

def play_random_music_track():
    global current_music_index, oled_on, manual_track_playing

    subprocess.run(["pkill", "-f", "mpg123"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if music_files:
        if not os.path.exists(MPV_SOCKET) or mpv_process is None or mpv_process.poll() is not None:
            start_mpv_daemon()
        current_music_index = random.randint(0, len(music_files)-1)
        track_name = music_files[current_music_index]
        full_path = os.path.join(MUSIC_DIR, track_name)
        mpv_loadfile(full_path)

        if oled_on:
            update_oled_display(f"♪ {track_name}", duration=3)
        else:
            with canvas(oled_device) as draw:
                draw_text(draw, f"♪ {music_files[current_music_index]}")
            threading.Thread(target=lambda: (time.sleep(3), oled_device.clear()), daemon=True).start()

def mpv_event_listener():
    global shuffle_mode, manual_track_playing

    while True:
        if not os.path.exists(MPV_SOCKET):
            time.sleep(1)
            continue

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(MPV_SOCKET)
        except socket.error:
            time.sleep(1)
            continue

        sock_file = sock.makefile('r')
        mpv_command(["enable_event", "end-file"])

        try:
            while True:
                line = sock_file.readline()
                if not line:
                    break

                try:
                    event = json.loads(line)

                    if event.get("event") == "end-file":
                        reason = event.get("reason", "N/A")

                        if reason == "eof":
                            if shuffle_mode:
                                play_random_music_track()
                            else:
                                manual_track_playing = None

                except json.JSONDecodeError:
                    continue

        except Exception as e:
            pass

        finally:
            sock.close()
            time.sleep(1)

# --- WEB COMMANDS ---

def process_queue():
        global led_state, display_on, oled_on, tm_current_val
        global current_speed_percent, current_volume, manual_track_playing, shuffle_mode, tm_clock_enabled, audiobook_playing
        global mpv_process

        while True:
            try:
                cmd, val = cmd_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if cmd == 'led_toggle':
                led_state = not led_state
                GPIO.output(LED_PIN, led_state)

            elif cmd == 'tm_toggle':
                tm_clock_enabled = False
                display_on = not display_on
                if display_on:
                    tm1637_display_number_right_aligned(tm_current_val)
                else:
                    tm1637_display.show("    ")
                    tm1637_display2.show("    ")

            elif cmd == 'tm_off':
                display_on = False
                tm_clock_enabled = False
                tm1637_display.show("    ")
                tm1637_display2.show("    ")

            elif cmd == 'oled_off':
                oled_on = False
                oled_device.clear()

            elif cmd == 'bushorn':
                play_bushorn()

            elif cmd == 'sleep':
                stop_led_effect()
                play_sleep()

            elif cmd == 'play_music':
                start_led_effect(rainbow_fade)
                if val:
                    file_path = os.path.join(MUSIC_DIR, val)
                    if os.path.exists(file_path):
                        subprocess.run(["pkill", "-f", "mpg123"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        if not os.path.exists(MPV_SOCKET) or mpv_process is None or mpv_process.poll() is not None:
                            start_mpv_daemon()
                        shuffle_mode = False
                        manual_track_playing = file_path
                        mpv_loadfile(file_path)
                        mpv_command(["set_property", "pause", False])
                        if oled_on:
                            track_name = os.path.basename(file_path)
                            update_oled_display(f"♪ {track_name}", duration=3)

            elif cmd == 'play_audiobook':
                start_led_effect(rainbow_fade)
                if val:
                    file_path = os.path.join(AUDIOBOOK_DIR, val)
                    if os.path.exists(file_path):
                        subprocess.run(["pkill", "-f", "mpg123"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        if not os.path.exists(MPV_SOCKET) or mpv_process is None or mpv_process.poll() is not None:
                            start_mpv_daemon()
                        shuffle_mode = False
                        audiobook_playing = True
                        manual_track_playing = file_path
                        mpv_loadfile(file_path)
                        mpv_command(["set_property", "pause", False])
                        if oled_on:
                            track_name = os.path.basename(file_path)
                            update_oled_display(f"♪ {track_name}", duration=3)

            elif cmd == 'mpv_play':
                mpv_command(["set_property", "pause", False])

            elif cmd == 'mpv_pause':
                stop_led_effect()
                audiobook_playing = False
                shuffle_mode = False
                mpv_command(["set_property", "pause", True])

            elif cmd == 'mpv_shuffle':
                start_led_effect(rainbow_fade)
                shuffle_mode = True
                audiobook_playing = False
                play_random_music_track()
            elif cmd == 'set_volume':
                set_mpv_volume(val)
                if oled_on:
                    update_oled_display(f"VOL {current_volume}%", duration=3)
            elif cmd == 'speed_up':
                current_speed_percent = min(100, current_speed_percent + SPEED_STEP)
                if oled_on:
                    update_oled_display(f"SPEED {current_speed_percent}%", duration=3)
            elif cmd == 'speed_down':
                current_speed_percent = max(10, current_speed_percent - SPEED_STEP)
                if oled_on:
                    update_oled_display(f"SPEED {current_speed_percent}%", duration=3)

            elif cmd == "tm_clock_on":
                tm_clock_enabled = True
                threading.Thread(target=tm_clock, daemon=True).start()

            elif cmd == "led_effect":
                if val == "rainbow":
                    start_led_effect(rainbow_fade)

                elif val == "breathing":
                    start_led_effect(lambda: breathing(Color(0, 0, 255)))

                elif val == "scanner":
                    start_led_effect(scanner)

                elif val == "sparkle":
                    start_led_effect(sparkle)

                elif val == "meteor":
                    start_led_effect(meteor)

                elif val == "pulse":
                    start_led_effect(rainbow_pulse)

                elif val == "police":
                    start_led_effect(police)

                elif val == "off":
                    stop_led_effect()

# --- START WEB AND PROCESSES ---

if __name__=="__main__":
    threading.Thread(target=battery_monitor_thread, daemon=True).start()
    threading.Thread(target=process_queue, daemon=True).start()
    threading.Thread(target=mpv_event_listener, daemon=True).start()
    threading.Thread(target=start_camera_server,daemon=True).start()

    start_mpv_daemon()

    subprocess.run(['mpg123','-q','-o','alsa:dmix','-f','684', os.path.join(SOUNDS_DIR, "bushorn.mp3")])

    print("The Wheels on the Bus started... 🚌💨")
    print("For Leila and Bernat, the meaning of my life. ❤🌍💖👶👧✨")

    # HTTPS SocketIO
    def run_socketio():
        with open(os.devnull, 'w') as fnull:
            sys.stdout = fnull
            sys.stderr = fnull
            socketio.run(
                app,
                host='0.0.0.0',
                port=443,
                debug=False,
                allow_unsafe_werkzeug=True,
                certfile='ssl/fullchain.pem',
                keyfile='ssl/key.pem'
            )

    threading.Thread(target=run_socketio, daemon=True).start()

    while True:
        dev = find_gamepad()
        if dev:
            process_controller(dev)
        else:
            time.sleep(1)
