#!/usr/bin/env python3
"""离线批量重算入口（冻结研究包 → 配比结果 + 批次清单）。

用法（在 backend 目录之外也可运行；建议用 -I 隔离模式，防止输入包目录内的
同名模块劫持标准库）：

    python3 -I scripts/offline_batch.py run /data/frozen-pkgs --state-dir /data/batch-state
    python3 -I scripts/offline_batch.py status --state-dir /data/batch-state

特性：
- 仅读取冻结包内端元、样本与噪声参数，复用 src.services 同一套解混算法，不连现行库；
- 同批重复研究只算一次；坏包隔离到 state/quarantine 并在清单注明原因码；
- 中断后再次执行即续算，已完成配比绝不改写；
- 同一研究编号输入指纹变化时拒绝续跑（rejected），避免新旧配比混批；
- 全部条目到达终态才发布 batch-summary.json，且数量对齐输入/重复/成功/失败。

退出码：0 全部成功；3 批次完成但含失败/拒绝；2 批次未完成（仍有非终态条目）；
4 批次流程错误（锁冲突、目录错误等）；1 其他错误。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 以脚本自身位置定位可信后端源码树，避免依赖当前工作目录或 PYTHONPATH
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from src.offline_batch import ALGORITHM_VERSION, PACKAGE_FORMAT_VERSION  # noqa: E402
from src.offline_batch.errors import BatchError  # noqa: E402
from src.offline_batch.orchestrator import SUMMARY_NAME, run_batch  # noqa: E402
from src.offline_batch.state import LEDGER_NAME, Ledger  # noqa: E402

EXIT_OK = 0
_EXIT_INCOMPLETE = 2
_EXIT_WITH_FAILURES = 3
_EXIT_BATCH_ERROR = 4


def _cmd_run(args: argparse.Namespace) -> int:
    report = run_batch(
        Path(args.input_dir),
        Path(args.state_dir),
        format_version=args.format_version,
        algorithm_version=args.algorithm_version,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    counts = report["counts"]
    if not report["finished"]:
        print("批次尚未全部到达终态，未发布汇总。", file=sys.stderr)
        return _EXIT_INCOMPLETE
    print(
        "批次完成：输入 {input_total} 份，唯一研究 {unique_research} 个，"
        "重复 {duplicated} 份，成功 {completed}，失败 {failed}。".format(**counts),
        file=sys.stderr,
    )
    return EXIT_OK if counts["failed"] == 0 else _EXIT_WITH_FAILURES


def _cmd_status(args: argparse.Namespace) -> int:
    state_dir = Path(args.state_dir)
    ledger_path = state_dir / LEDGER_NAME
    if not ledger_path.is_file():
        print(json.dumps({"state": "no-ledger", "state_dir": str(state_dir)}, ensure_ascii=False))
        return _EXIT_INCOMPLETE
    ledger = Ledger(state_dir)
    by_state: dict[str, int] = {}
    for entry in ledger.entries().values():
        by_state[entry["state"]] = by_state.get(entry["state"], 0) + 1
    summary_path = state_dir / SUMMARY_NAME
    payload = {
        "state": "published" if summary_path.is_file() else "in-progress",
        "by_state": dict(sorted(by_state.items())),
        "ledger": str(ledger_path),
        "summary": str(summary_path) if summary_path.is_file() else None,
        "batch_meta": ledger.data.get("batch", {}),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return EXIT_OK if summary_path.is_file() else _EXIT_INCOMPLETE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="冻结研究包离线批量重算")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="执行或恢复一个批次（中断后重跑即续算）")
    p_run.add_argument("input_dir", help="冻结研究包目录（递归读取 .json，只读）")
    p_run.add_argument("--state-dir", required=True, help="批次状态/结果/隔离目录（不得与输入目录混用）")
    p_run.add_argument("--format-version", default=PACKAGE_FORMAT_VERSION)
    p_run.add_argument("--algorithm-version", default=ALGORITHM_VERSION)
    p_run.set_defaults(func=_cmd_run)

    p_status = sub.add_parser("status", help="只读查看批次台账状态，不执行计算")
    p_status.add_argument("--state-dir", required=True)
    p_status.set_defaults(func=_cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BatchError as exc:
        print(f"批次错误：{exc}", file=sys.stderr)
        return _EXIT_BATCH_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
