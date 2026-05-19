from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "patent" / "drawings"
FONT_DIR = Path("C:/Windows/Fonts")


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for candidate in [FONT_DIR / name, FONT_DIR / "malgun.ttf", FONT_DIR / "arial.ttf"]:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


F_TITLE = font("malgunbd.ttf", 45)
F_HEAD = font("malgunbd.ttf", 26)
F_SUBHEAD = font("malgunbd.ttf", 21)
F_BODY = font("malgun.ttf", 18)
F_SMALL = font("malgun.ttf", 15)
F_TINY = font("malgun.ttf", 13)


def text_width(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), text, font=fnt)
    return box[2] - box[0]


def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    fnt: ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if text_width(draw, candidate, fnt) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = word
        else:
            line = ""
            for char in word:
                candidate = f"{line}{char}"
                if text_width(draw, candidate, fnt) <= max_width:
                    line = candidate
                else:
                    if line:
                        lines.append(line)
                    line = char
            current = line
    if current:
        lines.append(current)
    return lines


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    fnt: ImageFont.ImageFont,
    fill: str,
    max_width: int,
    line_gap: int = 4,
) -> int:
    x, y = xy
    line_height = fnt.size + line_gap
    for line in wrap_text(draw, text, fnt, max_width):
        draw.text((x, y), line, font=fnt, fill=fill)
        y += line_height
    return y


def arrow(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    fill: str = "#172033",
    width: int = 3,
) -> None:
    draw.line([start, end], fill=fill, width=width)
    x1, y1 = end
    x0, y0 = start
    dx = x1 - x0
    dy = y1 - y0
    if abs(dx) >= abs(dy):
        direction = 1 if dx >= 0 else -1
        points = [(x1, y1), (x1 - 16 * direction, y1 - 9), (x1 - 16 * direction, y1 + 9)]
    else:
        direction = 1 if dy >= 0 else -1
        points = [(x1, y1), (x1 - 9, y1 - 16 * direction), (x1 + 9, y1 - 16 * direction)]
    draw.polygon(points, fill=fill)


def component_box(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    title: str,
    items: list[str],
    border: str,
    fill: str = "#fbfdff",
) -> None:
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle(xy, radius=12, outline=border, width=3, fill=fill)
    draw.text((x0 + 24, y0 + 24), title, font=F_HEAD, fill=border)
    row_y = y0 + 76
    row_h = 34
    for item in items:
        draw.rounded_rectangle(
            (x0 + 24, row_y, x1 - 24, row_y + row_h),
            radius=6,
            outline="#8aa7c8",
            width=1,
            fill="#ffffff",
        )
        draw_wrapped(draw, (x0 + 38, row_y + 7), item, F_SMALL, "#263445", x1 - x0 - 76, 2)
        row_y += row_h + 8


def save_pair(image: Image.Image, stem: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    color = OUT / f"{stem}.png"
    gray = OUT / f"{stem}_그레이스케일.jpg"
    image.save(color)
    image.convert("L").convert("RGB").save(gray, quality=95)


def render_architecture() -> None:
    image = Image.new("RGB", (1600, 950), "#f7f9fb")
    draw = ImageDraw.Draw(image)
    draw.text((60, 54), "도 1. 복수 AI 에이전트 협업 시스템 아키텍처", font=F_TITLE, fill="#16202b")

    blue = "#1f66a8"
    green = "#137a5b"
    purple = "#5c4a98"
    amber = "#9a6500"

    component_box(
        draw,
        (60, 135, 505, 485),
        "100 클라이언트/에이전트 계층",
        [
            "110 동종 에이전트 조합",
            "120 이종 업무 에이전트 조합",
            "130 고객지원/운영 에이전트",
            "140 코딩/리뷰/테스트/보안/문서화/배포",
        ],
        blue,
    )
    component_box(
        draw,
        (600, 135, 1045, 485),
        "200 프로토콜/정규화 계층",
        [
            "210 도구별 어댑터",
            "220 에이전트 인터페이스 및 CLI",
            "230 레드액션/프라이버시 정책",
            "240 컨텍스트 리뷰",
        ],
        blue,
    )
    component_box(
        draw,
        (1095, 135, 1540, 485),
        "300 도메인 지식 그래프 계층",
        [
            "310 AST/문서/업무 파서",
            "320 심볼/호출/업무 객체 그래프",
            "330 line-range/섹션 매핑",
            "340 evidence ref",
        ],
        blue,
    )
    component_box(
        draw,
        (60, 555, 525, 805),
        "400 협업 조정 계층",
        [
            "410 파일/심볼/업무 객체 예약",
            "420 advisory/strict/override 정책",
            "430 생성/갱신/해제/만료 이벤트",
        ],
        green,
    )
    component_box(
        draw,
        (625, 555, 1100, 805),
        "500 핸드오프/저장 계층",
        [
            "510 Handoff 객체",
            "520 의도/패치/검증/위험",
            "530 로컬 컨텍스트 저장소",
            "540 클라우드/원격 공유 저장소",
        ],
        purple,
    )
    component_box(
        draw,
        (1190, 555, 1540, 805),
        "600 활동 가시화 계층",
        [
            "610 DB source badge",
            "620 agent switchboard/lane",
            "630 sessions/timeline/lineage",
            "640 trace readiness",
        ],
        amber,
    )

    arrow(draw, (505, 310), (600, 310))
    arrow(draw, (1045, 310), (1095, 310))
    arrow(draw, (330, 485), (330, 555), green)
    arrow(draw, (825, 485), (825, 555), purple)
    arrow(draw, (1320, 485), (1320, 555), amber)
    arrow(draw, (525, 680), (625, 680))
    arrow(draw, (1100, 680), (1190, 680))

    draw_wrapped(
        draw,
        (60, 870),
        "참조부호: 100 에이전트 계층, 200 프로토콜/정규화, 300 도메인 지식 그래프, "
        "400 협업 조정, 500 핸드오프/저장, 600 활동 가시화",
        F_SMALL,
        "#64748b",
        1480,
    )
    save_pair(image, "도면1_시스템_아키텍처")


def pipeline_step(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    code: str,
    text: str,
    border: str,
) -> None:
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle(xy, radius=10, outline=border, width=3, fill="#fbfffd")
    draw.text((x0 + 18, y0 + 20), code, font=F_SUBHEAD, fill=border)
    draw_wrapped(draw, (x0 + 18, y0 + 62), text, F_SMALL, "#263445", x1 - x0 - 36, 3)


def render_pipeline() -> None:
    image = Image.new("RGB", (1500, 880), "#f7f9fb")
    draw = ImageDraw.Draw(image)
    draw.text(
        (55, 55),
        "도 2. 도메인 지식 그래프 기반 예약 및 핸드오프 파이프라인",
        font=F_TITLE,
        fill="#16202b",
    )
    green = "#137a5b"
    purple = "#5c4a98"
    amber = "#9a6500"
    blue = "#1f66a8"

    boxes = [
        ((55, 155, 245, 285), "S10", "자산/산출물 수집\n스냅샷/변경"),
        ((285, 155, 475, 285), "S20", "구조 파싱\n지식 그래프 생성"),
        ((515, 155, 705, 285), "S30", "변경 범위 매핑\n라인/섹션/객체"),
        ((745, 155, 935, 285), "S40", "의존성 기반\n예약 후보/설정"),
        ((975, 155, 1165, 285), "S50", "의도/패치/증거\nHandoff 패키징"),
        ((1205, 155, 1395, 285), "S60", "후속 검증\n예약 해제/갱신"),
    ]
    for xy, code, text in boxes:
        pipeline_step(draw, xy, code, text, green)
    for x in [245, 475, 705, 935, 1165]:
        arrow(draw, (x, 220), (x + 40, 220), green)

    draw.rounded_rectangle(
        (200, 430, 595, 590),
        radius=10,
        outline=amber,
        width=3,
        fill="#fffdf7",
    )
    draw.text((230, 455), "충돌 판단", font=F_HEAD, fill=amber)
    draw_wrapped(
        draw,
        (230, 505),
        "활성 예약, 열린 핸드오프, lineage 및 관련 evidence를 함께 평가하여 "
        "차단/경고/override 정책을 결정",
        F_SMALL,
        "#263445",
        320,
    )
    draw.rounded_rectangle(
        (745, 430, 1195, 590), radius=10, outline=purple, width=3, fill="#fbfaff"
    )
    draw.text((775, 455), "컨텍스트 동기화", font=F_HEAD, fill=purple)
    draw_wrapped(
        draw,
        (775, 505),
        "handoff_summaries, reservation_events, change_entities, code_edges 및 업무 객체 관계를 "
        "에이전트 인터페이스 리소스와 대시보드 read model로 노출",
        F_SMALL,
        "#263445",
        370,
    )

    arrow(draw, (380, 285), (380, 430), amber)
    arrow(draw, (595, 510), (745, 510), purple)
    arrow(draw, (970, 430), (970, 285), purple)

    draw.rounded_rectangle((210, 715, 1290, 775), radius=10, outline=blue, width=2, fill="#f4fbff")
    draw_wrapped(
        draw,
        (245, 734),
        "효과: 단순 파일 잠금이 놓치는 업무 산출물 및 코드 의존성 충돌을 "
        "객체/심볼/라인/핸드오프 문맥으로 완화",
        F_SMALL,
        blue,
        1000,
    )
    save_pair(image, "도면2_AST_예약_핸드오프_파이프라인")


def main() -> None:
    render_architecture()
    render_pipeline()


if __name__ == "__main__":
    main()
