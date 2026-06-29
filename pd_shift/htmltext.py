from __future__ import annotations

import html
import re
from html.parser import HTMLParser


class _PlainTextParser(HTMLParser):
    _BLOCK = frozenset(
        {"p", "div", "li", "ul", "ol", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}
    )
    _SKIP = frozenset({"script", "style", "head"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in {"br", "hr"}:
            self._append_newline()
        elif tag in self._BLOCK:
            self._append_newline()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in self._BLOCK:
            self._append_newline()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._parts.append(data)

    def _append_newline(self) -> None:
        if not self._parts or not self._parts[-1].endswith("\n"):
            self._parts.append("\n")

    def get_text(self) -> str:
        return "".join(self._parts)


def html_to_plain(text: str) -> str:
    text = text.strip()
    if not text:
        return ""

    if "<" not in text and ">" not in text:
        return _normalize_plain(html.unescape(text))

    parser = _PlainTextParser()
    parser.feed(text)
    parser.close()
    return _normalize_plain(html.unescape(parser.get_text()))


def _normalize_plain(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    lines = [line for line in lines if line]
    return "\n".join(lines)
