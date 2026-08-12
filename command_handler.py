# -*- coding: utf-8 -*-
import base64
import shlex
from typing import Optional

from ncatbot.core import registrar
from ncatbot.event.qq import GroupMessageEvent, MessageEvent
from ncatbot.utils.logger import get_log

_log = get_log('group_chat_analyzer')

ANALYZE_HELP_TEXT = """📊 /gcanalyze - 生成群聊分析图表

用法：
  /gcanalyze [小时数]             生成综合分析报告（默认 24h）
  /gcanalyze trend [小时数]       生成消息趋势图
  /gcanalyze hourly [小时数]      生成时段活跃度图
  /gcanalyze daily [天数]         生成日活跃度趋势图
  /gcanalyze ranking [小时数]     生成发言排行榜
  /gcanalyze wordcloud [小时数] [最大词数]  生成词云图
  /gcanalyze help                 显示本帮助

示例：
  /gcanalyze         最近 24 小时报告
  /gcanalyze 48      最近 48 小时报告
  /gcanalyze trend   消息趋势图
  /gcanalyze wordcloud          生成词云图
  /gcanalyze wordcloud 48 200   最近48小时词云，最大200词"""

STATS_HELP_TEXT = """📊 /gcstats - 查看群聊统计信息

用法：
  /gcstats [小时数]               查看指定时间内的统计（默认 24h）
  /gcstats help                   显示本帮助

示例：
  /gcstats         最近 24 小时统计
  /gcstats 72      最近 72 小时统计"""

TOP_HELP_TEXT = """🏆 /gctop - 查看发言排行榜

用法：
  /gctop [数量] [小时数]          查看发言排行榜（默认前 10 名，24h）
  /gctop help                     显示本帮助

示例：
  /gctop          前 10 名（24h）
  /gctop 5        前 5 名（24h）
  /gctop 10 48    前 10 名（48h）"""

MONTHLY_HELP_TEXT = """📅 /gcmonthly - 生成月度活跃热力图

用法：
  /gcmonthly [月数]               生成月度每日活跃热力图（默认 6 个月）
  /gcmonthly help                 显示本帮助

示例：
  /gcmonthly       最近 6 个月热力图
  /gcmonthly 3     最近 3 个月热力图"""

PURGE_HELP_TEXT = """⚙️ /gcpurge - 清理旧数据（管理员专用）

用法：
  /gcpurge [天数]                 清理指定天数前的数据（默认 30 天）
  /gcpurge help                   显示本帮助

示例：
  /gcpurge        清理 30 天前数据
  /gcpurge 60     清理 60 天前数据"""

DB_HELP_TEXT = """⚙️ /gcdb - 查看数据库统计信息（管理员专用）

用法：
  /gcdb                           查看数据库统计
  /gcdb help                      显示本帮助"""

AUTO_SEND_HELP_TEXT = """⏰ /gcautosend - 配置日/周/月自动发送群聊总结

用法：
  /gcautosend set [key value ...]              创建/更新本群计划
  /gcautosend remove                           删除本群计划
  /gcautosend status                           查看本群计划
  /gcautosend help                             显示本帮助

配置参数（key value 成对传入）：
  interval daily|weekly|monthly                发送周期
  time HH:MM                                   发送时间
  weekday 0-6                                  周几发送（0=周一，仅 weekly）
  monthday 1-31                                每月几日（仅 monthly）
  scope N                                      统计范围小时数（0=自动）

示例：
  /gcautosend set
  /gcautosend set interval weekly time 10:00 weekday 1
  /gcautosend set interval monthly monthday 15
  /gcautosend status"""


class GroupChatAnalyzerCommandMixin:
    """命令处理逻辑 Mixin"""

    async def _send_image(self, event, chart_path: str, caption: str):
        """发送图片消息，支持 Base64 模式

        :param event: 消息事件
        :param chart_path: 图片路径
        :param caption: 图片说明文字
        """
        try:
            image_ref = chart_path
            if self.get_config('ForceBase64ImageSend', False):
                with open(chart_path, 'rb') as f:
                    image_data = f.read()
                chart_b64 = base64.b64encode(image_data).decode('utf-8')
                image_ref = 'data:image/png;base64,' + chart_b64
            await event.reply(text=caption, image=image_ref, at_sender=False)
        except Exception as e:
            _log.error(f'发送图片失败: {e}')
            await event.reply(text=f'发送图片失败: {e}', at_sender=False)

    async def _safe_params(self, event) -> Optional[list]:
        """对消息原始文本做 shlex 分词并去掉命令 token

        :param event: 消息事件
        :return: 参数列表；解析失败时回复错误并返回 None
        """
        try:
            return shlex.split(event.raw_message.replace('\\\\n', '\n'))[1:]
        except ValueError as e:
            _log.warning(f'命令解析失败: {e}')
            await event.reply(text='命令格式错误，请检查引号是否匹配！', at_sender=False)
            return None

    async def _check_admin(self, event) -> bool:
        """校验管理员权限（RBAC）

        :param event: 消息事件
        :return: 是否拥有权限
        """
        if self.check_permission(str(event.user_id), 'group_chat_analyzer.admin'):
            return True
        await event.reply(text='权限不足：该命令需要管理员权限', at_sender=False)
        return False

    @staticmethod
    def _validate_hours(hours: int) -> bool:
        """验证小时数是否在有效范围内

        :param hours: 小时数
        :return: 是否有效
        """
        return 1 <= hours <= 8760

    @staticmethod
    def _validate_days(days: int) -> bool:
        """验证天数是否在有效范围内

        :param days: 天数
        :return: 是否有效
        """
        return 1 <= days <= 365

    async def _refresh_user_names(self, event) -> None:
        """通过 API 获取群成员列表，刷新用户名缓存

        :param event: 消息事件
        """
        if not isinstance(event, GroupMessageEvent):
            return
        try:
            members = await self.api.qq.query.get_group_member_list(event.group_id)
            for member in members:
                uid = member.user_id
                nickname = member.nickname or member.card or str(uid)
                if uid:
                    self.db.save_user_name(uid, nickname)
        except Exception as e:
            _log.warning(f'刷新用户名缓存失败: {e}')

    @registrar.qq.on_command('/gcanalyze')
    async def on_analyze(self, event: MessageEvent):
        """处理 /gcanalyze 命令，生成群聊分析图表

        :param event: 消息事件
        :return: None
        """
        if not isinstance(event, GroupMessageEvent):
            await event.reply(text='该命令仅在群聊中可用', at_sender=False)
            return

        params = await self._safe_params(event)
        if params is None:
            return

        if params and params[0] == 'help':
            await event.reply(text=ANALYZE_HELP_TEXT, at_sender=False)
            return

        group_id = event.group_id
        subcommand = None
        hours = 24
        days = 7
        max_words = 100

        if params:
            if params[0] in ('trend', 'hourly', 'daily', 'ranking', 'wordcloud'):
                subcommand = params[0]
                if len(params) > 1:
                    try:
                        if subcommand in ('trend', 'hourly', 'ranking', 'wordcloud'):
                            hours = int(params[1])
                            if not self._validate_hours(hours):
                                await event.reply(text='小时数必须在 1-8760 之间', at_sender=False)
                                return
                            if subcommand == 'wordcloud' and len(params) > 2:
                                max_words = int(params[2])
                                if max_words < 1 or max_words > 1000:
                                    await event.reply(text='最大词数必须在 1-1000 之间', at_sender=False)
                                    return
                        elif subcommand == 'daily':
                            days = int(params[1])
                            if not self._validate_days(days):
                                await event.reply(text='天数必须在 1-365 之间', at_sender=False)
                                return
                    except ValueError:
                        await event.reply(text='时间参数必须是有效的数字', at_sender=False)
                        return
            else:
                try:
                    hours = int(params[0])
                    if not self._validate_hours(hours):
                        await event.reply(text='小时数必须在 1-8760 之间', at_sender=False)
                        return
                except ValueError:
                    await event.reply(text='无效的参数，请使用 /gcanalyze help 查看帮助', at_sender=False)
                    return

        # daily 子命令按天数检查；其余按小时检查，避免近 24h 无消息却误拒 daily
        if subcommand == 'daily':
            check_hours = days * 24
            empty_hint = f'最近 {days} 天内无消息记录'
        else:
            check_hours = hours
            empty_hint = f'最近 {hours} 小时内无消息记录'

        total_messages = self.db.get_message_count(group_id, check_hours)
        if total_messages == 0:
            all_messages = self.db.get_total_messages_count(group_id)
            if all_messages == 0:
                await event.reply(text='数据库中尚无群聊消息记录，请确保已开启消息监听并收到过消息',
                                   at_sender=False)
            else:
                await event.reply(text=empty_hint, at_sender=False)
            return

        await event.reply(text='正在生成分析图表，请稍候...', at_sender=False)
        await self._refresh_user_names(event)

        try:
            if subcommand is None:
                messages = self.db.get_messages(group_id, hours)
                hourly_data = self.db.get_hourly_activity(group_id, hours)
                # 日趋势天数与统计小时数对齐（至少 1 天，最多 7 天）
                report_days = max(1, min(hours // 24, 7))
                daily_data = self.db.get_daily_activity(group_id, report_days)
                user_stats = self.db.get_most_active_users(group_id, hours, 10)
                monthly_data = self.db.get_daily_activity(group_id, 180)

                chart_path = self.chart_generator.generate_combined_report_chart(
                    messages, hourly_data, daily_data, user_stats, group_id, hours,
                    monthly_data=monthly_data)
                if chart_path:
                    await self._send_image(event, chart_path, '综合分析报告已生成')
                else:
                    await event.reply(text='生成综合分析报告失败', at_sender=False)
                return

            if subcommand == 'trend':
                messages = self.db.get_messages(group_id, hours)
                chart_path = self.chart_generator.generate_message_trend_chart(messages, group_id, hours)
                if chart_path:
                    await self._send_image(event, chart_path, '消息趋势图已生成')
                else:
                    await event.reply(text='生成消息趋势图失败', at_sender=False)
                return

            if subcommand == 'hourly':
                hourly_data = self.db.get_hourly_activity(group_id, hours)
                chart_path = self.chart_generator.generate_hourly_heatmap(hourly_data, group_id, hours)
                if chart_path:
                    await self._send_image(event, chart_path, '时段活跃度图已生成')
                else:
                    await event.reply(text='生成时段活跃度图失败', at_sender=False)
                return

            if subcommand == 'daily':
                daily_data = self.db.get_daily_activity(group_id, days)
                chart_path = self.chart_generator.generate_daily_activity_chart(daily_data, group_id, days)
                if chart_path:
                    await self._send_image(event, chart_path, '日活跃度趋势图已生成')
                else:
                    await event.reply(text='生成日活跃度趋势图失败', at_sender=False)
                return

            if subcommand == 'ranking':
                user_stats = self.db.get_most_active_users(group_id, hours, 10)
                chart_path = self.chart_generator.generate_user_ranking_chart(user_stats, group_id, hours)
                if chart_path:
                    await self._send_image(event, chart_path, '发言排行榜已生成')
                else:
                    await event.reply(text='生成发言排行榜失败', at_sender=False)
                return

            if subcommand == 'wordcloud':
                messages = self.db.get_messages(group_id, hours)
                chart_path = self.chart_generator.generate_wordcloud_chart(
                    messages, group_id, hours, max_words)
                if chart_path:
                    await self._send_image(event, chart_path, '词云图已生成')
                else:
                    await event.reply(text='生成词云图失败，可能最近消息内容不足以生成词云', at_sender=False)
                return

        except Exception as e:
            _log.error(f'生成分析图表时发生错误: {e}')
            await event.reply(text=f'生成分析图表时发生错误: {e}', at_sender=False)

    @registrar.qq.on_command('/gcstats')
    async def on_stats(self, event: MessageEvent):
        """处理 /gcstats 命令，查看群聊统计信息

        :param event: 消息事件
        :return: None
        """
        if not isinstance(event, GroupMessageEvent):
            await event.reply(text='该命令仅在群聊中可用', at_sender=False)
            return

        params = await self._safe_params(event)
        if params is None:
            return

        if params and params[0] == 'help':
            await event.reply(text=STATS_HELP_TEXT, at_sender=False)
            return

        group_id = event.group_id

        try:
            hours = int(params[0]) if params else 24
            if not self._validate_hours(hours):
                await event.reply(text='小时数必须在 1-8760 之间', at_sender=False)
                return
        except ValueError:
            await event.reply(text='小时数必须是有效的数字', at_sender=False)
            return

        await self._refresh_user_names(event)

        try:
            total_messages = self.db.get_total_messages_count(group_id)
            recent_messages = self.db.get_message_count(group_id, hours)
            recent_active = self.db.get_active_users_count(group_id, hours)
            user_stats = self.db.get_most_active_users(group_id, hours, 5)
        except Exception as e:
            _log.error(f'获取统计信息失败: {e}')
            await event.reply(text='获取统计信息时发生错误，请稍后重试', at_sender=False)
            return

        stats_lines = [
            f'📊 群聊统计信息（最近 {hours} 小时）：',
            f'',
            f'消息统计：',
            f'  • 历史总消息数：{total_messages} 条',
            f'  • 最近消息数：{recent_messages} 条',
            f'  • 活跃用户数：{recent_active} 人',
            f'',
        ]

        if user_stats:
            stats_lines.append(f'🏆 发言排行榜 TOP{min(5, len(user_stats))}：')
            for i, user in enumerate(user_stats, 1):
                stats_lines.append(f'  {i}. {user["user_name"]} — {user["message_count"]} 条消息')

        await event.reply(text='\n'.join(stats_lines), at_sender=False)

    @registrar.qq.on_command('/gctop')
    async def on_top(self, event: MessageEvent):
        """处理 /gctop 命令，查看发言排行榜文本版

        :param event: 消息事件
        :return: None
        """
        if not isinstance(event, GroupMessageEvent):
            await event.reply(text='该命令仅在群聊中可用', at_sender=False)
            return

        params = await self._safe_params(event)
        if params is None:
            return

        if params and params[0] == 'help':
            await event.reply(text=TOP_HELP_TEXT, at_sender=False)
            return

        group_id = event.group_id

        try:
            limit = int(params[0]) if params else 10
            hours = int(params[1]) if len(params) > 1 else 24
        except ValueError:
            await event.reply(text='参数必须是有效的数字', at_sender=False)
            return

        if limit < 1 or limit > 50:
            await event.reply(text='显示数量必须在 1-50 之间', at_sender=False)
            return
        if not self._validate_hours(hours):
            await event.reply(text='小时数必须在 1-8760 之间', at_sender=False)
            return

        await self._refresh_user_names(event)

        try:
            user_stats = self.db.get_most_active_users(group_id, hours, limit)
        except Exception as e:
            _log.error(f'获取排行榜数据失败: {e}')
            await event.reply(text='获取排行榜数据时发生错误，请稍后重试', at_sender=False)
            return

        if not user_stats:
            await event.reply(text=f'最近 {hours} 小时内无消息记录', at_sender=False)
            return

        lines = [f'🏆 发言排行榜 TOP{min(limit, len(user_stats))}（最近 {hours} 小时）：', '']
        medals = ['🥇', '🥈', '🥉']
        for i, user in enumerate(user_stats, 1):
            medal = medals[i - 1] if i <= 3 else f'{i}.'
            lines.append(f'  {medal} {user["user_name"]} — {user["message_count"]} 条消息')

        await event.reply(text='\n'.join(lines), at_sender=False)

    @registrar.qq.on_command('/gcmonthly')
    async def on_monthly(self, event: MessageEvent):
        """处理 /gcmonthly 命令，生成月度活跃热力图

        :param event: 消息事件
        :return: None
        """
        if not isinstance(event, GroupMessageEvent):
            await event.reply(text='该命令仅在群聊中可用', at_sender=False)
            return

        params = await self._safe_params(event)
        if params is None:
            return

        if params and params[0] == 'help':
            await event.reply(text=MONTHLY_HELP_TEXT, at_sender=False)
            return

        group_id = event.group_id

        try:
            months = int(params[0]) if params else 6
        except ValueError:
            await event.reply(text='月数必须是有效的数字', at_sender=False)
            return

        if months < 1 or months > 12:
            await event.reply(text='月数必须在 1-12 之间', at_sender=False)
            return

        total_messages = self.db.get_total_messages_count(group_id)
        if total_messages == 0:
            await event.reply(text='数据库中尚无群聊消息记录，请确保已开启消息监听并收到过消息',
                               at_sender=False)
            return

        await event.reply(text=f'正在生成最近 {months} 个月的活跃热力图，请稍候...', at_sender=False)

        try:
            daily_data = self.db.get_daily_activity(group_id, months * 30)
            chart_path = self.chart_generator.generate_monthly_heatmap_chart(
                daily_data, group_id, months)
            if chart_path:
                await self._send_image(event, chart_path, '月度活跃热力图已生成')
            else:
                await event.reply(text='生成月度活跃热力图失败，可能数据量不足', at_sender=False)
        except Exception as e:
            _log.error(f'生成月度热力图时发生错误: {e}')
            await event.reply(text=f'生成月度热力图时发生错误: {e}', at_sender=False)

    @registrar.qq.on_command('/gcpurge')
    async def on_purge(self, event: MessageEvent):
        """处理 /gcpurge 命令，清理旧数据（管理员专用）

        :param event: 消息事件
        :return: None
        """
        if not await self._check_admin(event):
            return

        params = await self._safe_params(event)
        if params is None:
            return

        if params and params[0] == 'help':
            await event.reply(text=PURGE_HELP_TEXT, at_sender=False)
            return

        try:
            if params:
                try:
                    days = int(params[0])
                except ValueError:
                    await event.reply(text='保留天数必须是有效的数字', at_sender=False)
                    return
            else:
                days = 30

            if days < 1:
                await event.reply(text='保留天数必须大于 0', at_sender=False)
                return
            if days > 365:
                await event.reply(text='保留天数不能超过 365 天', at_sender=False)
                return

            config_retention = self.get_config('DataRetentionDays', 0)
            if isinstance(config_retention, str):
                config_retention = int(config_retention.split('|')[-1])
            if config_retention > 0 and days < config_retention:
                await event.reply(
                    text=f'保留天数不能小于配置的最小值 {config_retention} 天，'
                         f'请使用 /gcpurge {config_retention} 或更大的数值',
                    at_sender=False)
                return

            await event.reply(text=f'正在清理超过 {days} 天的旧数据，请稍候...', at_sender=False)
            deleted_count = self.db.cleanup_old_data(days)

            lines = [
                f'✅ 数据清理完成！',
                f'• 清理记录数：{deleted_count} 条',
                f'• 保留天数：{days} 天'
            ]
            await event.reply(text='\n'.join(lines), at_sender=False)

        except Exception as e:
            _log.error(f'清理数据时发生错误: {e}')
            await event.reply(text=f'清理数据时发生错误: {e}', at_sender=False)

    @registrar.qq.on_command('/gcdb')
    async def on_db(self, event: MessageEvent):
        """处理 /gcdb 命令，查看数据库统计（管理员专用）

        :param event: 消息事件
        :return: None
        """
        if not await self._check_admin(event):
            return

        params = await self._safe_params(event)
        if params is None:
            return

        if params and params[0] == 'help':
            await event.reply(text=DB_HELP_TEXT, at_sender=False)
            return

        try:
            if not isinstance(event, GroupMessageEvent):
                total_messages = self.db.get_total_messages_count(0)
                db_size_mb = self.db.get_db_size_mb()
                lines = [
                    f'📊 数据库统计信息：',
                    f'',
                    f'📝 总消息数：{total_messages} 条',
                    f'  • 数据库大小：{db_size_mb} MB',
                    f'  • 数据库路径：{self.db.db_path}',
                ]
                await event.reply(text='\n'.join(lines), at_sender=False)
                return

            group_id = event.group_id
            total = self.db.get_total_messages_count(group_id)

            hourly_active = self.db.get_active_users_count(group_id, 1)
            daily_active = self.db.get_active_users_count(group_id, 24)
            weekly_active = self.db.get_active_users_count(group_id, 168)

            db_size_mb = self.db.get_db_size_mb()

            lines = [
                f'📊 数据库统计信息：',
                f'',
                f'📝 本群消息总数：{total} 条',
                f'',
                f'👥 活跃用户数：',
                f'  • 最近 1 小时：{hourly_active} 人',
                f'  • 最近 24 小时：{daily_active} 人',
                f'  • 最近 7 天：{weekly_active} 人',
                f'',
                f'💾 数据库信息：',
                f'  • 数据库大小：{db_size_mb} MB',
                f'  • 数据库路径：{self.db.db_path}',
            ]
            await event.reply(text='\n'.join(lines), at_sender=False)

        except Exception as e:
            _log.error(f'获取数据库统计失败: {e}')
            await event.reply(text=f'获取数据库统计失败: {e}', at_sender=False)

    @registrar.qq.on_command('/gcautosend')
    async def on_autosend(self, event: MessageEvent):
        """处理 /gcautosend 命令，配置群聊自动发送计划（管理员专用）

        :param event: 消息事件
        :return: None
        """
        if not isinstance(event, GroupMessageEvent):
            await event.reply(text='该命令仅在群聊中可用', at_sender=False)
            return
        if not await self._check_admin(event):
            return

        params = await self._safe_params(event)
        if params is None:
            return

        group_id = str(event.group_id)
        plans = self.data.setdefault('auto_summary_plans', {})

        if len(params) < 1 or params[0] == 'help':
            await event.reply(text=AUTO_SEND_HELP_TEXT, at_sender=False)
            return

        subcommand = params[0]

        if subcommand == 'set':
            args = params[1:]

            # 没有额外参数 -> 用默认值创建
            if not args:
                plans[group_id] = {
                    'interval': 'daily',
                    'time': '22:00',
                    'weekday': 0,
                    'monthday': 1,
                    'scope': 0,
                    'last_send': '',
                }
                await self._register_auto_summary_plan(group_id, plans[group_id])
                self._save_data()
                await event.reply(text='✅ 已为本群创建自动发送计划（每日 22:00 发送日报）',
                                   at_sender=False)
                return

            # key value 成对解析
            if len(args) % 2 != 0:
                await event.reply(text='参数格式错误，请使用 key value 成对传入', at_sender=False)
                return

            if group_id not in plans:
                plans[group_id] = {
                    'interval': 'daily',
                    'time': '22:00',
                    'weekday': 0,
                    'monthday': 1,
                    'scope': 0,
                    'last_send': '',
                }

            plan = plans[group_id]
            changed = []

            for i in range(0, len(args), 2):
                key = args[i]
                value = args[i + 1]

                if key == 'interval':
                    if value not in ('daily', 'weekly', 'monthly'):
                        await event.reply(text=f'无效周期: {value}，可选 daily/weekly/monthly',
                                           at_sender=False)
                        return
                    plan['interval'] = value
                    plan['last_send'] = ''
                    changed.append(f'周期={value}')

                elif key == 'time':
                    if ':' not in value:
                        await event.reply(text='无效时间格式，请使用 HH:MM（如 22:00）', at_sender=False)
                        return
                    parts = value.split(':')
                    if len(parts) != 2:
                        await event.reply(text='无效时间格式，请使用 HH:MM（如 22:00）', at_sender=False)
                        return
                    try:
                        h, m = int(parts[0]), int(parts[1])
                    except ValueError:
                        await event.reply(text='无效时间格式，请使用 HH:MM（如 22:00）', at_sender=False)
                        return
                    if h < 0 or h > 23 or m < 0 or m > 59:
                        await event.reply(text='无效时间格式，请使用 HH:MM（如 22:00）', at_sender=False)
                        return
                    normalized = f'{h:02d}:{m:02d}'
                    plan['time'] = normalized
                    plan['last_send'] = ''
                    changed.append(f'时间={normalized}')

                elif key == 'weekday':
                    try:
                        wd = int(value)
                    except (ValueError, TypeError):
                        await event.reply(text='weekday 必须是 0-6 的整数（0=周一）', at_sender=False)
                        return
                    if wd < 0 or wd > 6:
                        await event.reply(text='weekday 必须是 0-6 的整数（0=周一）', at_sender=False)
                        return
                    plan['weekday'] = wd
                    plan['last_send'] = ''
                    changed.append(f'周几={wd}')

                elif key == 'monthday':
                    try:
                        md = int(value)
                    except (ValueError, TypeError):
                        await event.reply(text='monthday 必须是 1-31 的整数', at_sender=False)
                        return
                    if md < 1 or md > 31:
                        await event.reply(text='monthday 必须是 1-31 的整数', at_sender=False)
                        return
                    plan['monthday'] = md
                    plan['last_send'] = ''
                    changed.append(f'每月第{md}日')

                elif key == 'scope':
                    try:
                        s = int(value)
                    except (ValueError, TypeError):
                        await event.reply(text='scope 必须是非负整数（小时数，0=自动）', at_sender=False)
                        return
                    if s < 0:
                        await event.reply(text='scope 必须是非负整数（小时数，0=自动）', at_sender=False)
                        return
                    plan['scope'] = s
                    changed.append(f'统计范围={"自动" if s == 0 else f"{s}h"}')

                else:
                    await event.reply(
                        text=f'未知参数: {key}，支持 interval/time/weekday/monthday/scope',
                        at_sender=False)
                    return

            await self._register_auto_summary_plan(group_id, plan)
            self._save_data()
            await event.reply(text=f'✅ 已更新自动发送计划: {", ".join(changed)}', at_sender=False)

        elif subcommand == 'remove':
            if group_id not in plans:
                await event.reply(text='❌ 本群未设置自动发送计划', at_sender=False)
                return
            del plans[group_id]
            self.remove_scheduled_task(f'auto_summary_{group_id}')
            self._save_data()
            await event.reply(text='✅ 已删除本群自动发送计划', at_sender=False)

        elif subcommand == 'status':
            if group_id not in plans:
                await event.reply(text='本群未设置自动发送计划', at_sender=False)
                return

            plan = plans[group_id]
            interval_names = {'daily': '日报', 'weekly': '周报', 'monthly': '月报'}
            scope_desc = f'{plan["scope"]}h' if plan.get('scope', 0) > 0 else '自动'
            last_send = plan.get('last_send', '') or '尚未发送'

            lines = [
                f'📋 群聊自动发送计划：',
                f'',
                f'  • 周期：{interval_names.get(plan["interval"], plan["interval"])}',
                f'  • 时间：{plan["time"]}',
            ]
            if plan['interval'] == 'weekly':
                weekday_names = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
                lines.append(f'  • 发送日：{weekday_names[plan.get("weekday", 0)]}')
            elif plan['interval'] == 'monthly':
                lines.append(f'  • 发送日：每月{plan.get("monthday", 1)}日')
            lines.append(f'  • 统计范围：{scope_desc}')
            lines.append(f'  • 上次发送：{last_send}')

            await event.reply(text='\n'.join(lines), at_sender=False)

        else:
            await event.reply(text=AUTO_SEND_HELP_TEXT, at_sender=False)
