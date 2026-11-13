import io
import os
import sys
import asyncio
import time
import subprocess
from PIL import Image
import streamlit as st

@st.cache_resource(show_spinner=False)
def ensure_playwright_installed():
    # If pre-installed in Docker container (/ms-playwright exists), skip
    if os.path.exists("/ms-playwright"):
        return
    try:
        subprocess.run(["playwright", "install", "chromium"], check=False)
    except Exception:
        pass

ensure_playwright_installed()

from playwright.sync_api import sync_playwright

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


def extract_document_to_pdf(
    url: str, headless: bool, scale_factor: int, status_container
) -> io.BytesIO | None:
    status_container.write(f"Connecting to: `{url}`...")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        context = browser.new_context(
            viewport={"width": 1600, "height": 2400},
            device_scale_factor=scale_factor,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        status_container.write("Waiting for page containers (`#page1`) to mount...")
        try:
            page.wait_for_selector('div[id^="page"]', timeout=20000)
        except Exception:
            status_container.write(
                "⚠️ Initial page div not detected immediately. Continuing anyway..."
            )

        time.sleep(3)

        # -------------------------------------------------------------
        # STEP 1: Scroll through document to register all page IDs
        # -------------------------------------------------------------
        status_container.write("Scrolling document to register all pages...")
        scroll_script = """() => {
            const getScrollableContainer = () => {
                const candidates = document.querySelectorAll('div, section, main');
                for (const el of candidates) {
                    const style = window.getComputedStyle(el);
                    if (['auto', 'scroll'].includes(style.overflowY) && el.scrollHeight > el.clientHeight) {
                        return el;
                    }
                }
                return window;
            };

            const container = getScrollableContainer();
            if (container === window) {
                window.scrollBy(0, window.innerHeight * 0.85);
                return {
                    current: window.scrollY + window.innerHeight,
                    total: document.documentElement.scrollHeight
                };
            } else {
                container.scrollTop += container.clientHeight * 0.85;
                return {
                    current: container.scrollTop + container.clientHeight,
                    total: container.scrollHeight
                };
            }
        }"""

        stuck_count = 0
        for _ in range(80):
            metrics = page.evaluate(scroll_script)
            time.sleep(0.5)
            if metrics["current"] >= metrics["total"] - 50:
                stuck_count += 1
                if stuck_count >= 3:
                    break
            else:
                stuck_count = 0

        page_ids = page.evaluate("""() => {
            const divs = document.querySelectorAll('div[id^="page"]');
            const idSet = new Set();
            const regex = /^page(\\d+)$/;
            divs.forEach(d => {
                if (regex.test(d.id)) {
                    idSet.add(d.id);
                }
            });
            return Array.from(idSet).sort((a, b) => {
                const nA = parseInt(a.replace('page', ''), 10);
                const nB = parseInt(b.replace('page', ''), 10);
                return nA - nB;
            });
        }""")

        if not page_ids:
            browser.close()
            raise RuntimeError(
                "No page containers matching '#page1, #page2, ...' were found."
            )

        # -------------------------------------------------------------
        # STEP 2: Hide only fixed/sticky top bars so they don't cover pages
        # -------------------------------------------------------------
        page.evaluate("""() => {
            document.querySelectorAll('*').forEach(el => {
                const style = window.getComputedStyle(el);
                if ((style.position === 'fixed' || style.position === 'sticky') && !el.querySelector('div[id^="page"]')) {
                    el.style.display = 'none';
                }
            });
        }""")

        status_container.write(
            f"Found **{len(page_ids)}** pages. Capturing exact container snapshots..."
        )

        # -------------------------------------------------------------
        # STEP 3: Scroll to each #pageX, measure its exact rect, snapshot
        # -------------------------------------------------------------
        collected_images = []
        progress_bar = st.progress(0, text="Capturing pages...")

        for idx, pid in enumerate(page_ids):
            progress_bar.progress(
                (idx + 1) / len(page_ids),
                text=f"Capturing #{pid} ({idx + 1}/{len(page_ids)})...",
            )

            # Scroll the full-size #pid container into the center of the viewport
            found = page.evaluate(f"""() => {{
                const matches = Array.from(document.querySelectorAll('div[id="{pid}"]'));
                if (!matches.length) return false;

                let target = matches[0];
                let bestArea = 0;
                for (const el of matches) {{
                    const r = el.getBoundingClientRect();
                    if (r.width * r.height > bestArea) {{
                        bestArea = r.width * r.height;
                        target = el;
                    }}
                }}

                target.scrollIntoView({{ behavior: 'instant', block: 'center', inline: 'center' }});
                return true;
            }}""")

            if not found:
                status_container.write(f"⚠️ Skipping `#{pid}` (not found).")
                continue

            # Allow lazy-loaded image & text layer inside #pid to finish rendering
            time.sleep(1.0)

            # Read the exact rendered bounding box of #pid without modifying its CSS
            clip_box = page.evaluate(f"""() => {{
                const matches = Array.from(document.querySelectorAll('div[id="{pid}"]'));
                let target = matches[0];
                let bestArea = 0;
                for (const el of matches) {{
                    const r = el.getBoundingClientRect();
                    if (r.width * r.height > bestArea) {{
                        bestArea = r.width * r.height;
                        target = el;
                    }}
                }}

                const rect = target.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) return null;

                return {{
                    x: Math.max(0, Math.round(rect.left)),
                    y: Math.max(0, Math.round(rect.top)),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height)
                }};
            }}""")

            if not clip_box:
                status_container.write(f"⚠️ Skipping `#{pid}` (empty dimensions).")
                continue

            try:
                img_bytes = page.screenshot(
                    type="png",
                    clip=clip_box,
                    animations="disabled",
                    timeout=15000,
                )
                pil_img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                collected_images.append(pil_img)
            except Exception as e:
                status_container.write(f"⚠️ Failed to capture `#{pid}`: {e}")

        browser.close()
        progress_bar.empty()

        # -------------------------------------------------------------
        # STEP 4: Compile captured snapshots into PDF
        # -------------------------------------------------------------
        if not collected_images:
            return None

        status_container.write(f"Compiling {len(collected_images)} page(s) into PDF...")
        pdf_buffer = io.BytesIO()
        first_page = collected_images[0]
        other_pages = collected_images[1:]

        first_page.save(
            pdf_buffer,
            format="PDF",
            save_all=True,
            append_images=other_pages,
            resolution=150.0 * scale_factor,
        )
        pdf_buffer.seek(0)
        return pdf_buffer


# -------------------------------------------------------------
# STREAMLIT UI
# -------------------------------------------------------------
st.set_page_config(
    page_title="Document to PDF Converter", page_icon="📄", layout="centered"
)

st.title("📄 Document Page Snapshot to PDF")
st.markdown(
    "Captures the exact rendered bounding box of each `#pageX` container into a PDF."
)

url_input = st.text_input(
    "Document URL",
    placeholder="https://www.scribd.com/document/...",
)

col1, col2, col3 = st.columns([2, 1, 1])
with col1:
    filename_input = st.text_input("Output Filename", value="document.pdf")
with col2:
    scale_factor = st.selectbox(
        "Quality (DPI Scale)",
        options=[1, 2, 3],
        index=1,
        help="2x gives crisp text and sheet music.",
    )
with col3:
    st.write("")
    st.write("")
    has_display = bool(os.environ.get("DISPLAY"))
    headless_mode = st.checkbox(
        "Headless",
        value=True,
        disabled=not has_display,
        help="Headless mode is required in Docker or cloud environments without a display."
        if not has_display
        else "Run browser in background without opening a window.",
    )

if st.button("Generate PDF", type="primary", use_container_width=True):
    clean_url = url_input.strip()
    if not clean_url:
        st.error("Please enter a valid URL.")
    else:
        output_name = filename_input.strip() or "document.pdf"
        if not output_name.lower().endswith(".pdf"):
            output_name += ".pdf"

        try:
            with st.status("Running extraction...", expanded=True) as status:
                pdf_data = extract_document_to_pdf(
                    clean_url, headless_mode, scale_factor, status
                )

                if pdf_data:
                    status.update(
                        label="PDF compiled successfully!",
                        state="complete",
                        expanded=False,
                    )
                    st.session_state["pdf_bytes"] = pdf_data.getvalue()
                    st.session_state["pdf_filename"] = output_name
                else:
                    status.update(label="Extraction failed.", state="error")
                    st.error("No pages were captured. PDF creation aborted.")

        except Exception as e:
            st.error(f"An error occurred: {e}")

if "pdf_bytes" in st.session_state:
    st.success(f"Ready: **{st.session_state['pdf_filename']}**")
    st.download_button(
        label="⬇️ Download PDF",
        data=st.session_state["pdf_bytes"],
        file_name=st.session_state["pdf_filename"],
        mime="application/pdf",
        use_container_width=True,
    )
