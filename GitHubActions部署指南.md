# @binancezh 监控 — GitHub Actions 部署指南

> 监控运行在 GitHub 的服务器上（墙外，能直连 X），**每 5 分钟**检查一次 @binancezh，
> 一发新推文就通过 **Server酱 → 方糖服务号 → 你的个人微信** 推送。
> 全程免费、不需要信用卡、不需要家里电脑/家云开机。

## 为什么是这个方案

| 环境 | 能否连 X | 结论 |
|---|---|---|
| 家云（192.168.1.3） | ❌ 域名全被 GFW 封 | 不可用 |
| Oracle 免费服务器 | ✅ | 需要信用卡验证，你的卡（livi 已停用/蚂蚁无卡产品）走不通 |
| **GitHub Actions** | ✅ 墙外免费服务器 | **采用** |

数据源用 Twitter 官方 widget syndication 接口（免登录、免 cookie），已实测能拿到 @binancezh 全部历史推文。

---

## 消息在微信里怎么显示（已优化）

Server酱 的微信消息卡片**不点开直接展示的是 title（≤32 字符）+ short 摘要（≤64 字符）**，正文 desp 要再点一层才看到。所以监控程序把**推文原文直接放进 title 和 short**：

- 微信消息列表 / 通知栏：直接显示推文原文（超长自动截断）
- 点开卡片：short 摘要 + 完整原文 + 原推链接 + 发布时间

> 如果你之前上传过旧版 monitor.py，按下面「更新已部署的仓库」覆盖一次即可生效。

---

## 部署步骤（约 5 分钟）

### 1. 注册 / 登录 GitHub
打开 https://github.com → Sign up（有账号直接登录）。免费，**不需要信用卡**。

### 2. 创建仓库（必须选 Public 公开）
1. 右上角 **+ → New repository**
2. Repository name 填 `binancezh-monitor`
3. **选 Public**（⚠️ 必须公开：公开仓库的 Actions 时长无限；私有仓库每月只有 2000 分钟，不够每 5 分钟跑一次。仓库里只有监控代码，没有任何敏感信息）
4. 点 **Create repository**

### 3. 上传文件
在新建的仓库页面点 **Add file → Upload files**，把下面 **4 项**拖进去（保持目录结构）：

```
monitor.py
requirements.txt
.gitignore
.github/workflows/monitor.yml
```

（在 `F:\work日常杂任务\binancezh-monitor\` 目录下可以找到这 4 项；
`.github` 是个文件夹，直接整个拖进去即可，网页上传会保留目录结构。）

⚠️ **不要上传 config.env**（里面有你的密钥，.gitignore 已排除它）。

拖完后点 **Commit changes**。

### 4. 配置推送密钥（Secret）
仓库页面 → **Settings → Secrets and variables → Actions → New repository secret**：

| Name | Value |
|---|---|
| `SERVERCHAN_KEY` | `SCT439703ThOhUqpdLdlhPkHmmuS9BuCFr` |

### 5. 测试推送（30 秒验证整条链路）
仓库页面 → 上方 **Actions** 标签 → 左边选 **binancezh-monitor** →
右边 **Run workflow** 按钮 → mode 选 **test-push** → 点绿色的 **Run workflow**。

→ 几秒后刷新，出现绿色 ✅；你的微信应收到「✅ 推送测试」消息。

### 6. 启动监控
再点 **Run workflow** → mode 选 **check** → Run。

- **第一次 check 只会记录历史推文、不推送**（防止一上来轰炸你几十条历史）
- 之后每 5 分钟自动运行；@binancezh 发新推文 → 微信立刻收到

---

## 日常使用

- **看运行记录**：仓库 → Actions → 最近一次 run → 点进去看日志
- **看推送了什么**：仓库里的 `.last_seen.json` 就是已推送清单
- **改监控账号**：编辑 `.github/workflows/monitor.yml` 里 `TW_SCREEN_NAME: binancezh` 改成别的账号
- **停止监控**：Actions → binancezh-monitor → Disable workflow

## 想立刻验证"真实推文推送"（不想等新推文）
1. 等第一次 check 跑完（记录完历史）
2. 仓库里打开 `.last_seen.json`，删掉**最新那条推文的 ID**（最长的那串数字），Commit
3. 手动 Run workflow（check）→ 微信会立刻收到这条"新推文"推送

## 故障告警
如果连续 5 次抓取失败（如 X 改了接口结构），会主动往你微信推「⚠️ 监控源异常」，不用盯着 GitHub。

## 备选方案（暂不使用）
- Oracle 免费服务器：等你哪天有可用的 Visa/Mastercard 卡（如内地双币信用卡），随时可以平移过去，代码不用改
- 家云：已确认连不上 X，放弃

## 更新已部署的仓库（代码有改动时）

本地 `F:\work日常杂任务\binancezh-monitor\monitor.py` 是唯一权威版本，改动后覆盖到 GitHub：

1. 仓库页面 → 点开 `monitor.py` → 右上角**铅笔图标（编辑）**
2. 全选删除，把本地新版内容整个粘贴进去（或 Add file → Upload files 传同名文件覆盖）
3. 点 **Commit changes**
4. 之后每次 Actions 运行用的就是新代码；想立即验证就手动 Run workflow（check）
