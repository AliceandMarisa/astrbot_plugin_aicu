🔍 AstrBot AICU - B站数据分析插件

一个用于 AstrBot 的插件，可查询 B 站用户的评论、弹幕、直播互动及入场记录等多维度数据，并生成可视化的图片报表。

### 📁 目录结构

```
astrbot_plugin_aicu_analysis/
├── main.py               # 核心插件逻辑
├── template.html         # 评论查询渲染模板
├── template_danmaku.html # 弹幕查询渲染模板
├── template_live.html    # 直播弹幕渲染模板
├── template_entry.html   # 入场记录渲染模板
├── metadata.yaml         # 插件元数据
├── requirements.txt      # 依赖库
├── _conf_schema.json     # 配置定义
└── README.md             # 说明文档
```

### ✨ 功能特性

- 用户评论分析：获取用户评论记录、活跃时段、发言习惯
- 视频弹幕查询：查看用户在视频中的弹幕历史
- 直播弹幕分析：分析用户在直播间的互动记录
- 入场记录追踪：查询用户进入直播间的时间、观看时长等数据
- B站基础资料：头像、等级、粉丝数、关注数、个性签名
- 设备识别：展示用户评论时使用的设备型号
- 历史昵称：显示用户曾用名记录
- 粉丝牌与大航海：查询用户拥有的粉丝牌和大航海信息
- AI评论分析（可选）：使用AI分析用户评论特点和发言风格
- 精美报表：使用 Playwright + Jinja2 生成 HTML 并渲染为图片发送

### 🛠️ 安装与依赖

使用前请确保在控制台执行以下命令安装必要的 Python 依赖：

```bash
pip install "curl_cffi>=0.7.0" playwright jinja2
playwright install chromium
```

### ⚙️ 配置说明 (Cookie)

为了获取完整的用户信息（如头像、名称等），**强烈建议**配置 AICU Cookie。

#### 1. 获取 Cookie

**PC 端：**

* 登录 [aicu.cc](https://aicu.cc)
* 按 `F12` 打开开发者工具
* 点击「网络」(Network) 标签
* 刷新页面
* 在请求列表中点击任意一个请求，复制请求头（Request Headers）中的 `Cookie` 值。

![Cookie 获取示意图](https://youke1.picui.cn/s1/2025/12/02/692e4b26b66ac.jpg)
*（如图所示，我们只需要划线部分的 Cookie 值即可）*

**移动端：**

* 可以使用 **Via 浏览器** 等支持查看网络资源的浏览器获取 Cookie。
* *Via 操作简述*：点击地址栏左上角的角标 -> 选择查看 Cookie -> 复制即可。

> ⚠️ **注意**：
> 如果不配置 Cookie，可能导致头像和名称无法正常显示。

#### 2. 填写配置

| 指令 | 说明 |
|---|---|
| `AICU 网站 Cookie (必须包含 ASession=...)` | 填写获取到的cookie |
| `max_danmaku_count` | 最大弹幕查询数量（默认：100） |
| `max_reply_count` | 最大评论查询数量（默认：100） |
| `dd_page_size` | 入场信息每页查询数量（默认：20） |
| `enable_video_info` | 是否启用视频信息获取（默认：true） |
| `enable_ai_analysis` | 是否启用AI分析评论功能（默认：false） |
| `max_ai_comments` | AI分析的最大评论条数（默认：20） |
| `browser_timeout` | 浏览器渲染图片的超时时间（秒，默认：30） |
| `ai_analysis_timeout` | AI分析请求的超时时间（秒，默认：30） |
| `browser_headless` | 是否使用无头浏览器模式（默认：true） |

---  

### 💬 使用指令

| 指令 | 说明 |
|---|---|
| `/uid <UID>` | 查询用户评论数据（支持多种UID格式） |
| `/弹幕 <UID>` | 查询用户视频弹幕记录 |
| `/直播弹幕 <UID>` | 查询用户直播弹幕记录 |
| `/入场 <UID>` | 查询用户入场记录及粉丝牌信息 |
| `/b站帮助` | 显示插件帮助信息 |

---

### 📊 数据说明
数据来源：本插件数据主要来自 aicu.cc 及相关API
隐私保护：仅查询公开可获取的用户数据

### ⚠️ 注意事项
Cookie配置：为获取完整功能，建议配置有效的AICU Cookie
查询限制：大量查询可能导致API限制，请合理使用

### 🤝 贡献指南
欢迎提交 Issue 和 Pull Request 来改进本插件！

