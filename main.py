import io
import time
import urllib.request
from PIL import Image
from playwright.sync_api import sync_playwright


def download_scribd_doc(url: str, output_pdf: str = "document.pdf"):
    print(f"Connecting to: {url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1400, "height": 1800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        # Use 'domcontentloaded' instead of 'networkidle' to avoid timeouts
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # Wait for the first page div to appear in the DOM
        print("Waiting for page container (#page1) to mount...")
        try:
            page.wait_for_selector('div[id^="page"]', timeout=20000)
        except Exception:
            print(
                "Warning: Initial page div not detected immediately. Continuing anyway..."
            )

        time.sleep(3)

        # -------------------------------------------------------------
        # STEP 1: Scroll all the way to the end to trigger lazy loading
        # -------------------------------------------------------------
        print("Scrolling through the document to trigger full page hydration...")

        # Find the actual scrollable element or fallback to window
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
                window.scrollBy(0, window.innerHeight * 0.9);
                return {
                    current: window.scrollY + window.innerHeight,
                    total: document.documentElement.scrollHeight
                };
            } else {
                container.scrollTop += container.clientHeight * 0.9;
                return {
                    current: container.scrollTop + container.clientHeight,
                    total: container.scrollHeight
                };
            }
        }"""

        last_total = 0
        stuck_count = 0
        max_steps = 60

        for step in range(max_steps):
            metrics = page.evaluate(scroll_script)
            time.sleep(1.0)

            # Check if we reached the bottom or content stopped growing
            if metrics["current"] >= metrics["total"] - 50:
                stuck_count += 1
                if stuck_count >= 3:
                    print("Reached end of document.")
                    break
            else:
                stuck_count = 0

            last_total = metrics["total"]

        print("Finished scrolling. Allowing assets to settle...")
        time.sleep(3)

        # -------------------------------------------------------------
        # STEP 2: Retrieve all matching page divs sorted numerically
        # -------------------------------------------------------------
        page_ids = page.evaluate("""() => {
            const divs = document.querySelectorAll('div[id^="page"]');
            const ids = [];
            const regex = /^page(\\d+)$/;
            divs.forEach(d => {
                if (regex.test(d.id)) {
                    ids.push(d.id);
                }
            });
            return ids.sort((a, b) => {
                const nA = parseInt(a.replace('page', ''), 10);
                const nB = parseInt(b.replace('page', ''), 10);
                return nA - nB;
            });
        }""")

        if not page_ids:
            print("Error: No divs matching '#page1, #page2, ...' found.")
            browser.close()
            return

        print(f"Found {len(page_ids)} page container(s): {page_ids}")

        # -------------------------------------------------------------
        # STEP 3: Extract and download images for each page
        # -------------------------------------------------------------
        collected_images = []

        for pid in page_ids:
            print(f"Processing #{pid}...")
            container = page.locator(f"#{pid}")
            container.scroll_into_view_if_needed()
            time.sleep(0.5)

            img_bytes = None
            img_locator = container.locator("img")

            if img_locator.count() > 0:
                first_img = img_locator.first
                src = first_img.get_attribute("src") or ""

                if src.startswith("http://") or src.startswith("https://"):
                    try:
                        req = urllib.request.Request(
                            src,
                            headers={
                                "User-Agent": (
                                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                                    "Chrome/124.0.0.0 Safari/537.36"
                                ),
                                "Referer": "https://www.scribd.com/",
                            },
                        )
                        with urllib.request.urlopen(req, timeout=15) as response:
                            img_bytes = response.read()
                    except Exception as e:
                        print(
                            f"Direct download failed for {src}: {e}. Falling back to element screenshot."
                        )

                # Fallback to element screenshot if URL download failed
                if not img_bytes:
                    try:
                        img_bytes = first_img.screenshot()
                    except Exception:
                        img_bytes = None

            # Fallback to container screenshot if no <img> tag or it failed
            if not img_bytes:
                img_bytes = container.screenshot()

            try:
                pil_img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                collected_images.append(pil_img)
            except Exception as e:
                print(f"Could not parse image for #{pid}: {e}")

        browser.close()

        # -------------------------------------------------------------
        # STEP 4: Compile downloaded images into PDF
        # -------------------------------------------------------------
        if collected_images:
            print(
                f"\nCompiling {len(collected_images)} image(s) into '{output_pdf}'..."
            )
            first_page = collected_images[0]
            other_pages = collected_images[1:]

            first_page.save(
                output_pdf,
                save_all=True,
                append_images=other_pages,
                resolution=150.0,
            )
            print(f"Done. PDF saved successfully as: {output_pdf}")
        else:
            print("No images were extracted. PDF creation aborted.")


if __name__ == "__main__":
    scribd_url = "https://www.scribd.com/document/819012042/Rock-Pop-Drums-Session-Skills-Initial-Grade-2"
    output_path = "Rock_Pop_Drums_Session_Skills.pdf"

    download_scribd_doc(scribd_url, output_path)
