# data/ — 你的专属模型语料库

本目录是整个「专属 AI」计划的**数据地基**。模型可以换、框架可以换，但这里沉淀的
语料属于你——它才是「专属」二字的真正含义。

## 目录结构

```
data/
├── README.md            # 本文件
├── COLLECTION_GUIDE.md  # 多领域采集清单（照着填）
├── .gitignore
├── raw/                 # 原始素材（你采集的内容放这里）
│   ├── general/         # 通用中文对话
│   ├── legal/           # 法律领域
│   ├── medical/         # 医疗领域
│   ├── code/            # 代码
│   ├── customer_service/# 客服对话
│   └── personal/        # 个人笔记 / 风格（最"你"的部分）
└── prepared/            # data_prep.py 生成的训练集（可重新生成，不入库）
```

## 怎么用

1. 把素材丢进 `raw/<领域>/`（支持 `.txt` `.md` `.json` `.jsonl`）。
   - 纯文档（.txt/.md）→ 自动派生成种子问答。
   - 已有指令数据（.json/.jsonl，Alpaca/ShareGPT 格式）→ 原样转换。
2. 运行整理脚本（纯 CPU，无需 torch）：

   ```bash
   python train/data_prep.py --input data/raw --output data/prepared --mode auto
   ```

   得到 `data/prepared/train.jsonl` 与 `val.jsonl`，直接喂给 `train/finetune.py`。

3. 训练 / 部署：见 `train/README.md`。

## 关于「专属」的实话

- 现在起步，我们仍会用开源基座（如 Qwen2.5）做微调——这是成本最低、最快拿到
  可用模型的路径。但**你在这里积累的语料是 100% 属于你的资产**，它会一路带到
  未来的「从零训练」阶段，不会浪费。
- 想彻底不依赖任何他人模型，终点是「从零训练」（自有架构 + 自有语料 + 大算力）。
  那一步依旧依赖本目录的数据。所以**现在就开始采集，就是在为终点铺路**。
- 采集时优先你的第一方内容（文档 / 笔记 / 对话导出）；若引用开源数据，务必确认
  许可证干净、并用你自己的表述重写。

详见 `COLLECTION_GUIDE.md`。
