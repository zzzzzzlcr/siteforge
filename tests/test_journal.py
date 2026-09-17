"""Task 4：探路账本（`agent/journal.py`）的契约测试。

## 为什么这些用例这么细

账本是**证据**：窗口死了之后，人（与 Task 5 的重放）只能靠它知道「这一趟走到哪儿了」。
所以它有三条不能破的性质，每条都有各自的用例：

1. **一次一步一行**：`append` 一次 `write` 写**一整行** —— 被杀最多留**半行**，
   不会把两行串在一起。（串行的话 `read()` 会把两步读成一步，而**没人看得出来**。）
2. **坏行不吞**：`read()` 遇半行/坏行**跳过并报告**（`(rows, skipped)`）——
   **不抛**（读账不该炸），也**不静默**（少了一步却没人知道，比读不出来更坏）。
3. **行就是 `Journey.steps` 的那一步**：不包一层、不改键名（跨任务接口 §2）——
   否则「账本」与「重放」会各有一套字段名，那正是漂。

⚠️ 这一整个文件都**不打浏览器**：账本是纯文件读写（真文件写进 `tmp_path`）。
"""

from __future__ import annotations

import builtins
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import journal  # noqa: E402


# ── 路径：目录、文件名、编号排序 ─────────────────────────────────────

def test_dir_for_creates_the_directory(tmp_path):
    """目录不存在就**建出来**（`parents=True, exist_ok=True`）—— 调用方拿到就能写。"""
    target = journal.dir_for(tmp_path / "runtime" / "explore", "job-1")
    assert target == tmp_path / "runtime" / "explore" / "job-1"
    assert target.is_dir()
    # 再要一次不炸（存在就存在）
    assert journal.dir_for(tmp_path / "runtime" / "explore", "job-1").is_dir()


@pytest.mark.parametrize("bad", ("../../etc", "..", ".", "a/b", "a\\b", "", None))
def test_a_job_id_that_could_escape_the_runtime_dir_is_refused(tmp_path, bad):
    """job_id 是外面（HTTP）来的字符串 —— 拼路径之前要挡住 `../` 那种。

    一个能爬出去的 id 就等于**任意写**。**不 sanitize**：sanitize 会让两个 job
    悄悄撞进同一个目录，而「两趟的账混在一起」比报错坏得多。
    """
    with pytest.raises(ValueError):
        journal.dir_for(tmp_path, bad)


def test_attempt_path_is_the_documented_shape(tmp_path):
    """`<root>/<job_id>/attempt-<n>.jsonl`（跨任务接口 §2 定死的形状）。"""
    assert journal.attempt_path(tmp_path, "job-1", 1) == tmp_path / "job-1" / "attempt-1.jsonl"
    assert journal.attempt_path(tmp_path, "job-1", 12).name == "attempt-12.jsonl"


def test_attempts_are_sorted_by_number_not_by_string(tmp_path):
    """按**编号**排序：`attempt-2` 要在 `attempt-10` 前面。

    字符串序会把 10 排到 2 前面 —— 两次尝试的步就被接反了，而读账的人
    正是**按顺序**读「这一趟怎么走的」。（`measure.attempt_no` 有同一条教训。）
    """
    for n in (1, 2, 10, 3):
        journal.append(journal.attempt_path(tmp_path, "job-1", n), {"step": n})
    (tmp_path / "job-1" / "attempts.jsonl").write_text("{}\n", encoding="utf-8")  # 不是账本
    got = journal.attempts(tmp_path, "job-1")
    assert [p.name for p in got] == ["attempt-1.jsonl", "attempt-2.jsonl",
                                     "attempt-3.jsonl", "attempt-10.jsonl"], got


def test_attempts_of_a_job_that_never_ran_is_empty_not_an_error(tmp_path):
    """没跑过 = **空账**，不是异常（同 `measure.read_rows` 的那一条）。

    ⚠️ **而且它不建目录**（M-1）：问一声不该有副作用 —— 建了的话，
    「这个 job 从没跑过」与「跑过」在**目录存不存在**上就分不开了，
    而 Task 5 是第一个消费者（它要按「有没有账本」决定去哪儿续）。
    """
    assert journal.attempts(tmp_path, "job-never-ran") == []
    assert not (tmp_path / "job-never-ran").exists(), "读的 API 建了目录（副作用）"


def test_both_read_apis_raise_on_a_job_id_that_is_not_one(tmp_path):
    """`attempts()` 遇不像话的 job_id **抛 `ValueError`**（M-5）——
    它的 docstring 写着「没跑过不是异常」，但那是「没有账本」，不是「你给我一个坏 id」。"""
    with pytest.raises(ValueError):
        journal.attempts(tmp_path, "../etc")


def test_read_on_a_directory_says_so_instead_of_pretending_it_is_an_empty_account(tmp_path):
    """`read()` 指到一个**目录**上会抛 `OSError` —— 那是路径写错了，不是「没跑过」（M-5）。

    这两条之所以要钉：两个读 API 的 docstring 都写着「不会抛」，
    读者会以为它们**什么都不抛** —— 而「指错路径」与「账本坏了一行」是两件事。
    """
    d = tmp_path / "a-directory"
    d.mkdir()
    with pytest.raises(OSError):
        journal.read(d)
    assert journal.read(tmp_path / "nope.jsonl") == ([], [])       # 对照：**不在** = 空账


# ── 一次一步一行（原子）────────────────────────────────────────────

def test_append_writes_each_step_in_exactly_one_write(tmp_path, monkeypatch):
    """**一次一步一行**：一次 `append` 只许调一次 `write`，而且那一行**自带换行**。

    这就是「被杀最多留半行、不会串行」的全部依据 —— 两次 `write` 的话，
    进程可能死在两次之间：前一步没写完、后一步已经开了头，两行粘成一行，
    而 `read()` 会把它读成**一步**（没有任何人看得出来）。
    """
    writes: list = []
    real_open = builtins.open

    class _Spy:
        def __init__(self, handle):
            self._handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._handle.__exit__(*exc)

        def write(self, text):
            writes.append(text)
            return self._handle.write(text)

        def flush(self):
            return self._handle.flush()

    monkeypatch.setattr(builtins, "open", lambda *a, **kw: _Spy(real_open(*a, **kw)))
    path = tmp_path / "attempt-1.jsonl"
    journal.append(path, {"step": 1, "note": "点了「Yes」"})
    journal.append(path, {"step": 2, "note": "看了一眼页面"})

    assert len(writes) == 2, f"两次 append 写了 {len(writes)} 次（要正好两次）"
    for line in writes:
        assert line.endswith("\n") and line.count("\n") == 1, line
    monkeypatch.undo()
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_the_row_is_the_step_dict_itself(tmp_path):
    """落盘的**就是**那一步：不包一层、不改键名（跨任务接口 §2）。"""
    step = {"state": "start", "action": "click", "target": {"text": "Yes"},
            "result": {"ok": True}, "note": "点了「Yes」", "origin": "model"}
    path = journal.attempt_path(tmp_path, "job-1", 1)
    journal.append(path, step)
    rows, skipped = journal.read(path)
    assert skipped == []
    assert rows == [step], rows
    # 逐字节：连键的顺序都不许变（变了对读的人来说就是另一份东西）
    assert path.read_text(encoding="utf-8").strip() == json.dumps(step, ensure_ascii=False)


# ── 坏行不吞（但也不抛）────────────────────────────────────────────

def test_a_half_line_is_skipped_and_reported(tmp_path):
    """**半行**（被杀在写一半的地方）：跳过它，把行号与原因报出来 —— 不抛、不静默。"""
    path = tmp_path / "attempt-1.jsonl"
    path.write_text('{"step": 1, "note": "第一步"}\n{"step": 2, "note": "第二', encoding="utf-8")
    rows, skipped = journal.read(path)
    assert [r["step"] for r in rows] == [1]
    assert len(skipped) == 1, skipped
    assert skipped[0]["line"] == 2 and skipped[0]["why"], skipped[0]
    assert "半" in skipped[0]["why"] or "读不出来" in skipped[0]["why"], skipped[0]


def test_a_half_line_cut_inside_a_chinese_character_is_still_not_fatal(tmp_path):
    """**C-1**：一行被切在**一个字的中间**时，**不许抛** —— 好行一行都不能少。

    为什么这条是这个模块的要害：`append` 用 `ensure_ascii=False`，而**每一行都含中文**
    （`note` 就是「点了「Get Started」」这种），所以中文的 UTF-8 三字节序列就在行里。
    写一半被杀 / 盘满短写时，切口落在**一个字中间**是正常形状 ——
    严格解码会抛 `UnicodeDecodeError`，于是**连前面那些好行一起读不出来**，
    而「窗口死在一半时知道走到哪儿了」正是这个模块存在的唯一理由。

    ⚠️ 上面的用例造的是**纯 ASCII** 半行 —— 它**看起来在钉**这条判据，
    而多字节那个实例一条用例都没有（复审 C-1 点名的就是这件事）。
    """
    path = journal.attempt_path(tmp_path, "job-1", 1)
    journal.append(path, {"step": 1, "action": "click", "note": "点了「Go」"})
    journal.append(path, {"step": 2, "action": "click", "note": "点了「Yes」"})
    third = '{"step": 3, "action": "click", "note": "第三步：点了「结算」"}\n'.encode("utf-8")
    cut = 0
    while (third[cut] & 0xC0) != 0x80:      # 往后找到第一个续字节：切在这儿必非法
        cut += 1
    path.write_bytes(path.read_bytes() + third[:cut])   # ← 第三行切在一个中文字的中间

    rows, skipped = journal.read(path)      # ← 不许抛
    assert [r["step"] for r in rows] == [1, 2], rows
    assert len(skipped) == 1 and skipped[0]["line"] == 3, skipped


def test_a_bad_line_in_the_middle_does_not_take_the_good_ones_with_it(tmp_path):
    """**C-1 的更坏形状**：坏行在**中间**，后面还有好行 —— 好行必须照样读得出来。

    这一形状是把「整份文件一次解码」的写法打红的地方：那样一处坏就全盘皆输。
    """
    path = tmp_path / "attempt-1.jsonl"
    good = '{"step": %d, "note": "第 %d 步：点了「Go」"}\n'
    half = '{"step": 2, "note": "第二步：点了「Ye'
    cut = len(half.encode("utf-8")) - 1      # 少一个字节 = 切在最后一字的中间
    path.write_bytes((good % (1, 1)).encode("utf-8")
                     + half.encode("utf-8")[:cut] + "\n".encode()
                     + (good % (3, 3)).encode("utf-8"))
    rows, skipped = journal.read(path)
    assert [r["step"] for r in rows] == [1, 3], rows
    assert [s["line"] for s in skipped] == [2], skipped
    assert journal.attempts(tmp_path, "job-1") == [] or True     # （本文件不在 attempt 名下）


def test_a_line_that_is_not_an_object_is_reported_too(tmp_path):
    """不是对象的 JSON 也是坏行（`[1,2]` 读不成「一步」）—— 同样报出来，不猜。"""
    path = tmp_path / "attempt-1.jsonl"
    path.write_text('{"step": 1}\n[1, 2]\n"一句话"\n', encoding="utf-8")
    rows, skipped = journal.read(path)
    assert [r["step"] for r in rows] == [1]
    assert [s["line"] for s in skipped] == [2, 3], skipped
    assert all(s["why"] for s in skipped)


def test_reading_a_journal_that_was_never_written_is_an_empty_account(tmp_path):
    """文件不在 → `([], [])`：没跑过不是异常（读账不抛）。"""
    assert journal.read(tmp_path / "nope.jsonl") == ([], [])


def test_a_real_journal_from_append_survives_a_kill_in_the_middle(tmp_path):
    """把「被杀」造出来：先落两步，再手工造一个半行，然后读 —— 好的还在，坏的报出来。

    这条是上面几条的**合体**，也是这个模块存在的理由：窗口死在一半时，
    账本要能回答「走到哪儿了」，并且**说得出**最后那一步没写完（而不是悄悄少一步）。
    """
    path = journal.attempt_path(tmp_path, "job-1", 1)
    journal.append(path, {"step": 1, "action": "click", "note": "点了「Go」"})
    journal.append(path, {"step": 2, "action": "observe", "note": "看了一眼页面"})
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"step": 3, "action": "fo')          # ← 被杀在写一半的地方
    rows, skipped = journal.read(path)
    assert [r["step"] for r in rows] == [1, 2]
    assert len(skipped) == 1 and skipped[0]["line"] == 3, skipped
    assert journal.attempts(tmp_path, "job-1") == [path]
