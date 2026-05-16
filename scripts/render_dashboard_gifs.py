from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCREENSHOTS = ROOT / "tmp" / "dashboard_browser"
DEFAULT_OUTPUT = ROOT / "docs" / "assets"
WIDTH = 960
HEIGHT = 540
BG = (18, 24, 27)
TEXT = (238, 244, 241)
ACCENT = (11, 107, 99)

GIFS = {
    "geond_dashboard_operations.gif": [
        ("Mission Control", "mission.png"),
        ("Sessions", "sessions.png"),
        ("Handoffs", "handoffs.png"),
        ("Changesets", "changesets.png"),
    ],
    "geond_dashboard_evidence.gif": [
        ("Usage Evidence", "usage.png"),
        ("Code Risk", "code-risk.png"),
        ("Graph", "graph.png"),
    ],
    "geond_dashboard_timeline_review.gif": [
        ("Timeline", "timeline.png"),
        ("Filtered Timeline", "timeline-filtered.png"),
        ("Related Context", "timeline-related.png"),
        ("Relationships", "relationships.png"),
    ],
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Render dashboard workflow GIFs from screenshots.")
    parser.add_argument("--screenshots", type=Path, default=DEFAULT_SCREENSHOTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--duration", type=int, default=1300)
    args = parser.parse_args()

    screenshots = args.screenshots.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    title_font = load_font(28, bold=True)
    outputs: list[Path] = []
    for output_name, frames in GIFS.items():
        rendered = [
            render_frame(screenshots / file_name, title, title_font) for title, file_name in frames
        ]
        output = output_dir / output_name
        rendered[0].save(
            output,
            save_all=True,
            append_images=rendered[1:],
            duration=args.duration,
            loop=0,
            optimize=True,
        )
        outputs.append(output)
    for output in outputs:
        print(output)


def render_frame(path: Path, title: str, title_font: ImageFont.ImageFont) -> Image.Image:
    if not path.exists():
        raise FileNotFoundError(path)
    with Image.open(path) as source:
        source = source.convert("RGB")
        source.thumbnail((WIDTH, HEIGHT - 46), Image.Resampling.LANCZOS)
        image = Image.new("RGB", (WIDTH, HEIGHT), BG)
        x = (WIDTH - source.width) // 2
        y = 46 + (HEIGHT - 46 - source.height) // 2
        image.paste(source, (x, y))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 46), fill=ACCENT)
    draw.text((24, 8), f"Geond dashboard - {title}", fill=TEXT, font=title_font)
    return image


def load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/consolab.ttf" if bold else "C:/Windows/Fonts/consola.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


if __name__ == "__main__":
    main()
