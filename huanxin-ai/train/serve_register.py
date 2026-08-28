"""serve_register.py — 把训练好的「你的模型」挂到幻炘AI 的 /v1 API 对外服务。

我们之前已搭好 OpenAI 兼容模型 API（huanxin/api/model_api.py），它通过环境变量
HUANXIN_MODELS 声明可用后端。本脚本帮你把训练产物（LoRA 适配器或合并模型）生成
对应的 HUANXIN_MODELS 片段，并给出用 vLLM / Ollama 把它加载成 OpenAI 兼容服务的命令。

流程（Phase 3）：
  1) 用 vLLM 或 Ollama 把模型作为 OpenAI 兼容服务跑起来（监听某端口）
  2) 把本脚本生成的片段写进部署环境变量 HUANXIN_MODELS
  3) 外部用户即可用标准 OpenAI SDK 调 https://你的域名/v1 ，model=<name>

用法：
  python serve_register.py --adapter-dir models/my-qwen --name my-qwen
  python serve_register.py --merged-dir models/my-qwen-merged --name my-qwen --port 8000
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def build_huanxin_models_entry(name: str, base_url: str, default_model: str) -> dict:
    """生成单条 HUANXIN_MODELS 记录（OpenAI 兼容后端）。"""
    return {
        name: {
            "provider": "openai",
            "base_url": base_url,
            "default_model": default_model,
            "description": f"自建微调模型 {name}",
        }
    }


def vllm_command(model_path: str, served_name: str, port: int, tp: int) -> str:
    return (
        f"python -m vllm serve {model_path} "
        f"--served-model-name {served_name} --port {port} --tensor-parallel-size {tp}"
    )


def ollama_commands(model_path: str, served_name: str) -> str:
    modelfile = f'FROM {model_path}\nTEMPLATE """{{{{ .System }}}}{{{{ .Prompt }}}}"""\n'
    return (
        f'# 1) 写 Modelfile：\n'
        f'cat > Modelfile.{served_name} <<\'EOF\'\n{modelfile}EOF\n'
        f'# 2) 创建并运行：\n'
        f'ollama create {served_name} -f Modelfile.{served_name}\n'
        f'ollama run {served_name}'
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成 HUANXIN_MODELS 片段并给出服务命令")
    ap.add_argument("--name", required=True, help="暴露给外部用户的模型名（如 my-qwen）")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--adapter-dir", help="LoRA 适配器目录（需配合基座由 vLLM/Ollama 加载）")
    src.add_argument("--merged-dir", help="已合并的完整模型目录")
    ap.add_argument("--port", type=int, default=8000, help="本地推理服务端口")
    ap.add_argument("--tensor-parallel", type=int, default=1, help="vLLM 张量并行（多卡时>1）")
    ap.add_argument("--backend", choices=["vllm", "ollama"], default="vllm")
    ap.add_argument("--out", help="可选：把 HUANXIN_MODELS 片段写入该 JSON 文件")
    args = ap.parse_args(argv)

    model_path = args.merged_dir or args.adapter_dir
    base_url = f"http://localhost:{args.port}/v1"

    entry = build_huanxin_models_entry(args.name, base_url, args.name)

    print("=" * 64)
    print("① HUANXIN_MODELS 环境变量片段（写入部署环境）")
    print("=" * 64)
    print(json.dumps(entry, ensure_ascii=False, indent=2))

    print("\n" + "=" * 64)
    print(f"② 用 {args.backend} 把模型跑成 OpenAI 兼容服务")
    print("=" * 64)
    if args.backend == "vllm":
        print(vllm_command(model_path, args.name, args.port, args.tensor_parallel))
    else:
        print(ollama_commands(model_path, args.name))

    print("\n" + "=" * 64)
    print("③ 外部用户调用示例（已对接 /v1）")
    print("=" * 64)
    print(
        'from openai import OpenAI\n'
        f'c = OpenAI(base_url="https://你的域名/v1", api_key="sk-xxxx")\n'
        'c.chat.completions.create(\n'
        f'    model="{args.name}",\n'
        '    messages=[{"role": "user", "content": "你好"}]\n'
        ')'
    )

    if args.out:
        Path(args.out).write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[serve_register] 已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
