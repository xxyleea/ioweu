# IoU — UI 功能简介

> Neighbourhood mutual-credit board. Pastel 多彩设计,桌面端 dashboard 布局(左侧导航 + 内容区),黑色胶囊主按钮,每个任务分类一个专属 pastel 色。

## 页面结构(7 个页面)

### 0. 注册 / 登录
- **登录页**:pastel 渐变底 + 居中卡片,demo 账号提示
- **注册向导**(7 步独立页面,逐步跳转):
  1. 语言选择(English 默认 / 粵語 / 普通话)
  2. 账号(姓名 / 邮箱 / 密码)
  3. 地址:Region(港岛/九龙/新界)→ District 联动下拉 → 详细地址
  4. HKID 输入(仅验证用途,Account 页打码显示)
  5. 生日:**年/月/日三列滚轮选择器**(滑动吸附),自动计算年龄
  6. 技能大 topic:10 张彩色大卡片多选(🏠📚💻🎨🔧🚗🧑‍🤝‍🧑👶🐶🌱)
  7. 细分技能 chips(只展开已选 topic,卡片固定高度内滚动)
- 最后一步才出现 **Join IoU** 按钮 → 转圈 2 秒 → Welcome 界面 2 秒 → **自动进入主页**,全程无需额外点击

### 1. Home(`/`)- 主页
- 问候语 + 4 张 pastel bento 统计卡:可用 credits / 托管中 / 完成交易数 / Kudos 收入
- 快捷按钮:Ask for help / Help a neighbour / Track my posts
- Credit activity 流水账本(收支红绿标色,最近 6 条)
- 管理员可见:纠纷仲裁队列(无/部分/全额退款裁决)

### 2. Get help(`/my-posts`)- 我的求助
- 状态标签页:All / Waiting for helper / In progress / Completed(纯前端切换)
- 右上 **+ New task** → 发帖向导
- 发帖成功回跳后显示绿色 "Posted successfully!" 横幅

### 3. Help others(`/helping`)- 我帮别人
- 独立页面展示我接单的任务,All / In progress / Completed 标签页
- 空状态引导去 Community 找任务

### 4. Community(`/browse`)- 社区任务板
- **★ For you 推荐**:根据注册技能反查所属大类,匹配任务打黑金星徽章并默认置顶
- Genre 彩色 filter chips(10 类,各带 emoji)
- **排序**:For you / 距离(同 district 优先)/ Credits 高低 / 所需时间长短
- 卡片显示:分类色条、urgency 徽章(urgent 红 / soon 黄 / low 绿)、📍地区、⏰期限、~分钟数、双方报价、AI 建议价
- 点卡片进任务详情页

### 5. 任务详情(`/requests/{id}`)
- 完整任务信息 + 右侧"How this exchange works"四步说明
- **Offer to help** → 发帖人实时收到系统消息
- **议价面板**:双方各报 value ± buffer,彼此可见;无重叠时 **AI 自动插入建议价**;区间重叠自动取中点成交
- 议价超范围需勾选确认(内联警告,不再跳错误页)
- 成交 → Start task(credits 进托管)→ 双方各自 Confirm → 放款 → Kudos +10%
- 完成后 3 天内可上传证据开 dispute

### 6. Messages(`/messages`)+ Chat(`/chat/{id}`)
- 会话列表:任务标题、双方、最新消息预览、状态 pill、时间
- 聊天气泡界面:自己右侧黄色、对方左侧、**出价是彩色气泡**、系统消息居中灰条(offer/成交/托管/完成/Kudos/纠纷全记录)
- 可直接约 meetup 时间地点

### 7. Help(`/help`)+ Account(`/account`)
- **Help**:How IoU works 六步图解、Community terms(credits 非货币、-20 下限、HKID 隐私)、5 条 FAQ 折叠问答
- **Account**:头像卡(余额/托管/Community helper 徽章)+ 资料明细(年龄、District、地址、打码 HKID、生日、语言、注册日期)+ 技能墙 + 全部可编辑,Edit skills 可重进分层选择页

## 发帖向导(4 步,无空白页)
1. 选 Genre(彩色 chips)
2. 详情:标题自动预填;地址输入框**聚焦时弹出 "📍 Use my address" 提示条**(点击填充,不点自填);Needed by 日期 → **自动算 Urgency**(≤3 天 urgent / ≤7 天 soon / 更久 low)
3. AI 建议价大数字展示 + 自填 offer/buffer
4. **预览页**:完整还原邻居看到的卡片,超范围报价内联警告勾选,底部 Post / Cancel;Post 后转圈动画 → 成功横幅

## 演示数据
- 10 个模拟邻居(港岛 10 区,各有地址/HKID/技能/余额),11 条不同 genre/credits/urgency 的 open 任务,密码均 `demo123`
- 12 条历史成交记录(用于 AI 估价引擎)
- 老 demo 账号:alex / sam / moderator(`@example.com`,密码 `demo123`)
