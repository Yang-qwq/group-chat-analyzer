# -*- coding: utf-8 -*-
"""pytest 全局配置：测试环境隔离

1. 在导入 ncatbot 之前指定配置文件路径，避免读取/污染仓库根目录的真实 config.yaml。
2. 清理 pytest 收集机制对插件包导入状态的污染，保证每个用例的 harness
   都能在正确的插件上下文中加载 handler。
"""
import os
import sys
from pathlib import Path

import pytest

os.environ["NCATBOT_CONFIG_PATH"] = str(
    Path(__file__).resolve().parent / "ncatbot_test_config.yaml"
)

# 插件包顶层名（与插件目录名一致，见 manifest.toml folder_name）
_PLUGIN_PKG = "GroupChatAnalyzer"


def _purge_plugin_modules() -> None:
    """清除插件包的预导入缓存。

    pytest 对「位于含 __init__.py 的包目录内的测试」会在用例 setup 阶段
    预导入包顶层 ``__init__.py``（``from .main import ...`` 级联导入全部子模块）。
    该次导入发生在插件加载上下文之外，``@registrar.qq.*`` 装饰器收集到的 handler
    被归入 ``__global__``；随后 harness 的 loader ``import_module`` 命中
    ``sys.modules`` 缓存、不再重执行装饰器，导致进程内第一个 harness 注册 0 个
    handler（事件注入全部丢失）。修复方式：harness 创建前清掉缓存，loader 会在
    ``set_current_plugin`` 上下文内重新导入并正确注册。
    """
    stale = [
        name for name in list(sys.modules)
        if name == _PLUGIN_PKG or name.startswith(_PLUGIN_PKG + ".")
    ]
    if not stale:
        return
    for name in stale:
        del sys.modules[name]
    # 同步清掉收集期在插件上下文之外收集的 handler，防止 loader 的
    # __global__ 兜底逻辑（loader/core.py）把它们错误归属到其它插件
    try:
        from ncatbot.core import _pending_handlers

        _pending_handlers.pop("__global__", None)
    except Exception:  # pragma: no cover - 框架内部符号变动时静默降级
        pass


@pytest.fixture(autouse=True)
def fresh_plugin_import_state():
    """autouse：每个用例执行前清理插件包预导入状态（测试间隔离）。

    fixture 在 pytest 父收集器 setup（含包 ``__init__.py`` 预导入）之后运行、
    测试函数体（含 harness 创建）之前运行，恰好清除该污染。
    """
    _purge_plugin_modules()
    yield
