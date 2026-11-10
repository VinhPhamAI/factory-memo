"""Canned AI proposals for the ticket board demo.

The demo shows the ticket flow before the real proposal service exists. A
proposal is a fixed Vietnamese template picked by keywords in the ticket's
title and situation. It reads no knowledge base, calls no model and is
labelled as a demo that an expert must confirm. It is stored as a marked block
at the end of `context.proposed_fix`; text before the marker is the user's own.
"""

from __future__ import annotations

from backend.src.storage.inquiries import DEMO_PROPOSAL_MARKER

MAX_PROPOSED_FIX = 4000

_SAFETY = "1. Dừng máy, ngắt nguồn và khóa an toàn (LOTO) trước khi kiểm tra."
_CONFIRM = "Cần chuyên gia xác nhận trước khi áp dụng."

# (keywords, steps after the safety step); the first template with a keyword wins.
_TEMPLATES: list[tuple[tuple[str, ...], list[str]]] = [
    (
        ("rò rỉ", "chảy dầu", "xì dầu", "xì hơi", "leak"),
        [
            "Xác định vị trí rò: lau sạch khu vực, chạy thử ngắn và quan sát điểm rỉ.",
            "Kiểm tra gioăng, phớt và các đầu nối ống tại vị trí rò; siết lại theo lực siết quy định.",
            "Nếu gioăng/phớt hỏng, thay mới đúng mã linh kiện của thiết bị.",
            "Kiểm tra mức dầu/chất lỏng và bổ sung nếu thiếu.",
            "Chạy thử 15 phút, xác nhận không còn rò.",
        ],
    ),
    (
        ("rung", "kêu to", "tiếng ồn", "tiếng lạ", "vibration"),
        [
            "Kiểm tra bu lông chân đế và giá đỡ; siết lại nếu lỏng.",
            "Kiểm tra độ đồng tâm khớp nối giữa động cơ và trục tải.",
            "Kiểm tra vòng bi: nghe tiếng, đo nhiệt độ, kiểm tra độ rơ.",
            "Kiểm tra cánh/puly có bám bẩn hoặc mất cân bằng; vệ sinh nếu cần.",
            "Chạy thử và so sánh mức rung với lần đo gần nhất.",
        ],
    ),
    (
        ("quá nhiệt", "nóng", "nhiệt độ cao", "overheat"),
        [
            "Kiểm tra quạt làm mát và lưới tản nhiệt; vệ sinh bụi bẩn.",
            "Kiểm tra mức và lưu lượng chất làm mát/dầu bôi trơn.",
            "Đo dòng điện động cơ khi chạy, so với dòng định mức.",
            "Kiểm tra cảm biến nhiệt độ và giá trị cài đặt ngưỡng báo động.",
            "Chạy thử có tải và theo dõi nhiệt độ trong 30 phút.",
        ],
    ),
    (
        ("mã lỗi", "báo lỗi", "alarm", "lỗi e", "error"),
        [
            "Ghi lại mã lỗi, thời điểm và thao tác ngay trước khi lỗi xuất hiện.",
            "Tra mã lỗi trong sổ tay thiết bị để xác định nhóm nguyên nhân.",
            "Kiểm tra dây và giắc cắm cảm biến liên quan đến mã lỗi.",
            "Reset lỗi theo đúng quy trình của nhà sản xuất, rồi chạy thử.",
            "Nếu lỗi lặp lại, giữ nguyên hiện trạng và chụp màn hình lỗi cho chuyên gia.",
        ],
    ),
    (
        ("không chạy", "không khởi động", "dừng đột ngột", "mất nguồn", "không lên"),
        [
            "Kiểm tra nguồn cấp, aptomat và cầu chì của tủ điện.",
            "Kiểm tra nút dừng khẩn cấp và các khóa liên động an toàn.",
            "Kiểm tra màn hình điều khiển có báo lỗi hay không.",
            "Kiểm tra rơ le nhiệt và contactor của động cơ.",
            "Cấp lại nguồn và khởi động theo đúng trình tự.",
        ],
    ),
]

_GENERIC = [
    "Mô tả lại hiện tượng: thời điểm, tần suất, điều kiện vận hành khi xảy ra.",
    "Kiểm tra trực quan: lỏng, nứt, bẩn, rò rỉ, dây hoặc giắc cắm bất thường.",
    "Đối chiếu với lịch sử bảo trì và các sự cố tương tự trên cùng thiết bị.",
    "Chụp ảnh/video hiện trạng và đính kèm vào ticket.",
]


def demo_proposal_text(title: str, situation: str) -> str:
    """Return the canned proposal for a ticket, chosen by keywords in its title and situation."""

    text = f"{title}\n{situation}".lower()
    steps = next(
        (steps for keywords, steps in _TEMPLATES if any(word in text for word in keywords)),
        _GENERIC,
    )
    numbered = [_SAFETY, *(f"{index}. {step}" for index, step in enumerate(steps, start=2))]
    return "\n".join(["Các bước kiểm tra đề xuất:", *numbered, "", _CONFIRM])


def with_demo_proposal(proposed_fix: str, title: str, situation: str) -> str:
    """Replace the demo block of `proposed_fix` with a fresh one; keep the user's own text.

    Args:
        proposed_fix: The ticket's current proposed fix, possibly holding a previous block.
        title: Ticket title used to pick the template.
        situation: Ticket situation used to pick the template.

    Returns:
        The user's text, then the marked demo block, cut to the field limit
        while the marker and the block stay whole.
    """

    block = f"{DEMO_PROPOSAL_MARKER}\n{demo_proposal_text(title, situation)}"
    own = proposed_fix.split(DEMO_PROPOSAL_MARKER)[0].rstrip()
    room = MAX_PROPOSED_FIX - len(block) - 2
    own = own[:max(room, 0)].rstrip()
    return f"{own}\n\n{block}" if own else block
