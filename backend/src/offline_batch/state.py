"""批处理状态存储：清单台账、原子写入、单实例文件锁。

台账 batch-ledger.json 是恢复续算的唯一事实来源：
- 已完成的研究只登记一次，绝不改写既有配比结果；
- 同一 research_id 的内容指纹变化时标记 rejected，拒绝续跑；
- 写入一律先临时文件后 os.replace，崩溃只可能留下旧台账或新台账，不会出现半截 JSON。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .errors import BatchError

LEDGER_NAME = "batch-ledger.json"
LOCK_NAME = "batch.lock"

# 条目生命周期
ST_PENDING = "pending"
ST_RUNNING = "running"
ST_COMPLETED = "completed"
ST_FAILED = "failed"
ST_INVALID = "invalid"
ST_REJECTED = "rejected"
ST_DUPLICATE = "duplicate"
# 恢复时发现源文件已不在输入目录
ST_MISSING = "missing"

# 终态语义：
# - invalid/rejected/duplicate/missing：本批次内不再变动（rejected 必须另开批次，
#   避免新旧混批；invalid/duplicate 无计算必要；missing 来源已消失）。
# - failed 是“隔离后的失败”，但文件被修正后允许在同状态目录重算，因此不放进终态集，
#   汇总门控仍把它视作可发布的收尾状态（批次允许带失败发布，见 orchestrator 计数）。
TERMINAL_STATES = {ST_COMPLETED, ST_INVALID, ST_REJECTED, ST_DUPLICATE, ST_MISSING}
# 门控专用：failed 也允许发布汇总（整批已处理完，只是有失败条目）
PUBLISHABLE_STATES = TERMINAL_STATES | {ST_FAILED}


class Ledger:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.path = state_dir / LEDGER_NAME
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise BatchError(f"台账损坏，无法安全恢复：{exc}（请人工核对后处理 {self.path}）") from exc
            if not isinstance(data, dict) or "entries" not in data or "batch" not in data:
                raise BatchError(f"台账结构不完整，拒绝在不明状态上续算：{self.path}")
            return data
        return {"batch": {}, "entries": {}}

    def save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".ledger-", suffix=".tmp", dir=self.state_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def get(self, key: str) -> dict[str, Any] | None:
        return self.data["entries"].get(key)

    def upsert(self, key: str, entry: dict[str, Any]) -> None:
        self.data["entries"][key] = entry
        self.save()

    def entries(self) -> dict[str, Any]:
        return self.data["entries"]

    def set_batch_meta(self, **fields: Any) -> None:
        self.data["batch"].update(fields)
        self.save()


class DirLock:
    """fcntl 排他锁：同一状态目录禁止两个批处理并发，避免台账互相覆盖。"""

    def __init__(self, state_dir: Path):
        state_dir.mkdir(parents=True, exist_ok=True)
        self._path = state_dir / LOCK_NAME
        self._fh = None

    def acquire(self) -> None:
        import fcntl

        self._fh = self._path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._fh.close()
            self._fh = None
            raise BatchError(f"状态目录已有批处理在运行（锁 {self._path}），禁止并发续算") from exc
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(f"pid={os.getpid()}\n")
        self._fh.flush()

    def release(self) -> None:
        import fcntl

        if self._fh is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None
