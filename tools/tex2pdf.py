#!/usr/bin/env python3
"""
LaTeX → PDF 编译工具
把 md2tex 生成的 .tex 编译成 PDF（xelatex，自动补跑目录/交叉引用）。
命名规则: name.date.time.paper.pdf

用法:
  python3 tools/tex2pdf.py                     # 编译 output/ 最新一次的全部 .tex
  python3 tools/tex2pdf.py 高等数学             # 只编译指定笔记
  python3 tools/tex2pdf.py /path/to/x.tex      # 编译任意 .tex
  python3 tools/tex2pdf.py --once              # 只跑一遍 xelatex（跳过补跑）
"""

import glob, os, re, shutil, subprocess, sys, unicodedata

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(PROJECT, "output")

# name.date.time.paper 例如 高等数学.20261024.211500.a4
STEM_RE = re.compile(r"^(?P<name>.+)\.(?P<ts>\d{8}\.\d{6})\.(?P<paper>a4|b5)$")
TS_DIR_RE = re.compile(r"^\d{8}\.\d{6}$")


def parse_args():
    args = list(sys.argv[1:])
    once = "--once" in args
    if once:
        args.remove("--once")
    targets = [a for a in args if not a.startswith("-")]
    return targets, once


def dated_dirs():
    """output/ 下的时间戳目录，新的在前。"""
    if not os.path.isdir(OUTPUT):
        return []
    dirs = [d for d in os.listdir(OUTPUT)
            if TS_DIR_RE.match(d) and os.path.isdir(os.path.join(OUTPUT, d))]
    return sorted(dirs, reverse=True)


def collect(targets):
    """按参数收集 .tex；无参数时取最新一次转换的产物。"""
    dirs = dated_dirs()
    texs = []

    if targets:
        for t in targets:
            if os.path.isfile(t) and t.lower().endswith(".tex"):
                texs.append(os.path.abspath(t))
                continue
            if os.path.isfile(t + ".tex"):
                texs.append(os.path.abspath(t + ".tex"))
                continue
            hit = []
            for d in dirs:
                for f in sorted(glob.glob(os.path.join(OUTPUT, d, "*.tex"))):
                    stem = os.path.splitext(os.path.basename(f))[0]
                    m = STEM_RE.match(stem)
                    name = m.group("name") if m else stem
                    if name == t or name.startswith(t):
                        hit.append(f)
                if hit:
                    break
            texs.extend(hit)
    elif dirs:
        texs = sorted(glob.glob(os.path.join(OUTPUT, dirs[0], "*.tex")))
    else:
        texs = sorted(glob.glob(os.path.join(PROJECT, "*.tex")))

    seen, out = set(), []
    for f in texs:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def needs_rerun(log_path):
    try:
        with open(log_path, encoding="utf-8", errors="ignore") as f:
            txt = f.read()
    except OSError:
        return False
    return "Rerun to get" in txt or "Label(s) may have changed" in txt


def page_count(pdf):
    if not shutil.which("pdfinfo"):
        return "—"
    r = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True)
    for line in r.stdout.split("\n"):
        if line.startswith("Pages:"):
            return line.split(":")[1].strip()
    return "—"


def disp_width(s):
    """终端显示宽度（中日韩全角字符算 2 格）。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def pad(s, width):
    return s + " " * max(0, width - disp_width(s))


def build(tex_path, once):
    """在独立 _build 目录里编译，成功后把 PDF 移回 .tex 旁边。"""
    stem = os.path.splitext(os.path.basename(tex_path))[0]
    src_dir = os.path.dirname(tex_path)
    work = os.path.join(src_dir, "_build", stem)
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)

    max_passes = 1 if once else 3
    min_passes = 1 if once else 2  # 有目录时至少两遍，否则 .toc 页码是旧的
    ok = False
    for p in range(1, max_passes + 1):
        cmd = [
            "xelatex",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-file-line-error",
            f"-output-directory={work}",
            os.path.basename(tex_path),
        ]
        r = subprocess.run(cmd, cwd=src_dir, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  失败: 第 {p} 遍 xelatex 退出码 {r.returncode}")
            for line in (r.stdout or "").split("\n"):
                if line.startswith("!") or (".tex:" in line and ":" in line):
                    print(f"    {line.strip()}")
            break
        ok = True
        # 至少跑 min_passes 遍；之后只在 LaTeX 提示 Rerun 时补跑
        if p < min_passes:
            continue
        if once or not needs_rerun(os.path.join(work, stem + ".log")):
            break

    pdf = os.path.join(work, stem + ".pdf")
    if not ok or not os.path.exists(pdf):
        shutil.rmtree(work, ignore_errors=True)
        return None

    dst = os.path.join(src_dir, stem + ".pdf")
    shutil.move(pdf, dst)
    shutil.rmtree(work, ignore_errors=True)
    build_root = os.path.join(src_dir, "_build")
    if os.path.isdir(build_root) and not os.listdir(build_root):
        os.rmdir(build_root)
    return dst


def main():
    targets, once = parse_args()

    if not shutil.which("xelatex"):
        print("错误: 未找到 xelatex，请先安装 TeX Live")
        sys.exit(1)

    texs = collect(targets)
    if not texs:
        if targets:
            print(f"未找到匹配的 .tex: {targets}")
        else:
            print("未找到任何 .tex")
        print("  先运行: python3 tools/md2tex.py")
        sys.exit(1)

    passes = 1 if once else "2~3"
    print("=" * 44)
    print("  LaTeX → PDF 编译工具")
    print(f"  引擎: xelatex（{passes} 遍）")
    print(f"  文件: {len(texs)} 个")
    for f in texs:
        print(f"    {os.path.basename(f)}")
    print("=" * 44)

    rows = []
    ok_count = 0
    for tex in texs:
        stem = os.path.splitext(os.path.basename(tex))[0]
        print(f"\n--- {stem} ---")
        pdf = build(tex, once)
        if not pdf:
            print("  xelatex 编译失败")
            rows.append((stem + ".pdf", "失败"))
            continue

        pages = page_count(pdf)
        nbytes = os.path.getsize(pdf)
        size = f"{nbytes / 1024:.0f} KB" if nbytes < 1024 * 1024 else f"{nbytes / 1024 / 1024:.1f} MB"
        print(f"  pdf  -> {os.path.basename(pdf)}")
        print(f"  {pages} 页, {size}")

        m = STEM_RE.match(stem)
        if m:
            latest = os.path.join(OUTPUT, f"{m.group('name')}.latest.{m.group('paper')}.pdf")
            shutil.copy2(pdf, latest)
        rows.append((os.path.basename(pdf), pages))
        ok_count += 1

    print(f"\n{'=' * 44}")
    print("  编译完成")
    width = max([disp_width(name) for name, _ in rows] + [disp_width("文件")])
    print(f"  {pad('文件', width)} 页数")
    print(f"  {'-' * width} ----")
    for name, pages in rows:
        print(f"  {pad(name, width)} {pages}")
    print("=" * 44)
    sys.exit(0 if ok_count == len(texs) else 1)


if __name__ == "__main__":
    main()
