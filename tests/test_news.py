"""Focused, dated, balanced news rather than general-interest recency."""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import sources as S


class NewsTests(unittest.TestCase):
    def test_focus(self):
        for title in ['Best crypto to buy now: 100x presale', 'Bitcoin price prediction for 2030',
                      'Best credit cards', 'Celebrity divorce shocks fans', 'Apple iPhone review']:
            self.assertIsNone(S._news_topic(title), title)
        for title, topic in [('Fed holds interest rates as inflation rises', 'Macro'),
                             ('Bitcoin ETF inflows accelerate', 'Crypto'),
                             ('Nvidia raises AI data center guidance', 'Tech')]:
            self.assertEqual(S._news_topic(title), topic)

    def test_balance_dates_duplicates_and_no_summary_keyword_leakage(self):
        now = time.time()
        items = [S.Headline('Fed interest rate decision '+str(i), 'WSJ', now-i) for i in range(10)]
        items += [S.Headline('Bitcoin ETF inflows accelerate', 'CoinDesk', now-20),
                  S.Headline('Nvidia AI chip demand grows', 'FT', now-30),
                  S.Headline('Nvidia AI chip demand grows!', 'WSJ', now-31),
                  S.Headline('Fed interest rate old story', 'FT', now-90000),
                  S.Headline('Bitcoin ETF missing date', 'CoinDesk', 0),
                  S.Headline('Bitcoin ETF future story', 'CoinDesk', now+7200),
                  S.Headline('Celebrity lifestyle', 'FT', now, summary='Bitcoin Fed AI')]
        with patch.object(S, '_fetch_feed', return_value=items):
            S._feeds.clear()
            result = S.headlines({'feeds': ['markets'], 'max_age_h': 18}, True)
        self.assertEqual([h.section.split(' · ')[0] for h in result[:3]], ['Macro', 'Crypto', 'Tech'])
        self.assertEqual(len(result), 12)
        self.assertNotIn('Celebrity lifestyle', [h.title for h in result])
        self.assertTrue(all(h.published > now-90000 for h in result))

    def test_non_feed_is_failure_not_successful_empty_refresh(self):
        response = Mock(content=b'<html>blocked</html>')
        with patch.object(S.requests, 'get', return_value=response):
            with self.assertRaises(ValueError):
                S._fetch_feed('markets')


if __name__ == '__main__':
    unittest.main()
