# Copyright (c) 2026 Cyber O͘-hîm ki-tē
# Licensed under the MIT License: https://opensource.org/licenses/MIT
#
# 2026-03-19: Fixed shared conversion bugs (hnn, Ň, NG2, UAN nasal match) to maintain strict 1-to-1 parity.

# --- Converter Logic (Python port of converter.js) ---
#
# NOT ported (browser/extension-only):
#   convertPageContent, convertSelection, fetchArticleContent,
#   undo, undoAll, updateState, pushHistory
#
# Fully ported:
#   tl_tiau_2_tl_soo, tl_soo_2_poj_soo, poj_soo_2_poj_tiau,
#   tl_tiau_2_poj_tiau (= convert_text), run_tests
#
# Unicode codepoints used:
#   U+0358  COMBINING DOT ABOVE RIGHT        (o͘)
#   U+030D  COMBINING VERTICAL LINE ABOVE    (a̍  tone 8)
#   U+030B  COMBINING DOUBLE ACUTE ACCENT    (a̋  tone 9)
#   U+0300  COMBINING GRAVE ACCENT
#   U+0302  COMBINING CIRCUMFLEX ACCENT
#   U+030C  COMBINING CARON
#   U+0304  COMBINING MACRON
#   U+0306  COMBINING BREVE
#   U+207F  SUPERSCRIPT LATIN SMALL LETTER N (ⁿ)
#
# re.sub() rule:
#   \uXXXX IS interpreted in regex PATTERN strings (raw or not).
#   \uXXXX is NOT interpreted in REPLACEMENT strings.
#   All replacement strings therefore use actual Unicode characters;
#   back-references are written as \\g<N> inside normal strings.

import re

# ---------------------------------------------------------------------------
# Global flags  (mirrors window.TL_* flags)
# ---------------------------------------------------------------------------
TL_TONE6_USE_CARON       = False  # False=acute (same as tone2); True=caron
TL_ALL_CAPS_SUPPORT      = True   # True=enable OO->O͘, TS->CH, etc.
TL_USE_NASAL_SUPERSCRIPT = True   # True=convert nn/N tail to ⁿ

# ---------------------------------------------------------------------------
# Regex helper factories  (exact ports of createRC / createRC1 / etc.)
# ---------------------------------------------------------------------------
_TL_BUE = '((?:rm|rng|rn|r|m|ng|n|nnh|nn|\u207f|N)?)'   # ⁿ = U+207F


def _rc(char):
    """createRC: char + [aAeEgGhHiIkKmMnNoOpPtTuU]*"""
    return re.compile(char + r'([aAeEgGhHiIkKmMnNoOpPtTuU]*)', re.UNICODE)


def _rc1(tailo):
    """createRC1: prefix + TL_BUE + lastChar"""
    prefix, last = tailo[:-1], tailo[-1]
    return re.compile(prefix + _TL_BUE + last, re.UNICODE)


def _rc2(tailo):
    """createRC2: tailo[0] + (u|i) + (N|ⁿ|nn)? + tailo[1]"""
    return re.compile(
        tailo[0] + r'(u|i)((?:N|\u207f|nn)?)' + tailo[1], re.UNICODE
    )


def _rc3(pattern):
    """createRC3 / createRC3_Regex: literal pattern (raw string OK for patterns)"""
    return re.compile(pattern, re.UNICODE)


def _rc4(tailo):
    """createRC4: prefix + stop-consonant + lastChar?"""
    prefix, last = tailo[:-1], tailo[-1]
    return re.compile(
        prefix + r'(h(?:N|\u207f|nn)?|H(?:N|\u207f|nn)?|p|P|t|T|k|K)' + last + r'?',
        re.UNICODE,
    )


def _rc8(tailo):
    """createRC8: prefix + stop-consonant + lastChar (required)"""
    prefix, last = tailo[:-1], tailo[-1]
    return re.compile(
        prefix + r'(h(?:N|\u207f|nn)?|H(?:N|\u207f|nn)?|p|P|t|T|k|K)' + last,
        re.UNICODE,
    )


# ---------------------------------------------------------------------------
# 1. TL Tone Marks -> TL Numbers
# ---------------------------------------------------------------------------
def tl_tiau_2_tl_soo(tl_tiau, nasal='nn', use14=False):
    s = tl_tiau
    if not s:
        return ''

    # Replacement strings must contain actual Unicode chars (not r'\uXXXX').
    # Back-reference written as \\g<1> inside a normal (non-raw) string.
    TL_T2S = [
        # ── a ────────────────────────────────────────────────────
        (_rc('\u00e1'), 'a\\g<1>2'),   (_rc('\u00c1'), 'A\\g<1>2'),  # á Á
        (_rc('\u00e0'), 'a\\g<1>3'),   (_rc('\u00c0'), 'A\\g<1>3'),  # à À
        (_rc('\u00e2'), 'a\\g<1>5'),   (_rc('\u00c2'), 'A\\g<1>5'),  # â Â
        (_rc('\u01ce'), 'a\\g<1>6'),   (_rc('\u01cd'), 'A\\g<1>6'),  # ǎ Ǎ
        (_rc('\u0101'), 'a\\g<1>7'),   (_rc('\u0100'), 'A\\g<1>7'),  # ā Ā
        (_rc('a\u030d'), 'a\\g<1>8'), (_rc('A\u030d'), 'A\\g<1>8'), # a̍ A̍
        (_rc('a\u030b'), 'a\\g<1>9'), (_rc('A\u030b'), 'A\\g<1>9'), # a̋ A̋
        # ── e ────────────────────────────────────────────────────
        (_rc('\u00e9'), 'e\\g<1>2'),   (_rc('\u00c9'), 'E\\g<1>2'),  # é É
        (_rc('\u00e8'), 'e\\g<1>3'),   (_rc('\u00c8'), 'E\\g<1>3'),  # è È
        (_rc('\u00ea'), 'e\\g<1>5'),   (_rc('\u00ca'), 'E\\g<1>5'),  # ê Ê
        (_rc('\u011b'), 'e\\g<1>6'),   (_rc('\u011a'), 'E\\g<1>6'),  # ě Ě
        (_rc('\u0113'), 'e\\g<1>7'),   (_rc('\u0112'), 'E\\g<1>7'),  # ē Ē
        (_rc('e\u030d'), 'e\\g<1>8'), (_rc('E\u030d'), 'E\\g<1>8'), # e̍ E̍
        (_rc('e\u030b'), 'e\\g<1>9'), (_rc('E\u030b'), 'E\\g<1>9'), # e̋ E̋
        # ── i ────────────────────────────────────────────────────
        (_rc('\u00ed'), 'i\\g<1>2'),   (_rc('\u00cd'), 'I\\g<1>2'),  # í Í
        (_rc('\u00ec'), 'i\\g<1>3'),   (_rc('\u00cc'), 'I\\g<1>3'),  # ì Ì
        (_rc('\u00ee'), 'i\\g<1>5'),   (_rc('\u00ce'), 'I\\g<1>5'),  # î Î
        (_rc('\u01d0'), 'i\\g<1>6'),   (_rc('\u01cf'), 'I\\g<1>6'),  # ǐ Ǐ
        (_rc('\u012b'), 'i\\g<1>7'),   (_rc('\u012a'), 'I\\g<1>7'),  # ī Ī
        (_rc('i\u030d'), 'i\\g<1>8'), (_rc('I\u030d'), 'I\\g<1>8'), # i̍ I̍
        (_rc('i\u030b'), 'i\\g<1>9'), (_rc('I\u030b'), 'I\\g<1>9'), # i̋ I̋
        # ── oo (o + U+0358) — BEFORE plain o ─────────────────────
        (_rc('\u00f3\u0358'), 'oo\\g<1>2'), (_rc('\u00d3\u0358'), 'Oo\\g<1>2'), # ó͘ Ó͘
        (_rc('\u00f2\u0358'), 'oo\\g<1>3'), (_rc('\u00d2\u0358'), 'Oo\\g<1>3'), # ò͘ Ò͘
        (_rc('\u00f4\u0358'), 'oo\\g<1>5'), (_rc('\u00d4\u0358'), 'Oo\\g<1>5'), # ô͘ Ô͘
        (_rc('\u01d2\u0358'), 'oo\\g<1>6'), (_rc('\u01d1\u0358'), 'Oo\\g<1>6'), # ǒ͘ Ǒ͘
        (_rc('\u014d\u0358'), 'oo\\g<1>7'), (_rc('\u014c\u0358'), 'Oo\\g<1>7'), # ō͘ Ō͘
        (_rc('o\u030d\u0358'), 'oo\\g<1>8'), (_rc('O\u030d\u0358'), 'Oo\\g<1>8'), # o̍͘ O̍͘
        (_rc('\u0151\u0358'), 'oo\\g<1>9'), (_rc('\u0150\u0358'), 'Oo\\g<1>9'), # ő͘ Ő͘
        # ── plain o ──────────────────────────────────────────────
        (_rc('\u00f3'), 'o\\g<1>2'),   (_rc('\u00d3'), 'O\\g<1>2'),  # ó Ó
        (_rc('\u00f2'), 'o\\g<1>3'),   (_rc('\u00d2'), 'O\\g<1>3'),  # ò Ò
        (_rc('\u00f4'), 'o\\g<1>5'),   (_rc('\u00d4'), 'O\\g<1>5'),  # ô Ô
        (_rc('\u01d2'), 'o\\g<1>6'),   (_rc('\u01d1'), 'O\\g<1>6'),  # ǒ Ǒ
        (_rc('\u014d'), 'o\\g<1>7'),   (_rc('\u014c'), 'O\\g<1>7'),  # ō Ō
        (_rc('o\u030d'), 'o\\g<1>8'), (_rc('O\u030d'), 'O\\g<1>8'), # o̍ O̍
        (_rc('\u0151'), 'o\\g<1>9'),   (_rc('\u0150'), 'O\\g<1>9'),  # ő Ő
        # ── u ────────────────────────────────────────────────────
        (_rc('\u00fa'), 'u\\g<1>2'),   (_rc('\u00da'), 'U\\g<1>2'),  # ú Ú
        (_rc('\u00f9'), 'u\\g<1>3'),   (_rc('\u00d9'), 'U\\g<1>3'),  # ù Ù
        (_rc('\u00fb'), 'u\\g<1>5'),   (_rc('\u00db'), 'U\\g<1>5'),  # û Û
        (_rc('\u01d4'), 'u\\g<1>6'),   (_rc('\u01d3'), 'U\\g<1>6'),  # ǔ Ǔ
        (_rc('\u016b'), 'u\\g<1>7'),   (_rc('\u016a'), 'U\\g<1>7'),  # ū Ū
        (_rc('u\u030d'), 'u\\g<1>8'), (_rc('U\u030d'), 'U\\g<1>8'), # u̍ U̍
        (_rc('\u0171'), 'u\\g<1>9'),   (_rc('\u0170'), 'U\\g<1>9'),  # ű Ű
        # ── m ────────────────────────────────────────────────────
        (_rc('\u1e3f'), 'm\\g<1>2'),   (_rc('\u1e3e'), 'M\\g<1>2'),  # ḿ Ḿ
        (_rc('m\u0300'), 'm\\g<1>3'), (_rc('M\u0300'), 'M\\g<1>3'), # m̀ M̀
        (_rc('m\u0302'), 'm\\g<1>5'), (_rc('M\u0302'), 'M\\g<1>5'), # m̂ M̂
        (_rc('m\u030c'), 'm\\g<1>6'), (_rc('M\u030c'), 'M\\g<1>6'), # m̌ M̌
        (_rc('m\u0304'), 'm\\g<1>7'), (_rc('M\u0304'), 'M\\g<1>7'), # m̄ M̄
        (_rc('m\u030d'), 'm\\g<1>8'), (_rc('M\u030d'), 'M\\g<1>8'), # m̍ M̍
        (_rc('m\u030b'), 'm\\g<1>9'), (_rc('M\u030b'), 'M\\g<1>9'), # m̋ M̋
        # ── n ────────────────────────────────────────────────────
        (_rc('\u0144'), 'n\\g<1>2'),   (_rc('\u0143'), 'N\\g<1>2'),  # ń Ń
        (_rc('\u01f9'), 'n\\g<1>3'),   (_rc('\u01f8'), 'N\\g<1>3'),  # ǹ Ǹ
        (_rc('n\u0302'), 'n\\g<1>5'), (_rc('N\u0302'), 'N\\g<1>5'), # n̂ N̂
        (_rc('\u0148'), 'n\\g<1>6'),   (_rc('\u0147'), 'N\\g<1>6'), # ň Ň
        (_rc('n\u0304'), 'n\\g<1>7'), (_rc('N\u0304'), 'N\\g<1>7'), # n̄ N̄
        (_rc('n\u030d'), 'n\\g<1>8'), (_rc('N\u030d'), 'N\\g<1>8'), # n̍ N̍
        (_rc('n\u030b'), 'n\\g<1>9'), (_rc('N\u030b'), 'N\\g<1>9'), # n̋ N̋
    ]

    for (pat, repl) in TL_T2S:
        s = pat.sub(repl, s)

    # Bare o͘/O͘ (no tone mark) -> oo/Oo
    s = s.replace('o\u0358', 'oo')
    s = s.replace('O\u0358', 'Oo')
    # ⁿ -> nn
    s = s.replace('\u207f', 'nn')

    if nasal == 'N':
        s = re.sub(r'(?<=[aeiouAEIOU])nn', 'N', s)

    if use14:
        s = re.sub(r'(a|A|e|E|i|I|o|O|u|U|m|M|ng|Ng|n)\b', r'\g<1>1', s)
        s = re.sub(r'(h|H|p|P|t|T|k|K)\b',                 r'\g<1>4', s)

    return s


# ---------------------------------------------------------------------------
# 2. TL Numbers -> POJ Numbers
# ---------------------------------------------------------------------------
def tl_soo_2_poj_soo(tailo_soo, keep14=False):
    if not tailo_soo:
        return ''
    s = tailo_soo

    if not keep14:
        s = re.sub(
            r'([aeiouAEIOU](?:nn|N)?'
            r'|[aeiouAEIOU](?:ng|n|m)'
            r'|(?:p|P|ph|Ph|m|M|b|B|t|T|th|Th|n|N|l|L|k|K|kh|Kh|ng|Ng|g|G|s|S|h|H)?'
            r'(?:ng|Ng|m|M))1',
            r'\1', s,
        )
        s = re.sub(
            r'([aeiouAEIOU](?:(?:nn|N)?(?:h|H)|p|P|t|T|k|K)'
            r'|(?:m|M|ng|Ng)(?:h|H))4',
            r'\1', s,
        )

    # Order matters: longer patterns before shorter ones (e.g. oonn before oo)
    tailo2poj = [
        ('oonn', 'o\u0358nn'), ('Oonn', 'O\u0358nn'),
        # ['onn','o͘N'],['Onn','O͘N'] REMOVED — onn = oⁿ, not o͘N
        ('ts', 'ch'), ('Ts', 'Ch'),
        ('oo', 'o\u0358'), ('Oo', 'O\u0358'),
        ('ua', 'oa'), ('Ua', 'Oa'),
        ('ue', 'oe'), ('Ue', 'Oe'),
        ('ing', 'eng'), ('Ing', 'Eng'),
        ('ik', 'ek'),  ('Ik', 'Ek'),
        ('nnh', 'hnn'),   # nnh -> hnn (-> hⁿ in stage 3)
    ]

    if TL_ALL_CAPS_SUPPORT:
        tailo2poj += [
            ('OONN', 'O\u0358NN'), ('ONN', 'O\u0358NN'),
            ('TS', 'CH'),
            ('OO', 'O\u0358'),
            ('UA', 'OA'), ('UE', 'OE'),
            ('ING', 'ENG'), ('IK', 'EK'),
            ('NNH', 'HNN'),
        ]

    for (k, v) in tailo2poj:
        s = re.sub(k, v, s)

    return s


# ---------------------------------------------------------------------------
# 3. POJ Numbers -> POJ Tone Marks
# ---------------------------------------------------------------------------
def poj_soo_2_poj_tiau(poj_soo):
    if not poj_soo:
        return ''
    s = poj_soo

    # ── Nasal superscript ─────────────────────────────────────────────────
    # nn after vowel (± U+0358, ± h): ALWAYS -> ⁿ
    # Replacement: actual ⁿ char in string (\\g<1> not needed — groups 1,2 captured)
    _nn_repl = '\\g<1>' + '\u207f' + '\\g<2>'
    s = re.sub(
        r'([aeiouAEIOUmM]\u0358?h?|[aeiouAEIOUmM]\u0358?|[hH])nn(\d?)\b',
        _nn_repl, s,
    )

    if TL_USE_NASAL_SUPERSCRIPT:
        _N_repl = '\\g<1>' + '\u207f' + '\\g<2>'
        s = re.sub(
            r'([aeioumM]\u0358?h?|[aeioumM]\u0358?|[hH])N(\d?)\b',
            _N_repl, s,
        )
        # Nh -> hⁿ  (aNh -> ahⁿ)
        _Nh_repl = '\\g<1>h' + '\u207f' + '\\g<2>'
        s = re.sub(
            r'([aeioumM]\u0358?)Nh?(\d?)\b',
            _Nh_repl, s,
        )

    # ── Tone 6 ───────────────────────────────────────────────────────────
    if TL_TONE6_USE_CARON:
        t6 = {
            'a': '\u01ce', 'A': '\u01cd',        # ǎ Ǎ
            'e': '\u011b', 'E': '\u011a',        # ě Ě
            'i': '\u01d0', 'I': '\u01cf',        # ǐ Ǐ
            'o': '\u01d2', 'O': '\u01d1',        # ǒ Ǒ
            'u': '\u01d4', 'U': '\u01d3',        # ǔ Ǔ
            'o\u0358': '\u01d2\u0358',            # ǒ͘
            'O\u0358': '\u01d1\u0358',            # Ǒ͘
            'm': 'm\u030c', 'M': 'M\u030c',      # m̌ M̌
            'n': '\u0148',  'N': '\u0147',        # ň Ň
        }
    else:
        t6 = {
            'a': '\u00e1', 'A': '\u00c1',        # á Á
            'e': '\u00e9', 'E': '\u00c9',        # é É
            'i': '\u00ed', 'I': '\u00cd',        # í Í
            'o': '\u00f3', 'O': '\u00d3',        # ó Ó
            'u': '\u00fa', 'U': '\u00da',        # ú Ú
            'o\u0358': '\u00f3\u0358',            # ó͘
            'O\u0358': '\u00d3\u0358',            # Ó͘
            'm': '\u1e3f', 'M': '\u1e3e',        # ḿ Ḿ
            'n': '\u0144', 'N': '\u0143',        # ń Ń
        }

    # ── POJ_S2T table ─────────────────────────────────────────────────────
    POJ_S2T = [
        # ── a + diphthong (RC2) ──────────────────────────────────────────
        (_rc2('a1'), 'a\\g<1>\\g<2>'),
        (_rc2('A1'), 'A\\g<1>\\g<2>'),
        (_rc2('a2'), '\u00e1\\g<1>\\g<2>'),   # á
        (_rc2('A2'), '\u00c1\\g<1>\\g<2>'),   # Á
        (_rc2('a3'), '\u00e0\\g<1>\\g<2>'),   # à
        (_rc2('A3'), '\u00c0\\g<1>\\g<2>'),   # À
        (_rc3(r'a(i|u)((?:N|\u207f|nn)?)h4'), 'a\\g<1>\\g<2>h'),
        (_rc3(r'A(i|u)((?:N|\u207f|nn)?)h4'), 'A\\g<1>\\g<2>h'),
        (_rc2('a5'), '\u00e2\\g<1>\\g<2>'),   # â
        (_rc2('A5'), '\u00c2\\g<1>\\g<2>'),   # Â
        (_rc2('a6'), t6['a'] + '\\g<1>\\g<2>'),
        (_rc2('A6'), t6['A'] + '\\g<1>\\g<2>'),
        (_rc2('a7'), '\u0101\\g<1>\\g<2>'),   # ā
        (_rc2('A7'), '\u0100\\g<1>\\g<2>'),   # Ā
        (_rc3(r'a(i|u)h(?:N|\u207f|nn)8'), 'a\u030d\\g<1>h\u207f'),   # a̍…hⁿ
        (_rc3(r'A(i|u)h(?:N|\u207f|nn)8'), 'A\u030d\\g<1>h\u207f'),
        (_rc3(r'a(i|u)h8'),               'a\u030d\\g<1>h'),           # a̍…h
        (_rc3(r'A(i|u)h8'),               'A\u030d\\g<1>h'),
        (_rc2('a9'), '\u0103\\g<1>\\g<2>'),   # ă
        (_rc2('A9'), '\u0102\\g<1>\\g<2>'),   # Ă

        # ── u + diphthong (RC2) ──────────────────────────────────────────
        (_rc2('u1'), 'u\\g<1>\\g<2>'),
        (_rc2('U1'), 'U\\g<1>\\g<2>'),
        (_rc2('u2'), '\u00fa\\g<1>\\g<2>'),   # ú
        (_rc2('U2'), '\u00da\\g<1>\\g<2>'),   # Ú
        (_rc2('u3'), '\u00f9\\g<1>\\g<2>'),   # ù
        (_rc2('U3'), '\u00d9\\g<1>\\g<2>'),   # Ù
        (_rc3(r'uih((?:N|\u207f|nn)?)4'), 'uih\\g<1>'),
        (_rc3(r'Uih((?:N|\u207f|nn)?)4'), 'Uih\\g<1>'),
        (_rc2('u5'), '\u00fb\\g<1>\\g<2>'),   # û
        (_rc2('U5'), '\u00db\\g<1>\\g<2>'),   # Û
        (_rc2('u6'), t6['u'] + '\\g<1>\\g<2>'),
        (_rc2('U6'), t6['U'] + '\\g<1>\\g<2>'),
        (_rc2('u7'), '\u016b\\g<1>\\g<2>'),   # ū
        (_rc2('U7'), '\u016a\\g<1>\\g<2>'),   # Ū
        (_rc3(r'uih((?:N|\u207f|nn)?)8'), 'u\u030dih\\g<1>'),   # u̍ih
        (_rc3(r'Uih((?:N|\u207f|nn)?)8'), 'U\u030dih\\g<1>'),
        (_rc2('u9'), '\u016d\\g<1>\\g<2>'),   # ŭ
        (_rc2('U9'), '\u016c\\g<1>\\g<2>'),   # Ŭ

        # ── oa/oe compound (RC3) ─────────────────────────────────────────
        (_rc3(r'(o|O)([ae])((?:N|\u207f|nn)?)1?\b'), '\\g<1>\\g<2>\\g<3>'),
        (_rc3(r'o([ae])((?:N|\u207f|nn)?)2\b'), '\u00f3\\g<1>\\g<2>'),  # ó
        (_rc3(r'O([ae])((?:N|\u207f|nn)?)2\b'), '\u00d3\\g<1>\\g<2>'),  # Ó
        (_rc3(r'o([ae])((?:N|\u207f|nn)?)3\b'), '\u00f2\\g<1>\\g<2>'),  # ò
        (_rc3(r'O([ae])((?:N|\u207f|nn)?)3\b'), '\u00d2\\g<1>\\g<2>'),  # Ò
        (_rc3(r'o([ae])((?:N|\u207f|nn)?)5\b'), '\u00f4\\g<1>\\g<2>'),  # ô
        (_rc3(r'O([ae])((?:N|\u207f|nn)?)5\b'), '\u00d4\\g<1>\\g<2>'),  # Ô
        (_rc3(r'o([ae])((?:N|\u207f|nn)?)6\b'), t6['o'] + '\\g<1>\\g<2>'),
        (_rc3(r'O([ae])((?:N|\u207f|nn)?)6\b'), t6['O'] + '\\g<1>\\g<2>'),
        (_rc3(r'o([ae])((?:N|\u207f|nn)?)7\b'), '\u014d\\g<1>\\g<2>'),  # ō
        (_rc3(r'O([ae])((?:N|\u207f|nn)?)7\b'), '\u014c\\g<1>\\g<2>'),  # Ō
        (_rc3(r'o([ae])((?:N|\u207f|nn)?)9\b'), '\u014f\\g<1>\\g<2>'),  # ŏ
        (_rc3(r'O([ae])((?:N|\u207f|nn)?)9\b'), '\u014e\\g<1>\\g<2>'),  # Ŏ

        # ── a (RC1 / RC4 / RC8) ──────────────────────────────────────────
        (_rc1('a1'), 'a\\g<1>'),             (_rc1('A1'), 'A\\g<1>'),
        (_rc1('a2'), '\u00e1\\g<1>'),        (_rc1('A2'), '\u00c1\\g<1>'),  # á Á
        (_rc1('a3'), '\u00e0\\g<1>'),        (_rc1('A3'), '\u00c0\\g<1>'),  # à À
        (_rc4('a4'), 'a\\g<1>'),             (_rc4('A4'), 'A\\g<1>'),
        (_rc1('a5'), '\u00e2\\g<1>'),        (_rc1('A5'), '\u00c2\\g<1>'),  # â Â
        (_rc1('a6'), t6['a'] + '\\g<1>'),   (_rc1('A6'), t6['A'] + '\\g<1>'),
        (_rc1('a7'), '\u0101\\g<1>'),        (_rc1('A7'), '\u0100\\g<1>'),  # ā Ā
        (_rc8('a8'), 'a\u030d\\g<1>'),       (_rc8('A8'), 'A\u030d\\g<1>'), # a̍ A̍
        (_rc1('a9'), '\u0103\\g<1>'),        (_rc1('A9'), '\u0102\\g<1>'),  # ă Ă

        # ── e ────────────────────────────────────────────────────────────
        (_rc1('e1'), 'e\\g<1>'),             (_rc1('E1'), 'E\\g<1>'),
        (_rc1('e2'), '\u00e9\\g<1>'),        (_rc1('E2'), '\u00c9\\g<1>'),  # é É
        (_rc1('e3'), '\u00e8\\g<1>'),        (_rc1('E3'), '\u00c8\\g<1>'),  # è È
        (_rc4('e4'), 'e\\g<1>'),             (_rc4('E4'), 'E\\g<1>'),
        (_rc1('e5'), '\u00ea\\g<1>'),        (_rc1('E5'), '\u00ca\\g<1>'),  # ê Ê
        (_rc1('e6'), t6['e'] + '\\g<1>'),   (_rc1('E6'), t6['E'] + '\\g<1>'),
        (_rc1('e7'), '\u0113\\g<1>'),        (_rc1('E7'), '\u0112\\g<1>'),  # ē Ē
        (_rc8('e8'), 'e\u030d\\g<1>'),       (_rc8('E8'), 'E\u030d\\g<1>'), # e̍ E̍
        (_rc1('e9'), '\u0115\\g<1>'),        (_rc1('E9'), '\u0114\\g<1>'),  # ĕ Ĕ

        # ── i ────────────────────────────────────────────────────────────
        (_rc1('i1'), 'i\\g<1>'),             (_rc1('I1'), 'I\\g<1>'),
        (_rc1('i2'), '\u00ed\\g<1>'),        (_rc1('I2'), '\u00cd\\g<1>'),  # í Í
        (_rc1('i3'), '\u00ec\\g<1>'),        (_rc1('I3'), '\u00cc\\g<1>'),  # ì Ì
        (_rc4('i4'), 'i\\g<1>'),             (_rc4('I4'), 'I\\g<1>'),
        (_rc1('i5'), '\u00ee\\g<1>'),        (_rc1('I5'), '\u00ce\\g<1>'),  # î Î
        (_rc1('i6'), t6['i'] + '\\g<1>'),   (_rc1('I6'), t6['I'] + '\\g<1>'),
        (_rc1('i7'), '\u012b\\g<1>'),        (_rc1('I7'), '\u012a\\g<1>'),  # ī Ī
        (_rc8('i8'), 'i\u030d\\g<1>'),       (_rc8('I8'), 'I\u030d\\g<1>'), # i̍ I̍
        (_rc1('i9'), '\u012d\\g<1>'),        (_rc1('I9'), '\u012c\\g<1>'),  # ĭ Ĭ

        # ── o͘  (o + U+0358) RC1 / RC4 / RC8 ─────────────────────────────
        (_rc1('o\u03581'), 'o\u0358\\g<1>'),              (_rc1('O\u03581'), 'O\u0358\\g<1>'),
        (_rc1('o\u03582'), '\u00f3\u0358\\g<1>'),         (_rc1('O\u03582'), '\u00d3\u0358\\g<1>'), # ó͘ Ó͘
        (_rc1('o\u03583'), '\u00f2\u0358\\g<1>'),         (_rc1('O\u03583'), '\u00d2\u0358\\g<1>'), # ò͘ Ò͘
        (_rc4('o\u03584'), 'o\u0358\\g<1>'),              (_rc4('O\u03584'), 'O\u0358\\g<1>'),
        (_rc1('o\u03585'), '\u00f4\u0358\\g<1>'),         (_rc1('O\u03585'), '\u00d4\u0358\\g<1>'), # ô͘ Ô͘
        (_rc1('o\u03586'), t6['o\u0358'] + '\\g<1>'),    (_rc1('O\u03586'), t6['O\u0358'] + '\\g<1>'),
        (_rc1('o\u03587'), '\u014d\u0358\\g<1>'),         (_rc1('O\u03587'), '\u014c\u0358\\g<1>'), # ō͘ Ō͘
        (_rc8('o\u03588'), 'o\u030d\u0358\\g<1>'),        (_rc8('O\u03588'), 'O\u030d\u0358\\g<1>'), # o̍͘ O̍͘
        (_rc1('o\u03589'), '\u014f\u0358\\g<1>'),         (_rc1('O\u03589'), '\u014e\u0358\\g<1>'), # ŏ͘ Ŏ͘

        # ── plain o ──────────────────────────────────────────────────────
        (_rc1('o1'), 'o\\g<1>'),             (_rc1('O1'), 'O\\g<1>'),
        (_rc1('o2'), '\u00f3\\g<1>'),        (_rc1('O2'), '\u00d3\\g<1>'),  # ó Ó
        (_rc1('o3'), '\u00f2\\g<1>'),        (_rc1('O3'), '\u00d2\\g<1>'),  # ò Ò
        (_rc4('o4'), 'o\\g<1>'),             (_rc4('O4'), 'O\\g<1>'),
        (_rc1('o5'), '\u00f4\\g<1>'),        (_rc1('O5'), '\u00d4\\g<1>'),  # ô Ô
        (_rc1('o6'), t6['o'] + '\\g<1>'),   (_rc1('O6'), t6['O'] + '\\g<1>'),
        (_rc1('o7'), '\u014d\\g<1>'),        (_rc1('O7'), '\u014c\\g<1>'),  # ō Ō
        (_rc8('o8'), 'o\u030d\\g<1>'),       (_rc8('O8'), 'O\u030d\\g<1>'), # o̍ O̍
        (_rc1('o9'), '\u014f\\g<1>'),        (_rc1('O9'), '\u014e\\g<1>'),  # ŏ Ŏ

        # ── u ────────────────────────────────────────────────────────────
        (_rc1('u1'), 'u\\g<1>'),             (_rc1('U1'), 'U\\g<1>'),
        (_rc1('u2'), '\u00fa\\g<1>'),        (_rc1('U2'), '\u00da\\g<1>'),  # ú Ú
        (_rc1('u3'), '\u00f9\\g<1>'),        (_rc1('U3'), '\u00d9\\g<1>'),  # ù Ù
        (_rc4('u4'), 'u\\g<1>'),             (_rc4('U4'), 'U\\g<1>'),
        (_rc1('u5'), '\u00fb\\g<1>'),        (_rc1('U5'), '\u00db\\g<1>'),  # û Û
        (_rc1('u6'), t6['u'] + '\\g<1>'),   (_rc1('U6'), t6['U'] + '\\g<1>'),
        (_rc1('u7'), '\u016b\\g<1>'),        (_rc1('U7'), '\u016a\\g<1>'),  # ū Ū
        (_rc8('u8'), 'u\u030d\\g<1>'),       (_rc8('U8'), 'U\u030d\\g<1>'), # u̍ U̍
        (_rc1('u9'), '\u016d\\g<1>'),        (_rc1('U9'), '\u016c\\g<1>'),  # ŭ Ŭ

        # ── m (syllabic) ─────────────────────────────────────────────────
        (_rc3('m1'), 'm'),           (_rc3('M1'), 'M'),
        (_rc3('m2'), '\u1e3f'),      (_rc3('M2'), '\u1e3e'),     # ḿ Ḿ
        (_rc3('m3'), 'm\u0300'),     (_rc3('M3'), 'M\u0300'),    # m̀ M̀
        (_rc3('mh4'), 'mh'),         (_rc3('Mh4'), 'Mh'),
        (_rc3('m5'), 'm\u0302'),     (_rc3('M5'), 'M\u0302'),    # m̂ M̂
        (_rc3('m6'), t6['m']),       (_rc3('M6'), t6['M']),
        (_rc3('m7'), 'm\u0304'),     (_rc3('M7'), 'M\u0304'),    # m̄ M̄
        (_rc3('mh8'), 'm\u030dh'),   (_rc3('Mh8'), 'M\u030dh'), # m̍h M̍h
        (_rc3('m9'), 'm\u0306'),     (_rc3('M9'), 'M\u0306'),    # m̆ M̆

        # ── ng (syllabic) ────────────────────────────────────────────────
        (_rc3('ng1'), 'ng'),         (_rc3('Ng1'), 'Ng'),
        (_rc3('ng2'), '\u0144g'),    (_rc3('Ng2'), '\u0143g'),   # ńg Ńg
        (_rc3('ng3'), '\u01f9g'),    (_rc3('Ng3'), '\u01f8g'),   # ǹg Ǹg
        (_rc3('ngh4'), 'ngh'),       (_rc3('Ngh4'), 'Ngh'),
        (_rc3('ng5'), 'n\u0302g'),   (_rc3('Ng5'), 'N\u0302g'), # n̂g N̂g
        (_rc3('ng6'), t6['n'] + 'g'), (_rc3('Ng6'), t6['N'] + 'g'),
        (_rc3('ng7'), 'n\u0304g'),   (_rc3('Ng7'), 'N\u0304g'), # n̄g N̄g
        (_rc3('ngh8'), 'n\u030dgh'), (_rc3('Ngh8'), 'N\u030dgh'),# n̍gh N̍gh
        (_rc3('ng9'), 'n\u0306g'),   (_rc3('Ng9'), 'N\u0306g'), # n̆g N̆g
    ]

    if TL_ALL_CAPS_SUPPORT:
        POJ_S2T += [
            (_rc3('NG1'), 'NG'),
            (_rc3('NG2'), '\u0143G'),          # ŃG (always acute, like ng2)
            (_rc3('NG3'), '\u01f8G'),           # ǸG
            (_rc3('NGH4'), 'NGH'),
            (_rc3('NG5'), 'N\u0302G'),          # N̂G
            (_rc3('NG6'), t6['N'] + 'G'),
            (_rc3('NG7'), 'N\u0304G'),          # N̄G
            (_rc3('NGH8'), 'N\u030dGH'),        # N̍GH
            (_rc3('NG9'), 'N\u0306G'),          # N̆G
        ]

    for (pat, repl) in POJ_S2T:
        s = pat.sub(repl, s)

    return s


# ---------------------------------------------------------------------------
# Main pipeline: TL Tone Marks -> POJ Tone Marks
# ---------------------------------------------------------------------------
def tl_tiau_2_poj_tiau(tl_tiau):
    # ── 0a. Preserve URLs and markdown links ──────────────────────────────
    url_ph = []

    def _save_url(m):
        url_ph.append(m.group(0))
        return '\u27eaURL{}\u27eb'.format(len(url_ph) - 1)

    text = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', _save_url, tl_tiau)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', _save_url, text)
    text = re.sub(r'(https?|ftp)://[^\s\)\]\\]+', _save_url, text)

    # ── 0b. Escape sequences \[...\] -> preserve ──────────────────────────
    esc_ph = []
    processed = ''
    i = 0
    while i < len(text):
        if text[i] == '\\' and i + 1 < len(text) and text[i + 1] == '[':
            start = i + 2
            end = text.find('\\]', start)
            if end == -1:
                esc_ph.append(text[start:])
                processed += '\u27eaESC{}\u27eb'.format(len(esc_ph) - 1)
                break
            esc_ph.append(text[start:end])
            processed += '\u27eaESC{}\u27eb'.format(len(esc_ph) - 1)
            i = end + 2
        else:
            processed += text[i]
            i += 1

    # ── 0c. Preserve existing POJ syllables (contain ⁿ or o͘/O͘) ──────────
    poj_ph = []

    def _save_poj(m):
        poj_ph.append(m.group(0))
        return '\u27eaPOJ{}\u27eb'.format(len(poj_ph) - 1)

    def _save_poj_oo(m):
        if '\u27eaPOJ' in m.group(0):
            return m.group(0)
        poj_ph.append(m.group(0))
        return '\u27eaPOJ{}\u27eb'.format(len(poj_ph) - 1)

    processed = re.sub(r'(?:[^\W\d_]|[\u0300-\u036f])+\u207f', _save_poj, processed, flags=re.UNICODE)
    processed = re.sub(r'(?:[^\W\d_]|[\u0300-\u036f])*[oO]\u0358(?:[^\W\d_]|[\u0300-\u036f])*',
                       _save_poj_oo, processed, flags=re.UNICODE)

    # ── 1-3. Three-stage conversion ───────────────────────────────────────
    result = poj_soo_2_poj_tiau(tl_soo_2_poj_soo(tl_tiau_2_tl_soo(processed)))

    # ── 4. Restore placeholders ───────────────────────────────────────────
    result = re.sub(r'\u27eaESC(\d+)\u27eb', lambda m: esc_ph[int(m.group(1))], result)
    result = re.sub(r'\u27eaPOJ(\d+)\u27eb', lambda m: poj_ph[int(m.group(1))], result)
    result = re.sub(r'\u27eaURL(\d+)\u27eb', lambda m: url_ph[int(m.group(1))], result)

    return result


# Public alias matching JS export name
convert_text = tl_tiau_2_poj_tiau


# ---------------------------------------------------------------------------
# Self-test (mirrors JS runTests() exactly)
# ---------------------------------------------------------------------------
def run_tests():
    global TL_USE_NASAL_SUPERSCRIPT, TL_TONE6_USE_CARON, TL_ALL_CAPS_SUPPORT

    orig_nasal    = TL_USE_NASAL_SUPERSCRIPT
    orig_tone6    = TL_TONE6_USE_CARON
    orig_all_caps = TL_ALL_CAPS_SUPPORT

    def _run(inp, expected, config=None):
        global TL_USE_NASAL_SUPERSCRIPT, TL_TONE6_USE_CARON, TL_ALL_CAPS_SUPPORT
        cfg = config or {}
        TL_USE_NASAL_SUPERSCRIPT = cfg.get('nasal', True)
        TL_TONE6_USE_CARON       = cfg.get('tone6', False)
        TL_ALL_CAPS_SUPPORT      = cfg.get('allCaps', True)
        result = tl_tiau_2_poj_tiau(inp)
        ok = result == expected
        if not ok:
            print(f'[FAIL] In: {inp!r} | Expected: {expected!r} | Got: {result!r} | Cfg: {cfg}')
        return ok

    passed = total = 0
    TESTS = [
        # 1. Basic rules
        {'in': 'phah',               'out': 'phah'},
        {'in': 'T\u00e2i-u\u00e2n',  'out': 'T\u00e2i-o\u00e2n'},  # Tâi-uân -> Tâi-oân
        {'in': 'tsu\u00ed',          'out': 'ch\u00fai'},            # tsuí -> chúi
        {'in': 'tshu\u00ed',         'out': 'chh\u00fai'},           # tshuí -> chhúi
        {'in': 'ing',                'out': 'eng'},
        {'in': 'tsa',                'out': 'cha'},
        {'in': 'ua',                 'out': 'oa'},
        {'in': 'ue',                 'out': 'oe'},
        # 2. OO mapping
        {'in': '\u00f3o',            'out': '\u00f3\u0358'},         # óo -> ó͘
        {'in': 'oo',                 'out': 'o\u0358'},              # oo -> o͘
        {'in': 'Oo',                 'out': 'O\u0358'},              # Oo -> O͘
        {'in': '\u014do',            'out': '\u014d\u0358'},         # Ōo -> Ō͘
        # 3. Nasal superscript (default ON)
        {'in': 'siann',              'out': 'sia\u207f'},
        {'in': 'Siann',              'out': 'Sia\u207f'},
        {'in': 'hnn',                'out': 'h\u207f'},
        {'in': 'Hnn',                'out': 'H\u207f'},
        {'in': 'mng',                'out': 'mng'},
        {'in': 'ng',                 'out': 'ng'},
        {'in': 'ann',                'out': 'a\u207f'},
        {'in': 'aN',                 'out': 'a\u207f',  'config': {'nasal': True}},
        {'in': 'aN',                 'out': 'aN',       'config': {'nasal': False}},
        {'in': 'annh',               'out': 'ah\u207f'},
        {'in': 'innh',               'out': 'ih\u207f'},
        {'in': 'aNh',                'out': 'ah\u207f', 'config': {'nasal': True}},
        {'in': 'aNh',                'out': 'aNh',      'config': {'nasal': False}},
        # 4. Tone 6
        {'in': 'si6',                'out': 's\u00ed',  'config': {'tone6': False}},  # sí
        {'in': 'si6',                'out': 's\u01d0',  'config': {'tone6': True}},   # sǐ
        # 5. All-caps
        {'in': 'TSUI',               'out': 'CHUI',     'config': {'allCaps': True}},
        {'in': 'TSUI',               'out': 'TSUI',     'config': {'allCaps': False}},
        {'in': 'TSHUI',              'out': 'CHHUI',    'config': {'allCaps': True}},
        {'in': 'UAN',                'out': 'OAN',      'config': {'allCaps': True}},
        {'in': 'OO',                 'out': 'O\u0358',  'config': {'allCaps': True}},
        # 6. Mixed text / pass-through
        {'in': '123',                'out': '123'},
        {'in': 'Hello (T\u00e2i-g\u00ed)!', 'out': 'Hello (T\u00e2i-g\u00ed)!'},
        {'in': 'html',               'out': 'html'},
        # 7. Escape sequences \[...\]
        {'in': '\\[T\u00e2i-u\u00e2n\\]',    'out': 'T\u00e2i-u\u00e2n'},
        {'in': '\\[tsu\u00ed\\]',             'out': 'tsu\u00ed'},
        {'in': '\\[line 1\nline 2\\]',        'out': 'line 1\nline 2'},
        {'in': '\\[no closing',               'out': 'no closing'},
        {'in': '\\[first \\[ second\\] rest', 'out': 'first \\[ second rest'},
        {'in': 'a tsu\u00ed \\[tsu\u00ed\\] b', 'out': 'a ch\u00fai tsu\u00ed b'},
        {'in': '\\[arr[0]\\]',                'out': 'arr[0]'},
        # 8. POJ preservation
        {'in': 'chi\u00e2\u207f',             'out': 'chi\u00e2\u207f'},   # chiâⁿ
        {'in': 'sia\u207f',                   'out': 'sia\u207f'},
        {'in': 'koa\u207f',                   'out': 'koa\u207f'},
        {'in': 'h\u00f3\u0358',              'out': 'h\u00f3\u0358'},      # hó͘
        {'in': 'g\u00f4\u0358',              'out': 'g\u00f4\u0358'},      # gô͘
        {'in': 'o\u0358',                    'out': 'o\u0358'},
        {'in': 'chi\u00e2\u207f tsu\u00ed',  'out': 'chi\u00e2\u207f ch\u00fai'},
        {'in': 'h\u00f3\u0358 oo',           'out': 'h\u00f3\u0358 o\u0358'},
        {'in': 'Chi\u00e2\u207f',            'out': 'Chi\u00e2\u207f'},    # Capital POJ
        {'in': '\u00d4\u0358',               'out': '\u00d4\u0358'},       # Ô͘
    ]

    for t in TESTS:
        total += 1
        if _run(t['in'], t['out'], t.get('config')):
            passed += 1

    TL_USE_NASAL_SUPERSCRIPT = orig_nasal
    TL_TONE6_USE_CARON       = orig_tone6
    TL_ALL_CAPS_SUPPORT      = orig_all_caps

    if passed == total:
        print(f'All {total} tests passed!')
    else:
        print(f'{passed}/{total} tests passed.')


# Self-test on load (default OFF — mirrors JS ENABLE_SELF_TEST = false)
ENABLE_SELF_TEST = False
if ENABLE_SELF_TEST:
    run_tests()
