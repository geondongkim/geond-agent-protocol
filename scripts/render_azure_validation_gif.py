from __future__ import annotations

import argparse
import json
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH = 960
HEIGHT = 540
BG = (14, 20, 28)
PANEL = (27, 37, 50)
ACCENT = (76, 154, 255)
OK = (67, 191, 128)
WARN = (245, 180, 72)
TEXT = (239, 244, 250)
MUTED = (158, 171, 187)


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/consolab.ttf" if bold else "C:/Windows/Fonts/consola.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font,
    fill=TEXT,
    width=76,
    line_gap=8,
):
    x, y = xy
    for paragraph in text.split("\n"):
        for line in textwrap.wrap(paragraph, width=width) or [""]:
            draw.text((x, y), line, font=font, fill=fill)
            y += font.size + line_gap
    return y


def status_color(status: str) -> tuple[int, int, int]:
    return OK if status == "ok" or status == "deleted" else WARN


def frame(title: str, subtitle: str, bullets: list[str], footer: str) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    title_font = load_font(38, bold=True)
    subtitle_font = load_font(22)
    bullet_font = load_font(24)
    footer_font = load_font(18)
    small_font = load_font(16)

    draw.rounded_rectangle((34, 30, WIDTH - 34, HEIGHT - 34), radius=8, fill=PANEL)
    draw.rectangle((34, 30, WIDTH - 34, 38), fill=ACCENT)
    draw.text((70, 72), title, font=title_font, fill=TEXT)
    draw.text((70, 124), subtitle, font=subtitle_font, fill=MUTED)

    y = 184
    for bullet in bullets:
        draw.ellipse((72, y + 8, 88, y + 24), fill=ACCENT)
        y = draw_wrapped(draw, (106, y), bullet, bullet_font, width=62)
        y += 12

    draw.text((70, HEIGHT - 82), footer, font=footer_font, fill=MUTED)
    draw.text((WIDTH - 235, HEIGHT - 82), "geond-agent-protocol", font=small_font, fill=ACCENT)
    return image


def read_summary(run_dir: Path) -> dict:
    with (run_dir / "summary.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_slm(run_dir: Path) -> dict | None:
    path = run_dir / "slm_vm_benchmark.json"
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def step(summary: dict, name: str) -> dict | None:
    for item in summary.get("steps", []):
        if item.get("name") == name:
            return item
    return None


def make_frames(summary: dict, slm: dict | None) -> list[Image.Image]:
    cleanup = summary.get("cleanup", {})
    resources = summary.get("resources", [])
    aoai = step(summary, "azure-openai-geond-benchmark")
    apim = step(summary, "apply-apim-ai-gateway-policy") or step(summary, "create-apim-consumption")
    vm = step(summary, "vm-slm-multilingual-benchmark")

    frames = [
        frame(
            "Azure validation smoke",
            f"Run {summary.get('run_id')} in {summary.get('location')}",
            [
                f"Temporary resource group: {summary.get('resource_group')}",
                f"Resources created: {len(resources)}",
                f"Cleanup status: {cleanup.get('status')}",
            ],
            "All Azure resources are created inside one tagged group, then deleted.",
        ),
        frame(
            "Azure OpenAI embeddings",
            "Geond benchmark through a real text-embedding-3-small deployment",
            [
                f"Status: {(aoai or {}).get('status', 'not-run')}",
                f"Embedded messages: {((aoai or {}).get('details') or {}).get('embedded', 'n/a')}",
                "Benchmark report saved as azure_openai_benchmark.md",
            ],
            "Provider mode: GEOND_EMBEDDING_PROVIDER=azure-openai",
        ),
        frame(
            "APIM gateway sample",
            "Consumption gateway and policy deployment smoke",
            [
                f"Status: {(apim or {}).get('status', 'not-run')}",
                "Backends/API/policy commands are recorded in summary.json",
                "Policy covers managed identity auth, rate limits, cache, and AI safety hooks.",
            ],
            "Failures are kept as evidence when a preview policy is not accepted.",
        ),
        frame(
            "Local multilingual SLM",
            "B2s Ubuntu VM benchmark with MiniLM multilingual embeddings",
            [
                f"Status: {(vm or {}).get('status', 'not-run')}",
                f"MRR: {(slm or {}).get('mrr', 'n/a')}",
                (
                    f"Load seconds: {(slm or {}).get('load_seconds', 'n/a')}, "
                    f"encode seconds: {(slm or {}).get('encode_seconds', 'n/a')}"
                ),
            ],
            "Cost signal: Standard_B2s Linux Korea Central at 0.052 USD/hour.",
        ),
        frame(
            "Evidence packaged",
            "Artifacts are ready for review and future cost modeling",
            [
                "summary.json: resources, durations, cleanup status",
                "azure_openai_benchmark.md: retrieval benchmark output",
                "slm_vm_benchmark.json: VM embedding benchmark metrics",
            ],
            f"Final cleanup: {cleanup.get('status')}",
        ),
    ]
    return frames


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render an Azure validation GIF from a run directory."
    )
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    summary = read_summary(run_dir)
    slm = read_slm(run_dir)
    frames = make_frames(summary, slm)
    output = args.output or run_dir / "geond_azure_validation.gif"
    frames[0].save(output, save_all=True, append_images=frames[1:], duration=1300, loop=0)
    print(output)


if __name__ == "__main__":
    main()
