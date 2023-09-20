"""This module implements a faster SequenceMatcher."""

from textension.utils import defaultdict_list, consume, map_len
from itertools import compress, repeat, count, islice
from difflib import SequenceMatcher
from textension import utils


# Does what ``[[] for _ in range(N)]`` does, just faster.
infinite_lists = map(list.__new__, repeat(list))
infinite_dicts = map(dict.__new__, repeat(dict))


@utils.inline
def map_append(lists, obj):
    return utils.partial(map, list.append)


class FastSequenceMatcher(utils.Variadic, SequenceMatcher):
    isjunk     = None
    opcodes    = None
    autojunk   = True
    fullbcount = None

    matching_blocks = None

    a = utils._variadic_index(0)
    b = utils._variadic_index(1)

    def __init__(self, a, b):
        # Construct a dictionary of ``b`` with empty lists as values.
        self.b2j = b2j = dict(zip(b, infinite_lists))

        # Map the indices of each occurrence and add to the lists.
        consume(map_append(map(b2j.__getitem__, b), count()))

        if (n := (len(b) // 100 + 1)) >= 3:
            self.b2j = defaultdict_list(compress(b2j.items(), map(n.__gt__, map_len(b2j.values()))))
        else:
            self.b2j = defaultdict_list(b2j)

    def find_longest_match(self, alo=0, ahi=None, blo=0, bhi=None):
        a   = self.a
        b   = self.b
        b2j = self.b2j

        besti    = alo
        bestj    = blo
        bestsize = 0

        j2len = {}
        
        for i, c, newj2len in zip(count(alo), islice(a, alo, ahi), infinite_dicts):
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

        while besti > alo and bestj > blo and a[besti - 1] == b[bestj - 1]:
            besti -= 1
            bestj -= 1
            bestsize += 1

        while besti + bestsize < ahi and bestj + bestsize < bhi and a[besti + bestsize] == b[bestj + bestsize]:
            bestsize += 1

        while besti > alo and bestj > blo and a[besti - 1] == b[bestj - 1]:
            besti -= 1
            bestj -= 1
            bestsize += 1

        while besti + bestsize < ahi and bestj + bestsize < bhi and a[besti + bestsize] == b[bestj + bestsize]:
            bestsize += 1

        return besti, bestj, bestsize
