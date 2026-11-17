"""Activity services & offline-testable HTML/WS parsers for Moodle course activities."""

from __future__ import annotations

from dataclasses import dataclass
import html
import logging
import re
from typing import Any, Optional
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from core.courses.models import CourseModuleItem, CourseSource
from core.html_compat import BS4_PARSER
from core.security import HTMLSanitizer

logger = logging.getLogger(__name__)

_SAFE_SCHEME_SET = frozenset({"http", "https"})


def _safe_http_url(url: Any, fallback: str = "") -> str:
    candidate = str(url or "").strip()
    if not candidate or any(ord(ch) < 32 or ord(ch) == 127 for ch in candidate):
        return fallback
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() in _SAFE_SCHEME_SET and parsed.netloc:
        return candidate
    return fallback


def _same_origin(url_a: str, url_b: str) -> bool:
    pa = urlsplit(url_a)
    pb = urlsplit(url_b)
    return (
        bool(pa.netloc)
        and pa.scheme.lower() == pb.scheme.lower()
        and pa.netloc.lower() == pb.netloc.lower()
    )


def _clean_text(node: Any) -> str:
    if node is None:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


@dataclass(frozen=True, slots=True)
class AttendanceRecord:
    """Single row in a Moodle ``mod_attendance`` student history table."""

    date_str: str
    description: str
    status: str
    points: str
    remarks: str
    can_submit: bool = False
    submit_url: str = ""


@dataclass(frozen=True, slots=True)
class AttendanceSummary:
    """Normalized attendance report and optional self-check-in form payload."""

    cmid: int
    title: str
    taken_sessions: str = ""
    points_over_taken: str = ""
    percentage: str = ""
    records: tuple[AttendanceRecord, ...] = ()
    submit_form: Optional[dict[str, str]] = None
    submit_action_url: str = ""
    status_options: tuple[tuple[str, str], ...] = ()
    requires_password: bool = False
    status_message: str = ""


@dataclass(frozen=True, slots=True)
class CourseGroupEntry:
    """Single group option inside ``mod_groupselect``, ``mod_choicegroup``, or ``mod_choice``."""

    group_id: str
    name: str
    description: str = ""
    member_count_label: str = ""
    members: tuple[str, ...] = ()
    is_member: bool = False
    can_join: bool = False
    can_leave: bool = False
    action_url: str = ""
    action_fields: tuple[tuple[str, str], ...] = ()
    choice_option_id: Optional[int] = None


@dataclass(frozen=True, slots=True)
class GroupActivitySummary:
    """Unified DTO for self-enrollment groups (``groupselect``/``choicegroup``/``choice``)."""

    cmid: int
    modname: str
    title: str
    intro: str = ""
    my_group_names: tuple[str, ...] = ()
    groups: tuple[CourseGroupEntry, ...] = ()
    is_radio_choice: bool = False
    submit_url: str = ""
    submit_fields: tuple[tuple[str, str], ...] = ()
    message: str = ""


@dataclass(frozen=True, slots=True)
class QuizAttemptInfo:
    """Student's single quiz attempt summary."""

    attempt_number: int
    attempt_id: int
    state: str
    score_text: str = ""
    time_submitted: str = ""
    review_url: str = ""


@dataclass(frozen=True, slots=True)
class QuizActivitySummary:
    """Metadata, access rules, and previous attempts for ``mod_quiz``."""

    cmid: int
    quiz_id: int
    title: str
    intro: str = ""
    time_open: int = 0
    time_close: int = 0
    time_limit_seconds: int = 0
    max_attempts: int = 0
    grade_method: str = ""
    access_rules: tuple[str, ...] = ()
    prevent_reasons: tuple[str, ...] = ()
    attempts: tuple[QuizAttemptInfo, ...] = ()
    can_attempt_in_webview: bool = True
    web_url: str = ""


@dataclass(frozen=True, slots=True)
class ForumPostEntry:
    """Single post inside a forum discussion thread."""

    post_id: int
    author: str
    created_at: str
    subject: str
    message_text: str
    attachments: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ForumDiscussionEntry:
    """Discussion topic row inside a Moodle forum."""

    discussion_id: int
    name: str
    author: str
    created_at: str
    num_replies: int = 0
    pinned: bool = False
    message_preview: str = ""
    discussion_url: str = ""


@dataclass(frozen=True, slots=True)
class ForumActivitySummary:
    """Forum metadata and discussions list."""

    cmid: int
    forum_id: int
    title: str
    intro: str = ""
    discussions: tuple[ForumDiscussionEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class BookChapterEntry:
    """Single chapter inside a Moodle ``mod_book`` activity."""

    chapter_id: str
    title: str
    content_markdown: str
    subchapter: bool = False


@dataclass(frozen=True, slots=True)
class RichActivityPage:
    """Rendered readable chapters/HTML for ``mod_page``, ``mod_label``, or ``mod_book``."""

    cmid: int
    modname: str
    title: str
    intro_markdown: str = ""
    chapters: tuple[BookChapterEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class QuizReviewChoice:
    """Single answer option or matching pair inside a Moodle quiz attempt review."""

    label: str
    text: str
    is_selected: bool = False
    state: str = "neutral"  # "correct" | "incorrect" | "partial" | "neutral"
    feedback: str = ""


@dataclass(frozen=True, slots=True)
class QuizReviewQuestion:
    """Structured question item extracted from Moodle ``mod_quiz`` review HTML/WS."""

    display_number: str
    slot: str
    question_code: str = ""
    qtype: str = ""
    state_kind: str = "neutral"  # "correct" | "incorrect" | "partial" | "complete" | "unanswered"
    status_label: str = ""
    mark: str = ""
    max_mark: str = ""
    question_text: str = ""
    choices: tuple[QuizReviewChoice, ...] = ()
    student_response_text: str = ""
    right_answer_text: str = ""
    specific_feedback: str = ""
    general_feedback: str = ""


@dataclass(frozen=True, slots=True)
class QuizAttemptReviewReport:
    """Complete parsed quiz attempt review with score metrics and ordered questions."""

    attempt_id: int
    attempt_number: int = 1
    state_label: str = ""
    grade_text: str = ""
    raw_score_text: str = ""
    duration_text: str = ""
    finished_at_text: str = ""
    correct_count: int = 0
    incorrect_count: int = 0
    partial_count: int = 0
    unanswered_count: int = 0
    questions: tuple[QuizReviewQuestion, ...] = ()


_NON_READABLE_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "canvas",
    "head",
    "title",
    "meta",
    "link",
    "iframe",
    "object",
    "embed",
)
_GIFT_PREFIX_RE = re.compile(
    r"^\s*::([^:\r\n]{1,120})::(?:\s*\[(?:html|markdown|moodle|plain)\])?\s*",
    re.IGNORECASE,
)
_FORMAT_TAG_PREFIX_RE = re.compile(
    r"^\s*\[(?:html|markdown|moodle|plain)\]\s*",
    re.IGNORECASE,
)
_LONG_UNDERSCORE_RE = re.compile(r"_{4,}")
_RIGHT_ANSWER_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"the\s+correct\s+answers?\s+(?:is|are)"
    r"|correct\s+answers?"
    r"|câu\s+trả\s+lời\s+đúng\s+là"
    r"|các\s+câu\s+trả\s+lời\s+đúng\s+là"
    r"|đáp\s+án\s+đúng\s+là"
    r")\s*[:：\-]?\s*",
    re.IGNORECASE,
)


def clean_moodle_question_text(raw_text: str) -> tuple[str, str]:
    """Strip GIFT ``::CODE::[html]`` artifacts and normalize blanks; return ``(text, code)``."""
    text = html.unescape(str(raw_text or "")).strip()
    if not text:
        return "", ""
    code = ""
    match = _GIFT_PREFIX_RE.match(text)
    if match:
        code = " ".join(match.group(1).split())
        text = text[match.end() :].lstrip()
    text = _FORMAT_TAG_PREFIX_RE.sub("", text).strip()
    text = _LONG_UNDERSCORE_RE.sub(" ______ ", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    cleaned = "\n".join(line for line in lines if line).strip()
    return cleaned, code


def _html_to_readable_markdown(raw_html: str) -> str:
    raw_soup = BeautifulSoup(raw_html or "", BS4_PARSER)
    for junk in list(raw_soup.find_all(_NON_READABLE_TAGS)):
        if not getattr(junk, "decomposed", False):
            junk.decompose()
    HTMLSanitizer.sanitize_soup(raw_soup)
    soup = raw_soup
    lines: list[str] = []
    for el in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "blockquote"]):
        txt = _clean_text(el)
        if not txt:
            continue
        tag = el.name.lower()
        if tag in {"h1", "h2"}:
            lines.append(f"## {txt}")
        elif tag in {"h3", "h4"}:
            lines.append(f"### {txt}")
        elif tag == "li":
            lines.append(f"- {txt}")
        elif tag == "pre":
            lines.append(f"```\n{el.get_text()}\n```")
        elif tag == "blockquote":
            lines.append(f"> {txt}")
        else:
            lines.append(txt)
    if not lines:
        fallback = " ".join(soup.get_text("\n", strip=True).split())
        return fallback
    return "\n\n".join(lines)


def _normalize_match_key(text: str) -> str:
    cleaned, _ = clean_moodle_question_text(text)
    lowered = " ".join(cleaned.lower().split()).rstrip(".。;；,， ")
    return lowered


def _format_score_num(val: Any) -> str:
    if val is None or isinstance(val, bool) or val == "":
        return ""
    try:
        num = float(val)
    except (TypeError, ValueError):
        return str(val).strip()
    if abs(num - round(num)) < 1e-6:
        return str(int(round(num)))
    return f"{num:.2f}".rstrip("0").rstrip(".")


def _format_duration_human(seconds: int) -> str:
    if seconds <= 0:
        return ""
    hours, rem = divmod(int(seconds), 3600)
    mins, secs = divmod(rem, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} giờ")
    if mins:
        parts.append(f"{mins} phút")
    if secs and not hours:
        parts.append(f"{secs} giây")
    return " ".join(parts) or f"{seconds} giây"


def _format_epoch_vn(ts: Any) -> str:
    try:
        epoch = int(float(ts or 0))
    except (TypeError, ValueError):
        return ""
    if epoch <= 0:
        return ""
    import datetime as _dt

    dt = _dt.datetime.fromtimestamp(epoch)
    return dt.strftime("%H:%M %d/%m/%Y")


def _classify_question_state(
    raw_state: str,
    raw_status: str,
    que_classes: set[str],
    mark_val: str,
    max_val: str,
) -> tuple[str, str]:
    st = (raw_state or "").strip().lower()
    classes = {c.lower() for c in que_classes}
    if st in {"gradedright", "mangrright"} or "correct" in classes:
        return "correct", "Đúng"
    if st in {"gradedpartial", "mangrpartial"} or "partiallycorrect" in classes:
        return "partial", "Đúng một phần"
    if st in {"gradedwrong", "mangrwrong"} or "incorrect" in classes:
        return "incorrect", "Sai"
    if st in {"gaveup", "todo"} or "notanswered" in classes:
        return "unanswered", "Chưa trả lời"

    try:
        m_f = float(mark_val)
        mx_f = float(max_val)
        if mx_f > 0:
            if m_f >= mx_f - 1e-4:
                return "correct", "Đúng"
            if m_f > 0:
                return "partial", "Đúng một phần"
            if st in {"complete", "finished"} or raw_status:
                return "incorrect", "Sai"
    except (TypeError, ValueError):
        pass

    status_clean = " ".join((raw_status or "").split())
    low_status = status_clean.lower()
    if low_status in {"correct", "đúng"}:
        return "correct", "Đúng"
    if low_status in {"incorrect", "sai", "not correct"}:
        return "incorrect", "Sai"
    if "partial" in low_status or "một phần" in low_status:
        return "partial", "Đúng một phần"
    if "not answered" in low_status or "chưa trả lời" in low_status:
        return "unanswered", "Chưa trả lời"
    if "complete" in low_status or st == "complete":
        return "complete", status_clean or "Đã trả lời"
    return "neutral", status_clean or "Đã chấm"


def _extract_choice_label(raw_num: str, idx: int) -> str:
    cleaned = raw_num.strip().rstrip(".): ")
    if cleaned and len(cleaned) <= 4:
        return cleaned.upper() if len(cleaned) == 1 and cleaned.isalpha() else cleaned
    return chr(65 + (idx % 26))


def _parse_single_quiz_question_dom(
    que_soup: Any,
    *,
    seq_number: int,
    raw_meta: Optional[dict[str, Any]] = None,
) -> tuple[QuizReviewQuestion, bool]:
    """Parse one Moodle ``div.que`` DOM block into a structured ``QuizReviewQuestion``."""
    meta = raw_meta or {}
    for junk in list(que_soup.find_all(_NON_READABLE_TAGS)):
        if not getattr(junk, "decomposed", False):
            junk.decompose()

    que_root = que_soup.select_one("div.que") or que_soup
    raw_classes = que_root.get("class") if hasattr(que_root, "get") else None
    que_classes = {str(c) for c in (raw_classes or []) if c}

    qtype = str(meta.get("type") or "").strip().lower()
    if not qtype:
        known_types = {
            "multichoice",
            "truefalse",
            "shortanswer",
            "match",
            "numerical",
            "essay",
            "multianswer",
            "description",
            "gapselect",
            "ddwtos",
        }
        for cls_tok in que_classes:
            if cls_tok.lower() in known_types:
                qtype = cls_tok.lower()
                break

    raw_number = str(meta.get("number") or "").strip()
    if not raw_number:
        qno_el = que_root.select_one(".info .qno")
        if qno_el is not None:
            raw_number = _clean_text(qno_el)

    is_info_only = qtype == "description" or raw_number.lower() == "i"
    display_number = "Thông tin" if is_info_only else str(seq_number)
    slot = str(meta.get("slot") or raw_number or seq_number)

    # Extract grade/mark & state from metadata or .info block
    mark = _format_score_num(meta.get("mark"))
    max_mark = _format_score_num(meta.get("maxmark"))
    if not mark or not max_mark:
        grade_div = que_root.select_one(".info .grade")
        if grade_div is not None:
            grade_txt = _clean_text(grade_div)
            nums = re.findall(r"\d+(?:[.,]\d+)?", grade_txt)
            if len(nums) >= 2:
                mark = mark or _format_score_num(nums[0].replace(",", "."))
                max_mark = max_mark or _format_score_num(nums[1].replace(",", "."))

    raw_status = str(meta.get("status") or "").strip()
    if not raw_status:
        state_div = que_root.select_one(".info .state")
        if state_div is not None:
            raw_status = _clean_text(state_div)

    state_kind, status_label = _classify_question_state(
        str(meta.get("state") or ""),
        raw_status,
        que_classes,
        mark,
        max_mark,
    )

    # Remove screen-reader-only labels (.accesshide) before text extraction
    for hidden in list(que_root.select(".accesshide")):
        if not getattr(hidden, "decomposed", False):
            hidden.decompose()

    # Extract outcome blocks (.rightanswer, .specificfeedback, .generalfeedback)
    right_el = que_root.select_one(".outcome .rightanswer, .rightanswer")
    raw_right = _clean_text(right_el) if right_el is not None else ""
    stripped_right = _RIGHT_ANSWER_PREFIX_RE.sub("", raw_right).strip()
    clean_right, _ = clean_moodle_question_text(stripped_right)

    gen_fb_el = que_root.select_one(".outcome .generalfeedback, .generalfeedback")
    general_fb, _ = clean_moodle_question_text(_clean_text(gen_fb_el)) if gen_fb_el is not None else ("", "")

    spec_fb_el = que_root.select_one(".outcome > .specificfeedback, .outcome .feedback > .specificfeedback")
    specific_fb, _ = clean_moodle_question_text(_clean_text(spec_fb_el)) if spec_fb_el is not None else ("", "")

    # Extract question stem (.qtext)
    qtext_el = que_root.select_one(".qtext")
    raw_stem = ""
    if qtext_el is not None:
        raw_stem = _html_to_readable_markdown(str(qtext_el)) or _clean_text(qtext_el)
    else:
        formulation = que_root.select_one(".formulation")
        raw_stem = _clean_text(formulation or que_root)[:600]
    stem_text, q_code = clean_moodle_question_text(raw_stem)

    # Parse answer choices / student response inside .ablock / .answer
    choices: list[QuizReviewChoice] = []
    student_response_text = ""
    is_multi_checkbox = False

    answer_container = que_root.select_one(".ablock .answer, .answer")
    if answer_container is not None:
        # 1. Matching question table (<select>)
        select_rows = [
            tr for tr in answer_container.select("tr") if tr.find("select") is not None
        ]
        if select_rows:
            for idx_m, tr in enumerate(select_rows):
                stem_td = tr.select_one("td.text") or tr.find("td")
                sel = tr.find("select")
                chosen_opt = ""
                if sel is not None:
                    sel_opt = sel.find("option", selected=True) or sel.find(
                        "option", attrs={"selected": True}
                    )
                    if sel_opt is not None:
                        chosen_opt = _clean_text(sel_opt)
                tr_classes = {str(c).lower() for c in (tr.get("class") or [])}
                row_state = (
                    "correct"
                    if "correct" in tr_classes
                    else ("incorrect" if "incorrect" in tr_classes else "neutral")
                )
                left_txt, _ = clean_moodle_question_text(_clean_text(stem_td))
                right_txt, _ = clean_moodle_question_text(chosen_opt)
                pair_txt = f"{left_txt}  →  {right_txt or '(Chưa chọn)'}"
                choices.append(
                    QuizReviewChoice(
                        label=str(idx_m + 1),
                        text=pair_txt,
                        is_selected=bool(right_txt),
                        state=row_state,
                    )
                )
        else:
            # 2. Multiple choice / True-False option rows
            option_divs = [
                div
                for div in answer_container.find_all("div", recursive=False)
                if div.find("input", attrs={"type": lambda t: str(t or "").lower() in {"radio", "checkbox"}})
                is not None
                or any(str(c).startswith("r") for c in (div.get("class") or []))
            ]
            if not option_divs:
                option_divs = [
                    div
                    for div in answer_container.select("div.r0, div.r1")
                ]

            for idx_c, opt_div in enumerate(option_divs):
                inp = opt_div.find(
                    "input",
                    attrs={"type": lambda t: str(t or "").lower() in {"radio", "checkbox"}},
                )
                if inp is not None and str(inp.get("type") or "").lower() == "checkbox":
                    is_multi_checkbox = True
                is_checked = bool(
                    inp is not None
                    and (inp.has_attr("checked") or str(inp.get("checked") or "").lower() in {"checked", "true", "1"})
                )

                row_classes = {str(c).lower() for c in (opt_div.get("class") or [])}
                opt_state = "neutral"
                if "correct" in row_classes:
                    opt_state = "correct"
                elif "partiallycorrect" in row_classes:
                    opt_state = "partial"
                elif "incorrect" in row_classes:
                    opt_state = "incorrect"
                else:
                    icon_el = opt_div.select_one(
                        "i.fa-check, i.fa-check-circle, i.text-success, i.fa-remove, i.fa-times, i.fa-xmark, i.text-danger"
                    )
                    if icon_el is not None:
                        ic_cls = " ".join(icon_el.get("class") or []).lower()
                        if "check" in ic_cls or "success" in ic_cls:
                            opt_state = "correct"
                        elif any(k in ic_cls for k in ("remove", "times", "xmark", "danger")):
                            opt_state = "incorrect"

                # Clone option HTML to remove answernumber & inline feedback cleanly
                opt_clone = BeautifulSoup(str(opt_div), BS4_PARSER)
                ans_num_el = opt_clone.select_one(".answernumber")
                raw_ans_num = _clean_text(ans_num_el) if ans_num_el is not None else ""
                if ans_num_el is not None:
                    ans_num_el.decompose()
                inline_fb_el = opt_clone.select_one(".specificfeedback")
                inline_fb = ""
                if inline_fb_el is not None:
                    inline_fb, _ = clean_moodle_question_text(_clean_text(inline_fb_el))
                    inline_fb_el.decompose()
                for tag_rm in opt_clone.find_all(["input", "script", "style"]):
                    tag_rm.decompose()

                choice_txt, _ = clean_moodle_question_text(_clean_text(opt_clone))
                if not choice_txt:
                    continue
                if not raw_ans_num:
                    m_lead = re.match(r"^([A-Ha-h]|[1-8])[.)]\s+(.+)$", choice_txt, flags=re.DOTALL)
                    if m_lead:
                        raw_ans_num = m_lead.group(1)
                        choice_txt = m_lead.group(2).strip()
                label_str = _extract_choice_label(raw_ans_num, idx_c)
                if is_checked and opt_state == "neutral":
                    if state_kind == "correct":
                        opt_state = "correct"
                    elif state_kind == "incorrect":
                        opt_state = "incorrect"
                    elif state_kind == "partial":
                        opt_state = "partial"

                choices.append(
                    QuizReviewChoice(
                        label=label_str,
                        text=choice_txt,
                        is_selected=is_checked,
                        state=opt_state,
                        feedback=inline_fb,
                    )
                )

            # 3. Short-answer / numerical / essay inputs if no option rows
            if not choices:
                text_inp = answer_container.find(
                    "input",
                    attrs={"type": lambda t: str(t or "text").lower() in {"text", "number"}},
                )
                if text_inp is not None:
                    student_response_text = str(text_inp.get("value") or "").strip()
                else:
                    ta = answer_container.find("textarea")
                    if ta is not None:
                        student_response_text = _clean_text(ta)
                    else:
                        essay_box = que_root.select_one(".qtype_essay_response, .ablock")
                        if essay_box is not None and qtype in {"essay", "shortanswer", "numerical"}:
                            student_response_text, _ = clean_moodle_question_text(_clean_text(essay_box))

    # Cross-highlight the official right answer option when Moodle only put it in .rightanswer
    if choices and clean_right and not any(c.state == "correct" for c in choices):
        right_norm = _normalize_match_key(clean_right)
        candidate_norms = {right_norm}
        if is_multi_checkbox and ("," in clean_right or ";" in clean_right):
            for piece in re.split(r"[;,]", clean_right):
                pn = _normalize_match_key(piece)
                if pn:
                    candidate_norms.add(pn)
        upgraded: list[QuizReviewChoice] = []
        for ch in choices:
            ch_norm = _normalize_match_key(ch.text)
            if ch_norm and ch_norm in candidate_norms and ch.state == "neutral":
                upgraded.append(
                    QuizReviewChoice(
                        label=ch.label,
                        text=ch.text,
                        is_selected=ch.is_selected,
                        state="correct",
                        feedback=ch.feedback,
                    )
                )
            else:
                upgraded.append(ch)
        choices = upgraded

    return (
        QuizReviewQuestion(
            display_number=display_number,
            slot=slot,
            question_code=q_code,
            qtype=qtype,
            state_kind="neutral" if is_info_only else state_kind,
            status_label="" if is_info_only else status_label,
            mark="" if is_info_only else mark,
            max_mark="" if is_info_only else max_mark,
            question_text=stem_text,
            choices=tuple(choices),
            student_response_text=student_response_text,
            right_answer_text=clean_right,
            specific_feedback=specific_fb,
            general_feedback=general_fb,
        ),
        not is_info_only,
    )


def parse_quiz_attempt_review_payload(
    data: dict[str, Any], *, fallback_attempt_id: int = 0
) -> QuizAttemptReviewReport:
    """Parse Moodle ``mod_quiz_get_attempt_review`` JSON payload into a rich report."""
    att_obj = data.get("attempt") if isinstance(data.get("attempt"), dict) else {}
    attempt_id = int(att_obj.get("id") or fallback_attempt_id or 0)
    attempt_num = int(att_obj.get("attempt") or 1)
    raw_state = str(att_obj.get("state") or "finished").lower()
    state_map = {
        "finished": "Đã hoàn thành",
        "inprogress": "Đang làm",
        "overdue": "Quá hạn",
        "abandoned": "Đã hủy",
    }
    state_label = state_map.get(raw_state, raw_state)

    grade_str = _format_score_num(data.get("grade"))
    sumgrades_str = _format_score_num(att_obj.get("sumgrades"))

    t_start = int(float(att_obj.get("timestart") or 0))
    t_finish = int(float(att_obj.get("timefinish") or 0))
    duration_text = _format_duration_human(t_finish - t_start) if (t_finish > t_start > 0) else ""
    finished_at_text = _format_epoch_vn(t_finish)

    # Also check additionaldata table rows (Moodle often puts formatted Grade '9.50 out of 10.00 (95%)' here)
    formatted_grade_banner = ""
    for extra in data.get("additionaldata") or []:
        if not isinstance(extra, dict):
            continue
        title_l = str(extra.get("title") or "").lower()
        if "grade" in title_l or "điểm" in title_l:
            soup_ex = BeautifulSoup(str(extra.get("content") or ""), BS4_PARSER)
            formatted_grade_banner = _clean_text(soup_ex)
            break

    questions: list[QuizReviewQuestion] = []
    seq = 1
    correct_n = 0
    incorrect_n = 0
    partial_n = 0
    unanswered_n = 0

    for raw_q in data.get("questions") or []:
        if not isinstance(raw_q, dict):
            continue
        q_html = str(raw_q.get("html") or "")
        q_soup = BeautifulSoup(q_html, BS4_PARSER)
        parsed_q, counted = _parse_single_quiz_question_dom(
            q_soup, seq_number=seq, raw_meta=raw_q
        )
        questions.append(parsed_q)
        if counted:
            seq += 1
            if parsed_q.state_kind == "correct":
                correct_n += 1
            elif parsed_q.state_kind == "incorrect":
                incorrect_n += 1
            elif parsed_q.state_kind == "partial":
                partial_n += 1
            elif parsed_q.state_kind == "unanswered":
                unanswered_n += 1

    grade_display = formatted_grade_banner or (f"{grade_str} / 10" if grade_str else sumgrades_str)
    return QuizAttemptReviewReport(
        attempt_id=attempt_id,
        attempt_number=attempt_num,
        state_label=state_label,
        grade_text=grade_display,
        raw_score_text=sumgrades_str,
        duration_text=duration_text,
        finished_at_text=finished_at_text,
        correct_count=correct_n,
        incorrect_count=incorrect_n,
        partial_count=partial_n,
        unanswered_count=unanswered_n,
        questions=tuple(questions),
    )


def parse_quiz_review_html_page(
    html_text: str, *, attempt_id: int = 0
) -> QuizAttemptReviewReport:
    """Parse ``/mod/quiz/review.php`` HTML directly (for THNN or session fallback)."""
    soup = BeautifulSoup(html_text or "", BS4_PARSER)
    grade_text = ""
    duration_text = ""
    finished_text = ""
    for tr in soup.select("table.quizreviewsummary tr"):
        th = _clean_text(tr.find("th")).lower()
        td = _clean_text(tr.find("td"))
        if not td:
            continue
        if "grade" in th or "điểm" in th:
            grade_text = td
        elif "time taken" in th or "thời gian thực hiện" in th:
            duration_text = td
        elif "completed" in th or "kết thúc" in th:
            finished_text = td

    questions: list[QuizReviewQuestion] = []
    seq = 1
    correct_n = 0
    incorrect_n = 0
    partial_n = 0
    unanswered_n = 0
    for que_div in soup.select("div.que"):
        parsed_q, counted = _parse_single_quiz_question_dom(que_div, seq_number=seq)
        questions.append(parsed_q)
        if counted:
            seq += 1
            if parsed_q.state_kind == "correct":
                correct_n += 1
            elif parsed_q.state_kind == "incorrect":
                incorrect_n += 1
            elif parsed_q.state_kind == "partial":
                partial_n += 1
            elif parsed_q.state_kind == "unanswered":
                unanswered_n += 1

    return QuizAttemptReviewReport(
        attempt_id=attempt_id,
        attempt_number=1,
        state_label="Đã hoàn thành",
        grade_text=grade_text,
        duration_text=duration_text,
        finished_at_text=finished_text,
        correct_count=correct_n,
        incorrect_count=incorrect_n,
        partial_count=partial_n,
        unanswered_count=unanswered_n,
        questions=tuple(questions),
    )


def _extract_cmid_from_url(url: str) -> int:
    qs = parse_qs(urlsplit(url or "").query)
    raw = (qs.get("id") or [""])[0]
    try:
        return int(raw)
    except ValueError:
        return 0


def parse_attendance_html(
    html_text: str, *, base_url: str, page_url: str, title: str = "Điểm danh"
) -> AttendanceSummary:
    """Parse Moodle ``mod_attendance`` summary stats, session table, and self check-in form."""
    soup = BeautifulSoup(html_text or "", BS4_PARSER)
    heading = soup.select_one("h2, .page-header-headings h1")
    clean_title = _clean_text(heading) or title

    taken_sessions = ""
    points_over_taken = ""
    percentage = ""
    for row in soup.select("table.attlist tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        label = _clean_text(cells[0]).lower()
        value = _clean_text(cells[1])
        if "points" in label or "điểm" in label:
            points_over_taken = value
        elif "percentage" in label or "tỷ lệ" in label or "%" in label:
            percentage = value
        elif "taken" in label or "buổi" in label:
            taken_sessions = value

    records: list[AttendanceRecord] = []
    att_table = soup.select_one("table.generaltable.attwidth") or soup.select_one(
        "table.generaltable"
    )
    submit_form: Optional[dict[str, str]] = None
    submit_action_url = ""
    status_options: list[tuple[str, str]] = []
    requires_password = False

    if att_table is not None:
        for row in att_table.select("tbody tr"):
            cols = row.find_all(["td", "th"])
            if len(cols) < 3:
                continue
            date_str = _clean_text(cols[0])
            desc = _clean_text(cols[1]) if len(cols) > 1 else ""
            status = _clean_text(cols[2]) if len(cols) > 2 else ""
            points = _clean_text(cols[3]) if len(cols) > 3 else ""
            remarks = _clean_text(cols[4]) if len(cols) > 4 else ""
            action_link = row.select_one("a[href*='attendance.php']")
            submit_url = ""
            if action_link is not None:
                submit_url = _safe_http_url(
                    urljoin(f"{base_url.rstrip('/')}/", str(action_link.get("href") or ""))
                )
            records.append(
                AttendanceRecord(
                    date_str=date_str,
                    description=desc,
                    status=status,
                    points=points,
                    remarks=remarks,
                    can_submit=bool(submit_url),
                    submit_url=submit_url,
                )
            )

    # Detect direct self-attendance submission form if present on page
    for form in soup.select("form[action*='attendance']"):
        radios = form.select("input[type='radio'][name='status']")
        pwd_input = form.select_one("input[name='studentpassword']")
        if not radios and pwd_input is None:
            continue
        fields: dict[str, str] = {}
        for inp in form.select("input"):
            name = str(inp.get("name") or "").strip()
            if not name:
                continue
            itype = str(inp.get("type") or "text").lower()
            val = str(inp.get("value") or "")
            if itype == "radio":
                label_el = inp.find_parent("label") or form.find(
                    "label", attrs={"for": inp.get("id")}
                )
                opt_label = _clean_text(label_el) or val
                if val:
                    status_options.append((val, opt_label))
                if inp.has_attr("checked") and "status" not in fields:
                    fields["status"] = val
            elif itype == "password" or name == "studentpassword":
                requires_password = True
                fields[name] = val
            elif itype in {"hidden", "submit"}:
                fields[name] = val
        if status_options and "status" not in fields:
            fields["status"] = status_options[0][0]
        raw_action = str(form.get("action") or page_url or "").strip()
        resolved_action = _safe_http_url(urljoin(f"{base_url.rstrip('/')}/", raw_action))
        if resolved_action and _same_origin(resolved_action, base_url):
            submit_action_url = resolved_action
            submit_form = fields
            break

    status_notice_el = soup.select_one(".alert-success, .alert-info, .alert-danger")
    status_message = _clean_text(status_notice_el)

    return AttendanceSummary(
        cmid=_extract_cmid_from_url(page_url),
        title=clean_title,
        taken_sessions=taken_sessions,
        points_over_taken=points_over_taken,
        percentage=percentage,
        records=tuple(records),
        submit_form=submit_form,
        submit_action_url=submit_action_url,
        status_options=tuple(status_options),
        requires_password=requires_password,
        status_message=status_message,
    )


def parse_groupselect_html(
    html_text: str,
    *,
    base_url: str,
    page_url: str,
    title: str = "Chọn nhóm",
    my_groups: tuple[str, ...] = (),
) -> GroupActivitySummary:
    """Parse Moodle ``mod_groupselect`` HTML table and join/leave form actions."""
    soup = BeautifulSoup(html_text or "", BS4_PARSER)
    heading = soup.select_one("h2, .page-header-headings h1")
    clean_title = _clean_text(heading) or title
    intro_el = soup.select_one("#intro, .activity-description")
    intro = _clean_text(intro_el)

    table = soup.select_one("table.groupselect-table") or soup.select_one("table.generaltable")
    groups: list[CourseGroupEntry] = []
    if table is not None:
        for row_idx, row in enumerate(table.select("tbody tr")):
            cols = row.find_all("td")
            if len(cols) < 2:
                continue
            name = _clean_text(cols[0])
            if not name:
                continue
            desc = _clean_text(cols[1]) if len(cols) > 1 else ""
            count_label = _clean_text(cols[2]) if len(cols) > 2 else ""
            members_text = _clean_text(cols[3]) if len(cols) > 3 else ""
            members = tuple(m.strip() for m in re.split(r"[,;\n]+", members_text) if m.strip())

            form = row.select_one("form")
            action_url = ""
            action_fields: list[tuple[str, str]] = []
            can_join = False
            can_leave = False
            is_member = name in my_groups
            if form is not None:
                raw_action = str(form.get("action") or page_url).strip()
                resolved = _safe_http_url(urljoin(f"{base_url.rstrip('/')}/", raw_action))
                if resolved and _same_origin(resolved, base_url):
                    action_url = resolved
                for inp in form.select("input, button"):
                    inp_name = str(inp.get("name") or "").strip()
                    inp_val = str(inp.get("value") or "").strip()
                    if inp_name:
                        action_fields.append((inp_name, inp_val))
                        if inp_name == "select" or "join" in inp_name.lower():
                            can_join = True
                        if inp_name == "unselect" or "leave" in inp_name.lower():
                            can_leave = True
                            is_member = True
                btn_text = _clean_text(form.select_one("button, input[type='submit']")).lower()
                if not can_leave and ("unselect" in btn_text or "rời" in btn_text or "hủy" in btn_text):
                    can_leave = True
                    is_member = True
                elif not can_leave and (action_url and (can_join or "tham" in btn_text or "chọn" in btn_text or "become" in btn_text)):
                    can_join = True

            gid = ""
            for k, v in action_fields:
                if k in {"select", "unselect", "group"} and v:
                    gid = v
                    break
            if not gid:
                gid = str(row_idx + 1)

            groups.append(
                CourseGroupEntry(
                    group_id=gid,
                    name=name,
                    description=desc,
                    member_count_label=count_label,
                    members=members,
                    is_member=is_member,
                    can_join=can_join,
                    can_leave=can_leave,
                    action_url=action_url,
                    action_fields=tuple(action_fields),
                )
            )

    notice = _clean_text(soup.select_one(".alert-success, .alert-info, .alert-danger"))
    return GroupActivitySummary(
        cmid=_extract_cmid_from_url(page_url),
        modname="groupselect",
        title=clean_title,
        intro=intro,
        my_group_names=my_groups,
        groups=tuple(groups),
        is_radio_choice=False,
        message=notice,
    )


def parse_choicegroup_html(
    html_text: str,
    *,
    base_url: str,
    page_url: str,
    title: str = "Chọn nhóm",
    my_groups: tuple[str, ...] = (),
) -> GroupActivitySummary:
    """Parse Moodle ``mod_choicegroup`` radio/checkbox form into ``GroupActivitySummary``."""
    soup = BeautifulSoup(html_text or "", BS4_PARSER)
    heading = soup.select_one("h2, .page-header-headings h1")
    clean_title = _clean_text(heading) or title
    intro = _clean_text(soup.select_one("#intro, .activity-description"))

    form = soup.select_one("form[action*='choicegroup']") or soup.select_one("form#form")
    submit_url = ""
    submit_fields: list[tuple[str, str]] = []
    groups: list[CourseGroupEntry] = []

    if form is not None:
        raw_action = str(form.get("action") or page_url).strip()
        resolved = _safe_http_url(urljoin(f"{base_url.rstrip('/')}/", raw_action))
        if resolved and _same_origin(resolved, base_url):
            submit_url = resolved
        for inp in form.select("input[type='hidden']"):
            n = str(inp.get("name") or "").strip()
            if n:
                submit_fields.append((n, str(inp.get("value") or "")))

        rows = form.select("table.choicegroups tbody tr, table.generaltable tbody tr")
        for idx, row in enumerate(rows):
            inp = row.select_one("input[name='answer'], input[name^='answer']")
            cols = row.find_all(["td", "th"])
            name = ""
            count_label = ""
            members: tuple[str, ...] = ()
            if cols:
                name = _clean_text(cols[1] if len(cols) > 1 else cols[0])
                if len(cols) > 2:
                    count_label = _clean_text(cols[2])
                if len(cols) > 3:
                    mtxt = _clean_text(cols[3])
                    members = tuple(m.strip() for m in re.split(r"[,;\n]+", mtxt) if m.strip())
            opt_val = str(inp.get("value") or idx + 1) if inp is not None else str(idx + 1)
            checked = bool(inp is not None and inp.has_attr("checked"))
            disabled = bool(inp is not None and inp.has_attr("disabled"))
            if not name and inp is not None:
                lbl = form.find("label", attrs={"for": inp.get("id")})
                name = _clean_text(lbl)
            if not name:
                continue
            groups.append(
                CourseGroupEntry(
                    group_id=opt_val,
                    name=name,
                    member_count_label=count_label,
                    members=members,
                    is_member=checked or (name in my_groups),
                    can_join=not disabled and bool(submit_url),
                    can_leave=checked and bool(submit_url),
                    action_url=submit_url,
                    action_fields=tuple([*submit_fields, ("answer", opt_val)]),
                )
            )

    notice = _clean_text(soup.select_one(".alert-success, .alert-info, .alert-danger"))
    return GroupActivitySummary(
        cmid=_extract_cmid_from_url(page_url),
        modname="choicegroup",
        title=clean_title,
        intro=intro,
        my_group_names=my_groups,
        groups=tuple(groups),
        is_radio_choice=True,
        submit_url=submit_url,
        submit_fields=tuple(submit_fields),
        message=notice,
    )


def parse_forum_html(
    html_text: str, *, base_url: str
) -> tuple[ForumDiscussionEntry, ...]:
    """Parse Moodle forum discussion rows from ``/mod/forum/view.php`` HTML."""
    soup = BeautifulSoup(html_text or "", BS4_PARSER)
    entries: list[ForumDiscussionEntry] = []
    for row in soup.select("table.discussion-list tbody tr, table.forumheaderlist tbody tr"):
        topic_link = row.select_one("th.topic a[href*='discuss.php'], td.topic a[href*='discuss.php']")
        if topic_link is None:
            continue
        title = _clean_text(topic_link)
        href = _safe_http_url(urljoin(f"{base_url.rstrip('/')}/", str(topic_link.get("href") or "")))
        qs = parse_qs(urlsplit(href).query)
        try:
            disc_id = int((qs.get("d") or ["0"])[0])
        except ValueError:
            disc_id = 0
        author_el = row.select_one(".author, td.author")
        replies_el = row.select_one(".replies, td.replies")
        try:
            replies = int(re.sub(r"\D+", "", _clean_text(replies_el)) or "0")
        except ValueError:
            replies = 0
        entries.append(
            ForumDiscussionEntry(
                discussion_id=disc_id,
                name=title or "Chủ đề thảo luận",
                author=_clean_text(author_el),
                created_at="",
                num_replies=replies,
                discussion_url=href,
            )
        )
    return tuple(entries)


def parse_book_html(html_text: str, *, title: str) -> tuple[BookChapterEntry, ...]:
    """Parse ``mod_book`` TOC and active chapter HTML into ``BookChapterEntry`` items."""
    soup = BeautifulSoup(html_text or "", BS4_PARSER)
    chapters: list[BookChapterEntry] = []
    toc_links = soup.select(".book_toc a[href*='chapterid='], #book_toc a[href*='chapterid=']")
    main_box = soup.select_one(".book_content, #region-main .box.generalbox")
    current_md = _html_to_readable_markdown(str(main_box)) if main_box is not None else ""
    current_heading = _clean_text(soup.select_one(".book_content h3, .book_content h2"))

    for idx, link in enumerate(toc_links):
        href = str(link.get("href") or "")
        qs = parse_qs(urlsplit(href).query)
        chap_id = (qs.get("chapterid") or [str(idx + 1)])[0]
        chap_title = _clean_text(link) or f"Chương {idx + 1}"
        is_sub = "subchapter" in " ".join(link.find_parent("li").get("class") or []) if link.find_parent("li") else False
        md = current_md if (chap_title == current_heading or idx == 0) else ""
        chapters.append(
            BookChapterEntry(
                chapter_id=chap_id,
                title=chap_title,
                content_markdown=md,
                subchapter=is_sub,
            )
        )

    if not chapters and current_md:
        chapters.append(
            BookChapterEntry(
                chapter_id="1",
                title=current_heading or title,
                content_markdown=current_md,
            )
        )
    return tuple(chapters)


def parse_page_html(html_text: str, *, title: str) -> str:
    """Extract clean Markdown body from a Moodle ``mod_page`` HTML document."""
    soup = BeautifulSoup(html_text or "", BS4_PARSER)
    container = (
        soup.select_one("#region-main .box.generalbox .no-overflow")
        or soup.select_one("#region-main .box.generalbox")
        or soup.select_one("#region-main")
    )
    return _html_to_readable_markdown(str(container or html_text or ""))


class CourseActivityService:
    """Unified service powering native in-app Attendance, Groups, Quiz, Forum, and Book flows."""

    def __init__(
        self,
        *,
        moodle_service: Any = None,
        courses_client: Any = None,
        thnn_client: Any = None,
    ) -> None:
        self._service = moodle_service
        self._clients = {"courses": courses_client, "thnn": thnn_client}

    def _client_for(self, source: CourseSource) -> Any:
        return self._clients.get(source)

    @staticmethod
    def _origin_for(client: Any, module_url: str) -> str:
        origin = getattr(client, "moodle_site_origin", "") or ""
        if origin:
            return origin.rstrip("/")
        parsed = urlsplit(module_url or "")
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
        return ""

    # ------------------------------------------------------------------
    # Attendance
    # ------------------------------------------------------------------
    def fetch_attendance(
        self, source: CourseSource, module: CourseModuleItem
    ) -> AttendanceSummary:
        client = self._client_for(source)
        target_url = _safe_http_url(module.web_url or module.url)
        if client is None or not target_url:
            return AttendanceSummary(
                cmid=module.cmid,
                title=module.name or "Điểm danh",
                status_message="Chưa có phiên Moodle để tải dữ liệu điểm danh.",
            )
        try:
            final_url, html_text, redirected = client.open_session_url(target_url)
        except Exception as exc:
            return AttendanceSummary(
                cmid=module.cmid,
                title=module.name or "Điểm danh",
                status_message=f"Không thể tải trang điểm danh: {exc}",
            )
        if redirected:
            return AttendanceSummary(
                cmid=module.cmid,
                title=module.name or "Điểm danh",
                status_message="Trang điểm danh chuyển hướng ngoài hệ thống.",
            )
        base = self._origin_for(client, final_url or target_url)
        parsed = parse_attendance_html(
            html_text,
            base_url=base,
            page_url=final_url or target_url,
            title=module.name or "Điểm danh",
        )
        # If there's a session check-in link in the table and no inline form yet, follow first link
        if parsed.submit_form is None:
            for rec in parsed.records:
                if rec.can_submit and rec.submit_url and _same_origin(rec.submit_url, base):
                    try:
                        s_url, s_html, s_redir = client.open_session_url(rec.submit_url)
                    except Exception:
                        break
                    if not s_redir:
                        sub_parsed = parse_attendance_html(
                            s_html,
                            base_url=base,
                            page_url=s_url or rec.submit_url,
                            title=parsed.title,
                        )
                        if sub_parsed.submit_form is not None:
                            return AttendanceSummary(
                                cmid=parsed.cmid or module.cmid,
                                title=parsed.title,
                                taken_sessions=parsed.taken_sessions,
                                points_over_taken=parsed.points_over_taken,
                                percentage=parsed.percentage,
                                records=parsed.records,
                                submit_form=sub_parsed.submit_form,
                                submit_action_url=sub_parsed.submit_action_url,
                                status_options=sub_parsed.status_options,
                                requires_password=sub_parsed.requires_password,
                                status_message=sub_parsed.status_message or parsed.status_message,
                            )
                    break
        return parsed

    def submit_attendance(
        self,
        source: CourseSource,
        action_url: str,
        fields: dict[str, str],
        *,
        fallback_title: str = "Điểm danh",
    ) -> tuple[bool, str, Optional[AttendanceSummary]]:
        client = self._client_for(source)
        if client is None or not action_url:
            return (False, "Không thể gửi điểm danh do thiếu phiên đăng nhập.", None)
        try:
            final_url, resp_html, redirected = client.submit_session_form(action_url, fields)
        except Exception as exc:
            return (False, f"Gửi điểm danh thất bại: {exc}", None)
        if redirected:
            return (False, "Phiên gửi điểm danh chuyển hướng ngoài trang trường.", None)
        base = self._origin_for(client, final_url or action_url)
        updated = parse_attendance_html(
            resp_html,
            base_url=base,
            page_url=final_url or action_url,
            title=fallback_title,
        )
        err_box = BeautifulSoup(resp_html, BS4_PARSER).select_one(".alert-danger, .errorbox")
        if err_box is not None:
            return (False, _clean_text(err_box) or "Không thể lưu điểm danh.", updated)
        return (
            True,
            updated.status_message or "Đã gửi điểm danh thành công.",
            updated,
        )

    # ------------------------------------------------------------------
    # Groups (groupselect / choicegroup / choice)
    # ------------------------------------------------------------------
    def _my_group_names(self, source: CourseSource, course_id: int) -> tuple[str, ...]:
        if source != "courses" or self._service is None or course_id <= 0:
            return ()
        getter = getattr(self._service, "get_course_user_groups", None)
        if not callable(getter):
            return ()
        try:
            rows = getter(int(course_id)) or []
        except Exception:
            return ()
        return tuple(
            str(item.get("name") or "").strip()
            for item in rows
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        )

    def fetch_group_activity(
        self,
        source: CourseSource,
        module: CourseModuleItem,
        *,
        course_id: int,
    ) -> GroupActivitySummary:
        my_groups = self._my_group_names(source, course_id)
        modname = (module.modname or "").lower()

        if modname == "choice" and source == "courses" and self._service is not None and module.instance_id > 0:
            opts_fn = getattr(self._service, "get_choice_options", None)
            if callable(opts_fn):
                raw_opts = opts_fn(int(module.instance_id)) or []
                entries: list[CourseGroupEntry] = []
                for opt in raw_opts:
                    if not isinstance(opt, dict):
                        continue
                    oid = int(opt.get("id") or 0)
                    text = html.unescape(str(opt.get("text") or "").strip())
                    checked = bool(opt.get("checked"))
                    disabled = bool(opt.get("disabled"))
                    count = int(opt.get("numberofuser") or 0)
                    max_ans = int(opt.get("maxanswers") or 0)
                    cnt_label = f"{count}/{max_ans}" if max_ans > 0 else str(count)
                    usernames = tuple(
                        str(u.get("fullname") or "").strip()
                        for u in (opt.get("userresponses") or [])
                        if isinstance(u, dict) and str(u.get("fullname") or "").strip()
                    )
                    entries.append(
                        CourseGroupEntry(
                            group_id=str(oid),
                            name=text or f"Lựa chọn {oid}",
                            member_count_label=cnt_label,
                            members=usernames,
                            is_member=checked,
                            can_join=not disabled,
                            can_leave=checked and not disabled,
                            choice_option_id=oid,
                        )
                    )
                if entries:
                    return GroupActivitySummary(
                        cmid=module.cmid,
                        modname="choice",
                        title=module.name or "Lựa chọn nhóm",
                        intro=_html_to_readable_markdown(module.description_html or ""),
                        my_group_names=my_groups,
                        groups=tuple(entries),
                        is_radio_choice=True,
                    )

        client = self._client_for(source)
        target_url = _safe_http_url(module.web_url or module.url)
        if client is None or not target_url:
            return GroupActivitySummary(
                cmid=module.cmid,
                modname=modname,
                title=module.name or "Nhóm học phần",
                my_group_names=my_groups,
                message="Chưa thể kết nối phiên web để xem danh sách nhóm.",
            )
        try:
            final_url, html_text, redirected = client.open_session_url(target_url)
        except Exception as exc:
            return GroupActivitySummary(
                cmid=module.cmid,
                modname=modname,
                title=module.name or "Nhóm học phần",
                my_group_names=my_groups,
                message=f"Không thể tải hoạt động chọn nhóm: {exc}",
            )
        if redirected:
            return GroupActivitySummary(
                cmid=module.cmid,
                modname=modname,
                title=module.name or "Nhóm học phần",
                my_group_names=my_groups,
                message="Trang chọn nhóm chuyển hướng ra ngoài.",
            )
        base = self._origin_for(client, final_url or target_url)
        if modname == "choicegroup":
            return parse_choicegroup_html(
                html_text,
                base_url=base,
                page_url=final_url or target_url,
                title=module.name or "Chọn nhóm",
                my_groups=my_groups,
            )
        return parse_groupselect_html(
            html_text,
            base_url=base,
            page_url=final_url or target_url,
            title=module.name or "Chọn nhóm",
            my_groups=my_groups,
        )

    def submit_group_action(
        self,
        source: CourseSource,
        module: CourseModuleItem,
        *,
        action_url: str = "",
        fields: Optional[dict[str, str]] = None,
        choice_option_id: Optional[int] = None,
        course_id: int = 0,
    ) -> tuple[bool, str, GroupActivitySummary]:
        modname = (module.modname or "").lower()
        if modname == "choice" and choice_option_id is not None and self._service is not None:
            submit_fn = getattr(self._service, "submit_choice_response", None)
            if callable(submit_fn) and module.instance_id > 0:
                ok = bool(submit_fn(int(module.instance_id), [int(choice_option_id)]))
                refreshed = self.fetch_group_activity(source, module, course_id=course_id)
                return (
                    ok,
                    "Đã lưu lựa chọn thành công." if ok else "Không thể gửi lựa chọn.",
                    refreshed,
                )

        client = self._client_for(source)
        if client is None or not action_url:
            empty = GroupActivitySummary(cmid=module.cmid, modname=modname, title=module.name)
            return (False, "Thiếu đường dẫn xác nhận tham gia/rời nhóm.", empty)
        try:
            _final_url, _html_text, redirected = client.submit_session_form(
                action_url, dict(fields or {})
            )
        except Exception as exc:
            refreshed = self.fetch_group_activity(source, module, course_id=course_id)
            return (False, f"Thao tác nhóm thất bại: {exc}", refreshed)
        if redirected:
            refreshed = self.fetch_group_activity(source, module, course_id=course_id)
            return (False, "Trang thao tác nhóm chuyển hướng bất thường.", refreshed)
        refreshed = self.fetch_group_activity(source, module, course_id=course_id)
        return (True, refreshed.message or "Đã cập nhật trạng thái nhóm.", refreshed)

    # ------------------------------------------------------------------
    # Quiz
    # ------------------------------------------------------------------
    def fetch_quiz_activity(
        self,
        source: CourseSource,
        module: CourseModuleItem,
        *,
        course_id: int,
    ) -> QuizActivitySummary:
        quiz_meta: dict[str, Any] = {}
        access_info: dict[str, Any] = {}
        attempts: list[QuizAttemptInfo] = []
        quiz_id = int(module.instance_id or 0)
        web_url = _safe_http_url(module.web_url or module.url)

        if source == "courses" and self._service is not None:
            try:
                quizzes = self._service.get_quizzes_by_courses([int(course_id)]) or []
                for q in quizzes:
                    if isinstance(q, dict) and (
                        int(q.get("coursemodule") or 0) == int(module.cmid)
                        or (quiz_id > 0 and int(q.get("id") or 0) == quiz_id)
                    ):
                        quiz_meta = q
                        quiz_id = int(q.get("id") or quiz_id)
                        break
            except Exception as exc:
                logger.warning("get_quizzes_by_courses failed: %s", exc)

            if quiz_id > 0:
                access_fn = getattr(self._service, "get_quiz_access_information", None)
                if callable(access_fn):
                    res_acc = access_fn(quiz_id)
                    if isinstance(res_acc, dict):
                        access_info = res_acc
                try:
                    raw_attempts = self._service.get_quiz_attempts(quiz_id) or []
                    att_state_map = {
                        "finished": "Đã hoàn thành",
                        "inprogress": "Đang làm",
                        "overdue": "Quá hạn",
                        "abandoned": "Đã hủy",
                    }
                    origin_base = "https://courses.ut.edu.vn"
                    courses_cli = self._client_for("courses")
                    if courses_cli is not None:
                        origin_base = (
                            getattr(courses_cli, "moodle_site_origin", "")
                            or origin_base
                        ).rstrip("/")
                    for idx, att in enumerate(raw_attempts, start=1):
                        if not isinstance(att, dict):
                            continue
                        att_id = int(att.get("id") or 0)
                        sumgrades = att.get("sumgrades")
                        max_sum = quiz_meta.get("sumgrades")
                        max_grade = quiz_meta.get("grade")
                        score_text = ""
                        if sumgrades is not None:
                            try:
                                sg_f = float(sumgrades)
                                ms_f = float(max_sum) if max_sum is not None else 0.0
                                mg_f = float(max_grade) if max_grade is not None else 0.0
                                if ms_f > 0 and mg_f > 0 and abs(ms_f - mg_f) > 1e-6:
                                    scaled = (sg_f / ms_f) * mg_f
                                    score_text = f"{_format_score_num(scaled)} / {_format_score_num(mg_f)}"
                                elif max_sum is not None:
                                    score_text = f"{sumgrades} / {max_sum}"
                                else:
                                    score_text = str(sumgrades)
                            except (TypeError, ValueError):
                                score_text = (
                                    f"{sumgrades} / {max_sum}" if max_sum else str(sumgrades)
                                )
                        raw_st = str(att.get("state") or "finished").lower()
                        fin_str = _format_epoch_vn(att.get("timefinish"))
                        rev_url = (
                            f"{origin_base}/mod/quiz/review.php?attempt={att_id}"
                            if att_id > 0
                            else ""
                        )
                        attempts.append(
                            QuizAttemptInfo(
                                attempt_number=int(att.get("attempt") or idx),
                                attempt_id=att_id,
                                state=att_state_map.get(raw_st, raw_st),
                                score_text=score_text,
                                time_submitted=fin_str,
                                review_url=rev_url,
                            )
                        )
                except Exception as exc:
                    logger.warning("get_quiz_attempts failed: %s", exc)
        elif source == "thnn":
            client = self._client_for("thnn")
            if client is not None and web_url:
                try:
                    final_u, html_text, redir = client.open_session_url(web_url)
                    if not redir and html_text:
                        base = self._origin_for(client, final_u or web_url)
                        soup_q = BeautifulSoup(html_text, BS4_PARSER)
                        for idx_r, tr in enumerate(
                            soup_q.select("table.quizattemptsummary tbody tr"), start=1
                        ):
                            rev_a = tr.select_one("a[href*='review.php']")
                            href = str(rev_a.get("href") or "").strip() if rev_a else ""
                            full_rev = _safe_http_url(urljoin(f"{base}/", href)) if href else ""
                            qs_rev = parse_qs(urlsplit(full_rev).query)
                            raw_aid = (qs_rev.get("attempt") or ["0"])[0]
                            aid = int(raw_aid) if raw_aid.isdigit() else 0
                            cells = [_clean_text(td) for td in tr.find_all(["td", "th"])]
                            st_txt = cells[1] if len(cells) > 1 else "Đã hoàn thành"
                            sc_txt = cells[2] if len(cells) > 2 else ""
                            attempts.append(
                                QuizAttemptInfo(
                                    attempt_number=idx_r,
                                    attempt_id=aid,
                                    state=st_txt,
                                    score_text=sc_txt,
                                    review_url=full_rev,
                                )
                            )
                except Exception as exc:
                    logger.debug("THNN quiz summary HTML parse skipped: %s", exc)

        grade_methods = {
            1: "Điểm cao nhất",
            2: "Điểm trung bình",
            3: "Lần làm đầu tiên",
            4: "Lần làm cuối cùng",
        }
        return QuizActivitySummary(
            cmid=module.cmid,
            quiz_id=quiz_id,
            title=html.unescape(str(quiz_meta.get("name") or module.name or "Bài kiểm tra")),
            intro=_html_to_readable_markdown(str(quiz_meta.get("intro") or module.description_html or "")),
            time_open=int(quiz_meta.get("timeopen") or 0),
            time_close=int(quiz_meta.get("timeclose") or 0),
            time_limit_seconds=int(quiz_meta.get("timelimit") or 0),
            max_attempts=int(quiz_meta.get("attempts") or 0),
            grade_method=grade_methods.get(int(quiz_meta.get("grademethod") or 0), ""),
            access_rules=tuple(str(r) for r in (access_info.get("accessrules") or []) if r),
            prevent_reasons=tuple(
                str(r) for r in (access_info.get("preventaccessreasons") or []) if r
            ),
            attempts=tuple(attempts),
            can_attempt_in_webview=bool(web_url),
            web_url=web_url,
        )

    def fetch_quiz_review_report(
        self,
        attempt_id: int,
        *,
        source: CourseSource = "courses",
        review_url: str = "",
    ) -> QuizAttemptReviewReport:
        """Fetch and parse structured quiz review questions, options, and feedback."""
        if source == "courses" and self._service is not None and attempt_id > 0:
            fn = getattr(self._service, "get_quiz_attempt_review", None)
            if callable(fn):
                try:
                    data = fn(int(attempt_id))
                    if isinstance(data, dict) and data.get("questions"):
                        return parse_quiz_attempt_review_payload(
                            data, fallback_attempt_id=int(attempt_id)
                        )
                except Exception as exc:
                    logger.warning("get_quiz_attempt_review failed: %s", exc)

        # Fallback: session HTML scrape (/mod/quiz/review.php?attempt=ID&showall=1)
        client = self._client_for(source)
        if client is not None and (review_url or attempt_id > 0):
            base = self._origin_for(client, review_url or f"https://{source}.ut.edu.vn")
            target = _safe_http_url(review_url) or f"{base}/mod/quiz/review.php?attempt={int(attempt_id)}&showall=1"
            if "showall=" not in target:
                sep = "&" if "?" in target else "?"
                target = f"{target}{sep}showall=1"
            try:
                _final, html_text, redir = client.open_session_url(target)
                if not redir and html_text:
                    report = parse_quiz_review_html_page(html_text, attempt_id=int(attempt_id))
                    if report.questions:
                        return report
            except Exception as exc:
                logger.debug("Quiz HTML review fallback skipped: %s", exc)

        return QuizAttemptReviewReport(attempt_id=int(attempt_id))

    def fetch_quiz_review(self, attempt_id: int) -> list[dict[str, Any]]:
        report = self.fetch_quiz_review_report(attempt_id, source="courses")
        items: list[dict[str, Any]] = []
        for q in report.questions:
            items.append(
                {
                    "number": q.display_number,
                    "slot": q.slot,
                    "question_code": q.question_code,
                    "state_kind": q.state_kind,
                    "status": q.status_label,
                    "mark": q.mark,
                    "maxmark": q.max_mark,
                    "question_text": q.question_text,
                    "choices": [
                        {
                            "label": c.label,
                            "text": c.text,
                            "is_selected": c.is_selected,
                            "state": c.state,
                            "feedback": c.feedback,
                        }
                        for c in q.choices
                    ],
                    "right_answer": q.right_answer_text,
                    "general_feedback": q.general_feedback,
                }
            )
        return items

    # ------------------------------------------------------------------
    # Forum
    # ------------------------------------------------------------------
    def fetch_forum_activity(
        self,
        source: CourseSource,
        module: CourseModuleItem,
        *,
        course_id: int,
    ) -> ForumActivitySummary:
        forum_id = int(module.instance_id or 0)
        intro = ""
        discussions: list[ForumDiscussionEntry] = []

        if source == "courses" and self._service is not None:
            forums_fn = getattr(self._service, "get_forums_by_courses", None)
            disc_fn = getattr(self._service, "get_forum_discussions", None)
            if callable(forums_fn):
                for f in forums_fn([int(course_id)]) or []:
                    if isinstance(f, dict) and (
                        int(f.get("cmid") or 0) == int(module.cmid)
                        or (forum_id > 0 and int(f.get("id") or 0) == forum_id)
                    ):
                        forum_id = int(f.get("id") or forum_id)
                        intro = _html_to_readable_markdown(str(f.get("intro") or ""))
                        break
            if forum_id > 0 and callable(disc_fn):
                for d in disc_fn(forum_id) or []:
                    if not isinstance(d, dict):
                        continue
                    discussions.append(
                        ForumDiscussionEntry(
                            discussion_id=int(d.get("discussion") or d.get("id") or 0),
                            name=html.unescape(str(d.get("name") or d.get("subject") or "Chủ đề")),
                            author=str(d.get("userfullname") or ""),
                            created_at=str(d.get("created") or ""),
                            num_replies=int(d.get("numreplies") or 0),
                            pinned=bool(d.get("pinned")),
                            message_preview=_html_to_readable_markdown(str(d.get("message") or ""))[:220],
                        )
                    )

        if not discussions:
            client = self._client_for(source)
            url = _safe_http_url(module.web_url or module.url)
            if client is not None and url:
                try:
                    final_url, html_text, redir = client.open_session_url(url)
                    if not redir:
                        base = self._origin_for(client, final_url or url)
                        discussions = list(parse_forum_html(html_text, base_url=base))
                except Exception:
                    pass

        return ForumActivitySummary(
            cmid=module.cmid,
            forum_id=forum_id,
            title=module.name or "Diễn đàn",
            intro=intro or _html_to_readable_markdown(module.description_html or ""),
            discussions=tuple(discussions),
        )

    def fetch_discussion_posts(
        self, source: CourseSource, discussion_id: int
    ) -> list[ForumPostEntry]:
        if source != "courses" or self._service is None or discussion_id <= 0:
            return []
        posts_fn = getattr(self._service, "get_discussion_posts", None)
        if not callable(posts_fn):
            return []
        posts: list[ForumPostEntry] = []
        for p in posts_fn(int(discussion_id)) or []:
            if not isinstance(p, dict):
                continue
            author_obj = p.get("author") or {}
            author_name = (
                str(author_obj.get("fullname") or "")
                if isinstance(author_obj, dict)
                else ""
            )
            atts = tuple(
                str(a.get("filename") or "")
                for a in (p.get("attachments") or [])
                if isinstance(a, dict) and a.get("filename")
            )
            posts.append(
                ForumPostEntry(
                    post_id=int(p.get("id") or 0),
                    author=author_name or "Thành viên",
                    created_at=str(p.get("timecreated") or ""),
                    subject=html.unescape(str(p.get("subject") or "")),
                    message_text=_html_to_readable_markdown(str(p.get("message") or "")),
                    attachments=atts,
                )
            )
        return posts

    # ------------------------------------------------------------------
    # Book / Page
    # ------------------------------------------------------------------
    def fetch_rich_content(
        self,
        source: CourseSource,
        module: CourseModuleItem,
        *,
        course_id: int,
    ) -> RichActivityPage:
        modname = (module.modname or "").lower()
        title = module.name or "Nội dung"
        intro_md = _html_to_readable_markdown(module.description_html or "")

        if modname == "page" and source == "courses" and self._service is not None:
            try:
                pages = self._service.get_pages_by_courses(int(course_id)) or []
                for p in pages:
                    if isinstance(p, dict) and (
                        int(p.get("coursemodule") or 0) == int(module.cmid)
                        or (module.instance_id and int(p.get("id") or 0) == int(module.instance_id))
                    ):
                        body_md = _html_to_readable_markdown(str(p.get("content") or ""))
                        return RichActivityPage(
                            cmid=module.cmid,
                            modname="page",
                            title=html.unescape(str(p.get("name") or title)),
                            intro_markdown=intro_md,
                            chapters=(
                                BookChapterEntry(
                                    chapter_id="1",
                                    title=html.unescape(str(p.get("name") or title)),
                                    content_markdown=body_md or intro_md,
                                ),
                            ),
                        )
            except Exception:
                pass

        client = self._client_for(source)
        target_url = _safe_http_url(module.web_url or module.url)
        if client is not None and target_url:
            try:
                _final, html_text, redir = client.open_session_url(target_url)
                if not redir and html_text:
                    if modname == "book":
                        chapters = parse_book_html(html_text, title=title)
                    else:
                        chapters = (
                            BookChapterEntry(
                                chapter_id="1",
                                title=title,
                                content_markdown=parse_page_html(html_text, title=title),
                            ),
                        )
                    return RichActivityPage(
                        cmid=module.cmid,
                        modname=modname,
                        title=title,
                        intro_markdown=intro_md,
                        chapters=chapters,
                    )
            except Exception:
                pass

        return RichActivityPage(
            cmid=module.cmid,
            modname=modname,
            title=title,
            intro_markdown=intro_md,
            chapters=(
                BookChapterEntry(chapter_id="1", title=title, content_markdown=intro_md)
                if intro_md
                else ()
            ),
        )
