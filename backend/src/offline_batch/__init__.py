"""离线冻结研究包批量重算。

只读取导出的冻结研究包文件与本目录状态，不连接数据库、Redis 或任何现行库；
解混算法直接复用 services.analysis_service.analyze_unmix，保证与在线结果同版本同口径。
"""
PACKAGE_FORMAT_VERSION = "frozen-research-package/v1"
ALGORITHM_VERSION = "simplex-nullspace-kkt-gaussian-v2"
