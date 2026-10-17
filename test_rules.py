"""The Mac version's text-rule regression tests, run against the Windows port.

Run:  uv run --no-project python -m unittest test_rules -v
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import textrules as a


class TextRules(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        patcher = patch.object(a, "DICTIONARY", Path(self.folder.name) / "dictionary.txt")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.folder.cleanup)

    def test_short_dictations_from_the_mac_self_test(self):
        self.assertEqual(a.polish("Thanks, see you tomorrow."), "Thanks, see you tomorrow.")
        self.assertEqual(a.tidy("Um, so I I think, uh, the the the plan is fine."), "So I think, the plan is fine.")
        self.assertEqual(a.tidy("That is very very good, hmm."), "That is very very good.")
        self.assertEqual(a.tidy("Summer is warm."), "Summer is warm.")
        self.assertEqual(a.tidy("uh"), "")
        self.assertEqual(a.tidy("Uh."), "")
        self.assertEqual(a.tidy("Fine. Um. Hello there, erm, Hummer fans."), "Fine. Hello there, Hummer fans.")

    def test_dictionary_respelling_and_fuzzy_matching(self):
        a.DICTIONARY.write_text("Siobhan\nx ai -> xAI\nReykjavík\n", encoding="utf-8")
        self.assertEqual(a.terms(), "Siobhan, xAI, Reykjavík")
        self.assertEqual(a.respell("I work at X AI, not at Maxai."), "I work at xAI, not at Maxai.")
        self.assertEqual(a.fuzzy("Ask Siobhann's team, not the Siberian one."), "Ask Siobhan's team, not the Siberian one.")

    def test_complete_courtesy_sentences_skip_model_but_keep_text(self):
        samples = ["Sorry I am late. Please send the report before the meeting.",
                   "Sorry, I'm late. I'll be there in ten minutes.",
                   "Sorry for the delay. Thanks for waiting.",
                   "Thanks for waiting. Sorry I'm late."]
        with patch.object(a, "clean") as clean:
            for text in samples:
                self.assertEqual(a.polish(text), text)
            clean.assert_not_called()

    def test_corrections_go_through_clean(self):
        samples = ["Send it Tuesday, sorry, Wednesday.",
                   "Sorry I am late. Send it Tuesday, no wait, Wednesday.",
                   "Sorry, please cancel the flight.",
                   "I think we should, actually scratch that, send the invoice.",
                   "Sorry I'm late, no wait, I mean early.",
                   "Sorry I am late"]
        for text in samples:
            with patch.object(a, "clean", return_value="Corrected text.") as clean:
                self.assertEqual(a.polish(text), "Corrected text.")
                clean.assert_called_once_with(text)

    def test_corrections_without_a_model_keep_every_word(self):
        self.assertEqual(a.polish("um send it tuesday, sorry, wednesday"), "Send it tuesday, sorry, wednesday")

    def test_user_agenda_becomes_four_lines_with_introduction(self):
        introduction = "Thank you so much for reaching me out. I am writing to see that like how is everything going."
        raw = (introduction + " First our agenda is doing apple tree, second agenda is doing green tree, "
               "second third agenda is cleaning the grass and fourth agenda is making a barn house.")
        expected = (introduction + "\n\n1. Our agenda is doing apple tree\n2. Agenda is doing green tree\n"
                    "3. Agenda is cleaning the grass\n4. Agenda is making a barn house.")
        self.assertEqual(a.polish(raw), expected)

    def test_spoken_lists_with_punctuation_or_without_it(self):
        samples = {
            "First, review the report. Second, send the invoice.": "1. Review the report.\n2. Send the invoice.",
            "first buy apples second buy pears third buy milk": "1. Buy apples\n2. Buy pears\n3. Buy milk",
            "First: apples; and second: pears.": "1. Apples\n2. Pears.",
            "1st review the report, 2nd send it, third archive it.": "1. Review the report\n2. Send it\n3. Archive it.",
            "FIRST review this, SECOND send that.": "1. Review this\n2. Send that.",
        }
        for raw, expected in samples.items():
            with self.subTest(raw=raw):
                self.assertEqual(a.polish(raw), expected)

    def test_normal_ordinals_and_incomplete_lists_are_unchanged(self):
        samples = ["This is my first time here and I am having second thoughts.",
                   "First review the first draft. Send the second version tomorrow.",
                   "Our first year went well and our second year was better.",
                   "First place and second place receive prizes.",
                   "First review the report.", "Second review the report. Third send it.",
                   "First review it, third send it.", "First review it, second send it, second archive it.",
                   "First, second, third.", "First review it, second.",
                   "First review it, second send it, fourth archive it."]
        for raw in samples:
            with self.subTest(raw=raw):
                self.assertEqual(a.polish(raw), raw)

    def test_ordinals_used_as_adjectives_or_idioms_stay_prose(self):
        samples = ["At first I was unsure, but on second look it was fine.",
                   "The first quarter was strong and the second quarter was weak.",
                   "My first job was in Austin. My second job was in Dallas.",
                   "Our first customer paid late, and our second customer never paid.",
                   "He finished first, she finished second, and I finished third.",
                   "First we eat, then we talk. Second helpings are allowed.",
                   "First thing tomorrow, call Sam. Second thing, email Bob."]
        for raw in samples:
            with self.subTest(raw=raw):
                self.assertEqual(a.polish(raw), raw)
        self.assertEqual(a.polish("First of all, thank you. Second of all, I am sorry."),
                         "1. Thank you.\n2. I am sorry.")

    def test_tidy_keeps_real_words_that_look_like_fillers(self):
        samples = {"I was in the ER.": "I was in the ER.",
                   "Mm-hmm, I agree.": "Mm-hmm, I agree.",
                   'He said "um" twice.': 'He said "um" twice.',
                   "Call 5 5 5 1 2 3 4 now.": "Call 5 5 5 1 2 3 4 now.",
                   "Um, I think the the the plan is fine.": "I think the plan is fine."}
        for raw, expected in samples.items():
            with self.subTest(raw=raw):
                self.assertEqual(a.tidy(raw), expected)

    def test_punctuation_only_transcripts_are_not_speech(self):
        for raw in ("!", ".", "...", "?!", "", " - "):
            self.assertFalse(a.spoken(raw), raw)
        for raw in ("Hi.", "5", "é"):
            self.assertTrue(a.spoken(raw), raw)

    def test_ordinals_inside_items_are_preserved(self):
        raw = "First review the first draft on the second floor. Second send the second version."
        self.assertEqual(a.polish(raw), "1. Review the first draft on the second floor.\n2. Send the second version.")

    def test_self_correction_is_cleaned_before_list_formatting(self):
        raw = "First send it Tuesday, I mean Wednesday. Second archive it."
        with patch.object(a, "clean", return_value="First send it Wednesday. Second archive it.") as clean:
            self.assertEqual(a.polish(raw), "1. Send it Wednesday.\n2. Archive it.")
        clean.assert_called_once_with(raw)

    def test_list_formatting_preserves_dictionary_spellings(self):
        with patch.object(a, "term_list", return_value=["apple tree -> AppleTree"]):
            self.assertEqual(a.polish("First work on apple tree, second plant pears."),
                             "1. Work on AppleTree\n2. Plant pears.")

    def test_all_ten_ordinals_and_repeated_formatting(self):
        words = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth")
        raw = ", ".join(f"{word} do task {i}" for i, word in enumerate(words, 1))
        expected = "\n".join(f"{i}. Do task {i}" for i in range(1, 11))
        self.assertEqual(a.polish(raw), expected)
        self.assertEqual(a.spoken_list(expected), expected)

    def test_user_unnumbered_agenda_becomes_four_bullets(self):
        raw = ("Here is our agenda for the day. Cooking home, working on the app, "
               "freely running on the loan and working crazy on the computer.")
        expected = ("Here is our agenda for the day.\n\n- Cooking home\n- Working on the app\n"
                    "- Freely running on the loan\n- Working crazy on the computer.")
        self.assertEqual(a.polish(raw), expected)

    def test_bullet_introductions_and_inline_lists(self):
        samples = {
            "Here are my tasks: writing code, testing it, and shipping it.":
                "Here are my tasks:\n\n- Writing code\n- Testing it\n- Shipping it.",
            "Our agenda includes cooking at home, working on the app and running outside.":
                "Our agenda includes\n\n- Cooking at home\n- Working on the app\n- Running outside.",
            "My goals for today are cooking at home and working on the app.":
                "My goals for today are\n\n- Cooking at home\n- Working on the app.",
            "Shopping list: apples, bread, and milk.":
                "Shopping list:\n\n- Apples\n- Bread\n- Milk.",
            "Today's priorities: testing the app; fixing the errors; shipping it.":
                "Today's priorities:\n\n- Testing the app\n- Fixing the errors\n- Shipping it.",
        }
        for raw, expected in samples.items():
            with self.subTest(raw=raw):
                self.assertEqual(a.polish(raw), expected)

    def test_bullets_keep_compound_phrases_and_protected_commas(self):
        samples = {
            "Our agenda: research and development, testing the app and reviewing the budget.":
                "Our agenda:\n\n- Research and development\n- Testing the app\n- Reviewing the budget.",
            'My tasks: reading "Research, Development", writing the report.':
                'My tasks:\n\n- Reading "Research, Development"\n- Writing the report.',
            "My tasks: checking costs (food, transport), paying 1,200 dollars, and filing receipts.":
                "My tasks:\n\n- Checking costs (food, transport)\n- Paying 1,200 dollars\n- Filing receipts.",
            "My tasks: studying cooking and cleaning techniques, testing the app.":
                "My tasks:\n\n- Studying cooking and cleaning techniques\n- Testing the app.",
        }
        for raw, expected in samples.items():
            with self.subTest(raw=raw):
                self.assertEqual(a.polish(raw), expected)

    def test_bullets_keep_greetings_and_closing_prose(self):
        raw = "Hello team. Here is our agenda. Cooking at home, working on the app. Thanks for your help."
        self.assertEqual(a.polish(raw),
                         "Hello team. Here is our agenda.\n\n- Cooking at home\n- Working on the app.\n\nThanks for your help.")

    def test_ordinary_comma_prose_and_single_tasks_stay_unchanged(self):
        samples = ["I enjoy cooking at home, working on the app, and running outside.",
                   "We discussed the agenda yesterday, worked on the app, and went home.",
                   "Our agenda was useful, interesting, and practical.",
                   "Here is our agenda. Working on the app.",
                   "The agenda is ready, but we have not agreed on the tasks.",
                   "The agenda is new, exciting, and useful.",
                   "My tasks: cooking at home,, working on the app.",
                   'My tasks: reading "Cooking, writing the report.']
        for raw in samples:
            with self.subTest(raw=raw):
                self.assertEqual(a.polish(raw), raw)

    def test_numbered_agenda_keeps_numbered_format(self):
        self.assertEqual(a.polish("Our agenda: first cook dinner, second work on the app."),
                         "Our agenda:\n\n1. Cook dinner\n2. Work on the app.")

    def test_bullet_formatting_preserves_existing_lists(self):
        for text in ["Our agenda:\n\n- Cooking, cleaning\n- Working on the app.",
                     "Our agenda:\n\n1. Cooking, cleaning\n2. Working on the app."]:
            self.assertEqual(a.spoken_bullets(text), text)

    def test_bullets_format_after_corrections_and_dictionary(self):
        with patch.object(a, "clean", return_value="My tasks: working on say down, cooking at home."), \
             patch.object(a, "term_list", return_value=["say down -> Saydown"]):
            self.assertEqual(a.polish("My tasks: working on say down, cooking, I mean cooking at home."),
                             "My tasks:\n\n- Working on Saydown\n- Cooking at home.")

    def test_user_repeated_intent_agenda_without_intro_punctuation(self):
        raw = ("This is the agenda of the day I am going to cook, I am going to drive my car, "
               "I am going to clean the grasses and I am going to make the pesticides to clean "
               "the bad insects from the garden")
        expected = ("This is the agenda of the day\n\n- I am going to cook\n- I am going to drive my car\n"
                    "- I am going to clean the grasses\n- I am going to make the pesticides to clean "
                    "the bad insects from the garden")
        self.assertEqual(a.polish(raw), expected)

    def test_intent_lists_handle_punctuation_contractions_and_multiple_ands(self):
        samples = {
            "This is our agenda. I am going to cook, I am going to drive and I am going to clean.":
                "This is our agenda.\n\n- I am going to cook\n- I am going to drive\n- I am going to clean.",
            "This is our agenda I'm going to cook and I'm going to drive and I'm going to clean.":
                "This is our agenda\n\n- I'm going to cook\n- I'm going to drive\n- I'm going to clean.",
            "Here are our tasks we will cook we will drive we will clean":
                "Here are our tasks\n\n- We will cook\n- We will drive\n- We will clean",
            "My tasks for today are I need to cook, I need to drive and I need to clean.":
                "My tasks for today are\n\n- I need to cook\n- I need to drive\n- I need to clean.",
            "That's my agenda I’m going to cook, I’m going to drive.":
                "That's my agenda\n\n- I’m going to cook\n- I’m going to drive.",
            "This is the agenda of the day cooking at home, working on the app.":
                "This is the agenda of the day\n\n- Cooking at home\n- Working on the app.",
        }
        for raw, expected in samples.items():
            with self.subTest(raw=raw):
                self.assertEqual(a.polish(raw), expected)

    def test_repeated_action_sentences_are_bullets_and_keep_closing_prose(self):
        samples = {
            "This is our agenda. I will cook. I will drive. I will clean. Thanks for your help.":
                "This is our agenda.\n\n- I will cook.\n- I will drive.\n- I will clean.\n\nThanks for your help.",
            "Our agenda: Cooking at home. Working on the app. Thank you.":
                "Our agenda:\n\n- Cooking at home.\n- Working on the app.\n\nThank you.",
        }
        for raw, expected in samples.items():
            with self.subTest(raw=raw):
                self.assertEqual(a.polish(raw), expected)

    def test_repeated_intent_preserves_compounds_subordinate_clauses_and_quotes(self):
        samples = {
            "My tasks: I will cook fish and chips, I will drive and I will clean.":
                "My tasks:\n\n- I will cook fish and chips\n- I will drive\n- I will clean.",
            "Our agenda: I will decide whether I will drive, I will cook.":
                "Our agenda:\n\n- I will decide whether I will drive\n- I will cook.",
            'Our agenda: I will read "I will cook, I will drive", I will clean.':
                'Our agenda:\n\n- I will read "I will cook, I will drive"\n- I will clean.',
            "My tasks: I will cook (if I will be home), I will work on the app.":
                "My tasks:\n\n- I will cook (if I will be home)\n- I will work on the app.",
        }
        for raw, expected in samples.items():
            with self.subTest(raw=raw):
                self.assertEqual(a.polish(raw), expected)

    def test_intent_prose_and_single_action_are_not_lists(self):
        samples = ["I am going to cook, I am going to drive my car and I am going to clean the garden.",
                   "This is the agenda of the day I am going to cook.",
                   "This is the agenda of the day I will decide whether I will drive.",
                   "This is the agenda of the day we discussed yesterday.",
                   "We changed the agenda because I will cook, I will drive, and I will clean."]
        for raw in samples:
            with self.subTest(raw=raw):
                self.assertEqual(a.polish(raw), raw)

    def test_broader_list_wording_and_imperatives(self):
        samples = {
            "Here are the things I need to do: cook dinner, drive my car and clean the garden.":
                "Here are the things I need to do:\n\n- Cook dinner\n- Drive my car\n- Clean the garden.",
            "Here's what I need to do buy apples, cook dinner and review the report.":
                "Here's what I need to do\n\n- Buy apples\n- Cook dinner\n- Review the report.",
            "I have three things to do: cook dinner, drive my car and clean the garden.":
                "I have three things to do:\n\n- Cook dinner\n- Drive my car\n- Clean the garden.",
            "For today, my plans are cooking dinner, driving my car and cleaning the garden.":
                "For today, my plans are\n\n- Cooking dinner\n- Driving my car\n- Cleaning the garden.",
            "My to-do list: cook dinner and drive my car and clean the garden.":
                "My to-do list:\n\n- Cook dinner\n- Drive my car\n- Clean the garden.",
            "Shopping list: apples, milk and bread.":
                "Shopping list:\n\n- Apples\n- Milk\n- Bread.",
            "Shopping list: eggs, salt and pepper.":
                "Shopping list:\n\n- Eggs\n- Salt and pepper.",
        }
        for raw, expected in samples.items():
            with self.subTest(raw=raw):
                self.assertEqual(a.polish(raw), expected)

    def test_abbreviations_and_protected_punctuation_in_lists(self):
        samples = {
            'My tasks: call Dr. Lee, read "Draft. Final, approved", and clean the garden. Thanks.':
                'My tasks:\n\n- Call Dr. Lee\n- Read "Draft. Final, approved"\n- Clean the garden.\n\nThanks.',
            'My tasks: I will read "Cook, drive" and I will clean the garden.':
                'My tasks:\n\n- I will read "Cook, drive"\n- I will clean the garden.',
            "Our agenda: checking costs (food, transport), paying 1,200 dollars and reviewing the budget.":
                "Our agenda:\n\n- Checking costs (food, transport)\n- Paying 1,200 dollars\n- Reviewing the budget.",
        }
        for raw, expected in samples.items():
            with self.subTest(raw=raw):
                self.assertEqual(a.polish(raw), expected)

    def test_formatted_lists_and_paragraphs_survive_polish(self):
        for text in ["Our agenda:\n\n- Cooking, cleaning\n- Working on the app.",
                     "Our agenda:\n\n1. Cooking, cleaning\n2. Working on the app.",
                     "Hello team.\n\nHere is the report."]:
            self.assertEqual(a.polish(text), text)

    def test_quoted_ordinals_and_unbalanced_lists_are_not_reformatted(self):
        self.assertEqual(a.polish('My tasks: read "First cook dinner, second drive home", review the report.'),
                         'My tasks:\n\n- Read "First cook dinner, second drive home"\n- Review the report.')
        for text in ['She said "first cook dinner, second drive home" to me.',
                     'My tasks: buy food (milk, bread], clean the garden.',
                     '"My tasks: cook dinner, clean the garden."']:
            self.assertEqual(a.polish(text), text)

    def test_transcribed_comma_introductions_are_recognized(self):
        samples = {
            "I have three things to do, cook dinner, drive my car and clean the garden.":
                "I have three things to do,\n\n- Cook dinner\n- Drive my car\n- Clean the garden.",
            "My tasks, I will cook fish and chips, I will drive and I will clean.":
                "My tasks,\n\n- I will cook fish and chips\n- I will drive\n- I will clean.",
            "My tasks, call Dr. Lee, review the report and clean the garden.":
                "My tasks,\n\n- Call Dr. Lee\n- Review the report\n- Clean the garden.",
        }
        for raw, expected in samples.items():
            self.assertEqual(a.polish(raw), expected)


if __name__ == "__main__":
    unittest.main()
