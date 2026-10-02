"""Kokoro speech must not lose a sentence to one unfamiliar word.

misaki 0.7.4 with unk="" and no fallback leaves an out-of-lexicon word's phonemes None,
then raises TypeError joining them — the whole chunk produced no audio ("offsite",
"Ornith", most names), and the failure was only print()ed, so backend.log said nothing.
"""
import builtins
from types import SimpleNamespace

import pytest

misaki_en = pytest.importorskip("misaki.en")

TEXT = "We met at the offsite with Ornith to plan the quarter."


def _engine():
    from services.audio_llm import AudioLLMService
    eng = AudioLLMService.__new__(AudioLLMService)
    g2p = misaki_en.G2P(unk="")                       # exactly how kokoro_mlx builds it
    eng._model = SimpleNamespace(_phonemizer=SimpleNamespace(_g2p=g2p))
    return eng, g2p


def test_without_a_fallback_the_sentence_is_lost():
    with pytest.raises(TypeError):
        misaki_en.G2P(unk="")(TEXT)


def test_espeak_pronounces_unknown_words():
    pytest.importorskip("espeakng_loader")
    eng, g2p = _engine()
    eng._install_g2p_fallback()
    phonemes, _ = g2p(TEXT)
    assert "ɔɹnɪθ" in phonemes and "kwˈɔɹ" in phonemes         # Ornith said, sentence kept


def test_without_espeak_only_the_unknown_word_is_skipped(monkeypatch):
    real_import = builtins.__import__

    def no_espeak(name, *a, **k):
        if name == "espeakng_loader":
            raise ImportError("not bundled")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_espeak)
    eng, g2p = _engine()
    eng._install_g2p_fallback()
    phonemes, _ = g2p(TEXT)
    assert "ɔɹnɪθ" not in phonemes and "kwˈɔɹ" in phonemes     # rest of the sentence survives


def test_an_existing_fallback_is_left_alone():
    eng, g2p = _engine()
    mine = lambda t: ("x", 1)
    g2p.fallback = mine
    eng._install_g2p_fallback()
    assert g2p.fallback is mine


def test_a_long_install_path_is_reached_through_a_short_link(tmp_path):
    """eSpeak's path buffer is ~160 chars and it exit()s on a bad path — the bundle's data
    path was 192 chars from the build folder (2026-10-02)."""
    import os
    loader = pytest.importorskip("espeakng_loader")
    from services.audio_llm import AudioLLMService
    deep = tmp_path / ("x" * 60) / ("y" * 60) / "espeak-ng-data"
    deep.parent.mkdir(parents=True)
    os.symlink(loader.get_data_path(), deep)
    assert len(str(deep)) > 160
    short = AudioLLMService._espeak_data_path(str(deep))
    assert len(short) < AudioLLMService.ESPEAK_PATH_LIMIT
    assert os.path.isfile(os.path.join(short, "phontab"))


def test_incomplete_espeak_data_is_refused_before_espeak_starts(tmp_path):
    from services.audio_llm import AudioLLMService
    (tmp_path / "espeak-ng-data").mkdir()
    with pytest.raises(RuntimeError, match="incomplete"):
        AudioLLMService._espeak_data_path(str(tmp_path / "espeak-ng-data"))
