"""系统① 主线 pipeline 包(system1-app)。

数据流:sources(抓取)→ scoring(打分)→ 三闸门过滤 → AI 点评/标签 → Top20 → 入库。
本包 Step 2 先落地「数据源抽象层」:统一 Source 接口 + registry + HotItem 契约。
后续 Step 3 接主题映射,Step 4 接打分/AI/入库端到端。
"""
