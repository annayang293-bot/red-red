"""小红书数据源 —— STUB(forward-compat 占位,未实现)。

存在的意义 = 证明插件架构闭环:registry 已注册此 adapter。
注意:sources 表 seed 目前只含 Reddit + Product Hunt,**尚未** INSERT 小红书行。
真要接入时:① 把 fetch() 填上(产出 List[HotItem])② 往 sources 表 INSERT 一行
(source_key='xiaohongshu', adapter_class='XiaohongshuSource')。主线/打分/入库零改动。

实现路径(Richard 2026-05-24 调研结论):小红书瓶颈是 **请求签名层 + 登录墙**,
不是网页解析 —— 所以 **Firecrawl 不适合做 XHS 主路径**(它强在 JS 渲染/markdown 化,
不解决签名)。更 fit 的候选:① Apify 专用 actor(rednote scraper,免费层先验证);
② Browserbase / Computer Use 登录态浏览器驱动 + 真实 session,绕过签名军备竞赛。
(Firecrawl 仍可用于海外开放源 ingestion,但那归 reddit/PH 等 adapter,不是这里。)
"""
from __future__ import annotations

from .base import Source


class XiaohongshuSource(Source):
    name = "xiaohongshu"

    def fetch(self):
        raise NotImplementedError(
            "XiaohongshuSource 尚未实现 —— forward-compat 占位。"
            "接入时在此产出 List[HotItem](走 Firecrawl/官方/第三方),主线无需改动。"
        )
