#!/usr/bin/env python3
"""
Markdown → LaTeX 转换工具
把 .md 笔记转换成可被 xelatex 直接编译的独立 .tex。
命名规则: name.date.time.paper.tex

用法:
  python3 tools/md2tex.py                       # 转换全部笔记（A4）
  python3 tools/md2tex.py --b5                  # 同时生成 B5 版
  python3 tools/md2tex.py 高等数学               # 只转换指定笔记
  python3 tools/md2tex.py /path/to/x.md         # 转换任意路径的笔记
  python3 tools/md2tex.py --keep-header         # 保留生成的 _header.tex（排查用）
"""

import glob, os, shutil, subprocess, sys
from datetime import datetime

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(PROJECT, "tools")
OUTPUT = os.path.join(PROJECT, "output")
TIMESTAMP = datetime.now().strftime("%Y%m%d.%H%M%S")
DATED_DIR = os.path.join(OUTPUT, TIMESTAMP)
LATEST_DIR = OUTPUT

CJK_FONT = "Noto Serif CJK SC"
MAIN_FONT = "Noto Serif CJK SC"
MARGIN = "2cm"

# 数学字体：按顺序探测本机可用项（STIX Two Math 为当前实际字体名）
MATH_FONT_CANDIDATES = ["STIX Two Math", "STIX Math", "XITS Math"]

TEX_HEADER_SRC = os.path.join(TOOLS, "list-spacing.tex")
TEX_HEADER = os.path.join(TOOLS, "_header.tex")
LUA_FILTER = os.path.join(TOOLS, "unicode2latex.lua")


def parse_args():
    """拆出布尔开关和笔记参数（保持 noteANote 的手写解析风格）。"""
    args = list(sys.argv[1:])
    do_b5 = "--b5" in args
    if do_b5:
        args.remove("--b5")
    keep_header = "--keep-header" in args
    if keep_header:
        args.remove("--keep-header")
    targets = [a for a in args if not a.startswith("-")]
    return targets, do_b5, keep_header


def font_family(name):
    """用 fc-match 解析字体族名，判断字体是否真的装了。"""
    if not shutil.which("fc-match"):
        return name
    r = subprocess.run(["fc-match", "-f", "%{family}", name],
                       capture_output=True, text=True)
    return r.stdout.strip()


def resolve_math_font():
    for name in MATH_FONT_CANDIDATES:
        fam = font_family(name)
        if fam and name.lower() in fam.lower():
            return name
    return None


def collect_notes(targets):
    """收集 .md：项目根目录 + focus/，可用笔记名或路径筛选。"""
    found = []

    def scan(path):
        for f in sorted(glob.glob(os.path.join(path, "*.md"))):
            name = os.path.splitext(os.path.basename(f))[0]
            if name.lower() in ("readme", "claude"):
                continue
            found.append((name, f))

    scan(PROJECT)
    focus_dir = os.path.join(PROJECT, "focus")
    if os.path.isdir(focus_dir):
        scan(focus_dir)

    if not targets:
        return found

    picked = []
    for t in targets:
        if os.path.isfile(t) and t.lower().endswith(".md"):
            picked.append((os.path.splitext(os.path.basename(t))[0], os.path.abspath(t)))
            continue
        picked.extend([x for x in found if x[0] == t or x[0].startswith(t)])

    seen, notes = set(), []
    for name, path in picked:
        if path not in seen:
            seen.add(path)
            notes.append((name, path))
    return notes


def write_header():
    """把导出时间写进 LaTeX 头（占位符 EXPORTTIME）。"""
    with open(TEX_HEADER_SRC, encoding="utf-8") as f:
        content = f.read().replace("EXPORTTIME", TIMESTAMP)
    with open(TEX_HEADER, "w", encoding="utf-8") as f:
        f.write(content)


def pandoc_cmd(paper, math_font):
    cmd = [
        "pandoc",
        "-s",
        "-t", "latex",
        "--pdf-engine=xelatex",
        "-V", "toc",
        "-V", "toc-depth=3",
        "-V", f"CJKmainfont={CJK_FONT}",
        "-V", f"mainfont={MAIN_FONT}",
        "-V", "mainfontoptions=Mapping=tex-text",
        "-V", f"monofont={MAIN_FONT}",
    ]
    if math_font:
        cmd += ["-V", f"mathfont={math_font}"]
    cmd += [
        "-V", "classoption=twoside",
        "--lua-filter", LUA_FILTER,
        "-H", TEX_HEADER,
        "-V", f"geometry:margin={MARGIN}",
        "-V", f"geometry:{paper}",
    ]
    return cmd


def run(cmd):
    """Run a command, return True on success."""
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  失败: 退出码 {r.returncode}")
        if r.stderr:
            for line in r.stderr.strip().split("\n")[-5:]:
                print(f"    {line}")
        return False
    if r.stderr:
        for line in r.stderr.strip().split("\n"):
            if line.startswith("[WARNING]") and "Missing character" in line:
                pass  # suppress font warnings
            elif line.strip():
                print(f"  [!] {line.strip()}")
    return True


def main():
    targets, do_b5, keep_header = parse_args()

    for cmd in ["pandoc"]:
        if not shutil.which(cmd):
            print(f"错误: 未找到 {cmd}，请先安装")
            sys.exit(1)

    formats = [("a4", "a4paper")]
    if do_b5:
        formats.append(("b5", "b5paper"))

    notes = collect_notes(targets)
    if not notes:
        if targets:
            print(f"未找到匹配的笔记: {targets}")
        else:
            print("未找到任何 .md 文件")
        sys.exit(1)

    math_font = resolve_math_font()
    if CJK_FONT.lower() not in font_family(CJK_FONT).lower():
        print(f"警告: 未找到中文字体 {CJK_FONT}，PDF 可能出现豆腐块")

    os.makedirs(DATED_DIR, exist_ok=True)

    # 清理上一轮 latest
    for old in glob.glob(os.path.join(LATEST_DIR, "*.latest.*.tex")):
        os.remove(old)

    write_header()

    print("=" * 44)
    print("  Markdown → LaTeX 转换工具")
    print(f"  时间: {TIMESTAMP}")
    print(f"  输出: {DATED_DIR}")
    print(f"  笔记: {len(notes)} 个")
    for name, path in notes:
        print(f"    {name} <- {os.path.relpath(path, PROJECT)}")
    print(f"  字体: {MAIN_FONT} / {math_font or '默认数学字体'}")
    print("=" * 44)

    ok_count = 0
    for note_name, md_path in notes:
        print(f"\n--- {note_name} ---")
        toc_title = f"{note_name} - Contents"

        for short, paper in formats:
            cmd = pandoc_cmd(paper, math_font)
            out_name = f"{note_name}.{TIMESTAMP}.{short}.tex"
            out_path = os.path.join(DATED_DIR, out_name)
            if run(cmd + ["-V", f"toc-title={toc_title}", md_path, "-o", out_path]):
                if os.path.getsize(out_path) < 500:
                    print(f"  {short}   -> {out_name} (文件过小，可能转换异常)")
                    continue
                print(f"  {short}   -> {out_name}")
                shutil.copy2(out_path, os.path.join(LATEST_DIR, f"{note_name}.latest.{short}.tex"))
                ok_count += 1

    if not keep_header and os.path.exists(TEX_HEADER):
        os.remove(TEX_HEADER)

    total = len(glob.glob(os.path.join(DATED_DIR, "*.tex")))
    print(f"\n{'=' * 44}")
    print("  转换完成")
    print(f"  目录: {DATED_DIR}")
    print(f"  文件: {total} 个")
    print()
    print("  下一步: python3 tools/tex2pdf.py")
    print("=" * 44)
    sys.exit(0 if ok_count else 1)


if __name__ == "__main__":
    main()
