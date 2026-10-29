"""AI 能力按 Port + Adapter 组织：业务层只依赖端口，不依赖具体模型 SDK。

text_rules  : 纯文本规则（正则/日期/标签），无 DB、无网络
entity_match: 实体定位与权限内解析（DB 相关）
ports       : 提供者接口（IntentExtractor 等）
adapters    : 规则实现 / 受控 LLM 实现
"""
