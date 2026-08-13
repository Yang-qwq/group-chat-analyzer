# -*- coding: utf-8 -*-
"""pytest 全局配置：测试环境隔离

在导入 ncatbot 之前指定配置文件路径，避免读取/污染仓库根目录的真实 config.yaml。
"""
import os
from pathlib import Path

os.environ["NCATBOT_CONFIG_PATH"] = str(
    Path(__file__).resolve().parent / "ncatbot_test_config.yaml"
)
