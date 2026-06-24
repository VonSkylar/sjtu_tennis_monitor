import queue
import unittest
from types import SimpleNamespace

from sjtu_tennis_toolkit.browser.rusher import RushBooker
from sjtu_tennis_toolkit.browser.monitor import VenueMonitor
from sjtu_tennis_toolkit.exceptions import BookingPageNotReady
from sjtu_tennis_toolkit.models import VENUES_BY_KEY


class FakeLocator:
    def __init__(self, text: str = "", count: int = 0) -> None:
        self.text = text
        self._count = count

    def inner_text(self, timeout: int) -> str:
        return self.text

    def count(self) -> int:
        return self._count


class FakePage:
    def __init__(self, url: str, body_text: str = "") -> None:
        self.url = url
        self.body_text = body_text
        self.goto_calls = []

    def is_closed(self) -> bool:
        return False

    def locator(self, selector: str) -> FakeLocator:
        if selector == "body":
            return FakeLocator(self.body_text)
        return FakeLocator()

    def goto(self, url: str, wait_until: str) -> None:
        self.goto_calls.append((url, wait_until))
        self.url = url


class FakeContext:
    def __init__(self, pages) -> None:
        self.pages = pages
        self.new_page_called = False

    def new_page(self):
        self.new_page_called = True
        raise AssertionError("不应该创建第二个登录标签页")


class BrowserMonitorNavigationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.monitor = VenueMonitor(lambda: None, queue.Queue())
        self.venue = VENUES_BY_KEY["huxiaoming"]

    def test_target_venue_url_does_not_navigate_again(self) -> None:
        self.assertFalse(self.monitor._should_navigate_to_venue(self.venue.url, self.venue))

    def test_sports_home_redirect_navigates_back_to_huxiaoming(self) -> None:
        self.assertTrue(
            self.monitor._should_navigate_to_venue("https://sports.sjtu.edu.cn/pc/#/", self.venue)
        )

    def test_sports_home_redirect_navigates_back_to_east(self) -> None:
        east = VENUES_BY_KEY["east"]
        self.assertTrue(
            self.monitor._should_navigate_to_venue("https://sports.sjtu.edu.cn/pc/#/", east)
        )

    def test_other_venue_page_navigates_to_selected_venue(self) -> None:
        east = VENUES_BY_KEY["east"]
        self.assertTrue(self.monitor._should_navigate_to_venue(east.url, self.venue))

    def test_jaccount_login_page_is_left_for_user_login(self) -> None:
        self.assertFalse(
            self.monitor._should_navigate_to_venue(
                "https://jaccount.sjtu.edu.cn/jaccount/jalogin",
                self.venue,
            )
        )

    def test_existing_login_page_prevents_second_login_page(self) -> None:
        login_page = FakePage("https://jaccount.sjtu.edu.cn/jaccount/jalogin")
        context = FakeContext([login_page])
        with self.assertRaises(BookingPageNotReady):
            self.monitor._find_or_open_venue_page(context, self.venue)
        self.assertFalse(context.new_page_called)

    def test_successful_login_redirects_other_cached_login_page(self) -> None:
        east = VENUES_BY_KEY["east"]
        east_login = FakePage("https://jaccount.sjtu.edu.cn/jaccount/jalogin")
        huxiaoming_page = FakePage(self.venue.url)
        self.monitor._pages_by_venue = {
            east.key: east_login,
            self.venue.key: huxiaoming_page,
        }

        self.monitor._redirect_misaligned_venue_pages()

        self.assertEqual(east_login.goto_calls, [(east.url, "domcontentloaded")])
        self.assertEqual(huxiaoming_page.goto_calls, [])

    def test_successful_login_redirects_other_cached_home_page_immediately(self) -> None:
        east = VENUES_BY_KEY["east"]
        east_home = FakePage("https://sports.sjtu.edu.cn/pc/#/")
        huxiaoming_page = FakePage(self.venue.url)
        self.monitor._pages_by_venue = {
            east.key: east_home,
            self.venue.key: huxiaoming_page,
        }

        self.monitor._redirect_misaligned_venue_pages()

        self.assertEqual(east_home.goto_calls, [(east.url, "domcontentloaded")])
        self.assertEqual(huxiaoming_page.goto_calls, [])

    def test_monitor_booking_check_redirects_home_page_to_target_venue(self) -> None:
        page = FakePage(
            "https://sports.sjtu.edu.cn/pc/#/",
            body_text="Shanghai Jiao Tong University Venue Reservation System",
        )

        with self.assertRaises(BookingPageNotReady):
            self.monitor._ensure_booking_page(page, self.venue)

        self.assertEqual(page.goto_calls, [(self.venue.url, "domcontentloaded")])

    def test_rush_booker_redirects_home_page_to_target_venue(self) -> None:
        page = FakePage(
            "https://sports.sjtu.edu.cn/pc/#/",
            body_text="Shanghai Jiao Tong University Venue Reservation System",
        )
        config = SimpleNamespace(venue=self.venue)
        booker = RushBooker(lambda: config, queue.Queue())

        with self.assertRaises(BookingPageNotReady):
            booker._ensure_rush_venue_page(page, config)

        self.assertEqual(page.goto_calls, [(self.venue.url, "domcontentloaded")])


if __name__ == "__main__":
    unittest.main()
