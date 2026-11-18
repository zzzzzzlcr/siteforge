"""生成期规则：手机号整号 / 前缀（如 04 / 0207）、生日随机、密码强随机。

这一层只做**纯函数**：校验载荷、解析日期格式、把规则写进 FILLS。
运行时的生成器在 template.py / template_loop.py 里（产物必须自包含，不能 import 本仓）。
"""
from __future__ import annotations

import random
import re

PHONE_PREFIX_MIN = 2
PHONE_PREFIX_MAX = 10
_DOB_TOKENS = {"D", "DD", "M", "MM", "YYYY", "YY"}
_DATE_RE = re.compile(
    r"(?i)\b(D{1,2}|M{1,2}|Y{2,4})[\/\-\.](D{1,2}|M{1,2}|Y{2,4})[\/\-\.](D{1,2}|M{1,2}|Y{2,4})\b"
)


class RuleError(ValueError):
    """载荷里的规则不合法（`/run` 门口 400 用）。"""


def _fail(msg: str) -> "RuleError":
    return RuleError("规则不合法：%s" % msg)


def _phone_prefix(x: Any) -> str:
    """手机号前缀：2-10 位数字；运行时补随机数字到 10 位。

    ★ 2026-10-23（运营）：「英国电话没有 0 开头的，一般是 `20 7040 XXXX`」——
    ⇒ **不再要求 0 开头**：英国号去掉长途 0 的前缀就是 `20`（补到 10 位 = 后 8 位随机）；
    澳洲手机仍写 `04`（含长途 0）。
    """
    text = str(x or "").strip()
    if not text.isdigit() or not (PHONE_PREFIX_MIN <= len(text) <= PHONE_PREFIX_MAX):
        raise _fail("手机号前缀得是 2-10 位数字（例 04 / 0207 / 20）；给的是 %r" % text)
    return text


PHONE_EXACT_MIN = 7
PHONE_EXACT_MAX = 15
#: 人话里点名「电话 / 手机 / phone」后面的那一串号（`电话填2014237916`）。
_PHONE_HINT_RE = re.compile(
    r"(?:phone|telephone|mobile|tel|电话|手机号?)[^\d+]{0,24}"
    r"(\+?\d[\d\s\-().]{5,}\d)",
    re.I,
)


def _phone_exact(x: Any) -> str:
    """人给的一整个号码：只留数字（7-15 位）。`+` / 空格 / 横线 / 括号都收。"""
    text = str(x or "").strip()
    if not re.fullmatch(r"\+?[\d\s\-().]+", text):
        raise _fail("手机号得是一串数字（可带 + / 空格 / 横线）；给的是 %r" % text)
    digits = re.sub(r"\D", "", text)
    if not (PHONE_EXACT_MIN <= len(digits) <= PHONE_EXACT_MAX):
        raise _fail("手机号得是 %d-%d 位数字；给的是 %r"
                    % (PHONE_EXACT_MIN, PHONE_EXACT_MAX, text))
    return digits


def phone_from_hints(hints: Any) -> str:
    """人说的话里**点名**给的那个号码（`电话填2014237916` / `phone number填…`）。

    只认「电话/手机/phone」后面紧跟的那一串；没有 = 空串（**不猜**）。
    后说的赢（人在闸上改口时，后一句才是他现在的意思）。
    """
    for hint in reversed(list(hints or [])):
        m = _PHONE_HINT_RE.search(str(hint or ""))
        if not m:
            continue
        digits = re.sub(r"\D", "", m.group(1))
        if PHONE_EXACT_MIN <= len(digits) <= PHONE_EXACT_MAX:
            return digits
    return ""


def pad_phone_prefix(prefix: str) -> str:
    """前缀 + 随机数字补到 10 位；10 位北美号里交换局首位不许是 0/1。

    否则 `20` + 随机 8 位有一半的几率落成 `20X 0… / 20X 1…`，站方按 US 校验会拒
    （真站 `Please enter a valid phone number`）。
    """
    prefix = str(prefix)
    value = prefix + "".join(str(random.randint(0, 9))
                             for _ in range(max(0, 10 - len(prefix))))
    if (len(prefix) <= 3 and not prefix.startswith("0")
            and len(value) == 10 and value[3] in "01"):
        value = value[:3] + str(random.randint(2, 9)) + value[4:]
    return value


def phone_for_explore(rules: Optional[dict], hints: Any = None) -> str:
    """探路那一趟该用哪个号：人点名的整号 → rules.phone → 前缀现编；都没有 = 空串。

    探路与产物**必须是同一个号**，否则窗口里填的跟最后脚本填的对不上
    （用户 2026-10-29：「不按我给的数据输」）。
    """
    exact = phone_from_hints(hints)
    if exact:
        return exact
    rules = rules or {}
    exact = str(rules.get("phone") or "").strip()
    if exact:
        return exact
    prefixes = [str(x) for x in (rules.get("phone_prefixes") or []) if str(x)]
    if prefixes:
        return pad_phone_prefix(random.choice(prefixes))
    return ""


def normalize_rules(raw: Any) -> Optional[dict]:
    """把 `/run` 的 rules 对象规范化；None/空对象 ⇒ None。非法 ⇒ RuleError。

    认 `phone`（整号）/ `phone_prefixes`（前缀现编）/ `dob` / `password`。
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise _fail("`rules` 得是一个对象")
    unknown = set(raw) - {"phone", "phone_prefixes", "dob", "password"}
    if unknown:
        raise _fail("不认识的规则：%s（只认 phone / phone_prefixes / dob / password）"
                    % "、".join(sorted(unknown)))
    out: dict = {}
    exact = raw.get("phone")
    if exact is not None:
        out["phone"] = _phone_exact(exact)
    phones = raw.get("phone_prefixes")
    if phones is not None:
        if not isinstance(phones, list) or any(not isinstance(x, str) for x in phones):
            raise _fail("`phone_prefixes` 得是一串字符串")
        cleaned = []
        for x in phones:
            x = _phone_prefix(x)
            if x not in cleaned:
                cleaned.append(x)
        if cleaned:
            out["phone_prefixes"] = cleaned
    dob = raw.get("dob")
    if dob is not None:
        if not isinstance(dob, dict):
            raise _fail("`dob` 得是对象：{\"random\": true, \"format\": \"DD/MM/YYYY\"}")
        unknown_dob = set(dob) - {"random", "format"}
        if unknown_dob:
            raise _fail("`dob` 里不认识的键：%s" % "、".join(sorted(unknown_dob)))
        if dob.get("random") is not True:
            raise _fail("`dob.random` 必须是 true")
        fmt = dob.get("format")
        if fmt is not None:
            if not isinstance(fmt, str):
                raise _fail("生日格式得是字符串")
            fmt = parse_date_format(fmt) or ""
            if not fmt:
                raise _fail("生日格式认不出：%r（例：DD/MM/YYYY）" % dob.get("format"))
        out["dob"] = {"random": True, **({"format": fmt} if fmt else {})}
    password = raw.get("password")
    if password is not None:
        if password != "strong":
            raise _fail("`password` 只认 \"strong\"")
        out["password"] = "strong"
    return out or None


def parse_date_format(text: Any) -> Optional[str]:
    """从一段页面文字/格式串里提取日期格式；提取不到 ⇒ None。

    只认 D/DD、M/MM、YYYY/YY 三个 token，分隔符 `/`、`-`、`.`。
    这是**格式**，不是**值**：`3/10/1970` 这种数字开头的不算。
    """
    if not isinstance(text, str):
        return None
    m = _DATE_RE.search(text)
    if not m:
        return None
    parts = [p.upper() for p in (m.group(1), m.group(2), m.group(3))]
    if any(p not in _DOB_TOKENS for p in parts):
        return None
    if sum(p in ("D", "DD") for p in parts) != 1:
        return None
    if sum(p in ("M", "MM") for p in parts) != 1:
        return None
    if sum(p in ("YY", "YYYY") for p in parts) != 1:
        return None
    return text[m.start():m.end()].upper()


def _fill_text(fill: dict) -> str:
    target = fill.get("target") if isinstance(fill.get("target"), dict) else {}
    hay = " ".join(str(x or "") for x in (
        fill.get("name"), fill.get("kind"), fill.get("source"), fill.get("label"),
        target.get("label"), target.get("hint"),
    ))
    #: 下划线/连字符当空格：`phone_number` / `birth_date` 要能按词认出。
    return hay.lower().replace("_", " ").replace("-", " ")


def _has_word(text: str, word: str) -> bool:
    return bool(re.search(r"(?:^|[^a-z])" + re.escape(word) + r"(?:[^a-z]|$)", text))


def is_phone_fill(fill: dict) -> bool:
    text = _fill_text(fill)
    #: `PhoneNumber` 是连写；`hotel` 里的 `tel` 不算（用户 2026-10-20 的 Review）。
    return ("phone" in text or "mobile" in text) or _has_word(text, "tel")


def is_dob_fill(fill: dict) -> bool:
    text = _fill_text(fill)
    #: 只认真正的出生日期；`Place of birth` 不是 DOB（Review finding 8）。
    return ("dob" in text or "birthday" in text or "birthdate" in text
            or "date of birth" in text or "birth date" in text)


def is_password_fill(fill: dict) -> bool:
    return "password" in _fill_text(fill)


def _detect_date_format(fill: dict) -> tuple[Optional[str], Optional[str]]:
    target = fill.get("target") if isinstance(fill.get("target"), dict) else {}
    for source, value in (
        ("placeholder", target.get("placeholder")),
        ("label", target.get("label")),
        ("label", fill.get("label")),
        ("hint", target.get("hint")),
    ):
        fmt = parse_date_format(value)
        if fmt:
            return fmt, source
    return None, None


def apply_to_fills(fills: dict, rules: Optional[dict]) -> dict:
    """把规则写成 FILLS 的 `rule`/`prefixes`/`date_format`；不修改入参。"""
    out = {name: dict(fill or {}) for name, fill in (fills or {}).items()}
    result = {"fills": out, "applied": [], "unmatched": [],
              "needs_format": [], "effective": None}
    if not rules:
        return result

    exact = str(rules.get("phone") or "").strip()
    phones = list(rules.get("phone_prefixes") or [])
    if exact or phones:
        hits = [n for n, f in out.items() if is_phone_fill(f)]
        if hits:
            for n in hits:
                if exact:
                    out[n]["rule"] = "phone_exact"
                    out[n]["value"] = exact
                    out[n].pop("prefixes", None)
                else:
                    out[n]["rule"] = "phone_prefix"
                    out[n]["prefixes"] = phones
            if exact:
                result["applied"].append("phone_exact:" + exact)
            else:
                result["applied"].extend("phone:" + p for p in phones)
        else:
            if exact:
                result["unmatched"].append("phone_exact:" + exact)
            result["unmatched"].extend("phone:" + p for p in phones)

    dob = rules.get("dob")
    if dob:
        hits = [n for n, f in out.items() if is_dob_fill(f)]
        if hits:
            resolved = {}
            for n in hits:
                fmt = dob.get("format")
                source = "operator"
                if not fmt:
                    fmt, source = _detect_date_format(out[n])
                if not fmt:
                    result["needs_format"].append(n)
                    continue
                out[n]["rule"] = "dob_random"
                out[n]["date_format"] = fmt
                out[n]["date_format_source"] = source
                resolved.setdefault("format", fmt)
                resolved.setdefault("format_source", source)
            if len(result["needs_format"]) < len(hits):
                result["applied"].append("dob_random")
            if resolved:
                result["effective"] = dict(dob, **resolved)
        else:
            result["unmatched"].append("dob_random")

    password = rules.get("password")
    if password:
        hits = [n for n, f in out.items() if is_password_fill(f)]
        if hits:
            for n in hits:
                out[n]["rule"] = "password_strong"
            result["applied"].append("password_strong")
        else:
            result["unmatched"].append("password_strong")

    effective = dict(rules)
    if result["effective"] and "dob" in effective:
        effective["dob"] = result["effective"]
    effective["applied"] = list(result["applied"])
    effective["unmatched"] = list(result["unmatched"])
    result["effective"] = effective
    return result
