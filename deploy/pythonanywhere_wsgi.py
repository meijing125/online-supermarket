# -*- coding: utf-8 -*-
"""
PythonAnywhere WSGI 入口配置

用法：
    在 PythonAnywhere 的 Web 标签页创建应用后，把它自动生成的 WSGI 文件
    内容整体替换为本文件内容，然后按下面两处说明修改即可。

本项目结构说明：
    app.py 中 Flask 实例名为 app，因此需要 `from app import app as application`。
"""

import os
import sys

# ============ 需要修改 ①：项目所在路径 ============
# 把 USERNAME 换成你的 PythonAnywhere 用户名
project_home = '/home/USERNAME/online-supermarket'

if project_home not in sys.path:
    sys.path.insert(0, project_home)

# ============ 需要修改 ②：Session 密钥 ============
# 生成方法（本机执行）：python -c "import secrets; print(secrets.token_hex(32))"
# 必须是固定值，否则每次重载应用都会导致所有人被登出。
os.environ.setdefault('SECRET_KEY', 'CHANGE_ME_TO_A_RANDOM_STRING')

# 数据库文件默认生成在项目根目录（PythonAnywhere 的家目录是持久化的，
# 因此商品、用户、订单数据在应用重载后不会丢失）。
# 如无特殊需要，不必设置 DB_PATH。

# ============ 以下为标准引入，无需修改 ============

# 加载 Flask 应用。app.py 在导入时会自动建表并插入初始数据，
# 因此首次请求前数据库就已就绪。
from app import app as application
