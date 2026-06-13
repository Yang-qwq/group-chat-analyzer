# 群聊内容分析插件

[![Version](https://img.shields.io/badge/version-0.1.0-blue.svg)](https://github.com/Yang-qwq/group-chat-analyzer)
[![License](https://img.shields.io/badge/license-AGPL-red.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)

一个基于 NcatBot 框架的群聊内容分析插件，自动记录群聊消息并使用 SQLite3 存储，通过 matplotlib 生成丰富的分析图表。

## ✨ 功能特性

- 📝 **自动记录**：自动保存群聊消息到 SQLite 数据库
- 📊 **消息趋势图**：展示消息随时间的变化趋势
- ⏰ **时段活跃度**：分析各时段的群聊活跃度分布
- 📅 **日活跃趋势**：展示每日消息数和活跃用户变化
- 🗓️ **月度热力图**：GitHub 风格日历活跃热力图
- ☁️ **词云**：基于群聊文本生成词云
- 🏆 **发言排行榜**：统计群成员的发言次数排名
- 📈 **综合分析报告**：六合一（3×2）综合报告图
- 🔍 **文本查询命令**：快速查看统计数据
- ⏰ **自动发送**：按日/周/月定时发送群聊总结（管理员配置）
- 🔒 **权限管理**：管理命令自动校验管理员身份

## 📋 系统要求

- Python 3.10+
- NcatBot 框架
- 依赖：matplotlib, numpy, jieba, wordcloud

## 🚀 安装方法

### 手动安装

将插件源码放置到你的 `plugins` 目录下：

```bash
git clone https://github.com/Yang-qwq/group-chat-analyzer.git plugins/group_chat_analyzer
```

### Git Submodule

```bash
cd /path/to/ncatbot/
git submodule add https://github.com/Yang-qwq/group-chat-analyzer.git plugins/group_chat_analyzer
```

### 安装依赖

```bash
pip install -r requirements.txt
```

## ⚙️ 配置说明

插件会自动加载，无需额外配置。数据库存储在插件工作区，图表临时存储在系统临时目录中。

### 配置项

| 配置项                    | 类型   | 默认值    | 说明                   |
|------------------------|------|--------|----------------------|
| `EnableAutoRecord`     | bool | true   | 是否自动记录群聊消息           |
| `ForceBase64ImageSend` | bool | false  | 是否强制使用 Base64 编码发送图片 |
| `DataRetentionDays`    | int  | 30     | 数据自动保留天数（0 表示不自动清理）  |
| `AutoSendSummary`      | bool | false  | 全局总开关：是否允许自动发送群聊总结 |

## 📖 使用指南

### 分析命令

```bash
# 生成综合分析报告（默认 24 小时）
/gcanalyze

# 指定时间范围
/gcanalyze 48

# 单独生成趋势图
/gcanalyze trend [小时数]

# 单独生成时段活跃度
/gcanalyze hourly [小时数]

# 单独生成日活跃趋势（天数）
/gcanalyze daily [天数]

# 单独生成发言排行榜
/gcanalyze ranking [小时数]

# 生成词云
/gcanalyze wordcloud [小时数] [最大词数]

# 显示帮助信息
/gcanalyze help
```

### 查询命令

```bash
# 查看群聊统计信息
/gcstats [小时数]

# 查看发言排行榜文本版
/gctop [数量] [小时数]

# 生成月度活跃热力图
/gcmonthly [月数]

# 显示帮助信息
/gcstats help
/gctop help
/gcmonthly help
```

### 管理命令（管理员专用）

```bash
# 清理旧数据（默认 30 天）
/gcpurge [天数]

# 查看数据库统计信息
/gcdb

# 配置本群自动发送总结计划
/gcautosend set [key value ...]
/gcautosend remove
/gcautosend status
/gcautosend help

# 显示帮助信息
/gcpurge help
/gcdb help
```

`/gcautosend` 常用参数：`interval`（daily/weekly/monthly）、`time`（HH:MM）、`weekday`（0=周一）、`monthday`、`scope`（统计小时数，0=自动）。全局开关 `AutoSendSummary` 需开启后计划才会真正发送。

## 📊 图表类型

### 综合分析报告
六合一（3×2）面板：消息趋势 → 时段活跃度 → 日活跃趋势 → 月度热力图 → 发言排行榜 → 词云

### 消息趋势图
展示消息在时间轴上的分布密度

### 时段活跃度图
展示 24 小时内各时段的消息量分布

### 日活跃趋势图
双轴图展示每日消息数和活跃用户数变化

### 月度活跃热力图
GitHub 贡献墙风格，展示每日活跃强度

### 发言排行榜
横向柱状图展示最活跃的群成员

### 词云
基于近期群聊文本生成词云图

## 🗄️ 数据存储

插件使用 SQLite3 存储数据，包含以下表：

### group_messages
群聊消息记录表
- 记录每条消息的发送者、内容、时间
- 支持按时间和用户高效查询

### user_names
用户名映射表
- 缓存 user_id → 昵称，以及累计发言数

## 📁 文件结构

```
group_chat_analyzer/
├── __init__.py          # 插件初始化
├── main.py              # 插件主入口
├── database.py          # 数据库模块
├── analyzer.py          # 图表生成模块
├── command_handler.py   # 命令处理模块
├── requirements.txt     # Python 依赖
├── Pipfile              # Pipenv 依赖管理
├── README.md            # 说明文档
└── LICENSE              # AGPL 许可证
```

## 🔧 故障排除

### 图表中文显示异常
确保系统已安装中文字体：
```bash
# Debian/Ubuntu
apt install fonts-noto-cjk
# 或
apt install fonts-wqy-zenhei fonts-wqy-microhei
```

### 数据库错误
- 检查数据库文件权限
- 确认 SQLite3 支持
- 使用 `/gcdb` 查看数据库状态

### 图表生成失败
- 确认有足够的历史消息数据
- 检查 matplotlib 是否正确安装
- 查看日志获取详细错误信息

## 许可证

本项目采用 GNU Affero General Public License v3.0 许可证，详见 [LICENSE](LICENSE) 文件。

## 贡献

欢迎提交 Issue 和 Pull Request！🚀
