# group_chat_analyzer

ncatbot 插件，自动记录 QQ 群消息到 SQLite 并生成统计图表（matplotlib）。

## 仓库事实

- **Git 子模块**（`dev` 分支）—— 修改后必须在子模块目录内 `git commit`
- **无测试、无 linter、无 typechecker**（没有 pytest/ruff/mypy 配置）
- **无 pyproject.toml** — 依赖见 `requirements.txt` 或 `Pipfile`

## 架构要点

| 层 | 文件 | 职责 |
|---|---|---|
| 入口 | `__init__.py` → `main.py:GroupChatAnalyzerPlugin` | ncatbot 插件生命周期 |
| Mixin | `command_handler.py:GroupChatAnalyzerCommandMixin` | 命令路由 & 业务逻辑 |
| 数据 | `database.py:DatabaseManager` | SQLite 读写，表 `group_messages` / `user_names` |
| 图表 | `analyzer.py:ChartGenerator` | matplotlib 生成 PNG（`matplotlib.use('Agg')`） |

MRO: `GroupChatAnalyzerPlugin(GroupChatAnalyzerCommandMixin, BasePlugin)`

## 命令体系

**用户命令**（前缀 `/gc`）— `register_user_func()` → `user_command_handler()` → `shlex.split` 路由：
- `/gcanalyze [sub] [hours/days]` — sub: `trend`, `hourly`, `daily`, `ranking`, `wordcloud`；无参 = 综合报告 3×2
- `/gcstats [hours]` — 文本统计
- `/gctop [limit] [hours]` — 文本排行
- `/gcmonthly [months]` — GitHub 风格日历热力图

**管理员命令**（regex `r'^/gc(purge|db|autosend)'`）— `register_admin_func()` → `admin_command_handler()`：
- `/gcpurge [days]` — 清理旧数据（强制 ≥ `DataRetentionDays`）
- `/gcdb` — 数据库统计
- `/gcautosend set [key value ...]` — 创建/更新本群自动发送计划
- `/gcautosend remove` — 删除本群计划
- `/gcautosend status` — 查看本群计划
- `/gcautosend help` — 帮助

## 数据库

SQLite, WAL 模式, `check_same_thread=False`。路径：`{workspace}/group_chat_data.db`。
读写类公开方法以 `ensure_connection()` 开头做连接保活。例外：`get_most_active_users`（转调 `get_user_stats`）、`get_db_size_mb`（只读文件大小）、`refresh_user_names`（空实现）、`close`。

```sql
group_messages (id, group_id, user_id, message, timestamp)
user_names    (user_id PK, user_name, total_messages, created_at, updated_at)
```

索引：`idx_group_messages_group_time(group_id, timestamp)`, `idx_group_messages_user(group_id, user_id)`

## 图表生成

`ChartGenerator` 的 `output_dir` 为系统临时目录（`get_system_temp_dir()`），非插件 workspace。
输出路径格式：`os.path.join(self.output_dir, 'group_chat_analyzer_chart_{group_id}_{type}.png')`
每种 `generate_*()` 方法末尾必须 `plt.close()`。
CJK 字体通过 `self.CJK_FONTS` 列表匹配，词云额外走 `_find_cjk_font_path()`。
`jieba` / `wordcloud` 仅在词云方法内部 `import`（懒加载）。

综合报告：3×2 面板，Z 字形顺序 → 消息趋势 → 时段活跃度 → 日活跃趋势 → 月度热力图 → 发言排行 → 词云

现有方法：`generate_message_trend_chart`、`generate_hourly_heatmap`（无 `_chart` 后缀）、`generate_daily_activity_chart`、`generate_user_ranking_chart`、`generate_wordcloud_chart`、`generate_monthly_heatmap_chart`、`generate_combined_report_chart`。

## 编码约定

- 所有方法使用中文 `:param` / `:return` docstring
- 所有新增或修改的代码必须编写注释和日志记录，不可跳过（包括 `_log.debug`，便于后期调试维护）
- 日志用 `from ncatbot.utils.logger import get_log` 的 `_log`
- 错误处理：`try/except` → `_log.error(msg)` + `await event.reply_text(msg)`
- 命令回复的图片发送走 `_send_image()`；自动发送在 `main._execute_auto_send_summary` 内联处理（同样读 `ForceBase64ImageSend`）
- 命令解析用 `shlex.split(event.raw_message.replace('\\\\n', '\n'))`
- 手动 `/gcpurge` 与定时清理（每 6h）都读取 `DataRetentionDays`；purge 额外要求天数 ≥ 配置值

## 配置项

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `EnableAutoRecord` | bool | true | 自动记录消息 |
| `ForceBase64ImageSend` | bool | false | Base64 编码发送图片 |
| `DataRetentionDays` | int | 30 | 数据保留天数（0=不清理） |
| `AutoSendSummary` | bool | false | 全局自动发送总结总开关 |

持久化路径：`data/group_chat_analyzer/group_chat_analyzer.json`

## 自动发送计划（auto_summary_plans）

存储在 `self.data['data']['auto_summary_plans']`，以群号为 key。

```python
{
  "123456": {                         # group_id
    "interval": "daily",              # daily | weekly | monthly
    "time": "22:00",                  # HH:MM
    "weekday": 0,                     # 0=周一，仅 weekly
    "monthday": 1,                    # 仅 monthly
    "scope": 0,                       # 统计范围小时数（0=自动: 24/168/720）
    "last_send": "2026-06-26"         # 防重复，格式取决于 interval
  }
}
```

每个群独立计划，通过 `/gcautosend` 命令管理，`remove` 彻底删除对应任务和存储。
插件 `on_load()` 遍历 `auto_summary_plans` 用 `add_scheduled_task` 重新注册所有计划。
定时任务内部检查全局开关 `AutoSendSummary` 和周期条件，通过 `self.api.post_group_msg()` 无事件上下文发送。

## 新增命令

1. `command_handler.py` 顶部加 `X_HELP_TEXT` 常量
2. 在 `GroupChatAnalyzerCommandMixin` 加 `async def handle_x_command()`
3. 在 `user_command_handler()` 或 `admin_command_handler()` 加 elif 路由
4. 如需不同前缀，在 `main.py:on_load()` 用 `register_user_func()` / `register_admin_func()`

## 新增图表

- 方法签名优先：`def generate_xxx_chart(self, data, group_id, ...) -> Optional[str]`（既有例外：`generate_hourly_heatmap`）
- 返回图片路径或 `None`；失败用 `_log.error()` 记录
- 必须 `plt.close()`；中文字体走 `self.CJK_FONTS` / `_setup_font()`
- 路径用 `os.path.join(self.output_dir, ...)`
