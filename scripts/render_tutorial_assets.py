from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from textwrap import wrap

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "learn" / "assets"
WIDTH = 960
HEIGHT = 540
BG = (15, 21, 27)
PANEL = (25, 35, 43)
PANEL_ALT = (34, 47, 57)
TEXT = (236, 242, 238)
MUTED = (157, 174, 170)
LINE = (74, 96, 110)
ACCENT = (74, 164, 156)
BLUE = (98, 151, 248)
WARN = (236, 180, 80)
OK = (104, 203, 145)


@dataclass(frozen=True)
class Step:
    title: str
    command: str
    result: str
    note: str


@dataclass(frozen=True)
class Lesson:
    output: str
    title: str
    objective: str
    steps: tuple[Step, ...]


LESSONS: tuple[Lesson, ...] = (
    Lesson(
        output="geond_lesson_01_local_memory.gif",
        title="Lesson 1: Local Shared Memory",
        objective="Bring up local Postgres, seed sample evidence, and query it through CLI/MCP.",
        steps=(
            Step(
                "Start local storage",
                "docker compose up -d postgres",
                "Postgres runs locally; no cloud account required.",
                "Geond starts local-first and can later switch profiles.",
            ),
            Step(
                "Apply schema and seed",
                "uv run geond seed-sample",
                "A sample workspace, session, and messages are inserted.",
                "The sample workspace is safe to purge after the lesson.",
            ),
            Step(
                "Search memory",
                "uv run geond search app_context --mode keyword",
                "Search returns compact snippets and evidence metadata.",
                "Keyword mode works without embedding credentials.",
            ),
            Step(
                "Smoke the MCP surface",
                "uv run geond mcp-smoke --format text --strict",
                "MCP initialize, resources, and search all respond.",
                "This mirrors the stdio shape used by external MCP clients.",
            ),
        ),
    ),
    Lesson(
        output="geond_lesson_02_handoff_reservation.gif",
        title="Lesson 2: Handoffs And Reservations",
        objective="Use review context, symbol reservations, and handoffs before parallel edits.",
        steps=(
            Step(
                "Review before editing",
                "uv run geond review-context <workspace> --symbol build_answer",
                "The agent sees reservations, handoffs, and lineage first.",
                "This is the preflight check before risky work.",
            ),
            Step(
                "Reserve active work",
                "uv run geond reserve-symbols <workspace> --symbol build_answer",
                "The symbol becomes visible as claimed work.",
                "Policies can be advisory, strict, or override-with-reason.",
            ),
            Step(
                "Check conflicts",
                "uv run geond conflicts <workspace> --symbol build_answer",
                "Another agent can detect overlap before editing.",
                "Conflict reads also clean expired reservations.",
            ),
            Step(
                "Leave a handoff",
                "uv run geond record-handoff <workspace> --next-action ...",
                "Next steps, tested commands, blockers, and risks persist.",
                "The next agent starts from a structured packet.",
            ),
        ),
    ),
    Lesson(
        output="geond_lesson_03_pair_coding.gif",
        title="Lesson 3: AI Pair Coding Workflow",
        objective="Let Agent A and Agent B share repo context without becoming one tool.",
        steps=(
            Step(
                "Agent A reads context",
                "search_dev_memory + review_workspace_context",
                "Agent A recovers prior decisions and current ownership.",
                "Any MCP-capable client can use this read path.",
            ),
            Step(
                "Agent B records work",
                "record_agent_action + record_changeset",
                "Agent B leaves durable evidence for the same workspace.",
                "CLI/importer paths can record work outside a live MCP client.",
            ),
            Step(
                "Both share handoffs",
                "reserve_files + record_handoff_summary",
                "The pair can divide work and preserve next actions.",
                "Verified example: Codex + Antigravity.",
            ),
            Step(
                "Reviewer sees one trail",
                "dashboard-overview + dashboard-events",
                "The dashboard links agents, sessions, work, and evidence refs.",
                "Geond is the shared substrate, not the agent runner.",
            ),
        ),
    ),
    Lesson(
        output="geond_lesson_04_team_db.gif",
        title="Lesson 4: Shared PostgreSQL Team Mode",
        objective="Point local Geond processes on multiple machines at one shared database.",
        steps=(
            Step(
                "Keep local tools",
                "uv run geond-mcp",
                "Each developer runs their own MCP server and dashboard.",
                "The MCP process does not need to be centrally hosted.",
            ),
            Step(
                "Switch database profile",
                "GEOND_DATABASE_PROFILE=azure",
                "The same commands use a shared PostgreSQL-compatible backend.",
                "Azure PostgreSQL is validated but optional.",
            ),
            Step(
                "Read another machine's work",
                "uv run geond dashboard-overview <workspace>",
                "Sessions, reservations, conflicts, handoffs, and events appear.",
                "No private transcript files need to be copied between machines.",
            ),
            Step(
                "Clean up cloud validation",
                "az group delete --name rg-geond-team-validate-<run-id>",
                "Temporary validation resources are deleted when the run ends.",
                "Shared DB tests must include cleanup evidence.",
            ),
        ),
    ),
)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    title_font = load_font(32, bold=True)
    subtitle_font = load_font(22, bold=True)
    body_font = load_font(21)
    mono_font = load_font(20, mono=True)
    small_font = load_font(17)
    for lesson in LESSONS:
        frames = [
            render_frame(
                lesson,
                index + 1,
                step,
                title_font,
                subtitle_font,
                body_font,
                mono_font,
                small_font,
            )
            for index, step in enumerate(lesson.steps)
        ]
        output = OUTPUT_DIR / lesson.output
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
    lesson: Lesson,
    index: int,
    step: Step,
    title_font: ImageFont.ImageFont,
    subtitle_font: ImageFont.ImageFont,
    body_font: ImageFont.ImageFont,
    mono_font: ImageFont.ImageFont,
    small_font: ImageFont.ImageFont,
) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((30, 26, WIDTH - 30, HEIGHT - 26), radius=10, fill=PANEL)
    draw.rectangle((30, 26, WIDTH - 30, 35), fill=ACCENT)
    draw.text((64, 58), "Geond learning path", font=small_font, fill=ACCENT)
    draw.text((64, 86), lesson.title, font=title_font, fill=TEXT)
    draw_wrapped(draw, (64, 132), lesson.objective, body_font, MUTED, width=78)
    draw_progress(draw, index, len(lesson.steps), small_font)

    y = 192
    y = card(draw, y, "Step", step.title, subtitle_font, body_font, BLUE)
    y = code_card(draw, y + 14, step.command, mono_font)
    y = card(draw, y + 14, "Expected result", step.result, subtitle_font, body_font, OK)
    card(draw, y + 14, "Safety note", step.note, subtitle_font, body_font, WARN)
    return image


def card(
    draw: ImageDraw.ImageDraw,
    y: int,
    label: str,
    text: str,
    label_font: ImageFont.ImageFont,
    body_font: ImageFont.ImageFont,
    accent: tuple[int, int, int],
) -> int:
    h = 70
    draw.rounded_rectangle((64, y, WIDTH - 64, y + h), radius=8, fill=PANEL_ALT)
    draw.rectangle((64, y, 70, y + h), fill=accent)
    draw.text((84, y + 12), label, font=label_font, fill=accent)
    draw_wrapped(draw, (254, y + 13), text, body_font, TEXT, width=54, line_gap=3)
    return y + h


def code_card(
    draw: ImageDraw.ImageDraw,
    y: int,
    command: str,
    font: ImageFont.ImageFont,
) -> int:
    h = 62
    draw.rounded_rectangle((64, y, WIDTH - 64, y + h), radius=8, fill=(18, 26, 32))
    draw.text((86, y + 18), f"$ {command}", font=font, fill=TEXT)
    return y + h


def draw_progress(
    draw: ImageDraw.ImageDraw,
    index: int,
    count: int,
    font: ImageFont.ImageFont,
) -> None:
    x = WIDTH - 244
    y = 72
    draw.text((x, y - 28), f"{index}/{count}", font=font, fill=MUTED)
    for offset in range(count):
        color = ACCENT if offset < index else LINE
        draw.rounded_rectangle(
            (x + offset * 42, y, x + offset * 42 + 30, y + 8),
            radius=4,
            fill=color,
        )


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
        y += int(getattr(font, "size", 18)) + line_gap
    return y


def load_font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.ImageFont:
    if mono:
        candidates = [
            "C:/Windows/Fonts/consolab.ttf" if bold else "C:/Windows/Fonts/consola.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        ]
    else:
        candidates = [
            "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
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
