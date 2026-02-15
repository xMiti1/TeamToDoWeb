import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

IMG_RE = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
LINK_RE = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')


def _safe_url(url):
    url = (url or '').strip()
    if url.startswith('/media/') or url.startswith('http://') or url.startswith('https://'):
        return url
    return '#'


@register.filter(name='render_rich_text')
def render_rich_text(value):
    text = escape(value or '')

    def repl_img(match):
        alt = escape(match.group(1))
        src = escape(_safe_url(match.group(2)))
        return f'<img src="{src}" alt="{alt}" class="img-fluid rounded border mt-1 mb-1" style="max-width:320px;max-height:220px;">'

    def repl_link(match):
        label = escape(match.group(1))
        href = escape(_safe_url(match.group(2)))
        return f'<a href="{href}" target="_blank" rel="noopener">{label}</a>'

    text = IMG_RE.sub(repl_img, text)
    text = LINK_RE.sub(repl_link, text)
    text = text.replace('\n', '<br>')
    return mark_safe(text)
