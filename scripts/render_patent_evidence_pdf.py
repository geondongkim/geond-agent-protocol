from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, JpegImagePlugin  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
PATENT = ROOT / "docs" / "patent"
META = PATENT / "공개사실증빙_metadata.json"
OUT = PATENT / "공개사실증빙.pdf"
PREVIEW = ROOT / "tmp" / "pdfs" / "patent_evidence_pages"
FONT_DIR = Path("C:/Windows/Fonts")
PAGE = (1240, 1754)
MARGIN = 86


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for candidate in [FONT_DIR / name, FONT_DIR / "malgun.ttf", FONT_DIR / "arial.ttf"]:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


F_TITLE = font("malgunbd.ttf", 48)
F_H1 = font("malgunbd.ttf", 31)
F_H2 = font("malgunbd.ttf", 24)
F_BODY = font("malgun.ttf", 22)
F_SMALL = font("malgun.ttf", 17)
F_MONO = font("consola.ttf", 18)


def text_width(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), text, font=fnt)
    return box[2] - box[0]


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.ImageFont, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = word if not current else f"{current} {word}"
        if text_width(draw, candidate, fnt) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = word
        else:
            piece = ""
            for char in word:
                candidate = f"{piece}{char}"
                if text_width(draw, candidate, fnt) <= width:
                    piece = candidate
                else:
                    if piece:
                        lines.append(piece)
                    piece = char
            current = piece
    if current:
        lines.append(current)
    return lines


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    fnt: ImageFont.ImageFont,
    fill: str = "#1f2937",
    width: int = 980,
    gap: int = 8,
) -> int:
    x, y = xy
    for line in wrap(draw, text, fnt, width):
        draw.text((x, y), line, font=fnt, fill=fill)
        y += fnt.size + gap
    return y


def new_page(title: str, page_no: int) -> tuple[Image.Image, ImageDraw.ImageDraw, int]:
    image = Image.new("RGB", PAGE, "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, PAGE[0], 32), fill="#182233")
    draw.text((MARGIN, 66), title, font=F_H1, fill="#111827")
    draw.line((MARGIN, 118, PAGE[0] - MARGIN, 118), fill="#ccd5df", width=2)
    draw.text((PAGE[0] - 170, PAGE[1] - 60), f"- {page_no} -", font=F_SMALL, fill="#6b7280")
    return image, draw, 150


def card(draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int], heading: str) -> int:
    draw.rounded_rectangle(xy, radius=14, fill="#f8fafc", outline="#cbd5e1", width=2)
    draw.text((xy[0] + 28, xy[1] + 24), heading, font=F_H2, fill="#1d4f8f")
    return xy[1] + 72


def paste_image_fit(
    page: Image.Image,
    path: Path,
    box: tuple[int, int, int, int],
    label: str,
    draw: ImageDraw.ImageDraw,
) -> None:
    source = Image.open(path).convert("RGB")
    max_w = box[2] - box[0]
    max_h = box[3] - box[1]
    ratio = min(max_w / source.width, max_h / source.height)
    size = (int(source.width * ratio), int(source.height * ratio))
    resized = source.resize(size, Image.Resampling.LANCZOS)
    x = box[0] + (max_w - size[0]) // 2
    y = box[1]
    draw.rectangle(
        (box[0] - 8, box[1] - 8, box[2] + 8, box[3] + 8),
        fill="#f8fafc",
        outline="#cbd5e1",
        width=2,
    )
    page.paste(resized, (x, y))
    draw.text((box[0], box[3] + 22), label, font=F_SMALL, fill="#475569")


def load_metadata() -> dict:
    return json.loads(META.read_text(encoding="utf-8"))


def page_cover(meta: dict) -> Image.Image:
    image = Image.new("RGB", PAGE, "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, PAGE[0], 210), fill="#182233")
    draw.text((MARGIN, 78), "공개사실 증빙서", font=F_TITLE, fill="#ffffff")
    draw.text((MARGIN, 145), "Geond Agent Protocol 특허 출원 참고자료", font=F_H2, fill="#dbeafe")
    y = 285
    rows = [
        ("대상 저장소", meta["repository_url"]),
        ("공개 상태", meta["repository_visibility"]),
        ("저장소 생성 시각", meta["repository_created_at"]),
        ("최초 commit", meta["first_commit"]),
        ("최초 commit 시각", meta["first_commit_committer_date"]),
        ("증빙서 작성일", "2026-05-19 KST"),
    ]
    for key, value in rows:
        y = card(draw, (MARGIN, y, PAGE[0] - MARGIN, y + 118), key)
        draw_wrapped(draw, (MARGIN + 28, y), value, F_BODY, "#111827", PAGE[0] - 2 * MARGIN - 56, 6)
        y += 72
    note_y = card(draw, (MARGIN, 1265, PAGE[0] - MARGIN, 1535), "증빙 목적")
    draw_wrapped(
        draw,
        (MARGIN + 28, note_y),
        "본 PDF는 공개 GitHub 저장소의 공개 상태, 저장소 URL, 최초 commit 및 관련 화면 캡처를 "
        "공지예외주장 검토용으로 정리한 증빙자료입니다. 계정 토큰, 연결 문자열, subscription id "
        "또는 고객 데이터는 포함하지 않는 것을 원칙으로 합니다.",
        F_BODY,
        "#111827",
        PAGE[0] - 2 * MARGIN - 56,
    )
    draw.text((PAGE[0] - 170, PAGE[1] - 60), "- 1 -", font=F_SMALL, fill="#6b7280")
    return image


def page_metadata(meta: dict) -> Image.Image:
    image, draw, y = new_page("공개 사실 요약", 2)
    fields = [
        ("Repository URL", meta["repository_url"]),
        ("Visibility", meta["repository_visibility"]),
        ("Created at", meta["repository_created_at"]),
        ("First commit URL", meta["first_commit_url"]),
        ("First commit date", meta["first_commit_committer_date"]),
        ("First commit subject", meta["first_commit_subject"]),
        ("Remote main head at capture", meta["remote_main_head_at_capture"]),
    ]
    for key, value in fields:
        draw.text((MARGIN, y), key, font=F_H2, fill="#1d4f8f")
        y = draw_wrapped(draw, (MARGIN, y + 34), value, F_BODY, "#111827", PAGE[0] - 2 * MARGIN, 6)
        y += 28
    draw.text((MARGIN, y + 20), "보관 대상", font=F_H2, fill="#1d4f8f")
    y += 62
    evidence_items = [
        "GitHub public repository home screenshot",
        "GitHub first commit page screenshot",
        "Repository URL and first commit URL",
        "Metadata JSON containing capture time and commit identifiers",
        "Original screenshot PNG files used in this PDF",
    ]
    for item in evidence_items:
        draw.text((MARGIN, y), "-", font=F_BODY, fill="#111827")
        y = draw_wrapped(
            draw, (MARGIN + 24, y), item, F_BODY, "#111827", PAGE[0] - 2 * MARGIN - 24, 6
        )
        y += 10
    return image


def page_screenshot(path: Path, title: str, page_no: int, label: str) -> Image.Image:
    image, draw, _ = new_page(title, page_no)
    paste_image_fit(image, path, (MARGIN, 170, PAGE[0] - MARGIN, 1280), label, draw)
    y = 1360
    draw.text((MARGIN, y), "원본 파일", font=F_H2, fill="#1d4f8f")
    draw_wrapped(draw, (MARGIN, y + 38), str(path), F_MONO, "#111827", PAGE[0] - 2 * MARGIN, 6)
    return image


def page_evidence_files(meta: dict) -> Image.Image:
    image, draw, y = new_page("증빙 원본 및 확인 항목", 5)
    sections = [
        (
            "증명하려는 공개 사실",
            "대상 저장소가 public 상태로 공개되어 있었고, 저장소 생성 시각과 최초 commit 시각 및 "
            "최초 commit URL을 통해 공개 일자를 확인할 수 있다는 점입니다.",
        ),
        (
            "첨부 또는 보관할 원본",
            "GitHub 저장소 홈 화면 캡처, 최초 commit 화면 캡처, 각 캡처의 원본 PNG 파일, "
            "저장소 URL, 최초 commit URL 및 metadata JSON을 함께 보관합니다.",
        ),
        (
            "제출 전 확인",
            "증빙 화면에 비밀키, 연결 문자열, subscription id, 개인 계정 토큰, 고객 데이터가 "
            "노출되지 않았는지 다시 확인합니다.",
        ),
    ]
    for heading, text in sections:
        body_y = card(draw, (MARGIN, y, PAGE[0] - MARGIN, y + 205), heading)
        draw_wrapped(
            draw,
            (MARGIN + 28, body_y),
            text,
            F_BODY,
            "#111827",
            PAGE[0] - 2 * MARGIN - 56,
            7,
        )
        y += 240
    draw.text((MARGIN, y + 18), "관련 공개 commit", font=F_H2, fill="#1d4f8f")
    y += 62
    commits = [
        {
            "commit": meta["first_commit"][:7],
            "subject": meta["first_commit_subject"],
        },
        *[
            commit
            for commit in meta["latest_relevant_commits"]
            if commit["commit"] != "pending-local"
        ],
    ]
    for commit in commits:
        draw_wrapped(
            draw,
            (MARGIN, y),
            f"{commit['commit']}: {commit['subject']}",
            F_BODY,
            "#111827",
            PAGE[0] - 2 * MARGIN,
            6,
        )
        y += 48
    return image


def main() -> None:
    Image.init()
    meta = load_metadata()
    pages = [
        page_cover(meta),
        page_metadata(meta),
        page_screenshot(
            PATENT / "screenshots" / "github_repo_home_top.png",
            "GitHub 저장소 공개 화면",
            3,
            "공개 저장소 홈 화면 상단 캡처",
        ),
        page_screenshot(
            PATENT / "screenshots" / "github_first_commit_top.png",
            "최초 commit 공개 화면",
            4,
            "최초 commit 화면 상단 캡처",
        ),
        page_evidence_files(meta),
    ]
    PREVIEW.mkdir(parents=True, exist_ok=True)
    for idx, page in enumerate(pages, start=1):
        page.save(PREVIEW / f"page_{idx:02}.png")
    pages[0].save(OUT, save_all=True, append_images=pages[1:], resolution=150.0)
    print(f"wrote {OUT}")
    print(f"preview pages: {PREVIEW}")


if __name__ == "__main__":
    main()
