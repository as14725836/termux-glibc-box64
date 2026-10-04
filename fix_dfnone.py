#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 新增运行时变量 BOX64_DYNAREC_DFNONE_OLD，用于在新版 box64 上复现“旧版”
# 的 df-none（延迟 FLAGS 置0）处理效果。
#
#   BOX64_DYNAREC_DFNONE_OLD=1  -> 旧行为：过渡点立即写盘   （本仓库默认值）
#   BOX64_DYNAREC_DFNONE_OLD=0  -> 上游新行为：推迟到块出口
#
# 需要改 4 处：
#   env.h     注册变量（默认 1）
#   helper.h  SET_DFNONE() 按变量二选一
#   pass0.h   分析阶段收敛 pending（仅当变量为1）
#   pass1.h   同上
import os
import re
import sys

BS = chr(92)   # 反斜杠

NEW_MACRO = (
    "#define SET_DFNONE()                          " + BS + "\n"
    "    do {                                      " + BS + "\n"
    "        if (dyn->f != status_none) {          " + BS + "\n"
    "            if (BOX64ENV(dynarec_dfnone_old)) { " + BS + "\n"
    "                FORCE_DFNONE();               " + BS + "\n"
    "                dyn->f = status_none;         " + BS + "\n"
    "            } else {                          " + BS + "\n"
    "                dyn->f = status_none_pending; " + BS + "\n"
    "            }                                 " + BS + "\n"
    "        }                                     " + BS + "\n"
    "    } while (0)")

CONV = "        if(BOX64ENV(dynarec_dfnone_old) && dyn->f==status_none_pending) dyn->f=status_none;  " + BS
CONV_RE = re.compile(r"^\s*if\(.*status_none_pending\)\s*dyn->f\s*=\s*status_none;")

ENV_H = "src/include/env.h"
HELPER = "src/dynarec/arm64/dynarec_arm64_helper.h"
HELPER_C = "src/dynarec/arm64/dynarec_arm64_helper.c"
PASSES = ["src/dynarec/arm64/dynarec_arm64_pass0.h",
          "src/dynarec/arm64/dynarec_arm64_pass1.h"]


def add_env(path):
    L = open(path).read().split("\n")
    for l in L:
        if "BOX64_DYNAREC_DFNONE_OLD" in l:
            return "already"
    idx = -1
    for i, l in enumerate(L):
        if l.strip().startswith("BOOLEAN(BOX64_DYNAREC_DF,"):
            idx = i
            break
    if idx < 0:
        return "fail"
    col = L[idx].index(BS)
    body = "    BOOLEAN(BOX64_DYNAREC_DFNONE_OLD, dynarec_dfnone_old, 1, 1, 1)"
    line = body + " " * max(1, col - len(body)) + BS
    L.insert(idx + 1, line)
    open(path, "w").write("\n".join(L))
    return "ok"


def fix_macro(path):
    s = open(path).read()
    m = re.search(r"#define SET_DFNONE\(\)[^\n]*\n(?:[^\n]*" + BS + BS + r"\n)*[^\n]*while \(0\)", s)
    if not m:
        return "fail"
    if "dynarec_dfnone_old" in m.group(0):
        return "already"
    open(path, "w").write(s[:m.start()] + NEW_MACRO + s[m.end():])
    return "ok"


def fix_pass(path):
    L = open(path).read().split("\n")
    for i, l in enumerate(L):
        if CONV_RE.match(l):
            if "dynarec_dfnone_old" in l:
                return "already"
            L[i] = CONV
            open(path, "w").write("\n".join(L))
            return "ok-replace"
    i = -1
    for j, l in enumerate(L):
        if l.startswith("#define SETFLAGS"):
            i = j
            break
    if i < 0:
        return "fail"
    for j in range(i, min(i + 14, len(L))):
        if "dyn->f=" in L[j] and L[j].rstrip().endswith(BS):
            L.insert(j + 1, CONV)
            open(path, "w").write("\n".join(L))
            return "ok-insert"
    return "fail"


def fix_transform(path):
    s = open(path).read()
    if "dfnone_old_tx" in s:
        return "already"
    anchor = "    if(dyn->insts[jmp].df_notneeded)" + chr(10) + "        return;"
    line = ("    if(BOX64ENV(dynarec_dfnone_old) && dyn->insts[jmp].f_entry==status_none && "
            "dyn->insts[ninst].f_exit!=status_none && !(dyn->insts[jmp].x64.need_before&X_PEND)) { FORCE_DFNONE(); } // dfnone_old_tx")
    k = s.find(anchor)
    if k < 0:
        return "fail"
    k += len(anchor)
    s = s[:k] + chr(10) + line + s[k:]
    open(path, "w").write(s)
    return "ok"
def applicable():
    """旧代（pre-deferred-flags 枚举）代码里没有这些标记，视为不需要改，直接成功退出。"""
    try:
        h = open(HELPER).read()
        e = open(ENV_H).read()
    except Exception:
        return True
    return "status_none_pending" in h


def main():
    if not os.path.exists(HELPER):
        print("ERROR: 找不到 " + HELPER + "（请在 box64 源码根目录运行）")
        return 1
    if not applicable():
        print("dfnone fix not applicable (pre-v0.4.0 model), skipped")
        return 0
    bad = []
    for path, fn in [(ENV_H, add_env), (HELPER, fix_macro), (HELPER_C, fix_transform)] + [(p, fix_pass) for p in PASSES]:
        r = fn(path)
        print("%-46s: %s" % (path, r))
        if r == "fail":
            bad.append(path)
    if bad:
        print("ERROR: 以下文件未能修改: " + ", ".join(bad))
        return 1
    print("dfnone envvar OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
