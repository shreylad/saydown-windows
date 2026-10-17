"""End to end, on this PC: start Saydown, press its shortcut in a real window, check what it pastes.

The microphone is replaced by the WAV files make_test_audio.ps1 writes, so nobody needs to speak; everything else is
real: the keyboard hook, the pill, Whisper on the graphics card, the text rules, the clipboard and Ctrl+V.
It moves the mouse to click the pill once, so leave the keyboard and mouse alone for the minute it takes.

Run:  powershell -File make_test_audio.ps1
      uv run --no-project python test_e2e.py
"""
import ctypes, json, os, shutil, subprocess, sys, tempfile, time, tkinter as tk, urllib.request, wave
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).parent
AUDIO = HERE / "test-audio"
STATE = "http://127.0.0.1:8737/api/state"
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetForegroundWindow.restype = wintypes.HWND
ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]
    _anonymous_ = ("i",)
    _fields_ = [("type", wintypes.DWORD), ("i", _I)]


def key(vk, up=False):  # as a person's keyboard would: not tagged as Saydown's own keys
    extended = 1 if vk in (0xA5, 0xA3) else 0
    event = INPUT(type=1, ki=KEYBDINPUT(vk, user32.MapVirtualKeyW(vk, 0), (2 if up else 0) | extended, 0, 0))
    user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))


def click(x, y):
    user32.SetCursorPos(x, y)
    for flag in (2, 4):  # MOUSEEVENTF_LEFTDOWN, LEFTUP
        event = INPUT(type=0, mi=MOUSEINPUT(0, 0, 0, flag, 0, 0))
        user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))
        time.sleep(0.05)


def state():
    with urllib.request.urlopen(STATE, timeout=2) as reply:
        return json.load(reply)


def seconds(name):
    with wave.open(str(AUDIO / f"{name}.wav")) as f:
        return f.getnframes() / f.getframerate()


def pill_window():
    """The pill: a small visible window titled Saydown that is not this test's."""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        title = ctypes.create_unicode_buffer(64)
        user32.GetWindowTextW(hwnd, title, 64)
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if title.value == "Saydown" and user32.IsWindowVisible(hwnd) and rect.right - rect.left < 400:
            found.append(rect)
        return True

    user32.EnumWindows(each, 0)
    return found[0] if found else None


CASES = [  # (recording, how to dictate it, what the pasted text must contain, what it must not)
    ("plain", "hold", ["launch moves to Thursday", "review the report"], []),
    ("filler", "hold", ["I think", "plan is fine"], ["Um", "uh"]),
    ("list", "hold", ["1. Review the report", "2. Send the invoice", "3. Book the flights"], []),
    ("bullets", "handsfree", ["- Writing code", "- Testing it", "- Shipping it"], []),
    ("plain", "pill", ["launch moves to Thursday"], []),
    ("correction", "hold", ["Wednesday at"], ["Tuesday", "no wait"]),  # the text model resolves it
    ("scratch", "undo", [], []),  # a voice command: takes back the dictation before it
]


def main():
    missing = [name for name, *_ in CASES if not (AUDIO / f"{name}.wav").exists()]
    if missing:
        sys.exit(f"missing test audio {missing}: run  powershell -File make_test_audio.ps1  first")
    data = Path(tempfile.mkdtemp(prefix="saydown-e2e-"))
    mic = data / "mic.wav"
    shutil.copy(AUDIO / "plain.wav", mic)
    log = (data / "console.log").open("w", encoding="utf-8")
    app = subprocess.Popen(["uv", "run", "saydown.py"], cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
                           env=os.environ | {"SAYDOWN_DATA": str(data), "SAYDOWN_FAKE_MIC": str(mic),
                                             "SAYDOWN_NO_WINDOW": "1", "PYTHONUNBUFFERED": "1"})
    print("started Saydown; waiting for the speech model (the first run downloads 1.6 GB)...")
    deadline = time.time() + 1200
    while True:
        try:
            setup = state()["setup"]
            if not setup["loading"]:
                break
        except OSError:
            pass
        if app.poll() is not None or time.time() > deadline:
            log.close()
            sys.exit("Saydown did not start:\n" + (data / "console.log").read_text(encoding="utf-8"))
        time.sleep(1)
    print(f"ready: speech model on {setup['device']}, error: {setup['speech_error'] or 'none'}")
    print("waiting for the AI model (the first run downloads 5.5 GB)...")
    while state()["setup"]["ai"]["step"] not in ("ready", "failed") and app.poll() is None and time.time() < deadline:
        time.sleep(1)
    print("AI model:", state()["setup"]["ai"])
    urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8737/api/settings", b'{"voice_commands": true}',
                                                  {"Content-Type": "application/json"}), timeout=5)

    root = tk.Tk()
    root.title("Saydown test target")
    root.geometry("640x360+80+80")
    menu = tk.Menu(root)  # a menu bar: a stray Alt press would move the focus into it and break the paste
    menu.add_cascade(label="File", menu=tk.Menu(menu, tearoff=0))
    root.config(menu=menu)
    box = tk.Text(root, font=("Segoe UI", 11), wrap="word", undo=True)  # Ctrl+Z works, as in a real editor
    box.pack(fill="both", expand=True)
    results, ours = [], []

    def text():
        return box.get("1.0", "end-1c")

    def focus():
        root.deiconify()
        root.lift()
        root.attributes("-topmost", True)
        root.after(200, lambda: root.attributes("-topmost", False))
        root.focus_force()
        box.focus_set()

    def steps():
        focus()
        yield 800
        ours.append(user32.GetForegroundWindow())
        before = "the clipboard before dictating ✓"
        root.clipboard_clear()
        root.clipboard_append(before)
        root.update()
        starts = []  # the text before each case, so an undo can be checked against the one before it
        for name, how, wanted, unwanted in CASES:
            shutil.copy(AUDIO / f"{name}.wav", mic)
            start_text = text()
            starts.append(start_text)
            if how in ("hold", "undo"):
                key(0xA5)
                yield int(seconds(name) * 1000) + 500
                key(0xA5, up=True)
            elif how == "handsfree":
                key(0xA5)
                key(0xA5, up=True)
                yield 120
                key(0xA5)
                key(0xA5, up=True)
                yield int(seconds(name) * 1000) + 500
                if not state()["recording"]:
                    results.append((name, how, False, "hands-free recording was not running"))
                    continue
                key(0xA5)
                key(0xA5, up=True)
            else:  # click the pill, and the focus must stay in this window
                rect = pill_window()
                if not rect:
                    results.append((name, how, False, "pill window not found"))
                    continue
                click((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
                yield 400
                if user32.GetForegroundWindow() != ours[0]:
                    results.append((name, how, False, "clicking the pill took the focus"))
                    continue
                yield int(seconds(name) * 1000) + 300
                rect = pill_window()
                click((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
            waited = time.time()
            while text() == start_text and time.time() - waited < 30:
                yield 200
            took = time.time() - waited
            if how == "undo":
                yield 500  # Saydown presses Ctrl+Z; nothing should be pasted after it
                ok = text() == starts[-2]
                results.append((name, how, ok, f"{took:.1f}s after release: text is back to {text()[-40:]!r}"))
            else:
                pasted = text()[len(start_text):]
                ok = bool(pasted) and all(w in pasted for w in wanted) and not any(u in pasted for u in unwanted)
                results.append((name, how, ok, f"{took:.1f}s after release: {pasted!r}"))
            yield 1200  # Saydown puts the clipboard back 0.5 s after pasting
        clipboard = root.clipboard_get()
        results.append(("clipboard", "restore", clipboard == before, repr(clipboard)))
        root.destroy()

    sequence = steps()

    def advance():
        try:
            root.after(next(sequence), advance)
        except StopIteration:
            pass
        except tk.TclError:
            pass

    root.after(500, advance)
    root.mainloop()

    history = state()["history"]
    try:
        urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8737/api/quit", b"{}", method="POST"), timeout=2)
    except OSError:
        pass
    app.wait(10)
    log.close()
    print("\nresults")
    for name, how, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:9} {how:9} {detail}")
    print(f"\n{len(history)} dictations saved in history")
    print("\nSaydown's console:\n" + (data / "console.log").read_text(encoding="utf-8"))
    sys.exit(0 if results and all(ok for *_, ok, _ in results) else 1)


if __name__ == "__main__":
    main()
