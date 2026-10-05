"""Recognize verified bank image challenges locally, without saving images."""

import base64
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import json
import re
import shutil
import subprocess
import sys

from playwright.sync_api import Error as PlaywrightError


# Only enable forms whose image, length and alphabet have been inspected.
@dataclass(frozen=True)
class CaptchaSpec:
    image: str
    length: int
    refresh: str
    alphabet: str = "digits"
    tiles: int = 1
    background: bool = False
    check_maxlength: bool = True
    tile_border: int = 0
    tile_gap: int = 0
    rendered_image: bool = False
    consensus_fallback: bool = False


CAPTCHA_IMAGES = {
    "chb": CaptchaSpec("img.cimg", 6, ".captcha a.btn-refresh"),
    "sinopac": CaptchaSpec("#imgCode", 6, "#imgCode"),
    "fubon": CaptchaSpec("#captchaImage", 6, "#regenCaptchaLink"),
    "feib": CaptchaSpec('#vercode0area img[data-role="vercode"]', 6, '#vercode0area button[data-action="resetcode"]'),
    "hncb": CaptchaSpec("#code_Cap", 4, 'a[onclick="chgCaptcha();"]', consensus_fallback=True),
    "taishin": CaptchaSpec("img._field_item__verify-code", 6, "button.js-btn-refresh"),
    "shanghai": CaptchaSpec(".ved_img", 5, "button.chg_link", background=True),
    "first_bank": CaptchaSpec("#code_verify", 4, 'a[onclick*="chgImg"]', "alnum", consensus_fallback=True),
    "skbank": CaptchaSpec(".verify img", 4, "a.icon__login--refresh", "alnum", tiles=4,
                          tile_border=2, tile_gap=8, consensus_fallback=True),
    "ubot": CaptchaSpec('img[alt="CAPTCHA"]', 6,
                        '#CAPTCHA + div > div:has(> svg[data-icon="rotate"])', check_maxlength=False),
}
MIN_CHARACTER_CONFIDENCE = 0.95
MIN_INDEPENDENT_CONFIDENCE = 0.50


class CaptchaError(ValueError):
    def __init__(self, message, code="captcha_recognition_failed"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Recognition:
    text: str | None
    reason: str


REASONS = {
    "invalid_image": "驗證碼圖片為空或過大",
    "invalid_format": "辨識文字的長度或字元格式不符",
    "model_disagreement": "兩個 OCR 模型結果不同",
    "low_confidence": "辨識信心不足，第三個 OCR 也未一致確認",
    "dependency_missing": "OCR 相依套件缺漏",
    "ocr_error": "OCR 模型執行失敗",
    "worker_crashed": "OCR 程序異常結束",
    "worker_timeout": "OCR 程序超過 20 秒",
    "worker_unavailable": "無法啟動 OCR 程序",
    "invalid_response": "OCR 程序回傳格式不符",
}


@lru_cache(maxsize=2)
def _model(beta):
    import ddddocr
    return ddddocr.DdddOcr(show_ad=False, beta=beta)


def _decode_result(result, length, alphabet="digits"):
    """Collapse CTC repeats; check each digit, rather than blank confidence."""
    import numpy as np
    try:
        charset = result["charset"]
        probabilities = np.asarray(result["probabilities"], dtype=float)
        if probabilities.ndim == 3:
            if probabilities.shape[1] == 1:
                probabilities = probabilities[:, 0, :]
            elif probabilities.shape[0] == 1:
                probabilities = probabilities[0]
            else:
                return None
        if (probabilities.ndim != 2 or not probabilities.size or
                probabilities.shape[1] != len(charset) or not charset or charset[0] != "" or
                not np.isfinite(probabilities).all() or (probabilities < 0).any() or
                (probabilities > 1).any()):
            return None
        characters, confidences = [], []
        previous = None
        for row in probabilities:
            index = int(row.argmax())
            confidence = float(row[index])
            if index != previous:
                if index != 0:
                    characters.append(charset[index])
                    confidences.append(confidence)
            elif index != 0:
                confidences[-1] = max(confidences[-1], confidence)
            previous = index
        text = "".join(characters)
        allowed = "[0-9]" if alphabet == "digits" else "[A-Za-z0-9]"
        if text != result["text"] or not re.fullmatch(rf"{allowed}{{{length}}}", text):
            return None
        return text, confidences
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def decode_numeric_result(result, length, alphabet="digits"):
    decoded = _decode_result(result, length, alphabet)
    return decoded[0] if decoded and min(decoded[1], default=0) >= MIN_CHARACTER_CONFIDENCE else None


def _tesseract_text(image):
    executable = shutil.which("tesseract")
    if not executable:
        return None
    try:
        from PIL import Image, ImageOps
        picture = Image.open(BytesIO(image)).convert("L")
        picture = picture.point(lambda value: 255 if value > 60 else 0)
        picture = picture.resize((picture.width * 4, picture.height * 4))
        picture = ImageOps.expand(picture, border=16, fill=255)
        buffer = BytesIO()
        picture.save(buffer, format="PNG")
        image = buffer.getvalue()
    except (OSError, ValueError):
        return None
    # A third independent OCR must agree in two segmentation modes. Never
    # strip unexpected characters, alter case, or use a merely plausible code.
    readings = []
    for mode in (6, 7):
        try:
            result = subprocess.run([executable, "stdin", "stdout", "--psm", str(mode),
                                     "-c", "tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"],
                                    input=image, capture_output=True, timeout=5)
            text = re.sub(r"\s", "", result.stdout.decode("ascii").strip())
            if result.returncode or not re.fullmatch(r"[A-Za-z0-9]+", text):
                return None
            readings.append(text)
        except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError):
            return None
    return readings[0] if readings[0] == readings[1] else None


def _tesseract_agrees(image, expected):
    return _tesseract_text(image) == expected


def _worker_result(image, length, alphabet="digits", consensus_fallback=False):
    if not image or len(image) > 512_000:
        return Recognition(None, "invalid_image")
    try:
        results = [_decode_result(_model(beta).classification(image, probability=True), length, alphabet)
                   for beta in (False, True)]
        if consensus_fallback and (not all(results) or results[0][0] != results[1][0]):
            candidates = {result[0] for result in results if result and
                          min(result[1], default=0) >= MIN_INDEPENDENT_CONFIDENCE}
            if all(results) and results[0][0].casefold() == results[1][0].casefold():
                confidence = min(max(a, b) for a, b in zip(results[0][1], results[1][1]))
                if confidence >= MIN_INDEPENDENT_CONFIDENCE:
                    candidates.add(results[0][0])
            # Preserve the exact case recognized by Tesseract in both modes.
            # A complete supported model result must corroborate the glyphs;
            # never uppercase/lowercase the input or merge guessed characters.
            independent = _tesseract_text(image) if candidates else None
            if independent and any(candidate.casefold() == independent.casefold() for candidate in candidates):
                return Recognition(independent, "independent_ocr_consensus")
        if not all(results):
            return Recognition(None, "invalid_format")
        if results[0][0] != results[1][0]:
            return Recognition(None, "model_disagreement")
        # Both models must agree on every character. Their confidence differs
        # by font; require strong evidence from at least one model per character.
        confidence = min(max(a, b) for a, b in zip(results[0][1], results[1][1]))
        text = results[0][0]
        if confidence >= MIN_CHARACTER_CONFIDENCE:
            return Recognition(text, "strong_agreement")
        if consensus_fallback and _tesseract_agrees(image, text):
            return Recognition(text, "three_model_consensus")
        return Recognition(None, "low_confidence")
    except ImportError:
        return Recognition(None, "dependency_missing")
    except Exception:
        return Recognition(None, "ocr_error")


def _recognize_in_worker(image, length, alphabet="digits", consensus_fallback=False):
    return _worker_result(image, length, alphabet, consensus_fallback).text


def run_captcha_worker(image, length, alphabet="digits", consensus_fallback=False):
    if not image or len(image) > 512_000 or length not in (4, 5, 6) or alphabet not in ("digits", "alnum"):
        return Recognition(None, "invalid_image")
    try:
        # Isolate native OCR threads from Playwright's browser event loop.
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(length), alphabet,
                                 "consensus" if consensus_fallback else "strict"],
                                input=image, capture_output=True, timeout=20)
        if result.returncode:
            return Recognition(None, "worker_crashed")
        payload = json.loads(result.stdout.decode("ascii"))
        text, reason = payload["text"], payload["reason"]
        allowed = "[0-9]" if alphabet == "digits" else "[A-Za-z0-9]"
        if text is None and reason in REASONS:
            return Recognition(None, reason)
        if isinstance(text, str) and re.fullmatch(rf"{allowed}{{{length}}}", text) and reason in ("strong_agreement", "three_model_consensus", "independent_ocr_consensus"):
            if reason != "strong_agreement" and not consensus_fallback:
                return Recognition(None, "invalid_response")
            return Recognition(text, reason)
        return Recognition(None, "invalid_response")
    except subprocess.TimeoutExpired:
        return Recognition(None, "worker_timeout")
    except OSError:
        return Recognition(None, "worker_unavailable")
    except (UnicodeDecodeError, ValueError, KeyError, TypeError):
        return Recognition(None, "invalid_response")


def recognize_numeric_captcha(image, length, alphabet="digits"):
    return run_captcha_worker(image, length, alphabet).text


def _image_bytes(images, spec):
    sources = tuple(images.nth(i).get_attribute("style" if spec.background else "src") or ""
                    for i in range(spec.tiles))
    pictures = []
    for i, source in enumerate(sources):
        if not spec.rendered_image and re.match(r"^data:image/(?:png|jpeg|gif);base64,", source):
            if len(source) > 700_000:
                raise ValueError("challenge too large")
            pictures.append(base64.b64decode(source.split(",", 1)[1], validate=True))
        else:
            item = images.nth(i)
            if not spec.background:
                item.evaluate("""async e => {
                    let timer;
                    try {
                        await Promise.race([
                            e.decode(),
                            new Promise((_, reject) => {
                                timer = setTimeout(() => reject(Error('image load timeout')), 5000);
                            })
                        ]);
                        if (!e.complete || !e.naturalWidth) throw Error('image not ready');
                    } finally { clearTimeout(timer); }
                }""")
            pictures.append(item.screenshot(timeout=5000))
    if spec.tiles == 1:
        return sources, pictures[0]
    from PIL import Image
    tiles = [Image.open(BytesIO(picture)).convert("RGB") for picture in pictures]
    if spec.tile_border:
        border = spec.tile_border
        if any(tile.width <= 2 * border or tile.height <= 2 * border for tile in tiles):
            raise ValueError("challenge tile too small")
        # Shin Kong renders four separately boxed characters. Remove only the
        # inspected outer border, then preserve a gap between the characters.
        tiles = [tile.crop((border, border, tile.width - border, tile.height - border)) for tile in tiles]
    canvas = Image.new("RGB", (sum(tile.width for tile in tiles) + spec.tile_gap * (len(tiles) - 1),
                               max(tile.height for tile in tiles)), "white")
    offset = 0
    for tile in tiles:
        canvas.paste(tile, (offset, 0))
        offset += tile.width + spec.tile_gap
    output = BytesIO()
    canvas.save(output, format="PNG")
    return sources, output.getvalue()


def _fill_once(page, frame, spec, field, is_official):
    images = frame.locator(spec.image + ":visible")
    image_count = images.count()
    if image_count == 0:
        # Visibility of the input does not imply that its SPA image has arrived.
        images.first.wait_for(state="visible", timeout=5000)
        image_count = images.count()
    maxlength = field.get_attribute("maxlength")
    if image_count != spec.tiles or (spec.check_maxlength and maxlength != str(spec.length)):
        raise CaptchaError(f"驗證碼表單不符：圖片 {image_count}/{spec.tiles}，"
                           f"maxlength {maxlength!r}/{spec.length}（未送出登入）", "captcha_form_unavailable")
    if field.input_value():
        return False
    sources, image = _image_bytes(images, spec)
    result = run_captcha_worker(image, spec.length, spec.alphabet, spec.consensus_fallback)
    if not result.text:
        raise CaptchaError(REASONS[result.reason], "captcha_ocr_unavailable" if result.reason in
                           ("dependency_missing", "ocr_error", "worker_crashed", "worker_timeout", "worker_unavailable", "invalid_response")
                           else "captcha_recognition_failed")
    text = result.text
    if (not is_official(page.url) or not is_official(frame.url) or field.input_value() or
            tuple(images.nth(i).get_attribute("style" if spec.background else "src") or ""
                  for i in range(spec.tiles)) != sources):
        return False
    # Real key events update banks that validate on keyup, not only input.
    field.press_sequentially(text, delay=40, timeout=5000)
    field.press("Tab", timeout=5000)
    if field.input_value() != text:
        raise CaptchaError("銀行網頁清除或改寫了驗證碼欄位", "captcha_fill_failed")
    return True


def try_fill_captcha(page, frame, bank, field, is_official):
    spec = CAPTCHA_IMAGES.get(bank)
    if not spec or not is_official(page.url) or not is_official(frame.url):
        return False
    import importlib.util
    if importlib.util.find_spec("ddddocr") is None:
        raise CaptchaError(f"目前 Python（{sys.executable}）缺少 ddddocr，請安裝 requirements.lock", "captcha_ocr_unavailable")
    last_error = CaptchaError("圖片在辨識期間變更或欄位已有其他輸入")
    for attempt in range(3):
        if not is_official(page.url) or not is_official(frame.url) or field.input_value():
            return False
        try:
            print(f"[{bank}] 正在自動辨識驗證碼（第 {attempt + 1}/3 張，OCR 最多 20 秒）…", flush=True)
            # SPA images can arrive after the input becomes visible.
            page.wait_for_timeout(500)
            if _fill_once(page, frame, spec, field, is_official):
                return True
        except CaptchaError as error:
            last_error = error
            print(f"[{bank}] {error}", flush=True)
            if error.code in ("captcha_ocr_unavailable", "captcha_fill_failed", "captcha_form_unavailable"):
                raise
        except (PlaywrightError, ValueError, OSError):
            last_error = CaptchaError("驗證碼圖片尚未載入或頁面操作失敗", "captcha_image_unavailable")
            print(f"[{bank}] {last_error}", flush=True)
        if attempt < 2:
            refresh = frame.locator(spec.refresh + ":visible")
            if refresh.count() != 1 or not is_official(page.url) or not is_official(frame.url):
                break
            print(f"[{bank}] 重新產生驗證碼（{attempt + 2}/3）；尚未送出登入。", flush=True)
            try:
                refresh.click(timeout=5000)
            except PlaywrightError:
                break
    raise last_error


if __name__ == "__main__":
    if (len(sys.argv) != 4 or sys.argv[1] not in ("4", "5", "6") or
            sys.argv[2] not in ("digits", "alnum") or sys.argv[3] not in ("strict", "consensus")):
        raise SystemExit(2)
    result = _worker_result(sys.stdin.buffer.read(512_001), int(sys.argv[1]), sys.argv[2], sys.argv[3] == "consensus")
    sys.stdout.write(json.dumps({"text": result.text, "reason": result.reason}))
