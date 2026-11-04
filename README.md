# 每日财经早报 Agent（云上定时 + 微信推送）

每天早上 **北京时间 07:00** 自动采集「隔夜美股 / 全球市场」+「国内前一工作日的财经政策」新闻，
调用 LLM 生成一份**结构化中文早报**，推送到你的**微信**（通过 Server酱）。

- 采集层：国际用 RSS（CNBC / MarketWatch / Investing / Yahoo / AP），国内用**长城证券实时资讯 API + 新浪滚动新闻 JSON**（均已验证稳定）作为主源；另接入**金十电报、财联社电报**作为"尽力而为"的可选源（接口有反爬，需在 GitHub 上首次运行验证，失败自动跳过）。华尔街见闻/财新/证券时报等 RSS 作为可选补充。任意单个源失效都会自动跳过，不影响整体。
- 精选层：LLM 把几十条原始新闻压缩成「今日重点 + 分栏目 + 利好/利空标注」的早报。
- 推送层：Server酱 → 微信。
- 语音层：每次同时生成 **`briefing.html` 网页版**，右上角带 **🔊 播放** 按钮，用浏览器中文语音朗读（免费，适合开车听）。可开启 GitHub Pages 让手机点链接即听。
- 运行：GitHub Actions 免费云定时，不用开电脑。

---

## 一、目录结构

```
morning-briefing/
├── briefing.py            # 主程序（采集→精选→推送→生成语音网页）
├── config.yaml            # 信源列表 + 采集窗口（最常改的文件）
├── requirements.txt
├── .env.example           # 本地调试用的密钥样例
├── .github/workflows/
│   └── daily.yml          # GitHub Actions 定时任务（含 GitHub Pages 可选发布）
├── 部署指南.md            # 零基础部署 + 语音功能 + 多端访问说明
├── sample-briefing.md     # 格式样板（示例内容）
├── sample-briefing.html   # 带播放键的样板网页（可直接预览）
└── README.md
```

---

## 二、部署步骤（约 10 分钟）

### 1. 把这个文件夹推到 GitHub
fork / 上传到你的一个 **私有** 仓库（私有仓库 Actions 免费额度足够个人用）。

### 2. 申请 Server酱（微信推送）
- 打开 https://sct.ftqq.com ，用微信扫码登录。
- 拿到你的 **SENDKEY**（一串字符）。

### 3. 配置 LLM（默认用 DeepSeek，便宜好用）
- 注册 https://platform.deepseek.com ，拿到 API Key。
- （可选）换成通义千问：Key 去阿里云百炼申请，`LLM_BASE_URL` 填 `https://dashscope.aliyuncs.com/compatible-mode/v1`，`LLM_MODEL` 填 `qwen-plus`。

### 4. 在 GitHub 仓库里填 Secrets
进入仓库 **Settings → Secrets and variables → Actions → New repository secret**，添加：

| 名称 | 值 |
|---|---|
| `LLM_API_KEY` | 你的 LLM API Key |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1`（或通义地址） |
| `LLM_MODEL` | `deepseek-chat`（或 `qwen-plus`） |
| `SERVERCHAN_KEY` | 你的 Server酱 SENDKEY |

### 5. 开启并测试
- 进入仓库 **Actions** 标签页，启用 workflow。
- 点 **Run workflow** 手动跑一次，几分钟后微信就能收到测试早报。
- 之后每天 07:00 自动推送。

---

## 三、本地调试

```bash
pip install -r requirements.txt
cp .env.example .env      # 填入你的密钥
python briefing.py        # 没填 SERVERCHAN_KEY 时，早报会直接打印在终端
```

---

## 四、常见问题

**Q：国内某些 RSS 抓不到？**
代码会自动跳过失效源。国内主源是**新浪滚动新闻 JSON**（已验证稳定可用）；华尔街见闻/财新/证券时报等 RSS 可能因站点改版失效，可在 `config.yaml` 的 `sources` 里增删调整（支持 RSS 与 `type: json` 两种源）。

**Q：金十 / 财联社 显示 0 条或报错？**
这两个是国内实时电报源，接口有反爬/签名/限流。本机（或某些网络）可能连不上，但部署到 **GitHub Actions（美国干净 IP）后通常可用**。首次部署后去 **Actions 运行日志**看一眼：若它们正常出条就保留；若持续 0 条，把日志发我，我按真实返回结构微调 `config.yaml` 里的字段映射即可。

**Q：想加长城证券的每日早间要闻？**
已接入 ✅。长城证券 H5 背后的实时资讯接口 `fintechapp.cgws.com/api/business-news/.../latest/list`（需带 `appPlatform`/`appVerName` 头）已在本环境实测稳定，返回标题+正文+北京时间，作为国内主源之一。

**Q：想只在工作日推送？**
把 `daily.yml` 里的 `cron: "0 23 * * *"` 改成 `0 23 * * 1-5`（周一到周五）。

**Q：想多/少抓一些新闻？**
改 `config.yaml` 的 `time_window_hours`（采集窗口小时数）和 `max_items_for_llm`（喂给 LLM 的条数，影响成本）。

**Q：成本多少？**
RSS 免费；LLM 每天一份早报约几分到几毛钱；GitHub Actions 个人免费额度足够。

**Q：想换推送渠道？**
`push_to_wechat` 函数是独立的一块，替换成企业微信/飞书机器人 webhook 即可（README 之外按需扩展）。
