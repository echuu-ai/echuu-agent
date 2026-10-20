import logging

logger = logging.getLogger("echuu.diagnostics")
logger.addHandler(logging.NullHandler())


def diagnostic(*values: object, sep: str = " ", end: str = "\n", flush: bool = False) -> None:
    """旧引擎的输出转为可选调试日志；默认不向宿主 stdout 输出内容。"""
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("%s", sep.join(str(value) for value in values))
