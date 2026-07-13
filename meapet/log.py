import logging
import logging.handlers
import os
import re
import datetime
import traceback
import sys

# ====================== 全局配置项 ======================
LOG_LEVEL = "DEBUG"          # 默认级别，可设为 "DEBUG","INFO","WARN","ERROR"
LOG_DIR = "logs"             # 日志文件存放目录
LOG_KEEP_DAYS = 7            # 保留最近几天的日志文件
# ====================================================

# ---------- 自定义 namer：把后缀式日期改为插入式 ----------
def _daily_namer(default_name):
    base, ext = os.path.splitext(default_name)   # base=logs/app.log  ext=.2026-07-13
    date_part = ext.lstrip('.')                   # 2026-07-13
    # 再拆一次，把 .log 分离出来
    root, real_ext = os.path.splitext(base)       # root=logs/app  real_ext=.log
    return f"{root}_{date_part}{real_ext}"


def _daily_rotator(source, dest):
    """滚动时重命名文件"""
    os.rename(source, dest)


class ColorFormatter(logging.Formatter):
    """控制台彩色格式化器"""

    LEVEL_COLORS = {
        'DEBUG':    '\033[36m',   # 青色
        'INFO':     '\033[32m',   # 绿色
        'WARNING':  '\033[33m',   # 黄色
        'WARN':     '\033[33m',   # 黄色
        'ERROR':    '\033[31m',   # 红色
    }
    RESET  = '\033[0m'
    PURPLE = '\033[35m'
    NAME   = '\033[34m'  # 暗蓝色

    # 匹配消息中的 [xxx]（方括号内非空且不含方括号）
    BRACKET_RE = re.compile(r'\[[^\[\]]+\]')

    def format(self, record):
        try:
            level_color = self.LEVEL_COLORS.get(record.levelname, '')
            reset = self.RESET

            asctime = self.formatTime(record, self.datefmt)
            msg = record.getMessage()

            # 对消息中的 [xxx] 加紫色
            msg = self.BRACKET_RE.sub(
                lambda m: f'{self.PURPLE}{m.group(0)}{reset}',
                msg
            )

            colored_time  = f'{level_color}{asctime}{reset}'
            colored_level = f'{level_color}[{record.levelname}]{reset}'
            colored_name  = f'{self.NAME}[{record.name}]{reset}'

            result = f'{colored_time} {colored_level} {colored_name} {msg}'

            if record.exc_info and not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
            if record.exc_text:
                result = f'{result}\n{record.exc_text}'

            return result

        except Exception:
            return f"{self.formatTime(record, self.datefmt)} [{record.levelname}] {record.getMessage()}"


def get_color_logger(name="app", log_dir=LOG_DIR, keep_days=LOG_KEEP_DAYS,
                     console_level=None, file_level=None, enable_file=True):
    """
    获取带彩色控制台输出和文件写入的日志记录器

    :param name:          logger 名称
    :param log_dir:       日志文件目录
    :param keep_days:     保留天数
    :param console_level: 控制台输出级别（字符串），None 则使用全局级别
    :param file_level:    文件输出级别（字符串），None 则使用全局级别
    :param enable_file:   是否启用文件输出
    :return:              logging.Logger 实例
    """
    # ---------- 确定级别 ----------
    if console_level is None:
        console_level = LOG_LEVEL
    if file_level is None:
        file_level = LOG_LEVEL

    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARN": logging.WARNING,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    console_level_num = level_map.get(console_level.upper(), logging.INFO)
    file_level_num = level_map.get(file_level.upper(), logging.INFO)

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    # ---------- 控制台 Handler（彩色） ----------
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level_num)
    console_formatter = ColorFormatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # ---------- 文件 Handler（用标准库 TimedRotatingFileHandler 替换自定义实现） ----------
    if enable_file:
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"{name}.log")

        file_handler = logging.handlers.TimedRotatingFileHandler(
            filename=log_path,
            when='midnight',          # 每天午夜滚动
            interval=1,
            backupCount=keep_days,    # 保留最近 N 天的备份
            encoding='utf-8',
            utc=False,
        )
        file_handler.setLevel(file_level_num)
        file_handler.namer = _daily_namer       # 自定义文件命名
        file_handler.rotator = _daily_rotator   # 自定义滚动行为

        file_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            # or "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    return logger