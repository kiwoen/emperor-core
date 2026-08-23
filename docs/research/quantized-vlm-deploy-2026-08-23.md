# 量化版多模态 / 视觉语言模型（VLM）低显存部署调研报告

> 项目：emperor-core 视觉子系统 | 替代对象：LongCat-Video（13.6B DiT，显卡要求高）
> 目标：在保证输出质量与时间的前提下，显著降低本地/云部署的硬件配置要求
> 调研日期：2026-08-23 | 调研人：许清楚（产品经理 / software-product-manager）
> 部署约束：OpenAI 兼容 `/v1` 接口接入中转站（New API）；中端单卡（RTX3060/4090）或 CPU-offload；秒级响应（非分钟级）；视觉推理不路由外部，量化模型作为本地/中转站托管的自治执行器。

---

## 一、研究口径（搜了哪些源）

本次调研基于真实联网检索，覆盖以下来源类型：

1. **官方模型仓库 / 技术博客**
   - Hugging Face 官方模型卡（Qwen、OpenBMB、HuggingFaceTB、Microsoft、OpenGVLab、lliuhaotian 等）
   - Qwen 官方博客（qwenlm.github.io/blog/qwen2.5-vl）
   - InternVL 官方页（internvl.github.io）与 InternVL3 发布博客
   - Hugging Face 博客（huggingface.co/blog/smolvlm2）
2. **显存 / 部署计算器**
   - Spheron GPU Recommender（按 safetensors 元数据估算峰值 VRAM，含权重+激活+KV cache，误差约 ±15%）
   - FitMyLLM（InternVL3-14B 量化 VRAM 矩阵）
   - RunThisModel（Phi-3.5-vision、MiniCPM-V 2.6 量化 VRAM 与吞吐估算）
3. **部署实践 / 工程博客**
   - vLLM 部署 Qwen2.5-VL 实战（hblee.xyz、CSDN ADG、CSDN 72B 部署记录）
   - llama.cpp 官方文档与 Multimodal 文档（mintlify.wiki / ggml-org）
   - Ollama / llama.cpp GGUF 多模态推理（HuggingFace 模型卡、unsloth GGUF）
   - Phi-3.5-vision Ollama 部署（dev.to）
   - SmolVLM2 本地视频摘要管线（KDnuggets）
4. **量化方法对比**
   - LLM Quantization Guide 2026（singularitymoments.com）
   - GGUF/GPTQ/AWQ/bitsandbytes 对比（CSDN 多篇、calmops.com、byteledger.vizleo.com）
5. **基准数据**
   - Qwen2.5-VL 官方量化基准表（MMMU/DocVQA/MMBench/MathVista，VLMEvalkit）
   - 第三方视频基准汇总（Video-MME / MVBench / MLVU / MMBench-Video）
   - MiniCPM-V 2.6 OpenCompass 对比（GPT-4o mini / GPT-4V）

> 说明：部分第三方 benchmark（如 SmolVLM2 的 Video-MME、MiniCPM-V 的 MMMU）来自厂商/社区自报，已尽量交叉验证；凡无法完全确认的数值标注「待核实」。

---

## 二、主表：量化 VLM 候选对比

> 显存为「量化后推理峰值 VRAM（含权重+激活+KV cache，近似）」，不同来源估算口径略有差异，取较保守值。
> 延迟为单卡中端 GPU（RTX 3090/4090 量级）单图/短视频的近似秒级响应，非压测吞吐。
> 推荐度：针对 emperor-core 需求（OpenAI 兼容、低显存、视频/图像、秒级）的主观评级（★越多越推荐）。

| 模型 | 参数量 | 量化方式 | 量化后显存 | 推理延迟(近似) | 质量基准 | 视频支持 | OpenAI 兼容部署栈 | 量化权重可用性 | 推荐度 |
|---|---|---|---|---|---|---|---|---|---|
| **Qwen2.5-VL-7B-Instruct-AWQ** | 7.75B | AWQ(INT4) | ~6.5 GB（磁盘6.5GB；Spheron估INT4约4.5GB） | 秒级（vLLM 连续批处理，单图<2s） | MMBench 84.2 / DocVQA 94.6 / MMMU 55.6（AWQ官方）；OCRBench 837 | ✅ 原生视频（多帧/长视频，MRoPE） | vLLM（原生 `--quantization awq`）、SGLang、Transformers | ✅ 官方 HF 权重；GPTQ 社区版也有 | ★★★★★ |
| **Qwen2.5-VL-3B-Instruct-AWQ** | 3.75B | AWQ(INT4) | ~3.2 GB（磁盘3.2GB；实测推理显存~3.3GB） | 秒级（更轻，<1.5s） | MMBench 78.0 / DocVQA 91.8 / MMMU 49.1（AWQ官方）；Video-MME 61.5、MLVU 68.2 | ✅ 原生视频（同架构，长视频64k可扩） | vLLM、SGLang、Transformers、GGUF(llama.cpp) | ✅ 官方 AWQ；unsloth/samgreen GGUF | ★★★★★ |
| **SmolVLM2-2.2B-Instruct** | 2.2B | BF16 原生（已极轻；可 INT4 再压） | ~5.2 GB（视频推理；INT4 待核实） | 秒级（pixel-shuffle 压缩，吞吐 7.5–16× 优于 Qwen2-VL-2B） | Video-MME 52.1 / MLVU 55.2 / MVBench 46.27；MathVista 51.5、OCRBench 72.9 | ✅ 多帧视频（逐帧图像序列，最多50帧） | Transformers、llama.cpp(GGUF)、MLX、Ollama(社区) | ✅ 官方 HF（HuggingFaceTB）；GGUF 社区版 | ★★★★☆ |
| **MiniCPM-V 2.6 (INT4)** | 8B（SigLip-400M + Qwen2-7B） | INT4（官方）/ GGUF(Q4_K_M) | ~7 GB（INT4 官方）；GGUF Q4_K_M 4.68GB | 秒级（Q4_K_M 约 125 tok/s 单用户） | MMBench 78.0 / OCRBench 851 / MMMU 49.8 / MathVista 60.6；Video-MME 60.9（w/o subs） | ✅ 原生视频（最多64帧，需 trust_remote_code） | Ollama（`ollama run minicpm-v`）、llama.cpp、vLLM(待核实)、LM Studio | ✅ 官方 INT4 + lmstudio-community GGUF | ★★★★☆ |
| **InternVL3-14B** | 14B（ViT-300M + Qwen2.5-14B） | GGUF(Q4_K_M) 等 | Q4_K_M ~9.7–12 GB（fitmyllm：权重9.7+KV2.3）；Q3_K_M ~8GB | 秒级~数秒（依卡而定） | MMBench 85.6 / MMMU 67.1（fitmyllm）；原生多模态预训练 | ✅ 多图+视频（动态分辨率，V2PE 长上下文） | llama.cpp(GGUF)、Ollama、vLLM（待核实） | ✅ GGUF 社区（bartowski 等）；AWQ 待核实 | ★★★☆☆ |
| **InternVL2.5-4B / 2B** | 4B / 2B | GGUF / AWQ | 4B：~3–4GB（待核实）；2B 更小 | 秒级 | Video-MME 62.3(4B)、MVBench 71.6(4B)；MMBench 待核实 | ✅ 多图+视频 | llama.cpp(GGUF，官方支持)、vLLM | ✅ llama.cpp 官方支持 InternVL2.5/3 | ★★★☆☆ |
| **Phi-3.5-vision-instruct** | 4.1B | GGUF(Q4_K_M) / INT4 | Q4_K_M ~3.2 GB；INT4 ~2.3GB（Spheron） | 秒级（单图~2.1s 实测） | MMBench 待核实；单图 OCR/图表强；128K 上下文 | ❌ 仅图像（无原生视频输入） | Ollama（`phi-3.5-vision`）、llama.cpp、LM Studio | ✅ 官方 GGUF（microsoft/Phi-3.5-vision-instruct-GGUF） | ★★☆☆☆ |
| **LLaVA-OneVision-7B (qwen2)** | 7B（SigLIP + Qwen2-7B） | BF16 原生（GGUF 社区/量化待核实） | 官方 8B 版~12GB（ravchat 实测）；4B 版<8GB | 秒级（7B）；4B 更轻 | VQAv2 78%（论文）；Video-MME 待核实 | ✅ 多图+视频（196 token/帧，≤32帧） | Transformers（llava 生态）、llama.cpp（有限）、vLLM（待核实） | ⚠️ 官方未发标准 INT4/AWQ；GGUF 量化权重待核实 | ★★☆☆☆ |

### 关键来源链接
- Qwen2.5-VL 官方 AWQ 基准与权重：https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct-AWQ 、https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct-AWQ
- Qwen2.5-VL GPTQ-Int4（社区，含 ChartQA/OCRBench）：https://www.modelscope.cn/models/ChineseAlpacaGroup/Qwen2.5-VL-7B-Instruct-GPTQ-Int4
- Spheron VRAM 估算（Qwen2.5-VL-7B-AWQ / Phi-3.5 / SmolVLM2-500M）：https://www.spheron.ai/tools/gpu-recommender
- SmolVLM2 官方博客与基准：https://huggingface.co/blog/smolvlm2
- SmolVLM2 视频管线（KDnuggets）：https://www.kdnuggets.com/local-video-summarization-pipeline-processing-frames-with-smolvlm2-2-2b
- MiniCPM-V 2.6 官方卡与 INT4：https://huggingface.co/openbmb/MiniCPM-V-2_6 、https://huggingface.co/openbmb/MiniCPM-V-2_6-int4
- MiniCPM-V 2.6 GGUF（lmstudio-community）：https://www.huggingface.co/lmstudio-community/MiniCPM-V-2_6-GGUF
- InternVL3 发布：https://internvl.github.io/blog/2025-04-11-InternVL-3.0/ ；InternVL3-14B VRAM：https://www.fitmyllm.com/model/internvl3-14b
- Phi-3.5-vision GGUF：https://huggingface.co/microsoft/Phi-3.5-vision-instruct-GGUF ；RunThisModel：https://runthismodel.com/models/phi-3.5-vision
- LLaVA-OneVision 论文：https://lacuna.tiptreesystems.com/paper/llava-onevision-easy-visual-task-transfer/art_293cafeee2fc4fe9bb57fc6e5e26645e
- vLLM 部署 Qwen2.5-VL 实践：https://blog.hblee.xyz?p=199/ 、https://adg.csdn.net/6a2fa07710ee7a33f27d6131.html
- llama.cpp 多模态文档：https://mintlify.wiki/ggml-org/llama.cpp/inference/multimodal
- 量化方法对比 2026：https://singularitymoments.com/llm-quantization-gguf-awq-gptq-guide 、https://calmops.com/algorithms/llm-quantization-gptq-awq-gguf/

---

## 三、量化方式对比（GPTQ vs AWQ vs GGUF vs BitsAndBytes NF4）

针对 VLM 的可用性、显存节省、质量损失、推理速度综合对比：

| 维度 | GGUF (llama.cpp) | AWQ | GPTQ | BitsAndBytes NF4 |
|---|---|---|---|---|
| 主战场 | CPU/混合/消费级 GPU | GPU 服务（vLLM） | GPU 推理（旧宠） | 训练时加载 / 快速原型 |
| 典型工具链 | llama.cpp / Ollama / LM Studio | AutoAWQ + vLLM | AutoGPTQ / ExLlamaV2 | Transformers `load_in_4bit=True` |
| 显存节省(INT4) | ~75–80% | ~70–75% | ~72–76% | ~65–70% |
| 质量损失(vs BF16) | Q4_K_M 约 -2%（MMLU）；Q6_K 近无损 | 极低（MT-Bench 仅降~1.2分） | 中（ARC-Challenge 降~3.5%） | 较高（4bit 下 AlpacaEval 降~5.2分） |
| 推理速度 | CPU 最快；GPU 单用户适中 | GPU 最快（vLLM 原生，2.8× vs BF16） | 需 Marlin 内核才快（否则慢于 BF16） | 一般（1.5× vs BF16） |
| VLM 可用性 | ✅ 官方支持 Qwen2.5-VL/SmolVLM/InternVL2.5-3/MiniCPM-V/LLaVA | ✅ Qwen2.5-VL 官方 AWQ 最佳 | ⚠️ 社区版为主（非官方） | ⚠️ 仅 Transformers 路径，部署不优 |
| OpenAI 兼容 | ✅ llama-server `/v1` | ✅ vLLM `/v1` | ✅ vLLM/TGI `/v1` | ❌ 无独立服务 |
| 多语言质量 | Q4_K_M 对非英语略降，建议 Q6_K | 较稳 | 较稳 | 降最多 |

**结论（对 emperor-core 的选型含义）：**
- 若用 **单卡中端 GPU 走中转站 OpenAI 兼容接口**：优先 **AWQ + vLLM**（质量高、吞吐好、原生 `/v1`），对应 Qwen2.5-VL-3B/7B-AWQ。
- 若需 **CPU-offload / 极致低显存 / 跨平台（Mac、RTX3060）**：优先 **GGUF + llama.cpp(llama-server)**，对应 Qwen2.5-VL-3B GGUF、MiniCPM-V 2.6 GGUF、SmolVLM2 GGUF。
- **GPTQ**：社区版可用但非官方，质量与 AWQ 接近甚至略好（Qwen 社区测 OCRBench 845 vs AWQ 837），但生态被 AWQ 反超，新部署不优先。
- **BitsAndBytes NF4**：仅适合原型/微调，生产推理速度差、无独立服务，不推荐作为中转站执行器格式。

---

## 四、低显存部署栈对比

| 部署栈 | 对量化 VLM 友好度 | OpenAI 兼容 `/v1` | 中端单卡易用性 | 备注 |
|---|---|---|---|---|
| **vLLM** | ★★★★★（AWQ 原生最优） | ✅ 原生 `/v1/chat/completions` | 高（一条 `vllm serve`） | 支持 Qwen2.5-VL AWQ/GPTQ/GGUF；视频需 `--limit-mm-per-prompt image=,video=`；KV-cache fp8 可省一半显存 |
| **llama.cpp (llama-server)** | ★★★★☆（GGUF 多模态全支持） | ✅ `--server` 暴露 `/v1` | 极高（无 Python 依赖，跨平台） | 官方 libmtmd 支持 Qwen2.5-VL/SmolVLM/InternVL2.5-3/MiniCPM-V/LLaVA/Gemma3；适合 CPU-offload 与 RTX3060 |
| **Ollama** | ★★★★☆（封装 llama.cpp） | ✅ `:11434/api/chat` 兼容 | 极高（一条 `ollama run`） | `minicpm-v`、`phi-3.5-vision` 官方；Qwen2.5-VL/SmolVLM2 需社区 modelfile；中转站 New API 可对接 |
| **TensorRT-LLM** | ★★★☆☆（需 H100/ recent NVIDIA） | ✅ 经 TRT-LLM 服务 | 中（编译复杂） | FP8 质量最佳但需新硬件；对非 H 卡收益有限，emperor-core 中端卡不优先 |
| **Transformers(bitsandbytes)** | ★★☆☆☆（仅推理脚本） | ❌ 需自包装 FastAPI | 中 | 适合研究验证；MiniCPM-V 需 `trust_remote_code=True`；不推荐做生产执行器 |

**对 emperor-core 中转站（New API 走 OpenAI 兼容）的接入建议：**
- 中转站后端挂一个 OpenAI 兼容推理服务即可，New API 无需关心底层是 vLLM 还是 llama.cpp。
- 首选两条路径，按硬件二选一或并存：
  1. **GPU 优先路径**：vLLM 起 Qwen2.5-VL-3B/7B-AWQ，暴露 `:8000/v1`，中转站指向该地址。
  2. **低显存/CPU-offload 路径**：llama.cpp `llama-server` 起 Qwen2.5-VL-3B GGUF 或 MiniCPM-V 2.6 GGUF，暴露 `:8080/v1`。

---

## 五、成本 / 质量 / 时间权衡

| 候选 | 显存需求 | 推理延迟 | 质量（图像/视频） | 量化可用性 | 综合适配 |
|---|---|---|---|---|---|
| Qwen2.5-VL-7B-AWQ | 6.5GB | 秒级 | 高（MMBench84/DocVQA95/OCRBench837） | 官方 | 质量/配置最佳平衡（单卡 8GB+ 首选） |
| Qwen2.5-VL-3B-AWQ | 3.2GB | <1.5s | 中高（DocVQA92/Video-MME61.5） | 官方+GGUF | 极低显存+视频，RTX3060 友好 |
| SmolVLM2-2.2B | 5.2GB | 秒级(快) | 中（视频基准领先同尺寸） | 官方 | 视频理解极致轻量，图像质量弱于 Qwen |
| MiniCPM-V 2.6 INT4 | 7GB / 4.68GB(GGUF) | 秒级(125tok/s) | 中高（OCRBench851，对标 GPT-4V） | 官方+GGUF | OCR/文档强，视频原生，商业需登记 |
| InternVL3-14B Q4 | 9.7–12GB | 秒级~数秒 | 高（MMBench85.6/MMMU67.1） | GGUF | 质量高但显存门槛高，需 12GB+ 卡 |
| Phi-3.5-vision | 3.2GB | ~2s | 中（图像强，无视频） | 官方 GGUF | 仅图像，不适合视频场景 |
| LLaVA-OneVision-7B | ~12GB | 秒级 | 中高（VQAv2 78） | 量化权重待核实 | 视频可用但量化链不成熟 |

---

## 六、Top 推荐

### 🥇 推荐 1：Qwen2.5-VL-7B-Instruct-AWQ（质量/配置最优，单卡 8GB+ 首选）

- **为何**：官方 AWQ 权重，质量保留 90%+（MMBench 84.2 / DocVQA 94.6 / OCRBench 837），原生支持视频（多帧 + 长视频 MRoPE，可扩至 64k），vLLM 原生 `--quantization awq` 一键起 OpenAI 兼容服务，完美对接中转站 New API。
- **显存**：约 6.5GB（INT4 权重），RTX3060(12G)/4090(24G)/云单卡中端均轻松跑。
- **部署命令思路（vLLM）**：
  ```bash
  pip install vllm>=0.7.2 transformers>=4.49.0 accelerate qwen-vl-utils
  # 起 OpenAI 兼容服务
  vllm serve Qwen/Qwen2.5-VL-7B-Instruct-AWQ \
    --port 8000 --host 0.0.0.0 \
    --dtype float16 \
    --quantization awq \
    --limit-mm-per-prompt image=3,video=2 \
    --max-model-len 8192 --max-num-seqs 8 \
    --gpu-memory-utilization 0.85
  # 中转站 New API 指向 http://localhost:8000/v1
  ```
- **更低配（RTX3060/CPU-offload）备选**：用 GGUF + llama.cpp
  ```bash
  ./llama-server -hf unsloth/Qwen2.5-VL-7B-Instruct-GGUF:UD-Q4_K_XL --port 8080
  # 或 Q3_K_M 进一步压到 ~3.5GB（质量略降）
  ```

### 🥈 推荐 2：Qwen2.5-VL-3B-Instruct-AWQ（极致低显存 + 视频，RTX3060 首选）

- **为何**：3B 参数 AWQ 仅 ~3.2GB 显存，仍保留视频能力（Video-MME 61.5、MLVU 68.2），质量对多数应用足够（DocVQA 91.8）。是「秒级响应 + 单卡最低门槛 + 视频」的最佳折中，特别适合 emperor-core 视觉子系统被频繁调用的场景。
- **部署命令思路（vLLM）**：
  ```bash
  vllm serve Qwen/Qwen2.5-VL-3B-Instruct-AWQ \
    --port 8000 --host 0.0.0.0 --dtype float16 \
    --quantization awq --limit-mm-per-prompt image=1,video=1 \
    --max-model-len 10000 --max-num-seqs 4
  ```
- **GGUF 跨平台备选（llama.cpp）**：
  ```bash
  ./llama-server -hf unsloth/Qwen2.5-VL-3B-Instruct-GGUF:Q4_K_M --port 8080
  ```

### 备选（按场景）
- **视频理解极致轻量**：SmolVLM2-2.2B（5.2GB，视频基准领先同尺寸；图像质量弱于 Qwen，仅作视频专用执行器）。
- **OCR/文档/中文场景强 + 视频**：MiniCPM-V 2.6 INT4（GGUF Q4_K_M 4.68GB，OCRBench 851；注意商业使用需填写问卷登记）。
- **质量优先且有 12GB+ 卡**：InternVL3-14B GGUF（Q4_K_M ~9.7–12GB，MMBench 85.6）。

---

## 七、关键风险提示（待核实 / 需注意）

1. **MiniCPM-V 2.6 商业授权**：权重免费但商业使用需填写问卷登记（Apache-2.0 代码 + 单独模型许可），上线前需确认合规。
2. **LLaVA-OneVision 量化权重**：官方未发布标准 INT4/AWQ，GGUF 量化权重成熟度待核实，暂不推荐作为主执行器。
3. **SmolVLM2 视频质量**：视频为「逐帧图像序列」近似，非原生视频编码器；长视频/复杂时序理解弱于 Qwen2.5-VL，需按 emperor-core 实际视频长度评估。
4. **中文质量与量化**：激进量化对非英语（含中文）质量下降更明显，建议 Qwen 系列至少 AWQ/Q4_K_M 档，不要降到 Q2_K / Int3。
5. **vLLM 视频支持**：需明确传 `--limit-mm-per-prompt video=N`，且客户端请求按 OpenAI 多模态格式传 `image_url` / 视频帧（vLLM 对视频直接输入的成熟度低于图像，部分版本需抽帧后按多图传入——待核实具体版本）。
6. **GPTQ 权重非官方**：Qwen2.5-VL 的 GPTQ 多为社区（ChineseAlpacaGroup/hfl）出品，质量可用但建议优先官方 AWQ。

---

## 八、给架构师的执行摘要（转交要点）

- **替代 LongCat-Video 的最优解**：Qwen2.5-VL-7B-AWQ（质量优先）或 Qwen2.5-VL-3B-AWQ（显存优先），均原生支持视频、OpenAI 兼容、单卡中端可跑。
- **部署栈**：vLLM（AWQ，GPU 路径）或 llama.cpp（GGUF，低显存/CPU-offload 路径），两者都暴露 `/v1`，中转站 New API 直接对接。
- **量化方式**：优先 AWQ（vLLM）或 GGUF Q4_K_M（llama.cpp），避免 BitsAndBytes NF4 做生产执行器。
- **接口契约**：VideoExecutor 调用中转站 `/v1/chat/completions`，传多模态 message（图像 base64 / 视频抽帧为图像序列），不路由外部。
- **硬件矩阵**：RTX3060(12G) → 3B-AWQ 或 7B-AWQ 均可行；RTX4090(24G) → 7B-AWQ 从容；CPU-offload → GGUF + llama.cpp。
