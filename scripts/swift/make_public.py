#!/usr/bin/env python3
"""Adds `public` to every declaration at module or type scope in the given Swift files.

Used once for the Phase 1 extraction (docs/swift-migration): files that lived in the app had internal access,
and the app now sees them through a module boundary. Declarations that already carry an access modifier
(private, fileprivate, internal, public, open) are left alone. Members of protocols take no modifier.
Local declarations inside function bodies and closures are skipped by tracking brace scopes outside string
literals and comments. Review the diff: this is a text tool, not a compiler.
"""
import re, sys

DECL = re.compile(
    r'^(?P<indent>\s*)(?P<attrs>(?:@\w+(?:\([^)]*\))?\s+)*)'
    r'(?P<mods>(?:(?:final|static|class|nonisolated(?:\(unsafe\))?|override|required|convenience|lazy|weak|unowned(?:\(safe\)|\(unsafe\))?|mutating|nonmutating|indirect|dynamic|optional|consuming|borrowing|private\(set\)|internal\(set\)|fileprivate\(set\))\s+)*)'
    r'(?P<kw>struct|class|enum|actor|protocol|extension|typealias|func|var|let|init|subscript)\b')
ACCESS = re.compile(r'^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:\w+(?:\([^)]*\))?\s+)*(private|fileprivate|internal|public|open|package)\b(?!\(set\))')
TYPE_KW = {'struct', 'class', 'enum', 'actor', 'extension'}

def strip_code(line, state):
    """Returns the line with string literals and comments blanked, updating multi-line state."""
    out = []
    i = 0
    n = len(line)
    while i < n:
        if state['block_comment']:
            j = line.find('*/', i)
            if j < 0: return ''.join(out), state
            i = j + 2; state['block_comment'] = False; continue
        if state['multiline']:
            close = '"""' + '#' * state['hashes']
            j = line.find(close, i)
            if j < 0: return ''.join(out), state
            i = j + len(close); state['multiline'] = False; continue
        c = line[i]
        if line.startswith('//', i): break
        if line.startswith('/*', i): state['block_comment'] = True; i += 2; continue
        if c == '#' or c == '"':
            m = re.match(r'(#*)"""', line[i:])
            if m:
                state['multiline'] = True; state['hashes'] = len(m.group(1)); i += m.end(); continue
            m = re.match(r'(#+)"', line[i:]) if c == '#' else None
            if m or c == '"':
                hashes = len(m.group(1)) if m else 0
                i += (m.end() if m else 1)
                close = '"' + '#' * hashes
                while i < n:
                    if hashes == 0 and line[i] == '\\': i += 2; continue
                    if line.startswith(close, i): i += len(close); break
                    i += 1
                out.append(' ')
                continue
            out.append(c); i += 1; continue
        out.append(c); i += 1
    return ''.join(out), state

def process(path):
    src = open(path).read().split('\n')
    state = {'block_comment': False, 'multiline': False, 'hashes': 0}
    stack = []            # kinds of open scopes: 'type' | 'protocol' | 'code'
    pending = 'code'      # what the next '{' opens
    out = []
    changed = 0
    for line in src:
        code, state = strip_code(line, state)
        in_literal = state['multiline']
        scope = stack[-1] if stack else 'top'
        new_line = line
        m = DECL.match(code) if (scope in ('top', 'type')) else None
        if m and not ACCESS.match(code) and not (m.group('kw') == 'extension'):
            # `case` lines never match; `deinit` never matches; `init` does
            insert_at = len(m.group('indent')) + len(m.group('attrs'))
            new_line = line[:insert_at] + 'public ' + line[insert_at:]
            changed += 1
        dm = DECL.match(code) if scope in ('top', 'type', 'protocol') else None
        if dm:
            kw = dm.group('kw')
            if kw in TYPE_KW: pending = 'type'
            elif kw == 'protocol': pending = 'protocol'
            elif kw in ('func', 'var', 'let', 'init', 'subscript'): pending = 'code'
        # scopes
        for ch in code:
            if ch == '{':
                stack.append(pending); pending = 'code'
            elif ch == '}':
                if stack: stack.pop()
        out.append(new_line)
    open(path, 'w').write('\n'.join(out))
    return changed, len(stack), state

if __name__ == '__main__':
    for p in sys.argv[1:]:
        changed, depth, state = process(p)
        flag = '' if depth == 0 and not state['multiline'] and not state['block_comment'] else '   <-- UNBALANCED, check by hand'
        print(f'{p}: {changed} declarations made public{flag}')
