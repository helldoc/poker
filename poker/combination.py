# -*- coding: utf-8 -*-

import enum


class CombinationGroup(enum.Enum):
    __order__ = 'HIGH_CARD PAIR TWO_PAIR THREE_OF_A_KIND STRAIGHT FLUSH FULL_HOUSE FOUR_OF_A_KIND STRAIGHT_FLUSH'


HIGH_CARD = "high card"
PAIR = "pair"
TWO_PAIR = "two pair"
THREE_OF_A_KIND = "three of a kind"
STRAIGHT = "straight"
FLUSH = "flush"
FULL_HOUSE = "full house"
FOUR_OF_A_KIND = "four of a kind"
STRAIGHT_FLUSH = "straight flush", "royal flush"
