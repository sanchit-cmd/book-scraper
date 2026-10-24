# Scribd Document Scraper

A small Python utility that opens a Scribd document in a Chromium browser, scrolls through it to load its pages, collects page images, and combines them into a PDF.

## Requirements

- Python 3.12 or newer
- uv for dependency management
- A working Playwright Chromium installation

## Setup

Install the project dependencies and the Chromium browser:

```bash
uv sync
uv run playwright install chromium
```

## Usage

In `main.py`, set `scribd_url` to the document you are authorized to access and `output_path` to the desired PDF filename. Then run:

```bash
uv run python main.py
```

The script opens a visible browser window, scrolls through the document to trigger lazy loading, and saves the collected pages as an image-based PDF at the configured output path.

The scraper expects the document viewer to expose page containers with IDs such as `page1`, `page2`, and so on. Changes to Scribd's viewer or page markup may prevent extraction from working.

## Notes

- The output PDF is made from page images rather than selectable text.
- Browser access, document availability, and image extraction may vary by document.
- Use this utility only for content you are permitted to access and download. Respect Scribd's terms, copyright, and applicable laws. It does not bypass access controls.
