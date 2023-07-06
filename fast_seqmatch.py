from textension.utils import _TupleBase, _named_index
from collections import defaultdict
from itertools import compress
from difflib import SequenceMatcher


class FastSequenceMatcher(SequenceMatcher, _TupleBase):
    isjunk     = None
    opcodes    = None
    autojunk   = True
    fullbcount = None

    matching_blocks = None

    a = _named_index(0)
    b = _named_index(1)

    def __init__(self, _):
        b = self.b
        self.b2j = b2j = dict.fromkeys(b)

        for key in b2j:
            b2j[key] = []

        for i, elt in enumerate(b):
            b2j[elt] += [i]

        if (n := (len(b) // 100 + 1)) >= 3:
            self.b2j = defaultdict(list, compress(b2j.items(), map(n.__gt__, map(len, b2j.values()))))
        else:
            self.b2j = defaultdict(list, b2j)

    def find_longest_match(self, alo=0, ahi=None, blo=0, bhi=None):
        a   = self.a
        b   = self.b
        b2j = self.b2j

        besti    = alo
        bestj    = blo
        bestsize = 0

        j2len = {}
        for i, c in enumerate(a[alo:ahi], start=alo):
            newj2len = {}
            if c in b2j:
                for j in b2j[c]:
                    if j < blo:
                        continue
                    elif j >= bhi:
                        break
                    if j - 1 in j2len:
                        k = newj2len[j] = j2len[j - 1] + 1
                    else:
                        k = newj2len[j] = 1
                    if k > bestsize:
                        besti = i - k + 1
                        bestj = j - k + 1
                        bestsize = k
            j2len = newj2len

        while besti > alo and bestj > blo and a[besti-1] == b[bestj-1]:
            besti, bestj, bestsize = besti-1, bestj-1, bestsize+1

        while besti + bestsize < ahi and bestj + bestsize < bhi and a[besti + bestsize] == b[bestj + bestsize]:
            bestsize += 1

        while besti > alo and bestj > blo and a[besti - 1] == b[bestj - 1]:
            besti, bestj, bestsize = besti - 1, bestj - 1, bestsize + 1

        while besti + bestsize < ahi and bestj + bestsize < bhi and a[besti + bestsize] == b[bestj + bestsize]:
            bestsize += 1

        return besti, bestj, bestsize
