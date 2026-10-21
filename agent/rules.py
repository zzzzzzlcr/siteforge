"""生成期规则：手机号前缀（如 04 / 0207）、生日随机、密码强随机。

这一层只做**纯函数**：校验载荷、解析日期格式、把规则写进 FILLS。
运行时的生成器在 template.py / template_loop.py 里（产物必须自包含，不能 import 本仓）。
"""
from __future__ import annotations

import re
from typing import Any, Optional

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
    """AU 手机号前缀：0 开头、2-10 位数字；运行时补随机数字到 10 位。"""
    text = str(x or "").strip()
    if (not text.isdigit() or not text.startswith("0")
            or not (PHONE_PREFIX_MIN <= len(text) <= PHONE_PREFIX_MAX)):
        raise _fail("手机号前缀得是 0 开头的 2-10 位数字（例 04 / 0207）；给的是 %r" % text)
    return text


def normalize_rules(raw: Any) -> Optional[dict]:
    """把 `/run` 的 rules 对象规范化；None/空对象 ⇒ None。非法 ⇒ RuleError。"""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise _fail("`rules` 得是一个对象")
    unknown = set(raw) - {"phone_prefixes", "dob", "password"}
    if unknown:
        raise _fail("不认识的规则：%s（只认 phone_prefixes / dob / password）"
                    % "、".join(sorted(unknown)))
    out: dict = {}
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

    phones = list(rules.get("phone_prefixes") or [])
    if phones:
        hits = [n for n, f in out.items() if is_phone_fill(f)]
        if hits:
            for n in hits:
                out[n]["rule"] = "phone_prefix"
                out[n]["prefixes"] = phones
            result["applied"].extend("phone:" + p for p in phones)
        else:
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
