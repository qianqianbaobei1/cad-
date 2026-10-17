# -*- coding: utf-8 -*-
"""天正文字解密 (TgDataEncrypt::TDUnEncrypt @0x1005544fc) 单元测试。"""

import pytest

from extractor.tangent import (
    _post_process,
    looks_like_tangent_cipher,
    safe_decode_tangent_if_cipher,
    tangent_decrypt_bytes,
    tangent_unencrypt_string,
    tianzheng_key,
)


def _encrypt_default(plain: str) -> str:
    """默认分支 (version<0x11, key=0x528, base='A') 的逆向构造：每字节 -> 两个大写字母。"""
    out = []
    for b in plain.encode("gb18030"):
        t = b ^ 0x05
        out.append(chr(0x41 + t // 26) + chr(0x41 + t % 26))
    return "".join(out)


def _encrypt_generic(data: bytes, version: int, n: int) -> str:
    """任意分支的逆向构造（不含后处理）：c1 低字节取 0，c2 低字节解同余方程。"""
    key, base = tianzheng_key(version, n)
    kb = (key >> 8) & 0xFF
    out = []
    for b in data:
        t = b ^ kb
        y = (t + 27 * base) & 0xFF
        out.append(chr(0x100) + chr(0x100 | y))
    return "".join(out)


@pytest.mark.parametrize("plain", ["配电箱", "AL1 照明配电箱", "BV-3x2.5", "WDZN-YJY-5x16 SC32"])
def test_default_branch_roundtrip(plain):
    cipher = _encrypt_default(plain)
    assert cipher.isalpha() and cipher.isupper()
    assert tangent_unencrypt_string(cipher) == plain
    assert tangent_unencrypt_string(cipher, version=0x10, key=7) == plain  # n 不影响默认分支


def test_default_branch_known_vector():
    # '配' = GB18030 C5 E4；0xC5^5=0xC0=192=7*26+10 -> "HK"，0xE4^5=0xE1=225=8*26+17 -> "IR"
    assert tangent_unencrypt_string("HKIR") == "配"


def test_safe_decode_default_cipher():
    cipher = _encrypt_default("照明")
    assert looks_like_tangent_cipher(cipher)
    assert safe_decode_tangent_if_cipher(cipher) == "照明"
    assert safe_decode_tangent_if_cipher("MCCB-250A/3P") == "MCCB-250A/3P"
    assert tangent_unencrypt_string("A") == "A"
    assert tangent_unencrypt_string("") == ""


@pytest.mark.parametrize(
    "version,n,expected",
    [
        (0x10, 0, (0x528, 0x41)),
        (0x0F, 99, (0x528, 0x41)),
        # 0x11
        (0x11, 0, (0x9D2, 0xDF)),
        (0x11, 0x63, (0x9D2 + 0x63, 0xDF)),
        (0x11, 0x64, (0x9D2, 0xC6)),
        # 0x12
        (0x12, 4, (10 * 4 + 0x72, 8 * 4 + 0x76)),
        (0x12, 5, (11 * 5 + 0x93, 8 * 5 + 0xDA)),
        (0x12, 9, (11 * 9 + 0x93, 8 * 9 + 0xDA)),
        (0x12, 0xE, (13 * 0xE + 0x13A, 8 * 0xE + 0x13E)),
        (0x12, 0x13, (15 * 0x13 + 0x9D, 8 * 0x13 + 0x1A2)),
        (0x12, 0x14, (18 * 0x14 + 0xD6, 3 * 0x14 + 0x206)),
        # 0x13
        (0x13, 0x13, (10 * 0x13 + 0x68, 5 * 0x13 + 0xDA)),
        (0x13, 0x27, (11 * 0x27 + 0x89, 6 * 0x27 + 0x26A)),
        (0x13, 0x36, (13 * 0x36 + 0x144, 7 * 0x36 + 0x76)),
        (0x13, 0x59, (15 * 0x59 + 0x9D, 8 * 0x59 + 0xDA)),
        (0x13, 0x5A, (8 * 0x5A + 0xDF, 3 * 0x5A + 0x13E)),
        # 0x14
        (0x14, 0x13, (10 * 0x13 + 0x72, 5 * 0x13 + 0xD0)),
        (0x14, 0x27, (11 * 0x27 + 0x25, 6 * 0x27 + 0xDA)),
        (0x14, 0x36, (13 * 0x36 + 0xE0, 7 * 0x36 + 0x6C)),
        (0x14, 0x59, (15 * 0x59 + 0x101, 8 * 0x59 + 0x13E)),
        (0x14, 0x5A, (8 * 0x5A + 0x7B, 3 * 0x5A + 0x76)),
        # > 0x14
        (0x15, 0x13, (10 * 0x13 + 0x6E, 5 * 0x13 + 0xCB)),
        (0x15, 0x27, (11 * 0x27 + 0x27, 6 * 0x27 + 0xD0)),
        (0x20, 0x36, (13 * 0x36 + 0xCC, 7 * 0x36 + 0x76)),
        (0x20, 0x59, (15 * 0x59 + 0xE3, 8 * 0x59 + 0x134)),
        (0x20, 0x5A, (8 * 0x5A + 0x71, 3 * 0x5A + 0x80)),
        # 第一段为有符号比较，负 n 落在第一段
        (0x12, -1, (10 * -1 + 0x72, 8 * -1 + 0x76)),
    ],
)
def test_tianzheng_key_table(version, n, expected):
    assert tianzheng_key(version, n) == expected


@pytest.mark.parametrize(
    "version,vals,expected",
    [
        (0x10, [1, 2, 3, 4, 5], [1, 2, 3, 4, 5]),
        (0x11, [1, 2], [1, 2]),
        (0x11, [1, 2, 3, 4, 5], [2, 4, 5]),
        (0x12, [1, 2, 3, 4, 5], [3, 5]),
        (0x13, [1, 2, 3, 4], [3]),
        (0x12, [1, 2, 3], [2]),
        (0x13, [1, 2, 3], [2]),
        (0x14, [1, 2, 3, 4, 5], [4, 2, 1]),
        (0x15, [1, 2, 3], [2]),
    ],
)
def test_post_process(version, vals, expected):
    assert _post_process(vals, version) == expected


@pytest.mark.parametrize("version,n", [(0x11, 3), (0x11, 0x70), (0x12, 2), (0x12, 12), (0x13, 30),
                                       (0x14, 45), (0x14, 0x60), (0x16, 0)])
def test_high_version_roundtrip(version, n):
    data = bytes(range(0x30, 0x3A))
    cipher = _encrypt_generic(data, version, n)
    assert tangent_decrypt_bytes(cipher, version=version, n=n) == bytes(_post_process(list(data), version))


def test_high_version_short_strings_skip_post_process():
    data = b"\xc5\xe4"  # '配'，两字节 < 3 不做后处理
    cipher = _encrypt_generic(data, 0x14, 1)
    assert tangent_unencrypt_string(cipher, version=0x14, key=1) == "配"


@pytest.mark.parametrize("word", ["WDZN", "ALAP", "SCPC", "PEPE", "FIRE", "MCCB", "CAFE", "BEEF", "POWER", "NHYJ"])
def test_safe_decode_keeps_plain_uppercase(word):
    # 这些普通标注外形像密文，但对不上密文结构 / GB2312 明文，必须原样保留
    assert safe_decode_tangent_if_cipher(word) == word
