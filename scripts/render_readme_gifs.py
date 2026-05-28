from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from textwrap import wrap

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "docs" / "assets"
WIDTH = 960
HEIGHT = 540
BG = (16, 22, 28)
PANEL = (24, 34, 42)
PANEL_ALT = (32, 44, 54)
LINE = (74, 95, 108)
TEXT = (236, 242, 238)
MUTED = (158, 174, 170)
ACCENT = (71, 161, 153)
ACCENT_2 = (95, 151, 255)
WARN = (237, 178, 74)
OK = (96, 202, 142)


@dataclass(frozen=True)
class Frame:
    title: str
    surface: str
    shared_state: str
    outcome: str


@dataclass(frozen=True)
class Scenario:
    output: str
    label: str
    summary: str
    frames: tuple[Frame, ...]


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        output="geond_readme_pair_coding.gif",
        label="AI pair coding across agent tools",
        summary="Different agent tools can share one repo-aware evidence layer.",
        frames=(
            Frame(
                title="1. Agent A reads context",
                surface="Agent surface: any MCP-capable assistant",
                shared_state=(
                    "Shared state: search_dev_memory, review_workspace_context, dashboard overview"
                ),
                outcome=(
                    "Visible outcome: the agent sees prior sessions and active "
                    "coordination state before work starts."
                ),
            ),
            Frame(
                title="2. Agent B records work",
                surface="Agent surface: CLI agent, editor agent, or imported transcript",
                shared_state="Shared state: sessions, messages, changesets, benchmark rows",
                outcome=(
                    "Visible outcome: Agent B activity becomes searchable evidence "
                    "for the next agent."
                ),
            ),
            Frame(
                title="3. Both agents coordinate ownership",
                surface="Agent surface: MCP tools or CLI wrappers",
                shared_state=(
                    "Shared state: file reservations, symbol reservations, context review, handoffs"
                ),
                outcome=(
                    "Visible outcome: agents can split work without losing "
                    "current ownership and risk context."
                ),
            ),
            Frame(
                title="4. Reviewer sees one evidence trail",
                surface=(
                    "Agent surface: record_agent_action, record_changeset, record_handoff_summary"
                ),
                shared_state="Shared state: lineage graph, timeline, reservations, open handoffs",
                outcome=(
                    "Visible outcome: reviewers can see who did what, why, "
                    "and what should happen next."
                ),
            ),
        ),
    ),
    Scenario(
        output="geond_readme_team_db.gif",
        label="Multi-PC shared PostgreSQL profile",
        summary="Each machine runs local tools while reading the same shared database.",
        frames=(
            Frame(
                title="1. Windows runs local Geond",
                surface="Agent surface: Codex, VS Code, or CLI on one developer machine",
                shared_state=(
                    "Shared state: local geond-mcp and dashboard point at GEOND_DATABASE_URL"
                ),
                outcome=(
                    "Visible outcome: local workflows work offline against "
                    "Docker PostgreSQL by default."
                ),
            ),
            Frame(
                title="2. Switch to an Azure profile",
                surface="Agent surface: same commands, profile-specific database URL",
                shared_state=(
                    "Shared state: GEOND_DATABASE_PROFILE=azure, "
                    "AZURE_GEOND_DATABASE_URL=postgresql://..."
                ),
                outcome=(
                    "Visible outcome: the same MCP server now reads and writes "
                    "shared PostgreSQL memory."
                ),
            ),
            Frame(
                title="3. Another PC sees the same work",
                surface="Agent surface: MacBook, teammate laptop, or CI/PM agent",
                shared_state=(
                    "Shared state: sessions, reservations, conflicts, handoffs, dashboard events"
                ),
                outcome=(
                    "Visible outcome: the second machine sees Windows-created "
                    "evidence without copying transcripts."
                ),
            ),
            Frame(
                title="4. Dashboard labels the source",
                surface="Agent surface: read-only browser dashboard on each machine",
                shared_state="Shared state: safe source metadata, no password or token display",
                outcome=(
                    "Visible outcome: reviewers know whether they are looking "
                    "at local, Azure, or remote PostgreSQL."
                ),
            ),
        ),
    ),
    Scenario(
        output="geond_readme_review_loop.gif",
        label="Reviewer and PM dashboard loop",
        summary="A human can review agent work without reading raw MCP JSON.",
        frames=(
            Frame(
                title="1. Mission Control",
                surface="Agent surface: dashboard-overview and get_dashboard_overview",
                shared_state="Shared state: active agents, sessions, reservations, latest actions",
                outcome="Visible outcome: a PM sees ownership and blockers in seconds.",
            ),
            Frame(
                title="2. Handoffs",
                surface="Agent surface: record_handoff_summary and list_handoff_summaries",
                shared_state="Shared state: next action, tested commands, blockers, risks",
                outcome=(
                    "Visible outcome: the next agent or reviewer starts from a structured packet."
                ),
            ),
            Frame(
                title="3. Code Risk",
                surface="Agent surface: code graph, changesets, file and symbol reservations",
                shared_state="Shared state: hot files, touched symbols, fan-out, open claims",
                outcome="Visible outcome: reviewers spot risky overlap before another edit lands.",
            ),
            Frame(
                title="4. Timeline evidence",
                surface="Agent surface: sessions, actions, changesets, benchmarks, usage evidence",
                shared_state="Shared state: compact snippets and evidence refs with detail paths",
                outcome=(
                    "Visible outcome: the review trail stays inspectable without "
                    "flooding the LLM context."
                ),
            ),
        ),
    ),
)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    title_font = load_font(34, bold=True)
    subtitle_font = load_font(22, bold=True)
    body_font = load_font(21)
    small_font = load_font(17)

    for scenario in SCENARIOS:
        frames = [
            render_frame(
                scenario,
                index + 1,
                frame,
                title_font,
                subtitle_font,
                body_font,
                small_font,
            )
            for index, frame in enumerate(scenario.frames)
        ]
        output = OUTPUT_DIR / scenario.output
        frames[0].save(
            output,
            save_all=True,
            append_images=frames[1:],
            duration=1450,
            loop=0,
            optimize=True,
        )
        print(output)


def render_frame(
    scenario: Scenario,
    frame_number: int,
    frame: Frame,
    title_font: ImageFont.ImageFont,
    subtitle_font: ImageFont.ImageFont,
    body_font: ImageFont.ImageFont,
    small_font: ImageFont.ImageFont,
) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((30, 26, WIDTH - 30, HEIGHT - 26), radius=10, fill=PANEL)
    draw.rectangle((30, 26, WIDTH - 30, 35), fill=ACCENT)
    draw.text((64, 58), "geond-agent-protocol", font=small_font, fill=ACCENT)
    draw.text((64, 84), scenario.label, font=title_font, fill=TEXT)
    draw_wrapped(draw, (64, 130), scenario.summary, body_font, MUTED, width=76, line_gap=4)

    draw_progress(draw, frame_number, len(scenario.frames), small_font)

    y = 186
    y = draw_card(draw, 64, y, "Frame", frame.title, subtitle_font, body_font, ACCENT_2)
    y = draw_card(draw, 64, y + 14, "Agent surface", frame.surface, subtitle_font, body_font, WARN)
    y = draw_card(
        draw,
        64,
        y + 14,
        "Shared state",
        frame.shared_state,
        subtitle_font,
        body_font,
        ACCENT,
    )
    y = draw_card(draw, 64, y + 14, "Visible outcome", frame.outcome, subtitle_font, body_font, OK)

    return image


def draw_progress(
    draw: ImageDraw.ImageDraw,
    frame_number: int,
    frame_count: int,
    small_font: ImageFont.ImageFont,
) -> None:
    x = WIDTH - 244
    y = 70
    draw.text((x, y - 28), f"{frame_number}/{frame_count}", font=small_font, fill=MUTED)
    for index in range(frame_count):
        color = ACCENT if index < frame_number else LINE
        draw.rounded_rectangle(
            (x + index * 42, y, x + index * 42 + 30, y + 8),
            radius=4,
            fill=color,
        )


def draw_card(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    label: str,
    text: str,
    label_font: ImageFont.ImageFont,
    body_font: ImageFont.ImageFont,
    accent: tuple[int, int, int],
) -> int:
    card_h = 72
    draw.rounded_rectangle((x, y, WIDTH - 64, y + card_h), radius=8, fill=PANEL_ALT)
    draw.rectangle((x, y, x + 6, y + card_h), fill=accent)
    draw.text((x + 20, y + 12), label, font=label_font, fill=accent)
    draw_wrapped(draw, (x + 190, y + 13), text, body_font, TEXT, width=58, line_gap=3)
    return y + card_h


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    width: int,
    line_gap: int = 6,
) -> int:
    x, y = xy
    for line in wrap(text, width=width):
        draw.text((x, y), line, font=font, fill=fill)
        y += font_size(font) + line_gap
    return y


def font_size(font: ImageFont.ImageFont) -> int:
    return int(getattr(font, "size", 18))


def load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/consolab.ttf" if bold else "C:/Windows/Fonts/consola.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


if __name__ == "__main__":
    main()
