"""gpuguard + OCREngine: syncs wait for a quiet GPU; a page asked for by hand does not."""

from viwoods import gpuguard as G
from viwoods.ocr import OCREngine


def card(tmp_path, name, value):
    d = tmp_path / name / "device"
    d.mkdir(parents=True, exist_ok=True)
    (d / "gpu_busy_percent").write_text(f"{value}\n")


def pat(tmp_path):
    return str(tmp_path / "card*/device/gpu_busy_percent")


def test_busy_percent_takes_the_busiest_card_and_ignores_unreadable_ones(tmp_path):
    assert G.busy_percent(pat(tmp_path)) is None
    card(tmp_path, "card0", 3)
    card(tmp_path, "card1", 91)
    assert G.busy_percent(pat(tmp_path)) == 91
    (tmp_path / "card1/device/gpu_busy_percent").write_text("n/a")
    assert G.busy_percent(pat(tmp_path)) == 3


def test_is_busy_uses_the_mean_and_never_blocks_what_it_cannot_measure(tmp_path):
    def quiet(seconds):
        return None

    assert G.is_busy(50, pattern=pat(tmp_path), sleep=quiet) is False       # nothing to read
    card(tmp_path, "card0", 90)
    assert G.is_busy(50, pattern=pat(tmp_path), sleep=quiet) is True
    assert G.is_busy(0, pattern=pat(tmp_path), sleep=quiet) is False        # 0 = off
    card(tmp_path, "card0", 49)
    assert G.is_busy(50, pattern=pat(tmp_path), sleep=quiet) is False


def test_wait_until_quiet_polls_until_the_gpu_drops(tmp_path):
    card(tmp_path, "card0", 95)
    naps, said = [], []

    def nap(seconds):
        naps.append(seconds)
        if len(naps) >= 6:                     # a few readings, then the game closes
            card(tmp_path, "card0", 5)

    waited = G.wait_until_quiet(50, poll=10, announce=said.append, pattern=pat(tmp_path), sleep=nap)
    assert waited > 0 and len(said) == 1 and 10 in naps
    assert G.wait_until_quiet(50, poll=10, announce=said.append, pattern=pat(tmp_path), sleep=lambda s: None) == 0


def test_a_sync_waits_before_a_model_call_but_a_hand_asked_page_does_not(config, monkeypatch):
    waits = []
    monkeypatch.setattr("viwoods.gpuguard.wait_until_quiet", lambda limit, **kw: waits.append(limit) or 0.0)
    config.gpu_busy_limit = 50
    ocr = OCREngine(config)
    ocr._gpu_gate()
    assert waits == []                          # interactive: not held
    ocr.hold_for_gpu = True
    ocr._gpu_gate()
    assert waits == [50]
    config.gpu_busy_limit = 0
    ocr._gpu_gate()
    assert waits == [50]                        # limit 0: off


def test_keep_alive_is_sent_only_when_set(config):
    ocr = OCREngine(config)
    assert "keep_alive" not in ocr._keep_alive({"model": "m"})
    config.ollama_keep_alive = "30s"
    assert ocr._keep_alive({"model": "m"})["keep_alive"] == "30s"
