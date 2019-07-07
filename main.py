import requests, zlib, hashlib, json
import time, threading
import logging
import random

from multiprocessing.pool import ThreadPool

from enum import Enum


class GameConstants:
    RUBLES = '5449016a4bdc2d6f028b456f'
    DOLLARS = '5696686a4bdc2da3298b456a'
    EURO = '569668774bdc2da2298b4568'

    Therapist = '54cb57776803fa99248b456e'

    gasan = '590a3efd86f77437d351a25b'
    labKey = '5c94bbff86f7747ee735c08f'
    salewa = '544fb45d4bdc2dee738b4568'


class FleaOffer:
    def __init__(self, offer: dict):
        self.offer = offer

        self.id = offer['_id']

        self.user = dict()
        self.user['id'] = offer['user']['id']
        self.user['nickname'] = offer['user']['nickname']

        self.item_tpl = offer['items'][0]['_tpl']
        self.count = offer['items'][0]['upd']['StackObjectsCount']

        self.requirements = list()
        self.requirements = offer['requirements']

        self.summary_cost = offer['summaryCost']

        self.start_time = offer['startTime']
        self.end_time = offer['endTime']

    def __str__(self):
        return f'{self.user["nickname"]} - {self.summary_cost} - x{self.count}'

    def _repr_(self):
        return str(self)


def remove_white_space(s):
    return s.replace(' ', '').replace('\n', '')


class GameRequest:
    def __init__(self, url: str, data: str, cookies={}):
        self.request = requests.post(url, data, cookies=cookies)
        self.cookies = self.request.cookies.get_dict()

    def _get_content(self):
        return zlib.decompress(self.request.content).decode()

    def __str__(self):
        return self._get_content()

    def __repr__(self):
        return str(self)

    def get_json(self) -> dict:
        return json.loads(self._get_content())


class GameConnection:
    logger = logging.getLogger("GameConnection")
    logger.setLevel("DEBUG")

    cookies = None

    def __init__(self, email="", password="", cookies=''):
        self.logger.debug("Connecting to game")
        if cookies != '':
            self.cookies = {'PHPSESSID': cookies}
        else:
            self.email = email
            self.password = password
            self.cookies = self._get_cookies()
        self.logger.debug(f"Using cookies: {self.cookies}")

    def _get_cookies(self):
        loginReq = self._login()
        return loginReq.cookies

    def _login(self):
        if self.email == "" or self.password == "":
            raise ValueError("Email or password are invalid")

        device_id = "ENTER_YOUR_DEVICE_ID_HERE"
        major_v = "ENTER_MAJOR_GAME_VERSION_HERE" # eg. "0.11.7.3087"
        # minor_v = "bgkidft87ddd"

        data = dict()
        data['version'] = {}
        data['version']['major'] = major_v
        data['version']['backend'] = 6

        data['device_id'] = device_id
        data['develop'] = True

        data['email'] = self.email
        data['pass'] = self.password

        req = self.prod_request('/client/game/login', json.dumps(data))

        self.logger.debug("Login request: " + str(req))
        # TODO: require Hardware code if login is done for the first time
        return req

    def _send_request(self, path, data):
        data = zlib.compress(remove_white_space(data).encode())

        cookies = {}
        if self.cookies is not None:
            cookies = self.cookies

        req = GameRequest(path, data=data, cookies=cookies)
        return req

    def prod_request(self, path: str, data: str) -> GameRequest:
        return self._send_request("http://prod.escapefromtarkov.com" + path, data)

    def trading_request(self, path: str, data: str) -> GameRequest:
        return self._send_request("http://trading.escapefromtarkov.com" + path, data)

    def ragfair_request(self, path: str, data: str) -> GameRequest:
        return self._send_request("http://ragfair.escapefromtarkov.com" + path, data)


class FleaBuyResult(Enum):
    OK = 0,
    BOUGHT = 1
    OUTOFSPACE = 2

    UNKNOWN = -1


class Game:
    logger = logging.getLogger("Game")
    logger.setLevel("DEBUG")

    profileLock = threading.Lock()

    def __init__(self, email="", password="", cookies=None):
        self.logger.debug("Initializing game")
        self.connection = GameConnection(email, password, cookies)

        self.keep_alive_thread = threading.Thread(target=self._keep_alive)
        self.keep_alive_thread.daemon = True
        self.keep_alive_thread.start()

        self.moneyStacks = {}
        self.PMC = None
        self.update_profile()

        self.connection.prod_request('/client/game/profile/select',
                                     json.dumps({'uid': self.PMC['_id']}))

        self.all_item_list = self.connection.prod_request('/client/items', '{}').get_json()['data']

    """
    Method running in separate thread. Sends alive req to server to keep cookies valid.
    """

    def _keep_alive(self):
        while True:
            self.connection.prod_request('/client/game/keepalive', '')
            time.sleep(5 * 60)

    """
    Reloads information about PMC, including money, inventory items etc.
    """
    def update_profile(self):
        with self.profileLock:
            list_req = self.connection.prod_request("/client/game/profile/list", "{}")
            profile_list = list_req.get_json()

            for item in profile_list['data']:
                if item['Info']['LastTimePlayedAsSavage'] == 0:
                    self.PMC = item

            self._inventory = dict()
            for item in self.PMC['Inventory']['items']:
                self._inventory[item['_id']] = item

            for currency in (GameConstants.RUBLES, GameConstants.DOLLARS, GameConstants.EURO):
                self.moneyStacks[currency] = {}

            for item_id, item in self._inventory.items():
                for currency in (GameConstants.RUBLES, GameConstants.DOLLARS, GameConstants.EURO):
                    if item['_tpl'] == currency:
                        count = item['upd']['StackObjectsCount']
                        self.moneyStacks[currency][item_id] = count

    """
    :return dictionary of pairs item_id -> item_desc
    """
    def get_inventory(self):
        with self.profileLock:
            return self._inventory

    """
    Returns money stack ids, which sum >= value 
    """
    def find_moneystack(self, money: int, currency=GameConstants.RUBLES) -> list:
        with self.profileLock:
            result = []
            for (id, value) in self.moneyStacks[currency].items():
                if value >= money:
                    result.append((id, money))
                    break
                else:
                    money -= value
                    result.append((id, value))
            return result

    """
    Get inventory item ids by item template
    """

    def inventory_items_ids(self, item_tpl: str) -> list:
        return [item['_id'] for item in self.PMC['Inventory']['items'] if item['_tpl'] == item_tpl]

    def get_traders_list(self):
        req = self.connection.trading_request('/client/trading/api/getTradersList', '')
        result = dict()
        for trader in req.get_json()['data']:
            result[trader['_id']] = trader
        return result

    def get_trader_assort(self, trader_id: str) -> list:
        req = self.connection.trading_request('/client/trading/api/getTraderAssort/' + trader_id, '')
        return req.get_json()['data']

    def flea_find(self, limit=15, priceFrom=0, priceTo=0,
                  removeBartering=True, removeMerchantOffers=True, item_tpl=''):
        data = {
            "page": 0,
            "limit": limit,
            "sortType": 5,
            "sortDirection": 0,
            "currency": 0,
            "priceFrom": priceFrom,
            "priceTo": priceTo,
            "quantityFrom": 0,
            "quantityTo": 0,
            "conditionFrom": 0,
            "conditionTo": 100,
            "oneHourExpiration": False,
            "onlyPrioritized": False,
            "removeBartering": removeBartering,
            "removeMerchantOffers": removeMerchantOffers,
            "onlyFunctional": True,
            "updateOfferCount": True,
            "handbookId": item_tpl,
            "linkedSearchId": "",
            "neededSearchId": ""
        }

        req = self.connection.ragfair_request('/client/ragfair/search', json.dumps(data))
        # for item in req.get_json()['data']['offers']:
        offers = req.get_json()['data']['offers']

        result = list()
        for offer in offers:
            result.append(FleaOffer(offer))

        result.sort(key=lambda x: x.summary_cost)

        return result

    def flea_buy(self, offer: FleaOffer) -> FleaBuyResult:
        self.logger.info(f'------------ Buying {offer.id} x {offer.count} for {offer.summary_cost} ------------')

        spent_time = time.time() - offer.start_time

        start_from = 56

        if spent_time < start_from:
            to_wait = start_from - spent_time
            self.logger.info(f"Need to wait {to_wait}")
            time.sleep(to_wait)

        while time.time() < offer.end_time:
            try:

                time.sleep(0.05)

                data = {
                    "data": [
                        {
                            "Action": "RagFairBuyOffer",
                            "offerId": offer.id,
                            "count": offer.count,
                            "items": []
                        }
                    ]
                }

                # TODO: support not only rubbles purchases
                stacks = self.find_moneystack(offer.summary_cost * offer.count, GameConstants.RUBLES)
                for stack in stacks:
                    stack_info = dict()
                    stack_info['id'] = stack[0]
                    stack_info['count'] = stack[1]
                    data['data'][0]['items'].append(stack_info)

                req = self.connection.prod_request('/client/game/profile/items/moving', json.dumps(data))
                result_data = req.get_json()
                
                # still is not available
                if result_data['err'] in (228, 1512):
                    continue

                if result_data['err'] == 1505:
                    return FleaBuyResult.OUTOFSPACE

                # this means that transaction is okay
                if result_data['err'] == 0:

                    self.update_profile()

                    # offer was sold out
                    if len(result_data['data']['badRequest']) > 0:
                        return FleaBuyResult.BOUGHT
                    # added new item to inventory
                    elif len(result_data['data']['items'].keys()) > 0:
                        return FleaBuyResult.OK

                print(result_data)

                return FleaBuyResult.UNKNOWN

            except Exception as e:
                self.logger.exception(str(e))

    def get_stash_size(self):
        stash_id = self.PMC['Inventory']['stash']
        stash_tpl = self.get_inventory()[stash_id]['tpl']

        stash_props = self.all_item_list[stash_tpl]['_props']['Grids'][0]['_props']
        return stash_props['cellsV'], stash_props['cellsH']


class FleaBuyThread(ThreadPool):
    def __init__(self, game: Game, offer: FleaOffer):
        super().__init__(processes=1)
        self.game = game
        self.offer = offer
        self.async_result = None

    def start(self):
        self.async_result = self.apply_async(self.game.flea_buy, [self.offer])

    def is_ready(self):
        return self.async_result.ready()

    def get_result(self, timeout=None):
        return self.async_result.get(timeout)


class TarkovBot:
    logger = logging.getLogger("TarkovBot")
    logger.setLevel("DEBUG")

    def __init__(self, email='', password='', cookies=''):
        FORMAT = '%(asctime)s: %(message)s'
        logging.basicConfig(format=FORMAT, )

        self.logger.debug("Initializing bot")
        self.game = Game(email, password, cookies)

    def filter_inventory(self, item_tpl):
        inv = self.game.get_inventory()
        return list(filter(lambda x: x[1]['_tpl'] == item_tpl, inv.items()))

    def flea_market_buy(self, item_tpl: str, upper_price: int, offer_count=5, until_amount=None, delay_from=5, delay_to=10):
        if until_amount is None:
            until_amount = 1000

        offer_container = list()
        offer_id_set = set()

        while len(self.filter_inventory(item_tpl)) < until_amount:
            try:
                container_copy = list(offer_container)
                for offer_thread in offer_container:
                    if offer_thread.is_ready():
                        offer_id_set.remove(offer_thread.offer.id)
                        container_copy.remove(offer_thread)

                        result = offer_thread.get_result()
                        offer = offer_thread.offer

                        assert isinstance(offer, FleaOffer)

                        if result == FleaBuyResult.OK:
                            self.logger.info(
                                f'------------ Successfully bought offer {offer.id}'
                                f' for {offer.summary_cost} -----------'
                            )
                        else:
                            self.logger.info(
                                f'------------ Failed to buy offer {offer.id} - {result} -----------'
                            )
                offer_container = container_copy

                if len(offer_container) < offer_count:
                    new_offers = self.game.flea_find(limit=15, priceTo=upper_price, item_tpl=item_tpl)

                    new_offers = [item for item in new_offers if item.id not in offer_id_set]

                    if len(new_offers) != 0:
                        can_add_count = offer_count - len(offer_container)
                        self.logger.info(f'Found {len(new_offers)} offers. Can add {can_add_count}')

                        for i in range(min(can_add_count, len(new_offers))):
                            buy_thread = FleaBuyThread(self.game, new_offers[i])
                            buy_thread.start()
                            offer_container.append(buy_thread)
                            offer_id_set.add(new_offers[i].id)
            except KeyboardInterrupt as keyBoard:
                for offer_thread in offer_container:
                    self.logger.debug(f"Terminating thread for {offer_thread.offer.id}")
                    offer_thread.terminate()
                break
            except Exception as e:
                self.logger.exception(str(e))
            finally:
                try:
                    time.sleep(random.randint(delay_from, delay_to))
                except KeyboardInterrupt as keyBoard:
                    for offer_thread in offer_container:
                        self.logger.debug(f"Terminating thread for {offer_thread.offer.id}")
                        offer_thread.terminate()
                    break
    """
    Tries to free some space by merging and transfering ruble stacks
    """
    def merge_all_rubles(self):
        all_rubles = sorted(list(bot.game.moneyStacks[GameConstants.RUBLES].items()), key=lambda x: x[1])
        all_rubles = [item for item in all_rubles if item[1] != 500000] 

        merge_data = [] 

        for i in range(len(all_rubles)):
            itemI = list(all_rubles[i]) 

            if itemI[1] == 500000:
                continue    

            for j in range(i + 1, len(all_rubles)):
                itemJ = list(all_rubles[j])
                # merge i to j
                if itemI[1] == 0 or itemJ[1] == 500000:
                    continue    

                can_merge = 500000 - itemJ[1]   

                if itemI[1] > can_merge:
                    itemI[1] -= can_merge
                    itemJ[1] = 500000
                    merge_data.append([itemI[0], itemJ[0], can_merge])
                else:
                    itemJ[1] += itemI[1]
                    itemI[1] = 0
                    merge_data.append([itemI[0], itemJ[0]]) 

                all_rubles[i] = itemI
                all_rubles[j] = itemJ   

                if itemI[1] == 0:
                    break   

        data = {
            'data': []
        }   

        for merge in merge_data:
            if len(merge) == 2:
                d = {"Action":"Merge","item":merge[0],"with":merge[1]}
            else:
                d = {"Action":"Transfer","item":merge[0],"with":merge[1], 'count': merge[2]}
            data['data'].append(d)  

        if len(data['data']) > 0:
            req = bot.game.connection.prod_request('/client/game/profile/items/moving', json.dumps(data)) 
            print(req)
            bot.game.update_profile()


email = "ENTER_YOUR_EMAIL_HERE"
password = "ENTER_YOUR_PASSWORD_HASH_HERE"
cookie = 'ENTER_COOKIE_IF_NEEDED_HERE'

bot = TarkovBot(email=email, password=password, cookies=cookie)
