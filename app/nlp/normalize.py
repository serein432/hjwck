# -*- coding: utf-8 -*-
"""
中文文本规范化（FR-015 / 8.9 / 附录A）：
数字分位、金额（元角分）、订单号/编号逐字或分段、单位、车牌、英文缩写。
纯规则 + 正则，可配置扩展。
"""
import re

# 中文数字
_CN_DIGITS = "零一二三四五六七八九"
_CN_UNITS = ["", "十", "百", "千"]
_CN_BIG = ["", "万", "亿"]

# 英文字母读音（订单号/编号用）
_EN_LETTERS = {
    "a": "艾", "b": "必", "c": "西", "d": "迪", "e": "伊", "f": "艾弗",
    "g": "吉", "h": "艾奇", "i": "艾", "j": "杰", "k": "开", "l": "艾勒",
    "m": "艾姆", "n": "恩", "o": "欧", "p": "皮", "q": "秋", "r": "啊",
    "s": "艾斯", "t": "提", "u": "优", "v": "维", "w": "达不溜",
    "x": "艾克斯", "y": "歪", "z": "贼",
}

# 车牌省份简称读音
_PLATE_PROVINCE = {
    "京": "京", "津": "津", "冀": "冀", "晋": "晋", "蒙": "蒙", "辽": "辽",
    "吉": "吉", "黑": "黑", "沪": "沪", "苏": "苏", "浙": "浙", "皖": "皖",
    "闽": "闽", "赣": "赣", "鲁": "鲁", "豫": "豫", "鄂": "鄂", "湘": "湘",
    "粤": "粤", "桂": "桂", "琼": "琼", "渝": "渝", "川": "川", "贵": "贵",
    "云": "云", "藏": "藏", "陕": "陕", "甘": "甘", "青": "青", "宁": "宁",
    "新": "新",
}

# 单位规范化（正则 → 中文朗读）
_UNIT_MAP = [
    (r"(\d+(?:\.\d+)?)\s*km", r"公里"),
    (r"(\d+(?:\.\d+)?)\s*kg", r"千克"),
    (r"(\d+(?:\.\d+)?)\s*℃", r"度"),
    (r"(\d+(?:\.\d+)?)\s*度", r"度"),
    (r"(\d+(?:\.\d+)?)\s*元", r"元"),
]


def _int_to_cn(n):
    """整数 → 中文分位读法（1234 → 一千二百三十四）。"""
    n = int(n)
    if n == 0:
        return "零"
    if n < 0:
        return "负" + _int_to_cn(-n)
    parts = []
    big_i = 0
    while n > 0:
        seg = n % 10000
        if seg != 0:
            seg_str = _seg_to_cn(seg)
            parts.append(seg_str + _CN_BIG[big_i])
        elif parts and seg == 0 and (n // 10000) % 10000 != 0:
            parts.append("零")
        n //= 10000
        big_i += 1
    parts.reverse()
    s = "".join(parts)
    # 处理连续零
    while "零零" in s:
        s = s.replace("零零", "零")
    return s


def _seg_to_cn(seg):
    """0~9999 → 中文。"""
    if seg == 0:
        return ""
    s = ""
    digits = []
    while seg > 0:
        digits.append(seg % 10)
        seg //= 10
    digits.reverse()
    n = len(digits)
    zero_flag = False
    for i, d in enumerate(digits):
        unit = n - 1 - i
        if d == 0:
            if s and not s.endswith("零"):
                zero_flag = True
        else:
            if zero_flag:
                s += "零"
                zero_flag = False
            if d == 1 and unit == 1 and not s:
                s += "十"
            else:
                s += _CN_DIGITS[d]
            s += _CN_UNITS[unit]
    return s


def _num_to_cn(s):
    """阿拉伯数字串 → 中文（含小数）。"""
    if "." in s:
        ip, fp = s.split(".", 1)
        base = _int_to_cn(int(ip)) if ip else "零"
        frac = "".join(_CN_DIGITS[int(c)] for c in fp if c.isdigit())
        return base + "点" + frac
    return _int_to_cn(int(s))


def _amount_to_cn(s):
    """金额 58.5 → 五十八元五角 58.56 → 五十八元五角六分。"""
    try:
        val = float(s)
    except ValueError:
        return s
    total = round(val * 100)
    yuan = total // 100
    jiao = (total // 10) % 10
    fen = total % 10
    out = _int_to_cn(yuan) + "元" if yuan else ""
    if jiao:
        out += _CN_DIGITS[jiao] + "角"
    if fen:
        out += _CN_DIGITS[fen] + "分"
    return out or "零元"


def _id_to_cn(s):
    """订单号/编号：字母按读音，数字逐位（A1024 → 艾 一零二四）。"""
    out = []
    for ch in s:
        if ch.isalpha():
            out.append(_EN_LETTERS.get(ch.lower(), ch))
        elif ch.isdigit():
            out.append(_CN_DIGITS[int(ch)])
        else:
            out.append(ch)
    return "".join(out)


def _plate_to_cn(s):
    """车牌：省份简称 + 字母读音 + 数字逐位（粤A12345 → 粤 艾 一二三四五）。"""
    out = []
    for i, ch in enumerate(s):
        if i == 0 and ch in _PLATE_PROVINCE:
            out.append(_PLATE_PROVINCE[ch])
        elif ch.isalpha():
            out.append(_EN_LETTERS.get(ch.lower(), ch))
        elif ch.isdigit():
            out.append(_CN_DIGITS[int(ch)])
        else:
            out.append(ch)
    return "".join(out)


def _float_to_cn(s):
    """小数 → 中文（38.5 → 三十八点五）。"""
    if "." in s:
        ip, fp = s.split(".", 1)
        base = _int_to_cn(int(ip)) if ip else "零"
        frac = "".join(_CN_DIGITS[int(c)] for c in fp if c.isdigit())
        return base + "点" + frac
    return _int_to_cn(int(s))


def normalize(text):
    """主入口：把原始文本规范化为适合朗读的中文。"""
    if not text:
        return text
    t = text
    # 1) 车牌（先于订单号，避免误判）
    t = re.sub(r"([京津冀晋蒙辽吉黑沪苏浙皖闽赣鲁豫鄂湘粤桂琼渝川贵云藏陕甘青宁新][A-Z][A-Z0-9]{4,5})",
               lambda m: _plate_to_cn(m.group(1)), t)
    # 2) 日期：YYYY年MM月DD日 → 逐位读（2026年10月23日 → 二零二六年十月二十三日）
    def _date(m):
        y = m.group(1)
        mon = _int_to_cn(int(m.group(2)))
        d = _int_to_cn(int(m.group(3)))
        ycn = "".join(_CN_DIGITS[int(c)] for c in y)
        return f"{ycn}年{mon}月{d}日"
    t = re.sub(r"(\d{4})年(\d{1,2})月(\d{1,2})日", _date, t)
    # 3) 金额（带 元 的，先处理避免被小数规则吃掉）
    t = re.sub(r"(\d+(?:\.\d+)?)\s*元", lambda m: _amount_to_cn(m.group(1)), t)
    # 4) 订单号/编号：字母+数字混合（A1024、NO.20261022），字母在前或点号编号
    t = re.sub(r"\b([A-Za-z]+\.\s*)?([A-Za-z]+-?\d{3,}[A-Za-z0-9]*)\b",
               lambda m: _id_to_cn((m.group(1) or "") + m.group(2)), t)
    # 5) 长数字串（>=4位）逐位读（订单/日期号，避免分位歧义）
    t = re.sub(r"\b(\d{4,})\b", lambda m: "".join(_CN_DIGITS[int(c)] for c in m.group(1)), t)
    # 6) 小数（38.5 → 三十八点五）
    t = re.sub(r"\b(\d+\.\d+)\b", lambda m: _float_to_cn(m.group(1)), t)
    # 7) 一般数字分位（1234 → 一千二百三十四）
    t = re.sub(r"\b(\d{2,3})\b", lambda m: _int_to_cn(int(m.group(1))), t)
    # 8) 单位（度/公里/千克等，数字已中文）
    t = re.sub(r"(度|公里|千米|千克|米|秒|分钟|小时|个|台|张|单|件)\b", r"\1", t)
    return t.strip()


if __name__ == "__main__":
    cases = [
        "新订单已到达，订单号 A1024，金额 58 元",
        "3号传感器温度超限，当前38.5度",
        "订单号20261022金额1234.56元",
        "车牌粤A12345已入库",
        "距离还有12.5km",
        "现在是2026年10月23日",
    ]
    for c in cases:
        print(f"{c}  =>  {normalize(c)}")
