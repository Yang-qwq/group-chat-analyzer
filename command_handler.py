# -*- coding: utf-8 -*-
import base64
import shlex

from ncatbot.core import BaseMessage, GroupMessage, PrivateMessage, MessageChain, Image
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
            message_chain = [caption]
            if self.config.get('ForceBase64ImageSend', False):
                with open(chart_path, 'rb') as f:
                    image_data = f.read()
                chart_b64 = base64.b64encode(image_data).decode('utf-8')
                message_chain.append(Image('data:image/png;base64,' + chart_b64))
            else:
                message_chain.append(Image(chart_path))
            await event.reply(rtf=MessageChain(message_chain))
        except Exception as e:
            _log.error(f'发送图片失败: {e}')
            await event.reply_text(f'发送图片失败: {e}')

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
        if event.message_type != 'group':
            return
        try:
            result = await self.api.get_group_member_list(event.group_id, no_cache=True)
            data = result.get('data') if isinstance(result, dict) else result
            if not data:
                return
            for member in data:
                uid = member.get('user_id')
                nickname = member.get('nickname') or member.get('card') or str(uid)
                if uid:
                    self.db.save_user_name(uid, nickname)
        except Exception as e:
            _log.warning(f'刷新用户名缓存失败: {e}')

    async def handle_analyze_command(self, event: BaseMessage | GroupMessage | PrivateMessage,
                                     command: list) -> None:
        """处理 /gcanalyze 命令，生成群聊分析图表

        :param event: 消息事件
        :param command: 命令参数列表
        :return: None
        """
        if event.message_type != 'group':
            await event.reply_text('该命令仅在群聊中可用')
            return

        if len(command) > 1 and command[1] == 'help':
            await event.reply_text(ANALYZE_HELP_TEXT)
            return

        group_id = event.group_id
        subcommand = None
        hours = 24
        days = 7
        max_words = 100

        if len(command) > 1:
            if command[1] in ('trend', 'hourly', 'daily', 'ranking', 'wordcloud'):
                subcommand = command[1]
                if len(command) > 2:
                    try:
                        if subcommand in ('trend', 'hourly', 'ranking', 'wordcloud'):
                            hours = int(command[2])
                            if not self._validate_hours(hours):
                                await event.reply_text('小时数必须在 1-8760 之间')
                                return
                            if subcommand == 'wordcloud' and len(command) > 3:
                                max_words = int(command[3])
                                if max_words < 1 or max_words > 1000:
                                    await event.reply_text('最大词数必须在 1-1000 之间')
                                    return
                        elif subcommand == 'daily':
                            days = int(command[2])
                            if not self._validate_days(days):
                                await event.reply_text('天数必须在 1-365 之间')
                                return
                    except ValueError:
                        await event.reply_text('时间参数必须是有效的数字')
                        return
            else:
                try:
                    hours = int(command[1])
                    if not self._validate_hours(hours):
                        await event.reply_text('小时数必须在 1-8760 之间')
                        return
                except ValueError:
                    await event.reply_text('无效的参数，请使用 /gcanalyze help 查看帮助')
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
                await event.reply_text('数据库中尚无群聊消息记录，请确保已开启消息监听并收到过消息')
            else:
                await event.reply_text(empty_hint)
            return

        await event.reply_text(f'正在生成分析图表，请稍候...')
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
                    await event.reply_text('生成综合分析报告失败')
                return

            if subcommand == 'trend':
                messages = self.db.get_messages(group_id, hours)
                chart_path = self.chart_generator.generate_message_trend_chart(messages, group_id, hours)
                if chart_path:
                    await self._send_image(event, chart_path, '消息趋势图已生成')
                else:
                    await event.reply_text('生成消息趋势图失败')
                return

            if subcommand == 'hourly':
                hourly_data = self.db.get_hourly_activity(group_id, hours)
                chart_path = self.chart_generator.generate_hourly_heatmap(hourly_data, group_id, hours)
                if chart_path:
                    await self._send_image(event, chart_path, '时段活跃度图已生成')
                else:
                    await event.reply_text('生成时段活跃度图失败')
                return

            if subcommand == 'daily':
                daily_data = self.db.get_daily_activity(group_id, days)
                chart_path = self.chart_generator.generate_daily_activity_chart(daily_data, group_id, days)
                if chart_path:
                    await self._send_image(event, chart_path, '日活跃度趋势图已生成')
                else:
                    await event.reply_text('生成日活跃度趋势图失败')
                return

            if subcommand == 'ranking':
                user_stats = self.db.get_most_active_users(group_id, hours, 10)
                chart_path = self.chart_generator.generate_user_ranking_chart(user_stats, group_id, hours)
                if chart_path:
                    await self._send_image(event, chart_path, '发言排行榜已生成')
                else:
                    await event.reply_text('生成发言排行榜失败')
                return

            if subcommand == 'wordcloud':
                messages = self.db.get_messages(group_id, hours)
                chart_path = self.chart_generator.generate_wordcloud_chart(
                    messages, group_id, hours, max_words)
                if chart_path:
                    await self._send_image(event, chart_path, '词云图已生成')
                else:
                    await event.reply_text('生成词云图失败，可能最近消息内容不足以生成词云')
                return

        except Exception as e:
            _log.error(f'生成分析图表时发生错误: {e}')
            await event.reply_text(f'生成分析图表时发生错误: {e}')

    async def handle_stats_command(self, event: BaseMessage | GroupMessage | PrivateMessage,
                                    command: list) -> None:
        """处理 /gcstats 命令，查看群聊统计信息

        :param event: 消息事件
        :param command: 命令参数列表
        :return: None
        """
        if event.message_type != 'group':
            await event.reply_text('该命令仅在群聊中可用')
            return

        if len(command) > 1 and command[1] == 'help':
            await event.reply_text(STATS_HELP_TEXT)
            return

        group_id = event.group_id

        try:
            hours = int(command[1]) if len(command) > 1 else 24
            if not self._validate_hours(hours):
                await event.reply_text('小时数必须在 1-8760 之间')
                return
        except ValueError:
            await event.reply_text('小时数必须是有效的数字')
            return

        await self._refresh_user_names(event)

        try:
            total_messages = self.db.get_total_messages_count(group_id)
            recent_messages = self.db.get_message_count(group_id, hours)
            recent_active = self.db.get_active_users_count(group_id, hours)
            user_stats = self.db.get_most_active_users(group_id, hours, 5)
        except Exception as e:
            _log.error(f'获取统计信息失败: {e}')
            await event.reply_text('获取统计信息时发生错误，请稍后重试')
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

        await event.reply_text('\n'.join(stats_lines))

    async def handle_top_command(self, event: BaseMessage | GroupMessage | PrivateMessage,
                                  command: list) -> None:
        """处理 /gctop 命令，查看发言排行榜文本版

        :param event: 消息事件
        :param command: 命令参数列表
        :return: None
        """
        if event.message_type != 'group':
            await event.reply_text('该命令仅在群聊中可用')
            return

        if len(command) > 1 and command[1] == 'help':
            await event.reply_text(TOP_HELP_TEXT)
            return

        group_id = event.group_id

        try:
            limit = int(command[1]) if len(command) > 1 else 10
            hours = int(command[2]) if len(command) > 2 else 24
        except ValueError:
            await event.reply_text('参数必须是有效的数字')
            return

        if limit < 1 or limit > 50:
            await event.reply_text('显示数量必须在 1-50 之间')
            return
        if not self._validate_hours(hours):
            await event.reply_text('小时数必须在 1-8760 之间')
            return

        await self._refresh_user_names(event)

        try:
            user_stats = self.db.get_most_active_users(group_id, hours, limit)
        except Exception as e:
            _log.error(f'获取排行榜数据失败: {e}')
            await event.reply_text('获取排行榜数据时发生错误，请稍后重试')
            return

        if not user_stats:
            await event.reply_text(f'最近 {hours} 小时内无消息记录')
            return

        lines = [f'🏆 发言排行榜 TOP{min(limit, len(user_stats))}（最近 {hours} 小时）：', '']
        medals = ['🥇', '🥈', '🥉']
        for i, user in enumerate(user_stats, 1):
            medal = medals[i - 1] if i <= 3 else f'{i}.'
            lines.append(f'  {medal} {user["user_name"]} — {user["message_count"]} 条消息')

        await event.reply_text('\n'.join(lines))

    async def handle_monthly_command(self, event: BaseMessage | GroupMessage | PrivateMessage,
                                     command: list) -> None:
        """处理 /gcmonthly 命令，生成月度活跃热力图

        :param event: 消息事件
        :param command: 命令参数列表
        :return: None
        """
        if event.message_type != 'group':
            await event.reply_text('该命令仅在群聊中可用')
            return

        if len(command) > 1 and command[1] == 'help':
            await event.reply_text(MONTHLY_HELP_TEXT)
            return

        group_id = event.group_id

        try:
            months = int(command[1]) if len(command) > 1 else 6
        except ValueError:
            await event.reply_text('月数必须是有效的数字')
            return

        if months < 1 or months > 12:
            await event.reply_text('月数必须在 1-12 之间')
            return

        total_messages = self.db.get_total_messages_count(group_id)
        if total_messages == 0:
            await event.reply_text('数据库中尚无群聊消息记录，请确保已开启消息监听并收到过消息')
            return

        await event.reply_text(f'正在生成最近 {months} 个月的活跃热力图，请稍候...')

        try:
            daily_data = self.db.get_daily_activity(group_id, months * 30)
            chart_path = self.chart_generator.generate_monthly_heatmap_chart(
                daily_data, group_id, months)
            if chart_path:
                await self._send_image(event, chart_path, '月度活跃热力图已生成')
            else:
                await event.reply_text('生成月度活跃热力图失败，可能数据量不足')
        except Exception as e:
            _log.error(f'生成月度热力图时发生错误: {e}')
            await event.reply_text(f'生成月度热力图时发生错误: {e}')

    async def handle_purge_command(self, event: BaseMessage | GroupMessage | PrivateMessage,
                                    command: list) -> None:
        """处理 /gcpurge 命令，清理旧数据

        :param event: 消息事件
        :param command: 命令参数列表
        :return: None
        """
        if len(command) > 1 and command[1] == 'help':
            await event.reply_text(PURGE_HELP_TEXT)
            return

        try:
            if len(command) < 2:
                days = 30
            else:
                try:
                    days = int(command[1])
                except ValueError:
                    await event.reply_text('保留天数必须是有效的数字')
                    return

            if days < 1:
                await event.reply_text('保留天数必须大于 0')
                return
            if days > 365:
                await event.reply_text('保留天数不能超过 365 天')
                return

            config_retention = self.config.get('DataRetentionDays', 0)
            if isinstance(config_retention, str):
                config_retention = int(config_retention.split('|')[-1])
            if config_retention > 0 and days < config_retention:
                await event.reply_text(
                    f'保留天数不能小于配置的最小值 {config_retention} 天，'
                    f'请使用 /gcpurge {config_retention} 或更大的数值')
                return

            await event.reply_text(f'正在清理超过 {days} 天的旧数据，请稍候...')
            deleted_count = self.db.cleanup_old_data(days)

            lines = [
                f'✅ 数据清理完成！',
                f'• 清理记录数：{deleted_count} 条',
                f'• 保留天数：{days} 天'
            ]
            await event.reply_text('\n'.join(lines))

        except Exception as e:
            _log.error(f'清理数据时发生错误: {e}')
            await event.reply_text(f'清理数据时发生错误: {e}')

    async def handle_db_command(self, event: BaseMessage | GroupMessage | PrivateMessage) -> None:
        """处理 /gcdb 命令，查看数据库统计

        :param event: 消息事件
        :return: None
        """
        try:
            raw = event.raw_message.strip()
            if raw.lower().endswith('help') or raw == '/gcdb help':
                await event.reply_text(DB_HELP_TEXT)
                return

            if event.message_type != 'group':
                total_messages = self.db.get_total_messages_count(0)
                db_size_mb = self.db.get_db_size_mb()
                lines = [
                    f'📊 数据库统计信息：',
                    f'',
                    f'📝 总消息数：{total_messages} 条',
                    f'  • 数据库大小：{db_size_mb} MB',
                    f'  • 数据库路径：{self.db.db_path}',
                ]
                await event.reply_text('\n'.join(lines))
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
            await event.reply_text('\n'.join(lines))

        except Exception as e:
            _log.error(f'获取数据库统计失败: {e}')
            await event.reply_text(f'获取数据库统计失败: {e}')

    async def handle_autosend_command(self, event: BaseMessage | GroupMessage | PrivateMessage,
                                       command: list) -> None:
        """处理 /gcautosend 命令，配置群聊自动发送计划

        :param event: 消息事件
        :param command: 命令参数列表
        :return: None
        """
        if event.message_type != 'group':
            await event.reply_text('该命令仅在群聊中可用')
            return

        group_id = str(event.group_id)
        plans = self.data['data'].setdefault('auto_summary_plans', {})

        if len(command) < 2 or command[1] == 'help':
            await event.reply_text(AUTO_SEND_HELP_TEXT)
            return

        subcommand = command[1]

        if subcommand == 'set':
            args = command[2:]

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
                self.data.save()
                await event.reply_text('✅ 已为本群创建自动发送计划（每日 22:00 发送日报）')
                return

            # key value 成对解析
            if len(args) % 2 != 0:
                await event.reply_text('参数格式错误，请使用 key value 成对传入')
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
                        await event.reply_text(f'无效周期: {value}，可选 daily/weekly/monthly')
                        return
                    plan['interval'] = value
                    plan['last_send'] = ''
                    changed.append(f'周期={value}')

                elif key == 'time':
                    if ':' not in value:
                        await event.reply_text('无效时间格式，请使用 HH:MM（如 22:00）')
                        return
                    parts = value.split(':')
                    if len(parts) != 2:
                        await event.reply_text('无效时间格式，请使用 HH:MM（如 22:00）')
                        return
                    try:
                        h, m = int(parts[0]), int(parts[1])
                    except ValueError:
                        await event.reply_text('无效时间格式，请使用 HH:MM（如 22:00）')
                        return
                    if h < 0 or h > 23 or m < 0 or m > 59:
                        await event.reply_text('无效时间格式，请使用 HH:MM（如 22:00）')
                        return
                    normalized = f'{h:02d}:{m:02d}'
                    plan['time'] = normalized
                    plan['last_send'] = ''
                    changed.append(f'时间={normalized}')

                elif key == 'weekday':
                    try:
                        wd = int(value)
                    except (ValueError, TypeError):
                        await event.reply_text('weekday 必须是 0-6 的整数（0=周一）')
                        return
                    if wd < 0 or wd > 6:
                        await event.reply_text('weekday 必须是 0-6 的整数（0=周一）')
                        return
                    plan['weekday'] = wd
                    plan['last_send'] = ''
                    changed.append(f'周几={wd}')

                elif key == 'monthday':
                    try:
                        md = int(value)
                    except (ValueError, TypeError):
                        await event.reply_text('monthday 必须是 1-31 的整数')
                        return
                    if md < 1 or md > 31:
                        await event.reply_text('monthday 必须是 1-31 的整数')
                        return
                    plan['monthday'] = md
                    plan['last_send'] = ''
                    changed.append(f'每月第{md}日')

                elif key == 'scope':
                    try:
                        s = int(value)
                    except (ValueError, TypeError):
                        await event.reply_text('scope 必须是非负整数（小时数，0=自动）')
                        return
                    if s < 0:
                        await event.reply_text('scope 必须是非负整数（小时数，0=自动）')
                        return
                    plan['scope'] = s
                    changed.append(f'统计范围={"自动" if s == 0 else f"{s}h"}')

                else:
                    await event.reply_text(f'未知参数: {key}，支持 interval/time/weekday/monthday/scope')
                    return

            await self._register_auto_summary_plan(group_id, plan)
            self.data.save()
            await event.reply_text(f'✅ 已更新自动发送计划: {", ".join(changed)}')

        elif subcommand == 'remove':
            if group_id not in plans:
                await event.reply_text('❌ 本群未设置自动发送计划')
                return
            del plans[group_id]
            self.remove_scheduled_task(f'auto_summary_{group_id}')
            self.data.save()
            await event.reply_text('✅ 已删除本群自动发送计划')

        elif subcommand == 'status':
            if group_id not in plans:
                await event.reply_text('本群未设置自动发送计划')
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

            await event.reply_text('\n'.join(lines))

        else:
            await event.reply_text(AUTO_SEND_HELP_TEXT)

    async def admin_command_handler(self, event: BaseMessage | GroupMessage | PrivateMessage):
        """处理管理员命令事件

        :param event: 消息事件
        :return: None
        """
        replaced_message = event.raw_message.replace('\\\\n', '\n')

        try:
            command = shlex.split(replaced_message)
        except ValueError as e:
            _log.warning(f'管理员命令解析失败: {e}')
            await event.reply_text('命令格式错误，请检查引号是否匹配！')
            return

        if not command:
            return

        try:
            if command[0] == '/gcpurge':
                await self.handle_purge_command(event, command)
            elif command[0] == '/gcdb':
                await self.handle_db_command(event)
            elif command[0] == '/gcautosend':
                await self.handle_autosend_command(event, command)
        except Exception as e:
            _log.error(f'处理管理员命令时发生错误: {e}')
            await event.reply_text('处理命令时发生错误，请稍后重试')

    async def user_command_handler(self, event: BaseMessage | GroupMessage | PrivateMessage):
        """处理用户命令事件

        :param event: 消息事件
        :return: None
        """
        replaced_message = event.raw_message.replace('\\\\n', '\n')

        try:
            command = shlex.split(replaced_message)
        except ValueError as e:
            _log.warning(f'用户命令解析失败: {e}')
            await event.reply_text('命令格式错误，请检查引号是否匹配！')
            return

        if not command:
            return

        try:
            if command[0] == '/gcanalyze':
                await self.handle_analyze_command(event, command)
            elif command[0] == '/gcstats':
                await self.handle_stats_command(event, command)
            elif command[0] == '/gctop':
                await self.handle_top_command(event, command)
            elif command[0] == '/gcmonthly':
                await self.handle_monthly_command(event, command)
            else:
                return
        except Exception as e:
            _log.error(f'处理用户命令时发生错误: {e}')
            await event.reply_text('处理命令时发生错误，请稍后重试')
