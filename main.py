import io
import sys
import asyncio
import time
from PIL import Image
import streamlit as st
from playwright.sync_api import sync_playwright

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


def extract_document_to_pdf(
    url: str, headless: bool, scale_factor: int, status_container
) -> io.BytesIO | None:
    status_container.write(f"Connecting to: `{url}`...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            viewport={"width": 1920, "height": 2600},
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
        # STEP 1: Scroll through document to discover all page IDs
        # -------------------------------------------------------------
        status_container.write("Scrolling document to register all page containers...")
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

        # Collect unique sorted page IDs (#page1, #page2, ...)
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
        # STEP 2: Strip headers/sidebars & remove duplicate hidden thumbnails
        # -------------------------------------------------------------
        status_container.write(
            "Cleaning up site headers, duplicate thumbnails, and overlays..."
        )
        page.evaluate("""() => {
            // 1. Remove duplicate hidden thumbnail elements that share #pageX IDs
            const seen = new Set();
            document.querySelectorAll('div[id^="page"]').forEach(el => {
                const rect = el.getBoundingClientRect();
                // Keep only the full-sized reader page (width > 300px)
                if (rect.width < 300 || seen.has(el.id)) {
                    el.remove();
                } else {
                    seen.add(el.id);
                }
            });

            // 2. Remove headers, toolbars, sidebars, and promos
            const selectorsToRemove = [
                'header', 'nav', 'aside',
                '[data-testid="global-header"]',
                '[class*="GlobalHeader"]',
                '[class*="DocumentToolbar"]',
                '[class*="Toolbar"]',
                '[class*="Sidebar"]',
                '[class*="PromoBanner"]',
                '[class*="Paywall"]',
                '[id*="header"]',
                '[id*="toolbar"]'
            ];
            selectorsToRemove.forEach(sel => {
                document.querySelectorAll(sel).forEach(el => {
                    if (!el.querySelector('div[id^="page"]')) {
                        el.remove();
                    }
                });
            });

            // 3. Remove any remaining fixed/sticky elements
            document.querySelectorAll('*').forEach(el => {
                const style = window.getComputedStyle(el);
                if ((style.position === 'fixed' || style.position === 'sticky') && !el.querySelector('div[id^="page"]')) {
                    el.remove();
                }
            });

            // 4. Remove blur filters
            document.querySelectorAll('[class*="blurred"], [style*="blur"]').forEach(el => {
                el.style.filter = 'none';
                el.style.webkitFilter = 'none';
                el.style.opacity = '1';
            });

            // 5. Widen ancestor containers so right-side music staves never clip
            const firstPage = document.getElementById('page1');
            if (firstPage) {
                let parent = firstPage.parentElement;
                while (parent && parent !== document.body) {
                    parent.style.maxWidth = 'none';
                    parent.style.width = '100%';
                    parent.style.overflow = 'visible';
                    parent.style.marginLeft = '0';
                    parent.style.paddingLeft = '20px';
                    parent = parent.parentElement;
                }
            }
        }""")

        status_container.write(
            f"Found **{len(page_ids)}** pages. Capturing full pages..."
        )

        # -------------------------------------------------------------
        # STEP 3: Hydrate & clip-capture exact bounding box of each #pageX
        # -------------------------------------------------------------
        collected_images = []
        progress_bar = st.progress(0, text="Capturing pages...")

        for idx, pid in enumerate(page_ids):
            progress_bar.progress(
                (idx + 1) / len(page_ids),
                text=f"Capturing #{pid} ({idx + 1}/{len(page_ids)})...",
            )

            # Scroll #pageX to the top-left of the viewport to trigger lazy asset hydration
            page.evaluate(f"""() => {{
                const el = document.getElementById('{pid}');
                if (el) {{
                    el.scrollIntoView({{ behavior: 'instant', block: 'start', inline: 'start' }});
                }}
            }}""")
            time.sleep(1.1)

            # Calculate the exact unclipped bounding box of #pageX including inner layers
            clip_box = page.evaluate(f"""() => {{
                const el = document.getElementById('{pid}');
                if (!el) return null;

                el.style.overflow = 'visible';
                const elRect = el.getBoundingClientRect();
                let minLeft = elRect.left;
                let minTop = elRect.top;
                let maxRight = elRect.right;
                let maxBottom = elRect.bottom;

                el.querySelectorAll('div, img').forEach(child => {{
                    const r = child.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {{
                        if (r.left < minLeft) minLeft = r.left;
                        if (r.top < minTop) minTop = r.top;
                        if (r.right > maxRight) maxRight = r.right;
                        if (r.bottom > maxBottom) maxBottom = r.bottom;
                    }}
                }});

                const width = Math.ceil(maxRight - minLeft);
                const height = Math.ceil(maxBottom - minTop);

                if (width <= 0 || height <= 0) return null;

                return {{
                    x: Math.max(0, Math.floor(minLeft)),
                    y: Math.max(0, Math.floor(minTop)),
                    width: width,
                    height: height
                }};
            }}""")

            if not clip_box:
                status_container.write(f"⚠️ Skipping `#{pid}` (not rendered in DOM).")
                continue

            try:
                # Using page.screenshot(clip=...) avoids locator visibility/stability timeouts
                # and captures the exact union bounding box of the staves + text layer
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
        # STEP 4: Compile captured page images into PDF
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

st.title("📄 Full-Page Document Capture to PDF")
st.markdown(
    "Captures each `#pageX` container cleanly with full musical staves and text layers."
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
    headless_mode = st.checkbox("Headless", value=True)

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
