"""{% asset 'collection/app.js' %}: the static URL plus ?v=<last modified time>.

Browsers cache scripts and stylesheets, and the development server lets them keep
stale copies after an update (new page, old script). The version changes whenever
the file does, so browsers fetch the new one right away."""
import os

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()
_cache = {}


@register.simple_tag
def asset(path):
    found = finders.find(path)
    version = ""
    if found:
        mtime = int(os.path.getmtime(found))
        version = _cache.get((path, mtime))
        if version is None:
            version = _cache[(path, mtime)] = str(mtime)
    url = static(path)
    return f"{url}?v={version}" if version else url
