# 手机号验证码登录系统

基于 Flask + 腾讯云短信 + SQLite 的手机号验证码登录系统。

## 功能特性

- 手机号 + 短信验证码登录（无需密码）
- 腾讯云 SMS 发送6位随机验证码
- 验证码5分钟有效，1分钟防刷限制
- 首次登录自动注册新用户
- Session 会话管理（7天有效期）
- 手机号脱敏显示
- 响应式登录页面

## 项目结构

```
phone-login-app/
├── app.py              # 主应用（路由、短信、数据库）
├── templates/
│   ├── login.html      # 登录页面
│   └── index.html      # 首页（登录后）
├── requirements.txt    # Python 依赖
├── .env.example        # 环境变量模板
├── Procfile            # 部署启动命令
├── runtime.txt         # Python 版本
└── README.md           # 本文件
```

## 一、腾讯云短信配置（前置步骤）

### 1. 创建 API 密钥

1. 登录 [腾讯云控制台](https://console.cloud.tencent.com/)
2. 进入 **访问管理 → API密钥管理**
3. 点击「新建密钥」，记录 `SecretId` 和 `SecretKey`

### 2. 创建短信应用

1. 进入 [短信控制台](https://console.cloud.tencent.com/smsv2)
2. 点击「创建应用」，填写应用名称
3. 记录 `SDK AppID`（如 1400xxxxxx）

### 3. 创建短信签名

1. 在短信控制台 →「国内短信」→「签名管理」
2. 点击「创建签名」
3. 签名来源选择「自用」，填写你的应用/公司名称
4. 等待审核通过（通常1-2小时）

### 4. 创建短信模板

1. 在「模板管理」中点击「创建模板」
2. 模板类型选择「验证码」
3. 模板内容填写：

```
您的验证码为{1}，{2}分钟内有效，请勿泄露给他人。
```

4. 等待审核通过，记录模板 ID

## 二、本地运行

### 1. 安装依赖

```bash
cd phone-login-app
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env`，填入你的腾讯云配置：

```bash
# Windows
copy .env.example .env

# macOS/Linux
cp .env.example .env
```

编辑 `.env` 文件，填入实际值：

```
SECRET_KEY=一个随机长字符串
FLASK_ENV=development
TENCENT_SECRET_ID=AKIDxxxxxxxxxxxxxxxx
TENCENT_SECRET_KEY=xxxxxxxxxxxxxxxxxxxxxxxx
SMS_SDK_APP_ID=1400xxxxxx
SMS_SIGN_NAME=你的签名
SMS_TEMPLATE_ID=xxxxxx
```

### 3. 加载环境变量并启动

```bash
# Windows (CMD)
for /f "tokens=*" %i in (.env) do set %i
python app.py

# Windows (PowerShell)
Get-Content .env | ForEach-Object { if ($_ -match '^([^#][^=]+)=(.*)$') { [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2]) } }
python app.py

# macOS/Linux
export $(grep -v '^#' .env | xargs)
python app.py
```

### 4. 开发模式说明

当 `FLASK_ENV=development` 时，如果短信发送失败（如配置不完整），验证码会打印在控制台，方便本地测试。生产环境不会打印。

访问 http://localhost:5000/login 即可看到登录页面。

## 三、部署到 Railway

### 1. 推送代码到 GitHub

```bash
cd phone-login-app
git init
git add .
git commit -m "feat: phone verification code login system"
git remote add origin https://github.com/你的用户名/phone-login-app.git
git push -u origin main
```

### 2. Railway 部署

1. 登录 [Railway](https://railway.app)
2. 点击「New Project → Deploy from GitHub repo」
3. 选择 `phone-login-app` 仓库
4. Railway 会自动识别 Procfile 和 requirements.txt

### 3. 配置环境变量

在 Railway 项目 → Settings → Variables 中添加：

| 变量名 | 值 |
|--------|-----|
| SECRET_KEY | 随机字符串（至少32位） |
| FLASK_ENV | production |
| TENCENT_SECRET_ID | 你的 SecretId |
| TENCENT_SECRET_KEY | 你的 SecretKey |
| SMS_SDK_APP_ID | 你的 AppID |
| SMS_SIGN_NAME | 你的签名 |
| SMS_TEMPLATE_ID | 你的模板ID |

### 4. 生成域名

在 Railway 项目 → Settings → Networking → Generate Domain

## 四、部署到其他平台

### Gunicorn 直接运行（Linux 服务器）

```bash
pip install -r requirements.txt
export SECRET_KEY=xxx
export TENCENT_SECRET_ID=xxx
# ... 其他环境变量
gunicorn app:app --bind 0.0.0.0:5000 --workers 2
```

### Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:5000", "--workers", "2"]
```

## 五、API 接口说明

| 接口 | 方法 | 说明 |
|------|------|------|
| `/login` | GET | 登录页面 |
| `/` | GET | 首页（需登录） |
| `/api/send-code` | POST | 发送验证码 `{phone}` |
| `/api/login` | POST | 登录验证 `{phone, code}` |
| `/api/logout` | POST | 退出登录 |
| `/api/user-info` | GET | 获取用户信息 |
| `/health` | GET | 健康检查 |

## 六、安全说明

- 所有敏感配置通过环境变量注入，代码中不含任何密钥
- Session 使用 HttpOnly Cookie，防止 XSS 窃取
- 验证码使用后立即失效，防止重放攻击
- 1分钟发送频率限制，防止短信轰炸
- 手机号在接口返回中脱敏处理（138****1234）
- 生产环境不打印验证码到日志

## 七、数据库

使用 SQLite，数据文件默认为项目根目录下的 `users.db`，包含两张表：

- `users`: 用户表（id, phone, created_at, last_login, login_count）
- `verification_codes`: 验证码记录表（phone, code, created_at, expires_at, used）

如需更换为 MySQL/PostgreSQL，只需修改 `get_db()` 和 `init_db()` 函数。
