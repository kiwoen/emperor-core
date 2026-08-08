#!/usr/bin/env python3
"""emperor-core 自主运行主入口

Usage:
    python main.py --mode chat      交互式对话模式
    python main.py --mode demo      自动演示模式（无需外部API）
    python main.py --mode server    启动 Web 服务
    python main.py --mode demo --mock   强制使用 mock 数据
"""

import argparse
import signal
import sys
import os

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BANNER = r"""
  ╔══════════════════════════════════════════════════════════════╗
  ║                                                              ║
  ║     ███████╗███╗   ███╗██████╗ ███████╗██████╗  ██████╗ ██████╗
  ║     ██╔════╝████╗ ████║██╔══██╗██╔════╝██╔══██╗██╔═══██╗██╔══██╗
  ║     █████╗  ██╔████╔██║██████╔╝█████╗  ██████╔╝██║   ██║██████╔╝
  ║     ██╔══╝  ██║╚██╔╝██║██╔═══╝ ██╔══╝  ██╔══██╗██║   ██║██╔══██╗
  ║     ███████╗██║ ╚═╝ ██║██║     ███████╗██║  ██║╚██████╔╝██║  ██║
  ║     ╚══════╝╚═╝     ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝
  ║                                                              ║
  ║              C O R E   v2.0.0                                ║
  ║      自进化多智能体协同系统                                     ║
  ╚══════════════════════════════════════════════════════════════╝
"""


def setup_signal_handlers(cleanup_fn):
    """设置信号处理器，优雅退出"""
    def handler(sig, frame):
        print("\n\n[emperor-core] 收到退出信号，正在清理...")
        if cleanup_fn:
            cleanup_fn()
        print("[emperor-core] 朝堂已关闭。吾皇万岁万岁万万岁！")
        sys.exit(0)
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


def load_config(config_path=None):
    """加载配置文件"""
    import yaml
    default_config = {
        "emperor": {
            "name": "Emperor",
            "mode": "auto",
            "llm_provider": "mock",
            "llm_model": "mock-model",
            "num_ministers": 12,
        },
        "ministers": {
            "enable_evolution": True,
            "evolution_interval": 10,
            "merit_decay": 0.99,
        },
        "consensus": {
            "default_strategy": "majority_vote",
            "max_rounds": 3,
            "min_confidence": 0.6,
        },
    }
    if config_path and os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f)
            if user_config:
                # 浅合并
                for k, v in user_config.items():
                    if isinstance(v, dict) and k in default_config:
                        default_config[k].update(v)
                    else:
                        default_config[k] = v
    return default_config


def run_chat_mode(config):
    """交互式对话模式"""
    from jarvis.emperor import Emperor
    from jarvis.router import Router

    emperor = Emperor(config=config)
    router = Router()

    print(BANNER)
    print(f"  Emperor [{config['emperor']['name']}] 已就位")
    print(f"  朝中大臣：{len(emperor.ministers)} 位")
    print(f"  共识策略：{config['consensus']['default_strategy']}")
    print(f"  输入 'quit' 或 'exit' 退朝")
    print("-" * 60)

    def cleanup():
        emperor.shutdown()

    setup_signal_handlers(cleanup)

    while True:
        try:
            user_input = input("\n[老板] ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "退朝", "退下"):
            print("[Emperor] 退朝！众爱卿辛苦了。")
            break

        # 路由
        intent = router.classify(user_input) if hasattr(router, 'classify') else {"category": "general", "confidence": 0.9}

        print(f"\n[Router] 意图: {intent.get('category', 'general')} (置信度: {intent.get('confidence', 0.9):.2f})")

        # 大臣处理
        try:
            if hasattr(emperor, 'deliberate'):
                result = emperor.deliberate(user_input, ministers=None, strategy=None)
            else:
                result = emperor.process(user_input)
            print(f"\n[Emperor] {result}")
        except Exception as e:
            print(f"\n[Emperor] 朝堂谏议出错：{e}")
            # 尝试自愈
            if hasattr(emperor, 'heal'):
                restored = emperor.heal()
                if restored:
                    print("[自愈] 朝堂已恢复，请重试。")

    cleanup()


def run_demo_mode(config, use_mock=True):
    """自动演示模式"""
    from jarvis.demo import DemoRunner

    print(BANNER)
    print("  演示模式启动...")
    print(f"  Mock 模式: {'开启' if use_mock else '关闭'}")
    print("-" * 60)

    runner = DemoRunner(config=config, use_mock=use_mock)
    results = runner.run_all_demos()

    print("\n" + "=" * 60)
    print("  演示报告")
    print("=" * 60)

    passed = sum(1 for r in results if r.get("status") == "passed")
    failed = sum(1 for r in results if r.get("status") == "failed")

    for r in results:
        icon = "[OK]" if r.get("status") == "passed" else "[FAIL]"
        print(f"  {icon} {r['name']}")
        if r.get("summary"):
            print(f"       {r['summary']}")

    print(f"\n  总计: {len(results)} 场景, {passed} 通过, {failed} 失败")
    return 0 if failed == 0 else 1


def run_server_mode(config, host="0.0.0.0", port=5000):
    """启动 Web 服务"""
    print(BANNER)
    print(f"  Web 服务启动中... http://{host}:{port}")
    print("-" * 60)

    try:
        from jarvis.server import create_app
        app = create_app(config)
        app.run(host=host, port=port, debug=False)
    except ImportError:
        print("[ERROR] Web 服务模块未安装。请确保 Flask 已安装。")
        print("  运行 pip install flask 后重试。")
        return 1


def main():
    parser = argparse.ArgumentParser(description="emperor-core 自主运行入口")
    parser.add_argument("--mode", choices=["chat", "demo", "server"], default="demo",
                        help="运行模式 (默认: demo)")
    parser.add_argument("--config", type=str, default=None,
                        help="配置文件路径 (YAML)")
    parser.add_argument("--port", type=int, default=5000,
                        help="Web 服务端口 (默认: 5000)")
    parser.add_argument("--mock", action="store_true", default=True,
                        help="使用 Mock 数据 (默认开启)")
    parser.add_argument("--no-mock", dest="mock", action="store_false",
                        help="禁用 Mock，使用真实 API")

    args = parser.parse_args()

    config = load_config(args.config)
    if args.mock:
        config.setdefault("emperor", {})["llm_provider"] = "mock"

    if args.mode == "demo":
        return run_demo_mode(config, use_mock=args.mock)
    elif args.mode == "chat":
        return run_chat_mode(config)
    elif args.mode == "server":
        return run_server_mode(config, port=args.port)


if __name__ == "__main__":
    sys.exit(main() or 0)
