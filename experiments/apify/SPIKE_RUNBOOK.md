# 小红书 Apify spike runbook(Richard 2026-05-24,线 2-A)

> 目标:验证「小红书作为系统①热点源」是否可行。跑个小 job(10-20 结果)填下面 6 项,回 Richard → 他出长期选型 + 成本模型。**别一次烧光免费额度。**

## Setup
- Apify 免费号($5/月 credits,无需信用卡)。**待 Anna 给 API token。**
- 主试 actor:`zhorex/rednote-xiaohongshu-scraper`(免费层 ~1000 结果)。
- 备选:`easyapi` / `datapilot` rednote-search-scraper(若 zhorex 不支持关键词搜)。

## 6 项验证

**1｜字段覆盖(最关键)— 能不能喂系统① hot_score(点赞+评论+转发+时间衰减)**
逐项核 actor 输出 JSON 实际有没有(✅有/❌无/⚠部分):
- [ ] 点赞数 ⭐  [ ] 收藏数 ⭐  [ ] 评论数 ⭐  [ ] 分享数  [ ] 发布时间 ⭐(时间衰减必需)
- [ ] 标题  [ ] 正文  [ ] 作者  [ ] 话题标签  [ ] 图片/视频 URL  [ ] 笔记 URL
- ⭐ 缺了 = fail 核心需求(等于又一个"PH RSS 无票数")。

**2｜搜索模式 — 发现热点 vs 只监控已知号**
- [ ] 能按关键词/话题搜吗(试 "AI" / "AI创业")?还是只能按 URL/用户主页/笔记 ID?
- [ ] 能拉热门/explore feed 吗?
- 只支持 URL/用户 = "监控标杆号"工具,非"按主题发现热点"。

**3｜频率/限流**:单次最多返回几条、跑一次多久、有无限流/失败。
**4｜数据新鲜度**:返回笔记发布时间 vs 现在 —— 近几天实时 还是 数周前旧数据?旧 = fail。
**5｜稳定性**:同 job 跑 2-3 次记成功率;看 actor 近期 review + 最后更新日期。
**6｜真实成本(含隐藏主成本)**:从 Apify run 控制台记 单次 CU / 代理带宽消耗+计费 ⭐ / 是否另收 per-result 费 → 折算每 1000 结果成本 → 按目标量算月成本。

## Pass / Fail
- **PASS**:互动指标 + 发布时间 + 正文齐 + 关键词搜可用 + 数据近期 + 成本可接受 → 进长期选型。
- **PARTIAL**:字段齐但只能按 URL/用户 → 做"监控标杆号"可以,"按主题发现热点"不行。
- **FAIL**:缺互动/发布时间、或数据陈旧、或成本过高 → 换备选 actor;仍不行 → 转 Browserbase/CU 登录态自建。

## 执行结果(2026-05-24,lil-Anna 实跑,Anna 的 token)
actor=`zhorex/rednote-xiaohongshu-scraper`。跑了 search "AI" 10 条 + post_details 1 条。
- **字段:要两步**。`search`(无 cookie)只给 likes+postUrl+author+截断title;`post_details` 才全(实测一条:likes130/saves6/comments7/shares14 + publishedAt(epoch ms)+ content(正文+话题)+ tags[3]+images[1])。
- **⚠️ 关键词搜索不精确**:搜"AI"返回"海豹喝冰咖啡治愈漫画"等无关内容(精确匹配 login-gated)。`user_posts`(按账号)会精确。
- 频率 maxResults≤500;search10条~30s。新鲜度:publishedAt 可判(post_details)。稳定:2 run 0 fail(有 unknown event 'post-scraped' 计费警告)。成本:run compute ~$0.0002;文档 ~$0.005/result;两步~2x。
- **判定 PARTIAL**。给 Anna 方向选择:A 按关键词发现(需登录 cookie)/ B 监控标杆号(user_posts,不用登录,下一步可试)。等 Anna 定方向 + Richard 出长期选型。
