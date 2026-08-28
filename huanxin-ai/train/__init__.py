"""train — 幻炘AI「自有模型」训练工具链。

Phase 0  数据准备  data_prep.py   （纯标准库 + pyyaml，CPU 可跑）
Phase 1  微调脚手架  finetune.py   （Qwen2.5 + LoRA，自动探测 GPU/CPU）
Phase 2  训练执行    见 README / finetune.py --config
Phase 3  接入 /v1    serve_register.py（生成 HUANXIN_MODELS 片段）

设计目标：让「完全属于自己的模型」从"想法"变成可执行的流水线。
不依赖你未知的算力 —— 数据准备现在就能跑，训练等算力确认后一条命令开训。
"""

__all__ = ["data_prep", "finetune", "serve_register"]
