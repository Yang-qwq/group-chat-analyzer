# -*- coding: utf-8 -*-
"""配置项处理逻辑测试

覆盖 5 个配置项的运行时行为（默认值注册 + 各开关/数值在使用点的实际效果）：
1. EnableAutoRecord     — 群消息落库开关（默认开；关闭后不落库）
2. DataRetentionDays    — 清理阈值（默认 30；0=不清理；字符串值解析；/gcpurge 下限拦截）
3. AutoSendSummary      — 自动发送全局总开关（默认关；关闭时跳过且不发群消息；开启后发送并写 last_send）
4. ForceBase64ImageSend — 图片发送模式（默认 false=本地路径；true=Base64 data URI）
5. EnableGroupOwnerAutoAuth — 见 test_permission.py（开关关闭后群主不再放行）

Mock 记录要点（源码 .venv/Lib/site-packages/ncatbot/adapter/mock/api.py + api/qq/sugar.py）：
- `event.reply(text=, image=)` 与 `api.qq.post_group_msg(text=, image=)` 均归一录制为
  action `send_group_msg`；`image` 不是顶层参数，而是 `message` 段列表中
  `{"type": "image", "data": {"file": <引用>}}` 段的 file 字段（见 _extract_image_file_ref）。
- `event.reply` 路径 recording 的 group_id 为事件内字符串；自动发送 `post_group_msg(gid)`
  以 int 传入，断言须按调用路径区分类型。

历史数据直接插入插件 SQLite（tmp_path 隔离，不触碰仓库根目录真实 config.yaml / data/）；
自动发送用例 monkeypatch 图表生成器（图表渲染属 analyzer 职责，本文件只测配置门控）。

运行：python -m pytest tests -v -o "addopts="
"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from ncatbot.testing import PluginTestHarness, group_message

pytestmark = pytest.mark.asyncio(mode="strict")

# 插件根目录的上一级（仓库 plugins/ 目录，内含本插件 manifest.toml）
PLUGINS_DIR = Path(__file__).resolve().parents[2]
ROOT_QQ = "123456"
GROUP_ID = "200200"


def _harness() -> PluginTestHarness:
    """构造隔离的插件测试环境（不启动，由调用方 async with / start）"""
    return PluginTestHarness(
        plugin_names=["group_chat_analyzer"],
        plugins_dir=PLUGINS_DIR,
    )


def _seed_messages(plugin, rows) -> None:
    """向插件 SQLite 直接插入历史消息（供清理/分析类测试铺数据）

    :param plugin: 插件实例（复用其 DatabaseManager 连接，位于 tmp_path 隔离目录）
    :param rows: [(user_id, text, timestamp_datetime), ...]
    """
    conn = plugin.db.conn
    for uid, text, ts in rows:
        conn.execute(
            'INSERT INTO group_messages (group_id, user_id, message, timestamp) '
            'VALUES (?, ?, ?, ?)',
            (int(GROUP_ID), uid, text, ts.strftime('%Y-%m-%d %H:%M:%S')),
        )
    conn.commit()


def _count_all(plugin) -> int:
    """统计插件数据库中全部消息数（不限群）

    事件注入落库的 group_id 形态与直插 int 可能存在 TEXT/INTEGER 存储差异，
    断言行数时统一用全局计数，避免依赖 SQLite 隐式类型转换。

    :param plugin: 插件实例
    :return: 消息总行数
    """
    return plugin.db.conn.execute('SELECT COUNT(*) FROM group_messages').fetchone()[0]


def _extract_image_file_ref(h: PluginTestHarness):
    """从 send_group_msg 调用记录中提取最后一个 image 段的 file 引用

    :param h: harness（读取 qq 平台 mock 调用记录）
    :return: file 字符串（本地路径或 data URI）；无图片段时返回 None
    """
    calls = h.mock_api_for("qq").get_calls("send_group_msg")
    for call in reversed(calls):
        for seg in call.params.get("message") or []:
            if isinstance(seg, dict) and seg.get("type") == "image":
                return seg.get("data", {}).get("file")
    return None


# ---------------------------------------------------------------------------
# EnableAutoRecord：群消息落库开关
# ---------------------------------------------------------------------------

async def test_auto_record_enabled_by_default(tmp_path, monkeypatch):
    """默认开启：群消息注入后落库（含用户名与发言计数）"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin("group_chat_analyzer")
        # 确认 on_load 的 init_defaults 注册了 True
        assert plugin.get_config('EnableAutoRecord', None) is True

        await h.inject(group_message("测试消息", group_id=GROUP_ID, user_id="555"))
        # SQLite WAL 提交在 Windows 上可能较慢，放宽 settle 避免落库未完成误判
        await h.settle(0.3)
        assert _count_all(plugin) == 1
        # 用户名与发言计数同步落库
        row = plugin.db.conn.execute(
            'SELECT user_name, total_messages FROM user_names WHERE user_id = ?', (555,)
        ).fetchone()
        assert row == ("测试用户", 1)


async def test_auto_record_can_be_disabled(tmp_path, monkeypatch):
    """关闭后群消息不再落库"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin("group_chat_analyzer")
        plugin.config["EnableAutoRecord"] = False

        await h.inject(group_message("测试消息", group_id=GROUP_ID, user_id="555"))
        await h.settle()
        assert _count_all(plugin) == 0


# ---------------------------------------------------------------------------
# DataRetentionDays：清理阈值与 /gcpurge 下限
# ---------------------------------------------------------------------------

async def test_data_retention_zero_disables_auto_cleanup(tmp_path, monkeypatch):
    """DataRetentionDays=0 时定时清理任务跳过，旧数据保留"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin("group_chat_analyzer")
        _seed_messages(plugin, [("555", "旧消息", datetime.now() - timedelta(days=40))])
        assert _count_all(plugin) == 1

        plugin.config["DataRetentionDays"] = 0
        await plugin._auto_cleanup_database()
        # 0 天 = 不自动清理，40 天前的数据仍在
        assert _count_all(plugin) == 1


async def test_data_retention_positive_triggers_auto_cleanup(tmp_path, monkeypatch):
    """默认 30 天：超过保留期的数据被定时清理任务删除，未超期的保留"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin("group_chat_analyzer")
        # 确认 on_load 的 init_defaults 注册了 30
        assert plugin.get_config('DataRetentionDays', None) == 30

        _seed_messages(plugin, [
            ("555", "旧消息", datetime.now() - timedelta(days=40)),
            ("555", "新消息", datetime.now() - timedelta(hours=1)),
        ])
        assert plugin.db.get_total_messages_count(int(GROUP_ID)) == 2

        await plugin._auto_cleanup_database()
        assert plugin.db.get_total_messages_count(int(GROUP_ID)) == 1


async def test_data_retention_string_value_is_parsed(tmp_path, monkeypatch):
    """字符串形式的配置值（'说明|30' 形态）能正确解析出天数并生效"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin("group_chat_analyzer")
        _seed_messages(plugin, [("555", "旧消息", datetime.now() - timedelta(days=40))])

        plugin.config["DataRetentionDays"] = "保留天数|30"
        await plugin._auto_cleanup_database()
        # 解析出 30 → 40 天前的数据应被清理
        assert plugin.db.get_total_messages_count(int(GROUP_ID)) == 0


async def test_gcpurge_blocked_below_configured_retention(tmp_path, monkeypatch):
    """/gcpurge 保留天数低于 DataRetentionDays 配置值时拒绝执行，数据不删"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin("group_chat_analyzer")
        plugin.config["DataRetentionDays"] = 90
        # 关闭落库：注入的 /gcpurge 命令消息本身也会被消息事件记录，会污染行数断言
        plugin.config["EnableAutoRecord"] = False
        # 40 天前的数据：若 30 天清理被执行会被删除，用于判别拦截是否生效
        _seed_messages(plugin, [("555", "旧消息", datetime.now() - timedelta(days=40))])

        # root 持有全局管理员权限（on_load 自动授权），走严格 RBAC，不查群角色
        await h.inject(group_message("/gcpurge 30", group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle(0.2)
        h.assert_api("send_group_msg").with_text("不能小于配置的最小值 90")
        assert plugin.db.get_total_messages_count(int(GROUP_ID)) == 1


async def test_gcpurge_allowed_above_configured_retention(tmp_path, monkeypatch):
    """/gcpurge 保留天数等于 DataRetentionDays 配置值时正常执行清理"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin("group_chat_analyzer")
        plugin.config["DataRetentionDays"] = 90
        # 关闭落库：避免注入的命令消息本身落库污染行数断言
        plugin.config["EnableAutoRecord"] = False
        _seed_messages(plugin, [
            ("555", "旧消息", datetime.now() - timedelta(days=100)),
            ("555", "新消息", datetime.now() - timedelta(days=10)),
        ])

        await h.inject(group_message("/gcpurge 90", group_id=GROUP_ID, user_id=ROOT_QQ))
        await h.settle(0.2)
        h.assert_api("send_group_msg").with_text("数据清理完成")
        # 100 天前的被删，10 天前的保留
        assert plugin.db.get_total_messages_count(int(GROUP_ID)) == 1


# ---------------------------------------------------------------------------
# AutoSendSummary：自动发送全局总开关
# ---------------------------------------------------------------------------

async def test_auto_send_summary_off_skips_sending(tmp_path, monkeypatch):
    """全局开关关闭（默认）：定时任务入口直接跳过，不调用发群消息 API，任务不被误删"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin("group_chat_analyzer")
        # 默认关闭
        assert plugin.get_config('AutoSendSummary', None) is False

        plan = {
            'interval': 'daily', 'time': '22:00', 'weekday': 0,
            'monthday': 1, 'scope': 0, 'last_send': '',
        }
        plugin.data['auto_summary_plans'][GROUP_ID] = plan
        await plugin._register_auto_summary_plan(GROUP_ID, plan)
        assert f'auto_summary_{GROUP_ID}' in plugin.list_scheduled_tasks()

        # 即使群内有数据（铺数据排除“无数据跳过”干扰），开关关闭也必须短路
        _seed_messages(plugin, [("555", "群聊内容", datetime.now() - timedelta(hours=1))])
        await plugin._auto_summary_handler(GROUP_ID)
        h.assert_api("send_group_msg").not_called()
        assert plan['last_send'] == ''


async def test_auto_send_summary_on_sends_report(tmp_path, monkeypatch):
    """全局开关开启 + 当日未发送：生成综合报告并发送，last_send 写入当日防重复

    图表生成 monkeypatch 为假路径（ForceBase64ImageSend 默认关，image 仅透传不读文件），
    聚焦配置开关门控与发送行为本身。
    """
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin("group_chat_analyzer")
        plugin.config["AutoSendSummary"] = True

        fake_chart = str(tmp_path / 'group_chat_analyzer_chart_200200_report.png')
        monkeypatch.setattr(
            plugin.chart_generator, 'generate_combined_report_chart',
            lambda *a, **k: fake_chart,
        )

        _seed_messages(plugin, [
            ("555", "今天群里讨论了很多内容", datetime.now() - timedelta(hours=1)),
            ("666", "这个话题很有意思", datetime.now() - timedelta(hours=2)),
            ("555", "我再补充一点看法", datetime.now() - timedelta(hours=3)),
        ])
        plan = {
            'interval': 'daily', 'time': '22:00', 'weekday': 0,
            'monthday': 1, 'scope': 0, 'last_send': '',
        }
        plugin.data['auto_summary_plans'][GROUP_ID] = plan

        await plugin._auto_summary_handler(GROUP_ID)

        # 自动发送经 post_group_msg(int gid) → mock 记录 group_id 为 int
        h.assert_api("send_group_msg").called().with_params(group_id=int(GROUP_ID))
        h.assert_api("send_group_msg").with_text("日报已生成")
        # ForceBase64ImageSend 默认关闭：image 段 file 为本地文件路径而非 data URI
        image_ref = _extract_image_file_ref(h)
        assert image_ref == fake_chart
        assert not image_ref.startswith('data:image/png;base64,')
        # 防重复：last_send 写入当日
        assert plan['last_send'] == datetime.now().strftime('%Y-%m-%d')


# ---------------------------------------------------------------------------
# ForceBase64ImageSend：图片发送模式（经 /gcanalyze trend 真实图表链路）
# ---------------------------------------------------------------------------

async def test_image_send_default_is_local_path(tmp_path, monkeypatch):
    """默认（未开启 Base64）：/gcanalyze 经 _send_image 发送的 image 段为本地 .png 路径"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin("group_chat_analyzer")
        # 确认 init_defaults 注册了 False
        assert plugin.get_config('ForceBase64ImageSend', None) is False

        _seed_messages(plugin, [
            ("555", "第一条消息", datetime.now() - timedelta(hours=1)),
            ("666", "第二条消息", datetime.now() - timedelta(hours=2)),
        ])

        await h.inject(
            group_message("/gcanalyze trend 24", group_id=GROUP_ID, user_id=ROOT_QQ)
        )
        await h.settle(0.2)
        h.assert_api("send_group_msg").with_text("消息趋势图已生成")
        image_ref = _extract_image_file_ref(h)
        assert image_ref is not None
        assert not image_ref.startswith('data:image/png;base64,')
        assert image_ref.endswith('.png')


async def test_force_base64_image_send_uses_data_uri(tmp_path, monkeypatch):
    """开启 ForceBase64ImageSend：/gcanalyze 经 _send_image 发送的 image 段为 Base64 data URI"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin("group_chat_analyzer")
        plugin.config["ForceBase64ImageSend"] = True

        _seed_messages(plugin, [
            ("555", "第一条消息", datetime.now() - timedelta(hours=1)),
            ("666", "第二条消息", datetime.now() - timedelta(hours=2)),
            ("555", "第三条消息", datetime.now() - timedelta(hours=3)),
        ])

        await h.inject(
            group_message("/gcanalyze trend 24", group_id=GROUP_ID, user_id=ROOT_QQ)
        )
        await h.settle(0.2)
        h.assert_api("send_group_msg").with_text("消息趋势图已生成")
        image_ref = _extract_image_file_ref(h)
        assert image_ref is not None
        assert image_ref.startswith('data:image/png;base64,')
        # Base64 内容非空（图表文件真实生成并被读取编码）
        assert len(image_ref) > len('data:image/png;base64,') + 100
