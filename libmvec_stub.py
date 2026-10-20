#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
box64 libmvec 向量数学桩注入（补 _ZGVbN2v_log2 等缺失符号）

背景
----
Wine 的 x86_64-unix/libx265.so.217 由 glibc 的 libm.so 链接脚本带入
AS_NEEDED(libmvec.so.1)，因此 ELF 里带有 R_X86_64_GLOB_DAT 引用
_ZGVbN2v_log2（ABI: __m128d __m128d）。

box64 的 src/wrapped/wrappedlibmvec.c 目前只实现了 sin/cos/exp/log/pow
这几个 GOM 桩，log2 / log10 / exp2 / cbrt / tan / ... 整族仍是上游

    //GO(_ZGVbN2v_log2, vFv)

这样的注释占位。于是装载 libx265 时符号解析失败：

    [BOX64] Error: Global Symbol _ZGVbN2v_log2 not found,
            cannot apply R_X86_64_GLOB_DAT @0x3f16507320 ((nil))
            in /data/data/.../lib/wine/x86_64-unix/libx265.so.217

box64 会把该 GOT 槽写成 0。x265 一旦走到向量化 log2 分支
（psy-rd / 码率控制），就是 call 0 → SIGSEGV。

本脚本做什么
------------
1) 向 wrappedlibmvec_private.h 追加 `GOM(_ZGV..., vFEv)` 导出项；
2) 向 wrappedlibmvec.c 追加对应的 `EXPORT void my__ZGV...(x64emu_t*)`
   实现，内部逐 lane 调用宿主标量 libm 函数
   （apply_v2d_unary / apply_v4d_unary / apply_v4f_unary / apply_v8f_unary
     以及对应的 binary 版本，都是上游已有的 helper）。

纯追加式注入：不依赖上下文行号，上游改代码也不会打不上；
已存在同名 my_ 实现时自动跳过（幂等）。
"""

import os
import re
import sys

# ---- 需要补齐的标量函数（libmvec 里就是这些的向量化版本）----
UNARY_D = ['log2', 'log10', 'exp2', 'cbrt', 'tan', 'tanh', 'sinh', 'cosh',
           'atan', 'asin', 'acos', 'asinh', 'acosh', 'atanh', 'erf', 'erfc',
           'log1p', 'expm1']
UNARY_F = [n + 'f' for n in UNARY_D]
BINARY_D = ['atan2', 'hypot']
BINARY_F = [n + 'f' for n in BINARY_D]

# (符号前缀, 'D'=double / 'F'=float, 上游 helper)
UNARY_VARIANTS = [
    ('_ZGVbN2v_', 'D', 'apply_v2d_unary'),   # SSE2  2 x double  (xmm0)
    ('_ZGVcN4v_', 'D', 'apply_v4d_unary'),   # AVX   4 x double  (ymm0)
    ('_ZGVdN4v_', 'D', 'apply_v4d_unary'),   # AVX2  4 x double  (ymm0)
    ('_ZGVbN4v_', 'F', 'apply_v4f_unary'),   # SSE2  4 x float   (xmm0)
    ('_ZGVcN8v_', 'F', 'apply_v8f_unary'),   # AVX   8 x float   (ymm0)
    ('_ZGVdN8v_', 'F', 'apply_v8f_unary'),   # AVX2  8 x float   (ymm0)
]
BINARY_VARIANTS = [
    ('_ZGVbN2vv_', 'D', 'apply_v2d_binary'),
    ('_ZGVcN4vv_', 'D', 'apply_v4d_binary'),
    ('_ZGVdN4vv_', 'D', 'apply_v4d_binary'),
    ('_ZGVbN4vv_', 'F', 'apply_v4f_binary'),
    ('_ZGVcN8vv_', 'F', 'apply_v8f_binary'),
    ('_ZGVdN8vv_', 'F', 'apply_v8f_binary'),
]

C_MARKER = '#include "wrappedlib_init.h"'


def find_tree():
    """定位 box64 源码树：优先 cwd，其次脚本上一级（CI 里 cwd 就是 box64/）。"""
    for base in (os.getcwd(), os.path.dirname(os.path.abspath(__file__)),
                 os.path.join(os.getcwd(), 'box64')):
        if os.path.isfile(os.path.join(base, 'src/wrapped/wrappedlibmvec.c')):
            return base
    return None


def main(argv):
    if len(argv) >= 3:
        cpath, hpath = argv[1], argv[2]
    else:
        base = find_tree()
        if base is None:
            print('❌ 找不到 box64 源码树（src/wrapped/wrappedlibmvec.c）')
            print('   用法: libmvec_stub.py [wrappedlibmvec.c] [wrappedlibmvec_private.h]')
            return 3
        cpath = os.path.join(base, 'src/wrapped/wrappedlibmvec.c')
        hpath = os.path.join(base, 'src/wrapped/wrappedlibmvec_private.h')

    for p in (cpath, hpath):
        if not os.path.isfile(p):
            print('❌ 缺少文件: %s' % p)
            print('   该 box64 提交尚未合入 libmvec 包装，无法注入桩')
            return 3

    src = open(cpath, encoding='utf-8').read()
    hdr = open(hpath, encoding='utf-8').read()

    entries = []
    for prefix, kind, helper in UNARY_VARIANTS:
        for name in (UNARY_D if kind == 'D' else UNARY_F):
            entries.append((prefix + name, name, helper))
    for prefix, kind, helper in BINARY_VARIANTS:
        for name in (BINARY_D if kind == 'D' else BINARY_F):
            entries.append((prefix + name, name, helper))

    # 上游已经实现过的不重复注入
    todo = [e for e in entries if ('my_' + e[0]) not in src]
    skipped = len(entries) - len(todo)

    bad_helpers = sorted({e[2] for e in todo if e[2] not in src})
    if bad_helpers:
        print('❌ 上游 wrappedlibmvec.c 里找不到 helper: %s' % ' '.join(bad_helpers))
        print('   （上游可能重构了 apply_* 辅助函数，需要人工适配）')
        return 4

    pos = src.rfind(C_MARKER)
    if pos < 0:
        print('❌ wrappedlibmvec.c 里找不到 %s 锚点' % C_MARKER)
        return 5

    cblock = ['',
              '// ===== libmvec 向量数学桩（由 libmvec_stub.py 注入）=====',
              '// 逐 lane 调用宿主标量 libm 函数，补齐 glibc libmvec 的 _ZGV* ABI。']
    for mangled, scalar, helper in todo:
        cblock.append('EXPORT void my_%s(x64emu_t* emu) { %s(emu, %s); }'
                      % (mangled, helper, scalar))
    src_new = src[:pos] + '\n'.join(cblock) + '\n\n' + src[pos:]

    # 注意：box64 的 rebuild_wrappers.py 把 `//GO(sym, vFv)` 注释行也当成
    # 一个“已声明符号”（用来预登记 ABI 类型）。若我们另起一行追加 GOM，
    # 同名就会出现两次，CI 会报：
    #     Function or data _ZGVbN2v_log2 is duplicated! (wrappedlibmvec_private.h)
    # 所以必须「把注释行原地改成 GOM」，而不是追加。
    replaced = 0
    appended = []
    for mangled, scalar, helper in todo:
        pat = re.compile(r'^//GO\(%s,[^\n]*\)[ \t]*$' % re.escape(mangled), re.M)
        if pat.search(hdr):
            hdr = pat.sub('GOM(%s, vFEv)' % mangled, hdr, count=1)
            replaced += 1
        elif re.search(r'^GOM\(%s,' % re.escape(mangled), hdr, re.M):
            continue                      # 已经是导出项，跳过
        else:
            appended.append('GOM(%s, vFEv)' % mangled)
    if appended:
        hdr = hdr.rstrip('\n') + '\n' + \
              '\n'.join(['', '// ===== libmvec 向量数学桩（由 libmvec_stub.py 注入）====='] + appended) + '\n'
    added_h = replaced + len(appended)

    # 自检：同一符号名不允许在导出表里出现两次
    seen = {}
    for line in hdr.splitlines():
        m = re.match(r'^[ \t]*(?://)?[ \t]*(GO|GOM|GO2|DATA)\([ \t]*([A-Za-z0-9_$]+)', line)
        if m:
            seen.setdefault(m.group(2), []).append(line.strip())
    dups = {k: v for k, v in seen.items() if len(v) > 1}
    if dups:
        print('❌ 导出表出现重复符号（rebuild_wrappers.py 会报 duplicated）:')
        for k, v in list(dups.items())[:10]:
            print('   %s -> %s' % (k, ' | '.join(v)))
        return 7
    hdr_new = hdr

    open(cpath, 'w', encoding='utf-8').write(src_new)
    open(hpath, 'w', encoding='utf-8').write(hdr_new)

    print('   wrappedlibmvec.c        : +%d 个 my_ 实现' % len(todo))
    print('   wrappedlibmvec_private.h: +%d 个 GOM 导出项' % added_h)
    print('   跳过（上游已实现）       : %d 个' % skipped)
    print('   覆盖变体                 : SSE2(b) / AVX(c) / AVX2(d)')
    ok = ('EXPORT void my__ZGVbN2v_log2' in src_new) and \
         bool(re.search(r'^GOM\(_ZGVbN2v_log2,', hdr_new, re.M))
    print('   MVEC_LOG2_STUB=%s' % ('OK' if ok else 'MISSING'))
    return 0 if ok else 6


if __name__ == '__main__':
    sys.exit(main(sys.argv))