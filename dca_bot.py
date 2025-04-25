from utils.timing import *
from utils.exchange import *
from utils.stats_and_plots import *
from utils.mail_notifier import Notifier
from utils.trade_strategies import PriceMapper

import ccxt
import logging
import time
from dateutil.relativedelta import relativedelta
import pandas as pd
from pathlib import Path
import os
import tweepy
import yaml
import json
from datetime import datetime, date, timedelta
from datetime import time as dtime
import subprocess

def load_config_from_env():
    config_yaml = os.getenv('CONFIG_YML')
    if config_yaml:
        return yaml.safe_load(config_yaml)
    with open('config/config.yml', 'r') as file:
        return yaml.safe_load(file)

def load_api_keys_from_env():
    mexc_keys = {
        'MEXC': {
            'API_KEY': os.getenv('MEXC_API_KEY'),
            'SECRET': os.getenv('MEXC_SECRET')
        }
    }
    twitter_keys = {
        'TWITTER': {
            'API_KEY': os.getenv('TWITTER_API_KEY'),
            'API_SECRET': os.getenv('TWITTER_API_SECRET'),
            'ACCESS_TOKEN': os.getenv('TWITTER_ACCESS_TOKEN'),
            'ACCESS_TOKEN_SECRET': os.getenv('TWITTER_ACCESS_TOKEN_SECRET')
        }
    }
    return mexc_keys, twitter_keys

def push_to_github():
    try:
        subprocess.run(['git', 'add', 'portfolio.json', 'trades/orders.csv'], check=True)
        subprocess.run(['git', 'commit', '-m', 'Update portfolio and orders'], check=True)
        subprocess.run(['git', 'push', 'origin', 'main'], check=True)
        logging.info("Pushed portfolio and orders to GitHub")
    except Exception as e:
        logging.error(f"Failed to push to GitHub: {str(e)}")

class Dca(object):
    def __init__(self, cfg_path=None):
        log_file = Path('trades/log.txt')
        log_file.parent.mkdir(parents=True, exist_ok=True)
        register_logger(log_file=log_file)
        logging.info('Program started. Initializing variables...')

        self.cfg = load_config_from_env()
        self.mexc_keys, self.twitter_keys = load_api_keys_from_env()

        self.twitter_client = self.connect_to_twitter(self.twitter_keys)
        self.portfolio = self.load_portfolio()
        self.test_mode = self.cfg.get('TEST', False)

        if self.cfg['SEND_NOTIFICATIONS']:
            self.notify = Notifier(self.cfg)

        try:
            self.exchange = connect_to_exchange(self.cfg, self.mexc_keys)
        except Exception as e:
            if self.cfg['SEND_NOTIFICATIONS']:
                self.notify.critical(e, "launching the bot")
            raise e

        try:
            balance = get_non_zero_balance(self.exchange, sort_by='total')
            if balance.shape[0] == 0:
                balance_str = 'No coin found in your wallet!'
            else:
                balance_str = balance.to_string()
            logging.info("Your balance from the exchange:\n" + balance_str + "\n")
        except Exception as e:
            logging.warning("Balance checking failed: " + type(e).__name__ + " " + str(e))

        self.coin = {}
        for coin in self.cfg['COINS']:
            self.coin[coin.upper()] = self.cfg['COINS'][coin]
            # Ajouter la précision des prix et des quantités
            self.coin[coin.upper()]['price_precision'] = 2 if coin.upper() == 'BTC' else 4  # 2 pour BTC, 4 pour BKN et ATR
            self.coin[coin.upper()]['quantity_precision'] = 8 if coin.upper() == 'BTC' else 2  # 8 pour BTC, 2 pour BKN et ATR

        self.order_book = {}
        self.coin_to_buy = []
        self.next_order = []

        self.csv_path = Path('trades/orders.csv')
        if Path(self.csv_path).is_file():
            self.df_orders = read_csv_custom(self.csv_path)
        else:
            self.df_orders = pd.DataFrame()

        self.stats_path = Path('trades/stats.csv')
        if Path(self.stats_path).is_file():
            self.df_stats = read_csv_custom(self.stats_path)
        else:
            self.df_stats = pd.DataFrame([], columns=['Coin', 'N', 'Quantity', 'AvgPrice', 'TotalCost', 'ROI', 'ROI%'])
            self.df_stats.set_index(['Coin'], inplace=True)

        self.json_path = Path('trades/orders.json')
        self.order_book_path = Path('trades/next_purchases.csv')

        self.get_dca_strategy()
        self.initialize_order_book()
        df = self.update_order_book()
        logging.info("Summary of the investment plans:\n" + df.to_string() + "\n")

        self.retry_for_funds, self.retry_for_network = retry_info()
        check_cost_limits(self.exchange, self.coin)

        if self.cfg['SEND_NOTIFICATIONS']:
            info = 'DCA bot has just been started'
            self.notify.info(info)

        logging.info('Everything up and running!')

        while True:
            if not isinstance(self.coin[self.coin_to_buy]['LASTERROR'], ccxt.InsufficientFunds):
                self.check_funds()
            self.wait()
            self.buy()
            self.update_order_book()

    def connect_to_twitter(self, twitter_keys):
        client = tweepy.Client(
            consumer_key=twitter_keys['TWITTER']['API_KEY'],
            consumer_secret=twitter_keys['TWITTER']['API_SECRET'],
            access_token=twitter_keys['TWITTER']['ACCESS_TOKEN'],
            access_token_secret=twitter_keys['TWITTER']['ACCESS_TOKEN_SECRET']
        )
        try:
            user = client.get_me()
            logging.info(f"Connexion à Twitter réussie : {user.data.username}")
        except Exception as e:
            logging.error(f"Échec de la connexion à Twitter : {str(e)}")
            raise e
        return client

    def load_portfolio(self):
        try:
            with open('portfolio.json', 'r') as file:
                portfolio = json.load(file)
                if 'challenge_day' not in portfolio:
                    portfolio['challenge_day'] = 0
                return portfolio
        except FileNotFoundError:
            return {
                "BTC": {"total_quantity": 0, "average_price": 0, "purchases": []},
                "BKN": {"total_quantity": 0, "average_price": 0, "purchases": []},
                "ATR": {"total_quantity": 0, "average_price": 0, "purchases": []},
                "challenge_day": 0
            }

    def save_portfolio(self, portfolio):
        with open('portfolio.json', 'w') as file:
            json.dump(portfolio, file, indent=4)

    def update_portfolio(self, coin, quantity, price, amount):
        portfolio = self.portfolio
        portfolio[coin]['purchases'].append({"quantity": quantity, "price": price, "amount": amount})
        total_quantity = sum(p['quantity'] for p in portfolio[coin]['purchases'])
        total_amount = sum(p['amount'] for p in portfolio[coin]['purchases'])
        portfolio[coin]['total_quantity'] = total_quantity
        portfolio[coin]['average_price'] = total_amount / total_quantity if total_quantity > 0 else 0
        self.save_portfolio(portfolio)

    def calculate_performance(self, coin, current_price):
        avg_price = self.portfolio[coin]['average_price']
        if avg_price == 0:
            return 0
        return ((current_price - avg_price) / avg_price) * 100

    def buy(self):
        day_number = self.portfolio.get('challenge_day', 0) + 1
        tweet_lines = [f"Jour {day_number}",
                       f"Challenge DCA quotidiens dans laquelle j'achète sur la plateforme MEXC (code parrainage : 12KxM2). 5$ en BTC, 1$ en BKN et 1$ en ATR",
                       ""]
        coins_to_buy = list(self.coin.keys())

        for coin in coins_to_buy:
            self.coin_to_buy = coin
            order = self.execute_order(coin)
            if order:
                df = order_to_dataframe(self.exchange, order, coin)
                string_order = f"Bought {df['filled'][0]} {coin} at price {df['price'][0]} {self.coin[coin]['PAIRING']} (Cost = {df['cost'][0]} {self.coin[coin]['PAIRING']})"
                logging.info("-> " + string_order)
                self.df_orders = pd.concat([self.df_orders, df]).reset_index(drop=True)
                self.df_orders.index.names = ['N']
                self.df_orders.to_csv(self.csv_path)
                plot_purchases(coin, self.df_orders, self.coin[coin]['PAIRING'])
                self.df_stats = calculate_stats(coin, self.df_orders, self.df_stats, self.stats_path)
                
                if not self.test_mode:
                    self.update_portfolio(coin, df['filled'][0], df['price'][0], df['cost'][0])
                    current_price = get_price(self.exchange, self.coin[coin]['SYMBOL'])
                    performance = self.calculate_performance(coin, current_price)
                    total_cost = sum(p['amount'] for p in self.portfolio[coin]['purchases'])
                    price_precision = self.coin[coin]['price_precision']
                    quantity_precision = self.coin[coin]['quantity_precision']
                    tweet_lines.append(
                        f"- #{coin}: Acheté {df['filled'][0]:.{quantity_precision}f} à {df['price'][0]:.{price_precision}f} {self.coin[coin]['PAIRING']} "
                        f"({df['cost'][0]:.2f}$), Total: {self.portfolio[coin]['total_quantity']:.{quantity_precision}f} "
                        f"(Coût: {total_cost:.2f}$), Évolution: {'+' if performance >= 0 else ''}{performance:.2f}%"
                    )
                
                if self.cfg['SEND_NOTIFICATIONS']:
                    next_purchase = self.coin[coin]['SCHEDULE'].strftime('%d %b %Y at %H:%M')
                    self.notify.success(df,
                                        self.coin[coin]['CYCLE'],
                                        next_purchase,
                                        datetime.now().strftime('%d %b %Y at %H:%M'),
                                        self.coin[coin]['PAIRING'],
                                        self.df_stats.loc[coin],
                                        f"Mode: {self.coin[coin]['STRATEGY_STRING']}")
        
        if not self.test_mode and len(tweet_lines) > 3:
            self.portfolio['challenge_day'] = self.portfolio.get('challenge_day', 0) + 1
            self.save_portfolio(self.portfolio)
            push_to_github()  # Sauvegarde portfolio.json et orders.csv sur GitHub
            tweet_lines.append("")
            tweet_lines.append("#DCA #Crypto #MEXC #Investing #Bitcoin #Trading #Blockchain")
            tweet = "\n".join(tweet_lines)
            if len(tweet) > 280:
                logging.warning(f"Tweet trop long ({len(tweet)} caractères), réduction des hashtags")
                tweet_lines[-1] = "#DCA #Crypto #MEXC"
                tweet = "\n".join(tweet_lines)
            try:
                self.twitter_client.create_tweet(text=tweet)
                logging.info(f"Posted to X: {tweet}")
            except Exception as e:
                logging.error(f"Error posting to X: {str(e)}")

    def execute_order(self, coin):
        type_order = 'market'
        side = 'buy'
        symbol = self.coin[coin]['SYMBOL']
        price = None

        try:
            if self.coin[coin]['STRATEGY'] == 'BuyBelow' or self.coin[coin]['STRATEGY'] == 'VariableAmount':
                price = get_price(self.exchange, self.coin[coin]['SYMBOL'])
                amount = self.coin[coin]['MAPPER'].get_amount(price)
                if amount == 0:
                    string_order = f"{coin} price above buy condition ({price} {self.coin[coin]['PAIRING']})." \
                                   f" This iteration will be skipped."
                    self.handle_successful_trade(coin, string_order)
                    return False
            else:
                amount = self.coin[coin]['AMOUNT']

            if 'binance' in self.exchange.id:
                params = {'quoteOrderQty': amount}
                order = self.exchange.create_order(symbol, type_order, side, amount, price, params)
            else:
                amount = get_quantity_to_buy(self.exchange, amount, symbol)
                order = self.exchange.create_order(symbol, type_order, side, amount, price)
                waiting_time = 0.25
                total_time = 0
                while order['status'] != 'closed':
                    if total_time > 1:
                        raise Exception("The exchange did not return a closed order")
                    time.sleep(waiting_time)
                    order = self.exchange.fetch_order(order['id'], symbol)
                    total_time += waiting_time
            self.handle_successful_trade(coin)
            return order
        except (ccxt.DDoSProtection, ccxt.ExchangeNotAvailable,
                ccxt.InvalidNonce, ccxt.RequestTimeout, ccxt.NetworkError) as e:
            self.handle_recoverable_errors(coin, e)
            if self.cfg['SEND_NOTIFICATIONS'] and self.coin[coin]['ERROR_ATTEMPT'] == 1:
                self.notify.error(coin, self.retry_for_network[self.coin[coin]['CYCLE']], e)
        except ccxt.InsufficientFunds as e:
            self.handle_recoverable_errors(coin, e)
            if self.cfg['SEND_NOTIFICATIONS'] and self.coin[coin]['ERROR_ATTEMPT'] == 1:
                self.notify.error(coin, self.retry_for_funds[self.coin[coin]['CYCLE']], e)
        except ccxt.ExchangeError as e:
            logging.error(type(e).__name__ + ' ' + str(e))
            if self.cfg['SEND_NOTIFICATIONS']:
                when = f"attempting to purchase <strong>{coin}</strong>"
                self.notify.critical(e, when)
            raise e
        except Exception as e:
            logging.error(type(e).__name__ + ' ' + str(e))
            when = f"attempting to purchase <strong>{coin}</strong>"
            if self.cfg['SEND_NOTIFICATIONS']:
                self.notify.critical(e, when)
            raise e
        return False

    def handle_successful_trade(self, coin, string=None):
        self.update_next_datetime(coin)
        self.coin[coin]['LASTERROR'] = []
        self.coin[coin]['ERROR_ATTEMPT'] = 0
        if string:
            logging.info("" + string)

    def handle_recoverable_errors(self, coin, e):
        retry_after = self.get_retry_time(coin, e)
        self.update_next_datetime(coin, retry_after=retry_after)
        if retry_after:
            error_msg = f"{type(e).__name__} {str(e)}\nNext attempt will be in {retry_after} s"
            logging.warning(error_msg)
        else:
            error_msg = f"{type(e).__name__} {str(e)}\nToo many attempts. Skipping this iteration."
            logging.error(error_msg)
        self.coin[coin]['LASTERROR'] = e

    def get_retry_time(self, coin, error):
        self.coin[coin]['ERROR_ATTEMPT'] += 1
        if isinstance(error, ccxt.InsufficientFunds):
            max_attempt = self.retry_for_funds[self.coin[coin]['CYCLE']][0]
            if self.coin[coin]['ERROR_ATTEMPT'] <= max_attempt:
                retry_time = self.retry_for_funds[self.coin[coin]['CYCLE']][1]
                return retry_time
            else:
                self.coin[coin]['ERROR_ATTEMPT'] = 0
                return False
        elif isinstance(error, (ccxt.DDoSProtection, ccxt.ExchangeNotAvailable, ccxt.InvalidNonce, ccxt.RequestTimeout, ccxt.NetworkError)):
            max_attempt = self.retry_for_network[self.coin[coin]['CYCLE']][0]
            if self.coin[coin]['ERROR_ATTEMPT'] <= max_attempt:
                retry_time = self.retry_for_network[self.coin[coin]['CYCLE']][1]
                return retry_time
            else:
                self.coin[coin]['ERROR_ATTEMPT'] = 0
                return False

    def update_next_datetime(self, coin, retry_after=False):
        if retry_after:
            self.order_book[coin] = datetime.today() + timedelta(seconds=retry_after)
        else:
            if self.coin[coin]['CYCLE'].lower() == 'minutely':
                if not self.cfg['TEST']:
                    error_string = 'Cycle "minutely" is only available in TEST mode.'
                    logging.error(error_string)
                    raise Exception(error_string)
                self.coin[coin]['SCHEDULE'] = datetime.now()
            elif self.coin[coin]['CYCLE'].lower() == 'daily':
                at_time = get_hour_minute(self.coin[coin]['AT_TIME'])
                scheduled_datetime = datetime.combine(date.today(), dtime(at_time[0], at_time[1]))
                if scheduled_datetime < datetime.now():
                    scheduled_datetime = scheduled_datetime + timedelta(days=1)
                self.coin[coin]['SCHEDULE'] = scheduled_datetime
            elif 'weekly' in self.coin[coin]['CYCLE'].lower():
                at_time = get_hour_minute(self.coin[coin]['AT_TIME'])
                on_weekday = get_on_weekday(self.coin[coin]['ON_WEEKDAY'])
                today = date.today()
                scheduled_datetime = datetime.combine(today + timedelta((on_weekday - today.weekday()) % 7),
                                                     dtime(at_time[0], at_time[1]))
                if scheduled_datetime < datetime.now():
                    scheduled_datetime = scheduled_datetime + timedelta(days=7)
                if 'bi-weekly' in self.coin[coin]['CYCLE'].lower() and self.order_book_path.exists():
                    df = read_csv_custom(self.order_book_path)
                    previously = None
                    for cn in df.index:
                        if cn == coin and df.loc[cn]['Cycle'] == 'bi-weekly':
                            previously = df.loc[cn]['Purchase Time']
                    if previously:
                        previously = datetime.strptime(previously, '%Y-%m-%d %H:%M:%S')
                        if previously == scheduled_datetime + timedelta(days=7):
                            scheduled_datetime = previously
                self.coin[coin]['SCHEDULE'] = scheduled_datetime
            elif self.coin[coin]['CYCLE'].lower() == 'monthly':
                at_time = get_hour_minute(self.coin[coin]['AT_TIME'])
                on_day = get_on_day(self.coin[coin]['ON_DAY'])
                today = datetime.now()
                scheduled_datetime = datetime.combine(datetime(today.year, today.month, on_day),
                                                     dtime(at_time[0], at_time[1]))
                if scheduled_datetime < datetime.now():
                    scheduled_datetime = scheduled_datetime + relativedelta(months=1)
                self.coin[coin]['SCHEDULE'] = scheduled_datetime
            else:
                error_string = 'Cycle not recognized. Valid cycle strings are: "daily", "weekly", ' \
                               '"bi-weekly" and "monthly".'
                logging.error(error_string)
                raise Exception(error_string)

            self.order_book[coin] = self.coin[coin]['SCHEDULE']

    def get_dca_strategy(self):
        for coin in self.coin:
            if os.path.exists(f"trades/graph_{coin}_buy_conditions.png"):
                os.remove(f"trades/graph_{coin}_buy_conditions.png")
            if type(self.coin[coin]['AMOUNT']) is dict:
                if 'RANGE' not in self.coin[coin]['AMOUNT'] or 'PRICE_RANGE' not in self.coin[coin]['AMOUNT'] or 'MAPPING' not in self.coin[coin]['AMOUNT']:
                    raise Exception('If AMOUNT is a dictionary the following keys are required: '
                                    '"AMOUNT", "PRICE_RANGE", "MAPPING".')
                self.coin[coin]['MAPPER'] = PriceMapper(self.coin[coin]['AMOUNT']['RANGE'],
                                                        self.coin[coin]['AMOUNT']['PRICE_RANGE'],
                                                        self.coin[coin]['AMOUNT']['MAPPING'],
                                                        coin,
                                                        self.coin[coin]['PAIRING'])
                self.coin[coin]['MAPPER'].plot()
                self.coin[coin]['STRATEGY'] = 'VariableAmount'
                cost = f"{self.coin[coin]['AMOUNT']['RANGE'][0]}-" \
                       f"{self.coin[coin]['AMOUNT']['RANGE'][1]}"
                price_range = f"{self.coin[coin]['AMOUNT']['PRICE_RANGE'][0]}-" \
                              f"{self.coin[coin]['AMOUNT']['PRICE_RANGE'][1]}"
                self.coin[coin]['STRATEGY_STRING'] = f"{cost} {self.coin[coin]['PAIRING']} to {price_range} {coin} {self.coin[coin]['AMOUNT']['MAPPING'][0:3]}."
                if 'BUYBELOW' in self.coin[coin] and self.coin[coin]['BUYBELOW'] is not None:
                    logging.warning('Option "BUYBELOW" is not compatible with a range of AMOUNT values. '
                                    'Disabling it')
                    self.coin[coin]['BUYBELOW'] = None
            elif 'BUYBELOW' in self.coin[coin] and self.coin[coin]['BUYBELOW'] is not None:
                self.coin[coin]['MAPPER'] = PriceMapper([0, self.coin[coin]['AMOUNT']],
                                                        [0, self.coin[coin]['BUYBELOW']],
                                                        'constant',
                                                        coin,
                                                        self.coin[coin]['PAIRING'])
                self.coin[coin]['MAPPER'].plot()
                self.coin[coin]['STRATEGY'] = 'BuyBelow'
                self.coin[coin]['STRATEGY_STRING'] = f"BuyBelow {self.coin[coin]['BUYBELOW']} {self.coin[coin]['PAIRING']}"
            else:
                self.coin[coin]['STRATEGY'] = 'Classic'
                self.coin[coin]['STRATEGY_STRING'] = f"Classic"

    def check_funds(self):
        cost = self.coin[self.coin_to_buy]['AMOUNT']
        if type(cost) is dict:
            cost = cost['RANGE'][1]
        pairing = self.coin[self.coin_to_buy]['PAIRING']
        try:
            balance = self.exchange.fetch_balance()
        except:
            balance = []
            logging.warning("Balance checking failed.")

        if balance:
            balance_type = 'total' if self.exchange.id == 'kraken' else 'free'
            if pairing in balance[balance_type]:
                coin_balance = balance[balance_type][pairing]
            else:
                coin_balance = 0
            if cost > coin_balance:
                logging.warning(f"Insufficient funds for the next {self.coin_to_buy} purchase. Top up your account!")
                if self.cfg['SEND_NOTIFICATIONS']:
                    next_purchase = self.next_order[1].strftime('%d %b %Y at %H:%M')
                    self.notify.warning_funds(self.coin_to_buy,
                                              next_purchase,
                                              pairing,
                                              cost,
                                              coin_balance)

    def wait(self):
        time_remaining = (self.next_order[1] - datetime.today()).total_seconds()
        if time_remaining < 0:
            time_remaining = 0
        if self.coin[self.next_order[0]]['STRATEGY'] == 'VariableAmount':
            cost = f"{self.coin[self.next_order[0]]['AMOUNT']['RANGE'][0]}-" \
                   f"{self.coin[self.next_order[0]]['AMOUNT']['RANGE'][1]}"
        else:
            cost = self.coin[self.next_order[0]]['AMOUNT']
        logging.info(f"Next purchase: {self.next_order[0]} ({cost} "
                     f"{self.coin[self.next_order[0]]['PAIRING']}) on {self.next_order[1].strftime('%Y-%m-%d %H:%M')}."
                     f"\nTime remaining: {int(time_remaining)} s")

        time.sleep(time_remaining)

    def update_order_book(self):
        self.next_order = min(self.order_book.items(), key=lambda x: x[1])
        self.coin_to_buy = self.next_order[0]
        ordered_order_book = dict(sorted(self.order_book.items(), key=lambda item: item[1]))
        df = pd.DataFrame([ordered_order_book]).T.rename_axis('Coin').rename(columns={0: 'Purchase Time'})
        cycle = []
        strategy = []
        for coin in df.index:
            cycle.append(self.coin[coin]['CYCLE'].lower())
            strategy.append(self.coin[coin]['STRATEGY_STRING'])
        df['Cycle'] = cycle
        df['Strategy'] = strategy
        df.to_csv(self.order_book_path)
        return df

    def initialize_order_book(self):
        for coin in self.coin:
            if self.coin[coin]['CYCLE'].lower() == 'minutely':
                if not self.cfg['TEST']:
                    error_string = 'Cycle "minutely" is only available in TEST mode.'
                    logging.error(error_string)
                    raise Exception(error_string)
                self.coin[coin]['SCHEDULE'] = datetime.now()
            elif self.coin[coin]['CYCLE'].lower() == 'daily':
                at_time = get_hour_minute(self.coin[coin]['AT_TIME'])
                scheduled_datetime = datetime.combine(date.today(), dtime(at_time[0], at_time[1]))
                if scheduled_datetime < datetime.now():
                    scheduled_datetime = scheduled_datetime + timedelta(days=1)
                self.coin[coin]['SCHEDULE'] = scheduled_datetime
            elif 'weekly' in self.coin[coin]['CYCLE'].lower():
                at_time = get_hour_minute(self.coin[coin]['AT_TIME'])
                on_weekday = get_on_weekday(self.coin[coin]['ON_WEEKDAY'])
                today = date.today()
                scheduled_datetime = datetime.combine(today + timedelta((on_weekday - today.weekday()) % 7),
                                                     dtime(at_time[0], at_time[1]))
                if scheduled_datetime < datetime.now():
                    scheduled_datetime = scheduled_datetime + timedelta(days=7)
                if 'bi-weekly' in self.coin[coin]['CYCLE'].lower() and self.order_book_path.exists():
                    df = read_csv_custom(self.order_book_path)
                    previously = None
                    for cn in df.index:
                        if cn == coin and df.loc[cn]['Cycle'] == 'bi-weekly':
                            previously = df.loc[cn]['Purchase Time']
                    if previously:
                        previously = datetime.strptime(previously, '%Y-%m-%d %H:%M:%S')
                        if previously == scheduled_datetime + timedelta(days=7):
                            scheduled_datetime = previously
                self.coin[coin]['SCHEDULE'] = scheduled_datetime
            elif self.coin[coin]['CYCLE'].lower() == 'monthly':
                at_time = get_hour_minute(self.coin[coin]['AT_TIME'])
                on_day = get_on_day(self.coin[coin]['ON_DAY'])
                today = datetime.now()
                scheduled_datetime = datetime.combine(datetime(today.year, today.month, on_day),
                                                     dtime(at_time[0], at_time[1]))
                if scheduled_datetime < datetime.now():
                    scheduled_datetime = scheduled_datetime + relativedelta(months=1)
                self.coin[coin]['SCHEDULE'] = scheduled_datetime
            else:
                error_string = 'Cycle not recognized. Valid cycle strings are: "daily", "weekly", ' \
                               '"bi-weekly" and "monthly".'
                logging.error(error_string)
                raise Exception(error_string)

            self.order_book[coin] = self.coin[coin]['SCHEDULE']
            self.coin[coin]['SYMBOL'] = coin + '/' + self.coin[coin]['PAIRING']
            self.coin[coin]['LASTERROR'] = []
            self.coin[coin]['ERROR_ATTEMPT'] = 0

if __name__ == "__main__":
    dca = Dca()