"""
Ministers Factory — 八大臣创建工厂

Each minister is assigned a capability profile extracted from real-world
AI strengths, as identified by market analysis (SuperCLUE, industry reports, etc).

The profiles map to real AIs:
    丞相      → GPT-5/GPT family: comprehensive reasoning, task decomposition, writing
    御史大夫  → Claude-Opus family: long-text review, safety, beautiful writing
    太史令    → Perplexity-style: real-time search, fact-checking, source attribution
    工部尚书  → DeepSeek-R1 + Cursor: code generation, debugging, cost-efficiency
    太常      → Gemini family: multimodal understanding, media processing
    大司农    → DeepSeek-cost-optimizer: resource management, math reasoning
    太卜      → Claude-extended-thinking: scientific reasoning, prediction, complex math
    卫尉      → Constitutional AI + security: vulnerability detection, privacy protection
"""

from huanxin.court.minister import Minister, MinisterProfile
from huanxin.court.ministers.chancellor import ChancellorMinister
from huanxin.court.ministers.censor import CensorMinister
from huanxin.court.ministers.historian import HistorianMinister
from huanxin.court.ministers.works import WorksMinister
from huanxin.court.ministers.ceremonies import CeremoniesMinister
from huanxin.court.ministers.finance import FinanceMinister
from huanxin.court.ministers.diviner import DivinerMinister
from huanxin.court.ministers.guard import GuardMinister


def create_ministers() -> list[Minister]:
    """Create and return all eight standard ministers."""
    return [
        ChancellorMinister(),
        CensorMinister(),
        HistorianMinister(),
        WorksMinister(),
        CeremoniesMinister(),
        FinanceMinister(),
        DivinerMinister(),
        GuardMinister(),
    ]
