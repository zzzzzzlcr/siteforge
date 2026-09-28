"""窗口尺寸对齐生产那套 + 指纹（`agent/service.py` 的 `parse_viewport` / `_align_window_size`）。

**为什么要有这两格**（用户 2026-09-28 报的那件事）：这个站的页头**按视口/UA 换版式** ——
222 那个档案是 iPhone 18.1.1 / 1000×1000，生产 auto-farm 那个是 iPhone 18.7 / **393×852**；
在 1280 宽下汉堡与 BOOK NOW 是 0×0（cdp 如实拒点），窄到 500 时页头那个 Join Now 又藏起来了。
⇒ 账本里那串选择器**只在录它的那个尺寸下成立**，而账本里原本一个字都没记这件事，
于是每一单都要人去后台再修一遍。

所以：探路/自测**开窗前先把窗口尺寸对齐生产那套**（这一格），指纹再由产物自己核（`test_template`）。
这两条钉子盯的是「**对准了没有**」与「**读不出来时不许当成对准了**」。
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import service  # noqa: E402


class _Window:
    """窗口层的替身：只认服务真会用的那三样（`configured_size` / `set_viewport` / `alive`）。"""

    def __init__(self, size=(1000, 1000), fail=False):
        self.size = size
        self.fail = fail
        self.calls = []

    def configured_size(self):
        return self.size

    def set_viewport(self, width, height):
        if self.fail:
            raise RuntimeError("Bit 说不行")
        self.calls.append((int(width), int(height)))
        self.size = (int(width), int(height))

    def alive(self):
        return True


class _Svc:
    """只带 `_window` 的服务替身 —— `_align_window_size` 只碰这一格。"""

    def __init__(self, window):
        self._window = window


# ── 旋钮怎么读 ───────────────────────────────────────────────────────────────

def test_a_viewport_string_is_read_as_two_numbers():
    assert service.parse_viewport("393x852") == (393, 852)
    assert service.parse_viewport("393×852") == (393, 852)      # 全角 ×
    assert service.parse_viewport(" 393 X 852 ") == (393, 852)  # 大小写与空白都收


def test_no_value_means_the_production_size_and_dash_means_hands_off():
    """不给 = 默认生产那套（393×852）；`-` = **一个字节都不动**（老行为，留着好回退）。"""
    assert service.parse_viewport("") == service.DEFAULT_PROD_VIEWPORT
    assert service.parse_viewport(None) == service.DEFAULT_PROD_VIEWPORT
    assert service.parse_viewport("-") is None


@pytest.mark.parametrize("bad", ["393", "a x b", "393x0", "0x852"])
def test_a_broken_knob_raises_instead_of_falling_back(bad):
    """读不出来**抛** —— 退回默认值的话，下一趟探路会在**错的尺寸**下录一遍，
    而那种错当时看不出来（产物照旧能自测，只是到生产点不动）。"""
    with pytest.raises(ValueError):
        service.parse_viewport(bad)


# ── 对齐那一下 ───────────────────────────────────────────────────────────────

def test_the_window_is_aligned_to_the_production_size(monkeypatch):
    window = _Window(size=(1000, 1000))
    monkeypatch.setenv(service.PROD_VIEWPORT_ENV, "393x852")
    service.Service._align_window_size(_Svc(window))
    assert window.calls == [(393, 852)]


def test_an_already_matching_window_is_left_alone(monkeypatch):
    window = _Window(size=(393, 852))
    monkeypatch.setenv(service.PROD_VIEWPORT_ENV, "393x852")
    service.Service._align_window_size(_Svc(window))
    assert window.calls == []


def test_dash_never_touches_the_profile(monkeypatch):
    window = _Window(size=(1280, 900))
    monkeypatch.setenv(service.PROD_VIEWPORT_ENV, "-")
    service.Service._align_window_size(_Svc(window))
    assert window.calls == []


def test_a_layer_that_cannot_do_it_is_not_a_crash(monkeypatch, capsys):
    """这个部署没接窗口层 / 写被拒 ⇒ **照跑**，但要把原因说出来（不是静默跳过）。"""
    monkeypatch.setenv(service.PROD_VIEWPORT_ENV, "393x852")
    service.Service._align_window_size(_Svc(None))                    # 没接窗口层
    service.Service._align_window_size(_Svc(_Window(fail=True)))      # 写被拒
    said = capsys.readouterr().out
    assert "对齐" in said and "没成" in said, said


def test_a_broken_knob_does_not_stop_the_run(monkeypatch, capsys):
    """旋钮写错了 ⇒ 说出来、**不动窗口**、照跑（一次探路不该因为一个环境变量就死）。"""
    window = _Window(size=(1000, 1000))
    monkeypatch.setenv(service.PROD_VIEWPORT_ENV, "393")
    service.Service._align_window_size(_Svc(window))
    assert window.calls == []
    assert "读不出来" in capsys.readouterr().out


def test_an_unreadable_size_is_not_a_match(monkeypatch, capsys):
    """**读不出配置尺寸 ≠ 已经对准**：读不出来时不许当它是对的（那会把「没量到」写成「没问题」）。"""
    window = _Window(size=(1000, 1000))
    window.configured_size = lambda: None
    monkeypatch.setenv(service.PROD_VIEWPORT_ENV, "393x852")
    service.Service._align_window_size(_Svc(window))
    assert window.calls == [(393, 852)]
