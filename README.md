

## 部署

### 本地运行

```bash
pip install -r requirements.txt
python plagiarism_checker.py
# 访问 http://localhost:5099
```

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `APP_PASSWORD` | 登录密码 | `orbit2024` |
| `SECRET_KEY` | Flask session 密钥 | 随机生成 |
| `PORT` | 端口号 | `5099` |

### 部署到 Render / Railway

1. Fork 本仓库
2. 在平台上创建 Web Service，连接仓库
3. 设置环境变量 `APP_PASSWORD` 和 `SECRET_KEY`
4. Start Command: `python plagiarism_checker.py`

## 默认密码

`orbit2024`（建议部署时通过环境变量修改）
