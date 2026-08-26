#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全量翻新 emperor-core -> huanxin-ai 的文档/配置残留（非代码 .py 已全干净）。

替换策略（顺序敏感，先精确后语义）：
  1. 文件名引用：emperor.py->core.py, emperor_cli.py->court_cli.py,
     test_emperor*->test_huanxin*, jarvis.*->huanxin.*
  2. 品牌/显示名：J.A.R.V.I.S.->幻炘AI, Jarvis->Huanxin, JARVIS->HUANXIN
  3. 产品/容器/卷名（连字符复合，先长后短）：
     emperor-core->huanxin-ai, emperor-data->huanxin-data, emperor-db->huanxin-db,
     emperor-relay->huanxin-relay, emperor-absorb->huanxin-absorb,
     emperor-backup->huanxin-backup, emperor-watch->huanxin-watch,
     emperor.example.com->huanxin.example.com
  4. 环境变量前缀：EMPEROR_->HUANXIN_, 孤立 EMPEROR->HUANXIN
  5. 专名 Emperor/emperor（语境感知）：
     - court 语境（路径含 /court/ 或内容含 天子/内阁/朝廷/圣旨/谕令/大臣/都察院/ImperialCourt）
       -> Sovereign / sovereign
     - 其余（主系统）-> Huanxin / huanxin

保留（不替换）：
  - 中文隐喻词：皇帝/天子/圣旨/谕令/内阁/大臣/都察院（它们是中文语义，非旧英文名）
  - 代码 .py 文件（已全干净，且避免误改逻辑）
  - runtime 快照、改名工具自身

用法：
  python fix_legacy_docs.py            # dry-run，只报告不改盘
  python fix_legacy_docs.py --apply    # 实跑内容替换
"""
import os
import sys

ROOT = r"E:/yuxing/AI自我进化/emperor-core-fresh"
SKIP_DIRS = {'.git', '.venv', '__pycache__', 'node_modules', 'build', 'dist',
             '.testdata', 'data', 'logs', 'versions'}
TEXT_EXT = {'.md', '.txt', '.yml', '.yaml', '.toml', '.cfg', '.ini', '.sh', '.bat',
            '.html', '.mermaid', '.dockerfile', '.rst', '.svg', '.css', '.js', '.ts', '.mk'}
SPECIAL = {'README.md', 'Makefile', 'Dockerfile', '.env.example', '.dockerignore', '.gitignore'}
TOOL = {'rename_to_huanxin.py', 'dryrun.txt', 'runlog.txt', 'compile.txt',
        'classify.txt', 'legacy_lines.txt', 'fix_legacy_docs.py'}
RUNTIME = {'huanxin/court/snapshots/index.json'}

# 精确替换（顺序敏感）
EXACT = [
    ('jarvis.court.emperor', 'huanxin.court.sovereign'),
    ('jarvis.emperor', 'huanxin.core'),
    ('jarvis.court', 'huanxin.court'),
    ('jarvis.cli', 'huanxin.cli'),
    ('emperor.py', 'core.py'),
    ('emperor_cli.py', 'court_cli.py'),
    ('test_emperor', 'test_huanxin'),
    ('jarvis.', 'huanxin.'),
    ('jarvis/', 'huanxin/'),
    ('jarvis', 'huanxin'),
    ('J.A.R.V.I.S.', '幻炘AI'),
    ('Jarvis', 'Huanxin'),
    ('JARVIS', 'HUANXIN'),
    ('emperor-core', 'huanxin-ai'),
    ('emperor-data', 'huanxin-data'),
    ('emperor-db', 'huanxin-db'),
    ('emperor-relay', 'huanxin-relay'),
    ('emperor-absorb', 'huanxin-absorb'),
    ('emperor-backup', 'huanxin-backup'),
    ('emperor-watch', 'huanxin-watch'),
    ('emperor.example.com', 'huanxin.example.com'),
    ('EMPEROR_', 'HUANXIN_'),
    ('EMPEROR', 'HUANXIN'),
]

COURT_KW = ['天子', '内阁', '朝廷', '圣旨', '谕令', '大臣', '都察院',
            'ImperialCourt', 'imperial_court']


def is_court(rel, text):
    if '/court/' in rel:
        return True
    return any(k in text for k in COURT_KW)


def process(rel, text):
    s = text
    for a, b in EXACT:
        s = s.replace(a, b)
    if is_court(rel, text):
        s = s.replace('Emperor', 'Sovereign').replace('emperor', 'sovereign')
    else:
        s = s.replace('Emperor', 'Huanxin').replace('emperor', 'huanxin')
    return s


def main():
    apply = '--apply' in sys.argv
    print('MODE:', 'APPLY' if apply else 'DRY-RUN')
    changed = []
    residuals = []
    for dp, dn, fn in os.walk(ROOT):
        if '.git' in dp.split(os.sep):
            continue
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if f in TOOL:
                continue
            fp = os.path.join(dp, f)
            ext = os.path.splitext(f)[1].lower()
            if ext not in TEXT_EXT and f not in SPECIAL:
                continue
            rel = os.path.relpath(fp, ROOT).replace(os.sep, '/')
            if rel in RUNTIME:
                continue
            try:
                t = open(fp, encoding='utf-8', errors='replace').read()
            except Exception:
                continue
            after = process(rel, t)
            if after != t:
                changed.append(rel)
                r = sum(1 for _ in __import__('re').finditer(
                    r'emperor|Emperor|EMPEROR|jarvis|JARVIS|J\.A\.R\.V\.I\.S\.', after))
                if r:
                    residuals.append((rel, r))
                if apply:
                    open(fp, 'w', encoding='utf-8').write(after)
    print('FILES TO CHANGE:', len(changed))
    for rel in sorted(changed):
        print('  ', rel)
    print('FILES WITH RESIDUAL AFTER FIX:', len(residuals))
    for rel, r in residuals:
        print('   RESIDUAL', rel, r)
    if apply:
        print('APPLIED.')


if __name__ == '__main__':
    main()
