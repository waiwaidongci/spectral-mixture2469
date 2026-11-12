"""冻结研究包的发现、读取、结构校验与来源指纹。

冻结包是导出时一次性写入的 JSON 文件（PACKAGE_FORMAT_VERSION），包含端元、样本、
波长与噪声参数。本模块只用文件系统与标准库，不读取任何现行库。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..utils.fingerprint import fingerprint
from .errors import PackageError

_REQUIRED_TOP = ("format_version", "research_id", "snapshot", "noise_sigma", "repeats", "seed")
_SNAPSHOT_KEYS = ("library", "endmembers", "samples")


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """逐份来源指纹：对包文件原始字节做 SHA256，与解析/重排无关。"""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class FrozenPackage:
    research_id: str
    snapshot: dict
    noise_sigma: float
    repeats: int
    seed: int
    algorithm_version: str
    source_path: Path
    source_sha256: str
    content_fingerprint: str
    raw: dict


def _is_real_number(value: Any) -> bool:
    # bool 是 int 子类，必须显式排除
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise PackageError(code, message)


def _validate_snapshot(snapshot: Any) -> dict:
    _require(isinstance(snapshot, dict), "SNAPSHOT_NOT_OBJECT", "snapshot 必须为对象")
    for key in _SNAPSHOT_KEYS:
        _require(key in snapshot, "SNAPSHOT_MISSING_KEY", f"snapshot 缺少字段 {key}")
    library = snapshot["library"]
    _require(isinstance(library, dict), "LIBRARY_NOT_OBJECT", "snapshot.library 必须为对象")
    _require(
        isinstance(library.get("wavelengths"), list) and library["wavelengths"],
        "WAVELENGTHS_EMPTY",
        "library.wavelengths 必须为非空数组",
    )
    _require(
        all(_is_real_number(w) for w in library["wavelengths"]),
        "WAVELENGTHS_NOT_NUMBER",
        "波长必须全部为数字",
    )
    endmembers = snapshot["endmembers"]
    _require(
        isinstance(endmembers, list) and 2 <= len(endmembers) <= 6,
        "ENDMEMBER_COUNT",
        "端元数量必须在 2–6 之间",
    )
    em_codes: set[str] = set()
    n_bands = len(library["wavelengths"])
    for idx, em in enumerate(endmembers):
        _require(isinstance(em, dict), "ENDMEMBER_NOT_OBJECT", f"端元 #{idx} 必须为对象")
        code = em.get("code")
        _require(isinstance(code, str) and code, "ENDMEMBER_CODE", f"端元 #{idx} 缺少 code")
        _require(code not in em_codes, "ENDMEMBER_DUPLICATE_CODE", f"端元编号重复：{code}")
        em_codes.add(code)
        spectrum = em.get("spectrum")
        _require(
            isinstance(spectrum, list) and len(spectrum) == n_bands,
            "ENDMEMBER_SPECTRUM_SHAPE",
            f"端元 {code} 谱长须与波长数（{n_bands}）一致",
        )
        _require(
            all(_is_real_number(v) for v in spectrum),
            "ENDMEMBER_SPECTRUM_NOT_NUMBER",
            f"端元 {code} 光谱必须全部为数字",
        )
    samples = snapshot["samples"]
    _require(isinstance(samples, list) and 1 <= len(samples) <= 6, "SAMPLE_COUNT", "样本数量必须在 1–6 之间")
    sample_codes: set[str] = set()
    for idx, sample in enumerate(samples):
        _require(isinstance(sample, dict), "SAMPLE_NOT_OBJECT", f"样本 #{idx} 必须为对象")
        code = sample.get("code")
        _require(isinstance(code, str) and code, "SAMPLE_CODE", f"样本 #{idx} 缺少 code")
        _require(code not in sample_codes, "SAMPLE_DUPLICATE_CODE", f"样本编号重复：{code}")
        sample_codes.add(code)
        observed = sample.get("observed")
        _require(
            isinstance(observed, list) and len(observed) == n_bands,
            "SAMPLE_OBSERVED_SHAPE",
            f"样本 {code} 观测谱长须与波长数（{n_bands}）一致",
        )
        _require(
            all(_is_real_number(v) for v in observed),
            "SAMPLE_OBSERVED_NOT_NUMBER",
            f"样本 {code} 观测谱必须全部为数字",
        )
    return snapshot


def load_package(path: Path, expected_format: str, expected_algorithm: str) -> FrozenPackage:
    """读取并校验单个冻结包；任何结构问题抛 PackageError（原因码可直接入清单）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PackageError("ENCODING", f"文件不是 UTF-8 文本：{exc}") from exc
    except OSError as exc:
        raise PackageError("UNREADABLE", f"文件无法读取：{exc}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PackageError("JSON_PARSE", f"JSON 解析失败（第{exc.lineno}行第{exc.colno}列）：{exc.msg}") from exc
    _require(isinstance(raw, dict), "TOP_NOT_OBJECT", "包顶层必须为 JSON 对象")
    for key in _REQUIRED_TOP:
        _require(key in raw, "MISSING_FIELD", f"缺少必填字段：{key}")
    _require(
        raw["format_version"] == expected_format,
        "FORMAT_VERSION",
        f"格式版本不匹配：包为 {raw['format_version']!r}，本工具支持 {expected_format!r}",
    )
    research_id = raw["research_id"]
    _require(
        isinstance(research_id, str) and research_id.strip(),
        "RESEARCH_ID",
        "research_id 必须为非空字符串",
    )
    algorithm_version = raw.get("algorithm_version", expected_algorithm)
    _require(
        algorithm_version == expected_algorithm,
        "ALGORITHM_VERSION",
        f"算法版本不匹配：包为 {algorithm_version!r}，本工具为 {expected_algorithm!r}，拒绝跨版本重算",
    )
    noise_sigma = raw["noise_sigma"]
    _require(_is_real_number(noise_sigma), "SIGMA_TYPE", "noise_sigma 必须为有限数字")
    repeats = raw["repeats"]
    _require(isinstance(repeats, int) and not isinstance(repeats, bool), "REPEATS_TYPE", "repeats 必须为整数")
    seed = raw["seed"]
    _require(isinstance(seed, int) and not isinstance(seed, bool), "SEED_TYPE", "seed 必须为整数")
    snapshot = _validate_snapshot(raw["snapshot"])
    source_sha = sha256_file(path)
    pkg = FrozenPackage(
        research_id=research_id.strip(),
        snapshot=snapshot,
        noise_sigma=float(noise_sigma),
        repeats=repeats,
        seed=seed,
        algorithm_version=algorithm_version,
        source_path=path,
        source_sha256=source_sha,
        content_fingerprint=fingerprint(
            {
                "method": algorithm_version,
                "snapshot": snapshot,
                "noise_sigma": float(noise_sigma),
                "repeats": repeats,
                "seed": seed,
            }
        ),
        raw=raw,
    )
    return pkg


def discover_package_files(input_dir: Path) -> list[Path]:
    """递归发现 .json 包文件，按相对路径排序保证批次稳定可复现。"""
    return sorted(p for p in input_dir.rglob("*.json") if p.is_file())
