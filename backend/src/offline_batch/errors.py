"""离线批处理错误类型。原因码进入清单，便于隔离坏包并注明原因。"""


class PackageError(ValueError):
    """冻结研究包结构或内容不合法。code 为稳定原因码，message 为中文说明。"""

    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


class BatchError(RuntimeError):
    """批处理流程级错误（锁冲突、状态目录不可用等）。"""
