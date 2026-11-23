#!/usr/bin/env python3
"""
Recon for Temp Numbers (gen-stats). Logs in, opens the target page, clicks
"Show Report" and prints ONLY the structure of what the page loads
(endpoints, JSON keys, table headers). Digits masked for public logs.
"""
import os
import re
import sys
import time
from urllib.parse import urlparse, parse_qs

from playwright.sync_api import sync_playwright

LOGIN_URL = os.getenv("LOGIN_URL", "https://tempnumbers.net/login")
TARGET_URL = os.getenv("TARGET_URL", "https://tempnumbers.net/gen-stats")
# Test account (hardcoded for this smoke recon)
USER = os.getenv("PANEL_USER", "test_panel")
PASSWORD = os.getenv("PANEL_PASS", "test_panel")


def mask(value, limit=48):
    text = re.sub(r"\d", "#", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit] + "…"


def shape(value, depth=0):
    """Structure only: keys and types, never values."""
    if depth > 4:
        return "…"
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in list(value.items())[:25]}
    if isinstance(value, list):
        return [f"len={len(value)}", shape(value[0], depth + 1) if value else None]
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "num"
    if value is None:
        return "null"
    return f"str({len(str(value))})"


def first_sample(value):
    """First object of the first list found, with every value masked."""
    if isinstance(value, list):
        if value and isinstance(value[0], dict):
            return {k: mask(v) for k, v in list(value[0].items())[:25]}
        return None
    if isinstance(value, dict):
        for v in value.values():
            found = first_sample(v)
            if found:
                return found
    return None


captured = []


def on_response(resp):
    req = resp.request
    if req.resource_type not in ("xhr", "fetch"):
        return
    url = urlparse(resp.url)
    entry = {
        "method": req.method,
        "path": url.path,
        "query_keys": sorted(parse_qs(url.query).keys()),
        "status": resp.status,
        "ctype": resp.headers.get("content-type", "").split(";")[0],
    }
    if req.method != "GET":
        try:
            body = req.post_data_json
            entry["request_body_keys"] = (
                sorted(body.keys()) if isinstance(body, dict) else type(body).__name__
            )
        except Exception:
            entry["request_body"] = "not json"
    try:
        if "json" in entry["ctype"]:
            data = resp.json()
            entry["shape"] = shape(data)
            entry["sample_row"] = first_sample(data)
        else:
            entry["bytes"] = len(resp.body())
    except Exception as exc:
        entry["error"] = type(exc).__name__
    captured.append(entry)


def flush(title):
    print(f"\n----- {title}: {len(captured)} API call(s) -----", flush=True)
    for e in captured:
        print(
            f"{e['method']} {e['path']} q={e['query_keys']} -> {e['status']} {e['ctype']}",
            flush=True,
        )
        for key in ("request_body_keys", "request_body", "bytes", "error"):
            if key in e:
                print(f"    {key}: {e[key]}", flush=True)
        if "shape" in e:
            print(f"    shape: {e['shape']}", flush=True)
        if e.get("sample_row"):
            print(f"    sample_row(masked): {e['sample_row']}", flush=True)
    captured.clear()


def describe_page(page, label):
    print(f"\n===== PAGE: {label} =====", flush=True)
    print("url:", page.url, "| title:", page.title(), flush=True)
    try:
        btns = page.locator("button").all_inner_texts()
        print("buttons:", [mask(t, 30) for t in btns][:25], flush=True)
    except Exception as e:
        print("buttons: error", e, flush=True)
    try:
        print(
            "inputs:",
            [
                {
                    "type": i.get_attribute("type"),
                    "name": i.get_attribute("name"),
                    "placeholder": i.get_attribute("placeholder"),
                }
                for i in page.locator("input, select").all()[:25]
            ],
            flush=True,
        )
    except Exception as e:
        print("inputs: error", e, flush=True)
    tables = page.locator("table").all()
    print("tables:", len(tables), flush=True)
    for n, table in enumerate(tables[:4]):
        headers = [mask(h, 30) for h in table.locator("th").all_inner_texts()]
        rows = table.locator("tbody tr").count()
        print(f"  table[{n}] headers={headers} rows={rows}", flush=True)
        if rows:
            print(
                f"    first row (masked): {mask(table.locator('tbody tr').first.inner_text(), 160)}",
                flush=True,
            )


def fill_react(page, selector: str, value: str) -> None:
    loc = page.locator(selector).first
    loc.wait_for(state="visible", timeout=30000)
    loc.click()
    loc.fill("")
    loc.type(value, delay=15)
    page.evaluate(
        """([sel, val]) => {
            const el = document.querySelector(sel);
            if (!el) return;
            const desc = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            );
            if (desc && desc.set) desc.set.call(el, val);
            else el.value = val;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""",
        [selector, value],
    )


def is_logged_in(page) -> bool:
    url = (page.url or "").lower()
    if any(x in url for x in ("/dashboard", "/gen-stats", "/cli-search", "/numbers")):
        return True
    if url.rstrip("/").endswith("/login"):
        return False
    try:
        body = page.inner_text("body")
        if "Sign in" in body[:600] and "nl-username" in page.content():
            return False
        if "Dashboard" in body or "CLI Search" in body or "test_panel" in body:
            return True
    except Exception:
        pass
    return False


def login(page) -> bool:
    print("[1] Opening login page...", flush=True)
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    page.wait_for_selector("#nl-username", timeout=30000)
    print("[1] Login form visible", flush=True)

    print("[2] Filling credentials...", flush=True)
    fill_react(page, "#nl-username", USER)
    fill_react(page, "#nl-password", PASSWORD)
    print(f"[2] username field={page.input_value('#nl-username')!r}", flush=True)

    print("[2] Clicking Sign in...", flush=True)
    btn = page.locator("button.nl-submit, button[type='submit']").first
    if btn.count():
        btn.click()
    else:
        page.get_by_role("button", name=re.compile(r"sign\s*in|log\s*in", re.I)).first.click()

    for i in range(45):
        time.sleep(1)
        if is_logged_in(page):
            print(f"[2] Login OK after {i+1}s → {page.url}", flush=True)
            return True
        if i in (5, 10, 20, 30):
            print(f"[2] waiting... url={page.url}", flush=True)

    print(f"[FAIL] still not logged in: {page.url}", flush=True)
    try:
        page.screenshot(path="login_fail.png", full_page=True)
    except Exception:
        pass
    return False


def main():
    print("== Recon gen-stats ==", flush=True)
    print(f"login={LOGIN_URL}", flush=True)
    print(f"target={TARGET_URL}", flush=True)
    print(f"user={USER}", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(viewport={"width": 1366, "height": 900})
        page = context.new_page()
        page.set_default_timeout(60000)
        page.on("response", on_response)

        if not login(page):
            print("[FAIL] login did not leave /login", flush=True)
            browser.close()
            sys.exit(1)
        print("[OK] logged in →", urlparse(page.url).path, flush=True)
        flush("after login")

        now = time.time()
        print(
            "\ncookies:",
            [
                {
                    "name": c["name"],
                    "httpOnly": c["httpOnly"],
                    "secure": c["secure"],
                    "expires_in_h": (
                        None
                        if c["expires"] in (-1, None)
                        else round((c["expires"] - now) / 3600, 1)
                    ),
                }
                for c in context.cookies()
            ],
            flush=True,
        )
        try:
            print("localStorage keys:", page.evaluate("Object.keys(localStorage)"), flush=True)
            print("sessionStorage keys:", page.evaluate("Object.keys(sessionStorage)"), flush=True)
        except Exception as e:
            print("storage keys error:", e, flush=True)

        print("[3] Opening target page...", flush=True)
        page.goto(TARGET_URL, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        time.sleep(2)
        describe_page(page, "target (before Show Report)")
        flush("loading the target page")

        show = page.get_by_role("button", name=re.compile(r"show\s*report", re.I))
        if show.count():
            print("[4] Clicking Show Report...", flush=True)
            show.first.click()
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            time.sleep(2)
            describe_page(page, "target (after Show Report)")
            flush("after clicking Show Report")
        else:
            print("\n[INFO] no 'Show Report' button found", flush=True)

        print("\nwaiting 25s to see whether the page refreshes on its own…", flush=True)
        time.sleep(25)
        flush("while idle for 25s")
        browser.close()

    print("\n== RECON DONE ==", flush=True)


if __name__ == "__main__":
    main()
