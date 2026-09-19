# 臻品汇数码奢品商城

一个基于 **Python Flask + SQLite** 的网上超市 / 商城系统，前端使用原生 JavaScript + Bootstrap 5。

## 功能模块

| 模块 | 说明 |
| --- | --- |
| 用户系统 | 注册 / 登录 / 退出，密码使用 PBKDF2-SHA256（10万次迭代 + 随机盐）哈希存储 |
| 商品展示 | 首页商品列表，按上架时间倒序 |
| 后台管理 | 管理员上架新品、编辑商品、上下架、上传商品图片 |
| 购物车 | Ajax 异步增删改，实时更新小计、总价与导航栏角标 |
| 结算下单 | 库存检查 → 扣减库存 → 创建订单 → 清空购物车，全程事务保护 |
| 订单记录 | 查看历史订单及其商品明细快照 |

## 技术栈

- **后端**：Flask 3.x
- **数据库**：SQLite（通过 `db.py` 适配层封装）
- **前端**：原生 JavaScript + Bootstrap 5 + Jinja2 模板
- **部署**：Gunicorn + Render

## 项目结构

```
online-supermarket/
├── app.py                  # 主程序：路由、业务逻辑、鉴权
├── db.py                   # 数据库访问层（SQLite 适配 + 建表 + 初始数据）
├── requirements.txt        # 依赖清单
├── render.yaml             # Render 部署蓝图
├── .env.example            # 环境变量示例
├── init.sql                # 早期 MySQL 版建表脚本（已废弃，保留备查）
├── static/
│   ├── style.css
│   └── uploads/            # 用户上传的商品图片
└── templates/              # Jinja2 页面模板
```

## 本地运行

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动（首次运行会自动建表并插入 12 件示例商品）
python app.py

# 3. 浏览器打开 http://localhost:5000
```

数据库文件 `supermarket.db` 会在首次启动时自动生成在项目根目录，无需任何额外配置。

**默认管理员账号：`admin` / `admin123`**

## 环境变量

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `SECRET_KEY` | Flask Session 加密密钥。不设置则每次启动随机生成，重启后所有用户会被登出 | 随机值 |
| `DB_PATH` | SQLite 数据库文件路径 | 项目根目录 `supermarket.db` |
| `PORT` | 监听端口 | `5000` |
| `FLASK_DEBUG` | 设为 `1` 开启调试模式（**生产环境不要开启**） | 关闭 |

生成一个固定密钥：

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## 部署到 Render

1. 把本仓库推送到 GitHub。
2. 打开 [render.com](https://render.com) → **New** → **Blueprint** → 选择本仓库。
3. Render 会自动读取 `render.yaml`，点击确认即可。
   （也可以选 **Web Service** 手动配置：Build Command 填 `pip install -r requirements.txt`，
   Start Command 填 `gunicorn --workers 1 --threads 4 --bind 0.0.0.0:$PORT app:app`。）
4. 部署完成后 Render 会给出一个 `https://xxx.onrender.com` 的公开网址，任何人都能打开。

## 已知限制

这几点是 SQLite + 免费云平台的固有限制，部署前请知悉：

1. **免费实例会休眠**：Render 免费套餐在 15 分钟无访问后会休眠，下次访问需要约 30 秒冷启动。
2. **上传的图片会在重启后丢失**：免费套餐没有持久化磁盘，`static/uploads/` 下的图片在实例
   重启或重新部署后会消失。示例商品使用的是外部图床链接，不受影响。
3. **数据库会被重置**：同上，`supermarket.db` 也没有持久化。若需保留数据，可在 Render 上
   挂载付费磁盘并把 `DB_PATH` 指向该磁盘。
4. **演示用并发模型**：SQLite 采用数据库级写锁，无法像 MySQL 那样用 `SELECT ... FOR UPDATE`
   做行级锁。当前实现为单 worker + 多线程，能应对演示规模，但不适合高并发抢购场景。
5. **默认管理员密码**：`admin123` 仅用于演示，公开部署后请及时修改。

## 测试

- `商城_测试用例.md` / `商城_测试用例.xlsx` — 功能测试用例
- `gen_xlsx.py` — 测试用例表格生成脚本
