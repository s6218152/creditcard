"""Record bank-page structure on failure without credentials or account data."""
from contextlib import contextmanager
from pathlib import Path
import traceback
import re
import os
from urllib.parse import urlparse

from chrome_session import system_chrome_context


def redact_private_text(text: str, secrets=()) -> str:
    for value in sorted({value for value in secrets if value}, key=len, reverse=True):
        text = text.replace(value, "[已隱藏]")
    text = re.sub(r"(?i)\b[A-Z][12]\d{8}\b", "[身分識別碼已隱藏]", text)
    text = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[電子郵件已隱藏]", text)
    text = re.sub(r"(?<!\w)(?:\d[\s-]?){7,16}(?!\w)", "[帳號已隱藏]", text)
    text = re.sub(r"(?i)(?:NT\$|NTD|TWD|新?[臺台]幣)\s*[+-]?[\d,]+(?:\.\d{1,2})?",
                  "[金額已隱藏]", text)
    text = re.sub(r"(?<!\w)[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?!\w)",
                  "[金額已隱藏]", text)
    return text


def collect_private_page_diagnostics(page, is_official):
    """Failure-only visible text for the local private diagnostic file."""
    result = []
    if not is_official(page.url):
        return result
    secrets = [value for key, value in os.environ.items() if value and
               any(word in key.upper() for word in
                   ("PASSWORD", "TOKEN", "SECRET", "_ID", "BIRTHDAY", "EMAIL"))]
    for frame in page.frames:
        if not is_official(frame.url):
            continue
        try:
            # inner_text does not collect form input values or HTML attributes.
            text = frame.locator("body").inner_text(timeout=1500)
            text = redact_private_text(text, secrets)
            result.append({"host": urlparse(frame.url).hostname,
                           "visible_text": text[:8000]})
        except Exception:
            continue
    return result


def has_verification_challenge(page, is_official):
    selectors = ('input[autocomplete="one-time-code"]:visible, '
                 'input[placeholder*="OTP" i]:visible, '
                 'input[placeholder*="動態密碼"]:visible, '
                 'input[placeholder*="簡訊驗證碼"]:visible')
    for frame in page.frames:
        if is_official(page.url) and is_official(frame.url):
            try:
                if frame.locator(selectors).count():
                    return True
            except Exception:
                continue
    return False


def collect_page_diagnostics(page, bank, is_official):
    from bank_captcha import CAPTCHA_IMAGES
    from bank_login import LOGIN_FORMS

    result = {"bank": bank, "frames": [], "verification_input_visible": False}
    if not is_official(page.url):
        return {**result, "official_page": False}
    result["verification_input_visible"] = has_verification_challenge(page, is_official)
    for frame in page.frames:
        if not is_official(frame.url):
            continue
        try:
            item = {"host": urlparse(frame.url).hostname}
            spec = LOGIN_FORMS.get(bank)
            if spec:
                item["login_fields"] = [frame.locator(selector + ":visible").count()
                                        if selector else None for selector in spec.fields]
                item["login_buttons"] = frame.locator(spec.submit + ":visible").count()
                if spec.group:
                    item["group_fields"] = frame.locator(spec.group + ":visible").count()
                if spec.captcha:
                    item["captcha_fields"] = frame.locator(spec.captcha + ":visible").evaluate_all(
                        'es=>es.map(e=>({maxlength:e.getAttribute("maxlength")}))')
            if bank in CAPTCHA_IMAGES:
                spec = CAPTCHA_IMAGES[bank]
                item["captcha_images"] = frame.locator(spec.image + ":visible").evaluate_all(
                    'es=>es.map(e=>({complete:e.complete??null,width:e.naturalWidth??null,height:e.naturalHeight??null}))')
            if bank == "ctbc":
                item["login_fields"] = [frame.locator(f'input[formcontrolname="{name}"]:visible').count()
                                        for name in ("custIxd", "userIxd", "pxd")]
                item["visible_input_count"] = frame.locator("input:visible").count()
                item["credential_validation"] = frame.locator(
                    'input[formcontrolname="custIxd"]:visible, '
                    'input[formcontrolname="userIxd"]:visible, '
                    'input[formcontrolname="pxd"]:visible').evaluate_all(
                    'es=>es.map(e=>({disabled:e.disabled,readonly:e.readOnly,invalid:e.classList.contains("ng-invalid")}))')
                item["login_buttons"] = frame.get_by_role("button", name="登入", exact=True).evaluate_all(
                    'es=>es.map(e=>({tag:e.tagName,disabled:!!e.disabled,aria_disabled:e.getAttribute("aria-disabled"),pointer_events:getComputedStyle(e).pointerEvents,visible:!!e.getClientRects().length}))')
            result["frames"].append(item)
        except Exception:
            result["frames"].append({"state": "page_unavailable"})
    return result


@contextmanager
def diagnostic_chrome_context(playwright, bank, is_official, context_factory=system_chrome_context):
    with context_factory(playwright) as context:
        try:
            yield context
        except Exception as error:
            error.private_diagnostics = [item for page in getattr(context, "pages", [])
                                         if not page.is_closed()
                                         for item in collect_private_page_diagnostics(page, is_official)]
            error.diagnostics = [collect_page_diagnostics(page, bank, is_official)
                                 for page in getattr(context, "pages", []) if not page.is_closed()]
            cause = error.__cause__ or error
            trace = [{"file": Path(item.filename).name, "line": item.lineno, "function": item.name}
                     for item in traceback.extract_tb(cause.__traceback__)
                     if Path(item.filename).name in ("manual_bank_balance.py", "bank_login.py",
                                                     "bank_captcha.py", "ctbc_balance.py")]
            if trace:
                error.diagnostics.append({"failure_trace": trace})
            navigation = getattr(cause, "navigation_state", None)
            if navigation:
                error.diagnostics.append({"navigation": navigation})
            # Extract only fixed failure categories, never Playwright's raw
            # action log (which may contain fill arguments or DOM values).
            message = str(cause).lower()
            reasons = {"intercepts pointer events": "pointer_intercepted",
                       "outside of the viewport": "outside_viewport",
                       "element is not enabled": "disabled",
                       "element is not visible": "not_visible",
                       "element is not stable": "not_stable",
                       "strict mode violation": "multiple_matches",
                       "execution context was destroyed": "navigation_context_replaced",
                       "frame was detached": "frame_detached",
                       "frame has been detached": "frame_detached",
                       "cannot find context": "navigation_context_replaced",
                       "target page, context or browser has been closed": "browser_closed"}
            interactions = [label for phrase, label in reasons.items() if phrase in message]
            if interactions:
                error.diagnostics.append({"interaction_reasons": interactions})
            interceptors = []
            for line in str(cause).splitlines():
                if "intercepts pointer events" not in line:
                    continue
                for tag, attributes in re.findall(r'<([a-z][\w-]*)\b([^>]*)>', line):
                    classes = re.search(r'\bclass="([^"]*)"', attributes)
                    # Only structural class names, never text, IDs, values,
                    # hrefs, or the complete action log.
                    names = [name for name in (classes.group(1).split() if classes else [])
                             if re.fullmatch(r'[a-zA-Z_-]{1,60}', name)]
                    item = {"tag": tag, "classes": names[:8]}
                    if item not in interceptors:
                        interceptors.append(item)
            if interceptors:
                error.diagnostics.append({"click_interceptors": interceptors[:8]})
            raise
