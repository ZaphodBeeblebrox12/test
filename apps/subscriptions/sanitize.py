"""Whitelist HTML sanitizer for admin-entered rich text (Plan.description_html).

Zero-dependency: strips everything except a safe tag/attribute whitelist,
neutralizes scripts/event handlers, escapes text nodes, and forces
rel/target on links. Upgrade path: swap clean_html() internals for
nh3.clean() if richer sanitization is ever needed.
"""
import html
from html.parser import HTMLParser

ALLOWED_TAGS = {"p", "br", "ul", "ol", "li", "strong", "b", "em", "i", "u",
                "s", "a", "blockquote", "code", "pre", "h4", "h5", "h6", "span"}
ALLOWED_ATTRS = {"a": {"href", "title"}}
_BAD_SCHEMES = ("javascript:", "data:", "vbscript:")


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._out = []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            return  # tag stripped; its text content is kept (escaped)
        safe = []
        for name, value in attrs:
            name = name.lower()
            if tag == "a" and name in ALLOWED_ATTRS["a"] and value:
                if name == "href" and value.strip().lower().startswith(_BAD_SCHEMES):
                    continue
                safe.append((name, value))
        attr_txt = "".join(
            f' {n}="{html.escape(str(v), quote=True)}"' for n, v in safe)
        if tag == "a":
            attr_txt += ' target="_blank" rel="noopener noreferrer nofollow"'
        self._out.append(f"<{tag}{attr_txt}>")

    def handle_startendtag(self, tag, attrs):
        if tag in ALLOWED_TAGS:
            self.handle_starttag(tag, attrs)
            if tag != "br":
                self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in ALLOWED_TAGS:
            self._out.append(f"</{tag}>")

    def handle_data(self, data):
        self._out.append(html.escape(data))

    def handle_comment(self, data):
        pass  # strip comments entirely


def clean_html(value):
    """Sanitize HTML to the safe whitelist. Returns '' for empty input."""
    if not value:
        return ""
    parser = _Sanitizer()
    parser.feed(value)
    parser.close()
    return "".join(parser._out).strip()
