# 离线批量重算（冻结研究包）

在离线环境对导出的**冻结研究包**批量重算光谱解混。工具只读取包文件与自身状态目录，
**不连接数据库、Redis 或任何现行库**；解混直接复用 `src/services/analysis_service.py`
的 `analyze_unmix`，与在线 worker 同算法版本（`simplex-nullspace-kkt-gaussian-v2`）、
同参数口径，结果可逐位复现。

## 入口

```sh
# 执行或恢复一个批次（中断后重跑同一条命令即续算）
python3 -I scripts/offline_batch.py run /data/frozen-pkgs --state-dir /data/batch-state

# 只读查看台账进度，不做任何计算
python3 -I scripts/offline_batch.py status --state-dir /data/batch-state
```

- `-I`（隔离模式）：忽略环境变量与当前目录的 `sys.path` 注入，防止输入包目录内
  被植入的同名模块（如 `json.py`）劫持标准库。脚本按自身位置定位可信后端源码树。
- 输入目录递归读取 `*.json`，**只读**；状态目录与输入目录必须分开。
- 仅依赖 NumPy（与 `requirements.txt` 同版本 2.2.1），无 FastAPI/SQLAlchemy/Redis 依赖。

## 冻结包格式（`frozen-research-package/v1`）

```json
{
  "format_version": "frozen-research-package/v1",
  "algorithm_version": "simplex-nullspace-kkt-gaussian-v2",
  "research_id": "STUDY-001",
  "snapshot": {
    "library":   {"code": "LS-A", "wavelengths": [400.0, 425.0, "..."], "...": "..."},
    "endmembers": [{"code": "EM-01", "spectrum": ["...与波长等长..."]}],
    "samples":    [{"code": "LS-A-001", "observed": ["...与波长等长..."]}]
  },
  "noise_sigma": 0.005,
  "repeats": 80,
  "seed": 2026
}
```

端元、样本、波长与噪声参数全部取自包内；校验规则与在线服务一致
（2–6 端元、1–6 样本、20–120 次重复、sigma ∈ [0, 0.2]、谱长与波长一致等）。
算法版本不匹配的包直接拒绝，不做跨版本重算。

## 状态目录布局

```
batch-state/
├── batch.lock            # fcntl 排他锁，禁止同状态目录并发批次
├── batch-ledger.json     # 台账（恢复续算的唯一事实来源，原子写入）
├── batch-summary.json    # 仅在全量到达终态时发布；未完成时不存在/被撤下
├── results/              # 每个保留研究一份配比结果（原子落盘，完成后绝不改写）
│   └── STUDY-001.json
└── quarantine/           # 坏包/拒绝包的原样副本，清单注明原因码
    └── STUDY-001__bad.json
```

## 处理规则

| 规则 | 行为 |
|---|---|
| 沿用包内参数 | 端元/样本/噪声/种子全部取自冻结包，不读现行库 |
| 同批重复研究 | 按相对路径排序，首个同编号同指纹包为保留条目，其余记 `duplicate`，只算一次 |
| 坏包隔离 | 结构非法记 `invalid`、算法拒绝/异常记 `failed`，副本进 `quarantine/`，原因码入清单 |
| 中断恢复 | `completed` 且结果文件存在 → 原样保留不改写；`running` → 重算并原子替换 |
| 同编号换输入 | 仅对**已产出配比**的条目（completed/running）生效：指纹变化记 `rejected` 并隔离，同编号已完成配比同时冻结，拒绝新旧混批 |
| 失败包修正 | `failed`/`invalid` 从未产出配比，文件被修正（指纹变化）后同状态目录可重算；未修正则保持隔离不空转 |
| 来源消失 | 恢复扫描时台账有、输入目录已无的来源记 `missing`，不静默丢弃 |
| 汇总门控 | 存在任一非终态条目（running/pending）则不发布 `batch-summary.json`；允许带 failed 发布 |

### 数量对齐（汇总发布前断言）

```
输入总数 = 唯一研究 + 重复 + 无法识别编号的坏包
唯一研究 = 成功(completed) + 失败(invalid/rejected/failed)
```

## 逐份来源指纹

每份输入保留两层指纹，写入台账、结果文件与汇总：

- `source_sha256`：包文件**原始字节**的 SHA256，与解析/字段重排无关，证明来源逐份未被篡改；
- `content_fingerprint`：对算法计算载荷（方法版本 + 规范化快照 + sigma/repeats/seed）
  做与在线 `input_hash` 同口径的 SHA256，用于“同编号换输入”判定。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 批次全部到达终态且无失败 |
| 2 | 批次未完成（仍有非终态条目），未发布汇总 |
| 3 | 批次完成但含失败/拒绝条目 |
| 4 | 流程错误（锁冲突、输入目录不存在、台账损坏等） |
| 1 | 其他未预期错误 |

## 测试

```sh
/tmp/batch-venv/bin/python -I tests/test_offline_batch.py
```

覆盖：去重、坏包隔离与原因码、中断恢复、已完成不改写、同编号换输入拒续、
异源同编号冻结、汇总门控、来源缺失、并发锁、CLI 隔离模式与植入模块防护。
