"""Unit tests for the adversarial-example defense (TP2 étape 3)."""

from tp1_digital_social_score.defense import correct_unknown_words, defend_raw_text

VOCAB = {"nice": 2, "toxic": 3, "stupid": 4, "hello": 5}


def test_defend_raw_text_strips_zero_width_space() -> None:
    assert defend_raw_text("ni​ce") == "nice"


def test_defend_raw_text_strips_joiners_and_bom() -> None:
    assert defend_raw_text("to‌xic‍﻿") == "toxic"


def test_defend_raw_text_leaves_normal_text_untouched() -> None:
    assert defend_raw_text("hello there") == "hello there"


def test_correct_unknown_words_fixes_a_homoglyph_substitution() -> None:
    # 'о' below is U+043E CYRILLIC SMALL LETTER O, not the Latin 'o'.
    assert correct_unknown_words("nіce", VOCAB) == "nice"


def test_correct_unknown_words_fixes_leetspeak() -> None:
    assert correct_unknown_words("stup1d", VOCAB) == "stupid"


def test_correct_unknown_words_fixes_an_adjacent_transposition() -> None:
    assert correct_unknown_words("niec", VOCAB) == "nice"


def test_correct_unknown_words_leaves_two_edits_away_untouched() -> None:
    assert correct_unknown_words("icne", VOCAB) == "icne"  # out of scope: two edits, not one


def test_correct_unknown_words_fixes_a_doubled_letter() -> None:
    assert correct_unknown_words("niice", VOCAB) == "nice"


def test_correct_unknown_words_leaves_known_words_untouched() -> None:
    assert correct_unknown_words("hello nice toxic", VOCAB) == "hello nice toxic"


def test_correct_unknown_words_leaves_short_or_long_unknown_tokens_untouched() -> None:
    assert correct_unknown_words("abc", VOCAB) == "abc"
    assert correct_unknown_words("a" * 30, VOCAB) == "a" * 30


def test_correct_unknown_words_leaves_genuinely_unknown_words_untouched() -> None:
    assert correct_unknown_words("zzzzzzzz", VOCAB) == "zzzzzzzz"
