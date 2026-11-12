"""离线批量重算端到端测试。

用可信合成数据生成冻结包（非外部下载数据），在临时目录中验证：
去重、坏包隔离、中断恢复、已完成不改写、输入变更拒续、汇总门控、逐份来源指纹。

运行（backend 目录下，隔离 venv 已装 numpy==2.2.1）：
    /tmp/batch-venv/bin/python -I tests/test_offline_batch.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import numpy as np  # noqa: E402

from src.offline_batch import ALGORITHM_VERSION, PACKAGE_FORMAT_VERSION  # noqa: E402
from src.offline_batch.errors import BatchError, PackageError  # noqa: E402
from src.offline_batch.orchestrator import SUMMARY_NAME, run_batch  # noqa: E402
from src.offline_batch.package_io import load_package, sha256_file  # noqa: E402
from src.offline_batch.state import (  # noqa: E402
    ST_COMPLETED,
    ST_DUPLICATE,
    ST_FAILED,
    ST_INVALID,
    ST_MISSING,
    ST_REJECTED,
    ST_RUNNING,
    DirLock,
    Ledger,
)

WAVES = np.arange(400.0, 700.0, 25.0)
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


def make_endmembers(near_collinear: bool = False) -> list[dict]:
    base = np.array(
        [
            0.14 + 0.5 / (1 + np.exp(-(WAVES - 565) / 25)),
            0.12 + 0.4 * np.exp(-0.5 * ((WAVES - 525) / 45) ** 2),
            0.76 + 0.03 * np.cos((WAVES - 400) / 160),
            0.12 + 0.4 * np.exp(-0.5 * ((WAVES - 445) / 38) ** 2),
        ]
    ).T
    if near_collinear:
        base[:, 1] = base[:, 0] + np.linspace(0, 1e-10, len(WAVES))
    return [
        {"id": i + 1, "code": f"EM-0{i+1}", "name": f"端元{i+1}", "spectrum": base[:, i].tolist()}
        for i in range(4)
    ]


def make_snapshot(sample_codes: list[str], *, near_collinear: bool = False, seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    ems = make_endmembers(near_collinear)
    A = np.array([e["spectrum"] for e in ems]).T
    samples = []
    for code in sample_codes:
        w = rng.dirichlet(np.ones(4) * 2)
        observed = A @ w + rng.normal(0, 0.005, len(WAVES))
        samples.append(
            {"id": code, "code": code, "name": f"样本{code}", "observed": observed.tolist(), "note": ""}
        )
    return {
        "library": {
            "id": 1,
            "code": "LS-T",
            "name": "离线测试库",
            "wavelengths": WAVES.tolist(),
            "source": "synthetic-test",
        },
        "endmembers": ems,
        "samples": samples,
    }


def write_package(path: Path, research_id: str, snapshot: dict, **overrides) -> Path:
    payload = {
        "format_version": PACKAGE_FORMAT_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "research_id": research_id,
        "snapshot": snapshot,
        "noise_sigma": 0.005,
        "repeats": 80,
        "seed": 2026,
    }
    payload.update(overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def entry_by_source(ledger: Ledger, rel: str):
    return ledger.entries().get(rel)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="offline-batch-test-"))
    inp = tmp / "in"
    state = tmp / "state"

    # ---------- 1. 首跑：3 个独立研究 + 1 个重复 + 1 个坏包 + 1 个算法拒绝包 ----------
    snap1 = make_snapshot(["LS-T-001", "LS-T-002"], seed=11)
    snap2 = make_snapshot(["LS-T-003"], seed=22)
    snap3 = make_snapshot(["LS-T-001", "LS-T-003"], seed=33)
    write_package(inp / "r1.json", "RES-001", snap1)
    write_package(inp / "r2.json", "RES-002", snap2)
    write_package(inp / "r3.json", "RES-003", snap3)
    write_package(inp / "zz-dup" / "r1-copy.json", "RES-001", snap1)  # 同编号同内容重复（路径排序在主包之后）
    # 结构坏包
    (inp / "broken.json").write_text("{not json", encoding="utf-8")
    # 算法拒绝：近共线端元（条件数超限）
    snap_bad = make_snapshot(["LS-T-001"], near_collinear=True, seed=44)
    write_package(inp / "illposed.json", "RES-BAD", snap_bad)

    report = run_batch(inp, state, format_version=PACKAGE_FORMAT_VERSION, algorithm_version=ALGORITHM_VERSION)
    c = report["counts"]
    check("1.1 输入计数为 6", c["input_total"] == 6, str(c))
    check("1.2 可识别唯一研究 4 个（含 1 个算法拒绝）", c["unique_research"] == 4, str(c))
    check("1.3 重复 1 份", c["duplicated"] == 1, str(c))
    check("1.4 无法识别编号坏包 1 份", c["unidentified"] == 1, str(c))
    check("1.5 成功 3 个", c["completed"] == 3, str(c))
    check("1.6 可识别条目失败 1 个（算法拒绝）", c["failed"] == 1, str(c))
    check("1.7 全终态时发布汇总", report["finished"] and Path(report["summary"]).is_file())

    ledger = Ledger(state)
    e_r1 = entry_by_source(ledger, "r1.json")
    check("1.8 r1 完成且有结果路径", e_r1["state"] == ST_COMPLETED and e_r1["result"])
    check("1.9 r1 保留来源指纹与内容指纹", bool(e_r1["source_sha256"]) and bool(e_r1["content_fingerprint"]))
    check(
        "1.10 来源指纹等于文件字节 SHA256",
        e_r1["source_sha256"] == sha256_file(inp / "r1.json"),
    )
    e_dup = entry_by_source(ledger, "zz-dup/r1-copy.json")
    check("1.11 重复条目标 duplicate 且指向保留条目", e_dup["state"] == ST_DUPLICATE and e_dup["duplicate_of"] == "r1.json")
    e_broken = entry_by_source(ledger, "broken.json")
    check("1.12 坏包 invalid 且注明原因码", e_broken["state"] == ST_INVALID and e_broken["reason_code"] == "JSON_PARSE")
    check("1.13 坏包已隔离", (state / e_broken["quarantine"]).is_file())
    e_ill = entry_by_source(ledger, "illposed.json")
    check("1.14 近共线包 failed 且注明算法原因", e_ill["state"] == ST_FAILED and e_ill["reason_code"] == "COMPUTATION_REJECTED")
    check("1.15 失败包已隔离", (state / e_ill["quarantine"]).is_file())

    # 结果文件内容与在线 analyze_unmix 口径一致
    result_doc = json.loads((state / e_r1["result"]).read_text(encoding="utf-8"))
    check("1.16 结果携带 input_hash 与算法版本", result_doc["result"]["input_hash"] and result_doc["result"]["algorithm_version"] == ALGORITHM_VERSION)
    check(
        "1.17 结果文件记录来源指纹",
        result_doc["source_sha256"] == e_r1["source_sha256"] and result_doc["content_fingerprint"] == e_r1["content_fingerprint"],
    )

    # 汇总数量对齐
    summary = json.loads((state / SUMMARY_NAME).read_text(encoding="utf-8"))
    sc = summary["counts"]
    check(
        "1.18 汇总对齐 输入=唯一+重复+无法识别",
        sc["input_total"] == sc["unique_research"] + sc["duplicated"] + sc["unidentified"],
    )
    check(
        "1.19 汇总对齐 唯一=成功+失败",
        sc["unique_research"] == sc["completed"] + sc["failed"],
    )

    # ---------- 2. 幂等重跑：已完成不改写 ----------
    r1_file = state / e_r1["result"]
    mtime_before = r1_file.stat().st_mtime_ns
    content_before = r1_file.read_bytes()
    time.sleep(0.02)
    report2 = run_batch(inp, state, format_version=PACKAGE_FORMAT_VERSION, algorithm_version=ALGORITHM_VERSION)
    check("2.1 重跑后计数不变", report2["counts"] == c)
    check(
        "2.2 已完成配比文件未改写",
        r1_file.stat().st_mtime_ns == mtime_before and r1_file.read_bytes() == content_before,
    )
    check("2.3 重跑仍发布汇总", report2["finished"])

    # ---------- 3. 中断恢复：伪造 running 条目后重入 ----------
    ledger.upsert(
        "r2.json",
        {**entry_by_source(ledger, "r2.json"), "state": ST_RUNNING},
    )
    r2_file = state / "results" / "RES-002.json"
    doc_before = json.loads(r2_file.read_text(encoding="utf-8"))
    report3 = run_batch(inp, state, format_version=PACKAGE_FORMAT_VERSION, algorithm_version=ALGORITHM_VERSION)
    e_r2 = entry_by_source(Ledger(state), "r2.json")
    check("3.1 中断条目重算后回到 completed", e_r2["state"] == ST_COMPLETED)
    doc3 = json.loads(r2_file.read_text(encoding="utf-8"))
    check(
        "3.2 重算配比与 input_hash 完全一致（确定性）",
        doc3["result"]["input_hash"] == doc_before["result"]["input_hash"]
        and doc3["result"]["samples"][0]["point_fit"]["weights"]
        == doc_before["result"]["samples"][0]["point_fit"]["weights"],
    )
    check("3.3 尝试次数累加", e_r2["attempts"] >= 2, str(e_r2.get("attempts")))

    # ---------- 4. 同编号换输入：拒绝续跑 ----------
    snap1_changed = make_snapshot(["LS-T-001", "LS-T-002"], seed=99)  # 同编号、不同观测
    r1_doc_before = json.loads(r1_file.read_text(encoding="utf-8"))
    write_package(inp / "r1.json", "RES-001", snap1_changed)
    report4 = run_batch(inp, state, format_version=PACKAGE_FORMAT_VERSION, algorithm_version=ALGORITHM_VERSION)
    ledger4 = Ledger(state)
    e_r1_v2 = entry_by_source(ledger4, "r1.json")
    check("4.1 变更输入被 rejected", e_r1_v2["state"] == ST_REJECTED and e_r1_v2["reason_code"] == "INPUT_CHANGED")
    check("4.2 变更包已隔离", (state / e_r1_v2["quarantine"]).is_file())
    check("4.3 记录了前后两个指纹", e_r1_v2.get("previous_fingerprint") and e_r1_v2["previous_fingerprint"] != e_r1_v2["content_fingerprint"])
    check("4.4 可识别失败计数增加（拒绝计入失败）", report4["counts"]["failed"] == 2, str(report4["counts"]))
    check("4.5 旧配比结果文件未被新输入覆盖", json.loads(r1_file.read_text())["content_fingerprint"] == r1_doc_before["content_fingerprint"])

    # ---------- 5. 另一来源出现同编号不同输入：双方冻结 ----------
    write_package(inp / "sibling" / "r2-alt.json", "RES-002", make_snapshot(["LS-T-009"], seed=55))
    run_batch(inp, state, format_version=PACKAGE_FORMAT_VERSION, algorithm_version=ALGORITHM_VERSION)
    ledger5 = Ledger(state)
    e_sib = entry_by_source(ledger5, "sibling/r2-alt.json")
    e_r2_v2 = entry_by_source(ledger5, "r2.json")
    check("5.1 异源同编号不同输入 rejected", e_sib["state"] == ST_REJECTED and e_sib["reason_code"] == "INPUT_CHANGED")
    check("5.2 原已完成条目被冻结不再发布", e_r2_v2["state"] == ST_REJECTED and e_r2_v2.get("result") is None)

    # ---------- 6. 汇总门控：非终态条目不发布汇总（独立小批次） ----------
    gate_inp = tmp / "gate-in"
    gate_state = tmp / "gate-state"
    write_package(gate_inp / "g1.json", "GATE-001", make_snapshot(["LS-T-001"], seed=71))
    write_package(gate_inp / "g2.json", "GATE-002", make_snapshot(["LS-T-002"], seed=72))
    run_batch(gate_inp, gate_state, format_version=PACKAGE_FORMAT_VERSION, algorithm_version=ALGORITHM_VERSION)
    gate_ledger = Ledger(gate_state)
    check("6.1 完整批次先发布汇总", (gate_state / SUMMARY_NAME).is_file())
    gate_ledger.upsert(
        "g1.json", {**entry_by_source(gate_ledger, "g1.json"), "state": ST_RUNNING}
    )
    gate_report = run_batch(gate_inp, gate_state, format_version=PACKAGE_FORMAT_VERSION, algorithm_version=ALGORITHM_VERSION)
    # g1 被重算完成 → 这里改为验证门控函数本身：构造一个不会被本轮修复的非终态
    check("6.2 中断重算后恢复发布", gate_report["finished"] and (gate_state / SUMMARY_NAME).is_file())
    gate_ledger = Ledger(gate_state)
    gate_ledger.upsert(
        "ghost.json",
        {"research_id": "GHOST", "state": ST_RUNNING, "source": "ghost.json",
         "source_sha256": None, "content_fingerprint": None, "attempts": 1},
    )
    from src.offline_batch.orchestrator import _is_finished, _write_summary_if_finished
    from src.offline_batch.orchestrator import BatchPaths

    gate_paths = BatchPaths.create(gate_inp, gate_state)
    active = set(gate_ledger.entries().keys())
    check("6.3 存在非终态条目时门控判定未完成", not _is_finished(gate_ledger, active))
    check(
        "6.4 未完成时不发布汇总且旧汇总被撤下",
        not _write_summary_if_finished(gate_paths, gate_ledger, active)
        and not (gate_state / SUMMARY_NAME).exists(),
    )

    # ---------- 7. 来源文件消失：标记 missing 且不静默丢弃 ----------
    (inp / "r3.json").unlink()
    report7 = run_batch(inp, state, format_version=PACKAGE_FORMAT_VERSION, algorithm_version=ALGORITHM_VERSION)
    ledger7 = Ledger(state)
    e_missing = entry_by_source(ledger7, "r3.json")
    check("7.1 消失来源标记 missing", e_missing["state"] == ST_MISSING and e_missing["reason_code"] == "SOURCE_MISSING")
    check("7.2 当前输入计数不含消失文件", report7["counts"]["input_total"] == 6)
    check("7.3 其余完成后重新发布汇总", report7["finished"] and Path(report7["summary"]).is_file())

    # ---------- 8. 文件锁：并发批次被拒绝 ----------
    lock = DirLock(state)
    lock.acquire()
    try:
        raised = False
        try:
            run_batch(inp, state, format_version=PACKAGE_FORMAT_VERSION, algorithm_version=ALGORITHM_VERSION)
        except BatchError:
            raised = True
        check("8.1 同状态目录并发执行被锁拒绝", raised)
    finally:
        lock.release()

    # ---------- 9. 包级校验：各种坏包原因码 ----------
    bad_dir = tmp / "bad"
    good = make_snapshot(["LS-T-001"])
    write_package(bad_dir / "fmt.json", "X", good, format_version="wrong/9")
    try:
        load_package(bad_dir / "fmt.json", PACKAGE_FORMAT_VERSION, ALGORITHM_VERSION)
        check("9.1 格式版本不匹配报错", False)
    except PackageError as exc:
        check("9.1 格式版本不匹配报错", exc.code == "FORMAT_VERSION")
    write_package(bad_dir / "algo.json", "X", good, algorithm_version="other-v9")
    try:
        load_package(bad_dir / "algo.json", PACKAGE_FORMAT_VERSION, ALGORITHM_VERSION)
        check("9.2 算法版本不匹配报错", False)
    except PackageError as exc:
        check("9.2 算法版本不匹配报错", exc.code == "ALGORITHM_VERSION")
    write_package(
        bad_dir / "shape.json",
        "X",
        snapshot={**good, "samples": [{"code": "S", "observed": [0.1, 0.2]}]},
    )
    try:
        load_package(bad_dir / "shape.json", PACKAGE_FORMAT_VERSION, ALGORITHM_VERSION)
        check("9.3 谱长不匹配报错", False)
    except PackageError as exc:
        check("9.3 谱长不匹配报错", exc.code == "SAMPLE_OBSERVED_SHAPE")

    # ---------- 10. CLI：-I 隔离模式端到端 ----------
    cli_inp = tmp / "cli-in"
    cli_state = tmp / "cli-state"
    write_package(cli_inp / "c1.json", "CLI-001", make_snapshot(["LS-T-001"], seed=61))
    proc = subprocess.run(
        [sys.executable, "-I", str(_BACKEND_ROOT / "scripts" / "offline_batch.py"),
         "run", str(cli_inp), "--state-dir", str(cli_state)],
        capture_output=True, text=True, cwd=tmp,
    )
    check("10.1 CLI 退出码 0", proc.returncode == 0, proc.stderr)
    cli_summary = cli_state / SUMMARY_NAME
    check("10.2 CLI 发布汇总", cli_summary.is_file())
    proc_status = subprocess.run(
        [sys.executable, "-I", str(_BACKEND_ROOT / "scripts" / "offline_batch.py"),
         "status", "--state-dir", str(cli_state)],
        capture_output=True, text=True, cwd=tmp,
    )
    status_doc = json.loads(proc_status.stdout)
    check("10.3 status 子命令只读可用", status_doc["state"] == "published" and proc_status.returncode == 0)

    # 安全守卫：输入目录放置同名 json.py 时，-I 且脚本目录可信，标准库不被劫持
    (cli_inp / "json.py").write_text("raise RuntimeError('planted module loaded')\n", encoding="utf-8")
    proc_safe = subprocess.run(
        [sys.executable, "-I", str(_BACKEND_ROOT / "scripts" / "offline_batch.py"),
         "run", str(cli_inp), "--state-dir", str(cli_state)],
        capture_output=True, text=True, cwd=tmp,
    )
    check("10.4 输入目录植入 json.py 不被加载", proc_safe.returncode in (0, 3) and "planted module" not in proc_safe.stderr)

    # ---------- 11. failed 包：未修正不空转，修正后同状态目录可重算 ----------
    fx_inp = tmp / "fix-in"
    fx_state = tmp / "fix-state"
    write_package(fx_inp / "bad.json", "FIX-001", make_snapshot(["LS-T-001"], near_collinear=True, seed=81))
    r11 = run_batch(fx_inp, fx_state, format_version=PACKAGE_FORMAT_VERSION, algorithm_version=ALGORITHM_VERSION)
    fx_ledger = Ledger(fx_state)
    e_bad = entry_by_source(fx_ledger, "bad.json")
    check("11.1 近共线包首跑 failed", e_bad["state"] == ST_FAILED and e_bad["reason_code"] == "COMPUTATION_REJECTED")
    attempts_first = e_bad["attempts"]
    # 未修正重跑：状态与尝试次数不变，不重复计算
    run_batch(fx_inp, fx_state, format_version=PACKAGE_FORMAT_VERSION, algorithm_version=ALGORITHM_VERSION)
    e_bad2 = entry_by_source(Ledger(fx_state), "bad.json")
    check("11.2 未修正失败包不重算", e_bad2["state"] == ST_FAILED and e_bad2["attempts"] == attempts_first)
    # 修正为可辨识端元后重算：同状态目录恢复为 completed
    write_package(fx_inp / "bad.json", "FIX-001", make_snapshot(["LS-T-001"], near_collinear=False, seed=82))
    r11b = run_batch(fx_inp, fx_state, format_version=PACKAGE_FORMAT_VERSION, algorithm_version=ALGORITHM_VERSION)
    e_good = entry_by_source(Ledger(fx_state), "bad.json")
    check("11.3 修正后重算成功", e_good["state"] == ST_COMPLETED and e_good["attempts"] == attempts_first + 1)
    check("11.4 修正后批次发布汇总", r11b["finished"] and (fx_state / SUMMARY_NAME).is_file())
    check("11.5 失败计数清零、成功 1", r11b["counts"]["failed"] == 0 and r11b["counts"]["completed"] == 1)

    print(f"\n临时目录：{tmp}")
    if FAILURES:
        print(f"\n{len(FAILURES)} 项失败：{FAILURES}")
        return 1
    print("\n全部测试通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
