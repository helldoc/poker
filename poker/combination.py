# -*- coding: utf-8 -*-

from ._common import PokerEnum, _ReprMixin
from .card import Rank

class CombinationGroup(PokerEnum):
    __order__ = 'HIGH_CARD PAIR TWO_PAIR THREE_OF_A_KIND STRAIGHT FLUSH FULL_HOUSE FOUR_OF_A_KIND STRAIGHT_FLUSH'

    HIGH_CARD = "high card",
    PAIR = "pair",
    TWO_PAIR = "two pair",
    THREE_OF_A_KIND = "three of a kind",
    STRAIGHT = "straight",
    FLUSH = "flush",
    FULL_HOUSE = "full house",
    FOUR_OF_A_KIND = "four of a kind",
    STRAIGHT_FLUSH = "straight flush",


class Combination(_ReprMixin):
    __slots__ = "group", "rank", "second_rank"
    _two_rank_combination = (CombinationGroup.TWO_PAIR, CombinationGroup.FULL_HOUSE)
    _high_card_combination = (CombinationGroup.FLUSH, CombinationGroup.STRAIGHT, CombinationGroup.STRAIGHT_FLUSH)
    _multi_combination = (CombinationGroup.TWO_PAIR, CombinationGroup.FULL_HOUSE, CombinationGroup.THREE_OF_A_KIND, CombinationGroup.FOUR_OF_A_KIND)

    #TODO validity check
    def __new__(cls, group, rank, second_rank):

        self = super(Combination, cls).__new__(cls)
        self.group = CombinationGroup(group)
        self.rank = Rank(rank)

        if second_rank and self.group in cls._two_rank_combination:
            self.second_rank = Rank(second_rank)
        elif second_rank:
            raise ValueError("Combination {!r} is one rank combination, but second_rank set {!r}".format(self.group.val, str(second_rank)))
        elif self.group in cls._two_rank_combination:
            raise ValueError("Combination %s is two rank combination, but second_rank set {!r}".format(self.group.val, str(second_rank)))
        else:
            self.second_rank = None
        if self.group == CombinationGroup.TWO_PAIR and self.rank < self.second_rank:
            self.second_rank, self.rank = self.rank, self.second_rank
        return self

    def __hash__(self):
        return hash(self.group) + hash(self.rank) + + hash(self.second_rank)

    def __getstate__(self):
        return {'group': self.group, 'rank': self.rank, 'second_rank': self.second_rank}

    def __setstate__(self, state):
        self.group, self.rank, self.second_rank = state['group'], state['rank'], state['second_rank']

    def __eq__(self, other):
        if self.__class__ is other.__class__:
            return self.group == other.group and self.rank == other.rank and self.second_rank == other.second_rank
        return NotImplemented

    def __lt__(self, other):
        if self.__class__ is not other.__class__:
            return NotImplemented

        # with same ranks, suit counts
        if self.group == other.group:
            if self.rank == other.rank:
                return self.second_rank < other.second_rank
            return self.rank < other.rank
        return self.group < other.group

    def __unicode__(self):
        return '{}, {}, {}'.format(self.group, self.rank, self.second_rank)

    def to_string(self):
        result = {
            CombinationGroup.HIGH_CARD: "high card " + self.rank.val_at(1),
            CombinationGroup.PAIR: "pair of " + self.rank.val_at(2),
            CombinationGroup.TWO_PAIR: "two pair, " + self.rank.val_at(2) + " and " + self.second_rank.val_at(2) if self.second_rank else "",
            CombinationGroup.THREE_OF_A_KIND: "three of a kind, " + self.rank.val_at(2),
            CombinationGroup.STRAIGHT: "straight, " + self.rank.val_at(1) + " high",
            CombinationGroup.FLUSH: "flush, " + self.rank.val_at(1) + " high",
            CombinationGroup.FULL_HOUSE: "full house, " + self.rank.val_at(2) + " full of " + self.second_rank.val_at(2) if self.second_rank else "",
            CombinationGroup.FOUR_OF_A_KIND: "four of a kind, " + self.rank.val_at(2),
            CombinationGroup.STRAIGHT_FLUSH: "straight flush, " + self.rank.val_at(1) + " high"
        }[self.group]
        return result
