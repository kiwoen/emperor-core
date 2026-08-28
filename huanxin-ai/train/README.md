# 幻炘AI · 自有模型训练工具链（`train/`）

把"完全属于自己的模型"从想法变成可执行的流水线。
**前提说明**：本工具链不训练、也不包含任何模型权重——它帮你把**自己的数据**喂给一个开源基座（默认 Qwen2.5），产出**带有你领域/风格的自定义模型**，再接入已搭好的 `/v1` 模型 API 对外服务。

---

## 0. 先厘清：什么叫"自己的模型"

| 方案 | 数据需求 | 算力 | 说明 |
|---|---|---|---|
| 自托管开源模型 | 无 | ECS | 部署别人权重，数据/模型都不属于你 |
| **微调开源基座（本工具链）** | 几百~几千条 | 单卡 24G | 权重"属于你"，最现实 |
| 从零训练 | TB 级 | 集群 | 个人基本不现实 |

**结论**：对绝大多数团队，"自己的模型" = 在开源基座上用你的数据做 LoRA 微调。本工具链就做这件事。

---

## 1. 目录结构

```
train/
├── data_prep.py          # Phase 0：素材 → 训练语料（CPU 可跑，纯标准库）
├── finetune.py           # Phase 1/2：Qwen2.5 + LoRA 微调（自动探测 GPU/CPU）
├── serve_register.py     # Phase 3：生成 HUANXIN_MODELS 片段，挂到 /v1
├── config.example.yaml   # 微调配置样例
├── requirements.txt      # 训练依赖（仅训练时安装）
└── README.md
```

---

## 2. Phase 0 — 数据准备（现在就能跑，不需要 GPU）

把你的文档 / 笔记 / 聊天导出放进一个目录，脚本自动整理成 `train.jsonl` / `val.jsonl`：

```bash
# 你"暂无数据"时：用文档派生"种子语料"（总结/主题问答/续写模板）
python train/data_prep.py --input data/raw --output data/prepared --mode seedonly

# 你已有指令数据时：自动识别 instruction / conversations 格式，原样转换
python train/data_prep.py --input data/raw --output data/prepared --mode passthrough

# 混合（默认 auto）：两者都要
python train/data_prep.py --input data/raw --output data/prepared
```

支持输入：`.txt` `.md` `.json` `.jsonl`（Alpaca / ShareGPT / OpenAI messages 均识别）。
> 种子语料只是起步，质量有限；真正"像你"的模型需要你逐步积累真实指令数据（人工编写或蒸馏得到）。

---

## 3. Phase 1/2 — 微调（确认算力后一条命令开训）

```bash
# 1) 安装训练依赖（独立环境，不要塞进主服务）
pip install -r train/requirements.txt

# 2) 复制并修改配置
cp train/config.example.yaml config.yaml
#    base_model: 入门用 Qwen/Qwen2.5-0.5B-Instruct，推荐 Qwen/Qwen2.5-7B-Instruct
#    domain:     领域标签（多领域各自一个，便于管理多个 LoRA）
#    quantization: 显存不够设 4bit

# 3) 先校验配置（无需 torch 也能跑）
python train/finetune.py --config config.yaml --check-config

# 4) 开训
python train/finetune.py --config config.yaml
```

产出：LoRA 适配器目录 `models/<domain>/`。设 `merge_and_save: true` 可合并为完整模型（需能放下全量权重的显存）。

**多领域扩展**：为"法律/医疗/客服"各建一份 `config.yaml`（不同 `domain` + `output_dir` + 数据），分别训练，得到各自 LoRA；服务时按需加载对应适配器即可。

---

## 4. Phase 3 — 接入 `/v1` 对外服务

```bash
python train/serve_register.py --merged-dir models/my-qwen-merged --name my-qwen --backend vllm
```

脚本会输出：
1. `HUANXIN_MODELS` 环境变量片段（写入部署环境）
2. 用 vLLM / Ollama 把模型跑成 OpenAI 兼容服务的命令
3. 外部用户的调用示例

之后，外部用户用标准 OpenAI SDK 即可接入：

```python
from openai import OpenAI
c = OpenAI(base_url="https://你的域名/v1", api_key="sk-xxxx")  # sk- 由 /api/me/api-keys 签发
c.chat.completions.create(model="my-qwen", messages=[{"role":"user","content":"你好"}])
```

`huanxin/api/model_api.py` 已支持 `default` + `FREE_PROVIDERS` + `HUANXIN_MODELS` 多后端，无需改代码。

---

## 5. 环境说明

- `data_prep.py`：仅依赖 Python 标准库 + `pyyaml`，**任何环境可跑**。
- `finetune.py` / `serve_register.py`：重依赖（torch 等）全部**懒加载**，未安装时仍可 `--help` / `--check-config`。
- 测试：`pytest tests/test_data_prep.py`（CPU，验证数据准备正确性）。
