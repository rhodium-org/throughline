# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Surgical, comment-preserving edits to a TOML document (SR-0183).

A schema change rewrites one key of ``throughline.toml`` and must leave the rest
of the file — above all its comments — exactly as it found it. Round-tripping
through a serialiser would parse to a data structure and re-emit it, which is
faultless for the values and deletes every comment in the file, so this edits the
text instead: it locates the span of the table or key it was asked for and
replaces only that.

The editor is deliberately narrow. It knows table headers, keys (dotted keys
included), and values that are strings, booleans, integers, arrays of those, or
inline tables of those. Anything else raises :class:`TomlEditError` rather than
being edited on a guess, because a wrong guess here corrupts the one file that
decides whether the rest of the project is valid. It refuses:

* an array-of-tables header, or a multi-line basic string;
* a value it cannot re-render;
* a comment sitting beside one member of a value, which describes that member
  and has nowhere to go once the value is re-rendered.

That last one is why adding to an array is its own operation rather than setting
the key to a longer list: an addition leaves the members before it alone, so it
is written in place and the grouping comments real vocabularies carry survive.
Only a change that must re-render the whole value can still be refused.

Keys and table names are carried as their *parts* rather than as one dotted
string, because that is the only form in which ``"attrs"."origin"`` — ``origin``
inside ``attrs`` — stays distinct from ``"attrs.origin"``, a single name that
happens to contain a dot. Callers naming a key as a plain string get the dotted
reading, which is what they always mean; the rare literal dot is passed as a
one-member tuple.

Callers compute *values* from the parsed config (``tomllib``) and ask this module
only to place them, so parsing and editing never drift into two half-parsers.
"""
from __future__ import annotations

import re
import textwrap

# A table header line: [name] or [ name ]. Array-of-tables ([[name]]) is matched
# so it can be refused by name rather than silently mistaken for a table.
_HEADER = re.compile(r"^\s*\[(\[?)\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")
_COMMENT_OR_BLANK = re.compile(r"^\s*(#.*)?$")

_COMMENT_WIDTH = 79


class TomlEditError(ValueError):
    """The document holds a construct this editor will not edit on a guess."""


def render_value(value) -> str:
    """Render a Python value as TOML. Supports the value kinds throughline's own
    configuration uses; anything else raises rather than being approximated."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        if "\n" in value:
            raise TomlEditError("cannot render a multi-line string")
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(render_value(v) for v in value) + "]"
    if isinstance(value, dict):
        body = ", ".join(f"{_render_key(k)} = {render_value(v)}"
                         for k, v in value.items())
        return "{ " + body + " }" if body else "{}"
    raise TomlEditError(f"cannot render a value of type {type(value).__name__}")


_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _render_key(key: str) -> str:
    """One key, quoted only if it has to be. The key is a literal name, so a dot
    in it is part of the name and the quotes are what keep it that way."""
    return key if _BARE_KEY.match(key) else '"' + key.replace('"', '\\"') + '"'


def _render_path(path: tuple[str, ...]) -> str:
    """A key as it is written on the left of an assignment or in a header. The
    parts are what carry the meaning — ``("attrs", "origin")`` is ``origin``
    inside ``attrs`` and writes as ``attrs.origin``, while the one-part
    ``("attrs.origin",)`` is a single name and writes as ``"attrs.origin"``. The
    quotes are the difference, so each part is quoted on its own."""
    return ".".join(_render_key(part) for part in path)


def _as_path(key: str | tuple[str, ...]) -> tuple[str, ...]:
    """Callers may name a key either way. A plain string is read as a dotted
    path, which is what every caller in this package means; a name that really
    does contain a dot is passed as a one-member tuple."""
    return key if isinstance(key, tuple) else tuple(key.split("."))


def render_assignment(key: str | tuple[str, ...], value) -> list[str]:
    """``key = value`` as one or more lines. A long array is wrapped and its
    continuations aligned under the opening bracket, which is how these files are
    written by hand — re-emitting a ten-member vocabulary as one 150-column line
    would degrade the document a little on every edit."""
    head = f"{_render_path(_as_path(key))} = "
    one = head + render_value(value)
    if len(one) <= _COMMENT_WIDTH or not isinstance(value, list) or not value:
        return [one]
    indent = " " * (len(head) + 1)
    lines: list[str] = []
    current = head + "["
    for i, member in enumerate(value):
        piece = render_value(member) + ("," if i < len(value) - 1 else "]")
        if len(current) + 1 + len(piece) > _COMMENT_WIDTH and current.strip() != "[":
            lines.append(current)
            current = indent + piece
        else:
            current += (" " if current.endswith(",") else "") + piece
    lines.append(current)
    return lines


def comment_block(text: str, indent: str = "") -> list[str]:
    """Wrap ``text`` into TOML comment lines (SR-0184)."""
    out: list[str] = []
    for para in text.strip().splitlines():
        para = para.strip()
        if not para:
            out.append(f"{indent}#")
            continue
        out.extend(f"{indent}# {line}" for line in
                   textwrap.wrap(para, width=_COMMENT_WIDTH - len(indent) - 2))
    return out


class TomlDocument:
    """One TOML file held as lines, edited in place."""

    def __init__(self, text: str):
        self._lines = text.splitlines()
        self._final_newline = text.endswith("\n") or not text

    def text(self) -> str:
        out = "\n".join(self._lines)
        return out + "\n" if self._final_newline and out else out

    # ---------------------------------------------------------------- lookup

    def _headers(self) -> list[tuple[int, tuple[str, ...], bool]]:
        """Every table header, as ``(line, path, is_array_of_tables)``. An
        array-of-tables still bounds the table before it, so it is recorded
        rather than rejected; only a request to edit one is refused."""
        return [(i, tuple(_unquote(p.strip())
                          for p in _split_outside_quotes(m.group(2).strip())),
                 bool(m.group(1)))
                for i, line in enumerate(self._lines)
                if (m := _HEADER.match(line))]

    def _table_span(self, name: str | tuple[str, ...]) -> tuple[int, int] | None:
        """``(header_index, end)`` for ``name``; ``end`` is exclusive and stops at
        the next header of any kind. ``None`` when the table is not present."""
        want = _as_path(name)
        headers = self._headers()
        for pos, (idx, found, is_array) in enumerate(headers):
            if found != want:
                continue
            if is_array:
                raise TomlEditError(
                    f"[[{_render_path(want)}]] is an array-of-tables; this "
                    "editor does not edit them")
            end = headers[pos + 1][0] if pos + 1 < len(headers) else len(self._lines)
            return idx, end
        return None

    def has_table(self, name: str | tuple[str, ...]) -> bool:
        return self._table_span(name) is not None

    def _key_span(self, start: int, end: int,
                  key: tuple[str, ...]) -> tuple[int, int] | None:
        """``(first, last_exclusive)`` of ``key = value`` between ``start`` and
        ``end``, following a value that spans lines."""
        i = start
        while i < end:
            line = self._lines[i]
            found = _split_key(line)
            if found is not None and found[0] == key:
                return i, _value_end(self._lines, i, end)
            i += 1
        return None

    def _carried_comment(self, first: int, last: int) -> str:
        """The trailing note on a value about to be rewritten. A comment on the
        closing line follows the whole value, so it belongs to the key and is
        carried over. A comment on an earlier line sits beside one member of the
        value, and once the value is re-rendered there is no member to put it
        back beside — so that is refused rather than dropped (SR-0183)."""
        for i in range(first, last - 1):
            if _scan(self._lines[i], first=i == first)[1] is not None:
                raise TomlEditError(
                    f"line {i + 1} comments one part of this value; rewriting "
                    "the value would drop that comment. Move it above the key, "
                    "or make this edit by hand")
        col = _scan(self._lines[last - 1], first=last - 1 == first)[1]
        return self._lines[last - 1][col:] if col is not None else ""

    def _comment_start(self, index: int, floor: int) -> int:
        """Walk back over the comment lines immediately above ``index`` so a
        removal takes the prose that introduced the key with it."""
        i = index
        while i - 1 > floor and self._lines[i - 1].lstrip().startswith("#"):
            i -= 1
        return i

    # ----------------------------------------------------------------- edits

    def set_key(self, table: str | tuple[str, ...], key: str | tuple[str, ...],
                value, *, because: str | None = None,
                comment: str | None = None) -> None:
        """Set ``table.key`` to ``value``, creating the table or the key when
        absent. ``because`` is recorded as a comment above the key (SR-0184);
        ``comment`` introduces a table created here."""
        rendered = render_assignment(key, value)
        span = self._table_span(table)
        if span is None:
            self.add_table(table, comment=comment)
            span = self._table_span(table)
        assert span is not None
        start, end = span
        found = self._key_span(start + 1, end, _as_path(key))
        note = comment_block(because) if because else []
        if found is None:
            at = _last_content_line(self._lines, start + 1, end)
            self._lines[at:at] = note + rendered
            return
        first, last = found
        kept = self._carried_comment(first, last)
        if kept:
            rendered[-1] += "  " + kept
        self._lines[first:last] = note + rendered

    def add_to_array(self, table: str | tuple[str, ...],
                     key: str | tuple[str, ...], additions: list, *,
                     because: str | None = None) -> None:
        """Add members to the end of an array already in the document, leaving
        every line that is already there exactly as it is.

        An addition does not disturb the members before it, so writing it in
        place keeps the author's own layout — and above all any comment grouping
        the members, which a re-render of the whole value would have nowhere to
        put back and so has to refuse (SR-0183). Only the line the new members
        land on is rewritten, and it is broken rather than run past the margin.
        """
        span = self._table_span(table)
        found = (self._key_span(span[0] + 1, span[1], _as_path(key))
                 if span is not None else None)
        if found is None:
            raise TomlEditError(
                f"no key '{_render_path(_as_path(key))}' in table "
                f"[{_render_path(_as_path(table))}] to add to")
        first, last = found
        opened = self._array_open(first)
        cline, ccol = self._array_close(first, last)
        body = ", ".join(render_value(v) for v in additions)
        pre, post = self._lines[cline][:ccol], self._lines[cline][ccol:]

        if pre.strip():                       # the last member shares this line
            head = pre.rstrip()
            gap = "" if head.endswith("[") else " " if head.endswith(",") else ", "
            if len(head + gap + body + post) <= _COMMENT_WIDTH:
                self._lines[cline] = head + gap + body + post
            else:
                indent = " " * (opened + 1 if cline == first
                                else len(pre) - len(pre.lstrip()))
                self._lines[cline:cline + 1] = [
                    head if head.endswith(("[", ",")) else head + ",",
                    indent + body + post]
        else:                                 # the bracket closes on its own line
            at = _last_content_line(self._lines, first + 1, cline)
            tail = self._lines[at - 1]
            content = tail[:_scan(tail, first=at - 1 == first)[1]].rstrip()
            trailing = "," if content.endswith(",") else ""
            if not content.endswith((",", "[")):
                self._lines[at - 1] = tail[:len(content)] + "," + tail[len(content):]
            indent = " " * (len(tail) - len(tail.lstrip()) if at - 1 != first
                            else opened + 1)
            self._lines[cline:cline] = [indent + body + trailing]
        if because:
            self._lines[first:first] = comment_block(because)

    def _array_open(self, first: int) -> int:
        """The column of the bracket opening the array assigned on ``first``.
        Nothing but whitespace can sit between the ``=`` and it, so the first
        non-blank character is the bracket or the value is not an array."""
        line = self._lines[first]
        at = line.index("=") + 1
        at += len(line[at:]) - len(line[at:].lstrip())
        if at >= len(line) or line[at] != "[":
            raise TomlEditError(
                f"the value on line {first + 1} is not an array; refusing to add "
                "a member to it")
        return at

    def _array_close(self, first: int, last: int) -> tuple[int, int]:
        depth = 0
        for i in range(first, last):
            depth, _, close = _scan(self._lines[i], first=i == first, depth=depth)
            if close is not None:
                return i, close
        raise TomlEditError(
            f"the value starting at line {first + 1} is not closed; refusing to "
            "edit a document this editor cannot follow")

    def remove_key(self, table: str | tuple[str, ...],
                   key: str | tuple[str, ...]) -> None:
        span = self._table_span(table)
        if span is None:
            raise TomlEditError(f"no table [{_render_path(_as_path(table))}] "
                                "in this document")
        start, end = span
        found = self._key_span(start + 1, end, _as_path(key))
        if found is None:
            raise TomlEditError(
                f"no key '{_render_path(_as_path(key))}' in table "
                f"[{_render_path(_as_path(table))}]")
        first, last = found
        del self._lines[self._comment_start(first, start):last]

    def add_table(self, name: str | tuple[str, ...], *,
                  comment: str | None = None) -> None:
        written = _render_path(_as_path(name))
        if self.has_table(name):
            raise TomlEditError(f"table [{written}] already exists")
        block = comment_block(comment) if comment else []
        tail = [""] if self._lines and self._lines[-1].strip() else []
        self._lines.extend(tail + block + [f"[{written}]"])

    def remove_table(self, name: str | tuple[str, ...]) -> None:
        span = self._table_span(name)
        if span is None:
            raise TomlEditError(f"no table [{_render_path(_as_path(name))}] "
                                "in this document")
        start, end = span
        del self._lines[self._comment_start(start, -1):end]

    def array_table_spans(self,
                          name: str | tuple[str, ...]) -> list[tuple[int, int]]:
        """``(header_index, end)`` for each ``[[name]]`` block, in file order.
        ``end`` is exclusive and stops at the next header of any kind."""
        want = _as_path(name)
        headers = self._headers()
        return [(idx, headers[pos + 1][0] if pos + 1 < len(headers)
                 else len(self._lines))
                for pos, (idx, found, is_array) in enumerate(headers)
                if found == want and is_array]

    def add_array_table(self, name: str | tuple[str, ...], values: dict, *,
                        because: str | None = None) -> None:
        """Append a ``[[name]]`` block holding ``values``.

        Appended rather than written in place, because the members of an
        array-of-tables are ordered and the ones already there are not this
        change's business — the same reason :meth:`add_to_array` writes at the
        end of an array (SR-0183)."""
        written = _render_path(_as_path(name))
        body: list[str] = []
        for key, value in values.items():
            body.extend(render_assignment(key, value))
        note = comment_block(because) if because else []
        tail = [""] if self._lines and self._lines[-1].strip() else []
        self._lines.extend(tail + note + [f"[[{written}]]"] + body)

    def remove_array_table(self, name: str | tuple[str, ...], index: int, *,
                           because: str | None = None) -> None:
        """Remove the ``index``-th (0-based) ``[[name]]`` block, taking the
        comment that introduced it with it.

        ``because`` is left behind in the block's place. A removal has no key or
        table left to hang its reason on, and the gap where the rule used to be
        is the one spot where a reader looking for it will pass (SR-0184)."""
        spans = self.array_table_spans(name)
        written = _render_path(_as_path(name))
        if index < 0 or index >= len(spans):
            raise TomlEditError(
                f"this document has {len(spans)} [[{written}]] block(s); "
                f"there is no #{index + 1}")
        start, end = spans[index]
        floor = spans[index - 1][1] - 1 if index else -1
        at = self._comment_start(start, floor)
        self._lines[at:end] = comment_block(because) if because else []

    def note_table(self, name: str | tuple[str, ...], because: str) -> None:
        """Record ``because`` above the header of an existing table."""
        span = self._table_span(name)
        if span is None:
            raise TomlEditError(f"no table [{_render_path(_as_path(name))}] "
                                "in this document")
        self._lines[span[0]:span[0]] = comment_block(because)


def _split_key(line: str) -> tuple[tuple[str, ...], str] | None:
    """``(path, remainder)`` when ``line`` starts a ``key = value`` assignment.
    The key comes back as its parts, because that is the only form that keeps
    ``"attrs"."origin"`` (two parts) apart from ``"attrs.origin"`` (one)."""
    if _COMMENT_OR_BLANK.match(line) or _HEADER.match(line):
        return None
    head, sep, rest = line.partition("=")
    if not sep:
        return None
    key = head.strip()
    if not key or key.endswith((",", "[", "{")):
        return None
    parts = [p.strip() for p in _split_outside_quotes(key)]
    if not all(p and (_BARE_KEY.match(p) or _quoted(p)) for p in parts):
        return None
    return tuple(_unquote(p) for p in parts), rest


def _split_outside_quotes(key: str) -> list[str]:
    """Split a key on the dots that separate its parts. A dot inside a quoted
    part is part of the name, not a separator, and is left where it is."""
    parts: list[str] = []
    current = ""
    quote = ""
    i = 0
    while i < len(key):
        ch = key[i]
        if quote:
            if ch == "\\" and quote == '"' and i + 1 < len(key):
                current += key[i:i + 2]
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == ".":
            parts.append(current)
            current = ""
            i += 1
            continue
        current += ch
        i += 1
    parts.append(current)
    return parts


def _quoted(part: str) -> bool:
    return len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'"


def _unquote(part: str) -> str:
    return part[1:-1] if _quoted(part) else part


def _value_end(lines: list[str], start: int, end: int) -> int:
    """Index just past the value beginning on ``lines[start]``, following the
    brackets and braces of a value written over several lines."""
    depth = 0
    i = start
    while i < end:
        depth = _scan(lines[i], first=i == start, depth=depth)[0]
        i += 1
        if depth <= 0:
            return i
    raise TomlEditError(
        f"the value starting at line {start + 1} is not closed before the next "
        "table; refusing to edit a document this editor cannot follow")


def _scan(line: str, *, first: bool,
          depth: int = 0) -> tuple[int, int | None, int | None]:
    """The bracket depth after ``line``, the column where its comment starts, and
    the column of the bracket that brought the depth back to zero — each read past
    string bodies, so a ``#`` or a bracket inside a string counts as none of them.
    ``depth`` is what the lines before this one left open."""
    quote = ""
    close = None
    i = 0
    # Only the value half of the first line counts; a bracket in the key would
    # otherwise be read as opening the value.
    if first:
        i = line.index("=") + 1
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return depth, i, close
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0 and close is None:
                close = i
        i += 1
    return depth, None, close


def _last_content_line(lines: list[str], start: int, end: int) -> int:
    """Where a new key should be inserted in a table: after its last assignment,
    before the blank lines and comments that belong to whatever follows."""
    at = end
    while at > start and _COMMENT_OR_BLANK.match(lines[at - 1]):
        at -= 1
    return at
