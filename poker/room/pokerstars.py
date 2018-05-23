# -*- coding: utf-8 -*-
from __future__ import unicode_literals, absolute_import, division, print_function

import logging
import re
from decimal import Decimal
from datetime import datetime
import attr
from lxml import etree
import pytz
from pathlib import Path
from zope.interface import implementer
from .. import handhistory as hh
from ..card import Card, Rank
from ..hand import Combo
from ..constants import Limit, Game, GameType, Currency, Action, MoneyType, Position
from ..combination import CombinationGroup, Combination

__all__ = ['PokerStarsHandHistory', 'PokerStarsTournamentHandHistory', 'Notes']


@implementer(hh.IStreet)
class _Street(hh._BaseStreet):
    def _parse_cards(self, boardline):
        self.cards = (Card(boardline[1:3]), Card(boardline[4:6]), Card(boardline[7:9]))

    def _parse_actions(self, actionlines):
        actions = []
        for line in actionlines:
            if line.startswith('Uncalled bet'):
                action = self._parse_uncalled(line)
            elif 'collected' in line:
                action = self._parse_collected(line)
            elif "doesn't show hand" in line:
                action = self._parse_muck(line)
            elif ' said, "' in line:  # skip chat lines
                continue
            elif ':' in line:
                action = self._parse_player_action(line)
            else:
                raise RuntimeError("bad action line: " + line)

            actions.append(hh._PlayerAction(*action))
        self.actions = tuple(actions) if actions else None

    @staticmethod
    def _parse_preflop_actions(actionlines):
        actions = []
        for line in actionlines:
            if line.startswith('Uncalled bet'):
                action = _Street._parse_uncalled(line)
            elif ':' in line:
                action = _Street._parse_player_action(line)
            else:
                raise RuntimeError("bad action line: " + line)

            actions.append(hh._PlayerAction(*action))
        result = tuple(actions) if actions else None
        return result

    @staticmethod
    def _parse_uncalled(line):
        first_paren_index = line.find('(')
        second_paren_index = line.find(')')
        amount = line[first_paren_index + 1:second_paren_index]
        amount = re.sub("[^\d\.]*", "", amount)
        name_start_index = line.find('to ') + 3
        name = line[name_start_index:]
        return name, Action.RETURN, float(amount)

    def _parse_collected(self, line):
        first_space_index = line.find(' ')
        name = line[:first_space_index]
        second_space_index = line.find(' ', first_space_index + 1)
        third_space_index = line.find(' ', second_space_index + 1)
        amount = line[second_space_index + 1:third_space_index]
        amount = re.sub("[^\d\.]*", "", amount)
        self.pot = float(amount)
        return name, Action.WIN, self.pot

    def _parse_muck(self, line):
        colon_index = line.find(':')
        name = line[:colon_index]
        return name, Action.MUCK, None

    @staticmethod
    def _parse_player_action(line):
        name, _, action = line.partition(': ')
        action, _, amount = action.partition(' ')
        amount, _, _ = amount.partition(' ')
        #amount can be with $, deleting it
        amount = re.sub("[^\d\.]*", "", amount)

        if amount:
            return name, Action(action), float(amount)
        else:
            return name, Action(action), None


@implementer(hh.IHandHistory)
class PokerStarsTournamentHandHistory(hh._SplittableHandHistoryMixin, hh._BaseHandHistory):
    """Parses PokerStars Tournament hands."""

    _DATE_FORMAT = '%Y/%m/%d %H:%M:%S ET'
    _TZ = pytz.timezone('US/Eastern')  # ET
    _split_re = re.compile(r" ?\*\*\* ?\n?|\n")
    _header_re = re.compile(r"""
                        ^PokerStars\s+                                # Poker Room
                        Hand\s+\#(?P<ident>\d+):\s+                   # Hand history id
                        (Tournament\s+\#(?P<tournament_ident>\d+),\s+ # Tournament Number
                         ((?P<freeroll>Freeroll)|(                    # buyin is Freeroll
                          \$?(?P<buyin>\d+(\.\d+)?)                   # or buyin
                          (\+\$?(?P<rake>\d+(\.\d+)?))?               # and rake
                          (\s+(?P<currency>[A-Z]+))?                  # and currency
                         ))\s+
                        )?
                        (?P<game>.+?)\s+                              # game
                        (?P<limit>(?:Pot\s+|No\s+|)Limit)\s+          # limit
                        (-\s+Level\s+(?P<tournament_level>\S+)\s+)?   # Level (optional)
                        \(
                         (((?P<sb>\d+)/(?P<bb>\d+))|(                 # tournament blinds
                          \$(?P<cash_sb>\d+(\.\d+)?)/                 # cash small blind
                          \$(?P<cash_bb>\d+(\.\d+)?)                  # cash big blind
                          (\s+(?P<cash_currency>\S+))?                # cash currency
                         ))
                        \)\s+
                        -\s+.+?\s+                                    # localized date
                        \[(?P<date>.+?)\]                             # ET date
                        """, re.VERBOSE)
    _table_re = re.compile(r"^Table '(.*)' (\d+)-max Seat #(?P<button>\d+) is the button")
    _seat_re = re.compile(r"^Seat (?P<seat>\d+): (?P<name>.+?) \(\$?(?P<stack>\d+(\.\d+)?) in chips\)")  # noqa
    _hero_re = re.compile(r"^Dealt to (?P<hero_name>.+?) \[(..) (..)\]")
    _pot_re = re.compile(r"^Total pot (\d+(?:\.\d+)?) .*\| Rake (\d+(?:\.\d+)?)")
    _winner_re = re.compile(r"^Seat (\d+): (.+?) collected \((\d+(?:\.\d+)?)\)")
    _showdown_re = re.compile(r"^Seat (\d+): (.+?) showed \[.+?\] and won")
    _ante_re = re.compile(r".*posts the ante (\d+(?:\.\d+)?)")
    _board_re = re.compile(r"(?<=[\[ ])(..)(?=[\] ])")

    def parse_header(self):
        # sections[0] is before HOLE CARDS
        # sections[-1] is before SUMMARY
        self._split_raw()

        match = self._header_re.match(self._splitted[0])

        self.extra = dict()
        self.ident = match.group('ident')

        # We cannot use the knowledege of the game type to pick between the blind
        # and cash blind captures because a cash game play money blind looks exactly
        # like a tournament blind

        self.sb = float(match.group('sb') or match.group('cash_sb'))
        self.bb = float(match.group('bb') or match.group('cash_bb'))

        if match.group('tournament_ident'):
            self.game_type = GameType.TOUR
            self.tournament_ident = match.group('tournament_ident')
            self.tournament_level = match.group('tournament_level')

            currency = match.group('currency')
            self.buyin = float(match.group('buyin') or 0)
            self.rake = float(match.group('rake') or 0)
        else:
            self.game_type = GameType.CASH
            self.tournament_ident = None
            self.tournament_level = None
            currency = match.group('cash_currency')
            self.buyin = None
            self.rake = None

        if match.group('freeroll') and not currency:
            currency = 'USD'

        if not currency:
            self.extra['money_type'] = MoneyType.PLAY
            self.currency = None
        else:
            self.extra['money_type'] = MoneyType.REAL
            self.currency = Currency(currency)

        self.game = Game(match.group('game'))
        self.limit = Limit(match.group('limit'))

        self._parse_date(match.group('date'))

        self.header_parsed = True

    def parse(self):
        """Parses the body of the hand history, but first parse header if not yet parsed."""
        if not self.header_parsed:
            self.parse_header()

        self._parse_table()
        self._parse_players()
        self._parse_button()
        self._parse_hero()
        self._parse_preflop()
        self._parse_flop()
        self._parse_street('turn')
        self._parse_street('river')
        self._parse_showdown()
        self._parse_pot()
        self._parse_board()
        self._parse_winners()

        self._del_split_vars()
        self.parsed = True

    def _parse_table(self):
        self._table_match = self._table_re.match(self._splitted[1])
        self.table_name = self._table_match.group(1)
        self.max_players = int(self._table_match.group(2))

    def _parse_players(self):
        self.players = self._init_seats(self.max_players)
        for line in self._splitted[2:]:
            match = self._seat_re.match(line)
            # we reached the end of the players section
            if not match:
                break
            index = int(match.group('seat')) - 1
            self.players[index] = hh._Player(
                name=match.group('name'),
                stack=int(match.group('stack')),
                seat=int(match.group('seat')),
                combo=None,
                position=None
            )

    def _parse_button(self):
        button_seat = int(self._table_match.group('button'))
        self.button = self.players[button_seat - 1]

    def _parse_hero(self):
        hole_cards_line = self._splitted[self._sections[0] + 2]
        match = self._hero_re.match(hole_cards_line)
        hero, hero_index = self._get_hero_from_players(match.group('hero_name'))
        hero.combo = Combo(match.group(2) + match.group(3))
        self.hero = self.players[hero_index] = hero
        if self.button.name == self.hero.name:
            self.button = hero

    def _parse_preflop(self):
        start = self._sections[0] + 3
        stop = self._sections[1]
        self.preflop_actions = tuple(self._splitted[start:stop])

    def _parse_flop(self):
        try:
            start = self._splitted.index('FLOP') + 1
        except ValueError:
            self.flop = None
            return
        stop = self._splitted.index('', start)
        floplines = self._splitted[start:stop]
        self.flop = _Street(floplines)

    def _parse_street(self, street):
        try:
            start = self._splitted.index(street.upper()) + 2
            stop = self._splitted.index('', start)
            street_actions = self._splitted[start:stop]
            setattr(self, "{}_actions".format(street.lower()),
                    tuple(street_actions) if street_actions else None)
        except ValueError:
            setattr(self, street, None)
            setattr(self, '{}_actions'.format(street.lower()), None)

    def _parse_showdown(self):
        self.show_down = 'SHOW DOWN' in self._splitted

    def _parse_pot(self):
        potline = self._splitted[self._sections[-1] + 2]
        match = self._pot_re.match(potline)
        self.total_pot = int(match.group(1))

    def _parse_board(self):
        boardline = self._splitted[self._sections[-1] + 3]
        if not boardline.startswith('Board'):
            return
        cards = self._board_re.findall(boardline)
        self.turn = Card(cards[3]) if len(cards) > 3 else None
        self.river = Card(cards[4]) if len(cards) > 4 else None

    def _parse_winners(self):
        winners = set()
        start = self._sections[-1] + 4
        for line in self._splitted[start:]:
            if not self.show_down and "collected" in line:
                match = self._winner_re.match(line)
                winners.add(match.group(2))
            elif self.show_down and "won" in line:
                match = self._showdown_re.match(line)
                winners.add(match.group(2))

        self.winners = tuple(winners)


#TODO:  """gapiropo has timed out while disconnected"""

@implementer(hh.IHandHistory)
class PokerStarsHandHistory(hh._SplittableHandHistoryMixin, hh._BaseHandHistory):
    """Parses PokerStars Zoom hands."""
    _logger = logging.getLogger('application.poker.room.PokerStarsHandHistory')
    _logger.setLevel(logging.DEBUG)
    _DATE_FORMAT = '%Y/%m/%d %H:%M:%S ET'
    _TZ = pytz.timezone('US/Eastern')  # ET
    _split_re = re.compile(r" ?\*\*\* ?\n?|\n")
    _header_re = re.compile(r"""
                        ^\s*PokerStars\s+
                        Hand\s+\#(?P<ident>\d*):\s+
                        (?P<game>[^\s]*)\s+
                        (?P<limit>No\ Limit|Pot\ Limit|Limit)\s+
                        \(
                         \$(?P<cash_sb>\d+(\.\d+)?)/                 # cash small blind
                         \$(?P<cash_bb>\d+(\.\d+)?)                  # cash big blind
                         (\s+(?P<cash_currency>\S+))                 # cash currency
                        \)\s+
                        -\s+
                        (?P<date>[0-9\ /:]*\s+ET)                     # ET date
                        """, re.VERBOSE)

    #_header_re = re.compile(r"""^\s+PokerStars\s+Hand\s+\#(?P<ident>\d*):\s+(?P<game>[^\s]*)\s+(?P<limit>No Limit|Pot Limit|Limit)\s+\(\$(?P<cash_sb>\d+(\.\d+)?)/\$(?P<cash_bb>\d+(\.\d+)?)(\s+(?P<cash_currency>\S+))\)\s+-\s+(?P<date>[0-9 /:]*\s+ET)""", re.VERBOSE)

    _table_re = re.compile(r"^Table\s+'(.*)'\s+(\d+)-max\s+Seat\s*#(?P<button>\d+) is the button")
    _seat_re = re.compile(r"^Seat (?P<seat>\d+): (?P<name>.+?) \(\$?(?P<stack>\d+(\.\d+)?) in chips\)")  # noqa
    _hero_re = re.compile(r"^Dealt to (?P<hero_name>.+?) \[(..) (..)\]")
    _pot_re = re.compile(r"^Total\s+pot\s+[$|€|£](?P<total_pot>\d+(?:\.\d+)?)\s+\|\s+Rake\s+[$|€|£](?P<rake>\d+(?:\.\d+)?)")
    _winner_re = re.compile(r"^Seat (?P<seat>\d+): (?P<name>.+?) (?P<position>\(?.*?\)?)collected (?:\([$|€|£](?P<gain>[\d\.]*)\))")
    _showdown_re = re.compile(r"^Seat (?P<seat>\d+): (?P<name>.+?) (?P<position>\(?.*?\)?)\s?showed \[(?P<cards>.+?)\] and (?P<status>.+?) (?:\([$|€|£](?P<gain>[\d\.]*)\) )?with (?P<combination>.*?)(?:, and (?P<status_second>.+?) (?:\([$|€|£](?P<gain_second>[\d\.]*)\) )?with (?P<combination_second>.*))?$")
    _ante_re = re.compile(r".*posts the ante (\d+(?:\.\d+)?)")
    _board_re = re.compile(r"(?<=[\[ ])(..)(?=[\] ])")
    _summary_fold_re = re.compile(r"^Seat (?P<seat>\d+): (?P<name>.+?) (?P<position>\(?.*?\)?)\s?folded (?P<stage>on the (?P<street>.+)|before Flop)")
    _summary_mucked_re = re.compile(r"^Seat (?P<seat>\d+): (?P<name>.+?) (?P<position>\(?.*?\)?)\s?mucked")
    def parse_header(self):
        # sections[0] is before HOLE CARDS
        # sections[-1] is before SUMMARY
        self._split_raw()

        for i in self._splitted:
            self._logger.debug([i])

        match = self._header_re.match(self._splitted[0])

        self.extra = dict()
        self.ident = match.group('ident')

        # We cannot use the knowledege of the game type to pick between the blind
        # and cash blind captures because a cash game play money blind looks exactly
        # like a tournament blind

        self.sb = float(match.group('cash_sb') or float(match.group('sb')))
        self.bb = float(match.group('cash_bb') or float(match.group('bb')))

        self.game_type = GameType.CASH
        self.tournament_ident = None
        self.tournament_level = None
        currency = match.group('cash_currency')
        self.buyin = None
        self.rake = None

        if not currency:
            self.extra['money_type'] = MoneyType.PLAY
            self.currency = None
        else:
            self.extra['money_type'] = MoneyType.REAL
            self.currency = Currency(currency)

        self.game = Game(unicode(match.group('game')))
        self.limit = Limit(unicode(match.group('limit')))

        self._parse_date(match.group('date'))
        self._check_twice_hand()
        self._check_splitted_pot()
        self.header_parsed = True

    def parse(self):
        """Parses the body of the hand history, but first parse header if not yet parsed."""
        if not self.header_parsed:
            self.parse_header()

        self._parse_table()
        self._parse_players()
        self._parse_button()

        #self._parse_hero()
        self._parse_preflop()
        self._parse_flop()
        self._parse_street('turn')
        self._parse_street('river')
        self._parse_showdown()
        self._parse_pot()
        self._parse_board()
        self._parse_winners()
        self._init_advanced_seat()
        self._parse_summary()
        self._parse_position()
        self._del_split_vars()
        self.parsed = True

    def _parse_table(self):
        self._table_match = self._table_re.match(self._splitted[1])
        self.table_name = self._table_match.group(1)
        self.max_players = int(self._table_match.group(2))

    def _parse_players(self):
        self.players = self._init_seats(self.max_players)
        for line in self._splitted[2:]:
            match = self._seat_re.match(line)
            # we reached the end of the players section
            if not match:
                break
            index = int(match.group('seat')) - 1
            self.players[index] = hh._Player(
                name=match.group('name'),
                stack=float(match.group('stack')),
                seat=int(match.group('seat')),
                combo=None,
                position=None
            )

    def _parse_button(self):
        self.button_seat = int(self._table_match.group('button'))
        self.button = self.players[self.button_seat - 1]

    def _parse_hero(self):
        hole_cards_line = self._splitted[self._sections[0] + 2]
        match = self._hero_re.match(hole_cards_line)
        hero, hero_index = self._get_hero_from_players(match.group('hero_name'))
        hero.combo = Combo(match.group(2) + match.group(3))
        self.hero = self.players[hero_index] = hero
        if self.button.name == self.hero.name:
            self.button = hero

    def _parse_preflop(self):
        start = self._sections[0] + 3
        stop = self._sections[1]
        self.preflop_actions = _Street._parse_preflop_actions(self._splitted[start:stop])

    def _parse_flop(self):
        try:
            start = self._splitted.index('FLOP') + 1
        except ValueError:
            self.flop = None
            return
        stop = self._splitted.index('', start)
        floplines = self._splitted[start:stop]
        self.flop = _Street(floplines)

    def _parse_street(self, street):
        try:
            start = self._splitted.index(street.upper()) + 1
            stop = self._splitted.index('', start)
            street_actions = self._splitted[start:stop]
            street_obj = _Street(street_actions)
            setattr(self, "{}_actions".format(street.lower()),
                    street_obj.actions if street_actions else None)
        except ValueError:
            setattr(self, street, None)

    def _parse_showdown(self):
        self.show_down = 'SHOW DOWN' in self._splitted

    def _parse_pot(self):
        potline = self._splitted[self._sections[-1] + 2]
        match = self._pot_re.match(potline)
        self.total_pot = float(match.group("total_pot"))
        self.rake = float(match.group("rake"))
    def _parse_board(self):
        boardline = self._splitted[self._sections[-1] + 3]
        if not boardline.startswith('Board'):
            return
        cards = self._board_re.findall(boardline)
        self.turn = Card(unicode(cards[3])) if len(cards) > 3 else None
        self.river = Card(unicode(cards[4])) if len(cards) > 4 else None

    def _parse_winners(self):
        winners = set()
        start = self._sections[-1] + 4
        for line in self._splitted[start:]:
            if not self.show_down and "collected" in line:
                match = self._winner_re.match(line)
                winners.add(match.group("name"))
            elif self.show_down and "showed" in line:
                match = self._showdown_re.match(line)
                name = match.group("name")
                status = match.group("status")
                seat = int(match.group("seat"))
                self.players[seat - 1].combo = match.group("combination")
                if status == "win":
                    winners.add(name)
        self.winners = tuple(winners)

    def _init_advanced_seat(self):
        self.players_advanced = []
        for i in range(self.max_players):
            self.players_advanced.append({"name": None})


    def _parse_summary(self):
        start = self._splitted.index('SUMMARY') + 1
        for line in self._splitted[start:]:
            name, seat = 0, 0
            stage, combination, action = "", "", ""
            hand = []
            hand_group, hand_combination = None, None
            is_winner = False
            if "showed" in line:
                action = "showed"
                match = self._showdown_re.match(line)
                name = match.group("name")
                status = match.group("status")
                seat = int(match.group("seat"))
                card_line = match.group("cards")
                cards = card_line.split(" ")
                for card_str in cards:
                    hand.append(card_str)
                stage = "showdown"
                combination_line = match.group("combination")
                combination = self._parse_poker_stars_combination(combination_line)
                hand_group = combination.group.val
                hand_combination = combination.to_string()
                if status == "won":
                    is_winner = True
            elif "folded" in line:
                action = "folded"
                match = self._summary_fold_re.match(line)
                name = match.group("name")
                seat = int(match.group("seat"))
                if match.group("stage") == "before Flop":
                    stage = "preflop"
                else:
                    stage = match.group("street").lower()
            elif "mucked" in line:
                action = "mucked"
                match = self._summary_fold_re.match(line)
                name = match.group("name")
                seat = int(match.group("seat"))
                stage = "showdown"
            else:
                continue
            self.players_advanced[seat - 1] = {
                "name": self.players[seat-1].name,
                "stage": stage,
                "is_winner": is_winner,
                "hand": hand,
                "action": action,
                "hand_combination": hand_combination
            }

    def _parse_poker_stars_combination(self, combination_line):
        group, rank, sec_rank = None, None, None
        if combination_line.startswith("a pair of"):
            group = CombinationGroup.PAIR
            rank = Rank(combination_line.split(" of ")[1])
        elif combination_line.startswith("high card"):
            group = CombinationGroup.HIGH_CARD
            rank = Rank(combination_line.split(" card ")[1])
        else:
            group_name, rank_line = combination_line.split(", ")
            # Remove article
            if group_name.startswith("a "):
                group_name = group_name[2:]
            group = CombinationGroup(unicode(group_name))
            if group in (CombinationGroup.STRAIGHT, CombinationGroup.STRAIGHT_FLUSH):
                rank_name = rank_line.split(" to ")[1]
                rank = Rank(rank_name)
            elif group == CombinationGroup.FULL_HOUSE:
                rank_name, sec_rank_name = rank_line.split(" full of ")
                rank = Rank(rank_name)
                sec_rank = Rank(sec_rank_name)
            elif group == CombinationGroup.TWO_PAIR:
                rank_name, sec_rank_name = rank_line.split(" and ")
                rank = Rank(rank_name)
                sec_rank = Rank(sec_rank_name)
            else:
                rank_name = rank_line.split(" ")[0]
                rank = Rank(rank_name)
        result = Combination(group, rank, sec_rank)
        return result

    def _parse_position(self):
        button_index = self.button_seat - 1

        shift_seat = lambda a: (button_index + a) % self.max_players

        if self.max_players == 2:
            self.players_advanced[shift_seat(0)]["position"] = Position.BTN.val
            self.players_advanced[shift_seat(1)]["position"] = Position.BB.val
            self.players[shift_seat(0)].position = Position.BTN
            self.players[shift_seat(1)].position = Position.BB
            return
        # players 2,3 = 0, 4,5,6 = 1 , 7,8,9 = 2
        last_position = int((self.max_players - 1) / 3)
        for i in range(self.max_players - last_position):
            self.players_advanced[shift_seat(i)]["position"] = Position(i).val
            self.players[shift_seat(i)].position = Position(i)


        if last_position == 2:
            self.players_advanced[shift_seat(self.max_players - 2)]["position"] = Position.HJ.val
            self.players[shift_seat(self.max_players - 2)].position = Position.HJ

            last_position -= 1

        if last_position == 1:
            self.players_advanced[shift_seat(self.max_players - 1)]["position"] = Position.CO.val
            self.players[shift_seat(self.max_players - 1)].position = Position.CO

    def _check_twice_hand(self):
        start = self._splitted.index('SUMMARY') + 1
        self.hand_run_twice = False
        if "Hand was run twice" in self._splitted[start:]:
            self.hand_run_twice = True


    def _check_splitted_pot(self):
        start = self._splitted.index('SUMMARY') + 1
        self.splitted_pot = False
        if "Main pot" in self._splitted[start:]:
            self.splitted_pot = True

@attr.s(slots=True)
class _Label(object):
    """Labels in Player notes."""
    id = attr.ib()
    color = attr.ib()
    name = attr.ib()


@attr.s(slots=True)
class _Note(object):
    """Player note."""
    player = attr.ib()
    label = attr.ib()
    update = attr.ib()
    text = attr.ib()


class NoteNotFoundError(ValueError):
    """Note not found for player."""


class LabelNotFoundError(ValueError):
    """Label not found in the player notes."""


class Notes(object):
    """Class for parsing pokerstars XML notes."""

    _color_re = re.compile('^[0-9A-F]{6}$')

    def __init__(self, notes):
        # notes need to be a unicode object
        self.raw = notes
        parser = etree.XMLParser(recover=True, resolve_entities=False)
        self.root = etree.XML(notes.encode('utf-8'), parser)

    def __unicode__(self):
        return str(self).decode('utf-8')

    def __str__(self):
        return etree.tostring(self.root, xml_declaration=True, encoding='UTF-8', pretty_print=True)

    @classmethod
    def from_file(cls, filename):
        """Make an instance from a XML file."""
        return cls(Path(filename).open().read())

    @property
    def players(self):
        """Tuple of player names."""
        return tuple(note.get('player') for note in self.root.iter('note'))

    @property
    def label_names(self):
        """Tuple of label names."""
        return tuple(label.text for label in self.root.iter('label'))

    @property
    def notes(self):
        """Tuple of notes.."""
        return tuple(self._get_note_data(note) for note in self.root.iter('note'))

    @property
    def labels(self):
        """Tuple of labels."""
        return tuple(_Label(label.get('id'), label.get('color'), label.text) for label
                     in self.root.iter('label'))

    def get_note_text(self, player):
        """Return note text for the player."""
        note = self._find_note(player)
        return note.text

    def get_note(self, player):
        """Return :class:`_Note` tuple for the player."""
        return self._get_note_data(self._find_note(player))

    def add_note(self, player, text, label=None, update=None):
        """Add a note to the xml. If update param is None, it will be the current time."""
        if label is not None and (label not in self.label_names):
            raise LabelNotFoundError('Invalid label: {}'.format(label))
        if update is None:
            update = datetime.utcnow()
        # converted to timestamp, rounded to ones
        update = update.strftime('%s')
        label_id = self._get_label_id(label)
        new_note = etree.Element('note', player=player, label=label_id, update=update)
        new_note.text = text
        self.root.append(new_note)

    def append_note(self, player, text):
        """Append text to an already existing note."""
        note = self._find_note(player)
        note.text += text

    def prepend_note(self, player, text):
        """Prepend text to an already existing note."""
        note = self._find_note(player)
        note.text = text + note.text

    def replace_note(self, player, text):
        """Replace note text with text. (Overwrites previous note!)"""
        note = self._find_note(player)
        note.text = text

    def change_note_label(self, player, label):
        label_id = self._get_label_id(label)
        note = self._find_note(player)
        note.attrib['label'] = label_id

    def del_note(self, player):
        """Delete a note by player name."""
        self.root.remove(self._find_note(player))

    def _find_note(self, player):
        # if player name contains a double quote, the search phrase would be invalid.
        # &quot; entitiy is searched with ", e.g. &quot;bootei&quot; is searched with '"bootei"'
        quote = "'" if '"' in player else '"'
        note = self.root.find('note[@player={0}{1}{0}]'.format(quote, player))
        if note is None:
            raise NoteNotFoundError(player)
        return note

    def _get_note_data(self, note):
        labels = {label.get('id'): label.text for label in self.root.iter('label')}
        label = note.get('label')
        label = labels[label] if label != "-1" else None
        timestamp = note.get('update')
        if timestamp:
            timestamp = int(timestamp)
            update = datetime.utcfromtimestamp(timestamp).replace(tzinfo=pytz.UTC)
        else:
            update = None
        return _Note(note.get('player'), label, update, note.text)

    def get_label(self, name):
        """Find the label by name."""
        label_tag = self._find_label(name)
        return _Label(label_tag.get('id'), label_tag.get('color'), label_tag.text)

    def add_label(self, name, color):
        """Add a new label. It's id will automatically be calculated."""
        color_upper = color.upper()
        if not self._color_re.match(color_upper):
            raise ValueError('Invalid color: {}'.format(color))

        labels_tag = self.root[0]
        last_id = int(labels_tag[-1].get('id'))
        new_id = str(last_id + 1)

        new_label = etree.Element('label', id=new_id, color=color_upper)
        new_label.text = name

        labels_tag.append(new_label)

    def del_label(self, name):
        """Delete a label by name."""
        labels_tag = self.root[0]
        labels_tag.remove(self._find_label(name))

    def _find_label(self, name):
        labels_tag = self.root[0]
        try:
            return labels_tag.xpath('label[text()="%s"]' % name)[0]
        except IndexError:
            raise LabelNotFoundError(name)

    def _get_label_id(self, name):
        return self._find_label(name).get('id') if name else '-1'

    def save(self, filename):
        """Save the note XML to a file."""
        with open(filename, 'w') as fp:
            fp.write(str(self))
