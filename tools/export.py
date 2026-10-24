#!/usr/bin/env python3
"""
笔记导出工具（一步完成）
按顺序调用 md2tex.py 与 tex2pdf.py，把 .md 笔记直接导出成 PDF。

用法:
  python3 tools/export.py                     # 全部笔记 → A4 PDF（带目录）
  python3 tools/export.py --b5                # A4 + B5
  python3 tools/export.py --no-toc            # 无目录版本
  python3 tools/export.py 高等数学             # 只导出指定笔记
  python3 tools/export.py --once              # xelatex 只跑一遍

等价于:
  python3 tools/md2tex.py [选项] && python3 tools/tex2pdf.py
"""

import os, subprocess, sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(PROJECT, "tools")

MD2TEX = os.path.join(TOOLS, "md2tex.py")
TEX2PDF = os.path.join(TOOLS, "tex2pdf.py")

# 只传给 md2tex 的开关
MD2TEX_FLAGS = ("--b5", "--no-toc", "--keep-header")
# 只传给 tex2pdf 的开关
TEX2PDF_FLAGS = ("--once",)


def split_args(argv):
    """把命令行拆成 md2tex 选项、tex2pdf 选项、笔记名三部分。"""
    md_flags, pdf_flags, notes = [], [], []
    for a in argv:
        if a in ("-h", "--help"):
            print(__doc__.strip())
            sys.exit(0)
        if a in MD2TEX_FLAGS:
            md_flags.append(a)
        elif a in TEX2PDF_FLAGS:
            pdf_flags.append(a)
        elif a.startswith("-"):
            print(f"错误: 未知选项 {a}")
            print("  可用: --b5  --no-toc  --keep-header  --once")
            sys.exit(2)
        else:
            notes.append(a)
    return md_flags, pdf_flags, notes


def run(cmd):
    """不捕获输出，实时显示子脚本的进度与报错。"""
    return subprocess.run(cmd).returncode


def main():
    md_flags, pdf_flags, notes = split_args(sys.argv[1:])

    print("=" * 44)
    print("  笔记导出（md2tex → tex2pdf）")
    print(f"  笔记: {' '.join(notes) if notes else '全部'}")
    print(f"  选项: {' '.join(md_flags + pdf_flags) or '无'}")
    print("=" * 44, flush=True)

    print("\n[1/2] Markdown → LaTeX", flush=True)
    code = run([sys.executable, MD2TEX] + md_flags + notes)
    if code != 0:
        print("\n  转换失败，已停止（未编译 PDF）")
        sys.exit(code or 1)

    # 不传笔记名：tex2pdf 默认编译最新一次转换的目录，正是上面刚生成的产物
    print("\n[2/2] LaTeX → PDF", flush=True)
    code = run([sys.executable, TEX2PDF] + pdf_flags)
    if code != 0:
        print("\n  编译失败")
        sys.exit(code or 1)

    print(f"\n{'=' * 44}")
    print("  导出完成")
    print(f"  输出: {os.path.join(PROJECT, 'output')}")
    print("=" * 44)


if __name__ == "__main__":
    main()
