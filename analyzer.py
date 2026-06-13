# -*- coding: utf-8 -*-
import datetime
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.font_manager as fm
import matplotlib.colors as mcolors
import numpy as np

from ncatbot.utils.logger import get_log

_log = get_log('group_chat_analyzer')


class ChartGenerator:
    """图表生成器"""

    # Linux 常见中文字体
    CJK_FONTS = [
        'Noto Sans CJK SC',
        'Noto Serif CJK SC',
        'Noto Sans Mono CJK SC',
        'WenQuanYi Zen Hei',
        'WenQuanYi Micro Hei',
        'Microsoft YaHei',
        'SimHei',
        'PingFang SC',
        'STHeiti',
        'Arial Unicode MS',
        'DejaVu Sans',
        'sans-serif',
    ]

    def __init__(self, output_dir: str):
        """初始化图表生成器

        :param output_dir: 图表输出目录
        """
        self.output_dir = output_dir

        self._setup_font()

    def _setup_font(self):
        """设置 matplotlib 中文字体"""
        matplotlib.rcParams['font.sans-serif'] = self.CJK_FONTS
        matplotlib.rcParams['axes.unicode_minus'] = False

        # 尝试精确匹配字体，确保中文显示
        for font_name in self.CJK_FONTS:
            try:
                font_path = fm.findfont(font_name, fallback_to_default=False)
                if font_path:
                    _log.debug(f'使用中文字体: {font_name} -> {font_path}')
                    matplotlib.rcParams['font.family'] = 'sans-serif'
                    matplotlib.rcParams['font.sans-serif'] = [font_name] + self.CJK_FONTS
                    return
            except Exception:
                continue

    @staticmethod
    def _is_command_message(message: str) -> bool:
        """判断是否为命令消息（以 / 开头）

        :param message: 消息内容
        :return: 是否为命令消息
        """
        return bool(message.strip().startswith('/'))

    @staticmethod
    def _extract_text_messages(messages: List[Dict]) -> str:
        """提取所有文本消息内容，过滤命令

        :param messages: 消息列表
        :return: 合并后的文本
        """
        texts = []
        for msg in messages:
            content = msg.get('message', '')
            if not content.startswith('/'):
                clean = re.sub(r'\[CQ:.*?\]', '', content)
                clean = re.sub(r'http[s]?://\S+', '', clean)
                clean = clean.strip()
                if clean and len(clean) > 1:
                    texts.append(clean)
        return ' '.join(texts)

    def _find_cjk_font_path(self) -> Optional[str]:
        """查找可用的中文字体文件路径

        :return: 字体文件路径或 None
        """
        for font_name in self.CJK_FONTS:
            try:
                font_path = fm.findfont(font_name, fallback_to_default=False)
                if font_path and os.path.exists(font_path):
                    return font_path
            except Exception:
                continue
        return None

    @staticmethod
    def _build_monthly_heatmap_matrix(daily_data: List[Dict]) -> Tuple[
        Optional[np.ndarray], List[str], List[int], List[str]
    ]:
        """将每日数据构建为 GitHub 风格热力图矩阵（7 天 × N 周）

        :param daily_data: 每日统计列表
        :return: (matrix, weekday_labels, month_positions, month_labels) 或 (None, [], [], [])
        """
        if not daily_data:
            return None, [], [], []

        import datetime as dt

        date_counts = {}
        for d in daily_data:
            date_counts[d['date']] = d['message_count']

        if not date_counts:
            return None, [], [], []

        all_dates = sorted(date_counts.keys())
        start_dt = dt.datetime.strptime(all_dates[0], '%Y-%m-%d')
        end_dt = dt.datetime.strptime(all_dates[-1], '%Y-%m-%d')

        start_weekday = start_dt.weekday()
        if start_weekday > 0:
            start_dt = start_dt - dt.timedelta(days=start_weekday)

        end_weekday = end_dt.weekday()
        if end_weekday < 6:
            end_dt = end_dt + dt.timedelta(days=6 - end_weekday)

        total_days = (end_dt - start_dt).days + 1
        num_weeks = max(total_days // 7, 1)

        matrix = np.zeros((7, num_weeks))
        for col in range(num_weeks):
            for row in range(7):
                day = start_dt + dt.timedelta(days=col * 7 + row)
                day_str = day.strftime('%Y-%m-%d')
                if day_str in date_counts:
                    matrix[row][col] = date_counts[day_str]

        weekday_labels = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

        month_positions = []
        month_labels = []
        last_month = None
        for col in range(num_weeks):
            week_day = start_dt + dt.timedelta(days=col * 7 + 3)
            month = week_day.month
            if month != last_month:
                month_positions.append(col)
                month_labels.append(week_day.strftime('%m月'))
                last_month = month

        return matrix, weekday_labels, month_positions, month_labels

    def generate_message_trend_chart(self, messages: List[Dict], group_id: int,
                                     hours: int = 24) -> Optional[str]:
        """生成消息趋势图（时间序列折线图）

        :param messages: 消息列表
        :param group_id: 群组 ID
        :param hours: 时间范围
        :return: 图片路径或 None
        """
        try:
            if not messages:
                return None

            # 按分钟聚合消息数量
            timestamps = []
            for msg in messages:
                try:
                    ts = datetime.datetime.strptime(msg['timestamp'], '%Y-%m-%d %H:%M:%S')
                    timestamps.append(ts)
                except (ValueError, KeyError):
                    continue

            if not timestamps:
                return None

            # 按 5 分钟间隔聚合
            min_time = min(timestamps)
            max_time = max(timestamps)
            interval = max(int(len(timestamps) / 30), 1)  # 大约 30 个数据点
            if (max_time - min_time).total_seconds() > 3600 * 6:
                # 超过 6 小时的数据用 30 分钟聚合
                bins = 30
            else:
                bins = min(len(timestamps), 48)

            fig, ax = plt.subplots(figsize=(12, 5))

            # 直方图
            ax.hist(timestamps, bins=bins, alpha=0.7, color='#4A90D9', edgecolor='white', linewidth=0.5)

            ax.set_xlabel('时间', fontsize=11)
            ax.set_ylabel('消息数量', fontsize=11)
            ax.set_title(f'群聊消息趋势（最近 {hours} 小时）', fontsize=13, fontweight='bold')
            ax.grid(True, alpha=0.3, linestyle='--')

            # 格式化时间轴
            if hours <= 6:
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
                ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=max(30, hours * 60 // 12)))
            else:
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d %H:%M'))
                ax.xaxis.set_major_locator(mdates.HourLocator(interval=max(1, hours // 8)))
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha='right')

            plt.tight_layout()

            chart_path = os.path.join(
                self.output_dir, f'group_chat_analyzer_chart_{group_id}_message_trend.png')
            plt.savefig(chart_path, dpi=150, bbox_inches='tight')
            plt.close()
            return chart_path

        except Exception as e:
            _log.error(f'生成消息趋势图失败: {e}')
            return None

    def generate_hourly_heatmap(self, hourly_data: Dict[str, int], group_id: int,
                                hours: int = 24) -> Optional[str]:
        """生成时段活跃度热力图/柱状图

        :param hourly_data: 小时 -> 消息数 字典
        :param group_id: 群组 ID
        :param hours: 时间范围
        :return: 图片路径或 None
        """
        try:
            if not hourly_data:
                return None

            fig, ax = plt.subplots(figsize=(10, 5))

            hours_list = list(range(24))
            labels = [f'{h:02d}:00' for h in hours_list]
            values = [hourly_data.get(f'{h:02d}', 0) for h in hours_list]

            colors = ['#4A90D9' if v > 0 else '#E8E8E8' for v in values]
            bars = ax.bar(hours_list, values, color=colors, edgecolor='white', linewidth=0.5)

            ax.set_xlabel('时段', fontsize=11)
            ax.set_ylabel('消息数量', fontsize=11)
            ax.set_title(f'群聊时段活跃度分布（最近 {hours} 小时）', fontsize=13, fontweight='bold')
            ax.set_xticks(hours_list)
            ax.set_xticklabels(labels, rotation=45, ha='right', fontsize=8)
            ax.grid(True, alpha=0.3, axis='y', linestyle='--')

            # 在柱状图上方显示数值
            for bar, val in zip(bars, values):
                if val > 0:
                    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                            str(val), ha='center', va='bottom', fontsize=7)

            plt.tight_layout()

            chart_path = os.path.join(
                self.output_dir, f'group_chat_analyzer_chart_{group_id}_hourly_activity.png')
            plt.savefig(chart_path, dpi=150, bbox_inches='tight')
            plt.close()
            return chart_path

        except Exception as e:
            _log.error(f'生成时段活跃图失败: {e}')
            return None

    def generate_daily_activity_chart(self, daily_data: List[Dict], group_id: int,
                                      days: int = 7) -> Optional[str]:
        """生成每日活跃度折线图

        :param daily_data: 每日统计列表
        :param group_id: 群组 ID
        :param days: 天数
        :return: 图片路径或 None
        """
        try:
            if not daily_data:
                return None

            dates = [d['date'] for d in daily_data]
            msg_counts = [d['message_count'] for d in daily_data]
            active_users = [d['active_users'] for d in daily_data]

            fig, ax1 = plt.subplots(figsize=(12, 5))

            color1 = '#4A90D9'
            color2 = '#E74C3C'

            # 消息数折线
            line1 = ax1.plot(range(len(dates)), msg_counts, color=color1, marker='o',
                             linewidth=2, markersize=6, label='消息数', zorder=3)
            ax1.fill_between(range(len(dates)), msg_counts, alpha=0.15, color=color1)
            ax1.set_ylabel('消息数量', fontsize=11, color=color1)
            ax1.tick_params(axis='y', labelcolor=color1)

            # 活跃用户数柱状图
            ax2 = ax1.twinx()
            bars = ax2.bar(range(len(dates)), active_users, alpha=0.4, color=color2,
                           width=0.4, label='活跃用户', zorder=1)
            ax2.set_ylabel('活跃用户数', fontsize=11, color=color2)
            ax2.tick_params(axis='y', labelcolor=color2)

            ax1.set_xticks(range(len(dates)))
            ax1.set_xticklabels(dates, rotation=30, ha='right', fontsize=9)
            ax1.set_title(f'群聊日活跃度趋势（最近 {days} 天）', fontsize=13, fontweight='bold')
            ax1.grid(True, alpha=0.3, linestyle='--')

            # 合并图例
            lines1, labels1 = ax1.get_legend_handles_labels()
            lines2, labels2 = ax2.get_legend_handles_labels()
            ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper left')

            plt.tight_layout()

            chart_path = os.path.join(
                self.output_dir, f'group_chat_analyzer_chart_{group_id}_daily_activity.png')
            plt.savefig(chart_path, dpi=150, bbox_inches='tight')
            plt.close()
            return chart_path

        except Exception as e:
            _log.error(f'生成日活跃度图失败: {e}')
            return None

    def generate_user_ranking_chart(self, user_stats: List[Dict], group_id: int,
                                    hours: int = 24, limit: int = 10) -> Optional[str]:
        """生成用户发言排行榜

        :param user_stats: 用户统计列表
        :param group_id: 群组 ID
        :param hours: 时间范围
        :param limit: 显示前几名
        :return: 图片路径或 None
        """
        try:
            if not user_stats:
                return None

            top_users = user_stats[:limit]
            top_users.reverse()  # 反转以便在图表中从上到下显示

            names = [u.get('user_name', str(u['user_id']))[:10] for u in top_users]
            counts = [u['message_count'] for u in top_users]
            max_count = max(counts) if counts else 1

            fig, ax = plt.subplots(figsize=(10, max(5, len(top_users) * 0.5)))

            colors = plt.cm.Blues(np.linspace(0.4, 0.9, len(top_users)))
            bars = ax.barh(range(len(top_users)), counts, color=colors, edgecolor='white', linewidth=0.5)

            ax.set_yticks(range(len(top_users)))
            ax.set_yticklabels(names, fontsize=10)
            ax.set_xlabel('发言次数', fontsize=11)
            ax.set_title(f'群聊发言排行榜 TOP{min(limit, len(user_stats))}（最近 {hours} 小时）',
                         fontsize=13, fontweight='bold')
            ax.grid(True, alpha=0.3, axis='x', linestyle='--')

            # 在条形上显示数值
            for bar, val in zip(bars, counts):
                ax.text(bar.get_width() + max_count * 0.01, bar.get_y() + bar.get_height() / 2,
                        str(val), ha='left', va='center', fontsize=10)

            ax.set_xlim(0, max_count * 1.15)

            plt.tight_layout()

            chart_path = os.path.join(
                self.output_dir, f'group_chat_analyzer_chart_{group_id}_user_ranking.png')
            plt.savefig(chart_path, dpi=150, bbox_inches='tight')
            plt.close()
            return chart_path

        except Exception as e:
            _log.error(f'生成用户排行榜失败: {e}')
            return None

    def generate_wordcloud_chart(self, messages: List[Dict], group_id: int,
                                 hours: int = 24, max_words: int = 100,
                                 width: int = 800, height: int = 400) -> Optional[str]:
        """生成词云图

        :param messages: 消息列表
        :param group_id: 群组 ID
        :param hours: 时间范围
        :param max_words: 最大词数
        :param width: 图片宽度
        :param height: 图片高度
        :return: 图片路径或 None
        """
        try:
            text = self._extract_text_messages(messages)
            if not text:
                return None

            import jieba
            from wordcloud import WordCloud

            words = jieba.lcut(text)
            stop_chars = set('，。！？、；：""''（）【】《》…—～· \t\n\r')
            filtered = [w for w in words
                        if len(w) > 1 and w not in stop_chars and not w.isdigit()]
            if not filtered:
                return None

            filtered_text = ' '.join(filtered)
            font_path = self._find_cjk_font_path()

            wc_kwargs: Dict[str, Any] = {
                'width': width,
                'height': height,
                'max_words': max_words,
                'background_color': 'white',
                'collocations': False,
            }
            if font_path:
                wc_kwargs['font_path'] = font_path

            wc = WordCloud(**wc_kwargs)
            wc.generate(filtered_text)

            fig, ax = plt.subplots(figsize=(10, 5))
            ax.imshow(wc, interpolation='bilinear')
            ax.axis('off')
            ax.set_title(f'群聊词云（最近 {hours} 小时）', fontsize=13, fontweight='bold')

            plt.tight_layout()
            chart_path = os.path.join(
                self.output_dir, f'group_chat_analyzer_chart_{group_id}_wordcloud.png')
            plt.savefig(chart_path, dpi=150, bbox_inches='tight')
            plt.close()
            return chart_path

        except Exception as e:
            _log.error(f'生成词云图失败: {e}')
            return None

    def generate_monthly_heatmap_chart(self, daily_data: List[Dict], group_id: int,
                                       months: int = 6) -> Optional[str]:
        """生成月度每日活跃热力图（GitHub 贡献墙风格）

        :param daily_data: 每日统计列表
        :param group_id: 群组 ID
        :param months: 时间范围（月）
        :return: 图片路径或 None
        """
        try:
            matrix, weekday_labels, month_positions, month_labels = (
                self._build_monthly_heatmap_matrix(daily_data)
            )
            if matrix is None:
                return None

            num_weeks = matrix.shape[1]

            colors_list = ['#ebedf0', '#9be9a8', '#40c463', '#30a14e', '#216e39']
            cmap = mcolors.ListedColormap(colors_list)

            max_val = matrix.max() if matrix.max() > 0 else 1
            bounds = [0, 1, max_val * 0.25, max_val * 0.5, max_val * 0.75, max_val]
            bounds = sorted(set(max(0, b) for b in bounds))
            if len(bounds) < 2:
                bounds = [0, 1]
                cmap = mcolors.ListedColormap([colors_list[0], colors_list[-1]])
            norm = mcolors.BoundaryNorm(bounds, cmap.N)

            fig, ax = plt.subplots(figsize=(max(14, num_weeks * 0.3), 3))
            ax.imshow(matrix, cmap=cmap, norm=norm, aspect='auto', interpolation='nearest')

            ax.set_yticks(range(7))
            ax.set_yticklabels(weekday_labels, fontsize=9)

            ax.set_xticks(month_positions)
            ax.set_xticklabels(month_labels, fontsize=9)
            ax.tick_params(top=True, labeltop=True, bottom=False, labelbottom=False)

            ax.set_title(f'群聊月度活跃热力图（最近 {months} 个月）', fontsize=13, fontweight='bold',
                         pad=20)

            plt.tight_layout()
            chart_path = os.path.join(
                self.output_dir, f'group_chat_analyzer_chart_{group_id}_monthly_heatmap.png')
            plt.savefig(chart_path, dpi=150, bbox_inches='tight')
            plt.close()
            return chart_path

        except Exception as e:
            _log.error(f'生成月度热力图失败: {e}')
            return None

    def generate_combined_report_chart(self, messages: List[Dict], hourly_data: Dict[str, int],
                                        daily_data: List[Dict], user_stats: List[Dict],
                                        group_id: int, hours: int = 24,
                                        monthly_data: Optional[List[Dict]] = None) -> Optional[str]:
        """生成综合报告图（六合一，3×2 布局，Z 字形阅读顺序）

        :param messages: 消息列表
        :param hourly_data: 时段活跃度数据
        :param daily_data: 日活跃度数据
        :param user_stats: 用户统计
        :param group_id: 群组 ID
        :param hours: 时间范围
        :param monthly_data: 月度每日活跃数据（用于热力图）
        :return: 图片路径或 None
        """
        try:
            fig = plt.figure(figsize=(20, 18))
            fig.suptitle(f'群聊数据分析报告（最近 {hours} 小时）',
                         fontsize=16, fontweight='bold', y=0.98)

            # 1) 消息趋势图 (1,1)
            ax1 = fig.add_subplot(3, 2, 1)
            if messages:
                timestamps = []
                for msg in messages:
                    try:
                        ts = datetime.datetime.strptime(msg['timestamp'], '%Y-%m-%d %H:%M:%S')
                        timestamps.append(ts)
                    except (ValueError, KeyError):
                        continue
                if timestamps:
                    bins = min(len(timestamps), 30)
                    ax1.hist(timestamps, bins=bins, alpha=0.7, color='#4A90D9',
                             edgecolor='white', linewidth=0.5)
                    if hours <= 6:
                        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
                    else:
                        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d %H:%M'))
                        ax1.xaxis.set_major_locator(mdates.HourLocator(interval=max(1, hours // 6)))
                    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=30, ha='right')
            ax1.set_title('消息趋势', fontsize=12, fontweight='bold')
            ax1.set_ylabel('消息数')
            ax1.grid(True, alpha=0.3, linestyle='--')

            # 2) 时段活跃度 (1,2)
            ax2 = fig.add_subplot(3, 2, 2)
            if hourly_data:
                hours_list = list(range(24))
                labels = [f'{h:02d}' for h in hours_list]
                values = [hourly_data.get(f'{h:02d}', 0) for h in hours_list]
                colors = ['#4A90D9' if v > 0 else '#E8E8E8' for v in values]
                ax2.bar(hours_list, values, color=colors, edgecolor='white', linewidth=0.5)
                ax2.set_xticks(hours_list)
                ax2.set_xticklabels(labels, fontsize=7, rotation=45)
            ax2.set_title('时段活跃度', fontsize=12, fontweight='bold')
            ax2.set_ylabel('消息数')
            ax2.grid(True, alpha=0.3, axis='y', linestyle='--')

            # 3) 日活跃趋势 (2,1)
            ax3 = fig.add_subplot(3, 2, 3)
            if daily_data:
                dates = [d['date'][-5:] for d in daily_data]
                msg_counts = [d['message_count'] for d in daily_data]
                active = [d['active_users'] for d in daily_data]

                ax3.plot(range(len(dates)), msg_counts, color='#4A90D9', marker='o',
                         linewidth=2, markersize=5, label='消息数')
                ax3.fill_between(range(len(dates)), msg_counts, alpha=0.15, color='#4A90D9')

                ax3b = ax3.twinx()
                ax3b.bar(range(len(dates)), active, alpha=0.4, color='#E74C3C',
                         width=0.4, label='活跃用户')

                ax3.set_xticks(range(len(dates)))
                ax3.set_xticklabels(dates, fontsize=8, rotation=30, ha='right')

                lines1, labels1 = ax3.get_legend_handles_labels()
                lines2, labels2 = ax3b.get_legend_handles_labels()
                ax3.legend(lines1 + lines2, labels1 + labels2, loc='upper left', fontsize=7)
            ax3.set_title('日活跃趋势', fontsize=12, fontweight='bold')
            ax3.set_ylabel('消息数')
            ax3.grid(True, alpha=0.3, linestyle='--')

            # 4) 月度热力图 (2,2)
            ax4 = fig.add_subplot(3, 2, 4)
            hm_generated = False
            if monthly_data:
                mtx, w_labels, m_pos, m_labs = self._build_monthly_heatmap_matrix(monthly_data)
                if mtx is not None:
                    colors_list = ['#ebedf0', '#9be9a8', '#40c463', '#30a14e', '#216e39']
                    hcmap = mcolors.ListedColormap(colors_list)
                    max_v = mtx.max() if mtx.max() > 0 else 1
                    hbounds = sorted(set(max(0, b) for b in
                                     [0, 1, max_v * 0.25, max_v * 0.5, max_v * 0.75, max_v]))
                    if len(hbounds) < 2:
                        hbounds = [0, 1]
                        hcmap = mcolors.ListedColormap([colors_list[0], colors_list[-1]])
                    hnorm = mcolors.BoundaryNorm(hbounds, hcmap.N)
                    ax4.imshow(mtx, cmap=hcmap, norm=hnorm, aspect='auto', interpolation='nearest')
                    ax4.set_yticks(range(7))
                    ax4.set_yticklabels(w_labels, fontsize=7)
                    ax4.set_xticks(m_pos)
                    ax4.set_xticklabels(m_labs, fontsize=7)
                    ax4.tick_params(top=True, labeltop=True, bottom=False, labelbottom=False)
                    hm_generated = True
            if not hm_generated:
                ax4.text(0.5, 0.5, '暂无足够月度数据',
                         ha='center', va='center', fontsize=10, color='#999',
                         transform=ax4.transAxes)
                ax4.set_xticks([])
                ax4.set_yticks([])
            ax4.set_title('月度活跃热力图', fontsize=12, fontweight='bold', pad=16)

            # 5) 发言排行榜 (3,1)
            ax5 = fig.add_subplot(3, 2, 5)
            if user_stats:
                top = user_stats[:8]
                top.reverse()
                names = [u.get('user_name', str(u['user_id']))[:8] for u in top]
                counts = [u['message_count'] for u in top]
                colors = plt.cm.Blues(np.linspace(0.4, 0.9, len(top)))
                ax5.barh(range(len(top)), counts, color=colors, edgecolor='white', linewidth=0.5)
                ax5.set_yticks(range(len(top)))
                ax5.set_yticklabels(names, fontsize=8)
                if counts:
                    ax5.set_xlim(0, max(counts) * 1.2)
            ax5.set_title('发言排行榜 TOP8', fontsize=12, fontweight='bold')
            ax5.set_xlabel('发言数')
            ax5.grid(True, alpha=0.3, axis='x', linestyle='--')

            # 6) 词云 (3,2)
            ax6 = fig.add_subplot(3, 2, 6)
            wc_generated = False
            if messages:
                text = self._extract_text_messages(messages)
                if text:
                    try:
                        import jieba
                        from wordcloud import WordCloud

                        words = jieba.lcut(text)
                        stop_chars = set('，。！？、；：""''（）【】《》…—～· \t\n\r')
                        filtered = [w for w in words
                                    if len(w) > 1 and w not in stop_chars and not w.isdigit()]
                        if filtered:
                            filtered_text = ' '.join(filtered)
                            font_path = self._find_cjk_font_path()
                            wc_kw: Dict[str, Any] = {
                                'width': 400, 'height': 300, 'max_words': 80,
                                'background_color': 'white', 'collocations': False,
                            }
                            if font_path:
                                wc_kw['font_path'] = font_path
                            _wc = WordCloud(**wc_kw).generate(filtered_text)
                            ax6.imshow(_wc, interpolation='bilinear')
                            wc_generated = True
                    except Exception:
                        pass
            if not wc_generated:
                ax6.text(0.5, 0.5, '暂无足够文本生成词云',
                         ha='center', va='center', fontsize=10, color='#999',
                         transform=ax6.transAxes)
            ax6.axis('off')
            ax6.set_title('词云', fontsize=12, fontweight='bold')

            plt.tight_layout(rect=[0, 0, 1, 0.95])

            chart_path = os.path.join(
                self.output_dir, f'group_chat_analyzer_chart_{group_id}_report.png')
            plt.savefig(chart_path, dpi=150, bbox_inches='tight')
            plt.close()
            return chart_path

        except Exception as e:
            _log.error(f'生成综合报告图失败: {e}')
            return None
