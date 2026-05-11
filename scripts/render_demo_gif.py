from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "assets" / "geond_demo.gif"
WIDTH = 960
HEIGHT = 540
PADDING = 36
BG = (18, 24, 27)
PANEL = (25, 34, 38)
TEXT = (226, 232, 229)
MUTED = (146, 161, 158)
ACCENT = (88, 166, 255)
OK = (103, 204, 145)

FRAMES = [
    (
        "1. Seed shared memory",
        "$ uv run geond seed-sample\n"
        '{ "status": "ok", "workspace_id": "...", "messages": 2 }\n\n'
        "$ uv run geond search app_context --mode keyword\n"
        "codex/caution: build_answer uses app_context for retrieval smoke tests",
    ),
    (
        "2. Index code graph with tree-sitter",
        "$ uv run geond index-tree-sitter src --workspace-uri file:///repo --workspace-name repo\n"
        '{ "indexed_files": 24, "entities": 189, "edges": 214 }\n\n'
        "geond://symbols/build_answer -> service.build_answer, callers, imports",
    ),
    (
        "3. Import agent sessions",
        "$ uv run geond parse-claude-code tests/fixtures/claude_code --limit 1\n"
        '{ "source": "claude-code", "events": 6, "messages": 2 }\n\n'
        "$ uv run geond import-codex ~/.codex/sessions --limit 5 ...",
    ),
    (
        "4. Measure retrieval quality",
        "$ uv run geond benchmark-search app_context build_answer \\\n"
        "    --mode keyword --judgments examples/benchmarks/search_judgments.json --save\n"
        '{ "recall_at_k": 1.0, "mrr": 1.0, "ndcg_at_k": 1.0 }',
    ),
    (
        "5. Share through MCP",
        "Resources:\n"
        "  geond://sessions\n"
        "  geond://workspaces/{id}/timeline\n"
        "  geond://workspaces/{id}/reservations\n"
        "  geond://workspaces/{id}/handoffs\n\n"
        "Tools: search_dev_memory, get_symbol_context, reserve_symbols",
    ),
]


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    font = load_font(26)
    small = load_font(21)
    title_font = load_font(34, bold=True)
    frames = [render_frame(title, body, title_font, font, small) for title, body in FRAMES]
    frames[0].save(
        OUTPUT,
        save_all=True,
        append_images=frames[1:],
        duration=1500,
        loop=0,
        optimize=True,
    )
    print(OUTPUT)


def render_frame(
    title: str,
    body: str,
    title_font: ImageFont.ImageFont,
    font: ImageFont.ImageFont,
    small: ImageFont.ImageFont,
) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (PADDING, PADDING, WIDTH - PADDING, HEIGHT - PADDING),
        radius=12,
        fill=PANEL,
        outline=(50, 65, 70),
        width=2,
    )
    draw.text((PADDING + 28, PADDING + 24), "geond-agent-protocol", fill=ACCENT, font=small)
    draw.text((PADDING + 28, PADDING + 60), title, fill=TEXT, font=title_font)
    y = PADDING + 122
    for line in wrap_lines(body, max_chars=74):
        color = OK if line.startswith("{") or line.strip().startswith("geond://") else TEXT
        if line.startswith("$"):
            color = ACCENT
        if not line.strip():
            y += 12
            continue
        draw.text((PADDING + 32, y), line, fill=color, font=font)
        y += 34
    draw.text(
        (PADDING + 28, HEIGHT - PADDING - 42),
        "local-first memory, code graph, coordination, benchmark evidence",
        fill=MUTED,
        font=small,
    )
    return image


def wrap_lines(text: str, max_chars: int) -> list[str]:
    wrapped: list[str] = []
    for line in text.splitlines():
        if len(line) <= max_chars:
            wrapped.append(line)
            continue
        remaining = line
        while len(remaining) > max_chars:
            split_at = remaining.rfind(" ", 0, max_chars)
            if split_at < 1:
                split_at = max_chars
            wrapped.append(remaining[:split_at].rstrip())
            remaining = "  " + remaining[split_at:].lstrip()
        wrapped.append(remaining)
    return wrapped


def load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/consolab.ttf" if bold else "C:/Windows/Fonts/consola.ttf",
        "C:/Windows/Fonts/seguisb.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


if __name__ == "__main__":
    main()
