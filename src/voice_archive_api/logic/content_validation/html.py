"""Strict limited HTML fragments; explicit balancing avoids browser repair."""

from html import escape, unescape
from html.entities import html5
from html.parser import HTMLParser

import html5lib  # type: ignore[import-untyped]

MAX_HTML_BYTES = 256_000
MAX_TEXT_LENGTH = 128_000
MAX_NODES = 4096
MAX_INLINE_DEPTH = 32
ALLOWED_CLASSES = {"align-left", "align-center", "align-right", "no-indent", "gap-before", "gap-after"}


def valid_characters(value: str) -> None:
    # HTML renderers normalize CR/NUL and invalid numeric references. Reject those
    # inputs instead of letting browser text diverge from stored narration text.
    if any((ord(c) < 32 and c not in "\n\t") or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError("HTML must contain valid Unicode without CR or control characters")


class FragmentParser(HTMLParser):
    def __init__(self, kind: str):
        super().__init__(convert_charrefs=False)
        self.kind = kind
        self.stack: list[str] = []
        self.html_output: list[str] = []
        self.text_parts: list[str] = []
        self.nodes_count = 0
        self.outer_blocks_count = 0
        self.text_length = 0

    def increment_node_count(self) -> None:
        self.nodes_count += 1
        if self.nodes_count > MAX_NODES:
            raise ValueError(f"HTML exceeds {MAX_NODES} nodes")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.increment_node_count()
        if not self.stack:
            allowed = {f"h{i}" for i in range(1, 7)} if self.kind == "heading" else {"p"}
            if self.outer_blocks_count or tag not in allowed:
                raise ValueError("Expected exactly one outer heading block or p matching segment kind")
            self.outer_blocks_count += 1
        elif tag not in {"strong", "em", "br"}:
            # Stack is not empty, so it's an inline element
            raise ValueError(f"Disallowed inline element: {tag}")

        if attrs and (self.stack or len(attrs) != 1 or attrs[0][0] != "class" or attrs[0][1] is None):
            raise ValueError("Only class on the outer block is allowed; duplicate attributes are forbidden")

        attribute = ""
        if attrs:
            classes = (attrs[0][1] or "").replace("\t", " ").replace("\n", " ").split(" ")
            classes = [token for token in classes if token]
            if (
                not classes
                or len(set(classes)) != len(classes)
                or not set(classes) <= ALLOWED_CLASSES
                or sum(c.startswith("align-") for c in classes) > 1
            ):
                raise ValueError("Invalid/duplicate class or multiple alignment classes")
            attribute = ' class="' + " ".join(sorted(classes)) + '"'
        self.html_output.append(f"<{tag}{attribute}>")
        if tag == "br":
            self.text_parts.append("\n")
            self.text_length += 1
        else:
            self.stack.append(tag)
            if len(self.stack) > MAX_INLINE_DEPTH + 1:
                raise ValueError(f"HTML exceeds inline depth {MAX_INLINE_DEPTH}")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "br":
            raise ValueError("Only br may be self-closing")
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack[-1] != tag:
            raise ValueError(f"Unbalanced or implicitly closed HTML element: {tag}")

        self.stack.pop()
        self.html_output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.increment_node_count()
        if not self.stack:
            raise ValueError("Text outside the outer block is forbidden")
        valid_characters(data)
        self.text_length += len(data)
        if self.text_length > MAX_TEXT_LENGTH:
            raise ValueError(f"Derived text exceeds {MAX_TEXT_LENGTH} characters")
        self.text_parts.append(data)
        self.html_output.append(escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if name + ";" not in html5:
            raise ValueError(f"Unknown HTML entity: {name}")
        self.handle_data(html5[name + ";"])

    def handle_charref(self, name: str) -> None:
        try:
            code = int(name[1:], 16) if name[:1].lower() == "x" else int(name)
            value = chr(code)
        except (ValueError, OverflowError) as exc:
            raise ValueError("Invalid numeric character reference") from exc
        valid_characters(value)

        if unescape(f"&#{name};") != value:
            raise ValueError("Numeric reference would be normalized by an HTML renderer")

        self.handle_data(value)

    def handle_comment(self, data: str) -> None:
        raise ValueError("HTML comments are forbidden")

    def handle_decl(self, decl: str) -> None:
        raise ValueError("HTML declarations are forbidden")

    def unknown_decl(self, data: str) -> None:
        raise ValueError("HTML declarations are forbidden")

    def handle_pi(self, data: str) -> None:
        raise ValueError("HTML processing instructions are forbidden")


def parse_fragment(value: str, kind: str) -> tuple[str, str]:
    valid_characters(value)

    if len(value.encode("utf-8")) > MAX_HTML_BYTES:
        raise ValueError(f"HTML exceeds {MAX_HTML_BYTES} UTF-8 bytes")

    # Reject malformed HTML syntax before enforcing our smaller allowed subset.
    try:
        html5lib.HTMLParser(strict=True).parseFragment(value)
    except html5lib.html5parser.ParseError as exc:
        raise ValueError(f"Malformed HTML: {exc}") from exc

    parser = FragmentParser(kind)
    parser.feed(value)
    parser.close()

    plain = "".join(parser.text_parts)

    if parser.stack or parser.outer_blocks_count != 1:
        raise ValueError("HTML requires exactly one explicitly closed outer block")

    if len(plain) > MAX_TEXT_LENGTH:
        raise ValueError(f"Derived text exceeds {MAX_TEXT_LENGTH} characters")

    if not plain.strip():
        raise ValueError("Segment must contain non-whitespace text")

    normalized = "".join(parser.html_output)
    if len(normalized.encode("utf-8")) > MAX_HTML_BYTES:
        raise ValueError(f"Serialized HTML exceeds {MAX_HTML_BYTES} UTF-8 bytes")
    return normalized, plain
