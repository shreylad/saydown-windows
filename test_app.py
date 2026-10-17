"""Checks of the Windows parts: the shortcut, settings, history, the pill's placement and the clipboard.

Run:  uv run --no-project --with numpy python -m unittest test_app -v
"""
import datetime, json, os, tempfile, unittest
from unittest.mock import Mock, patch

os.environ["SAYDOWN_DATA"] = tempfile.mkdtemp(prefix="saydown-test-")  # never touch the real settings or history
import saydown as a


def keyboard(**hotkey):
    """A Keyboard without its hook thread, recording what it asks the app to do."""
    board = a.Keyboard.__new__(a.Keyboard)
    board.act, board.on_hotkey_set = Mock(), Mock()
    board.down, board.held, board.mask, board.pending, board.quiet_until = set(), False, False, None, 0.0
    if hotkey:
        a.save_settings(dict(a.DEFAULTS) | {"hotkey": hotkey | {"label": a.describe(hotkey["vk"], hotkey["mods"])}})
    else:
        a.SETTINGS.unlink(missing_ok=True)
    a._cached[:] = [None, None]
    return board


class App(unittest.TestCase):
    def tearDown(self):
        a.SETTINGS.unlink(missing_ok=True)
        a.HISTORY.unlink(missing_ok=True)
        a.capture.update(until=0.0, error="")

    def test_shortcut_labels(self):
        self.assertEqual(a.describe(0xA5, 0), "Right Alt")
        self.assertEqual(a.describe(0x44, a.CTRL | a.ALT), "Ctrl+Alt+D")
        self.assertEqual(a.describe(0x78, 0), "F9")
        self.assertEqual(a.describe(0x20, a.CTRL | a.WIN), "Ctrl+Win+Space")

    def test_default_right_alt_hold_passes_through_and_hides_the_alt_release(self):
        board = keyboard()
        with patch.object(a, "send_keys") as send:
            self.assertFalse(board.key(0xA5, True))  # apps still see Alt go down
            self.assertFalse(board.key(0xA5, True))  # Windows repeats a held key: not a second press
            self.assertTrue(board.key(0xA5, False))  # the release is swallowed and sent again after the mask key
        self.assertEqual([c.args[0] for c in board.act.call_args_list], ["down", "up"])
        keys = [(i.ki.wVk, bool(i.ki.dwFlags & a.KEYUP)) for i in send.call_args.args]
        self.assertEqual(keys, [(a.MASK_KEY, False), (a.MASK_KEY, True), (0xA5, True)])
        self.assertTrue(all(i.ki.dwExtraInfo == a.OURS for i in send.call_args.args))

    def test_other_keys_are_left_alone(self):
        board = keyboard()
        for vk in (0x41, 0xA4, 0xA2, 0x20):
            self.assertFalse(board.key(vk, True))
            self.assertFalse(board.key(vk, False))
        board.act.assert_not_called()

    def test_right_ctrl_needs_no_mask(self):
        board = keyboard(vk=0xA3, mods=0)
        with patch.object(a, "send_keys") as send:
            self.assertFalse(board.key(0xA3, True))
            self.assertFalse(board.key(0xA3, False))
        send.assert_not_called()
        self.assertEqual([c.args[0] for c in board.act.call_args_list], ["down", "up"])

    def test_combination_is_swallowed_and_only_with_its_exact_modifiers(self):
        board = keyboard(vk=0x44, mods=a.CTRL | a.ALT)  # Ctrl+Alt+D
        self.assertFalse(board.key(0x44, True))  # D alone is typed as usual
        self.assertFalse(board.key(0x44, False))
        board.key(0xA2, True)
        board.key(0xA4, True)
        self.assertTrue(board.key(0x44, True))
        self.assertTrue(board.key(0x44, True))  # auto-repeat
        self.assertTrue(board.key(0x44, False))
        self.assertEqual([c.args[0] for c in board.act.call_args_list], ["down", "up"])
        board.key(0xA2, False)
        with patch.object(a, "send_keys") as send:
            self.assertTrue(board.key(0xA4, False))  # Alt alone after a swallowed key would open the menu bar
        self.assertEqual(send.call_args.args[-1].ki.wVk, 0xA4)

    def test_choosing_a_shortcut(self):
        board = keyboard()
        a.capture["until"] = a.time.time() + 10
        self.assertTrue(board.key(0xA3, True))
        self.assertTrue(board.key(0xA3, False))
        self.assertEqual(a.hotkey(), {"vk": 0xA3, "mods": 0, "label": "Right Ctrl"})
        board.on_hotkey_set.assert_called_once()

        a.capture["until"] = a.time.time() + 10
        board.key(0xA2, True)
        board.key(0x5B, True)
        board.key(0x20, True)
        self.assertEqual(a.hotkey()["label"], "Ctrl+Win+Space")

        board.down.clear()
        a.capture["until"] = a.time.time() + 10
        board.key(0x41, True)  # a bare letter is refused
        self.assertIn("Add Ctrl, Alt or Win", a.capture["error"])
        self.assertEqual(a.hotkey()["label"], "Ctrl+Win+Space")

    def test_settings_validation(self):
        with patch.object(a, "microphones", return_value=["USB Mic"]):
            self.assertEqual(a.valid_settings({"idle_alpha": 9})["idle_alpha"], 1.0)
            self.assertEqual(a.valid_settings({"pill_position": "top"})["pill_position"], "top")
            self.assertEqual(a.valid_settings({"microphone": "USB Mic"})["microphone"], "USB Mic")
            for bad in ({"language": "klingon"}, {"pill_position": "middle"}, {"microphone": "no such mic"}):
                with self.assertRaises(ValueError):
                    a.valid_settings(bad)

    def test_dictionary_terms(self):
        self.assertEqual(a.valid_terms([" Siobhan ", ""]), ["Siobhan"])
        for bad in (["two\nlines"], "not a list", [5]):
            with self.assertRaises((ValueError, TypeError)):
                a.valid_terms(bad)

    def test_stats_and_bad_history_lines(self):
        noon = lambda day: datetime.datetime(2026, 10, day, 12).timestamp()
        entries = [{"ts": noon(1), "text": "one two three", "audio_s": 30},
                   {"ts": noon(2), "text": "four five six", "audio_s": 30}]
        self.assertEqual(a.stats(entries, datetime.date(2026, 10, 3)), {"words": 6, "wpm": 6, "streak": 2})
        self.assertEqual(a.stats(entries, datetime.date(2026, 10, 5))["streak"], 0)
        good = {"ts": 1_700_000_000, "text": "Keep this, Reykjavík.", "audio_s": 3.5}
        raw = "\n".join([json.dumps(good), "{unfinished", "[]", json.dumps(good | {"audio_s": -1})]) + "\n"
        a.HISTORY.write_text(raw, encoding="utf-8")
        self.assertEqual(a.read_history(), [good])

    def test_pill_sits_on_the_chosen_edge_of_the_work_area(self):
        area = (0, 0, 1000, 760)  # a 40-pixel taskbar below
        self.assertEqual(a.pill_size("idle", "left"), (12, 44))
        self.assertEqual(a.pill_size("idle", "top"), (44, 12))
        self.assertEqual(a.pill_origin("bottom", area, (44, 12)), (478, 700))
        self.assertEqual(a.pill_origin("top", area, (44, 12)), (478, 48))
        self.assertEqual(a.pill_origin("left", area, (170, 28)), (24, 366))
        self.assertEqual(a.pill_origin("right", area, (170, 28)), (806, 366))

    def test_clipboard_round_trip_keeps_unicode_and_line_breaks(self):
        before = a.get_clipboard()
        try:
            a.copy("1. Café\n2. Reykjavík ✓")
            self.assertEqual(a.get_clipboard(), "1. Café\r\n2. Reykjavík ✓")
        finally:
            if before is not None:
                a.copy(before)

    def test_microphone_failure_reads_the_device_list_again_and_retries(self):
        recorder = a.Recorder()
        with patch.object(a.Recorder, "_open", side_effect=[OSError("element not found"), None]) as open_, \
             patch.object(a, "refresh_audio") as refresh:
            recorder.start("")
        self.assertEqual(open_.call_count, 2)
        refresh.assert_called_once()
        with patch.object(a.Recorder, "_open", side_effect=OSError("unplugged")), patch.object(a, "refresh_audio"), \
             self.assertRaises(OSError):
            recorder.start("")  # still failing after the refresh: the caller shows "No microphone"

    def test_server_only_answers_its_own_page(self):
        handler = a.make_handler(Mock(), Mock(), Mock(), lambda: False).__new__(
            a.make_handler(Mock(), Mock(), Mock(), lambda: False))
        handler.headers = {"Host": a.HOST, "Origin": "https://evil.example"}
        self.assertFalse(handler.allowed())
        handler.headers = {"Host": "evil.example:8737"}
        self.assertFalse(handler.allowed())
        handler.headers = {"Host": a.HOST, "Origin": f"http://{a.HOST}"}
        self.assertTrue(handler.allowed())


if __name__ == "__main__":
    unittest.main()
