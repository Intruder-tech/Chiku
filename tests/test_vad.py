import numpy as np

from chiku.audio.capture import resample
from chiku.audio.vad import (
    EnergyVAD,
    SpeechEnded,
    SpeechStarted,
    SustainedSpeech,
    UtteranceSegmenter,
)

F = np.zeros(512, dtype=np.float32)


def run(seg: UtteranceSegmenter, probs: list[float]) -> list:
    events = []
    for p in probs:
        events += seg.process(F + p, p)
    return events


def test_segmenter_emits_start_then_end_with_preroll() -> None:
    seg = UtteranceSegmenter(threshold=0.5, min_speech_ms=64, min_silence_ms=192, preroll_ms=96)
    events = run(seg, [0.0] * 5 + [0.9] * 10 + [0.0] * 6)
    kinds = [type(e) for e in events]
    assert kinds == [SpeechStarted, SpeechEnded]
    ended = events[1]
    # 3 pre-roll + 10 speech + up to 3 frames of tail, at 512 samples each
    assert 13 * 512 <= len(ended.audio) <= 16 * 512


def test_segmenter_ignores_short_blips() -> None:
    seg = UtteranceSegmenter(threshold=0.5, min_speech_ms=160, min_silence_ms=192)
    assert run(seg, [0.0, 0.9, 0.9, 0.0, 0.0, 0.9, 0.0] * 3) == []


def test_segmenter_hysteresis_keeps_speech_through_dips() -> None:
    seg = UtteranceSegmenter(threshold=0.5, min_speech_ms=64, min_silence_ms=192)
    events = run(seg, [0.9] * 4 + [0.4] * 20 + [0.9] * 2 + [0.0] * 7)
    assert [type(e) for e in events] == [SpeechStarted, SpeechEnded]


def test_segmenter_caps_utterance_length() -> None:
    seg = UtteranceSegmenter(threshold=0.5, min_speech_ms=64, min_silence_ms=500,
                             max_utterance_s=3.0)
    events = run(seg, [0.9] * 200)
    assert any(isinstance(e, SpeechEnded) for e in events)


def test_sustained_speech_requires_duration() -> None:
    s = SustainedSpeech(threshold=0.8, min_ms=128)  # 4 frames
    assert [s.feed(p) for p in [0.9, 0.9, 0.9, 0.9]] == [False, False, False, True]
    s.reset()
    assert not any(s.feed(p) for p in [0.9, 0.1, 0.9, 0.1, 0.9, 0.1])


def test_energy_vad_separates_loud_from_quiet() -> None:
    v = EnergyVAD()
    rng = np.random.default_rng(0)
    for _ in range(50):
        quiet = v((rng.standard_normal(512) * 0.001).astype(np.float32))
    loud = v((rng.standard_normal(512) * 0.2).astype(np.float32))
    assert quiet < 0.2 and loud > 0.8


def test_resample_lengths() -> None:
    x = np.sin(np.linspace(0, 100, 48000)).astype(np.float32)
    assert len(resample(x, 48000, 16000)) == 16000
    assert len(resample(x[:24000], 24000, 16000)) == 16000
    assert resample(x, 16000, 16000) is not None
