# -*- coding: utf-8 -*-
import datetime
import os
import sqlite3
from typing import Any, Dict, List, Optional

from ncatbot.utils.logger import get_log

_log = get_log('group_chat_analyzer')


class DatabaseManager:
    """群聊消息数据库管理器"""

    def __init__(self, db_path: str):
        """初始化数据库管理器

        :param db_path: 数据库文件路径
        """
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self.init_database()

    def init_database(self):
        """初始化数据库连接和表结构"""
        try:
            db_dir = os.path.dirname(self.db_path)
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)

            self.conn = sqlite3.connect(
                self.db_path,
                timeout=30.0,
                check_same_thread=False
            )

            # 启用 WAL 模式提高并发性能
            self.conn.execute('PRAGMA journal_mode=WAL')
            self.conn.execute('PRAGMA synchronous=NORMAL')
            self.conn.execute('PRAGMA cache_size=10000')
            self.conn.execute('PRAGMA temp_store=MEMORY')

            self._create_tables()
            _log.info(f'数据库初始化成功: {self.db_path}')

        except Exception as e:
            _log.error(f'数据库初始化失败: {e}')
            raise sqlite3.Error(f'数据库初始化失败: {e}')

    def ensure_connection(self):
        """确保数据库连接有效

        :return: None
        """
        try:
            if self.conn is None:
                self.init_database()
                return
            self.conn.execute('SELECT 1')
        except sqlite3.Error:
            _log.warning('数据库连接已断开，重新建立连接')
            try:
                if self.conn:
                    self.conn.close()
            except Exception:
                pass
            self.init_database()

    def _create_tables(self):
        """创建数据表"""
        cursor = self.conn.cursor()

        # 群聊消息记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS group_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                timestamp DATETIME NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
        ''')

        # 用户名映射表（user_id 为主键，留扩展余地）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_names (
                user_id        INTEGER PRIMARY KEY,
                user_name      TEXT NOT NULL DEFAULT '',
                total_messages INTEGER NOT NULL DEFAULT 0,
                created_at     DATETIME NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at     DATETIME NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
        ''')

        # 索引
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_group_messages_group_time
            ON group_messages(group_id, timestamp)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_group_messages_user
            ON group_messages(group_id, user_id)
        ''')
        self.conn.commit()

    def save_message(self, group_id: int, user_id: int, message: str) -> bool:
        """保存一条群聊消息

        :param group_id: 群组 ID
        :param user_id: 用户 ID
        :param message: 消息内容
        :return: 是否保存成功
        """
        try:
            self.ensure_connection()
            cursor = self.conn.cursor()
            now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute('''
                INSERT INTO group_messages (group_id, user_id, message, timestamp)
                VALUES (?, ?, ?, ?)
            ''', (group_id, user_id, message[:2000], now))
            self.conn.commit()

            _log.debug(f'[群组 {group_id}:{user_id}] 记录消息：{message[:100]}{"..." if len(message) > 100 else ""}')

            return True
        except Exception as e:
            _log.error(f'保存消息失败: {e}')
            self.ensure_connection()
            return False

    def get_message_count(self, group_id: int, hours: int = 24) -> int:
        """获取指定时间内的消息总数

        :param group_id: 群组 ID
        :param hours: 查询的时间范围（小时）
        :return: 消息数量
        """
        try:
            self.ensure_connection()
            cursor = self.conn.cursor()
            time_limit = (datetime.datetime.now() - datetime.timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')

            cursor.execute('''
                SELECT COUNT(*) FROM group_messages
                WHERE group_id = ? AND timestamp >= ?
            ''', (group_id, time_limit))

            return cursor.fetchone()[0]
        except Exception as e:
            _log.error(f'获取消息数量失败: {e}')
            return 0

    def get_messages(self, group_id: int, hours: int = 24) -> List[Dict[str, Any]]:
        """获取指定时间内的消息列表

        :param group_id: 群组 ID
        :param hours: 查询的时间范围（小时）
        :return: 消息列表
        """
        try:
            self.ensure_connection()
            cursor = self.conn.cursor()
            time_limit = (datetime.datetime.now() - datetime.timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')

            cursor.execute('''
                SELECT id, group_id, user_id, message, timestamp
                FROM group_messages
                WHERE group_id = ? AND timestamp >= ?
                ORDER BY timestamp ASC
            ''', (group_id, time_limit))

            rows = cursor.fetchall()
            messages = []
            for row in rows:
                messages.append({
                    'id': row[0],
                    'group_id': row[1],
                    'user_id': row[2],
                    'message': row[3],
                    'timestamp': row[4],
                })
            return messages
        except Exception as e:
            _log.error(f'获取消息列表失败: {e}')
            return []

    def get_user_stats(self, group_id: int, hours: int = 24) -> List[Dict[str, Any]]:
        """获取群成员发言统计

        :param group_id: 群组 ID
        :param hours: 查询的时间范围（小时）
        :return: 用户发言统计列表（按消息数降序）
        """
        try:
            self.ensure_connection()
            cursor = self.conn.cursor()
            time_limit = (datetime.datetime.now() - datetime.timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')

            cursor.execute('''
                SELECT gm.user_id,
                       COALESCE(un.user_name, '') as user_name,
                       COUNT(*) as msg_count,
                       MAX(gm.timestamp) as last_active
                FROM group_messages gm
                LEFT JOIN user_names un ON gm.user_id = un.user_id
                WHERE gm.group_id = ? AND gm.timestamp >= ?
                GROUP BY gm.user_id
                ORDER BY msg_count DESC
            ''', (group_id, time_limit))

            rows = cursor.fetchall()
            stats = []
            for row in rows:
                stats.append({
                    'user_id': row[0],
                    'user_name': row[1] or str(row[0]),
                    'message_count': row[2],
                    'last_active': row[3],
                })
            return stats
        except Exception as e:
            _log.error(f'获取用户统计失败: {e}')
            return []

    def get_hourly_activity(self, group_id: int, hours: int = 24) -> Dict[str, int]:
        """获取各小时段的发言量分布

        :param group_id: 群组 ID
        :param hours: 查询的时间范围（小时）
        :return: 小时 -> 消息数 字典
        """
        try:
            self.ensure_connection()
            cursor = self.conn.cursor()
            time_limit = (datetime.datetime.now() - datetime.timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')

            cursor.execute('''
                SELECT CAST(strftime('%H', timestamp) AS INTEGER) as hour,
                       COUNT(*) as msg_count
                FROM group_messages
                WHERE group_id = ? AND timestamp >= ?
                GROUP BY hour
                ORDER BY hour
            ''', (group_id, time_limit))

            activity = {str(i).zfill(2): 0 for i in range(24)}
            for row in cursor.fetchall():
                activity[str(row[0]).zfill(2)] = row[1]
            return activity
        except Exception as e:
            _log.error(f'获取时段活跃度失败: {e}')
            return {}

    def get_daily_activity(self, group_id: int, days: int = 7) -> List[Dict[str, Any]]:
        """获取每日发言统计

        :param group_id: 群组 ID
        :param days: 查询的天数
        :return: 每日统计列表
        """
        try:
            self.ensure_connection()
            cursor = self.conn.cursor()
            time_limit = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')

            cursor.execute('''
                SELECT date(timestamp) as day,
                       COUNT(*) as msg_count,
                       COUNT(DISTINCT user_id) as active_users
                FROM group_messages
                WHERE group_id = ? AND timestamp >= ?
                GROUP BY day
                ORDER BY day ASC
            ''', (group_id, time_limit))

            rows = cursor.fetchall()
            stats = []
            for row in rows:
                stats.append({
                    'date': row[0],
                    'message_count': row[1],
                    'active_users': row[2],
                })
            return stats
        except Exception as e:
            _log.error(f'获取每日统计失败: {e}')
            return []

    def get_most_active_users(self, group_id: int, hours: int = 24,
                              limit: int = 10) -> List[Dict[str, Any]]:
        """获取最活跃的群成员

        :param group_id: 群组 ID
        :param hours: 查询的时间范围（小时）
        :param limit: 返回数量限制
        :return: 活跃用户列表
        """
        stats = self.get_user_stats(group_id, hours)
        return stats[:limit]

    def cleanup_old_data(self, days: int = 30) -> int:
        """清理旧数据

        :param days: 保留最近多少天的数据
        :return: 清理的记录数
        """
        try:
            self.ensure_connection()
            cursor = self.conn.cursor()
            now = datetime.datetime.now()
            cutoff_datetime = (now - datetime.timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')

            cursor.execute('SELECT COUNT(*) FROM group_messages WHERE timestamp < ?', (cutoff_datetime,))
            count_to_delete = cursor.fetchone()[0]

            if count_to_delete == 0:
                return 0

            cursor.execute('DELETE FROM group_messages WHERE timestamp < ?', (cutoff_datetime,))
            deleted_messages = cursor.rowcount

            self.conn.commit()
            _log.info(f'已清理 {deleted_messages} 条超过 {days} 天的数据')
            return deleted_messages
        except Exception as e:
            _log.error(f'清理旧数据失败: {e}')
            try:
                self.ensure_connection()
            except Exception:
                pass
            return 0

    def get_all_group_ids(self) -> List[int]:
        """获取数据库中有消息记录的所有群号

        :return: 群号列表
        """
        try:
            self.ensure_connection()
            cursor = self.conn.cursor()
            cursor.execute('SELECT DISTINCT group_id FROM group_messages')
            return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            _log.error(f'获取所有群号失败: {e}')
            return []

    def get_total_messages_count(self, group_id: int) -> int:
        """获取群组消息总数

        :param group_id: 群组 ID
        :return: 消息总数
        """
        try:
            self.ensure_connection()
            cursor = self.conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM group_messages WHERE group_id = ?', (group_id,))
            return cursor.fetchone()[0]
        except Exception as e:
            _log.error(f'获取消息总数失败: {e}')
            return 0

    def get_active_users_count(self, group_id: int, hours: int = 24) -> int:
        """获取活跃用户数

        :param group_id: 群组 ID
        :param hours: 查询的时间范围
        :return: 活跃用户数量
        """
        try:
            self.ensure_connection()
            cursor = self.conn.cursor()
            time_limit = (datetime.datetime.now() - datetime.timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute('''
                SELECT COUNT(DISTINCT user_id) FROM group_messages
                WHERE group_id = ? AND timestamp >= ?
            ''', (group_id, time_limit))
            return cursor.fetchone()[0]
        except Exception as e:
            _log.error(f'获取活跃用户数失败: {e}')
            return 0

    def save_user_name(self, user_id: int, user_name: str) -> bool:
        """保存/更新用户名映射

        :param user_id: 用户 ID
        :param user_name: 用户昵称
        :return: 是否成功
        """
        try:
            self.ensure_connection()
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO user_names (user_id, user_name, updated_at)
                VALUES (?, ?, datetime('now', 'localtime'))
                ON CONFLICT(user_id) DO UPDATE SET
                    user_name = excluded.user_name,
                    updated_at = datetime('now', 'localtime')
            ''', (user_id, user_name[:50]))
            self.conn.commit()
            return True
        except Exception as e:
            _log.error(f'保存用户名失败: {e}')
            return False

    def increment_user_message_count(self, user_id: int) -> bool:
        """累加用户的总发言数缓存

        :param user_id: 用户 ID
        :return: 是否成功
        """
        try:
            self.ensure_connection()
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO user_names (user_id, total_messages, updated_at)
                VALUES (?, 1, datetime('now', 'localtime'))
                ON CONFLICT(user_id) DO UPDATE SET
                    total_messages = total_messages + 1,
                    updated_at = datetime('now', 'localtime')
            ''', (user_id,))
            self.conn.commit()
            return True
        except Exception as e:
            _log.error(f'累加发言数失败: {e}')
            return False

    def get_user_name(self, user_id: int) -> Optional[str]:
        """获取缓存的用户名

        :param user_id: 用户 ID
        :return: 用户名或 None
        """
        try:
            self.ensure_connection()
            cursor = self.conn.cursor()
            cursor.execute('SELECT user_name FROM user_names WHERE user_id = ?', (user_id,))
            row = cursor.fetchone()
            return row[0] if row else None
        except Exception as e:
            _log.error(f'获取用户名失败: {e}')
            return None

    def refresh_user_names(self, group_id: int, api_getter) -> int:
        """通过 API 批量刷新群成员用户名缓存

        :param group_id: 群组 ID
        :param api_getter: 异步函数，调用方式 await api_getter(group_id, user_id)
        :return: 刷新的用户数
        """
        return 0  # 暂由 command_handler 按需调用

    def get_db_size_mb(self) -> float:
        """获取数据库文件大小（MB），包含 WAL/SHM 文件

        :return: 数据库大小（MB），保留两位小数
        """
        total_bytes = 0
        for suffix in ('', '-wal', '-shm'):
            path = self.db_path + suffix
            try:
                if os.path.exists(path):
                    total_bytes += os.path.getsize(path)
            except OSError:
                continue
        return round(total_bytes / (1024 * 1024), 2)

    def close(self):
        """关闭数据库连接"""
        try:
            if self.conn:
                self.conn.close()
                self.conn = None
                _log.debug('数据库连接已关闭')
        except Exception as e:
            _log.error(f'关闭数据库连接失败: {e}')
