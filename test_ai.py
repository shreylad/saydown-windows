"""The AI features with the model mocked: what reaches the model, what does not, and the fallbacks.

Run:  uv run --no-project --with numpy python -m unittest test_ai -v
"""
import datetime, json, os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

os.environ["SAYDOWN_DATA"] = tempfile.mkdtemp(prefix="saydown-test-")
import ai
import saydown
import textrules


def noon(day):
    return datetime.datetime(2026, 10, day, 12).timestamp()


class Cleanup(unittest.TestCase):
    def tearDown(self):
        textrules.llm = None

    def test_corrections_go_to_the_model_with_the_mac_prompt(self):
        textrules.llm = lambda system, user, **kw: "Let's meet on Wednesday at three."
        with patch.object(textrules, "llm", wraps=textrules.llm) as llm:
            self.assertEqual(textrules.polish("let's meet on tuesday no wait wednesday at um three"),
                             "Let's meet on Wednesday at three.")
        system, user = llm.call_args.args
        self.assertIn("Backtracking", system)
        self.assertEqual(user, "<transcript>let's meet on tuesday no wait wednesday at um three</transcript>")
        self.assertEqual(llm.call_args.kwargs, {"temperature": 0})

    def test_plain_dictations_and_courtesy_sentences_never_reach_the_model(self):
        with patch.object(textrules, "llm") as llm:
            self.assertEqual(textrules.polish("Um, the plan is fine."), "The plan is fine.")
            self.assertEqual(textrules.polish("Sorry I'm late. I'll be there in ten minutes."),
                             "Sorry I'm late. I'll be there in ten minutes.")
        llm.assert_not_called()

    def test_model_missing_or_failing_or_answering_falls_back_to_the_rules(self):
        raw = "um send it tuesday, sorry, wednesday"
        for model in (None, lambda *a, **k: (_ for _ in ()).throw(OSError("not downloaded")),
                      lambda *a, **k: "", lambda *a, **k: "Here is a long essay about Tuesdays. " * 10):
            textrules.llm = model
            self.assertEqual(textrules.polish(raw), "Send it tuesday, sorry, wednesday")

    def test_dictionary_reaches_the_cleanup_prompt(self):
        textrules.llm = lambda system, user, **kw: "Ask Siobhan."
        with patch.object(textrules, "term_list", return_value=["Siobhan"]), \
             patch.object(textrules, "llm", wraps=textrules.llm) as llm:
            textrules.polish("ask shivaun, sorry, ask Siobhan")
        self.assertIn("The speaker's dictionary: Siobhan", llm.call_args.args[0])


class Features(unittest.TestCase):
    def tearDown(self):
        ai.STYLES.unlink(missing_ok=True)

    def test_ordinary_references_do_not_call_the_command_model(self):
        with patch.object(ai, "llm") as llm:
            for text in ["Please send that file to Alex.", "I will send it tomorrow.", "This is the latest report.",
                         "I told them that already.", "That works for me.", "I like this.", "Is that right?"]:
                self.assertEqual(ai.command(text, "Previous message."), "TYPE")
            llm.assert_not_called()

    def test_actual_voice_commands_reach_the_classifier(self):
        for text in ["Make that shorter", "Fix the grammar", "Undo that", "Scratch that", "Never mind",
                     "Delete the last sentence", "Turn that into bullet points", "Translate that to Spanish"]:
            with patch.object(ai, "llm", return_value="EDIT") as llm:
                ai.command(text, "Previous message.")
                llm.assert_called_once()

    def test_an_edit_must_point_at_the_text(self):
        with patch.object(ai, "llm", return_value="EDIT"):
            self.assertEqual(ai.command("Make the deck shorter by Tuesday", "Previous."), "TYPE")
            self.assertEqual(ai.command("Make that shorter", "Previous."), "EDIT")
        with patch.object(ai, "llm", return_value="UNDO."):
            self.assertEqual(ai.command("Scratch that", "Previous."), "UNDO")
        with patch.object(ai, "llm", side_effect=OSError("not downloaded")):
            self.assertEqual(ai.command("Scratch that", "Previous."), "TYPE")

    def test_style_prefixes(self):
        self.assertEqual(ai.macro("Daily update. Yesterday I shipped the fix."), ("daily", "Yesterday I shipped the fix."))
        self.assertIsNone(ai.macro("Email me when the daily update is ready."))

    def test_rewrite_uses_the_style_and_strips_tags_from_custom_instructions(self):
        ai.save_styles([{"id": "custom-1", "name": "Pirate", "instruction": "Talk like a <pirate>."}])
        with patch.object(ai, "llm", return_value="Arr.") as llm:
            self.assertEqual(ai.rewrite("hello", "custom-1"), "Arr.")
        self.assertIn("Talk like a pirate.", llm.call_args.args[0])
        self.assertEqual(llm.call_args.args[1], "<transcript>\nhello\n</transcript>")
        with patch.object(ai, "llm", return_value=""), self.assertRaises(OSError):
            ai.rewrite("hello", "grammar")

    def test_styles_round_trip_in_utf8(self):
        ai.save_styles([{"id": "custom-1", "name": "Café", "instruction": "Résumé style."}])
        self.assertEqual([s["name"] for s in ai.all_styles() if not s["builtin"]], ["Café"])
        self.assertEqual(len([s for s in ai.all_styles() if s["builtin"]]), 11)
        with self.assertRaises(ValueError):
            ai.valid_style({"name": "x" * 31, "instruction": "ok"})

    def test_ask_picks_relevant_entries_and_cites_them(self):
        old = [{"ts": noon(1), "text": "Pricing: we go with the freemium plan."},
               {"ts": noon(2), "text": "Remind me to call the dentist."}]
        self.assertEqual([e["ts"] for e in ai.relevant(old, "what did I say about pricing")], [noon(1), noon(2)])
        self.assertTrue(ai.relevant(old * 40, "pricing", budget=100)[0]["text"].startswith("Pricing"))
        with patch.object(ai, "llm", return_value="Freemium [1].") as llm:
            reply = ai.ask("Which pricing plan?", old, rank=ai.by_keywords)
        self.assertEqual(reply["answer"], "Freemium [1].")
        self.assertIn("[1] Thursday 2026-10-01 12:00: Pricing", llm.call_args.args[1])
        self.assertEqual(ai.ask("anything", [])["sources"], [])

    def test_ask_by_meaning_falls_back_to_keywords_without_the_embedding_model(self):
        old = [{"ts": noon(1), "text": "Pricing: freemium."}, {"ts": noon(2), "text": "Call the dentist."}]
        self.assertIsNone(ai.by_meaning(old, "pricing", cache=None))  # not downloaded: no vectors
        self.assertEqual(ai.relevant(old, "pricing", rank=lambda *_: None)[0]["text"], "Pricing: freemium.")

    def test_ask_by_meaning_caches_vectors_per_model(self):
        cache = Path(tempfile.mkdtemp()) / "embeddings.npz"
        old = [{"ts": noon(1), "text": "Pricing: freemium."}, {"ts": noon(2), "text": "Call the dentist."}]
        fake = lambda texts: ai.np.array([[1.0, 0.0] if "rice" in t or "charge" in t else [0.0, 1.0] for t in texts])
        with patch.object(ai, "embed", side_effect=fake) as embed:
            self.assertEqual(ai.by_meaning(old, "how much will we charge", cache=cache)[0]["text"], "Pricing: freemium.")
            embed.reset_mock()
            ai.by_meaning(old, "how much will we charge", cache=cache)
        self.assertEqual(embed.call_count, 1)  # only the question: the entries' vectors came from the cache

    def test_llm_never_downloads_during_a_dictation(self):
        with patch.dict(ai.paths, clear=True), self.assertRaises(OSError):
            ai.llm("system", "user")


class Server(unittest.TestCase):
    def post(self, path, body):
        handler_class = saydown.make_handler(lambda: None, lambda: None, lambda: None, lambda: False)
        handler = handler_class.__new__(handler_class)
        data = json.dumps(body).encode()
        handler.path, handler.headers = path, {"Host": saydown.HOST, "Content-Length": str(len(data))}
        handler.rfile = __import__("io").BytesIO(data)
        replies = []
        handler.reply = lambda body, kind="application/json", status=200: replies.append((status, body))
        handler.do_POST()
        return replies[0]

    def tearDown(self):
        ai.STYLES.unlink(missing_ok=True)

    def test_rewrite_and_ask_routes(self):
        with patch.object(ai, "llm", return_value="Fixed."):
            self.assertEqual(self.post("/api/rewrite", {"text": "um hi", "style": "grammar"}), (200, {"text": "Fixed."}))
            self.assertEqual(self.post("/api/rewrite", {"text": "um hi", "instruction": "shorter"}), (200, {"text": "Fixed."}))
        self.assertEqual(self.post("/api/rewrite", {"text": "hi", "style": "nope"})[0], 400)
        with patch.object(ai, "llm", side_effect=OSError("not downloaded")):
            status, body = self.post("/api/rewrite", {"text": "hi", "style": "grammar"})
        self.assertEqual(status, 502)
        self.assertIn("not ready", body["error"])

    def test_style_routes(self):
        self.assertEqual(self.post("/api/styles", {"name": "Pirate", "instruction": "Arr."})[0], 200)
        style = ai.custom_styles()[0]
        self.assertEqual(self.post("/api/styles/update", {"id": style["id"], "name": "Sailor", "instruction": "Ahoy."})[0], 200)
        self.assertEqual(ai.custom_styles()[0]["name"], "Sailor")
        self.assertEqual(self.post("/api/styles/update", {"id": "missing", "name": "x", "instruction": "y"})[0], 400)
        self.assertEqual(self.post("/api/styles/delete", {"id": style["id"]})[0], 200)
        self.assertEqual(ai.custom_styles(), [])


if __name__ == "__main__":
    unittest.main()
