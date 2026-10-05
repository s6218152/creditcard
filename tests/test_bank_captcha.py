from types import SimpleNamespace

import pytest

import bank_captcha
from bank_captcha import decode_numeric_result, recognize_numeric_captcha, try_fill_captcha


def prediction(text, confidence=0.999):
    charset = [""] + list("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
    blank = [1.0] + [0.0] * (len(charset) - 1)
    rows = [blank]
    for char in text:
        row = [0.0] * len(charset)
        row[0] = 1 - confidence
        row[charset.index(char)] = confidence
        rows.extend([row, row, blank])
    return {"text": text, "charset": charset, "probabilities": [[row] for row in rows]}


def test_hncb_uses_independent_ocr_consensus_for_noisy_four_digit_images():
    assert bank_captcha.CAPTCHA_IMAGES["hncb"].consensus_fallback


def test_decoder_preserves_leading_zeroes_and_separate_repeated_digits():
    assert decode_numeric_result(prediction("011002"), 6) == "011002"


@pytest.mark.parametrize("text, confidence", [("12345", 0.999), ("12345A", 0.999), ("123456", 0.8)])
def test_invalid_length_alphabet_or_weak_digits_are_not_submitted(text, confidence):
    assert decode_numeric_result(prediction(text, confidence), 6) is None


def test_disagreeing_models_require_manual_verification(monkeypatch):
    monkeypatch.setattr(bank_captcha, "_model", lambda beta: SimpleNamespace(
        classification=lambda *args, **kwargs: prediction("123456" if beta else "123457")))
    assert bank_captcha._recognize_in_worker(b"image", 6) is None


@pytest.mark.parametrize("strong, accepted", [(True, True), (False, False)])
def test_agreement_requires_strong_character_evidence_from_a_model(monkeypatch, strong, accepted):
    monkeypatch.setattr(bank_captcha, "_model", lambda beta: SimpleNamespace(
        classification=lambda *args, **kwargs: prediction("123456", 0.999 if beta and strong else 0.8)))
    assert bank_captcha._recognize_in_worker(b"image", 6) == ("123456" if accepted else None)


def test_crashed_worker_output_is_not_used(monkeypatch):
    monkeypatch.setattr(bank_captcha.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=-6, stdout=b"123456"))
    assert recognize_numeric_captcha(b"image", 6) is None


@pytest.mark.parametrize("changed", [False, True])
def test_refreshed_challenge_is_never_filled_with_old_code(monkeypatch, changed):
    sources = iter(["data:image/png;base64,aW1hZ2U=",
                    "data:image/png;base64,bmV3" if changed else "data:image/png;base64,aW1hZ2U="])
    images = SimpleNamespace(count=lambda: 1, get_attribute=lambda name: next(sources))
    images.nth = lambda i: images
    frame = SimpleNamespace(url="https://www.chb.com.tw/login", locator=lambda selector: images)
    page = SimpleNamespace(url=frame.url)
    fills = []
    field = SimpleNamespace(get_attribute=lambda name: "6", input_value=lambda: fills[-1] if fills else "",
                            press_sequentially=lambda value, **kwargs: fills.append(value), press=lambda *a, **kw: None)
    monkeypatch.setattr(bank_captcha, "run_captcha_worker", lambda *args: bank_captcha.Recognition("123456", "strong_agreement"))
    assert bank_captcha._fill_once(page, frame, bank_captcha.CAPTCHA_IMAGES["chb"], field,
                                  lambda url: url == frame.url) is (not changed)
    assert fills == ([] if changed else ["123456"])


def test_untrusted_page_never_reads_challenge():
    page = SimpleNamespace(url="https://evil.example/")
    assert not try_fill_captcha(page, None, "chb", None, lambda url: False)


def test_alphanumeric_challenges_keep_case_and_reject_wrong_length():
    assert decode_numeric_result(prediction("01A2"), 4, "alnum") == "01A2"
    assert decode_numeric_result(prediction("01A2"), 4) is None
    assert decode_numeric_result(prediction("01A2"), 5, "alnum") is None


def test_refresh_attempts_are_bounded_and_do_not_submit_login(monkeypatch):
    refreshes = []
    refresh = SimpleNamespace(count=lambda: 1, click=lambda **kw: refreshes.append(True))
    frame = SimpleNamespace(url="https://www.chb.com.tw/login", locator=lambda selector: refresh)
    page = SimpleNamespace(url=frame.url, wait_for_timeout=lambda ms: None)
    field = SimpleNamespace(input_value=lambda: "")
    attempts = []
    monkeypatch.setattr(bank_captcha, "_fill_once", lambda *a: attempts.append(True) or False)
    with pytest.raises(bank_captcha.CaptchaError):
        try_fill_captcha(page, frame, "chb", field, lambda url: True)
    assert len(attempts) == 3
    assert len(refreshes) == 2


@pytest.mark.parametrize("agrees", [False, True])
def test_third_model_consensus_must_be_enabled_and_confirm_exact_text(monkeypatch, agrees):
    monkeypatch.setattr(bank_captcha, "_model", lambda beta: SimpleNamespace(
        classification=lambda *a, **kw: prediction("123456", 0.8)))
    monkeypatch.setattr(bank_captcha, "_tesseract_agrees", lambda image, text: agrees)
    assert bank_captcha._recognize_in_worker(b"image", 6) is None
    assert bank_captcha._recognize_in_worker(b"image", 6, consensus_fallback=True) == ("123456" if agrees else None)


def test_worker_failure_is_reported_without_refreshing_or_filling(monkeypatch):
    calls = []
    page = SimpleNamespace(url="https://www.chb.com.tw/", wait_for_timeout=lambda ms: None)
    frame = SimpleNamespace(url=page.url, locator=lambda s: calls.append(s))
    field = SimpleNamespace(input_value=lambda: "")
    def fail(*a):
        raise bank_captcha.CaptchaError("OCR 程序異常結束", "captcha_ocr_unavailable")
    monkeypatch.setattr(bank_captcha, "_fill_once", fail)
    with pytest.raises(bank_captcha.CaptchaError) as error:
        try_fill_captcha(page, frame, "chb", field, lambda url: True)
    assert error.value.code == "captcha_ocr_unavailable"
    assert calls == []


def test_worker_timeout_has_a_distinct_reason(monkeypatch):
    def timeout(*a, **kw):
        raise bank_captcha.subprocess.TimeoutExpired("worker", 20)
    monkeypatch.setattr(bank_captcha.subprocess, "run", timeout)
    assert bank_captcha.run_captcha_worker(b"image", 6).reason == "worker_timeout"


@pytest.mark.parametrize("confidence, agrees, accepted", [(0.7, True, True), (0.4, True, False), (0.7, False, False)])
def test_independent_ocr_can_confirm_one_supported_complete_candidate(monkeypatch, confidence, agrees, accepted):
    monkeypatch.setattr(bank_captcha, "_model", lambda beta: SimpleNamespace(
        classification=lambda *a, **kw: prediction("654321" if beta else "123456", confidence)))
    monkeypatch.setattr(bank_captcha, "_tesseract_text", lambda image: "123456" if agrees else None)
    assert bank_captcha._recognize_in_worker(b"image", 6) is None
    assert bank_captcha._recognize_in_worker(b"image", 6, consensus_fallback=True) == ("123456" if accepted else None)


@pytest.mark.parametrize("reading, accepted", [("ABCD", True), ("ABCE", False), (None, False)])
@pytest.mark.parametrize("bank", ["skbank", "first_bank"])
def test_alphanumeric_banks_use_exact_independent_case_with_model_corroboration(monkeypatch, reading, accepted, bank):
    monkeypatch.setattr(bank_captcha, "_model", lambda beta: SimpleNamespace(
        classification=lambda *a, **kw: prediction("ABCD" if beta else "abcD", 0.7)))
    monkeypatch.setattr(bank_captcha, "_tesseract_text", lambda image: reading)
    assert bank_captcha._recognize_in_worker(b"image", 4, "alnum") is None
    spec = bank_captcha.CAPTCHA_IMAGES[bank]
    assert bank_captcha._recognize_in_worker(b"image", spec.length, spec.alphabet,
                                             spec.consensus_fallback, spec.all_model_consensus) == (reading if accepted else None)


@pytest.mark.parametrize("alternate, confidence, independent, accepted", [
    ("abCD", 0.7, "ABCD", True),
    ("ABCE", 0.999, "ABCD", False),
    ("ABCD", 0.999, "ABCE", False),
    ("ABCD", 0.999, None, False),
    ("ABCD", 0.4, "ABCD", False),
])
def test_first_bank_requires_all_three_models_even_with_strong_predictions(monkeypatch, alternate, confidence, independent, accepted):
    monkeypatch.setattr(bank_captcha, "_model", lambda beta: SimpleNamespace(
        classification=lambda *a, **kw: prediction(alternate if beta else "ABCD", confidence)))
    monkeypatch.setattr(bank_captcha, "_tesseract_text", lambda image: independent)
    spec = bank_captcha.CAPTCHA_IMAGES["first_bank"]
    result = bank_captcha._worker_result(b"image", spec.length, spec.alphabet,
                                        spec.consensus_fallback, spec.all_model_consensus)
    assert result.text == (independent if accepted else None)
