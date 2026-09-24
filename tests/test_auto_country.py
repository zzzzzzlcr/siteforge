"""T3：开修之前要把「那一单的国家」核成**代理链上的码** —— 核不到就不猜、不提交、报人开。

判据都在这儿：中文名 → 码（`agent/auto.SAY_CODE` / `code_for`），
并且**钉住它跟后端那张 `fmr.COUNTRY_SAY` 不许对不上**（两张表分头维护迟早打架）。
"""
import pytest

from agent import auto, fmr


def test_the_two_we_know_resolve():
    assert auto.code_for("美国") == "US"
    #: ⚠️ 「英国」写成 GB **不是** UK：代理链与文档里那个是 GB，UK 只是别名（见 auto.py 里的注释）
    assert auto.code_for("英国") == "GB"
    assert auto.code_for("  美国  ") == "US"        # 那一屏上的字前后带空格也得认


@pytest.mark.parametrize("unknown", ["", "   ", None, "澳大利亚", "AU", "us", "没记", "核不到", "US "])
def test_anything_else_is_None_never_a_guess(unknown):
    """表外 / 空 / 大小写不对 / 直接塞码 —— 一律 None（调用方按「报人」处理，**不许猜**）。"""
    assert auto.code_for(unknown) is None


def test_our_table_never_contradicts_the_backend_table():
    """我们那张是**反查**表：每一对都要在后端 `fmr.COUNTRY_SAY` 里有出处。

    ⚠️ 反过来的「后端有、我们没有」**允许**（后端表是全的，我们只用有把握的那几个）；
    但我们表里**不许出现后端表没有的** —— 那就成了我们自己编的。
    """
    assert auto.SAY_CODE, "表空了？"
    for say, code in auto.SAY_CODE.items():
        assert fmr.COUNTRY_SAY.get(code) == say, (say, code, fmr.COUNTRY_SAY.get(code))
