# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = [
#     "faster-whisper>=1.1", "sounddevice", "numpy", "llama-cpp-python==0.3.27",
#     "nvidia-cublas-cu12", "nvidia-cudnn-cu12>=9,<10", "nvidia-cuda-runtime-cu12",
# ]
# [[tool.uv.index]]  # llama-cpp-python's own index, with builds for NVIDIA graphics cards; PyPI has none
# url = "https://abetlen.github.io/llama-cpp-python/whl/cu125"
# ///
"""Saydown for Windows. Hold right Alt, talk, release: cleaned-up text is pasted at your cursor, in any app.

Run:  uv run saydown.py        (the first run downloads the speech model, about 1.6 GB)
Test: uv run --no-project python -m unittest test_rules test_app
"""
import ctypes, datetime, importlib.util, io, json, math, os, queue, subprocess, sys, threading, time, traceback, uuid
import wave
import webbrowser
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")  # Windows without Developer Mode copies instead; fine
# The xet downloader holds a file in memory until it is complete, so the setup card could not show progress.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
import ai
import textrules
from ai import plain
from textrules import polish, respell, spoken, term_list, terms

# ---- where things live --------------------------------------------------------------------------------------

HERE = Path(__file__).parent  # the code and the page
DATA = Path(os.environ.get("SAYDOWN_DATA") or Path(os.environ.get("APPDATA", Path.home())) / "Saydown")
DATA.mkdir(parents=True, exist_ok=True)
DICTIONARY = textrules.DICTIONARY = DATA / "dictionary.txt"  # names/jargon, one per line
HISTORY = DATA / "history.jsonl"
SETTINGS = DATA / "settings.json"
STYLES = ai.STYLES = DATA / "styles.json"  # rewrite styles you add yourself
EMBEDDINGS = ai.EMBEDDINGS = DATA / "embeddings.npz"  # one vector per saved dictation, so Ask can search by meaning
LOG = DATA / "saydown.log"

# ---- the shortcut -------------------------------------------------------------------------------------------

# A shortcut is a lone modifier key (any one of the eight below), a function key, or a combination such as Ctrl+Alt+D.
CTRL, ALT, SHIFT, WIN = 1, 2, 4, 8
MOD_KEYS = {  # virtual-key code -> (the modifier it is, label)
    0xA0: (SHIFT, "Left Shift"), 0xA1: (SHIFT, "Right Shift"), 0xA2: (CTRL, "Left Ctrl"), 0xA3: (CTRL, "Right Ctrl"),
    0xA4: (ALT, "Left Alt"), 0xA5: (ALT, "Right Alt"), 0x5B: (WIN, "Left Win"), 0x5C: (WIN, "Right Win"),
}
F_KEYS = {0x70 + i: f"F{i + 1}" for i in range(24)}
KEY_NAMES = F_KEYS | {0x20: "Space", 0x0D: "Enter", 0x09: "Tab", 0x08: "Backspace", 0x1B: "Esc", 0x2E: "Delete",
                      0x2D: "Insert", 0x24: "Home", 0x23: "End", 0x21: "Page Up", 0x22: "Page Down",
                      0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down", 0xC0: "`", 0xBD: "-", 0xBB: "=",
                      0xDB: "[", 0xDD: "]", 0xDC: "\\", 0xBA: ";", 0xDE: "'", 0xBC: ",", 0xBE: ".", 0xBF: "/"}
DEFAULT_HOTKEY = {"vk": 0xA5, "mods": 0, "label": "Right Alt"}
ESC = 0x1B
capture = {"until": 0.0, "error": ""}  # set by the settings page while it waits for you to press a new shortcut

# What the setup checklist in the window reports. "loading": the speech model is not in memory yet (the first run
# downloads it). "mic": True, or False when Windows privacy settings block desktop apps from the microphone.
status = {"loading": True, "mic": True, "model": {"step": "checking"}, "device": "", "speech_error": "", "delivery": ""}
LANGUAGES = {
    "auto": "Auto-detect", "en": "English", "hi": "Hindi", "gu": "Gujarati", "es": "Spanish", "fr": "French",
    "de": "German", "pt": "Portuguese", "it": "Italian", "nl": "Dutch", "ar": "Arabic", "bn": "Bengali",
    "mr": "Marathi", "ta": "Tamil", "te": "Telugu", "ur": "Urdu", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
}
TAP = 0.3  # a press shorter than this (seconds) is a tap, not a hold
DOUBLE_TAP = 0.6  # two taps within this many seconds start hands-free recording
QUIET = 0.01  # a recording whose loudest moment is below this is silence; Whisper's own voice filter catches the rest
# (a laptop's built-in microphone array can peak near 0.005 in a quiet room, and speech from it well under 0.1)
RATE = 16000
WHISPER = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"  # what faster-whisper calls "large-v3-turbo"
PORT = 8737  # the settings window talks to this process over localhost
HOST = f"127.0.0.1:{PORT}"
PILL_POSITIONS = ("bottom", "top", "left", "right")
DEFAULTS = {
    "idle_alpha": 0.3,  # how visible the pill is when idle (0.1 = nearly invisible, 1 = solid)
    "pill_position": "bottom",
    "save_history": True,
    "hotkey": DEFAULT_HOTKEY,
    "microphone": "",  # an input device name, or "" for the Windows default
    "language": "auto",
    "voice_commands": False,  # "make that shorter" / "undo that" right after a dictation edits it in place
}

# ---- settings, dictionary, history (as on the Mac) ----------------------------------------------------------

_cached = [None, None]  # settings file mtime, parsed settings


def settings():
    """Current settings. Called on every key event, so the file is re-read only when it changes."""
    try:
        mtime = SETTINGS.stat().st_mtime
        if _cached[0] != mtime:
            _cached[:] = [mtime, DEFAULTS | json.loads(SETTINGS.read_text(encoding="utf-8"))]
        return _cached[1]
    except (OSError, ValueError, TypeError):
        return DEFAULTS


def save_settings(new):
    SETTINGS.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")


def hotkey():
    key = settings()["hotkey"]
    valid = isinstance(key, dict) and all(isinstance(key.get(name), kind) for name, kind in
                                          (("vk", int), ("mods", int), ("label", str)))
    return key if valid else DEFAULT_HOTKEY


def modifier_only(key):
    return key["vk"] in MOD_KEYS and not key["mods"]


def describe(vk, mods):
    """The label for a shortcut: "Right Alt" for a lone modifier, "Ctrl+Alt+D" for a combination."""
    if vk in MOD_KEYS and not mods:
        return MOD_KEYS[vk][1]
    name = KEY_NAMES.get(vk) or (chr(vk) if 0x30 <= vk <= 0x5A else f"Key {vk:#04x}")
    return "".join(f"{label}+" for flag, label in ((CTRL, "Ctrl"), (ALT, "Alt"), (SHIFT, "Shift"), (WIN, "Win"))
                   if mods & flag) + name


def read_history():
    try:
        lines = HISTORY.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return []
    entries = []
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
                continue
            for key in ("ts", "audio_s"):
                value = entry.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError("invalid history number")
            if entry["audio_s"] < 0:
                continue
            datetime.datetime.fromtimestamp(entry["ts"])  # reject dates the statistics UI cannot display
            entries.append(entry)
        except (ValueError, TypeError, OverflowError, OSError):
            continue  # one incomplete/corrupt line must not hide the remaining history; leave the file intact
    return entries


def remember(text, raw, audio_s, **extra):
    if settings()["save_history"] or extra:  # a note exists only in history, so it is always saved
        with HISTORY.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "text": text, "raw": raw, "audio_s": round(audio_s, 1)} | extra)
                    + "\n")


def stats(entries, today=None):
    today = today or datetime.date.today()
    words = sum(len(e["text"].split()) for e in entries)
    minutes = sum(e["audio_s"] for e in entries) / 60
    days = {datetime.date.fromtimestamp(e["ts"]) for e in entries}
    day = today if today in days else today - datetime.timedelta(days=1)  # today not dictated yet: streak holds
    streak = 0
    while day in days:
        streak += 1
        day -= datetime.timedelta(days=1)
    return {"words": words, "wpm": round(words / minutes) if minutes else 0, "streak": streak}


def valid_terms(items):
    if not isinstance(items, list) or len(items) > 500:
        raise ValueError("bad dictionary")
    return [t for t in (plain(item, 80) for item in items) if t]


def valid_settings(body):
    new = dict(settings())
    if "idle_alpha" in body:
        new["idle_alpha"] = min(1.0, max(0.1, float(body["idle_alpha"])))
    for key in ("save_history", "voice_commands"):
        if key in body:
            new[key] = body[key] is True
    for key, choices in (("language", LANGUAGES), ("microphone", ["", *microphones()]),
                         ("pill_position", PILL_POSITIONS)):
        if key in body:
            if not isinstance(body[key], str) or body[key] not in choices:
                raise ValueError(f"bad {key}")
            new[key] = body[key]
    return new


# ---- Windows ------------------------------------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ULONG_PTR = ctypes.c_size_t
OURS = 0x5A7D0  # tags the keys Saydown sends, so its own keyboard hook lets them by


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):  # only here so INPUT has the size Windows expects
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]
    _anonymous_ = ("i",)
    _fields_ = [("type", wintypes.DWORD), ("i", _I)]


HOOKPROC = ctypes.WINFUNCTYPE(wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = wintypes.LPARAM
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowLongPtrW.restype = user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
kernel32.GetModuleHandleW.restype = wintypes.HMODULE  # a 64-bit handle; the default int would cut it in half
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GlobalAlloc.restype = kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalLock.argtypes = kernel32.GlobalUnlock.argtypes = kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
KEYUP, EXTENDED = 2, 1
EXTENDED_KEYS = {0xA3, 0xA5, 0x5B, 0x5C, 0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28}
MASK_KEY = 0xE8  # an unassigned key: sent between Alt down and up so Windows never opens a menu bar
CF_UNICODETEXT, GMEM_MOVEABLE = 13, 2


def key_input(vk, up=False):
    flags = (KEYUP if up else 0) | (EXTENDED if vk in EXTENDED_KEYS else 0)
    return INPUT(type=1, ki=KEYBDINPUT(vk, user32.MapVirtualKeyW(vk, 0), flags, 0, OURS))


def send_keys(*inputs):
    array = (INPUT * len(inputs))(*inputs)
    return user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))


def open_clipboard():
    for _ in range(20):  # another app can hold the clipboard for a moment
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def get_clipboard():
    """The clipboard's text, or None when it holds no text."""
    if not open_clipboard():
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(pointer) if pointer else None
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def copy(text):
    """Put text on the clipboard. Line breaks become \\r\\n, which every Windows app pastes as new lines."""
    data = text.replace("\r\n", "\n").replace("\n", "\r\n")
    buffer = ctypes.create_unicode_buffer(data)
    if not open_clipboard():
        raise OSError("the clipboard is busy")
    try:
        user32.EmptyClipboard()
        memory = kernel32.GlobalAlloc(GMEM_MOVEABLE, ctypes.sizeof(buffer))
        pointer = kernel32.GlobalLock(memory)
        ctypes.memmove(pointer, buffer, ctypes.sizeof(buffer))
        kernel32.GlobalUnlock(memory)
        if not user32.SetClipboardData(CF_UNICODETEXT, memory):
            kernel32.GlobalFree(memory)
            raise OSError("could not set the clipboard")
    finally:
        user32.CloseClipboard()


def paste(text):
    """Type text at the cursor by way of the clipboard, then put back what was on the clipboard before."""
    # ponytail: only text is put back; an image or files on the clipboard are replaced by the dictation
    saved = get_clipboard()
    copy(text)
    ours = user32.GetClipboardSequenceNumber()
    send_keys(key_input(0x11), key_input(0x56), key_input(0x56, up=True), key_input(0x11, up=True))  # Ctrl+V

    def restore():
        if user32.GetClipboardSequenceNumber() != ours:  # something was copied in the meantime; keep that
            return
        if saved is not None:
            copy(saved)

    threading.Timer(0.5, restore).start()  # after the app in front has read the clipboard for the paste


def deliver(text):
    paste(text)
    status["delivery"] = ""
    return True


def microphone_allowed():
    """False when Windows privacy settings stop desktop apps using the microphone."""
    import winreg
    base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
    for path in (base, base + r"\NonPackaged"):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                if winreg.QueryValueEx(key, "Value")[0] == "Deny":
                    return False
        except OSError:
            pass
    return True


def work_area():
    """(left, top, right, bottom) of the main screen, minus the taskbar, in physical pixels."""
    rect = wintypes.RECT()
    user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
    return rect.left, rect.top, rect.right, rect.bottom


def edge():
    for root in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        path = root and Path(root) / "Microsoft/Edge/Application/msedge.exe"
        if path and path.exists():
            return str(path)
    return None


def open_window():
    """The window is the settings page, shown as an app window by Edge, which every Windows 11 PC has."""
    browser = edge()
    if browser:
        subprocess.Popen([browser, f"--app=http://{HOST}/", "--window-size=1000,760"])
    else:
        webbrowser.open(f"http://{HOST}/")


# ---- the microphone -----------------------------------------------------------------------------------------

def input_devices():
    """{name: device index} for every microphone, from WASAPI, which gives full names and no duplicates."""
    import sounddevice as sd
    try:
        wasapi = next(i for i, api in enumerate(sd.query_hostapis()) if "WASAPI" in api["name"])
    except StopIteration:
        wasapi = None
    devices = {}
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0 and (wasapi is None or device["hostapi"] == wasapi):
            devices.setdefault(device["name"], index)
    return devices, wasapi


def refresh_audio():
    """Set up PortAudio again, on the calling thread, which reads Windows' list of audio devices afresh.

    Called once at startup on the thread that records, since WASAPI stops working when the thread that set it up
    ends; and again whenever the microphone will not open, as when a microphone is plugged in after launch."""
    import sounddevice as sd  # sets PortAudio up on import, on whichever thread imports it first
    sd._terminate()
    sd._initialize()
    _mics.clear()


def microphones():
    if "list" not in _mics:  # refreshed when a recording fails to start, which is when a stale list shows
        try:
            _mics["list"] = list(input_devices()[0])
        except Exception:
            _mics["list"] = []
    return _mics["list"]


_mics = {}
LEVEL_GAIN = 8  # how far normal speech pushes the pill's bars; raise it for a quiet microphone


class Recorder:
    """Records 16 kHz mono float32. Each recording opens the microphone and closes it again, so Windows shows the
    microphone in use only while you dictate."""

    def __init__(self):
        self.stream, self.chunks, self.lock, self.fake = None, [], threading.Lock(), None

    def start(self, name):
        self.chunks = []
        if os.environ.get("SAYDOWN_FAKE_MIC"):  # tests: play a WAV file into the recording instead of the microphone
            self.fake = threading.Event()
            threading.Thread(target=self._play, args=(os.environ["SAYDOWN_FAKE_MIC"], self.fake), daemon=True).start()
            return
        try:
            self._open(name)
        except Exception as error:
            print("the microphone could not start; reading Windows' device list again and retrying:", error)
            refresh_audio()
            self._open(name)

    def _open(self, name):
        import sounddevice as sd
        devices, wasapi = input_devices()
        device = devices.get(name)
        if name and device is None:
            print(f"microphone {name!r} not found, using the default")
        if device is None and wasapi is not None:
            device = sd.query_hostapis(wasapi)["default_input_device"]
            device = None if device < 0 else device
        info = sd.query_devices(device, "input")
        label = f"{info['name']} ({sd.query_hostapis(info['hostapi'])['name']})"
        if label != _mics.get("using"):
            print("microphone:", label)
            _mics["using"] = label
        try:  # WASAPI converts the rate and channels itself when asked to
            extra = sd.WasapiSettings(auto_convert=True) if "WASAPI" in label else None
            stream = sd.InputStream(device=device, samplerate=RATE, channels=1, dtype="float32",
                                    extra_settings=extra, callback=self._heard(RATE))
        except Exception as error:  # some drivers refuse: record in the device's own format and convert here
            print("opening the microphone at 16 kHz failed, converting instead:", error)
            rate = int(info["default_samplerate"])
            stream = sd.InputStream(device=device, samplerate=rate, channels=1, dtype="float32",
                                    callback=self._heard(rate))
        try:
            stream.start()
        except Exception:
            stream.close()
            raise
        self.stream = stream

    def _heard(self, rate):
        def callback(data, *_):
            chunk = data[:, 0].copy()
            if rate != RATE:
                chunk = np.interp(np.arange(0, len(chunk), rate / RATE), np.arange(len(chunk)), chunk).astype(np.float32)
            with self.lock:
                self.chunks.append(chunk)
        return callback

    def _play(self, path, stop):
        with wave.open(path, "rb") as f:
            audio = np.frombuffer(f.readframes(f.getnframes()), "<i2").astype(np.float32) / 32768
        step, begun = RATE // 20, time.monotonic()
        for at in range(0, len(audio), step):  # in step with the clock, as a microphone would deliver it
            if stop.wait(max(0.0, begun + (at + step) / RATE - time.monotonic())):
                return
            with self.lock:
                self.chunks.append(audio[at:at + step])

    def stop(self):
        """The recording so far, and the microphone closed."""
        if self.fake:
            self.fake.set()
            self.fake = None
        stream, self.stream = self.stream, None
        if stream:
            try:
                stream.stop()
                stream.close()
            except Exception:
                traceback.print_exc()
        with self.lock:
            audio = np.concatenate(self.chunks) if self.chunks else np.zeros(0, np.float32)
            self.chunks = []
        return audio

    def level(self):
        """How loud the last 50 ms were, from 0 to 1."""
        with self.lock:
            recent = self.chunks[-3:]
        heard = np.concatenate(recent)[-RATE // 20:] if recent else np.zeros(0)
        return min(1.0, float(np.sqrt(np.mean(heard ** 2))) * LEVEL_GAIN) if len(heard) else 0.0


# ---- speech to text -----------------------------------------------------------------------------------------

def gpu_libraries():
    """faster-whisper and llama.cpp need NVIDIA's CUDA libraries; they come as Python packages, so show Windows where."""
    for package in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_runtime"):
        try:
            spec = importlib.util.find_spec(package)
        except ModuleNotFoundError:
            continue
        for folder in (spec.submodule_search_locations or []) if spec else []:
            if (Path(folder) / "bin").is_dir():
                os.add_dll_directory(str(Path(folder) / "bin"))
                os.environ["PATH"] = str(Path(folder) / "bin") + os.pathsep + os.environ["PATH"]


def fetch(repo):
    """Download the speech model from Hugging Face unless all of it is here already, reporting progress."""
    import huggingface_hub as hub
    try:
        total = sum(f.size or 0 for f in hub.model_info(repo, files_metadata=True).siblings)
    except Exception:  # offline: snapshot_download() below uses what is on disk, or says what is missing
        total = 0
    blobs = Path(hub.constants.HF_HUB_CACHE) / ("models--" + repo.replace("/", "--")) / "blobs"
    arrived = threading.Event()

    def watch():  # the files grow in this folder as they arrive
        while not arrived.wait(0.5):
            try:
                status["model"] = {"step": "download", "done": sum(f.stat().st_size for f in blobs.iterdir()),
                                   "total": total}
            except OSError:
                pass

    threading.Thread(target=watch, daemon=True).start()
    try:
        return hub.snapshot_download(repo)
    finally:
        arrived.set()


def load_whisper():
    """The speech model on the NVIDIA GPU when there is one, otherwise on the processor."""
    from faster_whisper import WhisperModel
    folder = fetch(WHISPER)
    status["model"] = {"step": "loading"}
    for device, kind in (("cuda", "float16"), ("cpu", "int8")):
        try:
            model = WhisperModel(folder, device=device, compute_type=kind)
            list(model.transcribe(np.zeros(RATE, np.float32), language="en")[0])  # warm up, and prove it works
            status["device"] = device
            print(f"speech model ready on the {'GPU' if device == 'cuda' else 'CPU'}")
            return model
        except Exception as error:
            print(f"speech model could not run on {device}:", error)
    raise OSError("the speech model could not load")


def transcribe(model, audio, language):
    segments, _ = model.transcribe(audio, language=language, initial_prompt=terms() or None, vad_filter=True,
                                   condition_on_previous_text=False)
    segments = list(segments)
    return "".join(s.text for s in segments).strip(), segments


# ---- the keyboard -------------------------------------------------------------------------------------------

class Keyboard:
    """A low-level keyboard hook that watches for the shortcut in every app. The callback must return quickly or
    Windows drops the hook, so it only decides whether to pass each key on and hands the work to `act`."""

    def __init__(self, act, on_hotkey_set):
        self.act, self.on_hotkey_set = act, on_hotkey_set
        self.down = set()  # modifier keys held right now
        self.held = False  # the shortcut is down
        self.mask = False  # a swallowed combination was pressed with Alt or Win down: hide their release
        self.pending = None  # while choosing a shortcut: a modifier pressed first, maybe the start of a combination
        self.quiet_until = 0.0  # ignore the shortcut briefly after choosing it, while those keys are still down
        self.proc = HOOKPROC(self.hook)  # kept, or ctypes would free the callback Windows is calling
        threading.Thread(target=self.run, daemon=True, name="keyboard").start()

    def run(self):
        hook = user32.SetWindowsHookExW(13, self.proc, kernel32.GetModuleHandleW(None), 0)  # WH_KEYBOARD_LL
        if not hook:
            print("cannot watch the keyboard:", ctypes.get_last_error())
            return
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:  # the hook runs inside this loop
            pass

    def hook(self, code, wparam, lparam):
        try:
            if code == 0:
                event = KBDLLHOOKSTRUCT.from_address(lparam)
                if event.dwExtraInfo != OURS and self.key(event.vkCode, wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)):
                    return 1  # swallowed: the app in front never sees this key
        except Exception:
            traceback.print_exc()
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def mods(self):
        return sum({flag for vk, (flag, _) in MOD_KEYS.items() if vk in self.down})

    def key(self, vk, down):
        """True to swallow the key."""
        was_down = vk in self.down
        if vk in MOD_KEYS:
            (self.down.add if down else self.down.discard)(vk)
        if time.time() < capture["until"]:
            self.choose(vk, down, was_down)
            return True
        key = hotkey()
        if vk in MOD_KEYS and not down and self.mask and MOD_KEYS[vk][0] in (ALT, WIN):
            self.mask = False
            if vk == key["vk"] and modifier_only(key):
                self.act("up")
            send_keys(key_input(MASK_KEY), key_input(MASK_KEY, up=True), key_input(vk, up=True))
            return True  # the release is sent again just after the mask key
        if vk != key["vk"] or time.time() < self.quiet_until:
            return False
        if modifier_only(key):
            if down and not was_down:  # Windows repeats a held key; only the first press counts
                self.act("down")
            elif not down:
                self.act("up")
            if down and MOD_KEYS[vk][0] in (ALT, WIN):
                self.mask = True  # Alt or Win pressed and released alone would open a menu or Start
            return False
        if down and self.mods() == key["mods"]:
            if not self.held:
                self.held = True
                self.act("down")
            self.mask = self.mask or bool(key["mods"] & (ALT | WIN))
            return True
        if not down and self.held:
            self.held = False
            self.act("up")
            return True
        return False

    def choose(self, vk, down, was_down):
        if vk in MOD_KEYS:
            if down and not was_down:
                self.pending = vk
            elif not down and self.pending == vk:  # a modifier pressed and released on its own
                self.set_hotkey(vk, 0)
            return
        if not down:
            return
        mods = self.mods()
        if vk == ESC and not mods:
            capture["until"], self.pending = 0.0, None
        elif vk not in F_KEYS and not mods & (CTRL | ALT | WIN):
            # a bare letter as the shortcut would stop you typing that letter anywhere
            capture.update(until=0.0, error="Add Ctrl, Alt or Win to that key, or use a single modifier or function key.")
            self.pending = None
        else:
            self.set_hotkey(vk, mods)

    def set_hotkey(self, vk, mods):
        save_settings(dict(settings()) | {"hotkey": {"vk": vk, "mods": mods, "label": describe(vk, mods)}})
        capture["until"], self.pending, self.quiet_until = 0.0, None, time.time() + 1
        print("shortcut set to", describe(vk, mods))
        self.on_hotkey_set()

    def is_down(self):
        # ponytail: true while that key is down; enough to catch a release Windows never delivered
        key = hotkey()
        return not modifier_only(key) or bool(user32.GetAsyncKeyState(key["vk"]) & 0x8000)


# ---- the on-screen pill -------------------------------------------------------------------------------------

BARS, BAR, GAP, PAD = 9, 3, 3, 12  # the pill's level bars: how many, their width, the space between, the side margin
_bars = BARS * BAR + (BARS - 1) * GAP + 2 * PAD
PILL_EDGE = 48  # how far the pill sits from the top or bottom of the screen, above the taskbar
PILL_SIDE = 24  # the gap to the screen edge when the pill sits at the left or right
PILL_SIZES = {"idle": (44, 12), "listening": (_bars, 26), "busy": (_bars, 26), "handsfree": (_bars + 18, 26),
              "text": (170, 28)}
KEY_COLOR = "#ff00fe"  # painted where the window should be see-through, around the pill's rounded ends
INK, GREEN, RED = "#141a17", "#73d99e", "#ff5f57"


def pill_size(state, position):
    """(width, height): a pill at the left or right edge stands upright while idle."""
    width, height = PILL_SIZES.get(state, PILL_SIZES["text"])
    return (height, width) if state == "idle" and position in ("left", "right") else (width, height)


def pill_origin(position, area, size, scale=1.0):
    """Top-left corner of the pill on the work area (left, top, right, bottom), all in physical pixels."""
    left, top, right, bottom = area
    width, height = size
    edge, side = round(PILL_EDGE * scale), round(PILL_SIDE * scale)
    x, y = left + (right - left - width) // 2, top + (bottom - top - height) // 2
    return {"bottom": (x, bottom - edge - height), "top": (x, top + edge),
            "left": (left + side, y), "right": (right - side - width, y)}[position]


class Pill:
    """A small always-on-top pill that never takes the keyboard focus from the app you are typing in.

    Any thread may call pill(state); state is "", "listening", "handsfree", "busy" or a short message."""

    def __init__(self, on_click, on_right_click):
        import tkinter as tk
        import tkinter.font as tkfont
        self.root = root = tk.Tk()
        root.withdraw()
        root.title("Saydown")
        try:
            root.iconbitmap(default=str(HERE / "assets" / "saydown.ico"))
        except tk.TclError:
            pass
        root.overrideredirect(True)
        root.configure(bg=KEY_COLOR)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", KEY_COLOR)
        self.canvas = tk.Canvas(root, bg=KEY_COLOR, highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonRelease-1>", lambda _: on_click())
        self.canvas.bind("<ButtonRelease-3>", lambda _: on_right_click())
        self.scale = root.winfo_fpixels("1i") / 96
        self.font = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        self.requests, self.state, self.levels, self.frame = queue.Queue(), "busy", [0.0] * BARS, 0
        self.level = lambda: 0.0
        root.update_idletasks()
        root.deiconify()
        self.hwnd = hwnd = int(root.wm_frame(), 16)  # Tk's outer window; winfo_id() is a child inside it
        style = user32.GetWindowLongPtrW(hwnd, -20)  # GWL_EXSTYLE
        # WS_EX_NOACTIVATE: clicks never move the focus; WS_EX_TOOLWINDOW: no taskbar button or Alt+Tab entry
        user32.SetWindowLongPtrW(hwnd, -20, style | 0x08000000 | 0x80 | 0x8)
        self.layout()
        root.after(33, self.tick)

    def __call__(self, state):
        self.requests.put(state)

    def refresh(self):  # settings changed: the pill's place or visibility
        self.requests.put(None)

    def kind(self):
        return self.state if self.state in ("listening", "handsfree", "busy") else "text" if self.state else "idle"

    def layout(self):
        position = settings()["pill_position"]
        width, height = (round(v * self.scale) for v in pill_size(self.kind(), position))
        if self.kind() == "text":
            width = max(width, self.font.measure(self.state) + round(28 * self.scale))
        x, y = pill_origin(position, work_area(), (width, height), self.scale)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.attributes("-alpha", settings()["idle_alpha"] if self.kind() == "idle" else 1.0)
        self.root.attributes("-topmost", True)
        self.size = width, height
        self.draw()

    def draw(self):
        c, (width, height), s = self.canvas, self.size, self.scale
        c.delete("all")
        radius = min(width, height) / 2
        fill = INK if self.kind() != "idle" else "#3a443e"
        c.create_oval(0, 0, 2 * radius, 2 * radius, fill=fill, outline=fill)
        c.create_oval(width - 2 * radius - 1, height - 2 * radius - 1, width - 1, height - 1, fill=fill, outline=fill)
        if width > height:
            c.create_rectangle(radius, 0, width - radius, height - 1, fill=fill, outline=fill)
        else:
            c.create_rectangle(0, radius, width - 1, height - radius, fill=fill, outline=fill)
        kind = self.kind()
        if kind in ("listening", "handsfree"):
            left = PAD * s
            for i, level in enumerate(self.levels):
                bar = max(2 * s, level * (height - 8 * s))
                x = left + i * (BAR + GAP) * s
                c.create_rectangle(x, (height - bar) / 2, x + BAR * s, (height + bar) / 2, fill="white", outline="")
            if kind == "handsfree":  # a red dot: recording continues until you click or tap again
                x = width - 16 * s
                c.create_oval(x, height / 2 - 4 * s, x + 8 * s, height / 2 + 4 * s, fill=RED, outline="")
        elif kind == "busy":  # three dots taking turns: writing
            for i in range(3):
                lit = (self.frame // 6) % 3 == i
                x, r = width / 2 + (i - 1) * 10 * s, 2.5 * s
                c.create_oval(x - r, height / 2 - r, x + r, height / 2 + r, fill=GREEN if lit else "#5c6b62", outline="")
        elif kind == "text":
            c.create_text(width / 2, height / 2, text=self.state, fill="white", font=self.font)

    def tick(self):
        try:
            changed = False
            while not self.requests.empty():
                state = self.requests.get_nowait()
                if state is not None:
                    self.state = state
                changed = True
            self.frame += 1
            if self.kind() in ("listening", "handsfree"):
                self.levels = self.levels[1:] + [self.level()]
            if changed:
                self.layout()
            elif self.kind() != "idle" and self.kind() != "text":
                self.draw()
            if self.frame % 60 == 0:  # windows opened later can cover it otherwise
                self.root.attributes("-topmost", True)
        except Exception:
            traceback.print_exc()
        self.root.after(33, self.tick)

    def run(self):
        self.root.mainloop()


# ---- settings page server -----------------------------------------------------------------------------------

class StartupPending(ValueError):
    pass


def make_handler(on_settings_change, on_quit, on_note, taking_note, recording=lambda: False):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            if os.environ.get("SAYDOWN_DEBUG"):
                print("http:", *args)

        def reply(self, body, kind="application/json", status=200):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(data)
            except ConnectionError:  # the page went away mid-reply (closed, reloaded); nothing to do
                pass

        def allowed(self):
            # This server can read your dictations and edit your files, so only its own page may talk to it.
            # The Host check stops DNS rebinding; the Origin check stops other websites posting to it.
            return self.headers.get("Host") == HOST and self.headers.get("Origin") in (None, f"http://{HOST}")

        def do_GET(self):
            if not self.allowed():
                return self.reply({"error": "forbidden"}, status=403)
            if self.path == "/":
                return self.reply((HERE / "ui.html").read_bytes(), "text/html; charset=utf-8")
            if self.path in ("/favicon.ico", "/icon.png"):  # Edge shows it on the window and its taskbar button
                name, kind = ("saydown.ico", "image/x-icon") if self.path.endswith(".ico") else ("saydown.png", "image/png")
                return self.reply((HERE / "assets" / name).read_bytes(), kind)
            if self.path == "/api/state":
                entries = read_history()
                return self.reply({"history": entries[-300:][::-1], "stats": stats(entries),
                                   "dictionary": term_list(), "settings": settings() | {"hotkey": hotkey()},
                                   "setup": status | {"ai": ai.status}, "microphones": microphones(),
                                   "languages": LANGUAGES, "styles": ai.all_styles(),
                                   "note": taking_note(), "recording": recording(),
                                   "capturing": time.time() < capture["until"],
                                   "capture_error": capture["error"]})
            self.reply({"error": "not found"}, status=404)

        def do_POST(self):
            if not self.allowed():
                return self.reply({"error": "forbidden"}, status=403)
            try:
                size = int(self.headers.get("Content-Length", 0))
                if not 0 <= size <= 1_000_000:
                    raise ValueError("too large")
                body = json.loads(self.rfile.read(size) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("bad request")
                if self.path == "/api/dictionary":
                    DICTIONARY.write_text("".join(t + "\n" for t in valid_terms(body["terms"])), encoding="utf-8")
                elif self.path == "/api/settings":
                    save_settings(valid_settings(body))
                    on_settings_change()
                elif self.path == "/api/quit":
                    threading.Timer(0.3, on_quit).start()  # after this reply has gone out
                elif self.path == "/api/open-settings":  # Settings > Privacy > Microphone
                    os.startfile("ms-settings:privacy-microphone")
                elif self.path == "/api/hotkey/capture":  # the next key or combination pressed becomes the shortcut
                    capture.update(until=time.time() + 10, error="")
                elif self.path in ("/api/rewrite", "/api/ask"):
                    if self.path == "/api/ask":
                        question = plain(body["question"], 500)
                        if not question:
                            raise ValueError("empty question")
                        answer = lambda: ai.ask(question, read_history())
                    else:
                        text = body["text"]
                        # ponytail: about 30 minutes of speech fits the model's context; summarise in parts past that
                        if not isinstance(text, str) or not text.strip() or len(text) > 40000:
                            raise ValueError("bad text")
                        if "instruction" in body:  # free text instead of a saved style
                            instruction = plain(body["instruction"], 300)
                            if not instruction:
                                raise ValueError("empty instruction")
                            answer = lambda: {"text": ai.edit(text, instruction)}
                        elif body["style"] in [s["id"] for s in ai.all_styles()]:
                            answer = lambda: {"text": ai.rewrite(text, body["style"])}
                        else:
                            raise ValueError("unknown style")
                    try:
                        return self.reply(answer())
                    except OSError as e:
                        print(self.path, "failed:", e)
                        return self.reply({"error": "The AI model is not ready yet. Its download shows in this window."},
                                          status=502)
                elif self.path == "/api/styles":  # add a custom style
                    styles = ai.custom_styles()
                    if len(styles) >= 50:
                        raise ValueError("too many styles")
                    ai.save_styles(styles + [{"id": "custom-" + uuid.uuid4().hex[:8]} | ai.valid_style(body)])
                elif self.path == "/api/styles/update":  # built-in styles are not in this list, so cannot be edited
                    styles = ai.custom_styles()
                    next(s for s in styles if s["id"] == body["id"]).update(ai.valid_style(body))
                    ai.save_styles(styles)
                elif self.path == "/api/styles/delete":
                    ai.save_styles([s for s in ai.custom_styles() if s["id"] != body["id"]])
                elif self.path == "/api/note":  # start, or stop and save, a recording that is kept instead of typed
                    on_note()
                elif self.path == "/api/history/clear":
                    HISTORY.unlink(missing_ok=True)
                    EMBEDDINGS.unlink(missing_ok=True)
                elif self.path == "/api/copy":
                    if not isinstance(body["text"], str):
                        raise ValueError("bad text")
                    copy(body["text"])
                else:
                    return self.reply({"error": "not found"}, status=404)
            except StartupPending as error:
                return self.reply({"error": str(error)}, status=503)
            except (ValueError, TypeError, KeyError, AttributeError, OSError, StopIteration):
                return self.reply({"error": "bad request"}, status=400)
            self.reply({"ok": True})

    return Handler


# ---- the app ------------------------------------------------------------------------------------------------

class Tee(io.TextIOBase):
    """Print to the console, when there is one, and to saydown.log in the data folder."""

    def __init__(self, console):
        self.console, self.file = console, LOG.open("a", encoding="utf-8", buffering=1)

    def write(self, text):
        if self.console:
            try:
                self.console.write(text)
                self.console.flush()
            except (OSError, ValueError):
                pass
        self.file.write(text)
        return len(text)


def main():
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # a Hindi dictation must not crash the console
    sys.stdout = sys.stderr = Tee(sys.__stdout__)
    print(f"\n--- Saydown starting {datetime.datetime.now():%Y-%m-%d %H:%M} ---")
    try:  # sharp text and true pixel sizes on scaled displays; must happen before any window exists
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass

    recording, note = [False], [False]  # the settings server reads these immediately at launch
    actions = {}  # filled in as the pill comes up

    def later(name):  # HTTP callbacks can arrive before the pill is ready
        if name not in actions:
            raise StartupPending("Saydown is still starting. Please try again in a moment.")
        return actions[name]()

    try:  # the port doubles as the single-instance lock
        server = ThreadingHTTPServer(("127.0.0.1", PORT), make_handler(
            lambda: actions.get("refresh", lambda: None)(), lambda: os._exit(0), lambda: later("note"),
            lambda: recording[0] and note[0], lambda: recording[0]))
    except OSError:
        print("Saydown is already running; opening its window")
        open_window()
        return
    controls, jobs = queue.Queue(), queue.Queue()  # what the keys and the pill ask for; recordings to transcribe

    def control_loop():  # one long-lived thread does everything the keys and the pill ask, in order
        while True:
            action = controls.get()
            try:
                action()
            except Exception:
                traceback.print_exc()

    threading.Thread(target=control_loop, daemon=True, name="controls").start()
    # Windows audio (WASAPI) belongs to the thread that sets it up and stops working when that thread ends. Set it up
    # on the thread that opens the microphone, before the server's short-lived request threads can touch it first.
    audio_ready = threading.Event()
    controls.put(lambda: (refresh_audio(), audio_ready.set()))
    audio_ready.wait(10)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    gpu_libraries()
    textrules.llm = ai.llm  # self-corrections go to the text model once it is downloaded
    speech_ready = threading.Event()
    threading.Thread(target=ai.setup, args=(speech_ready,), daemon=True, name="ai-setup").start()

    recorder = Recorder()
    lock = threading.Lock()
    hands_free = [False]  # True when recording runs until the next click or tap, with no key held
    pressed_at, last_tap = [0.0], [0.0]
    last_paste = [None]  # {"text", "at", "before"}: what a voice command would change; "before" is the text it replaced
    pill = Pill(lambda: controls.put(toggle), open_window)
    pill.level = recorder.level
    actions["refresh"] = pill.refresh
    pill("busy")
    status["mic"] = microphone_allowed()

    def start(by_click, as_note=False):
        if status["loading"] or status["mic"] is False or status["speech_error"]:
            pill("Loading speech" if status["loading"] else "Speech unavailable" if status["speech_error"]
                 else "Allow microphone")
            return
        with lock:
            if not recording[0]:
                hands_free[0], note[0] = by_click, as_note
                recording[0] = True
                pill("handsfree" if by_click else "listening")
                try:
                    recorder.start(settings()["microphone"])
                    status["mic_error"] = ""
                except Exception as error:
                    recording[0] = False
                    print("the microphone could not start:", error)
                    status["mic_error"] = "Windows would not open the microphone. Check it in Settings > System > Sound."
                    pill("No microphone")
                    return
                print("recording...")

    def stop():
        with lock:
            if recording[0]:
                recording[0] = False
                audio = recorder.stop()
                pill("busy")
                jobs.put((audio, note[0]))

    def toggle():
        stop() if recording[0] else start(by_click=True)

    actions["note"] = lambda: controls.put(lambda: stop() if recording[0] else start(by_click=True, as_note=True))

    def key_down():
        pressed_at[0] = time.time()
        if hands_free[0] and recording[0]:
            stop()  # one more tap ends a hands-free recording
        else:
            start(by_click=False)

    def key_up():
        now = time.time()
        if hands_free[0]:
            return  # released during or right after a hands-free recording: nothing to do
        if now - pressed_at[0] < TAP and now - last_tap[0] < DOUBLE_TAP and recording[0]:
            with lock:  # second quick tap: keep recording with no key held
                hands_free[0] = True
                pill("handsfree")
        else:
            if now - pressed_at[0] < TAP:
                last_tap[0] = now
            stop()  # a tap records too little to transcribe, so it is discarded

    keyboard = Keyboard(lambda what: controls.put(key_down if what == "down" else key_up), pill.refresh)

    def dictate(model, audio, as_note):
        if len(audio) < RATE * TAP:  # accidental tap; Whisper invents text from silence
            print(f"skipped: only {len(audio) / RATE:.1f}s recorded")
            return
        peak = float(np.abs(audio).max())
        if peak < QUIET:
            print(f"skipped: too quiet to be speech (peak level {peak:.3f})")
            return
        print(f"transcribing {len(audio) / RATE:.1f}s (peak level {peak:.2f})...")
        begun = time.time()
        language = settings()["language"]
        raw, segments = transcribe(model, audio, None if language == "auto" else language)
        heard = time.time() - begun
        invented = len(raw.split()) > 6 * len(audio) / RATE + 3  # nobody speaks six words a second
        silent = segments and all(seg.no_speech_prob > 0.6 for seg in segments)
        if not spoken(raw) or invented or silent:
            print("skipped: no speech heard")
            return
        if as_note:  # ponytail: one voice, no speaker labels; needs a diarisation model to tell people apart
            print(f"[{time.time() - begun:.1f}s] note saved: {len(raw.split())} words")
            return remember(respell(raw), raw, len(audio) / RATE, kind="note")
        took = lambda: f"[{time.time() - begun:.1f}s, speech {heard:.1f}s]"
        last = last_paste[0]
        # only a short remark soon after a dictation can be a command, so normal dictations pay nothing for this
        if settings()["voice_commands"] and last and time.time() - last["at"] < 120 and len(raw.split()) <= 12:
            kind = ai.command(raw, last["text"])
            if kind != "TYPE":
                new = last["before"] if kind == "UNDO" else ai.edit(last["text"], raw)  # before any key is sent
                # ponytail: Ctrl+Z assumes nothing was typed since the paste; select the text instead if that bites
                send_keys(key_input(0x11), key_input(0x5A), key_input(0x5A, up=True), key_input(0x11, up=True))
                print(f"{took()} voice command {kind}: {raw}")
                if new is None:  # undoing a plain dictation: nothing to put back
                    last_paste[0] = None
                    return
                time.sleep(0.1)
                paste(new + " ")
                last_paste[0] = {"text": new, "at": time.time(), "before": None if kind == "UNDO" else last["text"]}
                if kind == "EDIT":
                    remember(new, last["text"], len(audio) / RATE)
                return
        text, styled = None, settings()["voice_commands"] and ai.macro(raw)
        if styled:
            try:
                text = respell(ai.rewrite(styled[1], styled[0]))
            except OSError as e:
                print("style failed, typing it as usual:", e)
        text = text or polish(raw)
        if not text:
            print("skipped: nothing but filler sounds")
            return
        deliver(text + " ")  # trailing space so back-to-back dictations don't run together
        last_paste[0] = {"text": text, "at": time.time(), "before": None}
        print(f"{took()} raw:   {raw}\n        clean: {text}")
        remember(text, raw, len(audio) / RATE)

    def worker():
        print("loading the speech model...")
        try:
            model = load_whisper()
            status["model"] = {"step": "ready"}
        except Exception as error:
            model = None
            status["speech_error"] = "The speech model could not load. Check your connection and restart Saydown."
            status["model"] = {"step": "failed", "error": str(error)}
            print(status["speech_error"], error)
        status["loading"] = False
        speech_ready.set()

        def idle():  # what the pill says when nothing is happening
            if status["mic"] is False:
                return "Allow microphone"
            if status["speech_error"]:
                return "Speech unavailable"
            if status.get("mic_error"):  # until the next recording opens it; clicking the pill tries again
                return "No microphone"
            return ""

        shown, recheck = idle(), 0.0
        pill(shown)
        print(f"ready: hold {hotkey()['label']}, double-tap it, or click the pill to dictate")
        print("       right-click the pill for history, dictionary and settings; Ctrl+C here to quit")
        while True:
            try:
                job = jobs.get(timeout=0.2)
            except queue.Empty:
                if recording[0] and not hands_free[0] and not keyboard.is_down() and time.time() - pressed_at[0] > 1:
                    print("key release was missed, stopping the recording")
                    controls.put(stop)
                if time.time() > recheck:  # what was changed in Windows privacy settings since the last look
                    recheck = time.time() + 2
                    status["mic"] = microphone_allowed()
                if not recording[0] and idle() != shown:
                    shown = idle()
                    pill(shown)
                continue
            try:
                dictate(model, *job)
            except Exception:
                status["delivery"] = "Dictation could not complete. The details are in saydown.log."
                traceback.print_exc()
            if jobs.empty() and not recording[0]:
                shown = idle()
                pill(shown)

    threading.Thread(target=worker, daemon=True, name="worker").start()
    if not os.environ.get("SAYDOWN_NO_WINDOW"):
        open_window()  # the window opens with the app; right-click the pill to bring it back

    def wake():  # lets Ctrl+C in the console reach Python while Tk waits for events
        pill.root.after(200, wake)

    wake()
    pill.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
