# Orbit Checker

OKX Orbit 帖子查重工具 — 对比两篇帖子的文本相似度，高亮重复内容，支持图片对比。

## 功能

- 支持 OKX Orbit 帖子链接（含短链接 oyidl.me）
- 自动提取帖子内容、作者昵称、渠道ID、发帖时间
- 文本相似度分析（TF-IDF + Jaccard + SequenceMatcher）
- 句级高亮显示相似片段
- 图片对比（支持放大查看）
- 批量粘贴：自动从文本中提取多个链接
- 密码保护

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
