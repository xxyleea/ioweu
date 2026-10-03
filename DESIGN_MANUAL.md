# IoU 设计手册（Design Manual）

> 版本：2026-10-03 · 对应 commit `0988fc9`
> 本手册面向任何要改动 IoU 界面的人。所有数值均来自 `static/style.css`（828 行，唯一样式文件）与模板实际用法。

---

## 1. 设计哲学

IoU 是邻里互助信用社区，视觉目标是 **"温暖、可信赖、像一张干净的便签纸"**：

- **奶油纸质底 + 深炭文字**：像社区公告栏，不像金融科技仪表盘。
- **粉彩（pastel）做信息编码**：颜色永远"有含义"（分类、状态、紧急度），不做纯装饰。
- **大圆角 + 轻阴影**：友好、无攻击性；绝不使用深色模式、玻璃拟态、渐变文字。
- **一次一个动作**：每个页面顶部只放一个主 CTA（Current Action / ws-actionbar），其余藏进 "More actions" 折叠。
- **聊天即交易工作台**：Task = Transaction = Conversation，能在一个页面完成的事不跳页。

---

## 2. 设计令牌（Design Tokens）

全部定义在 `style.css` 的 `:root`：

### 2.1 颜色

| Token | 值 | 用途 |
|---|---|---|
| `--bg` | `#faf6ee` | 全局页面背景（奶油色） |
| `--ink` | `#23272b` | 主文字 / 主按钮底 / 激活态 |
| `--muted` | `#7d838a` | 次要文字、占位符、icon |
| `--line` | `#eee5d5` | 边框、分隔线（带一点暖调，不用灰） |
| `--card` | `#ffffff` | 卡片底色 |
| `--yellow` | `#fdeab2` | 行动/警示粉彩、聊天"我方"气泡、credit chip |
| `--green` | `#dff0bd` | 完成、正向、成功 banner |
| `--pink` | `#fbd5e2` | Creative 分类、范围警告底 |
| `--purple` | `#e7ddfb` | avatar 底、Technology、in_progress |
| `--blue` | `#d7eafb` | open/confirmed 状态、Education |
| `--peach` | `#fbdfca` | Repair & DIY、agreement_pending |

功能色（不在 tokens 里，固定值）：

| 值 | 含义 |
|---|---|
| `#c0392b` | 危险/错误/负余额 |
| `#2e7d4f` / `#3e6b23` / `#3d6b35` | 成功、正向文字 |
| `#8a6212` | 黄色底上的深棕文字（deadline、kudos-done） |
| `#f3b0a7` | disputed / human_review 状态 |
| `#e8e6e1` | 中性灰底（completed/resolved/cancelled 状态、时间线轨道） |
| `#e05c5c` / `#e4572e` / `#e74c3c` | 红点/badge 类（铃铛角标、未读） |

**规则**：状态色只用上表语义；新增状态时先查语义表再新增，禁止随手取色。

### 2.2 字体

- 单一字体族：`Sora, Inter, system-ui, sans-serif`（Google Fonts 加载 Sora 400/600/700/800）。
- 字阶：

| 元素 | 大小 | 字重 | 备注 |
|---|---|---|---|
| `h1` | `2.1rem` | 800 | `letter-spacing:-.02em` |
| `h2` | `1.15rem` | 700 | 卡片内小标题 |
| `h3` | `1rem` | 700 | |
| `.section-title` | `1.3rem` | 800 | 页面区块标题 |
| `.stat-big` | `2.6rem` | 800 | 大数字（余额等），`letter-spacing:-.03em` |
| `.status` pill | `.75rem` | 700 | `text-transform:capitalize` |
| 正文 `p` | 1rem | 400 | `line-height:1.55` |

- 数字一律用 Sora 的 tabular 感（800 重），金额/积分是界面第一信息。

### 2.3 圆角

| 值 | 用在哪 |
|---|---|
| `22px`（`--radius`） | 所有卡片、modal、hub 块、轮播卡 —— 品牌的核心圆角 |
| `28px` | 登录/注册 `.auth-card`（更大的仪式感） |
| `18px` | 聊天气泡（对方 `18px 18px 18px 6px` / 我方 `18px 18px 6px 18px`）、topic-card、success-banner、action-banner、chat-task-card |
| `16px` | 侧栏用户卡、skill-section、ws-actionbar、offer-option |
| `14px` | 表单控件（input/textarea/select）、p4-node、ai-suggestion、addr-suggest、progress 子卡 |
| `12px` | 小 pill（notif-kind）、picker 高亮条、opt-input |
| `999px` | 一切"按钮/chip/标签"：btn-、genre-chip、filter-chip、status、urgency、credit-chip、match-badge、toast、tab、btab、hub-count |

口诀：**容器 22、卡片内块 14–18、标签按钮全胶囊**。

### 2.4 阴影

只有一个阴影变量：`--shadow: 0 10px 30px rgba(35,39,43,.06)` —— 极轻，几乎只是"纸浮起来一点"。
例外：modal-card 用 `0 18px 50px rgba(35,39,43,.25)`，toast 用 `0 12px 30px ...3`。
**禁止**加深阴影做层级，层级靠背景和边框。

### 2.5 间距

- 卡片内边距：`.card{padding:1.3rem 1.4rem}`，列表卡片 `1.5rem 1.6rem`。
- 区块间距：`.section-title{margin:2.2rem 0 .8rem}`、`.home-section{margin-bottom:2rem}`、堆叠卡 `margin-bottom:1.6rem`。
- 列表行距：`.action-list/.exchange-list/.card-list` 统一 `gap:.7rem`。
- 页面容器：`.content{max-width:1600px; padding:2.4rem clamp(1.2rem,3.5vw,3.5rem) 5rem}`。

---

## 3. 语义色系统

### 3.1 任务状态 pill（`.s-*`）

| 状态 | 背景 | 逻辑 |
|---|---|---|
| open / confirmed / scheduled | `--blue` | 开放/已确认/已排期（确定性状态） |
| negotiating / agreement_pending / completion_submitted | `--yellow` | **需要你行动** |
| in_progress | `--purple` | 正在进行 |
| completed | `--green` | 完成 |
| resolved / cancelled | `#e8e6e1` + muted 字 | 终态（灰色=退出舞台） |
| disputed / human_review | `#f3b0a7` | 冲突 |
| mediation | `--purple` | 调解中 |
| （旧）agreed / provider_confirmed | `--peach` | 遗留类名，仍在 CSS |

**4 段用户进度**（`STAGE_LABELS`）：Agree → Plan → Do → Complete →(Resolve)。
组件 `_progress.html`：`.progress4` 四列网格，节点底色 `rgba(0,0,0,.025)`，done 绿点 `#7fb069`，current 白卡+阴影，resolve 模式底色 `#fdeae6`。

### 3.2 紧急度（`.urgency-*`）

| 值 | 条件 | 样式 |
|---|---|---|
| urgent | ≤3 天 | `#f8cdd0` 底 `#a02225` 字 |
| soon | ≤7 天 | `--yellow` 底 `#8a6212` 字 |
| low | >7 天 | `--green` 底 `#3d6b35` 字 |

### 3.3 分类色（`.cat-*` → `--cat` 变量）

| 分类 | 色 |
|---|---|
| Daily Life / Errands | `--yellow` |
| Education / Home | `--blue` |
| Technology / Tech Help | `--purple` |
| Creative / Childcare | `--pink` |
| Repair & DIY / Pet Care | `--peach` |
| Transport | `#c9e8e4`（薄荷） |
| Companionship | `#f8cdd0`（深粉） |
| Family & Kids | `#cdeed8`（浅绿） |
| Pets & Animals | `#e5f0b8`（浅黄绿） |
| Community | `#ded7f8`（浅紫） |
| Tutoring | `--green` |

用法：`--cat` 驱动 request-card 左侧 7px 色条（`::before`）、rec-art 渐变、`cat-dot`。卡片必须保留内边距，否则色条压字（历史 bug）。

### 3.4 通知类型

- 行左侧 4px 条：`kind-action` 黄、`kind-success` 绿。
- `.notif-kind` 小标签：`k-action` `#fde3a7/#7a5a12`、`k-success` `#d6f2c0/#3e6b23`、默认 `#eceff3/#5a6472`。

---

## 4. 布局系统

### 4.1 骨架

```
.app-body (flex)
├── .sidebar        230px，sticky，100vh
└── .content        flex:1，max-width:1600px，居中
```

**Sidebar（从上往下）**：
1. `.brand-row`：logo2 图片（高 22px，无文字）｜🔔 铃铛+角标
2. `.brand-tag`：slogan "Help Comes Full Circle."（muted .85rem）
3. `.side-user` 卡：头像（36px 圆，紫底白字首字母）+ 名字/邮箱 + →（链到 /account）
4. `.nav-links`：Home ⌂ / Community ☷ / Messages ✉ / Help ? / Settings ⚙ —— 胶囊形，激活=ink 底白字
5. `.side-logout`：⏻ Sign out（margin-top:auto 贴底）

**Content**：每页 `page-head`（h1 + muted 副标题）打头。

### 4.2 断点

| 断点 | 行为 |
|---|---|
| `1250px` | content 内边距收紧到 `2rem 1.6rem` |
| `1100px` | hub-grid 2 列、home-2col 变单列 |
| `1000px` | home-primary 单列、progress4 变 2 列 |
| `900px` | bento/card-grid/two-col 单列、cf 卡缩小 |
| `820px` | chat 左栏 180px |
| `700px` | **移动端**：侧栏变顶部横条、nav 横向、隐藏 slogan 和用户邮箱 |

### 4.3 首页结构（v3）

1. page-head：Hello + `.credit-chip`（黄胶囊 💰 余额）
2. **Coverflow 轮播**（有推荐时）：中间大卡 scale 1、两侧 ±1 scale .8、±2 scale .62 半透明，3s 自动轮换，悬停暂停，左右圆形箭头，点侧卡跳到该卡
3. `.hub-grid` 4 块：My requests（peach）/ My tasks（mint）/ Post a task（yellow）/ Find a task（blue），块高由内容撑，hover 上移 2px
4. `.home-2col`（`1.4fr/1fr`，stretch 对齐）：左 = Action required（黄边条行）+ Active exchanges；右 = credit-panel；两列上下沿对齐
5. Moderator queue（仅 moderator）

### 4.4 聊天页（全出血 messenger）

- `.content-chat{max-width:none}` 撑满；workspace 高度 `100vh - 3.2rem`，两列：`260px` 会话栏 + 1fr 画布。
- 左栏：白卡圆角容器，行高自动（Carousell 风格：32px 圆形首字母 avatar + 标题/时间 + 单行预览 + 未读橙点），激活行 `--yellow` 底。
- 画布自上而下：**ws-context**（`<details>` 默认折叠，一行标题+状态+credits）→ **ws-actionbar**（单一主行动，"需要你"时 `#fdf6e3` 底）→ **chat-messages**（flex:1 滚动）→ **chat-toolbar**（阶段相关 ghost 按钮）→ **chat-input**（顶部细分隔线）。
- 气泡：对方白底 `18/18/18/6`，我方 `--yellow` `18/18/6/18`，连续消息收窄，特殊气泡（offer/meetup/settlement）带 💰📅⚖️ avatar 边框卡。
- 3 秒轮询 `/chat/{id}/feed`，状态变化整页刷新。

---

## 5. 组件清单

### 按钮

| 类 | 样式 |
|---|---|
| `.btn-primary` | ink 底白字，胶囊，`.75rem 1.4rem`，hover `opacity:.88` |
| `.btn-ghost` | 透明底 `--line` 1.5px 边，muted 字；`.danger` 变红边红字 |
| `.btn-small` | 小号胶囊（泡泡内行动） |
| `.btn-lg` | `padding:.85rem 2rem`（detail 页主 CTA） |

### 卡片 `.card`

白底、`--radius` 22、轻阴影、`1.3rem 1.4rem`。
变体：`.request-card`（左侧 7px 分类色条）、`.manage-card/.browse-card`（padding:0 + 内层自管，防色条压字）、`.hero-card`（左 8px 黄条）、`.action-card`（左 6px ink 条 + kicker 小字）、`.progress-card`、`.credit-panel`。

### 标签/chip

- `.status` / `.urgency` / `.match-badge`（ink 底黄字 ★For you）/ `.credit-chip` / `.hub-count` / `.skill-tag`：全部 999px 胶囊。
- `.genre-chip` / `.filter-chip` / `.topic-card`：分类色底（`--cat`），选中=ink 底白字；filter-chip 激活同理。
- `.board-tabs .btab`：白底轻阴影胶囊，激活 ink。

### 表单

- input/textarea/select：白底、`--line` 1.5px 边、14px 圆角、focus 时 `outline:2px solid ink`（不变色）。
- `.time-mode .radio-row`：bg 底 14px 圆角整行可点。
- **陷阱**：全局 `input{width:100%}`，radio/checkbox 必须 `width:auto !important`（`.check-row`、`.radio-row` 已处理，新组件要继承）。
- modal 内的表单控件更紧凑（12px 圆角）。

### Modal

`.modal` 固定全屏 `rgba(35,39,43,.45)` 遮罩；`.modal-card` 白底 22px 圆角、max-width 30rem、重阴影；底部 `.modal-actions` 右对齐。`[hidden]{display:none!important}` 全局生效，JS 切 hidden 即可。

### Toast

底部居中 ink 底白字胶囊，URL `?toast=` 驱动，2.5s 自动消失。

### 加载

`.spinner-overlay`：奶油色 90% 遮罩 + ink 顶边圆环；表单 submit 全局自动禁用按钮并显示 "Working…"（base.html 脚本）。

### 时间线（旧版 timeline）

横向步骤条，轨道 `#e8e6e1` 3px，done 绿、current 黄点+光晕。新页面用 `.progress4` 替代。

---

## 6. 各页面 UI 逻辑速查

| 页面 | 核心逻辑 |
|---|---|
| `/` Home | credit-chip → 轮播 → hub 四块 → 2 列（行动+交易｜信用面板）。无新闻。 |
| `/browse` | tabs（For you / Nearby·区名 / All）+ 分类 chips + 排序（Recommended/Distance/Newest/Credits/Time），JS 端 render() 不重载。`/recommended` 301→`/browse`。 |
| `/requests/{id}` | 顶部 Current Action 卡（kicker+label+单 CTA+More actions）→ progress4 → 详情卡 → offer 行。 |
| `/chat/{id}` | 交易工作台：上下文折叠条、actionbar 单行动、泡泡流、阶段工具条、输入框。Offer 泡泡两键：Accept / Counter-offer（预填对方金额）。 |
| `/messages` | 交易行列表（对方、状态、预览、"Action required" 黄字）。 |
| `/notifications` | System/Messages 两 tab；行=kind 标签+body+时间+CTA。 |
| `/new-request` | 4 步 wizard（分类→详情→参考价+报价→预览）。详情步先选 Online/In person，Online 隐藏地址并标 🌐；预览卡右侧 badge 列：open pill + "You offer N ± M credits"。报价超出参考范围需勾确认。 |
| `/register` | 8 步 wizard（语言→账号→住址→HKID→生日滚轮→主题→技能→**Consent**）。最后一步 4 必填+1 可选（默认关），前后端双校验。 |
| `/account` | 左 profile 卡（avatar、双列 stats、meta 列表）｜右技能+编辑表单（含 consent 开关）。 |
| `/help` | 条款+FAQ，信用规则文案（MIN_BALANCE=-5、负数不能发布/确认）。 |

---

## 7. UI/UX 信息架构（分页与功能地图）

> 核心范式：**Task = Transaction = Conversation**。一个请求（request）从头到尾只对应一条聊天记录，谈价、约时间、交付、争议全部发生在聊天里，尽量不跳页。

### 7.0 全站地图（按用户旅程分组）

```
未登录
├── /login 登录                    （demo123 三个演示号）
├── /register 注册（8 步 wizard）
│     语言 → 账号 → 住址 → HKID → 生日滚轮 → 兴趣主题 → 技能 → 注册同意(4必填+1可选)
│
登录后骨架：侧栏 5 项导航
│
├── 🏠 Home /
├── ☷ Community /browse
├── ✉ Messages /messages（列表） → /chat/{id}（工作台）
├── ? Help /help（条款+信用规则+FAQ）
└── ⚙ Settings /account（=个人资料+技能+consent）
```

其余页面全部从上述页面"长出来"，不在主导航：

```
任务生命周期
  /new-request        发布（4 步 wizard）
  /requests/{id}      任务详情（Current Action + 进度 + 详情）
  /requests/{id}/review   交付复核（owner 查看证据）
  /requests/{id}/dispute  发起争议表单
  /requests/{id}/mediation  查看"Fair Resolution"并 accept/reject

个人中心
  /my-posts    我发布的（管理卡）
  /helping     我在帮的（管理卡）
  /history     信用流水账
  /notifications  通知（System / Messages 两 tab）

增值功能（队友实现，入口较浅）
  /chain-invitations  任务链邀请（opt-in）
  /chains/suggestions, /chains/{id}  任务链建议/详情
  /community-test    社区公平观测试（+5 credits）

Moderator 专属
  /disputes/{id}/review   争议裁决
  /moderator/requests/{id}/policy  争议政策
```

### 7.1 页面功能明细

| 页面 | 承载功能 | 关键 UI |
|---|---|---|
| `/` Home | 个人 cockpit：余额、待办、推荐、快捷入口 | credit-chip、coverflow、hub 四块、Action required 黄边条行 |
| `/browse` | 找任务：浏览→筛选→点进详情 | tabs(For you/Nearby/All)+分类 chips+排序，JS 无刷新渲染 |
| `/requests/{id}` | 任务"档案页"：状态、进度、上下文 | Current Action 卡（单 CTA 原则）+ progress4 四段进度 |
| `/chat/{id}` | **交易工作台**：谈价/约时间/交付/争议一站式 | 左会话栏+右画布；ws-actionbar 单行动；特殊消息=可操作卡（offer/meetup/settlement） |
| `/messages` | 交易收件箱：哪些对话需要我 | 行内直接显示状态和"Action required" |
| `/notifications` | 系统通知 + 消息提醒两 tab | kind 标签色条、未读加粗 |
| `/my-posts` `/helping` | 我发布/我帮忙的两本台账 | 紧凑 manage-card：credits 左、CTA 右 |
| `/account` | 资料、技能、隐私 consent | 左 profile 卡 + 右编辑表单 |
| `/help` | 产品规则说明书（信用规则 -5、冻结期等） | 条款+折叠 FAQ |
| `/new-request` | 发布任务向导 | 4 步：分类→详情(Online/In person)→参考价+报价→预览 |
| `/history` | 信用分录流水 | 正负金额双色 |

### 7.2 核心用户流（对应后端 11 个状态）

**流 A：发布并成交（requester 视角）**
browse 看到邻居任务 → 决定发布 → `/new-request` 4 步 → 状态 `open` → 收到 offer（通知+聊天）→ 聊天里 Accept → `agreement_pending` → 点 Confirm Exchange（扣 credits 进托管）→ `confirmed` → 约见面 `scheduled` → 开工 `in_progress` → 对方提交完成 `completion_submitted` → 复核证据 Confirm → `completed_pending_release`（3 天争议窗）→ `completed` → 可送 Kudos（+10% 奖励）。

**流 B：帮忙赚 credits（provider 视角）**
Home 轮播/hub → `/browse` 选任务 → 详情页 Offer → 聊天谈判（可 Counter-offer，预填金额）→ 对方 Accept + Confirm → 约见面 → Start → Mark as Complete（传证据）→ 等 owner 确认 → 3 天后 credits 到账。

**流 C：争议阶梯（关键 demo 路径）**
任一方 Raise dispute → `disputed`（credits 冻结，双方可 Suggest a resolution 互相还价）→ 3 天未和解 → IoU 出 **Fair Resolution**（`mediation`，双方 1 天内 accept/reject）→ 仍僵持 → `human_review`（moderator 在 `/disputes/{id}/review` 裁决）→ `resolved`。
注：交付确认后还有 `dispute-after`（3 天窗口内追诉）。

### 7.3 信息设计原则（加工能前请遵守）

1. **单页单主行动**：每页顶部只一个 ink 主按钮；次要操作 ghost 或藏进 `<details>`（More actions / ws-context）。
2. **状态可见性**：任何任务卡都必须能看到状态 pill + credits；列表行要显示"下一步是谁的行动"（exchange_flag 逻辑：needs/cta/label/href）。
3. **黄=轮到你**：凡是用户要做事的地方用 `--yellow`（action-row 边条、ws-actionbar 底、completion_submitted、轮播激活行）。
4. **钱的三态**：报价（offered，灰字）→ 托管中（held，蓝字 "held safely"）→ 已释放（released，绿）。聊天上下文条里永远写清楚。
5. **新功能落位**：优先挂进 `/chat/{id}`（交易内）或 Home 区块；只有独立实体（像任务链）才配新顶层页；新页面必须接侧栏导航或从现有页有明显入口。
6. **空状态要有指引**：每个列表的空态都带下一步动作文案，不放干巴巴的 "No items"。
7. **权限闸**：balance ≤ 0 不能发布、不能确认交换；发布前 wizard 第 3 步强制看参考价；超出参考范围必须勾确认。新涉及 credits 的功能沿用这套闸。

---

## 8. 动效规范

| 场景 | 时长/曲线 |
|---|---|
| 通用 hover 上浮 | `transform .12s ease`（translateY(-2px)） |
| 轮播换位 | `.6s ease`（transform+opacity+filter） |
| wizard 步骤切换 | `fadeIn .25s ease`（上移 6px） |
| toast | `.25s` 淡入上移 |
| spinner | `.9s linear infinite` |
| 按钮按下感 | genre-chip `.08s` translateY(-1px) |

原则：动效只用于"位置变化"和"步骤切换"，不做装饰性动画。

---

## 9. Emoji & 图标使用规则

1. **标题前不放 emoji**（对齐问题）——h1/h2/section-title 一律纯文字。
2. emoji 允许出现在：hub 块、rec-art、气泡 avatar（💰📅⚖️👋）、meta 行（📍⏰🌐）、credit-chip 💰。
3. 导航图标用字符：⌂ ☷ ✉ ? ⚙ ⏻，固定 1.2rem 宽居中。
4. logo 一律用 `iou-logo2.png`（侧栏高 22px）；favicon 用 `favicon.png`（= iou-logo.png 裁剪版）。

---

## 10. 品牌资产

- Slogan：**"Help Comes Full Circle."**（侧栏 tag + 登录页 + 首页副标题）
- Logo：`static/iou-logo2.png`（页内）、`static/favicon.png`（标签页）
- 字体：Sora（Google Fonts）
- 名字写法：**IoU**（i 小写、o 小写、U 大写）

---

## 11. 开发约定（改样式前必读）

1. **追加覆盖模式**：新样式追加到 `style.css` 文件末尾（后写者赢），旧规则尽量不动；确需改旧值就直接编辑。
2. **CSS 陷阱**：
   - `[hidden]{display:none!important}` —— 显示用 `el.hidden=false`。
   - `input{width:100%}` —— 单选/复选务必 `width:auto !important`。
   - `.request-card::before` 的 7px 左侧色条需要卡片内层有 padding —— padding:0 的卡片（browse/manage）要在内层补。
   - Jinja 模板里 `genre_emoji` 的 key 是全小写，取值用 `.get(item.category|lower, '📋')`。
3. **响应式**：新页面必须考虑 700/900/1100 三档；聊天页走全出血例外。
4. **状态语义**：新增状态 pill 时先扩展 `.s-*` 映射，同时更新 `STAGE_MAP`（`app.py`）和本手册 §3.1。
