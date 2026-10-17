"""Safe Markdown rendering and sectioning for the shared Learning Room."""
import html
import re

import bleach
import markdown as markdown_library

_ALLOWED_TAGS = {
    "a", "blockquote", "br", "code", "del", "em", "figcaption", "figure",
    "h1", "h2", "h3", "h4", "h5", "hr", "img", "li", "ol", "p", "pre",
    "s", "strong", "sub", "sup", "table", "tbody", "td", "th", "thead", "tr", "ul",
}
_ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "rel"],
    "img": ["src", "alt", "title", "loading", "width", "height"],
    "th": ["align"], "td": ["align"],
}
_IMAGE_REF = re.compile(r"asset://([0-9a-fA-F-]{36})")
_IMAGE_MARKDOWN = re.compile(r"!\[([^\]]*)\]\(asset://([0-9a-fA-F-]{36})(?:\s+[^)]*)?\)")
_HEADING = re.compile(r"^(#{3,6})\s+(.+?)\s*$")
_DANGEROUS_MARKDOWN = re.compile(
    r"<\s*/?\s*(?:script|style|iframe|object|embed|form|input|button|svg|math)\b"
    r"|<[^>]+\bon[a-z]+\s*=|javascript\s*:|data\s*:\s*text/html",
    re.IGNORECASE,
)


def validate_markdown_source(value):
    """Reject active markup while allowing normal Markdown and escaped examples."""
    value = (value or "").replace("\x00", "")
    if _DANGEROUS_MARKDOWN.search(value):
        from django.core.exceptions import ValidationError
        raise ValidationError("Active HTML, event handlers and executable URLs are not allowed in lesson content.")
    return value


def _resolve_asset_refs(source, asset_urls):
    def image_replacement(match):
        alt, asset_id = match.group(1), match.group(2)
        url = asset_urls.get(asset_id)
        if not url:
            return "**Image awaiting upload: " + html.escape(alt or "course figure") + "**"
        return "![" + alt.replace("]", "") + "](" + url + ")"
    source = _IMAGE_MARKDOWN.sub(image_replacement, source or "")
    return _IMAGE_REF.sub(lambda match: asset_urls.get(match.group(1), ""), source)


def render_safe_markdown(source, asset_urls=None):
    """Convert Markdown to a small, sanitised learner-safe HTML subset."""
    source = validate_markdown_source(source or "")
    resolved = _resolve_asset_refs(source, asset_urls or {})
    rendered = markdown_library.markdown(
        resolved,
        extensions=["tables", "sane_lists", "smarty"],
        output_format="html",
    )
    return bleach.clean(
        rendered,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        protocols=["http", "https", "mailto"],
        strip=True,
    )


def build_lesson_sections(source, asset_urls=None):
    """Group source headings into the three reusable Learning Room tabs."""
    lines = (source or "").splitlines()
    groups = {"idea": [], "examples": [], "practice": []}
    current_group = "idea"
    current_heading = None
    current_lines = []

    def finish_section():
        nonlocal current_lines, current_heading, current_group
        if not current_lines and not current_heading:
            return
        text = "\n".join(current_lines).strip()
        if current_heading:
            text = "### " + current_heading + "\n\n" + text
        groups[current_group].append(text)
        current_lines = []
        current_heading = None

    for line in lines:
        match = _HEADING.match(line)
        if match:
            finish_section()
            current_heading = match.group(2)
            heading = current_heading.casefold()
            if any(word in heading for word in (
                "worked", "compare", "comparison", "chart example", "look closely",
                "case study", "case a", "case b", "figure",
            )):
                current_group = "examples"
            elif any(word in heading for word in (
                "task", "try a new", "practice", "test your", "model response",
                "model reasoning", "feedback", "knowledge check", "apply",
            )):
                current_group = "practice"
            else:
                current_group = "idea"
            continue
        current_lines.append(line)
    finish_section()

    # Preserve any lesson that has not yet adopted section headings.
    if not any(groups.values()):
        groups["idea"] = [source or "Lesson content is being prepared."]

    rendered = {key: "\n\n".join(part for part in parts if part.strip()) for key, parts in groups.items()}
    if not rendered["idea"].strip():
        rendered["idea"] = source or "Lesson content is being prepared."
    if not rendered["examples"].strip():
        rendered["examples"] = (
            "No separately labelled chart comparison is included in this lesson yet. "
            "Read the explanation first, then look for the evidence that would distinguish its alternative outcomes."
        )
    if not rendered["practice"].strip():
        rendered["practice"] = (
            "Describe one observable fact, one possible interpretation, and the next piece of evidence "
            "that would strengthen or weaken your reading."
        )

    figure_count = len(re.findall(r"!\[[^\]]*\]\(", source or ""))
    return {
        "idea_html": render_safe_markdown(rendered["idea"], asset_urls),
        "examples_html": render_safe_markdown(rendered["examples"], asset_urls),
        "practice_html": render_safe_markdown(rendered["practice"], asset_urls),
        "figure_count": figure_count,
    }
