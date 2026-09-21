"""G1 rendering: safe variable substitution + visual blocks -> HTML.

- Variables are {{ name }} tokens substituted from an explicit context, using
  ONLY declared VariableSpec names; unknown tokens are left verbatim (never
  executed).  No eval/exec/template-injection.
- HTML-authored versions are returned verbatim (never normalized/rewritten).
- Visual blocks render to email-safe table HTML.
"""
import re

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def substitute_variables(html, context, allowed_names=None):
    """Replace {{ name }} with context[name].  Only names in allowed_names
    (the version's declared variables) are substituted; others stay verbatim."""
    def _sub(m):
        name = m.group(1)
        if allowed_names is not None and name not in allowed_names:
            return m.group(0)
        if name in context:
            return str(context[name])
        return m.group(0)
    return _VAR_RE.sub(_sub, html)


_BLOCK_RENDERERS = {
    "heading": lambda p: f'<h2 style="margin:16px 0 8px;font-size:20px;color:#1a1a1a;">{_e(p.get("text",""))}</h2>',
    "text": lambda p: f'<p style="margin:0 0 12px;font-size:15px;line-height:1.6;color:#333;">{_e(p.get("text",""))}</p>',
    "button": lambda p: f'<a href="{_attr(p.get("url","#"))}" style="display:inline-block;padding:12px 24px;background:#1a73e8;color:#fff;text-decoration:none;border-radius:4px;">{_e(p.get("label","Click"))}</a>',
    "image": lambda p: f'<img src="{_attr(p.get("src",""))}" alt="{_attr(p.get("alt",""))}" style="max-width:100%;height:auto;display:block;margin:12px 0;" />',
    "divider": lambda p: '<hr style="border:none;border-top:1px solid #e0e0e0;margin:20px 0;" />',
    "spacer": lambda p: f'<div style="height:{int(p.get("height",20))}px;line-height:0;">&nbsp;</div>',
    "footer": lambda p: f'<p style="margin:24px 0 0;font-size:12px;color:#888;">{_e(p.get("text",""))}</p>',
    "social": lambda p: "".join(
        f'<a href="{_attr(l.get("url","#"))}" style="margin-right:12px;">{_e(l.get("label", l.get("url","")))}</a>'
        for l in p.get("links", [])),
    "columns": lambda p: _columns(p),
}


def _e(s):
    import html as _h
    return _h.escape(str(s), quote=False)


def _attr(s):
    import html as _h
    return _h.escape(str(s), quote=True)


def _columns(p):
    cols = p.get("columns", [])
    if not cols:
        return ""
    cells = "".join(
        f'<td style="padding:0 8px;vertical-align:top;" width="{100//max(len(cols),1)}%">{_e(c.get("text",""))}</td>'
        for c in cols)
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{cells}</tr></table>'


def render_blocks_html(blocks):
    """Render a list of {type, props} visual blocks to email-safe HTML."""
    out = []
    for b in blocks or []:
        r = _BLOCK_RENDERERS.get(b.get("type"))
        if r:
            out.append(r(b.get("props", {})))
    return "\n".join(out)


def html_to_plain_text(html):
    """Minimal, deterministic HTML -> plain text (email-safe subset)."""
    import re as _re
    text = _re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    text = _re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = _re.sub(r"(?i)</(p|div|h[1-6]|tr|li)>", "\n", text)
    text = _re.sub(r"<[^>]+>", "", text)
    import html as _h
    return _h.unescape(text).strip()


def render_version(version, context):
    """Render a TemplateVersion to (subject, html, plain_text).

    HTML mode: html is authoritative and returned verbatim (variables
    substituted).  Visual mode: blocks -> HTML (or stored html if present).
    """
    allowed = set(version.variables.values_list("name", flat=True)) if version.pk else None
    html = version.html
    if version.editor_mode == version.EditorMode.VISUAL and version.visual_blocks and not html.strip():
        html = render_blocks_html(version.visual_blocks)
    html = substitute_variables(html, context, allowed)
    subject = substitute_variables(version.subject, context, allowed)
    plain = version.plain_text or html_to_plain_text(html)
    plain = substitute_variables(plain, context, allowed)
    return subject, html, plain
