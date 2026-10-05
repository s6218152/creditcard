## Quick orientation

This repository downloads credit-card statement PDFs from an IMAP inbox, extracts text, runs bank-specific parsers, and writes parsed JSON into `output/`.

- Entry points
  - `main.py::run_pipeline()` — full download + parse pipeline (default `config.yaml`).
  - `daemon.py` — long-running scheduler; `--once` runs the pipeline a single time.
  - `mail_fetcher.py::MailFetcher` — IMAP connect, header filtering and attachment download.
  - `pdf_processor.py::PDFProcessor` — text extraction using pypdf (default) or pdfplumber.
  - `fubon_downloader.py::FubonStatementDownloader` — protected Taipei Fubon link login, local captcha OCR, validated PDF download.
  - `ctbc_balance.py` — interactive Playwright query of a single CTBC deposit account balance after manual login.

## What to know about architecture and dataflow

1. MailFetcher (IMAP) -> downloads PDFs to `downloads/` and records history in `downloads/history.json`. Taipei Fubon messages contain a protected link instead of an attachment; those links are recorded and downloaded once through `FubonStatementDownloader`.
2. `main.run_pipeline()` reads `config.yaml`, picks PDF files, filters to the latest period (see `statement_filter.latest_statement_flags`) and passes each to `pdf_processor.PDFProcessor`.
3. Extracted text + filename are dispatched to `parsers.engine.get_parser_for_text()` which returns a parser instance (registry in `parsers/engine.py`).
4. Parser result + metadata are written as JSON to `output/` with a `_parsed.json` suffix. `output/latest.json` lists only successful results from the current latest-period selection; older/error JSON files may remain in the directory. The pipeline caches by SHA256 and `PARSER_ENGINE_VERSION` to skip already-parsed files.

Key files to inspect for behavior examples: `mail_fetcher.py`, `main.py`, `pdf_processor.py`, `parsers/ctbc.py`, `parsers/engine.py`, `statement_filter.py`.

## Project-specific conventions an agent should follow

- Config-driven: runtime values live in `config.yaml`. Many passwords are stored as `${ENV_VAR}` placeholders and resolved from environment variables in `main.py`.
- Naming conventions: download filenames are prefixed with UID/date by `MailFetcher`. Parsers detect bank by filename (e.g. `ctbc` or `CTBC` maps to `parsers.ctbc.CtbcParser`).
- Parser selection: `get_parser_for_text(text, filename)` prefers filename hints over text. CTBC uses filename detection because embedded fonts make text labels unreliable (`parsers/ctbc.py`).
- PDF extraction choice: For CTBC, code uses pdfplumber (`use_pdfplumber = is_ctbc_statement(pdf_path)` in `main.py`) because pypdf can fail for embedded-font glyphs.
- Caching: `main.process_single_pdf()` compares `pdf_sha256` and `PARSER_ENGINE_VERSION` to decide whether to reuse `output/*_parsed.json`.

## Important environment variables and runtime setup

- .env variables used in code:
  - YAHOO_EMAIL, YAHOO_APP_PASSWORD — IMAP login (used in `mail_fetcher.connect`).
  - PDF_PASSWORD or bank-specific secrets referenced in `config.yaml` (e.g. `${TSB_CREDITCARD_PDF_PASSWORD}`) — used when decrypting PDFs.
  - Any `${...}` in `config.yaml` is replaced with the corresponding env var string (see `main.py` password resolution logic).
  - FUBON_ID, FUBON_BIRTHDAY — protected Taipei Fubon statement login fields.
  - Taipei Fubon captcha OCR also requires the system `tesseract` executable.

## How to run and debug (concrete commands)

1. Install dependencies (uses pip): `requirements.txt` states supported minimums; `requirements.lock` captures the tested exact versions.

2. Run pipeline once (reads `config.yaml` and `.env`):

```bash
python main.py
```

3. Run daemon mode (interval set in `config.yaml`):

```bash
python daemon.py        # runs the repeating daemon
python daemon.py --once # run once and exit
```

4. Run tests (pytest):

```bash
pytest -q
```

Debug tips:
- If downloads aren't happening, ensure `YAHOO_EMAIL` and `YAHOO_APP_PASSWORD` are set and `config.yaml.mail.folder` is correct.
- To reproduce parser selection issues, call `parsers.engine.get_parser_for_text(text, filename=...)` with the extracted text and filename used in `main.py`.

## Notable patterns & gotchas for agents

- IMAP search: `MailFetcher.get_search_cutoff_date()` supports `search_mode: latest_month` in `config.yaml` — prefer that logic rather than hard-coded date ranges.
- Filename-based bank detection is intentionally used for reliability (see `main.detect_bank_from_filename` and `parsers/engine`). Avoid changing parser selection to rely only on free text unless adding tests.
- `statement_filter.latest_statement_flags()` groups filenames by normalized templates and keeps only the latest period per group — modifying it affects which PDFs are parsed.
- CTBC statements: `parsers/ctbc.CtbcParser` pulls due date and amount from nearby lines because embedded fonts break label extraction; tests expect this behavior (`tests/test_main.py`).

## Tests and expectations

- Unit tests live in `tests/` and use pytest. Key test files:
  - `tests/test_main.py` (filename detection + parser selection)
  - `tests/test_mail_fetcher.py` (MailFetcher utilities)
  - `tests/test_pdf_processor.py` and `tests/test_parsers.py`

- When changing parser behavior, update `PARSER_ENGINE_VERSION` in `parsers/engine.py` so the cache invalidation triggers re-parsing of existing `output/*_parsed.json` files.

## If you need to change behavior

- To add a new bank parser:
  1. Add parser class under `parsers/` (follow `BaseParser` API in `parsers/base.py`).
  2. Register instance in `parsers/engine.py`'s `PARSERS_REGISTRY` and bump `PARSER_ENGINE_VERSION`.
  3. Add focused unit tests in `tests/` to cover filename detection and parsing edge cases.

## Where to look first when something breaks

- Download problems: `mail_fetcher.py` (credentials, search criteria, local_filters).
- Missing text or wrong amounts: `pdf_processor.py` extraction mode and `parsers/*` for bank-specific logic.
- Unexpected skipping of files: `statement_filter.py` and the cache check in `main.process_single_pdf()`.

---
If any section is unclear or you want more examples (small unit-test stubs, a local reproduction script, or sample `.env`), tell me which area to expand.
