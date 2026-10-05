import base64
import hashlib
import http.cookiejar
import io
import json
import re
import shutil
import ssl
import subprocess
import tempfile
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

from PIL import Image, ImageOps
import pypdf
from storage_utils import private_directory, private_file


class FubonDownloadError(RuntimeError):
    pass


class FubonStatementDownloader:
    BASE_URL = "https://fbmbill.taipeifubon.com.tw/"
    SERIAL_PATTERN = re.compile(r"/client/pdf/([0-9a-f]+)", re.IGNORECASE)

    def __init__(self, identity: str, birthday: str, download_dir: Path, captcha_retries: int = 5):
        if not identity or not birthday:
            raise ValueError("台北富邦下載需要 FUBON_ID 與 FUBON_BIRTHDAY")
        self.identity = identity
        self.birthday = birthday
        self.download_dir = Path(download_dir)
        private_directory(self.download_dir)
        self.captcha_retries = captcha_retries
        if self.captcha_retries <= 0:
            raise ValueError("captcha_retries 必須大於 0")
        if not shutil.which("tesseract"):
            raise FubonDownloadError("找不到 tesseract，無法自動辨識富邦驗證碼")

        context = ssl.create_default_context()
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        handler = urllib.request.HTTPSHandler(context=context)
        cookies = urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        self.opener = urllib.request.build_opener(handler, cookies)

    def _request(self, path: str, payload=None, headers=None, method="GET") -> bytes:
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        data = None
        if method == "POST":
            data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            urllib.parse.urljoin(self.BASE_URL, path),
            data=data,
            headers=request_headers,
        )
        with self.opener.open(request, timeout=30) as response:
            return response.read()

    @staticmethod
    def _solve_captcha(image_bytes: bytes) -> str:
        with tempfile.TemporaryDirectory(prefix="fubon-captcha-") as temp_dir:
            source = Path(temp_dir) / "captcha.jpg"
            source.write_bytes(image_bytes)

            image = Image.open(source).convert("RGB")
            candidates = []
            # Try several blue-channel masks and segmentation modes. Wrong OCR
            # candidates are rejected by the bank and retried with a new image.
            thresholds = ((25, 15, 60), (35, 20, 80), (45, 30, 100))
            for index, (red_gap, green_gap, minimum_blue) in enumerate(thresholds):
                cleaned = Path(temp_dir) / f"captcha-clean-{index}.png"
                output = Image.new("L", image.size, 255)
                output.putdata([
                    0 if blue > red + red_gap
                    and blue > green + green_gap
                    and blue > minimum_blue else 255
                    for red, green, blue in image.getdata()
                ])
                output = output.resize((800, 320))
                output = ImageOps.expand(output, border=100, fill=255)
                output.save(cleaned)

                for page_mode in (7, 8, 13):
                    process = subprocess.run(
                        [
                            "tesseract", str(cleaned), "stdout", "--psm", str(page_mode),
                            "-c", "tessedit_char_whitelist=0123456789",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=15,
                        check=False,
                    )
                    code = re.sub(r"\D", "", process.stdout)
                    if process.returncode == 0 and len(code) == 4:
                        candidates.append(code)

            if not candidates:
                raise FubonDownloadError("驗證碼辨識失敗")
            return Counter(candidates).most_common(1)[0][0]

    def _get_captcha(self) -> tuple[str, bytes]:
        response = self._request("checkImgs/captcha.jpg", payload=None, method="POST").decode("utf-8")
        try:
            captcha_key, encoded_image = response.split(",", 1)
            return captcha_key, base64.b64decode(encoded_image)
        except (ValueError, TypeError) as exc:
            raise FubonDownloadError("富邦驗證碼回應格式錯誤") from exc

    def _login(self, serial_key: str) -> dict:
        last_error = "驗證碼辨識失敗"
        for attempt in range(1, self.captcha_retries + 1):
            captcha_key, image = self._get_captcha()
            try:
                captcha_code = self._solve_captcha(image)
            except FubonDownloadError as exc:
                last_error = str(exc)
                print(f"  [富邦] 第 {attempt} 次驗證碼辨識失敗，重新取得驗證碼。")
                continue

            response = self._request(
                "doLogin",
                payload={
                    "id": self.identity,
                    "captchaCode": f"{captcha_key},{captcha_code}",
                    "serialKey": serial_key,
                    "birthday": self.birthday,
                },
                method="POST",
            )
            data = json.loads(response.decode("utf-8"))
            error = data.get("errorMsg")
            if not error:
                return data
            last_error = error
            if "驗證碼" not in error:
                break
            print(f"  [富邦] 第 {attempt} 次驗證碼驗證失敗，重新辨識。")
        raise FubonDownloadError(f"富邦帳單登入失敗：{last_error}")

    @staticmethod
    def _output_name(bill_period: str) -> str:
        period = re.match(r"^(1\d{2})(\d{2})", bill_period or "")
        if period:
            year = int(period.group(1)) + 1911
            month = int(period.group(2))
            return f"台北富邦銀行{year}年{month}月信用卡帳單.pdf"
        safe_period = re.sub(r"[^\w-]", "", bill_period or "") or "最新"
        return f"台北富邦銀行{safe_period}信用卡帳單.pdf"

    def download(self, statement_url: str) -> Path:
        match = self.SERIAL_PATTERN.search(statement_url)
        if not match:
            raise FubonDownloadError(f"無法從連結取得富邦帳單序號：{statement_url}")
        login = self._login(match.group(1))

        required = ("jwt", "billPeriod", "batchPeriod", "uniqueIdentifier", "twYearMonth")
        missing = [key for key in required if not login.get(key)]
        if missing:
            raise FubonDownloadError(f"富邦登入回應缺少欄位：{', '.join(missing)}")

        query = urllib.parse.urlencode({
            "billPeriod": login["billPeriod"],
            "batchPeriod": login["batchPeriod"],
            "id": login["uniqueIdentifier"],
            "twYearMonth": login["twYearMonth"],
        })
        pdf_bytes = self._request(
            f"PDFReportProc?{query}",
            headers={"Authorization": login["jwt"]},
        )
        if not pdf_bytes.startswith(b"%PDF"):
            raise FubonDownloadError("富邦下載回應不是 PDF")

        try:
            reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
            if reader.is_encrypted and reader.decrypt(self.identity) == 0:
                raise FubonDownloadError("富邦下載的 PDF 解密驗證失敗")
            page_count = len(reader.pages)
        except Exception as exc:
            if isinstance(exc, FubonDownloadError):
                raise
            raise FubonDownloadError("富邦下載的 PDF 無法開啟") from exc
        if page_count < 1:
            raise FubonDownloadError("富邦下載的 PDF 沒有頁面")

        output = self.download_dir / self._output_name(login["billPeriod"])
        new_digest = hashlib.sha256(pdf_bytes).digest()
        if output.exists() and hashlib.sha256(output.read_bytes()).digest() == new_digest:
            private_file(output)
            return output

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                "wb", dir=output.parent, prefix=f".{output.name}.",
                suffix=".tmp", delete=False,
            ) as temp_file:
                temp_file.write(pdf_bytes)
                temp_path = Path(temp_file.name)
            temp_path.replace(output)
            private_file(output)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
        return output
