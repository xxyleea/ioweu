# IoU — UI/UX 深度交接文档（评审用）

> 目的：请从「交互逻辑 / 用户流程 / 排版与视觉层级」三个维度给出修改意见。
> 本文档精确描述**当前实现**，非设计稿。改动成本要按「FastAPI + Jinja 服务端渲染、SQLite、无 JS 框架」评估。

---

## 1. 产品与硬性约束

- **IoU**：邻里互助积分平台。HacKU 2026 FinTech Track 2「被忽视的价值（Invisible Made Visible）」。credit = 信任账簿，非货币；初始 10 分；余额下限 **-20**；demo 必须能走通**纠纷→协商→AI 调解→人工仲裁**链路。
- 截止：代码冻结 10/4 13:00。交付：public repo + live demo 或 3min 视频 + pitch deck。
- 技术：单文件 `app.py`（~1950 行）+ `templates/*.html` + 单 `static/style.css`。无构建步骤、无前端框架、无组件库。**任何重构都要考虑这个成本模型**。
- 运行：`uvicorn app:app --port 8000`。演示账号（密码均 `demo123`）：`maya.lam@example.com`（需求方）、`jason.wong@example.com`（帮忙方）、`moderator@example.com`（仲裁员）。

---

## 2. 设计 Token（`static/style.css :root`）

| Token | 值 | 用途 |
|---|---|---|
| `--bg` | `#faf6ee` | 全局奶油底 |
| `--ink` | `#23272b` | 主文字/主按钮/active 导航 |
| `--muted` | `#7d838a` | 次要文字 |
| `--line` | `#eee5d5` | sidebar 分隔线 |
| pastel | `--yellow:#fdeab2` `--green:#dff0bd` `--pink:#fbd5e2` `--purple:#e7ddfb` `--blue:#d7eafb` `--peach:#fbdfca` | 统计卡/状态/品类 |
| `--radius` | `22px` | 卡片圆角 |
| `--shadow` | `0 10px 30px rgba(35,39,43,.06)` | 卡片投影（很轻） |
| 字体 | Sora 400/600/700/800 | h1=2.1rem/800，h2=1.15rem/700 |

**品类色**（卡左色条+色点+chip）：Daily Life=黄 / Education=蓝 / Technology=紫 / Creative=粉 / Repair&Diy=peach / Transport=`#c9e8e4` / Companionship=`#f8cdd0` / Family&Kids=`#cdeed8` / Pets=`#e5f0b8` / Community=`#ded7f8`。

**状态药丸底色**（`.s-*`）：open=绿 / negotiating=黄 / agreement_pending=peach / confirmed=蓝 / scheduled=in_progress=紫 / completion_submitted=黄 / disputed+human_review=`#f3b0a7`(红粉) / completed+resolved=灰 `#e8e6e1`。

⚠️ 注意：negotiating 与 completion_submitted 同色、scheduled 与 in_progress 同色——语义不同却同色的潜在混淆点。

---

## 3. 全局框架

- **布局**：`display:flex`；左 sidebar `230px` sticky 全高；右内容区 `max-width:1440px; margin:0 auto; padding:2.4rem 3rem 5rem`。
- **Sidebar**（上→下）：品牌 IoU（conic-gradient 圆点）→ 🔔 铃铛（未读数 = 未读通知 + 未读消息，红底白字角标）→ 标语 → 7 个导航 pill（active=黑底白字）→ 底部用户卡（头像+名+邮箱+⏻登出）。
- **Toast**：所有 POST redirect 带 `?toast=...`；`base.html` 右下角黑色 toast，2.5s 消失，URL 用 `replaceState` 清掉参数。
- **防重复提交**：全局 submit 监听，按钮变 "Working…" 禁用 2.5s 自动恢复。
- **Modal**：`.modal[hidden]` 全屏遮罩 + `.modal-card`；`data-modal-open/close`；全局 CSS `[hidden]{display:none!important}`。
- **实时性**：聊天页每 3s GET `/chat/{id}/feed`（返回消息 HTML 片段 + `X-Status` header）；status 变化→整页 reload。无 WebSocket。
- **移动端**：仅 <900px 时 card-grid 单列、<1200px 聊天折叠单栏。基本未适配。

---

## 4. 页面级规格

### 4.1 Home `/`
自上而下：
1. `.page-head`：「Hello, {name}」h1 + 副文 + 右上「+ New request」黑 pill。
2. `.stats-grid` 4 张 pastel 大数字卡：**Available credits**(黄,余额) / **Held in escrow**(蓝,+进行中应收) / **Exchanges done**(粉) / **Kudos earned**(绿)。
3. `.quick-actions`：Ask for help(黑) / Help a neighbour / Track my posts（ghost）。
4. 📌 Pinned（如有）：完整任务卡（同 4.2）。
5. `.two-col.home-tasks` 两列彩色 mini 任务列表：
   - 左列「Your tasks in progress」橙系（`#fdf1e3` 底 + `#f0a860` 左边条+标题前色点）——我作为 requester；
   - 右列「You're helping with」薄荷系（`#e7f3f2` + `#5cb8ab`）——我作为 provider。
   - 每行 = 状态药丸 + 标题 + 积分，整行链到 detail。空态给引导链接。
6. Credit activity 链接卡 → `/history`。

**已知问题**：Pinned 卡是完整大卡占整行；双列区与统计卡之间节奏仍紧；铃铛虽在 sidebar，Home 不再有任何「待办」聚合视图。

### 4.2 任务卡（request_card，用于 Get help / Help others / Pinned）
白卡 + 左 4px 品类色条。左列：品类点+名、h2 标题（链 detail）、描述全文、meta 行（requester/helper · 📍 · ~分钟 · 🕒/⏰）。右列 `.badge-col`：📌 置顶钮（灰→彩色切换）、★ For you、urgency pill（Urgent=红/Soon=黄）、状态药丸。下方：报价条（黄底）、✨ Neighbourhood reference 条、Agreed value 条（如有）、per-status CTA 区（见 §5 矩阵）。

### 4.3 Community `/browse`
- 头部：标题+说明；品类 chips（active=黑）；Sort 下拉（For you / Newest / Credits / Near me），前端 JS 排序。
- 两列 card-grid。**精简卡**：品类点+名 → 标题+状态药丸（h2 内 flex）→ meta 一行（时间 · 地址）→ **积分报价大字号（b=1.55rem）** + 小号「View detail →」（0.75rem，70% 透明）。整卡 `<a>` 包裹。
- ★ For you（技能匹配）排最前。

### 4.4 Request detail `/requests/{id}`
1. Back + h1（品类 emoji + 标题 + **标题旁状态药丸**）+ meta 行；右侧「💬 Messages(n)」（参与者且有消息时）。
2. **Hero 卡**（按状态着左边条）：大状态药丸 + 「Your move」(黑 pill) 或「Waiting for the other side」+ 状态说明句 + agreed_value/escrow 提示 + **唯一主 CTA**（modal 或链接）。open 状态无 CTA（Offer 按钮移到了详情下方）。
3. **Progress 时间线**（open 不显示）：10 步横向点线，done=绿点/current=放大。
4. Request details 卡：描述、报价条、AI 参考价条、completion_note（如有）。
5. open 且非 owner → 详情下方「🤝 Offer to Help」大黑按钮；open 且 owner → 灰字等待提示。
6. 页尾一组条件 modal：offer-modal（两选项卡：Accept offer N credits | Make an offer+输入，默认 AI 值，超范围黄条警告但不阻止）/ confirm-exchange-modal / start-task-modal / complete-modal（完成说明+≤3 证据+确认勾选）。
- 「How this exchange works」已移至 `/help`。

### 4.5 Chat workspace `/chat/{id}`（核心：Task = Transaction Context）
三栏 grid（<1200px 单栏）：
- **左**：threads 列表（头像色点+标题+预览+未读「● n」）。
- **中**：顶部任务卡（标题+状态+积分+对方）→ **Action Required Banner**（黄底，当前行动方+主按钮，与 hero 同源）→ 消息流：普通气泡（我方右黑/对方左白）+ 系统行（灰居中，托管/放款等）+ 特殊卡片（offer / meetup / settlement：金额、状态、内嵌 Accept/Counter/Decline 按钮）→ 底部输入框 + 工具栏（按状态显示 Propose Meetup 等）。
- **右**：Task Summary 卡（状态、agreed credits、escrow 状态、✓/○ 迷你进度、View full task 链接）。

### 4.6 Messages `/messages` 与 Notifications `/notifications`
- Messages：单白卡面板内行式列表（与聊天左栏同款 `.ws-thread`）：标题+未读、预览 90 字、右侧状态+时间。
- Notifications：System / Messages 双 tab（各带计数）；System 行 = kind chip（Action 黄 / Done 绿 / Info 灰）+ 正文 + 时间 + 「Act now →」或「View →」（点击标已读并跳 link）。

### 4.7 其余页面
- `/new-request`：3 步向导（①品类大卡选 ②详情：标题/描述/地址/时长/难度/urgency/时间模式(Specific datetime | Flexible deadline) ③AI 建议价+确认）。无错误页；报价超 AI 范围仅警告可继续。发布后落 `/my-posts` toast。
- `/history`：积分流水表（escrow hold/release/kudos/dispute 调解）。
- `/mediation`：AI 调解页（双方 accept 即执行；reject→human_review）。
- `/dispute-review`：版主仲裁（人工审核约 3s 后随机 approve/deny 自动结案——demo 用）。
- `/help`：How IoU works 6 步 + 社区条款 + FAQ。
- `/account`：资料、区域、技能（影响 ★ For you）。

---

## 5. 交互核心：状态 × 角色 行动矩阵（hero_for 实际逻辑）

| status | requester(owner) | provider(helper) | 访客 |
|---|---|---|---|
| open | 「Posted. Waiting…」无 CTA | 「You can offer to help」→ Offer 按钮（详情下方） | 同 provider |
| negotiating | 收到报价→「Review Offer」→chat；己方报价后→等待 | 同左对称 | 仅浏览 |
| agreement_pending | 未确认方→「Confirm Exchange」modal；已确认方→等待 | 同左 | — |
| confirmed | 可「Propose Meetup」→chat；收到邀约→「Review Meeting」 | 同左 | — |
| scheduled | 「Start Task」modal（双方可见） | 同左 | — |
| in_progress | 「The helper is working」无 CTA | 「Mark as Complete」modal | — |
| completion_submitted | 「Review Completion」→ `/review` 页 | 等待 | — |
| disputed | 「Resolve Dispute」→chat（settlement 卡） | 同左 | — |
| human_review | 「moderator reviewing」无 CTA（~3s 自动结案） | 同左 | — |
| completed/resolved | 无 CTA；Kudos(+10%) 或 3 天内 dispute-after | 同左 | — |

**铁律**：任意时刻每角色只有一个主 CTA；轮不到你 = 「✓ You're all set. Waiting…」文案，**不给假按钮**。Guard 失败一律 redirect 回 detail/chat + toast，不出错误页。

---

## 6. 关键流程（实测可走通）

1. **Happy path**：visitor 在 browse 点卡 → detail 底部 Offer → modal 二选一 → toast → 双方进 agreement_pending → 各点 Confirm Exchange（校验 requester 余额 ≥ agreed-20）→ escrow → chat 里 Propose Meetup（date/time/location）→ 对方 Accept → scheduled → Start Task → in_progress → provider「Mark as Complete」modal（note+证据+勾选）→ owner `/review` 页 Confirm → 放款 + toast。
2. **议价**：offer 卡在 chat 里 Accept / Counter（填新值）/ Decline（decline 后请求回 open）。
3. **纠纷**：in_progress/completion_submitted 时可 Open Dispute（证据必填前后端双校验）→ disputed（冻结）→ chat 内 settlement 卡互相让步 → 任一方 Ask IoU to mediate → mediation 页双方 accept 执行 / 任一方 reject → human_review → ~3s 自动裁决。
4. **通知同步**：每步都 notify(kind=info|action|success)；铃铛角标、sidebar Messages 红点、thread 未读、toast 四处同步。

---

## 7. 文案规则
- AI 定价永远叫 **「Neighbourhood reference」**，禁止 "AI price/decision"；永远展示（即使报价相同）。
- 错误恢复优先：不出 error page；超范围=警告可继续。
- 时间线 10 步固定文案（见 §4.4.3），状态→步数映射：`open:0 negotiating:1 agreement_pending:2 confirmed:4 scheduled:5 in_progress:6 completion_submitted:7 completed:10 disputed/human_review:7 resolved:10`。

---

## 8. 待评审的痛点（按用户痛感排序）
1. **Home 密度**：统计卡→快捷行→Pinned 大卡→双列→流水卡，节奏与留白需要系统方案（是否合并统计卡？Pinned 是否降级为一行？）。
2. **通知架构冗余**：铃铛(System+Messages tabs) / sidebar Messages / Home 无聚合视图——三者职责需要裁决（用户倾向：通知全进铃铛，sidebar Messages 可砍，但未确认）。
3. **视觉层级**：状态药丸 11 种、颜色复用（§2 ⚠️）、urgency、★、AI 价、报价、CTA 在同一卡上争注意力。
4. **Chat 三栏过重**：Action Banner / hero 卡 / 右侧 Summary 三处都在回答「现在该谁干嘛」，需要合并或分层。
5. **Hero 卡 vs 卡内 CTA**：detail 页 hero 无按钮(open)而按钮在详情下方，状态间 CTA 位置不统一。
6. **移动端**未做；时间线 10 步在小屏必然溢出。
7. **Demo 路径**：评委 3 分钟内要从「发任务」看到「纠纷仲裁」，当前需要两个账号两台浏览器——是否要做一键 demo 模式？

## 9. 给评审 agent 的要求
- 建议按「改动成本 ≤ 服务端模板+CSS」约束提；不要假设 React/组件库。
- 优先级：demo 叙事流畅度 > Home 信息架构 > 卡片视觉层级 > 通知冗余 > 移动端。
- 输出格式：问题 → 建议方案 → 影响页面/模板文件 → 预估工作量（S/M/L）。
