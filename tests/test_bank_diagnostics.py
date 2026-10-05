import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from bank_diagnostics import collect_page_diagnostics, diagnostic_chrome_context, has_verification_challenge


def test_foreign_frames_are_never_inspected_for_verification():
    def forbidden(*args):
        raise AssertionError("foreign frame must not be read")
    page = SimpleNamespace(url="https://bank.example/", frames=[
        SimpleNamespace(url="https://evil.example/", locator=forbidden)])
    assert not has_verification_challenge(page, lambda url: url.startswith("https://bank.example/"))


def test_visible_image_captcha_is_not_evidence_of_otp():
    def locator(selector):
        return SimpleNamespace(count=lambda: int("captcha" in selector.lower()))
    frame = SimpleNamespace(url="https://bank.example/", locator=locator)
    assert not has_verification_challenge(SimpleNamespace(url=frame.url, frames=[frame]), lambda url: True)


def test_diagnostics_exclude_urls_queries_and_input_values():
    selector = SimpleNamespace(count=lambda: 1, evaluate_all=lambda script: [{"maxlength": "6"}])
    frame = SimpleNamespace(url="https://my.taishinbank.com.tw/?password=private-password",
                            locator=lambda css: selector)
    result = collect_page_diagnostics(SimpleNamespace(url=frame.url, frames=[frame]), "taishin", lambda url: True)
    text = json.dumps(result)
    assert "private-password" not in text and "?password" not in text
    assert result["frames"][0]["login_fields"] == [1, 1, 1]


def test_context_retains_original_error_and_collects_before_closing():
    page = SimpleNamespace(url="https://evil.example/", frames=[], is_closed=lambda: False)
    events = []
    @contextmanager
    def context_factory(playwright):
        try:
            yield SimpleNamespace(pages=[page])
        finally:
            events.append("closed")
    original = ValueError("private-credential: element intercepts pointer events")
    with pytest.raises(ValueError) as caught:
        with diagnostic_chrome_context(None, "ctbc", lambda url: False, context_factory):
            raise original
    assert caught.value is original
    assert original.diagnostics[0]["official_page"] is False
    assert original.diagnostics[-1]["interaction_reasons"] == ["pointer_intercepted"]
    assert "private-credential" not in json.dumps(original.diagnostics)
    assert events == ["closed"]
