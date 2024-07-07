import streamlit as st
import threading
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from bs4 import BeautifulSoup
import time
import re
import datetime
import json
from collections import defaultdict
from binance.client import Client

# Global variable to store the instance of ScrapeTask
scrape_task = None

class ScrapeTask:
    def __init__(self, link, api_key, api_secret, leverage, trader_portfolio_size, your_portfolio_size):
        self.link = link
        self.driver = None
        self.binance_client = None
        self.processed_orders = set()
        self.current_page = 1
        self.current_time = None
        self.all_orders = []
        self.timer = None
        self.running = False
        self.leverage = leverage
        self.trader_portfolio_size = trader_portfolio_size
        self.your_portfolio_size = your_portfolio_size
        self.close_only_mode = False
        self.reverse_copy = False
        self.api_key = api_key
        self.api_secret = api_secret
        self.min_order_quantity = {}  # Dictionary to store minimum order quantities by symbol
        self.initialize_binance_client()  # Initialize Binance client immediately
        self.fetch_min_order_quantities()  # Fetch minimum order quantities

    def initialize_driver(self):
        try:
            chrome_options = Options()
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--headless")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--disable-extensions")
            chrome_options.add_argument("--disable-dev-shm-usage")
            self.driver = webdriver.Chrome(options=chrome_options)
            self.driver.get(self.link)
            print("WebDriver initialized.")
        except Exception as e:
            print(f"Error initializing WebDriver: {e}")
            self.running = False

    def initialize_binance_client(self):
        try:
            self.binance_client = Client(self.api_key, self.api_secret)
            print("Binance client initialized.")
        except Exception as e:
            print(f"Error initializing Binance client: {e}")
            self.running = False

    def fetch_min_order_quantities(self):
        try:
            exchange_info = self.binance_client.futures_exchange_info()
            for symbol_info in exchange_info['symbols']:
                symbol = symbol_info['symbol']
                filters = symbol_info['filters']
                for f in filters:
                    if f['filterType'] == 'LOT_SIZE':
                        self.min_order_quantity[symbol] = float(f['minQty'])
                        break
        except Exception as e:
            print(f"Error fetching minimum order quantities: {e}")

    def start_scraping(self):
        if not self.driver:
            self.initialize_driver()
            self.accept_cookies()
            self.navigate_to_trade_history()

        self.running = True
        self.scrape_and_display_orders()

    def accept_cookies(self):
        try:
            time.sleep(2)
            accept_btn = self.find_element_with_retry(By.ID, "onetrust-accept-btn-handler")
            accept_btn.click()
            print("Accepted cookies.")
            time.sleep(2)
        except Exception as e:
            print(f"Error accepting cookies: {e}")

    def navigate_to_trade_history(self):
        try:
            move_to_trade_history = self.find_element_with_retry(By.CSS_SELECTOR, "#tab-tradeHistory > div")
            self.driver.execute_script("arguments[0].scrollIntoView(true);", move_to_trade_history)
            move_to_trade_history.click()
            print("Navigated to trade history tab.")
            time.sleep(2)
        except Exception as e:
            print(f"Trade history tab not found: {e}")
            self.driver.refresh()
            print("Page refreshed.")
            self.navigate_to_trade_history()

    def scrape_and_display_orders(self):
        try:
            while self.running:
                self.current_time = datetime.datetime.now().replace(second=0, microsecond=0)
                print(f"Current time: {self.current_time}")

                found_data = False
                soup = BeautifulSoup(self.driver.page_source, 'html.parser')
                orders = soup.select(".css-g5h8k8 > div > div > div > table > tbody > tr")

                for order in orders:
                    time_str = order.select_one("td:nth-child(1)").text.strip()
                    order_time = datetime.datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S').replace(second=0, microsecond=0)
                    time_diff = (self.current_time - order_time).total_seconds() / 60

                    if abs(time_diff) <= 2:
                        symbol = order.select_one("td:nth-child(2)").text.strip()
                        side = order.select_one("td:nth-child(3)").text.strip()
                        price_str = order.select_one("td:nth-child(4)").text.strip()
                        quantity_str = order.select_one("td:nth-child(5)").text.strip()
                        realized_profit_str = order.select_one("td:nth-child(6)").text.strip()

                        price = float(re.sub(r'[^\d.]', '', price_str.replace(',', '')))
                        quantity_str = re.sub(r'[^\d.]', '', quantity_str.replace(',', ''))
                        quantity = float(quantity_str)
                        realized_profit_str = realized_profit_str.replace('USDT', '').strip()
                        realized_profit = float(realized_profit_str.replace(',', ''))

                        symbol = self.add_space_before_and_remove_perpetual(symbol)

                        order_id = f"{time_str}-{symbol}-{side}-{price}"
                        if order_id not in self.processed_orders:
                            self.processed_orders.add(order_id)
                            order_data = {
                                "Time": time_str,
                                "Symbol": symbol,
                                "Side": side,
                                "Price": price,
                                "Quantity": quantity,
                                "Realized Profit": realized_profit
                            }
                            self.all_orders.append(order_data)
                            found_data = True
                            print(f"Added order: {order_id}")

                if not found_data:
                    print("No data found on current page.")
                    self.go_to_first_page()
                    continue

                next_page_button = self.find_element_with_retry(By.CSS_SELECTOR, "div.bn-pagination-next")
                self.driver.execute_script("arguments[0].scrollIntoView(true);", next_page_button)
                next_page_button.click()
                print("Navigated to next page.")
                time.sleep(2)
                self.current_page += 1

                if not self.has_next_page():
                    print("No next page found. Returning to first page.")
                    self.go_to_first_page()
                    time.sleep(2)

                self.save_orders_to_file()
                self.process_orders()

        except Exception as e:
            print(f"Error scraping and displaying orders: {e}")

        finally:
            if self.driver:
                self.driver.quit()
                print("WebDriver quit.")

    def find_element_with_retry(self, by, selector, max_attempts=3):
        attempts = 0
        while attempts < max_attempts:
            try:
                element = self.driver.find_element(by, selector)
                return element
            except Exception as e:
                attempts += 1
                print(f"Error finding element {selector} (Attempt {attempts}/{max_attempts}): {e}")
                time.sleep(2)
        raise NoSuchElementException(f"Element {selector} not found after {max_attempts} attempts")

    def has_next_page(self):
        try:
            next_page_button = self.driver.find_element(By.CSS_SELECTOR, "div.bn-pagination-next")
            return next_page_button.is_enabled()
        except NoSuchElementException:
            return False

    def go_to_first_page(self):
        try:
            self.driver.get(self.link)
            time.sleep(2)
            self.navigate_to_trade_history()
            self.current_page = 1
        except Exception as e:
            print(f"Error navigating to first page: {e}")

    def add_space_before_and_remove_perpetual(self, text):
        text = re.sub(r" ?Perpetual", "", text)
        return text.strip()

    def save_orders_to_file(self):
        summarized_orders = self.summarize_orders(self.all_orders)
        with open('trade_history.json', 'w') as json_file:
            json.dump(summarized_orders, json_file, indent=4)
        print("Orders saved to file.")

        self.process_orders()

        if self.timer:
            self.timer.cancel()
        self.timer = threading.Timer(300, self.delete_orders_from_file)
        self.timer.start()

    def delete_orders_from_file(self):
        self.all_orders.clear()
        with open('trade_history.json', 'w') as json_file:
            json.dump(self.all_orders, json_file, indent=4)
        print("Orders deleted from file after 5 minutes.")

    def summarize_orders(self, orders):
        summarized_dict = defaultdict(lambda: {"Quantity": 0.0, "Realized Profit": 0.0})
        for order in orders:
            key = (order["Time"], order["Symbol"], order["Side"], order["Price"])
            summarized_dict[key]["Quantity"] += order["Quantity"]
            summarized_dict[key]["Realized Profit"] += order["Realized Profit"]
        return [{"Time": key[0], "Symbol": key[1], "Side": key[2], "Price": key[3],
                 "Quantity": value["Quantity"], "Realized Profit": value["Realized Profit"]} for key, value in
                summarized_dict.items()]

    def process_orders(self):
        for order in self.all_orders:
            if self.should_open_position(order):
                if order["Side"] == "Open long" or order["Side"] == "Buy/long":
                    self.open_long_position(order)
                elif order["Side"] == "Close long" or order["Side"] == "Sell/Short":
                    self.close_long_position(order)
                elif order["Side"] == "Open short" or order["Side"] == "Buy/long":
                    self.open_short_position(order)
                elif order["Side"] == "Close short" or order["Side"] == "Buy/Long":
                    self.close_short_position(order)
                else:
                    print(f"Unsupported order side: {order['Side']}")
            else:
                st.write(f"Not opening position for order: {order}")

    def should_open_position(self, order):
        if self.close_only_mode:
            return False

        if order["Realized Profit"] != 0.0:
            return False

        order_time = datetime.datetime.strptime(order["Time"], '%Y-%m-%d %H:%M:%S')
        time_diff = (datetime.datetime.now() - order_time).total_seconds() / 60
        if time_diff > 1:
            return False

        calculated_quantity = (self.trader_portfolio_size * self.your_portfolio_size) / self.trader_portfolio_size
        if self.reverse_copy:
            if order["Side"] == "Open long" or order["Side"] == "Buy/long":
                return self.adjust_quantity_to_min(order["Symbol"], calculated_quantity) >= self.min_order_quantity[order["Symbol"]]
            elif order["Side"] == "Close long" or order["Side"] == "Sell/Short":
                return self.adjust_quantity_to_min(order["Symbol"], calculated_quantity * 1.05) >= self.min_order_quantity[order["Symbol"]]
            elif order["Side"] == "Open short" or order["Side"] == "Buy/long":
                return self.adjust_quantity_to_min(order["Symbol"], calculated_quantity) >= self.min_order_quantity[order["Symbol"]]
            elif order["Side"] == "Close short" or order["Side"] == "Buy/Long":
                return self.adjust_quantity_to_min(order["Symbol"], calculated_quantity * 1.05) >= self.min_order_quantity[order["Symbol"]]
            else:
                print(f"Unsupported order side: {order['Side']}")
                return False
        else:
            if order["Side"] == "Open long" or order["Side"] == "Buy/long":
                return self.adjust_quantity_to_min(order["Symbol"], calculated_quantity) >= self.min_order_quantity[order["Symbol"]]
            elif order["Side"] == "Close long" or order["Side"] == "Sell/Short":
                return self.adjust_quantity_to_min(order["Symbol"], calculated_quantity * 1.05) >= self.min_order_quantity[order["Symbol"]]
            elif order["Side"] == "Open short" or order["Side"] == "Buy/long":
                return self.adjust_quantity_to_min(order["Symbol"], calculated_quantity) >= self.min_order_quantity[order["Symbol"]]
            elif order["Side"] == "Close short" or order["Side"] == "Buy/Long":
                return self.adjust_quantity_to_min(order["Symbol"], calculated_quantity * 1.05) >= self.min_order_quantity[order["Symbol"]]
            else:
                print(f"Unsupported order side: {order['Side']}")
                return False

    def adjust_quantity_to_min(self, symbol, quantity):
        min_qty = self.min_order_quantity.get(symbol, None)
        if min_qty is not None and quantity < min_qty:
            print(f"Adjusting quantity for {symbol} from {quantity} to {min_qty}")
            return min_qty
        return quantity

    def open_long_position(self, order):
        try:
            quantity = (self.trader_portfolio_size * self.your_portfolio_size) / self.trader_portfolio_size
            quantity = self.adjust_quantity_to_min(order["Symbol"], quantity)
            if self.leverage:
                self.binance_client.futures_create_order(symbol=order["Symbol"], side="BUY",positionSide='LONG',
                                                         type="MARKET", quantity=quantity, leverage=self.leverage, recvWindow=60000)
                print(f"Opened long position for {order['Symbol']} with quantity {quantity} and leverage {self.leverage}.")
            else:
                self.binance_client.futures_create_order(symbol=order["Symbol"], side="BUY",
                                                         type="MARKET", quantity=quantity, recvWindow=60000)
                print(f"Opened long position for {order['Symbol']} with quantity {quantity}.")
        except Exception as e:
            print(f"Error opening long position for {order['Symbol']}: {e}")

    def close_long_position(self, order):
        try:
            quantity = ((self.trader_portfolio_size * self.your_portfolio_size) / self.trader_portfolio_size) * 1.05
            quantity = self.adjust_quantity_to_min(order["Symbol"], quantity)
            self.binance_client.futures_create_order(symbol=order["Symbol"], side="SELL",
                                                     type="MARKET", quantity=quantity, recvWindow=60000)
            print(f"Closed long position for {order['Symbol']} with quantity {quantity}.")
        except Exception as e:
            print(f"Error closing long position for {order['Symbol']}: {e}")

    def open_short_position(self, order):
        try:
            quantity = (self.trader_portfolio_size * self.your_portfolio_size) / self.trader_portfolio_size
            quantity = self.adjust_quantity_to_min(order["Symbol"], quantity)
            if self.leverage:
                self.binance_client.futures_create_order(symbol=order["Symbol"], side="SELL",
                                                         type="MARKET", quantity=quantity, leverage=self.leverage, recvWindow=60000)
                print(f"Opened short position for {order['Symbol']} with quantity {quantity} and leverage {self.leverage}.")
            else:
                self.binance_client.futures_create_order(symbol=order["Symbol"], side="SELL",
                                                         type="MARKET", quantity=quantity, recvWindow=60000)
                print(f"Opened short position for {order['Symbol']} with quantity {quantity}.")
        except Exception as e:
            print(f"Error opening short position for {order['Symbol']}: {e}")

    def close_short_position(self, order):
        try:
            quantity = ((self.trader_portfolio_size * self.your_portfolio_size) / self.trader_portfolio_size) * 1.05
            quantity = self.adjust_quantity_to_min(order["Symbol"], quantity)
            self.binance_client.futures_create_order(symbol=order["Symbol"], side="BUY",
                                                     type="MARKET", quantity=quantity, recvWindow=60000)
            print(f"Closed short position for {order['Symbol']} with quantity {quantity}.")
        except Exception as e:
            print(f"Error closing short position for {order['Symbol']}: {e}")

def main():
    st.title("Trading Automation Program")

    st.header("Settings")
    link = st.text_input("Enter Trader's Portfolio Link:")
    api_key = st.text_input("Enter Your Binance API Key:")
    api_secret = st.text_input("Enter Your Binance API Secret:")
    leverage = st.number_input("Enter Leverage (if any):", min_value=0, value=0)
    trader_portfolio_size = st.number_input("Enter Trader's Portfolio Size:")
    your_portfolio_size = st.number_input("Enter Your Portfolio Size:")

    global scrape_task

    if st.button("Start Scraping"):
        if link and api_key and api_secret and trader_portfolio_size and your_portfolio_size:
            st.success("Scraping started!")
            scrape_task = ScrapeTask(link, api_key, api_secret, leverage, trader_portfolio_size, your_portfolio_size)
            scrape_task.start_scraping()
        else:
            st.error("Please fill in all fields before starting.")

    if st.button("Stop Scraping"):
        if scrape_task:
            st.warning("Scraping stopped!")
            scrape_task.running = False
            scrape_task = None
        else:
            st.error("Scraping is not currently running.")

if __name__ == "__main__":
    main()
