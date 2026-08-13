# -*- coding: utf-8 -*-
import datetime
import os
from functools import partial

from ncatbot.core import registrar
from ncatbot.event.qq import GroupMessageEvent
from ncatbot.plugin import NcatBotPlugin
from ncatbot.utils import get_config_manager
from ncatbot.utils.logger import get_log

from .command_handler import GroupChatAnalyzerCommandMixin
from .database import DatabaseManager
from .analyzer import ChartGenerator

_log = get_log('group_chat_analyzer')


class GroupChatAnalyzerPlugin(GroupChatAnalyzerCommandMixin, NcatBotPlugin):
    """群聊内容分析插件

    自动记录群聊消息到 SQLite 数据库，支持生成多种分析图表。
    支持按群配置每日/每周/每月自动发送群聊分析总结。
    """
    name = 'group_chat_analyzer'
    version = '0.1.0'
    author = 'Yang-qwq'
    description = '群聊内容分析插件：自动记录群消息到 SQLite，生成统计图表与自动发送总结'

    @staticmethod
    def get_system_temp_dir() -> str | os.PathLike:
        """获取系统临时目录路径

        :return: 系统临时目录路径
        """
        system_temp_dir = os.getenv('TEMP') or os.getenv('TMP') or os.getenv('TMPDIR') or '/tmp'
        if not system_temp_dir.endswith(os.sep):
            system_temp_dir += os.sep
        return system_temp_dir

    async def on_load(self):
        """插件加载时的初始化"""
        try:
            # 注册配置默认值
            self.init_defaults({
                'EnableAutoRecord': True,
                'ForceBase64ImageSend': False,
                'DataRetentionDays': 30,
                'AutoSendSummary': False,
                'EnableGroupOwnerAutoAuth': True,
            })

            # 注册管理命令所需 RBAC 权限
            self.add_permission('group_chat_analyzer.admin')

            # 自动授予 root（config.yaml 中机器人 owner）全局管理员权限（幂等）
            root = get_config_manager().config.root
            if root and self.rbac and not self.check_permission(
                    root, 'group_chat_analyzer.admin'):
                self.rbac.grant('user', root, 'group_chat_analyzer.admin')
                _log.info(f'已自动授予 root({root}) 插件全局管理员权限')

            # 初始化数据库（位于插件工作区 data/group_chat_analyzer/）
            self.db = DatabaseManager(str(self.workspace / 'group_chat_data.db'))

            # 初始化图表生成器（输出到系统临时目录）
            self.chart_generator = ChartGenerator(self.get_system_temp_dir())

            # 初始化持久化数据
            self.data.setdefault('auto_summary_plans', {})

            # 注册自动清理任务（每 6 小时执行一次）
            self.add_scheduled_task(
                'group_chat_cleanup',
                21600,
                callback=self._auto_cleanup_database,
            )

            # 重新注册已持久化的自动发送计划
            for gid, plan in self.data.get('auto_summary_plans', {}).items():
                await self._register_auto_summary_plan(gid, plan)
                _log.debug(f'已恢复群({gid})自动发送计划：{plan["interval"]} {plan["time"]}')

            _log.debug(f'数据库路径: {self.db.db_path}')
            _log.debug(f'图表输出目录: {self.get_system_temp_dir()}')

        except Exception as e:
            _log.error(f'插件加载初始化失败: {e}')
            raise

    @registrar.qq.on_group_message()
    async def on_group_message(self, event: GroupMessageEvent):
        """处理群消息事件

        保存消息到数据库，供后续分析使用。

        :param event: 群消息事件
        :return: None
        """
        try:
            if self.get_config('EnableAutoRecord', True):
                self.db.save_user_name(
                    int(event.sender.user_id),
                    event.sender.nickname or str(event.sender.user_id),
                )
                self.db.increment_user_message_count(int(event.sender.user_id))
                self.db.save_message(
                    group_id=int(event.group_id),
                    user_id=int(event.sender.user_id),
                    message=event.raw_message,
                )
        except Exception as e:
            _log.error(f'保存群消息失败: {e}')

    async def _auto_cleanup_database(self):
        """自动清理过期数据，根据 DataRetentionDays 配置执行"""
        try:
            retention_days = self.get_config('DataRetentionDays', 0)
            if isinstance(retention_days, str):
                retention_days = int(retention_days.split('|')[-1])
            if retention_days > 0:
                deleted = self.db.cleanup_old_data(retention_days)
                if deleted > 0:
                    _log.info(f'自动清理完成：已删除 {deleted} 条超过 {retention_days} 天的数据')
        except Exception as e:
            _log.error(f'自动清理数据失败: {e}')

    async def _register_auto_summary_plan(self, group_id: str | int, plan: dict):
        """注册或重注册某个群的定时发送计划

        先移除已有同名任务，再以 plan 中的 time 注册新任务，
        任务触发后会进入 _auto_summary_handler 做周期检查。

        :param group_id: 群号
        :param plan: 计划配置字典
        """
        task_name = f'auto_summary_{group_id}'
        self.remove_scheduled_task(task_name)

        # v5 无 args 参数，通过 partial 绑定群号
        self.add_scheduled_task(
            task_name,
            plan['time'],
            callback=partial(self._auto_summary_handler, str(group_id)),
        )
        _log.info(f'已注册群({group_id})自动发送计划：{plan["interval"]} {plan["time"]}')

    async def _auto_summary_handler(self, group_id: str):
        """定时任务触发时的入口，检查各条件后执行自动发送

        :param group_id: 群号
        """
        try:
            if not self.get_config('AutoSendSummary', False):
                _log.debug(f'群({group_id})自动发送已关闭（全局开关），跳过')
                return

            plans = self.data.get('auto_summary_plans', {})
            plan = plans.get(group_id)
            if not plan:
                _log.debug(f'群({group_id})无自动发送计划，跳过')
                self.remove_scheduled_task(f'auto_summary_{group_id}')
                return

            now = datetime.datetime.now()
            interval = plan['interval']
            last_send = plan.get('last_send', '')

            # 按周期检查是否应发送
            if interval == 'weekly':
                weekday = plan.get('weekday', 0)
                if now.weekday() != weekday:
                    return
                current_period = f'{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}'
                if last_send == current_period:
                    return
            elif interval == 'monthly':
                monthday = plan.get('monthday', 1)
                if now.day != monthday:
                    return
                current_period = now.strftime('%Y-%m')
                if last_send == current_period:
                    return
            else:
                current_period = now.strftime('%Y-%m-%d')
                if last_send == current_period:
                    return

            # 确定统计范围
            scope = plan.get('scope', 0)
            if scope <= 0:
                scope = {'daily': 24, 'weekly': 168, 'monthly': 720}.get(interval, 24)

            sent = await self._execute_auto_send_summary(group_id, scope, interval)
            if sent:
                plan['last_send'] = current_period
                self._save_data()
                _log.info(f'群({group_id})自动{interval}总结发送完成')
            else:
                _log.debug(f'群({group_id})自动{interval}总结条件不满足，跳过')

        except Exception as e:
            _log.error(f'群({group_id})自动发送总结失败: {e}')

    async def _execute_auto_send_summary(self, group_id: str, hours: int,
                                         interval: str) -> bool:
        """执行自动发送总结：查询数据、生成图表、发送到群

        :param group_id: 群号
        :param hours: 统计范围小时数
        :param interval: 发送周期（仅用于文案）
        :return: 是否成功发送
        """
        gid = int(group_id)
        interval_names = {'daily': '日报', 'weekly': '周报', 'monthly': '月报'}
        period_name = interval_names.get(interval, '总结')

        try:
            messages = self.db.get_messages(gid, hours)
            total = len(messages)
            if total == 0:
                _log.debug(f'群({group_id})最近{hours}小时内无消息，跳过自动发送')
                return False

            hourly_data = self.db.get_hourly_activity(gid, hours)
            daily_data = self.db.get_daily_activity(gid, max(1, min(hours // 24, 7)))
            user_stats = self.db.get_most_active_users(gid, hours, 10)
            monthly_data = self.db.get_daily_activity(gid, 180)

            chart_path = self.chart_generator.generate_combined_report_chart(
                messages, hourly_data, daily_data, user_stats, gid, hours,
                monthly_data=monthly_data,
            )

            if not chart_path:
                _log.error(f'群({group_id})自动总结生成图表失败')
                return False

            caption = f'📊 群聊分析{period_name}已生成（最近 {hours} 小时，共 {total} 条消息）'

            if self.get_config('ForceBase64ImageSend', False):
                import base64
                with open(chart_path, 'rb') as f:
                    image_data = f.read()
                image_ref = 'data:image/png;base64,' + base64.b64encode(image_data).decode('utf-8')
            else:
                image_ref = chart_path

            await self.api.qq.post_group_msg(gid, text=caption, image=image_ref)
            _log.info(f'已向群({group_id})发送{period_name}（{total} 条消息）')
            return True

        except Exception as e:
            _log.error(f'群({group_id})自动发送总结执行失败: {e}')
            return False
