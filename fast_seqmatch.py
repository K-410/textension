"""This module implements a faster SequenceMatcher."""

from textension.utils import defaultdict_list, consume, map_len
from textension import utils
from itertools import compress, repeat, count, islice
from functools import partial
from operator import add
from difflib import SequenceMatcher


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

    def get_opcodes(self):
        i = 0
        j = 0
        opcodes = []

        for ai, bj, size in self.get_matching_blocks():
            if i < ai and j < bj:
                opcodes += ("replace", i, ai, j, bj),
            elif i < ai:
                opcodes += ("delete",  i, ai, j, bj),
            elif j < bj:
                opcodes += ("insert",  i, ai, j, bj),

            if not size:
                i = ai
                j = bj
            else:
                i = ai + size
                j = bj + size
                opcodes += ("equal", ai, i, bj, j),

        return opcodes

    def get_matching_blocks(self):
        a = self.a
        b = self.b
        la = len(a)
        lb = len(b)
        b2j = self.b2j

        pool = [(0, la, 0, lb)]
        matching_blocks = []
        islice_a = partial(islice, a)

        for alo, ahi, blo, bhi in iter(pool):

            bi = alo
            bj = blo
            bs = 0

            j2len = {}
            
            for i, c, newj2len in zip(count(alo), islice_a(alo, ahi), infinite_dicts):
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
                        if k > bs:
                            bi = i - k + 1
                            bj = j - k + 1
                            bs = k
                j2len = newj2len

            while bi > alo and bj > blo and a[bi - 1] == b[bj - 1]:
                bi -= 1
                bj -= 1
                bs += 1

            while bi + bs < ahi and bj + bs < bhi and a[bi + bs] == b[bj + bs]:
                bs += 1

            while bi > alo and bj > blo and a[bi - 1] == b[bj - 1]:
                bi -= 1
                bj -= 1
                bs += 1

            while bi + bs < ahi and bj + bs < bhi and a[bi + bs] == b[bj + bs]:
                bs += 1

            if bs:
                matching_blocks += (bi, bj, bs),

                if alo < bi and blo < bj:
                    pool += (alo, bi, blo, bj),

                if bi + bs < ahi and bj + bs < bhi:
                    pool += (bi + bs, ahi, bj + bs, bhi),

        i1 = 0
        j1 = 0
        k1 = 0
        non_adjacent = []

        for i2, j2, k2 in sorted(matching_blocks):
            if i1 + k1 == i2 and j1 + k1 == j2:
                k1 += k2

            else:
                if k1:
                    non_adjacent += (i1, j1, k1),

                i1 = i2
                j1 = j2
                k1 = k2
        if k1:
            non_adjacent += (i1, j1, k1),

        non_adjacent += (la, lb, 0),
        return non_adjacent


@utils.inline
def unified_diff(a, b) -> list[tuple[str, int, int, int, int]]:
    """Note: For performance, use this only if you know that `a` != `b`.
    Note 2: Only strings, or list of strings supported.
    """

    from textension.utils import map_ne, filtertrue
    from .fast_seqmatch import FastSequenceMatcher
    from itertools import repeat
    from operator import add, length_hint
    from builtins import len, min, map, reversed

    @utils.inline
    def get_end_indices(opcode):
        import operator
        return operator.itemgetter(2, 4)

    def unified_diff(a, b):
        la = len(a)
        lb = len(b)
        tail = 0
        head = lb
        opcodes = []

        # ``X in Iterable`` consumes the iterators until the first non-equal
        # element is found. ``length_hint`` then gives us the remaining size
        # of the iterator.
        iter_a = iter(a)
        if True in filtertrue(map_ne(iter_a, b)):
            head = la - length_hint(iter_a) - 1

        rev_a = reversed(a)
        if True in filtertrue(map_ne(rev_a, reversed(b))):
            tail = la - length_hint(rev_a) - 1

        old_end = la - tail
        new_end = lb - tail

        # It's possible for tails to overlap the head. If so, we need to clamp.
        # Consider "aaabc" vs "aaaabc".
        head = min(head, old_end, new_end)

        if head:
            opcodes += ("equal", 0, head, 0, head),

        # Feed only changed lines, then add the offsets to the opcode indices.
        offsets = repeat(head)

        # data[0]  opcode
        # data[1:] indices
        for data in FastSequenceMatcher(a[head:old_end], b[head:new_end]).get_opcodes():
            opcodes += (data[0], *map(add, offsets, data[1:])),

        if tail:
            j1, j2 = get_end_indices(opcodes[-1])
            opcodes += ("equal", j1, la, j2, lb),
        return opcodes

    return unified_diff
