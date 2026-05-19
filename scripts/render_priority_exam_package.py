from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, JpegImagePlugin  # noqa: F401
from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parents[1]
PATENT = ROOT / "docs" / "patent"
EVIDENCE = PATENT / "공개사실증빙.pdf"
COVER_PDF = PATENT / "우선심사신청_설명서.pdf"
MERGED_PDF = PATENT / "우선심사신청_설명서_공개사실증빙_병합본.pdf"
PREVIEW = ROOT / "tmp" / "pdfs" / "priority_exam_package"
FONT_DIR = Path("C:/Windows/Fonts")
PAGE = (1240, 1754)
MARGIN = 92


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for candidate in [FONT_DIR / name, FONT_DIR / "malgun.ttf", FONT_DIR / "arial.ttf"]:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


F_TITLE = font("malgunbd.ttf", 46)
F_H1 = font("malgunbd.ttf", 30)
F_H2 = font("malgunbd.ttf", 24)
F_BODY = font("malgun.ttf", 22)
F_SMALL = font("malgun.ttf", 17)


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
    fill: str = "#111827",
    width: int = 980,
    gap: int = 8,
) -> int:
    x, y = xy
    for line in wrap(draw, text, fnt, width):
        draw.text((x, y), line, font=fnt, fill=fill)
        y += fnt.size + gap
    return y


def section(
    draw: ImageDraw.ImageDraw,
    y: int,
    number: str,
    heading: str,
    body: str,
    height: int = 235,
) -> int:
    draw.rounded_rectangle(
        (MARGIN, y, PAGE[0] - MARGIN, y + height),
        radius=14,
        fill="#f8fafc",
        outline="#cbd5e1",
        width=2,
    )
    draw.ellipse((MARGIN + 24, y + 28, MARGIN + 70, y + 74), fill="#1d4f8f")
    draw.text((MARGIN + 39, y + 34), number, font=F_SMALL, fill="#ffffff")
    draw.text((MARGIN + 92, y + 28), heading, font=F_H2, fill="#1d4f8f")
    draw_wrapped(
        draw,
        (MARGIN + 92, y + 76),
        body,
        F_BODY,
        "#111827",
        PAGE[0] - 2 * MARGIN - 128,
        7,
    )
    return y + height + 35


def render_cover() -> None:
    image = Image.new("RGB", PAGE, "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, PAGE[0], 238), fill="#182233")
    draw.text((MARGIN, 78), "우선심사신청 설명서", font=F_TITLE, fill="#ffffff")
    draw.text((MARGIN, 148), "증제1호증 공개사실 증빙서 병합본", font=F_H1, fill="#dbeafe")

    y = 285
    y = section(
        draw,
        y,
        "1",
        "우선심사 신청 이유",
        "본 출원은 '출원인이 출원된 발명을 업으로서 실시 중이거나 "
        "실시 준비 중인 출원'에 해당합니다.",
        190,
    )
    y = section(
        draw,
        y,
        "2",
        "실시 또는 실시 준비 중인 사실",
        "본 출원 발명의 핵심 프로토콜 및 아키텍처는 현재 출원인의 GitHub 저장소"
        "(https://github.com/geondongkim/geond-agent-protocol)를 통해 오픈소스로 배포되어 "
        "소프트웨어 코드, 문서, 실행 절차 및 대시보드 산출물 형태로 공개되어 있습니다. "
        "해당 저장소의 "
        "프로토콜, 공유 컨텍스트 저장소, 도메인 지식 그래프, 예약, 핸드오프 및 활동 가시화 구성은 "
        "실제 소프트웨어 환경에서 구동 및 검증되고 있습니다.",
        285,
    )
    y = section(
        draw,
        y,
        "3",
        "업으로서 실시 또는 실시 준비 중인 사실",
        "출원인은 위 공개 저장소를 통해 본 출원 발명을 적용한 Geond Agent Protocol을 배포하고, "
        "복수 AI 에이전트의 협업 충돌 방지와 컨텍스트 동기화를 위한 소프트웨어 프로토콜 및 "
        "운영 도구로 제품화 또는 서비스화를 준비하고 있습니다. 따라서 단순한 개인적·실험적 공개가 "
        "아니라 실제 소프트웨어 배포 및 사업적 실시 준비에 관한 자료입니다.",
        305,
    )
    y = section(
        draw,
        y,
        "4",
        "증빙 서류",
        "상세한 공개 저장소 URL, 공개 상태, 최초 commit, 관련 화면 캡처 및 공개 사실은 "
        "본 설명서 뒤에 "
        "병합된 '증제1호증(공개사실 증빙서)'를 참조하여 주시기 바랍니다.",
        225,
    )

    draw.text((MARGIN, PAGE[1] - 100), "작성일: 2026-05-19 KST", font=F_SMALL, fill="#64748b")
    draw.text((PAGE[0] - 170, PAGE[1] - 60), "- 1 -", font=F_SMALL, fill="#6b7280")

    PREVIEW.mkdir(parents=True, exist_ok=True)
    image.save(PREVIEW / "priority_exam_cover.png")
    image.save(COVER_PDF, resolution=150.0)


def merge_pdf() -> None:
    writer = PdfWriter()
    for source in [COVER_PDF, EVIDENCE]:
        reader = PdfReader(str(source))
        for page in reader.pages:
            writer.add_page(page)
    with MERGED_PDF.open("wb") as fh:
        writer.write(fh)


def main() -> None:
    Image.init()
    render_cover()
    merge_pdf()
    print(f"wrote {COVER_PDF}")
    print(f"wrote {MERGED_PDF}")
    print(f"preview: {PREVIEW / 'priority_exam_cover.png'}")


if __name__ == "__main__":
    main()
