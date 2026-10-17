# -*- coding: utf-8 -*-
"""天正电气 (Tangent CAD) 私有代理实体解包与字符串解密模块。

基于 CAD快速看图 (CADReaderMac 5.2.4, arm64 切片) 逆向核对实现：
1. 逆向函数: TgDataEncrypt::TDUnEncrypt(int version, OdString const&) (地址 0x1005544fc)
   - this 的首个 int 字段（下称 n，对象自带的加密参数）参与密钥计算；
   - 调用方 TgDataEncrypt::TDReadInt32 (0x100554b5c) 在 version >= 0x10 时才走解密。
2. 实体反序列化: TDbText::dwgInFields / TDbEquipment::dwgInFields (地址 0x1005ac000 / 0x10057c458)

算法（详见 docs/逆向分析_CAD快速看图_兼容性与读取能力.md 第 6 节）：
- 按 (version, n) 查分段表得到 (key, base)；
- 每两个字符 v = ((c1 & 0xFF) - base) * 26 + (c2 & 0xFF) - base，每对产出一个值；
- 每个值只保留低字节，与 (key >> 8) & 0xFF 异或，高字节丢弃；
- version >= 0x11 且长度 >= 3 时做反转 / 删字符后处理；
- 最终字节串按 GB18030 解码。
"""

from __future__ import annotations
import re
from typing import Tuple


def tianzheng_key(version: int, n: int = 0) -> Tuple[int, int]:
    """按版本与对象加密参数 n 返回 (key, base)，对应 TDUnEncrypt 0x100554524 起的分段表。

    各分支第一段用有符号比较 (b.gt)，后续段用无符号比较 (b.hi)；n 为负数时落在第一段。
    """
    if version < 0x11:                                   # 0x10055456c
        return 0x528, 0x41
    if version == 0x11:                                  # 0x100554824
        if n <= 0x63:
            return 0x9D2 + n, 0xDF
        return 0x9D2, 0xC6                               # 0x1005549d4
    if version == 0x12:
        if n <= 0x4:                                     # 0x100554550
            return 10 * n + 0x72, 8 * n + 0x76
        if n <= 0x9:                                     # 0x100554954
            return 11 * n + 0x93, 8 * n + 0xDA
        if n <= 0xE:                                     # 0x1005549e4
            return 13 * n + 0x13A, 8 * n + 0x13E
        if n <= 0x13:                                    # 0x100554a74
            return 15 * n + 0x9D, 8 * n + 0x1A2
        return 18 * n + 0xD6, 3 * n + 0x206              # 0x100554ae0
    if version == 0x13:
        if n <= 0x13:                                    # 0x1005547d8
            return 10 * n + 0x68, 5 * n + 0xDA
        if n <= 0x27:                                    # 0x100554904
            return 11 * n + 0x89, 6 * n + 0x26A
        if n <= 0x36:                                    # 0x1005549a0 -> 0x100554aa0
            return 13 * n + 0x144, 7 * n + 0x76
        if n <= 0x59:                                    # 0x100554a34
            return 15 * n + 0x9D, 8 * n + 0xDA
        return 8 * n + 0xDF, 3 * n + 0x13E               # 0x100554ab8
    if version == 0x14:
        if n <= 0x13:                                    # 0x100554800
            return 10 * n + 0x72, 5 * n + 0xD0
        if n <= 0x27:                                    # 0x10055492c
            return 11 * n + 0x25, 6 * n + 0xDA
        if n <= 0x36:                                    # 0x1005549b8
            return 13 * n + 0xE0, 7 * n + 0x6C
        if n <= 0x59:                                    # 0x100554a54
            return 15 * n + 0x101, 8 * n + 0x13E
        return 8 * n + 0x7B, 3 * n + 0x76                # 0x100554acc
    # version > 0x14
    if n <= 0x13:                                        # 0x10055497c
        return 10 * n + 0x6E, 5 * n + 0xCB
    if n <= 0x27:                                        # 0x100554a08
        return 11 * n + 0x27, 6 * n + 0xD0
    if n <= 0x36:                                        # 0x100554a98
        return 13 * n + 0xCC, 7 * n + 0x76
    if n <= 0x59:                                        # 0x100554b04
        return 15 * n + 0xE3, 8 * n + 0x134
    return 8 * n + 0x71, 3 * n + 0x80                    # 0x100554b20


def _post_process(vals: list[int], version: int) -> list[int]:
    """version >= 0x11 的后处理（0x100554774 起），对象是解出的宽字符串。

    反汇编中 libc++ 调用经绑定表核对为 wstring::erase(pos, n) / operator=（自赋值，无效果）：
    - version == 0x11：不反转；erase(0,1)、erase(1,1)
    - version 为 0x12/0x13 且长度 >= 4：不反转；erase(0,1)（0x1005547ac）后再 erase(0,1)、erase(1,1)
    - 其余（0x12/0x13 且长度 == 3，或 version >= 0x14）：整串反转（0x100554854）后 erase(0,1)、erase(1,1)
    """
    if version < 0x11 or len(vals) < 3:
        return vals
    out = list(vals)
    if version in (0x12, 0x13) and len(out) != 3:
        del out[0]
    elif version != 0x11:
        out.reverse()
    del out[0]
    del out[1]
    return out


def tangent_decrypt_bytes(raw_str: str, version: int = 0x10, n: int = 0) -> bytes:
    """TDUnEncrypt 的字节级还原：返回解码前的字节串（每对字符 1 字节，含后处理）。"""
    key, base = tianzheng_key(version, n)
    kb = (key >> 8) & 0xFF
    vals = []
    for i in range(0, len(raw_str) - 1, 2):
        c1 = ord(raw_str[i]) & 0xFF
        c2 = ord(raw_str[i + 1]) & 0xFF
        v = ((c1 - base) * 26 + c2 - base) & 0xFFFF
        vals.append((v & 0xFF) ^ kb)
    return bytes(_post_process(vals, version))


def tangent_unencrypt_string(raw_str: str, version: int = 0x10, key: int = 0) -> str:
    """根据 CAD快速看图 TgDataEncrypt::TDUnEncrypt 逆向还原的天正字符串解密逻辑。

    ``key`` 为对象自带的加密参数 n（TgDataEncrypt 首个 int 字段），并非最终异或密钥。
    结果按 GB18030 解码，非法字节以替换符表示。
    """
    if not raw_str or len(raw_str) < 2:
        return raw_str or ""
    return tangent_decrypt_bytes(raw_str, version=version, n=key).decode("gb18030", errors="replace")


def looks_like_tangent_cipher(text: str, version: int = 0x10) -> bool:
    """判断字符串是否呈现天正 26 进制加密特征。

    version < 0x11 时 base 为 'A'，密文为偶数长度的纯英文字母；更高版本的 base 不在字母区间，
    只要求偶数长度且不含空白。
    """
    if not text or len(text) < 4 or len(text) % 2 != 0:
        return False
    if version < 0x11:
        return bool(re.match(r"^[A-Za-z]{4,}$", text))
    return not any(c.isspace() for c in text)


def _pairs_in_byte_range(text: str, version: int, n: int) -> bool:
    """默认分支（base='A'）下，编码器按 v = 字节 ^ 掩码 生成密文，每对字母的 v 必在 0..255，
    即每位都是大写字母且首字母不超过 'J'。普通英文单词（FIRE、PEPE）大多过不了这一关。"""
    if version >= 0x11:
        return True
    _, base = tianzheng_key(version, n)
    for i in range(0, len(text) - 1, 2):
        d1, d2 = ord(text[i]) - base, ord(text[i + 1]) - base
        if not (0 <= d1 <= 25 and 0 <= d2 <= 25) or d1 * 26 + d2 > 0xFF:
            return False
    return True


def _plausible_plain(data: bytes) -> bool:
    """明文只允许可打印 ASCII 与 GB2312 双字节区（首字节 A1-F7、尾字节 A1-FE），且至少一个汉字。"""
    i, hanzi = 0, 0
    while i < len(data):
        b = data[i]
        if 0x20 <= b < 0x7F:
            i += 1
            continue
        if i + 1 < len(data) and 0xA1 <= b <= 0xF7 and 0xA1 <= data[i + 1] <= 0xFE:
            hanzi += b >= 0xB0
            i += 2
            continue
        return False
    return hanzi > 0


def safe_decode_tangent_if_cipher(text: str, version: int = 0x10, key: int = 0) -> str:
    """若字符串符合天正密文特征则尝试解密，若解密后有效则返回明文，否则保持原样。

    纯大写的正常标注（WDZN、ALAP 等）也满足密文外形，所以只在密文结构与明文编码都对得上、
    且解出汉字时才采纳。
    """
    if not looks_like_tangent_cipher(text, version=version) or not _pairs_in_byte_range(text, version, key):
        return text
    data = tangent_decrypt_bytes(text, version=version, n=key)
    if not _plausible_plain(data):
        return text
    return data.decode("gb18030")
