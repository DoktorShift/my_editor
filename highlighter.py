#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
How code and Markdown look on screen, with nothing of it in the document.

The rules for each language (:class:`CodeRules`, :func:`code_rules`) are
shared by the two highlighters, so a Python file and a Python code block
in an article are colored alike:

- :class:`SyntaxHighlighter` colors a whole code file, in one language
  known by the file's extension or guessed from its text;
- :class:`RichTextLook` gives a document with Markdown structure its
  looks: code blocks in the code font, colored by the language their
  fence names (aliases such as "py" or "sh" included); inline code on a
  tinted chip; links in the link color; footnote marks raised, their
  definitions quieter.

One highlighter per document: two on one document undo each other's
work. Every format set here is layout only: nothing is saved, published
or counted as formatting.
"""

import re
import os
from functools import lru_cache
from typing import Callable

from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont, QTextFormat

import rich_text
from fonts import code_font, monospace_family


_EXT_TO_LANG: dict[str, str] = {
    '.py': 'python', '.pyw': 'python',
    '.js': 'javascript', '.mjs': 'javascript', '.cjs': 'javascript',
    '.jsx': 'javascript',
    '.ts': 'typescript', '.tsx': 'typescript',
    '.css': 'css', '.scss': 'css', '.sass': 'css', '.less': 'css',
    '.json': 'json', '.jsonc': 'json',
    '.sh': 'bash', '.bash': 'bash', '.zsh': 'bash', '.fish': 'bash',
    '.c': 'c', '.h': 'c',
    '.cpp': 'cpp', '.cc': 'cpp', '.cxx': 'cpp', '.hpp': 'cpp',
    '.rs': 'rust',
    '.go': 'go',
    '.java': 'java',
    '.yaml': 'yaml', '.yml': 'yaml',
    '.toml': 'toml',
    '.sql': 'sql',
    '.html': 'html', '.htm': 'html',
    '.md': 'markdown', '.markdown': 'markdown',
    '.rmd': 'markdown',
}

LANGUAGE_DISPLAY_NAMES: dict[str, str] = {
    'python':     'Python',
    'javascript': 'JavaScript',
    'typescript': 'TypeScript',
    'html':       'HTML',
    'css':        'CSS',
    'json':       'JSON',
    'bash':       'Shell',
    'c':          'C',
    'cpp':        'C++',
    'rust':       'Rust',
    'go':         'Go',
    'java':       'Java',
    'yaml':       'YAML',
    'toml':       'TOML',
    'sql':        'SQL',
    'markdown':   'Markdown',
}

# Token colors for dark / light themes
_DARK: dict[str, str] = {
    'keyword':   '#569CD6',
    'keyword2':  '#C586C0',
    'string':    '#CE9178',
    'comment':   '#6A9955',
    'number':    '#B5CEA8',
    'func':      '#DCDCAA',
    'type':      '#4EC9B0',
    'decorator': '#C586C0',
    'tag':       '#569CD6',
    'attr':      '#9CDCFE',
    'var':       '#9CDCFE',
    'key':       '#9CDCFE',
}

_LIGHT: dict[str, str] = {
    'keyword':   '#0070C1',
    'keyword2':  '#AF00DB',
    'string':    '#A31515',
    'comment':   '#008000',
    'number':    '#098658',
    'func':      '#795E26',
    'type':      '#267F99',
    'decorator': '#AF00DB',
    'tag':       '#800000',
    'attr':      '#FF0000',
    'var':       '#001080',
    'key':       '#001080',
}


# What code fences call a language, by the names this module knows it by
# (the keys of LANGUAGE_DISPLAY_NAMES).
_FENCE_ALIASES: dict[str, str] = {
    'py': 'python', 'python3': 'python', 'py3': 'python',
    'js': 'javascript', 'jsx': 'javascript', 'node': 'javascript', 'mjs': 'javascript',
    'ts': 'typescript', 'tsx': 'typescript',
    'sh': 'bash', 'shell': 'bash', 'zsh': 'bash', 'console': 'bash', 'shellsession': 'bash',
    'yml': 'yaml',
    'rs': 'rust',
    'golang': 'go',
    'c++': 'cpp', 'cc': 'cpp', 'cxx': 'cpp', 'hpp': 'cpp', 'h++': 'cpp',
    'h': 'c',
    'xml': 'html', 'xhtml': 'html', 'svg': 'html', 'htm': 'html',
    'scss': 'css', 'sass': 'css', 'less': 'css',
    'jsonc': 'json', 'json5': 'json',
    'postgresql': 'sql', 'postgres': 'sql', 'mysql': 'sql', 'sqlite': 'sql', 'psql': 'sql',
    'md': 'markdown',
}


def canonical_language(name: str | None) -> str | None:
    """The language a code fence names (``py``, ``Python``, ``sh``), as this
    module knows it, or None when it knows no such language."""
    key = (name or "").strip().lower()
    key = _FENCE_ALIASES.get(key, key)
    return key if key in LANGUAGE_DISPLAY_NAMES else None


def detect_language(path: str | None) -> str | None:
    if not path:
        return None
    ext = os.path.splitext(path)[1].lower()
    return _EXT_TO_LANG.get(ext)


def detect_language_from_content(text: str) -> str | None:
    """Heuristic language detection from text content (for untitled buffers)."""
    if not text or not text.strip():
        return None

    lines = text.splitlines()
    first = lines[0].strip() if lines else ''

    # Shebang
    if first.startswith('#!'):
        if 'python' in first:
            return 'python'
        if 'node' in first or 'deno' in first:
            return 'javascript'
        if 'bash' in first or first.endswith('/sh') or '/sh ' in first:
            return 'bash'
        if 'zsh' in first or 'fish' in first:
            return 'bash'

    sample = lines[:60]
    scores: dict[str, int] = {}
    hits: dict[str, int] = {}   # number of distinct lines that matched each lang

    def inc(lang: str, n: int = 1):
        scores[lang] = scores.get(lang, 0) + n

    def hit(lang: str, n: int = 1):
        """Score points AND count this as a matching line."""
        inc(lang, n)
        hits[lang] = hits.get(lang, 0) + 1

    for line in sample:
        s = line.strip()
        if not s:
            continue
        # Python — only strong structural indicators
        if re.match(r'(def \w+\s*\(|class \w+[\s:(]|from \w[\w.]* import|elif |except[\s:]|async def \w)', s):
            hit('python', 2)
        if re.match(r'(if __name__|@\w+\s*$|@\w+\()', s):
            hit('python')
        # JavaScript
        if re.match(r'(const |let |var |function \w|export (default )?|module\.exports)', s):
            hit('javascript', 2)
        if re.search(r'=>\s*[\{\(]', s):
            hit('javascript')
        if re.match(r"import .+ from ['\"]", s):
            hit('javascript', 2)
        # TypeScript (on top of JS)
        if re.match(r'(interface \w|type \w+ =|declare |readonly |abstract class)', s):
            hit('typescript', 3)
        if re.search(r':\s*(string|number|boolean|void|any|unknown)\b', s):
            hit('typescript', 2)
        # Rust
        if re.match(r'(fn \w|let mut |impl \w|pub fn |mod \w+\s*\{|struct \w|enum \w|trait \w|use \w+::)', s):
            hit('rust', 2)
        # Go
        if re.match(r'(func \w|type \w+ struct|import \()', s):
            hit('go', 2)
        if s == 'package main':
            hit('go', 5)
        elif re.match(r'package \w+', s):
            hit('go', 2)
        # Bash
        if re.match(r'(if \[|if \[\[)', s):
            hit('bash', 2)
        if re.match(r'\$\(', s):
            hit('bash', 2)
        if s in ('do', 'done', 'fi', 'then', 'esac'):
            hit('bash', 2)
        # C
        if re.match(r'#include\s*[<"]', s):
            hit('c', 3)
        if re.match(r'(int main\s*\(|printf\s*\(|typedef )', s):
            hit('c', 2)
        # C++
        if re.match(r'(std::|cout\s*<<|cin\s*>>|namespace \w|template\s*<)', s):
            hit('cpp', 2)
            inc('c')
        # Java
        if re.match(r'(public class \w|private \w|protected \w|@Override|System\.out\.)', s):
            hit('java', 2)
        # HTML
        if re.match(r'(<html|<!DOCTYPE|<head|<body|<div)', s, re.IGNORECASE):
            hit('html', 3)
        elif re.match(r'<\w[\w-]*[\s>/]', s):
            hit('html')
        # CSS
        if re.match(r'[\w.#:\*\[\]>~+]+\s*\{', s):
            hit('css', 2)
        if re.match(r'[\w-]+\s*:\s*[^{}\n]+;', s):
            hit('css', 2)
        # JSON
        if re.match(r'"[\w-]+"\s*:', s):
            hit('json', 2)
        # SQL — only unambiguous statement starters
        if re.match(r'(SELECT\s+\*?[\w,\s]+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|CREATE\s+TABLE|DROP\s+TABLE)\b', s, re.IGNORECASE):
            hit('sql', 4)
        # YAML — skip, too ambiguous for arbitrary text

    if not scores:
        return None

    # TypeScript absorbs JavaScript when TS-specific patterns are found
    if scores.get('typescript', 0) >= 4:
        scores.pop('javascript', None)
    # C++ absorbs C when C++-specific patterns found
    if scores.get('cpp', 0) >= 4:
        scores.pop('c', None)

    best = max(scores, key=scores.get)
    # Require: total score ≥ 8 AND at least 2 distinct matching lines
    if scores[best] >= 8 and hits.get(best, 0) >= 2:
        return best
    return None


SetFormat = Callable[[int, int, QTextCharFormat], None]


class CodeRules:
    """The coloring rules of one language in one theme: single-line
    patterns, and constructs that run over several lines (a block comment,
    a Python docstring). Built once per language and theme (code_rules)."""

    def __init__(self, language: str | None, is_dark: bool = True):
        self._language = language
        self._is_dark = is_dark
        # (pattern, format) for single-line rules
        self._rules: list[tuple[re.Pattern, QTextCharFormat]] = []
        # (start_pattern, end_pattern, format) for multi-line constructs
        self._ml_rules: list[tuple[re.Pattern, re.Pattern, QTextCharFormat]] = []
        self._build_rules()

    # ------------------------------------------------------------------
    # Format helpers
    # ------------------------------------------------------------------

    def _fmt(self, color_key: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
        palette = _DARK if self._is_dark else _LIGHT
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(palette.get(color_key, '#D4D4D4')))
        if bold:
            fmt.setFontWeight(QFont.Bold)
        if italic:
            fmt.setFontItalic(True)
        return fmt

    def _add(self, pattern: str, color_key: str, bold=False, italic=False, flags: int = 0):
        self._rules.append((re.compile(pattern, flags), self._fmt(color_key, bold, italic)))

    def _add_ml(self, start: str, end: str, color_key: str, italic: bool = False):
        self._ml_rules.append((
            re.compile(start),
            re.compile(end),
            self._fmt(color_key, italic=italic),
        ))

    # ------------------------------------------------------------------
    # Rule sets per language
    # ------------------------------------------------------------------

    def _build_rules(self):
        self._rules = []
        self._ml_rules = []
        lang = self._language
        dispatch = {
            'python':     self._python,
            'javascript': self._javascript,
            'typescript': self._typescript,
            'html':       self._html,
            'css':        self._css,
            'json':       self._json,
            'bash':       self._bash,
            'c':          self._c,
            'cpp':        self._cpp,
            'rust':       self._rust,
            'go':         self._go,
            'java':       self._java,
            'yaml':       self._yaml,
            'toml':       self._toml,
            'sql':        self._sql,
        }
        build = dispatch.get(lang)
        if build:
            build()

    def _python(self):
        kw = (r'\b(False|None|True|and|as|assert|async|await|break|class|continue'
              r'|def|del|elif|else|except|finally|for|from|global|if|import|in|is'
              r'|lambda|nonlocal|not|or|pass|raise|return|try|while|with|yield)\b')
        builtins = (r'\b(print|len|range|type|int|str|list|dict|set|tuple|bool|float'
                    r'|open|super|self|cls|isinstance|hasattr|getattr|setattr'
                    r'|enumerate|zip|map|filter|sorted|reversed|sum|min|max|abs|round)\b')
        self._add(kw, 'keyword', bold=True)
        self._add(builtins, 'type')
        self._add(r'@[\w.]+', 'decorator')
        self._add(r'(?<=def )\w+', 'func')
        self._add(r'(?<=class )\w+', 'type', bold=True)
        self._add(r'"[^"\\]*(?:\\.[^"\\]*)*"', 'string')
        self._add(r"'[^'\\]*(?:\\.[^'\\]*)*'", 'string')
        self._add(r'#[^\n]*', 'comment', italic=True)
        self._add(r'\b\d+(\.\d*)?(j|[eE][+\-]?\d+)?\b', 'number')
        self._add_ml(r'"""', r'"""', 'string')
        self._add_ml(r"'''", r"'''", 'string')

    def _javascript(self):
        kw = (r'\b(break|case|catch|class|const|continue|debugger|default|delete|do'
              r'|else|export|extends|finally|for|from|function|if|import|in|instanceof'
              r'|let|new|of|return|static|super|switch|this|throw|try|typeof|var|void'
              r'|while|with|yield|async|await)\b')
        literals = r'\b(true|false|null|undefined|NaN|Infinity)\b'
        self._add(kw, 'keyword', bold=True)
        self._add(literals, 'keyword2')
        self._add(r'(?<=function )\w+', 'func')
        self._add(r'(?<=class )\w+', 'type', bold=True)
        self._add(r'`[^`\\]*(?:\\.[^`\\]*)*`', 'string')
        self._add(r'"[^"\\]*(?:\\.[^"\\]*)*"', 'string')
        self._add(r"'[^'\\]*(?:\\.[^'\\]*)*'", 'string')
        self._add(r'//[^\n]*', 'comment', italic=True)
        self._add(r'\b\d+(\.\d+)?\b', 'number')
        self._add_ml(r'/\*', r'\*/', 'comment', italic=True)

    def _typescript(self):
        self._javascript()
        types = (r'\b(string|number|boolean|any|void|never|object|symbol|bigint'
                 r'|unknown|interface|type|enum|namespace|abstract|implements'
                 r'|declare|readonly|keyof|infer|satisfies)\b')
        self._add(types, 'type')

    def _html(self):
        self._add(r'</?[\w]+', 'tag', bold=True)
        self._add(r'/?>', 'tag')
        self._add(r'\b[\w-]+(?=\s*=)', 'attr')
        self._add(r'"[^"]*"', 'string')
        self._add(r"'[^']*'", 'string')
        self._add(r'<![\w]+', 'keyword')
        self._add_ml(r'<!--', r'-->', 'comment', italic=True)

    def _css(self):
        self._add(r'@[\w-]+', 'keyword', bold=True)
        self._add(r'[.#::][\w-]+', 'func')
        self._add(r'[\w-]+(?=\s*:)', 'attr')
        self._add(r'#[0-9a-fA-F]{3,8}\b', 'number')
        self._add(r'\b\d+(\.\d+)?(px|em|rem|vh|vw|%|pt|s|ms|ch|ex|vmin|vmax)?\b', 'number')
        self._add(r'"[^"]*"', 'string')
        self._add(r"'[^']*'", 'string')
        self._add_ml(r'/\*', r'\*/', 'comment', italic=True)

    def _json(self):
        self._add(r'"[^"]*"(?=\s*:)', 'key', bold=True)
        self._add(r':\s*"[^"]*"', 'string')
        self._add(r'\b-?\d+(\.\d+)?([eE][+\-]?\d+)?\b', 'number')
        self._add(r'\b(true|false|null)\b', 'keyword2')

    def _bash(self):
        kw = (r'\b(if|then|else|elif|fi|for|while|do|done|case|esac|in|function'
              r'|return|local|export|readonly|declare|source|echo|exit|shift|set'
              r'|unset|trap|break|continue|select|until)\b')
        self._add(kw, 'keyword', bold=True)
        self._add(r'#[^\n]*', 'comment', italic=True)
        self._add(r'\$\{?[\w@#?*!\-]+\}?', 'var')
        self._add(r'"[^"]*"', 'string')
        self._add(r"'[^']*'", 'string')
        self._add(r'\b\d+\b', 'number')

    def _c_common(self):
        kw = (r'\b(auto|break|case|char|const|continue|default|do|double|else|enum'
              r'|extern|float|for|goto|if|inline|int|long|register|restrict|return'
              r'|short|signed|sizeof|static|struct|switch|typedef|union|unsigned'
              r'|void|volatile|while)\b')
        self._add(kw, 'keyword', bold=True)
        self._add(r'#\s*\w+', 'decorator')
        self._add(r'"[^"\\]*(?:\\.[^"\\]*)*"', 'string')
        self._add(r"'[^'\\]*(?:\\.[^'\\]*)*'", 'string')
        self._add(r'//[^\n]*', 'comment', italic=True)
        self._add(r'\b0x[0-9a-fA-F]+\b', 'number')
        self._add(r'\b\d+(\.\d+)?[uUlLfF]*\b', 'number')
        self._add_ml(r'/\*', r'\*/', 'comment', italic=True)

    def _c(self):
        self._c_common()

    def _cpp(self):
        cpp_kw = (r'\b(alignas|alignof|and|and_eq|bitand|bitor|bool|catch|class'
                  r'|compl|concept|consteval|constexpr|constinit|co_await|co_return'
                  r'|co_yield|decltype|delete|explicit|export|false|final|friend'
                  r'|mutable|namespace|new|noexcept|not|not_eq|nullptr|operator|or'
                  r'|or_eq|override|private|protected|public|requires|static_assert'
                  r'|static_cast|dynamic_cast|reinterpret_cast|const_cast|template'
                  r'|this|thread_local|throw|true|try|typename|using|virtual|xor|xor_eq)\b')
        self._add(cpp_kw, 'keyword2', bold=True)
        self._c_common()

    def _rust(self):
        kw = (r'\b(as|async|await|break|const|continue|crate|dyn|else|enum|extern'
              r'|false|fn|for|if|impl|in|let|loop|match|mod|move|mut|pub|ref|return'
              r'|self|Self|static|struct|super|trait|true|type|unsafe|use|where|while)\b')
        types = (r'\b(i8|i16|i32|i64|i128|isize|u8|u16|u32|u64|u128|usize|f32|f64'
                 r'|bool|char|str|String|Vec|Option|Result|Box|Rc|Arc|HashMap|HashSet)\b')
        self._add(kw, 'keyword', bold=True)
        self._add(types, 'type')
        self._add(r'#!\[[\w::()\s,]+\]', 'decorator')
        self._add(r'#\[[\w::()\s,]+\]', 'decorator')
        self._add(r'"[^"\\]*(?:\\.[^"\\]*)*"', 'string')
        self._add(r"'[^'\\]*(?:\\.[^'\\]*)*'", 'string')
        self._add(r'//[^\n]*', 'comment', italic=True)
        self._add(r'\b\d+(\.\d+)?(_\d+)?(u\d+|i\d+|f\d+)?\b', 'number')
        self._add_ml(r'/\*', r'\*/', 'comment', italic=True)

    def _go(self):
        kw = (r'\b(break|case|chan|const|continue|default|defer|else|fallthrough'
              r'|for|func|go|goto|if|import|interface|map|package|range|return'
              r'|select|struct|switch|type|var)\b')
        builtins = (r'\b(bool|byte|complex64|complex128|error|float32|float64|int'
                    r'|int8|int16|int32|int64|rune|string|uint|uint8|uint16|uint32'
                    r'|uint64|uintptr|true|false|nil|iota|make|new|len|cap|append'
                    r'|copy|delete|close|panic|recover|print|println)\b')
        self._add(kw, 'keyword', bold=True)
        self._add(builtins, 'type')
        self._add(r'`[^`]*`', 'string')
        self._add(r'"[^"\\]*(?:\\.[^"\\]*)*"', 'string')
        self._add(r"'[^'\\]*(?:\\.[^'\\]*)*'", 'string')
        self._add(r'//[^\n]*', 'comment', italic=True)
        self._add(r'\b\d+(\.\d+)?\b', 'number')
        self._add_ml(r'/\*', r'\*/', 'comment', italic=True)

    def _java(self):
        kw = (r'\b(abstract|assert|break|case|catch|class|const|continue|default'
              r'|do|else|enum|extends|final|finally|for|goto|if|implements|import'
              r'|instanceof|interface|native|new|package|private|protected|public'
              r'|return|static|strictfp|super|switch|synchronized|this|throw|throws'
              r'|transient|try|var|volatile|while)\b')
        literals = r'\b(true|false|null)\b'
        types = (r'\b(boolean|byte|char|double|float|int|long|short|void|String'
                 r'|Integer|Long|Double|Boolean|Object|Class|System|List|Map|Set'
                 r'|ArrayList|HashMap|Optional)\b')
        self._add(kw, 'keyword', bold=True)
        self._add(literals, 'keyword2')
        self._add(types, 'type')
        self._add(r'@\w+', 'decorator')
        self._add(r'"[^"\\]*(?:\\.[^"\\]*)*"', 'string')
        self._add(r"'[^'\\]*(?:\\.[^'\\]*)*'", 'string')
        self._add(r'//[^\n]*', 'comment', italic=True)
        self._add(r'\b\d+(\.\d+)?[lLfFdD]?\b', 'number')
        self._add_ml(r'/\*', r'\*/', 'comment', italic=True)

    def _yaml(self):
        self._add(r'^[\s-]*[\w-]+(?=\s*:)', 'key', bold=True)
        self._add(r':\s*[^#\n]+', 'string')
        self._add(r'#[^\n]*', 'comment', italic=True)
        self._add(r'\b\d+(\.\d+)?\b', 'number')
        self._add(r'\b(true|false|null|yes|no|on|off)\b', 'keyword2')

    def _toml(self):
        self._add(r'^\[[\w.]+\]', 'type', bold=True)
        self._add(r'^[\w.-]+(?=\s*=)', 'key', bold=True)
        self._add(r'=\s*"[^"]*"', 'string')
        self._add(r"=\s*'[^']*'", 'string')
        self._add(r'#[^\n]*', 'comment', italic=True)
        self._add(r'\b\d+(\.\d+)?\b', 'number')
        self._add(r'\b(true|false)\b', 'keyword2')

    def _sql(self):
        kw = (r'\b(SELECT|FROM|WHERE|JOIN|LEFT|RIGHT|INNER|OUTER|CROSS|FULL|ON|AND|OR'
              r'|NOT|IN|IS|NULL|AS|DISTINCT|GROUP|BY|ORDER|HAVING|LIMIT|OFFSET|INSERT'
              r'|INTO|VALUES|UPDATE|SET|DELETE|CREATE|TABLE|INDEX|VIEW|DROP|ALTER|ADD'
              r'|COLUMN|PRIMARY|KEY|FOREIGN|REFERENCES|UNIQUE|DEFAULT|CHECK|CONSTRAINT'
              r'|BEGIN|COMMIT|ROLLBACK|TRANSACTION|GRANT|REVOKE|UNION|ALL|EXISTS|CASE'
              r'|WHEN|THEN|ELSE|END|LIKE|BETWEEN|WITH|RECURSIVE|RETURNING)\b')
        types = (r'\b(INT|INTEGER|BIGINT|SMALLINT|TINYINT|FLOAT|DOUBLE|DECIMAL|NUMERIC'
                 r'|CHAR|VARCHAR|TEXT|BLOB|DATE|TIME|DATETIME|TIMESTAMP|BOOLEAN|BOOL'
                 r'|SERIAL|AUTOINCREMENT|UUID|JSON|JSONB)\b')
        self._add(kw, 'keyword', bold=True, flags=re.IGNORECASE)
        self._add(types, 'type', flags=re.IGNORECASE)
        self._add(r"'[^']*'", 'string')
        self._add(r'\b\d+(\.\d+)?\b', 'number')
        self._add(r'--[^\n]*', 'comment', italic=True)
        self._add_ml(r'/\*', r'\*/', 'comment', italic=True)

    # ------------------------------------------------------------------
    # Applying them
    # ------------------------------------------------------------------

    def apply(self, text: str, previous_state: int, set_format: SetFormat) -> int:
        """Color one line through ``set_format(start, length, format)``.
        ``previous_state`` is what this returned for the line before (0 for
        none); the result is this line's, which says whether a multi-line
        construct goes on into the next line."""
        # --- Continue a multiline construct from the previous block ---
        offset = 0
        if previous_state > 0:
            idx = previous_state - 1
            if idx < len(self._ml_rules):
                _, end_pat, fmt = self._ml_rules[idx]
                m = end_pat.search(text)
                if m:
                    set_format(0, m.end(), fmt)
                    offset = m.end()
                else:
                    set_format(0, len(text), fmt)
                    return previous_state

        # --- Single-line rules on the remaining text ---
        for pat, fmt in self._rules:
            for m in pat.finditer(text, offset):
                set_format(m.start(), m.end() - m.start(), fmt)

        # --- Check whether a multiline construct starts in remaining text ---
        state = 0
        for i, (start_pat, end_pat, fmt) in enumerate(self._ml_rules):
            m_s = start_pat.search(text, offset)
            if not m_s:
                continue
            m_e = end_pat.search(text, m_s.end())
            if m_e:
                # Starts and ends on this line
                set_format(m_s.start(), m_e.end() - m_s.start(), fmt)
            else:
                # Spans into next block(s)
                set_format(m_s.start(), len(text) - m_s.start(), fmt)
                state = i + 1
            break  # handle only the first multiline found per line
        return state


@lru_cache(maxsize=64)
def code_rules(language: str | None, is_dark: bool) -> CodeRules:
    """The rules of ``language`` in a theme, made once."""
    return CodeRules(language, is_dark)


class SyntaxHighlighter(QSyntaxHighlighter):
    """Colors a whole code file in one language."""

    def __init__(self, document, language: str, is_dark: bool = True):
        super().__init__(document)
        self._language = language
        self._is_dark = is_dark
        self._code = code_rules(language, is_dark)

    def set_theme(self, is_dark: bool):
        if self._is_dark != is_dark:
            self._is_dark = is_dark
            self._code = code_rules(self._language, is_dark)
            self.rehighlight()

    def highlightBlock(self, text: str):
        state = self._code.apply(text, max(0, self.previousBlockState()), self.setFormat)
        self.setCurrentBlockState(state)


# A footnote mark ("[^1]", "[^note]"), and a paragraph that is one's
# definition ("[^1]: The source."): rich_text's, the one way they are found.
FOOTNOTE_MARK = rich_text.FOOTNOTE_MARK
FOOTNOTE_DEFINITION = rich_text.FOOTNOTE_DEFINITION


class RichTextLook(QSyntaxHighlighter):
    """How a document with Markdown structure looks on screen.

    Some of what a Markdown document holds has no look of its own in the
    editor: a code block's lines are set in the font of the text around
    them, inline code too, a link keeps whatever color the file it came
    from gave it, and a footnote mark is plain text. This adds the look
    (code blocks in the code font, colored by their language; a tinted
    chip behind inline code; links in the theme's link color; footnote
    marks raised in the link color, their definitions quieter) without
    putting anything into the document, so nothing of it is saved,
    published, or counted as formatting. (The editor paints the box
    behind a code block.)

    One highlighter per document: a code file gets SyntaxHighlighter, a
    document with Markdown structure this one.
    """

    _CODE_BACKGROUND = {True: "#33363B", False: "#EEF0F2"}
    _LINK = {True: "#4AA3FF", False: "#0A66C2"}
    _MUTED = {True: "#9A9A9A", False: "#6B6B6B"}

    def __init__(self, document, is_dark: bool = True):
        super().__init__(document)
        self._is_dark = is_dark
        self._build_formats()

    def set_theme(self, is_dark: bool):
        if self._is_dark != is_dark:
            self._is_dark = is_dark
            self._build_formats()
            self.rehighlight()

    def _build_formats(self):
        self._code = QTextCharFormat()
        self._code.setBackground(QColor(self._CODE_BACKGROUND[self._is_dark]))
        # A font that is monospaced on this system: "monospace", which
        # Markdown gives inline code, is not a font on macOS or Windows.
        self._code.setFontFamilies([monospace_family(), "monospace"])
        # A link reads as one in either theme, whatever color the file
        # it came from gave it.
        self._link = QTextCharFormat()
        self._link.setForeground(QColor(self._LINK[self._is_dark]))
        self._link.setFontUnderline(True)
        # A code block's lines, in the font code is set in; each language's
        # colors over it.
        self._code_line = QTextCharFormat()
        self._code_line.setFont(code_font())
        self._over_code: dict[int, tuple] = {}
        # Footnote marks raised in the link color; definitions quieter.
        self._footnote_mark = QTextCharFormat()
        self._footnote_mark.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignSuperScript)
        self._footnote_mark.setForeground(QColor(self._LINK[self._is_dark]))
        self._footnote_text = QTextCharFormat()
        self._footnote_text.setForeground(QColor(self._MUTED[self._is_dark]))
        self._footnote_text.setProperty(QTextFormat.Property.FontSizeAdjustment, -1)

    def _on_code(self, fmt: QTextCharFormat) -> QTextCharFormat:
        """``fmt`` (a language's color) on a code line, made once. The
        cache holds ``fmt`` itself too, so its id cannot be reused."""
        entry = self._over_code.get(id(fmt))
        if entry is None or entry[0] is not fmt:
            made = QTextCharFormat(self._code_line)
            made.merge(fmt)
            entry = self._over_code[id(fmt)] = (fmt, made)
        return entry[1]

    def _highlight_code(self, block, text: str, language: str) -> None:
        self.setFormat(0, len(text), self._code_line)
        canonical = canonical_language(language)
        if canonical is None or canonical == "markdown":
            self.setCurrentBlockState(0)
            return
        previous = block.previous()
        # A construct goes on only from the line before in the same block.
        state = (max(0, self.previousBlockState())
                 if previous.isValid() and rich_text.same_code_block(previous, block) else 0)
        rules = code_rules(canonical, self._is_dark)
        self.setCurrentBlockState(rules.apply(
            text, state, lambda start, length, fmt: self.setFormat(
                start, length, self._on_code(fmt))))

    def _highlight_footnotes(self, text: str) -> None:
        """A definition ("[^1]: The source.") quieter, its label in the link
        color; every other mark raised in the link color."""
        start = 0
        definition = FOOTNOTE_DEFINITION.match(text)
        if definition is not None:
            self.setFormat(0, len(text), self._footnote_text)
            label = QTextCharFormat(self._footnote_text)
            label.setForeground(QColor(self._LINK[self._is_dark]))
            begin = text.index("[")
            self.setFormat(begin, definition.end() - 1 - begin, label)
            start = definition.end()
        for mark in FOOTNOTE_MARK.finditer(text, start):
            self.setFormat(mark.start(), mark.end() - mark.start(), self._footnote_mark)

    def highlightBlock(self, text: str):
        block = self.currentBlock()
        language = rich_text.code_language(block)
        if language is not None:
            self._highlight_code(block, text, language)
            return
        self.setCurrentBlockState(0)
        if "[^" in text:
            self._highlight_footnotes(text)
        start = block.position()
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.isValid():
                fmt = fragment.charFormat()
                if fmt.isImageFormat():
                    pass
                elif fmt.isAnchor():
                    look = QTextCharFormat(self._link)
                    if fmt.fontFixedPitch():
                        look.merge(self._code)
                    self.setFormat(fragment.position() - start, fragment.length(), look)
                elif fmt.fontFixedPitch():
                    self.setFormat(fragment.position() - start, fragment.length(), self._code)
            it += 1
