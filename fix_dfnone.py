#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 把 ARM64 dynarec 的 df-none（延迟 FLAGS 置0）改回旧版语义：
# 在过渡点立即写盘，而不是推迟到代码块出口。
#
# v0.4.0 之后的 status 枚举模型需要改 3 处：
#   1) dynarec_arm64_helper.h  SET_DFNONE() -> 立即 FORCE_DFNONE()
#   2) dynarec_arm64_pass0.h   分析阶段不再产生 status_none_pending
#   3) dynarec_arm64_pass1.h   同上
# 只改 1) 会使分析态(f_entry=pending)与 codegen 态(none)不一致，2)3) 必须配套。
import os
import re
import sys

NEW_MACRO = "#define SET_DFNONE()                      \\\n    do {                                  \\\n        if (dyn->f != status_none) {      \\\n            FORCE_DFNONE();               \\\n            dyn->f = status_none;         \\\n        }                                 \\\n    } while (0)"

OLD_MACRO = "#define SET_DFNONE()                      \\\n    do {                                  \\\n        if (dyn->f != status_none) {      \\\n            dyn->f = status_none_pending; \\\n        }                                 \\\n    } while (0)"

CONV = "        if(dyn->f==status_none_pending) dyn->f=status_none;  \\"
MARK = "status_none_pending) dyn->f=status_none"

HELPER = "src/dynarec/arm64/dynarec_arm64_helper.h"
PASSES = ["src/dynarec/arm64/dynarec_arm64_pass0.h",
          "src/dynarec/arm64/dynarec_arm64_pass1.h"]


def fix_macro(path):
    s = open(path).read()
    if NEW_MACRO in s:
        return "already"
    if OLD_MACRO in s:
        open(path, "w").write(s.replace(OLD_MACRO, NEW_MACRO, 1))
        return "ok"
    m = re.search(r"#define SET_DFNONE\(\)[^\n]*\n(?:[^\n]*\\\n)*[^\n]*while \(0\)", s)
    if not m:
        return "fail"
    open(path, "w").write(s[:m.start()] + NEW_MACRO + s[m.end():])
    return "ok-regex"


def fix_pass(path):
    L = open(path).read().split("\n")
    for l in L:
        if MARK in l:
            return "already"
    i = -1
    for j, l in enumerate(L):
        if l.startswith("#define SETFLAGS"):
            i = j
            break
    if i < 0:
        return "fail"
    for j in range(i, min(i + 14, len(L))):
        if "dyn->f=" in L[j] and L[j].rstrip().endswith("\\"):
            L.insert(j + 1, CONV)
            open(path, "w").write("\n".join(L))
            return "ok"
    return "fail"


def main():
    if not os.path.exists(HELPER):
        print("ERROR: 找不到 " + HELPER + "（请在 box64 源码根目录运行）")
        return 1
    bad = []
    r = fix_macro(HELPER)
    print("%-26s: %s" % (HELPER, r))
    if r == "fail":
        bad.append(HELPER)
    for p in PASSES:
        r = fix_pass(p)
        print("%-26s: %s" % (p, r))
        if r == "fail":
            bad.append(p)
    if bad:
        print("ERROR: 以下文件未能修改: " + ", ".join(bad))
        return 1
    print("dfnone-fix OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
