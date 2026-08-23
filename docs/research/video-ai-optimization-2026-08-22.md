# 视频类 AI 项目优化研究报告（反哺 emperor-core）

> 研究员 / 产品经理：许清楚（emperor-core 产品经理）
> 日期：2026-08-22
> 目的：联网调研视频类 AI 项目（生成 / 理解 / 多模态智能体），提炼可借鉴优化点与短板，反哺 emperor-core 既有子系统。

---

## 一、研究口径与方法

### 1.1 调研范围
聚焦「能反哺 emperor-core 的优化点」，而非泛泛视频 AI 科普。覆盖维度：
- 视频生成（开源 SOTA 模型、长视频、低成本训练）
- 视频理解（长视频、多模态智能体、agentic search）
- 视频 RAG / 长视频记忆系统
- 多模型编排 / 自演化 / 评测闭环（不限纯视频）
- 成本、延迟、安全与合规（水印 / 溯源 / 标识）

### 1.2 信息源与筛选标准
- **GitHub 仓库**：Open-Sora、Multifront、VideoRAG、LongCat-Video（官方 README / 代码结构）
- **官方 blog / 论文 blog**：ReelMind（Nolan / 评测 / 合规）、LearnOpenCV（VideoRAG 解析）、martinuke0（多模型编排框架综述）
- **技术媒体 / 中文资讯**：量子位、Pandaily、Xinhua、EmergentMind、Nuvox、Upuply、SimaLabs
- **论文**：LVAgent（ICCV 2025）、VideoDeepResearch（ICLR 2025）、VideoAgent（ECCV 2024）、Event-Causal RAG（arXiv 2026）、Neural Computers（Meta/KAUST 2026）
- **筛选**：每条结论尽量附原始链接；不确定信息标注「待核实」；公开资料稀少处说明并给出替代调研对象。

### 1.3 关于「cncs」的处理（歧义说明）
用户原话提到「cncs」。经多组合检索（"cncs AI video" / "cncs 视频" / "CNC video AI"），**不存在一个明确叫 "cncs" 的视频 AI 项目**。实际命中三类相关但不同的对象：
1. **CNC = Centre National du Cinéma（法国国家电影中心）**：2024 年成立 Observatoire 观察站跟踪 AI 在影视全流程应用与影响；另有 CNC 用 AI 做国家电影档案元数据生成 / 检索的探讨（ReelMind blog）。→ 偏「行业监管 / 档案」，非工程架构，对 emperor-core 借鉴有限。
2. **CNCs = Core Narrative Components（ReelMind 叙事核心组件）**：在其「AI 短视频营销」方法论中，把故事拆成 protagonist/conflict/resolution + visual lexicon。→ 是**叙事编排概念**，可借鉴到「智能体剧本/任务拆解」。
3. **CNC = Completely Neural Computer（Meta/KAUST 2026 论文 Neural Computers）**：把神经网络作为「运行时计算机」，用视频扩散模型（Wan2.1）实例化 CLI/GUI 屏幕帧生成。→ 是前沿范式，与「世界模型 / 生成式界面」相关。

**结论**：「cncs」属模糊歧义项，本报告不将其作为单一项目锁定，而是把上述 3 类含义分别在主表 / 歧义小节中标注，并以更明确的「ReelMind Nolan AI Director」「VideoRAG」「Neural Computers」等中文/英文项目替代，避免卡住。

---

## 二、主表：视频类 AI 项目 / 源 → emperor-core 映射

| # | 项目/源 | 链接 | 它做什么 | 可借鉴的优化点 | 它的短板/风险 | 映射到 emperor-core 子系统 | 借鉴紧急度 |
|---|---------|------|----------|----------------|----------------|---------------------------|------------|
| 1 | **Open-Sora 2.0** (hpcaitech) | https://github.com/hpcaitech/Open-Sora | 11B 开源视频生成，T2V/I2V，宣称 $200K 训练成本，级联 refine 管线 | 模块化多模型栈：T5/CLIP 文本编码 → ChatGPT Prompt Refine → Flux T2I 首帧 → DiT+VAE 视频；配置驱动推理、显存 offload | 推理慢（768px 单卡 1656s），需 H100；强依赖外部 API（ChatGPT 改写） | `MultiModelRouter` 多执行器范式、`DistillationStore`（同 prompt 多模型 trace）、离线 mock 隔离 | **P1** |
| 2 | **LongCat-Video** (美团) | https://github.com/meituan-longcat/LongCat-Video | 13.6B DiT，统一 T2V/I2V/续写，5 分钟 720p/30fps，C2F + 块稀疏注意力，GRPO 多奖励 | 「统一架构 + 条件帧数区分任务」可借鉴为**能力热插拔**；续写预训练解决长时序一致；多奖励 RLHF 思路可迁移到 `llm_judge` 评测 | 仅推理阶段可用，训练成本仍高；Avatar 分支依赖 Whisper/音频 | 视觉子系统（替代 Groq 借用）、`eval/llm_judge`、`Self-Evolution` 基因/奖励 | **P0**（视觉独立性） |
| 3 | **ReelMind / Nolan AI Director** | https://reelmind.ai/blog/your-ai-video-partner-nolanai-for-intelligent-content-creation | AI Agent Director：101+ 模型库，依赖注入后端（NestJS），AIGC 任务队列，credit 计费 | 「导演智能体」编排多模型 + 参数注入；**依赖注入 + 模块边界**与 emperor-core 第一性原则契合；任务队列管理 GPU | 闭源商业化；credit 绑定模型，vendor lock-in；中文文档弱 | `MultiModelRouter` 角色路由、`MCP` 工具注册、`Self-Evolution` | **P1** |
| 4 | **VideoRAG** (HKUDS) | https://github.com/HKUDS/VideoRAG | 首个长视频 RAG：图驱动知识索引 + 分层多模态编码，134+ 小时基准 SOTA，单卡 RTX3090 | **双通道记忆**：知识图谱（文本接地）+ 多模态上下文编码；可做 emperor-core 视频记忆后端；跨视频语义关系 | 单卡但需现代 GPU；Vimo 仍为 beta；非流式增量存储压力 | `记忆`(ChromaDB 向量记忆)、`RAG 引擎`、`DistillationStore` 视频 trace | **P0**（视频记忆） |
| 5 | **Event-Causal RAG (EC-RAG)** | https://arxiv.org/html/2605.06185v2 | 超长/流式视频推理：事件级 State-Event-State(SES) 图记忆，双向图检索，RTX5090 持续处理 | 「事件而非固定片段」做记忆单元，释放已完成视觉状态 → 无限时长零增量存储；**流式记忆**可借鉴到实时视频流 | 仅视觉-音频哨兵；存储/在线成本仍敏感；论文阶段 | `记忆` 流式扩展、`telemetry`、`RAG 引擎` | **P2** |
| 6 | **LVAgent** (ICCV 2025) | https://github.com/64327069/LVAgent | 长视频理解：MLLM 智能体多轮动态协作（Selection/Perception/Action/Reflection），超 GPT-4o | 「动态组建 Agent 团队 + Reflection 优化团队」= emperor-core 大臣协同 + `ReflexionEngine` 的现成范式；80% LVU 准确率 | 依赖单一/少数 MLLM；计算开销大 | `Self-Evolution`(UCB 派发/多样性)、`ReflexionEngine`、`MultiModelRouter` | **P1** |
| 7 | **VideoDeepResearch** (ICLR 2025) | https://arxiv.org/pdf/2506.10821v1 | 仅用文本推理模型(LRM)+ 模块化多模态工具（检索器/感知器）做长视频理解 | 「LRM 认知核心 + 工具按需调用」完美契合 emperor-core「教师只在蒸馏时咨询，推理时自治」；工具化视觉 | 依赖外部 VLM 工具；长视频仍受工具覆盖限制 | `MultiModelRouter`、`MCP` 工具、`记忆` 分层 | **P1** |
| 8 | **Multifront** (camerasearch) | https://github.com/camerasearch/Multifront | 多 LLM 编排引擎：角色路由(ModelRouter)、ReAct、VerifierAgent（7 维验证）、后验投票、EvidenceStore、TraceCollector | **角色→模型映射**（PLANNER/VERIFIER/REASONER 解耦）；置信阈值分级（T0/T1/T2）控制成本；证据库替代长上下文；Trace 用于回放/自进化 | 固定流水线，无真正自进化；skill bank 缺失；上下文仅间接压缩 | `MultiModelRouter` 角色路由、`ReflexionEngine` 验证、`telemetry`、`Self-Evolution` | **P0**（路由/验证） |
| 9 | **VideoAgent** (ECCV 2024) | https://scholar.google.com/citations?view_op=view_citation | 记忆增强多模态智能体：结构化记忆存事件描述 + 对象追踪状态，工具交互 | 「统一记忆机制」协调多基础模型，对象中心追踪 → 可借鉴到 emperor-core 大臣共享记忆 | 长视频仍受限；需 zero-shot 工具 | `记忆`(ChromaDB)、`MCP`、`RAG 引擎` | **P2** |
| 10 | **Neural Computers (CNC)** (Meta/KAUST 2026) | https://ar5iv.labs.arxiv.org/html/2604.06425 | 用视频扩散模型实例化「神经网络即计算机」，NC_CLIGen/NC_GUIWorld 生成屏幕帧 | 「世界模型 + 生成式界面」前沿；SVG 光标监督 8.7%→98.7% 的「数据质量>数量」启示（110h 目标数据 ≈ 1400h 随机） | 概念早期；符号稳定性/例程复用仍挑战 | `Self-Evolution`(gap 检测)、`DistillationStore` 数据质量 | **P2** |
| 11 | **AI 视频评测/成本基准** (Nuvox / ReelMind Bench) | https://nuvox-ai.com/ai-video-creation-tools-2025-complete-benchmarked-guide ；https://reelmind.ai/blog/video-synthesis-benchmarks-evaluating-coherence-vs-computational-cost | 统一 prompt 跨 5 平台实测：成本 $15-180/小时视频（2023 $500-2000）；FVD/FID 标准；coherence vs cost | 「一致性 vs 算力」权衡框架；FVD 时序指标、credit/秒、推理延迟三指标 → 可接入 emperor-core `eval/llm_judge` 与 telemetry | 基准随模型快速过时；需持续更新 | `eval/llm_judge`、`telemetry`、`DistillationStore` 成本维度 | **P1** |
| 12 | **AI 内容标识合规** (中国《标识办法》/ EU AI Act / C2PA) | https://www.xinhuanet.com/digital/20250902/d08b592223954340af8c02ac3d7161e1/c.html ；https://sparkco.ai/blog/ai-model-watermarking-authenticity-verification | 2025-09-01 起中国强制显式+隐式标识；EU 机器可读标记+水印；C2PA 加密溯源 | **溯源/水印**应成为 emperor-core 输出合规标配（显式提示 + 隐式元数据 AIGC 标记 + 哈希）；与 `PromptGuard` 互补 | 恶意用户可转码规避；跨境执法难；合规成本 | `护栏`(PromptGuard/HallucinationGuard)、`telemetry` 审计、输出层 | **P1**（合规） |
| 13 | **多模型编排框架综述** (LangGraph/CrewAI/Eino/LiveKit) | https://martinuke0.github.io/posts/2026-03-06-beyond-the-chatbot-mastering-agentic-workflows-with-open-source-multi-model-orchestration-frameworks | Planner→Executor→Evaluator、Tool-Use Loop、Memory-Backed State Machine、事件驱动 | 「Planner-Executor-Evaluator」与 emperor-core「皇帝编排大臣」同构；可参考其生产级监控/异步执行 | 框架碎片化；需自建 glue | `MultiModelRouter`、`Self-Evolution`、`MCP` | **P2** |

---

## 三、「cncs 等歧义项」说明

用户提到的 **"cncs"** 经检索不存在明确同名视频 AI 项目，实际命中以下 3 类不同对象，分别标注并给出 emperor-core 关联：

| 歧义含义 | 实质 | 来源 | 与 emperor-core 的关系 | 处理 |
|----------|------|------|----------------------|------|
| **CNC（法国国家电影中心）** | 行业监管机构，2024 设 Observatoire 跟踪 AI 影视应用 | https://www.aiww.com/s/9e4df988d1bc75df | 偏行业/政策，工程借鉴弱；但其「AI 全流程落地监测」思路可映射到 `telemetry` 观测 | 仅作背景，不纳入主表核心 |
| **CNCs（Core Narrative Components）** | ReelMind 方法论：把故事拆为 protagonist/conflict/resolution + visual lexicon | https://reelmind.ai/blog/the-storytelling-blueprint-using-ai-to-structure-influencer-campaigns | 「核心叙事组件」可借鉴为**智能体任务拆解 / 剧本模板**，对应 emperor-core 大臣角色定义 | 概念借鉴，替代「cncs」工程含义 |
| **CNC（Completely Neural Computer）** | Meta/KAUST 2026 论文：神经网络作为运行时计算机，视频扩散实例化界面 | https://ar5iv.labs.arxiv.org/html/2604.06425 | 「世界模型 + 生成式界面」前沿，呼应 emperor-core 视觉独立性方向 | 已作为主表 #10 纳入 |

**替代明确项目**：鉴于「cncs」模糊，本报告以 **Reel 生成平台（Nolan）、VideoRAG、Neural Computers、Multifront** 等工程明确的视频/多模型项目替代，确保优化建议可落地。

---

## 四、Top 5 优先建议（附具体下一步 + 负责子系统）

### 建议 1：把视觉子系统从「Groq 借用」改为独立自治模块（呼应第一性原则）
- **问题**：用户明确反对「服务时借用别的 AI」（Groq）。违反「独立性 > 借用」，且推理时路由外部 = 依赖 + 成本 + 不可控。
- **借鉴源**：LongCat-Video（统一架构、MIT 开源、可本地推理）、VideoDeepResearch（LRM 认知核心 + 工具化视觉，推理时自治）。
- **具体下一步**：
  1. 在 `MultiModelRouter` 注册一个**本地/自托管视觉执行器**（如 LongCat-Video-Avatar 或轻量 VLM），作为 `RealLLMExecutor` 的一种，经 `EMPEROR_RELAY_URL` 中转但不依赖 Groq。
  2. 参照 VideoDeepResearch：视觉仅作为「工具」在**蒸馏/学习阶段**被咨询，推理时由本地模块处理。
  3. 保留 `OfflineMockExecutor` 离线路径，确保视觉能力在无外部 API 时仍可 mock 自治。
- **负责子系统**：视觉模块 + `MultiModelRouter` + API 中转站集成
- **紧急度**：**P0**

### 建议 2：引入角色路由 + 置信阈值分级（降本且提升多样性）
- **问题**：当前 `MultiModelRouter` 并行/集成多模型，但缺乏「按角色选模型 + 置信门控」的成本感知策略。
- **借鉴源**：Multifront 的 `ModelRouter`（PLANNER/VERIFIER/REASONER 角色解耦）、T0/T1/T2 置信阈值升级。
- **具体下一步**：
  1. 在 `MultiModelRouter` 增加「角色→模型」映射配置（皇帝=PLANNER、大臣=WORKER、核验=VERIFIER），运行时可覆盖。
  2. 引入**置信阈值门控**：低置信才升级到更强/更贵模型，避免所有请求走旗舰模型。
  3. 用 `VerifierAgent` 式 7 维验证（事实/实体/算术/来源…）接进 `HallucinationGuard` / `ReflexionEngine`。
- **负责子系统**：`MultiModelRouter` + `ReflexionEngine` + `HallucinationGuard`
- **紧急度**：**P0**

### 建议 3：为 `DistillationStore` 增加「视频多模态 trace」与成本维度
- **问题**：`DistillationStore` 只记「同 prompt 不同模型怎么答」文本 trace，未覆盖视频/多模态，也未记成本。
- **借鉴源**：Open-Sora（同 prompt 多模型栈 trace）、Nuvox/ReelMind Bench（成本 $/小时、credit/秒、FVD）。
- **具体下一步**：
  1. 扩展 `DistillationStore` schema：增加 `modality`（text/video/audio）、`cost_credits`、`latency_ms`、`coherence_score(FVD)` 字段，**仅真实调用写入**（保持语料诚实）。
  2. 在蒸馏阶段并行跑同一视频 prompt 的多个视觉/生成模型，记录差异 → 支撑 `Self-Evolution` 的 UCB 派发与 gap 检测。
  3. 成本维度回灌 telemetry，做「一致性 vs 算力」权衡看板。
- **负责子系统**：`DistillationStore` + `telemetry` + `eval/llm_judge`
- **紧急 度**：**P1**

### 建议 4：把 `SocialCollector` 接入视频源 + 长视频 RAG 记忆后端
- **问题**：`SocialCollector` 目前 HN→语料，未覆盖视频；`记忆`(ChromaDB) 未专门处理长视频。
- **借鉴源**：VideoRAG（图索引+分层编码，134h 单卡）、EC-RAG（事件级 SES 流式记忆）、Neural Computers「数据质量>数量」。
- **具体下一步**：
  1. `SocialCollector` 增加视频源扩展点：YouTube/Papers-with-Video/arXiv 附带 demo、Bilibili 技术视频（用户语境为中文）——占位接口，先接 1-2 个免 key 源。
  2. 引入「视频知识图谱 + 多模态向量」双通道索引作为 `RAG 引擎` 的视频分支；长视频按**事件**而非固定片段切分（EC-RAG 启示）。
  3. 蒸馏阶段用高质量目标数据（非随机）训练视频理解，呼应 Neural Computers 的「110h 目标 > 1400h 随机」。
- **负责子系统**：`SocialCollector` + `记忆`(ChromaDB) + `RAG 引擎`
- **紧急度**：**P0/P1**（视频记忆为 P0，视频源采集为 P1）

### 建议 5：输出层加入 AI 内容标识（显式+隐式）合规能力
- **问题**：2025-09-01 中国《标识办法》强制显式+隐式标识；EU AI Act / C2PA 同理。emperor-core 输出无溯源。
- **借鉴源**：Xinhua《标识办法》解读、Sparkco 合规路线图（C2PA 清单、WORM 日志）。
- **具体下一步**：
  1. 在输出层（SSE 流末端 / 文件导出）自动注入**显式标识**（文本「AI 生成」提示 / 视频角标）与**隐式元数据**（AIGC 标记 + 模型名 + 唯一编号 + 哈希）。
  2. `telemetry` 增加**不可篡改审计日志**（append-only / WORM），记录生成参数与模型版本，支撑溯源。
  3. 与 `PromptGuard` 互补：护栏管「内容安全」，标识管「来源可信」。
- **负责子系统**：输出层 + `护栏`(PromptGuard) + `telemetry`
- **紧急度**：**P1**（合规风险，建议尽快）

---

## 五、关键结论速览（便于转交架构师/工程师）

1. **视觉必须自治**：LongCat-Video（MIT 开源、可本地）替代 Groq 借用，推理时不路由外部——直接落实第一性原则。
2. **路由要角色化+分级**：Multifront 的 ModelRouter + 置信阈值，是 `MultiModelRouter` 降本增多样的最快借鉴。
3. **蒸馏要扩到视频+成本**：`DistillationStore` 加 modality/cost/coherence 字段，支撑自进化数据飞轮。
4. **视频记忆用 VideoRAG/EC-RAG**：图索引 + 事件级流式记忆，作为 ChromaDB 的视频分支。
5. **合规不能拖**：中国《标识办法》已生效，输出层加显式+隐式标识 + 审计日志。

---

## 六、参考链接汇总

- Open-Sora: https://github.com/hpcaitech/Open-Sora
- LongCat-Video: https://github.com/meituan-longcat/LongCat-Video
- ReelMind/Nolan: https://reelmind.ai/blog/your-ai-video-partner-nolanai-for-intelligent-content-creation
- VideoRAG: https://github.com/HKUDS/VideoRAG ｜ 解析: https://learnopencv.com/videorag-long-context-video-comprehension/
- Event-Causal RAG: https://arxiv.org/html/2605.06185v2
- LVAgent: https://github.com/64327069/LVAgent ｜ ICCV: https://openaccess.thecvf.com/content/ICCV2025/html/Chen_LVAgent_Long_Video_Understanding_by_Multi-Round_Dynamical_Collaboration_of_MLLM_ICCV_2025_paper.html
- VideoDeepResearch: https://arxiv.org/pdf/2506.10821v1
- Multifront: https://github.com/camerasearch/Multifront
- Neural Computers: https://ar5iv.labs.arxiv.org/html/2604.06425
- 评测/成本: https://nuvox-ai.com/ai-video-creation-tools-2025-complete-benchmarked-guide ｜ https://reelmind.ai/blog/video-synthesis-benchmarks-evaluating-coherence-vs-computational-cost
- 合规: https://www.xinhuanet.com/digital/20250902/d08b592223954340af8c02ac3d7161e1/c.html ｜ https://sparkco.ai/blog/ai-model-watermarking-authenticity-verification
- 多模型编排综述: https://martinuke0.github.io/posts/2026-03-06-beyond-the-chatbot-mastering-agentic-workflows-with-open-source-multi-model-orchestration-frameworks
- CNC 歧义: https://www.aiww.com/s/9e4df988d1bc75df ｜ https://reelmind.ai/blog/the-storytelling-blueprint-using-ai-to-structure-influencer-campaigns ｜ https://reelmind.ai/blog/cnc-centre-national-cinematographie-ai-for-national-film-archives

---
*报告生成方式：真实联网检索（WebSearch + WebFetch），覆盖 GitHub / 官方 blog / 论文 / 中文资讯；不确定处以「待核实」标注。*
