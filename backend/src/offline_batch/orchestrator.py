"""批量重算编排：发现 → 校验 → 去重/拒续 → 复用算法重算 → 落盘 → 汇总门控。

关键规则：
1. 只使用冻结包内端元、样本与噪声参数，复用 services.analysis_service.analyze_unmix，
   不读取现行库（无数据库、无 Redis、无任何环境变量依赖）。
2. 同批重复 research_id 只算一次，后续重复包记 duplicate 并指向保留条目。
3. 坏包（结构/参数不合法、算法拒绝）隔离到 quarantine/，清单注明原因码。
4. 中断后重入：completed 条目的配比文件绝不改写；running 条目视为中断，重新计算并
   原子替换；同一 research_id 内容指纹变化一律 rejected，拒绝续跑。
5. 未完成批次不发布 summary；summary 仅在全量到达终态时写出。

台账以“输入目录内相对路径”为条目主键：每份输入文件在台账中恰好对应一条，
扫描时按路径排序，保证批次与去重结论稳定可复现。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..services.analysis_service import analyze_unmix
from .errors import BatchError, PackageError
from .package_io import discover_package_files, load_package
from .state import (
    LEDGER_NAME,
    PUBLISHABLE_STATES,
    ST_COMPLETED,
    ST_DUPLICATE,
    ST_FAILED,
    ST_INVALID,
    ST_MISSING,
    ST_PENDING,
    ST_REJECTED,
    ST_RUNNING,
    DirLock,
    Ledger,
)

SUMMARY_NAME = "batch-summary.json"
_TS = lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


@dataclass
class BatchPaths:
    input_dir: Path
    state_dir: Path
    results_dir: Path
    quarantine_dir: Path

    @classmethod
    def create(cls, input_dir: Path, state_dir: Path) -> "BatchPaths":
        for sub in ("results", "quarantine"):
            (state_dir / sub).mkdir(parents=True, exist_ok=True)
        return cls(
            input_dir=input_dir,
            state_dir=state_dir,
            results_dir=state_dir / "results",
            quarantine_dir=state_dir / "quarantine",
        )

    def result_path(self, research_id: str) -> Path:
        return self.results_dir / f"{research_id}.json"

    def quarantine_copy(self, source: Path, research_id: str | None) -> Path:
        safe_id = (research_id or "unidentified").replace(os.sep, "_")
        target = self.quarantine_dir / f"{safe_id}__{source.name}"
        # 同一来源重复隔离时保留副本，不覆盖既有证据
        if target.exists():
            stem, suffix = target.stem, target.suffix
            target = self.quarantine_dir / f"{stem}.{int(time.time())}{suffix}"
        return target


@dataclass
class BatchCounters:
    input_total: int = 0
    unique_research: int = 0  # 非重复且能识别研究编号的条目
    duplicated: int = 0
    unidentified: int = 0  # 结构损坏到无法识别研究编号的坏包
    completed: int = 0
    failed: int = 0  # 可识别条目的 invalid/rejected/failed 合计
    by_state: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, int]:
        return {
            "input_total": self.input_total,
            "unique_research": self.unique_research,
            "duplicated": self.duplicated,
            "unidentified": self.unidentified,
            "completed": self.completed,
            "failed": self.failed,
            "by_state": dict(sorted(self.by_state.items())),
        }


def _quarantine(paths: BatchPaths, source: Path, research_id: str | None) -> str:
    target = paths.quarantine_copy(source, research_id)
    shutil.copy2(source, target)
    return str(target.relative_to(paths.state_dir))


def _tally(ledger: Ledger, active_keys: set[str]) -> BatchCounters:
    counters = BatchCounters(input_total=len(active_keys), by_state={})
    kept: set[str] = set()  # 可识别的非重复来源条目（含被拒的异源同编号条目）
    for key in active_keys:
        entry = ledger.entries().get(key)
        if entry is None:
            continue
        state = entry["state"]
        counters.by_state[state] = counters.by_state.get(state, 0) + 1
        rid = entry.get("research_id")
        if state == ST_DUPLICATE:
            counters.duplicated += 1
            continue
        if rid is None:
            # 结构损坏到无法识别编号的坏包：单独计数，不混入唯一研究
            counters.unidentified += 1
            continue
        # 以“来源路径+研究编号”为唯一条目键：异源同编号（被拒）各自占一个唯一名额
        kept.add(f"{rid}\u0000{entry['source']}")
        if state == ST_COMPLETED:
            counters.completed += 1
        if state in (ST_INVALID, ST_REJECTED, ST_FAILED):
            counters.failed += 1
    counters.unique_research = len(kept)
    return counters


def _is_finished(ledger: Ledger, active_keys: set[str]) -> bool:
    # 所有在扫条目都到达“可发布”状态（含 failed：整批处理完毕，允许带失败发布汇总）
    return all(
        ledger.entries()[key]["state"] in PUBLISHABLE_STATES
        for key in active_keys
        if key in ledger.entries()
    )


def _write_summary_if_finished(
    paths: BatchPaths, ledger: Ledger, active_keys: set[str]
) -> bool:
    summary_path = paths.state_dir / SUMMARY_NAME
    if not _is_finished(ledger, active_keys):
        # 未完成批次不发布汇总：移除可能过期的旧汇总，避免被误读为本次结论
        if summary_path.exists():
            summary_path.unlink()
        return False
    counters = _tally(ledger, active_keys)
    active_entries = [ledger.entries()[key] for key in sorted(active_keys)]
    # 数量对齐：
    #   输入 = 唯一研究（含失败）+ 重复 + 无法识别编号的坏包
    #   唯一研究 = 成功 + 可识别失败（invalid/rejected/failed）
    assert len(active_entries) == counters.input_total
    assert (
        counters.input_total
        == counters.unique_research + counters.duplicated + counters.unidentified
    )
    assert counters.unique_research == counters.completed + counters.failed
    missing = [
        {"source": e["source"], "reason": e.get("reason", "")}
        for e in ledger.entries().values()
        if e["state"] == ST_MISSING
    ]
    summary = {
        "status": "completed",
        "published_at": _TS(),
        "input_dir": str(paths.input_dir.resolve()),
        "counts": counters.as_dict(),
        "reconciliation": {
            "input_equals_unique_plus_duplicate_plus_unidentified": True,
            "unique_equals_completed_plus_failed": True,
        },
        "missing_sources": missing,
        "entries": [
            {
                "research_id": e.get("research_id"),
                "state": e["state"],
                "source": e["source"],
                "source_sha256": e.get("source_sha256"),
                "content_fingerprint": e.get("content_fingerprint"),
                "result": e.get("result") if e["state"] == ST_COMPLETED else None,
                "reason_code": e.get("reason_code"),
                "reason": e.get("reason"),
                "quarantine": e.get("quarantine"),
                "duplicate_of": e.get("duplicate_of"),
                "attempts": e.get("attempts", 1),
            }
            for e in sorted(active_entries, key=lambda x: (str(x.get("research_id")), x["source"]))
        ],
    }
    _atomic_write_json(summary_path, summary)
    return True


def run_batch(
    input_dir: Path,
    state_dir: Path,
    *,
    format_version: str,
    algorithm_version: str,
) -> dict[str, Any]:
    """执行（或恢复）一个批次，返回本次调用的统计与发布状态。全程持锁，禁止并发。"""
    if not input_dir.is_dir():
        raise BatchError(f"输入目录不存在或不是目录：{input_dir}")
    state_dir.mkdir(parents=True, exist_ok=True)
    lock = DirLock(state_dir)
    lock.acquire()
    try:
        return _run_locked(input_dir, state_dir, lock, format_version, algorithm_version)
    finally:
        lock.release()


def _run_locked(input_dir, state_dir, lock, format_version, algorithm_version) -> dict[str, Any]:
    paths = BatchPaths.create(input_dir, state_dir)
    files = discover_package_files(input_dir)
    ledger = Ledger(state_dir)
    ledger.set_batch_meta(
        input_dir=str(input_dir.resolve()),
        state_dir=str(state_dir.resolve()),
        last_scan_at=_TS(),
        input_file_count=len(files),
    )

    active_keys: set[str] = set()
    # research_id -> 保留条目来源（同编号首个、且输入一致的包）；先从旧台账恢复映射。
    # 可作为归属锚点的状态：计算类状态与 rejected（rejected 的同指纹副本仍记 duplicate，
    # 处置跟随主条目）；duplicate/invalid/missing 不作为锚点。
    _KEEP_STATES = {ST_COMPLETED, ST_RUNNING, ST_PENDING, ST_FAILED, ST_REJECTED}
    first_kept: dict[str, str] = {}
    for key, entry in ledger.entries().items():
        rid = entry.get("research_id")
        if rid and entry["state"] in _KEEP_STATES and rid not in first_kept:
            first_kept[rid] = entry["source"]

    for source in files:
        rel = source.relative_to(input_dir).as_posix()
        active_keys.add(rel)
        existing = ledger.get(rel)

        # 第一遍：读取与结构校验，坏包立即隔离（无法可信识别研究编号，一律清空）
        try:
            pkg = load_package(source, format_version, algorithm_version)
        except PackageError as exc:
            ledger.upsert(
                rel,
                {
                    "research_id": None,
                    "state": ST_INVALID,
                    "source": rel,
                    "source_sha256": None,
                    "reason_code": exc.code,
                    "reason": exc.message,
                    "quarantine": _quarantine(paths, source, None),
                    "updated_at": _TS(),
                },
            )
            continue

        # 已拒绝的条目保持终态，不再重算
        if existing and existing["state"] == ST_REJECTED:
            continue

        kept_source = first_kept.get(pkg.research_id)

        # 规则：同批重复研究只算一次（先于同路径变更判定，避免误伤同内容副本）
        if kept_source and kept_source != rel:
            kept_entry = ledger.get(kept_source)
            # 同内容：与保留条目当前指纹相同，或与其变更前指纹相同（主条目已被拒、
            # 但该副本仍是旧输入的同内容拷贝）。
            anchor_fingerprints = {kept_entry.get("content_fingerprint")} if kept_entry else set()
            if kept_entry and kept_entry.get("previous_fingerprint"):
                anchor_fingerprints.add(kept_entry["previous_fingerprint"])
            same_fingerprint = pkg.content_fingerprint in anchor_fingerprints
            if same_fingerprint:
                # 同内容副本：无论保留条目当前是 completed 还是 rejected，都保持 duplicate，
                # 其处置跟随主条目，不单独产生新的成功/失败，保证 输入=唯一+重复+无法识别 恒等
                ledger.upsert(
                    rel,
                    {
                        "research_id": pkg.research_id,
                        "state": ST_DUPLICATE,
                        "source": rel,
                        "source_sha256": pkg.source_sha256,
                        "content_fingerprint": pkg.content_fingerprint,
                        "duplicate_of": kept_source,
                        "updated_at": _TS(),
                    },
                )
                continue
            # 同编号但输入不同：不是重复，是换输入，拒绝并隔离
            ledger.upsert(
                rel,
                {
                    "research_id": pkg.research_id,
                    "state": ST_REJECTED,
                    "source": rel,
                    "source_sha256": pkg.source_sha256,
                    "content_fingerprint": pkg.content_fingerprint,
                    "reason_code": "INPUT_CHANGED",
                    "reason": (
                        f"研究 {pkg.research_id} 与已登记来源 {kept_source} 输入指纹不一致，"
                        "拒绝混入同一批次"
                    ),
                    "quarantine": _quarantine(paths, source, pkg.research_id),
                    "updated_at": _TS(),
                },
            )
            # 冻结同编号已完成条目：不再发布其配比，避免新旧混批
            if kept_entry and kept_entry["state"] == ST_COMPLETED:
                ledger.upsert(
                    kept_source,
                    {
                        **kept_entry,
                        "state": ST_REJECTED,
                        "frozen": True,
                        "result": None,
                        "reason_code": "SIBLING_INPUT_CHANGED",
                        "reason": f"同编号研究 {pkg.research_id} 出现变更输入，已完成配比冻结、不再发布",
                        "updated_at": _TS(),
                    },
                )
            continue

        # 规则：同一研究编号换了输入（同路径内容变更）→ 拒绝续跑。
        # 仅针对已经产出/正在产出配比的状态（completed/running/pending）：
        # failed/invalid 从未产出过配比，指纹变化视为“修正后重算”，在下方处理。
        _PRODUCING_STATES = {ST_COMPLETED, ST_RUNNING, ST_PENDING}
        if (
            existing
            and existing.get("content_fingerprint")
            and existing["state"] in _PRODUCING_STATES
            and existing["content_fingerprint"] != pkg.content_fingerprint
        ):
            ledger.upsert(
                rel,
                {
                    **existing,
                    "state": ST_REJECTED,
                    "source_sha256": pkg.source_sha256,
                    "previous_fingerprint": existing["content_fingerprint"],
                    "content_fingerprint": pkg.content_fingerprint,
                    "reason_code": "INPUT_CHANGED",
                    "reason": (
                        f"研究 {pkg.research_id} 的输入指纹与已登记批次不一致，"
                        "拒绝在同一批次续跑，避免新旧配比混批"
                    ),
                    "quarantine": _quarantine(paths, source, pkg.research_id),
                    "updated_at": _TS(),
                },
            )
            continue

        # 恢复续算：已完成且结果文件仍在 → 原样保留，绝不改写
        if existing and existing["state"] == ST_COMPLETED and paths.result_path(pkg.research_id).is_file():
            first_kept.setdefault(pkg.research_id, rel)
            continue
        # failed 条目：仅当来源已被修正（指纹变化）才重算；未修正则保持隔离，不空转
        if existing and existing["state"] == ST_FAILED:
            if existing.get("content_fingerprint") == pkg.content_fingerprint:
                first_kept.setdefault(pkg.research_id, rel)
                continue
        first_kept[pkg.research_id] = rel

        attempts = (existing or {}).get("attempts", 0) + 1
        ledger.upsert(
            rel,
            {
                "research_id": pkg.research_id,
                "state": ST_RUNNING,
                "source": rel,
                "source_sha256": pkg.source_sha256,
                "content_fingerprint": pkg.content_fingerprint,
                "attempts": attempts,
                "started_at": _TS(),
                "updated_at": _TS(),
            },
        )

        try:
            result = analyze_unmix(pkg.snapshot, pkg.noise_sigma, pkg.repeats, pkg.seed)
        except ValueError as exc:
            # 算法层拒绝（病态矩阵、参数越界、KKT 失败等）：坏包隔离
            ledger.upsert(
                rel,
                {
                    "research_id": pkg.research_id,
                    "state": ST_FAILED,
                    "source": rel,
                    "source_sha256": pkg.source_sha256,
                    "content_fingerprint": pkg.content_fingerprint,
                    "attempts": attempts,
                    "reason_code": "COMPUTATION_REJECTED",
                    "reason": str(exc),
                    "quarantine": _quarantine(paths, source, pkg.research_id),
                    "updated_at": _TS(),
                },
            )
            continue
        except Exception as exc:  # 数值/系统类意外：隔离并保留原因，不拖垮整批
            ledger.upsert(
                rel,
                {
                    "research_id": pkg.research_id,
                    "state": ST_FAILED,
                    "source": rel,
                    "source_sha256": pkg.source_sha256,
                    "content_fingerprint": pkg.content_fingerprint,
                    "attempts": attempts,
                    "reason_code": "COMPUTATION_ERROR",
                    "reason": f"{type(exc).__name__}: {exc}",
                    "quarantine": _quarantine(paths, source, pkg.research_id),
                    "updated_at": _TS(),
                },
            )
            continue

        # 结果原子落盘；中断只会留下旧文件或新文件，不会出现半截结果
        _atomic_write_json(
            paths.result_path(pkg.research_id),
            {
                "research_id": pkg.research_id,
                "source": rel,
                "source_sha256": pkg.source_sha256,
                "content_fingerprint": pkg.content_fingerprint,
                "input_hash": result["input_hash"],
                "computed_at": _TS(),
                "result": result,
            },
        )
        ledger.upsert(
            rel,
            {
                "research_id": pkg.research_id,
                "state": ST_COMPLETED,
                "source": rel,
                "source_sha256": pkg.source_sha256,
                "content_fingerprint": pkg.content_fingerprint,
                "attempts": attempts,
                "result": str(paths.result_path(pkg.research_id).relative_to(state_dir)),
                "input_hash": result["input_hash"],
                "updated_at": _TS(),
            },
        )

    # 台账中有、本轮输入已消失的来源：标记 missing，不静默丢弃（不计入本次输入数量）
    for stale_key, entry in list(ledger.entries().items()):
        if stale_key in active_keys or entry["state"] == ST_MISSING:
            continue
        ledger.upsert(
            stale_key,
            {
                **entry,
                "state": ST_MISSING,
                "reason_code": "SOURCE_MISSING",
                "reason": "来源文件在恢复扫描时已不在输入目录",
                "updated_at": _TS(),
            },
        )

    published = _write_summary_if_finished(paths, ledger, active_keys)
    counters = _tally(ledger, active_keys)
    return {
        "input_total": counters.input_total,
        "counts": counters.as_dict(),
        "finished": published,
        "summary": str(paths.state_dir / SUMMARY_NAME) if published else None,
        "ledger": str(paths.state_dir / LEDGER_NAME),
    }
