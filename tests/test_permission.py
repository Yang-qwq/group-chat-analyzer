"""权限系统测试

覆盖三层授权机制：
1. root（config.yaml 中机器人 owner）启动时自动获得全局管理员权限
2. /gcrbac 命令授予/撤销/查看全局管理员（仅全局管理员/root 可用）
3. 群主/群管理自动放行本群管理员命令（受 EnableGroupOwnerAutoAuth 开关控制）

全部用例经框架 MockAdapter 驱动：事件注入走真实分发链路，API 调用通过
`assert_api()` / `mock.call_count()` 做精确断言（如 get_group_member_list 查询、
300s 角色缓存命中、/gcrbac 严格路径不查询群角色）。

运行：python -m pytest tests -v -o "addopts="
"""
from pathlib import Path

import pytest
from ncatbot.testing import PluginTestHarness, group_message
from ncatbot.types.napcat.group import GroupMemberInfo

pytestmark = pytest.mark.asyncio(mode="strict")

# 插件根目录的上一级（仓库 plugins/ 目录，内含本插件 manifest.toml）
PLUGINS_DIR = Path(__file__).resolve().parents[2]
ROOT_QQ = "123456"
PERM = "group_chat_analyzer.admin"
GROUP_ID = "200200"


def _harness() -> PluginTestHarness:
    """构造隔离的插件测试环境（不启动，由调用方 async with / start）"""
    return PluginTestHarness(
        plugin_names=["group_chat_analyzer"],
        plugins_dir=PLUGINS_DIR,
    )


def _member_list(roles):
    """构造群成员列表（user_id -> role）"""
    return [
        GroupMemberInfo(user_id=uid, role=role, group_id=GROUP_ID)
        for uid, role in roles.items()
    ]


async def test_on_load_grants_root_admin(tmp_path, monkeypatch):
    """root 在插件加载后自动获得全局管理员权限，非 root 无权限"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin("group_chat_analyzer")
        assert plugin is not None
        assert plugin.check_permission(ROOT_QQ, PERM) is True
        assert plugin.check_permission("999999", PERM) is False


async def test_gcrbac_grant_revoke_list(tmp_path, monkeypatch):
    """root 可通过 /gcrbac 授予/撤销/查看全局管理员"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin("group_chat_analyzer")

        # root 授予 888888
        await h.inject(
            group_message("/gcrbac grant 888888", group_id=GROUP_ID, user_id=ROOT_QQ)
        )
        await h.settle()
        assert plugin.check_permission("888888", PERM) is True
        h.assert_api("send_group_msg").with_params(group_id=GROUP_ID).with_text(
            "已授予 888888"
        )

        h.reset_api()

        # 重复授予给出提示
        await h.inject(
            group_message("/gcrbac grant 888888", group_id=GROUP_ID, user_id=ROOT_QQ)
        )
        await h.settle()
        h.assert_api("send_group_msg").with_text("已是全局管理员")

        # list 能看到 root 与 888888
        h.reset_api()
        await h.inject(
            group_message("/gcrbac list", group_id=GROUP_ID, user_id=ROOT_QQ)
        )
        await h.settle()
        h.assert_api("send_group_msg").with_text("全局管理员列表").with_text(
            ROOT_QQ, "888888"
        )

        # 撤销 888888
        h.reset_api()
        await h.inject(
            group_message("/gcrbac revoke 888888", group_id=GROUP_ID, user_id=ROOT_QQ)
        )
        await h.settle()
        assert plugin.check_permission("888888", PERM) is False
        h.assert_api("send_group_msg").with_text("已撤销 888888")

        # /gcrbac 全程为严格 RBAC 路径，不查询群成员列表
        h.assert_api("get_group_member_list").not_called()


async def test_gcrbac_grant_revoke_with_at_component(tmp_path, monkeypatch):
    """/gcrbac 支持 @ 成员（CQ:at 组件）形式授予/撤销全局管理员"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin("group_chat_analyzer")

        # At 组件（含 name 附加参数）授予
        await h.inject(
            group_message(
                "/gcrbac grant",
                group_id=GROUP_ID,
                user_id=ROOT_QQ,
                raw_message="/gcrbac grant [CQ:at,qq=888888]",
                message=[
                    {"type": "text", "data": {"text": "/gcrbac grant "}},
                    {"type": "at", "data": {"qq": "888888"}},
                ],
            )
        )
        await h.settle()
        assert plugin.check_permission("888888", PERM) is True
        h.assert_api("send_group_msg").with_text("已授予 888888")

        h.reset_api()

        # At 组件撤销
        await h.inject(
            group_message(
                "/gcrbac revoke",
                group_id=GROUP_ID,
                user_id=ROOT_QQ,
                raw_message="/gcrbac revoke [CQ:at,qq=888888]",
                message=[
                    {"type": "text", "data": {"text": "/gcrbac revoke "}},
                    {"type": "at", "data": {"qq": "888888"}},
                ],
            )
        )
        await h.settle()
        assert plugin.check_permission("888888", PERM) is False
        h.assert_api("send_group_msg").with_text("已撤销 888888")
        # 严格 RBAC 路径不查询群角色
        h.assert_api("get_group_member_list").not_called()


async def test_gcrbac_at_all_and_invalid_rejected(tmp_path, monkeypatch):
    """@全体成员与非数字/非法 At 目标无法被授予，且不产生授权"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin("group_chat_analyzer")

        # @全体成员（qq=all）不可授予
        await h.inject(
            group_message(
                "/gcrbac grant",
                group_id=GROUP_ID,
                user_id=ROOT_QQ,
                raw_message="/gcrbac grant [CQ:at,qq=all]",
            )
        )
        await h.settle()
        h.assert_api("send_group_msg").with_text("纯数字或 @ 成员")
        assert plugin.check_permission("all", PERM) is False

        h.reset_api()

        # 非法文本（非数字、非 At CQ 码）
        await h.inject(
            group_message("/gcrbac grant abc", group_id=GROUP_ID, user_id=ROOT_QQ)
        )
        await h.settle()
        h.assert_api("send_group_msg").with_text("纯数字或 @ 成员")
        assert plugin.check_permission("abc", PERM) is False
        h.assert_api("get_group_member_list").not_called()


async def test_gcrbac_denied_for_non_admin(tmp_path, monkeypatch):
    """非全局管理员无法使用 /gcrbac（不查询群角色，防止群主/群管理越权）"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin("group_chat_analyzer")
        await h.inject(
            group_message("/gcrbac grant 888888", group_id=GROUP_ID, user_id="999999")
        )
        await h.settle()
        assert plugin.check_permission("888888", PERM) is False
        h.assert_api("send_group_msg").with_text("权限不足")
        h.assert_api("get_group_member_list").not_called()


async def test_group_owner_and_admin_can_use_admin_command(tmp_path, monkeypatch):
    """群主/群管理经 MockAdapter 查询群成员角色后自动放行，普通成员被拒"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        mock = h.mock_api_for("qq")
        mock.set_response(
            "get_group_member_list",
            _member_list({"111": "owner", "222": "admin", "333": "member"}),
        )

        # 群主放行：经真实 API 查询群角色，回复群内数据库统计
        await h.inject(group_message("/gcdb", group_id=GROUP_ID, user_id="111"))
        await h.settle()
        h.assert_api("get_group_member_list").called().with_params(group_id=GROUP_ID)
        h.assert_api("send_group_msg").called().with_params(group_id=GROUP_ID).with_text(
            "数据库统计信息"
        )

        # 群管理放行：命中 300s 角色缓存，不再重复请求群成员列表
        await h.inject(group_message("/gcdb", group_id=GROUP_ID, user_id="222"))
        await h.settle()
        assert mock.call_count("get_group_member_list") == 1
        h.assert_api("send_group_msg").with_text("数据库统计信息")

        # 普通成员拒绝：角色为 member，回复权限不足
        await h.inject(group_message("/gcdb", group_id=GROUP_ID, user_id="333"))
        await h.settle()
        assert mock.call_count("get_group_member_list") == 1
        h.assert_api("send_group_msg").with_text("权限不足")


async def test_group_owner_cannot_grant_global_admin(tmp_path, monkeypatch):
    """群主/群管理不能通过 /gcrbac 授予全局管理员（防止提权）"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin("group_chat_analyzer")
        h.mock_api_for("qq").set_response(
            "get_group_member_list", _member_list({"444": "owner"})
        )

        await h.inject(
            group_message("/gcrbac grant 888888", group_id=GROUP_ID, user_id="444")
        )
        await h.settle()
        assert plugin.check_permission("888888", PERM) is False
        h.assert_api("send_group_msg").with_text("权限不足")
        # 即使对方是群主，/gcrbac 也不查询群角色（严格 RBAC 路径）
        h.assert_api("get_group_member_list").not_called()


async def test_group_owner_auto_auth_can_be_disabled(tmp_path, monkeypatch):
    """EnableGroupOwnerAutoAuth 关闭后群主不再自动放行（且不查询群成员列表）"""
    monkeypatch.chdir(tmp_path)
    async with _harness() as h:
        plugin = h.get_plugin("group_chat_analyzer")
        h.mock_api_for("qq").set_response(
            "get_group_member_list", _member_list({"111": "owner"})
        )
        plugin.config["EnableGroupOwnerAutoAuth"] = False

        await h.inject(group_message("/gcdb", group_id=GROUP_ID, user_id="111"))
        await h.settle()
        h.assert_api("send_group_msg").with_text("权限不足")
        h.assert_api("get_group_member_list").not_called()
