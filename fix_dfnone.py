import sys
import re

FILE = 'src/dynarec/arm64/dynarec_arm64_helper.h'

OLD = '''#define SET_DFNONE()                      \\
    do {                                  \\
        if (dyn->f != status_none) {      \\
            dyn->f = status_none_pending; \\
        }                                 \\
    } while (0)'''

NEW = '''#define SET_DFNONE()                      \\
    do {                                  \\
        if (dyn->f != status_none) {      \\
            FORCE_DFNONE();               \\
            dyn->f = status_none;         \\
        }                                 \\
    } while (0)'''


def main():
    try:
        s = open(FILE).read()
    except FileNotFoundError:
        print("ERROR: 找不到 " + FILE)
        return 1

    if OLD in s:
        open(FILE, 'w').write(s.replace(OLD, NEW, 1))
        print("OK")
        return 0

    if NEW in s:
        print("OK")
        return 0

    m = re.search(r'#define SET_DFNONE\(\).*?while \(0\)', s, re.DOTALL)
    print("ERROR")
    print(m.group(0) if m else "找不到 SET_DFNONE")
    return 1


if __name__ == '__main__':
    sys.exit(main())
  
