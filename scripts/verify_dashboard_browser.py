from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlencode

from PIL import Image, ImageStat
from playwright.sync_api import (
    Page,
    sync_playwright,
)
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "tmp" / "dashboard_browser"
BROWSER_CANDIDATES = [
    Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
    Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
]

VIEW_CHECKS = [
    ("mission", "Mission Control", "#metrics .metric", "mission"),
    ("sessions", "Sessions", "#session-board", "sessions"),
    ("handoffs", "Handoffs", "#handoff-board", "handoffs"),
    ("code-risk", "Code Risk", "#code-risk-files", "code-risk"),
    ("changesets", "Changesets", "#changeset-board", "changesets"),
    ("graph", "Graph", "#graph-nodes", "graph"),
    ("usage", "Usage Evidence", "#usage-evidence", "usage"),
    ("timeline", "Timeline", "#timeline", "timeline"),
    ("trace", "Relationships", "#trace-grid", "relationships"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the local Geond dashboard in a browser.")
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--workspace", default="")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--browser", type=Path, default=None)
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    browser_path = args.browser or find_browser()
    if browser_path is None:
        raise SystemExit("No Edge or Chrome executable found; pass --browser or install a browser.")

    report: dict[str, object] = {
        "url": args.url,
        "workspace": args.workspace,
        "browser": str(browser_path),
        "screenshots": [],
        "checks": [],
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=str(browser_path))
        page = browser.new_page(viewport={"width": 1440, "height": 980}, device_scale_factor=1)
        try:
            load_dashboard(page, args.url, args.workspace, args.timeout_ms)
            for view, label, selector, name in VIEW_CHECKS:
                click_view(page, view, label, selector, args.timeout_ms)
                screenshot = capture(page, output_dir, name)
                report["screenshots"].append(str(screenshot.relative_to(ROOT)))
                report["checks"].append(view_report(page, view, label, selector))
            verify_timeline_filter(page, args.timeout_ms)
            filtered = capture(page, output_dir, "timeline-filtered")
            report["screenshots"].append(str(filtered.relative_to(ROOT)))
            report["checks"].append(
                {"view": "timeline-filtered", "status": "ok", "selector": "#activity-count"}
            )
            related_kind = verify_timeline_related_context(page, args.timeout_ms)
            related = capture(page, output_dir, "timeline-related")
            report["screenshots"].append(str(related.relative_to(ROOT)))
            report["checks"].append(
                {
                    "view": "timeline-related",
                    "status": "ok",
                    "kind": related_kind,
                    "selector": "Related Review Context",
                }
            )
        finally:
            browser.close()

    for path in report["screenshots"]:
        assert_nonblank_png(ROOT / path)
    report_path = output_dir / "dashboard_browser_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(report_path)


def find_browser() -> Path | None:
    for candidate in BROWSER_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def dashboard_url(base_url: str, workspace: str) -> str:
    params = {"limit": "100", "refresh": "0"}
    if workspace:
        params["workspace"] = workspace
    return f"{base_url.rstrip('/')}?{urlencode(params)}"


def load_dashboard(page: Page, base_url: str, workspace: str, timeout_ms: int) -> None:
    page.goto(dashboard_url(base_url, workspace), wait_until="domcontentloaded", timeout=timeout_ms)
    page.locator("#workspace option").first.wait_for(state="attached", timeout=timeout_ms)
    page.wait_for_function(
        "document.querySelector('#status')?.textContent.startsWith('Loaded')",
        timeout=timeout_ms,
    )
    expect_nonempty(page, "#metrics .metric", "Mission Control metrics", timeout_ms)


def click_view(page: Page, view: str, label: str, selector: str, timeout_ms: int) -> None:
    page.locator(f"button[data-view='{view}']").click(timeout=timeout_ms)
    page.locator(f"[data-view-panel='{view}']").wait_for(state="visible", timeout=timeout_ms)
    expect_nonempty(page, selector, label, timeout_ms)


def expect_nonempty(page: Page, selector: str, label: str, timeout_ms: int) -> None:
    locator = page.locator(selector)
    locator.first.wait_for(state="attached", timeout=timeout_ms)
    if locator.count() < 1:
        raise AssertionError(f"{label} rendered no matching nodes for {selector}")


def capture(page: Page, output_dir: Path, name: str) -> Path:
    path = output_dir / f"{name}.png"
    page.screenshot(path=path, full_page=True)
    return path


def view_report(page: Page, view: str, label: str, selector: str) -> dict[str, object]:
    return {
        "view": view,
        "label": label,
        "selector": selector,
        "count": page.locator(selector).count(),
        "status_text": page.locator("#status").inner_text(),
    }


def verify_timeline_filter(page: Page, timeout_ms: int) -> None:
    click_view(page, "timeline", "Timeline", "#timeline", timeout_ms)
    apply_activity_kind(page, "agent_action", timeout_ms)
    details = page.locator(".event-detail summary")
    if details.count():
        details.first.click()


def verify_timeline_related_context(page: Page, timeout_ms: int) -> str:
    click_view(page, "timeline", "Timeline", "#timeline", timeout_ms)
    for kind in ["changeset", "handoff_summary", "session"]:
        apply_activity_kind(page, kind, timeout_ms)
        summaries = page.locator(".event-detail summary")
        for index in range(min(summaries.count(), 5)):
            summaries.nth(index).click()
            try:
                page.get_by_text("Related Review Context", exact=True).first.wait_for(
                    state="visible",
                    timeout=2_000,
                )
                return kind
            except PlaywrightTimeoutError:
                continue
    raise AssertionError("No Timeline event exposed Related Review Context")


def apply_activity_kind(page: Page, kind: str, timeout_ms: int) -> None:
    page.locator("#activity-kind-filter").select_option(kind)
    page.locator("#activity-filter-apply").click()
    page.wait_for_function(
        "document.querySelector('#status')?.textContent.startsWith('Loaded')",
        timeout=timeout_ms,
    )
    page.wait_for_function(
        "document.querySelector('#activity-count')?.textContent.includes('filtered events')",
        timeout=timeout_ms,
    )
    page.wait_for_function(
        "kind => Array.from(document.querySelectorAll('.event .meta'))"
        ".some((node) => node.textContent.includes(kind))",
        arg=kind,
        timeout=timeout_ms,
    )


def assert_nonblank_png(path: Path) -> None:
    with Image.open(path) as image:
        grayscale = image.convert("L")
        stat = ImageStat.Stat(grayscale)
        low, high = stat.extrema[0]
        if high - low < 8:
            raise AssertionError(f"Screenshot appears blank: {path}")


if __name__ == "__main__":
    main()
