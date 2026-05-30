"""Adapters — opt-in subpackage with extra deps per host system.

Each adapter has its own optional extra:
    [bench]   — for use with agent-merge-bench's Resolver protocol (testing)
    [langmem] — for use with LangMem's MemoryStoreManager
    [mem0]    — planned, v0.2
    [letta]   — planned, v0.2

Adapters are NOT auto-imported. Use `from resolver_oss.adapters.langmem import LangMemAdapter`.
"""
